#!/usr/bin/env python3
"""Receive gamepad state from the Android app and replay it on a virtual pad."""
import argparse
import ctypes
import json
import os
import select
import signal
import socket
import sys
import time
from pathlib import Path

from evdev import AbsInfo, UInput, UInputError, ecodes as e, ff

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # core.py is shared with Windows, one folder up

import audio
import core  # noqa: E402
from core import *  # noqa: F401,F403 (gui.py and the tests read the shared names from here)

try:
    import cable
except ImportError:
    cable = None

MAX_EFFECTS = 16

# Bit order matches BUTTON_KEYS in MainActivity.java.
BUTTONS = [
    e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST, e.BTN_TL, e.BTN_TR,
    e.BTN_SELECT, e.BTN_START, e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR,
]
STICK = AbsInfo(0, -32767, 32767, 16, 128, 0)
TRIGGER = AbsInfo(0, 0, 255, 0, 0, 0)
HAT = AbsInfo(0, -1, 1, 0, 0, 0)
# Order matches the axes in the packet. Same layout as xpad, so games map it as an Xbox pad.
AXES = [
    (e.ABS_X, STICK), (e.ABS_Y, STICK), (e.ABS_RX, STICK), (e.ABS_RY, STICK),
    (e.ABS_Z, TRIGGER), (e.ABS_RZ, TRIGGER), (e.ABS_HAT0X, HAT), (e.ABS_HAT0Y, HAT),
]


def apply(ui, state):
    buttons, axes = state
    for bit, code in enumerate(BUTTONS):
        ui.write(e.EV_KEY, code, buttons >> bit & 1)
    for value, (code, _) in zip(axes, AXES):
        ui.write(e.EV_ABS, code, value)
    ui.syn()


class Pad(UInput):
    """python-evdev 2.0 never sets request_id, so it would answer the wrong game once two requests overlap."""

    def begin_upload(self, request_id):
        return self._begin(ff.UInputUpload(), request_id, self.dll._uinput_begin_upload)

    def begin_erase(self, request_id):
        return self._begin(ff.UInputErase(), request_id, self.dll._uinput_begin_erase)

    def _begin(self, request, request_id, ioctl):
        request.request_id = request_id
        if ioctl(self.fd, ctypes.byref(request)):
            raise UInputError('uinput force feedback request failed')
        return request


def make_pad(name='Musubi'):
    return Pad({e.EV_KEY: BUTTONS, e.EV_ABS: AXES, e.EV_FF: [e.FF_RUMBLE]}, name=name, vendor=0x045e,
               product=0x028e, version=0x110, bustype=e.BUS_USB, max_effects=MAX_EFFECTS)


class Bridge(core.Bridge):
    """The shared bridge on a uinput pad, where games upload force feedback effects and then play them by id."""

    def __init__(self, *args, make_pad=make_pad, **kwargs):
        super().__init__(*args, make_pad=make_pad, **kwargs)
        # Effect id to (strong, weak, length in ms), as the game uploaded them.
        self.effects = {}
        # shortcut: one effect plays at a time, the latest play wins, mix them if a game layers effects.
        self.playing = None
        self.playing_until = None

    def _apply(self, state):
        apply(self.pad, state)

    def _forget_rumble(self):
        self.effects = {}
        self.playing = None

    def _wanted_rumble(self, now):
        if self.playing is not None and self.playing_until is not None and now >= self.playing_until:
            self.playing = None
        if self.playing is not None:
            strong, weak, _ = self.effects[self.playing]
            if strong or weak:
                return strong, weak, self.playing_until
        return RUMBLE_STOPPED

    def on_pad_events(self):
        """Force feedback from games: effect uploads and erases, then plays and stops."""
        try:
            events = list(self.pad.read())
        except OSError:
            return
        for event in events:
            try:
                if event.type == e.EV_UINPUT and event.code == e.UI_FF_UPLOAD:
                    upload = self.pad.begin_upload(event.value)
                    effect = upload.effect
                    rumble = effect.u.ff_rumble_effect
                    self.effects[effect.id] = (rumble.strong_magnitude, rumble.weak_magnitude, effect.ff_replay.length)
                    upload.retval = 0
                    self.pad.end_upload(upload)
                elif event.type == e.EV_UINPUT and event.code == e.UI_FF_ERASE:
                    erase = self.pad.begin_erase(event.value)
                    self.effects.pop(erase.effect_id, None)
                    if erase.effect_id == self.playing:
                        self.playing = None
                    erase.retval = 0
                    self.pad.end_erase(erase)
                elif event.type == e.EV_FF and event.code in self.effects:
                    if event.value:
                        length = self.effects[event.code][2]
                        self.playing = event.code
                        # The value is a repeat count; a length of 0 plays until stopped.
                        self.playing_until = self.clock() + length * event.value / 1000 if length else None
                    elif event.code == self.playing:
                        self.playing = None
            except (OSError, UInputError):
                continue  # A game's bad request must never take the pad down.
        self._send_rumble()


def bind_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))
    return sock


def run_console(phone_ip):
    sock = bind_socket()
    shown = {}

    def on_status():
        status = bridge.status()
        phone = status['phone']
        now = {'phone': phone and phone['ip'], 'pad': status['pad'], 'error': status['pad_error']}
        if now != shown:
            shown.update(now)
            print(f"phone: {now['phone'] or 'none'}, virtual pad: {'on' if now['pad'] else 'off'}"
                  + (f", error: {now['error']}" if now['error'] else ''))

    bridge = Bridge(sock, [phone_ip], on_status)
    print(f'waiting for {phone_ip} on UDP {PORT}')
    try:
        while True:
            readable = select.select([sock] + ([bridge.pad] if bridge.pad is not None else []), [], [], TICK)[0]
            if bridge.pad is not None and bridge.pad in readable:
                bridge.on_pad_events()
            if sock in readable:
                bridge.on_datagram(*sock.recvfrom(MAX_DATAGRAM))
            bridge.on_tick()
    finally:
        bridge.close()


def run_service():
    """Backend for gui.py: commands on stdin, one JSON event per line on stdout."""
    out = sys.stdout.fileno()
    stdin = sys.stdin.fileno()
    os.set_blocking(out, False)
    try:
        sock = bind_socket()
    except OSError as ex:
        write_event(out, {'event': 'error', 'message': str(ex)})
        return

    link = cable.Cable() if cable else None
    sources = [sock, stdin] + ([link.reader] if link else [])
    watching = [False]
    last_input = [0.0, None]
    sound_enabled = [True]

    def send_status():
        cable_error = link.error if link else 'Install python-pyusb to connect phones with a cable.'
        write_event(out, {'event': 'status', **bridge.status(), 'cable_error': cable_error,
                           'sound_enabled': sound_enabled[0]})

    def on_input(state):
        last_input[1] = state
        now = time.monotonic()
        if watching[0] and now - last_input[0] >= 1 / 30:
            flush_input(now)

    def flush_input(now):
        last_input[0] = now
        buttons, axes = last_input[1]
        write_event(out, {'event': 'input', 'buttons': buttons, 'axes': axes})
        last_input[1] = None

    def on_rumble(strong, weak):
        write_event(out, {'event': 'rumble', 'strong': strong, 'weak': weak})

    bridge = Bridge(sock, [], send_status, on_input, on_rumble, send_usb=link.send if link else None)
    sound = audio.PhoneAudio()
    sound.remove_stale()
    levels = audio.Levels()
    # SystemExit runs the finally below, so a terminated service doesn't leave its sink behind.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    buffered = b''
    next_status = 0
    try:
        while True:
            pad = [bridge.pad] if bridge.pad is not None else []
            readable = select.select(sources + sound.sources() + pad, [], [], TICK)[0]
            # Before the datagrams, which can close this pad.
            if pad and pad[0] in readable:
                bridge.on_pad_events()
            if sound.capture is not None and sound.capture.stdout in readable:
                for seq, pcm in sound.read():
                    if bridge.wants_audio():
                        bridge.send_to_phone(audio_message(seq, pcm))
                        if watching[0]:
                            levels.add(pcm)
            if sock in readable:
                bridge.on_datagram(*sock.recvfrom(MAX_DATAGRAM))
            if link and link.reader in readable:
                bridge.on_datagram(link.reader.recv(MAX_DATAGRAM), (USB, 0))
            if stdin in readable:
                chunk = os.read(stdin, 4096)
                if not chunk:
                    return  # The window is gone, so nobody can approve or see anything.
                buffered += chunk
                while b'\n' in buffered:
                    line, buffered = buffered.split(b'\n', 1)
                    command = json.loads(line)
                    if 'allow' in command:
                        bridge.set_allowed(command['allow'])
                    if 'watch' in command:
                        watching[0] = bool(command['watch'])
                    if 'sound' in command:
                        sound_enabled[0] = bool(command['sound'])
                        send_status()
            bridge.on_tick()
            sync_sound(sound, sound_enabled[0] and bridge.wants_audio())
            sound.set_stable(bool((bridge.hello or {}).get('stable')))
            now = time.monotonic()
            if watching[0]:
                pair = levels.poll(now)
                if pair:
                    write_event(out, {'event': 'levels', 'l': pair[0], 'r': pair[1]})
            # Trailing input after a throttled burst, so the drawing ends on the true state.
            if watching[0] and last_input[1] is not None and now - last_input[0] >= 1 / 30:
                flush_input(now)
            # Repeating this covers any status event a full pipe dropped.
            if now >= next_status:
                next_status = now + 1
                send_status()
    finally:
        sound.stop()
        bridge.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phone_ip', nargs='?', help='only packets from this address are accepted')
    parser.add_argument('--service', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.service:
        run_service()
    elif args.phone_ip:
        run_console(args.phone_ip)
    else:
        parser.error('give the phone IP, or use gui.py')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
