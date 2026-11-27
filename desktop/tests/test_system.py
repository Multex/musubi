#!/usr/bin/env python3
import ipaddress
import json
import logging
import logging.handlers
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import paths
import system
from system import (blocked_sources, covers, default_network, load_config, save_config, startup_command,
                    ufw_allowed_sources, ufw_enabled)

RULES = """
### tuple ### allow udp 9999 0.0.0.0/0 any 192.168.1.10 in
-A ufw-user-input -p udp --dport 9999 -s 192.168.1.10 -j ACCEPT
### tuple ### allow tcp 8000 0.0.0.0/0 any 192.168.1.10 in
### tuple ### deny udp 9999 0.0.0.0/0 any 10.0.0.0/8 in
### tuple ### allow udp 9999 0.0.0.0/0 any 192.168.2.0/24 in
### tuple ### allow udp 53 0.0.0.0/0 any 0.0.0.0/0 in
### tuple ### allow udp 9999 0.0.0.0/0 any not-an-address in
"""


class FirewallTest(unittest.TestCase):
    def test_reads_only_allow_rules_for_our_port(self):
        self.assertEqual(ufw_allowed_sources(RULES), [
            ipaddress.ip_network('192.168.1.10/32'), ipaddress.ip_network('192.168.2.0/24')])

    def test_covers_single_hosts_and_networks(self):
        sources = ufw_allowed_sources(RULES)
        self.assertTrue(covers(sources, '192.168.1.10'))
        self.assertTrue(covers(sources, '192.168.2.0/24'))
        self.assertFalse(covers(sources, '192.168.1.0/24'))
        self.assertFalse(covers(sources, '192.168.1.16'))
        self.assertFalse(covers(sources, 'not-an-ip'))

    def test_a_wider_rule_covers_the_network(self):
        self.assertTrue(covers([ipaddress.ip_network('192.168.0.0/16')], '192.168.1.0/24'))
        self.assertTrue(covers([ipaddress.ip_network('0.0.0.0/0')], '192.168.1.0/24'))
        self.assertFalse(covers([ipaddress.ip_network('::/0')], '192.168.1.0/24'))

    def test_reads_whether_ufw_is_on(self):
        self.assertTrue(ufw_enabled('# comment\nENABLED=yes\n'))
        self.assertFalse(ufw_enabled('ENABLED=no\n'))

    def test_finds_who_was_blocked_on_our_port_only(self):
        log = ('[UFW BLOCK] IN=enp8s0 SRC=192.168.1.16 DST=192.168.1.12 PROTO=UDP SPT=59847 DPT=9999 LEN=28\n'
               '[UFW BLOCK] IN=enp8s0 SRC=192.168.1.20 DST=192.168.1.12 PROTO=UDP SPT=1 DPT=99990 LEN=28\n'
               'something else SRC=192.168.1.30 DPT=9999\n')
        self.assertEqual(blocked_sources(log), {'192.168.1.16'})


class NetworkTest(unittest.TestCase):
    ROUTE = json.dumps([{'dst': 'default', 'dev': 'enp8s0', 'prefsrc': '192.168.1.12'}])

    def addr(self, local):
        return json.dumps([
            {'ifname': 'lo', 'addr_info': [{'local': '127.0.0.1', 'prefixlen': 8}]},
            {'ifname': 'enp8s0', 'addr_info': [{'local': local, 'prefixlen': 24}]},
        ])

    def test_finds_the_pc_and_its_network(self):
        self.assertEqual(default_network(self.ROUTE, self.addr('192.168.1.12')), ('192.168.1.12', '192.168.1.0/24'))

    def test_refuses_a_public_network(self):
        self.assertEqual(default_network(self.ROUTE, self.addr('8.8.8.8')), (None, None))

    def test_no_route_means_no_network(self):
        self.assertEqual(default_network('[]', self.addr('192.168.1.12')), (None, None))
        self.assertEqual(default_network('junk', 'junk'), (None, None))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'musubi' / 'config.json'

    def test_defaults_without_a_file(self):
        self.assertEqual(load_config(self.path), {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': False})

    def test_survives_saving(self):
        config = {'phones': {'192.168.1.10': 'LG K40S'}, 'tray': False, 'sound_enabled': False, 'mic': True}
        save_config(config, self.path)
        self.assertEqual(load_config(self.path), config)

    def test_a_broken_file_is_kept_aside_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{ not json')
        self.assertEqual(load_config(self.path), {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': False})
        self.assertEqual(self.path.with_suffix('.broken').read_text(), '{ not json')

    def test_an_unreadable_file_is_left_in_place(self):
        config = {'phones': {'192.168.1.10': 'LG K40S'}, 'tray': False, 'sound_enabled': False, 'mic': True}
        save_config(config, self.path)
        with mock.patch.object(Path, 'read_text', side_effect=PermissionError('locked')):
            self.assertEqual(load_config(self.path), {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': False})
        self.assertTrue(self.path.exists())
        self.assertFalse(self.path.with_suffix('.broken').exists())
        self.assertEqual(load_config(self.path), config)

    def test_wrong_json_shape_is_kept_aside(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"phones": null}')
        self.assertEqual(load_config(self.path), {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': False})
        self.assertEqual(self.path.with_suffix('.broken').read_text(), '{"phones": null}')

    def test_a_broken_file_whose_rename_fails_still_returns_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{ not json')
        with mock.patch.object(Path, 'replace', side_effect=PermissionError('locked')):
            self.assertEqual(load_config(self.path), {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': False})
        self.assertEqual(self.path.read_text(), '{ not json')
        self.assertFalse(self.path.with_suffix('.broken').exists())


class AutostartTest(unittest.TestCase):
    def test_paths_with_spaces_stay_one_argument(self):
        with mock.patch('system.GUI_SCRIPT', Path('/home/me/My Apps/musubi/gui.py')), \
                mock.patch('sys.executable', '/usr/bin/python3'):
            self.assertEqual(shlex.split(startup_command()),
                             ['/usr/bin/python3', '/home/me/My Apps/musubi/gui.py', '--hidden'])


class LoggingTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = folder.name
        self.dir = Path(folder.name) / 'state' / 'musubi'
        self.log = logging.getLogger('musubi')
        before, level = list(self.log.handlers), self.log.level

        def restore():
            for handler in [h for h in self.log.handlers if h not in before]:
                self.log.removeHandler(handler)
                handler.close()
                if getattr(handler, 'stream', None):
                    handler.stream.close()  # StreamHandler.close leaves its stream open.
            self.log.setLevel(level)

        self.addCleanup(restore)

    def test_rotating_log_is_created_once(self):
        handler = system.setup_log('musubi.log', log_dir=self.dir)
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.assertEqual((handler.maxBytes, handler.backupCount), (system.LOG_MAX_BYTES, system.LOG_BACKUPS))
        count = len(self.log.handlers)
        self.assertIsNone(system.setup_log('musubi.log', log_dir=self.dir))
        self.assertIsNone(system.setup_log('other.log', log_dir=self.dir))
        self.assertEqual(len(self.log.handlers), count)
        self.assertFalse((self.dir / 'other.log').exists())
        self.log.info('hello %s', 'there')
        self.assertRegex((self.dir / 'musubi.log').read_text(), r'^\d{4}-\d\d-\d\d [\d:,]+ INFO hello there\n$')

    def test_the_window_log_starts_fresh(self):
        self.dir.mkdir(parents=True)
        path = self.dir / 'musubi-window.log'
        path.write_text('last launch\n')
        with mock.patch.object(system.os, 'dup2') as dup2:
            system.setup_log(path.name, rotate=False, log_dir=self.dir)
        self.log.info('this launch')
        dup2.assert_not_called()
        self.assertNotIn('last launch', path.read_text())
        self.assertIn('this launch', path.read_text())

    def test_stderr_goes_to_the_file_without_a_terminal(self):
        terminal = mock.Mock(isatty=mock.Mock(return_value=False))
        with mock.patch('sys.stderr', terminal), mock.patch.object(system.os, 'dup2') as dup2:
            handler = system.setup_log('musubi-window.log', rotate=False, capture_stderr=True, log_dir=self.dir)
            self.assertIs(sys.stderr, handler.stream)
            dup2.assert_called_once_with(handler.stream.fileno(), 2)
            print('a traceback', file=sys.stderr)
        self.assertIn('a traceback', (self.dir / 'musubi-window.log').read_text())

    def test_a_terminal_keeps_stderr(self):
        terminal = mock.Mock(isatty=mock.Mock(return_value=True))
        with mock.patch('sys.stderr', terminal), mock.patch.object(system.os, 'dup2') as dup2:
            handler = system.setup_log('musubi-window.log', rotate=False, capture_stderr=True, log_dir=self.dir)
            self.assertIs(sys.stderr, terminal)
        dup2.assert_not_called()
        self.assertIsInstance(handler, logging.FileHandler)

    def test_an_unwritable_folder_is_not_fatal(self):
        blocker = Path(self.state) / 'file'
        blocker.write_text('')
        count = len(self.log.handlers)
        self.assertIsNone(system.setup_log('musubi.log', log_dir=blocker / 'musubi'))
        self.assertIsNone(system.setup_log('musubi.log', rotate=False, log_dir=blocker))
        self.assertEqual(len(self.log.handlers), count)

    def test_the_log_folder_follows_xdg_state_home(self):
        code = f"import sys; sys.path.insert(0, {str(paths.DESKTOP / 'linux')!r}); import system; print(system.LOG_DIR)"
        result = subprocess.run([sys.executable, '-c', code], env={**os.environ, 'XDG_STATE_HOME': self.state},
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), str(Path(self.state) / 'musubi'))


if __name__ == '__main__':
    unittest.main()
