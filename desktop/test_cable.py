#!/usr/bin/env python3
import unittest

try:
    import cable
except ImportError:
    cable = None


class FakePhone:
    def __init__(self, protocol):
        self.protocol = protocol
        self.calls = []

    def ctrl_transfer(self, request_type, request, value, index, data):
        self.calls.append((request, index, data))
        return bytes([self.protocol, 0]) if request == cable.GET_PROTOCOL else None


@unittest.skipIf(cable is None, 'python-pyusb is not installed')
class CableTest(unittest.TestCase):
    def frame(self, message):
        return cable.LENGTH.pack(len(message)) + message

    def test_splits_messages_and_keeps_a_partial_one_for_later(self):
        data = self.frame(b'a' * 20) + self.frame(b'hello') + self.frame(b'cut off')[:4]
        messages, rest = cable.split_frames(data)
        self.assertEqual(messages, [b'a' * 20, b'hello'])
        messages, rest = cable.split_frames(rest + self.frame(b'cut off')[4:])
        self.assertEqual((messages, rest), ([b'cut off'], b''))

    def test_switch_sends_every_string_then_starts(self):
        phone = FakePhone(protocol=2)
        self.assertTrue(cable.switch(phone))
        strings = [(index, data) for request, index, data in phone.calls if request == cable.SEND_STRING]
        self.assertEqual(strings, [(i, s.encode() + b'\0') for i, s in enumerate(cable.STRINGS)])
        self.assertEqual(phone.calls[-1][0], cable.START)

    def test_a_device_without_accessory_support_is_left_alone(self):
        phone = FakePhone(protocol=0)
        self.assertFalse(cable.switch(phone))
        self.assertEqual(len(phone.calls), 1)


if __name__ == '__main__':
    unittest.main()
