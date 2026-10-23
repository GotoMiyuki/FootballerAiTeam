"""Read-only PES21 BAL interface: capture, inspect and compare saved snapshots.

No process hooks, game control, save editor or application import. Unknown
personal-stat semantics stay explicitly experimental in every JSON response.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from game_adapters.pes2021_save import RAW_ROOT, SaveError, decrypt_container, read_copy
from game_adapters.pes2021_bal_research import research_bal

SCHEMA = "pes2021-bal-readonly-v1"
PROTECTED_BAL_SLOTS = (1,)  # User's Chelsea career; never a reuse target.


def inspect_copy(path):
    metadata, blocks = read_copy(Path(path))
    return {**research_bal(metadata, blocks), "schema": SCHEMA, "source": metadata}


def default_save_dir():
    base = Path.home() / "Documents/KONAMI/eFootball PES 2021 SEASON UPDATE"
    candidates = sorted(path for path in base.glob("*/save") if path.is_dir())
    if len(candidates) != 1:
        raise SaveError("Save directory missing or ambiguous; specify --save-dir")
    return candidates[0]


def capture_slot(save_dir, slot, output, *, raw_root=RAW_ROOT, expected_native_player_id=None):
    """Capture a stable, validated original into a new isolated directory."""
    if not 1 <= slot <= 20:
        raise SaveError("BAL slot must be between 1 and 20")
    output = Path(output).resolve()
    if not output.is_relative_to(Path(raw_root).resolve()) or output.exists() or not output.parent.is_dir():
        raise SaveError("Capture output must be a new directory inside existing raw/real_test")
    source = Path(save_dir).resolve() / f"BL{slot - 1:08d}"
    stat0 = source.stat()
    if not 528 <= stat0.st_size <= 64_000_000:
        raise SaveError("Truncated or oversized original save")
    blob = source.read_bytes()
    metadata, blocks = decrypt_container(blob)
    report = research_bal(metadata, blocks)
    if expected_native_player_id is not None and report["snapshot"]["native_player_id"] != expected_native_player_id:
        raise SaveError("Slot belongs to a different player; refusing reuse archive")
    # Decryption can take time. Never accept a save which changed meanwhile.
    if source.stat().st_mtime_ns != stat0.st_mtime_ns or source.read_bytes() != blob:
        raise SaveError("Original changed during capture; retry after saving finishes")
    digest = hashlib.sha256(blob).hexdigest()
    output.mkdir(exist_ok=False)
    copy = output / "save.bin"
    with copy.open("xb") as file:
        file.write(blob)
    if hashlib.sha256(copy.read_bytes()).hexdigest() != digest:
        raise SaveError("Isolated copy hash mismatch")
    source_meta = {"original_path": str(source), "source_path": str(copy),
                   "source_sha256": digest, "source_size": len(blob),
                   "original_last_write_utc": datetime.fromtimestamp(stat0.st_mtime, timezone.utc).isoformat(),
                   "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                   "original_unchanged_at_copy": source.stat().st_mtime_ns == stat0.st_mtime_ns and source.read_bytes() == blob,
                   "slot": slot, **metadata}
    if not source_meta["original_unchanged_at_copy"]:
        raise SaveError("Original changed while copying; isolated directory retained for diagnosis")
    report = {**report, "schema": SCHEMA, "source": source_meta}
    with (output / "report.json").open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    return report


def prepare_slot_reuse(save_dir, slot, output, expected_native_player_id, *, raw_root=RAW_ROOT):
    """Archive a test slot before normal in-game overwrite; never write a save.

    The ticket describes the exact archived state, not a timeless permission:
    recheck the original hash immediately before choosing the slot in-game.
    """
    if slot in PROTECTED_BAL_SLOTS:
        raise SaveError("Protected Chelsea slot cannot be prepared for reuse")
    if expected_native_player_id <= 0:
        raise SaveError("A positive expected native player ID is required")
    report = capture_slot(save_dir, slot, output, raw_root=raw_root,
                          expected_native_player_id=expected_native_player_id)
    ticket = {"schema": SCHEMA, "status": "test_slot_archived_for_ui_reuse",
              "slot": slot, "native_player_id": report["snapshot"]["native_player_id"],
              "player_name": report["snapshot"]["identity"]["name"],
              "saved_game_date": report["snapshot"]["saved_game_date"],
              "archived_copy": report["source"]["source_path"],
              "original_path": report["source"]["original_path"],
              "archived_sha256": report["source"]["source_sha256"],
              "protected_slots": list(PROTECTED_BAL_SLOTS),
              "instruction": "Recheck original hash and in-game target; overwrite only through normal game save UI",
              "game_writes": 0, "application_writes": 0}
    with (Path(output) / "reuse-ticket.json").open("x", encoding="utf-8") as file:
        json.dump(ticket, file, ensure_ascii=False, indent=2)
    return ticket


def compare_reports(before, after):
    key = lambda report: (report["snapshot"]["native_player_id"], report["snapshot"]["identity"]["name"])
    if key(before) != key(after):
        raise SaveError("Different target player; refusing comparison")
    # Fixture keys are only research anchors, not domain event identifiers.
    def indexed(report):
        result = {}
        for row in report["club_fixtures"]:
            anchor = (row["game_date"], row["home"]["team_index_raw"], row["away"]["team_index_raw"], row["sequence_raw"])
            if anchor in result:
                raise SaveError("Ambiguous fixture comparison anchor")
            result[anchor] = row
        return result
    old, new = indexed(before), indexed(after)
    changes = []
    for anchor in sorted(old.keys() | new.keys()):
        left, right = old.get(anchor), new.get(anchor)
        if left and right and left["record_sha256"] == right["record_sha256"]:
            continue
        changes.append({"research_anchor": list(anchor), "before": left, "after": right})
    old_stats, new_stats = before["personal_stat_candidates"], after["personal_stat_candidates"]
    old_cumulative, new_cumulative = before.get("cumulative_stat_candidates"), after.get("cumulative_stat_candidates")
    completed_changes = [change["research_anchor"] for change in changes
                         if change["before"] and change["before"].get("bit30_completed_candidate")]
    old_date, new_date = before["snapshot"]["saved_game_date"], after["snapshot"]["saved_game_date"]
    date_decreased = new_date < old_date if old_date and new_date else None
    return {"schema": SCHEMA, "status": "experimental_fixture_diff",
            "source_hashes": {side: report["source"]["source_sha256"] for side, report in (("before", before), ("after", after))},
            "saved_dates": {side: report["snapshot"]["saved_game_date"] for side, report in (("before", before), ("after", after))},
            "changed_fixture_count": len(changes), "changed_fixtures": changes,
            "identity_checks": {"saved_date_decreased": date_decreased,
                                "previously_completed_anchors_changed": completed_changes,
                                "requires_branch_review": bool(date_decreased or completed_changes),
                                "branch_verified": False, "save_hash_is_event_identity": False,
                                "interpretation": "Review clues only; identical anchors can contain different replayed results, and no clue does not certify a branch"},
            "personal_stat_candidates": {"before": old_stats, "after": new_stats},
            "personal_candidate_records_identical": sorted(row["record_sha256"] for row in old_stats) == sorted(row["record_sha256"] for row in new_stats),
            "cumulative_stat_candidates": {"before": old_cumulative, "after": new_cumulative},
            "cumulative_candidate_records_identical":
                sorted(row["record_sha256"] for row in old_cumulative) == sorted(row["record_sha256"] for row in new_cumulative)
                if old_cumulative is not None and new_cumulative is not None else None,
            "interpretation": "Identical records may be stale; changes do not prove new personal appearances. No automatic last-fixture binding.",
            "career_branch": "unverified; matching native player/name is insufficient",
            "match_records_verified": False, "skipped_match_collection_verified": False,
            "application_writes": 0, "game_writes": 0}


def stats_preview(report):
    """Compact native-only view; no UI values or automatic cache/event binding."""
    completed = [row for row in report["club_fixtures"] if row.get("bit30_completed_candidate")]
    completed.sort(key=lambda row: (row["game_date"], row["sequence_raw"], row["table_slot_raw"]))
    personal = []
    for fixture in completed:
        for target in fixture["target_roster_slots"]:
            personal.append({"research_anchor": [fixture["game_date"], fixture["home"]["team_index_raw"],
                                                 fixture["away"]["team_index_raw"], fixture["sequence_raw"]],
                             "game_date": fixture["game_date"], "home": fixture["home"], "away": fixture["away"],
                             "score_candidate": fixture["score_candidate"],
                             "penalty_shootout_candidate": fixture.get("penalty_shootout_candidate"),
                             "target_row_data_offset": target["data_offset"],
                             "fields": target["stat_field_candidates"],
                             "appearance_verified": False})
    latest = completed[-1] if completed else None
    cache = report["personal_stat_candidates"]
    return {"schema": SCHEMA, "status": "experimental_native_stats_preview",
            "source": report["source"], "data_source": "decrypted_save_bytes_only",
            "native_player_id": report["snapshot"]["native_player_id"],
            "saved_game_date": report["snapshot"]["saved_game_date"],
            "single_match_candidates": personal,
            "latest_completed_club_fixture":
                {"game_date": latest["game_date"], "sequence_raw": latest["sequence_raw"],
                 "score_candidate": latest["score_candidate"],
                 "penalty_shootout_candidate": latest.get("penalty_shootout_candidate"),
                 "target_row_found": bool(latest["target_roster_slots"]),
                 "personal_appearance": "unverified; absent row is not a zero-minute record"} if latest else None,
            "cumulative_stat_candidates": report.get("cumulative_stat_candidates"),
            "cumulative_binding_diagnostics": report.get("cumulative_binding_diagnostics"),
            "recent_24b_cache": {"candidates": cache, "availability": "candidate_found" if cache else "unavailable",
                                 "match_binding": "unverified; not assigned to latest club fixture"},
            "unavailable_single_match_details": {field: None for field in
                ("shots", "shots_on_target", "passes_attempted", "passes_completed", "tackles", "yellow_cards", "red_cards")},
            "missing_semantics": report["missing_semantics"],
            "event_identity": "unverified; research anchor requires explicit branch evidence",
            "d1_complete": False, "match_records_verified": False,
            "application_writes": 0, "game_writes": 0}


def write_new_json(path, report):
    path = Path(path).resolve()
    if not path.is_relative_to(RAW_ROOT.resolve()) or path.exists() or not path.parent.is_dir():
        raise SaveError("JSON output must be a new file inside existing raw/real_test")
    with path.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="Read an on-disk BAL slot into a new isolated dataset")
    capture.add_argument("--save-dir", type=Path)
    capture.add_argument("--slot", type=int, required=True)
    capture.add_argument("--output", type=Path, required=True)
    reuse = commands.add_parser("prepare-reuse", help="Archive a verified test slot before normal in-game overwrite")
    reuse.add_argument("--save-dir", type=Path)
    reuse.add_argument("--slot", type=int, required=True)
    reuse.add_argument("--expected-native-player-id", type=int, required=True)
    reuse.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect", help="Preview one isolated save copy")
    inspect.add_argument("copy", type=Path)
    inspect.add_argument("--output", type=Path)
    stats = commands.add_parser("stats", help="Native-only preview of separate single-match, cumulative and cache layers")
    stats.add_argument("copy", type=Path)
    stats.add_argument("--output", type=Path)
    compare = commands.add_parser("compare", help="Compare fixture and personal-stat candidates")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    compare.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            report = capture_slot(args.save_dir or default_save_dir(), args.slot, args.output)
        elif args.command == "prepare-reuse":
            report = prepare_slot_reuse(args.save_dir or default_save_dir(), args.slot, args.output,
                                       args.expected_native_player_id)
        elif args.command == "inspect":
            report = inspect_copy(args.copy)
        elif args.command == "stats":
            report = stats_preview(inspect_copy(args.copy))
        else:
            report = compare_reports(inspect_copy(args.before), inspect_copy(args.after))
        if args.command not in ("capture", "prepare-reuse") and args.output:
            write_new_json(args.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, SaveError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
