"""Musubi's receiver for Windows, as a console program."""
import ctypes
import logging
import logging.handlers
import os
import platform
import queue
import select
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # core.py is shared with Windows, one folder up

import core  # noqa: E402
import pad as win_pad  # noqa: E402

try:
    from build import BUILD
except ImportError:
    BUILD = 'from source'

LOG_FILE = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'musubi' / 'musubi.log'
FIREWALL_HINT_AFTER = 30
GAP_LOG_AFTER = 0.4  # A connected phone sends its state more often than this.
MEMORY_LOG_AFTER = 300
log = logging.getLogger('musubi')


def local_ip():
    """This PC's address on the network it reaches the internet through. Sends nothing."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('192.0.2.1', 9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


class MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_ulong), ('page_faults', ctypes.c_ulong)] + [
        (name, ctypes.c_size_t) for name in ('peak_working_set', 'working_set', 'peak_paged', 'paged',
                                             'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]


def memory_mb():
    """RAM this process holds, as Task Manager counts it."""
    counters = MemoryCounters()
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), ctypes.sizeof(counters))
    return round(counters.working_set / 2 ** 20, 1)


class Console:
    """The receiver loop. Threads hand it work through `post`, so the bridge is only touched from one thread."""

    def __init__(self, sock, make_pad=None, config_path=core.CONFIG_FILE, say=print):
        self.sock = sock
        self.say = say
        self.config_path = config_path
        self.config = core.load_config(config_path)
        self.inbox = queue.SimpleQueue()
        self.wake, self.waker = socket.socketpair()
        self.shown = None
        self.running = True
        self.asked = set()
        self.line = 'No phone connected.'
        self.heard = False
        self.rumbled = False
        self.last_packet = None
        self.longest_step = 0.0
        self.recv_errors = 0
        self.bridge = core.Bridge(sock, self.config['phones'], self.on_status,
                                  make_pad=make_pad or (lambda: win_pad.Pad(self.on_rumble)))

    def post(self, *item):
        self.inbox.put(item)
        self.waker.send(b'\0')

    def on_rumble(self, large, small):
        self.post('rumble', large, small)

    def on_status(self):
        status = self.bridge.status()
        if status == self.shown:
            return
        self.shown = status
        log.info('status %s', status)
        for phone in status['pending']:
            if phone['ip'] not in self.asked:
                self.asked.add(phone['ip'])
                self.say(f"{phone['name'] or 'A phone'} ({phone['ip']}) wants to connect. "
                         'Type y and press Enter to allow it.')
        phone = status['phone']
        if phone:
            line = (f"Phone: {phone['name'] or phone['ip']}. Controller: {phone['controller'] or 'none'}. "
                    f"Virtual pad: {'on' if status['pad'] else 'off'}.")
        else:
            line = 'No phone connected.'
        if status['pad_error']:
            line += f" The virtual pad could not start: {status['pad_error']}"
        if line != self.line:
            self.line = line
            self.say(line)

    def handle(self, kind, *details):
        if kind == 'rumble':
            large, small = details
            if not self.rumbled:
                self.rumbled = True
                log.info('first rumble from a game: %s %s', large, small)
            self.bridge.set_rumble(large * 257, small * 257)
        elif kind == 'allow':
            self.allow(details[0])
        elif kind == 'quit':
            self.running = False
        elif kind == 'line' and details[0].lower() in ('y', 'yes'):
            self.allow(None)

    def allow(self, ip):
        """Lets the waiting phone at ip connect, or every waiting phone when ip is None, and remembers it."""
        pending = [phone for phone in self.bridge.status()['pending'] if ip in (None, phone['ip'])]
        if not pending:
            self.say('No phone is waiting.')
            return
        for phone in pending:
            self.config['phones'][phone['ip']] = phone['name']
            self.say(f"Allowed {phone['name'] or phone['ip']}.")
        core.save_config(self.config, self.config_path)
        self.bridge.set_allowed(self.config['phones'])

    def step(self, timeout=core.TICK):
        readable = select.select([self.sock, self.wake], [], [], timeout)[0]
        now = time.perf_counter()
        if self.wake in readable:
            self.wake.recv(64)
            while not self.inbox.empty():
                self.handle(*self.inbox.get())
        if self.sock in readable:
            try:
                data, addr = self.sock.recvfrom(core.MAX_DATAGRAM)
            except OSError as ex:
                # Windows raises here after a send to a phone that closed its port, and for oversized packets.
                self.recv_errors += 1
                if self.recv_errors % 100 == 1:
                    log.warning('receive error %s (%d so far)', ex, self.recv_errors)
            else:
                if not self.heard:
                    self.heard = True
                    log.info('first packet, from %s', addr[0])
                if self.last_packet is not None and self.bridge.active is not None \
                        and now - self.last_packet > GAP_LOG_AFTER:
                    log.warning('gap %d ms since the last packet, longest loop step meanwhile %d ms, '
                                'receive errors so far %d', (now - self.last_packet) * 1000,
                                self.longest_step * 1000, self.recv_errors)
                    self.longest_step = 0.0
                self.last_packet = now
                self.bridge.on_datagram(data, addr)
        self.bridge.on_tick()
        busy = time.perf_counter() - now
        self.longest_step = max(self.longest_step, busy)
        if busy > 0.05:
            log.warning('slow loop step %d ms', busy * 1000)

    def close(self):
        self.bridge.close()


def read_lines(console):
    # Started without a console there is no stdin at all.
    for line in sys.stdin or ():
        console.post('line', line.strip())


def keep_running_when_clicked():
    """A click in a Windows console selects text and freezes the program until Enter, so turn selecting off."""
    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    # Standard input, then line input, echo and processed input, with the extended flag that clears quick edit.
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), 0x0007 | 0x0080)


def check_driver():
    """None if a virtual pad can be made, else what is wrong."""
    try:
        win_pad.Pad(lambda large, small: None).close()
    except OSError as ex:
        log.error('driver check: %s', ex)
        return str(ex)
    log.info('driver found')
    return None


def serve(console, say):
    """Runs the receiver loop until a 'quit' is posted, then closes it."""
    started = time.monotonic()
    hinted = False
    memory_logged = False
    try:
        while console.running:
            console.step()
            running = time.monotonic() - started
            if not hinted and not console.heard and running > FIREWALL_HINT_AFTER:
                hinted = True
                log.warning('no packet in %s s', FIREWALL_HINT_AFTER)
                say('Nothing has arrived from a phone yet. Check that the phone is on the same Wi-Fi '
                    'and that Windows Firewall allows Musubi on both private and public networks.')
            if not memory_logged and running > MEMORY_LOG_AFTER:
                memory_logged = True
                log.info('memory after %s s: %s MB', MEMORY_LOG_AFTER, memory_mb())
    finally:
        console.close()
        log.info('stopped')


def run():
    keep_running_when_clicked()
    print(f'Musubi for Windows, test build ({BUILD})')
    print(f'Log file: {LOG_FILE}')
    log.info('build %s, %s, python %s, %s MB', BUILD, platform.platform(), platform.python_version(), memory_mb())
    error = check_driver()
    if error:
        print(f'ViGEmBus driver: {error}')
        print('The controller will not work until that is fixed. Then start Musubi again.')
    else:
        print('ViGEmBus driver: found.')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(('0.0.0.0', core.PORT))
    except OSError as ex:
        log.error('bind: %s', ex)
        print(f'Another program is using UDP port {core.PORT}. Is Musubi already running?')
        return

    address = local_ip()
    log.info('listening, local address %s', address)
    print(f"This PC's address: {address or 'unknown, check your network settings'}")
    print('Enter that address in the Musubi app on your phone. Press Ctrl+C here to quit.')

    console = Console(sock)
    threading.Thread(target=read_lines, args=(console,), daemon=True).start()
    serve(console, print)


def setup_log():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=256 * 1024, backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def main():
    setup_log()
    try:
        run()
    except KeyboardInterrupt:
        return
    except Exception:
        log.exception('crashed')
        print(f'Musubi stopped because of an error. Please send this file: {LOG_FILE}')
    input('Press Enter to close.')


if __name__ == '__main__':
    main()
