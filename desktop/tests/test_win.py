#!/usr/bin/env python3
import ctypes
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import paths  # noqa: F401
import core
import main as win_main
import pad as win_pad
from core import HELLO, HELLO_MAGIC, PACKET


class ReportTest(unittest.TestCase):
    def report(self, buttons=0, axes=(0,) * 8):
        return win_pad.xbox_report((buttons, list(axes)))

    def test_buttons_follow_packet_order(self):
        names = [0x1000, 0x2000, 0x4000, 0x8000, 0x0100, 0x0200, 0x0020, 0x0010, 0x0400, 0x0040, 0x0080]
        for bit, mask in enumerate(names):
            self.assertEqual(self.report(1 << bit)[0], mask)
        self.assertEqual(self.report(0b11)[0], 0x3000)

    def test_sticks_flip_y_and_triggers_pass_through(self):
        self.assertEqual(self.report(axes=(100, 200, -300, -32767, 255, 7, 0, 0)), (0, 255, 7, 100, -200, -300, 32767))

    def test_hat_becomes_dpad_bits(self):
        self.assertEqual(self.report(axes=(0, 0, 0, 0, 0, 0, -1, -1))[0], win_pad.DPAD_LEFT | win_pad.DPAD_UP)
        self.assertEqual(self.report(axes=(0, 0, 0, 0, 0, 0, 1, 1))[0], win_pad.DPAD_RIGHT | win_pad.DPAD_DOWN)

    def test_report_fits_the_driver_struct(self):
        self.assertEqual(ctypes.sizeof(win_pad.Report), 12)
        win_pad.Report(*self.report(0x7ff, (32767, -32767, 32767, -32767, 255, 255, 1, 1)))


class FakePad:
    def __init__(self):
        self.states = []
        self.closed = False

    def apply(self, state):
        self.states.append(state)

    def close(self):
        self.closed = True


def hello(features=core.FEATURE_RUMBLE):
    return HELLO.pack(HELLO_MAGIC, 1, 9) + b'Pixel\0Xbox pad\0' + bytes([features])


class ConsoleTest(unittest.TestCase):
    """The real loop over real UDP sockets on this machine, with a fake pad."""

    def setUp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.phone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.phone.settimeout(2)
        self.config = Path(tempfile.mkdtemp()) / 'config.json'
        self.said = []
        self.pads = []
        self.console = win_main.Console(self.sock, self.make_pad, self.config, self.said.append)

    def tearDown(self):
        self.console.close()
        for sock in (self.sock, self.phone, self.console.wake, self.console.waker):
            sock.close()

    def make_pad(self):
        self.pads.append(FakePad())
        return self.pads[-1]

    def send(self, data):
        self.phone.sendto(data, self.sock.getsockname())
        self.console.step(timeout=2)

    def allow(self):
        self.send(hello())
        self.console.post('line', 'y')
        self.console.step(timeout=2)

    def test_unknown_phone_asks_and_y_allows_and_saves_it(self):
        self.send(hello())
        self.assertIn('Pixel (127.0.0.1) wants to connect', self.said[0])
        self.assertEqual(self.pads, [])
        self.console.post('line', 'y')
        self.console.step(timeout=2)
        self.assertEqual(core.load_config(self.config)['phones'], {'127.0.0.1': 'Pixel'})

    def test_allowed_phone_drives_the_pad_and_gets_pings_back(self):
        self.allow()
        self.send(hello())
        self.send(PACKET.pack(1, 5, 0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(self.pads[0].states[-1], (1, [5, 0, 0, 0, 0, 0, 0, 0]))
        self.send(b'12345678')
        self.assertEqual(self.phone.recv(64), b'12345678')

    def test_a_stall_from_a_connected_phone_is_logged(self):
        self.allow()
        self.send(hello())
        self.console.last_packet = time.perf_counter() - 2
        with self.assertLogs('musubi', 'WARNING') as logs:
            self.send(hello())
        self.assertIn('gap', logs.output[0])

    def test_rumble_from_the_driver_thread_reaches_the_phone(self):
        self.allow()
        self.send(hello())
        self.console.on_rumble(255, 0)
        self.console.step(timeout=2)
        self.assertEqual(self.phone.recv(64), core.rumble_message(0xffff, 0, 0))
        self.console.on_rumble(0, 0)
        self.console.step(timeout=2)
        self.assertEqual(self.phone.recv(64), core.rumble_message(0, 0, 0))

    def test_a_phone_that_never_said_rumble_gets_none(self):
        self.allow()
        self.send(hello(features=0))
        self.console.on_rumble(255, 255)
        self.console.step(timeout=2)
        self.phone.settimeout(0.2)
        with self.assertRaises(TimeoutError):
            self.phone.recv(64)


class XInputState(ctypes.Structure):
    _fields_ = [('packet', ctypes.c_ulong), ('pad', win_pad.Report)]


class PlugTest(unittest.TestCase):
    def pad(self, plug_results):
        lib = mock.MagicMock()
        lib.vigem_connect.return_value = win_pad.SUCCESS
        lib.vigem_target_x360_register_notification.return_value = win_pad.SUCCESS
        lib.vigem_target_add.side_effect = plug_results
        with mock.patch.object(win_pad, 'load', return_value=lib):
            return win_pad.Pad(lambda large, small: None), lib

    def test_a_plug_in_that_times_out_is_tried_again_on_a_new_connection(self):
        pad, lib = self.pad([0xE0000007, win_pad.SUCCESS])
        self.assertEqual((lib.vigem_target_add.call_count, lib.vigem_disconnect.call_count), (2, 1))
        self.assertTrue(pad.target)

    def test_it_gives_up_after_three_tries(self):
        with self.assertRaisesRegex(OSError, 'plug in the virtual pad'):
            self.pad([0xE0000007] * 3)


@unittest.skipUnless(sys.platform == 'win32', 'needs Windows')
class DriverTest(unittest.TestCase):
    """The real driver, read back the way a game reads it."""

    def setUp(self):
        self.rumbles = []
        try:
            self.pad = win_pad.Pad(lambda large, small: self.rumbles.append((large, small)))
        except OSError as ex:
            self.skipTest(str(ex))
        self.xinput = ctypes.windll.xinput1_4

    def tearDown(self):
        self.pad.close()

    def wait_for(self, check):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = check()
            if result is not None:
                return result
            time.sleep(0.05)
        self.fail('timed out')

    def find_slot(self):
        state = XInputState()
        for slot in range(4):
            if self.xinput.XInputGetState(slot, ctypes.byref(state)) == 0 and state.pad.buttons == 0x1000:
                return slot, state
        return None

    def test_games_see_the_buttons_and_we_see_their_rumble(self):
        self.pad.apply((1, [1000, 2000, 0, 0, 255, 0, 0, 0]))
        slot, state = self.wait_for(self.find_slot)
        self.assertEqual((state.pad.lx, state.pad.ly, state.pad.left_trigger), (1000, -2000, 255))
        vibration = (ctypes.c_ushort * 2)(0xffff, 0)
        self.assertEqual(self.xinput.XInputSetState(slot, ctypes.byref(vibration)), 0)
        self.wait_for(lambda: True if (255, 0) in self.rumbles else None)


if __name__ == '__main__':
    unittest.main()
