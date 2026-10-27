"""Real loopback HTTP/SSE disconnect, replay and repeated resume audit."""
import argparse
import json
import os
from pathlib import Path
import socket
import threading
import time
from unittest.mock import patch
import httpx
import uvicorn
from evaluation.m4_audit import REPO, dump, harness, wait_for, protected_hashes


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    root = Path(p.parse_args().root).resolve()
    assert root.is_relative_to(REPO / 'local_data/game_imports/m4-audit')
    root.mkdir(parents=True, exist_ok=False)
    (root / 'temp').mkdir()
    os.environ.update(TEMP=str(root / 'temp'), TMP=str(root / 'temp'), PYTHONUTF8='1')
    before = protected_hashes(root)
    h = harness(root / 'http', require_input=True)
    model_enter, model_release = threading.Event(), threading.Event()
    doc_enter, doc_release = threading.Event(), threading.Event()
    def runtime(path, on_start):
        def hook(name, state):
            on_start(name, state)
            if name == 'manager':
                model_enter.set()
                if not model_release.wait(15):
                    raise TimeoutError('Audit-only safety cap')
            if name == 'document':
                doc_enter.set()
                if not doc_release.wait(15):
                    raise TimeoutError('Audit-only safety cap')
        return h.runtime(path, hook)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(h.app(), host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    output = {'host': '127.0.0.1', 'port': port, 'transport': 'real uvicorn + loopback httpx; deterministic graph nodes'}
    with patch('backend.runtime.create_runtime', side_effect=runtime):
        thread.start()
        try:
            wait_for(lambda: server.started)
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=5) as c:
                response = c.post('/api/messages', json={'conversation_id': 'conv_session', 'content': 'transport audit'})
                assert response.status_code == 202, response.text
                identity = response.json()['mission_id']
                base = '/api/missions/' + identity
                assert model_enter.wait(15)
                def read_one(cursor=0, header=None):
                    headers = {'Last-Event-ID': str(header)} if header is not None else {}
                    with c.stream('GET', base + f'/events?after={cursor}', headers=headers) as stream:
                        assert stream.status_code == 200
                        for line in stream.iter_lines():
                            if line.startswith('data: '):
                                return json.loads(line[6:])
                    raise AssertionError('No SSE event')
                first = read_one()
                output['first_event'] = first
                output['after_disconnect'] = c.get(base).json()
                output['active_after_disconnect'] = identity in server.config.app.state.service.active
                output['cancel_running_http'] = c.post(base + '/cancel').status_code
                model_release.set()
                def paused():
                    v = c.get(base).json()
                    return v if v['status'] == 'BLOCKED' and identity not in server.config.app.state.service.active else None
                view = wait_for(paused)
                assert view['blocked']['reason'] == 'missing_user_input'
                output['cancel_blocked_http'] = c.post(base + '/cancel').status_code
                field = view['blocked']['required_inputs'][0]['key']
                body = {'values': {field: 'audit user feedback'}}
                output['input_first_http'] = c.post(base + '/input', json=body).status_code
                wait_for(paused)
                output['input_replay_http'] = c.post(base + '/input', json=body).status_code
                approved = {'values': {'approved': True}}
                output['approval_first_http'] = c.post(base + '/input', json=approved).status_code
                assert doc_enter.wait(15)
                output['approval_concurrent_http'] = c.post(base + '/input', json=approved).status_code
                doc_release.set()
                final = wait_for(lambda: (v if (v := c.get(base).json())['status'] == 'COMPLETED' and identity not in server.config.app.state.service.active else None))
                output['approval_terminal_replay_http'] = c.post(base + '/input', json=approved).status_code
                output['cancel_completed_http'] = c.post(base + '/cancel').status_code
                service = server.config.app.state.service
                events = service.store.events(identity, 0)
                last = events[-1]['sequence']
                replay = read_one(0, last - 1)
                output.update(final=final, replay=replay, last_sequence=last, events=events, calls=h.calls,
                    document_count=sum(n == 'document' for n, _ in h.calls),
                    resume_message_count=sum(m.kind == 'resume' for m in service.store.messages('conv_session')))
                assert replay['sequence'] == last and replay['data']['snapshot']['status'] == 'COMPLETED'
                assert output['document_count'] == 1 and output['resume_message_count'] == 2
        finally:
            model_release.set()
            doc_release.set()
            server.should_exit = True
            thread.join(10)
            sock.close()
            assert not thread.is_alive(), 'Own audit HTTP server did not stop'
    dump(root / 'transport.json', output)
    after = protected_hashes(root)
    dump(root / 'protected-check.json', {'files_checked': len(before), 'changed': sorted(k for k in set(after) | set(before) if after.get(k) != before.get(k))})
    print(json.dumps({k: v for k, v in output.items() if k.endswith('_http') or k in ['document_count', 'resume_message_count', 'last_sequence', 'active_after_disconnect']}))


if __name__ == '__main__':
    main()
