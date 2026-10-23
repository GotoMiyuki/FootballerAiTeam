"""Bounded, read-only native BAL player-record experiment; no UI or injection.

Only opens the exact local PES executable with QUERY_INFORMATION | VM_READ.
Exports matching candidate records, never chooses a current-state pointer.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from game_adapters.pes2021_save import ATTRIBUTE_LAYOUT, POSITIONS, RAW_ROOT, bits

PES_PATH = Path(__file__).resolve().parents[2] / "pes" / "PES2021.exe"
RECORD_SIZE = 0x17C


def inspect_record(record, player_id, name):
    if len(record) != RECORD_SIZE or bits(record, 0x30, 0, 32) != player_id:
        return None
    try:
        actual_name = record[0x38:0x75].split(b"\0", 1)[0].decode("utf-8")
    except UnicodeError:
        return None
    age, position = bits(record, 0x1C, 0, 6), bits(record, 7, 4, 4)
    if actual_name != name or not (15 <= age <= 60 and position < len(POSITIONS)
                                  and 100 <= record[0] <= 230 and 30 <= record[1] <= 150):
        return None
    return {"native_player_id": player_id, "name": actual_name, "age": age,
            "position": POSITIONS[position], "height_cm": record[0], "weight_kg": record[1],
            "attributes": {key: bits(record, offset, start, 7)
                           for key, (offset, start) in ATTRIBUTE_LAYOUT.items()},
            "validation": "candidate; active BAL ownership and pointer lifetime unverified"}


def probe(pid, player_id, name, seconds=25, max_bytes=512 * 1024 * 1024, max_region_bytes=None):
    if not (isinstance(name, str) and 1 <= len(name.encode("utf-8")) <= 60
            and type(player_id) is int and 0 < player_id <= 0xFFFFFFFF
            and type(pid) is int and 0 < pid <= 0xFFFFFFFF and 0 < seconds <= 25
            and 0 < max_bytes <= 2 * 1024 * 1024 * 1024):
        raise RuntimeError("Invalid target or probe budget")
    if sys.platform != "win32" or C.sizeof(C.c_void_p) != 8:
        raise RuntimeError("Requires 64-bit Windows Python")
    k = C.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes, k.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
    k.CloseHandle.argtypes, k.CloseHandle.restype = [W.HANDLE], W.BOOL
    k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    k.QueryFullProcessImageNameW.restype = W.BOOL
    class Region(C.Structure):
        _fields_ = [("base", C.c_void_p), ("allocation_base", C.c_void_p),
                    ("allocation_protect", W.DWORD), ("size", C.c_size_t),
                    ("state", W.DWORD), ("protect", W.DWORD), ("type", W.DWORD)]
    k.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(Region), C.c_size_t]
    k.VirtualQueryEx.restype = C.c_size_t
    k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
    k.ReadProcessMemory.restype = W.BOOL
    handle = k.OpenProcess(0x0410, False, pid)  # No WRITE/OPERATION/thread/debug privileges.
    if not handle:
        raise C.WinError(C.get_last_error())
    started, read_bytes, regions, failures, address, stopped = time.monotonic(), 0, 0, 0, 0, "address_space_end"
    candidates, seen, name_hits, skipped_large_regions = [], set(), 0, 0
    needle = name.encode("utf-8") + b"\0"
    try:
        path, length = C.create_unicode_buffer(32768), W.DWORD(32768)
        if not k.QueryFullProcessImageNameW(handle, 0, path, C.byref(length)):
            raise C.WinError(C.get_last_error())
        if Path(path.value).resolve() != PES_PATH.resolve():
            raise RuntimeError("PID is not the exact authorized PES2021.exe")
        def read(at, size):
            buffer, count = C.create_string_buffer(size), C.c_size_t()
            ok = k.ReadProcessMemory(handle, C.c_void_p(at), buffer, size, C.byref(count))
            return buffer.raw[:count.value] if ok else b""
        while address < 0x7FFFFFFF0000:
            if time.monotonic() - started >= seconds or read_bytes >= max_bytes:
                stopped = "budget_reached"
                break
            region = Region()
            if k.VirtualQueryEx(handle, C.c_void_p(address), C.byref(region), C.sizeof(region)) != C.sizeof(region):
                break
            base, end = int(region.base or 0), int(region.base or 0) + region.size
            if end <= address:
                stopped = "invalid_region"
                break
            address = end
            if region.state != 0x1000 or region.type != 0x20000 or region.protect & 0x100:
                continue
            if region.protect & 0xFF not in {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}:
                continue
            if max_region_bytes is not None and region.size > max_region_bytes:
                skipped_large_regions += 1
                continue
            regions += 1
            cursor, previous = base, b""
            while cursor < end:
                if time.monotonic() - started >= seconds or read_bytes >= max_bytes:
                    stopped = "budget_reached"
                    break
                size = min(1024 * 1024, end - cursor, max_bytes - read_bytes)
                chunk = read(cursor, size)
                read_bytes += size
                if not chunk:
                    failures += 1
                    previous = b""
                    cursor += size
                    continue
                data, origin, search = previous + chunk, cursor - len(previous), 0
                while (hit := data.find(needle, search)) != -1:
                    if time.monotonic() - started >= seconds:
                        stopped = "budget_reached"
                        break
                    name_hits += 1
                    search = hit + 1
                    record_address = origin + hit - 0x38
                    if record_address in seen or record_address < base or record_address + RECORD_SIZE > end:
                        continue
                    seen.add(record_address)
                    record = read(record_address, RECORD_SIZE)
                    parsed = inspect_record(record, player_id, name)
                    if parsed is not None:
                        candidates.append({"address": record_address, "record": record, "fields": parsed})
                        if len(candidates) >= 16:
                            stopped = "candidate_limit"
                            break
                if stopped in {"candidate_limit", "budget_reached"}:
                    break
                previous, cursor = data[-RECORD_SIZE:], cursor + size
            if stopped in {"candidate_limit", "budget_reached"}:
                break
        # Reread each exact record once: stability is evidence, not proof of active ownership.
        for candidate in candidates:
            candidate["unchanged_on_reread"] = read(candidate["address"], RECORD_SIZE) == candidate["record"]
        return {"pid": pid, "executable": path.value, "access_mask": "0x0410",
                "scanned_bytes": read_bytes, "readable_private_regions": regions,
                "name_hits": name_hits,
                "max_region_bytes": max_region_bytes, "skipped_large_regions": skipped_large_regions,
                "read_failures": failures, "elapsed_seconds": round(time.monotonic() - started, 3),
                "stop_reason": stopped, "candidates": candidates,
                "active_pointer_verified": False, "game_writes": 0, "application_writes": 0}
    finally:
        k.CloseHandle(handle)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--player-id", type=int, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-mib", type=int, choices=(512, 1024, 2048), default=512)
    parser.add_argument("--max-region-mib", type=int, choices=(16, 32, 64))
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if not output.is_relative_to(RAW_ROOT.resolve()) or output.exists():
            raise RuntimeError("Output must be a new raw/real_test directory")
        result = probe(args.pid, args.player_id, args.name, max_bytes=args.max_mib * 1024 * 1024,
                       max_region_bytes=args.max_region_mib * 1024 * 1024 if args.max_region_mib else None)
        output.mkdir(parents=True, exist_ok=False)
        for index, candidate in enumerate(result["candidates"]):
            record = candidate.pop("record")
            reference = f"candidate-{index:02}.bin"
            (output / reference).write_bytes(record)
            candidate["raw_reference"], candidate["sha256"] = reference, hashlib.sha256(record).hexdigest()
        result["captured_at"] = datetime.now(timezone.utc).isoformat()
        (output / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "candidate_probe_only", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
