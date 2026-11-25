#!/usr/bin/env python3
"""A pretend phone for trying the app by hand or in CI: a controller and the sound feature, sent to 127.0.0.1.

Usage: fake_phone.py SECONDS [PORT]
"""
import math
import socket
import sys
import time

import paths  # noqa: F401
import core


def main():
    seconds = float(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else core.PORT
    phone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    hello = (core.HELLO.pack(core.HELLO_MAGIC, 1, 8) + b'Fake phone\0Xbox Wireless Controller\0'
             + bytes([core.FEATURE_AUDIO]) + (140).to_bytes(2, 'big'))
    started = time.monotonic()
    next_hello = 0
    while (now := time.monotonic() - started) < seconds:
        if now >= next_hello:
            next_hello = now + 1
            phone.sendto(hello, ('127.0.0.1', port))
        # The left stick going round and A pressed every other second, so the drawing visibly moves.
        state = core.PACKET.pack(int(now) % 2, int(32767 * math.cos(now)), int(32767 * math.sin(now)), 0, 0, 0, 0, 0, 0)
        phone.sendto(state, ('127.0.0.1', port))
        time.sleep(0.3)
    phone.close()


if __name__ == '__main__':
    main()
