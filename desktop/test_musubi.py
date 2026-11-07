#!/usr/bin/env python3
import unittest

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


def hello(phone=b'LM-X430', controller=b'Xbox Wireless Controller', connected=True, rtt=9):
    return HELLO.pack(HELLO_MAGIC, connected, rtt) + phone + b'\0' + controller


class HelloTest(unittest.TestCase):
    def test_reads_every_field(self):
        self.assertEqual(decode_hello(hello()), {
            'name': 'LM-X430', 'controller': 'Xbox Wireless Controller',
            'controller_connected': True, 'rtt': 9,
        })

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
        self.bridge = Bridge(self.sock, [self.PHONE[0]], self.on_status,
                             make_pad=self.make_pad, clock=lambda: self.now)

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

    def test_a_pad_error_is_reported_instead_of_crashing(self):
        def denied():
            raise PermissionError('no access to /dev/uinput')
        bridge = Bridge(self.sock, [self.PHONE[0]], self.on_status, make_pad=denied, clock=lambda: self.now)
        bridge.on_datagram(hello(), self.PHONE)
        self.assertIn('uinput', bridge.status()['pad_error'])


if __name__ == '__main__':
    unittest.main()
