# vigil-python

Gospel chats in python, on top of [libkyrka](https://sanctorum.se).

This is the message part of [vigil](https://github.com/jorisvink/vigil)
taken out of the app and made into a module you can build programs with. It
is `pygospel` on the path and in `import`, from a repository called
vigil-python.

It speaks gospel over sanctum tunnels, it is compatible with
[Vigil](https://github.com/jorisvink/vigil) and with
the [gospel](https://github.com/jorisvink/gospel) project. It does text both
ways, sends images and can receive data, see [Images](#images) and
[Receiving data](#receiving-data). It makes and takes voice calls, see
[Calls](#calls). Unless you ask it to take data it never announces that it
can, so a Vigil peer that has something for you sends you a message saying so
instead, which is what Vigil already does for a peer that cannot take data.
The other bit it will set is the signed-name bit, and only when it has a name
that verifies, see [Signed names](#signed-names).

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
address) are read and ignored. `liturgy_group` is the group calls signal in,
chats do not use it. It only matters here with `discoverable=True`, which
puts this device in the list Vigil shows for that group. Vigil then also
offers to call it, which rings here with `take_calls=True`, see [Calls](#calls).

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

A group message goes to the members we have a tunnel with, one whose tunnel is
down gets it when it comes back, and nothing is kept for a member that left.
`on_group_delivered` counts down as they acknowledge it.

# Images

```python
chat.send_image(0x02, "plot.png")
chat.send_image(0x02, png_bytes, name="plot.png", ref=7)
chat.send_group_image("plot.png")
```

An image is a path or bytes holding a PNG, JPEG, GIF or WebP of at most 4 MiB,
anything else raises `GospelError`. It waits in the outbox like a message
does and `on_sent`, `on_delivered` and `on_group_delivered` follow it, with
the file name in `msg.name`. A peer that does not take data, such as gospel,
gets a message saying it was sent an image instead.

# Receiving data

```python
chat = pygospel.GospelChat("gospel.conf", take_data=True)
chat.on_data = lambda msg: open(msg.name, "wb").write(msg.data)
```

`msg.media` is `image`, `file` or `voice`, `msg.name` the file name its sender
gave it and `msg.data` the bytes, at most 4 MiB. The name came from a peer, so
treat it like anything else that did. With `auto_ack=False` call `msg.ack()`
once you have kept it, or it comes again after a minute. A peer's avatar comes
through the same channel but not through `on_data`, see [Avatars](#avatars).

# Avatars

```python
chat.set_avatar("face.png")
chat.set_avatar(None)
print(chat.peer(0x02).avatar)
```

An avatar is the picture a device shows for itself: a path or bytes holding a
PNG, JPEG, GIF or WebP of at most 32 KiB, which is what Vigil calls
`VIGIL_CHAT_AVATAR_MAX`. It is not a message. It holds no place in an outbox,
goes to every tunnel that takes data once the data waiting there has gone, and
gets no `on_sent` or `on_delivered`. Peers learn you have one from a bit in the
heartbeat, so taking it away is enough to make them drop it. Heartbeats also
say which picture a peer already holds of you, so a tunnel coming back up does
not send it again; a peer that restarted or that predates this gets it anew.

What a peer sends lands in `peer.avatar`, and `on_avatar(peer)` says it
happened, both when it arrives and when the peer stops announcing one. Avatars
ride the data channel, so you take them with `take_data=True`.

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
| `on_data(msg)` | an image, file or voice message arrived |
| `on_avatar(peer)` | a peer's picture arrived, or it took it away |
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
| `discoverable` | show up as a peer for devices using the same `liturgy_group` |
| `take_data` | receive images, files and voice messages through `on_data` |
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
$ python3 examples/agent.py -c gospel.conf -d ~/src
```

Before `make install` they need the built module on the path instead:

```
$ PYTHONPATH=build/host/libkyrka/obj/python python3 examples/chatter.py -c gospel.conf -n alice
```

`chatter.py` is a terminal client with a prompt, `/to 02` and `/join 0007`
to point it at a device or a group. `echo.py` is the smallest thing that
works, about twenty lines on top of `GospelChat`. `presence.py` watches the
flock and says who is reachable and how. `call.py` answers or makes a voice
call and sends back what it hears, see [Calls](#calls).

`agent.py` is a peer rather than a client: it hands whatever a device sends
it to a `pi --mode rpc` child, one per conversation and one session each,
and answers with what the agent comes back with. The agent works in the
folder `--dir` names, its answer is split to fit messages of 512 bytes, a
message sent while it is working waits for its own turn, and `/abort`, `/status`,
`/model` and `/new` are there to type into a chat. Devices in `--allow` are
the only ones it answers.

An agent sends an image by putting `[image: path]` on a line of its own in
its answer. The path is relative to `--dir` and has to stay inside it.

Images and files sent to an agent are kept under
`~/.local/state/vigil-python/inbox`, and the agent is told where. They stay
there until you remove them. A voice message becomes a prompt with
`--transcribe`, a command that prints the words of the audio file it is given,
see [Requirements](#requirements):

```
$ python3 examples/agent.py -c gospel.conf -a 02 \
    --transcribe "$HOME/pygospel/agent/whisper/bin/python tools/transcribe.py"
```

That runs on this machine, and the audio is deleted once it has its words. A
transcript is marked as one, so it cannot run a command or answer a permission
question. In a group an agent only takes data with `--group-all`.

A device in `--allow` can also call the agent, with `--hear` and `--speak`.
Both are commands that stay up for the length of a call, and `--liturgy` is what
gets the agent listed as online. Everything runs on this machine, the audio is
only ever in memory, and the models load while the call rings. Install them
with [`make requirements`](#requirements):

```
$ AGENT=$HOME/pygospel/agent
$ HF_HOME=$AGENT/huggingface PYTHONPATH=build/host/libkyrka/obj/python \
    $AGENT/venv/bin/python examples/agent.py -c gospel.conf -a 02 --liturgy \
    --hear "$AGENT/whisper/bin/python tools/hear.py" \
    --speak "$AGENT/piper/bin/python tools/speak.py \
    $AGENT/piper/en_US-lessac-medium.onnx"
```

What you say is a prompt in the same conversation as your chat, and the answer
is spoken as the agent writes it, one sentence at a time. It also goes to the
chat as usual, so nothing is lost by what speech leaves out: code, images and
anything past the first 1200 characters. A soft sound every few seconds means
it is working.

Talking over it stops it talking, and it gets told how far you heard. What you
said is then the next message, so it does not stop the agent working, `/abort`
in the chat does that. A transcript cannot run a command or answer a question:
when it needs your permission it says so, and you type the answer in the chat.

`tools/hear.py` takes `--language`, worth setting because a few spoken words are
not much to detect one from, and `--device cuda --compute float16` for a GPU.
The voice speaks the one language it was made for, so pick a voice to match.

`!render <prompt>` draws an image and sends it back, without involving the
agent. It needs `--render`, a command that reads the prompt on stdin and writes
a PNG to the path it is given as its last argument. `tools/render.py` does that
with Z-Image-Turbo on a GPU, downloading the models the first time, see
[Requirements](#requirements):

```
$ python3 examples/agent.py -c gospel.conf -a 02 \
    --render "$HOME/pygospel/agent/render/bin/python tools/render.py"
```

Only devices in `--allow` can ask, one image is drawn at a time, and a group
needs no mention for it. The model is loaded for each image and gone from the
GPU afterwards, so asking takes a little longer and costs nothing in between.

While a job runs, the agent says how it is doing every minute: how long it has
been at it, how many tools it ran, the last one, and what the backend reports.
That is context use, session tokens and cost for `pi`, context and tokens
written for Claude Code. A job shorter than that says nothing, and neither does
one waiting on an answer from the chat. `--stats` sets the seconds between
lines, 0 turns them off.

An agent shows no avatar of its own unless you give it one, and it only logs
the ones peers send:

```
$ python3 examples/agent.py -c gospel.conf --avatar ~/face.png
```

`--backend claude` runs Claude Code instead of `pi`. Anything its permission
rules do not already allow comes to the chat as a question showing what it
wants to run, and the device that sent the prompt answers with `yes`, `no` or
`yes to all`. The turn waits until it does, and `/abort` stops it. Your Claude
Code settings still apply, so what they allow runs without asking and what
they deny stays denied.

`yes to all` stops the asking for that device in that conversation, and every
command it then allows goes to the log. It ends with `/ask`, `/new`, a restart
or the agent being retired after `--idle` minutes without a message, and a
session picked up again after that asks again.

By default the agent signals the whole flock. With `--liturgy 0007` it shows
up as a peer only for devices using that `liturgy_group`, and a chat starts
when one of them opens it. With `--group 0007`, or `/join 0007` in a
one-to-one chat, it joins a group chat and answers what mentions it. Add
`--no-signal-all` if it should only be seen in that group. A device outside
the `liturgy_group` can still open a chat by id, the flock is what admits it
and `--allow` is what gets it an answer.

# Calls

Voice calls need [opuslib](https://pypi.org/project/opuslib/) and are
limited to 1 call at a time.

```python
class Phone(pygospel.GospelChat):
    def on_call_ring(self, call):
        call.answer()

    def on_call_audio(self, call, pcm):
        call.send_audio(pcm)

    def on_call_end(self, call, reason):
        print("%02x: %s" % (call.peer.id, reason))

Phone("gospel.conf", take_calls=True, discoverable=True).run()
```

Vigil only offers to call a device it counts as online, which is what
`discoverable` is for, and both sides need the same `liturgy_group`. The devices
we see there are in `chat.callable`, so an empty one means no Vigil is in that
group. `examples/call.py --liturgy 0007` says this as it happens.

`on_call_ring` fires once the tunnel to the caller is up, which is the proof
of who it is, so `call.peer.id` is safe to decide on. It rings until you call
`call.answer()` or `call.hangup()`, or for 60 seconds.

`chat.call(0x02)` calls a device and returns the same `Call`. It needs no
`take_calls`, that only decides whether others can ring you.

| | |
|---|---|
| `on_call_ring(call)` | a peer proved it is calling us |
| `on_call_talking(call)` | the first audio arrived |
| `on_call_audio(call, pcm)` | 20 ms of audio, lost frames already repaired |
| `on_call_end(call, reason)` | `local`, `remote`, `timeout` or `error` |

`call.send_audio(pcm)` takes audio of any length and `call.queued` says how
many milliseconds still wait, up to five minutes. `call.clear_audio()` drops
them. A talking call sends fifty frames a second, silence when nothing is
queued, because a peer takes twenty seconds without one for a dead call.

Those fifty frames depend on `run()` or `poll()` being called, so a hook that
blocks stutters the call. Do slow work in another process, the way
`examples/agent.py` does. `examples/call.py` answers or makes a call and sends
back what it hears.

If two devices call each other at the same moment neither rings, and both give
up after 60 seconds. Video is not supported, and a video frame is ignored.

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

# Files

| file | what |
|---|---|
| `src/pygospel.py` | the chat, the configuration and the wire format |
| `tests/fakekyrka.py` | an in-process libkyrka and cathedral |
| `tests/gospel_test.py` | the protocol and state machine, no network |
| `tests/smoke.py` | the same against the real libkyrka |
| `examples/` | a terminal client, an echo bot, a flock watcher, an agent peer |
| `tools/cathedral/` | fetches sanctum, runs a cathedral, talks to it |
| `tools/transcribe.py` | the words of an audio file, for agents |
| `tools/hear.py` | the words of a call as they are said, for agents |
| `tools/speak.py` | an answer as speech on a call, for agents |
| `tools/render.py` | an image from a prompt, for agents |
| `tools/check-style.py` | tabs, alignment and column checks |
| `tools/align.py` | applies what check-style.py asks for |
| `dist-build/` | fetching, patching and building libkyrka and sanctum |
