#!/usr/bin/env python3

"""
A terminal gospel client: one-to-one chats, a group chat, and a status line.

    $ make
    $ PYTHONPATH=build/host/libkyrka/obj/python:src \\
        python3 examples/chatter.py -c gospel.conf -n alice

Type a device id, or /to 02, to talk to one device; /join 0007 to talk to a
group. Whatever you type then goes to wherever you are pointed. /help lists
the rest.
"""

import os
import sys
import signal
import argparse

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pygospel  # noqa: E402


HELP = """\
  <text>            send to wherever we are pointed
  /to <id>          talk to a device, e.g. /to 02
  /open <id>        keep signaling a device, e.g. while its chat is open
  /close <id>       stop signaling it
  /forget <id>      drop the messages you still hold for it
  /join <group>     join a group, e.g. /join 0007
  /leave            leave the group
  /peers            who we can reach, and how
  /outbox <id>      what we still hold for a device
  /status           what this chat is doing
  /typing on|off    let them know you are typing
  /quiet on|off     hold messages back until you type
  /quit             leave
"""


class Chatter(pygospel.GospelChat):
    """A gospel chat that also has a prompt."""

    def __init__(self, config, nickname, to=0):
        self.to = to
        self.hold = []
        self.quiet = False
        self.target = "direct"

        super().__init__(config, nickname=nickname)

    #
    # The chat talking to us.
    #

    def line(self, text):
        """Print something without wrecking the prompt."""

        if self.quiet:
            self.hold.append(text)
            return

        sys.stdout.write("\r\033[K" + text + "\r\n")
        self.prompt()

    def prompt(self):
        where = "%04x" % self.group if self.target == "group" \
            else ("%02x" % self.to if self.to else "?")

        sys.stdout.write("\r\033[K(%s) %s> " % (where, self.nickname or "*"))
        sys.stdout.flush()

    def on_message(self, msg):
        who = self.peer(msg.peer).best_name

        if msg.group is not None:
            who += "@%04x" % msg.group

        self.line("[%s] %s" % (who, msg.text))

    def on_delivered(self, ref, msg):
        self.line("  . %02x has it" % msg.peer)

    def on_group_delivered(self, ref, group, acked, pending):
        if pending:
            self.line("  . %d in %04x have it, %d to go"
                % (acked, group, pending))
        else:
            self.line("  . all of %04x has it" % group)

    def on_peer(self, peer):
        self.line("  ~ %02x (%s) is around" % (peer.id, peer.best_name))

    def on_name(self, peer):
        self.line("  ~ %02x now goes by %s" % (peer.id, peer.best_name))

    def on_joined(self, peer):
        self.line("  + %s joined the group" % peer.best_name)

    def on_left(self, peer):
        self.line("  - %s left the group" % peer.best_name)

    def on_typing(self, peer, typing):
        if typing:
            self.line("  ... %s is typing" % peer.best_name)

    def on_offline(self):
        self.line("  ! the cathedral stopped answering")

    #
    # Us talking to the chat.
    #

    def on_input(self, source):
        line = source.readline()

        if not line:
            self.stop()
            return

        self.command(line.rstrip("\r\n"))

    def command(self, line):
        if not line.strip():
            self.prompt()
            return

        parts = line.split(None, 2)
        head = parts[0].lower()

        if head in ("/quit", "/exit", "/q"):
            self.stop()
        elif head == "/help":
            sys.stdout.write(HELP)
            self.prompt()
        elif head in ("/to", "/open", "/close", "/forget", "/outbox"):
            self.device_command(head, parts)
        elif head == "/join":
            self.join(int(parts[1], 16) if len(parts) > 1 else self.group)
            self.target = "group" if self.group else "direct"
            self.prompt()
        elif head == "/leave":
            self.leave()
            self.target = "direct"
            self.prompt()
        elif head == "/peers":
            self.show_peers()
        elif head == "/status":
            for key, value in sorted(self.status().items()):
                sys.stdout.write("  %-10s %s\r\n" % (key, value))
            self.prompt()
        elif head == "/typing":
            self.typing(len(parts) > 1 and parts[1] in ("on", "yes", "1"))
            self.prompt()
        elif head == "/quiet":
            self.quiet = len(parts) > 1 and parts[1] in ("on", "yes", "1")

            if not self.quiet:
                for text in self.hold:
                    sys.stdout.write(text + "\r\n")
                self.hold = []
                self.prompt()
        elif head.startswith("/"):
            sys.stdout.write("  unknown, /help lists the commands\r\n")
            self.prompt()
        else:
            self.say(line)

    def device_command(self, head, parts):
        if len(parts) < 2:
            sys.stdout.write("  %s wants a device id, like 02\r\n" % head)
            self.prompt()
            return

        try:
            peer = int(parts[1], 16)
        except ValueError:
            sys.stdout.write("  not a device id: %s\r\n" % parts[1])
            self.prompt()
            return

        try:
            if head == "/to":
                self.to = peer
                self.target = "direct"
                self.open(peer)
            elif head == "/open":
                self.open(peer)
            elif head == "/close":
                self.close_chat(peer)
            elif head == "/forget":
                self.forget(peer)
            elif head == "/outbox":
                for msg in self.outbox(peer):
                    sys.stdout.write("  %s\r\n" % msg)
        except pygospel.GospelError as e:
            sys.stdout.write("  %s\r\n" % e)

        self.prompt()

    def show_peers(self):
        for peer in self.roster():
            sys.stdout.write("  %02x %-14s %-14s %-8s %s\r\n" % (peer.id,
                peer.nick or "-", peer.name or "-", peer.link,
                "group" if peer.member else ""))

        self.prompt()

    def say(self, text):
        try:
            if self.target == "group":
                self.send_group(text)
            elif self.to:
                self.send(self.to, text)
            else:
                sys.stdout.write("  say /to 02 or /join 0007 first\r\n")
        except pygospel.GospelError as e:
            sys.stdout.write("  %s\r\n" % e)

        self.prompt()


def main():
    ap = argparse.ArgumentParser(description="a text-only gospel client")
    ap.add_argument("-c", "--config", required=True,
        help="a Vigil configuration in gospel mode")
    ap.add_argument("-n", "--nickname", default="",
        help="the name peers see, up to 16 letters and digits")
    ap.add_argument("-g", "--group", type=lambda x: int(x, 16), default=None,
        help="the group to join, 0 for none, else the one configured")

    args = ap.parse_args()

    chat = Chatter(args.config, args.nickname)

    if args.group is not None and args.group:
        chat.join(args.group)
        chat.target = "group"

    try:
        chat.add_reader(sys.stdin, chat.on_input)
    except (OSError, ValueError):
        sys.exit("this client needs to read from a terminal or a pipe")

    signal.signal(signal.SIGINT, lambda signum, frame: chat.stop())

    chat.prompt()

    while chat.running:
        chat.poll(0.5)

    chat.remove_reader(sys.stdin)
    chat.close()
    sys.stdout.write("\r\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
