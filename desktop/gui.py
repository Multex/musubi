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
.divider { border-top: 1px solid #332E28; margin-top: 14px; padding-top: 12px; }
.caption { font-size: 12px; color: #8F8573; }
.tab-label { font-size: 11px; color: #8F8573; }
.player-tab { padding: 6px 8px; border-radius: 6px 6px 0 0; border-bottom: 2px solid transparent; }
.player-tab.selected { background: #171512; border-bottom: 2px solid #E6DEC8; }
.detail { font-size: 13px; color: #8F8573; }
.muted { color: #8F8573; }
.value { font-size: 16px; font-weight: 500; }
.row-title { font-size: 15px; font-weight: 500; }
.body { font-size: 14px; }
.title { font-size: 24px; font-weight: 700; }
.page-title { font-size: 18px; font-weight: 500; }
.address { font-family: monospace; font-size: 28px; font-weight: 500; }
.mono { font-family: monospace; font-size: 12px; color: #8F8573; }
.mono-subnet { font-family: monospace; font-size: 13px; color: #8F8573; }
.readout { font-family: monospace; font-size: 12px; color: #8F8573; }
.readout.live { color: #FF9E1B; }
.alert { color: #E04F3E; }
.link-text { font-size: 14px; font-weight: 400; text-decoration: underline; }
button { min-height: 26px; min-width: 0; padding: 4px 14px; border-radius: 6px; box-shadow: none; background: none;
         background-image: none; color: #E6DEC8; border: 1px solid #6B6256; font-size: 14px; font-weight: 400; }
button:hover { background: #171512; }
button:active { background: #332E28; }
button:focus-visible { outline: 2px solid #FF9E1B; outline-offset: 2px; }
button:disabled { color: #8F8573; border-color: #332E28; opacity: 0.5; }
button.primary { min-height: 32px; min-width: 112px; background: #E6DEC8; color: #0D0C0A; border: none;
                 font-size: 15px; font-weight: 700; }
button.primary:hover { background: #F4EDDA; }
button.primary:active { background: #CFC7B2; }
button.plain { border: none; }
button.back-btn { min-height: 28px; padding: 4px 6px; border: none; border-radius: 6px; }
button.sidebar-item { min-height: 36px; padding: 4px 8px; border: none; border-radius: 6px; }
button.sidebar-item:disabled { color: #E6DEC8; border: none; opacity: 1; }
button.sidebar-item.selected, button.plain.selected { background: #171512; }
.mic-item { min-height: 36px; padding: 4px 8px; }
button.overview-row { min-height: 68px; padding: 8px 0; border: none; border-radius: 6px; }
.overview-static { min-height: 68px; padding: 8px 0; }
.hairline { background: #332E28; min-height: 1px; }
button.row { padding: 6px 0; border: none; border-radius: 6px; }
button.row:hover, button.row:active { background: none; }
button.row:hover .link-text, button.row:hover .row-title { color: #F4EDDA; }
button.row:active .link-text, button.row:active .row-title { color: #FF9E1B; }
entry { min-height: 36px; background: #171512; color: #E6DEC8; border: 1px solid #6B6256; border-radius: 6px; }
switch { min-width: 44px; min-height: 22px; background: none; border: 1px solid #6B6256; border-radius: 12px; }
/* Sized here because some themes, like Breeze, draw the knob bigger than the track. */
switch slider { min-width: 18px; min-height: 18px; margin: 2px; background: #8F8573; border: none; border-radius: 9px; box-shadow: none; }
switch:checked { background: #E6DEC8; border-color: #E6DEC8; }
switch:checked slider { background: #0D0C0A; }
switch image { color: transparent; }
switch:focus-visible { outline: 2px solid #FF9E1B; outline-offset: 2px; }
"""


def _rumble_wave(x0, y0, x1, y1, sx, sy):
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    nx, ny = dy / length * 9.0, -dx / length * 9.0
    points = tuple(
        (x0 + dx * i / 9.0, y0 + dy * i / 9.0,
         (0.0 if i in (0, 9) else (nx if i % 2 else -nx)),
         (0.0 if i in (0, 9) else (ny if i % 2 else -ny)))
        for i in range(10)
    )
    return points[0], points[1:], sx, sy


class PadArea(Gtk.DrawingArea):
    """The same drawing as android/PadView.java, from the -20 0 600 348 viewBox."""
    WIDTH, HEIGHT = 600, 348
    PAD_WIDTH = 560
    ART_X, ART_Y, ART_WIDTH, ART_HEIGHT = -20, 0, 600, 348
    RUMBLE_WAVES = (
        _rumble_wave(172.0, 280.0, 130.0, 328.0, 1.1, 1.0),
        _rumble_wave(17.5, 201.0, 1.5, 281.0, 1.4, 0.3),
    )

    def __init__(self, width=None):
        compact = width is not None and width < 200
        super().__init__(hexpand=width is None, vexpand=not compact,
                         halign=Gtk.Align.FILL if width is None else Gtk.Align.CENTER,
                         valign=Gtk.Align.CENTER if compact else Gtk.Align.FILL)
        if width is not None:
            self.set_size_request(width, round(width * self.HEIGHT / self.WIDTH))
        self.compact = compact
        self.fixed_width = width is not None
        self.buttons = 0
        self.axes = [0] * 8
        self.strong = 0.0
        self.weak = 0.0
        self.left_level = 0.0
        self.right_level = 0.0
        self.left_fade = 0.0
        self.right_fade = 0.0
        self.clock_ms = 0.0
        self.last_tick_ms = 0.0
        self.tick_id = 0
        self.update_property([Gtk.AccessibleProperty.LABEL], ['Controller'])
        self.set_draw_func(self.draw)
        self.connect('map', self.on_map)
        self.connect('unmap', self.on_unmap)

    def set_state(self, buttons, axes):
        if buttons != self.buttons or axes != self.axes:
            self.buttons, self.axes = buttons, axes
            self.queue_draw()

    def set_rumble(self, strong, weak):
        strong = min(max(float(strong), 0.0), 1.0)
        weak = min(max(float(weak), 0.0), 1.0)
        if strong == self.strong and weak == self.weak:
            return
        self.strong, self.weak = strong, weak
        if not self.get_mapped():
            self.left_level = strong
            self.right_level = weak
            self.left_fade = 1.0 if strong > 0.0 else 0.0
            self.right_fade = 1.0 if weak > 0.0 else 0.0
            return
        if strong > 0.0:
            self.left_level = strong
        if weak > 0.0:
            self.right_level = weak
        self.sync_rumble()
        self.queue_draw()

    def on_map(self, _widget):
        self.sync_rumble()

    def on_unmap(self, _widget):
        if self.tick_id:
            self.remove_tick_callback(self.tick_id)
            self.tick_id = 0
        self.left_level = self.strong
        self.right_level = self.weak
        self.left_fade = 1.0 if self.strong > 0.0 else 0.0
        self.right_fade = 1.0 if self.weak > 0.0 else 0.0

    def sync_rumble(self):
        want = self.get_mapped() and (
            self.strong > 0.0 or self.weak > 0.0 or self.left_fade > 0.0 or self.right_fade > 0.0
        )
        if want and not self.tick_id:
            self.last_tick_ms = GLib.get_monotonic_time() / 1000.0
            self.clock_ms = self.last_tick_ms
            self.tick_id = self.add_tick_callback(self.on_tick)
        elif not want and self.tick_id:
            self.remove_tick_callback(self.tick_id)
            self.tick_id = 0

    def on_tick(self, _widget, clock):
        if not self.get_mapped():
            self.tick_id = 0
            return GLib.SOURCE_REMOVE
        now = clock.get_frame_time() / 1000.0
        dt = max(0.0, now - self.last_tick_ms) if self.last_tick_ms else 0.0
        self.last_tick_ms = now
        self.clock_ms = now
        if self.strong > 0.0:
            self.left_fade = min(1.0, self.left_fade + dt / 120.0)
        elif self.left_fade > 0.0:
            self.left_fade = max(0.0, self.left_fade - dt / 400.0)
            if self.left_fade == 0.0:
                self.left_level = 0.0
        if self.weak > 0.0:
            self.right_fade = min(1.0, self.right_fade + dt / 120.0)
        elif self.right_fade > 0.0:
            self.right_fade = max(0.0, self.right_fade - dt / 400.0)
            if self.right_fade == 0.0:
                self.right_level = 0.0
        self.queue_draw()
        if self.strong == 0.0 and self.weak == 0.0 and self.left_fade == 0.0 and self.right_fade == 0.0:
            self.tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

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

    def trigger(self, cr, x, amount, label_x, name, show_label=True):
        if show_label:
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

    def draw_waves(self, cr, level, fade, rising, period_ms, right):
        if level <= 0.0 or fade <= 0.0:
            return
        envelope = fade * (2.0 - fade) if rising else fade * fade
        alpha = min(1.0, level * 5.0) * envelope
        if alpha <= 0.0:
            return
        buzz = -1.0 if int(self.clock_ms // period_ms) & 1 else 1.0
        cr.set_source_rgba(ACTIVE[0], ACTIVE[1], ACTIVE[2], alpha)
        for (bx0, by0, ox0, oy0), rest, sx, sy in self.RUMBLE_WAVES:
            dx = buzz * sx
            dy = buzz * sy
            x = bx0 + level * ox0 + dx
            cr.move_to(self.PAD_WIDTH - x if right else x, by0 + level * oy0 + dy)
            for bx, by, ox, oy in rest:
                x = bx + level * ox + dx
                cr.line_to(self.PAD_WIDTH - x if right else x, by + level * oy + dy)
            cr.stroke()

    def draw(self, _area, cr, width, height, _data=None):
        if not self.compact:
            cr.set_source_rgb(*BACKGROUND)
            cr.paint()
        margin = 0 if self.fixed_width else 16
        scale = min((width - margin) / self.ART_WIDTH, (height - margin) / self.ART_HEIGHT)
        cr.translate((width - self.ART_WIDTH * scale) / 2 - self.ART_X * scale,
                     (height - self.ART_HEIGHT * scale) / 2 - self.ART_Y * scale)
        cr.scale(scale, scale)
        cr.set_line_width(2)
        cr.set_line_join(1)
        axes = self.axes

        self.trigger(cr, 112, axes[4] / TRIGGER_MAX, 96, 'L2', not self.compact)
        self.trigger(cr, 364, axes[5] / TRIGGER_MAX, 464, 'R2', not self.compact)

        for bit, side in ((L1, 1), (R1, -1)):
            origin = 0 if side == 1 else self.PAD_WIDTH
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

        if self.left_fade > 0.0 or self.right_fade > 0.0:
            cr.set_line_width(3)
            cr.set_line_join(0)
            cr.set_line_cap(1)
            self.draw_waves(cr, self.left_level, self.left_fade, self.strong > 0.0, 70.0, False)
            self.draw_waves(cr, self.right_level, self.right_fade, self.weak > 0.0, 40.0, True)


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
        label.set_max_width_chars(34 if css == 'title' else 58)
    if ellipsize:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(15)
    return label


def button(value, on_click, css=None, align=Gtk.Align.START):
    widget = Gtk.Button(label=value)
    if align is not None:
        widget.set_halign(align)
    if css:
        widget.add_css_class(css)
    if css == 'primary':
        widget.set_size_request(140, 40)
    else:
        widget.set_size_request(-1, 36)
    widget.connect('clicked', lambda _: on_click())
    return widget


def column(spacing=2, css=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    if css:
        box.add_css_class(css)
    return box


def spacer():
    return Gtk.Box(vexpand=True)


def hairline():
    line = Gtk.Box()
    line.add_css_class('hairline')
    return line


def block(caption, value, detail=None, detail_css='detail', divided=False):
    box = column(2, 'divider' if divided else None)
    box.append(text(caption, 'caption', ellipsize=True))
    box.append(text(value, 'value', wrap=True))
    if detail is not None:
        box.append(text(detail, detail_css, wrap=True))
    return box


class HeadphonesArea(Gtk.DrawingArea):
    """Flat headphones drawing from the 300 x 266 coordinates in Headphones-Solid.dc.html."""
    WIDTH, HEIGHT = 300, 266

    def __init__(self, width=80, mode='off'):
        super().__init__(valign=Gtk.Align.CENTER)
        self.set_size_request(width, round(width * self.HEIGHT / self.WIDTH))
        self.mode = mode
        self.left = 0.0
        self.right = 0.0
        self.timer_id = None
        self.set_opacity(0.4 if mode == 'none' else 1.0)
        self.sync_accessible()
        self.set_draw_func(self.draw)

    def sync_accessible(self):
        if self.mode == 'none':
            label = 'Headphones, no earbuds connected'
        elif self.mode == 'off':
            label = 'Headphones, sound off'
        elif self.left > 0 or self.right > 0:
            label = 'Headphones, sound arriving'
        else:
            label = 'Headphones, nothing playing'
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            if mode != 'on':
                self.left = 0.0
                self.right = 0.0
            self.set_opacity(0.4 if mode == 'none' else 1.0)
            self.sync_accessible()
            self.queue_draw()

    def set_levels(self, left, right):
        if self.mode != 'on':
            return
        self.left = max(min(max(left, 0.0), 1.0), self.left)
        self.right = max(min(max(right, 0.0), 1.0), self.right)
        if (self.left > 0 or self.right > 0) and self.timer_id is None:
            self.timer_id = GLib.timeout_add(33, self.tick)
        self.sync_accessible()
        self.queue_draw()

    def tick(self):
        if not self.get_mapped():
            self.left = 0.0
            self.right = 0.0
            self.sync_accessible()
            self.timer_id = None
            return GLib.SOURCE_REMOVE
        self.left = self.left * 0.85 if self.left * 0.85 >= 0.005 else 0.0
        self.right = self.right * 0.85 if self.right * 0.85 >= 0.005 else 0.0
        self.sync_accessible()
        self.queue_draw()
        if (self.left == 0.0 and self.right == 0.0) or self.mode != 'on':
            self.timer_id = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def rounded(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    @staticmethod
    def label(cr, value, x, y, size, color):
        cr.select_font_face('Roboto', 0, 1)
        cr.set_font_size(size)
        extents = cr.text_extents(value)
        cr.set_source_rgb(*color)
        cr.move_to(x - extents.width / 2 - extents.x_bearing, y - extents.height / 2 - extents.y_bearing)
        cr.show_text(value)

    def draw(self, _area, cr, width, height, _data=None):
        scale = min(width / self.WIDTH, height / self.HEIGHT)
        cr.translate((width - self.WIDTH * scale) / 2, (height - self.HEIGHT * scale) / 2)
        cr.scale(scale, scale)

        shell = LATER if self.mode == 'off' else IDLE
        pad = THUMB if self.mode == 'off' else LATER
        left = self.left if self.mode == 'on' else 0.0
        right = self.right if self.mode == 'on' else 0.0

        cr.new_sub_path()
        cr.move_to(32, 156)
        cr.line_to(32, 140)
        cr.arc(150, 140, 118, math.pi, 2 * math.pi)
        cr.line_to(268, 156)
        cr.line_to(246, 156)
        cr.line_to(246, 140)
        cr.arc_negative(150, 140, 96, 2 * math.pi, math.pi)
        cr.line_to(54, 156)
        cr.close_path()
        cr.set_source_rgb(*shell)
        cr.fill()

        for shell_x, pad_x, level in ((22, 56, left), (240, 218, right)):
            self.rounded(cr, shell_x, 146, 38, 92, 15)
            cr.set_source_rgb(*shell)
            cr.fill()
            if level > 0:
                h = 92 * level
                self.rounded(cr, shell_x, 238 - h, 38, h, min(15, h / 2))
                cr.set_source_rgb(*ACTIVE)
                cr.fill()
            self.rounded(cr, pad_x, 132, 26, 120, 12)
            cr.set_source_rgb(*pad)
            cr.fill()

        if left > 0 or right > 0:
            cr.new_sub_path()
            for x in range(96, 205, 2):
                t = (x - 96) / 108
                amp = 20 * (left + (right - left) * t) * math.sin(math.pi * t)
                y = 192 - amp * math.sin(2 * math.pi * (x - 96) / 18)
                if x == 96:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
            cr.set_source_rgb(*ACTIVE)
            cr.set_line_width(3)
            cr.set_line_cap(1)
            cr.set_line_join(1)
            cr.stroke()

        self.label(cr, 'L', 41, 254, 13, TEXT if left > 0 else MUTED)
        self.label(cr, 'R', 259, 254, 13, TEXT if right > 0 else MUTED)


class Chevron(Gtk.DrawingArea):
    """An 18px MUTED right chevron for rows, or a 20px TEXT left chevron for back buttons."""

    def __init__(self, left=False):
        super().__init__(valign=Gtk.Align.CENTER)
        self.left = left
        size = 20 if left else 18
        self.set_size_request(size, size)
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, width, height, _data=None):
        cx, cy = width / 2.0, height / 2.0
        sign = -1 if self.left else 1
        cr.set_source_rgb(*(TEXT if self.left else MUTED))
        cr.set_line_width(1.8)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.move_to(cx - 2.5 * sign, cy - 5.0)
        cr.line_to(cx + 2.5 * sign, cy)
        cr.line_to(cx - 2.5 * sign, cy + 5.0)
        cr.stroke()


class CordSource(Gtk.DrawingArea):
    """The 12px phone dot at the top of the 24px cord column, with the 2px line below it."""

    def __init__(self):
        super().__init__(vexpand=True)
        self.set_size_request(24, -1)
        self.dot = 'idle'
        self.line_active = False
        self.spin = False
        self.tick_id = 0
        self.angle = 0.0
        self.last_time = 0
        self.set_draw_func(self.draw)
        self.connect('map', lambda _: self.sync_spin())
        self.connect('unmap', lambda _: self.sync_spin())

    def set_state(self, dot, line_active, spin=False):
        if (dot, line_active, spin) != (self.dot, self.line_active, self.spin):
            self.dot, self.line_active, self.spin = dot, line_active, spin
            self.sync_spin()
            self.queue_draw()

    def sync_spin(self):
        # Ticks run only while the ring waits and the widget is on screen.
        want = self.spin and self.get_mapped()
        if want and not self.tick_id:
            self.last_time = 0
            self.tick_id = self.add_tick_callback(self.on_tick)
        elif not want and self.tick_id:
            self.remove_tick_callback(self.tick_id)
            self.tick_id = 0

    def on_tick(self, _widget, clock):
        now = clock.get_frame_time()
        if self.last_time:
            self.angle = (self.angle + (now - self.last_time) * 2 * math.pi / 2.5e6) % (2 * math.pi)
        self.last_time = now
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def draw(self, _area, cr, _width, height, _data=None):
        cx, cy = 12.0, 14.0
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
            cr.translate(cx, cy)
            cr.rotate(self.angle)
            cr.arc(0, 0, 5, 0, 2 * math.pi)
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


class PlayerNumber(Gtk.DrawingArea):
    """A 24px player number badge for the Controllers tab strip."""

    def __init__(self, number, state='free'):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.set_size_request(24, 24)
        self.number = str(number)
        self.state = state
        self.set_draw_func(self.draw)

    def draw(self, _area, cr, width, height, _data=None):
        cx, cy = width / 2.0, height / 2.0
        if self.state == 'on':
            cr.arc(cx, cy, 12, 0, 2 * math.pi)
            cr.set_source_rgb(*ACTIVE)
            cr.fill()
            color = BACKGROUND
        else:
            cr.arc(cx, cy, 11, 0, 2 * math.pi)
            cr.set_line_width(2)
            if self.state == 'lost':
                cr.set_source_rgb(*ALERT)
                color = ALERT
            else:
                cr.set_dash([3, 2])
                cr.set_source_rgb(*IDLE)
                color = MUTED
            cr.stroke()
            cr.set_dash([])
        cr.select_font_face('Roboto', 0, 1)
        cr.set_font_size(13)
        extents = cr.text_extents(self.number)
        cr.set_source_rgb(*color)
        cr.move_to(cx - extents.width / 2 - extents.x_bearing, cy - extents.height / 2 - extents.y_bearing)
        cr.show_text(self.number)


class App(Gtk.Application):
    def __init__(self, hidden):
        super().__init__(application_id='io.github.multex.musubi')
        self.start_hidden = hidden
        self.window = None
        self.process = None
        self.partial = b''
        self.stopping = False
        self.status = {'phone': None, 'pad': False, 'pad_error': None, 'pending': [], 'sound_enabled': True}
        self.error = None
        self.stopped = False
        self.config = system.load_config()
        self.ignored = set()
        self.tray = None
        self.has_tray = False
        self.watching = None
        self.panel_key = None
        self.overview_link = None
        self.overview_delay = None
        self.overview_pad = None
        self.overview_headphones = None
        self.overview_sound_row = None
        self.overview_sound_value = None
        self.overview_sound_detail = None
        self.sound_switch = None
        self.sound_link = None
        self.sound_headphones = None
        self.sound_value = None
        self.sound_detail = None
        self.sound_delay = None
        self.readout = None
        self.blocked = set()
        self.firewall_dismissed = False
        self.firewall_message = None
        self.autostart_message = None
        self.autostart_lock = threading.Lock()
        self.renaming = None
        self.selected_item = 'overview'
        self.source_cord = None
        self.header_btn = None
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
        self.overview_panel = None
        self.controllers_panel = None
        self.sound_panel = None
        self.settings_panel = None
        self.phones_panel = None
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
        cord_click = Gtk.GestureClick()
        cord_click.connect('released', lambda *_: self.header_btn.get_sensitive() and self.select_item('overview'))
        self.source_cord.add_controller(cord_click)
        header_row.append(self.source_cord)
        self.header_btn = Gtk.Button(hexpand=True)
        self.header_btn.add_css_class('plain')
        self.header_btn.add_css_class('sidebar-item')
        self.header_btn.set_margin_bottom(10)
        self.header_btn.set_valign(Gtk.Align.START)
        header_labels = column(2)
        header_labels.set_valign(Gtk.Align.CENTER)
        header_labels.set_hexpand(True)
        self.header_name = text('No phone yet', 'value', ellipsize=True)
        self.header_status = text('Waiting', 'mono', ellipsize=True)
        self.header_status.set_single_line_mode(True)
        self.header_status.set_width_chars(16)
        self.header_status.set_max_width_chars(16)
        header_labels.append(self.header_name)
        header_labels.append(self.header_status)
        self.header_btn.set_child(header_labels)
        self.header_btn.connect('clicked', lambda _: self.select_item('overview'))
        header_row.append(self.header_btn)
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

        overview_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.overview_panel = column(0)
        self.overview_panel.set_vexpand(True)
        overview_scroller.set_child(self.overview_panel)
        self.pages.add_named(overview_scroller, 'overview')

        self.pad = PadArea(width=360)
        controllers_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.controllers_panel = column(0)
        self.controllers_panel.set_vexpand(True)
        controllers_scroller.set_child(self.controllers_panel)
        self.pages.add_named(controllers_scroller, 'controllers')

        sound_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.sound_panel = column(0)
        self.sound_panel.set_vexpand(True)
        sound_scroller.set_child(self.sound_panel)
        self.pages.add_named(sound_scroller, 'sound')

        state_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.panel = column(0)
        self.panel.set_vexpand(True)
        state_scroller.set_child(self.panel)
        self.pages.add_named(state_scroller, 'state')

        settings_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.settings_panel = column(0)
        self.settings_panel.set_vexpand(True)
        settings_scroller.set_child(self.settings_panel)
        self.pages.add_named(settings_scroller, 'settings')

        phones_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.phones_panel = column(0)
        self.phones_panel.set_vexpand(True)
        phones_scroller.set_child(self.phones_panel)
        self.pages.add_named(phones_scroller, 'phones')

        self.pages.connect('notify::visible-child-name', lambda *_: self.sync_watch())
        shell.append(self.pages)
        self.window.set_child(shell)

    def sync_watch(self):
        if self.window is None or self.process is None:
            return
        want = (self.window.get_visible()
                and self.pages.get_visible_child_name() in ('overview', 'controllers', 'sound'))
        if want != self.watching:
            self.watching = want
            self.send({'watch': want})

    def show_window(self):
        self.window.present()
        self.sync_watch()

    def on_close(self, _window):
        if self.has_tray and self.config['tray']:
            self.window.set_visible(False)
            self.sync_watch()
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
        self.watching = None
        try:
            self.process = subprocess.Popen([sys.executable, str(RECEIVER), '--service'], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, bufsize=0)
        except OSError as ex:
            self.error = str(ex)
            return
        GLib.io_add_watch(self.process.stdout.fileno(), GLib.PRIORITY_DEFAULT,
                          GLib.IOCondition.IN | GLib.IOCondition.HUP, self.on_receiver_output, self.process)
        self.send_allowed()
        self.send({'sound': self.config['sound_enabled']})
        self.sync_watch()

    def stop_receiver(self):
        process, self.process = self.process, None
        self.stopping = True
        self.watching = None
        if process:
            # Closing stdin makes the receiver release the pad and exit by itself.
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        self.status = {'phone': None, 'pad': False, 'pad_error': None, 'pending': [],
                       'sound_enabled': self.config['sound_enabled']}

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
        self.watching = None
        if not self.stopping and self.error is None:
            self.error = 'The receiver stopped unexpectedly.'
            self.status = {'phone': None, 'pad': False, 'pad_error': None, 'pending': [],
                           'sound_enabled': self.config['sound_enabled']}
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
            if self.overview_pad is not None:
                self.overview_pad.set_state(event['buttons'], event['axes'])
            if self.readout is not None:
                description = describe_input(event['buttons'], event['axes'])
                self.readout.set_label(description or 'Press a button to test')
                self.readout.remove_css_class('live')
                if description:
                    self.readout.add_css_class('live')
        elif kind == 'rumble':
            self.pad.set_rumble(event['strong'], event['weak'])
            if self.overview_pad is not None:
                self.overview_pad.set_rumble(event['strong'], event['weak'])
        elif kind == 'levels':
            for area in (self.overview_headphones, self.sound_headphones):
                if area is not None:
                    area.set_levels(event['l'], event['r'])
        elif kind == 'error':
            self.error = event['message']
            self.refresh()
        elif kind == 'status':
            self.status = {key: event.get(key) for key in (
                'phone', 'pad', 'pad_error', 'pending', 'cable_error', 'sound_enabled')}
            self.refresh()

    def sound_enabled(self):
        if self.status.get('sound_enabled') is not None:
            return bool(self.status['sound_enabled'])
        return bool(self.config.get('sound_enabled', True))

    def sound_state(self, phone):
        if not phone:
            return False, 'No phone yet', 'Needs a phone'
        if not self.sound_enabled():
            return False, 'Off on this PC', 'Turn it on here to send sound'
        if phone.get('audio'):
            return True, 'Playing', 'Through the "Musubi" output'
        return False, 'Off on the phone', 'Turn on Play PC sound on the phone'

    def on_sound_switch(self, enabled):
        if enabled == self.config['sound_enabled'] and enabled == self.status.get('sound_enabled'):
            return
        self.config['sound_enabled'] = enabled
        self.status['sound_enabled'] = enabled
        self.save_config()
        self.send({'sound': enabled})
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
        for widget, active in (
            (self.header_btn, visible == 'overview'),
            (self.ctrl_btn, visible == 'controllers'),
            (self.sound_btn, visible == 'sound'),
            (self.settings_btn, visible in ('settings', 'phones')),
        ):
            if active:
                widget.add_css_class('selected')
            else:
                widget.remove_css_class('selected')
        self.sync_watch()

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
        sound_on = bool(has_phone and self.sound_enabled() and phone.get('audio'))
        players = 1 if controller_on else 0

        seg1 = has_phone and (controller_on or sound_on)
        seg2 = has_phone and sound_on

        self.source_cord.set_state(dot, seg1, state == 'waiting')
        self.ctrl_node.set_state('live' if controller_on else 'idle', seg1, seg2)
        self.sound_node.set_state('live' if sound_on else 'idle', seg2, False)
        self.mic_node.set_state('later', False, False)

        self.ctrl_dots.set_players(players)
        self.ctrl_caption.set_label(f'{players} of 4 players' if has_phone else 'Needs a phone')
        self.sound_caption.set_label(('Playing' if sound_on else 'Off') if has_phone else 'Needs a phone')

        self.items_box.set_opacity(1.0 if has_phone else 0.45)
        self.header_btn.set_sensitive(has_phone)
        self.ctrl_btn.set_sensitive(has_phone)
        self.sound_btn.set_sensitive(has_phone)
        self.stop_btn.set_visible(not self.error and not self.stopped)
        self.update_selection()

    def refresh(self):
        phone = self.status['phone']
        if phone is None:
            self.selected_item = 'overview'
        if not self.status['pad']:
            self.pad.set_state(0, [0] * 8)
            self.pad.set_rumble(0, 0)
            if self.overview_pad is not None:
                self.overview_pad.set_state(0, [0] * 8)
                self.overview_pad.set_rumble(0, 0)
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
        if self.overview_link is not None and phone:
            self.overview_link.set_label(self.link_line(phone))
        if self.sound_link is not None and phone:
            self.sound_link.set_label(self.link_line(phone))
        if phone:
            self.update_sound(phone)
        if self.tray:
            self.tray.set_status(self.tray_text())

    def has_sound_delay(self, phone):
        delay = phone.get('delay')
        return (self.sound_enabled() and bool(phone.get('audio'))
                and isinstance(delay, (int, float)) and not isinstance(delay, bool))

    def update_overview_delay(self, phone):
        has_delay = self.has_sound_delay(phone)
        self.overview_delay.set_visible(has_delay)
        if has_delay:
            self.overview_delay.set_label(f"Delay ≈ {int(phone['delay'])} ms")

    def update_sound_delay(self, phone):
        self.sound_delay.set_label(f"Delay ≈ {int(phone['delay'])} ms" if self.has_sound_delay(phone) else '')

    def update_sound(self, phone):
        sound_on, sound_value, sound_detail = self.sound_state(phone)
        mode = 'on' if sound_on else 'off'
        if self.overview_headphones is not None:
            self.overview_headphones.set_mode(mode)
        if self.overview_sound_value is not None:
            self.overview_sound_value.set_label(sound_value)
        if self.overview_sound_detail is not None:
            self.overview_sound_detail.set_label(sound_detail)
        if self.overview_sound_row is not None:
            self.overview_sound_row.update_property([Gtk.AccessibleProperty.LABEL],
                                                    [f'Sound · to the phone, {sound_value}'])
        if self.overview_delay is not None:
            self.update_overview_delay(phone)
        if self.sound_switch is not None and self.sound_switch.get_active() != self.sound_enabled():
            self.sound_switch.set_active(self.sound_enabled())
        if self.sound_headphones is not None:
            self.sound_headphones.set_mode(mode)
        if self.sound_value is not None:
            self.sound_value.set_label(sound_value)
        if self.sound_detail is not None:
            self.sound_detail.set_label(sound_detail)
        if self.sound_delay is not None:
            self.update_sound_delay(phone)

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
    def clear_box(box):
        while (child := box.get_first_child()) is not None:
            box.remove(child)

    def clear_panel(self):
        self.overview_link = None
        self.overview_delay = None
        self.overview_pad = None
        self.overview_headphones = None
        self.overview_sound_row = None
        self.overview_sound_value = None
        self.overview_sound_detail = None
        self.sound_switch = None
        self.sound_link = None
        self.sound_headphones = None
        self.sound_value = None
        self.sound_detail = None
        self.sound_delay = None
        self.readout = None
        self.clear_box(self.panel)
        self.clear_box(self.overview_panel)
        self.clear_box(self.controllers_panel)
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
            self.panel_overview(phone)
            self.panel_controllers(phone)
            self.panel_sound(phone)
        else:
            self.panel_waiting()
        if not in_settings:
            self.pages.set_visible_child_name(self.selected_item if state == 'connected' else 'state')

    @staticmethod
    def overview_row(labels, drawing, on_click, accessible_label):
        row = Gtk.Button()
        row.add_css_class('plain')
        row.add_css_class('overview-row')
        row.set_size_request(-1, 84)
        row.update_property([Gtk.AccessibleProperty.LABEL], [accessible_label])
        box = Gtk.Box(spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.append(labels)
        box.append(drawing)
        box.append(Chevron())
        row.set_child(box)
        row.connect('clicked', lambda _: on_click())
        return row

    def panel_overview(self, phone):
        top = column(2)
        top.append(text('Phone', 'caption', ellipsize=True))
        name = text(self.phone_name(phone), 'title', ellipsize=True)
        name.set_max_width_chars(24)
        top.append(name)
        self.overview_link = text(self.link_line(phone), 'mono', ellipsize=True)
        self.overview_link.set_single_line_mode(True)
        top.append(self.overview_link)
        if phone['ip'] != 'usb':
            ip_label = text(phone['ip'], 'mono', ellipsize=True)
            ip_label.set_single_line_mode(True)
            top.append(ip_label)
        self.overview_panel.append(top)

        self.overview_panel.append(spacer())

        rows = column(0)
        controller_on = bool(phone['controller'] is not None or self.status['pad'])
        players = 1 if controller_on else 0
        ctrl_labels = column(2)
        ctrl_labels.set_hexpand(True)
        ctrl_labels.set_valign(Gtk.Align.CENTER)
        ctrl_labels.append(text('Controllers · to this PC', 'caption', ellipsize=True))
        ctrl_labels.append(text(f'{players} of 4 players', 'value', ellipsize=True))
        ctrl_detail = (phone['controller'] or 'Connected') if controller_on else 'No controller yet'
        ctrl_labels.append(text(ctrl_detail, 'detail', ellipsize=True))
        ctrl_sub = ('Games see Xbox 360 controller 1' if controller_on
                    else 'Turn one on that is paired with the phone')
        ctrl_sub_label = text(ctrl_sub, 'detail', wrap=True)
        ctrl_sub_label.set_max_width_chars(36)
        ctrl_labels.append(ctrl_sub_label)

        self.overview_pad = PadArea(width=128)
        if controller_on:
            self.overview_pad.set_state(self.pad.buttons, self.pad.axes)
            self.overview_pad.set_rumble(self.pad.strong, self.pad.weak)
        self.overview_pad.set_opacity(1.0 if controller_on else 0.35)

        rows.append(self.overview_row(
            ctrl_labels,
            self.overview_pad,
            lambda: self.select_item('controllers'),
            f'Controllers · to this PC, {players} of 4 players',
        ))
        rows.append(hairline())

        sound_on, sound_value, sound_detail = self.sound_state(phone)
        sound_labels = column(2)
        sound_labels.set_hexpand(True)
        sound_labels.set_valign(Gtk.Align.CENTER)
        sound_labels.append(text('Sound · to the phone', 'caption', ellipsize=True))
        self.overview_sound_value = text(sound_value, 'value', ellipsize=True)
        sound_labels.append(self.overview_sound_value)
        self.overview_sound_detail = text(sound_detail, 'detail', wrap=True)
        self.overview_sound_detail.set_max_width_chars(36)
        sound_labels.append(self.overview_sound_detail)
        self.overview_delay = text('', 'mono', ellipsize=True)
        self.overview_delay.set_single_line_mode(True)
        self.update_overview_delay(phone)
        sound_labels.append(self.overview_delay)

        self.overview_headphones = HeadphonesArea(width=80, mode='on' if sound_on else 'off')
        self.overview_sound_row = self.overview_row(
            sound_labels,
            self.overview_headphones,
            lambda: self.select_item('sound'),
            f'Sound · to the phone, {sound_value}',
        )
        rows.append(self.overview_sound_row)
        rows.append(hairline())

        mic_row = Gtk.Box(spacing=12)
        mic_row.add_css_class('overview-static')
        mic_row.set_size_request(-1, 84)
        mic_labels = column(2)
        mic_labels.set_hexpand(True)
        mic_labels.set_valign(Gtk.Align.CENTER)
        mic_labels.append(text('Mic · to this PC', 'caption', ellipsize=True))
        mic_value = text('Coming later', 'value', ellipsize=True)
        mic_value.add_css_class('muted')
        mic_labels.append(mic_value)
        mic_row.append(mic_labels)
        rows.append(mic_row)

        self.overview_panel.append(rows)
        self.overview_panel.append(spacer())

    def panel_sound(self, phone):
        self.sound_panel.append(text('Sound on phone', 'page-title'))
        self.sound_panel.append(spacer())

        body = Gtk.Box(spacing=20)
        body.set_valign(Gtk.Align.CENTER)

        left = column(0)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.CENTER)

        switch_row = Gtk.Box(spacing=12)
        switch_labels = column(2)
        switch_labels.set_hexpand(True)
        switch_title = text('Play PC sound on the phone', 'row-title', wrap=True)
        switch_title.set_max_width_chars(30)
        switch_detail = text('When off, this PC removes the Musubi output', 'detail', wrap=True)
        switch_detail.set_max_width_chars(36)
        switch_labels.append(switch_title)
        switch_labels.append(switch_detail)
        switch_row.append(switch_labels)
        self.sound_switch = Gtk.Switch(active=self.sound_enabled(), valign=Gtk.Align.CENTER)
        self.sound_switch.update_property([Gtk.AccessibleProperty.LABEL], ['Play PC sound on the phone'])
        self.sound_switch.connect('notify::active', lambda widget, _: self.on_sound_switch(widget.get_active()))
        switch_row.append(self.sound_switch)
        left.append(switch_row)

        phone_box = column(2, 'divider')
        phone_box.append(text('Phone', 'caption', ellipsize=True))
        if phone:
            phone_name = text(self.phone_name(phone), 'value', ellipsize=True)
            phone_name.set_single_line_mode(True)
            phone_name.set_max_width_chars(28)
            phone_box.append(phone_name)
            self.sound_link = text(self.link_line(phone), 'mono', ellipsize=True)
            self.sound_link.set_single_line_mode(True)
            phone_box.append(self.sound_link)
            if phone['ip'] != 'usb':
                ip_label = text(phone['ip'], 'mono', ellipsize=True)
                ip_label.set_single_line_mode(True)
                phone_box.append(ip_label)
        else:
            phone_box.append(text('No phone yet', 'value', ellipsize=True))
        left.append(phone_box)

        output_box = column(2, 'divider')
        output_box.append(text('Output', 'caption', ellipsize=True))
        output_detail = text('In your sound settings, pick "Musubi" as the output. '
                             'Games set to the default output follow it.', 'detail', wrap=True)
        output_detail.set_max_width_chars(40)
        output_box.append(output_detail)
        left.append(output_box)

        sound_on, sound_value, sound_detail = self.sound_state(phone)
        right = column(4)
        right.set_size_request(150, -1)
        right.set_hexpand(False)
        right.set_halign(Gtk.Align.CENTER)
        right.set_valign(Gtk.Align.CENTER)

        self.sound_headphones = HeadphonesArea(width=140, mode='on' if sound_on else 'off')
        self.sound_headphones.set_halign(Gtk.Align.CENTER)
        right.append(self.sound_headphones)

        self.sound_value = text(sound_value, 'value', wrap=True)
        self.sound_value.set_max_width_chars(18)
        self.sound_value.set_xalign(0.5)
        self.sound_value.set_justify(Gtk.Justification.CENTER)
        self.sound_value.set_halign(Gtk.Align.FILL)
        right.append(self.sound_value)

        self.sound_detail = text(sound_detail, 'detail', wrap=True)
        self.sound_detail.set_max_width_chars(20)
        self.sound_detail.set_xalign(0.5)
        self.sound_detail.set_justify(Gtk.Justification.CENTER)
        self.sound_detail.set_halign(Gtk.Align.FILL)
        right.append(self.sound_detail)

        self.sound_delay = text('', 'mono', ellipsize=True)
        self.sound_delay.set_single_line_mode(True)
        self.sound_delay.set_xalign(0.5)
        self.sound_delay.set_justify(Gtk.Justification.CENTER)
        self.sound_delay.set_halign(Gtk.Align.FILL)
        right.append(self.sound_delay)
        if phone:
            self.update_sound_delay(phone)

        body.append(left)
        body.append(right)
        self.sound_panel.append(body)
        self.sound_panel.append(spacer())

    def panel_error(self):
        self.panel.append(spacer())
        content = column(10)
        heading = text("Musubi can't run", 'title', wrap=True)
        heading.add_css_class('alert')
        content.append(heading)
        message = self.error
        if 'in use' in message:
            message = f'Another program is using UDP port {system.PORT}. It may be musubi.py running in a terminal.'
        content.append(text(message, 'body', wrap=True))
        try_again = button('Try again', self.on_start, 'primary')
        try_again.set_margin_top(6)
        content.append(try_again)
        self.panel.append(content)
        self.panel.append(spacer())

    def panel_stopped(self):
        self.panel.append(spacer())
        content = column(10)
        content.set_halign(Gtk.Align.CENTER)
        for line in (text('Musubi is stopped', 'title', wrap=True),
                     text("Phones can't connect until you start it again.", 'body', wrap=True)):
            line.set_xalign(0.5)
            line.set_justify(Gtk.Justification.CENTER)
            line.set_halign(Gtk.Align.CENTER)
            content.append(line)
        start = button('Start', self.on_start, 'primary', align=Gtk.Align.CENTER)
        start.set_margin_top(6)
        content.append(start)
        self.panel.append(content)
        self.panel.append(spacer())

    def panel_waiting(self):
        self.panel.append(spacer())
        content = column(8)
        content.append(text('Waiting for a phone', 'title', wrap=True))
        if self.local_ip:
            content.append(text("Open Musubi on your phone and enter this PC's address:", 'body', wrap=True))
            addr = text(self.local_ip, 'address', ellipsize=True)
            addr.set_single_line_mode(True)
            addr.set_max_width_chars(24)
            content.append(addr)
        else:
            content.append(text('This PC has no local network right now. Connect it to the same network as your phone.',
                                'body', wrap=True))
        if self.status.get('cable_error'):
            usb = block('Or use a USB cable', "Can't use it", self.status['cable_error'], divided=True)
            usb.get_last_child().add_css_class('alert')
            content.append(usb)
        else:
            content.append(block('Or use a USB cable', 'Plug in your phone',
                                 'Set its USB mode to File transfer. Musubi connects by itself.', divided=True))
        ignored = self.ignored_phones()
        if ignored:
            phone = ignored[0]
            ignored_box = column(2, 'divider')
            ignored_box.append(text('Ignored phone', 'caption', ellipsize=True))
            name = text(phone['name'] or 'Unknown model', 'value', ellipsize=True)
            name.set_single_line_mode(True)
            name.set_max_width_chars(32)
            ignored_box.append(name)
            wifi_line = text('Wi-Fi', 'mono', ellipsize=True)
            wifi_line.set_single_line_mode(True)
            ignored_box.append(wifi_line)
            ip_line = text(phone['ip'], 'mono', ellipsize=True)
            ip_line.set_single_line_mode(True)
            ignored_box.append(ip_line)
            allow_btn = button('Allow', lambda: self.allow(phone))
            allow_btn.set_margin_top(4)
            ignored_box.append(allow_btn)
            content.append(ignored_box)
        self.panel.append(content)
        self.panel.append(spacer())

    def panel_controllers(self, phone):
        controller_on = bool(phone['controller'] is not None or self.status['pad'])
        ctrl_name = phone['controller'] or 'Connected'

        strip = Gtk.Box(spacing=4, homogeneous=True)
        for player in range(1, 5):
            tab = column(3, 'player-tab')
            if player == 1:
                tab.add_css_class('selected')
                num_state = 'on' if controller_on else 'free'
                tab_text = 'Connected' if controller_on else 'Free'
                accessible = f"Player 1, {ctrl_name if controller_on else 'free'}"
            else:
                num_state = 'free'
                tab_text = 'Later'
                accessible = f'Player {player}, later'
            tab.update_property([Gtk.AccessibleProperty.LABEL], [accessible])
            tab.append(PlayerNumber(player, num_state))
            label = text(tab_text, 'tab-label', ellipsize=True)
            label.set_xalign(0.5)
            label.set_halign(Gtk.Align.CENTER)
            label.set_single_line_mode(True)
            tab.append(label)
            strip.append(tab)
        self.controllers_panel.append(strip)
        self.controllers_panel.append(hairline())

        header = column(2)
        header.set_margin_top(14)
        value_label = text(ctrl_name if controller_on else 'Player 1 is free', 'value', ellipsize=True)
        value_label.set_single_line_mode(True)
        value_label.set_max_width_chars(40)
        header.append(value_label)

        if self.status['pad_error']:
            detail_label = text(self.status['pad_error'], 'detail', wrap=True)
            detail_label.add_css_class('alert')
        elif controller_on:
            detail_label = text('Player 1 · games see Xbox 360 controller 1', 'detail', wrap=True)
        else:
            detail_label = text("Turn on a controller that's paired with the phone, or pair one in the phone's "
                                "Bluetooth settings. Games see it as Xbox 360 controller 1.", 'detail', wrap=True)
        detail_label.set_max_width_chars(62)
        header.append(detail_label)
        self.controllers_panel.append(header)

        self.pad.set_opacity(1.0 if controller_on else 0.35)
        self.controllers_panel.append(self.pad)

        if controller_on:
            description = describe_input(self.pad.buttons, self.pad.axes)
            self.readout = text(description or 'Press a button to test', 'readout', ellipsize=True)
            if description:
                self.readout.add_css_class('live')
            bottom_line = self.readout
        else:
            bottom_line = text('Waiting for a controller', 'readout', ellipsize=True)
        bottom_line.set_single_line_mode(True)
        bottom_line.set_max_width_chars(56)
        bottom_line.set_xalign(0.5)
        bottom_line.set_justify(Gtk.Justification.CENTER)
        bottom_line.set_halign(Gtk.Align.FILL)
        self.controllers_panel.append(bottom_line)

    def panel_approve(self, phone):
        self.panel.append(spacer())
        content = column(12)
        content.append(text('Allow this phone?', 'title', wrap=True))

        phone_box = column(2, 'divider')
        phone_box.append(text('New phone', 'caption', ellipsize=True))
        name = text(phone['name'] or 'Unknown model', 'value', ellipsize=True)
        name.set_single_line_mode(True)
        name.set_max_width_chars(32)
        phone_box.append(name)
        wifi_line = text('Wi-Fi', 'mono', ellipsize=True)
        wifi_line.set_single_line_mode(True)
        phone_box.append(wifi_line)
        ip_line = text(phone['ip'], 'mono', ellipsize=True)
        ip_line.set_single_line_mode(True)
        phone_box.append(ip_line)
        content.append(phone_box)

        content.append(text("It will be able to press buttons in your games and play this PC's sound. "
                            "Only allow phones you recognize.", 'body', wrap=True))

        actions = Gtk.Box(spacing=10, halign=Gtk.Align.START)
        actions.set_margin_top(4)
        actions.append(button('Allow', lambda: self.allow(phone), 'primary'))
        ignore_btn = button('Ignore', lambda: self.ignore(phone))
        ignore_btn.set_size_request(96, 40)
        actions.append(ignore_btn)
        content.append(actions)

        self.panel.append(content)
        self.panel.append(spacer())

    def panel_firewall(self):
        self.panel.append(spacer())
        content = column(8)
        heading = text('Your firewall is blocking phones', 'title', wrap=True)
        heading.add_css_class('alert')
        content.append(heading)
        if self.network:
            content.append(text(f'Musubi can open UDP port {system.PORT} for devices on your network. '
                                'Your PC will ask for your password once.', 'body', wrap=True))
            subnet = text(self.network, 'mono-subnet', ellipsize=True)
            subnet.set_single_line_mode(True)
            subnet.set_max_width_chars(32)
            content.append(subnet)
            content.append(text('New phones still need your approval here.', 'detail', wrap=True))
        else:
            content.append(text("Musubi can't tell which network to open. Check your connection.", 'body', wrap=True))
        self.append_firewall_message(content)

        actions = Gtk.Box(spacing=10, halign=Gtk.Align.START)
        actions.set_margin_top(6)
        if self.network:
            actions.append(button('Open the port', self.open_port, 'primary'))
        not_now = button('Not now', self.dismiss_firewall)
        not_now.set_size_request(96, 40 if self.network else 36)
        actions.append(not_now)
        content.append(actions)

        self.panel.append(content)
        self.panel.append(spacer())

    def append_firewall_message(self, parent, width_chars=58):
        if self.firewall_message:
            message = text(self.firewall_message, 'detail', wrap=True)
            message.set_max_width_chars(width_chars)
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

    def setting_switch(self, title, detail, active, on_change):
        row = Gtk.Box(spacing=12)
        labels = column(2)
        labels.set_hexpand(True)
        title_label = text(title, 'row-title', wrap=True)
        title_label.set_max_width_chars(24)
        detail_label = text(detail, 'caption', wrap=True)
        detail_label.set_max_width_chars(28)
        labels.append(title_label)
        labels.append(detail_label)
        row.append(labels)
        switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
        switch.update_property([Gtk.AccessibleProperty.LABEL], [title])
        switch.connect('notify::active', lambda widget, _: on_change(widget.get_active()))
        row.append(switch)
        return row

    def refresh_settings(self):
        self.clear_box(self.settings_panel)
        heading = text('Settings', 'page-title')
        heading.set_margin_bottom(16)
        self.settings_panel.append(heading)

        left = column(10)
        left.set_hexpand(True)
        left.append(text('Startup', 'caption', ellipsize=True))
        left.append(self.setting_switch('Start with my PC', 'Opens hidden in the tray',
                                        system.autostart_enabled(), self.on_autostart))
        if self.autostart_message:
            message = text(self.autostart_message, 'detail', wrap=True)
            message.set_max_width_chars(28)
            message.add_css_class('alert')
            left.append(message)
        left.append(self.setting_switch('Keep running in the tray', 'Closing the window keeps Musubi on',
                                        self.config['tray'], self.on_tray_setting))

        firewall_box = column(6, 'divider')
        firewall_box.append(text('Firewall', 'caption', ellipsize=True))
        self.append_firewall_status(firewall_box)
        left.append(firewall_box)

        right = column(10)
        right.set_hexpand(True)
        right.append(text('Phones', 'caption', ellipsize=True))
        count = len(self.config['phones'])
        manage = Gtk.Button()
        manage.add_css_class('row')
        manage.update_property([Gtk.AccessibleProperty.LABEL], [f'Manage phones, {count} allowed'])
        manage_row = Gtk.Box(spacing=12)
        labels = column(2)
        labels.set_hexpand(True)
        labels.append(text('Manage phones', 'row-title', ellipsize=True))
        labels.append(text(f'{count} allowed', 'caption', ellipsize=True))
        manage_row.append(labels)
        manage_row.append(Chevron())
        manage.set_child(manage_row)
        manage.connect('clicked', lambda _: self.open_phones())
        right.append(manage)

        about_box = column(6, 'divider')
        about_box.append(text('About', 'caption', ellipsize=True))
        about_box.append(self.link('Setup guide', f'{REPO}#setup'))
        about_box.append(self.link('Source code', REPO))
        right.append(about_box)
        right.append(spacer())
        version_line = text(f'Musubi {VERSION} · MIT license', 'mono', ellipsize=True)
        version_line.set_single_line_mode(True)
        version_line.set_max_width_chars(28)
        right.append(version_line)

        columns = Gtk.Box(spacing=28, homogeneous=True, vexpand=True)
        columns.append(left)
        columns.append(right)
        self.settings_panel.append(columns)

    def append_firewall_status(self, parent):
        kind, sources = system.firewall_status()
        if kind == 'none':
            title = text("ufw isn't on", 'row-title', wrap=True)
            title.set_max_width_chars(26)
            detail = text(f'Musubi has nothing to open. If you use another firewall, allow UDP port {system.PORT}.',
                          'caption', wrap=True)
            detail.set_max_width_chars(28)
            parent.append(title)
            parent.append(detail)
            return
        if kind == 'unknown':
            title = text("Can't read the firewall rules", 'row-title', wrap=True)
            title.set_max_width_chars(26)
            detail = text(f"If phones can't connect, allow UDP port {system.PORT}.", 'caption', wrap=True)
            detail.set_max_width_chars(28)
            parent.append(title)
            parent.append(detail)
            return
        if self.network is not None and system.covers(sources, self.network):
            parent.append(text('Port open', 'row-title', ellipsize=True))
            mono = text(f'UDP {system.PORT} · {self.network}', 'mono', ellipsize=True)
            mono.set_single_line_mode(True)
            mono.set_max_width_chars(28)
            parent.append(mono)
            return
        phones = len(sources)
        if phones:
            title = text(f'Open for {phones} address{"" if phones == 1 else "es"}', 'row-title', wrap=True)
            title.set_max_width_chars(26)
            detail = text('Those phones connect fine. Others on your network are blocked until you open it.',
                          'caption', wrap=True)
            detail.set_max_width_chars(28)
        else:
            title = text('Port closed', 'row-title', wrap=True)
            title.set_max_width_chars(26)
            detail = text(f"UDP {system.PORT} is blocked, so phones can't connect.", 'caption', wrap=True)
            detail.set_max_width_chars(28)
        parent.append(title)
        parent.append(detail)
        if self.network:
            parent.append(button('Open for my network', self.open_port))
        self.append_firewall_message(parent, 28)

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
        widget = Gtk.Button(child=text(title, 'link-text'), halign=Gtk.Align.START)
        widget.add_css_class('row')
        widget.connect('clicked', lambda _: Gtk.UriLauncher(uri=url).launch(self.window, None, None))
        return widget

    def open_phones(self):
        self.refresh_phones()
        self.pages.set_visible_child_name('phones')
        self.update_selection()

    def refresh_phones(self):
        self.clear_box(self.phones_panel)
        header = Gtk.Box(spacing=6)
        header.set_margin_bottom(12)
        back = Gtk.Button(child=Chevron(left=True), valign=Gtk.Align.CENTER)
        back.add_css_class('back-btn')
        back.update_property([Gtk.AccessibleProperty.LABEL], ['Back to Settings'])
        back.connect('clicked', lambda _: self.open_settings())
        header.append(back)
        title = text('Phones', 'page-title')
        title.set_valign(Gtk.Align.CENTER)
        header.append(title)
        self.phones_panel.append(header)
        self.phones_panel.append(hairline())

        if not self.config['phones']:
            empty = column(4)
            empty.set_margin_top(16)
            empty.append(text('No phones allowed yet', 'row-title', wrap=True))
            empty.append(text('When a phone connects, Musubi asks you here first.', 'caption', wrap=True))
            self.phones_panel.append(empty)
            return

        active = self.status['phone'] and self.status['phone']['ip']
        for ip, name in self.config['phones'].items():
            row = Gtk.Box(spacing=12)
            row.set_margin_top(12)
            row.set_margin_bottom(12)
            labels = column(2)
            labels.set_hexpand(True)
            labels.set_valign(Gtk.Align.CENTER)
            if self.renaming == ip:
                entry = Gtk.Entry(text=name, hexpand=True, valign=Gtk.Align.CENTER)
                entry.update_property([Gtk.AccessibleProperty.LABEL], [f'New name for {name}'])
                entry.connect('activate', lambda widget, phone=ip: self.save_rename(phone, widget.get_text()))
                labels.append(entry)
                row.append(labels)
                save_btn = button('Save', lambda phone=ip, widget=entry: self.save_rename(phone, widget.get_text()),
                                  align=None)
                save_btn.set_valign(Gtk.Align.CENTER)
                row.append(save_btn)
            else:
                name_label = text(name, 'row-title', ellipsize=True)
                name_label.set_single_line_mode(True)
                name_label.set_max_width_chars(30)
                labels.append(name_label)
                status_label = text('Connected' if ip == active else 'Allowed', 'mono', ellipsize=True)
                status_label.set_single_line_mode(True)
                labels.append(status_label)
                ip_label = text(ip, 'mono', ellipsize=True)
                ip_label.set_single_line_mode(True)
                labels.append(ip_label)
                row.append(labels)
                actions = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
                actions.append(button('Rename', lambda phone=ip: self.start_rename(phone), align=None))
                actions.append(button('Remove', lambda phone=ip: self.remove_phone(phone), align=None))
                row.append(actions)
            self.phones_panel.append(row)
            self.phones_panel.append(hairline())

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
        # Typing the name turned on GTK's keyboard focus ring, and the rebuilt list hands focus to the back arrow.
        self.window.set_focus(None)
        self.window.set_focus_visible(False)

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
