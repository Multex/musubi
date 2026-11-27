#!/usr/bin/env python3
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

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


@unittest.skipIf(window is None, 'needs GTK 4')
class HubPipeTest(unittest.TestCase):
    def test_corrupt_line_is_skipped_without_unhooking_the_watch(self):
        received = []
        stub = types.SimpleNamespace(partial=b'', on_event=received.append, quit=mock.Mock())
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        os.write(w, b'{cut{"event":"present"}\n{"event":"present"}\n')
        os.close(w)
        self.assertTrue(window.App.on_hub_output(stub, r, window.GLib.IOCondition.IN))
        stub.quit.assert_not_called()
        self.assertEqual(received, [{'event': 'present'}])


@unittest.skipIf(window is None, 'needs GTK 4')
class OpenLogsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / 'state' / 'musubi'
        self.app = types.SimpleNamespace(window=object(), log_dir=lambda: self.folder, _on_open_logs_done=mock.Mock(),
                                         _open_folder_fallback=mock.Mock())

    def test_log_dir_comes_from_the_system_module(self):
        with mock.patch.object(window, 'system', types.SimpleNamespace(LOG_DIR=self.folder)):
            self.assertEqual(window.App.log_dir(None), self.folder)
        with mock.patch.object(window, 'system', types.SimpleNamespace()):
            self.assertIsNone(window.App.log_dir(None))

    def test_the_folder_is_made_and_handed_to_the_file_launcher(self):
        with mock.patch.object(window.Gtk, 'FileLauncher', create=True) as launcher:
            window.App.open_logs(self.app)
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(launcher.call_args.kwargs['file'].get_path(), str(self.folder))
        launcher.return_value.launch.assert_called_once_with(self.app.window, None, self.app._on_open_logs_done,
                                                             self.folder)
        self.app._open_folder_fallback.assert_not_called()

    def test_an_older_gtk_uses_the_fallback(self):
        with mock.patch.object(window, 'Gtk', types.SimpleNamespace()):
            window.App.open_logs(self.app)
        self.app._open_folder_fallback.assert_called_once_with(self.folder)

    @unittest.skipUnless(hasattr(getattr(window, 'Gtk', None), 'DialogError'), 'needs GTK 4.10')
    def test_a_launcher_error_falls_back_unless_dismissed(self):
        for code, calls in ((window.Gtk.DialogError.DISMISSED, 0), (window.Gtk.DialogError.FAILED, 1)):
            error = window.GLib.Error.new_literal(window.Gtk.DialogError.quark(), 'no', code)
            launcher = mock.Mock(launch_finish=mock.Mock(side_effect=error))
            self.app._open_folder_fallback.reset_mock()
            window.App._on_open_logs_done(self.app, launcher, object(), self.folder)
            self.assertEqual(self.app._open_folder_fallback.call_count, calls)

    def test_the_fallback_tries_the_default_app_then_xdg_open(self):
        with mock.patch.object(window, 'Gio') as gio, mock.patch.object(window.subprocess, 'Popen') as popen:
            gio.AppInfo.launch_default_for_uri.return_value = True
            window.App._open_folder_fallback(None, self.folder)
            gio.AppInfo.launch_default_for_uri.assert_called_once_with(self.folder.as_uri(), None)
            popen.assert_not_called()
            for failure in (False, window.GLib.Error('no handler')):
                gio.AppInfo.launch_default_for_uri.side_effect = [failure]
                popen.reset_mock()
                window.App._open_folder_fallback(None, self.folder)
                popen.assert_called_once_with(['xdg-open', str(self.folder)], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_open_logs_skips_missing_or_uncreatable_folder_and_accepts_str(self):
        with mock.patch.object(window.Gtk, 'FileLauncher', create=True) as launcher:
            self.app.log_dir = lambda: None
            window.App.open_logs(self.app)
            launcher.assert_not_called()

            self.app.log_dir = lambda: str(self.folder)
            window.App.open_logs(self.app)
            self.assertEqual(launcher.call_args.kwargs['file'].get_path(), str(self.folder))
            launcher.return_value.launch.assert_called_once_with(self.app.window, None, self.app._on_open_logs_done,
                                                                 self.folder)

            launcher.reset_mock()
            with mock.patch.object(window.Path, 'mkdir', side_effect=OSError('read-only')), \
                    self.assertLogs('musubi', 'WARNING') as logs:
                window.App.open_logs(self.app)
            launcher.assert_not_called()
            self.assertEqual(len(logs.output), 1)
            self.app._open_folder_fallback.assert_not_called()


@unittest.skipUnless(window is not None and window.Gtk.init_check(), 'needs GTK 4 display')
class AboutBoxTest(unittest.TestCase):
    def labels(self, widget):
        if isinstance(widget, window.Gtk.Button) and isinstance(widget.get_child(), window.Gtk.Label):
            yield widget.get_child().get_label()
        child = widget.get_first_child()
        while child is not None:
            yield from self.labels(child)
            child = child.get_next_sibling()

    def test_open_logs_shows_only_with_a_log_folder(self):
        config = {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': False}
        with mock.patch.object(window.system, 'load_config', return_value=config), \
                mock.patch.object(window.system, 'autostart_enabled', return_value=False), \
                mock.patch.object(window.system, 'firewall_status', return_value=('none', [])):
            app = window.App()
            app.settings_panel = window.column()
            for folder, shown in ((Path('/logs'), True), (None, False)):
                with mock.patch.object(app, 'log_dir', return_value=folder):
                    app.refresh_settings()
                self.assertEqual('Open logs' in self.labels(app.settings_panel), shown)


if __name__ == '__main__':
    unittest.main()
