"""Read explicitly reviewed native D1 test packages. No imports or game control.

The local ledger supplies reviewed test context/event identities, never a hash
or a guessed native ID. This is not an automatic career/rollback resolver.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

from game_adapters.pes2021_bal_research import research_bal
from game_adapters.pes2021_pages import PackageError, local_path, object_pairs, require, valid_identity
from game_adapters.pes2021_runtime_career import EXTRA_SIGNATURES, parse_buffers
from game_adapters.pes2021_runtime_stats import SIGNATURES, RECORD_SIZE, parse_player_record
from game_adapters.pes2021_save import RAW_ROOT as SAVE_ROOT, decrypt_container

SCHEMA = "pes2021-native-d1-package-v1"
VERSION = "pes2021-native-reviewed-preview-v1"
RAW_ROOT = SAVE_ROOT.parent
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
ENVIRONMENT = {"game_id": "pes2021", "game_version": "1.07.01", "data_pack": "7.00",
               "patch_label": "SmokePatch V4", "mode": "BAL",
               "collection_method": "native_save_and_runtime_readonly"}


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        require(parsed.utcoffset() is not None, "invalid_time", "Timezone required")
        return parsed
    except (ValueError, TypeError) as error:
        raise PackageError("invalid_time", "Invalid capture/review time") from error


def parse_save_bytes(blob):
    metadata, blocks = decrypt_container(blob)
    report = research_bal(metadata, blocks)
    at = report["snapshot"]["player_data_offset"] + 0x2C
    report["native_player_reference"] = {"native_player_id": report["snapshot"]["native_player_id"],
                                         "packed_index_raw": struct.unpack_from("<I", blocks["data"], at)[0]}
    return report


class Sources:
    def __init__(self, root):
        self.root, self.blobs, self.references, self.saves = root, {}, {}, {}

    def read(self, ref):
        require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "invalid_source", "Path and digest required")
        path = local_path(self.root, ref["path"])
        require(isinstance(ref["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]), "invalid_digest", ref["path"])
        if path in self.references:
            require(self.references[path] == ref["sha256"], "source_conflict", ref["path"])
            return path, self.blobs[path]
        require(len(self.references) < 512 and path.is_file() and 0 < path.stat().st_size <= 64_000_000, "invalid_source", ref["path"])
        require(sum(len(b) for b in self.blobs.values()) + path.stat().st_size <= 134_217_728,
                "source_budget", "Package source bytes exceed 128MiB")
        blob = path.read_bytes()
        require(hashlib.sha256(blob).hexdigest() == ref["sha256"], "source_hash_mismatch", ref["path"])
        require(path not in self.references or self.references[path] == ref["sha256"], "source_conflict", ref["path"])
        self.references[path], self.blobs[path] = ref["sha256"], blob
        return path, blob

    def json(self, ref):
        path, blob = self.read(ref)
        require(len(blob) <= 8_388_608, "json_too_large", ref["path"])
        try:
            value = json.loads(blob.decode("utf-8"), object_pairs_hook=object_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(PackageError("invalid_number", path.name)))
        except (ValueError, UnicodeError) as error:
            raise PackageError("invalid_json", path.name) from error
        require(isinstance(value, dict), "invalid_object", path.name)
        return value

    def save(self, ref):
        path, blob = self.read(ref)
        if path not in self.saves:
            self.saves[path] = parse_save_bytes(blob)
        return self.saves[path]

    def stable(self):
        for path, digest in self.references.items():
            require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "source_changed", path.name)


def verified_review(sources, ref, identity, kind):
    review = sources.json(ref)
    require(review.get("kind") == kind and review.get("identity") == identity
            and review.get("status") == "reviewed" and review.get("reviewer") in {"assistant", "user"},
            "unreviewed_binding", kind)
    timestamp(review.get("reviewed_at"))
    require(isinstance(review.get("method"), str) and bool(review["method"].strip())
            and isinstance(review.get("evidence"), list) and review["evidence"], "missing_evidence", kind)
    for evidence in review["evidence"]:
        sources.read(evidence)
    return review


def check_snapshot(report, native):
    snapshot = report["snapshot"]
    require(snapshot["native_player_id"] == native["native_player_id"]
            and snapshot["identity"]["name"] == native["name"]
            and report["club_binding"]["team_index_raw"] == native["team_index_raw"],
            "native_binding_mismatch", "Player/club differs from reviewed baseline")
    require(report["native_player_reference"] == {"native_player_id": native["native_player_id"], "packed_index_raw": native["packed_index_raw"]},
            "native_binding_mismatch", "Expected exact player reference missing")
    return snapshot


def career_report(sources, ref, anchor, binding):
    report = sources.json(ref)
    require(report.get("schema") == "pes2021-runtime-career-experimental-v1" and report.get("source_mode") == "live_read"
            and report.get("status") == "runtime_career_preview", "unsupported_source", "Native career report required")
    require(report.get("process_instance") == binding["process_instance"], "process_context_mismatch", "Reviewed process instance differs")
    expected = {hex(rva): digest for rva, _, digest in (*SIGNATURES, *EXTRA_SIGNATURES)}
    require(report.get("verified_code_sha256") == expected, "unsupported_build", "Loaded-code profile differs")
    n = binding["native"]
    require(report.get("expected_target") == {"native_player_id": n["native_player_id"], "team_index_raw": n["team_index_raw"],
                                             "packed_index_raw": n["packed_index_raw"]}, "native_binding_mismatch", "Caller references differ")
    try:
        parsed = parse_buffers(bytes.fromhex(report["fixture"]["raw_hex"]), bytes.fromhex(report["recent_cache"]["raw_hex"]),
                               anchor[3], n["native_player_id"], n["team_index_raw"], n["packed_index_raw"])
    except (KeyError, ValueError, TypeError) as error:
        raise PackageError("invalid_native_bytes", "Invalid fixture/cache buffers") from error
    require(parsed["fixture"]["research_anchor"] == anchor, "fixture_mismatch", "Review and native fixture differ")
    require(parsed["fixture"]["sha256"] == report["fixture"]["sha256"]
            and parsed["recent_cache"]["sha256"] == report["recent_cache"]["sha256"], "native_digest_mismatch", "Raw buffer digest differs")
    require(parsed["target"]["state"] in {"absent", "unique_reference_candidate"}, "target_ambiguous", parsed["target"]["state"])
    parsed["captured_at"] = report["captured_at"]
    timestamp(parsed["captured_at"])
    return parsed


def detail_report(sources, ref, binding, side):
    report = sources.json(ref)
    require(report.get("schema") == "pes2021-runtime-player-stats-experimental-v1" and report.get("status") == "runtime_preview",
            "unsupported_source", "Available native detail report required")
    require(report.get("process_instance") == binding["process_instance"], "process_context_mismatch", "Detail process differs")
    require(report.get("verified_code_sha256") == {hex(r): s for r, _, s in SIGNATURES}, "unsupported_build", "Detail code differs")
    rows = []
    try:
        blocks = report["raw_record_blocks"]
        require(isinstance(blocks, list) and [b["side"] for b in blocks] == [0, 1], "invalid_native_bytes", "Two explicit sides required")
        for block in blocks:
            blob = base64.b64decode(block["data"], validate=True)
            require(len(blob) == 40 * RECORD_SIZE and hashlib.sha256(blob).hexdigest() == block["sha256"],
                    "invalid_native_bytes", "Detail capacity/digest differs")
            for slot in range(40):
                row = parse_player_record(blob[slot * RECORD_SIZE:(slot + 1) * RECORD_SIZE])
                if row is not None:
                    rows.append({**row, "side": block["side"], "roster_slot": slot})
    except (KeyError, ValueError, TypeError) as error:
        raise PackageError("invalid_native_bytes", "Invalid detail buffers") from error
    n = binding["native"]
    target = [r for r in rows if r["native_player_id"] == n["native_player_id"]]
    require(len(target) == 1 and target[0]["opaque_header_u32"] == n["packed_index_raw"] and target[0]["side"] == side,
            "native_binding_mismatch", "Detail target is missing, duplicate, or another side/reference")
    timestamp(report.get("captured_at"))
    projection = {(r["side"], r["roster_slot"]): (r["native_player_id"], r["opaque_header_u32"],
                   r["minutes_candidate"]["raw_start"], r["minutes_candidate"]["raw_end"], r["rating_candidate"]["value"],
                   r["counters"]["gk_saves"], r["started_flag_candidate"]["raw_u8"]) for r in rows}
    return {"captured_at": report["captured_at"], "target": target[0], "valid_rows": len(rows), "raw_capacity_bytes": 680000,
            "_cache_projection": projection}


def read_package(root, manifest="native-d1/package.json", *, raw_root=RAW_ROOT):
    root, raw_root = Path(root).resolve(), Path(raw_root).resolve()
    purpose = next((kind for kind in ("real_test", "fixtures") if root.is_relative_to(raw_root / kind)), None)
    require(purpose is not None, "source_root", "Native packages must remain in isolated raw roots")
    sources = Sources(root)
    manifest_path = local_path(root, manifest)
    package = sources.json({"path": manifest, "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()})
    require(package.get("schema") == SCHEMA and package.get("purpose") == purpose, "unsupported_package", "Schema/purpose mismatch")
    require(package.get("environment") == ENVIRONMENT, "unsupported_environment", "Exact reviewed environment required")
    binding = package["binding"]
    identity = valid_identity(binding["identity"])
    native = binding["native"]
    require(all(type(native.get(k)) is int for k in ("native_player_id", "team_index_raw", "packed_index_raw"))
            and 0 < native["native_player_id"] < 0xFFFFFFFF and 0 <= native["team_index_raw"] < 700
            and 0 <= native["packed_index_raw"] <= 0xFFFFFFFF, "invalid_binding", "Invalid native references")
    process = binding["process_instance"]
    require(set(process) == {"pid", "creation_time_100ns"} and all(type(v) is int and v > 0 for v in process.values()),
            "invalid_binding", "Explicit process instance required")
    review = verified_review(sources, binding["review"], identity, "native_test_binding_review")
    require(review.get("native") == binding["native"] and review.get("baseline") == binding["baseline"]
            and review.get("process_instance") == binding["process_instance"]
            and review.get("baseline_capture") == binding["baseline_capture"], "unreviewed_binding", "Binding review differs")
    baseline = sources.save(binding["baseline"])
    snapshot = check_snapshot(baseline, binding["native"])
    require(snapshot["attributes"], "missing_snapshot", "No native attributes")
    baseline_capture = binding["baseline_capture"]
    require(isinstance(baseline_capture["capture_id"], str) and ID.fullmatch(baseline_capture["capture_id"]), "invalid_capture", "Baseline capture ID required")
    timestamp(baseline_capture["captured_at"])
    require(baseline_capture["game_time"] == {"value": snapshot["saved_game_date"], "precision": "day"}, "invalid_time", "Snapshot date differs from native save")
    accepted = review.get("accepted_attribute_fields")
    require(isinstance(accepted, list) and accepted and len(set(accepted)) == len(accepted)
            and all(name in snapshot["attributes"] for name in accepted), "unreviewed_fields", "Explicit reviewed native attributes required")
    events, ids, anchors, captures = [], set(), set(), set()
    captures.add(baseline_capture["capture_id"])
    previous_date = None
    require(isinstance(package.get("events"), list) and 1 <= len(package["events"]) <= 256, "missing_event", "One to 256 reviewed events required")
    for ordinal, item in enumerate(package["events"], 1):
        event_id, anchor, capture_id = item["event_id"], item["research_anchor"], item["capture_id"]
        require(isinstance(event_id, str) and ID.fullmatch(event_id) and event_id not in ids
                and type(item["sequence"]) is int and item["sequence"] == ordinal, "invalid_event", "Explicit unique ledger IDs/order required")
        require(isinstance(anchor, list) and len(anchor) == 4 and tuple(anchor) not in anchors, "branch_review_required", "Repeated/ambiguous fixture anchor")
        require(previous_date is None or anchor[0] >= previous_date, "branch_review_required", "Game time moved backwards")
        ids.add(event_id); anchors.add(tuple(anchor)); previous_date = anchor[0]
        require(isinstance(capture_id, str) and ID.fullmatch(capture_id) and capture_id not in captures, "duplicate_capture", "Settlement capture ID reused")
        captures.add(capture_id)
        event_review = verified_review(sources, item["review"], identity, "native_test_event_review")
        require(all(event_review.get(k) == item[k] for k in ("event_id", "capture_id", "sequence", "research_anchor", "before", "after", "scenario", "details")),
                "unreviewed_event", "Reviewed event/source association differs")
        window = event_review["capture_window"]
        window_start, window_end = timestamp(window["start"]), timestamp(window["end"])
        require(window_start < window_end <= timestamp(event_review["reviewed_at"]), "invalid_time", "Invalid reviewed capture window")
        after = career_report(sources, item["after"], anchor, binding)
        require(window_start <= timestamp(after["captured_at"]) <= window_end, "invalid_transition", "Settlement outside reviewed window")
        require(after["fixture"]["bit30_completed_candidate"], "incomplete_event", "Native fixture not completed")
        if item["before"]["kind"] == "save":
            before_save = sources.save(item["before"]["source"])
            check_snapshot(before_save, binding["native"])
            candidates = [f for f in before_save["club_fixtures"] if [f["game_date"], f["home"]["team_index_raw"], f["away"]["team_index_raw"], f["sequence_raw"]] == anchor]
            require(len(candidates) == 1 and not candidates[0]["bit30_completed_candidate"], "invalid_transition", "Save baseline already complete or missing")
        else:
            require(item["before"]["kind"] == "career", "unsupported_source", "Unknown before source kind")
            before = career_report(sources, item["before"]["source"], anchor, binding)
            require(not before["fixture"]["bit30_completed_candidate"] and timestamp(before["captured_at"]) < timestamp(after["captured_at"]),
                    "invalid_transition", "Before/after completion/time mismatch")
            require(timestamp(before["captured_at"]) >= window_start, "invalid_transition", "Before capture outside reviewed window")
        details = []
        side = anchor[1:3].index(binding["native"]["team_index_raw"])
        require(isinstance(item["details"], list) and len(item["details"]) <= 4, "invalid_capture", "At most four explicit detail captures")
        for capture in item["details"]:
            capture_id = capture["capture_id"]
            require(isinstance(capture_id, str) and ID.fullmatch(capture_id) and capture_id not in captures, "duplicate_capture", "Capture ID reused")
            captures.add(capture_id)
            require(item["scenario"] == "normal_play", "source_scope_mismatch", "Details cannot be assigned to a simulated event")
            detail = detail_report(sources, capture["source"], binding, side)
            require(window_start <= timestamp(detail["captured_at"]) <= timestamp(after["captured_at"]), "invalid_transition", "Detail outside reviewed event window")
            cache = after["recent_cache"]
            require(cache["state"] == "header_fixture_candidate", "fixture_mismatch", "Detail needs native cache's explicit fixture header")
            projection = {(r["side"], r["slot"]): (r["native_player_id"], r["packed_index_raw"],
                          r["minute_interval_candidate"]["raw_start"], r["minute_interval_candidate"]["raw_end"],
                          r["rating_candidate"], r["gk_saves_candidate"], r["initial_first11_flag_u8"]) for r in cache["rows_raw"]}
            require(detail.pop("_cache_projection") == projection, "event_conflict", "Detail rows differ from fixture-owned cache")
            details.append({"capture_id": capture_id, "source": capture["source"], **detail})
        if details:
            first = details[0]["target"]
            require(after["target"]["record"] is not None, "event_conflict", "Normal event lacks fixture-owned target summary")
            summary = after["target"]["record"]["stat_fields_candidate"]
            require((first["counters"]["goals"], first["counters"]["assists"], first["minutes_candidate"]["value"]) ==
                    (summary["goals_candidate"], summary["assists_candidate"], summary["minutes_candidate"]),
                    "event_conflict", "Personal fixture summary and detailed record differ")
            rating = first["rating_candidate"]["value"]
            if rating is not None:
                native_tenths = int(struct.unpack("<f", struct.pack("<f", rating * 10))[0])
                require(native_tenths == summary["rating_tenths_candidate"], "event_conflict", "Fixture/detailed rating differs")
            for detail in details[1:]:
                for key in ("counters", "native_buckets", "continuous", "minutes_candidate", "rating_candidate", "started_flag_candidate"):
                    require(detail["target"][key] == first[key], "event_conflict", "Repeated final target fields differ")
            require(first["minutes_candidate"]["value"] is not None and first["minutes_candidate"]["value"] > 0
                    and sum(first["counters"].values()) > 0, "participation_unverified", "No positive native activity/minute evidence")
            participation = "played_unknown_role"
        elif item["scenario"] == "selected_skip":
            require(after["target"]["record"] is not None and after["target"]["record"]["stat_fields_candidate"]["minutes_candidate"] > 0,
                    "participation_unverified", "Selected simulation lacks personal summary")
            participation = "simulated_appearance_recorded"
        elif item["scenario"] in {"not_selected", "bench_skip"}:
            require(after["target"]["state"] == "absent", "event_conflict", "Expected absent personal row")
            participation = "not_selected_reviewed" if item["scenario"] == "not_selected" else "bench_selection_only_actual_participation_unknown"
        else:
            raise PackageError("unsupported_scenario", str(item["scenario"]))
        events.append({"event_id": event_id, "capture_id": item["capture_id"], "sequence": ordinal, "identity": identity,
                       "identity_source": "explicit_reviewed_test_ledger; not_native_event_id",
                       "game_time": {"value": anchor[0], "precision": "day"}, "research_anchor": anchor,
                       "captured_at": after["captured_at"], "completion": "reviewed_native_transition", "participation": participation,
                       "fixture": after["fixture"], "personal_summary": after["target"], "recent_cache": after["recent_cache"],
                       "detailed_captures": details, "review": item["review"],
                       "native_sources": {"before": item["before"], "after": item["after"]},
                       "unknown": {"cumulative_scope": None, "historical_branch_ancestry": None,
                                   **({"shots": None, "passes": None, "dribble_distance_m": None,
                                       "action_detail_reason": "not_observed_in_this_simulation_source"} if not details else {})}})
    sources.stable()
    match_ready = any(e["participation"] in {"played_unknown_role", "simulated_appearance_recorded"} for e in events)
    return {"schema": SCHEMA, "adapter_version": VERSION, "status": "reviewed_native_test_preview", "purpose": purpose,
            "environment": package["environment"], "identity": identity, "binding_assurance": "explicit_controlled_test_review_only",
            "native_identity_auto_detection": False, "snapshot": snapshot, "snapshot_source": binding["baseline"], "events": events,
            "collection_quality": {"state": "reviewed_controlled_sample", "source_hashes_verified": True,
                                   "native_bytes_reparsed": True, "unsupported_semantics": "preserved_as_unknown",
                                   "scope": "explicit_test_binding_and_event_reviews"}, "errors": [],
            "snapshot_capture": baseline_capture, "reviewed_attribute_fields": accepted,
            "snapshot_ready": True, "match_ready": match_ready,
            "d1_minimum_sample_ready": match_ready, "real_game_sample_ready": purpose == "real_test" and match_ready,
            "three_event_sample_coverage": len(events) >= 3,
            "d2_ready": False, "requires_mapping_before_import": True, "application_writes": 0, "game_writes": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--manifest", default="native-d1/package.json")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    try:
        report = read_package(args.dataset, args.manifest)
        if args.summary:
            report = {key: report[key] for key in ("status", "purpose", "identity", "snapshot_ready", "match_ready",
                      "d1_minimum_sample_ready", "real_game_sample_ready", "three_event_sample_coverage", "d2_ready", "application_writes", "game_writes")} | {
                      "events": [{"event_id": e["event_id"], "participation": e["participation"], "detail_captures": len(e["detailed_captures"])} for e in report["events"]]}
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
        return 0
    except (PackageError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error), "errors": [str(error)], "application_writes": 0, "game_writes": 0}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
