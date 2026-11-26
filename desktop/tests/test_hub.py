#!/usr/bin/env python3
"""The Windows hub over real UDP sockets on this machine, with a fake pad, fake sound and a pretend window."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import paths  # noqa: F401
import core
import hub
import sound as win_sound
from core import HELLO, HELLO_MAGIC, PACKET
from test_win import FakePad, FakeSound, hello as slots_hello


def free_port():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(('0.0.0.0', 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class FakeWindow:
    """Two pipes in place of the window process: the test writes its commands and reads the hub's events."""

    def __init__(self):
        commands_read, commands_write = os.pipe()
        events_read, events_write = os.pipe()
        self.stdout = os.fdopen(commands_read, 'rb')
        self.commands = os.fdopen(commands_write, 'wb')
        self.stdin = os.fdopen(events_write, 'wb')
        self.events = os.fdopen(events_read, 'rb')
        self.killed = False

    def wait(self, timeout=None):
        self.commands.close()  # A process that exits closes its end, which the hub's reader sees.
        return 0

    def kill(self):
        self.killed = True
        self.events.close()


class FakeTray:
    def __init__(self, on_open, on_quit):
        self.on_open = on_open
        self.on_quit = on_quit
        self.status = 'Musubi'
        self.closed = False

    def set_status(self, text):
        self.status = text

    def close(self):
        self.closed = True


class HubTest(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.config = Path(tempfile.mkdtemp()) / 'config.json'
        self.window = None
        self.windows = []
        self.tray = None
        self.pads = []
        self.sounds = []
        self.no_driver = False
        self.phone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.phone.close)

    def start(self, allowed=(), tray=False, tray_enabled=True):
        core.save_config({'phones': {ip: 'Pixel' for ip in allowed}, 'tray': tray_enabled, 'sound_enabled': True},
                         self.config)
        self.hub = hub.Hub(self.make_pad, self.make_sound, self.port, self.config, self.spawn_window,
                           lambda: self.no_driver, ensure_firewall=lambda: None,
                           make_tray=self.make_tray if tray else None)
        self.thread = threading.Thread(target=self.hub.run, daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)
        self.wait_for(lambda: self.windows)
        # Sent once the port is bound, so packets from here on arrive.
        return self.next_event(lambda event: event['event'] == 'state')

    def finish(self):
        for window in self.windows:
            window.commands.close()
        if self.tray:
            self.tray.on_quit()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        for window in self.windows:
            window.events.close()

    def spawn_window(self):
        self.window = FakeWindow()
        self.windows.append(self.window)
        return self.window

    def make_tray(self, on_open, on_quit):
        self.tray = FakeTray(on_open, on_quit)
        return self.tray

    def wait_for(self, check, timeout=3):
        end = time.monotonic() + timeout
        while not check():
            if time.monotonic() > end:
                self.fail('timed out')
            time.sleep(0.01)

    def close_window(self):
        self.window.commands.close()
        self.wait_for(lambda: self.hub.window is None)

    def make_pad(self, slot=0):
        pad = FakePad()
        pad.slot = slot
        self.pads.append(pad)
        return pad

    def make_sound(self, on_pcm):
        self.sounds.append(FakeSound(on_pcm))
        return self.sounds[-1]

    def command(self, **command):
        self.window.commands.write(json.dumps(command).encode() + b'\n')
        self.window.commands.flush()

    def hello(self, features=core.FEATURE_AUDIO):
        self.phone.sendto(HELLO.pack(HELLO_MAGIC, 1, 9) + b'Pixel\0Xbox pad\0' + bytes([features]),
                          ('127.0.0.1', self.port))

    def next_event(self, test, timeout=3):
        """The first event the test accepts, skipping the rest."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            event = json.loads(self.window.events.readline())
            if test(event):
                return event
        self.fail('no such event')

    def status(self, test=lambda event: True):
        return self.next_event(lambda event: event['event'] == 'status' and test(event))

    def test_the_window_gets_state_then_a_status_with_sound_and_cable(self):
        self.assertEqual(self.start(),
                         {'event': 'state', 'error': None, 'stopped': False, 'ignored': [], 'page': 'overview'})
        status = self.status(lambda event: 'cable_error' in event)
        self.assertEqual((status['cable_error'], status['sound_enabled']), (None, True))

    def test_allow_lets_a_waiting_phone_in(self):
        self.start()
        self.hello()
        self.status(lambda event: event['pending'])
        self.command(allow=['127.0.0.1'])
        self.status(lambda event: not event['pending'])
        self.hello()
        self.assertEqual(self.status(lambda event: event['phone'])['phone']['ip'], '127.0.0.1')

    def test_input_flows_only_while_watching(self):
        self.start(['127.0.0.1'])
        self.hello()
        self.status(lambda event: event['pad'])
        self.phone.sendto(PACKET.pack(1, 5, 0, 0, 0, 0, 0, 0, 0), ('127.0.0.1', self.port))
        self.command(watch=True)
        self.phone.sendto(PACKET.pack(2, 6, 0, 0, 0, 0, 0, 0, 0), ('127.0.0.1', self.port))
        event = self.next_event(lambda event: event['event'] == 'input' and event['buttons'] == 2)
        self.assertEqual((event['slot'], event['axes']), (0, [6, 0, 0, 0, 0, 0, 0, 0]))

    def test_a_second_controller_reaches_the_window_with_its_slot(self):
        self.start(['127.0.0.1'])
        self.command(watch=True)
        self.phone.sendto(slots_hello(slots_mask=0b0011, extra=(b'Pad 2',)), ('127.0.0.1', self.port))
        self.status(lambda event: event['pads'] == [True, True, False, False])
        self.phone.sendto(core.slotted_packet(1, PACKET.pack(2, 6, 0, 0, 0, 0, 0, 0, 0)), ('127.0.0.1', self.port))
        event = self.next_event(lambda event: event['event'] == 'input' and event['buttons'] == 2)
        self.assertEqual((event['slot'], event['axes']), (1, [6, 0, 0, 0, 0, 0, 0, 0]))
        self.hub.receiver.on_rumble(255, 0, 1)
        event = self.next_event(lambda event: event['event'] == 'rumble')
        self.assertEqual((event['slot'], event['strong'], event['weak']), (1, 1.0, 0.0))

    def test_input_is_not_sent_without_watch(self):
        self.start(['127.0.0.1'])
        self.hello()
        self.status(lambda event: event['pad'])
        self.phone.sendto(PACKET.pack(1, 5, 0, 0, 0, 0, 0, 0, 0), ('127.0.0.1', self.port))
        seen = []
        self.status(lambda event: seen.append(event) or len(seen) > 2)
        self.assertNotIn('input', [event['event'] for event in seen])
        self.assertIn((1, [5, 0, 0, 0, 0, 0, 0, 0]), self.pads[0].states)

    def test_sound_off_stops_the_sound_and_shows_in_status(self):
        self.start(['127.0.0.1'])
        self.hello()
        self.status(lambda event: event['phone'])
        self.assertTrue(self.sounds[0].capturing)
        self.command(sound=False)
        self.status(lambda event: event['sound_enabled'] is False)
        self.hello()
        self.status()
        self.assertFalse(self.sounds[0].capturing)

    def test_sound_error_reaches_the_window_and_clears_when_sound_stops(self):
        def failing_open(_on_pcm):
            raise OSError('PC sound needs 48000 Hz')

        def make_failing_sound(on_pcm):
            snd = win_sound.Sound(on_pcm, open_capture=failing_open)
            self.sounds.append(snd)
            return snd

        with mock.patch.object(self, 'make_sound', side_effect=make_failing_sound), \
                mock.patch.object(win_sound.log, 'error') as log_error:
            self.start(['127.0.0.1'])
            self.hello(features=core.FEATURE_AUDIO)
            failed = self.status(lambda event: event.get('sound_error'))
            self.assertEqual(failed['sound_error'], 'PC sound needs 48000 Hz')
            self.wait_for(lambda: log_error.called)
            self.command(sound=False)
            cleared = self.status(lambda event: event['sound_enabled'] is False)
            self.assertIsNone(cleared['sound_error'])

    def test_stop_frees_the_port_and_start_takes_it_back(self):
        self.start()
        self.status()
        self.command(stop=True)
        self.assertTrue(self.next_event(lambda event: event['event'] == 'state')['stopped'])
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(('0.0.0.0', self.port))
        probe.close()
        self.command(start=True)
        self.assertFalse(self.next_event(lambda event: event['event'] == 'state')['stopped'])
        self.hello()
        self.status(lambda event: event['pending'])

    def test_a_taken_port_gives_the_error_state(self):
        taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        taken.bind(('0.0.0.0', self.port))
        self.assertEqual(self.start()['error'],
                         f'Another program is using UDP port {self.port}. Is Musubi already running?')
        taken.close()
        self.command(start=True)
        self.assertIsNone(self.next_event(lambda event: event['event'] == 'state')['error'])

    def test_a_missing_driver_is_said_until_check_again_finds_it(self):
        self.no_driver = True
        self.start(['127.0.0.1'])
        status = self.status(lambda event: event.get('driver_missing'))
        self.assertEqual(status['pad_error'], hub.NO_DRIVER)
        self.no_driver = False
        self.command(driver=True)
        status = self.status(lambda event: event.get('driver_missing') is False)
        self.assertIsNone(status['pad_error'])

    def test_the_loop_ends_when_the_window_does(self):
        self.start()
        self.status()
        self.window.commands.close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertIsNone(self.hub.receiver)

    def test_with_the_tray_a_closed_window_leaves_the_hub_running_until_quit(self):
        self.start(['127.0.0.1'], tray=True)
        self.command(watch=True)
        self.wait_for(lambda: self.hub.receiver.watching)
        self.close_window()
        self.wait_for(lambda: not self.hub.receiver.watching)
        self.hello()
        self.phone.sendto(PACKET.pack(1, 5, 0, 0, 0, 0, 0, 0, 0), ('127.0.0.1', self.port))
        self.wait_for(lambda: self.pads and (1, [5, 0, 0, 0, 0, 0, 0, 0]) in self.pads[0].states)
        self.assertTrue(self.thread.is_alive())
        self.tray.on_open()
        self.wait_for(lambda: len(self.windows) == 2)
        self.assertIsNone(self.next_event(lambda event: event['event'] == 'state')['error'])
        self.status(lambda event: event['phone'])
        self.tray.on_open()
        self.next_event(lambda event: event['event'] == 'present')
        self.assertEqual(len(self.windows), 2)
        self.tray.on_quit()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.tray.closed)
        self.assertIsNone(self.hub.receiver)

    def test_with_the_tray_setting_off_closing_the_window_quits(self):
        self.start(tray=True, tray_enabled=False)
        self.window.commands.close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.tray.closed)

    def test_a_new_phone_reopens_the_window_once(self):
        self.start(tray=True)
        self.close_window()
        self.hello()
        self.wait_for(lambda: len(self.windows) == 2)
        self.status(lambda event: event['pending'])
        self.wait_for(lambda: self.tray.status == 'Musubi: a new phone is asking')
        self.close_window()
        self.hello()
        time.sleep(0.3)
        self.assertEqual(len(self.windows), 2)

    def test_the_tray_text_follows_the_phone_and_stop(self):
        self.start(['127.0.0.1'], tray=True)
        self.wait_for(lambda: self.tray.status == 'Musubi: waiting for a phone')
        core.save_config({'phones': {'127.0.0.1': 'Couch'}, 'tray': True, 'sound_enabled': True}, self.config)
        self.command(reload=True)
        self.hello()
        self.wait_for(lambda: self.tray.status == 'Musubi: Couch')
        self.command(stop=True)
        self.wait_for(lambda: self.tray.status == 'Musubi: stopped')

    def test_quit_does_not_hang_on_a_window_that_stopped_reading(self):
        self.start(tray=True)
        self.hub.send_window({'event': 'filler', 'data': 'x' * 2 ** 20})
        time.sleep(0.1)
        self.tray.on_quit()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.window.killed)


class DriverCheckTest(unittest.TestCase):
    def test_the_driver_check_never_plugs_in_a_pad(self):
        with mock.patch.object(hub.win_pad, 'Pad') as pad, mock.patch.object(hub.win_pad, 'check_driver') as check:
            self.assertFalse(hub.driver_missing())
            check.side_effect = hub.win_pad.DriverMissing('not installed')
            with self.assertLogs('musubi', 'WARNING'):
                self.assertTrue(hub.driver_missing())
            check.side_effect = OSError('timed out')
            with self.assertLogs('musubi', 'WARNING'):
                self.assertFalse(hub.driver_missing())
        self.assertEqual(check.call_count, 3)
        pad.assert_not_called()


class WindowExitTest(unittest.TestCase):
    def test_window_exits_immediately_while_stdin_stays_open(self):
        try:
            import gi
            gi.require_version('Gtk', '4.0')
        except (ImportError, ValueError):
            self.skipTest('needs GTK 4')
        local_app_data = Path(tempfile.mkdtemp())
        code = (
            f"import sys, threading, time\n"
            f"sys.path.insert(0, {str(paths.DESKTOP / 'windows')!r})\n"
            "import gtk_window\n"
            "def fake_run(self, _):\n"
            "    threading.Thread(target=self.read_hub, daemon=True).start()\n"
            "    time.sleep(0.05)\n"
            "    return 0\n"
            "gtk_window.App.run = fake_run\n"
            "gtk_window.main()\n"
        )
        env = {**os.environ, 'LOCALAPPDATA': str(local_app_data)}
        proc = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        try:
            self.assertEqual(proc.wait(timeout=2), 0)
            self.assertEqual(proc.stdout.read(), b'')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stdin.close()
            proc.stdout.close()
        self.assertIn('closed', (local_app_data / 'musubi' / 'musubi-window.log').read_text(encoding='utf-8'))


class WindowsWindowUITest(unittest.TestCase):
    def test_unplugged_mic_and_windows_copy_and_drawing(self):
        try:
            import cairo  # noqa: F401
            import gi
            gi.require_version('Gtk', '4.0')
            gi.require_version('PangoCairo', '1.0')
            from gi.repository import Gtk
            if not Gtk.init_check():
                self.skipTest('needs a display')
        except (ImportError, ValueError):
            self.skipTest('needs GTK 4 and PyCairo')

        temp_dir = Path(tempfile.mkdtemp())
        code = f"""
import sys
sys.path.insert(0, {str(paths.DESKTOP / 'windows')!r})
import cairo
from gi.repository import Gtk
import gtk_window

def all_labels(root):
    out = []
    if isinstance(root, Gtk.Label):
        out.append(root.get_label())
    child = root.get_first_child()
    while child is not None:
        out.extend(all_labels(child))
        child = child.get_next_sibling()
    return out

def count_switches(root):
    n = 1 if isinstance(root, Gtk.Switch) else 0
    child = root.get_first_child()
    while child is not None:
        n += count_switches(child)
        child = child.get_next_sibling()
    return n

app = gtk_window.App()
app.register()
app.build_window()
app.local_ip = '192.168.1.20'

# 1. Waiting state: custom sidebar nodes, no USB cable copy, Ignored phone still shows.
app.on_event({{
    'event': 'status',
    'phone': None,
    'pad': False,
    'pads': [False, False, False, False],
    'pad_error': None,
    'pending': [{{'ip': '192.168.1.99', 'name': 'Ignored Pixel'}}],
    'sound_enabled': True,
    'cable_error': None,
    'driver_missing': False,
}})
app.ignored = {{'192.168.1.99'}}
app.refresh()

assert isinstance(app.sound_node, gtk_window.SoundCordNode)
assert isinstance(app.mic_node, gtk_window.UnpluggedCordNode)
assert app.mic_caption.get_label() == 'Coming to Windows'
waiting_labels = all_labels(app.panel)
assert 'Phone and PC need the same Wi-Fi network.' in waiting_labels, waiting_labels
assert not any('USB cable' in s or 'File transfer' in s for s in waiting_labels), waiting_labels
assert 'Ignored phone' in waiting_labels, waiting_labels

# 2. Connected with mic=1 and audio=False: cord stays unlit.
phone = {{
    'ip': '192.168.1.34',
    'name': 'Galaxy A12',
    'controller': None,
    'controllers': [None, None, None, None],
    'controller_known': True,
    'rtt': 4,
    'audio': False,
    'delay': None,
    'mic': 1,
}}
app.on_event({{
    'event': 'status',
    'phone': phone,
    'pad': False,
    'pads': [False, False, False, False],
    'pad_error': None,
    'pending': [],
    'sound_enabled': True,
    'cable_error': None,
    'driver_missing': False,
}})
assert app.source_cord.line_active is False
assert app.ctrl_node.line_above is False
assert app.ctrl_node.line_below is False
assert app.sound_node.line_above is False

# 3. Connected with audio=True: Unplugged Mic & Windows Sound copy.
phone['audio'] = True
app.on_event({{
    'event': 'status',
    'phone': phone,
    'pad': False,
    'pads': [False, False, False, False],
    'pad_error': None,
    'pending': [],
    'sound_enabled': True,
    'cable_error': None,
    'driver_missing': False,
}})
assert isinstance(app.overview_mic_area, gtk_window.UnpluggedMicArea)
assert isinstance(app.mic_area, gtk_window.UnpluggedMicArea)
assert app.mic_switch is None
assert app.mic_mute is None
assert app.overview_mic_value.get_label() == 'Unplugged on Windows'
assert app.overview_mic_detail.get_label() == 'Needs a virtual mic driver first'
assert app.mic_status.get_label() == 'Unplugged'
assert app.mic_detail.get_label() == 'Coming to Windows'

assert app.sound_value.get_label() == 'Playing'
assert app.sound_detail.get_label() == "From this PC's default output"
sound_labels = all_labels(app.sound_panel)
assert "When off, Musubi stops listening to this PC's sound" in sound_labels, sound_labels
assert 'Musubi copies whatever plays on your default output. Change it in the Windows sound settings.' in sound_labels, sound_labels
assert not any('Musubi" output' in s for s in sound_labels), sound_labels

app.on_event({{'event': 'mic_level', 'level': 0.9}})
assert app.mic_area.left == 0.0

# 3b. Sound error shows Can't play alert in Sidebar, Overview, and Sound panel, then recovers cleanly.
phone = {{**phone, 'audio': True, 'delay': 80}}
app.on_event({{
    'event': 'status',
    'phone': phone,
    'pad': False,
    'pads': [False, False, False, False],
    'pad_error': None,
    'pending': [],
    'sound_enabled': True,
    'sound_error': 'PC sound needs 48000 Hz',
    'cable_error': None,
    'driver_missing': False,
}})
assert app.sound_caption.get_label() == "Can't play"
assert app.sound_caption.has_css_class('alert')
assert app.source_cord.line_active is False
assert app.sound_node.state == 'idle'
assert app.sound_node.line_above is False
assert app.overview_sound_value.get_label() == "Can't play"
assert app.overview_sound_value.has_css_class('alert')
assert app.overview_sound_detail.get_label() == 'PC sound needs 48000 Hz'
assert app.overview_headphones.mode == 'off'
assert app.overview_delay.get_visible() is False
assert app.sound_value.get_label() == "Can't play"
assert app.sound_value.has_css_class('alert')
assert app.sound_detail.get_label() == 'PC sound needs 48000 Hz'
assert app.sound_headphones.mode == 'off'
assert app.sound_delay.get_label() == ''

app.on_event({{
    'event': 'status',
    'phone': phone,
    'pad': False,
    'pads': [False, False, False, False],
    'pad_error': None,
    'pending': [],
    'sound_enabled': True,
    'sound_error': None,
    'cable_error': None,
    'driver_missing': False,
}})
assert app.sound_caption.get_label() == 'Playing'
assert not app.sound_caption.has_css_class('alert')
assert app.sound_node.state == 'live'
assert app.overview_sound_value.get_label() == 'Playing'
assert not app.overview_sound_value.has_css_class('alert')
assert app.overview_sound_detail.get_label() == "From this PC's default output"
assert app.overview_headphones.mode == 'on'
assert app.sound_value.get_label() == 'Playing'
assert not app.sound_value.has_css_class('alert')
assert app.sound_detail.get_label() == "From this PC's default output"
assert app.sound_headphones.mode == 'on'
assert app.sound_delay.get_label() == 'Delay ≈ 80 ms'

# 4. Settings: Start with my PC has no switch; tray caption updates live; Open logs present.
app.refresh_settings()
settings_labels = all_labels(app.settings_panel)
assert 'Start with my PC' in settings_labels, settings_labels
assert 'Coming to Windows' in settings_labels, settings_labels
assert 'Open logs' in settings_labels, settings_labels
assert count_switches(app.settings_panel) == 1, count_switches(app.settings_panel)
assert app.tray_caption.get_label() == 'Closing the window keeps Musubi on'
app.on_tray_setting(False)
assert app.tray_caption.get_label() == 'Closing the window stops Musubi'

# 5. Cairo drawing smoke check on all custom widgets.
for widget, w, h in (
    (app.sound_node, 24, 48),
    (app.mic_node, 24, 48),
    (app.overview_mic_area, 80, 72),
    (app.mic_area, 120, 108),
):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    widget.draw(widget, cr, w, h)

app.window.destroy()
"""
        env = {**os.environ, 'LOCALAPPDATA': str(temp_dir), 'XDG_CONFIG_HOME': str(temp_dir)}
        proc = subprocess.run([sys.executable, '-c', code], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              env=env, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        log_text = (temp_dir / 'musubi' / 'musubi-window.log').read_text(encoding='utf-8')
        for bad in ('Traceback', 'ERROR', 'CRITICAL'):
            self.assertNotIn(bad, log_text)


if __name__ == '__main__':
    unittest.main()
