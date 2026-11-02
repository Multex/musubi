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


if __name__ == '__main__':
    unittest.main()
