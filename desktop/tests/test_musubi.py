#!/usr/bin/env python3
import fcntl
import io
import json
import os
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from evdev import InputEvent, ecodes as e, ff

import paths  # noqa: F401
import audio
import core
import musubi
from musubi import HELLO, HELLO_MAGIC, INPUT_MAGIC, PACKET, Bridge, decode, decode_hello, decode_input, slotted_packet


class DecodeTest(unittest.TestCase):
    def test_rejects_wrong_size(self):
        self.assertIsNone(decode(b''))
        self.assertIsNone(decode(bytes(PACKET.size + 1)))

    def test_reads_fields_in_packet_order(self):
        buttons, axes = decode(PACKET.pack(0b101, 1, -2, 3, -4, 200, 100, -1, 1))
        self.assertEqual(buttons, 0b101)
        self.assertEqual(axes, [1, -2, 3, -4, 200, 100, -1, 1])

    def test_clamps_values_a_real_pad_cannot_send(self):
        _, axes = decode(PACKET.pack(0, -32768, 0, 0, 0, 999, -5, 7, -7))
        self.assertEqual(axes, [-32767, 0, 0, 0, 255, 0, 1, -1])

    def test_neutral_releases_everything(self):
        buttons, axes = decode(musubi.NEUTRAL)
        self.assertEqual(buttons, 0)
        self.assertEqual(axes, [0] * 8)

    def test_decode_input_accepts_legacy_and_slotted_packets(self):
        raw = PACKET.pack(0b11, 10, -20, 0, 0, 128, 0, 1, -1)
        self.assertEqual(decode_input(raw), (0, (0b11, [10, -20, 0, 0, 128, 0, 1, -1])))
        for slot in range(1, musubi.MAX_PLAYERS):
            pkt = slotted_packet(slot, raw)
            self.assertEqual(pkt[:4], INPUT_MAGIC)
            self.assertEqual(decode_input(pkt), (slot, (0b11, [10, -20, 0, 0, 128, 0, 1, -1])))
        self.assertIsNone(decode_input(INPUT_MAGIC + bytes([musubi.MAX_PLAYERS]) + raw))
        self.assertIsNone(decode_input(b'BAD!' + bytes([1]) + raw))
        self.assertIsNone(decode_input(INPUT_MAGIC + bytes([1]) + raw[:-1]))


def hello(phone=b'LM-X430', controller=b'Xbox Wireless Controller', connected=True, rtt=9, features=None, delay=None,
          mic=None, slots_mask=None, extra=()):
    """Without features, this is the hello of a 0.5.x app."""
    packet = HELLO.pack(HELLO_MAGIC, connected, rtt) + phone + b'\0' + controller
    if features is None:
        return packet
    out = (packet + b'\0' + bytes([features]) + (b'' if delay is None else delay.to_bytes(2, 'big'))
           + (b'' if mic is None else bytes([mic])))
    if slots_mask is not None:
        out += bytes([slots_mask]) + b'\0'.join(extra)
    return out


class HelloTest(unittest.TestCase):
    def test_reads_every_field(self):
        self.assertEqual(decode_hello(hello()), {
            'name': 'LM-X430', 'controller': 'Xbox Wireless Controller',
            'controller_connected': True, 'controllers': ['Xbox Wireless Controller', None, None, None],
            'rtt': 9, 'audio': False, 'rumble': False, 'stable': False, 'delay': None, 'mic': 0,
        })

    def test_reads_multi_pad_slots_and_gaps(self):
        parsed = decode_hello(hello(features=0, delay=0, mic=0, slots_mask=0b1111,
                                    extra=(b'DualShock', b'Pad 3', b'Pad 4')))
        self.assertEqual(parsed['controllers'], ['Xbox Wireless Controller', 'DualShock', 'Pad 3', 'Pad 4'])

        gapped = decode_hello(hello(controller=b'', connected=False, features=0, delay=0, mic=0,
                                    slots_mask=0b1010, extra=(b'Pad 2', b'', b'Pad 4')))
        self.assertTrue(gapped['controller_connected'])
        self.assertEqual(gapped['controllers'], [None, 'Pad 2', None, 'Pad 4'])

        cleaned = decode_hello(hello(features=0, delay=0, mic=0, slots_mask=0b0010,
                                     extra=(b'\x1b[31mbad' + b'x' * 100,)))
        self.assertEqual(len(cleaned['controllers'][1]), musubi.MAX_NAME)
        self.assertTrue(cleaned['controllers'][1].startswith('[31mbad'))

        unnamed = decode_hello(hello(controller=b'', connected=False, features=0, delay=0, mic=0,
                                     slots_mask=0b0010, extra=(b'',)))
        self.assertEqual(unnamed['controllers'], [None, '', None, None])

    def test_reads_the_features_after_the_names(self):
        parsed = decode_hello(hello(features=musubi.FEATURE_AUDIO))
        self.assertEqual(parsed['controller'], 'Xbox Wireless Controller')
        self.assertTrue(parsed['audio'])
        self.assertFalse(decode_hello(hello(controller=b'', features=0))['audio'])

    def test_reads_the_stable_bit(self):
        self.assertTrue(decode_hello(hello(features=musubi.FEATURE_AUDIO | musubi.FEATURE_STABLE))['stable'])
        self.assertFalse(decode_hello(hello(features=musubi.FEATURE_AUDIO))['stable'])
        self.assertLess(musubi.FEATURE_STABLE, 0x20)

    def test_reads_the_delay_when_present(self):
        parsed = decode_hello(hello(features=musubi.FEATURE_AUDIO, delay=130))
        self.assertEqual((parsed['controller'], parsed['audio'], parsed['delay']),
                         ('Xbox Wireless Controller', True, 130))
        self.assertIsNone(decode_hello(hello(features=musubi.FEATURE_AUDIO, delay=0))['delay'])
        self.assertIsNone(decode_hello(hello(features=musubi.FEATURE_AUDIO))['delay'])
        self.assertIsNone(decode_hello(hello())['delay'])

    def test_reads_the_mic_state_and_clamps_junk(self):
        for byte, state in [(0, 0), (1, 1), (2, 2), (3, 3), (4, 0), (255, 0)]:
            self.assertEqual(decode_hello(hello(features=0, delay=0, mic=byte))['mic'], state)
        self.assertEqual(decode_hello(hello(features=0, delay=0))['mic'], 0)

    def test_older_desktops_dont_show_the_features_in_the_controller_name(self):
        # 0.5.x took everything after the first \0 as the controller name.
        rest = hello(features=musubi.FEATURE_AUDIO)[HELLO.size:].partition(b'\0')[2]
        self.assertEqual(musubi.clean_name(rest), 'Xbox Wireless Controller')

    def test_rejects_other_packets(self):
        self.assertIsNone(decode_hello(musubi.NEUTRAL))
        self.assertIsNone(decode_hello(bytes(musubi.PING_SIZE)))
        self.assertIsNone(decode_hello(HELLO_MAGIC))

    def test_names_cant_carry_control_characters_or_run_on(self):
        parsed = decode_hello(hello(phone=b'bad\x1b[31m\n\xff', controller=b'x' * 500))
        self.assertEqual(parsed['name'], 'bad[31m�')
        self.assertEqual(len(parsed['controller']), musubi.MAX_NAME)


class FakeSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class FailingSock:
    def sendto(self, data, addr):
        raise OSError('network is unreachable')


class FakeUi:
    def __init__(self, slot=0):
        self.slot = slot
        self.syncs = 0
        self.closed = False
        self.states = []

    def write(self, *_):
        pass

    def apply(self, state):
        self.states.append(state)
        self.syn()

    def syn(self):
        self.syncs += 1

    def close(self):
        self.closed = True


class FakeFfUi(FakeUi):
    """Stands in for the uinput fd: queued events, and the effects behind each upload request."""

    def __init__(self, slot=0):
        super().__init__(slot)
        self.events = []
        self.uploads = {}
        self.erases = {}
        self.answered = []

    def read(self):
        if not self.events:
            raise BlockingIOError
        events, self.events = self.events, []
        return iter(events)

    def queue(self, kind, code, value):
        self.events.append(InputEvent(0, 0, kind, code, value))

    def upload(self, request, effect_id, strong, weak, length):
        upload = ff.UInputUpload()
        upload.request_id = request
        upload.effect.type = e.FF_RUMBLE
        upload.effect.id = effect_id
        upload.effect.ff_replay.length = length
        upload.effect.u.ff_rumble_effect.strong_magnitude = strong
        upload.effect.u.ff_rumble_effect.weak_magnitude = weak
        self.uploads[request] = upload
        self.queue(e.EV_UINPUT, e.UI_FF_UPLOAD, request)

    def erase(self, request, effect_id):
        erase = ff.UInputErase()
        erase.request_id = request
        erase.effect_id = effect_id
        self.erases[request] = erase
        self.queue(e.EV_UINPUT, e.UI_FF_ERASE, request)

    def begin_upload(self, request):
        return self.uploads.pop(request)

    def end_upload(self, upload):
        self.answered.append(('upload', upload.request_id, upload.retval))

    def begin_erase(self, request):
        return self.erases.pop(request)

    def end_erase(self, erase):
        self.answered.append(('erase', erase.request_id, erase.retval))


class BridgeTest(unittest.TestCase):
    PHONE = ('192.168.1.10', 5000)
    OTHER = ('192.168.1.99', 5000)
    PRESSED = PACKET.pack(1, *[0] * 8)

    def setUp(self):
        self.sock = FakeSock()
        self.now = 100.0
        self.pads = []
        self.status_calls = 0
        self.usb_sent = []
        self.bridge = Bridge(self.sock, [self.PHONE[0]], self.on_status,
                             make_pad=self.make_pad, clock=lambda: self.now, send_usb=self.usb_sent.append)

    def on_status(self):
        self.status_calls += 1

    def make_pad(self, slot=0):
        self.pads.append(FakeUi(slot))
        return self.pads[-1]

    def test_echoes_pings_from_allowed_phones(self):
        ping = bytes(range(musubi.PING_SIZE))
        self.bridge.on_datagram(ping, self.PHONE)
        self.assertEqual(self.sock.sent, [(ping, self.PHONE)])
        self.assertEqual(self.pads, [])

    def test_unknown_phones_get_no_echo_and_no_pad_but_ask_for_approval(self):
        self.bridge.on_datagram(hello(phone=b'SM-A125M'), self.OTHER)
        self.bridge.on_datagram(bytes(musubi.PING_SIZE), self.OTHER)
        self.bridge.on_datagram(self.PRESSED, self.OTHER)
        self.assertEqual(self.sock.sent, [])
        self.assertEqual(self.pads, [])
        self.assertEqual(self.bridge.status()['pending'], [{'ip': self.OTHER[0], 'name': 'SM-A125M'}])

    def test_mic_packets_go_to_on_mic_and_leave_the_pad_alone(self):
        got = []
        bridge = Bridge(self.sock, [self.PHONE[0]], self.on_status, make_pad=self.make_pad,
                        clock=lambda: self.now, on_mic=lambda seq, pcm: got.append((seq, pcm)))
        inputs = []
        bridge.on_input = inputs.append
        bridge.on_datagram(b'PBMC\x01\x02' + b'\x07' * 480, self.PHONE)
        bridge.on_datagram(b'PBMC\x00', self.PHONE)
        bridge.on_datagram(b'PBMC\x00\x09' + b'\x07' * 480, self.OTHER)
        self.assertEqual(got, [(0x0102, b'\x07' * 480)])
        self.assertEqual((self.pads, inputs, bridge.pad), ([], [], None))

    def test_the_mic_switch_message_is_magic_and_one_byte(self):
        self.assertEqual([musubi.mic_set_message(state) for state in (0, 1, 2)],
                         [b'PBMS\x00', b'PBMS\x01', b'PBMS\x02'])

    def test_the_mic_is_unknown_until_the_first_hello(self):
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.assertIsNone(self.bridge.status()['phone']['mic'])
        self.bridge.on_datagram(hello(features=0, delay=0, mic=2), self.PHONE)
        self.assertEqual(self.bridge.status()['phone']['mic'], 2)

    def test_set_mic_goes_to_the_phone_on_its_link_and_shows_at_once(self):
        self.bridge.set_mic(1)
        self.bridge.on_datagram(hello(features=0, delay=0, mic=0), self.PHONE)
        moved = (self.PHONE[0], 6000)
        self.bridge.on_datagram(self.PRESSED, moved)
        calls = self.status_calls
        self.bridge.set_mic(1)
        self.assertEqual(self.sock.sent, [(b'PBMS\x01', moved)])
        self.assertEqual((self.bridge.status()['phone']['mic'], self.status_calls), (1, calls + 1))
        self.bridge.on_datagram(hello(features=0, delay=0, mic=0), (musubi.USB, 0))
        self.usb_sent.clear()
        self.bridge.set_mic(2)
        self.assertEqual(self.usb_sent, [b'PBMS\x02'])

    def test_a_paused_mic_stays_paused_until_it_is_turned_off(self):
        self.bridge.on_datagram(hello(features=0, delay=0, mic=3), self.PHONE)
        self.bridge.set_mic(2)
        self.assertEqual(self.bridge.status()['phone']['mic'], 3)
        self.bridge.set_mic(0)
        self.assertEqual(self.bridge.status()['phone']['mic'], 0)

    def test_junk_from_strangers_isnt_a_phone(self):
        self.bridge.on_datagram(b'junk!', self.OTHER)
        self.assertEqual(self.bridge.status()['pending'], [])

    def test_junk_from_an_allowed_ip_doesnt_make_it_active(self):
        self.bridge.on_datagram(bytes(58), self.PHONE)
        self.assertIsNone(self.bridge.active)
        self.assertEqual(self.pads, [])

    def test_strangers_cant_grow_the_pending_list_forever(self):
        for i in range(musubi.MAX_PENDING + 5):
            self.bridge.on_datagram(bytes(musubi.PING_SIZE), (f'10.0.0.{i}', 1))
        self.assertEqual(len(self.bridge.status()['pending']), musubi.MAX_PENDING)

    def test_a_phone_that_left_stops_asking(self):
        self.bridge.on_datagram(hello(), self.OTHER)
        self.now += musubi.PHONE_TIMEOUT + 1
        self.bridge.on_tick()
        self.assertEqual(self.bridge.status()['pending'], [])

    def test_allowing_a_pending_phone_lets_it_in(self):
        self.bridge.on_datagram(hello(), self.OTHER)
        self.bridge.set_allowed([self.OTHER[0]])
        self.assertEqual(self.bridge.status()['pending'], [])
        self.bridge.on_datagram(hello(), self.OTHER)
        self.assertEqual(self.bridge.status()['phone']['ip'], self.OTHER[0])
        self.assertEqual(len(self.pads), 1)

    def test_removing_the_active_phone_closes_its_pad(self):
        self.bridge.on_datagram(hello(), self.PHONE)
        self.bridge.set_allowed([])
        self.assertIsNone(self.bridge.status()['phone'])
        self.assertTrue(self.pads[0].closed)

    def test_pad_exists_only_while_a_controller_is_connected(self):
        self.bridge.on_datagram(hello(connected=False), self.PHONE)
        self.assertEqual(self.pads, [])
        self.bridge.on_datagram(hello(), self.PHONE)
        self.assertEqual(len(self.pads), 1)
        self.bridge.on_datagram(hello(connected=False), self.PHONE)
        self.assertTrue(self.pads[0].closed)
        self.assertFalse(self.bridge.status()['pad'])

    def test_new_app_never_flashes_a_pad_before_its_hello(self):
        self.bridge.on_datagram(musubi.NEUTRAL, self.PHONE)
        self.bridge.on_datagram(hello(connected=False), self.PHONE)
        self.now += musubi.HELLO_WAIT + 1
        self.bridge.on_tick()
        self.assertEqual(self.pads, [])

    def test_old_app_without_hello_still_gets_a_pad(self):
        self.bridge.on_datagram(musubi.NEUTRAL, self.PHONE)
        self.assertEqual(self.pads, [])
        self.now += musubi.HELLO_WAIT
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.assertEqual(self.pads[0].syncs, 1)

    def test_applies_a_new_state_once(self):
        self.bridge.on_datagram(hello(), self.PHONE)
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.assertEqual(self.pads[0].syncs, 1)

    def test_malformed_state_changes_nothing(self):
        self.bridge.on_datagram(hello(), self.PHONE)
        self.bridge.on_datagram(b'junk!', self.PHONE)
        self.assertEqual(self.pads[0].syncs, 0)

    def test_silence_releases_buttons_then_drops_the_phone(self):
        self.bridge.on_datagram(hello(), self.PHONE)
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.now += musubi.TIMEOUT + 0.1
        self.bridge.on_tick()
        self.assertEqual(self.pads[0].syncs, 2)
        self.assertFalse(self.pads[0].closed)
        self.now += musubi.PHONE_TIMEOUT
        self.bridge.on_tick()
        self.assertTrue(self.pads[0].closed)
        self.assertIsNone(self.bridge.status()['phone'])

    def test_a_second_allowed_phone_waits_its_turn(self):
        self.bridge.set_allowed([self.PHONE[0], self.OTHER[0]])
        self.bridge.on_datagram(hello(), self.PHONE)
        self.bridge.on_datagram(self.PRESSED, self.OTHER)
        self.assertEqual(self.bridge.status()['phone']['ip'], self.PHONE[0])
        self.assertEqual(self.pads[0].syncs, 0)

    def test_a_cable_needs_no_approval_and_takes_over_from_wifi_without_dropping_the_pad(self):
        usb = (musubi.USB, 0)
        self.bridge.on_datagram(hello(), self.PHONE)
        self.bridge.on_datagram(self.PRESSED, usb)
        self.assertEqual(self.bridge.status()['phone']['ip'], musubi.USB)
        self.assertEqual(len(self.pads), 1)
        self.assertFalse(self.pads[0].closed)
        self.assertEqual(self.pads[0].syncs, 1)
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.assertEqual(self.bridge.status()['phone']['ip'], musubi.USB)
        self.bridge.set_allowed([])
        self.assertEqual(self.bridge.status()['phone']['ip'], musubi.USB)

    def test_wifi_takes_over_from_an_unplugged_cable_without_dropping_the_pad(self):
        usb = (musubi.USB, 0)
        self.bridge.on_datagram(hello(), usb)
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.assertEqual(self.bridge.status()['phone']['ip'], musubi.USB)
        self.now += musubi.TIMEOUT + 0.1
        self.bridge.on_datagram(self.PRESSED, self.OTHER)
        self.assertEqual(self.bridge.status()['phone']['ip'], musubi.USB)
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.assertEqual(self.bridge.status()['phone']['ip'], self.PHONE[0])
        self.assertEqual(len(self.pads), 1)
        self.assertFalse(self.pads[0].closed)
        self.assertEqual(self.pads[0].syncs, 1)

    AUDIO = musubi.audio_message(70000, b'pcm')

    def test_audio_message_layout(self):
        self.assertEqual(self.AUDIO, b'PBAU\x11\x70pcm')

    def test_sends_audio_to_the_latest_port_of_a_phone_that_can_play_it(self):
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        moved = (self.PHONE[0], 6000)
        self.bridge.on_datagram(self.PRESSED, moved)
        self.bridge.send_to_phone(self.AUDIO)
        self.assertEqual(self.sock.sent, [(self.AUDIO, moved)])
        self.assertTrue(self.bridge.status()['phone']['audio'])

    def test_no_audio_for_a_phone_that_didnt_ask_for_it(self):
        self.bridge.on_datagram(hello(), self.PHONE)
        self.bridge.send_to_phone(self.AUDIO)
        self.bridge.on_datagram(self.PRESSED, (musubi.USB, 0))
        self.bridge.send_to_phone(self.AUDIO)
        self.assertEqual((self.sock.sent, self.usb_sent), ([], []))
        self.assertFalse(self.bridge.status()['phone']['audio'])

    def test_nothing_is_sent_to_unapproved_or_gone_phones(self):
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.OTHER)
        self.bridge.send_to_phone(self.AUDIO)
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        self.now += musubi.PHONE_TIMEOUT + 1
        self.bridge.on_tick()
        self.bridge.send_to_phone(self.AUDIO)
        self.assertEqual(self.sock.sent, [])

    def test_sends_audio_over_the_cable_when_the_phone_is_on_it(self):
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), (musubi.USB, 0))
        self.usb_sent.clear()
        self.bridge.send_to_phone(self.AUDIO)
        self.assertEqual((self.sock.sent, self.usb_sent), ([], [self.AUDIO]))

    def test_a_cable_hello_gets_the_pc_name_back_and_a_wifi_hello_does_not(self):
        with mock.patch('socket.gethostname', return_value='desk'):
            self.bridge.on_datagram(hello(), self.PHONE)
            self.bridge.on_datagram(hello(), (musubi.USB, 0))
        self.assertEqual((self.sock.sent, self.usb_sent), ([], [b'PBNM' + b'desk']))

    def test_the_name_is_cut_without_splitting_a_character(self):
        self.assertEqual(musubi.name_message('a' * 63 + 'é'), b'PBNM' + b'a' * 63)

    def test_a_failed_send_doesnt_crash(self):
        bridge = Bridge(FailingSock(), [self.PHONE[0]], self.on_status, make_pad=self.make_pad, clock=lambda: self.now)
        bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        bridge.send_to_phone(self.AUDIO)

    def test_sound_only_for_an_audio_phone_on_either_link(self):
        self.assertFalse(self.bridge.wants_audio())
        self.bridge.on_datagram(hello(), self.PHONE)
        self.assertFalse(self.bridge.wants_audio())
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        self.assertTrue(self.bridge.wants_audio())
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), (musubi.USB, 0))
        self.assertTrue(self.bridge.wants_audio())

    def test_the_sink_survives_wifi_to_cable_to_wifi(self):
        pactl = FakePactl()
        sound = audio.PhoneAudio(run=pactl, popen=mock.Mock())
        usb = (musubi.USB, 0)

        def tick():
            # The same choice run_service makes every loop.
            self.bridge.on_tick()
            if self.bridge.wants_audio():
                sound.start()
            else:
                sound.stop()

        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        tick()
        self.bridge.on_datagram(self.PRESSED, usb)
        tick()
        self.bridge.send_to_phone(self.AUDIO)
        self.assertEqual(self.usb_sent, [self.AUDIO])
        self.now += musubi.TIMEOUT + 0.1
        tick()
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        tick()
        self.assertEqual(self.bridge.status()['phone']['ip'], self.PHONE[0])
        self.assertEqual(pactl.calls, [audio.LOAD])
        self.now += musubi.PHONE_TIMEOUT + 1
        tick()
        self.assertEqual(pactl.calls, [audio.LOAD, ['pactl', 'unload-module', '42']])

    def test_sound_stops_when_the_phone_goes(self):
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        self.now += musubi.PHONE_TIMEOUT + 1
        self.bridge.on_tick()
        self.assertFalse(self.bridge.wants_audio())
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        self.bridge.set_allowed([])
        self.assertFalse(self.bridge.wants_audio())

    def test_a_pad_error_is_reported_instead_of_crashing(self):
        def denied(slot=0):
            raise PermissionError('no access to /dev/uinput')
        bridge = Bridge(self.sock, [self.PHONE[0]], self.on_status, make_pad=denied, clock=lambda: self.now)
        bridge.on_datagram(hello(), self.PHONE)
        self.assertIn('uinput', bridge.status()['pad_error'])

    def test_uinput_error_from_make_pad_is_reported_instead_of_crashing(self):
        with mock.patch.object(musubi, 'Pad', side_effect=musubi.UInputError('"/dev/uinput" cannot be opened for writing')):
            bridge = Bridge(self.sock, [self.PHONE[0]], self.on_status, clock=lambda: self.now)
            bridge.on_datagram(hello(), self.PHONE)
        self.assertIn('/dev/uinput', bridge.status()['pad_error'])
        self.assertIsNone(bridge.pad)

    def test_multi_pad_creates_slots_in_order_and_routes_input_independently(self):
        inputs = []
        self.bridge.on_input = lambda slot, state: inputs.append((slot, state))
        self.bridge.on_datagram(
            hello(controller=b'Xbox', features=0, delay=0, mic=0, slots_mask=0b0101, extra=(b'', b'EasySMX', b'')),
            self.PHONE)
        self.assertEqual([p.slot for p in self.pads], [0, 2])
        self.assertEqual(self.bridge.status()['pads'], [True, False, True, False])
        self.assertEqual(self.bridge.status()['phone']['controllers'], ['Xbox', None, 'EasySMX', None])

        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.assertEqual((self.pads[0].syncs, self.pads[1].syncs), (1, 0))
        self.bridge.on_datagram(slotted_packet(2, self.PRESSED), self.PHONE)
        self.assertEqual((self.pads[0].syncs, self.pads[1].syncs), (1, 1))
        self.assertEqual(inputs, [(0, decode(self.PRESSED)), (2, decode(self.PRESSED))])

    def test_disconnecting_player_1_keeps_player_2_pad_alive(self):
        self.bridge.on_datagram(
            hello(features=0, delay=0, mic=0, slots_mask=0b0011, extra=(b'EasySMX',)),
            self.PHONE)
        self.bridge.on_datagram(slotted_packet(1, self.PRESSED), self.PHONE)
        self.bridge.on_datagram(
            hello(controller=b'', connected=False, features=0, delay=0, mic=0, slots_mask=0b0010, extra=(b'EasySMX',)),
            self.PHONE)
        self.assertTrue(self.pads[0].closed)
        self.assertFalse(self.pads[1].closed)
        self.assertEqual(self.bridge.status()['pads'], [False, True, False, False])
        self.assertEqual(self.bridge.status()['phone']['controllers'], [None, 'EasySMX', None, None])

        self.bridge.on_datagram(slotted_packet(1, PACKET.pack(2, *[0] * 8)), self.PHONE)
        self.assertEqual(self.pads[1].syncs, 2)

    def test_per_slot_silence_releases_only_the_quiet_player(self):
        self.bridge.on_datagram(
            hello(features=0, delay=0, mic=0, slots_mask=0b0011, extra=(b'EasySMX',)),
            self.PHONE)
        self.bridge.on_datagram(self.PRESSED, self.PHONE)
        self.bridge.on_datagram(slotted_packet(1, self.PRESSED), self.PHONE)
        self.now += musubi.TIMEOUT - 0.1
        self.bridge.on_datagram(slotted_packet(1, self.PRESSED), self.PHONE)
        self.now += 0.2
        self.bridge.on_tick()
        self.assertEqual((self.pads[0].syncs, self.pads[1].syncs), (2, 1))

    def test_dropping_phone_or_revoking_approval_closes_all_pads_and_stops_all_rumble(self):
        bridge = core.Bridge(self.sock, [self.PHONE[0]], self.on_status, make_pad=self.make_pad, clock=lambda: self.now)
        h = hello(features=musubi.FEATURE_RUMBLE, delay=0, mic=0, slots_mask=0b0011, extra=(b'Pad 2',))
        bridge.on_datagram(h, self.PHONE)
        bridge.set_rumble(100, 0, slot=0)
        bridge.set_rumble(200, 0, slot=1)
        self.sock.sent.clear()
        self.now += musubi.PHONE_TIMEOUT + 1
        bridge.on_tick()
        self.assertTrue(self.pads[0].closed and self.pads[1].closed)
        self.assertEqual([d for d, _ in self.sock.sent][-2:],
                         [musubi.rumble_message(0, 0, 0, slot=0), musubi.rumble_message(0, 0, 0, slot=1)])

        bridge.on_datagram(h, self.PHONE)
        self.sock.sent.clear()
        bridge.set_allowed([])
        self.assertTrue(self.pads[2].closed and self.pads[3].closed)
        self.assertIsNone(bridge.status()['phone'])
        self.assertEqual(self.sock.sent, [])

    def test_cable_handoff_keeps_all_open_pads(self):
        self.bridge.on_datagram(
            hello(features=0, delay=0, mic=0, slots_mask=0b0011, extra=(b'Pad 2',)),
            self.PHONE)
        self.bridge.on_datagram(slotted_packet(1, self.PRESSED), (musubi.USB, 0))
        self.assertEqual(self.bridge.status()['phone']['ip'], musubi.USB)
        self.assertFalse(self.pads[0].closed or self.pads[1].closed)
        self.assertEqual(self.pads[1].syncs, 1)


def pcm(left, right, frames=240):
    return struct.pack('<2h', left, right) * frames


class RumbleTest(unittest.TestCase):
    PHONE = ('192.168.1.10', 5000)
    STOP = musubi.rumble_message(0, 0, 0)

    def setUp(self):
        self.sock = FakeSock()
        self.now = 100.0
        self.usb_sent = []
        self.rumbles = []
        self.bridge = Bridge(self.sock, [self.PHONE[0]], lambda: None,
                             on_rumble=lambda slot, strong, weak: self.rumbles.append((slot, strong, weak)),
                             make_pad=FakeFfUi, clock=lambda: self.now, send_usb=self.usb_sent.append)
        self.bridge.on_datagram(hello(features=musubi.FEATURE_RUMBLE), self.PHONE)
        self.pad = self.bridge.pad

    def sent(self):
        return [data for data, _ in self.sock.sent]

    def play(self, effect_id, count=1, slot=0):
        self.bridge.slots[slot].pad.queue(e.EV_FF, effect_id, count)
        self.bridge.on_pad_events(slot)

    def test_message_layout(self):
        self.assertEqual(musubi.rumble_message(0xffff, 0x8000, 250), b'PBRM\xff\xff\x80\x00\x00\xfa')
        for slot in range(1, musubi.MAX_PLAYERS):
            self.assertEqual(musubi.rumble_message(0xffff, 0x8000, 250, slot=slot),
                             b'PBRM\xff\xff\x80\x00\x00\xfa' + bytes([slot]))

    def test_hello_flag(self):
        self.assertTrue(decode_hello(hello(features=musubi.FEATURE_RUMBLE))['rumble'])
        self.assertFalse(decode_hello(hello(features=musubi.FEATURE_AUDIO))['rumble'])

    def test_upload_play_and_stop(self):
        self.pad.upload(3, 0, 0xffff, 0x1000, 500)
        self.bridge.on_pad_events()
        self.assertEqual(self.pad.answered, [('upload', 3, 0)])
        self.assertEqual(self.sent(), [])
        self.play(0)
        self.play(0, 0)
        self.assertEqual(self.sent(), [musubi.rumble_message(0xffff, 0x1000, 500), self.STOP])

    def test_play_count_multiplies_the_length_and_unknown_ids_are_ignored(self):
        self.pad.upload(0, 1, 100, 200, 300)
        self.play(5)
        self.play(1, 3)
        self.assertEqual(self.sent(), [musubi.rumble_message(100, 200, 900)])

    def test_updating_the_playing_effect_resends_and_repeats_dont(self):
        self.pad.upload(0, 0, 100, 0, 0)
        self.play(0)
        self.bridge.on_tick()
        self.pad.upload(1, 0, 100, 0, 0)
        self.bridge.on_pad_events()
        self.pad.upload(2, 0, 900, 0, 0)
        self.bridge.on_pad_events()
        self.assertEqual(self.sent(), [musubi.rumble_message(100, 0, 0), musubi.rumble_message(900, 0, 0)])

    def test_stops_when_the_length_runs_out(self):
        self.pad.upload(0, 0, 1, 1, 200)
        self.play(0)
        self.now += 0.1
        self.bridge.on_tick()
        self.now += 0.2
        self.bridge.on_tick()
        self.bridge.on_tick()
        self.assertEqual(self.sent(), [musubi.rumble_message(1, 1, 200), self.STOP])

    def test_endless_rumble_is_refreshed_before_the_phone_gives_up(self):
        self.pad.upload(0, 0, 1, 1, 0)
        self.play(0)
        self.now += musubi.RUMBLE_REFRESH / 2
        self.bridge.on_tick()
        self.now += musubi.RUMBLE_REFRESH / 2
        self.bridge.on_tick()
        self.assertEqual(self.sent(), [musubi.rumble_message(1, 1, 0)] * 2)

    def test_erasing_the_playing_effect_stops_it(self):
        self.pad.upload(0, 2, 5, 5, 0)
        self.play(2)
        self.pad.erase(7, 2)
        self.bridge.on_pad_events()
        self.assertEqual(self.pad.answered[-1], ('erase', 7, 0))
        self.assertEqual(self.sent()[-1], self.STOP)
        self.play(2)
        self.assertEqual(len(self.sent()), 2)

    def test_a_dropped_phone_gets_a_final_stop(self):
        self.pad.upload(0, 0, 5, 5, 0)
        self.play(0)
        self.now += musubi.PHONE_TIMEOUT + 1
        self.bridge.on_tick()
        self.assertEqual(self.sent()[-1], self.STOP)
        self.assertTrue(self.pad.closed)

    def test_a_disconnected_controller_stops_the_rumble(self):
        self.pad.upload(0, 0, 5, 5, 0)
        self.play(0)
        self.bridge.on_datagram(hello(connected=False, features=musubi.FEATURE_RUMBLE), self.PHONE)
        self.assertEqual(self.sent()[-1], self.STOP)
        self.assertIsNone(self.bridge.pad)

    def test_only_phones_with_the_flag_get_rumble(self):
        self.bridge.on_datagram(hello(features=musubi.FEATURE_AUDIO), self.PHONE)
        self.pad.upload(0, 0, 5, 5, 0)
        self.play(0)
        self.assertEqual(self.sent(), [])

    def test_goes_over_the_cable_too(self):
        self.bridge.on_datagram(hello(features=musubi.FEATURE_RUMBLE), (musubi.USB, 0))
        self.usb_sent.clear()
        self.pad.upload(0, 0, 5, 5, 0)
        self.play(0)
        self.assertEqual(self.usb_sent, [musubi.rumble_message(5, 5, 0)])

    def test_a_failed_request_is_skipped(self):
        self.pad.queue(e.EV_UINPUT, e.UI_FF_UPLOAD, 9)
        self.pad.begin_upload = mock.Mock(side_effect=OSError)
        self.bridge.on_pad_events()
        self.bridge.on_pad_events()
        self.assertEqual(self.sent(), [])

    def test_reports_normalized_levels_on_change_and_zero_when_stopped_or_expired(self):
        self.pad.upload(0, 0, 0xc000, 0x4000, 200)
        self.bridge.on_pad_events()
        self.assertEqual(self.rumbles, [])
        self.play(0)
        self.play(0)
        self.now += 0.1
        self.bridge.on_tick()
        self.assertEqual(self.rumbles, [(0, 0.75, 0.25)])
        self.now += 0.2
        self.bridge.on_tick()
        self.bridge.on_tick()
        self.assertEqual(self.rumbles, [(0, 0.75, 0.25), (0, 0.0, 0.0)])
        self.pad.upload(1, 0, 0xffff, 0, 0)
        self.play(0)
        self.now += musubi.RUMBLE_REFRESH
        self.bridge.on_tick()
        self.assertEqual(self.rumbles, [(0, 0.75, 0.25), (0, 0.0, 0.0), (0, 1.0, 0.0)])
        self.play(0, 0)
        self.assertEqual(self.rumbles[-1], (0, 0.0, 0.0))

    def test_multi_pad_independent_rumble_and_disconnect(self):
        self.bridge.on_datagram(
            hello(features=musubi.FEATURE_RUMBLE, delay=0, mic=0, slots_mask=0b0011, extra=(b'Pad 2',)),
            self.PHONE)
        pad2 = self.bridge.slots[1].pad
        pad2.upload(0, 0, 0x8000, 0x4000, 0)
        self.play(0, slot=1)
        self.assertEqual(self.sent(), [musubi.rumble_message(0x8000, 0x4000, 0, slot=1)])
        self.assertEqual(self.rumbles, [(1, 0.5, 0.25)])

        self.pad.upload(0, 0, 0xffff, 0, 0)
        self.play(0, slot=0)
        self.assertEqual(self.sent()[-1], musubi.rumble_message(0xffff, 0, 0, slot=0))

        self.sock.sent.clear()
        self.bridge.on_datagram(
            hello(features=musubi.FEATURE_RUMBLE, delay=0, mic=0, slots_mask=0b0001),
            self.PHONE)
        self.assertEqual(self.sent(), [musubi.rumble_message(0, 0, 0, slot=1)])
        self.assertTrue(pad2.closed)
        self.sock.sent.clear()
        self.now += musubi.RUMBLE_REFRESH
        self.bridge.on_tick()
        self.assertEqual(self.sent(), [musubi.rumble_message(0xffff, 0, 0, slot=0)])


class LevelsTest(unittest.TestCase):
    def test_mono_for_the_mic(self):
        levels = audio.Levels(channels=1)
        levels.add(struct.pack('<2h', 16384, -32768))
        self.assertEqual(levels.poll(1.0), (1.0,))
        self.assertEqual(levels.poll(2.0), (0.0,))
        self.assertIsNone(levels.poll(3.0))

    def test_peaks_on_known_pcm(self):
        levels = audio.Levels()
        levels.add(pcm(0, 0))
        self.assertIsNone(levels.poll(1.0))
        levels.add(pcm(32767, -32768))
        self.assertEqual(levels.poll(2.0), (1.0, 1.0))
        levels.add(pcm(16384, 0))
        self.assertEqual(levels.poll(3.0), (0.5, 0.0))

    def test_left_only_leaves_right_at_zero(self):
        levels = audio.Levels()
        levels.add(pcm(-30000, 0))
        left, right = levels.poll(1.0)
        self.assertGreater(left, 0.9)
        self.assertEqual(right, 0)

    def test_rate_limited_and_peak_kept_between_polls(self):
        levels = audio.Levels(interval=0.04)
        levels.add(pcm(8000, 8000))
        self.assertIsNotNone(levels.poll(1.0))
        levels.add(pcm(20000, 0))
        self.assertIsNone(levels.poll(1.02))
        levels.add(pcm(1000, 0))
        self.assertEqual(levels.poll(1.05)[0], round(20000 / 32767, 2))

    def test_one_zero_when_the_sound_stops(self):
        levels = audio.Levels()
        levels.add(pcm(5000, 5000))
        levels.poll(1.0)
        self.assertEqual(levels.poll(2.0), (0.0, 0.0))
        self.assertIsNone(levels.poll(3.0))


class SoundSwitchTest(unittest.TestCase):
    def test_off_never_creates_the_sink_and_removes_an_existing_one(self):
        pactl = FakePactl()
        sound = audio.PhoneAudio(run=pactl, popen=mock.Mock())
        musubi.sync_sound(sound, False)
        self.assertEqual(pactl.calls, [])
        musubi.sync_sound(sound, True)
        musubi.sync_sound(sound, True)
        self.assertEqual(pactl.calls, [audio.LOAD])
        musubi.sync_sound(sound, False)
        self.assertEqual(pactl.calls, [audio.LOAD, ['pactl', 'unload-module', '42']])


class FakePactl:
    def __init__(self, modules='', fail=False):
        self.calls = []
        self.modules = modules
        self.fail = fail

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self.fail:
            raise FileNotFoundError('pactl')
        out = '42\n' if args[1] == 'load-module' else self.modules if args[1] == 'list' else ''
        return subprocess.CompletedProcess(args, 0, out, '')


class PhoneMicTest(unittest.TestCase):
    def setUp(self):
        self.pactl = FakePactl()
        self.now = 0.0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, 'musubi-mic')
        self.mic = audio.PhoneMic(run=self.pactl, clock=lambda: self.now, path=self.path)
        self.pcm = bytes(range(240)) * 2
        self.read = None

    def _fifo(self):
        """What the module does on load: make the pipe and hold its read end."""
        os.mkfifo(self.path)
        self.read = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, self.read)

    def _received(self):
        try:
            return os.read(self.read, 1 << 20)
        except BlockingIOError:
            return b''

    def test_the_device_is_a_mono_pipe_source_with_its_description_in_one_argument(self):
        self.mic.load()
        self.mic.load()
        self.assertEqual(self.pactl.calls, [[
            'pactl', 'load-module', 'module-pipe-source', 'source_name=musubi_mic', f'file={self.path}',
            'format=s16le', 'rate=48000', 'channels=1', 'source_properties="device.description=\\"Musubi mic\\""']])

    def test_nothing_is_opened_before_the_device_is_loaded(self):
        self._fifo()
        self.mic.feed(1, self.pcm)
        self.assertIsNone(self.mic.fd)
        self.assertEqual(self._received(), b'')

    def test_the_pipe_is_opened_on_the_first_packet_that_finds_a_reader_and_kept_small(self):
        self.mic.load()
        self.mic.feed(1, self.pcm)
        self.assertEqual((self.mic.fd, self.mic.failed), (None, False))
        self._fifo()
        self.mic.feed(2, self.pcm)
        self.assertEqual(self._received(), self.pcm)
        self.assertEqual(fcntl.fcntl(self.mic.fd, 1032), audio.MIC_PIPE_BYTES)

    def test_old_and_repeated_numbers_are_dropped_and_the_wrap_is_followed(self):
        self._fifo()
        self.mic.load()
        for seq in (0xfffe, 0xfffe, 0xfffd, 0xffff, 0, 1, 0xffff):
            self.mic.feed(seq, self.pcm)
        self.assertEqual(len(self._received()), 4 * len(self.pcm))

    def test_a_gap_makes_a_low_number_a_new_stream(self):
        self._fifo()
        self.mic.load()
        self.mic.feed(500, self.pcm)
        self.now = 0.4
        self.mic.feed(3, self.pcm)
        self.now = 0.9
        self.mic.feed(3, self.pcm)
        self.mic.feed(4, self.pcm)
        self.assertEqual(len(self._received()), 3 * len(self.pcm))

    def test_a_full_pipe_drops_the_chunk_instead_of_blocking(self):
        self._fifo()
        self.mic.load()
        for seq in range(300):
            self.mic.feed(seq, self.pcm)
        self.assertLess(len(self._received()), 300 * len(self.pcm))
        self.assertFalse(self.mic.failed)

    def test_a_dead_pipe_gives_up_and_unloads_the_device(self):
        self._fifo()
        self.mic.load()
        self.mic.feed(1, self.pcm)
        os.close(self.read)
        self.read = os.open(os.devnull, os.O_RDONLY)
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            self.mic.feed(2, self.pcm)
        self.assertTrue(self.mic.failed)
        self.assertEqual((self.mic.fd, self.mic.module), (None, None))
        self.assertEqual(self.pactl.calls[-1], ['pactl', 'unload-module', '42'])

    def test_stop_closes_the_pipe_and_unloads_the_device(self):
        self._fifo()
        self.mic.load()
        self.mic.feed(1, self.pcm)
        self.mic.stop()
        self.assertEqual((self.mic.fd, self.mic.module), (None, None))
        self.assertEqual(self.pactl.calls[-1], ['pactl', 'unload-module', '42'])

    def test_remove_stale_matches_only_its_own_source_name(self):
        self.pactl.modules = ('7\tmodule-pipe-source\tsource_name=musubi_mic file=/run/x format=s16le\n'
                              '8\tmodule-null-sink\tsink_name=musubi sink_properties=device.description=Musubi\n'
                              '9\tmodule-pipe-source\tsource_name=musubi_mic2\n')
        self.mic.remove_stale()
        self.assertEqual(self.pactl.calls[1:], [['pactl', 'unload-module', '7']])
        pactl = FakePactl(self.pactl.modules)
        audio.PhoneAudio(run=pactl, popen=mock.Mock()).remove_stale()
        self.assertEqual(pactl.calls[1:], [['pactl', 'unload-module', '8']])


class SyncMicTest(unittest.TestCase):
    """The choice run_service makes every loop, on a real PhoneMic."""

    def setUp(self):
        self.pactl = FakePactl()
        self.mic = audio.PhoneMic(run=self.pactl, path=os.devnull)

    def loads(self):
        return sum(call[1] == 'load-module' for call in self.pactl.calls)

    def unloads(self):
        return sum(call[1] == 'unload-module' for call in self.pactl.calls)

    def test_the_phone_decides_while_it_is_there(self):
        for state in (1, 2, 3):
            self.assertTrue(musubi.sync_mic(self.mic, {'mic': state}, False))
        self.assertEqual((self.loads(), self.unloads()), (1, 0))
        self.assertFalse(musubi.sync_mic(self.mic, {'mic': 0}, True))
        self.assertEqual((self.loads(), self.unloads()), (1, 1))

    def test_the_device_comes_back_after_the_phone_turns_it_off(self):
        musubi.sync_mic(self.mic, {'mic': 1}, False)
        musubi.sync_mic(self.mic, {'mic': 0}, True)
        musubi.sync_mic(self.mic, {'mic': 1}, False)
        self.assertEqual((self.loads(), self.unloads()), (2, 1))

    def test_a_quick_unmute_counts_as_a_new_stream(self):
        self.mic.last_seq = 500
        musubi.sync_mic(self.mic, {'mic': 1}, False)
        self.assertEqual(self.mic.last_seq, 500)
        musubi.sync_mic(self.mic, {'mic': 2}, True)
        self.assertIsNone(self.mic.last_seq)

    def test_with_no_phone_the_last_choice_stays(self):
        self.assertTrue(musubi.sync_mic(self.mic, None, True))
        self.assertEqual(self.loads(), 1)
        self.assertFalse(musubi.sync_mic(self.mic, None, False))
        self.assertEqual(self.unloads(), 1)

    def test_turning_it_off_and_on_retries_a_failed_device(self):
        self.pactl.fail = True
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            musubi.sync_mic(self.mic, {'mic': 1}, False)
            musubi.sync_mic(self.mic, {'mic': 1}, True)
        self.assertEqual(self.loads(), 1)
        self.pactl.fail = False
        musubi.sync_mic(self.mic, {'mic': 0}, True)
        musubi.sync_mic(self.mic, {'mic': 1}, False)
        self.assertEqual((self.loads(), self.mic.module), (2, '42'))


class PhoneAudioTest(unittest.TestCase):
    def setUp(self):
        self.pactl = FakePactl()
        self.capture = mock.Mock()
        self.sound = audio.PhoneAudio(run=self.pactl, popen=mock.Mock(return_value=self.capture))

    def test_chunks_are_exactly_5_ms(self):
        self.assertEqual(audio.CHUNK, 960)
        chunks, rest = audio.split_chunks(bytes(960 * 2 + 100))
        self.assertEqual(([len(c) for c in chunks], len(rest)), ([960, 960], 100))

    def test_reads_become_numbered_chunks_and_the_number_wraps(self):
        self.sound.start()
        self.sound.seq = 0xffff
        pcm = bytes(range(256)) * 10
        with mock.patch.object(audio.os, 'read', side_effect=[pcm[:1000], pcm[1000:]]):
            first = self.sound.read()
            second = self.sound.read()
        self.assertEqual([seq for seq, _ in first + second], [0xffff, 0])
        self.assertEqual(b''.join(c for _, c in first + second), pcm[:1920])
        self.assertEqual(len(self.sound.buffered), len(pcm) - 1920)

    def _seqs(self, chunks):
        with mock.patch.object(audio.os, 'read', return_value=b''.join(chunks)):
            return [seq for seq, _ in self.sound.read()]

    def test_stable_switches_to_7_5_ms_chunks_and_back_without_restarting(self):
        self.sound.start()
        pcm = bytes(range(1, 256)) * 30
        with mock.patch.object(audio.os, 'read', side_effect=[pcm[:2000], pcm[2000:4000], pcm[4000:5000]]):
            first = self.sound.read()
            self.sound.set_stable(True)
            second = self.sound.read()
            self.sound.set_stable(False)
            third = self.sound.read()
        self.assertEqual([len(c) for _, c in first + second + third], [960, 960, 1440, 960])
        self.assertEqual([seq for seq, _ in first + second + third], [0, 1, 2, 3])
        self.assertEqual(b''.join(c for _, c in first + second + third), pcm[:4320])
        self.assertEqual(self.pactl.calls, [audio.LOAD])

    def test_stable_hangover_is_still_200_ms(self):
        self.sound.start()
        self.sound.set_stable(True)
        sound, zero = b'\x01' * audio.STABLE_CHUNK, bytes(audio.STABLE_CHUNK)
        self.assertEqual(len(self._seqs([sound] + [zero] * audio.HANGOVER)), 1 + audio.HANGOVER * audio.CHUNK // audio.STABLE_CHUNK)

    def test_silence_stops_after_the_hangover_and_sound_resumes_at_once(self):
        self.sound.start()
        sound, zero = b'\x01' * audio.CHUNK, audio.SILENCE
        self.assertEqual(self._seqs([zero] * 5), [])
        self.assertEqual(len(self._seqs([sound] + [zero] * (audio.HANGOVER + 10))), 1 + audio.HANGOVER)
        self.assertEqual(self._seqs([zero, sound]), [audio.HANGOVER + 1])

    def test_pauses_shorter_than_the_hangover_keep_sending(self):
        self.sound.start()
        sound, zero = b'\x01' * audio.CHUNK, audio.SILENCE
        pattern = ([sound] + [zero] * (audio.HANGOVER - 1)) * 3 + [sound]
        self.assertEqual(self._seqs(pattern), list(range(len(pattern))))

    def test_loads_once_and_unloads_its_own_module(self):
        self.sound.start()
        self.sound.start()
        self.sound.stop()
        self.sound.stop()
        self.assertEqual(self.pactl.calls, [audio.LOAD, ['pactl', 'unload-module', '42']])
        self.capture.terminate.assert_called_once()
        self.assertEqual((self.sound.sources(), self.sound.buffered), ([], b''))

    def test_removes_sinks_left_by_a_killed_service(self):
        self.pactl.modules = ('7\tmodule-null-sink\tsink_name=musubi sink_properties=device.description=Musubi\n'
                              '8\tmodule-null-sink\tsink_name=musubi2\n9\tmodule-x11-bell\t\n')
        self.sound.remove_stale()
        self.assertEqual(self.pactl.calls[1:], [['pactl', 'unload-module', '7']])

    def test_missing_pactl_is_logged_once_and_never_retried(self):
        sound = audio.PhoneAudio(run=FakePactl(fail=True), popen=mock.Mock())
        with mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            sound.remove_stale()
            sound.start()
            sound.start()
        self.assertEqual(stderr.getvalue().count('\n'), 1)
        self.assertEqual((sound.capture, sound.module), (None, None))

    def test_a_capture_that_dies_unloads_the_sink(self):
        self.sound.start()
        with mock.patch('sys.stderr'), mock.patch.object(audio.os, 'read', return_value=b''):
            self.assertEqual(self.sound.read(), [])
        self.assertEqual(self.pactl.calls[-1], ['pactl', 'unload-module', '42'])
        self.assertIsNone(self.sound.capture)

    def test_stop_clears_failure_so_a_later_start_retries(self):
        self.pactl.fail = True
        with mock.patch('sys.stderr'):
            self.sound.remove_stale()
            self.sound.start()
        self.assertTrue(self.sound.failed)
        self.assertEqual(self.pactl.calls, [['pactl', 'list', 'short', 'modules']])
        self.sound.stop()
        self.assertFalse(self.sound.failed)
        self.pactl.fail = False
        self.sound.start()
        self.assertEqual(self.pactl.calls[-1], audio.LOAD)
        self.assertIs(self.sound.capture, self.capture)

    def test_capture_that_dies_retries_only_after_stop(self):
        self.sound.start()
        with mock.patch('sys.stderr'), mock.patch.object(audio.os, 'read', return_value=b''):
            self.assertEqual(self.sound.read(), [])
        self.assertTrue(self.sound.failed)
        calls = len(self.pactl.calls)
        self.sound.start()
        self.assertEqual(len(self.pactl.calls), calls)
        self.sound.stop()
        self.assertFalse(self.sound.failed)
        self.sound.start()
        self.assertEqual(self.pactl.calls[-1], audio.LOAD)
        self.assertIs(self.sound.capture, self.capture)

    def test_failed_popen_unloads_sink_and_does_not_retry_until_stop(self):
        popen = mock.Mock(side_effect=OSError('parec missing'))
        sound = audio.PhoneAudio(run=self.pactl, popen=popen)
        with mock.patch('sys.stderr'):
            sound.start()
        self.assertEqual(self.pactl.calls, [audio.LOAD, ['pactl', 'unload-module', '42']])
        self.assertTrue(sound.failed)
        sound.start()
        self.assertEqual(popen.call_count, 1)
        sound.stop()
        self.assertFalse(sound.failed)
        sound.popen = mock.Mock(return_value=self.capture)
        sound.start()
        self.assertIs(sound.capture, self.capture)

    def test_stop_clears_failure_even_when_unload_fails(self):
        self.sound.start()
        self.pactl.fail = True
        with mock.patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.sound.stop()
        self.assertEqual(stderr.getvalue().count('\n'), 1)
        self.assertFalse(self.sound.failed)
        self.pactl.fail = False
        self.sound.start()
        self.assertIs(self.sound.capture, self.capture)


class ServiceTest(unittest.TestCase):
    def test_stdin_allow_is_handled_before_waiting_udp_packets(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0))
        self.addCleanup(sock.close)
        phone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(phone.close)
        phone.sendto(hello(connected=False), sock.getsockname())

        in_r, in_w = os.pipe()
        out_r, out_w = os.pipe()
        stdin = os.fdopen(in_r, 'rb')
        stdout = os.fdopen(out_w, 'wb')
        out_reader = os.fdopen(out_r, 'rb')
        self.addCleanup(stdin.close)
        self.addCleanup(out_reader.close)

        os.write(in_w, b'{"allow": ["127.0.0.1"]}\n')
        os.close(in_w)

        real_audio, real_mic = audio.PhoneAudio, audio.PhoneMic
        with mock.patch.object(musubi, 'bind_socket', return_value=sock), \
                mock.patch.object(musubi, 'cable', None), \
                mock.patch.object(musubi.signal, 'signal'), \
                mock.patch.object(audio, 'PhoneAudio', side_effect=lambda: real_audio(run=mock.Mock(return_value=mock.Mock(stdout='')), popen=mock.Mock())), \
                mock.patch.object(audio, 'PhoneMic', side_effect=lambda: real_mic(run=mock.Mock(return_value=mock.Mock(stdout='')))), \
                mock.patch.object(musubi.sys, 'stdin', stdin), \
                mock.patch.object(musubi.sys, 'stdout', stdout):
            try:
                musubi.run_service()
            finally:
                stdout.close()

        events = [json.loads(line) for line in out_reader.read().splitlines() if line]
        statuses = [event for event in events if event['event'] == 'status']
        self.assertTrue(statuses)
        for status in statuses:
            self.assertEqual(status['pending'], [])
        self.assertTrue(any(status['phone'] and status['phone']['ip'] == '127.0.0.1' for status in statuses))


if __name__ == '__main__':
    unittest.main()
