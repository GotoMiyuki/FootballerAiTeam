"""Synthetic packages in a temporary fixture root; no live game or application writes."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from game_adapters.pes2021_pages import PackageError, VERSION, read_capture, read_dataset


class PageReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / "raw"
        self.root = self.raw / "fixtures" / "synthetic-bal"
        self.capture = self.root / "captures" / "t0"
        (self.capture / "pages").mkdir(parents=True)
        # These bytes are explicitly synthetic integrity-test data, not a real game screenshot.
        self.page = self.capture / "pages" / "identity.jpg"
        self.page.write_bytes(b"synthetic screenshot bytes; not a game observation")
        self.identity = {"career_id": "fixture-career", "branch_id": "main", "player_id": "fixture-player"}
        self.env = {"schema_version": 1, "purpose": "fixtures", "game_id": "pes2021",
                    "game_version": "1.07.01", "data_pack": "7.00", "patch_label": "SmokePatch V4",
                    "mode": "become_a_legend", "collection_method": "manual_page_review"}
        self.binding = {"schema_version": 1, "status": "verified", "identity": self.identity,
                        "native": {"name": "Fixture Player", "club": "Fixture Club"},
                        "confirmation": {"reference": "setup/binding-confirmation.json"}}
        self.manifest = {"schema_version": 1, "adapter_version": VERSION, "capture_id": "t0",
                         "identity": self.identity, "captured_at": "2026-10-02T15:00:00Z",
                         "game_time": {"value": None, "precision": "unknown", "reason": "Not visible"},
                         "quality": "complete", "event_ref": None,
                         "pages": [{"id": "identity", "path": "pages/identity.jpg",
                                    "sha256": hashlib.sha256(self.page.read_bytes()).hexdigest()}]}
        self.fields = {"schema_version": 1, "capture_id": "t0", "fields": {
            "identity.name": self.observed("Fixture Player", "identity"),
            "identity.club": self.observed("Fixture Club", "identity"),
            "profile.overall_rating": self.observed(70, "snapshot"),
            "season.appearances": self.observed(0, "season"),
            "season.average_rating": {"state": "not_applicable", "value": None, "reason": "--- displayed"},
            "match.rating": {"state": "pending_review", "value": 8.5, "reason": "Unconfirmed recognition"}}}
        self.ledger = {"schema_version": 1, "identity": self.identity, "events": []}
        self.write("setup/binding-confirmation.json", {"identity": self.identity, "status": "verified",
                                                      "purpose": "synthetic fixture only"})
        self.flush()

    @staticmethod
    def observed(value, scope):
        return {"state": "observed", "value": value, "source": "page_review", "scope": scope,
                "review": {"status": "reviewed", "reviewer": "assistant"},
                "evidence": [{"page_id": "identity", "label": "Synthetic field", "region": "Synthetic region"}]}

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def flush(self):
        for name, value in [("environment.json", self.env), ("binding.json", self.binding),
                            ("event_ledger.json", self.ledger), ("captures/t0/manifest.json", self.manifest),
                            ("captures/t0/verified_fields.json", self.fields)]:
            self.write(name, value)

    def read(self):
        return read_capture(self.root, "t0", raw_root=self.raw)

    def assert_rejected(self, code):
        with self.assertRaises(PackageError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, code)

    def add_match(self):
        self.manifest["event_ref"] = "match-001"
        self.fields["fields"]["match.participation"] = self.observed("started", "match")
        self.fields["fields"]["match.goals"] = self.observed(0, "match")
        self.ledger["events"] = [{"event_id": "match-001", "sequence": 1, "capture_ids": ["t0"],
                                  "status": "completed", "confirmation_reference": "setup/match-confirmation.json"}]
        self.write("setup/match-confirmation.json", {"identity": self.identity, "event_id": "match-001",
                                                    "status": "completed", "purpose": "synthetic fixture only"})
        self.flush()

    def repeat_capture(self):
        repeated = self.root / "captures" / "repeat"
        (repeated / "pages").mkdir(parents=True)
        (repeated / "pages" / "identity.jpg").write_bytes(self.page.read_bytes())
        manifest, fields = copy.deepcopy(self.manifest), copy.deepcopy(self.fields)
        manifest["capture_id"] = fields["capture_id"] = "repeat"
        self.write("captures/repeat/manifest.json", manifest)
        self.write("captures/repeat/verified_fields.json", fields)
        self.ledger["events"][0]["capture_ids"].append("repeat")
        self.write("event_ledger.json", self.ledger)
        return fields

    def test_real_zero_unknown_and_unconfirmed_value_remain_distinct(self):
        preview = self.read()
        self.assertEqual(preview["observed_fields"]["season.appearances"]["value"], 0)
        self.assertEqual(preview["unknown_fields"]["season.average_rating"]["state"], "not_applicable")
        self.assertNotIn("match.rating", preview["observed_fields"])
        self.assertNotIn("value", preview["unknown_fields"]["match.rating"])
        self.assertTrue(preview["snapshot_ready"])
        self.assertFalse(preview["match_ready"])
        self.assertEqual(preview["purpose"], "fixtures")

    def test_deterministic_read_and_all_source_bytes_unchanged(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.read(), self.read())
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_missing_and_changed_page_are_rejected(self):
        self.page.write_bytes(b"truncated")
        self.assert_rejected("page_hash_mismatch")
        self.page.unlink()
        self.assert_rejected("missing_page")

    def test_page_path_escape_is_rejected(self):
        self.manifest["pages"][0]["path"] = "../../../../outside.jpg"
        self.flush()
        self.assert_rejected("path_escape")

    def test_cross_career_and_profile_identity_are_rejected(self):
        other = {**self.identity, "career_id": "another-career"}
        with self.assertRaises(PackageError) as caught:
            read_capture(self.root, "t0", expected_identity=other, raw_root=self.raw)
        self.assertEqual(caught.exception.code, "identity_mismatch")
        self.fields["fields"]["identity.name"]["value"] = "Other Player"
        self.flush()
        self.assert_rejected("identity_mismatch")

    def test_candidate_binding_retains_raw_but_is_not_ready(self):
        self.binding["status"] = "candidate"
        self.flush()
        self.assertFalse(self.read()["snapshot_ready"])

    def test_environment_and_fixture_provenance_are_not_silently_accepted(self):
        self.env["purpose"] = "real_test"
        self.flush()
        self.assert_rejected("source_mismatch")
        self.env["purpose"] = "fixtures"
        self.env["game_version"] = "other-version"
        self.flush()
        self.assert_rejected("unsupported_environment")

    def test_incomplete_pages_and_unreviewed_observation_are_rejected(self):
        self.manifest["quality"] = "partial"
        self.flush()
        self.assert_rejected("incomplete_capture")
        self.manifest["quality"] = "complete"
        self.fields["fields"]["profile.overall_rating"]["review"]["status"] = "pending"
        self.flush()
        self.assert_rejected("unreviewed_value")

    def test_match_fields_need_completed_event_and_match_scope(self):
        self.fields["fields"]["match.goals"] = self.observed(1, "season")
        self.flush()
        self.assert_rejected("invalid_field_source")
        self.fields["fields"]["match.goals"]["scope"] = "match"
        self.flush()
        self.assert_rejected("unbound_match")
        self.add_match()
        self.ledger["events"][0]["status"] = "scheduled"
        self.flush()
        self.assert_rejected("unconfirmed_event")

    def test_same_event_repeated_capture_is_one_match(self):
        self.add_match()
        self.repeat_capture()
        preview = read_dataset(self.root, raw_root=self.raw)
        self.assertEqual(preview["unique_completed_matches"], 1)
        self.assertEqual(set(preview["events"][0]["capture_ids"]), {"t0", "repeat"})
        self.assertTrue(preview["d1_minimum_sample_ready"])
        self.assertFalse(preview["real_game_sample_ready"])

    def test_same_event_changed_statistics_raise_conflict(self):
        self.add_match()
        fields = self.repeat_capture()
        fields["fields"]["match.goals"]["value"] = 2
        self.write("captures/repeat/verified_fields.json", fields)
        with self.assertRaises(PackageError) as caught:
            read_dataset(self.root, raw_root=self.raw)
        self.assertEqual(caught.exception.code, "event_conflict")

    def test_duplicate_json_keys_and_malformed_time_are_rejected(self):
        (self.capture / "verified_fields.json").write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        self.assert_rejected("duplicate_json_key")
        self.manifest["game_time"] = "not an object"
        self.flush()
        self.assert_rejected("invalid_game_time")

    def test_malformed_container_values_have_explicit_rejection(self):
        cases = [
            (self.binding, "status", "invalid_binding"),
            (self.manifest["game_time"], "precision", "invalid_game_time"),
            (self.fields["fields"]["identity.name"], "state", "invalid_field_state"),
            (self.fields["fields"]["identity.name"]["review"], "reviewer", "unreviewed_value"),
            (self.fields["fields"]["identity.name"]["evidence"][0], "page_id", "invalid_evidence"),
        ]
        for target, key, code in cases:
            with self.subTest(key=key):
                original = target[key]
                target[key] = []
                self.flush()
                self.assert_rejected(code)
                target[key] = original
        self.flush()

    def test_dataset_root_is_checked_before_directory_read(self):
        with self.assertRaises(PackageError) as caught:
            read_dataset(Path(self.tmp.name) / "outside", raw_root=self.raw)
        self.assertEqual(caught.exception.code, "source_root")

    def test_match_rating_cannot_be_a_text_or_boolean_value(self):
        self.add_match()
        for value in ("6.0", True, -1):
            self.fields["fields"]["match.rating"] = self.observed(value, "match")
            self.flush()
            self.assert_rejected("invalid_value")

    def test_confirmation_must_exist_and_match_context_and_event(self):
        self.binding["confirmation"]["reference"] = "setup/missing.json"
        self.flush()
        self.assert_rejected("invalid_json")
        self.binding["confirmation"]["reference"] = "setup/binding-confirmation.json"
        self.write("setup/binding-confirmation.json", {"identity": {**self.identity, "branch_id": "other"}, "status": "verified"})
        self.flush()
        self.assert_rejected("invalid_binding")
        self.write("setup/binding-confirmation.json", {"identity": self.identity, "status": "verified"})
        self.add_match()
        self.write("setup/match-confirmation.json", {"identity": self.identity, "event_id": "other", "status": "completed"})
        self.assert_rejected("unconfirmed_event")


if __name__ == "__main__":
    unittest.main()
