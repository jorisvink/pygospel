#!/usr/bin/env python3

"""
A gospel peer that answers with a coding agent rather than an echo.

    $ make
    $ PYTHONPATH=build/host/libkyrka/obj/python:src \
        python3 examples/agent.py -c gospel.conf -n pi -d ~/src

Every conversation runs as its own child with a session of its own, so the
messages from one device are one thread of work and a restart picks that
thread back up. What a peer sends becomes a prompt, and the agent's answer
comes back as chat text in pieces that fit a message.

The child is `pi --mode rpc` by default, or Claude Code with --backend claude:

    $ PYTHONPATH=build/host/libkyrka/obj/python:src \
        python3 examples/agent.py -c gospel.conf -d ~/src -a 02 \
        --backend claude

Claude Code asks before it runs anything its permission rules do not already
allow. The question goes to the chat, and the device that sent the prompt
answers it with yes or no, or with yes to all to stop being asked until /ask,
/new, or the agent going away.

Only the devices named in --allow are answered. What the agent can do is
what a shell on this machine can do, and every other device in the flock
could otherwise point it at something.
"""

import os
import re
import sys
import json
import time
import uuid
import shlex
import shutil
import argparse
import tempfile
import subprocess

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pygospel  # noqa: E402

IDLE_KILL    = 900
PROGRESS_GAP = 25
PROGRESS_MAX = 6
CHUNK        = 490
DIALOGS      = ("select", "confirm", "input", "editor")
YES          = ("y", "yes", "ok", "allow", "sure", "on", "true")
NO           = ("n", "no", "deny", "block", "off", "cancel", "false")
ASK_YES      = ("y", "yes")
ASK_NO       = ("n", "no")
ASK_ALL      = ("yes to all", "yes all")
ASK_MAX      = 1500
VOICE_MAX    = 300
IMAGE_LINE   = re.compile(r"^[ \t]*\[image:[ \t]*(.+?)[ \t]*\][ \t]*$", re.M)

# The session a child must not inherit: those describe whoever started this
# program, not the run the child is having on its own.
PI_VARS = ("PI_SESSION_ID", "PI_SESSION_FILE", "PI_CODING_AGENT",
    "PI_MODEL", "PI_PROVIDER", "PI_REASONING_LEVEL")
CLAUDE_VARS = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN")

HELP = """\
  <text>          a job for the agent
  /status         model, whether it is working, how long the session is
  /abort          stop what it is doing
  /new            start a fresh session
  /model <name>   switch model, as provider/id for pi, like sonnet for claude
  /idle <mins>    how long a session waits between messages
  /ask            ask about each command again, after a yes to all
  /peers          who the flock has told us is around
  /join <group>   join a group, in a one-to-one chat
  /leave          leave the group, in a one-to-one chat"""


def log(text):
    sys.stdout.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
    sys.stdout.flush()


def chunk(text, limit):
    """
    text as pieces of at most limit bytes, cut at the last newline or space
    that fits so a piece does not end mid word, and at a character boundary
    where there is no whitespace in it at all.
    """

    data = text.encode("utf-8")
    out = []

    while len(data) > limit:
        cut = data.rfind(b"\n", 0, limit)

        if cut < limit // 2:
            cut = data.rfind(b" ", 0, limit)

        if cut < limit // 2:
            cut = limit

            while cut > 0 and (data[cut] & 0xc0) == 0x80:
                cut -= 1

            if cut < 1:
                cut = limit

        if cut < limit and data[cut:cut + 1] in (b"\n", b" "):
            cut += 1

        out.append(data[:cut])
        data = data[cut:]

    out.append(data)

    return [piece.decode("utf-8", "replace") for piece in out if piece.strip()]


def agent_text(message):
    """The readable part of an assistant message, thinking left out."""

    content = message.get("content")

    if isinstance(content, str):
        return content.strip()

    out = [block.get("text", "") for block in content or []
        if isinstance(block, dict) and block.get("type") == "text"]

    return "\n".join(text for text in out if text).strip()


def parse_ids(text):
    """The device ids in a comma separated list, None for the whole flock."""

    if text.strip().lower() in ("all", "*"):
        return None

    ids = set()

    for part in text.replace(" ", ",").split(","):
        if not part:
            continue

        try:
            id = int(part, 16)
        except ValueError:
            raise SystemExit("not a device id: %s" % part)

        if not 1 <= id <= 0xfe:
            raise SystemExit("device ids run from 01 to fe, not %s" % part)

        ids.add(id)

    return ids


class Agent:
    """
    One conversation: a child process and the messages waiting on it.

    The child's stdout is a reader on the chat's own selector, so commands
    go out and events come back on the loop the tunnels run on, with no
    thread and no second timer. A subclass knows how to talk to one kind of
    child, this class knows how to talk to the chat.
    """

    ENV_DROP = ()

    def __init__(self, chat, key, opts):
        self.chat = chat
        self.opts = opts
        self.key = key
        self.in_group = key[0] == "g"
        self.group = int(key[1:], 16) if self.in_group else None
        self.peer = None if self.in_group else int(key[1:], 16)
        self.session = "gospel-%02x-%s" % (chat.spi, key)

        self.proc = None
        self.fd = None
        self.buf = b""
        self.wants = {}
        self.seq = 0
        self.busy = False
        self.gone = False
        self.targets = []
        self.last = None
        self.answer = None
        self.ticks = 0
        self.told = 0
        self.idle = time.monotonic()

        self.start()

    #
    # The child process.
    #

    def start(self):
        env = dict(os.environ)

        for name in self.ENV_DROP:
            env.pop(name, None)

        try:
            self.proc = subprocess.Popen(self.argv(), cwd=self.opts.dir,
                env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except OSError as e:
            self.gone = True
            log("%s: %s" % (self.key, e))
            return

        self.fd = self.proc.stdout.fileno()
        os.set_blocking(self.fd, False)
        self.chat.add_reader(self.fd, self.on_output)

        log("%s: agent started, session %s" % (self.key, self.session))

    def brief(self):
        who = ("the devices of group %04x" % self.group if self.in_group
            else "device %02x" % self.peer)

        text = ("You are one peer in a Vigil chat: device %02x on this "
            "machine, talking to %s. Every user message is text somebody "
            "typed into that chat, and your reply goes back as chat "
            "messages of at most 512 bytes each. Lead with what you did or "
            "what you found, keep it to a few short lines, and put anything "
            "long in a file under %s and name the path instead of pasting "
            "it. To send an image, write a PNG, JPEG, GIF or WebP of at most "
            "4 MiB under that folder and put [image: path] on a line of its "
            "own in your reply." % (self.chat.spi, who, self.opts.dir))

        if self.in_group:
            text += (" Each message arrives prefixed with the device and "
                "group that wrote it, as 02@0007: ...")

        return text

    def next_id(self):
        self.seq += 1

        return "%s%d" % (self.key, self.seq)

    def ask(self, cmd, want=None):
        """Write one command, and note what to do with its response."""

        if self.proc is None:
            return False

        if want is not None:
            self.wants[cmd["id"]] = want

        try:
            self.proc.stdin.write(json.dumps(cmd).encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except OSError as e:
            log("%s: no command went out: %s" % (self.key, e))
            self.eof()
            return False

        self.idle = time.monotonic()

        return True

    def close(self):
        if self.proc is None:
            return

        if self.fd is not None:
            self.chat.remove_reader(self.fd)

        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except OSError:
                pass

        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

        self.proc = None

    #
    # What the chat hands us.
    #

    def feed(self, msg):
        """One chat message, as a prompt or as the answer to a dialog."""

        self.idle = time.monotonic()

        if self.answered(msg):
            return

        self.last = msg
        self.targets.append(msg)
        self.answer = None
        self.ticks = 0
        self.told = time.monotonic()

        steer = self.busy

        if not steer:
            self.typing(True)
            self.busy = True

        if not self.submit(msg, steer):
            self.busy = False
            self.gone = True
            self.typing(False)
            self.tell(msg, "this peer has no agent running, its log says "
                "why")

    def command(self, msg, head, rest):
        """The few things a peer asks the bridge rather than the agent."""

        if head == "/status":
            self.status(msg)
        elif head == "/abort":
            self.abort(msg)
        elif head == "/new":
            self.fresh(msg)
        elif head == "/idle":
            self.set_idle(msg, rest)
        elif head == "/model":
            self.set_model(msg, rest)
        else:
            self.tell(msg, "%s is not one of these, /help lists them" % head)

    def set_idle(self, msg, rest):
        try:
            minutes = int(rest)
        except ValueError:
            self.tell(msg, "/idle wants minutes, like /idle 5")
            return

        self.opts.idle = max(30, minutes * 60)
        self.tell(msg, "agents go quiet after %d minutes without a message"
            % (self.opts.idle // 60))

    def prompt_of(self, msg):
        if not self.in_group:
            return msg.text

        return "%s: %s" % (msg.where, msg.text)

    def typing(self, on):
        try:
            self.chat.typing(on, self.peer)
        except pygospel.GospelError:
            pass

    def latest(self):
        return self.targets[-1] if self.targets else None

    #
    # Telling the chat about it.
    #

    def emit(self, msg, pieces, quoted=False):
        """
        Send pieces of text back where msg came from. The first one answers
        msg with a quote when `quoted`; the rest are plain messages, since
        a reply chain of pieces quoting each other is noise.
        """

        if msg is None:
            for piece in pieces:
                log(piece)
            return

        for n, piece in enumerate(pieces):
            try:
                if msg.group is None:
                    if n or not quoted:
                        self.chat.send(msg.peer, piece)
                    else:
                        self.chat.reply(msg.peer, piece, msg)
                elif n or not quoted:
                    self.chat.send_group(piece)
                else:
                    self.chat.reply_group(piece, msg)
            except pygospel.GospelError as e:
                log("%s: cannot send: %s" % (msg.where, e))
                return

    def tell(self, msg, text):
        """A line that is not the answer: an ack, a note, a failure."""

        self.emit(msg, chunk(text, CHUNK))

    def deliver(self, text):
        """The answer, quoted onto what it replies to and split to fit."""

        msg = self.latest()
        self.targets = []

        images = IMAGE_LINE.findall(text)
        text = IMAGE_LINE.sub("", text).strip()

        pieces = chunk(text, CHUNK)
        most = max(1, self.opts.max_out // CHUNK)

        if not pieces and not images:
            pieces = ["the agent had nothing to say"]
        elif len(pieces) > most:
            rest = sum(len(piece.encode("utf-8")) for piece in pieces[most:])
            pieces = pieces[:most] + ["(%d more bytes, not sent)" % rest]

        self.emit(msg, pieces, quoted=True)

        for path in images:
            self.send_image(msg, path)

    def send_image(self, msg, path):
        """Send an image from the folder the agent works in, and no other."""

        full = os.path.realpath(os.path.join(self.opts.dir, path))
        root = os.path.realpath(self.opts.dir)

        if msg is None:
            log("%s: no one to send %s to" % (self.key, path))
            return

        if os.path.commonpath((full, root)) != root:
            self.tell(msg, "not sending %s, it is outside %s" % (path, root))
            return

        try:
            if msg.group is None:
                self.chat.send_image(msg.peer, full)
            else:
                self.chat.send_group_image(full)
        except pygospel.GospelError as e:
            self.tell(msg, "cannot send %s: %s" % (path, e))

    def progress(self, what, args):
        if not self.busy or not self.opts.progress:
            return

        now = time.monotonic()

        if self.ticks >= PROGRESS_MAX or now - self.told < PROGRESS_GAP:
            return

        self.ticks += 1
        self.told = now

        args = args or {}
        detail = args.get("command") or args.get("path") or \
            args.get("pattern") or args.get("query") or ""
        detail = " ".join(str(detail).split())[:40]
        what = what or "work"
        line = "%s: %s" % (what, detail) if detail else "%s..." % what

        self.tell(self.latest(), line)

    #
    # What the child says, record by record.
    #

    def on_output(self, source):
        try:
            data = os.read(source, 65536)
        except BlockingIOError:
            return
        except OSError:
            self.eof()
            return

        if not data:
            self.eof()
            return

        self.buf += data

        # Records end at a LF and nowhere else: U+2028 and friends are legal
        # inside a JSON string, so no generic line reader here.
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self.record(line)

    def answered(self, msg):
        """Whether what a peer just typed answers something we asked."""

        return False

    def awaits(self, msg):
        """Whether this message is for us, even without a mention."""

        return False

    def eof(self):
        if self.gone:
            return

        code = self.proc.poll() if self.proc is not None else None
        self.gone = True

        if self.targets:
            self.tell(self.latest(), "the agent went away, status %s" % code)

        self.busy = False
        self.typing(False)
        self.close()

    def record(self, line):
        line = line.rstrip(b"\r")

        if not line.strip():
            return

        try:
            rec = json.loads(line)
        except ValueError:
            log("%s: not JSON: %s" % (self.key, line[:80]))
            return

        self.dispatch(rec)

    def expired(self, now):
        return not self.busy and now - self.idle > self.opts.idle


class PiAgent(Agent):
    """A `pi --mode rpc` child."""

    ENV_DROP = PI_VARS

    def __init__(self, chat, key, opts):
        self.dialog = None

        super().__init__(chat, key, opts)

    def argv(self):
        argv = [self.opts.pi, "--mode", "rpc", "--session-id", self.session,
            "--append-system-prompt", self.brief()]

        if self.opts.model:
            argv += ["--model", self.opts.model]

        return argv

    def submit(self, msg, steer):
        cmd = {"id": self.next_id(), "type": "prompt",
            "message": self.prompt_of(msg)}

        if steer:
            cmd["streamingBehavior"] = "steer"

        return self.ask(cmd, ("prompt", msg))

    def status(self, msg):
        self.ask({"id": self.next_id(), "type": "get_state"}, ("state", msg))

    def abort(self, msg):
        self.tell(msg, "stopping")
        self.ask({"id": self.next_id(), "type": "abort"}, ("note", msg))

    def fresh(self, msg):
        self.wants.clear()
        self.targets = []
        self.busy = False
        self.typing(False)
        self.tell(msg, "a fresh session")
        self.ask({"id": self.next_id(), "type": "new_session"},
            ("note", msg))

    def set_model(self, msg, rest):
        provider, slash, model_id = rest.partition("/")

        if not slash or not provider or not model_id:
            self.tell(msg, "say /model provider/id, for instance "
                "/model anthropic/claude-sonnet-4")
            return

        self.ask({"id": self.next_id(), "type": "set_model",
            "provider": provider, "modelId": model_id}, ("state", msg))

    def dispatch(self, rec):
        kind = rec.get("type")

        if kind == "response":
            self.on_response(rec)
        elif kind == "message_end":
            self.on_message_end(rec)
        elif kind == "tool_execution_start":
            self.progress(rec.get("toolName"), rec.get("args"))
        elif kind == "agent_settled":
            self.settle(rec.get("aborted"))
        elif kind == "extension_ui_request":
            self.on_dialog(rec)

    def on_response(self, rec):
        want = self.wants.pop(rec.get("id"), None)

        if rec.get("success"):
            if want is not None and want[0] == "state":
                self.tell(want[1], self.state_text(rec.get("data") or {}))
            return

        error = rec.get("error") or "it said no"

        if want is None:
            log("%s: %s failed: %s" % (self.key, rec.get("command"), error))
            return

        if want[0] == "prompt":
            self.busy = False
            self.typing(False)

        self.tell(want[1], "the agent would not do that: %s" % error)

    def state_text(self, data):
        model = data.get("model") or {}
        count = data.get("messageCount", 0)

        return "%s/%s, %s, %d message%s in it" % (model.get("provider", "?"),
            model.get("id", "?"),
            "working" if data.get("isStreaming") else "idle",
            count, "" if count == 1 else "s")

    def on_message_end(self, rec):
        message = rec.get("message") or {}

        if message.get("role") != "assistant":
            return

        text = agent_text(message)

        if text:
            self.answer = text

    def settle(self, aborted):
        self.busy = False
        self.typing(False)

        if not self.targets:
            return

        text = self.answer or ""
        self.answer = None

        if aborted:
            text = (text + "\n" if text else "") + "(aborted)"

        self.deliver(text)

    def on_dialog(self, rec):
        """
        An extension's question, put to the peer. A dialog blocks the agent
        until something answers it, and an agent waiting on a dialog is
        indistinguishable from one that has gone quiet.
        """

        method = rec.get("method")

        if method not in DIALOGS:
            if method == "notify":
                self.tell(self.latest(), rec.get("message") or "")
            return

        options = [str(option) for option in rec.get("options") or []]
        self.dialog = {"id": rec.get("id"), "method": method,
            "options": options}

        question = rec.get("title") or "the agent wants an answer"

        if rec.get("message"):
            question = "%s\n%s" % (question, rec["message"])

        if options:
            question = "%s (%s)" % (question, ", ".join(options))
        elif method == "confirm":
            question = "%s (yes or no)" % question
        else:
            question = "%s (an answer, or anything else to drop it)" % question

        self.tell(self.latest(), question)

    def answered(self, msg):
        """Whether what a peer just typed replies to a pending dialog."""

        text = msg.text
        want, self.dialog = self.dialog, None

        if want is None:
            return False

        reply = None

        if want["method"] in ("input", "editor"):
            reply = {"value": text}
        elif want["method"] == "confirm":
            head = text.strip().lower()

            if head in YES:
                reply = {"confirmed": True}
            elif head in NO:
                reply = {"confirmed": False}
        elif text.strip().lower() in [option.lower() for option
            in want["options"]]:
            reply = {"value": text.strip()}

        if reply is None:
            self.ask({"type": "extension_ui_response", "id": want["id"],
                "cancelled": True})
            return False

        response = {"type": "extension_ui_response", "id": want["id"]}
        response.update(reply)
        self.ask(response)

        return True

class ClaudeAgent(Agent):
    """
    A Claude Code child, headless and speaking JSON over its stdio.

    It asks for permission over the same stream, one control_request for
    each tool call its rules do not settle on their own. Those go to the
    chat one at a time and the turn waits for each answer.
    """

    ENV_DROP = CLAUDE_VARS

    def __init__(self, chat, key, opts):
        self.model = opts.model
        self.session_id = None
        self.resuming = False
        self.ready = False
        self.aborting = False
        self.turns = 0
        self.asks = []
        self.asked = None
        self.trusted = set()

        super().__init__(chat, key, opts)

    def argv(self):
        argv = [self.opts.claude, "-p", "--verbose",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--permission-mode", "manual",
            "--permission-prompt-tool", "stdio",
            "--disallowed-tools", "AskUserQuestion",
            "--append-system-prompt", self.brief()]

        if self.model:
            argv += ["--model", self.model]

        self.ready = False
        self.session_id = self.sessions().get(self.session_key())
        self.resuming = self.session_id is not None

        if self.resuming:
            return argv + ["--resume", self.session_id]

        self.session_id = str(uuid.uuid4())

        return argv + ["--session-id", self.session_id]

    #
    # Which session a conversation is in, kept for the next time we run.
    #

    def session_key(self):
        return "%s|%s" % (self.opts.dir, self.session)

    def sessions(self):
        try:
            with open(self.opts.sessions) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def remember(self, session_id):
        known = self.sessions()

        if session_id is None:
            known.pop(self.session_key(), None)
        else:
            known[self.session_key()] = session_id

        path = self.opts.sessions
        tmp = "%s.%d" % (path, os.getpid())

        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)

            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600), "w") as f:
                json.dump(known, f)

            os.replace(tmp, path)
        except OSError as e:
            log("%s: cannot keep the session: %s" % (self.key, e))

    #
    # What the chat asks of it.
    #

    def submit(self, msg, steer):
        return self.ask({"type": "user", "message": {"role": "user",
            "content": self.prompt_of(msg)}})

    def control(self, subtype, want, **fields):
        request_id = self.next_id()
        request = {"subtype": subtype}
        request.update(fields)

        self.wants[request_id] = want

        return self.ask({"type": "control_request", "request_id": request_id,
            "request": request})

    def status(self, msg):
        state = "idle"

        if self.asked is not None:
            state = "waiting for an answer"
        elif self.busy:
            state = "working"

        trust = ""

        if self.trusted:
            trust = ", allowing everything from %s" % ", ".join(
                "%02x" % id for id in sorted(self.trusted))

        self.tell(msg, "claude/%s, %s, %d turn%s in it%s" % (
            self.model or "?", state, self.turns,
            "" if self.turns == 1 else "s", trust))

    def abort(self, msg):
        if not self.busy:
            self.tell(msg, "there is nothing to stop")
            return

        self.tell(msg, "stopping")
        self.aborting = True
        self.control("interrupt", ("note", msg))

    def fresh(self, msg):
        self.close()
        self.remember(None)
        self.wants.clear()
        self.targets = []
        self.asks = []
        self.asked = None
        self.trusted = set()
        self.buf = b""
        self.busy = False
        self.aborting = False
        self.turns = 0
        self.typing(False)
        self.start()
        self.tell(msg, "a fresh session")

    def set_model(self, msg, rest):
        if not rest:
            self.tell(msg, "say /model and a name, for instance /model sonnet")
            return

        self.control("set_model", ("model", msg), model=rest)

    def command(self, msg, head, rest):
        if head != "/ask":
            super().command(msg, head, rest)
            return

        self.trusted.discard(msg.author)
        self.tell(msg, "asking about each command again")

    def expired(self, now):
        return self.asked is None and not self.asks and \
            super().expired(now)

    #
    # Asking the chat for permission.
    #

    def question(self, request):
        """What a tool wants to do, in full unless it is too long to send."""

        tool = request.get("display_name") or request.get("tool_name", "?")
        args = request.get("input") or {}
        lines = []

        for name in sorted(args, key=lambda x: (x != "command",
            x != "file_path", x)):
            if name == "description":
                continue

            value = args[name]

            if not isinstance(value, str):
                value = json.dumps(value)

            lines.append(value if name == "command"
                else "%s: %s" % (name, value))

        if request.get("blocked_path"):
            lines.append("outside the project: %s" % request["blocked_path"])

        body = "\n".join(lines).encode("utf-8")

        if len(body) > ASK_MAX:
            rest = len(body) - ASK_MAX
            body = "%s\n(%d more bytes not shown)" % (
                body[:ASK_MAX].decode("utf-8", "ignore"), rest)
        else:
            body = body.decode("utf-8")

        return "allow %s?\n%s\nyes, no or yes to all" % (tool, body)

    def ask_next(self):
        author = self.last.author if self.last else None

        while self.asked is None and self.asks:
            ask = self.asks.pop(0)

            if author in self.trusted:
                log("%s: allowed %s" % (self.key, json.dumps(ask["request"])))
                self.allow(ask)
                continue

            self.asked = ask
            self.asked["from"] = author
            self.typing(False)
            self.tell(self.last, self.question(ask["request"]))

    def allow(self, ask):
        self.respond(ask["id"], {"behavior": "allow",
            "updatedInput": ask["request"].get("input") or {}})

    def respond(self, request_id, response):
        self.ask({"type": "control_response", "response": {
            "subtype": "success", "request_id": request_id,
            "response": response}})

    def awaits(self, msg):
        return self.asked is not None and \
            " ".join(msg.text.lower().split()) in ASK_YES + ASK_NO + ASK_ALL

    def answered(self, msg):
        if self.asked is None:
            return False

        asked = self.asked
        text = " ".join(msg.text.lower().split())

        if asked["from"] is not None and msg.author != asked["from"]:
            self.tell(msg, "only %02x can answer that" % asked["from"])
            return True

        if text in ASK_ALL:
            self.trusted.add(msg.author)
            self.tell(msg, "allowing everything from %02x, /ask stops that"
                % msg.author)
        elif text not in ASK_YES + ASK_NO:
            self.tell(msg, "waiting for an answer first, /abort stops it")
            return True

        self.asked = None

        if text in ASK_NO:
            self.respond(asked["id"], {"behavior": "deny",
                "message": "The user said no in the chat."})
        else:
            self.allow(asked)

        self.ask_next()

        if self.asked is None and self.busy:
            self.typing(True)

        return True

    #
    # What the child says, record by record.
    #

    def dispatch(self, rec):
        kind = rec.get("type")

        if kind == "system":
            if rec.get("subtype") == "init":
                self.on_init(rec)
        elif kind == "assistant":
            self.on_assistant(rec)
        elif kind == "result":
            self.on_result(rec)
        elif kind == "control_request":
            self.on_control(rec)
        elif kind == "control_cancel_request":
            self.on_cancel(rec)
        elif kind == "control_response":
            self.on_control_response(rec)

    def on_init(self, rec):
        self.model = rec.get("model") or self.model
        self.session_id = rec.get("session_id") or self.session_id

        if not self.ready:
            self.ready = True
            self.remember(self.session_id)

    def on_assistant(self, rec):
        if rec.get("parent_tool_use_id"):
            return

        message = rec.get("message") or {}
        text = agent_text(message)

        if text:
            self.answer = text

        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                self.progress(block.get("name"), block.get("input"))

    def on_result(self, rec):
        if not self.ready:
            return

        aborted, self.aborting = self.aborting, False

        self.turns += 1
        self.busy = False
        self.asks = []
        self.asked = None
        self.typing(False)

        text = rec.get("result") or self.answer or ""
        self.answer = None

        if aborted:
            text = (text + "\n" if text else "") + "(aborted)"
        elif rec.get("is_error"):
            why = "; ".join(rec.get("errors") or []) or rec.get("subtype")
            text = (text + "\n" if text else "") + "it failed: %s" % why

        if not self.targets:
            if not text or self.last is None:
                return

            self.targets.append(self.last)

        self.deliver(text)

    def on_control(self, rec):
        request = rec.get("request") or {}

        if request.get("subtype") != "can_use_tool":
            self.ask({"type": "control_response", "response": {
                "subtype": "error", "request_id": rec.get("request_id"),
                "error": "not supported here"}})
            return

        self.asks.append({"id": rec.get("request_id"), "request": request})
        self.ask_next()

    def on_cancel(self, rec):
        request_id = rec.get("request_id")

        self.asks = [ask for ask in self.asks if ask["id"] != request_id]

        if self.asked is not None and self.asked["id"] == request_id:
            self.asked = None

            if not self.aborting:
                self.tell(self.last, "that question no longer applies")

            self.ask_next()

    def on_control_response(self, rec):
        response = rec.get("response") or {}
        want = self.wants.pop(response.get("request_id"), None)

        if want is None:
            return

        if response.get("subtype") != "success":
            self.tell(want[1], "it would not do that: %s"
                % (response.get("error") or "it said no"))
            return

        if want[0] == "model":
            self.model = (response.get("response") or {}).get("model") or \
                self.model
            self.tell(want[1], "the model is now %s" % self.model)

    def eof(self):
        if self.gone or not self.resuming or self.ready:
            super().eof()
            return

        log("%s: no session %s to resume, starting a new one" % (self.key,
            self.session_id))

        msg = self.latest()

        self.remember(None)
        self.close()
        self.buf = b""
        self.start()

        if msg is not None and self.proc is not None:
            self.submit(msg, False)


AGENTS = {"pi": PiAgent, "claude": ClaudeAgent}


class Transcriber:
    """
    Turns voice messages into text one at a time, with a child on the loop
    of the chat. The audio only exists in a private folder while its child
    runs.
    """

    def __init__(self, chat, argv):
        self.chat = chat
        self.argv = argv
        self.queue = []
        self.msg = None
        self.proc = None
        self.fd = None
        self.buf = b""
        self.started = 0
        self.dir = None

    def add(self, msg):
        self.queue.append(msg)
        self.next()

    def next(self):
        if self.proc is not None or not self.queue:
            return

        self.msg = self.queue.pop(0)
        self.buf = b""
        self.started = time.monotonic()

        if self.dir is None:
            self.dir = tempfile.mkdtemp(prefix="vigil-voice-")

        path = os.path.join(self.dir, "voice")

        try:
            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600), "wb") as f:
                f.write(self.msg.data)

            self.proc = subprocess.Popen(self.argv + [path],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL)
        except OSError as e:
            self.finish(None, str(e))
            return

        self.msg.data = None
        self.fd = self.proc.stdout.fileno()
        os.set_blocking(self.fd, False)
        self.chat.add_reader(self.fd, self.on_output)

    def on_output(self, source):
        try:
            data = os.read(source, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""

        if data:
            self.buf += data
            return

        self.chat.remove_reader(self.fd)
        self.proc.stdout.close()
        code = self.proc.wait()
        self.proc = None

        if code != 0:
            self.finish(None, "the transcriber exited with status %d" % code)
            return

        self.finish(self.buf.decode("utf-8", "replace").strip(), None)

    def finish(self, text, error):
        msg, self.msg = self.msg, None

        self.wipe()
        self.chat.heard(msg, text, error)
        self.next()

    def wipe(self):
        if self.dir is not None:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None

    def check(self, now):
        if self.proc is not None and now - self.started > VOICE_MAX:
            self.proc.kill()

    def close(self):
        if self.proc is not None:
            self.chat.remove_reader(self.fd)
            self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait()
            self.proc = None

        self.wipe()


class AgentChat(pygospel.GospelChat):
    """A gospel chat whose replies come from agent sessions."""

    CHAT_COMMANDS = ("/help", "/peers", "/join", "/leave")

    def __init__(self, config, nickname, opts):
        self.opts = opts
        self.agents = {}
        self.voices = None

        if opts.transcribe:
            self.voices = Transcriber(self, shlex.split(opts.transcribe))

        super().__init__(config, nickname=nickname, group=opts.group,
            signal_all=opts.signal_all, take_data=True,
            discoverable=opts.liturgy is not None)

    def on_message(self, msg):
        text = msg.text.strip()

        if not text or not self.allowed(msg):
            return

        head, _, rest = text.partition(" ")

        if head.startswith("/"):
            head = head.lower()
        elif not self.addressed(msg) and not self.awaited(msg):
            log("%s says %r, and does not mention us" % (msg.where, text))
            return

        if head in self.CHAT_COMMANDS:
            self.chat_command(msg, head, rest.strip())
            return

        agent = self.conversation(msg)

        if head.startswith("/"):
            agent.command(msg, head, rest.strip())
        else:
            agent.feed(msg)

    def on_data(self, msg):
        """A voice message becomes a prompt, anything else a file to read."""

        if not self.allowed(msg):
            return

        if not self.addressed(msg):
            log("%s sent %s, and does not mention us" % (msg.where, msg.name))
            return

        if msg.media != "voice":
            self.keep(msg)
            return

        if self.voices is None:
            self.say(msg, "I cannot listen to voice messages, I was started "
                "without --transcribe")
            return

        log("%s sent a voice message of %d bytes" % (msg.where,
            len(msg.data)))
        self.typing(True, msg.peer if msg.group is None else None)
        self.voices.add(msg)

    def heard(self, msg, text, error):
        """What the transcriber made of a voice message."""

        self.typing(False, msg.peer if msg.group is None else None)

        if error is not None:
            log("%s: no transcript: %s" % (msg.where, error))
            self.say(msg, "I could not transcribe that: %s" % error)
            return

        if not text:
            self.say(msg, "I could not make out any words in that")
            return

        msg.text = "(voice message, transcribed) %s" % text
        self.on_message(msg)

    def keep(self, msg):
        """Save what a peer sent where the agent can go and read it."""

        name = "%d-%s" % (time.time() * 1000, msg.name)
        path = os.path.join(self.opts.inbox, name)

        try:
            os.makedirs(self.opts.inbox, mode=0o700, exist_ok=True)

            with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600), "wb") as f:
                f.write(msg.data)
        except OSError as e:
            log("%s: cannot keep %s: %s" % (msg.where, msg.name, e))
            self.say(msg, "I could not keep %s" % msg.name)
            return

        msg.data = None
        msg.text = "(sent you a %s, saved as %s)" % (msg.media, path)
        self.on_message(msg)

    def chat_command(self, msg, head, rest):
        """
        What the bridge itself does, rather than any one agent. Joining and
        leaving a group is the bridge's own presence, so a peer can only ask
        for it where nobody else is watching.
        """

        if head == "/help":
            self.say(msg, HELP)
        elif head == "/peers":
            self.say(msg, self.peer_list())
        elif msg.group is not None:
            self.say(msg, "say %s to me in a one-to-one chat" % head)
        elif head == "/join":
            self.pick_group(msg, rest)
        else:
            self.leave()
            self.say(msg, "no group, I am only in one-to-one chats")

    def pick_group(self, msg, rest):
        try:
            group = int(rest, 16) & 0xffff
        except ValueError:
            self.say(msg, "/join wants a group id, like /join 0007")
            return

        if group == 0:
            self.leave()
            self.say(msg, "left the group")
            return

        if group == self.group:
            self.say(msg, "already in %04x" % group)
            return

        self.join(group)

        here = len(self.members())

        log("joined group %04x" % group)
        self.say(msg, "joined %04x%s" % (group, "" if not here
            else ", %d of them are here" % here))

    def say(self, msg, text):
        """Send text back where a message came from, in pieces that fit."""

        for n, piece in enumerate(chunk(text, CHUNK)):
            try:
                if msg.group is None:
                    if n:
                        self.send(msg.peer, piece)
                    else:
                        self.reply(msg.peer, piece, msg)
                elif n:
                    self.send_group(piece)
                else:
                    self.reply_group(piece, msg)
            except pygospel.GospelError as e:
                log("%s: cannot send: %s" % (msg.where, e))
                return

    def peer_list(self):
        out = ["group %04x" % self.group if self.group else "no group"]
        seen = 0

        for peer in self.roster():
            if not (peer.present or peer.member or peer.online):
                continue

            out.append("%02x %-14s %s%s" % (peer.id, peer.best_name,
                peer.link, " group" if peer.member else ""))
            seen += 1

        if not seen:
            out.append("no peer has told the cathedral it is around")

        return "\n".join(out)

    def allowed(self, msg):
        if self.opts.allow is None:
            return True

        if msg.author in self.opts.allow:
            return True

        log("%s says %r, and is not in --allow" % (msg.where, msg.text))

        return False

    def addressed(self, msg):
        if msg.group is None or self.opts.group_all:
            return True

        return msg.mentions_me or msg.text.startswith("/")

    def conversation_key(self, msg):
        return ("g%04x" % msg.group) if msg.group is not None \
            else ("p%02x" % msg.peer)

    def awaited(self, msg):
        agent = self.agents.get(self.conversation_key(msg))

        return agent is not None and agent.awaits(msg)

    def conversation(self, msg):
        key = self.conversation_key(msg)
        agent = self.agents.get(key)

        if agent is not None and agent.gone:
            agent.close()
            del self.agents[key]
            agent = None

        if agent is None:
            agent = AGENTS[self.opts.backend](self, key, self.opts)
            self.agents[key] = agent

        return agent

    def tick(self):
        super().tick()

        now = time.monotonic()

        if self.voices is not None:
            self.voices.check(now)

        for key in list(self.agents):
            agent = self.agents[key]

            if agent.gone or agent.expired(now):
                agent.close()
                del self.agents[key]

    def on_peer(self, peer):
        log("%02x (%s) is around, %s" % (peer.id, peer.best_name,
            self.link(peer.id)))

    def on_name(self, peer):
        log("%02x now goes by %s" % (peer.id, peer.best_name))

    def on_joined(self, peer):
        log("%s joined %04x" % (peer.best_name, self.group))

    def on_left(self, peer):
        log("%s left %04x" % (peer.best_name, self.group))

    def on_offline(self):
        log("the cathedral stopped answering")

    def close(self):
        for agent in list(self.agents.values()):
            agent.close()

        self.agents.clear()

        if self.voices is not None:
            self.voices.close()

        super().close()


def main():
    ap = argparse.ArgumentParser(description="a gospel peer whose replies "
        "come from a coding agent")
    ap.add_argument("-c", "--config", required=True,
        help="a Vigil configuration in gospel mode")
    ap.add_argument("-n", "--nickname", default="pi",
        help="the name peers see, up to 16 letters and digits")
    ap.add_argument("-d", "--dir", default=os.getcwd(),
        help="the folder the agent works in")
    ap.add_argument("-a", "--allow", default="", type=parse_ids,
        help="device ids to answer, as 01,02, or all; empty answers nobody")
    ap.add_argument("-g", "--group", type=lambda x: int(x, 16), default=0,
        help="the group to join at start up, 0 for none")
    ap.add_argument("-l", "--liturgy", nargs="?", const="config",
        help="show up as a peer in this liturgy_group, in hex, or in the one "
        "from the configuration if none is given; turns off signaling the "
        "whole flock")
    ap.add_argument("--group-all", action="store_true",
        help="answer everything in the group, not only what mentions us")
    ap.add_argument("--no-signal-all", dest="signal_all",
        action="store_false",
        help="stay out of the flock's peer lists, as Vigil does")
    ap.add_argument("--backend", choices=sorted(AGENTS), default="pi",
        help="the coding agent to run")
    ap.add_argument("--pi", default="pi", help="the pi command to run")
    ap.add_argument("--claude", default="claude",
        help="the Claude Code command to run")
    ap.add_argument("--model", help="the model the agents start on")
    ap.add_argument("--idle", type=int, default=IDLE_KILL // 60,
        help="minutes a session waits before its agent is retired")
    ap.add_argument("--max-out", type=int, default=4096,
        help="how much of one answer to send, in bytes")
    ap.add_argument("--transcribe",
        help="a command that prints the words of the audio file it is given "
        "as its last argument, see tools/transcribe.py")
    ap.add_argument("--progress", action="store_true",
        help="say which tools the agent runs while it works")

    args = ap.parse_args()
    args.dir = os.path.abspath(args.dir)
    args.idle = max(30, args.idle * 60)
    state = os.path.join(os.environ.get("XDG_STATE_HOME") or
        os.path.expanduser("~/.local/state"), "vigil-python")
    args.sessions = os.path.join(state, "claude-sessions.json")
    args.inbox = os.path.join(state, "inbox")

    if not os.path.isdir(args.dir):
        sys.exit("%s is not a folder" % args.dir)

    if args.liturgy is not None:
        args.signal_all = False

    try:
        config = pygospel.GospelConfig.load(args.config)

        if args.liturgy not in (None, "config"):
            config.group = int(args.liturgy, 16)
            config._validate()

        chat = AgentChat(config, args.nickname, args)
    except ValueError:
        sys.exit("--liturgy wants a group in hex, not %s" % args.liturgy)
    except (pygospel.GospelError, OSError) as e:
        sys.exit(str(e))

    who = ("every device in the flock" if args.allow is None
        else ", ".join("%02x" % id for id in sorted(args.allow))
            or "no one, pass --allow 02")

    log("gospel agent on %02x as %s in %s, %s answering %s" % (chat.spi,
        args.nickname or "*", args.dir, args.backend, who))
    log("%s the flock's peer lists, group %s" % ("in" if args.signal_all
        else "not in", "%04x" % chat.group if chat.group else "none"))

    if chat.presence is not None:
        log("a peer in liturgy_group %04x" % config.group)

    try:
        chat.run()
    except KeyboardInterrupt:
        pass
    finally:
        chat.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
