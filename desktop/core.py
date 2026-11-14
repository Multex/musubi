"""What every desktop OS shares: the packets, the phones and their approval, and the saved settings."""
import json
import os
import socket
import struct
import sys
import time
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
# The app times the echo of these to tell whether the PC is answering.
PING_SIZE = 8
# Magic, controller connected, ping in ms, then "phone name\0controller name\0" a features byte and a 16-bit sound delay in ms (0 = none). Sent once a second.
HELLO = struct.Struct('!4sBH')
HELLO_MAGIC = b'PBHI'
# Feature bits stay below 0x20: 0.5.x desktops read the byte as part of the controller name and drop it as a control character.
FEATURE_AUDIO = 1
FEATURE_RUMBLE = 2
# "Most stable": the phone asks for 7.5 ms audio packets instead of 5.
FEATURE_STABLE = 4
# PC to phone: magic, sequence number, then PCM.
AUDIO = struct.Struct('!4sH')
AUDIO_MAGIC = b'PBAU'
# PC to phone: magic, strong and weak motor 0..65535, then ms left (0 = until the next message). Sent on change only.
RUMBLE = struct.Struct('!4sHHH')
RUMBLE_MAGIC = b'PBRM'
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


def clean_name(raw):
    """Names come straight off the network, so they get no control characters or unbounded length."""
    text = raw.decode('utf-8', 'replace')
    return ''.join(c for c in text if c.isprintable())[:MAX_NAME]


def name_message(name):
    """The name cut to MAX_NAME bytes without splitting a character."""
    return NAME_MAGIC + name.encode('utf-8')[:MAX_NAME].decode('utf-8', 'ignore').encode('utf-8')


def decode_hello(data):
    """Return {name, controller, controller_connected, rtt, audio, rumble, stable, delay}, or None if this isn't a hello."""
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
        'stable': bool(flags & FEATURE_STABLE),
        'delay': delay or None,
    }


def audio_message(seq, pcm):
    return AUDIO.pack(AUDIO_MAGIC, seq & 0xffff) + pcm


def rumble_message(strong, weak, ms):
    return RUMBLE.pack(RUMBLE_MAGIC, strong, weak, ms)


class Bridge:
    """Drives one virtual pad from the first allowed phone that talks to it.

    The pad comes from `make_pad` and needs `apply(state)` and `close()`.
    """

    def __init__(self, sock, allowed, on_status, on_input=None, on_rumble=None, make_pad=None, clock=time.monotonic,
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
        # What the game wants from the motors now: strong, weak, and when it ends (None = until told).
        self.rumble = RUMBLE_STOPPED
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

    def _apply(self, state):
        self.pad.apply(state)

    def _forget_rumble(self):
        self.rumble = RUMBLE_STOPPED

    def _wanted_rumble(self, now):
        return self.rumble

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
        self._forget_rumble()
        self._send_rumble()
        self._apply(decode(NEUTRAL))
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
            if ip == USB:
                self.send_to_phone(name_message(socket.gethostname()))
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
            self._apply(state)
            self.last = data
            if self.on_input:
                self.on_input(state)

    def _send_rumble(self, refresh=False):
        now = self.clock()
        wanted = self._wanted_rumble(now)
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

    def set_rumble(self, strong, weak):
        """For pads that report motor levels, 0..65535, instead of effects."""
        self.rumble = (strong, weak, None) if strong or weak else RUMBLE_STOPPED
        self._send_rumble()

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
            self._send_rumble(refresh=self.rumble_sent[2] is None and now - self.rumble_sent_at >= RUMBLE_REFRESH)
        if self.pad is not None and self.last != NEUTRAL and now - self.last_state_at > TIMEOUT:
            self._apply(decode(NEUTRAL))
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
    config = {'phones': {}, 'tray': True, 'sound_enabled': True}
    try:
        stored = json.loads(path.read_text())
        # shortcut: phones are identified by IP, so a phone whose IP changes asks for approval again, add pairing keys if that gets annoying.
        config['phones'] = {str(ip): str(name) for ip, name in stored.get('phones', {}).items()}
        config['tray'] = bool(stored.get('tray', True))
        config['sound_enabled'] = bool(stored.get('sound_enabled', True))
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
