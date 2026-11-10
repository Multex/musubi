"""Phones over USB: switch an Android phone into accessory mode (AOA) and pass its packets on."""
import collections
import errno
import glob
import socket
import struct
import threading
import time

import usb.core
import usb.util

# These must match res/xml/accessory_filter.xml, or Android won't open the app.
STRINGS = ['Musubi', 'Musubi', 'your PC', '1',
           'https://github.com/Multex/musubi/releases/latest', '1']
GOOGLE = 0x18d1
# With and without USB debugging on.
ACCESSORY = (0x2d00, 0x2d01)
GET_PROTOCOL, SEND_STRING, START = 51, 52, 53
# MTP, PTP and ADB. Charging-only mode shows just ADB when USB debugging is on. Same list as the udev rule.
PHONE_INTERFACES = {(0xff, 0xff, 0x00), (0x06, 0x01, 0x01), (0xff, 0x42, 0x01)}
# The accessory driver on the phone drops data when a read asks for less than this.
READ_SIZE = 16384
SCAN_INTERVAL = 1
# Every message, both ways, starts with its length, since USB doesn't keep message boundaries.
LENGTH = struct.Struct('!H')
PING_SIZE = 8
# About 40 ms of audio. Older messages are dropped instead, since late audio is useless.
QUEUE_SIZE = 8
NO_ACCESS = "Musubi can't open USB phones. Run ./install.sh again, then plug the phone back in."


def looks_like_phone(dev):
    try:
        return any((i.bInterfaceClass, i.bInterfaceSubClass, i.bInterfaceProtocol) in PHONE_INTERFACES
                   for cfg in dev for i in cfg)
    except usb.core.USBError:
        return False


def is_accessory(dev):
    return dev.idVendor == GOOGLE and dev.idProduct in ACCESSORY


def switch(dev):
    """Ask a phone to come back as an accessory. True if it agreed."""
    data = dev.ctrl_transfer(0xc0, GET_PROTOCOL, 0, 0, 2)
    if len(data) < 2 or data[0] | data[1] << 8 < 1:
        return False
    for index, value in enumerate(STRINGS):
        dev.ctrl_transfer(0x40, SEND_STRING, 0, index, value.encode() + b'\0')
    dev.ctrl_transfer(0x40, START, 0, 0, None)
    return True


def split_frames(buffered):
    """Return (messages, leftover bytes) from the phone's length-prefixed stream."""
    messages = []
    while len(buffered) >= LENGTH.size:
        (size,) = LENGTH.unpack_from(buffered)
        if len(buffered) < LENGTH.size + size:
            break
        messages.append(buffered[LENGTH.size:LENGTH.size + size])
        buffered = buffered[LENGTH.size + size:]
    return messages, buffered


def frame(message):
    return LENGTH.pack(len(message)) + message


class Cable:
    """Background thread. Messages arrive on `self.reader` as datagrams, and `self.error` says what's blocking USB."""

    def __init__(self):
        self.reader, self.writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.error = None
        # Devices that aren't phones or refused to switch, so they're asked only once.
        self.skipped = set()
        self.outbox = collections.deque(maxlen=QUEUE_SIZE)
        self.wake = threading.Condition()
        threading.Thread(target=self._run, name='musubi-cable', daemon=True).start()

    def send(self, message):
        """Queue a message for the phone. Safe from any thread, and never blocks."""
        with self.wake:
            self.outbox.append(frame(message))
            self.wake.notify()

    def _write(self, endpoint, done):
        """Its own thread, so a phone that stops reading can't stall the reads."""
        while True:
            with self.wake:
                while not self.outbox and not done.is_set():
                    self.wake.wait()
                if done.is_set():
                    return
                data = self.outbox.popleft()
            # A timeout that sent part of a frame returns the count instead of raising, and the rest
            # must follow or the phone loses the framing. A timeout that sent nothing raises.
            sent = 0
            while sent < len(data):
                try:
                    sent += endpoint.write(data[sent:], timeout=1000)
                except usb.core.USBTimeoutError:
                    if sent == 0 or done.is_set():
                        break  # Lost, like a dropped datagram.
                except usb.core.USBError:
                    return  # Unplugged.

    def _run(self):
        seen = None
        while True:
            # Far cheaper than a libusb scan. Device numbers change on every reconnect, unlike sysfs names, which follow the port.
            now = set(glob.glob('/dev/bus/usb/*/*'))
            if now != seen:
                seen = now
                try:
                    if self._scan():
                        seen = None  # A phone can stay plugged in after its session ends, so look again.
                except usb.core.NoBackendError:
                    self.error = 'Install libusb to connect phones with a cable.'
                    return
            time.sleep(SCAN_INTERVAL)

    def _scan(self):
        for dev in usb.core.find(find_all=True):
            key = (dev.bus, dev.address)
            if key in self.skipped:
                continue
            try:
                if is_accessory(dev):
                    self._serve(dev)
                    return True
                if looks_like_phone(dev) and switch(dev):
                    continue  # It reconnects as an accessory, and the next scan finds it.
            except usb.core.USBError as ex:
                if ex.errno == errno.EACCES:
                    self.error = NO_ACCESS
                    continue  # Asked again once the user fixes it and plugs in again.
            self.skipped.add(key)

    def _serve(self, dev):
        try:
            try:
                intf = dev.get_active_configuration()[(0, 0)]
            except usb.core.USBError:
                dev.set_configuration()
                intf = dev.get_active_configuration()[(0, 0)]
            endpoint_in = usb.util.find_descriptor(
                intf, custom_match=lambda ep: usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN)
            endpoint_out = usb.util.find_descriptor(
                intf, custom_match=lambda ep: usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT)
            usb.util.claim_interface(dev, intf)
        except usb.core.USBError as ex:
            self.error = NO_ACCESS if ex.errno == errno.EACCES else str(ex)
            return
        self.error = None
        with self.wake:
            self.outbox.clear()
        done = threading.Event()
        writer_thread = threading.Thread(target=self._write, args=(endpoint_out, done), name='musubi-cable-writer', daemon=True)
        writer_thread.start()
        buffered = b''
        try:
            while True:
                try:
                    buffered += bytes(endpoint_in.read(READ_SIZE, timeout=1000))
                except usb.core.USBTimeoutError:
                    continue  # The app is paused or not open yet.
                messages, buffered = split_frames(buffered)
                for message in messages:
                    if len(message) == PING_SIZE:
                        self.send(message)
                    else:
                        self.writer.send(message)
        except usb.core.USBError:
            pass  # Unplugged.
        finally:
            with self.wake:
                done.set()
                self.wake.notify()
            writer_thread.join()  # At most one write timeout, and the device must not be released under it.
            usb.util.dispose_resources(dev)
