"""
An in-process stand-in for the libkyrka python module and a cathedral.

It lets two GospelChats talk to each other, and to the wire format, with
no network and no sanctum daemon. What it models:

  - a flock of devices, each with a signaling liturgy, group liturgies
    and chat tunnels, all keyed on their tunnel id
  - a cathedral that answers a notify with key material once both ends of
    a tunnel are around, and a liturgy with the peers that are present or
    that are in a group
  - relay: frames leave a tunnel and arrive whole at the other one
  - a clock the test turns, so heartbeats and resends happen on demand

It encrypts nothing. The gospel protocol does not care what is inside a
tunnel, only what comes out of the other end of one.
"""

import struct

# What the real extension module exports.

KYRKA_EVENT_KEYS_INFO              = 1
KYRKA_EVENT_KEYS_ERASED            = 2
KYRKA_EVENT_EXCHANGE_INFO          = 3
KYRKA_EVENT_PEER_DISCOVERY         = 4
KYRKA_EVENT_AMBRY_RECEIVED         = 5
KYRKA_EVENT_LITURGY_RECEIVED       = 6
KYRKA_EVENT_REMEMBRANCE_RECEIVED   = 7
KYRKA_EVENT_LOGMSG                 = 8

KYRKA_CATHEDRAL_MAGIC              = 0x4b4154454452414c
KYRKA_CATHEDRAL_NAT_MAGIC          = 0x4b4154454452414e
KYRKA_CATHEDRAL_LITURGY_MAGIC      = 0x4b4154454452414c
KYRKA_PACKET_SHROUD_CATHEDRAL      = 1
KYRKA_PACKET_SHROUD_PEER           = 2
KYRKA_PEERS_PER_FLOCK              = 255
KYRKA_CATHEDRALS_MAX               = 32
KYRKA_INFO_FLAG_SAME_EXTERNAL_IPV4 = 2

# Our fake wire: a byte for what it is, then the sender's flock and tunnel.
PEER      = b"D"
OFFER     = b"O"
HEAD      = struct.Struct("!QIH")

CATHEDRAL = ("203.0.113.7", 4469)


class Error(RuntimeError):
    """Whatever the real module would raise a RuntimeError for."""


def _device(tunnel):
    """Which device a tunnel id belongs to, ours being the high byte."""

    return tunnel >> 8 if tunnel >= 256 else tunnel


def _reverse(tunnel):
    """The same tunnel as the other side numbers it."""

    return ((tunnel & 0xff) << 8) | (tunnel >> 8)


class Clock:
    """The time the fake network lives in, turned by the test."""

    def __init__(self):
        self.mono = 1000.0
        self.wall = 1700000000.0

    def monotonic(self):
        return self.mono

    def time(self):
        return self.wall

    def advance(self, ms):
        self.mono += ms / 1000.0
        self.wall += ms / 1000.0


class Socket:
    """A fake UDP socket: one queue in, the network out."""

    def __init__(self, net):
        self.net = net
        self.addr = ("127.0.0.1", 40000 + len(net.sockets))
        self.inbox = []
        self.closed = False
        net.sockets.add(self)

    def __repr__(self):
        return "Socket(%s:%d)" % self.addr

    def setblocking(self, on):
        pass

    def bind(self, addr):
        pass

    def sendto(self, data, addr):
        if self.closed:
            raise OSError("bad file descriptor")

        self.net.deliver(self, bytes(data), addr)

        return len(data)

    def recvfrom(self, size):
        if not self.inbox:
            raise BlockingIOError

        data, from_addr = self.inbox.pop(0)

        return data, from_addr

    def close(self):
        if self.closed:
            return

        self.closed = True
        self.net.sockets.discard(self)
        self.net.drop(self)

    def push(self, data, from_addr):
        self.inbox.append((bytes(data), from_addr))


class Context:
    """One libkyrka context, the two ends of which the network knows."""

    def __init__(self, net):
        self.net = net
        self.event_cb = None
        self.heaven_cb = None
        self.purgatory_cb = None
        self.heaven_udata = None
        self.purgatory_udata = None
        self.event_udata = None
        self.send_cb = None
        self.flock_src = 0
        self.flock_dst = 0
        self.tunnel = 0
        self.group = 0
        self.identity = 0
        self.hops = 0
        self.hidden = False
        self.remembrance = False
        self.mtu = 0
        self.shroud = False
        self.secret = None
        self.cosk = None
        self.kek = None
        self.wanted = None
        self.online = False
        self.p2p = 0
        self.notifies = 0
        self.net.contexts.add(self)

    # The calls pygospel makes.

    def event_callback(self, fn, udata):
        self.event_cb, self.event_udata = fn, udata

    def heaven_callback(self, fn, udata):
        self.heaven_cb, self.heaven_udata = fn, udata

    def purgatory_callback(self, fn, udata):
        self.purgatory_cb, self.purgatory_udata = fn, udata

    def mtu_size(self, mtu):
        if mtu < 576 or mtu > 1500:
            raise Error("mtu size incorrect")

        self.mtu = mtu

        return True

    def shroud_enable(self):
        self.shroud = True

        return True

    def secret_load(self, data):
        self.secret = bytes(data)

        return True

    def device_kek_load(self, data):
        self.kek = bytes(data)

        return True

    def cathedral_secret_load(self, data):
        self.secret = bytes(data)

        return True

    def cathedral_cosk_load(self, data):
        self.cosk = bytes(data)

        return True

    def p2p_active(self, on):
        self.p2p = on

        return True

    def key_manage(self):
        return True

    def cathedral_configure(self, **kwargs):
        for key in ("flock_src", "flock_dst", "tunnel", "group", "identity",
            "hops"):
            setattr(self, key, kwargs[key])

        self.remembrance = kwargs["remembrance"]
        self.hidden = kwargs["hidden"]
        self.send_cb = kwargs["send"]

        if self.tunnel == 0 or self.flock_src == 0 or self.identity == 0:
            raise Error("KYRKA_ERROR_PARAMETER")

        if self.secret is None or self.cosk is None:
            raise Error("KYRKA_ERROR_NO_SECRET")

        return True

    def cathedral_notify(self):
        if self.secret is None or self.cosk is None:
            raise Error("KYRKA_ERROR_NO_SECRET")

        self.notifies += 1
        self._offer()
        self.net.notified(self)

        return True

    def cathedral_nat_detection(self):
        if self.secret is None or self.cosk is None:
            raise Error("KYRKA_ERROR_NO_COSK")

        self.send_cb(self, OFFER + HEAD.pack(self.flock_src, self.tunnel,
            self.group) + b"nat", KYRKA_CATHEDRAL_NAT_MAGIC, None)

        return True

    def cathedral_liturgy(self, peers=None):
        if self.secret is None or self.cosk is None:
            raise Error("KYRKA_ERROR_NO_SECRET")

        if peers is not None:
            if len(peers) != KYRKA_PEERS_PER_FLOCK:
                raise Error("peers must be %d bytes" % KYRKA_PEERS_PER_FLOCK)

            self.wanted = bytes(peers)

        self._offer(b"liturgy")
        self.net.liturgy(self)

        return True

    def heaven_input(self, data, shroud):
        """Cleartext for our peer, which becomes a packet to be sent."""

        peer = self.net.partner(self)

        if peer is None or not peer.online:
            raise Error("KYRKA_ERROR_NO_TX_KEY")

        if self.purgatory_cb is None:
            raise Error("KYRKA_ERROR_NO_CALLBACK")

        self.purgatory_cb(self, PEER + HEAD.pack(self.flock_src, self.tunnel,
            self.group) + bytes(data), 0, self.purgatory_udata)

        return True

    def purgatory_input(self, data, shroud):
        """Ciphertext off the wire, into whatever it turns out to be."""

        kind = data[:1]
        flock, tunnel, group = HEAD.unpack_from(data, 1)

        if kind == OFFER:
            sender = self.net.by_tunnel(flock, tunnel, group)

            if sender is None:
                raise Error("KYRKA_ERROR_NO_RX_KEY")

            self.net.offer(sender, data[1 + HEAD.size:])
            return True

        if kind != PEER or not self.online:
            raise Error("KYRKA_ERROR_NO_RX_KEY")

        self.net.frames.append((self, len(data[1 + HEAD.size:])))
        self.heaven_cb(self, data[1 + HEAD.size:], 0, self.heaven_udata)

        return True

    def _offer(self, what=b"info"):
        head = HEAD.pack(self.flock_src, self.tunnel, self.group)

        self.send_cb(self, OFFER + head + what, KYRKA_CATHEDRAL_MAGIC, None)

    def fire(self, event, info):
        if self.event_cb is not None:
            self.event_cb(self, event, info, self.event_udata)

    # Helpers for the tests.

    @property
    def device(self):
        return _device(self.tunnel)

    @property
    def peer(self):
        return self.tunnel & 0xff

    def __repr__(self):
        return "Context(flock %016x, tunnel %04x, group %04x)" % (
            self.flock_src, self.tunnel, self.group)


class Network:
    """A cathedral and the flock around it."""

    def __init__(self, clock=None):
        self.clock = clock if clock is not None else Clock()
        self.sockets = set()
        self.contexts = set()
        self.endpoints = {}
        self.frames = []
        self.packets = []

    # Getting bytes from one socket to another.

    def deliver(self, sock, data, addr):
        """
        The fake wire. Every packet we get says who sent it, which is how
        a cathedral knows where to answer, and how we find the other end.
        """

        if len(data) < 1 + HEAD.size:
            return

        kind = data[:1]
        flock, tunnel, group = HEAD.unpack_from(data, 1)
        sender = self.by_tunnel(flock, tunnel, group)

        if sender is None:
            return

        # The cathedral remembers where it last heard from this one.
        self.endpoints[(flock, tunnel, group)] = sock

        if kind == OFFER:
            self.offer(sender, data[1 + HEAD.size:])
            return

        peer = self.partner(sender)

        if peer is None:
            return

        target = self.endpoints.get((peer.flock_src, peer.tunnel, peer.group))

        if target is None:
            return

        # Relayed traffic comes back off the cathedral, never from the peer.
        self.packets.append((sender, peer, len(data) - HEAD.size - 1))
        target.push(PEER + HEAD.pack(flock, tunnel, sender.group) +
            data[1 + HEAD.size:], CATHEDRAL)

    def drop(self, sock):
        """A socket went away, so the cathedral can no longer reach it."""

        for key, endpoint in list(self.endpoints.items()):
            if endpoint is sock:
                del self.endpoints[key]

        for ctx in list(self.contexts):
            key = (ctx.flock_src, ctx.tunnel, ctx.group)
            if self.endpoints.get(key) is None:
                self.contexts.discard(ctx)

    def by_tunnel(self, flock, tunnel, group=None):
        for ctx in self.contexts:
            if ctx.flock_src != flock or ctx.tunnel != tunnel:
                continue
            if group is not None and ctx.group != group:
                continue
            return ctx

        return None

    def partner(self, ctx):
        if ctx.tunnel < 256:
            return None

        return self.by_tunnel(ctx.flock_src, _reverse(ctx.tunnel), ctx.group)

    def offer(self, sender, what):
        """The cathedral got an offer from one of its devices."""

        self.keys(sender)

    def notified(self, ctx):
        peer = self.partner(ctx)

        if peer is not None:
            self.keys(peer)

    def keys(self, ctx):
        """Both ends of a tunnel are here, so it has keys now."""

        if ctx.tunnel < 256:
            return

        peer = self.partner(ctx)

        if peer is None:
            return

        for one in (ctx, peer):
            if one.online:
                continue

            one.online = True
            one.fire(KYRKA_EVENT_KEYS_INFO, {"tx": 1, "rx": 1})

    def liturgy(self, ctx):
        """Answer a liturgy: who is around, or who is in the group."""

        us = _device(ctx.tunnel)
        present = bytearray(KYRKA_PEERS_PER_FLOCK)

        for other in self.contexts:
            if other.flock_src != ctx.flock_src or other is ctx:
                continue
            if other.tunnel >= 256:
                continue
            if other.group != ctx.group:
                continue

            id = _device(other.tunnel)

            if id >= KYRKA_PEERS_PER_FLOCK or id == us:
                continue

            if ctx.remembrance:
                # A signaling liturgy lists who is interested in us.
                if other.wanted is None:
                    present[id] = 1
                elif us < len(other.wanted):
                    present[id] = other.wanted[us]
            else:
                # A group liturgy lists who is in the group.
                present[id] = 1

        ctx.fire(KYRKA_EVENT_LITURGY_RECEIVED, {"peers": bytes(present)})


class Module:
    """Stands in for the libkyrka extension module."""

    def __init__(self, net):
        self.net = net

    def alloc(self):
        return Context(self.net)

    def version(self):
        return "fake-kyrka"

    def __getattr__(self, name):
        # The constants the real module pushes into itself.
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name)


class SocketModule:
    """Stands in for the socket module, only as far as pygospel uses it."""

    AF_INET = 2
    SOCK_DGRAM = 2
    gaierror = OSError

    def __init__(self, net):
        self.net = net

    def socket(self, *args):
        return Socket(self.net)

    def getaddrinfo(self, host, port, family=0, type=0):
        return [(self.AF_INET, self.SOCK_DGRAM, 0, "", (self._ip(host), port))]

    def inet_ntoa(self, raw):
        return ".".join(str(b) for b in raw)

    def inet_aton(self, text):
        return bytes(int(x) for x in text.split("."))

    def ntohs(self, value):
        return struct.unpack("!H", struct.pack("=H", value))[0]

    def _ip(self, host):
        try:
            socket = host.split(".")

            if len(socket) == 4 and all(s.isdigit() for s in socket):
                return host
        except AttributeError:
            pass

        return CATHEDRAL[0]


class TimeModule:
    """Stands in for the time module, on the fake network clock."""

    def __init__(self, net):
        self.net = net

    def monotonic(self):
        return self.net.clock.mono

    def time(self):
        return self.net.clock.wall

    def sleep(self, seconds):
        self.net.clock.advance(seconds * 1000)


class Selector:
    """Stands in for selectors.DefaultSelector, on queue contents."""

    def __init__(self):
        self.map = {}

    def register(self, sock, events, callback):
        self.map[id(sock)] = (sock, callback)

    def unregister(self, sock):
        self.map.pop(id(sock), None)

    def get_map(self):
        return self.map

    def select(self, timeout=0):
        ready = []

        for sock, callback in list(self.map.values()):
            if sock.closed:
                continue

            while sock.inbox:
                ready.append((_Key(sock, callback), 1))
                break

        return ready

    def close(self):
        self.map.clear()


class SelectorModule:
    """Stands in for the selectors module."""

    EVENT_READ = 1
    DefaultSelector = Selector


class _Key:
    __slots__ = ("fileobj", "data")

    def __init__(self, fileobj, data):
        self.fileobj = fileobj
        self.data = data


def install(mod, net=None):
    """Point a fresh copy of the chat module at a fake network."""

    net = net if net is not None else Network()

    mod.libkyrka = Module(net)
    mod.socket = SocketModule(net)
    mod.selectors = SelectorModule
    mod.time = TimeModule(net)

    return net
