"""Synthetic package boundary tests; genuine save decoding is tested separately."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from game_adapters import pes2021_native_package as package
from game_adapters.pes2021_runtime_career import parse_buffers
from game_adapters.test_pes2021_runtime_career import buffers


class NativePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name)
        self.root = self.raw / "fixtures" / "sample"
        self.root.mkdir(parents=True)
        self.identity = {"career_id": "test-career", "branch_id": "controlled", "player_id": "test-player"}
        self.native = {"native_player_id": 123, "packed_index_raw": 42, "team_index_raw": 2, "name": "Test"}
        self.process = {"pid": 456, "creation_time_100ns": 100}
        self.anchor = ["2020-09-23", 1, 2, 7]
        evidence = self.write("evidence.json", {"purpose": "synthetic_fixture"})
        baseline = self.write("baseline.bin", b"synthetic save decoder input")
        self.saved = {"snapshot": {"native_player_id": 123, "identity": {"name": "Test"},
                                   "attributes": {"curl": {"value": 50}}, "saved_game_date": "2020-09-23"},
                      "club_binding": {"team_index_raw": 2}, "native_player_reference": {"native_player_id": 123, "packed_index_raw": 42},
                      "club_fixtures": [{"game_date": "2020-09-23", "home": {"team_index_raw": 1},
                                         "away": {"team_index_raw": 2}, "sequence_raw": 7, "bit30_completed_candidate": False}]}
        self.decoder = patch.object(package, "parse_save_bytes", side_effect=lambda _: copy.deepcopy(self.saved))
        self.decoder.start(); self.addCleanup(self.decoder.stop)
        fixture, cache = buffers()
        struct.pack_into("<I", fixture, 0x24 + 17 * 16 + 12, 90 | (55 << 7))
        common = {"schema": "pes2021-runtime-career-experimental-v1", "source_mode": "live_read", "status": "runtime_career_preview",
                  "process_instance": self.process, "expected_target": {"native_player_id": 123, "team_index_raw": 2, "packed_index_raw": 42},
                  "verified_code_sha256": {hex(r): h for r, _, h in (*package.SIGNATURES, *package.EXTRA_SIGNATURES)}}
        self.after = {**common, **parse_buffers(fixture, cache, 7, 123, 2, 42), "captured_at": "2026-10-22T00:00:02+00:00"}
        before_fixture = bytearray(fixture); struct.pack_into("<I", before_fixture, 4, 0)
        self.before = {**common, **parse_buffers(before_fixture, cache, 7, 123, 2, 42), "captured_at": "2026-10-22T00:00:00+00:00"}
        self.detail = self.make_detail()
        self.binding_review = {"kind": "native_test_binding_review", "identity": self.identity, "status": "reviewed", "reviewer": "assistant",
                               "reviewed_at": "2026-10-22T01:00:00+00:00", "method": "synthetic test review", "evidence": [evidence],
                               "native": self.native, "baseline": baseline, "process_instance": self.process}
        capture = {"capture_id": "baseline", "captured_at": "2026-10-21T23:59:59+00:00", "game_time": {"value": "2020-09-23", "precision": "day"}}
        self.binding_review.update(baseline_capture=capture, accepted_attribute_fields=["curl"])
        self.item = {"event_id": "explicit-event-001", "capture_id": "settled-001", "sequence": 1, "research_anchor": self.anchor, "scenario": "normal_play",
                     "before": {"kind": "career", "source": self.write("before.json", self.before)},
                     "after": self.write("after.json", self.after),
                     "details": [{"capture_id": "detail-first", "source": self.write("detail.json", self.detail)},
                                 {"capture_id": "detail-repeat", "source": self.write("detail-repeat.json", self.detail)}]}
        self.event_review = {"kind": "native_test_event_review", "identity": self.identity, "status": "reviewed", "reviewer": "assistant",
                             "reviewed_at": "2026-10-22T01:00:00+00:00", "method": "synthetic controlled transition", "evidence": [evidence],
                             "capture_window": {"start": "2026-10-22T00:00:00+00:00", "end": "2026-10-22T00:00:03+00:00"}}
        self.manifest = {"schema": package.SCHEMA, "purpose": "fixtures", "environment": package.ENVIRONMENT,
                         "binding": {"identity": self.identity, "native": self.native, "baseline": baseline, "process_instance": self.process, "baseline_capture": capture},
                         "events": []}
        self.sync()

    def write(self, name, value):
        blob = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
        (self.root / name).write_bytes(blob)
        return {"path": name, "sha256": hashlib.sha256(blob).hexdigest()}

    def sync(self):
        self.manifest["binding"]["review"] = self.write("binding-review.json", self.binding_review)
        review = {**self.event_review, **{k: v for k, v in self.item.items() if k != "review"}}
        self.item["review"] = self.write("event-review.json", review)
        self.manifest["events"] = [self.item]
        self.write("package.json", self.manifest)

    def read(self):
        return package.read_package(self.root, "package.json", raw_root=self.raw)

    def rejected(self, text):
        with self.assertRaisesRegex(package.PackageError, text):self.read()

    def make_detail(self):
        blocks = [bytearray(40 * package.RECORD_SIZE) for _ in range(2)]
        at = 3 * package.RECORD_SIZE
        struct.pack_into("<II", blocks[1], at, 42, 123)
        blocks[1][at + 0x18EC:at + 0x18EE] = bytes([0, 90])
        blocks[1][at + 0x1933] = 1
        struct.pack_into("<f", blocks[1], at + 0x1934, 5.5)
        struct.pack_into("<I", blocks[1], at + 8 + 133 * 36, 2)
        struct.pack_into("<I", blocks[1], at + 8, 2)
        struct.pack_into("<I", blocks[1], at + 8 + 4 * 36, 1)
        return {"schema": "pes2021-runtime-player-stats-experimental-v1", "status": "runtime_preview",
                "captured_at": "2026-10-22T00:00:01+00:00", "process_instance": self.process,
                "verified_code_sha256": {hex(r): h for r, _, h in package.SIGNATURES},
                "raw_record_blocks": [{"side": side, "sha256": hashlib.sha256(blob).hexdigest(),
                                       "data": base64.b64encode(blob).decode()} for side, blob in enumerate(blocks)]}

    def test_reviewed_native_repeats_merge_under_explicit_event_without_import(self):
        first, second = self.read(), self.read()
        self.assertEqual(first, second)
        self.assertEqual(len(first["events"]), 1)
        self.assertEqual(first["events"][0]["event_id"], "explicit-event-001")
        self.assertEqual(len(first["events"][0]["detailed_captures"]), 2)
        self.assertTrue(first["d1_minimum_sample_ready"])
        self.assertFalse(first["real_game_sample_ready"])
        self.assertFalse(first["d2_ready"])
        self.assertEqual((first["game_writes"], first["application_writes"]), (0, 0))

    def test_same_native_id_with_other_club_or_reference_is_rejected(self):
        for key, value in (("team_index_raw", 3), ("packed_index_raw", 43)):
            old = self.native[key]; self.native[key] = value; self.sync()
            self.rejected("native_binding_mismatch")
            self.native[key] = old

    def test_unreviewed_or_mismatched_context_cannot_be_promoted_by_manifest(self):
        self.binding_review["status"] = "pending"; self.sync(); self.rejected("unreviewed_binding")
        self.binding_review["status"] = "reviewed"
        self.event_review["identity"] = {**self.identity, "branch_id": "another-branch"}
        self.sync(); self.rejected("unreviewed_binding")

    def test_source_corruption_is_not_unknown_or_zero(self):
        (self.root / "after.json").write_bytes(b"changed")
        self.rejected("source_hash_mismatch")

    def test_before_completed_and_after_incomplete_transitions_are_rejected(self):
        self.item["before"]["source"] = self.write("before.json", {**self.after, "captured_at": self.before["captured_at"]})
        self.sync(); self.rejected("invalid_transition")
        self.item["before"]["source"] = self.write("before.json", self.before)
        self.item["after"] = self.write("after.json", {**self.before, "captured_at": self.after["captured_at"]})
        self.sync(); self.rejected("incomplete_event")

    def test_replayed_anchor_requires_branch_review_even_with_new_event_id(self):
        extra = copy.deepcopy(self.item); extra.update(event_id="new-id", sequence=2)
        self.manifest["events"].append(extra); self.write("package.json", self.manifest)
        self.rejected("branch_review_required")

    def test_wrong_process_or_loaded_code_rejected(self):
        for key, value in (("process_instance", {"pid": 456, "creation_time_100ns": 101}), ("verified_code_sha256", {})):
            r = {**self.after, key: value}; self.item["after"] = self.write("after.json", r); self.sync()
            self.rejected("process_context_mismatch|unsupported_build")

    def test_old_detail_cannot_attach_to_new_fixture_by_matching_player_id(self):
        self.detail["captured_at"] = "2026-10-21T23:59:59+00:00"
        self.item["details"][0]["source"] = self.write("detail.json", self.detail); self.sync()
        self.rejected("invalid_transition")

    def test_detail_bytes_must_match_fixture_owned_cache_all_rows(self):
        block = self.detail["raw_record_blocks"][1]
        raw = bytearray(base64.b64decode(block["data"]))
        struct.pack_into("<I", raw, 3 * package.RECORD_SIZE + 8 + 133 * 36, 3)
        block.update(data=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
        self.item["details"][0]["source"] = self.write("detail.json", self.detail); self.sync()
        self.rejected("event_conflict")

    def test_simulation_cannot_receive_other_match_detailed_fields(self):
        self.item["scenario"] = "selected_skip"; self.sync(); self.rejected("source_scope_mismatch")

    def test_path_escape_and_false_fixture_provenance_are_rejected(self):
        self.manifest["binding"]["baseline"] = {"path": "../escape.bin", "sha256": "0" * 64}
        self.binding_review["baseline"] = self.manifest["binding"]["baseline"]
        self.sync(); self.rejected("path_escape")
        self.manifest["purpose"] = "real_test"; self.write("package.json", self.manifest)
        self.rejected("unsupported_package")

    def test_native_values_are_reparsed_from_bytes_not_report_convenience_fields(self):
        self.after["target"] = {"state": "absent", "record": None}
        self.item["after"] = self.write("after.json", self.after); self.sync()
        r = self.read()
        self.assertEqual(r["events"][0]["personal_summary"]["record"]["native_player_id"], 123)

    def test_absent_bench_row_stays_unknown_and_does_not_pass_personal_sample_gate(self):
        fixture = bytearray.fromhex(self.after["fixture"]["raw_hex"])
        struct.pack_into("<I", fixture, 0x24 + 17 * 16 + 4, 999)
        cache = bytearray(len(bytes.fromhex(self.after["recent_cache"]["raw_hex"])))
        struct.pack_into("<H", cache, 0, 0xFFFF)
        for side in range(2):struct.pack_into("<I", cache, 12 + side * 0x3C4, 0xFFFFFFFF)
        after = {**self.after, **parse_buffers(fixture, cache, 7, 123, 2, 42)}
        self.item.update(scenario="bench_skip", details=[], after=self.write("after.json", after))
        self.sync()
        result = self.read()
        event = result["events"][0]
        self.assertIsNone(event["personal_summary"]["record"])
        self.assertIsNone(event["unknown"]["shots"])
        self.assertEqual(event["participation"], "bench_selection_only_actual_participation_unknown")
        self.assertFalse(result["match_ready"])
        self.assertFalse(result["d1_minimum_sample_ready"])

    def test_source_changed_during_decoding_is_rejected(self):
        def changed(_):
            (self.root / "baseline.bin").write_bytes(b"save changed while reading")
            return copy.deepcopy(self.saved)
        with patch.object(package, "parse_save_bytes", side_effect=changed):
            self.rejected("source_changed")


if __name__ == "__main__":unittest.main()
