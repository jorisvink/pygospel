"""
Text-only gospel chats, driven by the libkyrka python module.

Gospel is the protocol Vigil uses for calls and messages over sanctum
tunnels. This module speaks the text part of it and nothing else: it sends
and receives text messages in one-to-one chats and in group chats, it is
compatible with Vigil and with confession, and it claims no other ability.

The fastest way in:

    import pygospel

    class Chats(pygospel.GospelChat):
        def on_message(self, msg):
            print("%02x: %s" % (msg.peer, msg.text))

    chat = Chats(config="gospel.conf", nickname="alice")
    chat.send(0x02, "blessed sanctum")
    chat.run()

Hooks can be plain functions instead of subclass methods:

    chat = pygospel.GospelChat(config="gospel.conf", nickname="alice")
    chat.on_message = lambda msg: print(msg.text)
    chat.join(0x0001)
    chat.run()

A chat needs a gospel configuration: a KEK, a cathedral secret, a COSK, a
flock and a device id (the `spi`, 01 to fe). All of it is read from a Vigil
configuration file, so a `mode gospel` config written for Vigil or Covenant
works as-is.

Nothing here opens a network interface or stores anything. A received
message is handed to the application and acknowledged as soon as it
arrives, unless auto_ack is off, in which case the application calls ack()
on the Message once it has kept it.
"""

import os
import re
import sys
import time
import socket
import struct
import hashlib
import selectors


class KyrkaMissing(ImportError):
    """
    libkyrka is not on the path.

    pygospel is only the top half of a tunnel: libkyrka is the C extension
    that does the key exchange and the crypto, and it has to be built
    before any of this works.
    """

    def __init__(self):
        super().__init__(
            "the libkyrka python module is required\n"
            "\n"
            "  libkyrka is a C extension, so build vigil-python, which\n"
            "  fetches and patches it for you:\n"
            "\n"
            "      $ git clone https://github.com/jorisvink/vigil-python\n"
            "      $ cd vigil-python && make\n"
            "      $ sudo make install\n"
            "\n"
            "  or point PYTHONPATH at a libkyrka you already built:\n"
            "\n"
            "      $ PYTHONPATH=/path/to/libkyrka/obj/python python3 ...\n")


try:
    import libkyrka
except ImportError:
    raise KyrkaMissing()

__all__ = [
    "GospelChat", "GospelConfig", "GospelError", "KyrkaMissing", "Message",
    "Peer", "Quote",
    "frame_pack", "frame_unpack", "heartbeat_pack", "heartbeat_unpack",
]

#
# The wire. Every chat frame goes out at full size so that a text message,
# an acknowledgement and a heartbeat look the same to whoever watches.
#
#   struct vigil_chat_frame {
#     uint64 id; uint16 len; uint8 type; uint8 data[512];
#   }
#

MESSAGE_MAX = 512
FRAME_SIZE  = 8 + 2 + 1 + MESSAGE_MAX
FRAME_ID    = struct.Struct("!QHB")

# What a frame carries. An id of 0 is reserved, an id of all ones is a
# heartbeat.
TYPE_TEXT      = 1
TYPE_ACK       = 2
TYPE_HEARTBEAT = 3
TYPE_DATA_ACK  = 4
TYPE_REPLY     = 5

# The features we tell our peers about. We take no data and no avatars, so
# those bits stay off; the name bit follows whether we have a name that
# verifies, see GospelChat._own_name.
FEATURE_DATA   = 0x01
FEATURE_REPLY  = 0x02
FEATURE_NAME   = 0x04
FEATURE_AVATAR = 0x08
FEATURES       = 0x00

NICK_MAX       = 16
INSTANCE_MAX   = 64
SIGNATURE_LEN  = 64
HEARTBEAT_ID   = b"\xff" * 8
HEARTBEAT_UID  = 8
HEARTBEAT_BODY = struct.Struct("!16sBB")
HEARTBEAT_MIN  = HEARTBEAT_UID + HEARTBEAT_BODY.size

# A reply carries who wrote the message it answers and that message's hash.
REPLY_HEAD = struct.Struct("!BQ")
REPLY_MAX  = MESSAGE_MAX - REPLY_HEAD.size

# Flock domains. One-to-one chats and group chats live in different ones, so
# the same two devices never share a key between a direct chat and a group.
CALL_DOMAIN  = 0x0a
FLOCK_DOMAIN = 0x0b
GROUP_DOMAIN = 0x0c

# The group the signaling liturgy runs in, every other one is a chat.
SIGNAL_GROUP    = 0x0000

PEERS_PER_FLOCK = 255
CATHEDRALS_MAX  = 32
SEEN_MAX        = 64

# How far our own id is shifted to make a tunnel id, a 1:1 tunnel between
# device 01 and device 02 is numbered 0x0102 from one side and 0x0201 back.
TUNNEL_SHIFT = 8

# The hops a commixtion tunnel takes, if our configuration asks for one.
COMMIXTION_HOPS = 2

# What Vigil will accept as a nickname.
NICK_RE = re.compile(rb"^[A-Za-z0-9]{2,16}$")

#
# Signed names. Covenant signs the `instance` of a device with the ed25519
# key of the flock and puts the signature in the configuration, together
# with the public half so every device can check what the others claim.
# The cathedrals never see a name, it only travels inside a tunnel.
#
#   m = "COVENANT.NAME" || flock || kek || len(name) || name
#
# The flock is 64-bit big endian, the KEK id and the length are one byte
# each. The KEK id is taken from the tunnel the name arrived on and the
# flock from our own configuration, so a device cannot spend the signed
# name of another device.
#

NAME_LABEL      = b"COVENANT.NAME"
NAME_MSG        = struct.Struct("!QBB")
INSTANCE_RE     = re.compile(rb"^[A-Za-z0-9._\- ]{1,63}$")
NAME_PUBKEY_LEN = 32

# Timings in milliseconds, the same Vigil uses.
HEARTBEAT_INTERVAL     = 12 * 1000
RESEND_INTERVAL        = 5 * 1000
TUNNEL_IDLE            = 60 * 1000
RX_TIMEOUT             = 30 * 1000
MANAGE_INTERVAL        = 1 * 1000
NOTIFY_INTERVAL        = 5 * 1000
PRESENT_EXPIRE         = 9 * 1000
CATHEDRAL_TIMEOUT_INIT = 20 * 1000
CATHEDRAL_TIMEOUT      = 45 * 1000
LITURGY_NOTIFY         = 3 * 1000
P2P_VERIFY             = 30 * 1000
P2P_RETRY              = 30 * 1000
P2P_RETRY_MAX          = 1800 * 1000
P2P_SILENT             = 1 * 1000
HOLEPUNCH              = 10 * 1000
HOLEPUNCH_INTERVAL     = 1 * 1000
STORE_WAIT             = 10 * 1000

# Where a Vigil configuration ends up if it does not say.
MTU_DEFAULT = 1408


class GospelError(Exception):
    """Something in a gospel configuration, or on a tunnel, went wrong."""


def _ms():
    return int(time.monotonic() * 1000)


def _fnv1a(data):
    """The 64 bit FNV-1a hash Vigil uses to spot a message it already had."""

    hash = 0xcbf29ce484222325

    for byte in data:
        hash ^= byte
        hash = (hash * 0x100000001b3) & 0xffffffffffffffff

    return hash


def _quote_hash(data):
    """The first eight bytes of the SHA-256 of a media type, LF and text."""

    return struct.unpack("!Q", hashlib.sha256(b"\n" + data).digest()[:8])[0]


def _normalize(data):
    """Replace what Vigil replaces: control codes and broken UTF-8."""

    out = bytearray(data)
    idx = 0
    end = len(out)

    while idx < end:
        head = out[idx]

        if head < 0x80:
            if head < 0x20 and head not in (0x0a, 0x09):
                out[idx] = 0x3f
            idx += 1
            continue

        if (head & 0xe0) == 0xc0:
            need, minimum, cp = 1, 0x80, head & 0x1f
        elif (head & 0xf0) == 0xe0:
            need, minimum, cp = 2, 0x800, head & 0x0f
        elif (head & 0xf8) == 0xf0:
            need, minimum, cp = 3, 0x10000, head & 0x07
        else:
            out[idx] = 0x3f
            idx += 1
            continue

        if need >= end - idx:
            out[idx] = 0x3f
            idx += 1
            continue

        off = 1

        while off <= need:
            if (out[idx + off] & 0xc0) != 0x80:
                break
            cp = (cp << 6) | (out[idx + off] & 0x3f)
            off += 1

        if off <= need or cp < minimum or cp > 0x10ffff or \
            0xd800 <= cp <= 0xdfff:
            out[idx] = 0x3f
            idx += 1
            continue

        idx += need + 1

    return bytes(out)


def _mentions(text, names):
    """An @ of one of the given names, which is how Vigil spells a mention."""

    for name in names:
        if re.search(r"(?<![A-Za-z0-9_\-])@%s(?![A-Za-z0-9_\-])"
            % re.escape(name), text, re.IGNORECASE):
            return True

    return False


def name_message(flock, kek, name):
    """
    The bytes Covenant signed for a name: the label, the flock, the KEK id
    of the device and the name itself, prefixed by its length.
    """

    if isinstance(name, str):
        name = name.encode("utf-8")

    return NAME_LABEL + NAME_MSG.pack(flock, kek, len(name)) + name


def _instance_valid(name):
    """Whether this is a name Vigil would put in a configuration."""

    return bool(INSTANCE_RE.match(name))


class _Verifier:
    """
    The ed25519 check, from whichever library is around.

    A signed name is only worth showing once it verifies, so if nothing is
    installed to check one we say so and show no names at all rather than
    showing a name somebody picked for themselves.
    """

    fn = None
    why = "not looked yet"
    looked = False

    @classmethod
    def get(cls):
        if cls.looked:
            return cls.fn

        cls.looked = True

        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import \
                Ed25519PublicKey

            def verify(signature, message, pubkey):
                try:
                    Ed25519PublicKey.from_public_bytes(pubkey).verify(
                        signature, message)
                    return True
                except Exception:
                    return False

            cls.fn = verify
            cls.why = ""
        except ImportError:
            try:
                import nacl.signing

                def verify(signature, message, pubkey):
                    try:
                        nacl.signing.VerifyKey(pubkey).verify(message,
                            signature)
                        return True
                    except Exception:
                        return False

                cls.fn = verify
                cls.why = ""
            except ImportError:
                cls.fn = None
                cls.why = ("no ed25519 verifier, install 'cryptography' or "
                    "'PyNaCl' to check signed names")

        return cls.fn

    @classmethod
    def reason(cls):
        cls.get()

        return cls.why


def frame_pack(msgid, ftype, payload=b"", length=None):
    """
    Build one full-size chat frame, the way Vigil puts it on the wire.

    msgid is an integer or eight raw bytes, length defaults to the payload
    size and the payload is padded out to a whole frame.
    """

    if length is None:
        length = len(payload)

    if isinstance(msgid, int):
        msgid = struct.pack("!Q", msgid)

    if len(msgid) != 8:
        raise GospelError("a frame id is eight bytes")

    if len(payload) > MESSAGE_MAX:
        raise GospelError("a frame carries at most %d bytes" % MESSAGE_MAX)

    return (msgid + struct.pack("!HB", length & 0xffff, ftype) +
        payload.ljust(MESSAGE_MAX, b"\x00"))


def frame_unpack(buf):
    """Split a chat frame into its id, length, type and payload."""

    if len(buf) != FRAME_SIZE:
        raise GospelError("a chat frame is %d bytes" % FRAME_SIZE)

    msgid, length, ftype = FRAME_ID.unpack_from(buf, 0)

    return msgid, length, ftype, buf[FRAME_ID.size:]


def heartbeat_pack(uid, nickname, typing, features=FEATURES,
    instance=b"", signature=b""):
    """Build a heartbeat frame, which carries no length of its own."""

    nick = nickname.encode("utf-8")[:NICK_MAX].ljust(NICK_MAX, b"\x00")
    body = uid + HEARTBEAT_BODY.pack(nick, 1 if typing else 0, features)
    body += instance.ljust(INSTANCE_MAX, b"\x00")
    body += signature.ljust(SIGNATURE_LEN, b"\x00")

    return frame_pack(HEARTBEAT_ID, TYPE_HEARTBEAT, body, 0)


def heartbeat_unpack(payload):
    """Take what a heartbeat says, as far as the payload reaches."""

    uid = payload[:HEARTBEAT_UID]
    nick, typing, features = HEARTBEAT_BODY.unpack_from(payload, HEARTBEAT_UID)
    instance = signature = b""

    if len(payload) >= HEARTBEAT_MIN + INSTANCE_MAX + SIGNATURE_LEN:
        off = HEARTBEAT_MIN
        instance = payload[off:off + INSTANCE_MAX]
        signature = payload[off + INSTANCE_MAX:off + INSTANCE_MAX +
            SIGNATURE_LEN]

    return uid, nick, bool(typing), features, instance, signature


class GospelConfig:
    """
    A parsed Vigil configuration, keeping what a text chat needs.

    Use load() with a path or parse() with the contents of a file. Options
    this module has no use for (routes, dns, tunnel addresses) are read and
    ignored.
    """

    def __init__(self):
        self.mode = "gospel"
        self.instance = ""
        self.spi = 0
        self.kek = b""
        self.mtu = MTU_DEFAULT
        self.shroud = False
        self.p2p = True
        self.commixtion = False
        self.group = 0x0001
        self.cathedral_host = ""
        self.cathedral_port = 0
        self.cathedral_nat_port = 0
        self.cathedral_id = 0
        self.cathedral_flock = 0
        self.cathedral_secret = b""
        self.cathedral_cosk = b""
        self.name_pubkey = b""
        self.name_signature = b""

    @classmethod
    def load(cls, path):
        """Read a Vigil configuration from a file."""

        with open(path, "rb") as f:
            return cls.parse(f.read())

    @classmethod
    def parse(cls, data):
        """Parse a Vigil configuration held in a string or in bytes."""

        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8", "replace")

        cfg = cls()
        version = 0

        for line in data.split("\n"):
            line = line.strip().rstrip("\r").strip()

            if line == "" or line.startswith("#"):
                continue

            if not re.match(r"\S+\s+\S", line):
                raise GospelError("option is missing a value: %s" % line)

            key, value = line.split(None, 1)
            value = value.strip()

            if version == 0:
                if key != "vigil":
                    raise GospelError("not a vigil configuration")
                if not value.isdigit() or int(value) == 0:
                    raise GospelError("not a vigil configuration")
                if int(value) != 1:
                    raise GospelError("unsupported configuration version %s"
                        % value)
                version = 1
                continue

            cfg._option(key, value)

        if version == 0:
            raise GospelError("not a vigil configuration")

        cfg._validate()

        return cfg

    def _option(self, key, value):
        try:
            if key == "mode":
                if value not in ("gospel", "voice"):
                    raise GospelError("mode is '%s', this is not gospel",
                        value)
                self.mode = "gospel"
            elif key == "instance":
                self.instance = value
            elif key == "spi":
                self.spi = int(value, 16)
            elif key == "kek":
                self.kek = self._hex(key, value, 32)
            elif key == "mtu":
                self.mtu = MTU_DEFAULT if value == "auto" else int(value)
            elif key == "shroud":
                self.shroud = self._yesno(key, value)
            elif key == "p2p":
                self.p2p = self._yesno(key, value)
            elif key == "cathedral_commixtion":
                self.commixtion = self._yesno(key, value)
            elif key == "liturgy_group":
                self.group = int(value, 16)
            elif key == "cathedral":
                host, _, port = value.rpartition(":")
                self.cathedral_host = host
                self.cathedral_port = int(port)
            elif key == "cathedral_nat_port":
                self.cathedral_nat_port = int(value)
            elif key == "cathedral_id":
                self.cathedral_id = int(value, 16)
            elif key == "cathedral_flock":
                self.cathedral_flock = int(value, 16)
            elif key == "cathedral_secret":
                self.cathedral_secret = self._hex(key, value, 32)
            elif key == "cathedral_cosk":
                self.cathedral_cosk = self._hex(key, value, 64)
            elif key == "name_pubkey":
                self.name_pubkey = bytes.fromhex(value)
            elif key == "name_signature":
                self.name_signature = bytes.fromhex(value)
            # Anything else is not ours to fail on.
        except ValueError:
            raise GospelError("%s has an invalid value '%s'" % (key, value))

    @staticmethod
    def _hex(key, value, length):
        if len(value) != length * 2:
            raise GospelError("%s must be %d hex characters"
                % (key, length * 2))

        try:
            return bytes.fromhex(value)
        except ValueError:
            raise GospelError("%s is not valid hex" % key)

    @staticmethod
    def _yesno(key, value):
        if value == "yes":
            return True
        if value == "no":
            return False

        raise GospelError("invalid %s option '%s', accepted: yes|no"
            % (key, value))

    def _validate(self):
        if not 1 <= self.spi < PEERS_PER_FLOCK:
            raise GospelError("spi must be between 01 and %02x"
                % (PEERS_PER_FLOCK - 1))
        if len(self.kek) != 32:
            raise GospelError("no kek was configured")
        if len(self.cathedral_secret) != 32:
            raise GospelError("no cathedral_secret was configured")
        if len(self.cathedral_cosk) != 64:
            raise GospelError("no cathedral_cosk was configured")
        if self.cathedral_flock == 0:
            raise GospelError("no cathedral_flock was configured")
        if self.cathedral_id == 0:
            raise GospelError("no cathedral_id was configured")
        if not self.cathedral_host or self.cathedral_port == 0:
            raise GospelError("no cathedral was configured")
        if self.mtu < 576 or self.mtu > 1500:
            raise GospelError("mtu %d is out of range" % self.mtu)
        if not 0 <= self.group < 0xffff:
            raise GospelError("liturgy_group must be below ffff")


class Quote:
    """Points at the message a reply answers: who wrote it, and its hash."""

    __slots__ = ("author", "hash")

    def __init__(self, author=0, hash=0):
        self.author = author
        self.hash = hash

    def __bool__(self):
        return self.hash != 0

    def __repr__(self):
        return "Quote(author=%02x, hash=%016x)" % (self.author, self.hash)


class Message:
    """
    One text message, either one that arrived or one we are holding.

    Attributes:
        text        the message, as a str
        peer        the device id it came from or goes to, 01 .. fe
        group       the group it belongs to, or None for a one-to-one chat
        ref         a token of your choosing that identifies the message
                    back to you in on_sent and on_delivered
        id          the wire id, only meaningful on the tunnel it rode
        hash        the digest of the text, what a reply points at
        quote       the Quote this message answers, or None; its author is a
                device id, ours if we wrote the message being answered
        author      who wrote it: our own spi, or the peer that sent it
        ts          when we saw it, in seconds since the epoch
        delivered   whether the peer has stored it
        mentions_me whether the text names us with an @
    """

    __slots__ = ("text", "peer", "group", "ref", "id", "hash", "quote",
        "author", "ts", "delivered", "_chat", "_ack")

    def __init__(self, text="", peer=0, group=None, ref=0, quote=None,
        author=None):
        self.text = text
        self.peer = peer
        self.group = group
        self.ref = ref
        self.id = 0
        self.hash = _quote_hash(text.encode("utf-8")) if text else 0
        self.quote = quote
        self.author = peer if author is None else author
        self.ts = time.time()
        self.delivered = False
        self._chat = None
        self._ack = None

    @property
    def where(self):
        """A short label of where this lives: '02', or '02@0007' in a group."""

        if self.group is None:
            return "%02x" % self.peer

        return "%02x@%04x" % (self.peer, self.group)

    @property
    def mentions_me(self):
        if self._chat is None:
            return False

        return _mentions(self.text, self._chat._mention_names())

    def ack(self):
        """
        Acknowledge this message once you have kept it. Only does anything
        when the chat runs with auto_ack turned off.
        """

        entry = self._ack

        if entry is None:
            return

        tunnel, slot = entry

        if slot.token == 0:
            return

        slot.token = 0
        slot.msg = None
        tunnel.send_ack(slot.msgid)

    def __repr__(self):
        return "Message(%s: %r)" % (self.where, self.text)


class Peer:
    """
    A device in our flock, as far as this chat knows it.

    Attributes:
        id        its device id
        nick      the nickname it last sent, chosen by it and checked by no
                  one, or "" if it sent nothing worth showing
        name      the name Covenant signed for it, once that verifies;
                  empty if it has no name or if we could not check one
        present   it told the cathedral it is around
        member    it is in the group we joined
        online    a live tunnel reaches it right now
        typing    it said it is typing
        opened    you marked its chat open, so we keep signaling it
        link      how we reach it: none, connecting, relay or p2p
    """

    def __init__(self, chat, id):
        self.id = id
        self.nick = ""
        self.name = ""
        self.signed = False
        self._dirty = False
        self.present = False
        self.member = False
        self.typing = False
        self.typing_out = False
        self.opened = False
        self.pushed = 0
        self.active = 0
        self.outbox = []
        self.signal_tunnel = None
        self.group_tunnel = None
        self._chat = chat

    @property
    def best_name(self):
        """What a user should call this peer: its name, its nick, its id."""

        return self.name or self.nick or "%02x" % self.id

    @property
    def online(self):
        return self._chat.tunnel_online(self.id)

    @property
    def link(self):
        return self._chat.link(self.id)

    def set_nick(self, nick):
        """Record a nickname, to be reported once the heartbeat is read."""

        if self.nick == nick:
            return False

        self.nick = nick
        self._dirty = True

        return True

    def set_name(self, name, signed):
        """
        Take the name a peer now goes under. signed tells us whether the
        flock key actually backed it up, which is the only kind of name a
        user should be shown as more than a nickname.
        """

        if self.name == name and self.signed == signed:
            return False

        self.name = name
        self.signed = signed
        self._dirty = True

        return True

    def report_name(self):
        """Tell the application once what a peer is now called."""

        if not self._dirty:
            return

        self._dirty = False
        self._chat._emit("on_name", self)

    def open(self):
        self._chat.open(self.id)

    def send(self, text, ref=None):
        return self._chat.send(self.id, text, ref)

    def __repr__(self):
        return "Peer(%02x, %r)" % (self.id, self.best_name)


class _Seen:
    """
    One slot of the small table that drops duplicated messages.

    A token of zero means the message is acked and we are done with it,
    anything else means we handed it to the application and are waiting for
    it to say it kept the message.
    """

    __slots__ = ("msgid", "hash", "token", "age", "msg")

    def __init__(self):
        self.msgid = 0
        self.hash = 0
        self.token = 0
        self.age = 0
        self.msg = None


class _Held:
    """A message we hold for a peer until its copy has been acked."""

    __slots__ = ("ref", "text", "quote", "msgid", "age", "pending", "sent",
        "group", "tally", "delivered")

    def __init__(self, ref, text, quote=None):
        self.ref = ref
        self.text = text
        self.quote = quote
        self.msgid = 0
        self.age = 0
        self.pending = False
        self.sent = False
        self.group = None
        self.tally = None
        self.delivered = False


class _Tally:
    """How many members of a group still owe us an ack for one message."""

    __slots__ = ("ref", "group", "pending", "acked", "report")

    def __init__(self, ref, group):
        self.ref = ref
        self.group = group
        self.pending = 0
        self.acked = 0
        self.report = False


class _Cathedral:
    """One cathedral a single context uses, and how well it is treating us."""

    __slots__ = ("addr", "last", "alive", "timeout")

    def __init__(self, addr, timeout=CATHEDRAL_TIMEOUT_INIT):
        self.addr = addr
        self.last = _ms()
        self.alive = False
        self.timeout = timeout

    def __repr__(self):
        return "Cathedral(%s:%d)" % self.addr


class _Liturgy:
    """
    A libkyrka context that only ever talks to a cathedral.

    The signaling liturgy tells us which peers are around and lets us say
    which peers we want; a group liturgy tells us who is in a group; the
    presence liturgy puts us in the list Vigil shows for a liturgy_group.
    """

    def __init__(self, chat, group, remembrance, domain=GROUP_DOMAIN):
        self.chat = chat
        self.group = group
        self.domain = domain
        self.remembrance = remembrance
        self.sock = chat._new_socket()
        self.ctx = chat._new_context(self, kek=False)
        self.cathedral = _Cathedral(chat.cathedrals[0],
            CATHEDRAL_TIMEOUT_INIT if remembrance else CATHEDRAL_TIMEOUT)
        self.addr = self.cathedral.addr
        self.source = None
        self.alive = False
        self.last = 0
        self.updated = 0
        self.notify = 0
        self.want = None
        self.closed = False

        chat._register(self.sock, self)

    def configure(self):
        self.ctx.cathedral_configure(**self.chat._cathedral_cfg(
            self, self.chat.spi, self.group,
            self.chat.flock | self.domain, self.remembrance, 0))

    def signal(self, want):
        """
        Tell the cathedral which peers we are interested in, want being a
        KYRKA_PEERS_PER_FLOCK byte array, or None for everyone.
        """

        now = _ms()
        want = None if want is None else bytes(want)

        # Inside our own notify window a liturgy is only worth sending
        # again if what we want has actually changed.
        if self.notify != 0 and now < self.notify and self.want == want:
            return

        self.updated = now
        self.notify = now + LITURGY_NOTIFY
        self.want = want

        self.chat._liturgy(self.ctx, want)

    def poll_all(self):
        """Ask the cathedral about the whole flock, this is how groups list."""

        self.signal(None)

    def check(self, now):
        """Drop to another cathedral if this one stopped answering us."""

        if (now - self.cathedral.last) <= self.cathedral.timeout:
            return

        was = self.cathedral.alive

        self.cathedral.alive = False
        self.cathedral.timeout = CATHEDRAL_TIMEOUT
        self.last = 0

        if was and self is self.chat.signaling:
            for target in self.chat.peers.values():
                target.present = False
            self.chat._emit("on_offline")

        self.chat._cathedral_pick(self.cathedral)

    def close(self):
        if self.closed:
            return

        self.closed = True
        self.chat._forget(self)


class _Tunnel:
    """
    A chat tunnel to one peer, either a one-to-one one or one that is in
    the group we joined. Both carry exactly the same frames; a group tunnel
    keeps its unacked messages to itself instead of in the peer outbox,
    because a peer that leaves the group no longer owes us anything.
    """

    def __init__(self, chat, peer, group):
        self.chat = chat
        self.peer = peer
        self.group = group
        self.is_group = group != SIGNAL_GROUP

        self.sock = chat._new_socket()
        self.ctx = chat._new_context(self, kek=True)

        self.local_uid = os.urandom(8)
        self.remote_uid = None
        self.next_msgid = 1

        self.cathedral = _Cathedral(chat.cathedrals[0])
        self.addr = self.cathedral.addr
        self.source = None
        self.online = 0
        self.last_rx = 0
        self.greeted = False
        self.heard = False
        self.features = 0
        self.typing_sent = False

        self.next_heartbeat = 0
        self.next_notify = 0
        self.next_manage = 0

        self.sendq = []
        self.waitq = []
        self.seen = [_Seen() for _ in range(SEEN_MAX)]
        self.seen_next = 0

        self.holepunch = 0
        self.p2p_addr = None
        self.p2p_failed = None
        self.p2p_rx = 0
        self.p2p_verify = 0
        self.p2p_retry = 0
        self.p2p_backoff = 0
        self.closed = False

        chat._register(self.sock, self)

        if self.is_group:
            peer.group_tunnel = self
        else:
            peer.signal_tunnel = self

        # Whatever we were already holding for this peer gets an id here.
        for held in self._held():
            if not held.pending:
                self._queue(held)

    def configure(self):
        flock = self.chat.flock | (GROUP_DOMAIN if self.is_group
            else FLOCK_DOMAIN)

        # Commixtion rides the shroud, so it needs the shroud turned on.
        hops = COMMIXTION_HOPS if self.chat.commixtion and self.chat.shroud \
            else 0

        self.ctx.cathedral_configure(**self.chat._cathedral_cfg(
            self, (self.chat.spi << TUNNEL_SHIFT) | self.peer.id,
            0, flock, False, hops))

    @property
    def alive(self):
        if not self.online:
            return False

        return (_ms() - self.last_rx) < RX_TIMEOUT

    @property
    def relay(self):
        return self.addr == self.cathedral.addr

    def _held(self):
        return self.waitq if self.is_group else self.peer.outbox

    #
    # Getting things out.
    #

    def _queue(self, held):
        """Give a held message an id on this tunnel and put it in line."""

        if held.pending:
            return

        held.msgid = self.next_msgid
        self.next_msgid += 1

        # Replies wait until we know if our peer takes them, see _resend().
        if held.quote is not None and held.quote.hash != 0 and not self.heard:
            held.age = 0
            return

        held.pending = True
        held.age = _ms()
        self.sendq.append(held)

    def send_frame(self, data):
        shroud = libkyrka.KYRKA_PACKET_SHROUD_CATHEDRAL if self.relay \
            else libkyrka.KYRKA_PACKET_SHROUD_PEER

        return self.chat._heaven_input(self.ctx, data, shroud)

    def send_heartbeat(self):
        self.greeted = True
        self.typing_sent = self.chat.group_typing if self.is_group \
            else self.peer.typing_out
        self.next_heartbeat = _ms() + (HOLEPUNCH_INTERVAL
            if _ms() < self.holepunch else HEARTBEAT_INTERVAL)

        return self.send_frame(heartbeat_pack(self.local_uid,
            self.chat.nickname, self.typing_sent, self.chat.features,
            self.chat.name_instance, self.chat.name_proof))

    def send_ack(self, msgid):
        return self.send_frame(frame_pack(msgid, TYPE_ACK))

    def transmit(self, held):
        """Put one held message on the wire, as plain text or as a reply."""

        data = held.text.encode("utf-8")
        held.age = _ms()

        if held.quote is not None and held.quote.hash != 0 and \
            (self.features & FEATURE_REPLY):
            author = self.chat.spi if held.quote.author == 0 \
                else held.quote.author
            body = REPLY_HEAD.pack(author, held.quote.hash) + data
            self.send_frame(frame_pack(held.msgid, TYPE_REPLY, body))
        else:
            self.send_frame(frame_pack(held.msgid, TYPE_TEXT, data))

    def manage(self):
        """Keys, the cathedral, heartbeats, and everything waiting to go."""

        now = _ms()

        self._cathedral_check(now)

        if self.p2p_verify != 0 and now >= self.p2p_verify:
            self._p2p_fail()

        if now >= self.next_notify:
            self.next_notify = now + NOTIFY_INTERVAL
            self.chat._notify(self.ctx)

            if self.chat.p2p:
                self.chat._nat_detection(self.ctx)

        if now >= self.next_manage:
            self.next_manage = now + MANAGE_INTERVAL
            self.chat._manage(self.ctx)

        if not self.online:
            return

        self._resend(now)

        typed = self.chat.group_typing if self.is_group \
            else self.peer.typing_out

        if not self.greeted or typed != self.typing_sent or \
            now >= self.next_heartbeat:
            self.send_heartbeat()

        while self.sendq:
            held = self.sendq.pop(0)
            held.pending = False
            self.transmit(held)

            if not held.sent:
                held.sent = True

                if not self.is_group:
                    self.chat._emit("on_sent", held.ref,
                        self.chat._message_of(held, self.peer.id, None))

    def _resend(self, now):
        """Queue again whatever we sent and never heard anything about."""

        for held in list(self._held()):
            if held.pending or held.msgid == 0:
                continue
            if now - held.age < RESEND_INTERVAL:
                continue
            if held.quote is not None and not self.heard:
                continue

            held.pending = True
            self.sendq.append(held)

    #
    # Getting things in.
    #

    def on_frame(self, buf):
        self.last_rx = _ms()

        if len(buf) != FRAME_SIZE:
            return

        msgid, length, ftype, payload = frame_unpack(buf)

        if msgid == 0 or length > MESSAGE_MAX:
            return

        if ftype == TYPE_TEXT:
            self._on_text(msgid, length, payload, None)
        elif ftype == TYPE_REPLY:
            self._on_reply(msgid, length, payload)
        elif ftype == TYPE_ACK:
            self._on_ack(msgid)
        elif ftype == TYPE_HEARTBEAT:
            self._on_heartbeat(payload)
        # Frames carrying data are not ours, we never asked to receive any.

    def _on_heartbeat(self, payload):
        uid, nick, typing, features, instance, signature = \
            heartbeat_unpack(payload)
        name = nick.split(b"\x00", 1)[0]

        if not NICK_RE.match(name):
            name = b""

        # Both halves of what a peer is called are settled before we say
        # anything, so a heartbeat is at most one on_name.
        self.peer.set_nick(name.decode("ascii", "replace"))
        self.chat._check_name(self.peer, features, instance, signature)
        self.peer.report_name()

        self.heard = True
        self.features = features

        if self.peer.typing != typing:
            self.peer.typing = typing
            self.chat._emit("on_typing", self.peer, typing)

        if self.remote_uid == uid:
            return

        if self.remote_uid is not None:
            # Our peer restarted, so its ids start over and what we hold
            # against them no longer means anything.
            for slot in self.seen:
                slot.msgid = 0
                slot.hash = 0
                slot.token = 0
                slot.msg = None

        self.remote_uid = uid

    def _on_text(self, msgid, length, payload, quote):
        if length == 0:
            self.send_ack(msgid)
            return

        raw = payload[:length]

        # Duplicates are spotted on the bytes that arrived, like Vigil
        # does; the hash a reply points at is that of the text it quotes.
        slot = self._seen_find(msgid, _fnv1a(raw))

        if slot is not None:
            if slot.token == 0:
                # We have it and we acked it, say so again.
                self.send_ack(msgid)
                return

            if _ms() - slot.age < STORE_WAIT:
                # The application still has it in hand, do not spoil it.
                return

            # The application never took it back, so it gets it again.
            msg = slot.msg
            slot.token = self.chat._next_token()
            slot.age = _ms()

            self.peer.active = _ms()
            self.chat._emit("on_message", msg)

            return

        data = _normalize(raw)

        msg = Message(data.decode("utf-8", "replace"), peer=self.peer.id,
            group=self.group if self.is_group else None, quote=quote,
            author=self.peer.id)
        msg.id = msgid
        msg.hash = _quote_hash(data)
        msg._chat = self.chat

        slot = self._seen_slot(msgid, _fnv1a(raw), msg)
        slot.token = self.chat._next_token()

        if self.chat.auto_ack:
            slot.token = 0
            self.send_ack(msgid)
        else:
            msg._ack = (self, slot)

        self.peer.active = _ms()
        self.chat.rx += length
        self.chat._emit("on_message", msg)

    def _on_reply(self, msgid, length, payload):
        if length < REPLY_HEAD.size:
            return

        author, hash = REPLY_HEAD.unpack_from(payload, 0)

        # On the wire an author of zero is us; the API keeps real ids.
        if author == 0 or author == self.chat.spi:
            author = self.chat.spi

        quote = Quote(author, hash) if hash != 0 else None

        self._on_text(msgid, length - REPLY_HEAD.size,
            payload[REPLY_HEAD.size:], quote)

    def _on_ack(self, msgid):
        for held in list(self._held()):
            if held.msgid != msgid:
                continue

            if held in self.sendq:
                self.sendq.remove(held)

            self._held().remove(held)
            held.delivered = True

            if self.is_group:
                self.chat._tally_update(held.tally, 1)
            else:
                self.chat._emit("on_delivered", held.ref,
                    self.chat._message_of(held, self.peer.id, None))

            return

    def _seen_find(self, msgid, hash):
        for slot in self.seen:
            if slot.msgid != 0 and slot.msgid == msgid and slot.hash == hash:
                return slot

        return None

    def _seen_slot(self, msgid, hash, msg):
        slot = self.seen[self.seen_next % SEEN_MAX]
        self.seen_next += 1
        slot.msgid = msgid
        slot.hash = hash
        slot.token = 0
        slot.msg = msg
        slot.age = _ms()

        return slot

    #
    # Working out where to send things.
    #

    def _cathedral_check(self, now):
        if (now - self.cathedral.last) <= self.cathedral.timeout:
            return

        if self.cathedral.alive:
            self.cathedral.alive = False

        self.chat._cathedral_pick(self.cathedral)
        self.next_notify = 0

        if not self.relay:
            self._p2p_off()

    def _follow_p2p(self):
        """Traffic that did not come from the cathedral is a path to use."""

        if not self.chat.p2p or self.source is None:
            return

        if self.source == self.addr:
            self.p2p_backoff = 0
            self.p2p_rx = _ms()
            self.p2p_verify = 0
            self.p2p_retry = 0
            return

        if not self.relay or self.source != self.p2p_addr:
            return

        self.addr = self.source
        self.p2p_rx = _ms()
        self.p2p_retry = 0
        self.p2p_verify = _ms() + P2P_VERIFY
        self.holepunch = _ms() + HOLEPUNCH
        self.next_heartbeat = _ms()

        self.chat._p2p_active(self.ctx, 1)

    def _abandon_p2p(self):
        if self.relay or self.p2p_verify != 0:
            return

        if (_ms() - self.p2p_rx) < P2P_SILENT:
            return

        self._p2p_fail()

    def _p2p_fail(self):
        if self.p2p_backoff == 0:
            self.p2p_backoff = P2P_RETRY
        else:
            self.p2p_backoff = min(self.p2p_backoff * 2, P2P_RETRY_MAX)

        self.p2p_verify = 0
        self.p2p_retry = _ms() + self.p2p_backoff
        self.p2p_failed = self.addr
        self.addr = self.cathedral.addr

        self.chat._p2p_active(self.ctx, 0)

    def close(self):
        if self.closed:
            return

        self.closed = True

        for held in self.sendq:
            held.pending = False

        if self.is_group:
            for held in self.waitq:
                self.chat._tally_update(held.tally, 0)
            self.waitq = []
            self.peer.group_tunnel = None
        else:
            # Unacked messages stay with the peer and get new ids on the
            # next tunnel, they are not this tunnel's to throw away.
            for held in self.peer.outbox:
                held.pending = False
                held.msgid = 0
            self.peer.signal_tunnel = None

        self.chat._forget(self)


class GospelChat:
    """
    A text-only gospel chat.

    Arguments:
        config      a GospelConfig, the path to a Vigil configuration file,
                    or the contents of one as a string
        nickname    the 2..16 alphanumeric name peers see you under, or ""
                    to send no nickname
        group       join this group on start up, 0 (the default) to join
                    none. This is not the `liturgy_group` from the config,
                    that one is what calls signal in.
        auto_ack    acknowledge messages as they arrive, on by default; with
                    False you call ack() on a Message once you stored it
        p2p         try direct paths before relaying, overriding the config
        cathedrals  [(host, port), ...] to use instead of the configured one
        signal_all  signal every device in the flock rather than only the
                    ones you hold messages for or have a chat open with
        features    the feature bits to announce; by default none, plus the
                    signed-name bit if our own name verifies against the
                    flock key in the configuration
        discoverable
                    show up as a peer on Vigil devices using the same
                    `liturgy_group` as our configuration. Vigil also shows
                    those peers as callable, and we answer no call.

    Hooks, either overridden in a subclass or assigned as plain functions:

        on_message(msg)                 a message arrived
        on_sent(ref, msg)               one of our messages left for a peer
        on_delivered(ref, msg)          a peer stored one of our messages
        on_group_delivered(ref, group, acked, pending)
                                        members stored (or lost) our message
        on_peer(peer)                   a peer became reachable
        on_peer_gone(peer)              we can no longer reach a peer
        on_name(peer)                   a peer changed its nickname
        on_typing(peer, typing)         a peer started or stopped typing
        on_joined(peer)                 a peer joined our group
        on_left(peer)                   a peer left our group
        on_offline()                    the cathedral stopped answering
    """

    def __init__(self, config, nickname="", group=None, auto_ack=True,
        p2p=None, cathedrals=None, signal_all=False, features=None,
        discoverable=False):
        if isinstance(config, GospelConfig):
            cfg = config
        elif isinstance(config, (bytes, bytearray)) or "\n" in str(config):
            cfg = GospelConfig.parse(config)
        else:
            cfg = GospelConfig.load(config)

        if cfg.mode != "gospel":
            raise GospelError("mode is '%s', this needs a gospel config"
                % cfg.mode)

        self.config = cfg
        self.spi = cfg.spi
        self.flock = cfg.cathedral_flock
        self.cathedral_id = cfg.cathedral_id
        self.shroud = cfg.shroud
        self.commixtion = cfg.commixtion
        self.p2p = cfg.p2p if p2p is None else bool(p2p)
        self.auto_ack = auto_ack
        self.signal_all = signal_all
        self.instance = cfg.instance
        self.mtu = cfg.mtu

        # A signed name is only announced when we actually have one that
        # checks out against the flock key, anything else would be us
        # claiming something a peer cannot confirm.
        self.name_proof = b""
        self.name_instance = b""
        self.named = False
        self.name_pubkey = cfg.name_pubkey
        self.name_status = self._own_name(cfg)

        if features is None:
            features = FEATURE_NAME if self.named else FEATURES

        self.features = features

        self.nickname = self._check_nick(nickname)
        self.group_typing = False

        self.peers = {}
        self.cathedrals = []
        self.signaling = None
        self.discovery = None
        self.presence = None
        self.group = 0
        self.tallies = []
        self.rx = 0
        self.tx = 0

        self._refs = 0
        self._tokens = 0
        self._switch = 0
        self._selector = selectors.DefaultSelector()
        self._owners = {}
        self._running = True
        self._closing = False

        self._resolve(cathedrals or [(cfg.cathedral_host, cfg.cathedral_port)])

        self.nat_port = cfg.cathedral_nat_port or self.cathedrals[0][1] + 1

        self.signaling = _Liturgy(self, SIGNAL_GROUP, True)
        self.signaling.configure()

        # The discovery liturgy of calls is one up from liturgy_group.
        if discoverable:
            self.presence = _Liturgy(self, cfg.group + 1, False, CALL_DOMAIN)
            self.presence.configure()
            self.presence.poll_all()

        # liturgy_group in a Vigil configuration is what calls signal in,
        # chats start in no group.
        if group is None:
            group = 0

        if group:
            self.join(group)

    #
    # What a program calls.
    #

    def send(self, peer, text, ref=None):
        """
        Send a text message to the device with the given id, 01 .. fe.

        It waits in that peer's outbox until the peer has stored it, so it
        still gets through if the peer was asleep. Returns a Message.
        """

        self._check_peer(peer)
        text = self._check_text(text)

        held = self._hold(peer, text, None, ref)
        tunnel = self.peer(peer).signal_tunnel

        if tunnel is not None:
            tunnel._queue(held)

        self._signal()

        return self._message_of(held, peer, None)

    def reply(self, peer, text, to, ref=None):
        """
        Reply to a message in a one-to-one chat. `to` is the Message being
        answered, or a Quote for one of our own.
        """

        self._check_peer(peer)
        text = self._check_text(text, REPLY_MAX)

        held = self._hold(peer, text, self._quote_of(to), ref)
        tunnel = self.peer(peer).signal_tunnel

        if tunnel is not None:
            tunnel._queue(held)

        self._signal()

        return self._message_of(held, peer, None)

    def send_group(self, text, ref=None, quote=None):
        """
        Send a text message to every member of our group that is online at
        this moment. on_group_delivered follows as they ack it.
        """

        if self.group == 0:
            raise GospelError("we are not in a group")

        text = self._check_text(text,
            MESSAGE_MAX if quote is None else REPLY_MAX)

        tally = _Tally(self._new_ref(ref), self.group)

        for id in sorted(self.peers):
            target = self.peers[id]

            if id == self.spi or not target.member:
                continue

            tunnel = target.group_tunnel

            if tunnel is None or not tunnel.alive:
                continue

            held = _Held(tally.ref, text, quote)
            held.tally = tally
            held.group = self.group
            tally.pending += 1

            tunnel.waitq.append(held)
            tunnel._queue(held)

        if tally.pending == 0:
            raise GospelError("no member of the group is online")

        self.tallies.append(tally)

        msg = Message(text, peer=self.spi, group=self.group, ref=tally.ref,
            quote=quote, author=self.spi)
        msg._chat = self

        return msg

    def reply_group(self, text, to, ref=None):
        """Send a reply to the group we joined."""

        return self.send_group(text, ref, self._quote_of(to))

    def join(self, group):
        """
        Join the group with the given 16 bit id, leaving the one we are in.
        Joining 0 only leaves.
        """

        group &= 0xffff

        if group == self.group:
            return

        self.leave()

        if group == SIGNAL_GROUP:
            return

        self.group = group
        self.discovery = _Liturgy(self, group, False)
        self.discovery.configure()
        self.discovery.poll_all()

    def leave(self):
        """Leave our group, dropping whatever waited for its members."""

        if self.group == 0:
            return

        for target in self.peers.values():
            if target.group_tunnel is not None:
                target.group_tunnel.close()
            target.member = False

        if self.discovery is not None:
            self.discovery.close()
            self.discovery = None

        self.group = 0

    def open(self, peer):
        """Say a chat is open, so we keep signaling its peer while it is."""

        self._check_peer(peer)
        self.peer(peer).opened = True
        self._signal()

    def close_chat(self, peer):
        """The mirror of open(); a closed chat still delivers its outbox."""

        self._check_peer(peer)
        self.peer(peer).opened = False
        self._signal()

    def typing(self, on, peer=None):
        """Tell a peer, or the whole group, that we are typing."""

        if peer is None:
            self.group_typing = bool(on)
            return

        self._check_peer(peer)
        self.peer(peer).typing_out = bool(on)

    def outbox(self, peer):
        """The messages we still hold for a peer, oldest first."""

        self._check_peer(peer)

        return [self._message_of(h, peer, None)
            for h in self.peer(peer).outbox]

    def forget(self, peer):
        """Drop everything we still hold for a peer."""

        self._check_peer(peer)
        target = self.peer(peer)
        tunnel = target.signal_tunnel

        for held in list(target.outbox):
            if tunnel is not None and held in tunnel.sendq:
                tunnel.sendq.remove(held)
            target.outbox.remove(held)

    def peer(self, id):
        """The Peer for a device id, creating it if we have not seen it."""

        self._check_peer(id)

        if id not in self.peers:
            self.peers[id] = Peer(self, id)

        return self.peers[id]

    def roster(self):
        """Peers worth showing: around, in our group, or with history."""

        return [p for p in sorted(self.peers.values(), key=lambda x: x.id)
            if p.present or p.member or p.active or p.outbox]

    def members(self):
        """The peers in the group we joined."""

        return [p for p in self.roster() if p.member]

    def tunnel_online(self, peer):
        """True if a live tunnel reaches this peer right now."""

        target = self.peer(peer)

        for tunnel in (target.signal_tunnel, target.group_tunnel):
            if tunnel is not None and tunnel.alive:
                return True

        return False

    def link(self, peer):
        """How we reach a peer: 'none', 'connecting', 'relay' or 'p2p'."""

        target = self.peer(peer)
        tunnel = target.signal_tunnel or target.group_tunnel

        if tunnel is None:
            return "none"
        if not tunnel.alive:
            return "connecting"

        return "relay" if tunnel.relay else "p2p"

    def status(self):
        """A snapshot for a status line: who we can reach and how."""

        return {
            "id": "%02x" % self.spi,
            "nickname": self.nickname,
            "group": "%04x" % self.group if self.group else None,
            "cathedral": self.cathedrals[0] if self.cathedrals else None,
            "liturgy": self.signaling is not None and self.signaling.alive,
            "liturgy_group": "%04x" % self.config.group if self.presence
                else None,
            "present": sum(1 for p in self.peers.values() if p.present),
            "members": sum(1 for p in self.peers.values() if p.member),
            "held": sum(len(p.outbox) for p in self.peers.values()),
            "rx": self.rx,
            "tx": self.tx,
        }

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    #
    # The loop.
    #

    def tick(self):
        """One timer pass: cathedrals, keys, tunnels, heartbeats, sending."""

        now = _ms()

        for lit in (self.signaling, self.discovery, self.presence):
            if lit is None:
                continue

            lit.check(now)

        if self.presence is not None:
            self.presence.poll_all()

        for id in range(1, PEERS_PER_FLOCK):
            if id == self.spi:
                continue

            target = self.peers.get(id)

            if target is None:
                continue

            if target.present and now - target.pushed >= PRESENT_EXPIRE:
                target.present = False

            self._tunnel_check(target, SIGNAL_GROUP)

            if self.group != 0:
                self._tunnel_check(target, self.group)

        if self.discovery is not None:
            self._group_notify(now)

        self._tallies_report()
        self._signal()

    def add_reader(self, fd, callback):
        """
        Watch a file descriptor with the chat's own loop, so a program can
        mix stdin, a pipe or a timerfd into run() without threads.
        """

        self._selector.register(fd, selectors.EVENT_READ,
            lambda source: callback(source))

    def remove_reader(self, fd):
        try:
            self._selector.unregister(fd)
        except (KeyError, ValueError):
            pass

    def poll(self, timeout=1.0):
        """Wait up to timeout for traffic, then run one tick()."""

        try:
            for key, _ in self._selector.select(timeout):
                key.data(key.fileobj)
        except OSError:
            pass
        except ValueError:
            pass

        self.tick()

    def run(self):
        """Block serving the chat until stop() or close(), Ctrl-C included."""

        try:
            while self._running:
                self.poll(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self.close()

    def stop(self):
        """Ask a running run() to return."""

        self._running = False

    @property
    def running(self):
        """Whether the chat is live, which stops at stop() and close()."""

        return self._running

    def close(self):
        """Tear down every tunnel, liturgy, context and socket we opened."""

        if self._closing:
            return

        self._closing = True
        self._running = False

        for target in list(self.peers.values()):
            for tunnel in (target.signal_tunnel, target.group_tunnel):
                if tunnel is not None:
                    tunnel.close()

        self.leave()

        if self.signaling is not None:
            self.signaling.close()
            self.signaling = None

        if self.presence is not None:
            self.presence.close()
            self.presence = None

        for owner in list(self._owners.values()):
            owner.close()

        self._owners.clear()

        try:
            self._selector.close()
        except Exception:
            pass

    #
    # Hooks, as no-ops a subclass may override or an instance may replace.
    #

    def on_message(self, msg):
        pass

    def on_sent(self, ref, msg):
        pass

    def on_delivered(self, ref, msg):
        pass

    def on_group_delivered(self, ref, group, acked, pending):
        pass

    def on_peer(self, peer):
        pass

    def on_peer_gone(self, peer):
        pass

    def on_name(self, peer):
        pass

    def on_typing(self, peer, typing):
        pass

    def on_joined(self, peer):
        pass

    def on_left(self, peer):
        pass

    def on_offline(self):
        pass

    #
    # Plumbing: contexts, sockets, and the callbacks they call us on.
    #

    def _new_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", 0))

        return sock

    def _new_context(self, owner, kek):
        ctx = libkyrka.alloc()

        self._owners[id(ctx)] = owner

        if self.shroud:
            ctx.shroud_enable()

        ctx.mtu_size(self.mtu)
        ctx.event_callback(self._event, None)
        ctx.heaven_callback(self._heaven, owner)
        ctx.purgatory_callback(self._purgatory, owner)

        if kek:
            ctx.device_kek_load(self.config.kek)

        ctx.cathedral_secret_load(self.config.cathedral_secret)
        ctx.cathedral_cosk_load(self.config.cathedral_cosk)

        return ctx

    def _cathedral_cfg(self, owner, tunnel, group, flock, remembrance, hops):
        chat = self

        def cathedral_send(ctx, data, magic, udata):
            chat._cathedral_send(owner, magic, data)

        if tunnel == 0:
            raise GospelError("a tunnel id of 0 is reserved")

        return {
            "udata": None,
            "send": cathedral_send,
            "flock_src": flock,
            "flock_dst": flock,
            "group": group,
            "tunnel": tunnel,
            "identity": self.cathedral_id,
            "hidden": False,
            "remembrance": remembrance,
            "hops": hops,
        }

    def _cathedral_send(self, owner, magic, data):
        addr = owner.cathedral.addr

        if magic == libkyrka.KYRKA_CATHEDRAL_NAT_MAGIC:
            addr = (addr[0], self.nat_port)

        self._send(owner.sock, data, addr)

    def _register(self, sock, owner):
        self._selector.register(sock, selectors.EVENT_READ,
            lambda fd, owner=owner: self._read(fd, owner))

    def _read(self, sock, owner):
        ctx = owner.ctx

        if ctx is None:
            return

        for _ in range(64):
            try:
                data, from_addr = sock.recvfrom(1500)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return

            if not data:
                continue

            self.rx += len(data)
            owner.source = from_addr

            if isinstance(owner, _Tunnel):
                if from_addr == owner.cathedral.addr:
                    owner.cathedral.last = _ms()
                    owner.cathedral.alive = True
                    owner.cathedral.timeout = CATHEDRAL_TIMEOUT

                    if self.p2p:
                        owner._abandon_p2p()
                elif self.p2p:
                    owner._follow_p2p()
            else:
                owner.cathedral.last = _ms()
                owner.cathedral.alive = True
                owner.cathedral.timeout = CATHEDRAL_TIMEOUT
                owner.alive = True

            shroud = libkyrka.KYRKA_PACKET_SHROUD_PEER \
                if isinstance(owner, _Tunnel) and \
                from_addr != owner.cathedral.addr else \
                libkyrka.KYRKA_PACKET_SHROUD_CATHEDRAL

            self._purgatory_input(ctx, data, shroud)

    def _next_token(self):
        self._tokens += 1

        return self._tokens

    def _heaven(self, ctx, data, seq, owner):
        if isinstance(owner, _Tunnel):
            owner.on_frame(data)

    def _purgatory(self, ctx, data, seq, owner):
        self._send(owner.sock, data, owner.addr)

    def _event(self, ctx, event, info, udata):
        owner = self._owners.get(id(ctx))

        if owner is None:
            return

        if isinstance(owner, _Liturgy):
            self._liturgy_event(owner, event, info)
        else:
            self._tunnel_event(owner, event, info)

    def _liturgy_event(self, lit, event, info):
        if event == libkyrka.KYRKA_EVENT_LITURGY_RECEIVED:
            peers = info.get("peers")

            if peers is None or len(peers) != PEERS_PER_FLOCK:
                return

            lit.alive = True
            lit.last = _ms()
            lit.cathedral.alive = True
            lit.cathedral.last = _ms()
            lit.cathedral.timeout = CATHEDRAL_TIMEOUT

            if lit is self.signaling:
                self._present(peers)
            elif lit is self.discovery:
                self._members(peers)
        elif event == libkyrka.KYRKA_EVENT_REMEMBRANCE_RECEIVED:
            self._more_cathedrals(info.get("cathedrals") or [])

    def _tunnel_event(self, tun, event, info):
        if event == libkyrka.KYRKA_EVENT_KEYS_INFO:
            if info.get("tx", 0) == 0 or tun.online:
                return

            tun.online = _ms()
            tun.last_rx = _ms()
            tun.next_notify = 0
            self._emit("on_peer", tun.peer)
        elif event == libkyrka.KYRKA_EVENT_KEYS_ERASED:
            tun.p2p_verify = 0
            tun.addr = tun.cathedral.addr
            self._p2p_active(tun.ctx, 0)
        elif event == libkyrka.KYRKA_EVENT_PEER_DISCOVERY:
            self._peer_discovery(tun, info)

    def _peer_discovery(self, tun, info):
        """The cathedral told us where our peer is, maybe we can reach it."""

        if not self.p2p:
            return

        ip = info.get("ip", 0)
        port = info.get("port", 0)

        if ip == 0 or port == 0:
            return

        addr = self._addr(ip, port)

        hairpin = bool(info.get("flags", 0) &
            libkyrka.KYRKA_INFO_FLAG_SAME_EXTERNAL_IPV4)

        tun.p2p_addr = tun.cathedral.addr if hairpin or addr[0] \
            .startswith("127.") else addr

        if _ms() < tun.p2p_retry and tun.p2p_addr == tun.p2p_failed:
            tun.p2p_addr = tun.cathedral.addr

        if tun.p2p_addr == tun.addr:
            return

        tun.addr = tun.p2p_addr

        if tun.addr == tun.cathedral.addr:
            tun.p2p_verify = 0
            self._p2p_active(tun.ctx, 0)
            return

        tun.p2p_rx = _ms()
        tun.p2p_verify = _ms() + P2P_VERIFY
        tun.holepunch = _ms() + HOLEPUNCH
        tun.next_heartbeat = _ms()
        self._p2p_active(tun.ctx, 1)

    #
    # Peers, presence and membership.
    #

    def _present(self, peers):
        """The cathedral says which peers signal us, so we can reach them."""

        for id in range(1, PEERS_PER_FLOCK):
            if id == self.spi:
                continue

            # The cathedral pushes each peer that signals us on its own.
            if not peers[id]:
                continue

            target = self.peer(id)
            target.pushed = _ms()

            if not target.present:
                target.present = True
                self._emit("on_peer", target)

    def _members(self, peers):
        """Whoever the liturgy of the group we joined lists."""

        for id in range(1, PEERS_PER_FLOCK):
            if id == self.spi:
                continue

            state = bool(peers[id])
            target = self.peers.get(id)

            if target is None:
                if not state:
                    continue
                target = self.peer(id)

            if state == target.member:
                continue

            target.member = state

            if state:
                self._emit("on_joined", target)
            else:
                if target.group_tunnel is not None:
                    target.group_tunnel.close()
                self._emit("on_left", target)

    #
    # Outboxes, tunnels and signaling.
    #

    def _hold(self, peer, text, quote, ref):
        """Add a message to a peer outbox, unless we already hold that ref."""

        target = self.peer(peer)
        ref = self._new_ref(ref)

        for held in target.outbox:
            if held.ref == ref:
                return held

        held = _Held(ref, text, quote)
        target.outbox.append(held)
        target.active = _ms()

        return held

    def _new_ref(self, ref):
        if ref is not None:
            return ref

        self._refs += 1

        return self._refs

    def _signal_want(self, target):
        """Do we want the cathedral to tell this peer we are around?"""

        if target.id == self.spi:
            return 0

        if self.signal_all:
            return 1

        if target.outbox:
            return 1

        if target.opened:
            return 1

        if target.active and _ms() - target.active < TUNNEL_IDLE:
            return 1

        if target.present and not self.tunnel_online(target.id):
            return 1

        return 0

    def _signal(self):
        if self.signaling is None:
            return

        if self.signal_all:
            want = bytearray(b"\x01" * PEERS_PER_FLOCK)
            want[self.spi] = 0
        else:
            want = bytearray(PEERS_PER_FLOCK)

            for id, target in self.peers.items():
                want[id] = self._signal_want(target)

        self.signaling.signal(want)

    def _tunnel_check(self, target, group):
        """Have a tunnel with this peer if we should, and look after it."""

        is_group = group != SIGNAL_GROUP
        current = target.group_tunnel if is_group else target.signal_tunnel
        want = target.member if is_group else target.present

        if is_group:
            if self.discovery is not None and self.discovery.last == 0 and \
                current is not None and not current.alive:
                want = False
        elif current is not None and current.alive and \
            self._signal_want(target):
            want = True

        if not want:
            if current is not None:
                current.close()
            return

        if current is None:
            current = _Tunnel(self, target, group)
            current.configure()

        current.manage()

    def _group_notify(self, now):
        self.discovery.poll_all()

    def _cathedral_pick(self, cathedral):
        """Move a context to another cathedral, from the ones we know of."""

        if len(self.cathedrals) > 1:
            self._switch = (self._switch + 1) % len(self.cathedrals)
            cathedral.addr = self.cathedrals[self._switch]

        cathedral.last = _ms()
        cathedral.alive = False
        cathedral.timeout = CATHEDRAL_TIMEOUT_INIT

    def _more_cathedrals(self, entries):
        """Cathedrals a remembrance told us about, on top of our own."""

        for ip, port in entries:
            if port == 0 or len(self.cathedrals) >= CATHEDRALS_MAX:
                continue

            addr = self._addr(ip, port)

            if addr not in self.cathedrals:
                self.cathedrals.append(addr)

    #
    # Tallies.
    #

    def _tally_update(self, tally, acked):
        if tally is None or tally.pending == 0:
            return

        tally.pending -= 1
        tally.report = True

        if acked:
            tally.acked += 1

    def _tallies_report(self):
        for tally in list(self.tallies):
            if not tally.report:
                continue

            tally.report = False

            if tally.pending == 0:
                self.tallies.remove(tally)

            self._emit("on_group_delivered", tally.ref, tally.group,
                tally.acked, tally.pending)

    #
    # Messages on their way to the caller.
    #

    def _message_of(self, held, peer, group):
        msg = Message(held.text, peer=peer,
            group=held.group if group is None else group, ref=held.ref,
            quote=held.quote, author=self.spi)
        msg.id = held.msgid
        msg.delivered = held.delivered
        msg.ts = time.time() - (_ms() - held.age) / 1000.0
        msg._chat = self

        return msg

    def _quote_of(self, to):
        if isinstance(to, Quote):
            return to

        if isinstance(to, Message):
            return Quote(0 if to.author == self.spi else to.peer, to.hash)

        raise GospelError("a reply needs the Message or Quote it answers")

    def _emit(self, hook, *args):
        getattr(self, hook)(*args)

    def _own_name(self, cfg):
        """
        Work out whether we have a signed name worth sending, and say why
        not when we do not. Only our own instance and our own KEK id go
        into this.
        """

        if not cfg.name_signature and not cfg.name_pubkey:
            return "no signed name in the configuration"

        if not cfg.name_signature or not cfg.name_pubkey:
            return "configuration has half of a signed name"

        if len(cfg.name_signature) != SIGNATURE_LEN or \
            len(cfg.name_pubkey) != NAME_PUBKEY_LEN:
            return "signed name is the wrong size"

        if not cfg.instance or not _instance_valid(cfg.instance.encode()):
            return "instance is not a name that can be signed"

        verify = _Verifier.get()

        if verify is None:
            return _Verifier.reason()

        try:
            good = verify(cfg.name_signature,
                name_message(cfg.cathedral_flock, cfg.spi, cfg.instance),
                cfg.name_pubkey)
        except Exception:
            return "the ed25519 check blew up"

        if not good:
            return "our signature does not verify against our instance"

        self.named = True
        self.name_instance = cfg.instance.encode("utf-8")
        self.name_proof = cfg.name_signature

        return "signed as %s" % cfg.instance

    def _check_name(self, peer, features, instance, signature):
        """
        Take the signed name off a heartbeat. What should have been signed
        is rebuilt from our own flock and the id of the peer that sent it,
        never from anything the peer tells us, so a device cannot spend the
        signed name of another device.
        """

        name = instance.split(b"\x00", 1)[0]

        if not (features & FEATURE_NAME):
            peer.set_name("", False)
            return

        if not name or len(signature) != SIGNATURE_LEN or \
            not _instance_valid(name) or not self.name_pubkey:
            peer.set_name("", False)
            return

        verify = _Verifier.get()

        if verify is None:
            return

        try:
            good = verify(signature,
                name_message(self.flock, peer.id, name), self.name_pubkey)
        except Exception:
            good = False

        if not good:
            peer.set_name("", False)
            return

        peer.set_name(name.decode("utf-8", "replace"), True)

    def _mention_names(self):
        names = [self.nickname, self.instance, "%02x" % self.spi]

        return set(n.lower() for n in names if n)

    def _forget(self, owner):
        ctx = getattr(owner, "ctx", None)

        if ctx is not None:
            self._owners.pop(id(ctx), None)

        try:
            self._selector.unregister(owner.sock)
        except (KeyError, ValueError):
            pass

        try:
            owner.sock.close()
        except OSError:
            pass

        owner.ctx = None

    def _resolve(self, hosts):
        for host, port in hosts:
            try:
                infos = socket.getaddrinfo(host, port, socket.AF_INET,
                    socket.SOCK_DGRAM)
            except socket.gaierror as e:
                raise GospelError("cathedral %s:%d does not resolve: %s"
                    % (host, port, e))

            for info in infos:
                addr = (info[4][0], info[4][1])

                if addr not in self.cathedrals:
                    self.cathedrals.append(addr)

        if not self.cathedrals:
            raise GospelError("no cathedral to talk to")

    @staticmethod
    def _addr(ip, port):
        """An address from libkyrka, which keeps it in network byte order."""

        return (socket.inet_ntoa(struct.pack("=I", ip & 0xffffffff)),
            socket.ntohs(port))

    #
    # libkyrka calls that are allowed to say no.
    #

    @staticmethod
    def _manage(ctx):
        try:
            ctx.key_manage()
        except RuntimeError:
            pass

    @staticmethod
    def _notify(ctx):
        try:
            ctx.cathedral_notify()
        except RuntimeError:
            pass

    @staticmethod
    def _liturgy(ctx, peers):
        try:
            if peers is None:
                ctx.cathedral_liturgy()
            else:
                ctx.cathedral_liturgy(bytes(peers))
        except RuntimeError:
            pass

    @staticmethod
    def _purgatory_input(ctx, data, shroud):
        try:
            ctx.purgatory_input(data, shroud)
        except RuntimeError:
            pass

    @staticmethod
    def _heaven_input(ctx, data, shroud):
        try:
            ctx.heaven_input(data, shroud)
            return True
        except RuntimeError:
            return False

    @staticmethod
    def _p2p_active(ctx, on):
        try:
            ctx.p2p_active(1 if on else 0)
        except RuntimeError:
            pass

    @staticmethod
    def _nat_detection(ctx):
        try:
            ctx.cathedral_nat_detection()
        except RuntimeError:
            pass

    def _send(self, sock, data, addr):
        try:
            sock.sendto(data, addr)
            self.tx += len(data)
            return True
        except OSError:
            return False

    #
    # Checks.
    #

    @staticmethod
    def _check_peer(peer):
        if not isinstance(peer, int) or not 1 <= peer < PEERS_PER_FLOCK:
            raise GospelError("a peer id is between 01 and %02x"
                % (PEERS_PER_FLOCK - 1))

    @staticmethod
    def _check_text(text, limit=MESSAGE_MAX):
        if isinstance(text, (bytes, bytearray)):
            raise GospelError("pass a str, bytes are not checked for UTF-8")

        if not isinstance(text, str):
            raise GospelError("a message is text")

        data = _normalize(text.encode("utf-8"))

        if not data:
            raise GospelError("a message is not empty")

        if len(data) > limit:
            raise GospelError("a message is at most %d bytes, this is %d"
                % (limit, len(data)))

        return data.decode("utf-8")

    @staticmethod
    def _check_nick(nickname):
        if not isinstance(nickname, str) or not nickname:
            return ""

        if not NICK_RE.match(nickname.encode("utf-8")):
            return ""

        return nickname
