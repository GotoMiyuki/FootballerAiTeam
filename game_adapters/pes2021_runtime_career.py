"""Experimental read-only career fixture/cache preview for one local PES build.

Works independently of the normal-match detail buffer. The caller supplies a
research fixture sequence and expected target references; none is an event ID.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct

from game_adapters.pes2021_bal_research import fixture_stat_fields
from game_adapters.pes2021_runtime_stats import (
    SIGNATURES, RuntimeStatsError, WindowsReader, new_output, pointer, qword, validate_profile,
)

SCHEMA = "pes2021-runtime-career-experimental-v1"
CAREER_ROOT_RVA = 0x3705E10
FIXTURE_BASE, FIXTURE_SIZE, FIXTURE_CAPACITY = 0xE9FF08, 0x254, 13000
CACHE_BASE, CACHE_SIDE_SIZE, CACHE_SIZE = 0x181C570, 0x3C4, 12 + 2 * 0x3C4
EXTRA_SIGNATURES = (
    (0x14B6A60, 8, "4c0b7b8a77fb0ca7bc413180484f4ea39f9b3c169372e2f666f1eab3bfd33fd1"),
    (0x14BC560, 219, "d307cde3dd3129cd128642d7f0af3b7bc66bb51e81cc4d69ee2efa2cbefa4b59"),
    (0x1349580, 1384, "7049e1d48961af7ecbce43dea967bee30071d11b970177c31840276b44777f39"),
)


def game_date(blob, offset):
    try:
        value = date(struct.unpack_from("<H", blob, offset)[0], blob[offset + 2], blob[offset + 3])
    except ValueError as error:
        raise RuntimeStatsError("Invalid native fixture/cache date") from error
    if not 1990 <= value.year <= 2100:
        raise RuntimeStatsError("Unsupported native year")
    return value.isoformat()


def target_candidate(rows, native_id, packed_ref, owner_side):
    found = [row for row in rows if row["native_player_id"] == native_id]
    if len(found) != 1:
        return {"state": "absent" if not found else "ambiguous_native_id", "record": None}
    row = found[0]
    if row["packed_index_raw"] != packed_ref or row["side"] != owner_side:
        return {"state": "binding_conflict", "record": None,
                "actual_reference_raw": row["packed_index_raw"], "actual_side": row["side"]}
    return {"state": "unique_reference_candidate", "record": row}


def parse_buffers(fixture, cache, sequence, native_id, expected_team, packed_ref):
    if len(fixture) != FIXTURE_SIZE or len(cache) != CACHE_SIZE:
        raise RuntimeStatsError("Truncated or oversized native career buffers")
    if struct.unpack_from("<H", fixture)[0] != sequence:
        raise RuntimeStatsError("Fixture sequence/index mismatch; no fallback scan")
    teams = [struct.unpack_from("<I", fixture, offset)[0] & 0x3FFF for offset in (0x14, 0x18)]
    if max(teams) >= 700 or teams[0] == teams[1] or expected_team not in teams:
        raise RuntimeStatsError("Native fixture/expected club binding mismatch")
    fixture_date = game_date(fixture, 8)
    flags = struct.unpack_from("<I", fixture, 4)[0]
    completed = bool(flags & 0x40000000)
    owner_side = teams.index(expected_team)
    rows = []
    for index in range(34):
        offset = 0x24 + index * 16
        words = list(struct.unpack_from("<4I", fixture, offset))
        if words[1] in (0, 0xFFFFFFFF):
            continue
        rows.append({"side": index // 17, "slot": index % 17,
                     "packed_index_raw": words[0], "native_player_id": words[1],
                     "words_raw": words,
                     "data_offsets_semantics": "relative_to_fixture_record; not_save_file_or_process_addresses",
                     "stat_fields_candidate": fixture_stat_fields(words, offset) if completed else None})
    target = target_candidate(rows, native_id, packed_ref, owner_side)
    cache_rows, counts = [], [0, 0]
    for side in range(2):
        for slot in range(40):
            offset = 16 + side * CACHE_SIDE_SIZE + slot * 24
            packed, player, interval, saves, flag, rating = struct.unpack_from("<5If", cache, offset)
            if player in (0, 0xFFFFFFFF):
                continue
            counts[side] += 1
            start, end = interval & 0xFF, (interval >> 8) & 0xFF
            cache_rows.append({"side": side, "slot": slot, "native_player_id": player,
                               "packed_index_raw": packed, "raw_hex": cache[offset:offset + 24].hex(),
                               "minute_interval_candidate": {"raw_start": start, "raw_end": end,
                                   "value": end - start if start <= end <= 150 else None},
                               "gk_saves_candidate": saves, "word_plus16_raw": flag,
                               "initial_first11_flag_u8": flag & 0xFF,
                               "rating_candidate": rating if math.isfinite(rating) and 0 < rating <= 10 else None,
                               "participation_verified": False})
    cache_sequence = struct.unpack_from("<H", cache)[0]
    cache_team_refs = [struct.unpack_from("<I", cache, 12 + side * CACHE_SIDE_SIZE)[0] for side in range(2)]
    binding = {"fixture_sequence_raw": cache_sequence, "team_references_raw": cache_team_refs,
               "valid_native_id_counts_by_side": counts, "target": {"state": "unbound", "record": None}}
    if cache_sequence == 0xFFFF and cache_team_refs == [0xFFFFFFFF, 0xFFFFFFFF] and counts == [0, 0]:
        binding.update(state="empty_native_cache", reason="empty_header_and_80_slot_ids; no_appearance_inference")
    else:
        try:
            cache_date = game_date(cache, 4)
            cache_teams = [value & 0x3FFF for value in cache_team_refs]
            binding.update(game_date_candidate=cache_date, team_indices_raw=cache_teams)
            if cache_sequence == sequence and (cache_date != fixture_date or cache_teams != teams or not completed):
                binding.update(state="conflicting_cache_header", reason="same_sequence_but_other_fixture_fields_conflict")
            elif cache_sequence != sequence:
                binding.update(state="other_fixture_cache", reason="never_assign_cache_to_requested_fixture")
            else:
                binding.update(state="header_fixture_candidate", target=target_candidate(cache_rows, native_id, packed_ref, owner_side))
        except RuntimeStatsError:
            binding.update(state="unverified_cache_header", reason="invalid_cache_date")
    return {"fixture": {"sequence_raw": sequence, "game_date": fixture_date,
                         "team_indices_raw": teams, "flags_raw": flags,
                         "bit30_completed_candidate": completed,
                         "score_candidate": {"home": fixture[0x1C], "away": fixture[0x1F]} if completed else None,
                         "research_anchor": [fixture_date, *teams, sequence], "player_rows_raw": rows,
                         "raw_hex": fixture.hex(), "sha256": hashlib.sha256(fixture).hexdigest()},
            "target": target, "recent_cache": {**binding, "rows_raw": cache_rows,
                        "raw_hex": cache.hex(), "sha256": hashlib.sha256(cache).hexdigest()}}


def career_roots(reader):
    root = qword(reader, reader.base + CAREER_ROOT_RVA)
    if not root:
        return None
    context = qword(reader, pointer(root) + 0x48)
    return (root, pointer(context)) if context else None


def capture_career(reader, sequence, native_id, expected_team, packed_ref):
    if (not 0 <= sequence < FIXTURE_CAPACITY or not 0 < native_id < 0xFFFFFFFF
            or not 0 <= expected_team < 700 or not 0 <= packed_ref <= 0xFFFFFFFF):
        raise RuntimeStatsError("Invalid fixture/target reference arguments")
    validate_profile(reader)
    for rva, size, expected in EXTRA_SIGNATURES:
        if hashlib.sha256(reader.read(reader.base + rva, size)).hexdigest() != expected:
            raise RuntimeStatsError("Unsupported loaded career code; refusing offsets")
    common = {"schema": SCHEMA, "profile": "local-pes2021-1.7.1-career-20261022",
              "source_mode": "live_read", "captured_at": datetime.now(timezone.utc).isoformat(),
              "process_instance": {"pid": getattr(reader, "pid", None),
                                   "creation_time_100ns": getattr(reader, "creation_time_100ns", None)},
              "expected_target": {"native_player_id": native_id, "team_index_raw": expected_team,
                                  "packed_index_raw": packed_ref},
              "verified_code_sha256": {hex(rva): sha for rva, _, sha in (*SIGNATURES, *EXTRA_SIGNATURES)},
              "career_branch_verified": False, "event_identity_verified": False,
              "snapshot_ready": False, "match_ready": False, "d2_ready": False,
              "game_writes": 0, "application_writes": 0,
              "identity_semantics": "caller references and research anchors do not certify career branches or event IDs",
              "unavailable_action_details": {key: None for key in ("shots", "passes", "dribble_distance_m")}}
    roots = career_roots(reader)
    if roots is None:
        return {**common, "status": "unavailable", "reason": "career_context_absent", "fixture": None,
                "target": {"state": "unavailable", "record": None}, "recent_cache": None}
    root, context = roots
    address = pointer(context + FIXTURE_BASE + sequence * FIXTURE_SIZE)
    cache_address = pointer(context + CACHE_BASE)
    fixture, cache = reader.read(address, FIXTURE_SIZE), reader.read(cache_address, CACHE_SIZE)
    result = parse_buffers(fixture, cache, sequence, native_id, expected_team, packed_ref)
    if (career_roots(reader) != roots or reader.read(address, FIXTURE_SIZE) != fixture
            or reader.read(cache_address, CACHE_SIZE) != cache):
        raise RuntimeStatsError("Career roots or fixture/cache changed during capture; retry")
    return {**common, **result, "status": "runtime_career_preview",
            "runtime_roots_diagnostic": [hex(root), hex(context)],
            "consistency": "roots_and_two_buffer_repeats_stable; not_an_atomic_frame"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--exe", type=Path, default=Path.home() / "Desktop/pes/PES2021.exe")
    parser.add_argument("--fixture-sequence", required=True, type=int)
    parser.add_argument("--native-player-id", required=True, type=int)
    parser.add_argument("--expected-team-index", required=True, type=int)
    parser.add_argument("--expected-packed-index", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reader = None
    try:
        output = new_output(args.output)
        if not 0 < args.pid < 2**32:
            raise RuntimeStatsError("Invalid PID")
        reader = WindowsReader(args.pid, args.exe)
        report = capture_career(reader, args.fixture_sequence, args.native_player_id,
                                args.expected_team_index, args.expected_packed_index)
        report.update(read_bytes=reader.bytes, executable=str(args.exe.resolve()))
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        target = report["target"]["record"]
        print(json.dumps({"status": report["status"], "output": str(output),
                          "target_state": report["target"]["state"],
                          "target_fields": target["stat_fields_candidate"] if target else None,
                          "cache_state": report["recent_cache"]["state"] if report["recent_cache"] else None,
                          "read_bytes": reader.bytes, "match_ready": False}, ensure_ascii=False))
        return 0
    except (RuntimeStatsError, OSError) as error:
        print(json.dumps({"status": "rejected", "reason": str(error), "game_writes": 0}, ensure_ascii=False))
        return 1
    finally:
        if reader:
            reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
