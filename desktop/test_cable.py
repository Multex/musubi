#!/usr/bin/env python3
import threading
import time
import unittest
from unittest import mock

try:
    import cable
except ImportError:
    cable = None


class FakeEndpoint:
    def __init__(self):
        self.written = b''

    def write(self, data, timeout):
        self.written += bytes(data)


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

    def test_messages_to_the_phone_survive_any_split_or_merge(self):
        messages = [bytes(8), b'PBAU\x00\x01' + b'p' * 960, b'']
        stream = b''.join(cable.frame(m) for m in messages)
        for cut in range(len(stream) + 1):
            first, rest = cable.split_frames(stream[:cut])
            second, rest = cable.split_frames(rest + stream[cut:])
            self.assertEqual((first + second, rest), (messages, b''))

    def idle_cable(self):
        with mock.patch.object(cable.threading, 'Thread'):
            return cable.Cable()

    def test_a_full_queue_drops_the_oldest_message(self):
        link = self.idle_cable()
        for i in range(cable.QUEUE_SIZE + 2):
            link.send(bytes([i]))
        self.assertEqual(list(link.outbox), [cable.frame(bytes([i])) for i in range(2, cable.QUEUE_SIZE + 2)])

    def test_the_writer_sends_framed_messages_in_order_until_the_session_ends(self):
        link, endpoint, done = self.idle_cable(), FakeEndpoint(), threading.Event()
        writer = threading.Thread(target=link._write, args=(endpoint, done))
        writer.start()
        link.send(bytes(8))
        link.send(b'PBAU\x00\x01pcm')
        deadline = time.monotonic() + 2
        while link.outbox and time.monotonic() < deadline:
            time.sleep(0.01)
        with link.wake:
            done.set()
            link.wake.notify()
        writer.join(2)
        self.assertFalse(writer.is_alive())
        self.assertEqual(endpoint.written, cable.frame(bytes(8)) + cable.frame(b'PBAU\x00\x01pcm'))

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
