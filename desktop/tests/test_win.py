#!/usr/bin/env python3
import ctypes
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from array import array
from pathlib import Path
from unittest import mock

import paths  # noqa: F401
import core
import firewall
import main as win_main
import pad as win_pad
import sound as win_sound
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


class FakeSound:
    def __init__(self, on_pcm):
        self.on_pcm = on_pcm
        self.capturing = False
        self.error = None
        self.starts = 0

    def start(self):
        if not self.capturing:
            self.capturing = True
            self.starts += 1

    def stop(self):
        self.capturing = False


def hello(features=core.FEATURE_RUMBLE, rtt=9, delay=0, slots_mask=None, extra=()):
    pkt = HELLO.pack(HELLO_MAGIC, 1, rtt) + b'Pixel\0Xbox pad\0' + bytes([features])
    if slots_mask is not None:
        return pkt + delay.to_bytes(2, 'big') + bytes([0, slots_mask]) + b'\0'.join(extra)
    if delay:
        return pkt + delay.to_bytes(2, 'big')
    return pkt


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
        self.console = win_main.Console(self.sock, self.make_pad, self.config, self.said.append, FakeSound)

    def tearDown(self):
        self.console.close()
        for sock in (self.sock, self.phone, self.console.wake, self.console.waker):
            sock.close()

    def make_pad(self, slot=0):
        pad = FakePad()
        pad.slot = slot
        self.pads.append(pad)
        return pad

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

    def test_ping_only_changes_do_not_log_status(self):
        self.allow()
        self.send(hello(rtt=9))
        with self.assertNoLogs('musubi', 'INFO'):
            self.send(hello(rtt=15))
        with self.assertLogs('musubi', 'INFO') as logs:
            self.send(hello(features=core.FEATURE_AUDIO, rtt=15))
        self.assertIn('status ', logs.output[0])

    def test_a_sound_delay_logs_when_it_appears_but_not_when_it_jitters(self):
        self.allow()
        self.send(hello())
        with self.assertLogs('musubi', 'INFO') as logs:
            self.send(hello(delay=98))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("'delay': 98", logs.output[0])
        with self.assertNoLogs('musubi', 'INFO'):
            self.send(hello(delay=105))

    def test_a_phone_that_wants_sound_gets_it_until_it_leaves(self):
        self.allow()
        self.send(hello(features=core.FEATURE_AUDIO))
        self.assertTrue(self.console.sound.capturing)
        pcm = b'\x01' * core.CHUNK
        self.console.sound.on_pcm(pcm)
        self.console.step(timeout=2)
        self.assertEqual(self.phone.recv(2048), b'PBAU\0\0' + pcm)
        self.console.bridge.last_seen -= core.PHONE_TIMEOUT + 1
        self.console.step(timeout=0)
        self.assertFalse(self.console.sound.capturing)

    def test_stable_sound_packs_7_5_ms_chunks(self):
        self.allow()
        self.send(hello(features=core.FEATURE_AUDIO | core.FEATURE_STABLE))
        pcm = b'\x01' * core.STABLE_CHUNK
        # Two callbacks' worth, so the packet has to be put together across them.
        self.console.sound.on_pcm(pcm[:core.CHUNK])
        self.console.sound.on_pcm(pcm[core.CHUNK:])
        self.console.step(timeout=2)
        self.assertEqual(self.phone.recv(2048), b'PBAU\0\0' + pcm)

    def test_a_phone_without_the_sound_feature_never_starts_it(self):
        self.allow()
        self.send(hello())
        self.assertEqual(self.console.sound.starts, 0)

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

    def test_multi_pad_routes_input_and_rumble_per_slot(self):
        self.allow()
        self.send(hello(slots_mask=0b0011, extra=(b'Pad 2',)))
        self.assertEqual([pad.slot for pad in self.pads], [0, 1])
        self.send(core.slotted_packet(1, PACKET.pack(2, 0, 0, 0, 0, 0, 0, 0, 0)))
        self.assertEqual(self.pads[0].states, [])
        self.assertEqual(self.pads[1].states[-1], (2, [0] * 8))
        self.console.on_rumble(255, 0, 1)
        self.console.step(timeout=2)
        self.assertEqual(self.phone.recv(64), core.rumble_message(0xffff, 0, 0, slot=1))


class SoundTest(unittest.TestCase):
    def start(self, sound):
        """start(), then wait for the opening it does on its own thread."""
        sound.start()
        thread = sound.opening
        if thread is not None:
            thread.join(2)

    def test_a_failed_start_waits_for_a_stop_before_trying_again(self):
        opener = mock.Mock(side_effect=OSError('No sound output was found on this PC.'))
        sound = win_sound.Sound(lambda pcm: None, opener)
        with self.assertLogs('musubi', 'ERROR'):
            self.start(sound)
        self.start(sound)
        self.assertEqual((opener.call_count, sound.error), (1, 'No sound output was found on this PC.'))
        sound.stop()
        self.assertIsNone(sound.error)

    def test_a_good_start_captures_until_stopped(self):
        capture = mock.Mock(channels=2)
        capture.name = 'Speakers [Loopback]'
        sound = win_sound.Sound(lambda pcm: None, mock.Mock(return_value=capture))
        self.start(sound)
        self.assertTrue(sound.capturing)
        sound.stop()
        self.assertFalse(sound.capturing)
        capture.close.assert_called_once()

    def test_a_slow_start_does_not_hold_the_caller_and_a_stop_meanwhile_closes_it(self):
        go = threading.Event()
        capture = mock.Mock(channels=2)

        def opener(on_pcm):
            go.wait(2)
            return capture

        sound = win_sound.Sound(lambda pcm: None, opener)
        sound.start()
        thread = sound.opening
        self.assertFalse(sound.capturing)
        sound.stop()
        go.set()
        thread.join(2)
        self.assertFalse(sound.capturing)
        capture.close.assert_called_once()

    def test_the_library_is_missing_off_windows(self):
        with mock.patch.dict(sys.modules, {'pyaudiowpatch': None}):
            with self.assertRaisesRegex(OSError, 'library is missing'):
                win_sound.open_capture(lambda pcm: None)

    def fake_library(self, rate=48000, channels=6):
        library = mock.Mock(paInt16=8, paContinue=0)
        audio = library.PyAudio.return_value
        audio.get_default_wasapi_loopback.return_value = {
            'index': 7, 'name': 'Speakers [Loopback]', 'defaultSampleRate': float(rate), 'maxInputChannels': channels}
        return library, audio

    def test_the_callback_hands_on_stereo_pcm(self):
        library, audio = self.fake_library()
        received = []
        with mock.patch.dict(sys.modules, {'pyaudiowpatch': library}):
            capture = win_sound.open_capture(received.append)
        callback = audio.open.call_args.kwargs['stream_callback']
        self.assertEqual(callback(bytes(6 * 2 * 300), 300, {}, 0), (None, 0))
        self.assertEqual([len(pcm) for pcm in received], [300 * 4])
        capture.close()
        audio.terminate.assert_called_once()

    def test_a_wrong_rate_says_what_to_change_and_lets_go_of_portaudio(self):
        library, audio = self.fake_library(rate=44100)
        with mock.patch.dict(sys.modules, {'pyaudiowpatch': library}):
            with self.assertRaisesRegex(OSError, 'at 44100 Hz'):
                win_sound.open_capture(lambda pcm: None)
        audio.terminate.assert_called_once()

    def test_surround_keeps_the_front_left_and_right(self):
        frames = array('h', [1, 2, 3, 4, 5, 6, -1, -2, -3, -4, -5, -6]).tobytes()
        self.assertEqual(array('h', win_sound.stereo(frames, 6)).tolist(), [1, 2, -1, -2])
        self.assertIs(win_sound.stereo(frames, 2), frames)


class XInputState(ctypes.Structure):
    _fields_ = [('packet', ctypes.c_ulong), ('pad', win_pad.Report)]


class PlugTest(unittest.TestCase):
    def pad(self, plug_results, on_rumble=lambda large, small: None):
        lib = mock.MagicMock()
        lib.vigem_connect.return_value = win_pad.SUCCESS
        lib.vigem_target_x360_register_notification.return_value = win_pad.SUCCESS
        lib.vigem_target_add.side_effect = plug_results
        with mock.patch.object(win_pad, 'load', return_value=lib):
            return win_pad.Pad(on_rumble), lib

    def test_a_plug_in_that_times_out_is_tried_again_on_a_new_connection(self):
        pad, lib = self.pad([0xE0000007, win_pad.SUCCESS])
        self.assertEqual((lib.vigem_target_add.call_count, lib.vigem_disconnect.call_count), (2, 1))
        self.assertTrue(pad.target)

    def test_it_gives_up_after_three_tries(self):
        with self.assertRaisesRegex(OSError, 'plug in the virtual pad'):
            self.pad([0xE0000007] * 3)

    def driver(self, connect_result):
        lib = mock.MagicMock()
        lib.vigem_connect.return_value = connect_result
        patcher = mock.patch.object(win_pad, 'load', return_value=lib)
        patcher.start()
        self.addCleanup(patcher.stop)
        return lib

    def test_check_driver_connects_without_plugging_in_a_pad(self):
        lib = self.driver(win_pad.SUCCESS)
        win_pad.check_driver()
        lib.vigem_connect.assert_called_once()
        lib.vigem_disconnect.assert_called_once()
        lib.vigem_free.assert_called_once()
        lib.vigem_target_add.assert_not_called()

    def test_check_driver_reports_missing_driver(self):
        lib = self.driver(win_pad.BUS_NOT_FOUND)
        with self.assertRaises(win_pad.DriverMissing):
            win_pad.check_driver()
        lib.vigem_disconnect.assert_not_called()
        lib.vigem_target_add.assert_not_called()
        lib.vigem_free.assert_called_once()

    def test_check_driver_raises_on_other_connect_error(self):
        lib = self.driver(0xE0000007)
        with self.assertRaises(OSError) as raised:
            win_pad.check_driver()
        self.assertNotIsInstance(raised.exception, win_pad.DriverMissing)
        lib.vigem_disconnect.assert_not_called()
        lib.vigem_free.assert_called_once()

    def test_notify_logs_assigned_xinput_player_once_and_forwards_rumble(self):
        rumbles = []
        pad, lib = self.pad([win_pad.SUCCESS], lambda large, small: rumbles.append((large, small)))
        notify = lib.vigem_target_x360_register_notification.call_args.args[2]
        with self.assertLogs('musubi', 'INFO') as logs:
            notify(None, None, 10, 20, 2, None)
            notify(None, None, 30, 40, 2, None)
        self.assertEqual(len(logs.output), 1)
        self.assertIn('XInput player 3', logs.output[0])
        self.assertEqual(rumbles, [(10, 20), (30, 40)])


class FirewallTest(unittest.TestCase):
    EXE = r'C:\Users\Ana\Downloads\Musubi-test.exe'

    def netsh(self, returncode=0, stdout=''):
        return mock.patch.object(firewall.subprocess, 'run', return_value=mock.Mock(
            returncode=returncode, stdout=stdout, stderr=''))

    def test_rule_counts_only_when_it_names_this_exe(self):
        with self.netsh(stdout=r'Program: c:\users\ana\downloads\musubi-test.exe') as run:
            self.assertTrue(firewall.rule_ok(self.EXE))
        self.assertIn('status=enabled', run.call_args.args[0])
        with self.netsh(stdout=r'Program: C:\Old\Musubi-test.exe'):
            self.assertFalse(firewall.rule_ok(self.EXE))
        with self.netsh(returncode=1, stdout=self.EXE):
            self.assertFalse(firewall.rule_ok(self.EXE))

    def test_old_rules_for_the_exe_go_before_the_new_one(self):
        with self.netsh() as run:
            self.assertTrue(firewall.open_port(self.EXE))
        calls = [c.args[0][3:] for c in run.call_args_list]
        self.assertEqual(calls[0], ['delete', 'rule', 'name=all', f'program={self.EXE}'])
        self.assertEqual(calls[1], ['delete', 'rule', 'name=Musubi'])
        self.assertEqual(calls[2][:2], ['add', 'rule'])
        for part in ('dir=in', 'action=allow', f'program={self.EXE}', 'protocol=UDP', 'localport=9999', 'profile=private,public'):
            self.assertIn(part, calls[2])


@unittest.skipUnless(sys.platform == 'win32', 'needs Windows')
class RealFirewallTest(unittest.TestCase):
    """Real netsh. The CI runner is admin."""

    def test_the_rule_is_added_and_read_back(self):
        exe = r'C:\musubi-test\fake.exe'
        self.addCleanup(subprocess.run, ['netsh', 'advfirewall', 'firewall', 'delete', 'rule', 'name=Musubi'],
                        capture_output=True)
        self.assertTrue(firewall.open_port(exe))
        self.assertTrue(firewall.rule_ok(exe))
        subprocess.run(['netsh', 'advfirewall', 'firewall', 'set', 'rule', 'name=Musubi', 'new', 'enable=no'],
                       check=True, capture_output=True)
        self.assertFalse(firewall.rule_ok(exe))


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
