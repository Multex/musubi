"""Musubi's window process on Windows: the Linux GTK window, talking to hub.py over stdin and stdout."""
import json
import logging
import os
import sys
import threading
import time
import types
import webbrowser
from pathlib import Path

STARTED = time.perf_counter()
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / 'linux')]

import main as win_main  # noqa: E402
import pad as win_pad  # noqa: E402
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
    from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
except Exception:
    # A windowed exe has nowhere else to say why GTK did not load.
    log.exception('could not load the window')
    raise

# Windows fonts, and dark title bar buttons that window.py's global button rule would otherwise box in.
WINDOWS_CSS = """
window, .root { font-family: "Segoe UI", sans-serif; }
.address, .mono, .mono-subnet, .readout { font-family: "Consolas", monospace; }
headerbar, .titlebar { background: #0D0C0A; background-image: none; color: #E6DEC8;
                       border-bottom: 1px solid #332E28; box-shadow: none; min-height: 36px; padding: 0 6px; }
headerbar:backdrop, .titlebar:backdrop { background: #0D0C0A; background-image: none; color: #8F8573; }
windowcontrols { border-spacing: 2px; }
windowcontrols button { min-height: 28px; min-width: 32px; margin: 0; padding: 0; border: none; border-radius: 6px;
                        background: transparent; background-image: none; box-shadow: none; color: #E6DEC8; }
headerbar windowcontrols.end > button:last-child { padding-right: 0; }
windowcontrols button > image { min-height: 16px; min-width: 16px; border-radius: 0; background: none; }
windowcontrols button:hover { background: #171512; }
windowcontrols button:active { background: #332E28; }
windowcontrols button.close:hover { background: #E04F3E; color: #0D0C0A; }
windowcontrols button.close:active { background: #B5382A; color: #F4EDDA; }
windowcontrols button:backdrop { color: #8F8573; }
"""


class Text:
    """A cairo context whose text goes through Pango. Cairo's own text calls, used by the drawings, crash on Windows."""

    def __init__(self, cr):
        self.cr = cr
        self.font = Pango.FontDescription()
        # PangoWin32 drops a comma list whose first font is missing and falls back to generic Sans.
        self.font.set_family('Segoe UI')

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


for drawing in window.PadArea, window.HeadphonesArea, window.MicArea, window.PlayerNumber:
    drawing.draw = through_pango(drawing.draw)


class App(window.App):
    driver_missing = False

    def on_event(self, event):
        if event['event'] == 'status':
            self.driver_missing = bool(event.get('driver_missing'))
        window.App.on_event(self, event)

    def panel_controllers(self, phone):
        window.App.panel_controllers(self, phone)
        if not self.driver_missing:
            return
        # Without the driver there is no pad to test, so the drawing gives way to the two ways forward.
        self.controllers_panel.remove(self.controllers_panel.get_last_child())
        self.controllers_panel.remove(self.pad)
        self.readout = None
        row = Gtk.Box(spacing=8)
        row.set_margin_top(16)
        row.append(window.button('Get the driver', lambda: webbrowser.open(win_pad.DRIVER_PAGE), 'primary'))
        row.append(window.button('Check again', lambda: self.send({'driver': True})))
        self.controllers_panel.append(row)

    def refresh_settings(self):
        window.App.refresh_settings(self)
        # The About box is the only divided block in the right column.
        about = self.settings_panel.get_last_child().get_last_child().get_first_child()
        while not about.has_css_class('divider'):
            about = about.get_next_sibling()
        logs = Gtk.Button(child=window.text('Open logs', 'link-text'), halign=Gtk.Align.START)
        logs.add_css_class('row')
        logs.update_property([Gtk.AccessibleProperty.LABEL], ['Open logs'])
        logs.connect('clicked', lambda _: self.open_logs())
        about.append(logs)

    def open_logs(self):
        folder = win_main.LOG_FILE.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except (AttributeError, OSError):  # Only Windows has startfile.
            webbrowser.open(folder.as_uri())

    def do_startup(self):
        window.App.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(WINDOWS_CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 2)
        # Windows needs GTK told to draw a dark title bar and to hint font metrics for crisp text at 1080p.
        settings = Gtk.Settings.get_default()
        if hasattr(Gtk, 'InterfaceColorScheme'):
            settings.set_property('gtk-interface-color-scheme', Gtk.InterfaceColorScheme.DARK)
        else:
            settings.set_property('gtk-application-prefer-dark-theme', True)
        if settings.find_property('gtk-hint-font-metrics') is not None:
            settings.set_property('gtk-hint-font-metrics', True)

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
