#!/usr/bin/env python3
import unittest

import padbridge
from padbridge import PACKET, decode


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
        buttons, axes = decode(padbridge.NEUTRAL)
        self.assertEqual(buttons, 0)
        self.assertEqual(axes, [0] * 8)


class FakeSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class FakeUi:
    def __init__(self):
        self.syncs = 0

    def write(self, *_):
        pass

    def syn(self):
        self.syncs += 1


class HandleTest(unittest.TestCase):
    PHONE = ('192.168.1.10', 5000)

    def setUp(self):
        self.sock, self.ui = FakeSock(), FakeUi()

    def run_handle(self, data, addr=PHONE, last=padbridge.NEUTRAL):
        return padbridge.handle(self.sock, self.ui, data, addr, self.PHONE[0], last)

    def test_echoes_pings_without_touching_the_pad(self):
        ping = bytes(range(padbridge.PING_SIZE))
        self.assertEqual(self.run_handle(ping), padbridge.NEUTRAL)
        self.assertEqual(self.sock.sent, [(ping, self.PHONE)])
        self.assertEqual(self.ui.syncs, 0)

    def test_ignores_other_hosts(self):
        ping = bytes(padbridge.PING_SIZE)
        state = PACKET.pack(1, *[0] * 8)
        self.assertEqual(self.run_handle(ping, ('192.168.1.99', 5000)), padbridge.NEUTRAL)
        self.assertEqual(self.run_handle(state, ('192.168.1.99', 5000)), padbridge.NEUTRAL)
        self.assertEqual(self.sock.sent, [])
        self.assertEqual(self.ui.syncs, 0)

    def test_applies_a_new_state_once(self):
        state = PACKET.pack(1, *[0] * 8)
        self.assertEqual(self.run_handle(state), state)
        self.assertEqual(self.run_handle(state, last=state), state)
        self.assertEqual(self.ui.syncs, 1)

    def test_keeps_the_last_state_after_a_malformed_packet(self):
        self.assertEqual(self.run_handle(b'junk!'), padbridge.NEUTRAL)
        self.assertEqual(self.ui.syncs, 0)


if __name__ == '__main__':
    unittest.main()
