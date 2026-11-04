#!/usr/bin/env python3
"""Receive gamepad state from the Android app and replay it on a virtual pad."""
import argparse
import select
import socket
import struct

from evdev import AbsInfo, UInput, ecodes as e

PORT = 9999
# The app resends the state 20 times per second, so this means the link is gone.
TIMEOUT = 0.5

# Whole state in every packet: a lost one is repaired by the next, so a release can't stick.
# shortcut: no sequence number, a reordered packet glitches until the next one, add it if jitter shows up.
PACKET = struct.Struct('!I8h')
NEUTRAL = PACKET.pack(0, *[0] * 8)
# The app times the echo of these to tell whether the PC is answering.
PING_SIZE = 8

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


def apply(ui, state):
    buttons, axes = state
    for bit, code in enumerate(BUTTONS):
        ui.write(e.EV_KEY, code, buttons >> bit & 1)
    for value, (code, _) in zip(axes, AXES):
        ui.write(e.EV_ABS, code, value)
    ui.syn()


def handle(sock, ui, data, addr, phone_ip, last):
    """Process one datagram and return the packet now applied to the pad."""
    if addr[0] != phone_ip:
        return last
    if len(data) == PING_SIZE:
        try:
            sock.sendto(data, addr)
        except OSError:
            pass  # A failed echo must never take the pad down mid-game.
        return last
    if data == last:
        return last
    state = decode(data)
    if state is None:
        return last
    apply(ui, state)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phone_ip', help='only packets from this address are accepted')
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))
    capabilities = {e.EV_KEY: BUTTONS, e.EV_ABS: AXES}
    with UInput(capabilities, name='padbridge', vendor=0x045e, product=0x028e,
                version=0x110, bustype=e.BUS_USB) as ui:
        print(f'{ui.device.path}: waiting for {args.phone_ip} on UDP {PORT}')
        last = NEUTRAL
        while True:
            if not select.select([sock], [], [], TIMEOUT)[0]:
                if last != NEUTRAL:
                    print('link lost, releasing everything')
                    apply(ui, decode(NEUTRAL))
                    last = NEUTRAL
                continue
            data, addr = sock.recvfrom(64)
            last = handle(sock, ui, data, addr, args.phone_ip, last)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
