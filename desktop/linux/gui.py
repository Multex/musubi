#!/usr/bin/env python3
"""Musubi's desktop app. This hub keeps the tray and the receiver; the GTK window runs as its own process only while
it's open, because GTK alone costs about 45 MB."""
import json
import os
import subprocess
import sys
from pathlib import Path

from gi.repository import Gio, GLib

import system
from tray import Tray, tray_available

HERE = Path(__file__).resolve().parent
RECEIVER = HERE / 'musubi.py'
# Window commands the hub handles itself. The rest go on to the receiver.
HUB_COMMANDS = ('stop', 'start', 'ignored', 'reload', 'page')


def idle_status(sound_enabled):
    return {'event': 'status', 'phone': None, 'pad': False, 'pad_error': None, 'pending': [],
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
        self.has_tray = False

    def do_activate(self):
        if self.started:
            self.show_window()
            return
        self.started = True
        self.hold()  # The hub has no windows, so without this it would exit right after starting.
        self.has_tray = tray_available()
        if self.has_tray:
            self.tray = Tray(self.show_window, self.quit_app)
        self.start_receiver()
        self.refresh()
        if not (self.start_hidden and self.has_tray):
            self.show_window()

    def quit_app(self):
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
            self.error = str(ex)
            return
        self.send_receiver({'allow': list(self.config['phones'])})
        self.send_receiver({'sound': self.config['sound_enabled']})
        if self.watch:
            self.send_receiver({'watch': True})

    def stop_receiver(self):
        process, self.receiver = self.receiver, None
        if process:
            close(process)
        self.status = idle_status(self.config['sound_enabled'])

    def send_receiver(self, command):
        if self.receiver is None:
            return
        try:
            self.receiver.stdin.write((json.dumps(command) + '\n').encode())
        except OSError:
            pass

    def on_receiver_line(self, line):
        event = json.loads(line)
        if event['event'] == 'error':
            self.error = event['message']
            self.send_state()
            self.refresh()
            return
        if event['event'] == 'status':
            self.status = event
            self.refresh()
        self.send_window(line)

    def on_receiver_end(self, process):
        if process is not self.receiver:
            return
        self.receiver = None
        close(process)
        if self.error is None:
            self.error = 'The receiver stopped unexpectedly.'
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
            return
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
                                     'ignored': sorted(self.ignored), 'page': self.page}).encode())
        self.send_window(json.dumps(self.status).encode())

    def on_window_line(self, line):
        command = json.loads(line)
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
        self.watch = False
        self.send_receiver({'watch': False})
        self.config = system.load_config()
        if not (self.has_tray and self.config['tray']):
            self.quit_app()


def main():
    if '--window' in sys.argv:
        import window  # Only the window process loads GTK.
        return window.main()
    return Hub('--hidden' in sys.argv).run([sys.argv[0]])


if __name__ == '__main__':
    sys.exit(main())
