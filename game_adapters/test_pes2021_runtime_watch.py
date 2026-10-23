import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from game_adapters import pes2021_runtime_watch as watch
from game_adapters.pes2021_runtime_stats import SCHEMA, RuntimeStatsError


def sample(passes=0, token="allocation-a", status="runtime_preview", creation=100):
    return {"schema": SCHEMA, "target_native_player_id": 123, "status": status,
            "captured_at": f"sample-{passes}", "runtime_instance_token": token,
            "process_instance": {"pid": 456, "creation_time_100ns": creation},
            "snapshot_ready": False, "match_ready": False, "d2_ready": False,
            "event_identity_verified": False,
            "records": [{"native_player_id": 123, "side": 1, "roster_slot": 32,
                         "counters": {"passes": passes, "goals": 0}}] if status == "runtime_preview" else None}


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recorder = watch.Recorder(self.root / "capture", "offline_replay", 123, raw_root=self.root)

    def saved(self, name):
        return json.loads((self.recorder.output / name).read_text(encoding="utf-8"))

    def test_release_preserves_last_valid_without_completed_match_claim(self):
        self.recorder.accept(sample(5), 0)
        self.recorder.accept(sample(12), 2)
        self.recorder.accept(sample(status="unavailable"), 3)
        manifest = self.recorder.finish("test", 4)
        last = self.saved(manifest["last_valid_checkpoint"])
        self.assertEqual(last["runtime_report"]["records"][0]["counters"]["passes"], 12)
        self.assertEqual(manifest["events"][-2]["kind"], "buffers_absent")
        self.assertFalse(manifest["events"][-2]["completion_verified"])
        self.assertFalse(manifest["live_runtime_acceptance"])
        self.assertFalse(last["match_ready"])

    def test_initial_absence_keeps_null_instead_of_zero_snapshot(self):
        self.recorder.accept(sample(status="unavailable"), 0)
        self.recorder.accept(sample(status="unavailable"), 1)
        manifest = self.recorder.finish("test", 1)
        self.assertIsNone(manifest["last_valid_checkpoint"])
        self.assertEqual(manifest["snapshots"], [])
        self.assertEqual(manifest["unavailable_observations"], 2)
        self.assertEqual(manifest["available_observations"], 0)
        self.assertEqual(manifest["last_unavailable_observation"]["elapsed_seconds"], 1)

    def test_allocation_change_keeps_both_observations_not_two_match_events(self):
        self.recorder.accept(sample(5), 0)
        self.recorder.accept(sample(12), 1)
        self.recorder.accept(sample(1, token="allocation-b"), 2)
        manifest = self.recorder.finish("test", 3)
        records = [self.saved(s["path"]) for s in manifest["snapshots"]]
        self.assertEqual([(r["observation_segment"], r["runtime_report"]["records"][0]["counters"]["passes"]) for r in records],
                         [(1, 5), (1, 12), (2, 1)])
        self.assertTrue(manifest["identity_review_needed"])
        self.assertFalse(manifest["event_identity_verified"])

    def test_same_address_counter_reset_flags_conflict_without_inventing_match(self):
        self.recorder.accept(sample(9), 0)
        self.recorder.accept(sample(2), 1)
        manifest = self.recorder.finish("test", 2)
        regression = next(e for e in manifest["events"] if e["kind"] == "counter_regression")
        self.assertFalse(regression["new_match_inferred"])
        self.assertEqual(self.recorder.segment, 1)
        self.assertTrue(manifest["identity_review_needed"])

    def test_pid_reuse_stops_and_preserves_old_process_sample(self):
        self.recorder.accept(sample(9), 0)
        self.recorder.accept(sample(1, creation=200), 1)
        manifest = self.recorder.finish("test", 2)
        self.assertEqual(manifest["stop_reason"], "process_changed")
        self.assertEqual(self.saved(manifest["last_valid_checkpoint"])["runtime_report"]["process_instance"]["creation_time_100ns"], 100)

    def test_read_error_is_not_absence_and_final_retains_last_sample(self):
        self.recorder.accept(sample(9), 0)
        self.recorder.accept(sample(12), 1)
        self.recorder.read_error(OSError("partial read"), 2)
        manifest = self.recorder.finish("interrupted", 3)
        self.assertTrue(manifest["continuity_uncertain"])
        self.assertNotIn("buffers_absent", [e["kind"] for e in manifest["events"]])
        self.assertEqual(self.saved(manifest["last_valid_checkpoint"])["runtime_report"]["records"][0]["counters"]["passes"], 12)

    def test_unchanged_observation_deduplicates_only_within_segment(self):
        first, second = sample(9), sample(9)
        second.update(captured_at="later", collection_seconds=0.2, read_bytes=987)
        self.recorder.accept(first, 0)
        self.recorder.accept(second, 40)
        self.recorder.accept(sample(status="unavailable"), 41)
        self.recorder.accept(second, 42)
        manifest = self.recorder.finish("test", 43)
        self.assertEqual(len(manifest["snapshots"]), 2)
        self.assertEqual([s["observation_segment"] for s in manifest["snapshots"]], [1, 2])

    def test_retention_limit_reserves_room_for_final_changed_sample(self):
        recorder = watch.Recorder(self.root / "limited", "offline_replay", 123,
                                  raw_root=self.root, max_snapshots=2, checkpoint_seconds=1)
        recorder.accept(sample(1), 0)
        recorder.accept(sample(2), 1)
        manifest = recorder.finish("test", 2)
        self.assertEqual(manifest["stop_reason"], "retention_budget")
        self.assertEqual(len(manifest["snapshots"]), 2)
        last = json.loads((recorder.output / manifest["last_valid_checkpoint"]).read_bytes())
        self.assertEqual(last["runtime_report"]["records"][0]["counters"]["passes"], 2)

    def test_output_does_not_overwrite_or_escape_raw(self):
        for path in (self.recorder.output, self.root, self.root.parent / "outside", self.root / "missing/new"):
            with self.assertRaises(RuntimeStatsError):
                watch.new_directory(path, self.root)
        marker = self.root / "existing.json"
        marker.write_text("preserve")
        with self.assertRaises(RuntimeStatsError):
            watch.new_directory(marker, self.root)
        self.assertEqual(marker.read_text(), "preserve")

    def test_wrong_target_or_promoted_input_rejected(self):
        for key, value in (("target_native_player_id", 124), ("schema", "unknown"),
                           ("match_ready", True), ("d2_ready", True)):
            bad = sample()
            bad[key] = value
            with self.assertRaises(RuntimeStatsError):
                self.recorder.accept(bad, 0)

    def test_replay_checks_provenance_and_is_explicitly_offline(self):
        source = self.root / "source.json"
        source.write_text(json.dumps(sample(12)), encoding="utf-8")
        trace = self.root / "trace.json"
        trace.write_text(json.dumps({"samples": [{"path": str(source), "elapsed_seconds": 0,
                           "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}))
        manifest = watch.replay(trace, self.recorder, self.root)
        snapshot = self.saved(manifest["last_valid_checkpoint"])
        self.assertEqual(snapshot["source_mode"], "offline_replay")
        self.assertEqual(snapshot["source"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertFalse(manifest["live_runtime_acceptance"])

    def test_tampered_replay_rejected_before_creating_snapshot(self):
        source = self.root / "source.json"
        source.write_text(json.dumps(sample(12)))
        trace = self.root / "trace.json"
        trace.write_text(json.dumps({"samples": [{"path": str(source), "elapsed_seconds": 0, "sha256": "wrong"}]}))
        with self.assertRaisesRegex(RuntimeStatsError, "SHA256"):
            watch.replay(trace, self.recorder, self.root)
        self.assertEqual(self.recorder.snapshots, [])

    def test_unknown_process_or_allocation_metadata_is_not_certified(self):
        old = sample(12)
        old.pop("process_instance")
        old.pop("runtime_instance_token")
        self.recorder.accept(old, 0)
        manifest = self.recorder.finish("test", 1)
        self.assertIsNone(manifest["process_instance"])
        self.assertFalse(manifest["events"][0]["allocation_token_available"])

    def test_legacy_gate_omission_is_kept_as_raw_not_promoted(self):
        old = sample(12)
        del old["snapshot_ready"], old["d2_ready"]
        self.recorder.accept(old, 0)
        manifest = self.recorder.finish("test", 1)
        wrapped = self.saved(manifest["last_valid_checkpoint"])
        self.assertNotIn("d2_ready", wrapped["runtime_report"])
        self.assertFalse(wrapped["d2_ready"])

    def test_lineup_only_change_does_not_invent_event_or_counter_regression(self):
        before, after = sample(12), copy.deepcopy(sample(12))
        before["lineup_headers_raw"] = [[1, 32]]
        after["lineup_headers_raw"] = [[1, 33]]
        self.recorder.accept(before, 0)
        self.recorder.accept(after, 1)
        manifest = self.recorder.finish("test", 2)
        self.assertEqual(self.recorder.segment, 1)
        self.assertNotIn("counter_regression", [e["kind"] for e in manifest["events"]])

    def test_realistic_full_capacity_raw_evidence_fits_checkpoint_and_is_preserved(self):
        from game_adapters import pes2021_runtime_stats as stats
        from game_adapters.test_pes2021_runtime_stats import FakeReader, record

        class FullReader(FakeReader):
            def read(self, address, size):
                result = super().read(address, size)
                if size == stats.CAPACITY * stats.RECORD_SIZE:
                    side = int(address > self.buffer + 8 + 0x750D0 + 0x64D8)
                    return b"".join(bytes(record(123 + side * stats.CAPACITY + slot))
                                    for slot in range(stats.CAPACITY))
                return result
        with patch.object(stats, "validate_profile"):
            report = stats.capture_stats(FullReader(), 123)
        self.assertEqual(len(report["records"]), 80)
        self.recorder.accept(report, 0)
        manifest = self.recorder.finish("test", 1)
        saved = self.saved(manifest["last_valid_checkpoint"])
        self.assertEqual(saved["runtime_report"]["raw_record_blocks"], report["raw_record_blocks"])
        self.assertLess((self.recorder.output / manifest["last_valid_checkpoint"]).stat().st_size,
                        watch.MAX_SAMPLE_BYTES)

    def test_cli_reports_original_error_even_when_finalization_storage_fails(self):
        import contextlib
        import io
        output = io.StringIO()
        argv = ["watch", "--replay", str(self.root / "missing.json"), "--native-player-id", "123",
                "--output", str(self.root / "new")]
        with patch("sys.argv", argv), patch.object(watch, "Recorder", return_value=self.recorder), \
                patch.object(self.recorder, "finish", side_effect=OSError("disk full")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(watch.main(), 1)
        diagnostic = json.loads(output.getvalue())
        self.assertEqual(diagnostic["status"], "rejected")
        self.assertIn("missing.json", diagnostic["reason"])
        self.assertEqual(diagnostic["finalization_error"], "disk full")


if __name__ == "__main__":
    unittest.main()
