"""What every desktop OS shares: the packets, the phones and their approval, and the saved settings."""
import json
import os
import socket
import struct
import sys
import time
from array import array
from pathlib import Path

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
MAX_PLAYERS = 4
# Multi-pad input: magic, slot index 0..3, then the same 20-byte state as PACKET. Slot 0 keeps using PACKET.
INPUT_MAGIC = b'PBIN'
SLOTTED_PACKET = struct.Struct('!4sBI8h')
# The app times the echo of these to tell whether the PC is answering.
PING_SIZE = 8
# Magic, controller connected, ping in ms, then "phone name\0controller name\0" a features byte, a 16-bit sound delay in ms (0 = none) and the mic state (0 off, 1 live, 2 muted, 3 paused), optional slots mask (bits 0..3) and slots 1..3 names separated by \0. Sent once a second.
HELLO = struct.Struct('!4sBH')
HELLO_MAGIC = b'PBHI'
# Feature bits stay below 0x20: 0.5.x desktops read the byte as part of the controller name and drop it as a control character.
FEATURE_AUDIO = 1
FEATURE_RUMBLE = 2
# "Most stable": the phone asks for 7.5 ms audio packets instead of 5.
FEATURE_STABLE = 4
# Phone to PC: magic, 16-bit sequence number, then 5 ms of mono 48 kHz s16le PCM (480 bytes).
MIC_MAGIC = b'PBMC'
# PC to phone: magic, sequence number, then PCM.
AUDIO = struct.Struct('!4sH')
AUDIO_MAGIC = b'PBAU'
RATE = 48000
CHANNELS = 2
# 5 ms of 16-bit stereo per packet, which fits the phone's 2048-byte UDP buffer.
CHUNK = RATE * CHANNELS * 2 * 5 // 1000
# 7.5 ms for a phone set to "Most stable": a third fewer packets, and 1440 bytes still fit one Wi-Fi frame (10 ms would split in two).
STABLE_CHUNK = CHUNK * 3 // 2
# A null sink's monitor gives exact zeros while nothing plays, so those chunks aren't worth sending.
SILENCE = bytes(CHUNK)
# 200 ms of zeros still go out, so a short pause between sounds doesn't make the phone stop and refill.
HANGOVER = 40
# PC to phone: magic, strong and weak motor 0..65535, then ms left (0 = until the next message), optional slot 1..3. Sent on change only.
RUMBLE = struct.Struct('!4sHHH')
RUMBLE_MAGIC = b'PBRM'
# PC to phone: magic, then the mic switch shared with the phone: 0 off, 1 on, 2 on and muted. Phones skip messages they don't know.
MIC_SET = struct.Struct('!4sB')
MIC_SET_MAGIC = b'PBMS'
# PC to phone over the cable: magic, then the PC name in UTF-8. Sent on every hello; Wi-Fi phones name PCs themselves.
NAME_MAGIC = b'PBNM'
RUMBLE_STOPPED = (0, 0, None)
# The phone stops an endless rumble after 2 s unless told again, so a lost stop can't leave the pad buzzing.
RUMBLE_REFRESH = 1.0
MAX_DATAGRAM = 512
MAX_NAME = 64
MAX_PENDING = 8
# Stands in for the IP of a phone on the USB cable.
USB = 'usb'

# Packet order: both sticks, both triggers, then the d-pad as two axes.
AXIS_LIMITS = [(-32767, 32767)] * 4 + [(0, 255)] * 2 + [(-1, 1)] * 2

if sys.platform == 'win32':
    CONFIG_DIR = Path(os.environ.get('APPDATA') or Path.home())
else:
    CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
CONFIG_FILE = CONFIG_DIR / 'musubi' / 'config.json'


def decode(data):
    """Return (button bits, clamped axis values), or None for a malformed packet."""
    if len(data) != PACKET.size:
        return None
    buttons, *axes = PACKET.unpack(data)
    return buttons, [min(max(v, low), high) for v, (low, high) in zip(axes, AXIS_LIMITS)]


def slotted_packet(slot, state_bytes):
    """Wrap a 20-byte state packet for slots 1..3 (slot 0 sends raw PACKET)."""
    if slot == 0:
        return state_bytes
    return INPUT_MAGIC + bytes([slot]) + state_bytes


def decode_input(data):
    """Return (slot, (button bits, clamped axis values)), or None for a malformed packet."""
    if len(data) == PACKET.size:
        state = decode(data)
        return (0, state) if state is not None else None
    if len(data) == SLOTTED_PACKET.size and data[:4] == INPUT_MAGIC:
        slot = data[4]
        if 0 <= slot < MAX_PLAYERS:
            state = decode(data[5:])
            return (slot, state) if state is not None else None
    return None


def clean_name(raw):
    """Names come straight off the network, so they get no control characters or unbounded length."""
    text = raw.decode('utf-8', 'replace')
    return ''.join(c for c in text if c.isprintable())[:MAX_NAME]


def name_message(name):
    """The name cut to MAX_NAME bytes without splitting a character."""
    return NAME_MAGIC + name.encode('utf-8')[:MAX_NAME].decode('utf-8', 'ignore').encode('utf-8')


def decode_hello(data):
    """Return {name, controller, controller_connected, controllers, rtt, audio, rumble, stable, delay, mic}, or None if this isn't a hello."""
    if len(data) < HELLO.size or data[:4] != HELLO_MAGIC:
        return None
    _, connected, rtt = HELLO.unpack_from(data)
    phone, _, rest = data[HELLO.size:].partition(b'\0')
    controller, _, features = rest.partition(b'\0')
    flags = features[0] if features else 0
    delay = int.from_bytes(features[1:3], 'big') if len(features) >= 3 else 0
    mic = features[3] if len(features) >= 4 and features[3] <= 3 else 0
    if len(features) >= 5:
        mask = features[4]
        extra = features[5:].split(b'\0')
        raw = [controller] + (extra[:MAX_PLAYERS - 1] + [b''] * (MAX_PLAYERS - 1))[:MAX_PLAYERS - 1]
        controllers = [clean_name(raw[i]) if (mask & (1 << i)) else None for i in range(MAX_PLAYERS)]
    else:
        controllers = [clean_name(controller) if connected else None] + [None] * (MAX_PLAYERS - 1)
    return {
        'name': clean_name(phone),
        'controller': clean_name(controller),
        'controller_connected': bool(connected) or any(c is not None for c in controllers),
        'controllers': controllers,
        'rtt': rtt,
        'audio': bool(flags & FEATURE_AUDIO),
        'rumble': bool(flags & FEATURE_RUMBLE),
        'stable': bool(flags & FEATURE_STABLE),
        'delay': delay or None,
        'mic': mic,
    }


def audio_message(seq, pcm):
    return AUDIO.pack(AUDIO_MAGIC, seq & 0xffff) + pcm


def split_chunks(buffered, size=CHUNK):
    """Return (whole chunks, leftover bytes)."""
    end = len(buffered) - len(buffered) % size
    return [buffered[i:i + size] for i in range(0, end, size)], buffered[end:]


def number_chunks(chunks, seq, quiet, silence=SILENCE, hangover=HANGOVER):
    """Return ((seq, chunk) pairs worth sending, next seq, next quiet count)."""
    numbered = []
    for chunk in chunks:
        if chunk != silence:
            quiet = 0
        elif quiet < hangover:
            quiet += 1
        else:
            continue
        # Skipped silence takes no number, so a gap the phone sees is always a lost packet.
        numbered.append((seq, chunk))
        seq = (seq + 1) & 0xffff
    return numbered, seq, quiet


def rumble_message(strong, weak, ms, slot=0):
    base = RUMBLE.pack(RUMBLE_MAGIC, strong, weak, ms)
    return base if slot == 0 else base + bytes([slot])


def mic_set_message(state):
    return MIC_SET.pack(MIC_SET_MAGIC, state)


class Levels:
    """Peaks of each channel of the PCM, 0..1, at most `interval` apart.

    Peak because max() and min() over array slices run in C, so a 5 ms chunk costs a few microseconds.
    """

    def __init__(self, interval=1 / 25, channels=CHANNELS):
        self.interval = interval
        self.channels = channels
        self.peak = [0] * channels
        self.last = 0.0
        self.live = False

    def add(self, chunk):
        samples = array('h', chunk)
        for side in range(self.channels):
            channel = samples[side::self.channels]
            self.peak[side] = max(self.peak[side], max(channel), -min(channel))

    def poll(self, now):
        """Return a peak per channel when one is due, a single all-zero one once the sound has stopped, else None."""
        if now - self.last < self.interval:
            return None
        self.last = now
        peaks = self.peak
        self.peak = [0] * self.channels
        if any(peaks):
            self.live = True
        elif self.live:
            self.live = False
        else:
            return None
        return tuple(round(min(peak / 32767, 1), 2) for peak in peaks)


class _PadSlot:
    """State for one of the four virtual controller slots."""

    def __init__(self, index):
        self.index = index
        self.pad = None
        self.last = NEUTRAL
        self.last_state_at = 0
        # What the game wants from the motors now: strong, weak, and when it ends (None = until told).
        self.rumble = RUMBLE_STOPPED
        self.rumble_sent = RUMBLE_STOPPED
        self.rumble_sent_at = 0


class Bridge:
    """Drives up to four virtual pads from the first allowed phone that talks to it.

    Each pad comes from `make_pad(slot)` and needs `apply(state)` and `close()`.
    """

    def __init__(self, sock, allowed, on_status, on_input=None, on_rumble=None, make_pad=None, clock=time.monotonic,
                 send_usb=None, on_mic=None):
        self.sock = sock
        self.send_usb = send_usb
        self.allowed = set(allowed)
        self.on_status = on_status
        self.on_input = on_input
        self.on_rumble = on_rumble
        self.on_mic = on_mic
        self.make_pad = make_pad
        self.clock = clock
        self.slots = [_PadSlot(i) for i in range(MAX_PLAYERS)]
        self.pad_error = None
        self.active = None
        self.addr = None
        self.hello = None
        self.last_seen = 0
        self.active_since = 0
        self.pending = {}
        self.pending_seen = {}

    @property
    def pad(self):
        return self.slots[0].pad

    def open_pads(self):
        return [(slot.index, slot.pad) for slot in self.slots if slot.pad is not None]

    def status(self):
        phone = None
        if self.active:
            hello = self.hello or {}
            controllers = list(hello.get('controllers') or [None] * MAX_PLAYERS)
            phone = {
                'ip': self.active,
                'name': hello.get('name', ''),
                # Version 0.2.0 sends no hello, so None means "can't tell", not "disconnected".
                'controller': controllers[0],
                'controllers': controllers,
                'controller_known': self.hello is not None,
                'rtt': hello.get('rtt'),
                'audio': hello.get('audio', False),
                'delay': hello.get('delay'),
                # None until the first hello, so nobody mistakes "not told yet" for "off".
                'mic': hello.get('mic'),
            }
        pads = [slot.pad is not None for slot in self.slots]
        return {
            'phone': phone,
            'pad': any(pads),
            'pads': pads,
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

    def _apply(self, slot, state):
        slot.pad.apply(state)

    def _forget_rumble(self, slot):
        slot.rumble = RUMBLE_STOPPED

    def _wanted_rumble(self, slot, now):
        return slot.rumble

    def _sync_pads(self):
        changed = False
        any_wanted = False
        before_error = self.pad_error
        for slot in self.slots:
            if self.hello is not None:
                wanted = self.hello['controllers'][slot.index] is not None
            else:
                # Waiting first stops a pad flashing on before a newer app's first hello arrives.
                wanted = slot.index == 0 and self.active is not None and self.clock() - self.active_since >= HELLO_WAIT
            any_wanted = any_wanted or wanted
            if wanted and slot.pad is None and self.pad_error is None:
                try:
                    slot.pad = self.make_pad(slot.index)
                    slot.last = NEUTRAL
                    changed = True
                except OSError as ex:
                    self.pad_error = str(ex)
            elif not wanted and slot.pad is not None:
                self._close_pad(slot)
                changed = True
        if not any_wanted:
            self.pad_error = None
        if changed or self.pad_error != before_error:
            self.on_status()

    def _close_pad(self, slot):
        self._forget_rumble(slot)
        self._send_rumble(slot)
        self._apply(slot, decode(NEUTRAL))
        slot.pad.close()
        slot.pad = None
        slot.last = NEUTRAL
        if self.on_input:
            self.on_input(slot.index, decode(NEUTRAL))

    def _drop_phone(self):
        # Before forgetting the phone, so its final rumble stop still has somewhere to go.
        for slot in self.slots:
            if slot.pad is not None:
                self._close_pad(slot)
        self.active = None
        self.addr = None
        self.hello = None
        self.pad_error = None

    def _note_unknown(self, ip, data):
        hello = decode_hello(data)
        if hello is None and len(data) != PING_SIZE and decode_input(data) is None:
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
        # Junk from an allowed IP (a TV's stray packet) must not make it the active phone.
        if decode_hello(data) is None and len(data) != PING_SIZE and decode_input(data) is None and not data.startswith(MIC_MAGIC):
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

        if data.startswith(MIC_MAGIC) and len(data) >= 6:
            if self.on_mic:
                self.on_mic(int.from_bytes(data[4:6], 'big'), data[6:])
            return
        hello = decode_hello(data)
        if hello is not None:
            if hello != self.hello:
                self.hello = hello
                self.on_status()
            if ip == USB:
                self.send_to_phone(name_message(socket.gethostname()))
            self._sync_pads()
            return
        if len(data) == PING_SIZE:
            try:
                self.sock.sendto(data, addr)
            except OSError:
                pass  # A failed echo must never take the pad down mid-game.
            return
        self._sync_pads()
        parsed = decode_input(data)
        if parsed is None:
            return
        slot_index, state = parsed
        slot = self.slots[slot_index]
        raw = data[-PACKET.size:]
        if raw == slot.last:
            slot.last_state_at = now
            return
        slot.last_state_at = now
        if slot.pad is not None:
            self._apply(slot, state)
            slot.last = raw
            if self.on_input:
                self.on_input(slot.index, state)

    def _send_rumble(self, slot, refresh=False):
        now = self.clock()
        wanted = self._wanted_rumble(slot, now)
        if wanted == slot.rumble_sent and not (refresh and wanted != RUMBLE_STOPPED):
            return
        before = slot.rumble_sent[:2]
        slot.rumble_sent = wanted
        slot.rumble_sent_at = now
        strong, weak, until = wanted
        ms = 0 if until is None else min(max(round((until - now) * 1000), 1), 0xffff)
        self.send_to_phone(rumble_message(strong, weak, ms, slot.index))
        if self.on_rumble and (strong, weak) != before:
            self.on_rumble(slot.index, round(strong / 0xffff, 2), round(weak / 0xffff, 2))

    def set_rumble(self, strong, weak, slot=0):
        """For pads that report motor levels, 0..65535, instead of effects."""
        target = self.slots[slot]
        target.rumble = (strong, weak, None) if strong or weak else RUMBLE_STOPPED
        self._send_rumble(target)

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

    def set_mic(self, state):
        """Switch the phone's mic (0 off, 1 on, 2 muted). The status shows it at once; the phone's next hello confirms or corrects it."""
        if self.hello is None:
            return
        self.send_to_phone(mic_set_message(state))
        # A paused mic stays paused: only the phone knows when its speaker stops.
        if state == 0 or self.hello.get('mic') != 3:
            self.hello = {**self.hello, 'mic': state}
        self.on_status()

    def wants_audio(self):
        """Sound goes to the active phone, on either link, once it said it can play it.

        A link switch keeps the phone and its hello, so the sink stays loaded and apps stay on it.
        """
        return self.active is not None and bool((self.hello or {}).get('audio'))

    def on_tick(self):
        now = self.clock()
        if self.active:
            self._sync_pads()
        for slot in self.slots:
            if slot.pad is not None:
                self._send_rumble(slot, refresh=slot.rumble_sent[2] is None and now - slot.rumble_sent_at >= RUMBLE_REFRESH)
            if slot.pad is not None and slot.last != NEUTRAL and now - slot.last_state_at > TIMEOUT:
                self._apply(slot, decode(NEUTRAL))
                slot.last = NEUTRAL
                if self.on_input:
                    self.on_input(slot.index, decode(NEUTRAL))
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
        for slot in self.slots:
            if slot.pad is not None:
                self._close_pad(slot)


def write_event(fd, event):
    """A slow window must never delay the pad, so a full pipe drops this event instead of blocking."""
    try:
        os.write(fd, (json.dumps(event) + '\n').encode())
    except BlockingIOError:
        pass


def sync_sound(sound, wanted):
    """The sound capture exists only while a phone wants sound and this PC allows it."""
    if wanted:
        sound.start()
    else:
        sound.stop()


def load_config(path=CONFIG_FILE):
    config = {'phones': {}, 'tray': True, 'sound_enabled': True, 'mic': False}
    try:
        stored = json.loads(path.read_text())
        # shortcut: phones are identified by IP, so a phone whose IP changes asks for approval again, add pairing keys if that gets annoying.
        config['phones'] = {str(ip): str(name) for ip, name in stored.get('phones', {}).items()}
        config['tray'] = bool(stored.get('tray', True))
        config['sound_enabled'] = bool(stored.get('sound_enabled', True))
        config['mic'] = bool(stored.get('mic', False))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError):
        # Saving over a file we couldn't read would lose it for good.
        path.replace(path.with_suffix('.broken'))
    return config


def save_config(config, path=CONFIG_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2))
    temporary.replace(path)
