#!/usr/bin/env python3
import unittest

import paths  # noqa: F401

try:
    import window
except (ImportError, ValueError):  # No GTK 4 here, like the Windows CI.
    window = None


@unittest.skipIf(window is None, 'needs GTK 4')
class MicStateTest(unittest.TestCase):
    def test_each_hello_state(self):
        self.assertEqual(window.mic_state(1, 'Pixel 8'), ('on', 'Live', 'Live as Musubi mic', 'From Pixel 8', 'Live'))
        self.assertEqual(window.mic_state(2, 'Pixel 8')[1:], (
            'Muted', 'Musubi mic sends silence', 'From Pixel 8', 'Muted, sending silence'))
        mode, short, _, status, _ = window.mic_state(3, 'Pixel 8')
        self.assertEqual((mode, short, status), ('paused', 'Paused on the phone', 'Paused on Pixel 8'))

    def test_off_and_not_told_yet_look_the_same(self):
        off = ('off', 'Off', 'Turn it on here or on the phone', 'Mic is off', 'Turn it on here or on the phone')
        self.assertEqual(window.mic_state(0, 'Pixel 8'), off)
        self.assertEqual(window.mic_state(None, 'Pixel 8'), off)


if __name__ == '__main__':
    unittest.main()
