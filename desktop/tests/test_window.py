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


@unittest.skipIf(window is None, 'needs GTK 4')
class MultiPlayerWindowTest(unittest.TestCase):
    def test_player_slots_reads_multi_pad_and_legacy_statuses(self):
        self.assertEqual(
            window.player_slots({'controller': None, 'controllers': [None] * 4}, {'pad': False, 'pads': [False] * 4}),
            ((False, None),) * 4,
        )
        self.assertEqual(
            window.player_slots(
                {'controller': 'Xbox', 'controllers': ['Xbox', 'DualShock', None, None]},
                {'pad': True, 'pads': [True, True, False, False]},
            ),
            ((True, 'Xbox'), (True, 'DualShock'), (False, None), (False, None)),
        )
        self.assertEqual(
            window.player_slots(
                {'controller': None, 'controllers': [None, 'EasySMX', None, None]},
                {'pad': True, 'pads': [False, True, False, False]},
            ),
            ((False, None), (True, 'EasySMX'), (False, None), (False, None)),
        )
        self.assertEqual(
            window.player_slots({'controller': 'Xbox'}, {'pad': True}),
            ((True, 'Xbox'), (False, None), (False, None), (False, None)),
        )

    def test_overview_controllers_text_formats_zero_one_and_many_players(self):
        self.assertEqual(
            window.overview_controllers_text(((False, None),) * 4),
            (0, 'No controller yet', 'Turn one on that is paired with the phone'),
        )
        self.assertEqual(
            window.overview_controllers_text(((True, 'Xbox'), (False, None), (False, None), (False, None))),
            (1, 'Xbox', 'Games see Xbox 360 controller 1'),
        )
        self.assertEqual(
            window.overview_controllers_text(((False, None), (True, 'EasySMX'), (False, None), (False, None))),
            (1, 'EasySMX', 'Games see Xbox 360 controller 2'),
        )
        self.assertEqual(
            window.overview_controllers_text(((True, 'Xbox'), (True, 'EasySMX'), (False, None), (False, None))),
            (2, 'Xbox · EasySMX', 'Games see Xbox 360 controllers 1 and 2'),
        )
        self.assertEqual(
            window.overview_controllers_text(((True, 'Xbox'), (True, 'EasySMX'), (True, 'Pad 3'), (False, None))),
            (3, 'Xbox · EasySMX · Pad 3', 'Games see Xbox 360 controllers 1, 2 and 3'),
        )


if __name__ == '__main__':
    unittest.main()
