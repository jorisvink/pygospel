#!/usr/bin/env python3

"""
The smallest gospel bot there is: answer whoever says something to you.

    $ PYTHONPATH=build/host/libkyrka/obj/python \\
        python3 examples/echo.py -c gospel.conf

Twenty lines on top of GospelChat.
"""

import os
import sys
import time
import argparse

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pygospel  # noqa: E402


class Echo(pygospel.GospelChat):
    def on_message(self, msg):
        text = msg.text.strip()

        if text.lower() == "time":
            reply = "%s: %s" % (self.nickname or "%02x" % self.spi,
                time.strftime("%Y-%m-%d %H:%M:%S"))
        elif text.lower() == "ping":
            reply = "pong"
        elif text.startswith("!"):
            reply = "I only take text, and %r is text I do not know" % text
        else:
            reply = None

        if reply is None:
            return

        try:
            if msg.group is None:
                self.reply(msg.peer, reply, msg)
            else:
                self.reply_group(reply, msg)
        except pygospel.GospelError:
            pass

    def on_peer(self, peer):
        print("%02x is around" % peer.id, flush=True)


def main():
    ap = argparse.ArgumentParser(description="a gospel echo bot")
    ap.add_argument("-c", "--config", required=True)
    ap.add_argument("-n", "--nickname", default="echo")
    ap.add_argument("-g", "--group", type=lambda x: int(x, 16), default=0)

    args = ap.parse_args()

    with Echo(args.config, nickname=args.nickname, group=args.group) as chat:
        print("echo on %02x, waiting" % chat.spi, flush=True)
        chat.run()

    return 0


if __name__ == "__main__":
    sys.exit(main())
