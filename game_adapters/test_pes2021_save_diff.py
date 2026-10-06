"""Research diff boundaries: changes must not certify match/career semantics."""
import copy
import unittest

from game_adapters.pes2021_save import SaveError
from game_adapters.pes2021_save_diff import byte_changes, compare_decoded
from game_adapters import test_pes2021_save as fixtures


class NativeDiffTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.NativeSaveTests()
        fixture.setUp()
        self.before = fixture.blocks

    def test_identical_read_stays_unknown(self):
        result = compare_decoded({}, self.before, {}, self.before)
        self.assertFalse(result["match_records_verified"])
        self.assertIn("unverified", result["target"]["career_binding"])
        self.assertEqual(result["blocks"]["data"]["changed_bytes_in_common"], 0)
        self.assertEqual(result["candidate_attribute_changes"], {})

    def test_changes_cross_chunk_boundary_and_tail_remain_distinct(self):
        after = bytearray(8192)
        after[4095:4098] = b"abc"
        after += b"tail"
        result = byte_changes(bytes(8192), bytes(after))
        self.assertEqual(result["changed_run_count"], 1)
        self.assertEqual(result["changed_bytes_in_common"], 3)
        self.assertEqual(result["run_samples"][0]["start"], 4095)
        self.assertEqual(result["run_samples"][0]["end_exclusive"], 4098)
        self.assertEqual(result["after_unpaired_tail_bytes"], 4)

    def test_many_changes_keep_exact_counts_with_bounded_samples(self):
        result = byte_changes(bytes(1000), b"\1\0" * 500, sample_limit=2)
        self.assertEqual(result["changed_run_count"], 500)
        self.assertEqual(result["changed_bytes_in_common"], 500)
        self.assertEqual(len(result["run_samples"]), 2)
        self.assertTrue(result["run_samples_truncated"])
        self.assertEqual(byte_changes(b"ab", b"xab")["comparison"], "fixed_offsets_without_insertion_alignment")

    def test_relocated_record_and_changed_candidate_do_not_imply_match(self):
        after = copy.deepcopy(self.before)
        body = bytearray(after["data"])
        body[3] = 64
        after["data"] = b"\0" * 100 + bytes(body)
        result = compare_decoded({}, self.before, {}, after)
        self.assertTrue(result["player_record_offset_changed"])
        self.assertEqual(result["candidate_attribute_changes"]["offensive_awareness"]["after"], 64)
        self.assertFalse(result["match_records_verified"])
        self.assertIn("cause_of_changes", result["unknown_fields"])

    def test_other_target_rejected(self):
        after = copy.deepcopy(self.before)
        body = bytearray(after["data"])
        body[0x30:0x34] = (1).to_bytes(4, "little")
        after["data"] = bytes(body)
        with self.assertRaisesRegex(SaveError, "Different target"):
            compare_decoded({}, self.before, {}, after)


if __name__ == "__main__":
    unittest.main()
