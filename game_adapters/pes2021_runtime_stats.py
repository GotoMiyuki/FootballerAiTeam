"""Experimental, read-only PES2021 match counters for one verified local build.

This is a runtime preview, not an importer or a completed-match detector. The
native record can outlive the match screen; its existence is not event identity.
"""
from __future__ import annotations

import argparse
import base64
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time

from game_adapters.pes2021_save import RAW_ROOT

SCHEMA = "pes2021-runtime-player-stats-experimental-v1"
PROFILE = "local-pes2021-1.7.1-smokepatch21-20261009"
MANAGER_RVA = 0x36F3DC8
RECORD_SIZE = 0x2134
CAPACITY = 40
# These loaded-code fingerprints protect offsets, including the UI's float and
# minute accessors. They do not claim compatibility with other PES2021 builds.
SIGNATURES = (
    (0xA3E8A0, 8, "72c7249f893293ddc291dc99356ce414ec0b9baeaa6a20f492b090fe6742d8ef"),
    (0x8C7210, 112, "28ca4459b5765595f2d585cdb533938f206979cc9e5bdd64d15aea3d63ab2ea1"),
    (0x8CA920, 188, "5e528c827b8c5854ce472b2df6ec17e0a8b91350fa8381b24ebfd86cd8f9a818"),
    (0x4DC3F0, 112, "75e76116cb069148443c011362c46341c182d4525de5c29aa779a3e6f381d2a5"),
    (0x4DCC2D, 179, "c183088dad65685b0ade593b11bf9e924c46b93aee3694f5f75d9d1619929d8f"),
    (0x6A6840, 64, "89d5138946885b7a9445b4951524865fc702054eda07b2d5aab783cf35be06b0"),
    # Rating list builder: native getter -> record+0x1934 -> stats_rating row.
    # Static source verified; positive live rating comparison is still pending.
    (0x4EAD30, 1168, "eed5ac2dbd2d96db8fbb529ec750869cc669672218a13aeffbf060453aa45061"),
)
METRICS = {
    "goals": 0, "own_goals": 1, "free_kick_goals": 2,
    "penalty_goals": 3, "assists": 4, "shots": 6,
    "shots_on_target": 0x11, "penalty_shots": 0x15,
    "passes": 0x16, "successful_passes": 0x17,
    "short_passes": 0x18, "successful_short_passes": 0x19,
    "through_passes": 0x1A, "successful_through_passes": 0x1B,
    "long_passes": 0x1C, "successful_long_passes": 0x1D,
    "crosses": 0x1E, "successful_crosses": 0x1F,
    "tackles": 0x31, "successful_tackles": 0x32,
    "clearances": 0x36, "fouls": 0x37, "offsides": 0x38,
    "yellow_cards": 0x39, "red_cards": 0x3A,
    "free_kicks": 0x3D, "corners": 0x3F,
    "native_dribbles": 0x41, "successful_native_dribbles": 0x42,
    "ball_touches": 0x43, "interceptions": 0x4E,
    "gk_shots_faced": 0x83, "gk_shots_on_target_faced": 0x84,
    "gk_saves": 0x85,
}


class RuntimeStatsError(ValueError):
    pass


def pointer(value):
    if not 0x10000 <= value <= 0x7FFFFFFFFFFF:
        raise RuntimeStatsError("Invalid runtime pointer")
    return value


def ratio(successes, attempts):
    if attempts == 0:
        return {"value": None, "reason": "zero_denominator"}
    if successes > attempts:
        return {"value": None, "reason": "inconsistent_counters"}
    return {"value": successes * 100.0 / attempts, "reason": None}


def parse_player_record(blob):
    if len(blob) != RECORD_SIZE:
        raise RuntimeStatsError("Truncated or oversized player record")
    opaque, native_id = struct.unpack_from("<II", blob)
    if native_id in (0, 0xFFFFFFFF):
        return None
    metrics, buckets = {}, {}
    for name, index in METRICS.items():
        values = struct.unpack_from("<9I", blob, 8 + index * 36)
        # Native getter mode 5 sums only the first eight of nine DWORD buckets.
        metrics[name] = sum(values[:8]) & 0xFFFFFFFF
        buckets[name] = list(values)
    floats = {}
    for name, offset in (("dribble_distance_m", 0x1B90), ("average_speed_kmh", 0x1B6C)):
        value = struct.unpack_from("<f", blob, offset)[0]
        floats[name] = {"value": value if math.isfinite(value) and value >= 0 else None,
                        "reason": None if math.isfinite(value) and value >= 0 else "invalid_float",
                        "raw_hex": blob[offset:offset + 4].hex()}
    start, end = blob[0x18EC:0x18EE]
    valid_minutes = start <= end <= 150
    rating = struct.unpack_from("<f", blob, 0x1934)[0]
    valid_rating = math.isfinite(rating) and 0 < rating <= 10
    starter_flag = blob[0x1933]
    return {
        "native_player_id": native_id, "opaque_header_u32": opaque,
        "record_sha256": hashlib.sha256(blob).hexdigest(),
        "counters": metrics, "native_buckets": buckets,
        "continuous": floats,
        "rating_candidate": {
            "value": rating if valid_rating else None,
            "raw_hex": blob[0x1934:0x1938].hex(), "record_offset": "0x1934",
            "reason": None if valid_rating else "native_zero_rating" if rating == 0 else "invalid_rating",
            "validation": "native_rating_list_consumer_static_only; positive_live_comparison_pending",
            "live_value_verified": False},
        "started_flag_candidate": {
            "raw_u8": starter_flag, "value": bool(starter_flag) if starter_flag in (0, 1) else None,
            "record_offset": "0x1933",
            "validation": "native_initial_first11_writer_static_only; substitution_comparison_pending",
            "participation_verified": False},
        "minutes_candidate": {"value": end - start if valid_minutes else None,
                              "raw_start": start, "raw_end": end,
                              "reason": None if valid_minutes else "unresolved_minute_encoding"},
        "rates_percent": {
            "pass_success": ratio(metrics["successful_passes"], metrics["passes"]),
            "native_dribble_success": ratio(metrics["successful_native_dribbles"], metrics["native_dribbles"]),
        },
        "participation_verified": False,
        "gk_role_flag_raw": struct.unpack_from("<I", blob, 0x18FC)[0],
    }


def validate_profile(reader):
    for rva, size, expected in SIGNATURES:
        if hashlib.sha256(reader.read(reader.base + rva, size)).hexdigest() != expected:
            raise RuntimeStatsError("Unsupported loaded game build; refusing offsets")


def qword(reader, address):
    return struct.unpack("<Q", reader.read(address, 8))[0]


def roots(reader):
    manager = qword(reader, reader.base + MANAGER_RVA)
    if not manager:
        return None
    pointer(manager)
    # Registry +0x18 cached fields can be NULL between frames. Resolve each
    # registered wrapper's backing buffer, as MatchResultPlayerStats does.
    record_wrapper = qword(reader, manager + 0x2A28)
    if not record_wrapper:
        return None
    record_buffer = qword(reader, pointer(record_wrapper) + 0x10)
    if not record_buffer:
        return None
    pointer(record_buffer)
    lineups = []
    for side in range(2):
        wrapper = qword(reader, manager + 0x1970 + side * 40)
        if not wrapper:
            return None
        buffer = qword(reader, pointer(wrapper) + 0x10)
        if not buffer:
            return None
        lineups.append((wrapper, pointer(buffer)))
    return manager, record_wrapper, record_buffer, tuple(lineups)


def select_target(records, native_id):
    matches = [r for r in records if r["native_player_id"] == native_id]
    if len(matches) == 1:
        return {"state": "unique_native_id_candidate", "record": matches[0],
                "career_branch_verified": False}
    return {"state": "absent" if not matches else "ambiguous_native_id",
            "record": None, "career_branch_verified": False}


def capture_stats(reader, target_native_id):
    validate_profile(reader)
    before = roots(reader)
    common = {
        "schema": SCHEMA, "profile": PROFILE,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "target_native_player_id": target_native_id,
        "snapshot_ready": False, "match_ready": False, "d2_ready": False,
        "event_identity_verified": False, "active_match_verified": False,
        "lifecycle_verified": False, "game_writes": 0, "application_writes": 0,
        "metric_indices": METRICS,
        "verified_code_sha256": {hex(rva): digest for rva, _, digest in SIGNATURES},
        "counter_semantics": "native totals; roster presence and zero counters do not certify appearance or applicability",
        "lineup_semantics": "current native slot order candidate; not result-menu row order or historical appearance list",
        "bucket_semantics": "mode5 sums first 8; ninth excluded; individual bucket scopes unresolved",
        "native_dribble_semantics": "native Dribble label; take-on interpretation unverified",
        "raw_record_semantics": "exact two-side capacity blocks, including empty slots and unknown fields; base64 bytes with SHA256",
        "process_instance": {"pid": getattr(reader, "pid", None),
                             "creation_time_100ns": getattr(reader, "creation_time_100ns", None)},
        "runtime_instance_token": hashlib.sha256(json.dumps(
            [reader.base, getattr(reader, "pid", None), getattr(reader, "creation_time_100ns", None), before],
            separators=(",", ":")).encode()).hexdigest() if before is not None else None,
        "runtime_token_semantics": "process/allocation diagnostic only; not a match ID; same addresses may be reused",
    }
    if before is None:
        return {**common, "status": "unavailable", "reason": "runtime_buffers_absent",
                "records": None, "raw_record_blocks": None,
                "target": {"state": "unavailable", "record": None}}
    manager, wrapper, buffer, lineup_roots = before
    stats_base = pointer(buffer + 8 + 0x750D0)
    records, lineup_headers, raw_blocks = [], [], []
    started = time.monotonic()
    for side, (_, lineup_buffer) in enumerate(lineup_roots):
        header = list(struct.unpack("<42I", reader.read(lineup_buffer + 8, 0xA8)))
        if header[0] != side or any(x != 255 and x >= CAPACITY for x in header[1:41]):
            raise RuntimeStatsError("Invalid lineup header")
        lineup_headers.append(header)
        block = reader.read(stats_base + 0x64D8 + side * 0x59650, CAPACITY * RECORD_SIZE)
        raw_blocks.append({"side": side, "encoding": "base64", "record_size_bytes": RECORD_SIZE,
                           "record_capacity": CAPACITY, "sha256": hashlib.sha256(block).hexdigest(),
                           "data": base64.b64encode(block).decode("ascii")})
        for slot in range(CAPACITY):
            record = parse_player_record(block[slot * RECORD_SIZE:(slot + 1) * RECORD_SIZE])
            if record is not None:
                record.update(side=side, roster_slot=slot,
                              current_pitch_slots_candidate=[i for i, x in enumerate(header[1:12]) if x == slot])
                records.append(record)
    # Pointer stability avoids assigning records from an object switched during
    # collection. This does NOT make live counters an atomic frame snapshot.
    if roots(reader) != before:
        raise RuntimeStatsError("Runtime roots changed during capture; retry")
    for side, (_, lineup_buffer) in enumerate(lineup_roots):
        if list(struct.unpack("<42I", reader.read(lineup_buffer + 8, 0xA8))) != lineup_headers[side]:
            raise RuntimeStatsError("Lineup changed during capture; retry")
    totals = [{name: sum(r["counters"][name] for r in records if r["side"] == side)
               for name in ("goals", "shots", "shots_on_target", "passes", "successful_passes")}
              for side in range(2)]
    return {**common, "status": "runtime_preview", "records": records,
            "raw_record_blocks": raw_blocks,
            "target": select_target(records, target_native_id),
            "personal_record_sums_by_side": totals, "lineup_headers_raw": lineup_headers,
            "collection_seconds": round(time.monotonic() - started, 6),
            "consistency": "roots_and_lineup_stable; counters_not_atomic",
            "reason": "match_completion_and_career_branch_not_certified"}


class WindowsReader:
    """Exact-executable QUERY/VM_READ only; bounded reads and handle lifetime."""

    def __init__(self, pid, executable):
        if sys.platform != "win32" or C.sizeof(C.c_void_p) != 8:
            raise RuntimeStatsError("64-bit Windows Python required")
        executable = Path(executable).resolve(strict=True)
        if executable.name.lower() != "pes2021.exe":
            raise RuntimeStatsError("Expected executable must be PES2021.exe")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
        self.k.ReadProcessMemory.restype = W.BOOL
        self.k.GetProcessTimes.argtypes = [W.HANDLE, *([C.POINTER(W.FILETIME)] * 4)]
        self.k.GetProcessTimes.restype = W.BOOL
        self.handle = self.k.OpenProcess(0x0410, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            path, length = C.create_unicode_buffer(32768), W.DWORD(32768)
            if not self.k.QueryFullProcessImageNameW(self.handle, 0, path, C.byref(length)):
                raise C.WinError(C.get_last_error())
            if Path(path.value).resolve() != executable:
                raise RuntimeStatsError("Exact executable mismatch")
            creation, ended, kernel, user = (W.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(self.handle, C.byref(creation), C.byref(ended), C.byref(kernel), C.byref(user)):
                raise C.WinError(C.get_last_error())
            self.pid = pid
            self.creation_time_100ns = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            ps = C.WinDLL("psapi", use_last_error=True)
            ps.EnumProcessModulesEx.argtypes = [W.HANDLE, C.POINTER(C.c_void_p), W.DWORD, C.POINTER(W.DWORD), W.DWORD]
            ps.EnumProcessModulesEx.restype = W.BOOL
            modules, needed = (C.c_void_p * 1024)(), W.DWORD()
            if not ps.EnumProcessModulesEx(self.handle, modules, C.sizeof(modules), C.byref(needed), 3):
                raise C.WinError(C.get_last_error())
            self.base = pointer(int(modules[0]))
            self.bytes = 0
            self.started = time.monotonic()
        except BaseException:
            self.close()
            raise

    def read(self, address, size):
        pointer(address)
        pointer(address + size - 1)
        if not 0 < size <= 1024 * 1024 or self.bytes + size > 8 * 1024 * 1024 or time.monotonic() - self.started > 10:
            raise RuntimeStatsError("Runtime read budget exceeded")
        blob, got = C.create_string_buffer(size), C.c_size_t()
        self.bytes += size
        if not self.k.ReadProcessMemory(self.handle, C.c_void_p(address), blob, size, C.byref(got)) or got.value != size:
            raise C.WinError(C.get_last_error())
        return blob.raw

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def new_output(path, raw_root=RAW_ROOT):
    output = Path(path).resolve()
    if not output.is_relative_to(Path(raw_root).resolve()) or output.exists() or not output.parent.is_dir():
        raise RuntimeStatsError("Output must be a new JSON inside existing raw/real_test")
    if output.suffix.lower() != ".json":
        raise RuntimeStatsError("JSON output required")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--exe", type=Path, default=Path.home() / "Desktop/pes/PES2021.exe")
    parser.add_argument("--native-player-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reader = None
    try:
        output = new_output(args.output)
        if not 0 < args.pid < 2**32 or not 0 < args.native_player_id < 0xFFFFFFFF:
            raise RuntimeStatsError("Invalid PID or native player ID")
        reader = WindowsReader(args.pid, args.exe)
        report = capture_stats(reader, args.native_player_id)
        report.update(pid=args.pid, executable=str(args.exe.resolve()), read_bytes=reader.bytes)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        target = report["target"]["record"]
        print(json.dumps({"status": report["status"], "output": str(output),
                          "records": len(report["records"]) if report["records"] is not None else None,
                          "target_state": report["target"]["state"],
                          "target_counters": target["counters"] if target else None,
                          "personal_record_sums_by_side": report.get("personal_record_sums_by_side"),
                          "read_bytes": reader.bytes, "match_ready": False}, ensure_ascii=False))
        return 0
    except (RuntimeStatsError, OSError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc),
                          "game_writes": 0, "application_writes": 0}, ensure_ascii=False))
        return 1
    finally:
        if reader:
            reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
