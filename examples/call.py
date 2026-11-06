#!/usr/bin/env python3

"""
Answer a voice call, or make one, and send back whatever is heard.

    $ python3 examples/call.py -c gospel.conf
    $ python3 examples/call.py -c gospel.conf --dial 02

Without --dial this takes the calls of the devices in --allow, or of every
device if it is not given. With --dial it says something the whole time, a
tone, and exits when the call is over: 0 if it got to hear the other side.
A Vigil only counts a device as online with --liturgy, and only if that is
the liturgy_group the Vigil uses. Without a group it is the one in the
configuration.
"""

import math
import os
import sys
import time
import signal
import struct
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "build/host/libkyrka/obj/python"))

import pygospel  # noqa: E402

TONE_HZ    = 440
TONE_LEVEL = 8000

# How much of the tone to keep queued, in milliseconds.
TONE_AHEAD = 100

# How long a cathedral gets to answer before we say so, in seconds.
ANSWER_WAIT = 10


def tone():
    """A second of tone, which repeats without a click."""

    return struct.pack("<%dh" % pygospel.VOICE_RATE, *(
        int(TONE_LEVEL * math.sin(2 * math.pi * TONE_HZ * n /
            pygospel.VOICE_RATE)) for n in range(pygospel.VOICE_RATE)))


def level(pcm):
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm)

    return int(math.sqrt(sum(s * s for s in samples) / len(samples)))


class Phone(pygospel.GospelChat):
    def __init__(self, config, allow, dial, seconds, **kwargs):
        self.allow = allow
        self.dial = dial
        self.seconds = seconds
        self.loudest = 0
        self.heard = False
        self.hangup_at = 0
        self.tone = tone() if dial else b""
        self.seen = None
        self.started = time.monotonic()
        self.answered = False

        super().__init__(config, take_calls=not dial, **kwargs)

        if self.presence is None:
            print("up as %02x, Vigil will not list us without --liturgy"
                % self.spi, flush=True)
        else:
            print("up as %02x in liturgy_group %04x" % (self.spi,
                self.config.group), flush=True)

    def on_call_ring(self, call):
        if self.allow and call.peer.id not in self.allow:
            print("%02x is calling, declining" % call.peer.id, flush=True)
            call.hangup()
            return

        print("%02x is calling, answering" % call.peer.id, flush=True)
        call.answer()

    def on_call_talking(self, call):
        self.heard = True
        print("talking to %02x via %s" % (call.peer.id, call.link), flush=True)

        if self.seconds:
            self.hangup_at = time.monotonic() + self.seconds

    def on_call_audio(self, call, pcm):
        self.loudest = max(self.loudest, level(pcm))

        if not self.dial:
            call.send_audio(pcm)

    def on_call_end(self, call, reason):
        print("%02x ended: %s, %d frames out, %d in, loudest %d" % (
            call.peer.id, reason, call.tx_frames, call.rx_frames,
            self.loudest), flush=True)
        self.loudest = 0

        if self.dial:
            self.stop()

    def report(self):
        """Say what the cathedral says, each time that changes."""

        lit = self.presence if self.presence is not None \
            else self.call_signaling

        if lit is None:
            return

        if lit.alive and not self.answered:
            self.answered = True
            print("the cathedral answers", flush=True)
        elif not self.answered and \
            time.monotonic() - self.started > ANSWER_WAIT:
            self.answered = True
            print("the cathedral does not answer", flush=True)

        if self.presence is None or not lit.alive or \
            self.seen == self.callable:
            return

        self.seen = set(self.callable)
        print("online in %04x: %s" % (self.config.group, " ".join(
            "%02x" % p for p in sorted(self.seen)) or "nobody else"),
            flush=True)

    def tick(self):
        super().tick()
        self.report()

        call = self.active_call

        if call is None:
            return

        if self.dial and call.queued < TONE_AHEAD:
            call.send_audio(self.tone)

        if self.hangup_at and time.monotonic() >= self.hangup_at:
            self.hangup_at = 0
            call.hangup()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("-c", "--config", required=True,
        help="the gospel configuration")
    ap.add_argument("--dial", type=lambda x: int(x, 16),
        help="call this device instead of waiting to be called")
    ap.add_argument("--allow", type=lambda x: [int(p, 16) for p in
        x.split(",")], default=[], help="the devices whose calls we take")
    ap.add_argument("--seconds", type=int, default=0,
        help="hang up this long after we started talking")
    ap.add_argument("-l", "--liturgy", nargs="?", const="config",
        help="show up as a peer in this liturgy_group, in hex, or in the one "
        "of the configuration without a value")

    args = ap.parse_args()

    try:
        config = pygospel.GospelConfig.load(args.config)

        if args.liturgy not in (None, "config"):
            config.group = int(args.liturgy, 16)
            config._validate()

        phone = Phone(config, args.allow, args.dial, args.seconds,
            discoverable=args.liturgy is not None)
    except ValueError:
        sys.exit("--liturgy wants a group in hex, not %s" % args.liturgy)
    except (pygospel.GospelError, OSError) as e:
        sys.exit(str(e))

    signal.signal(signal.SIGTERM, lambda signum, frame: phone.stop())

    if args.dial:
        phone.call(args.dial)

    phone.run()

    return 0 if phone.heard or not args.dial else 1


if __name__ == "__main__":
    sys.exit(main())
