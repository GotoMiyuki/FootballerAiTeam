"""Quiescent local backup, verification and restore into a new candidate directory.

Run python -m backend.backup --help. No model, game or automatic activation.
"""
import argparse
from contextlib import closing, ExitStack
from dataclasses import fields
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace

from backend.models import MissionView
from backend.runtime import inspect_checkpoint
from backend.storage_contracts import validate_database, checkpoint_writer
from backend.store import Store
from backend.task_context import validate_inputs, validate_pause
from backend.writer_lock import WorkspaceWriterLock
from game_adapters.source_paths import SourceMap, directory_files, file_hash, local_file, path_key, tree_hash
from player_data.json_repository import JsonPlayerRepository
from player_data.models import PlayerContext, content_hash
from player_data.writer_lock import actual_writer

REPO = Path(__file__).resolve().parents[1]
DEPENDENCIES = ('langgraph', 'langgraph-checkpoint-sqlite')
LABEL = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
SETTINGS = {'MODEL_NAME', 'EMBEDDING_MODEL', 'RERANK_MODEL', 'RERANK_ENABLED',
    'RAG_RETRIEVAL_K', 'RAG_TOP_K', 'RAG_CHUNK_SIZE', 'RAG_CHUNK_OVERLAP',
    'MAX_CONVERSATION_TURNS', 'SHORT_MEMORY_SIZE', 'DEBUG'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    from game_adapters.pes2021_import import atomic_json
    atomic_json(Path(path), value)


def validate_settings(settings):
    from backend.runtime_contracts import RuntimePolicy
    names = {'FAIT_RUNTIME_' + name.upper() for name in RuntimePolicy.__dataclass_fields__}
    if not isinstance(settings, dict) or set(settings) - SETTINGS - names:
        raise ValueError('settings只接受非秘密模型/RAG/运行预算配置；不接受密钥或.env')
    if any(not isinstance(v, (str, int, float, bool)) for v in settings.values()):
        raise ValueError('配置值必须为简单值')
    RuntimePolicy.from_env({key: str(value) for key, value in settings.items() if key in names})


def readonly(path, kind):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        validate_database(db, kind)
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('SQLite 完整性检查失败')
        return db
    except BaseException:
        db.close()
        raise


def sql_snapshot(source, destination):
    deadline = time.monotonic() + 30
    def progress(*_):
        if time.monotonic() > deadline:
            raise ValueError('SQLite 备份等待超时；请确认已停写')
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as src, \
            closing(sqlite3.connect(destination)) as dst:
        src.backup(dst, pages=256, progress=progress, sleep=.02)
        dst.execute('PRAGMA journal_mode=DELETE')


def contexts_in(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'context' and isinstance(item, dict) and {'career_id', 'branch_id', 'player_id'} <= item.keys():
                yield PlayerContext(**{k: item[k] for k in ('career_id', 'branch_id', 'player_id')})
            yield from contexts_in(item)
    elif isinstance(value, list):
        for item in value: yield from contexts_in(item)


def native_refs(value):
    if isinstance(value, str):
        if value.startswith('{'):
            try: yield from native_refs(json.loads(value))
            except json.JSONDecodeError: pass
    elif isinstance(value, dict):
        if value.get('mapping') == 'pes2021-canonical-v1' and 'dataset' in value:
            yield value
        for item in value.values(): yield from native_refs(item)
    elif isinstance(value, list):
        for item in value: yield from native_refs(item)


def inventory(actual, tasks):
    """Read stored authority only; never instantiate a service or create tables."""
    repository = JsonPlayerRepository(actual, fixture_root=actual.parent / ('.backup-fixture-' + content_hash(str(actual))[:12]))
    context_rows, required_contexts, native, generic = {}, set(), [], set()
    from player_data.imports import Observation
    observation_fields = {f.name for f in fields(Observation)} - {'collected_at', 'expected_state_version'}
    for path in actual.glob('contexts/*/*/*/state.json'):
        value = load(path)
        context = PlayerContext(**value['context'])
        if repository.state_path(context) != path.resolve(): raise ValueError('实际数据的路径与context不一致')
        state = repository.read_state(context)
        context_rows[content_hash(context.to_dict())] = {'context': context.to_dict(), 'state_version': state['state_version'],
            'snapshot_id': state['snapshot_id'], 'observations': len(state['observations'])}
        for row in state['observations']:
            if row['semantic_hash'] != content_hash({k: row[k] for k in observation_fields}):
                raise ValueError('观察语义摘要与原记录不一致')
            refs = list(native_refs(row.get('raw_reference')))
            native.extend(refs)
            if row['source_type'] == 'game_observation' and not refs:
                if row['provider'] == 'pes2021_native' or not row.get('raw_reference'):
                    raise ValueError('游戏观察原始来源缺失或不兼容')
                generic.add(row['raw_reference'])
    committed_contexts = set(context_rows)
    from game_adapters.pes2021_import import Collector
    for binding in Collector(repository, source_map=False).registry()['bindings'].values():
        context = PlayerContext(**binding['context'])
        context_rows.setdefault(content_hash(context.to_dict()), {'context': context.to_dict(),
            'state_version': 'EMPTY', 'snapshot_id': None, 'observations': 0})
    task_rows = {}
    for label, root in sorted(tasks.items()):
        with closing(readonly(root / 'workspace.sqlite3', 'workspace')) as db:
            store = Store.__new__(Store); store.db, store.lock = db, threading.RLock()
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            counts = {t: db.execute('SELECT count(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0] for t in tables}
            operations = store.operations() if 'operations' in tables else []
            for op in operations:
                if op.mission_id not in {m.id for m in store.missions()}: raise ValueError('操作缺少所属Mission')
            for table in tables:
                columns = {r[1] for r in db.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')}
                if 'payload' in columns:
                    for (payload,) in db.execute('SELECT payload FROM "' + table.replace('"', '""') + '"'):
                        value = json.loads(payload)
                        required_contexts.update(content_hash(c.to_dict()) for c in contexts_in(value))
                        native.extend(native_refs(value))
            cp = root / 'checkpoints.sqlite3'
            if cp.exists():
                with closing(readonly(cp, 'checkpoint')): pass
            mission_rows = {}
            from backend.service import MissionService
            validator = SimpleNamespace(store=store)
            for mission in store.missions():
                if mission.input_reference.verification != 'VERIFIED':
                    raise ValueError('本备份单元只支持有完整固定输入的actual任务；demo与旧CLI索引须单独保留')
                inputs = validate_inputs(mission, store.inputs(mission.id))
                required_contexts.add(content_hash(mission.input_reference.context))
                native.extend(native_refs(inputs))
                MissionService.frozen_memory(validator, mission.id)
                MissionService.frozen_feedback(validator, mission.id)
                report = store.report(mission.id)
                needs_cp = bool(mission.blocked or mission.report or report)
                if 'recommendations' in tables:
                    needs_cp |= bool(db.execute('SELECT 1 FROM recommendations WHERE mission_id=? LIMIT 1', (mission.id,)).fetchone())
                snapshot = inspect_checkpoint(cp, mission.id) if cp.exists() else None
                if needs_cp and (not snapshot or not snapshot.values): raise ValueError('任务依赖的checkpoint缺失')
                if mission.status == 'BLOCKED':
                    if mission.workflow_operation_id:
                        op = store.operation(mission.workflow_operation_id)
                        if (op.mission_id != mission.id or op.kind != 'workflow' or op.status != 'PAUSED'
                                or op.pause_id != mission.blocked.pause_id): raise ValueError('暂停操作身份不一致')
                    validate_pause(mission, snapshot, inputs=inputs)
                if mission.status == 'COMPLETED' and mission.report:
                    from execution_contracts import publishable
                    if (not publishable(snapshot.values) or snapshot.values.get('final_report') != report
                            or any((snapshot.values.get(k) or {}) != (inputs.get(k) or {})
                                for k in ('player_snapshot', 'continuation_context', 'career_memory_context'))):
                        raise ValueError('报告、当前成果或固定材料不一致')
                mission_rows[mission.id] = {'status': mission.status, 'pause': mission.blocked.reason if mission.blocked else None,
                    'input_hash': store.inputs(mission.id)['content_hash'], 'report_sha256': content_hash(report) if report else None}
            task_rows[label] = {'tables': counts, 'missions': mission_rows,
                'operations': [{'id': o.id, 'status': o.status, 'run_id': o.run_id} for o in operations]}
    if required_contexts - committed_contexts: raise ValueError('任务/E/C引用的context未包含在actual根内')
    return {'contexts': sorted(context_rows.values(), key=lambda r: content_hash(r['context'])), 'tasks': task_rows}, native, generic


def source_inventory(actual, refs, generic, declared, files, mapping=None):
    from game_adapters.pes2021_native_package import read_package
    from game_adapters.pes2021_import import Collector
    datasets = {path_key(r['dataset']): str(Path(r['dataset']).resolve()) for r in refs}
    datasets.update({path_key(p): str(Path(p).resolve()) for p in declared})
    rows, reports, physicals = [], {}, {}
    for key, logical in sorted(datasets.items()):
        if mapping:
            original, physical, raw_root = mapping.dataset(logical)
            report = read_package(physical, raw_root=raw_root)
        else:
            physical = Path(logical); report = read_package(physical)
        if report['purpose'] != 'real_test': raise ValueError('actual来源必须为已审阅real_test原生包')
        if any(Path(p).name.startswith('.env') for p in directory_files(physical)):
            raise ValueError('原生来源目录内含秘密配置，不能打包')
        relative = 'sources/real_test/' + content_hash(logical)[:20]
        rows.append({'original': logical, 'path': relative, 'tree_sha256': tree_hash(physical),
            'identity': report['identity'], 'adapter_version': report['adapter_version']})
        reports[key], physicals[relative] = report, physical
    for reference in refs:
        physical = physicals[next(r['path'] for r in rows if path_key(r['original']) == path_key(reference['dataset']))]
        for source in reference['sources']:
            if file_hash(local_file(physical, source['path'])) != source['sha256']:
                raise ValueError('历史原生来源摘要不一致')
    repository = JsonPlayerRepository(actual, fixture_root=actual.parent / '.backup-unrelated-fixture')
    registry = Collector(repository, source_map=False).registry()
    for key, binding in registry['bindings'].items():
        candidates = [r for r in rows if r['identity'] == binding['source_identity']]
        if key != content_hash(binding['source_identity']) or not candidates:
            raise ValueError('collector绑定缺少可核验的原生来源')
        context = PlayerContext(**binding['context'])
        repository.read_state(context, allow_empty=True)
        baseline = binding['source_baseline']
        if not any(file_hash(local_file(physicals[r['path']], baseline['path'])) == baseline['sha256'] for r in candidates):
            raise ValueError('collector绑定原始基线缺失')
    file_rows = []
    for reference in sorted(generic):
        if reference not in files: raise ValueError('缺少file_sources显式来源：' + reference)
        source = Path(files[reference]).resolve(strict=True)
        if source.name.startswith('.env'): raise ValueError('文件来源不能指向秘密配置文件')
        relative = 'files/' + content_hash(reference)[:20]
        file_rows.append({'reference': reference, 'path': relative, 'sha256': file_hash(source)})
        physicals[relative] = source
    return rows, file_rows, physicals


def archive_files(root):
    return {name: digest for name, digest in directory_files(root).items()
        if name not in {'manifest.json', 'READY.json'}}


def validate_payload(root, manifest):
    actual = local_file(root, manifest['actual'])
    tasks = {name: local_file(root, relative) for name, relative in manifest['tasks'].items()}
    observed, refs, generic = inventory(actual, tasks)
    if observed != manifest['inventory']: raise ValueError('归档身份、版本或任务记录与清单不一致')
    temporary_map = {'schema_version': 1, 'raw_root': 'sources', 'datasets': manifest['native_datasets']}
    # SourceMap uses a real file only for the public runtime entry; no writes are needed here.
    mapping = SourceMap.__new__(SourceMap)
    mapping.base, mapping.raw_root, mapping.value = root, root / 'sources', temporary_map
    files = {r['reference']: local_file(root, r['path']) for r in manifest['file_sources']}
    datasets, sources, _ = source_inventory(actual, refs, generic,
        [r['original'] for r in manifest['native_datasets']], files, mapping)
    if datasets != manifest['native_datasets'] or sources != manifest['file_sources']:
        raise ValueError('来源路径映射与清单不一致')
    return observed


def verify(root):
    root = Path(root).resolve(strict=True)
    manifest, ready = load(root / 'manifest.json'), load(root / 'READY.json')
    if manifest.get('schema_version') != 1 or ready.get('schema_version') != 1 or ready.get('manifest_sha256') != file_hash(root / 'manifest.json'):
        raise ValueError('备份清单版本或完成摘要不一致')
    if set(manifest['dependencies']) != set(DEPENDENCIES): raise ValueError('备份依赖声明不兼容')
    validate_settings(manifest['settings'])
    for package, saved in manifest['dependencies'].items():
        if version(package) != saved: raise ValueError('checkpoint依赖版本不一致，请使用清单对应环境')
    if archive_files(root) != manifest['files']: raise ValueError('备份文件缺失、多出或摘要不一致')
    validate_payload(root, manifest)
    return manifest


def backup(spec, output):
    if spec.get('schema_version') != 1 or not spec.get('tasks'): raise ValueError('需要v1备份单元及显式tasks目录')
    actual = Path(spec['actual_root']).resolve(strict=True)
    tasks = {name: Path(path).resolve(strict=True) for name, path in spec['tasks'].items()}
    if any(not LABEL.fullmatch(name) for name in tasks): raise ValueError('任务目录标签无效')
    output = Path(output).resolve()
    roots = [actual, *tasks.values()]
    for n, root in enumerate(roots):
        if not root.is_dir() or any(root.is_relative_to(p) or p.is_relative_to(root) for p in roots[:n]):
            raise ValueError('actual与任务目录必须独立、不重叠')
        if output.is_relative_to(root) or root.is_relative_to(output): raise ValueError('备份目录不能与源目录重叠')
    if output.exists(): raise ValueError('备份输出必须是全新目录')
    from backend.runtime_contracts import RuntimePolicy
    from config import config
    from dataclasses import asdict
    settings = {name: getattr(config, name) for name in SETTINGS}
    settings.update({'FAIT_RUNTIME_' + key.upper(): value for key, value in asdict(RuntimePolicy.from_env()).items()})
    settings.update(spec.get('settings', {}))
    validate_settings(settings)
    with ExitStack() as locks:
        for _, root in sorted(tasks.items(), key=lambda row: path_key(row[1])):
            locks.enter_context(closing(WorkspaceWriterLock(root)))
        for _, root in sorted(tasks.items(), key=lambda row: path_key(row[1])):
            locks.enter_context(checkpoint_writer(root / 'checkpoints.sqlite3'))
        locks.enter_context(actual_writer(actual))
        expected, refs, generic = inventory(actual, tasks)
        mapping = SourceMap(spec['source_map']) if spec.get('source_map') else None
        source_files = {}
        if mapping:
            for row in mapping.value.get('files', []):
                path = local_file(mapping.base, row['path'])
                if file_hash(path) != row['sha256']: raise ValueError('映射中的文件来源摘要不一致')
                source_files[row['reference']] = path
        source_files.update(spec.get('file_sources', {}))
        native, files, physicals = source_inventory(actual, refs, generic, spec.get('native_datasets', []), source_files, mapping)
        assets = {}
        for name, value in spec.get('assets', {}).items():
            if not LABEL.fullmatch(name): raise ValueError('附加资料标签无效')
            source = Path(value).resolve(strict=True)
            relative = 'assets/' + name
            assets[name] = relative; physicals[relative] = source
            if source.is_dir() and any(Path(p).name.startswith('.env') for p in directory_files(source)):
                raise ValueError('附加资料中含秘密配置，不能打包')
            if source.is_file() and source.name.startswith('.env'): raise ValueError('不能打包秘密配置文件')
        for source in physicals.values():
            if output.is_relative_to(source) or source.is_relative_to(output): raise ValueError('输出不能与来源或附加资料重叠')
        source_digests = {name: tree_hash(source) if source.is_dir() else file_hash(source) for name, source in physicals.items()}
        output.mkdir(parents=True, exist_ok=False)
        try:
            (output / 'actual').mkdir()
            # Actual aggregates/bindings/receipts are copied byte for byte, with all contexts.
            for relative in directory_files(actual, skip_suffixes=('.lock', '.tmp')):
                if Path(relative).name.startswith('.env'): raise ValueError('actual根内存在秘密配置文件，不能打包')
                target = local_file(output / 'actual', relative); target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(actual / relative, target)
            for name, root in tasks.items():
                target = output / 'tasks' / name; target.mkdir(parents=True)
                sql_snapshot(root / 'workspace.sqlite3', target / 'workspace.sqlite3')
                if (root / 'checkpoints.sqlite3').exists(): sql_snapshot(root / 'checkpoints.sqlite3', target / 'checkpoints.sqlite3')
            for relative, source in physicals.items():
                target = local_file(output, relative)
                if source.is_dir(): shutil.copytree(source, target)
                else: target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target)
            for relative in spec.get('legacy_metadata', {}):
                if not LABEL.fullmatch(relative): raise ValueError('旧索引标签无效')
                source = Path(spec['legacy_metadata'][relative]).resolve(strict=True)
                if not isinstance(load(source), list): raise ValueError('旧CLI元数据必须为JSON列表')
                target = output / 'legacy' / (relative + '.json'); target.parent.mkdir(exist_ok=True)
                shutil.copy2(source, target)
            manifest = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'strategy': 'all_declared_writers_locked_then_sqlite_backup', 'actual': 'actual',
                'tasks': {name: 'tasks/' + name for name in tasks}, 'original_roots': {'actual': str(actual), 'tasks': {k: str(v) for k, v in tasks.items()}},
                'native_datasets': native, 'file_sources': files, 'inventory': expected, 'settings': settings,
                'assets': assets, 'external_runtime': {'knowledge_directory': str(config.KNOWLEDGE_DIR),
                    'embedding_model_directory': str(config.EMBEDDING_MODEL_DIR), 'rerank_model_directory': str(config.RERANK_MODEL_DIR),
                    'note': 'Code/model credentials and undeclared external resources are supplied separately; assets are explicit preserved copies.'},
                'dependencies': {p: version(p) for p in DEPENDENCIES},
                'code_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
                'code_sha256': {str(p.relative_to(REPO)).replace('\\', '/'): file_hash(p) for folder in
                    ['backend', 'player_data', 'career_actions', 'career_memory', 'game_adapters'] for p in (REPO / folder).glob('*.py')},
                'files': archive_files(output)}
            validate_payload(output, manifest)
            if inventory(actual, tasks)[0] != expected: raise ValueError('备份期间源业务状态发生变化')
            if any((tree_hash(source) if source.is_dir() else file_hash(source)) != source_digests[name]
                    for name, source in physicals.items()): raise ValueError('备份期间来源或附加资料发生变化')
            save(output / 'manifest.json', manifest)
            save(output / 'READY.json', {'schema_version': 1, 'manifest_sha256': file_hash(output / 'manifest.json')})
        except BaseException:
            # An incomplete output is retained for inspection and never has READY.json.
            raise
    return {'status': 'BACKUP_READY', 'path': str(output), 'contexts': expected['contexts'], 'tasks': list(tasks)}


def restore(archive, output):
    archive, output = Path(archive).resolve(strict=True), Path(output).resolve()
    if output.exists() or output.is_relative_to(archive) or archive.is_relative_to(output):
        raise ValueError('恢复目标必须是与备份独立的全新候选目录')
    manifest = verify(archive)
    original_roots = [manifest['original_roots']['actual'], *manifest['original_roots']['tasks'].values(),
        *[r['original'] for r in manifest['native_datasets']]]
    if any(output.is_relative_to(Path(p).resolve()) or Path(p).resolve().is_relative_to(output) for p in original_roots):
        raise ValueError('恢复候选不能与原actual、任务或原生来源目录重叠')
    output.mkdir(parents=True, exist_ok=False)
    try:
        for relative in manifest['files']:
            target = local_file(output, relative); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_file(archive, relative), target)
        if directory_files(output) != manifest['files']: raise ValueError('恢复复制后的文件摘要不一致')
        validate_payload(output, manifest)
        save(output / 'source-map.json', {'schema_version': 1, 'raw_root': 'sources',
            'datasets': manifest['native_datasets'], 'files': manifest['file_sources']})
        configuration = {'FAIT_PLAYER_DATA_MODE': 'actual', 'FAIT_PLAYER_DATA_ROOT': str(output / 'actual'),
            'FAIT_NATIVE_SOURCE_MAP': str(output / 'source-map.json'), 'settings': manifest['settings'],
            'task_directories': {name: str(local_file(output, path)) for name, path in manifest['tasks'].items()},
            'contexts': manifest['inventory']['contexts'], 'activation': 'manual_only; select an explicit task directory and context'}
        configuration['assets'] = {name: str(local_file(output, path)) for name, path in manifest.get('assets', {}).items()}
        configuration['external_runtime'] = manifest['external_runtime']
        save(output / 'restored-config.json', configuration)
        save(output / 'RESTORE_READY.json', {'schema_version': 1, 'manifest_sha256': file_hash(archive / 'manifest.json'),
            'activated': False, 'inventory': manifest['inventory']})
    except BaseException as error:
        save(output / 'RESTORE_FAILED.json', {'status': 'REFUSED', 'error_type': type(error).__name__})
        raise
    return {'status': 'RESTORE_READY', 'path': str(output), 'activated': False, 'configuration': configuration}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create'); create.add_argument('--spec', type=Path, required=True); create.add_argument('--output', type=Path, required=True)
    check = commands.add_parser('verify'); check.add_argument('archive', type=Path)
    recover = commands.add_parser('restore'); recover.add_argument('archive', type=Path); recover.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'create': result = backup(load(args.spec), args.output)
        elif args.command == 'restore': result = restore(args.archive, args.output)
        else:
            manifest = verify(args.archive); result = {'status': 'VERIFIED', 'inventory': manifest['inventory']}
        print(json.dumps(result, ensure_ascii=True)); return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({'status': 'REFUSED', 'reason': str(error)}, ensure_ascii=True)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
