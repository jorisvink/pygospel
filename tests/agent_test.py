#!/usr/bin/env python3

"""
Tests for what examples/agent.py does with Claude Code, no child and no
network.

Run with: python3 tests/agent_test.py
"""

import io
import os
import sys
import json
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))

import fakekyrka  # noqa: E402

sys.modules["libkyrka"] = fakekyrka.Module(fakekyrka.Network())

import agent  # noqa: E402
import pygospel  # noqa: E402

agent.log = lambda text: None

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


class Chat:
    spi = 0x07

    def __init__(self):
        self.said = []
        self.typed = []
        self.images = []

    def send(self, peer, text):
        self.said.append(text)

    def reply(self, peer, text, msg):
        self.said.append(text)

    def send_group(self, text):
        self.said.append(text)

    def reply_group(self, text, msg):
        self.said.append(text)

    def send_image(self, peer, path):
        self.images.append((peer, path))

    def send_group_image(self, path):
        self.images.append((None, path))

    def typing(self, on, peer):
        self.typed.append(on)

    def add_reader(self, fd, callback):
        pass

    def remove_reader(self, fd):
        pass


class Proc:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stdin.close = lambda: None
        self.stdout.close = lambda: None

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class Quiet(agent.ClaudeAgent):
    """A ClaudeAgent writing to a buffer rather than to a child."""

    def __init__(self, chat, key, opts):
        self.starts = []

        super().__init__(chat, key, opts)

    def start(self):
        self.starts.append(self.argv())
        self.proc = Proc()

    def out(self):
        lines = self.proc.stdin.getvalue().splitlines()

        return [json.loads(line) for line in lines]


def opts(tmp, **kwargs):
    fields = dict(claude="claude", dir="/work", model=None, idle=900,
        max_out=4096, progress=False,
        sessions=os.path.join(tmp, "state", "sessions.json"))
    fields.update(kwargs)

    return types.SimpleNamespace(**fields)


def message(text, author=0x02, group=None):
    return types.SimpleNamespace(text=text, author=author, peer=author,
        group=group, where="%02x" % author)


def permission(request_id="r1", command="rm -rf build"):
    return {"type": "control_request", "request_id": request_id, "request": {
        "subtype": "can_use_tool", "tool_name": "Bash",
        "display_name": "Bash", "tool_use_id": "t1",
        "input": {"command": command, "description": "harmless, honest"}}}


def result(text="done"):
    return {"type": "result", "subtype": "success", "is_error": False,
        "result": text}


def running(tmp):
    chat = Chat()
    one = Quiet(chat, "p02", opts(tmp))

    one.feed(message("clean up"))
    one.dispatch({"type": "system", "subtype": "init", "model": "m1",
        "session_id": one.session_id})

    return chat, one


def test_prompt_goes_to_the_child():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        check("user message", one.out()[0], {"type": "user", "message": {
            "role": "user", "content": "clean up"}})
        check("typing", chat.typed, [True])

        argv = one.starts[0]

        check("asks over stdio", argv[argv.index(
            "--permission-prompt-tool") + 1], "stdio")
        check("asks by default", argv[argv.index("--permission-mode") + 1],
            "manual")


def test_yes_allows():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission())

        ok("question shows the command", "rm -rf build" in chat.said[-1])
        ok("question says how to answer", "yes to all" in chat.said[-1])
        ok("not the description", "harmless" not in chat.said[-1])
        check("stops typing to ask", chat.typed[-1], False)
        check("nothing answered yet", len(one.out()), 1)

        one.feed(message(" YES "))

        check("allowed", one.out()[-1]["response"], {
            "subtype": "success", "request_id": "r1", "response": {
                "behavior": "allow", "updatedInput": {
                    "command": "rm -rf build",
                    "description": "harmless, honest"}}})
        check("not a prompt", len(one.out()), 2)
        check("typing again", chat.typed[-1], True)
        check("the answer stays on the prompt", one.last.text, "clean up")

        one.dispatch(result("all clean"))

        check("output", chat.said[-1], "all clean")
        check("done typing", chat.typed[-1], False)


def test_no_denies():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission())
        one.feed(message("no"))

        response = one.out()[-1]["response"]["response"]

        check("denied", response["behavior"], "deny")
        ok("not allowed", "updatedInput" not in response)
        check("nothing pending", one.asked, None)


def test_anything_else_keeps_waiting():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission())

        for text in ("yes please", "ok", "sure", "what does it do"):
            one.feed(message(text))

            ok("still waiting after %r" % text, one.asked is not None)

        check("nothing went to the child", len(one.out()), 1)
        ok("said so", "waiting for an answer" in chat.said[-1])


def test_only_the_asker_answers():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission())
        one.feed(message("yes", author=0x03))

        ok("still waiting", one.asked is not None)
        check("nothing went to the child", len(one.out()), 1)
        ok("said who", "02" in chat.said[-1])


def test_one_question_at_a_time():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission("r1", "first"))
        one.dispatch(permission("r2", "second"))

        ok("first asked", "first" in chat.said[-1])
        ok("second held", "second" not in chat.said[-1])

        one.feed(message("yes"))

        ok("second asked", "second" in chat.said[-1])
        check("second not answered", one.out()[-1]["response"]["request_id"],
            "r1")

        one.feed(message("no"))

        check("second answered", one.out()[-1]["response"]["request_id"],
            "r2")


def test_yes_to_all():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission("r1", "first"))
        one.dispatch(permission("r2", "second"))
        one.feed(message("Yes  To ALL"))

        answers = [rec["response"] for rec in one.out()[1:]]

        check("the question and the one behind it",
            [a["request_id"] for a in answers], ["r1", "r2"])
        check("both allowed", [a["response"]["behavior"] for a in answers],
            ["allow", "allow"])
        check("second input is its own",
            answers[1]["response"]["updatedInput"]["command"], "second")
        ok("says how to stop", "/ask" in chat.said[-1])
        check("typing again", chat.typed[-1], True)

        before = len(chat.said)

        one.dispatch(permission("r3", "third"))

        check("no more questions", len(chat.said), before)
        check("allowed", one.out()[-1]["response"]["request_id"], "r3")

        one.command(message("/status"), "/status", "")

        ok("status says so", "allowing everything from 02" in chat.said[-1])

        one.dispatch(result("done"))
        one.feed(message("next job"))
        one.dispatch(permission("r4", "fourth"))

        check("lasts past the turn", one.out()[-1]["response"]["request_id"],
            "r4")

        one.command(message("/ask"), "/ask", "")
        one.dispatch(permission("r5", "fifth"))

        ok("/ask asks again", "fifth" in chat.said[-1])
        check("r5 is waiting", one.asked["id"], "r5")


def test_yes_to_all_is_not_for_others():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission("r1"))
        one.feed(message("yes to all", author=0x03))

        check("not from another device", one.trusted, set())
        ok("still waiting", one.asked is not None)

        one.feed(message("yes to all"))
        one.dispatch(result("done"))
        one.feed(message("my turn", author=0x03))
        one.dispatch(permission("r2", "from three"))

        ok("another device still gets asked", one.asked is not None)
        ok("the question shows", "from three" in chat.said[-1])


def test_yes_to_all_ends_with_the_session():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission())
        one.feed(message("yes all"))
        one.fresh(message("/new"))

        check("/new forgets", one.trusted, set())

        two = Quiet(chat, "p02", opts(tmp))

        check("nothing carries over", two.trusted, set())

        with open(one.opts.sessions) as f:
            ok("nothing on disk", "trust" not in f.read())


def test_long_input_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission(command="x" * (agent.ASK_MAX + 100)))

        ok("says what it left out", "100 more bytes not shown" in
            "".join(chat.said))


def test_abort():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission())
        one.command(message("/abort"), "/abort", "")

        request = one.out()[-1]

        check("interrupt", request["request"], {"subtype": "interrupt"})

        one.dispatch({"type": "control_cancel_request", "request_id": "r1"})

        check("question gone", one.asked, None)
        check("says nothing about it", chat.said[-1], "stopping")

        one.dispatch({"type": "control_response", "response": {
            "subtype": "success", "request_id": request["request_id"],
            "response": {"still_queued": []}}})
        one.dispatch({"type": "result", "subtype": "error_during_execution",
            "is_error": True, "result": "", "errors": ["noise"]})

        check("aborted", chat.said[-1], "(aborted)")
        check("idle", one.busy, False)

        one.command(message("/abort"), "/abort", "")

        ok("nothing to stop", "nothing" in chat.said[-1])


def test_failure_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch({"type": "result", "subtype": "error_max_turns",
            "is_error": True, "result": "", "errors": ["too many"]})

        ok("says why", "too many" in chat.said[-1])


def test_steering_shares_a_turn():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.feed(message("and also this"))

        check("goes in", one.out()[-1]["message"]["content"], "and also this")

        one.dispatch(result("both"))
        one.dispatch(result("more"))

        check("the first", chat.said[-2], "both")
        check("a late one still arrives", chat.said[-1], "more")


def test_only_the_final_text_arrives():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "looking"},
            {"type": "tool_use", "name": "Read", "input": {"path": "x"}}]}})
        one.dispatch({"type": "assistant", "parent_tool_use_id": "t",
            "message": {"content": [{"type": "text", "text": "subagent"}]}})
        one.dispatch(result(""))

        check("what it said last", chat.said, ["looking"])


def test_sessions_come_back():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)
        first = one.session_id

        check("a new one", one.starts[0][-2], "--session-id")

        two = Quiet(chat, "p02", opts(tmp))

        check("resumes", two.starts[0][-2:], ["--resume", first])

        other = Quiet(chat, "p03", opts(tmp))

        check("each their own", other.starts[0][-2], "--session-id")

        moved = Quiet(chat, "p02", opts(tmp, dir="/elsewhere"))

        check("each folder its own", moved.starts[0][-2], "--session-id")

        one.fresh(message("/new"))

        check("/new is new", one.starts[-1][-2], "--session-id")
        ok("with a new id", one.starts[-1][-1] != first)


def test_lost_session_starts_over():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)
        two = Quiet(chat, "p02", opts(tmp))

        two.feed(message("again"))
        two.dispatch({"type": "result", "subtype": "error_during_execution",
            "is_error": True, "result": "", "errors": ["No conversation"]})
        two.eof()

        check("said nothing", chat.said, [])
        check("a new one", two.starts[-1][-2], "--session-id")
        check("the prompt is not lost", two.out()[-1]["message"]["content"],
            "again")
        ok("still running", not two.gone)


def test_model():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.command(message("/model sonnet"), "/model", "sonnet")

        request = one.out()[-1]

        check("asks", request["request"], {"subtype": "set_model",
            "model": "sonnet"})

        one.dispatch({"type": "control_response", "response": {
            "subtype": "success", "request_id": request["request_id"],
            "response": {"model": "claude-sonnet"}}})

        ok("says so", "claude-sonnet" in chat.said[-1])

        one.command(message("/status"), "/status", "")

        ok("status shows it", "claude-sonnet" in chat.said[-1])


def test_unknown_control_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch({"type": "control_request", "request_id": "q",
            "request": {"subtype": "something_new"}})

        check("error", one.out()[-1]["response"]["subtype"], "error")


def test_not_retired_while_asking():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch(permission())
        one.busy = False

        ok("kept", not one.expired(one.idle + 100000))

        one.asked = None

        ok("retired", one.expired(one.idle + 100000))


def test_yes_in_a_group_needs_no_mention():
    with tempfile.TemporaryDirectory() as tmp:
        fakekyrka.install(pygospel, fakekyrka.Network())

        lines = ["vigil 1", "mode gospel", "spi 07", "kek " + "11" * 32,
            "cathedral 203.0.113.7:4469", "cathedral_id 5eb2411f",
            "cathedral_flock 493abf95a07e0c00",
            "cathedral_secret " + "22" * 32, "cathedral_cosk " + "33" * 64]

        room = agent.AgentChat("\n".join(lines) + "\n", "pi", opts(tmp,
            backend="claude", group=0, signal_all=False, liturgy=None,
            allow={0x02}, group_all=False))
        quiet = Chat()
        one = Quiet(quiet, "g0007", room.opts)

        room.agents["g0007"] = one

        ask = message("do it @pi", group=0x0007)
        ask.mentions_me = True
        chatter = message("nice weather", group=0x0007)
        chatter.mentions_me = False
        answer = message("yes", group=0x0007)
        answer.mentions_me = False

        room.on_message(ask)
        one.dispatch({"type": "system", "subtype": "init",
            "session_id": one.session_id})
        one.dispatch(permission())

        before = len(quiet.said)

        room.on_message(chatter)

        check("chatter is ignored", len(quiet.said), before)
        ok("still waiting", one.asked is not None)

        room.on_message(answer)

        check("answered", one.out()[-1]["response"]["response"]["behavior"],
            "allow")

        one.dispatch(permission("r2"))

        everything = message("yes to all", group=0x0007)
        everything.mentions_me = False

        room.on_message(everything)

        check("yes to all needs none either", one.trusted, {0x02})

        room.agents.clear()
        room.close()


PNG = b"\x89PNG\r\n\x1a\n" + b"data"


def image_agent(tmp, **kwargs):
    chat = Chat()
    root = os.path.join(tmp, "work")
    os.makedirs(root, exist_ok=True)

    with open(os.path.join(root, "plot.png"), "wb") as f:
        f.write(PNG)

    return Quiet(chat, "p02", opts(tmp, dir=root, **kwargs)), chat, root


def test_image_line():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        one.targets = [message("draw it")]
        one.deliver("here it is\n[image: plot.png]\n  [image: %s]  " %
            os.path.join(root, "plot.png"))

        check("the text without the lines", chat.said, ["here it is"])
        check("both images", chat.images,
            [(0x02, os.path.join(os.path.realpath(root), "plot.png"))] * 2)


def test_image_only():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        one.targets = [message("draw it")]
        one.deliver("[image: plot.png]")

        check("no text", chat.said, [])
        check("the image", len(chat.images), 1)


def test_image_in_a_sentence_is_text():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        one.targets = [message("how")]
        one.deliver("write [image: plot.png] on a line")

        check("left alone", chat.said, ["write [image: plot.png] on a line"])
        check("nothing sent", chat.images, [])


def test_image_stays_in_the_folder():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)
        out = os.path.join(tmp, "out.png")

        with open(out, "wb") as f:
            f.write(PNG)

        os.symlink(out, os.path.join(root, "link.png"))
        os.makedirs(root + "-other")

        for path in (out, "../out.png", "link.png",
            os.path.join(root + "-other", "x.png")):
            one.targets = [message("draw it")]
            chat.said = []
            one.deliver("[image: %s]" % path)

            check("refused " + path, len(chat.said), 1)
            ok("says so " + path, "outside" in chat.said[0])

        check("nothing sent", chat.images, [])


def test_image_failure_is_told():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        def refuse(peer, path):
            raise pygospel.GospelError("not a PNG")

        chat.send_image = refuse
        one.targets = [message("draw it")]
        one.deliver("[image: plot.png]")

        check("the reason", chat.said, ["cannot send plot.png: not a PNG"])


def test_image_in_a_group():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        root = os.path.join(tmp, "work")
        os.makedirs(root)

        one = Quiet(chat, "g0007", opts(tmp, dir=root))
        one.targets = [message("draw it", group=0x0007)]
        one.deliver("[image: plot.png]")

        check("to the group", chat.images,
            [(None, os.path.join(os.path.realpath(root), "plot.png"))])


def test_brief_tells_about_images():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        ok("the syntax", "[image: path]" in one.brief())
        ok("no more of this", "no attachments" not in one.brief())


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)]

    for name, fn in tests:
        start = len(failures)

        try:
            fn()
        except Exception as e:
            import traceback
            failures.append("%s raised %s: %s" % (name, type(e).__name__, e))
            traceback.print_exc()

        print("%-40s %s" % (name, "ok" if len(failures) == start else "FAIL"))

    print()
    print("%d checks, %d failures" % (checks, len(failures)))

    for fail in failures:
        print("  " + fail)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
