"""Musubi's window process on Windows: the Linux GTK window, talking to hub.py over stdin and stdout."""
import json
import logging
import os
import sys
import threading
import time
import types
from pathlib import Path

STARTED = time.perf_counter()
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / 'linux')]

import main as win_main  # noqa: E402
import win_system  # noqa: E402

# Its own file, because two processes can't share the hub's rotating log. Started fresh each time to stay small.
LOG_FILE = win_main.LOG_FILE.with_name('musubi-window.log')
log = logging.getLogger('musubi')
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
# A windowed exe has no stderr, and that is where GTK reports a drawing that failed.
sys.stderr = open(LOG_FILE, 'w', encoding='utf-8', buffering=1)
os.dup2(sys.stderr.fileno(), 2)  # GTK's own C warnings write to file 2, not to Python's sys.stderr.
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
log.addHandler(handler)
log.setLevel(logging.INFO)
# The window reads the firewall, the autostart and the network through Linux's system.py, so this stands in.
sys.modules['system'] = win_system

try:
    import window  # noqa: E402
    __import__('gi').require_version('PangoCairo', '1.0')
    from gi.repository import GLib, Gtk, Pango, PangoCairo  # noqa: E402
except Exception:
    # A windowed exe has nowhere else to say why GTK did not load.
    log.exception('could not load the window')
    raise


class Text:
    """A cairo context whose text goes through Pango. Cairo's own text calls, used by the drawings, crash on Windows."""

    def __init__(self, cr):
        self.cr = cr
        self.font = Pango.FontDescription()
        self.font.set_family('Roboto, Segoe UI, sans-serif')

    def __getattr__(self, name):
        return getattr(self.cr, name)

    def select_font_face(self, family, slant, weight):
        self.font.set_weight(Pango.Weight.BOLD if weight else Pango.Weight.NORMAL)

    def set_font_size(self, size):
        self.font.set_absolute_size(size * Pango.SCALE)

    def layout(self, text):
        layout = PangoCairo.create_layout(self.cr)
        layout.set_font_description(self.font)
        layout.set_text(text, -1)
        return layout

    def text_extents(self, text):
        ink = self.layout(text).get_pixel_extents()[0]
        # Pango measures from the top left where cairo measures from the baseline, and show_text below matches it.
        return types.SimpleNamespace(width=ink.width, height=ink.height, x_bearing=ink.x, y_bearing=ink.y)

    def show_text(self, text):
        PangoCairo.show_layout(self.cr, self.layout(text))


def through_pango(draw):
    return lambda self, area, cr, *rest: draw(self, area, Text(cr), *rest)


for drawing in window.PadArea, window.HeadphonesArea, window.PlayerNumber:
    drawing.draw = through_pango(drawing.draw)


class App(window.App):
    def do_startup(self):
        window.App.do_startup(self)
        # The title bar is GTK's own on Windows, and it should be dark like the rest.
        Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', True)

    def do_activate(self):
        if self.window is not None:
            self.show_window()
            return
        if sys.stdin is None or sys.stdout is None:
            log.error('started without pipes to the hub, so there is nothing to show')
            self.quit()
            return
        self.build_window()
        self.local_ip, self.network = win_system.read_network()
        # A thread rather than GLib's watch on stdin, which is unproven on Windows pipes.
        threading.Thread(target=self.read_hub, daemon=True).start()
        self.refresh()
        self.show_window()
        log.info('window shown after %.1f s', time.perf_counter() - STARTED)

    def read_hub(self):
        for line in sys.stdin.buffer:
            if line.strip():
                GLib.idle_add(self.on_hub_line, line)
        GLib.idle_add(self.quit)  # The hub quit or died.

    def on_hub_line(self, line):
        self.on_event(json.loads(line))
        return GLib.SOURCE_REMOVE

    def send(self, command):
        try:
            sys.stdout.buffer.write((json.dumps(command) + '\n').encode())
            sys.stdout.buffer.flush()
        except OSError:
            pass

    def check_firewall(self):
        return False

    def on_autostart_done(self, error):
        # The parent's message offers a Linux startup command. Rebuilding also turns the switch back off.
        self.autostart_message = error
        self.refresh_settings()
        return GLib.SOURCE_REMOVE


def main():
    log.info('window process, python %s, GTK %s.%s.%s', sys.version.split()[0], Gtk.get_major_version(),
             Gtk.get_minor_version(), Gtk.get_micro_version())
    try:
        App().run([sys.argv[0]])
    except Exception:
        log.exception('crashed')
        raise
    log.info('closed')
