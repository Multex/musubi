#!/usr/bin/env python3
import io
import subprocess
import unittest
from unittest import mock

import audio
import musubi
from musubi import HELLO, HELLO_MAGIC, PACKET, Bridge, decode, decode_hello


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


def hello(phone=b'LM-X430', controller=b'Xbox Wireless Controller', connected=True, rtt=9, features=None, delay=None):
    """Without features, this is the hello of a 0.5.x app."""
    packet = HELLO.pack(HELLO_MAGIC, connected, rtt) + phone + b'\0' + controller
    if features is None:
        return packet
    return packet + b'\0' + bytes([features]) + (b'' if delay is None else delay.to_bytes(2, 'big'))


class HelloTest(unittest.TestCase):
    def test_reads_every_field(self):
        self.assertEqual(decode_hello(hello()), {
            'name': 'LM-X430', 'controller': 'Xbox Wireless Controller',
            'controller_connected': True, 'rtt': 9, 'audio': False, 'delay': None,
        })

    def test_reads_the_features_after_the_names(self):
        parsed = decode_hello(hello(features=musubi.FEATURE_AUDIO))
        self.assertEqual(parsed['controller'], 'Xbox Wireless Controller')
        self.assertTrue(parsed['audio'])
        self.assertFalse(decode_hello(hello(controller=b'', features=0))['audio'])

    def test_reads_the_delay_when_present(self):
        parsed = decode_hello(hello(features=musubi.FEATURE_AUDIO, delay=130))
        self.assertEqual((parsed['controller'], parsed['audio'], parsed['delay']),
                         ('Xbox Wireless Controller', True, 130))
        self.assertIsNone(decode_hello(hello(features=musubi.FEATURE_AUDIO, delay=0))['delay'])
        self.assertIsNone(decode_hello(hello(features=musubi.FEATURE_AUDIO))['delay'])
        self.assertIsNone(decode_hello(hello())['delay'])

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
    def __init__(self):
        self.syncs = 0
        self.closed = False

    def write(self, *_):
        pass

    def syn(self):
        self.syncs += 1

    def close(self):
        self.closed = True


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

    def make_pad(self):
        self.pads.append(FakeUi())
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

    def test_junk_from_strangers_isnt_a_phone(self):
        self.bridge.on_datagram(b'junk!', self.OTHER)
        self.assertEqual(self.bridge.status()['pending'], [])

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
        self.bridge.send_to_phone(self.AUDIO)
        self.assertEqual((self.sock.sent, self.usb_sent), ([], [self.AUDIO]))

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
        def denied():
            raise PermissionError('no access to /dev/uinput')
        bridge = Bridge(self.sock, [self.PHONE[0]], self.on_status, make_pad=denied, clock=lambda: self.now)
        bridge.on_datagram(hello(), self.PHONE)
        self.assertIn('uinput', bridge.status()['pad_error'])


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


if __name__ == '__main__':
    unittest.main()
