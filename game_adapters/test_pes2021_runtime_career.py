"""Synthetic ownership/error cases; these do not certify real PES fields."""
import json
import math
import struct
import unittest
from unittest.mock import patch

from game_adapters import pes2021_runtime_career as career
from game_adapters.pes2021_runtime_stats import RuntimeStatsError


def buffers():
    fixture, cache = bytearray(career.FIXTURE_SIZE), bytearray(career.CACHE_SIZE)
    struct.pack_into("<HI", fixture, 0, 7, 0)
    struct.pack_into("<I", fixture, 4, 0x40000000)
    struct.pack_into("<HBB", fixture, 8, 2020, 9, 23)
    struct.pack_into("<II", fixture, 0x14, 1, 2)
    fixture[0x1C], fixture[0x1F] = 2, 1
    struct.pack_into("<4I", fixture, 0x24 + 17 * 16, 42, 123, 2 | (1 << 8), 90 | (58 << 7))
    struct.pack_into("<H", cache, 0, 7)
    struct.pack_into("<HBB", cache, 4, 2020, 9, 23)
    for side in range(2):
        struct.pack_into("<I", cache, 12 + side * career.CACHE_SIDE_SIZE, side + 1)
    struct.pack_into("<5If", cache, 16 + career.CACHE_SIDE_SIZE + 3 * 24,
                     42, 123, 90 << 8, 2, 1, 5.5)
    return fixture, cache


class FakeReader:
    base = 0x140000000
    pid, creation_time_100ns = 456, 100

    def __init__(self, *, absent=False, root_changed=False, cache_changed=False):
        self.fixture, self.cache = buffers()
        self.absent, self.root_changed, self.cache_changed = absent, root_changed, cache_changed
        self.root_reads, self.cache_reads = 0, 0

    def read(self, address, size):
        if address == self.base + career.CAREER_ROOT_RVA:
            self.root_reads += 1
            return struct.pack("<Q", 0 if self.absent or self.root_changed and self.root_reads > 1 else 0x100000)
        if address == 0x100000 + 0x48:
            return struct.pack("<Q", 0x200000)
        if address == 0x200000 + career.FIXTURE_BASE + 7 * career.FIXTURE_SIZE:
            return bytes(self.fixture)
        if address == 0x200000 + career.CACHE_BASE:
            self.cache_reads += 1
            result = bytearray(self.cache)
            if self.cache_changed and self.cache_reads > 1:
                result[-1] ^= 1
            return bytes(result)
        return bytes(size)  # deliberately unsupported code for the guard test


class CareerTests(unittest.TestCase):
    def parse(self, fixture=None, cache=None, team=2, ref=42):
        f, c = buffers()
        return career.parse_buffers(f if fixture is None else fixture, c if cache is None else cache,
                                    7, 123, team, ref)

    def test_complete_fixture_and_cache_are_separate_explicit_candidates(self):
        result = self.parse()
        self.assertEqual(result["fixture"]["research_anchor"], ["2020-09-23", 1, 2, 7])
        fields = result["target"]["record"]["stat_fields_candidate"]
        self.assertEqual((fields["goals_candidate"], fields["assists_candidate"], fields["rating_candidate"]), (2, 1, 5.8))
        self.assertFalse(fields["appearance_verified"])
        row = result["recent_cache"]["target"]["record"]
        self.assertEqual(row["gk_saves_candidate"], 2)
        self.assertFalse(row["participation_verified"])

    def test_absent_fixture_target_is_not_a_zero_minute_personal_row(self):
        fixture, _ = buffers()
        struct.pack_into("<I", fixture, 0x24 + 17 * 16 + 4, 999)
        target = self.parse(fixture=fixture)["target"]
        self.assertEqual(target, {"state": "absent", "record": None})

    def test_duplicate_id_wrong_reference_or_wrong_side_never_selects_row(self):
        fixture, _ = buffers()
        struct.pack_into("<4I", fixture, 0x24, 42, 123, 0, 0)
        self.assertEqual(self.parse(fixture=fixture)["target"]["state"], "ambiguous_native_id")
        for result in (self.parse(ref=99), self.parse(team=1)):
            self.assertEqual(result["target"]["state"], "binding_conflict")
            self.assertIsNone(result["target"]["record"])

    def test_different_cache_fixture_or_conflicting_header_does_not_bind(self):
        _, cache = buffers()
        struct.pack_into("<H", cache, 0, 8)
        result = self.parse(cache=cache)["recent_cache"]
        self.assertEqual(result["state"], "other_fixture_cache")
        self.assertIsNone(result["target"]["record"])
        struct.pack_into("<H", cache, 0, 7)
        cache[7] = 24
        result = self.parse(cache=cache)["recent_cache"]
        self.assertEqual(result["state"], "conflicting_cache_header")
        self.assertIsNone(result["target"]["record"])

    def test_empty_cache_requires_sentinels_and_all_slot_ids_empty(self):
        _, cache = buffers()
        struct.pack_into("<H", cache, 0, 0xFFFF)
        for side in range(2):
            struct.pack_into("<I", cache, 12 + side * career.CACHE_SIDE_SIZE, 0xFFFFFFFF)
        self.assertNotEqual(self.parse(cache=cache)["recent_cache"]["state"], "empty_native_cache")
        struct.pack_into("<I", cache, 16 + career.CACHE_SIDE_SIZE + 3 * 24 + 4, 0)
        self.assertEqual(self.parse(cache=cache)["recent_cache"]["state"], "empty_native_cache")

    def test_uncompleted_fixture_keeps_raw_bytes_without_score_or_personal_stats(self):
        fixture, _ = buffers()
        struct.pack_into("<I", fixture, 4, 0x20000000)
        result = self.parse(fixture=fixture)
        self.assertIsNone(result["fixture"]["score_candidate"])
        self.assertIsNone(result["target"]["record"]["stat_fields_candidate"])
        self.assertEqual(result["recent_cache"]["state"], "conflicting_cache_header")

    def test_invalid_cache_rating_is_null_and_raw_bytes_remain_available(self):
        _, cache = buffers()
        struct.pack_into("<f", cache, 16 + career.CACHE_SIDE_SIZE + 3 * 24 + 20, math.nan)
        result = self.parse(cache=cache)
        row = result["recent_cache"]["target"]["record"]
        self.assertIsNone(row["rating_candidate"])
        self.assertEqual(bytes.fromhex(result["recent_cache"]["raw_hex"]), cache)
        json.dumps(result, allow_nan=False)

    def test_bad_lengths_sequence_and_club_are_rejected(self):
        fixture, cache = buffers()
        for f, c, seq, team in ((fixture[:-1], cache, 7, 2), (fixture, cache[:-1], 7, 2),
                                (fixture, cache, 8, 2), (fixture, cache, 7, 3)):
            with self.assertRaises(RuntimeStatsError):
                career.parse_buffers(f, c, seq, 123, team, 42)

    @patch.object(career, "validate_profile")
    @patch.object(career, "EXTRA_SIGNATURES", ())
    def test_read_path_repeats_buffers_and_never_certifies_import(self, _):
        report = career.capture_career(FakeReader(), 7, 123, 2, 42)
        self.assertEqual(report["status"], "runtime_career_preview")
        self.assertEqual(report["process_instance"], {"pid": 456, "creation_time_100ns": 100})
        self.assertTrue(all(report[key] is False for key in
                            ("career_branch_verified", "event_identity_verified", "snapshot_ready", "match_ready", "d2_ready")))
        self.assertTrue(all(value is None for value in report["unavailable_action_details"].values()))

    @patch.object(career, "validate_profile")
    @patch.object(career, "EXTRA_SIGNATURES", ())
    def test_absent_context_and_torn_capture_are_not_valid_results(self, _):
        report = career.capture_career(FakeReader(absent=True), 7, 123, 2, 42)
        self.assertIsNone(report["fixture"])
        self.assertIsNone(report["recent_cache"])
        for reader in (FakeReader(root_changed=True), FakeReader(cache_changed=True)):
            with self.assertRaisesRegex(RuntimeStatsError, "changed during capture"):
                career.capture_career(reader, 7, 123, 2, 42)

    @patch.object(career, "validate_profile")
    def test_unknown_career_code_and_invalid_arguments_reject_before_root_reads(self, _):
        reader = FakeReader()
        with self.assertRaisesRegex(RuntimeStatsError, "Unsupported loaded career code"):
            career.capture_career(reader, 7, 123, 2, 42)
        self.assertEqual(reader.root_reads, 0)
        with self.assertRaisesRegex(RuntimeStatsError, "Invalid fixture"):
            career.capture_career(reader, 13000, 123, 2, 42)
        self.assertEqual(reader.root_reads, 0)


if __name__ == "__main__":
    unittest.main()
