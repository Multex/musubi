"""Musubi for Windows. This process is the receiver; the Linux GTK window runs as its own process and speaks the
same JSON lines as on Linux, so GTK's memory goes away with the window."""
import json
import logging
import platform
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / 'linux')]  # core.py, and audio.py for the sound levels

import core  # noqa: E402
import main as win_main  # noqa: E402
import pad as win_pad  # noqa: E402
from audio import Levels  # noqa: E402

log = logging.getLogger('musubi')
# Window commands the hub handles itself. The rest go on to the receiver.
HUB_COMMANDS = ('stop', 'start', 'ignored', 'reload', 'page', 'driver')
NO_DRIVER = ("Controllers need the ViGEmBus driver, and this PC doesn't have it. Install it, then press Check again. "
             'Sound works without it.')


def idle_status(sound_enabled):
    return {'event': 'status', 'phone': None, 'pad': False, 'pad_error': None, 'pending': [],
            'sound_enabled': sound_enabled}


def driver_missing():
    """Whether a virtual pad can't be made because ViGEmBus isn't installed. Any other failure shows up when a
    controller connects, with its own message."""
    try:
        win_pad.Pad(lambda large, small: None).close()
    except win_pad.DriverMissing:
        log.warning('the ViGEmBus driver is not installed')
        return True
    except OSError as ex:
        log.warning('driver check: %s', ex)
    return False


def spawn_window():
    script = [] if getattr(sys, 'frozen', False) else [str(Path(__file__).resolve())]
    return subprocess.Popen([sys.executable, *script, '--window'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)


class Receiver(win_main.Console):
    """The console's loop, reporting to the window through `emit` the way Linux's run_service does."""

    def __init__(self, sock, emit, watching=False, **kwargs):
        self.emit = emit
        self.watching = watching
        self.levels = Levels()
        self.input_at = 0.0
        self.latest_input = None
        self.next_status = 0
        self.driver_missing = False
        super().__init__(sock, say=lambda text: None, **kwargs)
        self.bridge.on_input = self.on_input
        self.bridge.on_rumble = lambda strong, weak: emit({'event': 'rumble', 'strong': strong, 'weak': weak})

    def send_status(self):
        status = self.bridge.status()
        if self.driver_missing:
            status['pad_error'] = NO_DRIVER
        self.emit({'event': 'status', **status, 'cable_error': None, 'sound_enabled': self.config['sound_enabled'],
                   'driver_missing': self.driver_missing})

    def on_status(self):
        super().on_status()
        self.send_status()

    def on_input(self, state):
        self.latest_input = state
        now = time.monotonic()
        if self.watching and now - self.input_at >= 1 / 30:
            self.flush_input(now)

    def flush_input(self, now):
        self.input_at = now
        buttons, axes = self.latest_input
        self.emit({'event': 'input', 'buttons': buttons, 'axes': axes})
        self.latest_input = None

    def handle(self, kind, *details):
        # The console skips long silences, which add nothing to a peak, so this matches what the phone gets.
        if kind == 'audio' and self.watching and self.bridge.wants_audio():
            for pcm in details:
                self.levels.add(pcm)
        super().handle(kind, *details)

    def command(self, command):
        if 'allow' in command:
            self.bridge.set_allowed(command['allow'])
        if 'watch' in command:
            self.watching = bool(command['watch'])
        if 'sound' in command:
            self.config['sound_enabled'] = bool(command['sound'])
            self.send_status()

    def step(self, timeout=core.TICK):
        super().step(timeout)
        now = time.monotonic()
        if self.watching:
            pair = self.levels.poll(now)
            if pair:
                self.emit({'event': 'levels', 'l': pair[0], 'r': pair[1]})
            # Trailing input after a throttled burst, so the drawing ends on the true state.
            if self.latest_input is not None and now - self.input_at >= 1 / 30:
                self.flush_input(now)
        # Repeating this covers any status the window's full queue dropped.
        if now >= self.next_status:
            self.next_status = now + 1
            self.send_status()


class Hub:
    """Linux's hub without the tray: the receiver, and the window until it closes. Its state lives on one thread."""

    def __init__(self, make_pad=None, make_sound=None, port=core.PORT, config_path=core.CONFIG_FILE,
                 spawn_window=spawn_window, driver_missing=driver_missing):
        self.make_pad = make_pad
        self.make_sound = make_sound
        self.port = port
        self.config_path = config_path
        self.spawn_window = spawn_window
        self.check_driver = driver_missing
        self.driver_missing = False
        self.config = core.load_config(config_path)
        self.receiver = None
        self.window = None
        self.error = None
        self.stopped = False
        self.status = idle_status(self.config['sound_enabled'])
        self.ignored = set()
        self.page = 'overview'  # The window's sidebar item, kept like Linux keeps it for a reopened window.
        self.watch = False
        self.lines = queue.SimpleQueue()
        # A stuck window drops lines instead of stalling the receiver. Status repeats every second.
        self.outbox = queue.Queue(maxsize=256)

    def start_receiver(self):
        self.config = core.load_config(self.config_path)
        self.error = None
        self.stopped = False
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('0.0.0.0', self.port))
        except OSError as ex:
            sock.close()
            log.error('bind: %s', ex)
            self.error = f'Another program is using UDP port {self.port}. Is Musubi already running?'
            return
        log.info('listening, local address %s', win_main.local_ip())
        self.receiver = Receiver(sock, self.on_receiver_event, self.watch, make_pad=self.make_pad,
                                 config_path=self.config_path, make_sound=self.make_sound)
        self.receiver.driver_missing = self.driver_missing

    def stop_receiver(self):
        receiver, self.receiver = self.receiver, None
        if receiver:
            receiver.close()
            for sock in (receiver.sock, receiver.wake, receiver.waker):
                sock.close()
        self.status = idle_status(self.config['sound_enabled'])

    def on_receiver_event(self, event):
        if event['event'] == 'status':
            self.status = event
        self.send_window(event)

    def send_window(self, event):
        try:
            self.outbox.put_nowait(json.dumps(event).encode() + b'\n')
        except queue.Full:
            pass

    def send_state(self):
        """Hub-owned state, plus the latest status so the window can draw at once."""
        self.send_window({'event': 'state', 'error': self.error, 'stopped': self.stopped,
                          'ignored': sorted(self.ignored), 'page': self.page})
        self.send_window(self.status)

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
            self.config = core.load_config(self.config_path)
        if 'page' in command:
            self.page = command['page']
        if 'watch' in command:
            self.watch = bool(command['watch'])
        if 'driver' in command:
            self.sync_driver()
        relay = {key: value for key, value in command.items() if key not in HUB_COMMANDS}
        if relay and self.receiver:
            self.receiver.command(relay)

    def sync_driver(self):
        """Looks for the driver again, and lets a pad that failed without it be tried once more."""
        self.driver_missing = self.check_driver()
        if self.receiver:
            self.receiver.driver_missing = self.driver_missing
            self.receiver.bridge.pad_error = None
            self.receiver.send_status()

    def on_window_end(self):
        log.info('window closed')
        window, self.window = self.window, None
        try:
            window.wait(timeout=2)
        except subprocess.TimeoutExpired:
            window.kill()

    def read_window(self, stdout):
        """Reader thread: hands the window's lines to the main thread, then None when the window is gone."""
        for line in stdout:
            if line.strip():
                self.lines.put(line)
                self.wake()
        self.lines.put(None)
        self.wake()

    def wake(self):
        receiver = self.receiver  # Only read here. The main thread owns it, and may have just closed it.
        if receiver is not None:
            try:
                receiver.post('wake')
            except OSError:
                pass

    def write_window(self, stdin):
        try:
            while True:
                stdin.write(self.outbox.get())
                stdin.flush()
        except (OSError, ValueError):
            pass  # The window is gone, and the reader thread reports that.

    def run(self):
        memory = win_main.memory_mb() if sys.platform == 'win32' else None
        log.info('build %s, %s, python %s, %s MB', win_main.BUILD, platform.platform(), platform.python_version(),
                 memory)
        try:
            self.start_receiver()
            self.window = self.spawn_window()
            log.info('window started')
            threading.Thread(target=self.read_window, args=(self.window.stdout,), daemon=True).start()
            threading.Thread(target=self.write_window, args=(self.window.stdin,), daemon=True).start()
            self.send_state()
            # After the window is up, because a driver that is slow to answer takes seconds here.
            self.sync_driver()
            while self.window is not None:
                if self.receiver is not None:
                    self.receiver.step()
                block = self.receiver is None
                try:
                    while self.window is not None:
                        line = self.lines.get(block, core.TICK)
                        block = False
                        if line is None:
                            self.on_window_end()
                        else:
                            self.on_window_line(line)
                except queue.Empty:
                    pass
        finally:
            self.stop_receiver()
            log.info('stopped')


def main():
    if '--window' in sys.argv:
        import gtk_window  # Only the window process loads GTK, and it keeps its own log.
        return gtk_window.main()
    win_main.setup_log()
    try:
        Hub().run()
    except Exception:
        log.exception('crashed')


if __name__ == '__main__':
    sys.exit(main())
