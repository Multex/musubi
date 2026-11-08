#!/usr/bin/env python3
"""Desktop window for Musubi. The receiver runs in its own process so drawing can't delay the pad."""
import json
import math
import os
import subprocess
import sys
import threading
from pathlib import Path

import gi

# This window draws little and may sit in the tray all day, so GPU renderers' extra memory isn't worth it.
os.environ.setdefault('GSK_RENDERER', 'cairo')
# Cairo's partial redraws leave stray pixels near text that changes, like the ping.
os.environ.setdefault('GSK_DEBUG', 'full-redraw')
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import system  # noqa: E402
from tray import Tray, tray_available  # noqa: E402

VERSION = '0.6.0'
REPO = 'https://github.com/Multex/musubi'
RECEIVER = Path(__file__).resolve().parent / 'musubi.py'
BUTTON_NAMES = ['A', 'B', 'X', 'Y', 'L1', 'R1', 'Select', 'Start', 'Guide', 'L3', 'R3']
A, B, X, Y, L1, R1, SELECT, START, _, L3, R3 = range(11)
STICK_MAX = 32767
TRIGGER_MAX = 255
# Sticks rarely rest at exactly zero, and this only affects what the drawing shows.
DEAD_ZONE = 0.05
WINDOW_WIDTH, WINDOW_HEIGHT = 760, 480
SIDEBAR_WIDTH = 230
PANEL_WIDTH = 210
FIREWALL_CHECK_SECONDS = 3
LOG_WINDOW = '-15s'


def rgb(value):
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


BACKGROUND, SURFACE, BORDER = rgb('#0D0C0A'), rgb('#171512'), rgb('#332E28')
IDLE, LATER, THUMB = rgb('#6B6256'), rgb('#4A443B'), rgb('#2A2621')
TEXT, PRIMARY_HOVER, MUTED = rgb('#E6DEC8'), rgb('#F4EDDA'), rgb('#8F8573')
ACTIVE, ALERT, GAME_BG = rgb('#FF9E1B'), rgb('#E04F3E'), rgb('#000000')

CSS = """
window, .root { background: #0D0C0A; color: #E6DEC8; font-family: Roboto, sans-serif; }
scrolledwindow, viewport { background: transparent; }
.sidebar { border-right: 1px solid #332E28; padding: 22px 12px 14px 12px; background: #0D0C0A; }
.page { padding: 22px 24px 20px 24px; background: #0D0C0A; }
.side { border-left: 1px solid #332E28; padding: 0 0 0 16px; background: #0D0C0A; }
.divider { border-top: 1px solid #332E28; margin-top: 12px; padding-top: 8px; }
.column { padding: 12px 16px; }
.column-divider { border-right: 1px solid #332E28; }
.header { border-bottom: 1px solid #332E28; padding: 4px 16px 4px 4px; }
.caption { font-size: 12px; color: #8F8573; }
.detail { font-size: 13px; color: #8F8573; }
.muted { color: #8F8573; }
.value { font-size: 16px; font-weight: 500; }
.row-title { font-size: 15px; font-weight: 500; }
.body { font-size: 14px; }
.title { font-size: 24px; font-weight: 700; }
.page-title { font-size: 18px; font-weight: 500; }
.address { font-family: monospace; font-size: 20px; font-weight: 500; }
.mono { font-family: monospace; font-size: 12px; color: #8F8573; }
.readout { font-family: monospace; font-size: 12px; color: #8F8573; }
.readout.live { color: #FF9E1B; }
.alert { color: #E04F3E; }
.link-text { font-size: 15px; font-weight: 400; text-decoration: underline; }
button { min-height: 28px; min-width: 0; padding: 4px 12px; border-radius: 6px; box-shadow: none; background: none;
         background-image: none; color: #E6DEC8; border: 1px solid #6B6256; font-size: 14px; font-weight: 400; }
button:hover { background: #171512; }
button:active { background: #332E28; }
button:focus-visible { outline: 2px solid #FF9E1B; outline-offset: 2px; }
button:disabled { color: #8F8573; border-color: #332E28; }
button.primary { min-height: 32px; background: #E6DEC8; color: #0D0C0A; border: none; font-size: 15px; font-weight: 700; }
button.primary:hover { background: #F4EDDA; }
button.primary:active { background: #CFC7B2; }
button.plain { border: none; }
button.sidebar-item { min-height: 36px; padding: 4px 8px; border: none; border-radius: 6px; }
button.sidebar-item:disabled { color: #E6DEC8; border: none; }
button.sidebar-item.selected, button.plain.selected { background: #171512; }
.mic-item { min-height: 36px; padding: 4px 8px; }
button.row { padding: 8px 0; border: none; border-radius: 0; }
button.row:hover, button.row:active { background: none; }
button.row:hover .link-text, button.row:hover .row-title { color: #FFFFFF; }
button.row:active .link-text, button.row:active .row-title { color: #FF9E1B; }
entry { min-height: 44px; background: #171512; color: #E6DEC8; border: 1px solid #6B6256; border-radius: 6px; }
switch { min-width: 44px; min-height: 22px; background: none; border: 1px solid #6B6256; border-radius: 12px; }
/* Sized here because some themes, like Breeze, draw the knob bigger than the track. */
switch slider { min-width: 18px; min-height: 18px; margin: 2px; background: #8F8573; border: none; border-radius: 9px; box-shadow: none; }
switch:checked { background: #E6DEC8; border-color: #E6DEC8; }
switch:checked slider { background: #0D0C0A; }
switch image { color: transparent; }
switch:focus-visible { outline: 2px solid #FF9E1B; outline-offset: 2px; }
"""


class PadArea(Gtk.DrawingArea):
    """The same drawing as android/PadView.java, from the same 560 x 348 coordinates."""
    WIDTH, HEIGHT = 560, 348
    ART_X, ART_Y, ART_WIDTH, ART_HEIGHT = 18, 3, 524, 338

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.buttons = 0
        self.axes = [0] * 8
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Controller'])
        self.set_draw_func(self.draw)

    def set_state(self, buttons, axes):
        if buttons != self.buttons or axes != self.axes:
            self.buttons, self.axes = buttons, axes
            self.queue_draw()

    def on(self, bit):
        return bool(self.buttons >> bit & 1)

    def shape(self, cr, ground, line):
        cr.set_source_rgb(*ground)
        cr.fill_preserve()
        cr.set_source_rgb(*line)
        cr.stroke()

    def circle(self, cr, x, y, radius, ground, line):
        cr.new_sub_path()
        cr.arc(x, y, radius, 0, 2 * math.pi)
        self.shape(cr, ground, line)

    def rounded(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    def label(self, cr, text, x, y, size, color, bold=False):
        cr.select_font_face('Roboto', 0, 1 if bold else 0)
        cr.set_font_size(size)
        extents = cr.text_extents(text)
        cr.set_source_rgb(*color)
        cr.move_to(x - extents.width / 2 - extents.x_bearing, y - extents.height / 2 - extents.y_bearing)
        cr.show_text(text)

    def path(self, cr, start, curves):
        cr.move_to(*start)
        for curve in curves:
            cr.curve_to(*curve) if len(curve) == 6 else cr.line_to(*curve)
        cr.close_path()

    def trigger(self, cr, x, amount, label_x, name):
        self.label(cr, name, label_x, 17, 12, MUTED)
        self.rounded(cr, x, 4, 84, 26, 8)
        self.shape(cr, SURFACE, ACTIVE if amount > 0 else IDLE)
        if amount > 0:
            self.rounded(cr, x + 4, 8, max(76 * amount, 10), 18, 5)
            cr.set_source_rgb(*ACTIVE)
            cr.fill()

    def stick(self, cr, bit, x, y, dx, dy):
        dx = 0 if abs(dx) < DEAD_ZONE else dx
        dy = 0 if abs(dy) < DEAD_ZONE else dy
        active = dx != 0 or dy != 0 or self.on(bit)
        line = ACTIVE if active else IDLE
        self.circle(cr, x, y, 36, BACKGROUND, line)
        if self.on(bit):
            cr.set_line_width(6)
            cr.arc(x, y, 36, 0, 2 * math.pi)
            cr.set_source_rgb(*ACTIVE)
            cr.stroke()
            cr.set_line_width(2)
        self.circle(cr, x + dx * 14, y + dy * 14, 20, ACTIVE if active else THUMB, line)

    def face(self, cr, bit, x, y, name):
        on = self.on(bit)
        self.circle(cr, x, y, 18, ACTIVE if on else BACKGROUND, ACTIVE if on else IDLE)
        self.label(cr, name, x, y, 14, BACKGROUND if on else TEXT, True)

    def pill(self, cr, bit, x, y):
        on = self.on(bit)
        self.rounded(cr, x, y, 30, 16, 8)
        self.shape(cr, ACTIVE if on else BACKGROUND, ACTIVE if on else IDLE)

    def draw(self, _area, cr, width, height, _data=None):
        cr.set_source_rgb(*BACKGROUND)
        cr.paint()
        scale = min((width - 16) / self.ART_WIDTH, (height - 16) / self.ART_HEIGHT)
        cr.translate((width - self.ART_WIDTH * scale) / 2 - self.ART_X * scale,
                     (height - self.ART_HEIGHT * scale) / 2 - self.ART_Y * scale)
        cr.scale(scale, scale)
        cr.set_line_width(2)
        cr.set_line_join(1)
        axes = self.axes

        self.trigger(cr, 112, axes[4] / TRIGGER_MAX, 96, 'L2')
        self.trigger(cr, 364, axes[5] / TRIGGER_MAX, 464, 'R2')

        for bit, side in ((L1, 1), (R1, -1)):
            origin = 0 if side == 1 else self.WIDTH
            self.path(cr, (origin + side * 70, 100), [
                (origin + side * 80, 62, origin + side * 118, 36, origin + side * 170, 36),
                (origin + side * 190, 36, origin + side * 208, 38, origin + side * 222, 42),
                (origin + side * 222, 70),
            ])
            self.shape(cr, ACTIVE if self.on(bit) else SURFACE, ACTIVE if self.on(bit) else IDLE)

        self.path(cr, (280, 62), [
            (320, 62, 360, 58, 400, 56), (440, 54, 478, 70, 492, 104), (512, 152, 532, 222, 540, 272),
            (546, 312, 528, 340, 498, 340), (470, 340, 452, 320, 436, 296), (420, 272, 402, 256, 376, 256),
            (184, 256), (158, 256, 140, 272, 124, 296), (108, 320, 90, 340, 62, 340),
            (32, 340, 14, 312, 20, 272), (28, 222, 48, 152, 68, 104), (82, 70, 120, 54, 160, 56),
            (200, 58, 240, 62, 280, 62),
        ])
        self.shape(cr, SURFACE, IDLE)
        self.label(cr, 'L1', 150, 47, 11, BACKGROUND if self.on(L1) else TEXT, True)
        self.label(cr, 'R1', 410, 47, 11, BACKGROUND if self.on(R1) else TEXT, True)

        self.stick(cr, L3, 150, 128, axes[0] / STICK_MAX, axes[1] / STICK_MAX)
        self.stick(cr, R3, 348, 208, axes[2] / STICK_MAX, axes[3] / STICK_MAX)

        cr.move_to(199, 172)
        for dx, dy in ((26, 0), (0, 23), (23, 0), (0, 26), (-23, 0), (0, 23), (-26, 0), (0, -23), (-23, 0), (0, -26), (23, 0)):
            cr.rel_line_to(dx, dy)
        cr.close_path()
        self.shape(cr, BACKGROUND, IDLE)
        cr.set_source_rgb(*ACTIVE)
        for x, y, lit in ((200, 173, axes[7] < 0), (200, 219, axes[7] > 0), (177, 196, axes[6] < 0), (223, 196, axes[6] > 0)):
            if lit:
                cr.rectangle(x, y, 24, 24)
                cr.fill()

        self.pill(cr, SELECT, 216, 120)
        self.label(cr, 'select', 231, 148, 10, MUTED)
        self.pill(cr, START, 314, 120)
        self.label(cr, 'start', 329, 148, 10, MUTED)
        self.circle(cr, 280, 100, 15, BACKGROUND, IDLE)

        self.face(cr, Y, 410, 94, 'Y')
        self.face(cr, X, 376, 128, 'X')
        self.face(cr, B, 444, 128, 'B')
        self.face(cr, A, 410, 162, 'A')


def describe_input(buttons, axes):
    parts = [name for bit, name in enumerate(BUTTON_NAMES) if buttons >> bit & 1]
    for value, name in ((axes[4], 'L2'), (axes[5], 'R2')):
        if value > 0:
            parts.append(f'{name} {value * 100 // TRIGGER_MAX}%')
    for value, name in ((axes[6] < 0, 'Left'), (axes[6] > 0, 'Right'), (axes[7] < 0, 'Up'), (axes[7] > 0, 'Down')):
        if value:
            parts.append(name)
    for value, name in zip(axes[:4], ('LX', 'LY', 'RX', 'RY')):
        if abs(value) / STICK_MAX >= DEAD_ZONE:
            parts.append(f'{name} {value / STICK_MAX:.2f}')
    return ' · '.join(parts)


def text(value, css=None, wrap=False, ellipsize=False):
    label = Gtk.Label(label=value, xalign=0, use_markup=False)
    if css:
        label.add_css_class(css)
    if wrap:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Larger text fits fewer characters in the same panel.
        label.set_max_width_chars(14 if css == 'title' else 20)
    if ellipsize:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(15)
    return label


def button(value, on_click, css=None):
    widget = Gtk.Button(label=value)
    if css:
        widget.add_css_class(css)
    widget.connect('clicked', lambda _: on_click())
    return widget


def column(spacing=2, css=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    if css:
        box.add_css_class(css)
    return box


def spacer():
    return Gtk.Box(vexpand=True)


def pair(first, second):
    row = Gtk.Box(spacing=8, homogeneous=True)
    row.append(first)
    row.append(second)
    return row


def block(caption, value, detail=None, detail_css='detail', divided=False):
    box = column(css='divider' if divided else None)
    box.append(text(caption, 'caption', ellipsize=True))
    box.append(text(value, 'value', wrap=True))
    if detail is not None:
        box.append(text(detail, detail_css, wrap=True))
    return box


class CordSource(Gtk.DrawingArea):
    """The 12px phone dot at the top of the 24px cord column, with the 2px line below it."""

    def __init__(self):
        super().__init__(vexpand=True)
        self.set_size_request(24, -1)
        self.dot = 'idle'
        self.line_active = False
        self.set_draw_func(self.draw)

    def set_state(self, dot, line_active):
        if (dot, line_active) != (self.dot, self.line_active):
            self.dot, self.line_active = dot, line_active
            self.queue_draw()

    def draw(self, _area, cr, _width, height, _data=None):
        cx, cy = 12.0, 10.0
        cr.set_line_width(2)
        cr.set_source_rgb(*(ACTIVE if self.line_active else BORDER))
        cr.move_to(cx, cy + 6)
        cr.line_to(cx, height)
        cr.stroke()
        if self.dot == 'active':
            cr.arc(cx, cy, 6, 0, 2 * math.pi)
            cr.set_source_rgb(*ACTIVE)
            cr.fill()
        else:
            cr.arc(cx, cy, 5, 0, 2 * math.pi)
            cr.set_source_rgb(*BACKGROUND)
            cr.fill_preserve()
            if self.dot == 'alert':
                cr.set_source_rgb(*ALERT)
            else:
                cr.set_dash([3, 2])
                cr.set_source_rgb(*IDLE)
            cr.stroke()


class CordNode(Gtk.DrawingArea):
    """A 22px feature node with a 12px flow arrow in the 24px cord column."""

    def __init__(self, direction, has_line_below=True, state='idle'):
        super().__init__()
        self.set_size_request(24, 44)
        self.direction = direction
        self.has_line_below = has_line_below
        self.state = state
        self.line_above = False
        self.line_below = False
        self.set_draw_func(self.draw)

    def set_state(self, state, line_above, line_below=False):
        if (state, line_above, line_below) != (self.state, self.line_above, self.line_below):
            self.state, self.line_above, self.line_below = state, line_above, line_below
            self.queue_draw()

    def draw(self, _area, cr, _width, height, _data=None):
        cx, cy = 12.0, height / 2.0
        cr.set_line_width(2)
        cr.set_source_rgb(*(ACTIVE if self.line_above else BORDER))
        cr.move_to(cx, 0)
        cr.line_to(cx, cy - 11)
        cr.stroke()
        if self.has_line_below:
            cr.set_source_rgb(*(ACTIVE if self.line_below else BORDER))
            cr.move_to(cx, cy + 11)
            cr.line_to(cx, height)
            cr.stroke()
        if self.state == 'live':
            cr.arc(cx, cy, 11, 0, 2 * math.pi)
            cr.set_source_rgb(*ACTIVE)
            cr.fill()
            arrow = BACKGROUND
        else:
            cr.arc(cx, cy, 10, 0, 2 * math.pi)
            cr.set_source_rgb(*BACKGROUND)
            cr.fill_preserve()
            if self.state == 'idle':
                cr.set_source_rgb(*IDLE)
                arrow = MUTED
            else:
                cr.set_dash([3, 2])
                cr.set_source_rgb(*LATER)
                arrow = IDLE
            cr.stroke()
            cr.set_dash([])
        sign = 1 if self.direction == 'down' else -1
        cr.set_line_width(1.8)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.set_source_rgb(*arrow)
        cr.move_to(cx, cy - 5 * sign)
        cr.line_to(cx, cy + 5 * sign)
        cr.stroke()
        cr.move_to(cx - 4, cy + sign)
        cr.line_to(cx, cy + 5 * sign)
        cr.line_to(cx + 4, cy + sign)
        cr.stroke()


class PlayerDots(Gtk.DrawingArea):
    """Four 8px dots showing connected players in the Controllers sidebar item."""

    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER)
        self.set_size_request(44, 10)
        self.players = 0
        self.set_draw_func(self.draw)

    def set_players(self, players):
        if players != self.players:
            self.players = players
            self.queue_draw()

    def draw(self, _area, cr, _width, height, _data=None):
        cy = height / 2.0
        for i in range(4):
            cr.arc(i * 12 + 4, cy, 4, 0, 2 * math.pi)
            cr.set_source_rgb(*(ACTIVE if i < self.players else BORDER))
            cr.fill()


class App(Gtk.Application):
    def __init__(self, hidden):
        super().__init__(application_id='io.github.multex.musubi')
        self.start_hidden = hidden
        self.window = None
        self.process = None
        self.partial = b''
        self.stopping = False
        self.status = {'phone': None, 'pad': False, 'pad_error': None, 'pending': []}
        self.error = None
        self.stopped = False
        self.config = system.load_config()
        self.ignored = set()
        self.tray = None
        self.has_tray = False
        self.panel_key = None
        self.phone_detail = None
        self.readout = None
        self.blocked = set()
        self.firewall_dismissed = False
        self.firewall_message = None
        self.autostart_message = None
        self.autostart_lock = threading.Lock()
        self.renaming = None
        self.selected_item = 'sound'
        self.source_cord = None
        self.header_name = None
        self.header_status = None
        self.items_box = None
        self.ctrl_node = None
        self.ctrl_btn = None
        self.ctrl_dots = None
        self.ctrl_caption = None
        self.sound_node = None
        self.sound_btn = None
        self.sound_caption = None
        self.mic_node = None
        self.settings_btn = None
        self.stop_btn = None
        self.pad = None
        self.pages = None
        self.panel = None
        self.connected_panel = None
        self.sound_panel = None
        self.local_ip, self.network = None, None

    def do_activate(self):
        if self.window is not None:
            self.show_window()
            return
        self.build_window()
        self.has_tray = tray_available()
        if self.has_tray:
            self.tray = Tray(self.show_window, self.quit_app)
        self.local_ip, self.network = system.read_network()
        self.start_receiver()
        GLib.timeout_add_seconds(FIREWALL_CHECK_SECONDS, self.check_firewall)
        self.refresh()
        if not (self.start_hidden and self.has_tray):
            self.show_window()

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        # GTK's automatic mode spaces small text unevenly on non-HiDPI screens. Older GTK has no setting for it.
        if hasattr(Gtk, 'FontRendering'):
            Gtk.Settings.get_default().set_property('gtk-font-rendering', Gtk.FontRendering.MANUAL)
        # Above the user's gtk.css: its button and switch rules would otherwise break this palette.
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    def build_sidebar(self):
        sidebar = column(0, 'sidebar')
        sidebar.set_size_request(SIDEBAR_WIDTH, -1)
        sidebar.set_hexpand(False)

        header_row = Gtk.Box(spacing=6)
        self.source_cord = CordSource()
        header_row.append(self.source_cord)
        header_labels = column(2)
        header_labels.set_hexpand(True)
        header_labels.set_margin_bottom(10)
        self.header_name = text('No phone yet', 'value', ellipsize=True)
        self.header_status = text('Waiting', 'mono', ellipsize=True)
        self.header_status.set_single_line_mode(True)
        self.header_status.set_width_chars(16)
        self.header_status.set_max_width_chars(16)
        header_labels.append(self.header_name)
        header_labels.append(self.header_status)
        header_row.append(header_labels)
        sidebar.append(header_row)

        self.items_box = column(0)

        ctrl_row = Gtk.Box(spacing=6)
        self.ctrl_node = CordNode('down', has_line_below=True)
        ctrl_row.append(self.ctrl_node)
        self.ctrl_btn = Gtk.Button(hexpand=True)
        self.ctrl_btn.add_css_class('plain')
        self.ctrl_btn.add_css_class('sidebar-item')
        ctrl_box = column(1)
        ctrl_box.set_valign(Gtk.Align.CENTER)
        ctrl_top = Gtk.Box(spacing=6)
        ctrl_title = text('Controllers', 'body', ellipsize=True)
        ctrl_title.set_hexpand(True)
        self.ctrl_dots = PlayerDots()
        ctrl_top.append(ctrl_title)
        ctrl_top.append(self.ctrl_dots)
        self.ctrl_caption = text('Needs a phone', 'caption', ellipsize=True)
        ctrl_box.append(ctrl_top)
        ctrl_box.append(self.ctrl_caption)
        self.ctrl_btn.set_child(ctrl_box)
        self.ctrl_btn.connect('clicked', lambda _: self.select_item('controllers'))
        ctrl_row.append(self.ctrl_btn)
        self.items_box.append(ctrl_row)

        sound_row = Gtk.Box(spacing=6)
        self.sound_node = CordNode('up', has_line_below=True)
        sound_row.append(self.sound_node)
        self.sound_btn = Gtk.Button(hexpand=True)
        self.sound_btn.add_css_class('plain')
        self.sound_btn.add_css_class('sidebar-item')
        sound_box = column(1)
        sound_box.set_valign(Gtk.Align.CENTER)
        sound_box.append(text('Sound on phone', 'body', ellipsize=True))
        self.sound_caption = text('Needs a phone', 'caption', ellipsize=True)
        sound_box.append(self.sound_caption)
        self.sound_btn.set_child(sound_box)
        self.sound_btn.connect('clicked', lambda _: self.select_item('sound'))
        sound_row.append(self.sound_btn)
        self.items_box.append(sound_row)

        mic_row = Gtk.Box(spacing=6)
        self.mic_node = CordNode('down', has_line_below=False, state='later')
        mic_row.append(self.mic_node)
        mic_box = column(1, 'mic-item')
        mic_box.set_valign(Gtk.Align.CENTER)
        mic_box.set_hexpand(True)
        mic_title = text('Mic', 'body', ellipsize=True)
        mic_title.add_css_class('muted')
        mic_box.append(mic_title)
        mic_box.append(text('Coming later', 'caption', ellipsize=True))
        mic_row.append(mic_box)
        self.items_box.append(mic_row)

        sidebar.append(self.items_box)
        sidebar.append(spacer())

        bottom = column(2)
        self.settings_btn = Gtk.Button(child=text('Settings', 'body', ellipsize=True))
        self.settings_btn.add_css_class('plain')
        self.settings_btn.connect(
            'clicked',
            lambda _: self.go_main() if self.pages.get_visible_child_name() == 'settings' else self.open_settings(),
        )
        bottom.append(self.settings_btn)
        self.stop_btn = Gtk.Button(child=text('Stop Musubi', 'body', ellipsize=True))
        self.stop_btn.add_css_class('plain')
        self.stop_btn.connect('clicked', lambda _: self.on_stop())
        bottom.append(self.stop_btn)
        sidebar.append(bottom)
        return sidebar

    def build_window(self):
        # A fixed size also makes tiling window managers float it, instead of stretching it across the screen.
        self.window = Gtk.ApplicationWindow(application=self, title='Musubi', default_width=WINDOW_WIDTH,
                                            default_height=WINDOW_HEIGHT, resizable=False)
        self.window.set_size_request(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.window.connect('close-request', self.on_close)
        self.window.add_css_class('root')

        shell = Gtk.Box()
        shell.append(self.build_sidebar())

        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE, hexpand=True, vexpand=True,
                               hhomogeneous=False, vhomogeneous=False)
        self.pages.add_css_class('page')

        controllers = Gtk.Box(spacing=24)
        self.pad = PadArea()
        controllers.append(self.pad)
        # A fixed width keeps the drawing from jumping when the panel's text changes.
        connected_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=False)
        connected_scroller.set_size_request(PANEL_WIDTH, -1)
        self.connected_panel = column(css='side')
        connected_scroller.set_child(self.connected_panel)
        controllers.append(connected_scroller)
        self.pages.add_named(controllers, 'controllers')

        sound_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.sound_panel = column(8)
        sound_scroller.set_child(self.sound_panel)
        self.pages.add_named(sound_scroller, 'sound')

        state_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.panel = column()
        state_scroller.set_child(self.panel)
        self.pages.add_named(state_scroller, 'state')

        shell.append(self.pages)
        self.window.set_child(shell)

    def show_window(self):
        self.window.present()
        self.send({'watch': True})

    def on_close(self, _window):
        if self.has_tray and self.config['tray']:
            self.window.set_visible(False)
            self.send({'watch': False})
            return True
        self.quit_app()
        return True

    def quit_app(self):
        self.stop_receiver()
        self.quit()

    def start_receiver(self):
        self.error = None
        self.stopped = False
        self.stopping = False
        self.partial = b''
        try:
            self.process = subprocess.Popen([sys.executable, str(RECEIVER), '--service'], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, bufsize=0)
        except OSError as ex:
            self.error = str(ex)
            return
        GLib.io_add_watch(self.process.stdout.fileno(), GLib.PRIORITY_DEFAULT,
                          GLib.IOCondition.IN | GLib.IOCondition.HUP, self.on_receiver_output, self.process)
        self.send_allowed()
        if self.window is not None and self.window.get_visible():
            self.send({'watch': True})

    def stop_receiver(self):
        process, self.process = self.process, None
        self.stopping = True
        if process:
            # Closing stdin makes the receiver release the pad and exit by itself.
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        self.status = {'phone': None, 'pad': False, 'pad_error': None, 'pending': []}

    def on_receiver_output(self, fd, condition, process):
        if process is not self.process:
            return False
        if condition & GLib.IOCondition.IN:
            data = os.read(fd, 65536)
            if data:
                self.partial += data
                while b'\n' in self.partial:
                    line, self.partial = self.partial.split(b'\n', 1)
                    self.on_event(json.loads(line))
                return True
        self.process = None
        if not self.stopping and self.error is None:
            self.error = 'The receiver stopped unexpectedly.'
            self.status = {'phone': None, 'pad': False, 'pad_error': None, 'pending': []}
            self.refresh()
        return False

    def send(self, command):
        if self.process is None:
            return
        try:
            self.process.stdin.write((json.dumps(command) + '\n').encode())
        except OSError:
            pass

    def send_allowed(self):
        self.send({'allow': list(self.config['phones'])})

    def on_event(self, event):
        kind = event['event']
        if kind == 'input':
            self.pad.set_state(event['buttons'], event['axes'])
            if self.readout is not None:
                description = describe_input(event['buttons'], event['axes'])
                self.readout.set_label(description or 'Press a button to test')
                self.readout.remove_css_class('live')
                if description:
                    self.readout.add_css_class('live')
        elif kind == 'error':
            self.error = event['message']
            self.refresh()
        elif kind == 'status':
            self.status = {key: event.get(key) for key in ('phone', 'pad', 'pad_error', 'pending', 'cable_error')}
            self.refresh()

    def phone_name(self, phone):
        return self.config['phones'].get(phone['ip']) or phone['name'] or phone['ip']

    def pending_phones(self):
        return [phone for phone in self.status['pending'] if phone['ip'] not in self.ignored]

    def ignored_phones(self):
        return [phone for phone in self.status['pending'] if phone['ip'] in self.ignored]

    def current_state(self, pending, firewall):
        if self.error:
            return 'error'
        if self.stopped:
            return 'stopped'
        if pending:
            return 'approve'
        if firewall:
            return 'firewall'
        if self.status['phone']:
            return 'connected'
        return 'waiting'

    def select_item(self, item):
        self.selected_item = item
        self.go_main()

    def update_selection(self):
        visible = self.pages.get_visible_child_name()
        in_settings = visible in ('settings', 'phones')
        for widget, active in (
            (self.ctrl_btn, not in_settings and self.selected_item == 'controllers'),
            (self.sound_btn, not in_settings and self.selected_item == 'sound'),
            (self.settings_btn, in_settings),
        ):
            if active:
                widget.add_css_class('selected')
            else:
                widget.remove_css_class('selected')

    def update_sidebar(self, state, phone, pending):
        headers = {
            'connected': ('active', phone and self.phone_name(phone), None, phone and self.link_line(phone)),
            'waiting': ('idle', 'No phone yet', 'muted', 'Waiting'),
            'firewall': ('alert', 'No phone yet', 'alert', 'Blocked by the firewall'),
            'approve': ('idle', 'New phone asking', 'muted', pending and f"Wi-Fi · {pending[0]['ip']}"),
            'error': ('alert', "Musubi can't run", 'alert', 'Not listening'),
            'stopped': ('idle', 'Musubi is stopped', 'muted', 'Not listening'),
        }
        dot, name, color_css, status_text = headers[state]
        self.header_name.set_label(name)
        self.header_name.remove_css_class('muted')
        self.header_name.remove_css_class('alert')
        if color_css:
            self.header_name.add_css_class(color_css)
        self.header_status.set_label(status_text)

        has_phone = state == 'connected'
        controller_on = bool(has_phone and (phone['controller'] is not None or self.status['pad']))
        sound_on = bool(has_phone and phone.get('audio'))
        players = 1 if controller_on else 0

        seg1 = has_phone and (controller_on or sound_on)
        seg2 = has_phone and sound_on

        self.source_cord.set_state(dot, seg1)
        self.ctrl_node.set_state('live' if controller_on else 'idle', seg1, seg2)
        self.sound_node.set_state('live' if sound_on else 'idle', seg2, False)
        self.mic_node.set_state('later', False, False)

        self.ctrl_dots.set_players(players)
        self.ctrl_caption.set_label(f'{players} of 4 players' if has_phone else 'Needs a phone')
        self.sound_caption.set_label(('Playing' if sound_on else 'Off') if has_phone else 'Needs a phone')

        self.items_box.set_opacity(1.0 if has_phone else 0.45)
        self.ctrl_btn.set_sensitive(has_phone)
        self.sound_btn.set_sensitive(has_phone)
        self.stop_btn.set_visible(not self.error and not self.stopped)
        self.update_selection()

    def refresh(self):
        phone = self.status['phone']
        if not self.status['pad']:
            self.pad.set_state(0, [0] * 8)
        pending = self.pending_phones()
        firewall = bool(self.blocked) and not self.firewall_dismissed and phone is None
        state = self.current_state(pending, firewall)
        key = (state, self.error, self.stopped, json.dumps(pending), json.dumps(self.ignored_phones()),
               firewall, self.firewall_message, self.status['pad'], self.status['pad_error'],
               self.status.get('cable_error'), self.local_ip,
               phone and (phone['ip'], self.phone_name(phone), phone['controller'],
                          phone['controller_known'], phone.get('audio')))
        if key != self.panel_key:
            self.panel_key = key
            self.build_panel(state, pending)
        self.update_sidebar(state, phone, pending)
        if self.phone_detail is not None and phone:
            self.phone_detail.set_label(self.phone_line(phone))
        if self.tray:
            self.tray.set_status(self.tray_text())

    def tray_text(self):
        phone = self.status['phone']
        if self.error:
            return 'Musubi: error'
        if self.stopped:
            return 'Musubi: stopped'
        if self.pending_phones():
            return 'Musubi: a new phone is asking'
        return f'Musubi: {self.phone_name(phone)}' if phone else 'Musubi: waiting for a phone'

    @staticmethod
    def link_line(phone):
        link = 'USB cable' if phone['ip'] == 'usb' else 'Wi-Fi'
        return f"{link} · {phone['rtt']} ms" if phone['rtt'] is not None else link

    @staticmethod
    def phone_line(phone):
        # The address gets its own line so a growing ping never rewraps the panel.
        return App.link_line(phone) if phone['ip'] == 'usb' else f"{App.link_line(phone)}\n{phone['ip']}"

    @staticmethod
    def clear_box(box):
        while (child := box.get_first_child()) is not None:
            box.remove(child)

    def clear_panel(self):
        self.phone_detail = None
        self.readout = None
        self.clear_box(self.panel)
        self.clear_box(self.connected_panel)
        self.clear_box(self.sound_panel)

    def main_page_name(self):
        pending = self.pending_phones()
        firewall = bool(self.blocked) and not self.firewall_dismissed and self.status['phone'] is None
        return self.selected_item if self.current_state(pending, firewall) == 'connected' else 'state'

    def build_panel(self, state, pending):
        self.clear_panel()
        in_settings = self.pages.get_visible_child_name() in ('settings', 'phones')
        if state == 'error':
            self.panel_error()
        elif state == 'stopped':
            self.panel_stopped()
        elif state == 'approve':
            self.panel_approve(pending[0])
            in_settings = False
            self.show_window()
        elif state == 'firewall':
            self.panel_firewall()
        elif state == 'connected':
            phone = self.status['phone']
            self.panel_connected(phone)
            self.panel_sound(phone)
        else:
            self.panel_waiting()
        if not in_settings:
            self.pages.set_visible_child_name(self.selected_item if state == 'connected' else 'state')

    def panel_sound(self, phone):
        self.sound_panel.append(text('Sound on phone', 'page-title'))
        if phone.get('audio'):
            self.sound_panel.append(block('Status', 'Playing', f'Sending PC sound to {self.phone_name(phone)}', divided=True))
        else:
            self.sound_panel.append(block('Status', 'Off', 'Turn on Play PC sound in Musubi on your phone.', divided=True))

    def panel_error(self):
        heading = text("Musubi can't run", 'title', wrap=True)
        heading.add_css_class('alert')
        self.panel.append(heading)
        message = self.error
        if 'in use' in message:
            message = 'Another program is using UDP port 9999. It may be musubi.py running in a terminal.'
        self.panel.append(text(message, 'body', wrap=True))
        self.panel.append(spacer())
        self.panel.append(button('Try again', self.on_start, 'primary'))

    def panel_stopped(self):
        self.panel.append(spacer())
        for line in (text('Musubi is stopped', 'title', wrap=True),
                     text("Phones can't connect until you start it again.", 'body', wrap=True)):
            line.set_xalign(0.5)
            line.set_justify(Gtk.Justification.CENTER)
            self.panel.append(line)
        self.panel.append(spacer())
        self.panel.set_spacing(8)
        self.panel.append(button('Start', self.on_start, 'primary'))

    def panel_waiting(self):
        self.panel.append(text('Waiting for a phone', 'title', wrap=True))
        if self.local_ip:
            self.panel.append(text("Open Musubi on your phone and enter this PC's address:", 'body', wrap=True))
            self.panel.append(text(self.local_ip, 'address'))
        else:
            self.panel.append(text('This PC has no local network right now. Connect it to the same network as your phone.',
                                   'body', wrap=True))
        if self.status.get('cable_error'):
            usb = block('USB cable', "Can't use it", self.status['cable_error'], divided=True)
            usb.get_last_child().add_css_class('alert')
            self.panel.append(usb)
        else:
            self.panel.append(block('USB cable', 'Plug in your phone', 'Set its USB mode to File transfer', divided=True))
        self.panel.append(block('Virtual controller', 'Off', 'Turns on when a controller connects', divided=True))
        ignored = self.ignored_phones()
        if ignored:
            phone = ignored[0]
            self.panel.append(block('Ignored phone', phone['name'] or 'Unknown model', f"Wi-Fi · {phone['ip']}", 'mono', divided=True))
            self.panel.append(button('Allow', lambda: self.allow(phone)))

    def panel_connected(self, phone):
        details = block('Phone', self.phone_name(phone), self.phone_line(phone), 'mono')
        self.phone_detail = details.get_last_child()
        self.connected_panel.append(details)
        if not phone['controller_known']:
            controller = block('Controller', 'Unknown', 'Update the phone app to see it here', divided=True)
        elif phone['controller'] is not None:
            controller = block('Controller', phone['controller'] or 'Connected', 'Connected to the phone', divided=True)
        else:
            controller = block('Controller', 'Not connected', "Turn it on, or pair it in the phone's Bluetooth settings.",
                               divided=True)
        self.connected_panel.append(controller)
        if self.status['pad_error']:
            virtual = block('Virtual controller', "Can't turn on", self.status['pad_error'], divided=True)
            virtual.get_last_child().add_css_class('alert')
        elif self.status['pad']:
            virtual = block('Virtual controller', 'Live', 'Games see an Xbox 360 controller', divided=True)
        else:
            virtual = block('Virtual controller', 'Off', 'Turns on when a controller connects', divided=True)
        self.connected_panel.append(virtual)
        if self.status['pad']:
            self.readout = text('Press a button to test', 'readout', wrap=True)
            self.readout.set_xalign(0.5)
            self.readout.set_justify(Gtk.Justification.CENTER)
            self.readout.set_vexpand(True)
            self.connected_panel.append(self.readout)

    def panel_approve(self, phone):
        self.panel.append(text('Allow this phone?', 'title', wrap=True))
        self.panel.append(block('New phone', phone['name'] or 'Unknown model', f"Wi-Fi · {phone['ip']}", 'mono', divided=True))
        self.panel.append(text('It will be able to press buttons in your games. Only allow phones you recognize.',
                               'body', wrap=True))
        self.panel.append(spacer())
        self.panel.set_spacing(8)
        self.panel.append(button('Allow', lambda: self.allow(phone), 'primary'))
        self.panel.append(button('Ignore', lambda: self.ignore(phone)))

    def panel_firewall(self):
        heading = text('Your firewall is blocking phones', 'title', wrap=True)
        heading.add_css_class('alert')
        self.panel.append(heading)
        self.panel.set_spacing(8)
        if self.network:
            self.panel.append(text(f'Musubi can open UDP port {system.PORT} for devices on your network. '
                                   'Your PC will ask for your password once.', 'body', wrap=True))
            self.panel.append(text(self.network, 'mono', wrap=True))
            self.panel.append(text('New phones still need your approval here.', 'detail', wrap=True))
        else:
            self.panel.append(text("Musubi can't tell which network to open. Check your connection.", 'body', wrap=True))
        self.append_firewall_message(self.panel)
        self.panel.append(spacer())
        if self.network:
            self.panel.append(button('Open the port', self.open_port, 'primary'))
        self.panel.append(button('Not now', self.dismiss_firewall))

    def append_firewall_message(self, parent):
        if self.firewall_message:
            message = text(self.firewall_message, 'detail', wrap=True)
            message.add_css_class('alert')
            parent.append(message)
            parent.append(button('Copy the command', self.copy_port_command))

    def allow(self, phone):
        self.ignored.discard(phone['ip'])
        self.config['phones'][phone['ip']] = phone['name'] or f"Phone at {phone['ip']}"
        self.save_config()
        self.send_allowed()
        self.refresh()

    def ignore(self, phone):
        self.ignored.add(phone['ip'])
        self.refresh()

    def save_config(self):
        try:
            system.save_config(self.config)
        except OSError as ex:
            self.firewall_message = None
            self.error = f"Couldn't save your settings: {ex}"

    def on_stop(self):
        self.stopped = True
        self.stop_receiver()
        self.pages.set_visible_child_name('state')
        self.refresh()

    def on_start(self):
        # A failed save leaves the receiver running, and a second one couldn't bind the port.
        self.stop_receiver()
        self.start_receiver()
        self.refresh()

    def dismiss_firewall(self):
        self.firewall_dismissed = True
        self.firewall_message = None
        self.refresh()

    def port_command(self):
        return ' '.join(['sudo', *system.open_port_command(self.network)])

    def copy_port_command(self):
        self.window.get_clipboard().set(self.port_command())

    def open_port(self):
        try:
            process = Gio.Subprocess.new(['pkexec', *system.open_port_command(self.network)],
                                         Gio.SubprocessFlags.STDERR_MERGE | Gio.SubprocessFlags.STDOUT_PIPE)
        except GLib.Error:
            self.on_open_port_failed()
            return
        process.communicate_utf8_async(None, None, self.on_port_opened)

    def on_port_opened(self, process, result):
        try:
            process.communicate_utf8_finish(result)
            worked = process.get_successful()
        except GLib.Error:
            worked = False
        if worked:
            self.blocked = set()
            # The log still holds the old blocks for a while, and they'd bring this screen back.
            self.firewall_dismissed = True
            self.firewall_message = None
            self.refresh()
            self.refresh_settings()
        else:
            self.on_open_port_failed()

    def on_open_port_failed(self):
        self.firewall_message = f"That didn't work. You can run this in a terminal instead: {self.port_command()}"
        self.refresh()
        self.refresh_settings()

    def check_firewall(self):
        if not self.window.get_visible():
            return GLib.SOURCE_CONTINUE
        if self.status['phone'] is None and not self.stopped and not self.error:
            try:
                process = Gio.Subprocess.new(['journalctl', '-k', '--no-pager', '-o', 'cat', f'--since={LOG_WINDOW}',
                                              '-g', f'UFW BLOCK.*DPT={system.PORT}'],
                                             Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
                process.communicate_utf8_async(None, None, self.on_firewall_log)
            except GLib.Error:
                pass  # Without the log, the Settings page still shows what the firewall rules say.
        return GLib.SOURCE_CONTINUE

    def on_firewall_log(self, process, result):
        try:
            _, output, _ = process.communicate_utf8_finish(result)
        except GLib.Error:
            return
        blocked = system.blocked_sources(output or '')
        if blocked != self.blocked:
            self.blocked = blocked
            self.refresh()

    def open_settings(self):
        self.refresh_settings()
        self.pages.set_visible_child_name('settings')
        self.update_selection()

    def go_main(self):
        self.pages.set_visible_child_name(self.main_page_name())
        self.update_selection()

    def page(self, name, title, on_back, body):
        old = self.pages.get_child_by_name(name)
        showing = old is not None and self.pages.get_visible_child() is old
        if old is not None:
            self.pages.remove(old)
        root = column(0)
        header = Gtk.Box(spacing=4)
        header.add_css_class('header')
        back = Gtk.Button(icon_name='go-previous-symbolic')
        back.add_css_class('plain')
        back.update_property([Gtk.AccessibleProperty.LABEL], ['Back'])
        back.connect('clicked', lambda _: on_back())
        header.append(back)
        header.append(text(title, 'page-title'))
        root.append(header)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(body)
        root.append(scroller)
        self.pages.add_named(root, name)
        if showing:
            self.pages.set_visible_child_name(name)

    def setting_switch(self, title, detail, active, on_change):
        row = Gtk.Box(spacing=12)
        labels = column()
        labels.set_hexpand(True)
        labels.append(text(title, 'row-title', wrap=True))
        labels.append(text(detail, 'caption', wrap=True))
        row.append(labels)
        switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
        switch.update_property([Gtk.AccessibleProperty.LABEL], [title])
        switch.connect('notify::active', lambda widget, _: on_change(widget.get_active()))
        row.append(switch)
        return row

    def refresh_settings(self):
        left = column(8, 'column')
        left.set_hexpand(True)
        left.add_css_class('column-divider')
        left.append(text('Startup', 'caption'))
        hidden_note = 'Opens hidden in the tray' if self.has_tray else 'Opens this window when you log in'
        left.append(self.setting_switch('Start with my PC', hidden_note, system.autostart_enabled(), self.on_autostart))
        if self.autostart_message:
            message = text(self.autostart_message, 'detail', wrap=True)
            message.add_css_class('alert')
            left.append(message)
        if self.has_tray:
            left.append(self.setting_switch('Keep running in the tray', 'Closing the window keeps Musubi on',
                                            self.config['tray'], self.on_tray_setting))
        divider = text('Firewall', 'caption')
        divider.add_css_class('divider')
        left.append(divider)
        self.append_firewall_status(left)

        right = column(8, 'column')
        right.set_hexpand(True)
        right.append(text('Phones', 'caption'))
        count = len(self.config['phones'])
        manage = Gtk.Button()
        manage.add_css_class('row')
        manage.update_property([Gtk.AccessibleProperty.LABEL], [f'Manage phones, {count} allowed'])
        manage_row = Gtk.Box(spacing=12)
        labels = column()
        labels.set_hexpand(True)
        labels.append(text('Manage phones', 'row-title', wrap=True))
        labels.append(text(f'{count} allowed', 'caption', wrap=True))
        manage_row.append(labels)
        manage_row.append(Gtk.Image(icon_name='go-next-symbolic'))
        manage.set_child(manage_row)
        manage.connect('clicked', lambda _: self.open_phones())
        right.append(manage)
        divider = text('About', 'caption')
        divider.add_css_class('divider')
        right.append(divider)
        right.append(self.link('Setup guide', f'{REPO}#setup'))
        right.append(self.link('Source code', REPO))
        right.append(spacer())
        right.append(text(f'Musubi {VERSION} · MIT license', 'mono', wrap=True))

        body = Gtk.Box(homogeneous=True)
        body.append(left)
        body.append(right)
        self.page('settings', 'Settings', self.go_main, body)

    def append_firewall_status(self, parent):
        kind, sources = system.firewall_status()
        if kind == 'none':
            parent.append(text("ufw isn't on", 'row-title', wrap=True))
            parent.append(text(f'Musubi has nothing to open. If you use another firewall, allow UDP port {system.PORT}.',
                               'caption', wrap=True))
            return
        if kind == 'unknown':
            parent.append(text("Can't read the firewall rules", 'row-title', wrap=True))
            parent.append(text(f'If phones can\'t connect, allow UDP port {system.PORT}.', 'caption', wrap=True))
            return
        if self.network is not None and system.covers(sources, self.network):
            parent.append(text('Port open', 'row-title', wrap=True))
            parent.append(text(f'UDP {system.PORT} · {self.network}', 'mono', wrap=True))
            return
        phones = len(sources)
        if phones:
            parent.append(text(f'Open for {phones} address{"" if phones == 1 else "es"}', 'row-title', wrap=True))
            parent.append(text('Those phones connect fine. Others on your network are blocked until you open it.',
                               'caption', wrap=True))
        else:
            parent.append(text('Port closed', 'row-title', wrap=True))
            parent.append(text(f'UDP {system.PORT} is blocked, so phones can\'t connect.', 'caption', wrap=True))
        if self.network:
            parent.append(button('Open for my network', self.open_port))
        self.append_firewall_message(parent)

    def on_autostart(self, enabled):
        # systemctl takes long enough to stall the switch's animation on the main thread.
        def work():
            with self.autostart_lock:
                error = system.set_autostart(enabled)
            GLib.idle_add(self.on_autostart_done, error)
        threading.Thread(target=work, daemon=True).start()

    def on_autostart_done(self, error):
        message = (f"That didn't work ({error}). Add this to your desktop's startup instead: "
                   f'{system.startup_command()}') if error else None
        if message != self.autostart_message:
            self.autostart_message = message
            self.refresh_settings()
        return GLib.SOURCE_REMOVE

    def on_tray_setting(self, enabled):
        self.config['tray'] = enabled
        self.save_config()

    def link(self, title, url):
        widget = Gtk.Button(child=text(title, 'link-text'))
        widget.add_css_class('row')
        widget.connect('clicked', lambda _: Gtk.UriLauncher(uri=url).launch(self.window, None, None))
        return widget

    def open_phones(self):
        self.refresh_phones()
        self.pages.set_visible_child_name('phones')

    def refresh_phones(self):
        body = column(0, 'column')
        if not self.config['phones']:
            body.append(text('No phones allowed yet', 'row-title', wrap=True))
            body.append(text('When a phone connects, Musubi asks you here first.', 'caption', wrap=True))
        active = self.status['phone'] and self.status['phone']['ip']
        for ip, name in self.config['phones'].items():
            row = Gtk.Box(spacing=12)
            row.set_margin_top(10)
            row.set_margin_bottom(10)
            labels = column()
            labels.set_hexpand(True)
            if self.renaming == ip:
                entry = Gtk.Entry(text=name)
                entry.update_property([Gtk.AccessibleProperty.LABEL], [f'New name for {name}'])
                entry.connect('activate', lambda widget, phone=ip: self.save_rename(phone, widget.get_text()))
                labels.append(entry)
                row.append(labels)
                row.append(button('Save', lambda phone=ip, widget=entry: self.save_rename(phone, widget.get_text())))
            else:
                labels.append(text(name, 'row-title', ellipsize=True))
                labels.append(text(f"{ip} · {'Connected' if ip == active else 'Allowed'}", 'mono', ellipsize=True))
                row.append(labels)
                row.append(button('Rename', lambda phone=ip: self.start_rename(phone)))
                row.append(button('Remove', lambda phone=ip: self.remove_phone(phone)))
            body.append(row)
        self.page('phones', 'Phones', self.open_settings, body)
        self.pages.set_visible_child_name('phones')

    def start_rename(self, ip):
        self.renaming = ip
        self.refresh_phones()

    def save_rename(self, ip, name):
        self.renaming = None
        name = name.strip()
        if name:
            self.config['phones'][ip] = name
            self.save_config()
        self.refresh_phones()
        self.refresh()

    def remove_phone(self, ip):
        del self.config['phones'][ip]
        # Removing should stop the phone, not immediately ask again while it's still sending.
        self.ignored.add(ip)
        self.save_config()
        self.send_allowed()
        self.refresh_phones()
        self.refresh()


def main():
    hidden = '--hidden' in sys.argv
    app = App(hidden)
    return app.run([sys.argv[0]])


if __name__ == '__main__':
    sys.exit(main())
