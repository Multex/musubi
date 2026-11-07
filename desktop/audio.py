"""PC sound for the phone: a virtual output device whose sound is captured and cut into small packets."""
import os
import subprocess
import sys

SINK = 'musubi'
RATE = 48000
CHANNELS = 2
# 5 ms of 16-bit stereo per packet, which fits the phone's 2048-byte UDP buffer.
CHUNK = RATE * CHANNELS * 2 * 5 // 1000
LOAD = ['pactl', 'load-module', 'module-null-sink', f'sink_name={SINK}',
        'sink_properties=device.description="Musubi (phone)"']
# The latency request also shrinks PipeWire's quantum to 256 frames, so reads arrive every 5.3 ms instead of 21.
CAPTURE = ['parec', '-d', f'{SINK}.monitor', '--raw', '--format=s16le', f'--rate={RATE}',
           f'--channels={CHANNELS}', '--latency-msec=5']


def split_chunks(buffered):
    """Return (whole chunks, leftover bytes)."""
    end = len(buffered) - len(buffered) % CHUNK
    return [buffered[i:i + CHUNK] for i in range(0, end, CHUNK)], buffered[end:]


class PhoneAudio:
    """The "Musubi (phone)" sink and its capture, which exist only while a phone can play the sound."""

    def __init__(self, run=subprocess.run, popen=subprocess.Popen):
        self.run = run
        self.popen = popen
        self.module = None
        self.capture = None
        self.buffered = b''
        self.seq = 0
        self.failed = False

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

    def _fail(self, ex):
        """Sound is extra, so it gives up for this run and the pad carries on."""
        if not self.failed:
            print(f'musubi: no phone audio: {ex}', file=sys.stderr)
        self.failed = True

    def sources(self):
        return [self.capture.stdout] if self.capture is not None else []

    def read(self):
        """The PCM that's ready, as 5 ms chunks with their sequence numbers."""
        data = os.read(self.capture.stdout.fileno(), 65536)
        if not data:
            self._fail(RuntimeError('parec stopped'))
            self.stop()
            return []
        chunks, self.buffered = split_chunks(self.buffered + data)
        numbered = []
        for chunk in chunks:
            numbered.append((self.seq, chunk))
            self.seq = (self.seq + 1) & 0xffff
        return numbered
