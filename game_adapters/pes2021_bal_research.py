"""Experimental BAL fixtures and personal-stat candidates from isolated save copies.

Team/fixture layout facts: leo-grayrat/pes-file-reader commit
47441ce09d4f287faa9e628d43a72956405d7382, exe-save-layout sections 7.6/7.9.1.
Independently implemented; no upstream program, hooks or writeback executed.
Stat layers are local controlled-snapshot research, not a certified schema.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

from game_adapters.pes2021_save import RAW_ROOT, SaveError, parse_bal_snapshot, read_copy

TEAM_BASE, TEAM_SIZE, TEAM_COUNT = 0x50, 0x690, 700
SCHEDULE_BASE, SCHEDULE_SIZE, SCHEDULE_CAPACITY = 0x329B00, 596, 13000
CUMULATIVE_BASE, CUMULATIVE_SIZE, CUMULATIVE_CAPACITY = 0xC06264, 44, 60


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def parse_teams(body):
    if len(body) < SCHEDULE_BASE + SCHEDULE_CAPACITY * SCHEDULE_SIZE:
        raise SaveError("Truncated BAL research table area")
    if (u32(body, 0), u32(body, 4), u32(body, 8), u32(body, 0x10), u32(body, 0x14)) != (11, 80, 0x015B6D44, TEAM_COUNT, TEAM_COUNT):
        raise SaveError("Unsupported BAL inner header/table counts")
    teams = []
    for index in range(TEAM_COUNT):
        base = TEAM_BASE + index * TEAM_SIZE
        ref = u32(body, base) & 0x3FFF
        if ref not in (index, 0x3FFF):
            raise SaveError("Team reference/table index mismatch")
        try:
            name = body[base + 4:base + 0x4A].split(b"\0", 1)[0].decode("utf-8")
        except UnicodeError as exc:
            raise SaveError("Invalid team UTF-8") from exc
        roster = []
        for slot in range(60):
            off = base + 0x14C + slot * 8
            player_id = u32(body, off + 4)
            if player_id in (0, 0xFFFFFFFF):
                break
            roster.append({"packed_index_raw": u32(body, off), "native_player_id": player_id})
        teams.append({"index": index, "reference_raw": ref, "name": name,
                      "data_offset": base, "roster": roster})
    return teams


def find_stat_candidates(body, snapshot):
    # Repeated exact [packed roster index, native ID] is a structural anchor,
    # not sufficient evidence for a stat's active match/career ownership.
    packed_index = u32(body, snapshot["player_data_offset"] + 0x2C)
    needle = struct.pack("<II", packed_index, snapshot["native_player_id"])
    cursor, candidates = 0, []
    while (base := body.find(needle, cursor)) != -1:
        cursor = base + 1
        if base + 24 > len(body):
            continue
        minutes_raw, stats_raw, status_raw = struct.unpack_from("<3I", body, base + 8)
        rating = struct.unpack_from("<f", body, base + 20)[0]
        start, end = minutes_raw & 0xFF, (minutes_raw >> 8) & 0xFF
        # Zero remains an unverified raw candidate, not a missing-data default.
        # Reject tiny float reinterpretations such as the integer 255 in T4.
        if (minutes_raw > 0xFFFF or not 0 <= start <= end <= 130 or status_raw not in (0, 1)
                or not math.isfinite(rating) or not (rating == 0 or 1 <= rating <= 10)):
            continue
        candidates.append({"data_offset": base, "record_size_candidate": 24,
                           "record_sha256": hashlib.sha256(body[base:base + 24]).hexdigest(),
                           "packed_index_raw": packed_index,
                           "native_player_id": snapshot["native_player_id"],
                           "rating_float_candidate": {"value": rating, "data_offset": base + 20},
                           "minute_interval_candidate": {"start": start, "end": end,
                                                          "difference": end - start,
                                                          "raw_u32": minutes_raw, "data_offset": base + 8},
                           "unclassified_words": {"offset_plus_12": stats_raw, "offset_plus_16": status_raw},
                           "word_classification": {
                               "offset_plus_12": "Unclassified; goal/assist positive samples do not encode their counts here",
                               "offset_plus_16": "0/1 participation-role candidate; starting/substitute distinction not certified"},
                           "validation": "local_structure_candidate_pending_controlled_progression",
                           "match_binding": "unverified; not assigned to the newest fixture"})
    return candidates


def fixture_stat_fields(words, offset):
    """Local field hypotheses, never a domain match/appearance record."""
    packed = words[3]
    return {"goals_candidate": words[2] & 0xFF,
            "assists_candidate": (words[2] >> 8) & 0xFF,
            "word2_upper16_raw": words[2] >> 16,
            "minutes_candidate": packed & 0x7F,
            "rating_tenths_candidate": (packed >> 7) & 0x7F,
            "rating_candidate": ((packed >> 7) & 0x7F) / 10,
            "minutes_semantics": "Packed local candidate; simulated penalty-shootout sample still encodes 90, not certified elapsed match time",
            "word3_bits14_to20_raw": (packed >> 14) & 0x7F,
            "word3_bits21_to31_raw": packed >> 21,
            "data_offsets": {"counts_word": offset + 8, "packed_word": offset + 12},
            "validation": "local_positive_goal_assist_and_selected_skip_comparison; broader_formats_unverified",
            "appearance_verified": False}


def find_cumulative_binding_diagnostics(body, snapshot):
    """Do not equate a same-ID record with the current player reference."""
    if len(body) < CUMULATIVE_BASE + CUMULATIVE_CAPACITY * CUMULATIVE_SIZE:
        return []
    expected = u32(body, snapshot["player_data_offset"] + 0x2C)
    diagnostics = []
    for index in range(CUMULATIVE_CAPACITY):
        base = CUMULATIVE_BASE + index * CUMULATIVE_SIZE
        if u32(body, base + 4) == snapshot["native_player_id"] and u32(body, base) != expected:
            diagnostics.append({"data_offset": base, "table_slot_raw": index,
                                "native_player_id": snapshot["native_player_id"],
                                "expected_packed_index_raw": expected, "actual_packed_index_raw": u32(body, base),
                                "words_raw": list(struct.unpack_from("<11I", body, base)),
                                "binding": "unverified; native ID matches but current packed reference differs; not a certified target cumulative record"})
    return diagnostics


def find_cumulative_candidates(body, snapshot):
    # A separately calibrated club-player table; two buckets must stay unnamed
    # until season/lifetime and competition ownership can be distinguished.
    if len(body) < CUMULATIVE_BASE + CUMULATIVE_CAPACITY * CUMULATIVE_SIZE:
        return []
    packed = u32(body, snapshot["player_data_offset"] + 0x2C)
    result = []
    for index in range(CUMULATIVE_CAPACITY):
        base = CUMULATIVE_BASE + index * CUMULATIVE_SIZE
        words = list(struct.unpack_from("<11I", body, base))
        if words[:2] != [packed, snapshot["native_player_id"]]:
            continue
        buckets = []
        for relative in (8, 32):
            counters, counts, ratings = struct.unpack_from("<3I", body, base + relative)
            buckets.append({"data_offset": base + relative, "words_raw": [counters, counts, ratings],
                            "match_counter_low16_raw": counters & 0xFFFF,
                            "appearances_high16_candidate": counters >> 16,
                            "goals_low16_candidate": counts & 0xFFFF,
                            "assists_high16_candidate": counts >> 16,
                            "rating_count_low16_candidate": ratings & 0xFFFF,
                            "rating_sum_tenths_high16_candidate": ratings >> 16,
                            "ownership": "unverified; not labelled season/lifetime or competition"})
        result.append({"data_offset": base, "table_slot_raw": index, "record_size_candidate": 44,
                       "record_sha256": hashlib.sha256(body[base:base + 44]).hexdigest(),
                       "native_player_id": words[1], "packed_index_raw": words[0], "words_raw": words,
                       "buckets": buckets, "unclassified_words_plus20_to28": words[5:8],
                       "validation": "local_0_1_2_appearance_progression; bucket_ownership_unverified"})
    if len(result) > 1:
        raise SaveError("Duplicate target in cumulative candidate table")
    return result


def research_bal(metadata, blocks):
    snapshot = parse_bal_snapshot(metadata, blocks)
    body = blocks["data"]
    teams = parse_teams(body)
    player_id = snapshot["native_player_id"]
    club_candidates = [team for team in teams if team["name"] == snapshot["identity"]["club"]
                       and any(row["native_player_id"] == player_id for row in team["roster"])]
    if len(club_candidates) != 1:
        raise SaveError("Current club/name/native-ID roster binding is missing or ambiguous")
    club = club_candidates[0]
    fixtures = []
    for index in range(SCHEDULE_CAPACITY):
        base = SCHEDULE_BASE + index * SCHEDULE_SIZE
        record = body[base:base + SCHEDULE_SIZE]
        seq = struct.unpack_from("<H", record)[0]
        if seq == 0xFFFF:
            continue
        home, away = (u32(record, off) & 0x3FFF for off in (0x14, 0x18))
        if club["index"] not in (home, away):
            continue
        if home == away or max(home, away) >= TEAM_COUNT or not teams[home]["name"] or not teams[away]["name"]:
            raise SaveError("Invalid target-club fixture team references")
        try:
            fixture_date = date(struct.unpack_from("<H", record, 8)[0], record[10], record[11]).isoformat()
        except ValueError as exc:
            raise SaveError("Invalid target-club fixture date") from exc
        if not 1990 <= int(fixture_date[:4]) <= 2100:
            raise SaveError("Unsupported target-club fixture year")
        side = "home" if home == club["index"] else "away"
        slots, player_rows = [], []
        cross_roster_counts = {"home": 0, "away": 0}
        for slot in range(34):
            off = 0x24 + slot * 16
            native_id = u32(record, off + 4)
            if native_id in (0, 0xFFFFFFFF):
                continue
            slot_side = "home" if slot < 17 else "away"
            team = teams[home if slot_side == "home" else away]
            roster_matches = any(row["native_player_id"] == native_id for row in team["roster"])
            if roster_matches:
                cross_roster_counts[slot_side] += 1
            words = list(struct.unpack_from("<4I", record, off))
            player_rows.append({"side": slot_side, "slot_index_raw": slot % 17,
                                "native_player_id": native_id, "data_offset": base + off,
                                "roster_cross_reference": roster_matches, "words_raw": words,
                                "word2_bytes_raw": list(record[off + 8:off + 12]),
                                "word3_bytes_raw": list(record[off + 12:off + 16]),
                                "stat_field_candidates": fixture_stat_fields(words, base + off)
                                    if u32(record, 4) & 0x40000000 and roster_matches else None,
                                "semantics": "Fixture-owned field candidates; not 24-byte rating cache"})
            if native_id == player_id:
                if slot_side != side or not roster_matches:
                    raise SaveError("Target fixture slot has inconsistent club ownership")
                slots.append({"side": slot_side, "slot_index_raw": slot % 17,
                              "data_offset": base + off, "words_raw": list(struct.unpack_from("<4I", record, off)),
                              "stat_field_candidates": fixture_stat_fields(words, base + off)
                                  if u32(record, 4) & 0x40000000 else None,
                              "participation": "unknown; membership does not prove appearance or starting role"})
        if len(slots) > 1:
            raise SaveError("Duplicate target player within one fixture")
        flags = u32(record, 4)
        fixtures.append({"table_slot_raw": index, "sequence_raw": seq,
                         "data_offset": base, "record_sha256": hashlib.sha256(record).hexdigest(),
                         "game_date": fixture_date, "home": {"team_index_raw": home, "name": teams[home]["name"]},
                         "away": {"team_index_raw": away, "name": teams[away]["name"]},
                         "target_club_side": side, "round_raw": u32(record, 0x10), "flags_raw": flags,
                         "bit30_completed_candidate": bool(flags & 0x40000000),
                         "completion": "unverified; flag meaning requires controlled progression",
                         "result_bytes_plus_1c_raw": list(record[0x1C:0x22]),
                         "score_candidate": {"home": record[0x1C], "away": record[0x1F],
                                             "validation": "local_1_1_auto_4_2_and_selected_skip_0_1_comparison; broader_formats_unverified"}
                                             if flags & 0x40000000 else None,
                         "penalty_shootout_candidate":
                             {"home": record[0x1E], "away": record[0x21],
                              "data_offsets": {"home": base + 0x1E, "away": base + 0x21},
                              "validation": "one_local_replayed_cup_0_0_PK_6_5_sample; presence_and_other_formats_unverified"}
                             if flags & 0x40000000 and (record[0x1E] or record[0x21]) else None,
                         "target_roster_slots": slots, "roster_cross_reference_counts": cross_roster_counts,
                         "fixture_player_rows_raw": player_rows,
                         "validation": "upstream_layout_and_local_roster_comparison; experimental"})
    stats = find_stat_candidates(body, snapshot)
    return {"status": "experimental_bal_research", "snapshot": snapshot,
            "club_binding": {"team_index_raw": club["index"], "native_player_id": player_id,
                             "method": "descriptor club name + exact roster native ID",
                             "career_branch": "unverified"},
            "club_fixtures": sorted(fixtures, key=lambda row: (row["game_date"], row["sequence_raw"], row["table_slot_raw"])),
            "personal_stat_candidates": stats,
            "cumulative_stat_candidates": find_cumulative_candidates(body, snapshot),
            "cumulative_binding_diagnostics": find_cumulative_binding_diagnostics(body, snapshot),
            "missing_semantics": {
                "fixture_target_row_missing": "No target record found; not a certified zero-minute appearance",
                "personal_stat_candidates_empty": "No matching 24B candidate; does not imply no personal appearance or zero rating",
                "cumulative_stat_candidates_empty": "Target cumulative structure unavailable; no zero substitution",
                "unplayed_fixture": "Score and stat field candidates are null even if raw bytes are nonzero"},
            "unknown_fields": {"goals_assists": "Fixture +8 low bytes have local positive candidates; 24B +12/+16 are not direct counts; no zero substitution",
                               "participation": "Fixture roster membership is not verified appearance",
                               "stats_match_binding": "Fixture membership binds local raw fields; 24B cache match and cumulative bucket ownership remain unverified",
                               "skipped_match_behavior": "Local selected active skip generates fixture/cumulative stats while 24B cache stays empty; one save does not identify execution path",
                               "stable_event_identity": "Table index/sequence not certified across reload or sorting"},
            "d1_complete": False, "match_records_verified": False, "skipped_match_collection_verified": False,
            "application_writes": 0, "game_writes": 0}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("copy", type=Path)
    parser.add_argument("--output", type=Path, help="New research JSON under raw/real_test")
    args = parser.parse_args()
    try:
        if args.output is not None:
            output = args.output.resolve()
            if not output.is_relative_to(RAW_ROOT.resolve()) or output.exists() or not output.parent.is_dir():
                raise SaveError("Output must be a new file under an existing raw/real_test directory")
        metadata, blocks = read_copy(args.copy)
        report = research_bal(metadata, blocks)
        report["source"] = metadata
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output is not None:
            with output.open("x", encoding="utf-8") as file:
                file.write(payload)
        print(payload)
    except (OSError, SaveError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
