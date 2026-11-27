"""PC sound for the phone: a virtual output device whose sound is captured and cut into small packets."""
import fcntl
import logging
import os
import subprocess
import sys
import tempfile
import time
from array import array

log = logging.getLogger('musubi')
if not log.handlers:
    log.addHandler(logging.NullHandler())

SINK = 'musubi'
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
LOAD = ['pactl', 'load-module', 'module-null-sink', f'sink_name={SINK}',
        'sink_properties=device.description=Musubi']
# The latency request also shrinks PipeWire's quantum to 256 frames, so reads arrive every 5.3 ms instead of 21.
CAPTURE = ['parec', '-d', f'{SINK}.monitor', '--raw', '--format=s16le', f'--rate={RATE}',
           f'--channels={CHANNELS}', '--latency-msec=5']


def split_chunks(buffered, size=CHUNK):
    """Return (whole chunks, leftover bytes)."""
    end = len(buffered) - len(buffered) % size
    return [buffered[i:i + size] for i in range(0, end, size)], buffered[end:]


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


class PhoneAudio:
    """The "Musubi" sink and its capture, which exist only while a phone can play the sound."""

    def __init__(self, run=subprocess.run, popen=subprocess.Popen):
        self.run = run
        self.popen = popen
        self.module = None
        self.capture = None
        self.buffered = b''
        self.seq = 0
        self.quiet = HANGOVER
        self.failed = False
        self.chunk = CHUNK
        self.silence = SILENCE
        self.hangover = HANGOVER

    def set_stable(self, on):
        """Only the packet size changes, so parec and the sink carry on; the leftover bytes start the next packet."""
        self.chunk = STABLE_CHUNK if on else CHUNK
        if len(self.silence) != self.chunk:
            self.silence = bytes(self.chunk)
            self.hangover = HANGOVER * CHUNK // self.chunk

    def _pactl(self, *args):
        return self.run(['pactl', *args], capture_output=True, text=True, check=True, timeout=5).stdout

    def remove_stale(self):
        """A killed service can't unload its sink, so the next one does."""
        try:
            for line in self._pactl('list', 'short', 'modules').splitlines():
                fields = line.split('\t')
                if len(fields) > 2 and fields[1] == 'module-null-sink' and f'sink_name={SINK}' in fields[2].split():
                    self._pactl('unload-module', fields[0])
                    log.info('removed stale audio sink module %s', fields[0])
        except (OSError, subprocess.SubprocessError) as ex:
            self._fail(ex)

    def start(self):
        if self.capture is not None or self.failed:
            return
        try:
            self.module = self.run(LOAD, capture_output=True, text=True, check=True, timeout=5).stdout.strip()
            self.capture = self.popen(CAPTURE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as ex:
            self._fail(ex)
            self._close()
        else:
            log.info('phone audio started (module %s)', self.module)

    def _close(self):
        if self.capture is not None or self.module:
            log.info('phone audio stopped')
        if self.capture is not None:
            self.capture.terminate()
            self.capture.wait()
            self.capture.stdout.close()
            self.capture = None
        if self.module:
            try:
                self._pactl('unload-module', self.module)
            except (OSError, subprocess.SubprocessError) as ex:
                self._fail(ex)
            self.module = None
        self.buffered = b''
        self.quiet = HANGOVER

    def stop(self):
        self._close()
        self.failed = False

    def _fail(self, ex):
        """Sound is extra, so it gives up until stopped and the pad carries on."""
        if not self.failed:
            log.warning('no phone audio: %s', ex)
            print(f'musubi: no phone audio: {ex}', file=sys.stderr)
        self.failed = True

    def sources(self):
        return [self.capture.stdout] if self.capture is not None else []

    def read(self):
        """The PCM worth sending, as 5 or 7.5 ms chunks with their sequence numbers."""
        data = os.read(self.capture.stdout.fileno(), 65536)
        if not data:
            self._fail(RuntimeError('parec stopped'))
            self._close()
            return []
        chunks, self.buffered = split_chunks(self.buffered + data, self.chunk)
        numbered = []
        for chunk in chunks:
            if chunk != self.silence:
                self.quiet = 0
            elif self.quiet < self.hangover:
                self.quiet += 1
            else:
                continue
            # Skipped silence takes no number, so a gap the phone sees is always a lost packet.
            numbered.append((self.seq, chunk))
            self.seq = (self.seq + 1) & 0xffff
        return numbered


MIC_SOURCE = 'musubi_mic'
# 4 KB holds 42 ms, so a fast phone clock can't build up more voice delay than that (the default 64 KB is 680 ms).
MIC_PIPE_BYTES = 4096
# The phone restarts its sequence at 0 whenever the mic restarts, so after this long a low number is a new stream.
MIC_GAP = 0.5
# The quotes inside the argv item reach pactl, which needs them to keep the space in the description.
MIC_DESCRIPTION = 'source_properties="device.description=\\"Musubi mic\\""'


class PhoneMic:
    """The "Musubi mic" input device: a PipeWire pipe source fed with the phone's mic packets."""

    def __init__(self, run=subprocess.run, clock=time.monotonic,
                 path=os.path.join(os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir(), 'musubi-mic')):
        self.run = run
        self.clock = clock
        self.path = path
        self.module = None
        self.fd = None
        self.last_seq = None
        self.last_at = 0.0
        self.failed = False

    def _pactl(self, *args):
        return self.run(['pactl', *args], capture_output=True, text=True, check=True, timeout=5).stdout

    def remove_stale(self):
        """A killed service can't unload its device, so the next one does."""
        try:
            for line in self._pactl('list', 'short', 'modules').splitlines():
                fields = line.split('\t')
                if len(fields) > 2 and fields[1] == 'module-pipe-source' and f'source_name={MIC_SOURCE}' in fields[2].split():
                    self._pactl('unload-module', fields[0])
                    log.info('removed stale mic source module %s', fields[0])
        except (OSError, subprocess.SubprocessError) as ex:
            self._fail(ex)

    def load(self):
        if self.module or self.failed:
            return
        try:
            self.module = self._pactl('load-module', 'module-pipe-source', f'source_name={MIC_SOURCE}', f'file={self.path}',
                                      'format=s16le', f'rate={RATE}', 'channels=1', MIC_DESCRIPTION).strip()
        except (OSError, subprocess.SubprocessError) as ex:
            self._fail(ex)
        else:
            log.info('phone mic loaded (module %s, fifo %s)', self.module, self.path)

    def _open(self):
        # PipeWire holds the read end once the module is loaded; until then this fails and the next packet retries.
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return
        try:
            fcntl.fcntl(fd, getattr(fcntl, 'F_SETPIPE_SZ', 1031), MIC_PIPE_BYTES)
        except OSError:
            pass
        self.fd = fd
        log.info('phone mic pipe opened')

    def feed(self, seq, pcm):
        """Never blocks: the receiver loop also drives the pad, so a full pipe loses this chunk."""
        if not self.module or self.failed or len(pcm) % 2:
            return
        if self.fd is None:
            self._open()
            if self.fd is None:
                return
        now = self.clock()
        # 16-bit wrap-around: only a number just ahead of the last one is new, unless the stream restarted.
        if self.last_seq is not None and now - self.last_at < MIC_GAP and not 0 < (seq - self.last_seq) & 0xffff < 0x8000:
            return
        self.last_seq = seq
        self.last_at = now
        try:
            os.write(self.fd, pcm)
        except BlockingIOError:
            pass
        except OSError as ex:
            self._fail(RuntimeError(f'mic pipe stopped: {ex}'))
            self.stop()

    def stop(self):
        if self.fd is not None or self.module:
            log.info('phone mic stopped')
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.module:
            try:
                self._pactl('unload-module', self.module)
            except (OSError, subprocess.SubprocessError) as ex:
                self._fail(ex)
            self.module = None

    def _fail(self, ex):
        if not self.failed:
            log.warning('no phone mic: %s', ex)
            print(f'musubi: no phone mic: {ex}', file=sys.stderr)
        self.failed = True
