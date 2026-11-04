#!/usr/bin/env python3

"""
Check pygospel.py against the real libkyrka module.

    $ make smoke

This needs no cathedral and reaches no network: it points a chat at a
closed port on localhost and confirms that libkyrka accepts everything we
hand it (a KEK, a cathedral secret, a COSK, a flock, a tunnel) and that
the chat keeps producing ciphertext. Anything wrong in how we configure a
context shows up here rather than in a running program.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src"))

try:
    import libkyrka
except ImportError:
    sys.exit("smoke.py needs the libkyrka module, run 'make'")

import pygospel  # noqa: E402

PORT   = 4599

CONFIG = """\
vigil 1
instance smoke
mode gospel
spi 0c
liturgy_group 0001
kek %s
cathedral 127.0.0.1:%d
cathedral_id 5eb2411f
cathedral_flock 493abf95a07e0c00
cathedral_secret %s
cathedral_cosk %s
""" % ("11" * 32, PORT, "22" * 32, "33" * 64)

failures = []


def check(what, got, want):
    if isinstance(want, bool):
        ok(what, got is want)
        return

    if got != want:
        failures.append("%s: got %r, want %r" % (what, got, want))


def ok(what, condition):
    if not condition:
        failures.append("%s: not true" % what)


def main():
    print("libkyrka %s" % libkyrka.version())

    cfg = pygospel.GospelConfig.parse(CONFIG)

    check("spi", cfg.spi, 0x0c)
    check("flock", cfg.cathedral_flock, 0x493abf95a07e0c00)
    check("mtu", cfg.mtu, pygospel.MTU_DEFAULT)

    chat = pygospel.GospelChat(CONFIG, nickname="smoker", p2p=False,
        group=0x0001, signal_all=True)

    # A signaling liturgy and a group liturgy, nothing more yet.
    check("two contexts", len(chat._owners), 2)

    msg = chat.send(0x0d, "blessed sanctum")

    ok("held for the peer", len(chat.peer(0x0d).outbox) == 1)

    for _ in range(8):
        chat.poll(0.05)

    ok("ciphertext left us", chat.tx > 0)
    check("nothing came in", chat.rx, 0)
    ok("still held", len(chat.peer(0x0d).outbox) == 1)
    check("not delivered", msg.delivered, False)
    ok("status works", chat.status()["id"] == "0c")

    # A tunnel to a peer we never reach must not blow up either.
    chat.join(0x0007)
    chat.poll(0.05)
    chat.send_group("anyone in this group") if chat.members() else None

    chat.leave()
    chat.poll(0.05)

    chat.close()

    check("contexts gone", len(chat._owners), 0)

    # Turning p2p on must be fine too, there is just nothing to discover.
    direct = pygospel.GospelChat(CONFIG, nickname="smoker", p2p=True, group=0)
    direct.send(0x0d, "with p2p")
    direct.poll(0.05)
    ok("p2p chat sent", direct.tx > 0)
    direct.close()

    for fail in failures:
        print("FAIL " + fail)

    if failures:
        print("%d failures" % len(failures))
        return 1

    print("smoke ok, %d bytes of cathedral traffic produced" %
        (chat.tx + direct.tx))

    return 0


if __name__ == "__main__":
    sys.exit(main())
