"""Read reviewed PES BAL page packages offline. Never import application facts.

Hashes check file integrity, not authenticity. Reviewed fields retain their
reviewer and page evidence; they are not automatically user-confirmed facts.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

RAW_ROOT = Path(__file__).resolve().parent / "raw"
VERSION = "pes2021-bal-pages-v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
FIELD = re.compile(r"(?:identity|profile|season|career|schedule|match)\.[a-z][a-z0-9_]*\Z")
PARTICIPATION = {"started", "substitute", "played_unknown_role", "unused_substitute", "not_selected"}
MISSING = {"not_provided", "not_captured", "unreadable", "pending_review", "parse_error", "not_applicable"}


class PackageError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def require(condition, code, detail):
    if not condition:
        raise PackageError(code, detail)


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key", key)
        result[key] = value
    return result


def local_path(root: Path, relative: str) -> Path:
    require(isinstance(relative, str) and relative and "\\" not in relative,
            "invalid_path", str(relative))
    path = (root / relative).resolve()
    require(not Path(relative).is_absolute() and path.is_relative_to(root.resolve()),
            "path_escape", relative)
    return path


def read_json(path: Path):
    try:
        require(path.stat().st_size <= 1_048_576, "json_too_large", path.name)
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(
                               PackageError("invalid_number", value)))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError("invalid_json", path.name) from exc
    require(isinstance(value, dict), "invalid_object", path.name)
    return value


def sha256(path: Path):
    try:
        require(0 < path.stat().st_size <= 32_000_000, "invalid_page_size", path.name)
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PackageError("missing_page", path.name) from exc


def valid_identity(value):
    require(isinstance(value, dict) and set(value) == {"career_id", "branch_id", "player_id"},
            "invalid_identity", "Three explicit identity components required")
    require(all(isinstance(v, str) and ID.fullmatch(v) for v in value.values()),
            "invalid_identity", "Invalid identity component")
    return value


def reviewed_evidence(evidence, pages):
    require(isinstance(evidence, list) and evidence, "missing_evidence", "No page reference")
    for ref in evidence:
        require(isinstance(ref, dict) and isinstance(ref.get("page_id"), str) and ref["page_id"] in pages
                and isinstance(ref.get("label"), str) and ref["label"].strip()
                and isinstance(ref.get("region"), str) and ref["region"].strip(),
                "invalid_evidence", "Unknown page or missing label/region")


def read_capture(dataset: Path, capture_id: str, *, expected_identity=None, raw_root=RAW_ROOT):
    dataset, raw_root = Path(dataset).resolve(), Path(raw_root).resolve()
    purpose = next((kind for kind in ("real_test", "fixtures")
                    if dataset.is_relative_to(raw_root / kind)), None)
    require(purpose is not None, "source_root", "Package must be inside raw/real_test or raw/fixtures")
    require(isinstance(capture_id, str) and ID.fullmatch(capture_id), "invalid_capture_id", str(capture_id))
    environment = read_json(local_path(dataset, "environment.json"))
    require(environment.get("schema_version") == 1 and environment.get("purpose") == purpose,
            "source_mismatch", "Environment provenance does not match the storage root")
    require((environment.get("game_id"), environment.get("game_version"), environment.get("data_pack"),
             environment.get("patch_label"), environment.get("mode"), environment.get("collection_method"))
            == ("pes2021", "1.07.01", "7.00", "SmokePatch V4", "become_a_legend", "manual_page_review"),
            "unsupported_environment", "Only the verified BAL page collection environment is supported")
    binding = read_json(local_path(dataset, "binding.json"))
    require(binding.get("schema_version") == 1 and isinstance(binding.get("status"), str)
            and binding["status"] in {"candidate", "verified"},
            "invalid_binding", "Explicit candidate or verified binding required")
    identity = valid_identity(binding.get("identity"))
    if expected_identity is not None:
        require(identity == valid_identity(expected_identity), "identity_mismatch", "Different career/branch/player")
    if binding["status"] == "verified":
        basis = binding.get("confirmation", {})
        require(isinstance(basis, dict) and isinstance(basis.get("reference"), str) and basis["reference"].strip(),
                "invalid_binding", "Verified binding needs a confirmation reference")
        confirmation = read_json(local_path(dataset, basis["reference"]))
        require(confirmation.get("identity") == identity and confirmation.get("status") == "verified",
                "invalid_binding", "Confirmation identity/status does not match the binding")
    capture = local_path(dataset, f"captures/{capture_id}")
    manifest = read_json(local_path(capture, "manifest.json"))
    require(manifest.get("schema_version") == 1 and manifest.get("adapter_version") == VERSION
            and manifest.get("capture_id") == capture_id, "invalid_manifest", "Schema, adapter or capture ID")
    require(manifest.get("identity") == identity, "identity_mismatch", "Capture and binding disagree")
    require(manifest.get("quality") == "complete", "incomplete_capture", "Incomplete pages cannot supply observations")
    require(isinstance(manifest.get("captured_at"), str), "invalid_capture_time", "Timestamp string required")
    try:
        timestamp = datetime.fromisoformat(manifest["captured_at"].replace("Z", "+00:00"))
        require(timestamp.tzinfo is not None, "invalid_capture_time", "Timezone required")
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("invalid_capture_time", "Invalid collection time") from exc
    game_time = manifest.get("game_time", {})
    require(isinstance(game_time, dict) and isinstance(game_time.get("precision"), str)
            and game_time["precision"] in {"unknown", "day"},
            "invalid_game_time", "Unknown or day precision required")
    if game_time["precision"] == "unknown":
        require(game_time.get("value") is None and bool(game_time.get("reason")),
                "invalid_game_time", "Unknown date must have a reason and no substituted system date")
    else:
        try:
            date.fromisoformat(game_time["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PackageError("invalid_game_time", "Invalid game date") from exc
    pages = {}
    require(isinstance(manifest.get("pages"), list) and manifest["pages"], "missing_page", "No pages")
    for page in manifest["pages"]:
        require(isinstance(page, dict) and isinstance(page.get("id"), str) and ID.fullmatch(page["id"]),
                "invalid_page", "Invalid page ID")
        require(page["id"] not in pages, "duplicate_page", page["id"])
        path = local_path(capture, page.get("path"))
        require(path.suffix.lower() in {".jpg", ".jpeg", ".png"}, "invalid_page", "Image reference required")
        require(sha256(path) == page.get("sha256"), "page_hash_mismatch", page["id"])
        pages[page["id"]] = page
    fields = read_json(local_path(capture, "verified_fields.json"))
    require(fields.get("schema_version") == 1 and fields.get("capture_id") == capture_id,
            "invalid_fields", "Fields schema or capture ID")
    require(isinstance(fields.get("fields"), dict), "invalid_fields", "Field object required")
    observed, unknown = {}, {}
    for name, field in sorted(fields["fields"].items()):
        require(FIELD.fullmatch(name) and isinstance(field, dict), "invalid_field", name)
        state = field.get("state")
        require(isinstance(state, str) and (state == "observed" or state in MISSING), "invalid_field_state", name)
        if state != "observed":
            require(isinstance(field.get("reason"), str) and bool(field["reason"].strip()), "missing_unknown_reason", name)
            require(state == "pending_review" or field.get("value") is None,
                    "invalid_unknown_value", name)
            unknown[name] = {"state": state, "reason": field["reason"]}
            continue
        expected_scope = {"identity": "identity", "profile": "snapshot", "season": "season",
                          "career": "career", "schedule": "fixture", "match": "match"}[name.split(".")[0]]
        require(field.get("source") == "page_review" and field.get("scope") == expected_scope,
                "invalid_field_source", name)
        review = field.get("review", {})
        require(isinstance(review, dict) and review.get("status") == "reviewed"
                and isinstance(review.get("reviewer"), str) and review["reviewer"] in {"assistant", "user"},
                "unreviewed_value", name)
        reviewed_evidence(field.get("evidence"), pages)
        value = field.get("value")
        require(type(value) in {str, int, float, bool} and value != "", "invalid_value", name)
        if type(value) is float:
            require(math.isfinite(value), "invalid_value", name)
        if name in {"profile.overall_rating", "profile.age", "season.appearances", "season.goals",
                    "season.assists", "career.appearances", "career.goals", "career.assists",
                    "match.goals", "match.assists", "match.minutes", "match.team_goals", "match.opponent_goals"}:
            require(type(value) is int and value >= 0, "invalid_value", name)
        if name in {"identity.name", "identity.club"}:
            require(isinstance(binding.get("native"), dict) and value == binding["native"].get(name.split(".")[1]),
                    "identity_mismatch", name)
        if name == "match.participation":
            require(value in PARTICIPATION and field["scope"] == "match", "invalid_participation", name)
        if name in {"match.rating", "season.average_rating", "career.average_rating"}:
            require(type(value) in {int, float} and value >= 0, "invalid_value", name)
        if name.startswith("match."):
            require(field["scope"] == "match", "scope_mismatch", "Cumulative statistics are not match statistics")
        observed[name] = field
    event = None
    event_ref = manifest.get("event_ref")
    if event_ref is not None:
        require(isinstance(event_ref, str) and ID.fullmatch(event_ref), "invalid_event", "Invalid event reference")
        ledger = read_json(local_path(dataset, "event_ledger.json"))
        require(ledger.get("schema_version") == 1 and ledger.get("identity") == identity
                and isinstance(ledger.get("events"), list), "invalid_ledger", "Ledger identity or schema")
        ids, sequences = set(), set()
        for item in ledger["events"]:
            require(isinstance(item, dict) and isinstance(item.get("event_id"), str) and ID.fullmatch(item["event_id"])
                    and type(item.get("sequence")) is int and item["sequence"] > 0,
                    "invalid_event", "Stable manually verified event ID and sequence required")
            require(item["event_id"] not in ids and item["sequence"] not in sequences,
                    "duplicate_event", item["event_id"])
            ids.add(item["event_id"])
            sequences.add(item["sequence"])
            if item["event_id"] == event_ref:
                event = item
        require(event is not None and isinstance(event.get("capture_ids"), list) and capture_id in event["capture_ids"]
                and event.get("status") == "completed" and isinstance(event.get("confirmation_reference"), str)
                and bool(event["confirmation_reference"].strip()),
                "unconfirmed_event", "No completed event with this capture and confirmation reference")
        confirmation = read_json(local_path(dataset, event["confirmation_reference"]))
        require(confirmation.get("identity") == identity and confirmation.get("event_id") == event_ref
                and confirmation.get("status") == "completed", "unconfirmed_event", "Event confirmation disagrees")
    require(event is not None or not any(name.startswith("match.") for name in observed),
            "unbound_match", "Match fields need a verified ledger event")
    return {"adapter_version": VERSION, "capture_id": capture_id, "purpose": purpose,
            "identity": identity, "binding_status": binding["status"], "environment": environment,
            "captured_at": manifest["captured_at"], "game_time": game_time, "event": event,
            "pages": list(pages.values()), "observed_fields": observed, "unknown_fields": unknown,
            "snapshot_ready": binding["status"] == "verified" and "identity.name" in observed
            and "profile.overall_rating" in observed,
            "match_ready": binding["status"] == "verified" and event is not None
            and "match.participation" in observed,
            "requires_user_confirmation_before_import": True, "application_writes": 0}


def read_dataset(dataset: Path, *, raw_root=RAW_ROOT):
    dataset = Path(dataset).resolve()
    raw_root = Path(raw_root).resolve()
    require(any(dataset.is_relative_to(raw_root / kind) for kind in ("real_test", "fixtures")),
            "source_root", "Package must be inside raw/real_test or raw/fixtures")
    try:
        captures = sorted(path.name for path in local_path(dataset, "captures").iterdir() if path.is_dir())
    except OSError as exc:
        raise PackageError("missing_capture", "No capture directory") from exc
    require(bool(captures), "missing_capture", "No capture packages")
    previews = [read_capture(dataset, name, raw_root=raw_root) for name in captures]
    events = {}
    for preview in previews:
        if not preview["match_ready"]:
            continue
        event = preview["event"]
        bucket = events.setdefault(event["event_id"], {"event_id": event["event_id"],
                                  "sequence": event["sequence"], "capture_ids": [], "fields": {}})
        bucket["capture_ids"].append(preview["capture_id"])
        for name, field in preview["observed_fields"].items():
            if not name.startswith("match."):
                continue
            previous = bucket["fields"].get(name)
            require(previous is None or previous["value"] == field["value"], "event_conflict", name)
            bucket["fields"][name] = field
    return {"adapter_version": VERSION, "purpose": previews[0]["purpose"],
            "identity": previews[0]["identity"], "captures": previews,
            "events": sorted(events.values(), key=lambda event: event["sequence"]),
            "unique_completed_matches": len(events),
            "d1_minimum_sample_ready": any(p["snapshot_ready"] for p in previews) and bool(events),
            "real_game_sample_ready": previews[0]["purpose"] == "real_test"
            and any(p["snapshot_ready"] for p in previews) and bool(events),
            "application_writes": 0, "requires_user_confirmation_before_import": True}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--capture", help="One capture; omitted means all captures grouped by verified event")
    args = parser.parse_args()
    try:
        preview = (read_capture(args.dataset, args.capture) if args.capture
                   else read_dataset(args.dataset))
    except PackageError as exc:
        print(json.dumps({"status": "rejected", "code": exc.code, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "preview_only", **preview}, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
