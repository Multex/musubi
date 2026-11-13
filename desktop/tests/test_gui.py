#!/usr/bin/env python3
import subprocess
import sys
import unittest

import paths
from gui import idle_status, tray_text

PHONE = {'ip': '192.168.1.20', 'name': 'Pixel 8'}


class TrayTextTest(unittest.TestCase):
    def status(self, phone=None, pending=()):
        return {**idle_status(True), 'phone': phone, 'pending': list(pending)}

    def test_error_and_stopped_come_first(self):
        asking = self.status(pending=[PHONE])
        self.assertEqual(tray_text(asking, 'boom', True, set(), {}), 'Musubi: error')
        self.assertEqual(tray_text(asking, None, True, set(), {}), 'Musubi: stopped')

    def test_ignored_phones_dont_count_as_asking(self):
        asking = self.status(pending=[PHONE])
        self.assertEqual(tray_text(asking, None, False, set(), {}), 'Musubi: a new phone is asking')
        self.assertEqual(tray_text(asking, None, False, {PHONE['ip']}, {}), 'Musubi: waiting for a phone')

    def test_saved_name_beats_the_model_name(self):
        connected = self.status(phone=PHONE)
        self.assertEqual(tray_text(connected, None, False, set(), {}), 'Musubi: Pixel 8')
        self.assertEqual(tray_text(connected, None, False, set(), {PHONE['ip']: 'Couch'}), 'Musubi: Couch')


class HubImportTest(unittest.TestCase):
    def test_hub_does_not_load_gtk(self):
        # GTK is most of the window's memory, and the hub stays running while the window is closed.
        code = f"import sys; sys.path.insert(0, {str(paths.DESKTOP / 'linux')!r}); import gui; " \
               "sys.exit('gi.repository.Gtk' in sys.modules)"
        self.assertEqual(subprocess.run([sys.executable, '-c', code]).returncode, 0)


if __name__ == '__main__':
    unittest.main()
