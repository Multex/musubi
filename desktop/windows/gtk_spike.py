"""Experiment: the Linux GTK window on Windows, fed made-up events. It has no receiver and is not the real app."""
import ipaddress
import logging
import math
import os
import sys
import time
import types
from pathlib import Path

STARTED = time.perf_counter()
DESKTOP = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(DESKTOP), str(DESKTOP / 'linux')]

LOG_FILE = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'musubi' / 'gtk-test.log'
log = logging.getLogger('musubi')
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
log.addHandler(handler)
log.setLevel(logging.INFO)
log.info('gtk test, python %s', sys.version.split()[0])

try:
    import core  # noqa: E402

    # The window reads the firewall, the autostart and the network through Linux's system.py, so this stands in.
    system = types.ModuleType('system')
    system.PORT = core.PORT
    system.load_config = lambda: {'phones': {}, 'tray': True, 'sound_enabled': True}
    system.save_config = lambda config: None
    system.read_network = lambda: ('192.168.1.12', ipaddress.ip_network('192.168.1.0/24'))
    system.firewall_status = lambda: ('none', [])
    system.blocked_sources = lambda text: set()
    system.covers = lambda sources, network: True
    system.open_port_command = lambda network: []
    system.autostart_enabled = lambda: False
    system.set_autostart = lambda enabled: None
    system.startup_command = lambda: ''
    sys.modules['system'] = system

    import window  # noqa: E402
    from gi.repository import GLib, Gtk  # noqa: E402
except Exception:
    # A windowed exe has nowhere else to say why GTK did not load.
    log.exception('could not load the window')
    raise

PHONE = {'ip': '192.168.1.11', 'name': 'motorola edge 60 fusion', 'controller': 'Xbox Wireless Controller',
         'controller_known': True, 'rtt': 8, 'audio': True, 'delay': 140}


def memory_mb():
    if sys.platform != 'win32':
        return None
    import main
    return main.memory_mb()


class Spike(window.App):
    def do_activate(self):
        self.build_window()
        self.local_ip, self.network = system.read_network()
        self.refresh()
        self.show_window()
        self.on_event({'event': 'status', 'phone': PHONE, 'pad': True, 'pad_error': None, 'pending': [],
                       'cable_error': None, 'sound_enabled': True})
        log.info('window shown after %.1f s, %s MB', time.perf_counter() - STARTED, memory_mb())
        GLib.timeout_add(16, self.animate)
        GLib.timeout_add_seconds(10, self.measure)
        seconds = os.environ.get('MUSUBI_TEST_SECONDS')
        if seconds:
            GLib.timeout_add_seconds(int(seconds), self.quit)

    def measure(self):
        log.info('after 10 s: %s MB', memory_mb())
        return False

    def animate(self):
        """A left stick going round, one button after another, and sound levels, as a real phone would send."""
        now = time.perf_counter() - STARTED
        axes = [int(32767 * math.cos(now * 2)), int(32767 * math.sin(now * 2)), 0, 0,
                int(127 + 127 * math.sin(now)), 0, 0, 0]
        self.on_event({'event': 'input', 'buttons': 1 << int(now * 2) % 4, 'axes': axes})
        level = round(abs(math.sin(now * 3)), 2)
        self.on_event({'event': 'levels', 'l': level, 'r': round(level * 0.7, 2)})
        self.on_event({'event': 'rumble', 'strong': 40000 if int(now) % 6 == 0 else 0, 'weak': 0})
        return True

    def send(self, command):
        pass  # There is no hub to tell.

    def check_firewall(self):
        return False


def main():
    log.info('GTK %s.%s.%s', Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
    try:
        Spike().run([])
    except Exception:
        log.exception('crashed')
        raise
    log.info('closed')


if __name__ == '__main__':
    main()
