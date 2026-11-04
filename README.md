# vigil-python

Text-only gospel chats in python, on top of [libkyrka](https://sanctorum.se).

This is the message part of [vigil](https://github.com/jorisvink/vigil)
taken out of the app and made into a module you can build programs with. It
is `pygospel` on the path and in `import`, from a repository called
vigil-python.

It speaks gospel over sanctum tunnels, it is compatible with
[Vigil](https://github.com/jorisvink/vigil) and with
the [gospel](https://github.com/jorisvink/gospel) project, and it does text
only: no calls, no images, no files. It never announces that it can take
data or avatars, so a Vigil peer that has one for you sends you a message
saying so instead, which is what Vigil already does for a peer that cannot
take data. The one bit it will set is the signed-name bit, and only when it
has a name that verifies, see [Signed names](#signed-names).

```python
import pygospel

chat = pygospel.GospelChat("gospel.conf", nickname="alice")
chat.on_message = lambda msg: print("%02x: %s" % (msg.peer, msg.text))
chat.send(0x02, "blessed sanctum")
chat.run()
```

# Building

The tunnels are driven by the libkyrka python module, which this fetches,
patches and builds for you:

```
$ make
$ make test
```

`make` downloads libkyrka, checks its checksum, unpacks it under `build/`
and applies `dist-build/patches`. It needs the python3 development headers
and libsodium (libkyrka gets AES-GCM, x25519 and ed25519 from it). If you
would rather not use the libsodium your distribution ships:

```
$ make LIBSODIUM_PATH=/path/to/installed/libsodium
```

The two patches are what makes libkyrka usable for chats:

- `libkyrka-graces.patch` — the same one vigil applies, it puts a packet
  type in the tunnel tail so heartbeats and traffic can be told apart.
- `libkyrka-python.patch` — exposes `cathedral_liturgy()` to python and
  hands the liturgy peer list to the event callback. Without those a
  client cannot tell a cathedral which devices it wants and cannot learn
  who is in a group, so there is no 1:1 delivery and no groups at all.

`make test` needs nothing built at all, it runs the chats against a fake.
`make smoke` runs the same code against the real module rather than a fake,
and `make lint` checks the style of the python in here.

Nothing is vendored. Everything under `build/` is fetched or generated, so
the repository is the python, the patches and four shell scripts.

# Installing

Build it and install it the way the rest of this family does:

```
$ make
$ sudo make install
```

That puts `pygospel.py` and the patched `libkyrka.so` next to each other in
your python's site directory, so `import pygospel` works from anywhere with
nothing on the path. Building is part of `make install`, so a clean tree
fetches and patches libkyrka first.

The site directory is asked off the interpreter rather than guessed, since
Debian uses `dist-packages` where everyone else uses `site-packages`, and
it follows a virtualenv without being told. Two ways elsewhere:

```
$ make install DESTDIR=/tmp/stage      # staged, the path kept under it
$ make install PYSITE=/opt/py/lib      # this exact directory
```

# Configuration

It takes a Vigil configuration, so a `mode gospel` config written for Vigil
or Covenant works as it is:

```
vigil 1
instance alice.laptop
mode gospel
spi 0c
kek <64 hex characters>
cathedral cathedral.reliquary.se:4469
cathedral_id 5eb2411f
cathedral_flock 493abf95a07e0c00
cathedral_secret <64 hex characters>
cathedral_cosk <128 hex characters>
```

`spi` is who you are: one byte, 01 to fe, and the id peers use to address
you. The `kek` is the device key, `cathedral_secret` the secret bound to
your `cathedral_id` and `cathedral_cosk` the offer signing key of the
flock. Pass the file path, the contents, or a `GospelConfig` you built
yourself:

```python
chat = pygospel.GospelChat(pygospel.GospelConfig.load("gospel.conf"))
```

Options that have no bearing on a text chat (routes, dns, a tunnel
address) are read and ignored. `liturgy_group` is read but not used by
chats: in a gospel configuration that is the group calls signal in.

# One-to-one chats

A message waits in the peer outbox until the peer has stored it, so it
gets through when the peer was asleep, and it is sent again every five
seconds until it is acknowledged. Outboxes live in memory; nothing is
written to disk here.

```python
msg = chat.send(0x02, "are you there")

chat.on_sent(ref, msg)        # it left
chat.on_delivered(ref, msg)   # the peer has it stored
```

Devices do not keep a tunnel open to every other device. A chat tells the
cathedral which devices it wants through a signaling liturgy, and the
cathedral says which devices want it. By default a device is signaled when
there is something to send to it, when its chat was opened with `open()` or
when it was talked to in the last minute, which is what Vigil does. Pass
`signal_all=True` to signal the whole flock and see all of it.

# Group chats

A group is a liturgy in the group chat domain. Every member says it is in
the group, the cathedral answers with the members that are online.

There is no group key. Each member gets its own tunnel with its own keys
and the message is sent over each in turn, so one member leaking does not
expose a conversation with another.

```python
chat.join(0x0007)
chat.send_group("anyone about")

chat.on_joined(peer)
chat.on_left(peer)
chat.on_group_delivered(ref, group, acked, pending)
```

A group message goes to whoever is online at that moment and nowhere else.
`on_group_delivered` counts down as they acknowledge it.

# Signed names

A configuration made by Covenant carries a name for the device that the
flock key signed:

```
instance alice.laptop
name_pubkey    <64 hex characters>
name_signature <128 hex characters>
```

What is signed is:

```
m = "COVENANT.NAME" || flock || kek || len(name) || name
```

with the flock 64-bit big endian and the KEK id and the length one byte
each. The KEK id is the id of the device the name belongs to, and it is
taken from the tunnel the name arrived on, never from the sender, so a
device cannot spend the signed name of another device.

Put those three lines in a configuration and the chat sends the name in its
heartbeats, sets the feature bit for it, and checks the names that come
back the other way:

```python
chat.named                    # True, our own signature checked out
chat.name_status              # 'signed as alice.laptop'
chat.peer(0x02).name          # 'bob.machine', verified
chat.peer(0x02).signed        # True
chat.peer(0x02).best_name     # the name, else the nickname, else '02'
```

Checking a name needs ed25519, which comes from `cryptography` or, failing
that, from `PyNaCl`. Install one of the two if you want signed names.
Without either the chat says so in `name_status`, sends no name, announces
no name bit and shows none. It does not fall back to showing an unchecked
name, because an unchecked name is only what a peer felt like saying.

Cathedrals never see a name, it travels inside the tunnel only.

# Writing programs

Everything happens on the timers gospel needs, so a program either lets
`run()` block or drives the loop itself.

```python
chat = pygospel.GospelChat("gospel.conf", nickname="alice")

chat.add_reader(sys.stdin, chat.on_input)   # your fds, same loop
while chat.running:
    chat.poll(0.5)                          # traffic, then timers
chat.close()
```

Hooks can be methods on a subclass or plain functions on the instance:

| hook | when |
|---|---|
| `on_message(msg)` | a message arrived |
| `on_sent(ref, msg)` | one of ours left for a peer |
| `on_delivered(ref, msg)` | a peer stored one of ours |
| `on_group_delivered(ref, group, acked, pending)` | the group tally moved |
| `on_peer(peer)` | a peer became reachable |
| `on_name(peer)` | a peer changed its nickname or its signed name |
| `on_typing(peer, typing)` | a peer started or stopped typing |
| `on_joined(peer)` / `on_left(peer)` | group membership changed |
| `on_offline()` | the cathedral stopped answering |

The rest of the surface a program uses:

```python
chat.reply(0x02, "hello back", msg)         # msg or a Quote
chat.reply_group("as do I", msg)
chat.leave()
chat.open(0x02) / chat.close_chat(0x02)
chat.typing(True) / chat.typing(True, 0x02)
chat.outbox(0x02) / chat.forget(0x02)
chat.roster() / chat.members() / chat.link(0x02) / chat.status()
```

A received `msg` carries `text`, `peer`, `group`, `author`, `hash`, `quote`
and `ref`. Text is normalized the way Vigil normalizes it, control
characters become `?` and broken UTF-8 becomes `?`, so `msg.text` is always
a `str` you can print. Messages are at most 512 bytes.

Constructor options beyond `config` and `nickname`:

| option | meaning |
|---|---|
| `group` | join on start up, 0 (the default) to join none |
| `auto_ack` | ack messages as they arrive, on by default |
| `p2p` | try direct paths, overriding the config |
| `cathedrals` | `[(host, port), ...]` instead of the configured one |
| `signal_all` | signal every device in the flock |
| `features` | bits to announce; none by default, plus the name bit when the signature verifies |

With `auto_ack=False` nothing is acknowledged until you say you kept it,
which is what you want if a message must reach a database before the sender
believes it arrived:

```python
chat = pygospel.GospelChat("gospel.conf", nickname="alice", auto_ack=False)

def on_message(self, msg):
    vault.store(msg)
    msg.ack()
```

A message you never ack is handed to you again after ten seconds.

# Examples

```
$ python3 examples/chatter.py -c gospel.conf -n alice
$ python3 examples/echo.py -c gospel.conf
$ python3 examples/presence.py -c gospel.conf
```

Before `make install` they need the built module on the path instead:

```
$ PYTHONPATH=build/host/libkyrka/obj/python python3 examples/chatter.py -c gospel.conf -n alice
```

`chatter.py` is a terminal client with a prompt, `/to 02` and `/join 0007`
to point it at a device or a group. `echo.py` is the smallest thing that
works, about twenty lines on top of `GospelChat`. `presence.py` watches the
flock and says who is reachable and how.

# How it reaches the network

Every tunnel and every liturgy gets its own UDP socket and its own
libkyrka context, which is how a cathedral knows where to answer. Nothing
here listens on a port you have to configure, and nothing opens a network
interface.

A 1:1 chat tunnel is numbered `our id << 8 | peer id` in the chat flock
domain, a tunnel to a group member the same number in the group flock
domain, and the liturgies carry our own id. Domains are stripped before
keying, so the same two devices share nothing between a chat, a call and a
group.

Frames always leave at full size, so a text message, an acknowledgement and
a heartbeat are the same length to anyone watching. Heartbeats go out every
twelve seconds, and also when someone starts or stops typing.

# Testing

```
$ make test
$ make smoke
```

`tests/gospel_test.py` runs two and three chats against each other over
`tests/fakekyrka.py`, an in-process stand-in for libkyrka and a cathedral
with a clock the test turns. That covers the wire format and the state
machine without a network: frame layouts, duplicates, outbox redelivery,
store-then-ack, name verification and the cases where a name must not be
shown.

`tests/smoke.py` runs the same module against the real libkyrka, pointed at
a closed port, so a mistake in how a context is configured shows up there
rather than in a running program.

Neither of those is a real cathedral. `tools/cathedral` gives you one, out
of a sanctum release tarball fetched and checksummed into `build/`.

```
$ make cathedral-e2e
```

That builds a rootless sanctum, generates a throwaway flock with its KEKs,
ambry, COSK and Cathedral Secret, signs a name per device, starts a
cathedral on a free port and has three chats talk to each other, one to
one and in a group. Step by step:

```
$ make cathedral            # fetch, build, start, provision
$ make cathedral-status
$ make cathedral-down
$ PORT=14500 make cathedral # a port of your own, instead of a free one
```

The sanctum version is pinned in `dist-build/sanctum.sh` and can be moved
with `SANCTUM_VERSION=`. `dist-build/rootless.patch` adds `geteuid()` guards
around the three places sanctum needs privileges, so a root run is
unchanged; it is a test build and a real flock should never point at it.

Everything the cathedral makes, its keys, ambry, configurations and log,
goes under `build/cathedral`. `make clean` takes it away along with the
built module, `make dist-clean` also drops the downloaded tarballs.

# Authorship

Your neighborhood LLM

# Files

| file | what |
|---|---|
| `src/pygospel.py` | the chat, the configuration and the wire format |
| `tests/fakekyrka.py` | an in-process libkyrka and cathedral |
| `tests/gospel_test.py` | the protocol and state machine, no network |
| `tests/smoke.py` | the same against the real libkyrka |
| `examples/` | a terminal client, an echo bot, a flock watcher |
| `tools/cathedral/` | fetches sanctum, runs a cathedral, talks to it |
| `tools/check-style.py` | tabs, alignment and column checks |
| `tools/align.py` | applies what check-style.py asks for |
| `dist-build/` | fetching, patching and building libkyrka and sanctum |
