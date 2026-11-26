"""Musubi's window process on Windows: the Linux GTK window, talking to hub.py over stdin and stdout."""
import json
import logging
import math
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


def replace_widget(old, new):
    parent = old.get_parent()
    parent.insert_child_after(new, old)
    parent.remove(old)
    return new


class SoundCordNode(window.CordNode):
    """Sound node with a dashed segment below it where the cord reaches the unplugged Mic node."""

    def __init__(self):
        super().__init__('up', has_line_below=False)

    def draw(self, area, cr, width, height, data=None):
        cr.save()
        cr.set_line_width(2)
        cr.set_source_rgb(*window.LATER)
        cr.set_dash([3, 2])
        cr.move_to(12.0, height / 2.0 + 11)
        cr.line_to(12.0, height)
        cr.stroke()
        cr.restore()
        super().draw(area, cr, width, height, data)


class UnpluggedCordNode(window.CordNode):
    """Dashed idle node with a small jack above it, showing Mic is unplugged on Windows."""

    def __init__(self):
        super().__init__('down', has_line_below=False)

    def draw(self, _area, cr, _width, height, _data=None):
        cx, cy = 12.0, height / 2.0
        jack_y = max(0.0, cy - 19)
        cr.set_line_width(2)
        cr.set_source_rgb(*window.LATER)
        if jack_y > 0:
            cr.set_dash([3, 2])
            cr.move_to(cx, 0)
            cr.line_to(cx, jack_y)
            cr.stroke()
            cr.set_dash([])
        window.HeadphonesArea.rounded(cr, cx - 3, jack_y, 6, 4, 1)
        cr.fill()

        cr.new_sub_path()
        cr.arc(cx, cy, 10, 0, 2 * math.pi)
        cr.set_source_rgb(*window.BACKGROUND)
        cr.fill_preserve()
        cr.set_dash([3, 2])
        cr.set_source_rgb(*window.IDLE)
        cr.stroke()
        cr.set_dash([])

        cr.set_line_width(1.8)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.set_source_rgb(*window.IDLE)
        cr.move_to(cx, cy - 5)
        cr.line_to(cx, cy + 5)
        cr.stroke()
        cr.move_to(cx - 4, cy + 1)
        cr.line_to(cx, cy + 5)
        cr.line_to(cx + 4, cy + 1)
        cr.stroke()


class UnpluggedMicArea(window.MicArea):
    """Dashed mic outline for Windows while virtual mic support is not built yet."""

    def sync_accessible(self):
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Mic, coming to Windows'])

    def set_mode(self, mode):
        pass

    def set_levels(self, left, right):
        pass

    def draw(self, _area, cr, width, height, _data=None):
        scale = min(width / self.WIDTH, height / self.HEIGHT)
        cr.translate((width - self.WIDTH * scale) / 2, (height - self.HEIGHT * scale) / 2)
        cr.scale(scale, scale)
        cr.set_source_rgb(*window.IDLE)
        cr.set_line_width(5)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.set_dash([10, 8])

        self.rounded(cr, 72, 10, 56, 104, 28)
        cr.stroke()

        cr.new_sub_path()
        cr.move_to(56, 70)
        cr.line_to(56, 80)
        cr.arc_negative(100, 80, 44, math.pi, 0)
        cr.line_to(144, 70)
        cr.move_to(100, 124)
        cr.line_to(100, 154)
        cr.move_to(74, 156)
        cr.line_to(126, 156)
        cr.stroke()


class App(window.App):
    driver_missing = False
    sound_error = None
    tray_caption = None

    def sound_alert(self, phone):
        return bool(phone and self.sound_enabled() and phone.get('audio') and self.sound_error)

    def build_sidebar(self):
        sidebar = window.App.build_sidebar(self)
        self.sound_node = replace_widget(self.sound_node, SoundCordNode())
        self.mic_node = replace_widget(self.mic_node, UnpluggedCordNode())
        return sidebar

    def update_sidebar(self, state, phone, pending):
        alert = state == 'connected' and self.sound_alert(phone)
        # Mask phone mic (and failed sound) so inactive Windows features never light up cord segments.
        win_phone = ({**phone, 'mic': None, 'audio': False if alert else phone.get('audio')}
                     if phone else phone)
        window.App.update_sidebar(self, state, win_phone, pending)
        if alert:
            self.sound_caption.set_label("Can't play")
            self.sound_caption.add_css_class('alert')
        else:
            self.sound_caption.remove_css_class('alert')
        self.mic_caption.set_label('Coming to Windows')
        self.mic_btn.update_property([Gtk.AccessibleProperty.LABEL], ['Mic, Coming to Windows'])

    def panel_overview(self, phone):
        window.App.panel_overview(self, phone)
        self.overview_mic_area = replace_widget(self.overview_mic_area, UnpluggedMicArea(width=80))

    def panel_mic(self, phone):
        self.mic_panel.append(window.text('Mic', 'page-title'))
        self.mic_panel.append(window.spacer())

        body = Gtk.Box(spacing=24)
        body.set_valign(Gtk.Align.CENTER)

        left = window.column(0)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.CENTER)

        intro = window.column(2)
        intro.append(window.text('Musubi mic on this PC', 'row-title', wrap=True))
        intro_detail = window.text(
            'Windows needs a virtual mic driver before an app can add a mic. '
            'Musubi will bring one in a later update, the way controllers use ViGEmBus.',
            'detail',
            wrap=True,
        )
        intro_detail.set_max_width_chars(40)
        intro.append(intro_detail)
        left.append(intro)

        until = window.column(2, 'divider')
        until.append(window.text('Until then', 'caption', ellipsize=True))
        until_detail = window.text(
            'Controllers and Sound on phone work as usual. On Linux, the mic works today.',
            'detail',
            wrap=True,
        )
        until_detail.set_max_width_chars(40)
        until.append(until_detail)
        left.append(until)

        right = window.column(4)
        right.set_size_request(150, -1)
        right.set_hexpand(False)
        right.set_halign(Gtk.Align.CENTER)
        right.set_valign(Gtk.Align.CENTER)

        self.mic_area = UnpluggedMicArea(width=120)
        self.mic_area.set_halign(Gtk.Align.CENTER)
        right.append(self.mic_area)

        self.mic_status = window.text('Unplugged', 'value', wrap=True)
        self.mic_status.set_margin_top(8)
        self.mic_detail = window.text('Coming to Windows', 'detail', wrap=True)
        for label, width in ((self.mic_status, 18), (self.mic_detail, 20)):
            label.set_max_width_chars(width)
            label.set_xalign(0.5)
            label.set_justify(Gtk.Justification.CENTER)
            label.set_halign(Gtk.Align.FILL)
            right.append(label)

        body.append(left)
        body.append(right)
        self.mic_panel.append(body)
        self.mic_panel.append(window.spacer())

    def update_mic(self, _phone):
        if self.overview_mic_value is not None:
            self.overview_mic_value.set_label('Unplugged on Windows')
            self.overview_mic_detail.set_label('Needs a virtual mic driver first')
            self.overview_mic_row.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ['Mic · to this PC, Unplugged on Windows'],
            )

    def sound_state(self, phone):
        on, value, detail = window.App.sound_state(self, phone)
        if on and self.sound_error:
            return False, "Can't play", self.sound_error
        if on:
            return on, value, "From this PC's default output"
        return on, value, detail

    def has_sound_delay(self, phone):
        return not self.sound_error and window.App.has_sound_delay(self, phone)

    def update_sound(self, phone):
        window.App.update_sound(self, phone)
        alert = self.sound_alert(phone)
        for label in (self.overview_sound_value, self.sound_value):
            if label is not None:
                if alert:
                    label.add_css_class('alert')
                else:
                    label.remove_css_class('alert')

    def panel_sound(self, phone):
        window.App.panel_sound(self, phone)
        self.sound_switch.get_prev_sibling().get_last_child().set_label(
            "When off, Musubi stops listening to this PC's sound"
        )
        self.sound_switch.get_parent().get_parent().get_last_child().get_last_child().set_label(
            'Musubi copies whatever plays on your default output. Change it in the Windows sound settings.'
        )

    def panel_waiting(self):
        window.App.panel_waiting(self)
        content = self.panel.get_first_child().get_next_sibling()
        child = content.get_first_child()
        while child is not None:
            if child.has_css_class('divider'):
                # Replace the Linux USB cable block, leaving any later Ignored phone divider intact.
                if self.local_ip:
                    replace_widget(child, window.text('Phone and PC need the same Wi-Fi network.', 'detail', wrap=True))
                else:
                    content.remove(child)
                break
            child = child.get_next_sibling()

    def on_event(self, event):
        if event['event'] == 'status':
            self.driver_missing = bool(event.get('driver_missing'))
            self.sound_error = event.get('sound_error')
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

    @staticmethod
    def tray_detail(enabled):
        return 'Closing the window keeps Musubi on' if enabled else 'Closing the window stops Musubi'

    def setting_switch(self, title, detail, active, on_change):
        if title == 'Start with my PC':
            box = window.column(2)
            box.set_hexpand(True)
            title_label = window.text(title, 'row-title', wrap=True)
            title_label.set_max_width_chars(24)
            detail_label = window.text('Coming to Windows', 'caption', wrap=True)
            detail_label.set_max_width_chars(28)
            box.append(title_label)
            box.append(detail_label)
            return box
        if title == 'Keep running in the tray':
            detail = self.tray_detail(active)
        row = window.App.setting_switch(self, title, detail, active, on_change)
        if title == 'Keep running in the tray':
            self.tray_caption = row.get_first_child().get_last_child()
        return row

    def on_tray_setting(self, enabled):
        window.App.on_tray_setting(self, enabled)
        if self.tray_caption is not None:
            self.tray_caption.set_label(self.tray_detail(enabled))

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
        # Raw os.read avoids holding BufferedReader's lock on sys.stdin during shutdown.
        fd = sys.stdin.fileno()
        partial = b''
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            *lines, partial = (partial + data).split(b'\n')
            for line in lines:
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


def main():
    log.info('window process, python %s, GTK %s.%s.%s', sys.version.split()[0], Gtk.get_major_version(),
             Gtk.get_minor_version(), Gtk.get_micro_version())
    code = 0
    try:
        code = App().run([sys.argv[0]]) or 0
    except Exception:
        log.exception('crashed')
        code = 1
    else:
        log.info('closed')
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    # Skip Python's stdin teardown: on Windows, closing stdin while read_hub blocks in ReadFile hangs or aborts.
    os._exit(code)
