#!/usr/bin/env python3
"""Musubi's desktop app. This hub keeps the tray and the receiver; the GTK window runs as its own process only while
it's open, because GTK alone costs about 45 MB."""
import json
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GLib

import system
from tray import Tray, tray_available

log = logging.getLogger('musubi')
HERE = Path(__file__).resolve().parent
RECEIVER = HERE / 'musubi.py'
# Window commands the hub handles itself. The rest go on to the receiver.
# How long a hidden start at login waits for the tray host to come up before showing the window instead.
TRAY_WAIT = 15
HUB_COMMANDS = ('stop', 'start', 'ignored', 'reload', 'page')


def idle_status(sound_enabled):
    return {'event': 'status', 'phone': None, 'pad': False, 'pads': [False] * 4, 'pad_error': None, 'pending': [],
            'sound_enabled': sound_enabled}


def pending_phones(status, ignored):
    return [phone for phone in status['pending'] if phone['ip'] not in ignored]


def tray_text(status, error, stopped, ignored, names):
    phone = status['phone']
    if error:
        return 'Musubi: error'
    if stopped:
        return 'Musubi: stopped'
    if pending_phones(status, ignored):
        return 'Musubi: a new phone is asking'
    if not phone:
        return 'Musubi: waiting for a phone'
    return f"Musubi: {names.get(phone['ip']) or phone['name'] or phone['ip']}"


def spawn(args, on_line, on_end):
    """Start a child that speaks JSON lines on stdout, reported to on_line until it closes."""
    process = subprocess.Popen([sys.executable, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    partial = b''

    def on_output(fd, condition):
        nonlocal partial
        if condition & GLib.IOCondition.IN:
            data = os.read(fd, 65536)
            if data:
                *lines, partial = (partial + data).split(b'\n')
                for line in lines:
                    on_line(line)
                return True
        process.source = None
        on_end(process)
        return False

    process.source = GLib.io_add_watch(process.stdout.fileno(), GLib.PRIORITY_DEFAULT,
                                       GLib.IOCondition.IN | GLib.IOCondition.HUP, on_output)
    return process


def close(process):
    if process.source is not None:
        GLib.source_remove(process.source)
    # Closing stdin makes the receiver release the pad and exit by itself, and makes the window quit.
    process.stdin.close()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    process.stdout.close()


class Hub(Gio.Application):
    def __init__(self, hidden):
        super().__init__(application_id='io.github.multex.musubi')
        self.start_hidden = hidden
        self.started = False
        self.config = system.load_config()
        self.receiver = None
        self.window = None
        self.error = None
        self.stopped = False
        self.status = idle_status(self.config['sound_enabled'])
        self.ignored = set()
        self.page = 'overview'  # The window's sidebar item, so a reopened window lands there.
        self.asking = []
        self.watch = False
        self.tray = None

    def do_activate(self):
        if self.started:
            self.show_window()
            return
        self.started = True
        # Only on the first activation, so a second launch never touches the running hub's log.
        system.setup_log('musubi-hub.log')
        log.info('musubi %s hub started, %s, python %s, session %s, hidden %s', system.VERSION, platform.platform(),
                 platform.python_version(), os.environ.get('XDG_SESSION_TYPE') or 'unknown', self.start_hidden)
        self.hold()  # The hub has no windows, so without this it would exit right after starting.
        try:
            # The icon registers itself whenever a tray host appears, even one that starts after us at login.
            self.tray = Tray(self.show_window, self.quit_app)
        except GLib.Error as ex:
            log.warning('tray unavailable: %s', ex)
            self.tray = None
        self.start_receiver()
        self.refresh()
        if not self.start_hidden or self.tray is None:
            self.show_window()
        elif not tray_available():
            log.info('tray host not ready, waiting %d s', TRAY_WAIT)
            GLib.timeout_add_seconds(TRAY_WAIT, self.show_without_tray)

    def show_without_tray(self):
        """A hidden start with no tray host by now would leave Musubi running with no way back to it."""
        if not tray_available():
            log.warning('no tray host after %d s, showing window', TRAY_WAIT)
            self.show_window()
        return False

    def quit_app(self):
        log.info('hub stopping')
        self.stop_receiver()
        window, self.window = self.window, None
        if window:
            close(window)
        self.quit()

    def refresh(self):
        asking = [phone['ip'] for phone in pending_phones(self.status, self.ignored)]
        # An open window raises itself for a new phone. A closed one opens once per new request.
        if asking and asking != self.asking and self.window is None and not self.error:
            self.show_window()
        self.asking = asking
        if self.tray:
            self.tray.set_status(tray_text(self.status, self.error, self.stopped, self.ignored, self.config['phones']))

    def start_receiver(self):
        self.config = system.load_config()
        self.error = None
        self.stopped = False
        try:
            self.receiver = spawn([str(RECEIVER), '--service'], self.on_receiver_line, self.on_receiver_end)
        except OSError as ex:
            log.error('could not start receiver: %s', ex)
            self.error = str(ex)
            return
        log.info('receiver started (pid %s)', self.receiver.pid)
        self.send_receiver({'allow': list(self.config['phones'])})
        self.send_receiver({'sound': self.config['sound_enabled']})
        self.send_receiver({'mic_keep': self.config['mic']})
        if self.watch:
            self.send_receiver({'watch': True})

    def stop_receiver(self):
        process, self.receiver = self.receiver, None
        if process:
            close(process)
            log.info('receiver stopped')
        self.status = idle_status(self.config['sound_enabled'])

    def send_receiver(self, command):
        if self.receiver is None:
            return
        try:
            self.receiver.stdin.write((json.dumps(command, ensure_ascii=False) + '\n').encode())
        except OSError:
            pass

    def on_receiver_line(self, line):
        try:
            event = json.loads(line)
        except ValueError:
            return
        if event['event'] == 'error':
            log.error('receiver error: %s', event['message'])
            self.error = event['message']
            self.send_state()
            self.refresh()
            return
        if event['event'] == 'status':
            self.status = event
            self.remember_mic(event['phone'])
            self.refresh()
        self.send_window(line)

    def remember_mic(self, phone):
        """The phone owns the mic switch. Saving its choice brings Musubi mic back at the next start, before the phone."""
        if not phone or phone.get('mic') is None or (phone['mic'] != 0) == self.config['mic']:
            return
        # Read fresh, so a phone the window approved a moment ago isn't saved away.
        config = {**system.load_config(), 'mic': phone['mic'] != 0}
        try:
            system.save_config(config)
        except OSError as ex:
            log.warning('could not save mic preference: %s', ex)
            return  # The next status tries again.
        self.config = config

    def on_receiver_end(self, process):
        if process is not self.receiver:
            return
        self.receiver = None
        unexpected = self.error is None
        close(process)
        if unexpected:
            log.warning('receiver exited unexpectedly (code %s)', process.returncode)
            self.error = 'The receiver stopped unexpectedly.'
        else:
            log.info('receiver exited (code %s)', process.returncode)
        self.status = idle_status(self.config['sound_enabled'])
        self.send_state()
        self.refresh()

    def show_window(self):
        if self.window:
            self.send_window(b'{"event": "present"}')
            return
        try:
            self.window = spawn([str(HERE / 'gui.py'), '--window'], self.on_window_line, self.on_window_end)
        except OSError:
            log.exception('could not start window')
            return
        log.info('window started (pid %s)', self.window.pid)
        # A stuck window drops lines instead of stalling the tray and the receiver. Status repeats every second.
        os.set_blocking(self.window.stdin.fileno(), False)
        self.send_state()

    def send_window(self, line):
        if self.window is None:
            return
        try:
            os.write(self.window.stdin.fileno(), line + b'\n')
        except OSError:
            pass

    def send_state(self):
        """Hub-owned state, plus the latest status so the window can draw at once."""
        self.send_window(json.dumps({'event': 'state', 'error': self.error, 'stopped': self.stopped,
                                     'ignored': sorted(self.ignored), 'page': self.page},
                                    ensure_ascii=False).encode())
        self.send_window(json.dumps(self.status, ensure_ascii=False).encode())

    def on_window_line(self, line):
        try:
            command = json.loads(line)
        except ValueError:
            return
        if 'stop' in command:
            self.stop_receiver()
            self.stopped = True
        if 'start' in command:
            # A failed save leaves the receiver running, and a second one couldn't bind the port.
            self.stop_receiver()
            self.start_receiver()
        if 'stop' in command or 'start' in command:
            self.send_state()
        if 'ignored' in command:
            self.ignored = set(command['ignored'])
        if 'reload' in command:
            self.config = system.load_config()
        if 'page' in command:
            self.page = command['page']
        if 'watch' in command:
            self.watch = bool(command['watch'])
        relay = {key: value for key, value in command.items() if key not in HUB_COMMANDS}
        if relay:
            self.send_receiver(relay)
        self.refresh()

    def on_window_end(self, process):
        if process is not self.window:
            return
        self.window = None
        close(process)
        log.info('window closed (code %s)', process.returncode)
        self.watch = False
        self.send_receiver({'watch': False})
        self.config = system.load_config()
        if not (self.config['tray'] and self.tray and tray_available()):
            self.quit_app()


def main():
    if '--window' in sys.argv:
        # Before the import, so a window that fails to load says why.
        system.setup_log('musubi-window.log', rotate=False, capture_stderr=True)
        try:
            import window  # Only the window process loads GTK.
        except Exception:
            log.exception('could not load the window')
            raise
        return window.main()
    try:
        return Hub('--hidden' in sys.argv).run([sys.argv[0]])
    except Exception:
        log.exception('hub crashed')
        raise


if __name__ == '__main__':
    sys.exit(main())
