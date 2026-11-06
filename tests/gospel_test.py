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
import fakeopus  # noqa: E402

sys.modules["libkyrka"] = fakekyrka.Module(fakekyrka.Network())

import pygospel  # noqa: E402

pygospel.opuslib = fakeopus


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

    uid, nick, typing, features, _, _, have = \
        pygospel.heartbeat_unpack(hb[11:])

    check("unpack have", have, 0)
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


def test_silent_cathedral_keeps_a_direct_path():
    fresh()

    chat = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    other = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)

    pump([chat, other], rounds=10)

    tunnel = chat.peer(0x02).signal_tunnel
    direct = ("192.0.2.1", 4242)
    tunnel.addr = direct

    tunnel._cathedral_check(tunnel.cathedral.last +
        tunnel.cathedral.timeout + 1)

    check("direct path stays", tunnel.addr, direct)
    check("notify again", tunnel.next_notify, 0)

    chat.cathedrals.append(("192.0.2.2", 4242))
    tunnel._cathedral_check(tunnel.cathedral.last +
        tunnel.cathedral.timeout + 1)

    check("direct path stays on a switch", tunnel.addr, direct)
    check("cathedral moved", tunnel.cathedral.addr, ("192.0.2.2", 4242))

    chat.close()
    other.close()


def test_silent_cathedral_moves_a_relayed_tunnel():
    fresh()

    chat = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    other = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)

    pump([chat, other], rounds=10)

    tunnel = chat.peer(0x02).signal_tunnel
    chat.cathedrals.append(("192.0.2.2", 4242))

    ok("relayed first", tunnel.relay)

    tunnel._cathedral_check(tunnel.cathedral.last +
        tunnel.cathedral.timeout + 1)

    check("follows the cathedral", tunnel.addr, ("192.0.2.2", 4242))
    ok("still relayed", tunnel.relay)

    chat.close()
    other.close()


def test_group_tunnel_does_not_stop_signaling_back():
    fresh()

    class Up:
        alive = True

    chat = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        group=0)
    target = chat.peer(0x02)
    target.present = True

    check("signal a peer that signals us", chat._signal_want(target), 1)

    target.group_tunnel = Up()
    check("still signal it with a group tunnel", chat._signal_want(target), 1)

    target.signal_tunnel = Up()
    check("not with its own tunnel up", chat._signal_want(target), 0)

    target.group_tunnel = None
    target.signal_tunnel = None
    chat.close()


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


def answer_pair(**kwargs):
    """A sender and a peer, online, and what the peer has been told so far."""

    fresh()

    got = []
    one = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        features=pygospel.FEATURE_REPLY, **kwargs)
    two = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        features=pygospel.FEATURE_REPLY, **kwargs)

    two.on_message = lambda msg: got.append(msg.text)

    return one, two, got


def test_answer_stays_in_order_past_a_waiting_reply():
    one, two, got = answer_pair(signal_all=True)

    pump([one, two], rounds=10)

    one.peer(0x02).signal_tunnel.close()

    one.reply(0x02, "first", pygospel.Quote(0x02, 0x1234))
    one.send(0x02, "second")
    one.send(0x02, "third")

    pump([one, two], rounds=30)

    check("in the order they were written", got, ["first", "second", "third"])
    check("nothing held", one.outbox(0x02), [])

    one.close()
    two.close()


def test_answer_stays_in_order_past_a_long_job():
    one, two, got = answer_pair()
    prompts = []

    one.on_message = lambda msg: prompts.append(msg)

    two.send(0x01, "make me a list")
    pump([one, two], rounds=20)

    check("the prompt arrived", len(prompts), 1)

    pump([one, two], rounds=pygospel.TUNNEL_IDLE // 1000 * 2)

    check("the tunnel went idle", one.peer(0x02).signal_tunnel, None)

    one.reply(0x02, "first", prompts[0])
    one.send(0x02, "second")
    one.send(0x02, "third")

    pump([one, two], rounds=40)

    check("in the order they were written", got, ["first", "second", "third"])

    one.close()
    two.close()


def test_answer_stays_in_order_past_a_lost_frame():
    one, two, got = answer_pair(signal_all=True)

    pump([one, two], rounds=10)

    tunnel = one.peer(0x02).signal_tunnel
    send_frame = tunnel.send_frame
    lost = []

    def lossy(data):
        if not lost and pygospel.frame_unpack(data)[2] == pygospel.TYPE_TEXT:
            lost.append(data)
            return True

        return send_frame(data)

    tunnel.send_frame = lossy

    one.send(0x02, "first")
    one.send(0x02, "second")
    one.send(0x02, "third")

    pump([one, two], rounds=4)

    check("one was lost", len(lost), 1)
    check("nothing went past it", got, [])

    pump([one, two], rounds=pygospel.RESEND_INTERVAL // 1000 + 4)

    check("in the order they were written", got, ["first", "second", "third"])

    one.close()
    two.close()


def test_group_answer_stays_in_order():
    one, two, got = answer_pair(group=0x0007)

    pump([one, two], rounds=10)

    tunnel = one.peer(0x02).group_tunnel
    tunnel.heard = False

    one.reply_group("first", pygospel.Quote(0x02, 0x1234))
    one.send_group("second")

    pump([one, two], rounds=30)

    check("in the order they were written", got, ["first", "second"])

    one.close()
    two.close()


def test_group_message_waits_for_a_tunnel_that_is_down():
    one, two, got = answer_pair(group=0x0007)

    pump([one, two], rounds=10)

    tunnel = one.peer(0x02).group_tunnel
    tunnel.last_rx = pygospel._ms() - pygospel.RX_TIMEOUT - 1

    ok("down", not tunnel.alive)

    one.send_group("for when it is back")

    pump([one, two], rounds=20)

    check("it got there", got, ["for when it is back"])

    one.close()
    two.close()


def test_reply_held_back_has_a_sensible_time():
    one, two, got = answer_pair(signal_all=True)

    pump([one, two], rounds=10)

    one.peer(0x02).signal_tunnel.heard = False

    msg = one.reply(0x02, "first", pygospel.Quote(0x02, 0x1234))

    ok("written just now", abs(msg.ts - pygospel.time.time()) < 5)

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


def avatar_pair():
    """Two chats that take data, online and holding nothing."""

    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True, take_data=True)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True, take_data=True)

    pump([a, b], rounds=8)

    return a, b


def test_avatar_on_the_wire():
    a, b, takes = image_pair()

    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    head = pygospel.DATA_HEAD.unpack_from(takes.blob, 0)

    check("kind", head[0], pygospel.DATA_AVATAR)
    check("name", head[2][:head[1]], b"avatar")
    check("the bytes", takes.blob[pygospel.DATA_HEAD.size:], PNG)
    check("announced", a.features & pygospel.FEATURE_AVATAR,
        pygospel.FEATURE_AVATAR)
    check("no message held", a.outbox(0x02), [])
    check("nothing in flight", a.peer(0x02).signal_tunnel.xfer, None)

    a.close()
    b.close()


def test_avatar_arrives():
    a, b = avatar_pair()
    seen = []

    b.on_avatar = lambda peer: seen.append((peer.id, peer.avatar))
    b.on_data = lambda msg: seen.append(("data", msg.media))

    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    check("it arrived", b.peer(0x01).avatar, PNG)
    check("once, as an avatar", seen, [(0x01, PNG)])
    check("no outbox", a.outbox(0x02), [])

    a.close()
    b.close()


def test_avatar_goes_to_a_group():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        group=0x0007)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        group=0x0007, take_data=True)

    pump([a, b], rounds=10)

    a.set_avatar(PNG)
    pump([a, b], rounds=30, ms=100)

    check("the member has it", b.peer(0x01).avatar, PNG)

    a.close()
    b.close()


def test_avatar_follows_a_change():
    a, b = avatar_pair()
    other = PNG + bytes(64)

    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    a.set_avatar(other)
    pump([a, b], rounds=20, ms=50)

    check("the new one arrived", b.peer(0x01).avatar, other)

    a.close()
    b.close()


def test_avatar_taken_away():
    a, b = avatar_pair()
    seen = []

    b.on_avatar = lambda peer: seen.append(peer.avatar)

    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    a.set_avatar(None)
    pump([a, b], rounds=6, ms=3000)

    check("the bit is off", a.features & pygospel.FEATURE_AVATAR, 0)
    check("ours is gone", a.avatar, None)
    check("theirs is gone", b.peer(0x01).avatar, None)
    check("said once each", seen, [PNG, None])

    a.close()
    b.close()


def test_avatar_starts_on_a_new_tunnel():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)

    a.set_avatar(PNG)

    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True, take_data=True)

    pump([a, b], rounds=25, ms=200)

    check("it arrived", b.peer(0x01).avatar, PNG)

    a.close()
    b.close()


def test_avatar_waits_for_data():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)
    heard = []

    b.on_message = lambda msg: heard.append(msg.text)

    a.set_avatar(PNG)
    pump([a, b], rounds=10)

    tunnel = a.peer(0x02).signal_tunnel

    check("nothing queued", tunnel.avatar, None)
    check("nothing in flight", tunnel.xfer, None)
    check("no notice text either", heard, [])

    a.close()
    b.close()


def test_avatar_alongside_an_image():
    a, b = avatar_pair()
    sent = []

    a.on_sent = lambda ref, msg: sent.append(ref)
    a.set_avatar(PNG)
    msg = a.send_image(0x02, PNG, ref=9)
    pump([a, b], rounds=40, ms=50)

    check("the image was held", len(a.outbox(0x02)), 0)
    check("sent once, for the image", sent, [9])
    check("both arrived", b.peer(0x01).avatar, PNG)

    a.close()
    b.close()


def test_avatar_have_on_the_wire():
    hb = pygospel.heartbeat_pack(b"\x01" * 8, "bob", False, 0, b"", b"",
        0x1122334455667788)

    off = 11 + 8 + 16 + 1 + 1 + 64 + 64

    check("where it goes", hb[off:off + 8],
        b"\x11\x22\x33\x44\x55\x66\x77\x88")
    check("what comes back", pygospel.heartbeat_unpack(hb[11:])[6],
        0x1122334455667788)
    check("the hash of Vigil", pygospel._fnv1a(b"a"), 0xaf63dc4c8601ec8c)


def avatar_held_pair():
    """Two chats where b holds the avatar of a, and a heard that it does."""

    a, b = avatar_pair()

    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    check("it arrived", b.peer(0x01).avatar, PNG)

    b.peer(0x01).signal_tunnel.next_heartbeat = 0
    pump([a, b], rounds=4, ms=50)

    check("a heard", a.peer(0x02).signal_tunnel.avatar_have,
        pygospel._avatar_hash(PNG))

    return a, b


def test_avatar_not_sent_again_to_a_peer_holding_it():
    a, b = avatar_held_pair()
    seen = []

    b.on_avatar = lambda peer: seen.append(peer.avatar)

    tunnel = a.peer(0x02).signal_tunnel
    tunnel.avatar_serial = 0
    pump([a, b], rounds=20, ms=50)

    check("nothing queued", tunnel.avatar, None)
    check("nothing arrived", seen, [])
    check("not asked again", tunnel.avatar_serial, a.avatar_serial)

    a.close()
    b.close()


def test_avatar_change_goes_to_a_peer_holding_the_old_one():
    a, b = avatar_held_pair()
    other = PNG + b"\x00"

    a.set_avatar(other)
    pump([a, b], rounds=20, ms=50)

    check("the new one", b.peer(0x01).avatar, other)

    a.close()
    b.close()


def test_avatar_sent_again_when_peer_says_it_lost_it():
    a, b = avatar_held_pair()
    seen = []

    b.on_avatar = lambda peer: seen.append(peer.avatar)

    tunnel = a.peer(0x02).signal_tunnel
    tunnel.on_frame(pygospel.heartbeat_pack(tunnel.remote_uid, "bob", False,
        pygospel.FEATURE_DATA))
    pump([a, b], rounds=20, ms=50)

    check("sent again", seen, [PNG])

    a.close()
    b.close()


def test_avatar_taken_away_stops_saying_we_hold_it():
    a, b = avatar_held_pair()

    a.set_avatar(None)
    a.peer(0x02).signal_tunnel.next_heartbeat = 0
    pump([a, b], rounds=4, ms=50)

    check("dropped", b.peer(0x01).avatar, None)
    check("says so", b.peer(0x01).avatar_held, 0)

    a.close()
    b.close()


def test_avatar_goes_after_waiting_data():
    a, b = avatar_pair()
    order = []

    b.on_data = lambda msg: order.append("image")
    b.on_avatar = lambda peer: order.append("avatar")

    a.set_avatar(PNG)
    a.send_image(0x02, PNG, ref=9)
    pump([a, b], rounds=40, ms=50)

    check("the image first", order, ["image", "avatar"])

    a.close()
    b.close()


def test_image_with_ref_zero_is_sent():
    a, b = avatar_pair()
    sent = []

    a.on_sent = lambda ref, msg: sent.append(ref)
    a.send_image(0x02, PNG, ref=0)
    pump([a, b], rounds=40, ms=50)

    check("said so", sent, [0])

    a.close()
    b.close()


def test_avatar_takes_one_seen_slot():
    a, b = avatar_pair()
    tunnel = b.peer(0x01).signal_tunnel
    before = tunnel.seen_next

    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    check("it arrived", b.peer(0x01).avatar, PNG)
    check("one slot", tunnel.seen_next - before, 1)

    a.close()
    b.close()


def test_avatar_repushed_when_peer_restarts():
    a, b = avatar_pair()

    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    check("it arrived", b.peer(0x01).avatar, PNG)

    # Our peer came back up with new ids and nothing of what it had.
    b.peer(0x01).avatar = None
    btun = b.peer(0x01).signal_tunnel
    btun.local_uid = os.urandom(8)
    btun.next_heartbeat = 0

    pump([a, b], rounds=20, ms=50)

    check("it came again", b.peer(0x01).avatar, PNG)

    a.close()
    b.close()


def test_avatar_checks():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False)

    boom("not an image", lambda: a.set_avatar(b"hello there"))
    boom("too large", lambda: a.set_avatar(
        PNG + bytes([0] * pygospel.AVATAR_MAX)))
    boom("neither bytes nor a path", lambda: a.set_avatar(1.5))

    check("nothing announced", a.features & pygospel.FEATURE_AVATAR, 0)
    check("nothing to send", a.avatar, None)

    a.set_avatar(b"")
    check("empty bytes take it away", a.avatar, None)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "face.png")

        with open(path, "wb") as f:
            f.write(PNG)

        a.set_avatar(path)
        check("from a file", a.avatar, PNG)

    a.close()


def test_avatar_is_no_activity():
    a, b = avatar_pair()

    b.peer(0x01).active = 0
    a.set_avatar(PNG)
    pump([a, b], rounds=20, ms=50)

    check("it arrived", b.peer(0x01).avatar, PNG)

    a.close()
    b.close()


def test_avatar_too_large_is_dropped():
    a, b = avatar_pair()
    tunnel = b.peer(0x01).signal_tunnel
    frames = []
    seen = []
    whole = blob(pygospel.DATA_AVATAR, "avatar",
        bytes([0] * (pygospel.AVATAR_MAX + 1)))

    tunnel.send_frame = lambda frame: frames.append(frame)
    b.on_avatar = lambda peer: seen.append(peer.avatar)

    for one in chunks(70, whole):
        tunnel.on_frame(one)

    types = [pygospel.frame_unpack(f)[2] for f in frames
        if len(f) == pygospel.FRAME_SIZE]

    ok("acked", pygospel.TYPE_ACK in types)
    check("nothing kept", b.peer(0x01).avatar, None)
    check("nothing said", seen, [])

    a.close()
    b.close()


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


def data_pair(**kwargs):
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True, features=pygospel.FEATURE_DATA)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True, take_data=True, **kwargs)
    got = []

    b.on_data = got.append
    pump([a, b], rounds=8)

    return a, b, got


def blob(kind, name, data):
    raw = name if isinstance(name, bytes) else name.encode()

    return pygospel.DATA_HEAD.pack(kind, len(raw), raw) + data


def chunks(msgid, whole):
    count = (len(whole) + 1151) // 1152
    out = []

    for idx in range(count):
        out.append(pygospel.CHUNK_HEAD.pack(msgid, idx, count, len(whole)) +
            whole[idx * 1152:(idx + 1) * 1152].ljust(1152, b"\x00"))

    return out


def test_data_bit():
    a, b, got = data_pair()

    ok("announced", b.features & pygospel.FEATURE_DATA)
    ok("heard", a.peer(0x02).signal_tunnel.features & pygospel.FEATURE_DATA)

    a.close()
    b.close()


def test_data_comes_in():
    a, b, got = data_pair()
    delivered = []

    a.on_delivered = lambda ref, msg: delivered.append(ref)
    a.send_image(0x02, PNG, name="plot.png", ref=4)
    pump([a, b], rounds=20, ms=50)

    check("one arrived", len(got), 1)
    check("bytes", got[0].data, PNG)
    check("name", got[0].name, "plot.png")
    check("media", got[0].media, "image")
    check("peer", got[0].peer, 0x01)
    check("author", got[0].author, 0x01)
    check("group", got[0].group, None)
    check("the sender knows", delivered, [4])
    check("no transfer left", b.peer(0x01).signal_tunnel.rxfer, None)
    check("none counted", b.rxfers, 0)
    ok("not busy", not b._busy())

    a.close()
    b.close()


def test_data_no_bit_no_data():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        signal_all=True)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        signal_all=True)
    got = []

    b.on_data = got.append
    pump([a, b], rounds=8)

    tunnel = b.peer(0x01).signal_tunnel

    for one in chunks(9, blob(1, "a.png", PNG)):
        tunnel.on_frame(one)

    check("dropped", got, [])
    check("no transfer", tunnel.rxfer, None)

    a.close()
    b.close()


def test_data_kinds():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel

    for msgid, kind in ((50, pygospel.DATA_FILE), (51, pygospel.DATA_VOICE)):
        for one in chunks(msgid, blob(kind, "x", b"data")):
            tunnel.on_frame(one)

    check("media", [m.media for m in got], ["file", "voice"])

    a.close()
    b.close()


def test_data_dropped_but_acked():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel
    frames = []

    tunnel.send_frame = lambda frame: frames.append(frame)

    for msgid, whole in ((60, blob(0, "avatar", b"data")),
        (61, blob(77, "x", b"data")), (62, blob(1, "../evil", b"data")),
        (63, blob(1, ".hidden", b"data")), (64, blob(1, "a\\b", b"data")),
        (65, blob(1, "", b"data")),
        (66, pygospel.struct.pack("!BB", 1, 200) + b"x" * 200)):
        frames.clear()

        for one in chunks(msgid, whole):
            tunnel.on_frame(one)

        types = [pygospel.frame_unpack(f)[2] for f in frames]

        ok("acked %d" % msgid, pygospel.TYPE_ACK in types)

    check("none handed over", got, [])

    a.close()
    b.close()


def test_data_bad_chunks():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel
    head = pygospel.CHUNK_HEAD
    pad = b"\x00" * 1152
    big = pygospel.WIRE_MAX + 1

    for what, fields in (("no id", (0, 0, 1, 200)),
        ("no count", (1, 0, 0, 200)),
        ("too many", (1, 0, pygospel.CHUNKS_MAX + 1, 200)),
        ("too small", (1, 0, 1, 128)),
        ("too large", (1, 0, 3642, big)),
        ("size past count", (1, 0, 1, 1153)),
        ("size short of count", (1, 0, 2, 1152)),
        ("index past count", (1, 1, 1, 200))):
        tunnel.on_frame(head.pack(*fields) + pad)
        check(what, tunnel.rxfer, None)

    a.close()
    b.close()


def test_data_new_id_replaces_old():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel
    old = chunks(70, blob(1, "a", PNG))
    new = chunks(71, blob(1, "b", b"x"))

    tunnel.on_frame(old[0])
    check("one going", b.rxfers, 1)
    tunnel.on_frame(new[0])

    check("the new one", [m.name for m in got], ["b"])
    check("old one gone", b.rxfers, 0)

    a.close()
    b.close()


def test_data_out_of_order_and_dupes():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel
    parts = chunks(72, blob(1, "a", PNG))

    for one in reversed(parts):
        tunnel.on_frame(one)
        tunnel.on_frame(one)

    check("once", len(got), 1)
    check("whole", got[0].data, PNG)

    for one in parts:
        tunnel.on_frame(one)

    check("not again", len(got), 1)

    a.close()
    b.close()


def test_data_stalls():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel

    tunnel.on_frame(chunks(73, blob(1, "a", PNG))[0])
    ok("busy", b._busy())
    pump([b], rounds=3, ms=pygospel.DATA_STALL)

    check("given up", b.rxfers, 0)
    ok("not busy", not b._busy())

    a.close()
    b.close()


def test_data_limit_on_transfers():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel

    b.rxfers = pygospel.RXFERS_MAX
    tunnel.on_frame(chunks(74, blob(1, "a", PNG))[0])
    check("not taken", tunnel.rxfer, None)
    b.rxfers = 0

    a.close()
    b.close()


def test_data_closed_tunnel_counts_down():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel

    tunnel.on_frame(chunks(75, blob(1, "a", PNG))[0])
    check("one going", b.rxfers, 1)
    tunnel.close()
    check("none going", b.rxfers, 0)

    a.close()
    b.close()


def test_data_no_auto_ack():
    a, b, got = data_pair(auto_ack=False)
    tunnel = b.peer(0x01).signal_tunnel
    parts = chunks(76, blob(1, "a", PNG))
    frames = []

    tunnel.send_frame = lambda frame: frames.append(frame)

    for one in parts:
        tunnel.on_frame(one)

    types = [pygospel.frame_unpack(f)[2] for f in frames]

    check("handed over", len(got), 1)
    ok("no ack yet", pygospel.TYPE_ACK not in types)

    frames.clear()
    tunnel.on_frame(parts[0])

    types = [pygospel.frame_unpack(f)[2] for f in frames]

    check("not handed over again", len(got), 1)
    check("only says it has it", types, [pygospel.TYPE_DATA_ACK])

    frames.clear()
    got[0].ack()

    check("now it does", [pygospel.frame_unpack(f)[2] for f in frames],
        [pygospel.TYPE_ACK])

    a.close()
    b.close()


def test_data_never_acked_comes_again():
    a, b, got = data_pair(auto_ack=False)
    tunnel = b.peer(0x01).signal_tunnel
    parts = chunks(77, blob(1, "a", PNG))

    for one in parts:
        tunnel.on_frame(one)

    pygospel.time.sleep((pygospel.DATA_WAIT + 1) / 1000.0)

    for one in parts:
        tunnel.on_frame(one)

    check("again", len(got), 2)

    a.close()
    b.close()


def test_data_ack_bits():
    a, b, got = data_pair()
    tunnel = b.peer(0x01).signal_tunnel
    parts = chunks(78, blob(1, "a", b"x" * 1152 * 20))
    frames = []

    tunnel.send_frame = lambda frame: frames.append(frame)

    for idx in (0, 1, 3, 5):
        tunnel.on_frame(parts[idx])

    tunnel._data_ack(78, tunnel.rxfer, len(parts))

    msgid, length, kind, payload = pygospel.frame_unpack(frames[-1])
    base, latest = pygospel.DATA_ACK_HEAD.unpack_from(payload, 0)

    check("id", msgid, 78)
    check("length", length, 512)
    check("kind", kind, pygospel.TYPE_DATA_ACK)
    check("base", base, 2)
    check("latest", latest, 5)
    check("bits past the base", payload[8] & 0x0f, 0b0101)

    a.close()
    b.close()


def test_group_data():
    fresh()

    a = pygospel.GospelChat(config(0x01), nickname="alice", p2p=False,
        group=0x0007)
    b = pygospel.GospelChat(config(0x02), nickname="bob", p2p=False,
        group=0x0007, take_data=True)
    got = []

    b.on_data = got.append
    pump([a, b], rounds=8)

    tunnel = b.peer(0x01).group_tunnel

    for one in chunks(80, blob(pygospel.DATA_VOICE, "v", b"data")):
        tunnel.on_frame(one)

    check("the group", got[0].group, 0x0007)
    check("where", got[0].where, "01@0007")

    a.close()
    b.close()


#
# Calls.
#

class Caller(pygospel.GospelChat):
    """A chat that keeps what its calls tell it."""

    def __init__(self, *args, answer=False, **kwargs):
        self.rang = []
        self.ended = []
        self.heard = []
        self.started = []
        self.answers = answer
        super().__init__(*args, **kwargs)

    def on_call_ring(self, call):
        self.rang.append(call)

        if self.answers:
            call.answer()

    def on_call_talking(self, call):
        self.started.append(call)

    def on_call_audio(self, call, pcm):
        self.heard.append(pcm)

    def on_call_end(self, call, reason):
        self.ended.append(reason)


def tone(value):
    return bytes([value]) * pygospel.VOICE_BYTES


def callers(answer=False):
    net = fresh()

    return (net, Caller(config(1)),
        Caller(config(2), take_calls=True, answer=answer))


def talking(answer=True):
    """Two chats on a call that has been set up."""

    net, alice, bob = callers(answer)
    call = alice.call(2)

    pump([alice, bob], rounds=6)
    pump([alice, bob], rounds=10, ms=20)

    return net, alice, bob, call, bob.active_call


def test_voice_frame():
    frame = pygospel.voice_pack(pygospel.VOICE_AUDIO, 0x0102, b"abc")

    check("voice size", len(frame), 160)
    check("voice head", frame[:6], b"\x01\x00\x01\x02\x00\x03")
    check("voice opus", frame[6:9], b"abc")
    check("voice pad", frame[9:], bytes(151))
    check("voice back", pygospel.voice_unpack(frame),
        (pygospel.VOICE_AUDIO, 0x0102, b"abc"))
    check("hangup size", len(pygospel.voice_pack(pygospel.VOICE_HANGUP)), 160)
    check("short frame", pygospel.voice_unpack(frame[:159]), None)
    check("long frame", pygospel.voice_unpack(frame + b"x"), None)
    check("bad length", pygospel.voice_unpack(
        struct.pack("!BBHH", 1, 0, 1, 155) + bytes(154)), None)
    boom("too much opus", lambda: pygospel.voice_pack(1, 1, bytes(155)))


def test_call_contexts():
    net, alice, bob = callers()

    lit = bob.call_signaling
    flock = int(FLOCK, 16) | 0x0a

    check("signals in the call domain", lit.ctx.flock_src, flock)
    check("signals in liturgy_group", lit.ctx.group, 1)
    check("signals as itself", lit.ctx.tunnel, 2)
    check("signaling remembers", lit.ctx.remembrance, True)
    check("no liturgy without a call", alice.call_signaling, None)

    call = alice.call(2)

    check("tunnel is ours then theirs", call.ctx.tunnel, 0x0102)
    check("tunnel is in the call domain", call.ctx.flock_src, flock)
    check("tunnel in no group", call.ctx.group, 0)
    check("tunnel has the kek", call.ctx.kek is not None, True)
    check("mtu is capped", call.ctx.mtu, 1100)
    ok("liturgy for the dialer", alice.call_signaling is not None)

    small = Caller(config(3, mtu=800))

    check("smaller mtu stays", small.call(1).ctx.mtu, 800)


def test_call_serial_is_signaled():
    net, alice, bob = callers()
    call = alice.call(2)

    pump([alice], rounds=1)

    want = alice.call_signaling.ctx.wanted

    ok("a serial, never 0", 1 <= call.serial <= 255)
    check("only for that peer", want[2], call.serial)
    check("nobody else", sum(1 for b in want if b), 1)

    call.hangup()
    pump([alice], rounds=2, ms=20)

    check("stops at a hangup", alice.call_signaling.ctx.wanted[2], 0)


def test_call_rings_only_with_a_tunnel():
    net, alice, bob = callers()

    bob._call_push(bytes([0, 7]) + bytes(253))
    pump([bob], rounds=1)

    ok("working on it", bob.active_call is not None)
    check("incoming", bob.active_call.incoming, True)
    check("connecting", bob.active_call.state, "connecting")
    check("no ring without a tunnel", bob.rang, [])

    alice.call(2)
    pump([alice, bob], rounds=6)

    check("rings once it is proven", len(bob.rang), 1)
    check("ringing", bob.active_call.state, "ringing")
    check("the right peer", bob.rang[0].peer.id, 1)
    check("the other side waits", alice.active_call.state, "online")
    check("no audio before an answer", alice.active_call.tx_frames, 0)


def test_call_needs_take_calls():
    net = fresh()
    alice = Caller(config(1))
    bob = Caller(config(2))

    call = alice.call(2)

    pump([alice, bob], rounds=62)

    check("nothing rang", bob.rang, [])
    check("no call here", bob.active_call, None)
    check("the caller gives up", alice.ended, ["timeout"])
    check("and says so", call.reason, "timeout")
    check("and it is gone", call.state, "ended")
    check("free again", alice.active_call, None)


def test_call_talks_both_ways():
    net, alice, bob, ours, theirs = talking()

    check("caller talks", ours.state, "talking")
    check("callee talks", theirs.state, "talking")
    check("told once", (len(alice.started), len(bob.started)), (1, 1))
    check("nobody ended", alice.ended + bob.ended, [])

    del alice.heard[:], bob.heard[:]

    ours.send_audio(tone(5) + tone(6))
    theirs.send_audio(tone(9))

    pump([alice, bob], rounds=6, ms=20)

    ok("bob heard alice", tone(5) in bob.heard and tone(6) in bob.heard)
    ok("in order", bob.heard.index(tone(5)) < bob.heard.index(tone(6)))
    ok("alice heard bob", tone(9) in alice.heard)
    check("silence after that", bob.heard[-1], tone(0))
    check("nothing left", ours.queued, 0)
    check("status knows", alice.status()["call"],
        {"peer": "02", "state": "talking", "link": "relay"})


def test_call_is_a_steady_stream():
    net, alice, bob, ours, theirs = talking()

    before = ours.tx_frames
    del net.frames[:]

    pump([alice, bob], rounds=50, ms=20)

    check("fifty frames a second", ours.tx_frames - before, 50)
    check("all the same size",
        set(size for _, size in net.frames), {160})

    before = ours.tx_frames
    pygospel.time.sleep(10)
    pump([alice, bob], rounds=1, ms=20)

    ok("no burst after a stall", ours.tx_frames - before <= 2)


def test_call_partial_audio_waits():
    net, alice, bob, ours, theirs = talking()

    half = tone(3)[:960]

    ours.send_audio(half)
    check("half a frame is 10 ms", ours.queued, 10)

    pump([alice, bob], rounds=3, ms=20)
    check("it waits for the rest", ours.queued, 10)

    ours.send_audio(half)
    pump([alice, bob], rounds=3, ms=20)

    check("sent once it is whole", ours.queued, 0)
    ok("arrived", tone(3) in bob.heard)

    ours.send_audio(tone(4) * 10)
    ours.clear_audio()
    check("cleared", ours.queued, 0)

    boom("half a sample", lambda: ours.send_audio(b"abc"))
    boom("too much", lambda: ours.send_audio(
        bytes(pygospel.CALL_AUDIO_MAX + 2)))


def test_call_loss_is_repaired():
    net, alice, bob, ours, theirs = talking()

    theirs._rx_seq = 100
    del bob.heard[:]

    def frame(seq, value):
        theirs.on_frame(pygospel.voice_pack(pygospel.VOICE_AUDIO, seq,
            bytes([value])))

    frame(101, 1)
    check("in order plays", bob.heard, [tone(1)])

    del bob.heard[:]
    frame(103, 2)
    check("one lost comes from fec", bob.heard,
        [tone(fakeopus.FEC), tone(2)])

    del bob.heard[:]
    frame(106, 3)
    check("more lost are concealed", bob.heard,
        [tone(fakeopus.PLC), tone(fakeopus.FEC), tone(3)])

    del bob.heard[:]
    frame(106, 4)
    frame(105, 4)
    check("late and duplicate are dropped", bob.heard, [])

    theirs._rx_seq = 0xffff
    frame(1, 5)
    ok("the sequence wraps", tone(5) in bob.heard)

    del bob.heard[:]
    frame(0, 6)
    check("0 has no sequence", bob.heard, [tone(6)])

    del bob.heard[:]
    theirs.on_frame(b"\x03" + bytes(2000))
    theirs.on_frame(bytes(10))
    theirs.on_frame(pygospel.voice_pack(4))
    check("other frames mean nothing", bob.heard, [])
    check("still up", theirs.ending, None)


def test_call_decline():
    net, alice, bob = callers()
    call = alice.call(2)

    pump([alice, bob], rounds=6)
    bob.rang[0].hangup()
    check("hanging up", bob.rang[0].state, "hanging up")

    pump([alice, bob], rounds=10, ms=200)

    check("the caller is told", alice.ended, ["remote"])
    check("we did it", bob.ended, ["local"])
    check("nothing talked", (len(alice.started), len(bob.started)), (0, 0))
    check("both free", (alice.active_call, bob.active_call), (None, None))
    boom("no answering now", lambda: bob.rang[0].answer())


def test_call_ended_does_not_ring_again():
    net, alice, bob = callers()
    alice.call(2)

    pump([alice, bob], rounds=6)
    bob.rang[0].hangup()

    # The dialer takes a moment to stop, this is what is still signaled.
    bob._callers[1][1] = pygospel._ms()
    pump([bob], rounds=10, ms=200)

    check("declined", bob.ended, ["local"])

    bob._call_push(bytes([0, bob._callers[1][0]]) + bytes(253))
    pump([bob], rounds=2, ms=200)

    check("same call stays down", (bob.active_call, len(bob.rang)), (None, 1))

    pump([alice, bob], rounds=12)

    alice.call(2)
    pump([alice, bob], rounds=6)

    check("a new call rings", len(bob.rang), 2)


def test_call_hangup_while_talking():
    net, alice, bob, ours, theirs = talking()

    theirs.hangup()
    pump([alice, bob], rounds=2, ms=20)

    check("the other side hears it", alice.ended, ["remote"])
    check("still lingering here", bob.ended, [])

    pump([bob], rounds=6, ms=200)

    check("then done", bob.ended, ["local"])
    check("reason is kept", ours.reason, "remote")

    ours.hangup()
    ours.send_audio(tone(1))
    boom("no answer", lambda: ours.answer())


def test_call_goes_quiet():
    net, alice, bob, ours, theirs = talking()

    pump([alice], rounds=19)
    check("still up at 19", alice.ended, [])

    pump([alice], rounds=2)
    check("gone at 20", alice.ended, ["timeout"])


def test_call_caller_never_shows_up():
    fresh()
    bob = Caller(config(2), take_calls=True)

    for _ in range(11):
        bob._call_push(bytes([0, 7]) + bytes(253))
        pump([bob], rounds=1)

    check("not proven in time", bob.ended, ["timeout"])
    check("never rang", bob.rang, [])


def test_call_caller_gives_up():
    fresh()
    bob = Caller(config(2), take_calls=True)

    bob._call_push(bytes([0, 7]) + bytes(253))
    pump([bob], rounds=11)

    check("gone with its signal", bob.ended, ["remote"])
    check("never rang", bob.rang, [])


def test_call_one_at_a_time():
    net = fresh()
    alice = Caller(config(1))
    bob = Caller(config(2), take_calls=True, answer=True)
    carol = Caller(config(3))

    alice.call(2)
    boom("a second one", lambda: alice.call(3))
    boom("ourselves", lambda: carol.call(3))
    boom("a bad id", lambda: carol.call(0))

    pump([alice, bob, carol], rounds=6)

    carol.call(2)
    pump([alice, bob, carol], rounds=6)

    check("the first stays", bob.active_call.peer.id, 1)
    check("one ring", len(bob.rang), 1)

    alice.active_call.hangup()
    pump([alice, bob, carol], rounds=6)

    check("then the next", bob.active_call.peer.id, 3)


def test_call_close_hangs_up():
    net, alice, bob, ours, theirs = talking()

    alice.close()
    check("ended by us", alice.ended, ["local"])

    pump([bob], rounds=2, ms=20)
    check("the peer was told", bob.ended, ["remote"])


def test_call_does_not_disturb_chats():
    net, alice, bob, ours, theirs = talking()

    got = []
    bob.on_message = lambda msg: got.append(msg.text)

    alice.send(2, "still here")
    pump([alice, bob], rounds=8)

    check("chat still works", got, ["still here"])
    ok("a chat tunnel of its own",
        alice.peer(2).signal_tunnel is not None and
        alice.peer(2).signal_tunnel.ctx.flock_src != ours.ctx.flock_src)


def test_call_lists_who_is_online():
    fresh()

    one = Caller(config(1), discoverable=True)
    two = Caller(config(2), discoverable=True)
    far = Caller(config(3, group="0009"), discoverable=True)

    pump([one, two, far], rounds=8)

    check("the same group", one.callable, {2})
    check("and the other way", two.callable, {1})
    check("another group", far.callable, set())
    check("status has it", one.status()["callable"], ["02"])


def test_call_needs_opus():
    fresh()

    pygospel.opuslib = None
    sys.modules["opuslib"] = None

    try:
        boom("take_calls says so at the start",
            lambda: pygospel.GospelChat(config(2), take_calls=True))

        chat = pygospel.GospelChat(config(1))

        boom("so does call", lambda: chat.call(2))
        check("nothing was left behind", chat.active_call, None)
    finally:
        pygospel.opuslib = fakeopus
        del sys.modules["opuslib"]


def test_call_opus_is_set_up_like_vigil():
    fresh()

    codec = pygospel._Codec()
    state = codec.encoder.encoder_state

    check("voip", codec.encoder.application, fakeopus.APPLICATION_VOIP)
    check("rate", (codec.encoder.fs, codec.decoder.fs), (48000, 48000))
    check("mono", (codec.encoder.channels, codec.decoder.channels), (1, 1))
    check("controls", state.ctl, {"bitrate": fakeopus.AUTO,
        "signal": fakeopus.SIGNAL_VOICE, "fec": 1, "loss": 5})
    check("room in a frame", state.max_bytes, None)

    codec.encode(tone(1))
    check("the limit is a frame", state.max_bytes, 154)


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
