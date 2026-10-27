"""Business publication can commit before accounting finalization; restart must close that gap."""
from contextlib import closing
import os
from pathlib import Path
import time
import unittest

from fastapi.testclient import TestClient
from backend.operation_metrics import runtime_view
from backend.process_supervisor import OwnedProcess
from evaluation import test_m46_recovery as recovery
from evaluation import test_session_semantics as semantics


class FinalizationRecoveryTests(unittest.TestCase):
    setUp = recovery.RecoveryTests.setUp
    app = recovery.RecoveryTests.app

    def test_completed_and_paused_unsettled_segments_stop_execution_clock(self):
        with TestClient(self.app()) as client:
            complete = self.h.start(client); self.h.complete(client, complete)
            paused = self.h.start(client); self.h.wait(client, paused, 'BLOCKED')
            service = client.app.state.service
            report = service.store.report(complete)
            originals = {}
            for mid in [complete, paused]:
                op = service.store.operation(service.store.get(mid).workflow_operation_id)
                originals[mid] = op.execution_seconds
                op.run_started_at, op.closed_at = op.created_at, None
                service.store.save_operation(op)
        with TestClient(self.app()) as client:
            service = client.app.state.service
            for mid in [complete, paused]:
                op = service.store.operation(service.store.get(mid).workflow_operation_id)
                self.assertIsNone(op.run_started_at)
                self.assertFalse(op.budget_history_complete)
                self.assertFalse(op.timing_history_complete)
                self.assertEqual(op.execution_seconds, originals[mid])
                timing = runtime_view(service.store, mid)['operations'][0]['timing']
                self.assertIsNone(timing['execution_seconds'])
                self.assertEqual(timing['known_execution_seconds'], originals[mid])
            self.assertEqual(service.store.report(complete), report)
            self.assertEqual(service.store.get(paused).status, 'BLOCKED')
            self.records.append({'terminal_and_pause_preserved': True, 'unfinished_time_unknown': True,
                'execution_live_projection_stopped': True, 'known_lower_bounds_preserved': True})


class PublishedProcessTailTests(unittest.TestCase):
    setUp = recovery.ProcessRecoveryTests.setUp
    wait_file = recovery.ProcessRecoveryTests.wait_file
    reopen = recovery.ProcessRecoveryTests.reopen

    def test_real_published_owner_crash_closes_accounting_without_revoking_delivery(self):
        root = self.root
        with (root / 'owner.log').open('wb') as log, OwnedProcess('evaluation.m46_owner', root, 'published', 'kill',
                root=recovery.REPO, output=log) as owner:
            ready = self.wait_file(root / 'ready.json', owner)
            owner.process.kill(); owner.wait(3)
            with closing(self.reopen(root)) as service:
                op = service.store.operation(ready['operation_id'])
                self.assertEqual(op.status, 'COMPLETED')
                self.assertIsNone(op.run_started_at)
                self.assertFalse(op.timing_history_complete)
                self.assertFalse(op.budget_history_complete)
                report = service.store.report(ready['mission_id'])
                self.assertTrue(report)
                view = runtime_view(service.store, ready['mission_id'])
                timing = view['operations'][0]['timing']
                self.assertFalse(timing['live'])
                self.assertIsNone(timing['execution_seconds'])
                time.sleep(.03)
                self.assertEqual(runtime_view(service.store, ready['mission_id'])['operations'][0]['timing'], timing)
            with closing(self.reopen(root)) as service:
                self.assertEqual(service.store.report(ready['mission_id']), report)
                self.assertEqual(runtime_view(service.store, ready['mission_id'])['operations'][0]['timing'], timing)
            self.records.append({**ready, 'delivery_preserved': True, 'timing': timing,
                'second_restart_idempotent': True})
