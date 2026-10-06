"""Offline checks: never send real Windows inputs."""
import ctypes
import unittest
from unittest.mock import patch

from game_adapters.keyboard_probe import INPUT, EXTENDED, KEYUP, SCANCODE, make_event, tap


class FakeApi:
    def __init__(self, foreground=7):
        self.current = foreground
        self.events = []

    def foreground(self):
        return self.current

    def held_keys(self):
        return []

    def send(self, event):
        self.events.append(event.ki.dwFlags)
        return {"inserted": 1, "last_error": 0}


class KeyboardProbeTests(unittest.TestCase):
    def test_native_input_union_abi(self):
        expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(INPUT), expected)
        self.assertEqual(INPUT.u.offset, 8 if expected == 40 else 4)

    def test_arrow_extended_scan_and_release(self):
        event = make_event("right", "scan", True)
        self.assertEqual((event.ki.wVk, event.ki.wScan, event.ki.dwFlags),
                         (0, 0x4D, EXTENDED | SCANCODE | KEYUP))
        self.assertEqual(make_event("enter", "vk").ki.wVk, 0x0D)

    def test_no_events_when_target_is_not_foreground(self):
        api = FakeApi(foreground=8)
        with self.assertRaisesRegex(RuntimeError, "not foreground"):
            tap(api, 7, "enter", "scan", 100)
        self.assertEqual(api.events, [])

    def test_no_events_when_physical_key_is_held(self):
        api = FakeApi()
        api.held_keys = lambda: [0x10]
        with self.assertRaisesRegex(RuntimeError, "held"):
            tap(api, 7, "enter", "scan", 100)
        self.assertEqual(api.events, [])

    def test_release_after_wait_exception(self):
        api = FakeApi()
        with patch("game_adapters.keyboard_probe.time.sleep", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                tap(api, 7, "enter", "scan", 100)
        self.assertEqual(api.events, [SCANCODE, SCANCODE | KEYUP])

    def test_focus_loss_still_releases_key(self):
        api = FakeApi()
        original = api.send

        def send(event):
            result = original(event)
            api.current = 8
            return result

        api.send = send
        result = tap(api, 7, "enter", "scan", 100)
        self.assertTrue(result["focus_lost"])
        self.assertEqual(api.events, [SCANCODE, SCANCODE | KEYUP])


if __name__ == "__main__":
    unittest.main()
