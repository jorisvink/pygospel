#!/usr/bin/env python3

"""
Talk through a real cathedral with as many gospel chats as you give it.

    $ ./cathedral.sh up
    $ ./cathedral.sh e2e
    $ ./e2e.py ../../build/cathedral/cathedral/clients/gospel-01.conf \
                ../../build/cathedral/cathedral/clients/gospel-02.conf

The first chat pokes the others, one-to-one every five seconds and, if
--group is given, to the group every seven. It prints what each side hears
and ends with a count of what arrived, so a run either works or says it did
not. Real key exchange, real ambries, real relay.
"""

import os
import sys
import time
import signal
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "build/host/libkyrka/obj/python"))

try:
    import pygospel
except ImportError:
    sys.exit("e2e.py needs the libkyrka module, run 'make' at the top")


class Chat(pygospel.GospelChat):
    """A gospel chat that says out loud what it is told."""

    def __init__(self, path, **kwargs):
        self.log = []
        super().__init__(path, **kwargs)

    def note(self, text):
        self.log.append(text)
        print("  [%02x] %s" % (self.spi, text), flush=True)

    def on_message(self, msg):
        where = "%04x" % msg.group if msg.group is not None else "-"

        if msg.group is None:
            self.note("MSG from %02x: %s" % (msg.peer, msg.text))
        else:
            self.note("MSG from %02x in %s: %s" % (msg.peer, where,
                msg.text))

    def on_sent(self, ref, msg):
        self.note("SENT ref=%d to %02x" % (ref, msg.peer))

    def on_delivered(self, ref, msg):
        self.note("DELIVERED ref=%d to %02x" % (ref, msg.peer))

    def on_group_delivered(self, ref, group, acked, pending):
        self.note("GROUP ref=%d acked=%d pending=%d" % (ref, acked, pending))

    def on_peer(self, peer):
        self.note("PEER %02x online via %s" % (peer.id, self.link(peer.id)))

    def on_name(self, peer):
        self.note("NAME %02x nick=%r name=%r%s" % (peer.id, peer.nick,
            peer.name, " (signed)" if peer.signed else ""))

    def on_joined(self, peer):
        self.note("JOINED %02x in %04x" % (peer.id, self.group))

    def on_left(self, peer):
        self.note("LEFT %02x from %04x" % (peer.id, self.group))

    def on_offline(self):
        self.note("OFFLINE")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--group", type=lambda x: int(x, 16), default=0,
        help="join this group in every chat and send to it")
    ap.add_argument("--text", default="blessed sanctum")
    ap.add_argument("--no-signals-all", action="store_true",
        help="only signal the devices we have a reason to reach")
    ap.add_argument("config", nargs="+",
        help="gospel configurations, one per chat")

    args = ap.parse_args()

    chats = []

    for path in args.config:
        chat = Chat(path, nickname="e" + os.path.basename(path)[-6:-5],
            signal_all=not args.no_signals_all)
        chat.note("up as %02x, %d cathedral(s)" % (chat.spi,
            len(chat.cathedrals)))
        chats.append(chat)

    if args.group:
        for chat in chats:
            chat.join(args.group)
            chat.note("joined %04x" % chat.group)

    stop = []
    signal.signal(signal.SIGINT, lambda s, f: stop.append(True))

    end = time.time() + args.seconds
    sent = 0
    last_direct = 0.0
    last_group = 0.0

    while not stop and time.time() < end:
        now = time.time()

        for chat in chats:
            chat.poll(0.25)

        if len(chats) > 1 and now - last_direct > 5:
            last_direct = now
            chats[0].send(chats[1].spi, "direct #%d" % sent)
            sent += 1

        if args.group and len(chats) > 1 and now - last_group > 7:
            last_group = now
            try:
                chats[0].send_group("group #%d" % sent)
                sent += 1
            except pygospel.GospelError as e:
                chats[0].note("group send failed: %s" % e)

    print()
    print("=== summary ===")

    for chat in chats:
        st = chat.status()
        got = sum(1 for line in chat.log if line.startswith("MSG"))

        print("%02x rx=%-8d tx=%-8d present=%-3d members=%-3d held=%-3d "
            "heard=%d" % (chat.spi, st["rx"], st["tx"], st["present"],
                st["members"], st["held"], got))

    for chat in chats:
        chat.close()

    # Everyone but the first chat should have heard something, the first
    # one is the one doing the talking.
    deaf = [c.spi for c in chats[1:]
        if not any(l.startswith("MSG") for l in c.log)]

    print()

    if deaf:
        print("nothing arrived at: " + ", ".join("%02x" % s for s in deaf))
        return 1

    print("%d chat(s) sent %d messages, everyone heard" % (len(chats), sent))

    return 0


if __name__ == "__main__":
    sys.exit(main())
