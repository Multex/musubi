"""PC sound for the phone: a WASAPI loopback capture of the default output, cut into 5 ms chunks."""
import logging
import threading
from array import array

import core

log = logging.getLogger('musubi')


def stereo(data, channels):
    """Only the first two channels of 16-bit frames with `channels` channels."""
    if channels == 2:
        return data
    samples = array('h', data)
    out = array('h', bytes(len(samples) // channels * 4))
    out[0::2] = samples[0::channels]
    out[1::2] = samples[1::channels]
    return out.tobytes()


class Capture:
    def __init__(self, audio, stream, name, channels):
        self.audio = audio
        self.stream = stream
        self.name = name
        self.channels = channels

    def close(self):
        for action in (self.stream.stop_stream, self.stream.close, self.audio.terminate):
            try:
                action()
            except OSError:
                pass


# shortcut: the capture stays on the output that was the default when it started, reopen it when the default output changes if users plug headphones mid-game
def open_capture(on_pcm):
    """Starts calling on_pcm with each 960-byte chunk, from a PortAudio thread, and returns the Capture."""
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        raise OSError('The sound library is missing from this build.') from None
    try:
        audio = pyaudio.PyAudio()
    except OSError as ex:
        raise OSError(f'PC sound could not start: {ex}') from ex
    try:
        try:
            device = audio.get_default_wasapi_loopback()
        except (OSError, LookupError, ValueError):
            raise OSError('No sound output was found on this PC.') from None
        rate = int(device['defaultSampleRate'])
        # PortAudio's WASAPI side converts the sample format but not the rate.
        if rate != core.RATE:
            raise OSError(f'PC sound needs the sound output set to 48000 Hz, and it is at {rate} Hz. Change it in '
                          'the Windows sound settings, then turn Play PC sound off and on again on the phone.')
        channels = device['maxInputChannels']
        if channels < 2:
            raise OSError('PC sound needs a stereo sound output.')
        buffered = b''
        failed = False

        def callback(data, frames, time_info, flags):
            nonlocal buffered, failed
            # An exception here would make PortAudio stop the stream for good.
            try:
                chunks, buffered = core.split_chunks(buffered + stereo(data, channels))
                for chunk in chunks:
                    on_pcm(chunk)
            except Exception:
                if not failed:
                    failed = True
                    log.exception('sound callback')
            return None, pyaudio.paContinue

        try:
            stream = audio.open(format=pyaudio.paInt16, channels=channels, rate=core.RATE, input=True,
                                input_device_index=device['index'], frames_per_buffer=240, stream_callback=callback)
        except OSError as ex:
            raise OSError(f'PC sound could not start: {ex}') from ex
    except BaseException:
        try:
            audio.terminate()
        except OSError:
            pass
        raise
    return Capture(audio, stream, device['name'], channels)


class Sound:
    """The capture, open only while a phone wants the sound. A failed start waits for the phone to ask again."""

    def __init__(self, on_pcm, open_capture=open_capture):
        self.on_pcm = on_pcm
        self.open_capture = open_capture
        self.capture = None
        self.error = None
        self.wanted = False
        self.opening = None
        self.lock = threading.Lock()

    @property
    def capturing(self):
        return self.capture is not None

    def start(self):
        with self.lock:
            self.wanted = True
            if self.capture is not None or self.error or self.opening is not None:
                return
            # On a thread: Windows took 3 s to say there was no sound device, and the controller froze meanwhile.
            self.opening = threading.Thread(target=self._open, daemon=True)
        self.opening.start()

    def _open(self):
        capture = error = None
        try:
            capture = self.open_capture(self.on_pcm)
        except OSError as ex:
            error = str(ex)
        with self.lock:
            self.opening = None
            kept = self.wanted
            if kept:
                self.capture, self.error = capture, error
        if not kept:
            # The phone stopped asking while this was opening.
            if capture is not None:
                capture.close()
        elif error:
            log.error('sound: %s', error)
        else:
            log.info('sound started, %s, %d Hz, %d channels', capture.name, core.RATE, capture.channels)

    def stop(self):
        with self.lock:
            self.wanted = False
            self.error = None
            capture, self.capture = self.capture, None
        if capture is not None:
            capture.close()
            log.info('sound stopped')
