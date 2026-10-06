import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from game_adapters.pes2021_save import SaveError
from game_adapters.pes2021_interface import capture_slot, compare_reports, prepare_slot_reuse, stats_preview


def fixture_report(digest="old"):
    return {"snapshot": {"native_player_id": 123, "identity": {"name": "Fixture"},
                         "saved_game_date": "2020-08-27"},
            "source": {"source_sha256": digest},
            "personal_stat_candidates": [{"record_sha256": "same-personal-record"}],
            "club_fixtures": [{"game_date": "2020-08-29", "home": {"team_index_raw": 1},
                               "away": {"team_index_raw": 2}, "sequence_raw": 17,
                               "record_sha256": digest, "flags_raw": 0}]}


class InterfaceTests(unittest.TestCase):
    def test_replay_and_rollback_require_review_but_container_hash_is_not_an_event(self):
        before, after = fixture_report(), fixture_report("new")
        before["club_fixtures"][0]["bit30_completed_candidate"] = True
        after["club_fixtures"][0]["bit30_completed_candidate"] = True
        replay = compare_reports(before, after)["identity_checks"]
        self.assertTrue(replay["requires_branch_review"])
        self.assertEqual(len(replay["previously_completed_anchors_changed"]), 1)
        after["club_fixtures"] = copy.deepcopy(before["club_fixtures"])
        same_node = compare_reports(before, after)["identity_checks"]
        self.assertFalse(same_node["requires_branch_review"])
        self.assertFalse(same_node["save_hash_is_event_identity"])
        self.assertFalse(same_node["branch_verified"])
        after["snapshot"]["saved_game_date"] = "2020-08-22"
        self.assertTrue(compare_reports(before, after)["identity_checks"]["saved_date_decreased"])

    def test_stats_keeps_last_personal_fixture_separate_from_latest_club_and_empty_cache(self):
        report = fixture_report()
        report['personal_stat_candidates'] = []
        report['missing_semantics'] = {'personal_stat_candidates_empty': 'unavailable, not zero'}
        first = report['club_fixtures'][0]
        first.update(table_slot_raw=17, bit30_completed_candidate=True, score_candidate={'home': 1, 'away': 1},
                     penalty_shootout_candidate={'home': 6, 'away': 5},
                     target_roster_slots=[{'data_offset': 100, 'stat_field_candidates': {'minutes_candidate': 90, 'rating_candidate': 4}}])
        later = copy.deepcopy(first)
        later.update(game_date='2020-09-12',sequence_raw=18,table_slot_raw=18,target_roster_slots=[])
        report['club_fixtures'].append(later)
        result = stats_preview(report)
        self.assertEqual(len(result['single_match_candidates']), 1)
        self.assertEqual(result['single_match_candidates'][0]['game_date'], '2020-08-29')
        self.assertEqual(result['single_match_candidates'][0]['penalty_shootout_candidate'], {'home': 6, 'away': 5})
        self.assertEqual(result['latest_completed_club_fixture']['game_date'], '2020-09-12')
        self.assertFalse(result['latest_completed_club_fixture']['target_row_found'])
        self.assertEqual(result['recent_24b_cache']['availability'], 'unavailable')
        self.assertIsNone(result['cumulative_stat_candidates'])
        self.assertTrue(all(value is None for value in result['unavailable_single_match_details'].values()))
        self.assertFalse(result['match_records_verified'])
        report['club_fixtures'] = []
        self.assertIsNone(stats_preview(report)['latest_completed_club_fixture'])

    def test_reuse_refuses_protected_slot_and_wrong_player_before_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "archive"
            with self.assertRaisesRegex(SaveError, "Protected Chelsea"):
                prepare_slot_reuse(root, 1, output, 123, raw_root=root)
            source = root / "BL00000001"
            blob = b"a" * 600
            source.write_bytes(blob)
            with patch("game_adapters.pes2021_interface.decrypt_container", return_value=({}, {})), patch(
                    "game_adapters.pes2021_interface.research_bal", return_value=fixture_report()):
                with self.assertRaisesRegex(SaveError, "different player"):
                    prepare_slot_reuse(root, 2, output, 999, raw_root=root)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), blob)

    def test_reuse_ticket_preserves_exact_snapshot_and_never_overwrites_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "archive"
            source = root / "BL00000001"
            blob = b"a" * 600
            source.write_bytes(blob)
            with patch("game_adapters.pes2021_interface.decrypt_container", return_value=({}, {})), patch(
                    "game_adapters.pes2021_interface.research_bal", return_value=fixture_report()):
                ticket = prepare_slot_reuse(root, 2, output, 123, raw_root=root)
                self.assertEqual((output / "save.bin").read_bytes(), blob)
                self.assertEqual(ticket["native_player_id"], 123)
                self.assertEqual(ticket["protected_slots"], [1])
                self.assertEqual(ticket["game_writes"], 0)
                self.assertEqual(source.read_bytes(), blob)
                self.assertEqual(json.loads((output / "reuse-ticket.json").read_text())["archived_sha256"], ticket["archived_sha256"])
                with self.assertRaises(SaveError):
                    prepare_slot_reuse(root, 2, output, 123, raw_root=root)

    def test_changed_fixture_retains_stale_candidate_warning(self):
        after = fixture_report("new")
        after["snapshot"]["saved_game_date"] = "2020-08-30"
        report = compare_reports(fixture_report(), after)
        self.assertEqual(report["changed_fixture_count"], 1)
        self.assertTrue(report["personal_candidate_records_identical"])
        self.assertIn("stale", report["interpretation"])
        self.assertFalse(report["match_records_verified"])
        self.assertFalse(report["skipped_match_collection_verified"])
        self.assertEqual(compare_reports(after, after)["changed_fixture_count"], 0)

    def test_empty_recent_cache_does_not_hide_cumulative_change(self):
        before, after = fixture_report(), fixture_report("new")
        before["personal_stat_candidates"] = after["personal_stat_candidates"] = []
        before["cumulative_stat_candidates"] = [{"record_sha256": "one-appearance"}]
        after["cumulative_stat_candidates"] = [{"record_sha256": "two-appearances"}]
        result = compare_reports(before, after)
        self.assertTrue(result["personal_candidate_records_identical"])
        self.assertFalse(result["cumulative_candidate_records_identical"])
        self.assertFalse(result["match_records_verified"])
        del before["cumulative_stat_candidates"]
        historical = compare_reports(before, after)
        self.assertIsNone(historical["cumulative_stat_candidates"]["before"])
        self.assertIsNone(historical["cumulative_candidate_records_identical"])

    def test_ambiguous_anchor_and_different_player_refused(self):
        after = fixture_report()
        after["club_fixtures"].append(copy.deepcopy(after["club_fixtures"][0]))
        with self.assertRaisesRegex(SaveError, "Ambiguous"):
            compare_reports(fixture_report(), after)
        after = fixture_report()
        after["snapshot"]["native_player_id"] = 999
        with self.assertRaisesRegex(SaveError, "Different target"):
            compare_reports(fixture_report(), after)
        after = fixture_report()
        after["snapshot"]["identity"]["name"] = "Different career with the same native ID"
        with self.assertRaisesRegex(SaveError, "Different target"):
            compare_reports(fixture_report(), after)

    def test_capture_preserves_source_and_refuses_overwrite_or_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "BL00000001"
            blob = b"fixture" * 100
            source.write_bytes(blob)
            isolated = root / "raw"
            isolated.mkdir()
            output = isolated / "capture"
            with patch("game_adapters.pes2021_interface.decrypt_container", return_value=({}, {})), patch(
                    "game_adapters.pes2021_interface.research_bal", return_value=fixture_report()):
                result = capture_slot(root, 2, output, raw_root=isolated)
                self.assertEqual(source.read_bytes(), blob)
                self.assertEqual((output / "save.bin").read_bytes(), blob)
                self.assertTrue(result["source"]["original_unchanged_at_copy"])
                self.assertEqual(json.loads((output / "report.json").read_text(encoding="utf-8"))["source"]["slot"], 2)
                for target, slot in ((output, 2), (root / "escape", 2), (isolated / "badslot", 0)):
                    with self.assertRaises(SaveError):
                        capture_slot(root, slot, target, raw_root=isolated)

    def test_changing_source_rejected_before_dataset_created(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "BL00000001"
            source.write_bytes(b"a" * 600)
            output = root / "capture"
            def external_change(blob):
                source.write_bytes(b"b" * 600)
                return {}, {}
            with patch("game_adapters.pes2021_interface.decrypt_container", side_effect=external_change), patch(
                    "game_adapters.pes2021_interface.research_bal", return_value=fixture_report()):
                with self.assertRaisesRegex(SaveError, "changed during capture"):
                    capture_slot(root, 2, output, raw_root=root)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
