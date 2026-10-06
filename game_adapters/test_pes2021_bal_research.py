"""Experimental fixture/stat boundaries; synthetic data does not certify PES fields."""
import struct
import unittest

from game_adapters import test_pes2021_save as fixtures
from game_adapters.pes2021_bal_research import (research_bal, parse_teams, SCHEDULE_BASE, SCHEDULE_CAPACITY,
                                              CUMULATIVE_BASE, CUMULATIVE_SIZE, CUMULATIVE_CAPACITY)
from game_adapters.pes2021_save import SaveError


class BalResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = fixtures.NativeSaveTests()
        fixture.setUp()
        body = bytearray(0xBE6698 + 24)
        struct.pack_into("<6I", body, 0, 11, 80, 0x015B6D44, 10701, 700, 700)
        for index in range(700):
            struct.pack_into("<I", body, 0x50 + index * 1680, index)
        for index, name in ((0, b"Fixture Club"), (1, b"Other Club")):
            base = 0x50 + index * 1680
            body[base + 4:base + 4 + len(name)] = name
        struct.pack_into("<4I", body, 0x50 + 0x14C, 0, 2147483834, 65535, 0)
        body[0xB7E294:0xB7E294 + 512] = fixture.blocks["data"]
        for index in range(13000):
            struct.pack_into("<H", body, SCHEDULE_BASE + index * 596, 65535)
        base = SCHEDULE_BASE
        struct.pack_into("<IIHBB", body, base, 1, 0x6000004F, 2020, 8, 22)
        struct.pack_into("<II", body, base + 0x14, 0, 1)
        for slot in range(34):
            struct.pack_into("<I", body, base + 0x24 + slot * 16, 65535)
        struct.pack_into("<4I", body, base + 0x24, 0, 2147483834, 0, 0)
        struct.pack_into("<5If", body, 0xBE6698, 0, 2147483834, 90 << 8, 0, 1, 4.0)
        cls.blocks = {**fixture.blocks, "data": bytes(body)}

    def changed(self, offset, payload):
        body = bytearray(self.blocks["data"])
        body[offset:offset + len(payload)] = payload
        return {**self.blocks, "data": bytes(body)}

    def test_roster_and_stats_do_not_certify_appearance_or_skip(self):
        result = research_bal({}, self.blocks)
        row = result["club_fixtures"][0]
        self.assertEqual((row["game_date"], row["home"]["name"], row["away"]["name"]),
                         ("2020-08-22", "Fixture Club", "Other Club"))
        self.assertIn("unknown", row["target_roster_slots"][0]["participation"])
        self.assertFalse(result["match_records_verified"])
        self.assertFalse(result["skipped_match_collection_verified"])
        self.assertEqual(len(result["personal_stat_candidates"]), 1)
        candidate = result["personal_stat_candidates"][0]
        self.assertEqual(candidate["rating_float_candidate"]["value"], 4.0)
        self.assertEqual(candidate["minute_interval_candidate"]["difference"], 90)
        self.assertIn("unverified", candidate["match_binding"])
        self.assertIn("goals_assists", result["unknown_fields"])

    def test_invalid_or_nan_rating_is_not_a_candidate(self):
        # Real T4 regression: another pair structure's integer 255 was read as
        # a float32 denormal and incorrectly presented as a rating candidate.
        for rating in (float("nan"), float("inf"), 10.5, struct.unpack("<f", struct.pack("<I", 255))[0]):
            result = research_bal({}, self.changed(0xBE6698 + 20, struct.pack("<f", rating)))
            self.assertEqual(result["personal_stat_candidates"], [])

    def test_fixture_fields_require_completed_fixture_and_roster_ownership(self):
        base = SCHEDULE_BASE + 0x24
        body = bytearray(self.blocks["data"])
        struct.pack_into("<II", body, base + 8, 2 | (1 << 8), 90 | (58 << 7) | (6 << 14) | (3 << 21))
        report = research_bal({}, {**self.blocks, "data": bytes(body)})
        candidate = report["club_fixtures"][0]["fixture_player_rows_raw"][0]["stat_field_candidates"]
        self.assertEqual((candidate["goals_candidate"], candidate["assists_candidate"],
                          candidate["minutes_candidate"], candidate["rating_candidate"]), (2, 1, 90, 5.8))
        self.assertFalse(candidate["appearance_verified"])
        struct.pack_into("<I", body, SCHEDULE_BASE + 4, 0x2000004F)
        unplayed = research_bal({}, {**self.blocks, "data": bytes(body)})["club_fixtures"][0]
        self.assertIsNone(unplayed["fixture_player_rows_raw"][0]["stat_field_candidates"])
        self.assertIsNone(unplayed["target_roster_slots"][0]["stat_field_candidates"])
        struct.pack_into("<I", body, SCHEDULE_BASE + 4, 0x6000004F)
        struct.pack_into("<I", body, base + 4, 999)
        foreign = research_bal({}, {**self.blocks, "data": bytes(body)})["club_fixtures"][0]
        self.assertIsNone(foreign["fixture_player_rows_raw"][0]["stat_field_candidates"])

    def test_cumulative_buckets_stay_separate_and_missing_is_not_zero(self):
        self.assertEqual(research_bal({}, self.blocks)["cumulative_stat_candidates"], [])
        body = bytearray(self.blocks["data"])
        body.extend(bytes(CUMULATIVE_BASE + CUMULATIVE_SIZE * CUMULATIVE_CAPACITY - len(body)))
        struct.pack_into("<11I", body, CUMULATIVE_BASE, 0, 2147483834,
                         3 | (2 << 16), 1 | (2 << 16), 2 | (98 << 16), 0, 0, 0,
                         5 | (4 << 16), 3 | (4 << 16), 4 | (198 << 16))
        result = research_bal({}, {**self.blocks, "data": bytes(body)})
        buckets = result["cumulative_stat_candidates"][0]["buckets"]
        self.assertEqual([b["appearances_high16_candidate"] for b in buckets], [2, 4])
        self.assertTrue(all("unverified" in b["ownership"] for b in buckets))
        self.assertFalse(result["d1_complete"])
        struct.pack_into("<II", body, CUMULATIVE_BASE + CUMULATIVE_SIZE, 0, 2147483834)
        with self.assertRaisesRegex(SaveError, "Duplicate target in cumulative"):
            research_bal({}, {**self.blocks, "data": bytes(body)})

    def test_same_native_id_with_different_reference_is_only_a_binding_diagnostic(self):
        body = bytearray(self.blocks["data"])
        body.extend(bytes(CUMULATIVE_BASE + CUMULATIVE_SIZE * CUMULATIVE_CAPACITY - len(body)))
        struct.pack_into("<11I", body, CUMULATIVE_BASE, 99, 2147483834,
                         207, 12, 13903, 0, 0, 0, 207, 12, 13903)
        result = research_bal({}, {**self.blocks, "data": bytes(body)})
        self.assertEqual(result["cumulative_stat_candidates"], [])
        diagnostic = result["cumulative_binding_diagnostics"][0]
        self.assertEqual((diagnostic["expected_packed_index_raw"], diagnostic["actual_packed_index_raw"]), (0, 99))
        self.assertIn("unverified", diagnostic["binding"])
        self.assertFalse(result["d1_complete"])

    def test_unplayed_zero_score_is_not_reported_as_a_draw(self):
        upcoming = research_bal({}, self.changed(SCHEDULE_BASE + 4, struct.pack("<I", 0x2000004F)))
        self.assertIsNone(upcoming["club_fixtures"][0]["score_candidate"])
        completed = research_bal({}, self.changed(SCHEDULE_BASE + 0x1C, bytes([4, 0, 0, 2, 0, 0])))
        candidate = completed["club_fixtures"][0]["score_candidate"]
        self.assertEqual((candidate["home"], candidate["away"]), (4, 2))
        self.assertIn("unverified", candidate["validation"])
        self.assertFalse(completed["match_records_verified"])

    def test_penalty_result_is_separate_and_unplayed_bytes_are_not_results(self):
        blocks = self.changed(SCHEDULE_BASE + 0x1C, bytes([0, 0, 6, 0, 0, 5]))
        row = research_bal({}, blocks)["club_fixtures"][0]
        self.assertEqual((row["score_candidate"]["home"], row["score_candidate"]["away"]), (0, 0))
        self.assertEqual((row["penalty_shootout_candidate"]["home"], row["penalty_shootout_candidate"]["away"]), (6, 5))
        body = bytearray(blocks["data"])
        struct.pack_into("<I", body, SCHEDULE_BASE + 4, 0x2000004F)
        unplayed = research_bal({}, {**blocks, "data": bytes(body)})["club_fixtures"][0]
        self.assertIsNone(unplayed["score_candidate"])
        self.assertIsNone(unplayed["penalty_shootout_candidate"])
        self.assertIsNone(research_bal({}, self.blocks)["club_fixtures"][0]["penalty_shootout_candidate"])

    def test_missing_club_native_id_binding_is_rejected(self):
        with self.assertRaisesRegex(SaveError, "roster binding"):
            research_bal({}, self.changed(0x50 + 0x14C + 4, struct.pack("<I", 1)))

    def test_date_and_duplicate_roster_target_are_rejected(self):
        with self.assertRaisesRegex(SaveError, "fixture date"):
            research_bal({}, self.changed(SCHEDULE_BASE + 10, bytes([0])))
        with self.assertRaisesRegex(SaveError, "Duplicate target"):
            research_bal({}, self.changed(SCHEDULE_BASE + 0x24 + 16, struct.pack("<4I", 0, 2147483834, 0, 0)))

    def test_wrong_team_reference_and_truncation_are_rejected(self):
        with self.assertRaisesRegex(SaveError, "index mismatch"):
            research_bal({}, self.changed(0x50 + 1680, struct.pack("<I", 99)))
        with self.assertRaisesRegex(SaveError, "Truncated"):
            parse_teams(self.blocks["data"][:SCHEDULE_BASE + SCHEDULE_CAPACITY * 596 - 1])
        missing_stats = research_bal({}, {**self.blocks, "data": self.blocks["data"][:0xB7E494]})
        self.assertEqual(missing_stats["personal_stat_candidates"], [])
        self.assertFalse(missing_stats["match_records_verified"])


if __name__ == "__main__":
    unittest.main()
