"""PC sound for the phone: a virtual output device whose sound is captured and cut into small packets."""
import fcntl
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # core.py is shared with Windows, one folder up

from core import CHANNELS, CHUNK, HANGOVER, Levels, RATE, SILENCE, STABLE_CHUNK, number_chunks, split_chunks  # noqa: E402,F401

SINK = 'musubi'
LOAD = ['pactl', 'load-module', 'module-null-sink', f'sink_name={SINK}',
        'sink_properties=device.description=Musubi']
# The latency request also shrinks PipeWire's quantum to 256 frames, so reads arrive every 5.3 ms instead of 21.
CAPTURE = ['parec', '-d', f'{SINK}.monitor', '--raw', '--format=s16le', f'--rate={RATE}',
           f'--channels={CHANNELS}', '--latency-msec=5']


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
            self.stop()

    def stop(self):
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

    def _fail(self, ex):
        """Sound is extra, so it gives up for this run and the pad carries on."""
        if not self.failed:
            print(f'musubi: no phone audio: {ex}', file=sys.stderr)
        self.failed = True

    def sources(self):
        return [self.capture.stdout] if self.capture is not None else []

    def read(self):
        """The PCM worth sending, as 5 or 7.5 ms chunks with their sequence numbers."""
        data = os.read(self.capture.stdout.fileno(), 65536)
        if not data:
            self._fail(RuntimeError('parec stopped'))
            self.stop()
            return []
        chunks, self.buffered = split_chunks(self.buffered + data, self.chunk)
        numbered, self.seq, self.quiet = number_chunks(chunks, self.seq, self.quiet, self.silence, self.hangover)
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
            print(f'musubi: no phone mic: {ex}', file=sys.stderr)
        self.failed = True
