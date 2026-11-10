#!/usr/bin/env python3
import ipaddress
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import paths  # noqa: F401
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
        self.assertEqual(load_config(self.path), {'phones': {}, 'tray': True, 'sound_enabled': True})

    def test_survives_saving(self):
        config = {'phones': {'192.168.1.10': 'LG K40S'}, 'tray': False, 'sound_enabled': False}
        save_config(config, self.path)
        self.assertEqual(load_config(self.path), config)

    def test_a_broken_file_is_kept_aside_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{ not json')
        self.assertEqual(load_config(self.path), {'phones': {}, 'tray': True, 'sound_enabled': True})
        self.assertEqual(self.path.with_suffix('.broken').read_text(), '{ not json')


class AutostartTest(unittest.TestCase):
    def test_paths_with_spaces_stay_one_argument(self):
        with mock.patch('system.GUI_SCRIPT', Path('/home/me/My Apps/musubi/gui.py')), \
                mock.patch('sys.executable', '/usr/bin/python3'):
            self.assertEqual(shlex.split(startup_command()),
                             ['/usr/bin/python3', '/home/me/My Apps/musubi/gui.py', '--hidden'])


if __name__ == '__main__':
    unittest.main()
