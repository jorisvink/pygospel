#!/usr/bin/env python3

"""
Watch a flock and say who is reachable, and how.

    $ PYTHONPATH=build/host/libkyrka/obj/python \\
        python3 examples/presence.py -c gospel.conf

By default this signals every device in the flock, which is the quickest way
to see the whole of it. With -s only the devices that signal us come up.
"""

import os
import sys
import time
import signal
import argparse

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pygospel  # noqa: E402


def stamp():
    return time.strftime("%H:%M:%S")


class Watcher(pygospel.GospelChat):
    def __init__(self, config, nickname, watch):
        self.watch = watch
        super().__init__(config, nickname=nickname, signal_all=watch)

    def on_peer(self, peer):
        print("%s  + %02x %-16s %s" % (stamp(), peer.id, peer.best_name,
            self.link(peer.id)), flush=True)

    def on_name(self, peer):
        print("%s  = %02x now %s" % (stamp(), peer.id, peer.best_name),
            flush=True)

    def on_joined(self, peer):
        print("%s  j %02x joined %04x" % (stamp(), peer.id, self.group),
            flush=True)

    def on_left(self, peer):
        print("%s  l %02x left %04x" % (stamp(), peer.id, self.group),
            flush=True)

    def on_message(self, msg):
        print("%s  > %02x@%s %s" % (stamp(), msg.peer,
            "%04x" % msg.group if msg.group is not None else "-", msg.text),
            flush=True)

    def on_offline(self):
        print("%s  ! the cathedral stopped answering" % stamp(), flush=True)


def main():
    ap = argparse.ArgumentParser(description="a gospel presence watcher")
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("-n", "--nickname", default="presence")
    ap.add_argument("-g", "--group", type=lambda x: int(x, 16), default=0,
        help="join this group too")
    ap.add_argument("-s", "--signals", action="store_true",
        help="only follow the devices that signal us")
    ap.add_argument("-i", "--interval", type=float, default=10.0,
        help="seconds between roster lines")

    args = ap.parse_args()

    chat = Watcher(args.config, args.nickname, not args.signals)

    if args.group:
        chat.join(args.group)

    stop = []
    signal.signal(signal.SIGINT, lambda s, f: stop.append(True))

    print("%s  watching as %02x, %s" % (stamp(), chat.spi,
        "all devices" if chat.signal_all else "whoever signals us"),
        flush=True)

    while not stop:
        chat.poll(1)

        if chat.running and int(time.time()) % max(1, int(args.interval)) == 0:
            around = chat.roster()
            print("%s  %d peers, %d online%s" % (stamp(), len(around),
                sum(1 for p in around if p.online),
                ", group %04x" % chat.group if chat.group else ""),
                flush=True)
            time.sleep(1)

    chat.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
