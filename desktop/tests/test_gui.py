#!/usr/bin/env python3
import json
import subprocess
import sys
import unittest
from unittest import mock

import paths
from gui import Hub, idle_status, tray_text

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


class HubMicTest(unittest.TestCase):
    def setUp(self):
        config = {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': True}
        patcher = mock.patch('gui.system.load_config', side_effect=lambda: dict(config))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hub = Hub(hidden=False)
        save = mock.patch('gui.system.save_config')
        self.save = save.start()
        self.addCleanup(save.stop)

    def status(self, mic):
        phone = {**PHONE, 'controller': None, 'controller_known': True, 'rtt': 5, 'audio': False, 'delay': None,
                 'mic': mic}
        self.hub.on_receiver_line(json.dumps({**idle_status(True), 'phone': phone}).encode())

    def test_the_receiver_starts_with_the_remembered_mic(self):
        with mock.patch('gui.spawn') as spawn:
            self.hub.start_receiver()
        sent = [json.loads(call.args[0]) for call in spawn.return_value.stdin.write.call_args_list]
        self.assertIn({'mic_keep': True}, sent)

    def test_the_phones_mic_choice_is_saved_once(self):
        self.status(0)
        self.status(0)
        self.save.assert_called_once()
        self.assertFalse(self.save.call_args.args[0]['mic'])
        self.status(3)
        self.assertTrue(self.save.call_args.args[0]['mic'])
        self.assertEqual(self.save.call_count, 2)

    def test_no_phone_or_no_hello_yet_saves_nothing(self):
        self.hub.on_receiver_line(json.dumps(idle_status(True)).encode())
        self.status(None)
        self.save.assert_not_called()

    def test_a_failed_save_is_tried_again(self):
        self.save.side_effect = OSError('read-only')
        self.status(0)
        self.save.side_effect = None
        self.status(0)
        self.assertEqual(self.save.call_count, 2)
        self.assertFalse(self.hub.config['mic'])

    def test_mic_commands_from_the_window_go_to_the_receiver(self):
        self.hub.receiver = mock.Mock()
        self.hub.on_window_line(b'{"mic": 2}')
        self.hub.receiver.stdin.write.assert_called_once_with(b'{"mic": 2}\n')


class HubImportTest(unittest.TestCase):
    def test_hub_does_not_load_gtk(self):
        # GTK is most of the window's memory, and the hub stays running while the window is closed.
        code = f"import sys; sys.path.insert(0, {str(paths.DESKTOP / 'linux')!r}); import gui; " \
               "sys.exit('gi.repository.Gtk' in sys.modules)"
        self.assertEqual(subprocess.run([sys.executable, '-c', code]).returncode, 0)


if __name__ == '__main__':
    unittest.main()
