#!/usr/bin/env python3
"""The Windows hub over real UDP sockets on this machine, with a fake pad, fake sound and a pretend window."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import paths  # noqa: F401
import core
import hub
from core import HELLO, HELLO_MAGIC, PACKET
from test_win import FakePad, FakeSound


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

    def wait(self, timeout=None):
        return 0


class HubTest(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.config = Path(tempfile.mkdtemp()) / 'config.json'
        self.window = FakeWindow()
        self.pads = []
        self.sounds = []
        self.no_driver = False
        self.phone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.phone.close)

    def start(self, allowed=()):
        core.save_config({'phones': {ip: 'Pixel' for ip in allowed}, 'tray': True, 'sound_enabled': True}, self.config)
        self.hub = hub.Hub(self.make_pad, self.make_sound, self.port, self.config, lambda: self.window,
                           lambda: self.no_driver)
        self.thread = threading.Thread(target=self.hub.run, daemon=True)
        self.thread.start()
        self.addCleanup(self.finish)
        # Sent once the port is bound, so packets from here on arrive.
        return self.next_event(lambda event: event['event'] == 'state')

    def finish(self):
        if not self.window.commands.closed:
            self.window.commands.close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.window.events.close()

    def make_pad(self):
        self.pads.append(FakePad())
        return self.pads[-1]

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
        self.assertEqual(event['axes'], [6, 0, 0, 0, 0, 0, 0, 0])

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


if __name__ == '__main__':
    unittest.main()
