import base64
import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from game_adapters import pes2021_runtime_stats as stats


def record(native_id=123):
    blob = bytearray(stats.RECORD_SIZE)
    struct.pack_into("<II", blob, 0, 456, native_id)
    return blob


def counter(blob, name, values):
    struct.pack_into("<9I", blob, 8 + stats.METRICS[name] * 36, *values)


class FakeReader:
    base = 0x140000000

    def __init__(self, absent=False, changed=False, lineup_changed=False):
        self.absent, self.changed, self.lineup_changed = absent, changed, lineup_changed
        self.root_reads = 0
        self.lineup_reads = {}
        self.manager, self.wrapper, self.buffer = 0x100000, 0x200000, 0x300000
        self.lineup_wrappers = [0x400000, 0x500000]
        self.lineup_buffers = [0x600000, 0x700000]

    def read(self, address, size):
        if address == self.base + stats.MANAGER_RVA:
            self.root_reads += 1
            value = 0 if self.absent else self.manager
            if self.changed and self.root_reads > 1:
                value = 0
            return struct.pack("<Q", value)
        pointers = {self.manager + 0x2A28: self.wrapper,
                    self.wrapper + 0x10: self.buffer}
        for side in range(2):
            pointers[self.manager + 0x1970 + side * 40] = self.lineup_wrappers[side]
            pointers[self.lineup_wrappers[side] + 0x10] = self.lineup_buffers[side]
        if address in pointers:
            return struct.pack("<Q", pointers[address])
        for side in range(2):
            if address == self.lineup_buffers[side] + 8:
                self.lineup_reads[side] = self.lineup_reads.get(side, 0) + 1
                header = [side, *range(40), 0]
                if self.lineup_changed and self.lineup_reads[side] > 1:
                    header[1], header[2] = header[2], header[1]
                return struct.pack("<42I", *header)
            if address == self.buffer + 8 + 0x750D0 + 0x64D8 + side * 0x59650:
                empty = bytes(record(0xFFFFFFFF))
                return bytes(record(123 + side)) + empty * 39
        raise AssertionError(f"Unexpected read {address:x}/{size}")


class RuntimeStatsTests(unittest.TestCase):
    def test_native_sum_excludes_ninth_bucket_and_keeps_evidence(self):
        blob = record()
        counter(blob, "passes", (1, 2, 3, 4, 5, 6, 7, 8, 999))
        counter(blob, "successful_passes", (1, 2, 3, 4, 5, 6, 7, 0, 999))
        parsed = stats.parse_player_record(blob)
        self.assertEqual(parsed["counters"]["passes"], 36)
        self.assertEqual(parsed["native_buckets"]["passes"][-1], 999)
        self.assertAlmostEqual(parsed["rates_percent"]["pass_success"]["value"], 100 * 28 / 36)

    def test_unsigned_native_id_and_float_units_match_positive_sample(self):
        blob = record(2147483834)
        struct.pack_into("<f", blob, 0x1B90, 10.594084739685059)
        struct.pack_into("<f", blob, 0x1B6C, 12.309699058532715)
        blob[0x18EC:0x18EE] = bytes((0, 45))
        parsed = stats.parse_player_record(blob)
        self.assertEqual(parsed["native_player_id"], 2147483834)
        self.assertEqual(round(parsed["continuous"]["dribble_distance_m"]["value"], 1), 10.6)
        self.assertEqual(round(parsed["continuous"]["average_speed_kmh"]["value"], 1), 12.3)
        self.assertEqual(parsed["minutes_candidate"]["value"], 45)
        self.assertFalse(parsed["participation_verified"])

    def test_absent_record_differs_from_zero_stat_and_undefined_rate(self):
        self.assertIsNone(stats.parse_player_record(record(0xFFFFFFFF)))
        self.assertIsNone(stats.parse_player_record(record(0)))
        parsed = stats.parse_player_record(record())
        self.assertEqual(parsed["counters"]["passes"], 0)
        self.assertEqual(parsed["rates_percent"]["pass_success"],
                         {"value": None, "reason": "zero_denominator"})
        self.assertFalse(parsed["participation_verified"])

    def test_invalid_fields_are_null_and_never_nan_json(self):
        blob = record()
        struct.pack_into("<f", blob, 0x1B90, math.nan)
        blob[0x18EC:0x18EE] = bytes((255, 255))
        counter(blob, "successful_passes", (1, 0, 0, 0, 0, 0, 0, 0, 0))
        parsed = stats.parse_player_record(blob)
        self.assertIsNone(parsed["continuous"]["dribble_distance_m"]["value"])
        self.assertIsNone(parsed["minutes_candidate"]["value"])
        self.assertIsNone(parsed["rates_percent"]["pass_success"]["value"])

    def test_rating_and_initial_first11_flag_are_candidates_not_participation(self):
        blob = record()
        struct.pack_into("<f", blob, 0x1934, 7.5)
        blob[0x1933] = 1
        parsed = stats.parse_player_record(blob)
        self.assertEqual(parsed["rating_candidate"]["value"], 7.5)
        self.assertEqual(parsed["rating_candidate"]["raw_hex"], struct.pack("<f", 7.5).hex())
        self.assertFalse(parsed["rating_candidate"]["live_value_verified"])
        self.assertTrue(parsed["started_flag_candidate"]["value"])
        self.assertFalse(parsed["started_flag_candidate"]["participation_verified"])
        for value in (0, -1, 11, math.nan, math.inf):
            struct.pack_into("<f", blob, 0x1934, value)
            parsed = stats.parse_player_record(blob)
            self.assertIsNone(parsed["rating_candidate"]["value"])
            json.dumps(parsed, allow_nan=False)
        blob[0x1933] = 2
        self.assertIsNone(stats.parse_player_record(blob)["started_flag_candidate"]["value"])

    def test_inconsistent_rate_and_bad_record_are_rejected(self):
        self.assertEqual(stats.ratio(3, 2)["reason"], "inconsistent_counters")
        for blob in (bytes(stats.RECORD_SIZE - 1), bytes(stats.RECORD_SIZE + 1)):
            with self.assertRaises(stats.RuntimeStatsError):
                stats.parse_player_record(blob)

    def test_native_id_alone_never_certifies_career_or_duplicate_owner(self):
        records = [{"native_player_id": 123, "side": 0}, {"native_player_id": 123, "side": 1}]
        self.assertEqual(stats.select_target(records, 123)["state"], "ambiguous_native_id")
        self.assertIsNone(stats.select_target(records, 123)["record"])
        self.assertEqual(stats.select_target(records, 456)["state"], "absent")
        unique = stats.select_target(records[:1], 123)
        self.assertEqual(unique["state"], "unique_native_id_candidate")
        self.assertFalse(unique["career_branch_verified"])

    def test_unknown_build_rejected_before_registry_reads(self):
        class WrongBuild:
            base = 0x140000000

            def read(self, address, size):
                self.address = address
                return bytes(size)
        reader = WrongBuild()
        with self.assertRaisesRegex(stats.RuntimeStatsError, "Unsupported"):
            stats.capture_stats(reader, 123)
        self.assertEqual(reader.address, reader.base + stats.SIGNATURES[0][0])

    @patch.object(stats, "validate_profile")
    def test_buffer_chain_and_lineup_resolve_two_sides_without_event_certification(self, _):
        report = stats.capture_stats(FakeReader(), 124)
        self.assertEqual(len(report["records"]), 2)
        self.assertEqual(report["target"]["record"]["side"], 1)
        self.assertEqual(report["target"]["record"]["current_pitch_slots_candidate"], [0])
        self.assertFalse(report["match_ready"])
        self.assertFalse(report["event_identity_verified"])
        self.assertEqual(report["game_writes"], 0)

    @patch.object(stats, "validate_profile")
    def test_missing_object_has_no_zero_records(self, _):
        report = stats.capture_stats(FakeReader(absent=True), 123)
        self.assertEqual(report["status"], "unavailable")
        self.assertIsNone(report["records"])
        self.assertIsNone(report["target"]["record"])
        self.assertIsNone(report["runtime_instance_token"])
        self.assertIsNone(report["raw_record_blocks"])

    @patch.object(stats, "validate_profile")
    def test_full_capacity_bytes_keep_unknown_and_empty_slot_fields_for_reanalysis(self, _):
        class RawReader(FakeReader):
            def read(self, address, size):
                blob = super().read(address, size)
                if size == stats.CAPACITY * stats.RECORD_SIZE:
                    blob = bytearray(blob)
                    blob[-1] = 0xA7  # unknown field of the last empty native slot
                    return bytes(blob)
                return blob
        report = stats.capture_stats(RawReader(), 123)
        self.assertEqual(len(report["records"]), 2)
        for block in report["raw_record_blocks"]:
            raw = base64.b64decode(block["data"], validate=True)
            self.assertEqual(len(raw), stats.CAPACITY * stats.RECORD_SIZE)
            self.assertEqual(raw[-1], 0xA7)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), block["sha256"])
            self.assertEqual(stats.parse_player_record(raw[:stats.RECORD_SIZE])["native_player_id"], 123 + block["side"])
        self.assertFalse(report["match_ready"])

    @patch.object(stats, "validate_profile")
    def test_allocation_token_changes_with_process_creation_and_is_not_event_id(self, _):
        first, second = FakeReader(), FakeReader()
        first.pid = second.pid = 456
        first.creation_time_100ns, second.creation_time_100ns = 100, 200
        a, b = stats.capture_stats(first, 123), stats.capture_stats(second, 123)
        self.assertNotEqual(a["runtime_instance_token"], b["runtime_instance_token"])
        self.assertEqual(a["process_instance"], {"pid": 456, "creation_time_100ns": 100})
        self.assertFalse(a["event_identity_verified"])

    @patch.object(stats, "validate_profile")
    def test_identical_roots_can_reuse_token_without_certifying_same_event(self, _):
        a, b = stats.capture_stats(FakeReader(), 123), stats.capture_stats(FakeReader(), 123)
        self.assertEqual(a["runtime_instance_token"], b["runtime_instance_token"])
        self.assertFalse(a["event_identity_verified"])
        self.assertIn("same addresses may be reused", a["runtime_token_semantics"])

    @patch.object(stats, "validate_profile")
    def test_object_switch_or_substitution_during_capture_rejected(self, _):
        with self.assertRaisesRegex(stats.RuntimeStatsError, "roots changed"):
            stats.capture_stats(FakeReader(changed=True), 123)
        with self.assertRaisesRegex(stats.RuntimeStatsError, "Lineup changed"):
            stats.capture_stats(FakeReader(lineup_changed=True), 123)

    def test_output_isolated_no_overwrite_and_pointer_bounds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "new.json"
            self.assertEqual(stats.new_output(output, root), output)
            output.write_text("existing", encoding="utf-8")
            for bad in (output, root.parent / "outside.json", root / "missing/new.json", root / "bad.bin"):
                with self.assertRaises(stats.RuntimeStatsError):
                    stats.new_output(bad, root)
            self.assertEqual(output.read_text(), "existing")
        for bad in (0, 0xFFFF, 0x800000000000):
            with self.assertRaises(stats.RuntimeStatsError):
                stats.pointer(bad)


if __name__ == "__main__":
    unittest.main()
