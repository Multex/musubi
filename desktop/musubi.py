#!/usr/bin/env python3
"""Receive gamepad state from the Android app and replay it on a virtual pad."""
import argparse
import ctypes
import json
import os
import select
import signal
import socket
import struct
import sys
import time

from evdev import AbsInfo, UInput, UInputError, ecodes as e, ff

import audio

try:
    import cable
except ImportError:
    cable = None

PORT = 9999
# The app resends the state 20 times per second, so this means the link is gone.
TIMEOUT = 0.5
# Longer than a Wi-Fi hiccup, so a game doesn't lose its controller over one.
PHONE_TIMEOUT = 5
# Version 0.2.0 of the app sends no hello, so after this long assume a controller is there.
HELLO_WAIT = 1.5
TICK = 0.25

# Whole state in every packet: a lost one is repaired by the next, so a release can't stick.
# shortcut: no sequence number, a reordered packet glitches until the next one, add it if jitter shows up.
PACKET = struct.Struct('!I8h')
NEUTRAL = PACKET.pack(0, *[0] * 8)
# The app times the echo of these to tell whether the PC is answering.
PING_SIZE = 8
# Magic, controller connected, ping in ms, then "phone name\0controller name\0" a features byte and a 16-bit sound delay in ms (0 = none). Sent once a second.
HELLO = struct.Struct('!4sBH')
HELLO_MAGIC = b'PBHI'
# Feature bits stay below 0x20: 0.5.x desktops read the byte as part of the controller name and drop it as a control character.
FEATURE_AUDIO = 1
FEATURE_RUMBLE = 2
# PC to phone: magic, sequence number, then PCM.
AUDIO = struct.Struct('!4sH')
AUDIO_MAGIC = b'PBAU'
# PC to phone: magic, strong and weak motor 0..65535, then ms left (0 = until the next message). Sent on change only.
RUMBLE = struct.Struct('!4sHHH')
RUMBLE_MAGIC = b'PBRM'
RUMBLE_STOPPED = (0, 0, None)
# The phone stops an endless rumble after 2 s unless told again, so a lost stop can't leave the pad buzzing.
RUMBLE_REFRESH = 1.0
MAX_EFFECTS = 16
MAX_DATAGRAM = 512
MAX_NAME = 64
MAX_PENDING = 8
# Stands in for the IP of a phone on the USB cable.
USB = 'usb'

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


def decode(data):
    """Return (button bits, clamped axis values), or None for a malformed packet."""
    if len(data) != PACKET.size:
        return None
    buttons, *axes = PACKET.unpack(data)
    return buttons, [min(max(v, info.min), info.max) for v, (_, info) in zip(axes, AXES)]


def clean_name(raw):
    """Names come straight off the network, so they get no control characters or unbounded length."""
    text = raw.decode('utf-8', 'replace')
    return ''.join(c for c in text if c.isprintable())[:MAX_NAME]


def decode_hello(data):
    """Return {name, controller, controller_connected, rtt, audio, delay}, or None if this isn't a hello."""
    if len(data) < HELLO.size or data[:4] != HELLO_MAGIC:
        return None
    _, connected, rtt = HELLO.unpack_from(data)
    phone, _, rest = data[HELLO.size:].partition(b'\0')
    controller, _, features = rest.partition(b'\0')
    flags = features[0] if features else 0
    delay = int.from_bytes(features[1:3], 'big') if len(features) >= 3 else 0
    return {
        'name': clean_name(phone),
        'controller': clean_name(controller),
        'controller_connected': bool(connected),
        'rtt': rtt,
        'audio': bool(flags & FEATURE_AUDIO),
        'rumble': bool(flags & FEATURE_RUMBLE),
        'delay': delay or None,
    }


def audio_message(seq, pcm):
    return AUDIO.pack(AUDIO_MAGIC, seq & 0xffff) + pcm


def rumble_message(strong, weak, ms):
    return RUMBLE.pack(RUMBLE_MAGIC, strong, weak, ms)


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


class Bridge:
    """Drives one virtual pad from the first allowed phone that talks to it."""

    def __init__(self, sock, allowed, on_status, on_input=None, on_rumble=None, make_pad=make_pad, clock=time.monotonic,
                 send_usb=None):
        self.sock = sock
        self.send_usb = send_usb
        self.allowed = set(allowed)
        self.on_status = on_status
        self.on_input = on_input
        self.on_rumble = on_rumble
        self.make_pad = make_pad
        self.clock = clock
        self.pad = None
        self.pad_error = None
        self.active = None
        self.addr = None
        self.hello = None
        self.last = NEUTRAL
        self.last_state_at = 0
        self.last_seen = 0
        self.active_since = 0
        self.pending = {}
        self.pending_seen = {}
        # Effect id to (strong, weak, length in ms), as the game uploaded them.
        self.effects = {}
        # shortcut: one effect plays at a time, the latest play wins, mix them if a game layers effects.
        self.playing = None
        self.playing_until = None
        self.rumble_sent = RUMBLE_STOPPED
        self.rumble_sent_at = 0

    def status(self):
        phone = None
        if self.active:
            hello = self.hello or {}
            phone = {
                'ip': self.active,
                'name': hello.get('name', ''),
                # Version 0.2.0 sends no hello, so None means "can't tell", not "disconnected".
                'controller': hello.get('controller') if hello.get('controller_connected') else None,
                'controller_known': self.hello is not None,
                'rtt': hello.get('rtt'),
                'audio': hello.get('audio', False),
                'delay': hello.get('delay'),
            }
        return {
            'phone': phone,
            'pad': self.pad is not None,
            'pad_error': self.pad_error,
            'pending': [{'ip': ip, 'name': name} for ip, name in self.pending.items()],
        }

    def set_allowed(self, allowed):
        self.allowed = set(allowed)
        for ip in self.allowed:
            self.pending.pop(ip, None)
            self.pending_seen.pop(ip, None)
        if self.active not in (None, USB) and self.active not in self.allowed:
            self._drop_phone()
        self.on_status()

    def _sync_pad(self):
        if self.hello is not None:
            wanted = self.hello['controller_connected']
        else:
            # Waiting first stops a pad flashing on before a newer app's first hello arrives.
            wanted = self.active is not None and self.clock() - self.active_since >= HELLO_WAIT
        if wanted and self.pad is None and self.pad_error is None:
            try:
                self.pad = self.make_pad()
                self.last = NEUTRAL
            except OSError as ex:
                self.pad_error = str(ex)
            self.on_status()
        elif not wanted and self.pad is not None:
            self._close_pad()
            self.on_status()

    def _close_pad(self):
        self.effects = {}
        self.playing = None
        self._send_rumble()
        apply(self.pad, decode(NEUTRAL))
        self.pad.close()
        self.pad = None
        self.last = NEUTRAL
        if self.on_input:
            self.on_input(decode(NEUTRAL))

    def _drop_phone(self):
        # Before forgetting the phone, so its final rumble stop still has somewhere to go.
        if self.pad is not None:
            self._close_pad()
        self.active = None
        self.addr = None
        self.hello = None
        self.pad_error = None

    def _note_unknown(self, ip, data):
        hello = decode_hello(data)
        if hello is None and len(data) not in (PING_SIZE, PACKET.size):
            return
        # A known name replaces the empty one, but junk can't grow the list without limit.
        if ip in self.pending:
            self.pending_seen[ip] = self.clock()
            if hello and hello['name'] != self.pending[ip]:
                self.pending[ip] = hello['name']
                self.on_status()
        elif len(self.pending) < MAX_PENDING:
            self.pending[ip] = hello['name'] if hello else ''
            self.pending_seen[ip] = self.clock()
            self.on_status()

    def on_datagram(self, data, addr):
        ip = addr[0]
        # Plugging in a cable is consent enough.
        if ip != USB and ip not in self.allowed:
            self._note_unknown(ip, data)
            return
        now = self.clock()
        if self.active != ip:
            # The cable takes over from Wi-Fi, and Wi-Fi from an unplugged cable. Both keep the hello so the pad doesn't drop out meanwhile.
            if self.active is not None and ip != USB and not (self.active == USB and now - self.last_seen > TIMEOUT):
                return
            if self.active is None:
                self.hello = None
            self.active = ip
            self.active_since = now
            self.on_status()
        self.last_seen = now
        # The app opens a new socket, so a new port, each time it resumes.
        self.addr = addr

        hello = decode_hello(data)
        if hello is not None:
            if hello != self.hello:
                self.hello = hello
                self.on_status()
            self._sync_pad()
            return
        if len(data) == PING_SIZE:
            try:
                self.sock.sendto(data, addr)
            except OSError:
                pass  # A failed echo must never take the pad down mid-game.
            return
        self._sync_pad()
        if data == self.last:
            self.last_state_at = now
            return
        state = decode(data)
        if state is None:
            return
        self.last_state_at = now
        if self.pad is not None:
            apply(self.pad, state)
            self.last = data
            if self.on_input:
                self.on_input(state)

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

    def _send_rumble(self, refresh=False):
        now = self.clock()
        if self.playing is not None and self.playing_until is not None and now >= self.playing_until:
            self.playing = None
        wanted = RUMBLE_STOPPED
        if self.playing is not None:
            strong, weak, _ = self.effects[self.playing]
            if strong or weak:
                wanted = (strong, weak, self.playing_until)
        if wanted == self.rumble_sent and not (refresh and wanted != RUMBLE_STOPPED):
            return
        before = self.rumble_sent[:2]
        self.rumble_sent = wanted
        self.rumble_sent_at = now
        strong, weak, until = wanted
        ms = 0 if until is None else min(max(round((until - now) * 1000), 1), 0xffff)
        self.send_to_phone(rumble_message(strong, weak, ms))
        if self.on_rumble and (strong, weak) != before:
            self.on_rumble(round(strong / 0xffff, 2), round(weak / 0xffff, 2))

    def send_to_phone(self, message):
        """Send to the active phone over its link. Audio and rumble go only to a phone that said it can use them."""
        if self.active is None or (self.active != USB and self.active not in self.allowed):
            return
        if message[:4] == AUDIO_MAGIC and not (self.hello or {}).get('audio'):
            return
        if message[:4] == RUMBLE_MAGIC and not (self.hello or {}).get('rumble'):
            return
        if self.active == USB:
            if self.send_usb:
                self.send_usb(message)
            return
        try:
            self.sock.sendto(message, self.addr)
        except OSError:
            pass  # Like the ping echo, a failed send must never take the pad down.

    def wants_audio(self):
        """Sound goes to the active phone, on either link, once it said it can play it.

        A link switch keeps the phone and its hello, so the sink stays loaded and apps stay on it.
        """
        return self.active is not None and bool((self.hello or {}).get('audio'))

    def on_tick(self):
        now = self.clock()
        if self.active:
            self._sync_pad()
        if self.pad is not None:
            self._send_rumble(refresh=self.playing_until is None and now - self.rumble_sent_at >= RUMBLE_REFRESH)
        if self.pad is not None and self.last != NEUTRAL and now - self.last_state_at > TIMEOUT:
            apply(self.pad, decode(NEUTRAL))
            self.last = NEUTRAL
            if self.on_input:
                self.on_input(decode(NEUTRAL))
        if self.active and now - self.last_seen > PHONE_TIMEOUT:
            self._drop_phone()
            self.on_status()
        # A phone that left shouldn't keep asking for approval.
        gone = [ip for ip, seen in self.pending_seen.items() if now - seen > PHONE_TIMEOUT]
        for ip in gone:
            self.pending.pop(ip, None)
            del self.pending_seen[ip]
        if gone:
            self.on_status()

    def close(self):
        if self.pad is not None:
            self._close_pad()


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


def write_event(fd, event):
    """A slow window must never delay the pad, so a full pipe drops this event instead of blocking."""
    try:
        os.write(fd, (json.dumps(event) + '\n').encode())
    except BlockingIOError:
        pass


def sync_sound(sound, wanted):
    """The Musubi sink exists only while a phone wants sound and this PC allows it."""
    if wanted:
        sound.start()
    else:
        sound.stop()


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
