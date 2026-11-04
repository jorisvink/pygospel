#!/usr/bin/env python3

"""
Tests for the text-only gospel chat.

Run with: python3 python/test_pygospel.py
"""

import os
import sys
import struct
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import fakekyrka  # noqa: E402

sys.modules["libkyrka"] = fakekyrka.Module(fakekyrka.Network())

import pygospel  # noqa: E402


KEK   = "11" * 32
CS    = "22" * 32
COSK  = "33" * 64
FLOCK = "493abf95a07e0c00"
CSID  = "5eb2411f"

failures = []
checks = 0


def check(what, got, want):
    global checks

    checks += 1

    if got != want:
        failures.append("%s: got %r, want %r" % (what, got, want))


def ok(what, condition):
    global checks

    checks += 1

    if not condition:
        failures.append("%s: not true" % what)


def boom(what, exception=Exception):
    global checks

    checks += 1

    try:
        exception()
    except Exception:
        return True

    failures.append("%s: nothing was raised" % what)

    return False


def config(spi, group="0001", **kwargs):
    lines = [
        "vigil 1",
        "instance device%02x" % spi,
        "mode gospel",
        "spi %02x" % spi,
        "kek " + KEK,
        "liturgy_group %s" % group,
        "cathedral 203.0.113.7:4469",
        "cathedral_id " + CSID,
        "cathedral_flock " + FLOCK,
        "cathedral_secret " + CS,
        "cathedral_cosk " + COSK,
    ]

    for key, value in kwargs.items():
        if isinstance(value, bool):
            value = "yes" if value else "no"
        lines.append("%s %s" % (key, value))

    return "\n".join(lines) + "\n"


def fresh():
    """A new fake flock, and the gospel module pointed at it."""

    net = fakekyrka.Network()
    fakekyrka.install(pygospel, net)

    return net


def pump(chats, rounds=6, ms=1000):
    """Advance the fake clock, letting every chat poll once per round."""

    for _ in range(rounds):
        for one in chats:
            one.poll(0)
        pygospel.time.sleep(ms / 1000.0)


#
# The wire itself.
#

def test_frame():
    frame = pygospel.frame_pack(1, pygospel.TYPE_TEXT, b"hi")

    check("frame size", len(frame), pygospel.FRAME_SIZE)
    check("frame id", frame[:8], b"\x00" * 7 + b"\x01")
    check("frame len", frame[8:10], b"\x00\x02")
    check("frame type", frame[10:11], b"\x01")
    check("frame pad", frame[11:13], b"hi")
    check("frame tail", frame[13:], b"\x00" * (pygospel.MESSAGE_MAX - 2))

    msgid, length, ftype, payload = pygospel.frame_unpack(frame)

    check("unpack id", msgid, 1)
    check("unpack len", length, 2)
    check("unpack type", ftype, pygospel.TYPE_TEXT)
    check("unpack text", payload[:length], b"hi")

    ack = pygospel.frame_pack(9, pygospel.TYPE_ACK)

    check("ack size", len(ack), pygospel.FRAME_SIZE)
    check("ack id", ack[:8], b"\x00" * 7 + b"\x09")
    check("ack len", ack[8:10], b"\x00\x00")
    check("ack type", ack[10:11], b"\x02")
    check("ack pad", ack[11:], b"\x00" * pygospel.MESSAGE_MAX)


def test_heartbeat():
    hb = pygospel.heartbeat_pack(b"\x01\x02\x03\x04\x05\x06\x07\x08",
        "bob", False, 0)

    check("heartbeat size", len(hb), pygospel.FRAME_SIZE)
    check("heartbeat id", hb[:8], b"\xff" * 8)
    check("heartbeat len", hb[8:10], b"\x00\x00")
    check("heartbeat type", hb[10:11], b"\x03")
    check("heartbeat uid", hb[11:19], b"\x01\x02\x03\x04\x05\x06\x07\x08")
    check("heartbeat nick", hb[19:35], b"bob".ljust(16, b"\x00"))
    check("heartbeat typing", hb[35:36], b"\x00")
    check("heartbeat features", hb[36:37], b"\x00")

    uid, nick, typing, features, _, _ = pygospel.heartbeat_unpack(hb[11:])

    check("unpack uid", uid, b"\x01\x02\x03\x04\x05\x06\x07\x08")
    check("unpack nick", nick, b"bob".ljust(16, b"\x00"))
    check("unpack typing", typing, False)
    check("unpack features", features, 0)

    # A peer that stops reading at the features must still be fine.
    short = pygospel.frame_pack(pygospel.HEARTBEAT_ID, pygospel.TYPE_HEARTBEAT,
        hb[11:37])

    ok("short heartbeat", len(short) == pygospel.FRAME_SIZE)


def test_normalize():
    check("keeps text", pygospel._normalize(b"hello"), b"hello")
    check("keeps newline", pygospel._normalize(b"a\nb\tc"), b"a\nb\tc")
    check("kills nul", pygospel._normalize(b"a\x00b"), b"a?b")
    check("kills bell", pygospel._normalize(b"a\x07b"), b"a?b")
    check("keeps utf8", pygospel._normalize("é✓".encode()), "é✓".encode())
    check("kills continuation", pygospel._normalize(b"a\x80b"), b"a?b")
    check("kills truncated", pygospel._normalize(b"a\xc3"), b"a?")
    check("kills surrogate", pygospel._normalize(b"\xed\xa0\x80"), b"???")
    check("kills overlong", pygospel._normalize(b"\xc0\x80"), b"??")


def test_hash():
    check("fnv empty", pygospel._fnv1a(b""), 0xcbf29ce484222325)
    check("fnv a", pygospel._fnv1a(b"a"), 0xaf63dc4c8601ec8c)
    check("fnv foobar", pygospel._fnv1a(b"foobar"), 0x85944171f73967e8)


def test_config():
    cfg = pygospel.GospelConfig.parse(config(0x0a))

    check("cfg spi", cfg.spi, 0x0a)
    check("cfg kek", cfg.kek, bytes.fromhex(KEK))
    check("cfg secret", cfg.cathedral_secret, bytes.fromhex(CS))
    check("cfg cosk", cfg.cathedral_cosk, bytes.fromhex(COSK))
    check("cfg flock", cfg.cathedral_flock, int(FLOCK, 16))
    check("cfg id", cfg.cathedral_id, int(CSID, 16))
    check("cfg group", cfg.group, 1)
    check("cfg host", cfg.cathedral_host, "203.0.113.7")
    check("cfg port", cfg.cathedral_port, 4469)
    check("cfg mtu", cfg.mtu, 1408)
    check("cfg p2p", cfg.p2p, True)
    check("cfg shroud", cfg.shroud, False)
    check("cfg instance", cfg.instance, "device0a")

    cfg = pygospel.GospelConfig.parse(config(1, shroud="yes", p2p="no",
        mtu="auto", liturgy_group="0007", cathedral_nat_port="4470"))

    check("cfg shroud on", cfg.shroud, True)
    check("cfg p2p off", cfg.p2p, False)
    check("cfg mtu auto", cfg.mtu, 1408)
    check("cfg group set", cfg.group, 7)
    check("cfg nat port", cfg.cathedral_nat_port, 4470)

    for bad in ("", "not a config\n", "mode tunnel\nspi 01\n",
        "spi 00\nkek " + KEK + "\n"):
        ok("rejects %r" % bad[:20], raises(pygospel.GospelError,
            pygospel.GospelConfig.parse, bad))

    parse = pygospel.GospelConfig.parse
    ok("rejects spi 00", raises(pygospel.GospelError, parse, config(0)))
    ok("rejects short kek", raises(pygospel.GospelError,
        pygospel.GospelConfig.parse, config(1).replace(KEK, "aa")))


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    except Exception:
        return False

    return False


#
# One-to-one chats.
#

def test_one_to_one():
    net = fresh()
    a = two = None

    class Recorder(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            self.sent = []
            self.delivered = []
            self.names = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

        def on_sent(self, ref, msg):
            self.sent.append(ref)

        def on_delivered(self, ref, msg):
            self.delivered.append(msg)

        def on_name(self, peer):
            self.names.append(peer.nick)

    one = Recorder(config(0x01), nickname="alice", p2p=False, group=0)
    two = Recorder(config(0x02), nickname="bob", p2p=False, group=0)

    one.signaling.wanted = None
    pump([one, two])

    # Both signal everyone, so both see the other.
    one.signal_all = two.signal_all = True
    pump([one, two])

    ok("one sees two", one.peer(0x02).present)
    ok("two sees one", two.peer(0x01).present)

    ok("tunnel exists", one.peer(0x02).signal_tunnel is not None)
    ok("online", one.tunnel_online(0x02))
    ok("relay", one.link(0x02) == "relay")

    msg = one.send(0x02, "blessed sanctum")

    check("sent text", msg.text, "blessed sanctum")
    check("sent peer", msg.peer, 0x02)
    check("sent group", msg.group, None)
    ok("has ref", msg.ref != 0)

    pump([one, two])

    check("delivered count", len(two.got), 1)
    check("delivered text", two.got[0].text, "blessed sanctum")
    check("delivered author", two.got[0].author, 0x01)
    check("delivered peer", two.got[0].peer, 0x01)
    check("delivered group", two.got[0].group, None)

    ok("sender heard sent", msg.ref in one.sent)
    ok("sender heard delivered", one.delivered and
        one.delivered[0].ref == msg.ref)
    ok("sender outbox empty", one.peer(0x02).outbox == [])
    ok("delivered flag", one.delivered[0].delivered)

    # Nicknames ride on heartbeats.
    ok("bob learned alice", two.peer(0x01).nick == "alice")
    ok("alice learned bob", one.peer(0x02).nick == "bob")

    # Every frame on the wire was a whole frame.
    sizes = set(n for _, _, n in net.packets)

    ok("only full frames", sizes == {pygospel.FRAME_SIZE})

    one.close()
    two.close()


def test_outbox():
    net = fresh()

    class Log(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            self.delivered = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

        def on_delivered(self, ref, msg):
            self.delivered.append(msg)

    # Two starts later, so One has to hold what it wrote.
    one = Log(config(0x01), nickname="alice", p2p=False, group=0)
    one.signal_all = True

    msg = one.send(0x02, "anyone there")

    ok("held", len(one.peer(0x02).outbox) == 1)
    check("held not delivered", msg.delivered, False)

    pump([one], rounds=3)

    ok("still held", len(one.peer(0x02).outbox) == 1)

    two = Log(config(0x02), nickname="bob", p2p=False, group=0)
    two.signal_all = True

    pump([one, two], rounds=8)

    check("arrived late", len(two.got), 1)
    check("late text", two.got[0].text if two.got else None, "anyone there")
    check("delivered now", len(one.delivered), 1)
    ok("outbox drained", one.peer(0x02).outbox == [])

    one.close()
    two.close()


def test_duplicates():
    net = fresh()

    class Counter(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

    one = Counter(config(0x01), nickname="alice", p2p=False, group=0,
        signal_all=True)
    two = Counter(config(0x02), nickname="bob", p2p=False, group=0,
        signal_all=True)

    pump([one, two])
    one.send(0x02, "once")

    # Hand the same frame over again without the sender knowing.
    tunnel = one.peer(0x02).signal_tunnel
    held = tunnel.sendq[0] if tunnel.sendq else one.peer(0x02).outbox[0]
    frame = pygospel.frame_pack(held.msgid, pygospel.TYPE_TEXT,
        held.text.encode(), len(held.text.encode()))
    peer = two.peer(0x01).signal_tunnel

    for _ in range(3):
        peer.ctx.heaven_cb(peer.ctx, frame, 0, peer.ctx.heaven_udata) \
            if False else two._heaven(peer.ctx, frame, 0, peer)

    pump([one, two], rounds=2)

    check("delivered once", len(two.got), 1)
    check("acked anyway", len([f for f in two.got if f.text == "once"]), 1)

    # A different message with the same id is not a duplicate.
    other = pygospel.frame_pack(held.msgid, pygospel.TYPE_TEXT, b"twice", 5)
    two._heaven(peer.ctx, other, 0, peer)
    pump([two], rounds=1)

    check("second one in", len(two.got), 2)

    one.close()
    two.close()


def test_no_auto_ack():
    net = fresh()

    class Store(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            self.delivered = []
            super().__init__(*args, **kwargs)

        def on_delivered(self, ref, msg):
            self.delivered.append(msg)

        def on_message(self, msg):
            self.got.append(msg)

    one = Store(config(0x01), nickname="alice", p2p=False, group=0,
        signal_all=True)
    two = Store(config(0x02), nickname="bob", p2p=False, group=0,
        signal_all=True, auto_ack=False)

    pump([one, two])
    m = one.send(0x02, "store me first")
    pump([one, two])

    ok("nothing delivered yet", one.delivered == [])
    ok("outbox holds it", len(one.peer(0x02).outbox) == 1)

    two.got[0].ack()
    pump([one, two])

    check("delivered after ack", len(one.delivered), 1)
    ok("outbox clear", one.peer(0x02).outbox == [])

    # Acking twice must not send a second ack.
    frames_before = len(net.frames)
    two.got[0].ack()
    pump([two])

    ok("ack is idempotent", len(net.frames) == frames_before)

    one.close()
    two.close()


def test_reply():
    """
    A reply only rides as one when the peer said it takes them, exactly
    like Vigil. Announcing nothing is the text-only default, so the quote
    is checked with both settings.
    """

    class Talker(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

    def exchange(**kwargs):
        net = fresh()

        one = Talker(config(0x01), nickname="alice", p2p=False, group=0,
            signal_all=True, **kwargs)
        two = Talker(config(0x02), nickname="bob", p2p=False, group=0,
            signal_all=True, **kwargs)

        pump([one, two], rounds=8)

        one.send(0x02, "the question")
        pump([one, two], rounds=8)

        question = two.got[0]
        check("question arrived", question.text, "the question")
        check("no quote on a question", question.quote, None)

        two.reply(0x01, "the answer", question)
        pump([one, two], rounds=8)

        answer = one.got[-1]

        check("answer text", answer.text, "the answer")

        return one, two, question, answer

    # Text only, nothing announced: a reply is just another message.
    kwargs = dict(features=pygospel.FEATURES)
    one, two, question, answer = exchange(**kwargs)

    check("reply carries no quote", answer.quote, None)
    check("we announce nothing", two.features, pygospel.FEATURES)
    check("peer announces nothing", two.peer(0x01).signal_tunnel.features,
        pygospel.FEATURES)

    one.close()
    two.close()

    # Both sides take replies, so the quote survives.
    one, two, question, answer = exchange(features=pygospel.FEATURE_REPLY)

    ok("reply quotes", answer.quote is not None)
    check("quote author", answer.quote.author, 0x01)
    check("quote hash", answer.quote.hash, question.hash)
    check("quote of a question", bool(question.quote), False)

    # A reply to one of our own messages is authored by us, or zero,
    # which is how Vigil spells "the other one".
    two.reply(0x01, "and to my own", answer)
    pump([one, two], rounds=8)

    check("quoting ourselves stays an id", one.got[-1].quote.author, 0x02)

    one.close()
    two.close()


def test_typing():
    net = fresh()

    class Watcher(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.events = []
            super().__init__(*args, **kwargs)

        def on_typing(self, peer, on):
            self.events.append((peer.id, on))

    one = Watcher(config(0x01), nickname="alice", p2p=False, group=0,
        signal_all=True)
    two = Watcher(config(0x02), nickname="bob", p2p=False, group=0,
        signal_all=True)

    pump([one, two])

    two.typing(True, 0x01)
    pump([one, two])

    ok("alice saw bob type", (0x02, True) in one.events)

    two.typing(False, 0x01)
    pump([one, two])

    ok("alice saw bob stop", (0x02, False) in one.events)

    one.close()
    two.close()


def test_typing_each_way():
    fresh()

    one = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    two = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)

    pump([one, two], rounds=10)

    sent = []
    tunnel = two.peer(0x01).signal_tunnel
    send_heartbeat = tunnel.send_heartbeat

    def counted():
        sent.append(tunnel.chat.peer(0x01).typing_out)
        return send_heartbeat()

    tunnel.send_heartbeat = counted

    one.typing(True, 0x02)
    pump([one, two], rounds=4)

    ok("two saw one type", two.peer(0x01).typing)
    ok("two says it is not typing", True not in sent)
    ok("one hears nobody typing", not one.peer(0x02).typing)

    one.typing(False, 0x02)
    two.typing(True, 0x01)
    pump([one, two], rounds=4)

    ok("one saw two type", one.peer(0x02).typing)
    ok("two hears nobody typing", not two.peer(0x01).typing)

    one.typing(True, 0x02)
    pump([one, two], rounds=2)
    one.typing(False, 0x02)
    pump([one, two], rounds=4)

    ok("two still typing", one.peer(0x02).typing)

    one.close()
    two.close()


def test_quote_hash_is_vigils():
    check("hash of hello", pygospel.Message("hello").hash, 0xa1ad92f331dce2e0)


def test_heartbeats_keep_going():
    fresh()

    one = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    two = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)

    pump([one, two], rounds=10)

    sent = []
    tunnel = one.peer(0x02).signal_tunnel
    send_heartbeat = tunnel.send_heartbeat

    def counted():
        sent.append(pygospel._ms())
        return send_heartbeat()

    tunnel.send_heartbeat = counted

    pump([one, two], rounds=120)

    check("one every twelve seconds", len(sent), 10)
    ok("still online", two.tunnel_online(0x01))

    one.close()
    two.close()


def test_push_names_one_peer():
    fresh()

    chat = pygospel.GospelChat(config(0x01), p2p=False)
    only = bytearray(pygospel.PEERS_PER_FLOCK)

    only[0x02] = 1
    chat._present(only)

    only = bytearray(pygospel.PEERS_PER_FLOCK)

    only[0x03] = 1
    chat._present(only)

    ok("02 is still around", chat.peer(0x02).present)
    ok("03 is around", chat.peer(0x03).present)

    pygospel.time.sleep(pygospel.PRESENT_EXPIRE / 1000.0)
    chat.tick()

    ok("02 expired", not chat.peer(0x02).present)

    chat.close()


def test_cathedral_is_not_the_peer():
    fresh()

    one = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    two = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)

    pump([one, two], rounds=10)

    tunnel = one.peer(0x02).signal_tunnel
    before = tunnel.last_rx

    pygospel.time.sleep(1)
    tunnel.sock.push(b"\x00" * 64, tunnel.cathedral.addr)
    one._read(tunnel.sock, tunnel)

    check("last_rx stays", tunnel.last_rx, before)

    one.close()
    two.close()


def test_reply_waits_for_heartbeat():
    fresh()

    class Talker(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

    one = Talker(config(0x01), nickname="alice", p2p=False, signal_all=True,
        features=pygospel.FEATURE_REPLY)
    two = Talker(config(0x02), nickname="bob", p2p=False, signal_all=True,
        features=pygospel.FEATURE_REPLY)

    pump([one, two], rounds=10)

    tunnel = two.peer(0x01).signal_tunnel
    tunnel.heard = False

    two.reply(0x01, "the answer", pygospel.Quote(0x01, 0x1234))

    check("not in line yet", tunnel.sendq, [])

    pump([one, two], rounds=2)
    check("nothing went out", one.got, [])

    pump([one, two], rounds=20)

    check("one arrived", len(one.got), 1)
    check("with its quote", one.got[0].quote.hash, 0x1234)

    one.close()
    two.close()


def test_discoverable():
    fresh()

    listed = {}

    class Watcher(pygospel.GospelChat):
        def _liturgy_event(self, lit, event, info):
            if lit is self.presence and "peers" in info:
                listed[self.spi] = [id for id, on in enumerate(info["peers"])
                    if on]
            super()._liturgy_event(lit, event, info)

    a = Watcher(config(0x01, group="0007"), p2p=False, discoverable=True)
    b = Watcher(config(0x02, group="0007"), p2p=False, discoverable=True)
    c = Watcher(config(0x03, group="0009"), p2p=False, discoverable=True)
    d = pygospel.GospelChat(config(0x04, group="0007"), p2p=False)

    for chat in (a, b, c, d):
        chat.signaling.ctx.wanted = bytes(pygospel.PEERS_PER_FLOCK)

    pump([a, b, c, d], rounds=8)

    check("the group calls discover in", a.presence.ctx.group, 0x0008)
    check("the domain of calls", a.presence.ctx.flock_src,
        int(FLOCK, 16) | 0x0a)
    check("no extra liturgy by default", d.presence, None)
    check("a sees b", listed[0x01], [0x02])
    check("b sees a", listed[0x02], [0x01])
    check("c sees neither", listed[0x03], [])
    check("status says so", a.status()["liturgy_group"], "0007")
    check("no group chat out of it", a.group, 0)
    check("nor any member", a.members(), [])

    check("no tunnel yet", a.peer(0x02).signal_tunnel, None)

    b.open(0x01)
    pump([a, b], rounds=8)

    ok("opening it brings the tunnel up", a.tunnel_online(0x02))
    ok("from both sides", b.tunnel_online(0x01))

    b.send(0x01, "anyone home")
    pump([a, b], rounds=4)

    pump([a, b], rounds=pygospel.TUNNEL_IDLE // 1000 + 30)

    ok("still up from a", a.tunnel_online(0x02))
    ok("still up from b", b.tunnel_online(0x01))

    for chat in (a, b, c, d):
        chat.close()

    boom("liturgy_group ffff is refused", lambda:
        pygospel.GospelConfig.parse(config(0x01, group="ffff")))


def test_group_bot():
    fresh()

    class Member(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

    bot = Member(config(0x07), nickname="pi", p2p=False,
        features=pygospel.FEATURE_REPLY)
    vigil = Member(config(0x02), nickname="joris", p2p=False, group=0x0007,
        features=pygospel.FEATURE_REPLY)

    pump([bot, vigil], rounds=4)

    check("not in it yet", bot.members(), [])

    bot.join(0x0007)
    pump([bot, vigil], rounds=10)

    check("sees the member", [p.id for p in bot.members()], [0x02])
    check("is seen", [p.id for p in vigil.members()], [0x07])
    ok("no one-to-one tunnel for it", bot.peer(0x02).signal_tunnel is None)

    vigil.send_group("what about you @pi.")
    pump([bot, vigil], rounds=4)

    question = bot.got[-1]

    check("in the group", question.group, 0x0007)
    ok("a dot ends a mention", question.mentions_me)

    bot.reply_group("I am here", question)
    pump([bot, vigil], rounds=4)

    check("answered", vigil.got[-1].text, "I am here")
    check("quote points at the question", vigil.got[-1].quote.hash,
        pygospel.Message("what about you @pi.").hash)

    pump([bot, vigil], rounds=90)

    ok("still online", vigil.tunnel_online(0x07))
    check("still a member", [p.id for p in vigil.members()], [0x07])

    boom("group reply too long", lambda:
        bot.reply_group("x" * pygospel.MESSAGE_MAX, question))
    bot.send_group("x" * pygospel.MESSAGE_MAX)
    pump([bot, vigil], rounds=2)

    bot.leave()
    pump([bot, vigil], rounds=20)

    check("gone from the group", vigil.members(), [])

    bot.close()
    vigil.close()


def test_mentions_end_like_vigil():
    names = {"pi", "07"}

    for text in ("@pi", "hey @PI, you", "@pi.", "(@pi)", "@07?"):
        ok("mention in %r" % text, pygospel._mentions(text, names))

    for text in ("pi", "a@pi", "@pie", "@pi-e", "@pi_e", "-@pi"):
        ok("no mention in %r" % text, not pygospel._mentions(text, names))


def test_addresses_in_network_order():
    fresh()

    chat = pygospel.GospelChat(config(0x01), p2p=True, signal_all=True)
    tunnel = pygospel._Tunnel(chat, chat.peer(0x02), pygospel.SIGNAL_GROUP)

    ip = struct.unpack("=I", bytes((198, 51, 100, 7)))[0]
    port = struct.unpack("=H", struct.pack("!H", 5000))[0]

    chat._peer_discovery(tunnel, {"ip": ip, "port": port, "flags": 0})
    check("peer address", tunnel.addr, ("198.51.100.7", 5000))

    chat._more_cathedrals([(ip, port)])
    ok("cathedral address", ("198.51.100.7", 5000) in chat.cathedrals)

    tunnel.addr = tunnel.cathedral.addr
    tunnel.p2p_verify = 0

    ip = struct.unpack("=I", bytes((127, 0, 0, 1)))[0]

    chat._peer_discovery(tunnel, {"ip": ip, "port": port, "flags": 0})
    ok("loopback stays on the relay", tunnel.relay)

    chat.close()


def test_peer_stops():
    net = fresh()

    one = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        group=0, signal_all=True)
    two = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        group=0, signal_all=True)

    pump([one, two])

    ok("up", one.tunnel_online(0x02))

    two.close()

    # Nothing arrives for RX_TIMEOUT, so the tunnel goes away.
    pump([one], rounds=pygospel.RX_TIMEOUT // 1000 + 4, ms=1000)

    ok("down", not one.tunnel_online(0x02))
    check("tunnel gone", one.peer(0x02).signal_tunnel, None)

    one.close()


#
# Group chats.
#

def test_group():
    net = fresh()

    class Member(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            self.done = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

        def on_group_delivered(self, ref, group, acked, pending):
            self.done.append((ref, group, acked, pending))

    a = Member(config(0x01), nickname="alice", p2p=False, group=0x0007)
    b = Member(config(0x02), nickname="bob", p2p=False, group=0x0007)
    c = Member(config(0x03), nickname="carol", p2p=False, group=0x0007)

    pump([a, b, c], rounds=8)

    check("a sees members", sorted(p.id for p in a.members()), [2, 3])
    check("b sees members", sorted(p.id for p in b.members()), [1, 3])
    ok("group tunnels", a.peer(0x02).group_tunnel is not None)
    ok("own id excluded", a.peer(0x01).member is False)

    msg = a.send_group("hello all")

    check("group tag", msg.group, 0x0007)
    check("author is us", msg.author, 0x01)

    pump([a, b, c], rounds=6)

    check("bob got it", len(b.got), 1)
    check("carol got it", len(c.got), 1)
    check("bob sees group", b.got[0].group, 0x0007)
    check("bob sees author", b.got[0].author, 0x01)
    check("bob sees speaker", b.got[0].peer, 0x01)

    ok("alice told done", len(a.done) >= 1)
    check("nothing pending", a.done[-1][3], 0)
    check("both acked", a.done[-1][2], 2)
    ok("no outbox for groups", a.peer(0x02).outbox == [])

    # Carol leaves, a message now only reaches Bob.
    c.leave()
    pump([a, b, c], rounds=8)

    check("carol out", len(a.members()), 1)

    a.send_group("only bob")
    pump([a, b, c], rounds=6)

    check("bob has two", len(b.got), 2)
    check("carol has one", len(c.got), 1)

    a.close()
    b.close()
    c.close()


def test_group_offline_member():
    net = fresh()

    class Quiet(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

    a = Quiet(config(0x01), nickname="alice", p2p=False, group=0x0007,
        signal_all=True)
    b = Quiet(config(0x02), nickname="bob", p2p=False, group=0x0007,
        signal_all=True)
    c = Quiet(config(0x03), nickname="carol", p2p=False, group=0x0007,
        signal_all=True)

    pump([a, b, c], rounds=8)

    c.close()

    # The liturgy still lists carol, but her tunnel is not alive, so the
    # message goes to bob only and the tally closes out.
    pump([a, b], rounds=8)
    msg = a.send_group("bob alone")
    pump([a, b], rounds=6)

    check("delivered to bob", len(b.got), 1)
    ok("tally closed", len(a.tallies) == 0)

    a.close()
    b.close()


def test_group_join_leave():
    net = fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False, group=0)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False, group=0)

    pump([a, b])

    check("no group", a.group, 0)

    a.join(0x0007)
    b.join(0x0007)
    pump([a, b], rounds=8)

    check("in group", a.group, 0x0007)
    check("bob is member", len(a.members()), 1)

    a.join(0x0008)
    pump([a, b], rounds=8)

    check("switched", a.group, 0x0008)
    check("bob left behind", len(a.members()), 0)

    a.leave()
    pump([a, b])

    check("left", a.group, 0)

    a.close()
    b.close()


#
# Guards and edges.
#

def test_limits():
    net = fresh()
    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False, group=0)

    check("max message", len(pygospel.GospelChat._check_text("x" * 512)), 512)
    long = pygospel.GospelChat._check_text
    ok("too long", raises(pygospel.GospelError, long, "x" * 513))
    ok("empty", raises(pygospel.GospelError, long, ""))
    ok("bytes", raises(pygospel.GospelError, long, b"bytes"))

    ok("peer 0", raises(pygospel.GospelError, a.send, 0, "hi"))
    ok("peer 255", raises(pygospel.GospelError, a.send, 255, "hi"))

    # Addressing ourselves is allowed, there is just no one to deliver it.
    a.send(0x01, "to myself")

    ok("held for ourselves", len(a.peer(0x01).outbox) == 1)
    pump([a], rounds=4)
    ok("never delivered", a.peer(0x01).signal_tunnel is None)

    check("nick ok", pygospel.GospelChat._check_nick("alice"), "alice")
    check("nick too short", pygospel.GospelChat._check_nick("a"), "")
    check("nick too long", pygospel.GospelChat._check_nick("a" * 17), "")
    check("nick symbols", pygospel.GospelChat._check_nick("al!ce"), "")
    check("nick none", pygospel.GospelChat._check_nick(None), "")

    b = pygospel.GospelChat(config(0x02), nickname="bad nick!", p2p=False,
        group=0)

    check("chat drops bad nick", b.nickname, "")
    hb = b.peer(0x01).signal_tunnel
    ok("nick is empty on wire", b.nickname == "")

    a.close()
    b.close()


def test_send_no_peer():
    net = fresh()
    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False, group=0)

    # No peer around: the message is simply held.
    msg = a.send(0x02, "to nobody")

    ok("held without tunnel", len(a.peer(0x02).outbox) == 1)
    check("not delivered", msg.delivered, False)

    pump([a], rounds=3)

    ok("still held", len(a.peer(0x02).outbox) == 1)
    check("status held", a.status()["held"], 1)

    a.forget(0x02)

    ok("forgotten", a.peer(0x02).outbox == [])

    a.close()


def test_status_and_repr():
    net = fresh()
    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False, group=0)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False, group=0)

    pump([a, b], rounds=6)

    st = a.status()

    check("status id", st["id"], "01")
    check("status nick", st["nickname"], "alice")
    ok("status cathedral", st["cathedral"][0].startswith("203.0.113."))

    pump([a, b], rounds=6)

    m = a.send(0x02, "hi")
    pump([a, b], rounds=8)

    check("where", m.where, "02")
    ok("repr", repr(m).startswith("Message(02: "))
    ok("peer repr", repr(a.peer(0x02)).startswith("Peer(02, "))
    check("best name", a.peer(0x02).best_name, "bob")
    ok("quote repr", "Quote(" in repr(pygospel.Quote(1, 2)))

    a.close()
    b.close()


def test_contexts_released():
    net = fresh()
    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False, group=0)

    pump([a])

    check("signaling only", len(net.contexts), 1)

    a.join(0x0007)
    pump([a])

    check("two liturgies", len(net.contexts), 2)

    a.leave()
    pump([a])

    check("one again", len(net.contexts), 2 - 1)

    a.close()

    check("all gone", len(net.contexts), 0)
    check("no sockets", len(net.sockets), 0)


def test_features_are_ours():
    """We must never announce a feature we cannot back up."""

    net = fresh()
    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False, group=0,
        signal_all=True)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False, group=0,
        signal_all=True)

    pump([a, b], rounds=8)

    tunnel = b.peer(0x01).signal_tunnel

    ok("a tunnel is up", tunnel is not None)
    check("nothing announced", tunnel.features & ~pygospel.FEATURES, 0)
    ok("no data bit", not tunnel.features & pygospel.FEATURE_DATA)
    ok("no avatar bit", not tunnel.features & pygospel.FEATURE_AVATAR)
    ok("no name bit", not tunnel.features & pygospel.FEATURE_NAME)

    a.close()
    b.close()


def test_domains():
    """One-to-one and group tunnels of the same pair must be apart."""

    net = fresh()
    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        group=0x0007, signal_all=True)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        group=0x0007, signal_all=True)

    pump([a, b], rounds=8)

    direct = a.peer(0x02).signal_tunnel
    group = a.peer(0x02).group_tunnel

    ok("both exist", direct is not None and group is not None)
    check("same tunnel id", direct.ctx.tunnel, group.ctx.tunnel)
    ok("different flock", direct.ctx.flock_src != group.ctx.flock_src)
    check("direct domain", direct.ctx.flock_src & 0xff, pygospel.FLOCK_DOMAIN)
    check("group domain", group.ctx.flock_src & 0xff, pygospel.GROUP_DOMAIN)
    check("tunnel number", direct.ctx.tunnel, 0x0102)
    check("other side", b.peer(0x01).signal_tunnel.ctx.tunnel, 0x0201)

    a.close()
    b.close()


def test_mentions():
    net = fresh()
    a = pygospel.GospelChat(config(0x01, instance="alice"), nickname="alice",
        p2p=False, group=0)

    ok("by nick", a.peer(0x02) is not None and
        pygospel._mentions("hey @alice", a._mention_names()))
    ok("by id", pygospel._mentions("@01 is here", a._mention_names()))
    ok("no mention", not pygospel._mentions("@bob is here",
        a._mention_names()))

    a.close()


def test_resend():
    net = fresh()

    class Once(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

    a = Once(config(0x01), nickname="alice", p2p=False, group=0,
        signal_all=True)
    b = Once(config(0x02), nickname="bob", p2p=False, group=0,
        signal_all=True)

    pump([a, b])

    # Bob stops answering without anyone noticing, then comes back.
    b.offline = True
    tunnel_b = b.peer(0x01).signal_tunnel
    original = tunnel_b.ctx.purgatory_input

    def drop(data, shroud):
        return True

    tunnel_b.ctx.purgatory_input = drop

    m = a.send(0x02, "are you there")
    frames = len(net.packets)
    pump([a], rounds=pygospel.RESEND_INTERVAL // 1000 + 3, ms=1000)

    ok("resent", len(net.packets) > frames)

    tunnel_b.ctx.purgatory_input = original
    pump([a, b], rounds=6)

    check("delivered once", len(b.got), 1)
    ok("outbox clear", a.peer(0x02).outbox == [])

    a.close()
    b.close()


def test_store_wait():
    """
    With auto_ack off a message we never took back is handed over again
    after STORE_WAIT, and not before, which is how Vigil treats a platform
    that has not stored it yet.
    """

    net = fresh()

    class Vault(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            self.delivered = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

        def on_delivered(self, ref, msg):
            self.delivered.append(msg)

    a = Vault(config(0x01), nickname="alice", p2p=False, group=0,
        signal_all=True)
    b = Vault(config(0x02), nickname="bob", p2p=False, group=0,
        signal_all=True, auto_ack=False)

    pump([a, b], rounds=8)

    a.send(0x02, "keep me")
    pump([a, b], rounds=4)

    check("handed over once", len(b.got), 1)

    tunnel = a.peer(0x02).signal_tunnel
    held = a.peer(0x02).outbox[0]
    frame = pygospel.frame_pack(held.msgid, pygospel.TYPE_TEXT,
        held.text.encode(), len(held.text.encode()))
    peer = b.peer(0x01).signal_tunnel

    # The same frame again, straight away: nothing new happens.
    b._heaven(peer.ctx, frame, 0, peer)
    pump([b], rounds=1)

    check("not handed over twice", len(b.got), 1)
    check("nothing delivered yet", len(a.delivered), 0)

    # Wait past STORE_WAIT without acking and the frame comes back.
    pump([a], rounds=(pygospel.STORE_WAIT // 1000) + 3, ms=1000)
    b._heaven(peer.ctx, frame, 0, peer)
    pump([b], rounds=1)

    check("handed over again", len(b.got), 2)

    # Storing it acks it, and from then a duplicate is only acked.
    b.got[-1].ack()
    pump([a, b], rounds=4)

    check("delivered after storing", len(a.delivered), 1)
    check("outbox drained", len(a.peer(0x02).outbox), 0)

    before = len(net.packets)
    b._heaven(peer.ctx, frame, 0, peer)
    pump([b], rounds=1)

    check("still two handovers", len(b.got), 2)
    ok("duplicate acked again", len(net.packets) > before)

    a.close()
    b.close()


def test_normalized_text():
    """
    Control characters are replaced on the way in, the way Vigil replaces
    them, and duplicates are spotted on the bytes that arrived.
    """

    net = fresh()

    class Counter(pygospel.GospelChat):
        def __init__(self, *args, **kwargs):
            self.got = []
            super().__init__(*args, **kwargs)

        def on_message(self, msg):
            self.got.append(msg)

    a = Counter(config(0x01), nickname="alice", p2p=False, group=0,
        signal_all=True)
    b = Counter(config(0x02), nickname="bob", p2p=False, group=0,
        signal_all=True)

    pump([a, b], rounds=8)

    peer = b.peer(0x01).signal_tunnel
    msgid = a.peer(0x02).signal_tunnel.next_msgid

    a.send(0x02, "a\x07b")
    pump([a, b], rounds=4)

    check("control replaced", b.got[0].text, "a?b")

    # The very same bytes again are the same message.
    frame = pygospel.frame_pack(msgid, pygospel.TYPE_TEXT, b"a?b", 3)
    b._heaven(peer.ctx, frame, 0, peer)
    pump([b], rounds=1)

    check("still one message", len(b.got), 1)

    # The same text before Vigil replaced anything is a different frame on
    # the wire, which is how Vigil counts it too.
    b._heaven(peer.ctx, pygospel.frame_pack(msgid, pygospel.TYPE_TEXT,
        b"a\x07b", 3), 0, peer)
    pump([b], rounds=1)

    check("a second way to spell it", len(b.got), 2)
    check("text is the same", b.got[-1].text, "a?b")

    a.close()
    b.close()


def signed_configs(flock=0x493abf95a07e0c00):
    """Two gospel configurations whose instances Covenant signed."""

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes_raw()

    cfgs = []

    for spi in (0x01, 0x02):
        instance = "device-%02x" % spi
        sig = key.sign(pygospel.name_message(flock, spi, instance))
        cfgs.append(config(spi, instance=instance,
            name_pubkey=pub.hex(), name_signature=sig.hex()))

    return cfgs


def test_signed_name():
    """
    A signed name travels in the heartbeat and is only shown once the flock
    key backs it up, which is the whole point of it.
    """

    net = fresh()
    one, two = signed_configs()

    a = pygospel.GospelChat(one, nickname="alice", p2p=False, group=0,
        signal_all=True)
    b = pygospel.GospelChat(two, nickname="bob", p2p=False, group=0,
        signal_all=True)

    ok("alice knows she is signed", a.named)
    ok("bob knows he is signed", b.named)
    ok("signed name bit announced", a.features & pygospel.FEATURE_NAME)
    check("name status", a.name_status, "signed as device-01")

    pump([a, b], rounds=10)

    check("alice sees bob signed", b.peer(0x01).name, "device-01")
    check("bob sees alice signed", a.peer(0x02).name, "device-02")
    ok("it is marked signed", a.peer(0x02).signed)
    check("best name prefers it", a.peer(0x02).best_name, "device-02")
    check("nickname still there", a.peer(0x02).nick, "bob")

    a.close()
    b.close()


def test_signed_name_is_not_a_suggestion():
    """
    A name signed for another device, or not signed at all, must not show
    up as a signed name. The flock comes from our own configuration and
    the KEK id from the tunnel it arrived on, so neither can be borrowed.
    """

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    net = fresh()
    one, two = signed_configs()

    a = pygospel.GospelChat(one, nickname="alice", p2p=False, group=0,
        signal_all=True)
    b = pygospel.GospelChat(two, nickname="bob", p2p=False, group=0,
        signal_all=True)

    pump([a, b], rounds=8)

    ok("setup saw the name", a.peer(0x02).name == "device-02")

    # What arrives on our tunnel to bob, from him.
    tunnel = a.peer(0x02).signal_tunnel
    key = Ed25519PrivateKey.generate()

    # Sign device-02's name with the KEK id of device-01, so it is a valid
    # signature over the wrong device.
    stolen = key.sign(pygospel.name_message(0x493abf95a07e0c00, 0x01,
        "device-02"))

    hb = pygospel.heartbeat_pack(tunnel.local_uid, "mallory", False,
        pygospel.FEATURE_NAME, b"device-02", stolen)
    a._heaven(tunnel.ctx, hb, 0, tunnel)
    pump([a], rounds=1)

    check("a name for another device is no name", a.peer(0x02).name, "")
    ok("and it is not marked signed", not a.peer(0x02).signed)

    # A name from a flock we know nothing about.
    other = key.sign(pygospel.name_message(0xdeadbeefcafebabe, 0x02, "victim"))
    hb = pygospel.heartbeat_pack(tunnel.local_uid, "mallory", False,
        pygospel.FEATURE_NAME, b"victim", other)
    a._heaven(tunnel.ctx, hb, 0, tunnel)
    pump([a], rounds=1)

    check("wrong flock is no name", a.peer(0x02).name, "")

    # Unsigned bytes with the bit set.
    hb = pygospel.heartbeat_pack(tunnel.local_uid, "mallory", False,
        pygospel.FEATURE_NAME, b"someone", b"\x00" * 64)
    a._heaven(tunnel.ctx, hb, 0, tunnel)
    pump([a], rounds=1)

    check("no signature is no name", a.peer(0x02).name, "")

    # A peer that drops the bit loses the name it had.
    hb = pygospel.heartbeat_pack(tunnel.local_uid, "bob", False, 0)
    a._heaven(tunnel.ctx, hb, 0, tunnel)
    pump([a], rounds=1)

    check("bit off clears it", a.peer(0x02).name, "")
    check("nick survives", a.peer(0x02).nick, "bob")

    a.close()
    b.close()


def test_no_verifier_means_no_names():
    """
    Without something that can do ed25519 we must show no signed names at
    all, and must not announce that we have one.
    """

    net = fresh()
    one, two = signed_configs()

    saved = (pygospel._Verifier.fn, pygospel._Verifier.looked,
        pygospel._Verifier.why)

    pygospel._Verifier.fn = None
    pygospel._Verifier.looked = True
    pygospel._Verifier.why = "no ed25519 verifier"

    try:
        a = pygospel.GospelChat(one, nickname="alice", p2p=False, group=0,
            signal_all=True)
        b = pygospel.GospelChat(two, nickname="bob", p2p=False, group=0,
            signal_all=True)

        ok("we are not signed", not a.named)
        check("and we say why", a.name_status, "no ed25519 verifier")
        ok("no name bit", not a.features & pygospel.FEATURE_NAME)

        pump([a, b], rounds=8)

        check("no name shown", a.peer(0x02).name, "")
        ok("nickname still works", a.peer(0x02).nick == "bob")

        a.close()
        b.close()
    finally:
        (pygospel._Verifier.fn, pygospel._Verifier.looked,
            pygospel._Verifier.why) = saved


def test_half_a_name():
    """A signature without a key, or the other way round, signs nothing."""

    net = fresh()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes_raw()
    msg = pygospel.name_message(0x493abf95a07e0c00, 0x01, "device-01")
    sig = key.sign(msg)

    only_sig = pygospel.GospelChat(config(0x01, instance="device-01",
        name_signature=sig.hex()), p2p=False, group=0)

    ok("signature alone", not only_sig.named)
    ok("says half", "half" in only_sig.name_status)
    ok("no bit announced", not only_sig.features & pygospel.FEATURE_NAME)

    only_key = pygospel.GospelChat(config(0x01, instance="device-01",
        name_pubkey=pub.hex()), p2p=False, group=0)

    ok("key alone", not only_key.named)

    # A signature over a different instance than the one we send.
    renamed = config(0x01, instance="someone-else", name_pubkey=pub.hex(),
        name_signature=sig.hex())
    other = pygospel.GospelChat(renamed, p2p=False, group=0)

    ok("does not verify", not other.named)
    ok("says it did not", "verify" in other.name_status)

    # Nothing at all is normal, not an error.
    plain = pygospel.GospelChat(config(0x01), p2p=False, group=0)

    check("no name at all", plain.name_status,
        "no signed name in the configuration")
    ok("plain stays quiet", not plain.features & pygospel.FEATURE_NAME)

    for chat in (only_sig, only_key, other, plain):
        chat.close()


def test_name_in_group():
    """A group tunnel must resolve names too, on every member tunnel."""

    net = fresh()
    one, two = signed_configs()

    a = pygospel.GospelChat(one, nickname="alice", p2p=False, group=0x0007,
        signal_all=True)
    b = pygospel.GospelChat(two, nickname="bob", p2p=False, group=0x0007,
        signal_all=True)

    pump([a, b], rounds=12)

    ok("in a group together", a.peer(0x02).member)
    check("named over the group tunnel", a.peer(0x02).name, "device-02")

    # And a name we already had must survive dropping out of the group.
    a.leave()
    pump([a, b], rounds=6)

    check("name kept", a.peer(0x02).name, "device-02")

    a.close()
    b.close()


PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40


class Takes:
    """What Vigil does with the chunks a tunnel gets: rebuild and ack."""

    def __init__(self, tunnel, drop=(), silent=False):
        self.tunnel = tunnel
        self.drop = set(drop)
        self.silent = silent
        self.plain = tunnel.on_frame
        self.chunks = {}
        self.blob = None
        self.ids = set()
        self.dropped = 0
        self.sizes = set()
        tunnel.on_frame = self.frame

    def frame(self, buf):
        if len(buf) == pygospel.FRAME_SIZE:
            self.plain(buf)
            return

        self.sizes.add(len(buf))

        msgid, idx, count, size = pygospel.CHUNK_HEAD.unpack_from(buf, 0)
        self.ids.add(msgid)

        if idx in self.drop:
            self.drop.remove(idx)
            self.dropped += 1
            return

        if self.silent:
            return

        self.chunks[idx] = buf[pygospel.CHUNK_HEAD.size:]
        base = 0

        while base in self.chunks:
            base += 1

        bits = bytearray(504)

        for off in range(min(504 * 8, count - base - 1)):
            if base + 1 + off in self.chunks:
                bits[off // 8] |= 1 << (off % 8)

        self.tunnel.send_frame(pygospel.frame_pack(msgid,
            pygospel.TYPE_DATA_ACK,
            pygospel.DATA_ACK_HEAD.pack(base, idx) + bytes(bits)))

        if base == count and self.blob is None:
            self.blob = b"".join(self.chunks[n] for n in range(count))[:size]
            self.tunnel.send_ack(msgid)


def image_pair(**kwargs):
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True, features=pygospel.FEATURE_DATA)

    pump([a, b], rounds=8)

    return a, b, Takes(b.peer(0x01).signal_tunnel, **kwargs)


def test_image_blob():
    a, b, takes = image_pair()
    sent, delivered, heard = [], [], []

    a.on_sent = lambda ref, msg: sent.append((ref, msg.name))
    a.on_delivered = lambda ref, msg: delivered.append((ref, msg.name))
    b.on_message = lambda msg: heard.append(msg.text)

    msg = a.send_image(0x02, PNG, name="plot.png", ref=7)

    check("the message is named", msg.name, "plot.png")
    check("it is held", len(a.outbox(0x02)), 1)

    pump([a, b], rounds=20, ms=50)

    head = pygospel.DATA_HEAD.unpack_from(takes.blob, 0)

    check("one chunk size", takes.sizes, {1172})
    check("kind", head[0], 1)
    check("name", head[2][:head[1]], b"plot.png")
    check("head size", pygospel.DATA_HEAD.size, 128)
    check("data", takes.blob[128:], PNG)
    check("on_sent once", sent, [(7, "plot.png")])
    check("on_delivered once", delivered, [(7, "plot.png")])
    check("no longer held", a.outbox(0x02), [])
    check("transfer gone", a.peer(0x02).signal_tunnel.xfer, None)
    check("no notice either", heard, [])

    a.close()
    b.close()


def test_image_lost_chunks():
    a, b, takes = image_pair(drop=(0, 3, 5))

    a.send_image(0x02, PNG)
    pump([a, b], rounds=40, ms=100)

    check("they were lost", takes.dropped, 3)
    check("it got there anyway", takes.blob[128:], PNG)
    check("no longer held", a.outbox(0x02), [])

    a.close()
    b.close()


def test_image_stalls_and_starts_over():
    a, b, takes = image_pair(silent=True)

    a.send_image(0x02, PNG)
    pump([a, b], rounds=3, ms=100)

    tunnel = a.peer(0x02).signal_tunnel
    old = tunnel.xfer.next

    ok("chunks went out", old > 0)
    pump([a, b], rounds=25, ms=1000)
    takes.silent = False
    pump([a, b], rounds=40, ms=100)

    check("it got there anyway", takes.blob[128:], PNG)

    a.close()
    b.close()


def test_image_probes_for_the_ack():
    a, b, takes = image_pair()
    plain = takes.tunnel.send_ack
    takes.tunnel.send_ack = lambda msgid: None

    a.send_image(0x02, PNG)
    pump([a, b], rounds=20, ms=50)

    tunnel = a.peer(0x02).signal_tunnel

    ok("all chunks are there", tunnel.xfer.done)
    ok("not a busy loop anymore", not a._busy())

    takes.sizes.clear()
    pump([a, b], rounds=3, ms=2000)

    check("the first chunk goes out again", takes.sizes, {1172})
    check("still held", len(a.outbox(0x02)), 1)

    takes.tunnel.send_ack = plain
    tunnel._on_ack(tunnel.xfer.held.msgid)

    check("the ack ends it", a.outbox(0x02), [])
    check("transfer gone", tunnel.xfer, None)

    a.close()
    b.close()


def test_image_to_who_takes_no_data():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)
    heard, delivered = [], []

    b.on_message = lambda msg: heard.append(msg.text)
    a.on_delivered = lambda ref, msg: delivered.append(ref)

    a.send_image(0x02, PNG, ref=9)
    pump([a, b], rounds=12)

    check("a notice instead", heard, [pygospel.DATA_NOTICE])
    check("that counts", delivered, [9])

    a.close()
    b.close()


def test_image_waits_for_the_peer():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False)

    a.send_image(0x02, PNG)
    pump([a], rounds=3)

    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True, features=pygospel.FEATURE_DATA)

    for _ in range(40):
        tunnel = b.peer(0x01).signal_tunnel

        if tunnel is not None:
            break

        pump([a, b], rounds=1)

    takes = Takes(tunnel)
    pump([a, b], rounds=40, ms=100)

    check("it got there", takes.blob[128:], PNG)

    a.close()
    b.close()


def test_image_same_ref_keeps_its_id():
    a, b, takes = image_pair()

    a.send_image(0x02, PNG, ref=5)
    pump([a, b], rounds=1, ms=20)
    a.send_image(0x02, PNG, ref=5)
    pump([a, b], rounds=20, ms=50)

    check("one id on the wire", len(takes.ids), 1)
    check("it got there", takes.blob[128:], PNG)

    a.close()
    b.close()


def test_image_forget():
    a, b, takes = image_pair(silent=True)

    a.send_image(0x02, PNG)
    pump([a, b], rounds=3, ms=50)

    tunnel = a.peer(0x02).signal_tunnel

    ok("it is on its way", tunnel.xfer is not None)
    a.forget(0x02)
    check("transfer gone", tunnel.xfer, None)
    ok("nothing to do", not a._busy())

    a.close()
    b.close()


def test_image_restarts_on_a_new_tunnel():
    a, b, takes = image_pair(silent=True)

    a.send_image(0x02, PNG)
    pump([a, b], rounds=3, ms=50)

    tunnel = a.peer(0x02).signal_tunnel
    tunnel.close()

    check("still held", len(a.outbox(0x02)), 1)
    check("no id", a.peer(0x02).outbox[0].msgid, 0)
    ok("still a blob", a.peer(0x02).outbox[0].blob is not None)

    a.close()
    b.close()


def test_image_checks():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False)
    big = PNG + b"\x00" * pygospel.DATA_MAX

    boom("not an image", lambda: a.send_image(0x02, b"hello there"))
    boom("empty", lambda: a.send_image(0x02, b""))
    boom("too large", lambda: a.send_image(0x02, big))
    boom("no peer", lambda: a.send_image(0x00, PNG))
    boom("no group", lambda: a.send_group_image(PNG))
    boom("no file", lambda: a.send_image(0x02, "/nonexistent/a.png"))
    boom("a folder", lambda: a.send_image(0x02, "/tmp"))
    boom("no bytes or path", lambda: a.send_image(0x02, 1.5))

    for name in ("", ".png", "a/b.png", "a\\b.png", "a\nb", "a\x7fb",
        "x" * 127, 5):
        boom("name %r" % name, lambda: a.send_image(0x02, PNG, name=name))

    check("nothing held", a.outbox(0x02), [])

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "plot.png")
        fifo = os.path.join(tmp, "fifo.png")
        text = os.path.join(tmp, "secret.png")

        with open(path, "wb") as f:
            f.write(PNG)

        with open(text, "wb") as f:
            f.write(b"not an image at all")

        os.mkfifo(fifo)

        check("named after the file", a.send_image(0x02, path).name,
            "plot.png")
        check("named by the caller", a.send_image(0x02, path, "x.png").name,
            "x.png")
        check("named by its kind", a.send_image(0x02, PNG).name, "image.png")
        boom("not a file", lambda: a.send_image(0x02, fifo))
        boom("the bytes decide", lambda: a.send_image(0x02, text))

    for kind, data in (("png", PNG), ("jpg", b"\xff\xd8\xff\xe0"),
        ("gif", b"GIF89a"), ("gif", b"GIF87a"),
        ("webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ")):
        check("type " + kind, pygospel.image_type(data), kind)

    a.close()


def test_group_image():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        group=0x0007)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        group=0x0007, features=pygospel.FEATURE_DATA)
    c = pygospel.GospelChat(config(0x03), nickname="carol", p2p=False,
        group=0x0007)
    told, heard = [], []

    a.on_group_delivered = lambda ref, group, acked, pending: \
        told.append((ref, acked, pending))
    c.on_message = lambda msg: heard.append(msg.text)

    pump([a, b, c], rounds=8)

    takes = Takes(b.peer(0x01).group_tunnel)
    msg = a.send_group_image(PNG, name="plot.png", ref=3)

    check("group", msg.group, 0x0007)
    check("name", msg.name, "plot.png")

    pump([a, b, c], rounds=30, ms=100)

    check("the member that takes data", takes.blob[128:], PNG)
    check("the one that does not", heard, [pygospel.DATA_NOTICE])
    check("tally", told[-1], (3, 2, 0))

    a.close()
    b.close()
    c.close()


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)]

    for name, fn in tests:
        failures_start = len(failures)

        try:
            fn()
        except Exception as e:
            import traceback
            failures.append("%s raised %s: %s" % (name, type(e).__name__, e))
            traceback.print_exc()

        status = "ok" if len(failures) == failures_start else "FAIL"

        print("%-30s %s" % (name, status))

    print()
    print("%d checks, %d failures" % (checks, len(failures)))

    for fail in failures:
        print("  " + fail)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
