"""M4 audit only. Deterministic faults; all mutations require a fresh --root.

Run with the repository venv: python -m evaluation.m4_audit --root <new directory>.
No production code changes, external model requests, game access or real data writes.
"""
import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_for(predicate, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise TimeoutError('Audit bounded wait expired')


def harness(root, require_input=False):
    from evaluation.test_session_semantics import SessionSemanticsTests
    from evaluation.player_fixtures import write_fixture
    from player_data.repository import FixtureRepository
    root.mkdir(parents=True, exist_ok=True)
    h = SessionSemanticsTests()
    h.root, h.fixture = root, root / 'fixture'
    if not h.fixture.exists():
        write_fixture(h.fixture, name='Isolated M4 Fixture', speed=44)
    h.repository = FixtureRepository(h.fixture)
    h.calls, h.require_input = [], require_input
    return h


def settled(client, identity, status=None):
    def check():
        if identity in client.app.state.service.active:
            return None
        v = client.get('/api/missions/' + identity).json()
        return v if status is None or v['status'] == status else None
    return wait_for(check)


def start(client, text='M4 isolated audit'):
    r = client.post('/api/messages', json={'conversation_id': 'conv_session', 'content': text})
    assert r.status_code == 202, r.text
    return r.json()['mission_id']


def protected_hashes(audit_root):
    paths = [REPO / 'memory', REPO / 'local_data', REPO / 'game_adapters' / 'raw']
    out = {}
    for directory in paths:
        if directory.exists():
            for p in directory.rglob('*'):
                if p.is_file() and not p.is_relative_to(REPO / 'local_data/game_imports/m4-audit'):
                    out[str(p.relative_to(REPO))] = digest(p)
    for name in ['docs/README.md', 'docs/guides/M4_运行可靠性审计_Codex任务书.md']:
        p = REPO / name
        out[name] = digest(p)
    return out


def sdk(root):
    """Real installed SDK retry transport, no network. Repository factory unchanged."""
    import httpx
    from config import config
    from utils.helpers import create_llm
    from agents.manager import ManagerAgent
    from langchain_core.messages import HumanMessage
    from utils.telemetry import token_usage_from_response
    out = {}
    with patch.object(config, 'OPENAI_API_KEY', 'audit-not-a-key'), patch.object(config, 'OPENAI_BASE_URL', 'http://audit.invalid/v1'):
        default = create_llm()
        out['factory_defaults'] = {'langchain_timeout': default.request_timeout,
            'langchain_max_retries': default.max_retries, 'sdk_timeout': str(default.root_client.timeout),
            'sdk_transport_timeout': str(default.root_client._client.timeout),
            'sdk_max_retries': default.root_client.max_retries}
        default.root_client.close()
        for scenario, code in [('500', 500), ('400', 400), ('500-exhausted', 500), ('read-timeout', 0)]:
            requests = []
            def transport(request):
                requests.append({'method': request.method, 'attempt': len(requests) + 1})
                if scenario == 'read-timeout':
                    raise httpx.ReadTimeout('audit transport timeout', request=request)
                if len(requests) == 1 or scenario == '500-exhausted':
                    return httpx.Response(code, json={'error': {'message': 'isolated fault', 'type': 'audit'}})
                return httpx.Response(200, json={'id': 'audit', 'object': 'chat.completion', 'created': 0,
                    'model': 'audit', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}})
            with httpx.Client(transport=httpx.MockTransport(transport)) as client:
                model = create_llm().model_copy(update={})
                # Build the same ChatOpenAI factory type with a controlled transport.
                model.root_client.close()
                model = type(model)(model='audit', api_key='audit-not-a-key', base_url='http://audit.invalid/v1', http_client=client)
                agent = ManagerAgent(model)
                error = None
                try:
                    agent._invoke_llm([HumanMessage(content='isolated')])
                except Exception as exc:
                    error = type(exc).__name__
                out[scenario] = {'transport_attempts': len(requests), 'error_type': error,
                    'telemetry_events': agent._consume_telemetry()}
        out['missing_usage'] = token_usage_from_response(SimpleNamespace(content='no usage'))
    assert out['500']['transport_attempts'] == 2 and out['400']['transport_attempts'] == 1
    assert out['500-exhausted']['transport_attempts'] == 3 and out['read-timeout']['transport_attempts'] == 3
    dump(root / 'sdk.json', out)
    return out


def core(root):
    from fastapi.testclient import TestClient
    from agents.manager import ManagerAgent
    from backend.models import MessageRequest
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    # Actual BaseAgent model/tool invocation inside a real checkpointed graph.
    for kind in ['model', 'tool']:
        h = harness(root / ('slow-' + kind))
        entered, release = threading.Event(), threading.Event()
        call_count = []
        def slow(*_):
            call_count.append(kind)
            entered.set()
            if not release.wait(10):
                raise TimeoutError('Audit safety cap, not a product deadline')
            return SimpleNamespace(content='released') if kind == 'model' else 'released'
        agent = ManagerAgent(Mock(invoke=slow))
        if kind == 'tool':
            agent.tools = [SimpleNamespace(name='AuditSlowTool', invoke=slow)]
        def runtime(path, on_start):
            def hooked(name, state):
                on_start(name, state)
                if name == 'manager':
                    if kind == 'model':
                        agent._invoke_llm([])
                    else:
                        agent._execute_tool_result('AuditSlowTool', {}, 'audit-call')
            return h.runtime(path, hooked)
        with patch('backend.runtime.create_runtime', side_effect=runtime):
            with TestClient(h.app()) as client:
                try:
                    first = start(client)
                    assert entered.wait(20)
                    second = start(client)
                    time.sleep(.2)  # controlled observation window, not a claimed SLA
                    before = [client.get('/api/missions/' + i).json() for i in [first, second]]
                    cancel = [client.post('/api/missions/' + first + '/cancel').status_code for _ in range(2)]
                    queue = client.app.state.service.executor._work_queue.qsize()
                finally:
                    release.set()
                first_pause, second_pause = settled(client, first, 'BLOCKED'), settled(client, second, 'BLOCKED')
                approval = client.post('/api/missions/' + first + '/input', json={'values': {'approved': True}})
                completed = settled(client, first, 'COMPLETED')
                recommendations = client.get('/api/missions/' + first + '/recommendations').json()
                events = client.app.state.service.store.events(first, 0)
                out[kind] = {'first': first, 'second': second, 'before_release': before, 'cancel_http': cancel,
                    'queue_depth': queue, 'after_release_status': [first_pause['status'], second_pause['status']],
                    'approval_http': approval.status_code, 'final': completed, 'recommendations': recommendations,
                    'events': events, 'controlled_calls': len(call_count), 'graph_calls': h.calls}
                dump(h.root / 'observed.json', out[kind])
        # Restart demonstrates no accepted/persisted cancellation existed.
        with TestClient(h.app()) as client:
            out[kind]['restart_status'] = client.get('/api/missions/' + first).json()['status']

    h = harness(root / 'duplicates')
    with patch('backend.runtime.create_runtime', side_effect=h.runtime):
        with TestClient(h.app()) as client:
            service = client.app.state.service
            body = {'conversation_id': 'conv_session', 'content': 'same lost-response request', 'request_id': 'client-same'}
            enqueued = []
            with patch.object(service, '_enqueue', side_effect=lambda *args: enqueued.append(args[0])):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    answers = list(pool.map(lambda _: client.post('/api/messages', json=body).json(), range(4)))
            ids = [a['mission_id'] for a in answers]
            assert len(set(ids)) == 4
            # Run a separate valid task and retry approval after it finishes.
            identity = start(client)
            h.complete(client, identity)
            doc_before = sum(n == 'document' for n, _ in h.calls)
            duplicate_approval = client.post('/api/missions/' + identity + '/input', json={'values': {'approved': True}})
            model = Mock(invoke=Mock(return_value=SimpleNamespace(content='isolated explanation', usage_metadata={'input_tokens': 7, 'output_tokens': 4})))
            before = service.store.get(identity).telemetry.copy()
            payload = {'conversation_id': 'conv_session', 'mission_id': identity, 'intent': 'explain', 'content': 'same explanation', 'request_id': 'same-explain'}
            with patch('utils.helpers.create_llm', return_value=model):
                for _ in range(2):
                    assert client.post('/api/messages', json=payload).status_code == 202
                    settled(client, identity)
            after = service.store.get(identity).telemetry
            out['duplicates'] = {'new_mission_ids': ids, 'enqueue_count': len(enqueued),
                'server_request_fields': list(MessageRequest.model_fields), 'duplicate_approval_http': duplicate_approval.status_code,
                'document_count_before': doc_before, 'document_count_after': sum(n == 'document' for n, _ in h.calls),
                'explanation_model_calls': model.invoke.call_count, 'telemetry_before': before, 'telemetry_after': after,
                'message_counts': len(service.store.messages('conv_session')), 'task_count': len(service.store.missions())}
        with TestClient(h.app()) as client:
            with patch.object(client.app.state.service, '_enqueue'):
                retry = client.post('/api/messages', json=body).json()
            out['duplicates']['post_restart_lost_response_retry'] = retry
            out['duplicates']['post_restart_task_count'] = len(client.app.state.service.store.missions())
    dump(root / 'core.json', out)
    return out


def own_process(root, args, *, env=None, supplied=None):
    # Windows venv python.exe is a launcher; use its identical base interpreter
    # with explicit venv imports so Popen.pid is the process executing the fault.
    child_env = dict(env or os.environ)
    child_env['PYTHONPATH'] = os.pathsep.join([str(REPO / 'venv/Lib/site-packages'), str(REPO), child_env.get('PYTHONPATH', '')])
    command = [getattr(sys, '_base_executable', sys.executable), '-m', 'evaluation.m4_audit', '--child', *args]
    name = f'process-{len(list(root.glob("process-*.json")))}'
    log = (root / (name + '.log')).open('w', encoding='utf-8')
    p = subprocess.Popen(command, cwd=REPO, env=child_env, stdin=subprocess.PIPE if supplied is not None else subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
    dump(root / (name + '.json'), {'pid': p.pid, 'command': command, 'created_by': 'evaluation.m4_audit', 'started': time.time()})
    if supplied is not None:
        p.stdin.write(supplied)
        p.stdin.close()
    p.audit_log = log
    return p


def stop_owned(p):
    # Popen handle belongs to this audit, never discover/kill external processes.
    if p.poll() is None:
        p.terminate()
    p.wait(timeout=10)
    p.audit_log.close()


def child(args):
    mode, folder, *rest = args
    root = Path(folder).resolve()
    if mode == 'import-race':
        from player_data.json_repository import JsonPlayerRepository, EMPTY_VERSION
        from player_data.imports import Observation, ObservationImporter
        from player_data.models import PlayerContext
        context = PlayerContext('audit-career', 'main', 'audit-player')
        repo = JsonPlayerRepository(root / 'actual', fixture_root=root / 'fixture', context=context)
        importer = ObservationImporter(repo, source_type='game_observation', provider='audit')
        original = importer._commit
        def gate(path, candidate):
            dump(root / (rest[0] + '.ready'), {'pid': os.getpid()})
            wait_for(lambda: (root / 'release').exists(), 20)
            return original(path, candidate)
        importer._commit = gate
        result = importer.import_observation(Observation(context, rest[0], 'game_observation', 'audit',
            '2026-10-26T10:00:00+08:00', '2026-10-26', 'game', 'day', 'profile_patch',
            {'name': rest[0]}, EMPTY_VERSION, source_record_id=rest[0], game_version='isolated', adapter_version='isolated', raw_reference='isolated-source'))
        dump(root / (rest[0] + '.result.json'), asdict(result))
        return
    if mode == 'lock':
        from backend.service import MissionService
        try:
            service = MissionService(root / 'tasks')
        except Exception as exc:
            dump(root / (rest[0] + '.json'), {'pid': os.getpid(), 'error': str(exc), 'type': type(exc).__name__})
            return
        dump(root / (rest[0] + '.json'), {'pid': os.getpid(), 'acquired': True})
        if rest[0] == 'owner':
            wait_for(lambda: (root / 'release-lock').exists(), 25)
        service.close()
        return
    # Fault hooks stop at real product commit boundaries, not artificial DB replicas.
    from fastapi.testclient import TestClient
    h = harness(root)
    entered, release = threading.Event(), threading.Event()
    def runtime(path, on_start):
        def hook(name, state):
            on_start(name, state)
            if mode in ['running', 'shutdown'] and name == 'manager':
                dump(root / 'ready.json', {'pid': os.getpid(), 'stage': name})
                entered.set()
                release.wait(35)
        return h.runtime(path, hook)
    with patch('backend.runtime.create_runtime', side_effect=runtime):
        with TestClient(h.app()) as client:
            service = client.app.state.service
            if mode == 'created':
                original = service._enqueue
                def enqueue(*args):
                    dump(root / 'ready.json', {'pid': os.getpid(), 'mission_id': args[0], 'stage': 'committed-before-enqueue'})
                    release.wait(35)
                    return original(*args)
                service._enqueue = enqueue
            identity = start(client)
            dump(root / 'identity.json', {'mission_id': identity, 'pid': os.getpid()})
            if mode == 'report':
                settled(client, identity, 'BLOCKED')
                original = service.store.set_report
                def write_report(*args):
                    result = original(*args)
                    dump(root / 'ready.json', {'pid': os.getpid(), 'mission_id': identity, 'stage': 'report-committed-before-completed'})
                    release.wait(35)
                    return result
                service.store.set_report = write_report
                client.post('/api/missions/' + identity + '/input', json={'values': {'approved': True}})
            elif mode == 'shutdown':
                assert entered.wait(20)
                closer = threading.Thread(target=service.close, daemon=True)
                closer.start()
                closer.join(.3)
                dump(root / 'shutdown.json', {'pid': os.getpid(), 'close_still_blocked': closer.is_alive()})
            wait_for(lambda: (root / 'release').exists(), 30)
            release.set()


def processes(root):
    from fastapi.testclient import TestClient
    from player_data.json_repository import JsonPlayerRepository
    from player_data.models import PlayerContext
    from utils.sessions import SessionRepository
    out = {}
    for mode in ['created', 'running', 'report', 'shutdown']:
        folder = root / mode
        folder.mkdir()
        p = own_process(root, [mode, str(folder)])
        try:
            wait_for(lambda: (folder / 'ready.json').exists())
            if mode == 'shutdown':
                wait_for(lambda: (folder / 'shutdown.json').exists())
            before = json.loads((folder / 'ready.json').read_text(encoding='utf-8'))
            assert before['pid'] == p.pid
            time.sleep(.1)
        finally:
            stop_owned(p)
        # Snapshot read before recovery, mode=ro; no constructors that initialize DB.
        path = folder / 'tasks/workspace.sqlite3'
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT id,snapshot,report FROM missions').fetchall()
        h = harness(folder)
        with TestClient(h.app()) as client:
            recovered = client.get('/api/missions').json()
            out[mode] = {'own_pid': p.pid, 'exit_code': p.returncode, 'fault_boundary': before,
                'pre_restart': [{'id': i, 'snapshot': json.loads(s), 'report_present': r is not None} for i, s, r in rows],
                'after_restart': recovered,
                'public_report_http': [client.get('/api/missions/' + i + '/report').status_code for i, _, _ in rows],
                'restart_model_calls': h.calls}
        if mode == 'shutdown':
            out[mode]['shutdown'] = json.loads((folder / 'shutdown.json').read_text(encoding='utf-8'))
        dump(root / 'processes.json', out)

    folder = root / 'writer-lock'
    folder.mkdir()
    owner = own_process(root, ['lock', str(folder), 'owner'])
    try:
        wait_for(lambda: (folder / 'owner.json').exists())
        contender = own_process(root, ['lock', str(folder), 'contender'])
        try:
            contender.wait(timeout=20)
        finally:
            stop_owned(contender)
        before = sorted(p.name for p in (folder / 'tasks').iterdir())
        # Real read-only CLI repository under an independently held writer lock.
        readonly = SessionRepository(folder / 'tasks').list()
        out['writer-lock'] = {'owner': json.loads((folder / 'owner.json').read_text(encoding='utf-8')),
            'contender': json.loads((folder / 'contender.json').read_text(encoding='utf-8')), 'readonly_history': readonly,
            'readonly_added_files': sorted(set(p.name for p in (folder / 'tasks').iterdir()) - set(before))}
    finally:
        stop_owned(owner)
    reopened = own_process(root, ['lock', str(folder), 'reopened'])
    try:
        reopened.wait(timeout=20)
    finally:
        stop_owned(reopened)
    out['writer-lock']['after_owner_kill'] = json.loads((folder / 'reopened.json').read_text(encoding='utf-8'))

    folder = root / 'actual-process-race'
    folder.mkdir()
    writers = [own_process(root, ['import-race', str(folder), name]) for name in ['writer-a', 'writer-b']]
    try:
        wait_for(lambda: all((folder / (n + '.ready')).exists() for n in ['writer-a', 'writer-b']))
        (folder / 'release').touch()
        for p in writers:
            p.wait(timeout=15)
    finally:
        for p in writers:
            stop_owned(p)
    repo = JsonPlayerRepository(folder / 'actual', fixture_root=folder / 'fixture', context=PlayerContext('audit-career', 'main', 'audit-player'))
    state = repo.read_raw()
    out['actual-process-race'] = {'results': [json.loads((folder / (n + '.result.json')).read_text(encoding='utf-8')) for n in ['writer-a', 'writer-b']],
        'pids': [p.pid for p in writers], 'final_revision': state['revision'], 'final_observation_ids': [o['observation_id'] for o in state['observations']],
        'final_integrity_readable': True, 'fault_hook': 'barrier immediately before real ObservationImporter._commit; both original CAS checks completed'}
    dump(root / 'processes.json', out)
    return out


def regressions(root, extra=False):
    """Existing tests unchanged; artifacts name every test run this round."""
    import unittest
    names = ['evaluation.' + m for m in ['test_session_semantics', 'test_reevaluation', 'test_player_imports',
        'test_player_data_boundary', 'test_result_invalidation', 'test_agent_output_validation', 'test_looping_plan',
        'test_recommendation_actions', 'test_execution_outcomes', 'test_feedback_followups', 'test_career_memory',
        'test_memory_retrieval', 'test_assessments']]
    names += ['evaluation.test_cli_persistence.CLIPersistenceTests.' + n for n in [
        'test_missing_input_then_approval_survive_three_distinct_processes',
        'test_completed_history_is_read_only_without_configuration_or_latest_access',
        'test_invalid_missing_checkpoint_or_snapshot_refuses_before_any_mutation',
        'test_one_writer_guard_blocks_other_process_but_allows_read_only_history',
        'test_failed_cli_task_retry_creates_child_and_never_resumes_original']]
    if extra:
        names = ['evaluation.' + m for m in ['test_finishing_assessment', 'test_player_repository',
            'test_dependency_context', 'test_recommendations', 'test_stage3_audit', 'test_web']]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    cases = []
    class Recorded(unittest.TextTestResult):
        def startTest(self, test):
            self.when = time.monotonic()
            super().startTest(test)
        def stopTest(self, test):
            cases.append({'id': test.id(), 'seconds': round(time.monotonic() - self.when, 3)})
            super().stopTest(test)
    with (root / 'unittest.log').open('w', encoding='utf-8') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Recorded).run(suite)
    out = {'tests_run': result.testsRun, 'successful': result.wasSuccessful(),
        'failures': [(t.id(), s) for t, s in result.failures], 'errors': [(t.id(), s) for t, s in result.errors],
        'skips': [(t.id(), s) for t, s in result.skipped], 'cases': cases}
    dump(root / 'regressions.json', out)
    return {k: v for k, v in out.items() if k != 'cases'}


def main():
    if '--child' in sys.argv:
        return child(sys.argv[sys.argv.index('--child') + 1:])
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--parts', nargs='+', choices=['sdk', 'core', 'processes', 'regressions', 'regressions-extra'], default=['sdk', 'core', 'processes', 'regressions'])
    args = parser.parse_args()
    root = Path(args.root).resolve()
    assert root.is_relative_to(REPO / 'local_data/game_imports/m4-audit'), 'Use a new isolated audit root'
    root.mkdir(parents=True, exist_ok=False)
    temp = root / 'temp'
    temp.mkdir()
    os.environ.update(TEMP=str(temp), TMP=str(temp), PYTHONUTF8='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    # Don't replace real configuration files. All runtime factories are patched locally.
    baseline = {'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'git_status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=REPO, text=True),
        'python': sys.version, 'executable': sys.executable,
        'versions': {p: metadata.version(p) for p in ['openai', 'langchain-openai', 'langgraph', 'langgraph-checkpoint-sqlite', 'fastapi', 'httpx', 'tavily-python']},
        'env_file_exists': (REPO / '.env').is_file(),
        'config_provenance': 'config.py load_dotenv(override=False): pre-existing process env wins, then .env, then defaults. Model factory overridden for tests; no secret values recorded.',
        'inherited_config_keys_present': sorted(k for k in os.environ if k.startswith('FAIT_') or k in ['OPENAI_API_KEY', 'OPENAI_BASE_URL', 'MODEL_NAME', 'TAVILY_API_KEY']),
        'protected_hashes': protected_hashes(root)}
    dump(root / 'baseline.json', baseline)
    results = {}
    for name, fn in [('sdk', sdk), ('core', core), ('processes', processes), ('regressions', regressions), ('regressions-extra', lambda r: regressions(r, extra=True))]:
        if name not in args.parts:
            continue
        folder = root / name
        folder.mkdir()
        print('START ' + name, flush=True)
        try:
            results[name] = fn(folder)
            print('DONE ' + name, flush=True)
        except Exception:
            results[name] = {'harness_error': traceback.format_exc()}
            print('HARNESS ERROR ' + name + '\n' + traceback.format_exc(), flush=True)
        dump(root / 'summary.json', results)
    after = protected_hashes(root)
    changed = sorted(k for k in set(after) | set(baseline['protected_hashes']) if after.get(k) != baseline['protected_hashes'].get(k))
    dump(root / 'protected-check.json', {'files_checked_before': len(baseline['protected_hashes']), 'files_checked_after': len(after), 'changed': changed})
    print(json.dumps({'root': str(root), 'protected_changed': changed, 'parts': list(results)}, ensure_ascii=False), flush=True)
    if changed or any('harness_error' in r or r.get('successful') is False for r in results.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
