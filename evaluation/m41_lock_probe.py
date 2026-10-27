"""Independent-process actual/collector writer fixture; synthetic observations only."""
import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from backend.process_supervisor import await_start
from evaluation.m4_process_probe import write


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['hold-import', 'import', 'read', 'collector-hold', 'bind'])
    p.add_argument('root', type=Path)
    p.add_argument('label')
    p.add_argument('--expected', default='EMPTY')
    p.add_argument('--branch', default='main')
    p.add_argument('--tasks')
    args = p.parse_args()
    root = args.root.resolve()
    repo_root = Path(__file__).resolve().parents[1]
    if not root.is_relative_to(repo_root / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Probe requires a new isolated actual root')
    await_start()
    from player_data.json_repository import JsonPlayerRepository
    from player_data.models import PlayerContext, PlayerDataError
    from player_data.imports import Observation, ObservationImporter
    context = PlayerContext('m41-career', args.branch, 'player')
    repo = JsonPlayerRepository(root / 'actual', fixture_root=root / 'fixture', context=context)
    service = None
    if args.tasks:
        from backend.service import MissionService
        service = MissionService(root / args.tasks, player_repository=repo, player_context=context)
    def gate():
        write(root / (args.label + '.ready.json'), {'pid': os.getpid(), 'mode': args.mode, 'tasks': args.tasks})
        end = time.monotonic() + 40
        while not (root / (args.label + '.release')).exists():
            if time.monotonic() > end:
                raise TimeoutError('Isolated probe safety cap expired')
            time.sleep(.02)
    try:
        if args.mode == 'read':
            result = {'snapshot': repo.read_snapshot().to_dict()}
        elif args.mode in {'import', 'hold-import'}:
            importer = ObservationImporter(repo, source_type='game_observation', provider='m41')
            if args.mode == 'hold-import':
                original = importer._commit
                def commit(path, candidate):
                    gate()
                    return original(path, candidate)
                importer._commit = commit
            observation = Observation(context, args.label, 'game_observation', 'm41',
                '2026-10-26T10:00:00+08:00',
                {'seed': '2026-10-01', 'writer_a': '2026-10-02', 'writer_b': '2026-10-03',
                 'owner': '2026-10-02', 'interrupted': '2026-10-02', 'after_kill': '2026-10-03'}.get(args.label, '2026-10-26'),
                'game', 'day', 'profile_patch',
                {'name': args.label}, args.expected, source_record_id=args.label,
                game_version='synthetic-v1', adapter_version='synthetic-v1', raw_reference='audit-only:' + args.label)
            result = asdict(importer.import_observation(observation))
        else:
            from game_adapters.pes2021_import import Collector, atomic_json
            from game_adapters.test_pes2021_import import fixture_report
            collector = Collector(repo)
            report = fixture_report()
            with patch('game_adapters.pes2021_import.read_package', side_effect=lambda _: deepcopy(report)):
                if args.mode == 'bind':
                    result = collector.bind(root / 'synthetic-dataset', context, basis='Explicit isolated synthetic confirmation')
                else:
                    collector.bind(root / 'synthetic-dataset', context, basis='Explicit isolated synthetic confirmation')
                    plan = collector.preview(root / 'synthetic-dataset')
                    def receipt(path, value):
                        if path.parent.name == 'receipts':
                            gate()
                        return atomic_json(path, value)
                    with patch('game_adapters.pes2021_import.atomic_json', side_effect=receipt):
                        result = collector.commit(root / 'synthetic-dataset', plan['preview_token'])
        write(root / (args.label + '.result.json'), {'pid': os.getpid(), 'result': result})
    except PlayerDataError as exc:
        write(root / (args.label + '.result.json'), {'pid': os.getpid(), 'result': {'status': 'REJECTED', 'reason': exc.code}})
    finally:
        if service:
            service.close()


if __name__ == '__main__':
    main()
