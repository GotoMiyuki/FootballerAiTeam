"""Synthetic structure tests; real save decryption evidence is kept locally."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from game_adapters.pes2021_save import SaveError, bits, decrypt_container, mt_by_array, parse_bal_snapshot, read_copy, verify_block_hashes
from game_adapters.pes2021_memory_probe import inspect_record, probe


class NativeSaveTests(unittest.TestCase):
    def setUp(self):
        header = bytearray(208)
        header[144:146] = b"BL"
        header[176:207] = b"eFootball PES 2021 SEASON UPDATE"
        description = bytearray(384)
        text = b"Fixture Player\nFixture Club / Fixture League\n2020/8/21"
        description[128:128 + len(text)] = text
        body = bytearray(0x200)
        body[0], body[1], body[7], body[0x1C] = 180, 75, 8 << 4, 17
        body[0x30:0x34] = (2147483834).to_bytes(4, "little")
        body[0x38:0x47] = b"Fixture Player\0"
        self.blocks = {"header": bytes(header), "description": bytes(description), "data": bytes(body)}

    def parse(self):
        return parse_bal_snapshot({}, self.blocks)

    def test_mt_matches_published_reference_vector(self):
        # Original MT19937 init_by_array example with four seed words.
        rng = mt_by_array([0x123, 0x234, 0x345, 0x456])
        self.assertEqual([rng.getrandbits(32) for _ in range(10)],
                         [1067595299, 955945823, 477289528, 4107218783, 4228976476,
                          3344332714, 3355579695, 227628506, 810200273, 2591290167])

    def test_native_identity_and_unknowns_are_explicit(self):
        before = copy.deepcopy(self.blocks)
        result = self.parse()
        self.assertEqual(result["native_player_id"], 2147483834)
        self.assertEqual(result["profile"]["age"], 17)
        self.assertEqual(result["profile"]["registered_position"], "AMF")
        self.assertEqual(result["identity"]["club"], "Fixture Club")
        self.assertEqual(result["saved_game_date"], "2020-08-21")
        self.assertIn("match_records", result["unknown_fields"])
        self.assertIn("overall_rating", result["unknown_fields"])
        self.assertEqual(result["attributes"]["stamina"]["validation"],
                         "upstream_layout_candidate_pending_field_comparison")
        self.assertEqual(self.blocks, before)

    def test_cross_byte_bits_and_bounds(self):
        self.assertEqual(bits(bytes([0x80, 0x03]), 0, 7, 7), 7)
        for offset in (-1, 1):
            with self.assertRaises(SaveError):
                bits(b"\0", offset, 0, 8)

    def test_ambiguous_player_records_are_rejected(self):
        self.blocks["data"] *= 2
        with self.assertRaisesRegex(SaveError, "Ambiguous"):
            self.parse()

    def test_missing_player_and_wrong_mode_are_rejected(self):
        self.blocks["data"] = bytes(512)
        with self.assertRaises(SaveError):
            self.parse()
        self.blocks["header"] = bytes(208)
        with self.assertRaisesRegex(SaveError, "container type/version"):
            self.parse()

    def test_invalid_native_date_is_rejected(self):
        self.blocks["description"] = self.blocks["description"].replace(b"2020/8/21", b"2020/0/21")
        with self.assertRaisesRegex(SaveError, "Invalid saved game date"):
            self.parse()

    def test_truncated_and_unrelated_containers_are_rejected(self):
        for blob in (b"", bytes(527), bytes(600)):
            with self.assertRaises(SaveError):
                decrypt_container(blob)

    def test_block_hashes_detect_corruption_and_wrong_order(self):
        blocks = {name: name.encode() for name in ("description", "logo", "data", "serial")}
        header = b"".join(hashlib.sha512(blocks[name]).digest() for name in blocks) + bytes(64)
        self.assertTrue(all(verify_block_hashes(header, blocks)["blocks_verified"].values()))
        for name in blocks:
            corrupted = {**blocks, name: blocks[name] + b"x"}
            with self.assertRaisesRegex(SaveError, name + " SHA512"):
                verify_block_hashes(header, corrupted)
        with self.assertRaises(SaveError):
            verify_block_hashes(header[64:128] + header[:64] + header[128:], blocks)

    def test_original_location_cannot_be_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SaveError, "isolated"):
                read_copy(Path(directory) / "BL00000001", raw_root=Path(directory) / "raw")

    def test_memory_candidate_requires_exact_native_id_and_name(self):
        record = self.blocks["data"][:0x17C]
        result = inspect_record(record, 2147483834, "Fixture Player")
        self.assertEqual(result["age"], 17)
        self.assertIn("unverified", result["validation"])
        self.assertIsNone(inspect_record(record, 1, "Fixture Player"))
        self.assertIsNone(inspect_record(record, 2147483834, "Other Player"))
        self.assertIsNone(inspect_record(record[:-1], 2147483834, "Fixture Player"))

    def test_invalid_probe_target_is_rejected_before_process_access(self):
        for name, player_id in (("", 1), ("A", 0), ("A" * 61, 1)):
            with self.assertRaisesRegex(RuntimeError, "Invalid target"):
                probe(1, player_id, name)


if __name__ == "__main__":
    unittest.main()
