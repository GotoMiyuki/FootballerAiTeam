"""One-key PES input experiment. Diagnostic-only unless --execute is explicit.

Uses documented Win32 SendInput, not a driver or game-process injection.
Run from the workspace with Python; no game control runs on import.
"""

from __future__ import annotations

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone


MENU_KEYS = {
    "enter": (0x0D, 0x1C, False), "esc": (0x1B, 0x01, False),
    "left": (0x25, 0x4B, True), "up": (0x26, 0x48, True),
    "right": (0x27, 0x4D, True), "down": (0x28, 0x50, True),
    "x": (0x58, 0x2D, False), "v": (0x56, 0x2F, False),
    "q": (0x51, 0x10, False), "e": (0x45, 0x12, False),  # Existing LB/RB mapping.
    "space": (0x20, 0x39, False),
}
PES_PATH = Path(__file__).resolve().parents[2] / "pes" / "PES2021.exe"
RAW_ROOT = Path(__file__).resolve().parent / "raw" / "real_test"
EXTENDED, KEYUP, SCANCODE = 0x01, 0x02, 0x08


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", C.c_uint16), ("wScan", C.c_uint16),
                ("dwFlags", C.c_uint32), ("time", C.c_uint32),
                ("dwExtraInfo", C.c_size_t)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", C.c_int32), ("dy", C.c_int32),
                ("mouseData", C.c_uint32), ("dwFlags", C.c_uint32),
                ("time", C.c_uint32), ("dwExtraInfo", C.c_size_t)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", C.c_uint32), ("wParamL", C.c_uint16),
                ("wParamH", C.c_uint16)]


class INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", C.c_uint32), ("u", INPUTUNION)]


class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [("Sid", C.c_void_p), ("Attributes", C.c_uint32)]


def make_event(key: str, encoding: str, release: bool = False) -> INPUT:
    vk, scan, extended = MENU_KEYS[key]
    flags = (EXTENDED if extended else 0) | (KEYUP if release else 0)
    if encoding == "scan":
        flags |= SCANCODE
        vk = 0
    elif encoding != "vk":
        raise ValueError("encoding must be scan or vk")
    return INPUT(type=1, ki=KEYBDINPUT(vk, scan, flags, 0, 0))


def safe_output(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(RAW_ROOT.resolve()) or path.suffix != ".json":
        raise ValueError("Audit must be a JSON file under game_adapters/raw/real_test")
    if path.exists():
        raise ValueError("Refusing to overwrite an audit")
    return path


class Win32:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows required")
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.advapi = C.WinDLL("advapi32", use_last_error=True)
        self._declare()
        expected = 40 if C.sizeof(C.c_void_p) == 8 else 28
        if C.sizeof(INPUT) != expected:
            raise RuntimeError(f"Incorrect INPUT ABI: {C.sizeof(INPUT)} != {expected}")

    def _declare(self):
        declarations = [
            (self.user.IsWindow, [C.c_void_p], C.c_int),
            (self.user.GetForegroundWindow, [], C.c_void_p),
            (self.user.GetWindowThreadProcessId, [C.c_void_p, C.POINTER(C.c_uint32)], C.c_uint32),
            (self.user.GetKeyboardLayout, [C.c_uint32], C.c_void_p),
            (self.user.GetWindowTextW, [C.c_void_p, C.c_wchar_p, C.c_int], C.c_int),
            (self.user.GetAsyncKeyState, [C.c_int], C.c_int16),
            (self.user.SendInput, [C.c_uint32, C.POINTER(INPUT), C.c_int], C.c_uint32),
            (self.kernel.OpenProcess, [C.c_uint32, C.c_int, C.c_uint32], C.c_void_p),
            (self.kernel.CloseHandle, [C.c_void_p], C.c_int),
            (self.kernel.QueryFullProcessImageNameW, [C.c_void_p, C.c_uint32, C.c_wchar_p, C.POINTER(C.c_uint32)], C.c_int),
            (self.kernel.GetCurrentProcess, [], C.c_void_p),
            (self.kernel.GetCurrentProcessId, [], C.c_uint32),
            (self.advapi.OpenProcessToken, [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)], C.c_int),
            (self.advapi.GetTokenInformation, [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)], C.c_int),
            (self.advapi.GetSidSubAuthorityCount, [C.c_void_p], C.POINTER(C.c_ubyte)),
            (self.advapi.GetSidSubAuthority, [C.c_void_p, C.c_uint32], C.POINTER(C.c_uint32)),
        ]
        for fn, args, result in declarations:
            fn.argtypes, fn.restype = args, result

    def integrity(self, process):
        token = C.c_void_p()
        if not self.advapi.OpenProcessToken(process, 0x0008, C.byref(token)):
            raise C.WinError(C.get_last_error())
        try:
            size = C.c_uint32()
            self.advapi.GetTokenInformation(token, 25, None, 0, C.byref(size))
            if not size.value:
                raise C.WinError(C.get_last_error())
            data = C.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(token, 25, data, size, C.byref(size)):
                raise C.WinError(C.get_last_error())
            sid = C.cast(data, C.POINTER(SID_AND_ATTRIBUTES)).contents.Sid
            count = self.advapi.GetSidSubAuthorityCount(sid).contents.value
            return self.advapi.GetSidSubAuthority(sid, count - 1).contents.value
        finally:
            self.kernel.CloseHandle(token)

    def describe(self, hwnd):
        if not self.user.IsWindow(hwnd):
            raise RuntimeError("Window is unavailable in this execution desktop")
        pid = C.c_uint32()
        tid = self.user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            raise C.WinError(C.get_last_error())
        try:
            path = C.create_unicode_buffer(32768)
            length = C.c_uint32(len(path))
            if not self.kernel.QueryFullProcessImageNameW(process, 0, path, C.byref(length)):
                raise C.WinError(C.get_last_error())
            if os.path.normcase(path.value) != os.path.normcase(str(PES_PATH)):
                raise RuntimeError("Window process is not the permitted PES executable")
            title = C.create_unicode_buffer(512)
            self.user.GetWindowTextW(hwnd, title, len(title))
            layout = self.user.GetKeyboardLayout(tid) or 0
            return {"hwnd": hwnd, "pid": pid.value, "thread_id": tid,
                    "executable": path.value, "title": title.value,
                    "foreground_hwnd": self.user.GetForegroundWindow(),
                    "layout": hex(layout), "language_id": hex(layout & 0xFFFF),
                    "integrity_rid": self.integrity(process)}
        finally:
            self.kernel.CloseHandle(process)

    def foreground(self):
        return self.user.GetForegroundWindow()

    def held_keys(self):
        keys = {v[0] for v in MENU_KEYS.values()} | {0x01, 0x02, 0x10, 0x11, 0x12, 0x5B, 0x5C}
        return sorted(k for k in keys if self.user.GetAsyncKeyState(k) & 0x8000)

    def send(self, event):
        C.set_last_error(0)
        sent = self.user.SendInput(1, C.byref(event), C.sizeof(INPUT))
        return {"inserted": sent, "last_error": C.get_last_error()}


def tap(api, hwnd, key, encoding, hold_ms):
    """One bounded tap; release our own key even if the window loses focus."""
    if not 20 <= hold_ms <= 500:
        raise ValueError("hold_ms must be 20..500")
    if api.foreground() != hwnd:
        raise RuntimeError("PES is not foreground; no input sent")
    if api.held_keys():
        raise RuntimeError("Physical key/button is held; no input sent")
    down, up = make_event(key, encoding), make_event(key, encoding, True)
    result = {"key": key, "encoding": encoding, "requested_hold_ms": hold_ms,
              "scan_code": hex(down.ki.wScan), "down_flags": hex(down.ki.dwFlags)}
    result["down"] = api.send(down)
    if result["down"]["inserted"] != 1:
        raise RuntimeError(f"SendInput down failed: {result['down']}")
    started = time.perf_counter()
    try:
        deadline = started + hold_ms / 1000
        while time.perf_counter() < deadline:
            if api.foreground() != hwnd:
                result["focus_lost"] = True
                break
            time.sleep(min(0.005, max(0, deadline - time.perf_counter())))
    finally:
        result["up"] = api.send(up)
        result["actual_hold_ms"] = round((time.perf_counter() - started) * 1000, 3)
    if result["up"]["inserted"] != 1:
        raise RuntimeError(f"SendInput release failed: {result}")
    result["foreground_after"] = api.foreground()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--key", choices=MENU_KEYS, default="enter")
    parser.add_argument("--encoding", choices=("scan", "vk"), default="scan")
    parser.add_argument("--hold-ms", type=int, default=100)
    parser.add_argument("--execute", action="store_true", help="Send exactly one key; default only diagnoses")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = safe_output(args.output) if args.output else None
    record = {"at": datetime.now(timezone.utc).isoformat(), "execute": args.execute,
              "input_size": C.sizeof(INPUT), "key": args.key,
              "encoding": args.encoding, "hold_ms": args.hold_ms}
    status = 0
    try:
        api = Win32()
        record["target"] = api.describe(args.hwnd)
        record["sender_integrity_rid"] = api.integrity(api.kernel.GetCurrentProcess())
        if args.execute:
            if record["sender_integrity_rid"] < record["target"]["integrity_rid"]:
                raise RuntimeError("Sender integrity is below target; no input sent")
            record["tap"] = tap(api, args.hwnd, args.key, args.encoding, args.hold_ms)
        record["status"] = "events_inserted" if args.execute else "diagnostic_only"
    except Exception as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        status = 1
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
