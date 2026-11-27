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


def make_pad(slot=0, name=None):
    name = name or ('Musubi' if slot == 0 else f'Musubi {slot + 1}')
    try:
        return Pad({e.EV_KEY: BUTTONS, e.EV_ABS: AXES, e.EV_FF: [e.FF_RUMBLE]}, name=name, vendor=0x045e,
                   product=0x028e, version=0x110, bustype=e.BUS_USB, max_effects=MAX_EFFECTS)
    except UInputError as ex:
        raise OSError(str(ex)) from ex


class _FfState:
    """Force feedback state for one uinput pad."""

    def __init__(self):
        # Effect id to (strong, weak, length in ms), as the game uploaded them.
        self.effects = {}
        # shortcut: one effect plays at a time, the latest play wins, mix them if a game layers effects.
        self.playing = None
        self.playing_until = None


class Bridge(core.Bridge):
    """The shared bridge on up to four uinput pads, where games upload force feedback effects and then play them by id."""

    def __init__(self, *args, make_pad=make_pad, **kwargs):
        super().__init__(*args, make_pad=make_pad, **kwargs)
        self._ff = [_FfState() for _ in range(MAX_PLAYERS)]

    def _apply(self, slot, state):
        apply(slot.pad, state)

    def _forget_rumble(self, slot):
        self._ff[slot.index] = _FfState()

    def _wanted_rumble(self, slot, now):
        ff_state = self._ff[slot.index]
        if ff_state.playing is not None and ff_state.playing_until is not None and now >= ff_state.playing_until:
            ff_state.playing = None
        if ff_state.playing is not None:
            strong, weak, _ = ff_state.effects[ff_state.playing]
            if strong or weak:
                return strong, weak, ff_state.playing_until
        return RUMBLE_STOPPED

    def on_pad_events(self, slot_index=0):
        """Force feedback from games: effect uploads and erases, then plays and stops."""
        slot = self.slots[slot_index]
        if slot.pad is None:
            return
        ff_state = self._ff[slot_index]
        try:
            events = list(slot.pad.read())
        except OSError:
            return
        for event in events:
            try:
                if event.type == e.EV_UINPUT and event.code == e.UI_FF_UPLOAD:
                    upload = slot.pad.begin_upload(event.value)
                    effect = upload.effect
                    rumble = effect.u.ff_rumble_effect
                    ff_state.effects[effect.id] = (rumble.strong_magnitude, rumble.weak_magnitude, effect.ff_replay.length)
                    upload.retval = 0
                    slot.pad.end_upload(upload)
                elif event.type == e.EV_UINPUT and event.code == e.UI_FF_ERASE:
                    erase = slot.pad.begin_erase(event.value)
                    ff_state.effects.pop(erase.effect_id, None)
                    if erase.effect_id == ff_state.playing:
                        ff_state.playing = None
                    erase.retval = 0
                    slot.pad.end_erase(erase)
                elif event.type == e.EV_FF and event.code in ff_state.effects:
                    if event.value:
                        length = ff_state.effects[event.code][2]
                        ff_state.playing = event.code
                        # The value is a repeat count; a length of 0 plays until stopped.
                        ff_state.playing_until = self.clock() + length * event.value / 1000 if length else None
                    elif event.code == ff_state.playing:
                        ff_state.playing = None
            except (OSError, UInputError):
                continue  # A game's bad request must never take the pad down.
        self._send_rumble(slot)


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
        pads = [i + 1 for i, on in enumerate(status['pads']) if on]
        now = {'phone': phone and phone['ip'], 'pads': pads, 'error': status['pad_error']}
        if now != shown:
            shown.update(now)
            pad_text = ', '.join(str(p) for p in pads) if pads else 'off'
            print(f"phone: {now['phone'] or 'none'}, virtual pads: {pad_text}"
                  + (f", error: {now['error']}" if now['error'] else ''))

    bridge = Bridge(sock, [phone_ip], on_status)
    print(f'waiting for {phone_ip} on UDP {PORT}')
    try:
        while True:
            pads = bridge.open_pads()
            readable = select.select([sock] + [pad for _, pad in pads], [], [], TICK)[0]
            for slot, pad in pads:
                if pad in readable:
                    bridge.on_pad_events(slot)
            if sock in readable:
                bridge.on_datagram(*sock.recvfrom(MAX_DATAGRAM))
            bridge.on_tick()
    finally:
        bridge.close()


def sync_mic(mic, hello, wanted):
    """Musubi mic exists while the phone's hello says its mic is on. With no hello, `wanted` (the last choice) keeps it
    for apps like Discord. Returns the new choice."""
    if hello is not None:
        wanted = hello['mic'] != 0
        # A stopped mic restarts its numbering at 0, so a quick unmute must count as a new stream, not old packets.
        if hello['mic'] != 1:
            mic.last_seq = None
    if wanted:
        mic.load()
    else:
        mic.stop()
        mic.failed = False  # Turning the mic off and on again retries a device that failed.
    return wanted


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
    last_input = [[0.0, None] for _ in range(MAX_PLAYERS)]
    sound_enabled = [True]
    mic_wanted = [False]

    def send_status():
        cable_error = link.error if link else 'Install python-pyusb to connect phones with a cable.'
        write_event(out, {'event': 'status', **bridge.status(), 'cable_error': cable_error,
                           'sound_enabled': sound_enabled[0]})

    def on_input(slot, state):
        last_input[slot][1] = state
        now = time.monotonic()
        if watching[0] and now - last_input[slot][0] >= 1 / 30:
            flush_input(slot, now)

    def flush_input(slot, now):
        last_input[slot][0] = now
        buttons, axes = last_input[slot][1]
        write_event(out, {'event': 'input', 'slot': slot, 'buttons': buttons, 'axes': axes})
        last_input[slot][1] = None

    def on_rumble(slot, strong, weak):
        write_event(out, {'event': 'rumble', 'slot': slot, 'strong': strong, 'weak': weak})

    def on_mic(seq, pcm):
        mic.feed(seq, pcm)
        if watching[0] and pcm and not len(pcm) % 2:
            mic_level.add(pcm)

    mic = audio.PhoneMic()
    mic_level = audio.Levels(channels=1)
    bridge = Bridge(sock, [], send_status, on_input, on_rumble, send_usb=link.send if link else None, on_mic=on_mic)
    sound = audio.PhoneAudio()
    sound.remove_stale()
    mic.remove_stale()
    levels = audio.Levels()
    # SystemExit runs the finally below, so a terminated service doesn't leave its sink behind.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    buffered = b''
    next_status = 0
    try:
        while True:
            pads = bridge.open_pads()
            readable = select.select(sources + sound.sources() + [pad for _, pad in pads], [], [], TICK)[0]
            # Before the datagrams, which can close this pad.
            for slot, pad in pads:
                if pad in readable:
                    bridge.on_pad_events(slot)
            if sound.capture is not None and sound.capture.stdout in readable:
                for seq, pcm in sound.read():
                    if bridge.wants_audio():
                        bridge.send_to_phone(audio_message(seq, pcm))
                        if watching[0]:
                            levels.add(pcm)
            # Before datagrams, so an initial allow list from gui.py is applied before any waiting packet.
            if stdin in readable:
                chunk = os.read(stdin, 4096)
                if not chunk:
                    return  # The window is gone, so nobody can approve or see anything.
                buffered += chunk
                while b'\n' in buffered:
                    line, buffered = buffered.split(b'\n', 1)
                    try:
                        command = json.loads(line)
                    except ValueError:
                        continue
                    if 'allow' in command:
                        bridge.set_allowed(command['allow'])
                    if 'watch' in command:
                        watching[0] = bool(command['watch'])
                    if 'sound' in command:
                        sound_enabled[0] = bool(command['sound'])
                        send_status()
                    if 'mic_keep' in command:
                        mic_wanted[0] = bool(command['mic_keep'])
                    if command.get('mic') in (0, 1, 2):
                        bridge.set_mic(command['mic'])
            if sock in readable:
                bridge.on_datagram(*sock.recvfrom(MAX_DATAGRAM))
            if link and link.reader in readable:
                bridge.on_datagram(link.reader.recv(MAX_DATAGRAM), (USB, 0))
            bridge.on_tick()
            mic_wanted[0] = sync_mic(mic, bridge.hello, mic_wanted[0])
            sync_sound(sound, sound_enabled[0] and bridge.wants_audio())
            sound.set_stable(bool((bridge.hello or {}).get('stable')))
            now = time.monotonic()
            if watching[0]:
                pair = levels.poll(now)
                if pair:
                    write_event(out, {'event': 'levels', 'l': pair[0], 'r': pair[1]})
                level = mic_level.poll(now)
                if level:
                    write_event(out, {'event': 'mic_level', 'level': level[0]})
            # Trailing input after a throttled burst, so the drawing ends on the true state.
            if watching[0]:
                for slot in range(MAX_PLAYERS):
                    if last_input[slot][1] is not None and now - last_input[slot][0] >= 1 / 30:
                        flush_input(slot, now)
            # Repeating this covers any status event a full pipe dropped.
            if now >= next_status:
                next_status = now + 1
                send_status()
    finally:
        mic.stop()
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
