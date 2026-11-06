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
import select
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))

import fakekyrka  # noqa: E402
import fakeopus  # noqa: E402

sys.modules["libkyrka"] = fakekyrka.Module(fakekyrka.Network())

import agent  # noqa: E402
import pygospel  # noqa: E402

agent.log = lambda text: None

CONFIG = """\
vigil 1
instance device07
mode gospel
spi 07
kek %s
cathedral 203.0.113.7:4469
cathedral_id 5eb2411f
cathedral_flock 493abf95a07e0c00
cathedral_secret %s
cathedral_cosk %s
""" % ("11" * 32, "22" * 32, "33" * 64)

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
    group = 0x0007

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
        max_out=4096, progress=False, stats=0, transcribe=None, render=None,
        hear=None, speak=None, signal_all=False, liturgy=None, group=None,
        inbox=os.path.join(tmp, "inbox"),
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


class QuietPi(agent.PiAgent):
    """A PiAgent writing to a buffer rather than to a child."""

    def start(self):
        self.proc = Proc()


def said(role, text):
    return {"role": role, "content": [{"type": "text", "text": text}]}


def test_pi_answer_keeps_text_before_a_tool():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        one = QuietPi(chat, "p02", opts(tmp))

        one.feed(message("what did you find"))
        one.dispatch({"type": "message_end",
            "message": said("assistant", "1. the first")})
        one.dispatch({"type": "message_end",
            "message": said("toolResult", "not for the chat")})
        one.dispatch({"type": "message_end",
            "message": said("assistant", "2. the second")})
        one.dispatch({"type": "agent_settled"})

        check("all of it", chat.said, ["1. the first\n\n2. the second"])


class Quoting(Chat):
    """A chat that also keeps which message each reply is quoted onto."""

    def __init__(self):
        super().__init__()
        self.quoted = []

    def reply(self, peer, text, msg):
        self.quoted.append((msg.text, text))
        super().reply(peer, text, msg)

    def reply_group(self, text, msg):
        self.quoted.append((msg.text, text))
        super().reply_group(text, msg)


def quiet_pi(tmp, key="p02", chat=None):
    chat = chat or Quoting()

    return chat, QuietPi(chat, key, opts(tmp))


def prompts(one):
    lines = one.proc.stdin.getvalue().splitlines()

    return [rec["message"] for rec in map(json.loads, lines)
        if rec.get("type") == "prompt"]


def pi_says(one, text):
    one.dispatch({"type": "message_end", "message": said("assistant", text)})


def test_pi_answers_each_message_in_turn():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("job one"))
        pi_says(one, "working on one")
        one.feed(message("job two"))
        one.feed(message("job three"))

        check("one at a time", prompts(one), ["job one"])
        for line in one.proc.stdin.getvalue().splitlines():
            ok("no steering", "streamingBehavior" not in json.loads(line))

        one.dispatch({"type": "agent_settled"})
        check("the second goes in", prompts(one), ["job one", "job two"])

        pi_says(one, "two is done")
        one.dispatch({"type": "agent_settled"})
        pi_says(one, "three is done")
        one.dispatch({"type": "agent_settled"})

        check("each on its own prompt", chat.quoted, [
            ("job one", "working on one"), ("job two", "two is done"),
            ("job three", "three is done")])
        ok("idle", not one.busy)


def test_pi_handled_prompt_does_not_block_the_queue():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("/ext thing"))
        one.feed(message("job two"))

        first = json.loads(one.proc.stdin.getvalue().splitlines()[0])

        one.dispatch({"type": "response", "id": first["id"],
            "command": "prompt", "success": True,
            "data": {"disposition": "handled"}})

        check("the next one goes in", prompts(one), ["/ext thing", "job two"])
        check("its turn", one.running.text, "job two")


def test_pi_refused_prompt_does_not_block_the_queue():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("job one"))
        one.feed(message("job two"))

        first = json.loads(one.proc.stdin.getvalue().splitlines()[0])

        one.dispatch({"type": "response", "id": first["id"],
            "command": "prompt", "success": False, "error": "no model"})

        ok("says why", "no model" in chat.said[-1])
        check("the next one goes in", one.running.text, "job two")


def test_abort_drops_what_waits():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("job one"))
        one.feed(message("job two"))
        one.abort(message("/abort"))

        ok("says so", "dropping 1 message" in chat.said[-1])

        one.dispatch({"type": "agent_settled", "aborted": True})

        check("nothing else went in", prompts(one), ["job one"])
        ok("idle", not one.busy)


def test_agent_going_away_says_what_it_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("job one"))
        one.feed(message("job two"))
        one.eof()

        ok("says so", "dropping 1 message" in chat.said[-1])
        check("nothing left", one.waiting, [])


def dialog(id="d1", method="confirm", **kwargs):
    rec = {"type": "extension_ui_request", "id": id, "method": method,
        "title": "run %s?" % id}
    rec.update(kwargs)

    return rec


def ui_responses(one):
    lines = one.proc.stdin.getvalue().splitlines()

    return [rec for rec in map(json.loads, lines)
        if rec.get("type") == "extension_ui_response"]


def test_pi_only_the_asker_answers_a_dialog():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp, "g0007")

        one.feed(message("job one", author=0x02, group=0x0007))
        one.dispatch(dialog())
        one.feed(message("what is the weather", author=0x03, group=0x0007))

        ok("told", "only 02 can answer" in chat.said[-1])
        check("still asked", ui_responses(one), [])
        ok("another is not awaited",
            not one.awaits(message("yes", author=0x03, group=0x0007)))
        ok("the asker is awaited",
            one.awaits(message("yes", author=0x02, group=0x0007)))

        one.feed(message("yes", author=0x02, group=0x0007))

        check("answered", ui_responses(one)[0]["confirmed"], True)


def test_pi_a_question_belongs_to_the_turn():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp, "g0007")

        one.feed(message("job one", author=0x02, group=0x0007))
        one.dispatch(dialog())

        check("from who started it", one.dialog["from"], 0x02)


def test_pi_dialogs_stack():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("job one"))
        one.dispatch(dialog("d1"))
        one.dispatch(dialog("d2"))

        ok("the first", "run d1?" in chat.said[-1])
        check("one question out", sum("run d" in x for x in chat.said), 1)

        one.feed(message("yes"))

        ok("the second", "run d2?" in chat.said[-1])

        one.feed(message("no"))

        check("both answered", [(r["id"], r["confirmed"])
            for r in ui_responses(one)], [("d1", True), ("d2", False)])


def test_pi_abort_lets_go_of_dialogs():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("job one"))
        one.dispatch(dialog("d1"))
        one.dispatch(dialog("d2"))
        one.abort(message("/abort"))

        check("both cancelled", [(r["id"], r["cancelled"])
            for r in ui_responses(one)], [("d1", True), ("d2", True)])


def test_pi_dialog_that_timed_out_takes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = quiet_pi(tmp)

        one.feed(message("job one"))
        one.dispatch(dialog("d1", timeout=1000))
        one.dialog["until"] = agent.time.monotonic() - 1
        one.feed(message("job two"))

        check("not an answer", ui_responses(one), [])
        check("it waits its turn", [m.text for m in one.waiting], ["job two"])


def test_claude_question_belongs_to_the_turn():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        one = Quiet(chat, "g0007", opts(tmp))

        one.feed(message("job one", author=0x02, group=0x0007))
        one.dispatch({"type": "system", "subtype": "init", "model": "m1",
            "session_id": one.session_id})
        one.feed(message("job two", author=0x03, group=0x0007))
        one.dispatch(permission())

        check("from who started it", one.asked["from"], 0x02)


def test_claude_trust_is_for_the_turn():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        one = Quiet(chat, "g0007", opts(tmp))

        one.trusted.add(0x03)
        one.feed(message("job one", author=0x02, group=0x0007))
        one.dispatch({"type": "system", "subtype": "init", "model": "m1",
            "session_id": one.session_id})
        one.feed(message("job two", author=0x03, group=0x0007))
        one.dispatch(permission())

        ok("not allowed on 03's word", one.asked is not None)


class Empty(Chat):
    """A group nobody is around in, until somebody is."""

    def __init__(self):
        super().__init__()
        self.around = False

    def send_group(self, text):
        if not self.around:
            raise pygospel.GospelError("no member of the group to send to")

        super().send_group(text)

    def reply_group(self, text, msg):
        if not self.around:
            raise pygospel.GospelError("no member of the group to send to")

        super().reply_group(text, msg)


def test_group_answer_waits_for_somebody():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Empty()
        one = QuietPi(chat, "g0007", opts(tmp))
        msg = message("job one", group=0x0007)

        one.feed(msg)
        one.running = msg
        one.deliver("the answer")
        one.tell(msg, "and a note")

        check("nothing yet", chat.said, [])
        ok("kept around", not one.expired(one.idle + 100000))

        one.flush(agent.time.monotonic())
        check("still nothing", chat.said, [])

        chat.around = True
        one.flush(agent.time.monotonic())

        check("in order", chat.said, ["the answer", "and a note"])
        check("all of it", one.unsent, [])


def test_group_answer_gives_up_in_the_end():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Empty()
        one = QuietPi(chat, "g0007", opts(tmp))
        msg = message("job one", group=0x0007)

        one.tell(msg, "a note")
        chat.around = True
        one.flush(agent.time.monotonic() + agent.UNSENT_MAX + 1)

        check("dropped", chat.said, [])
        check("nothing kept", one.unsent, [])


def test_answer_stays_out_of_another_group():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        one = QuietPi(chat, "g0009", opts(tmp))

        one.tell(message("job one", group=0x0009), "for the old group")

        check("not said", chat.said, [])
        check("not kept", one.unsent, [])


def test_one_to_one_answer_is_not_held():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        one = QuietPi(chat, "p02", opts(tmp))

        def refuse(peer, text):
            raise pygospel.GospelError("no")

        chat.send = refuse
        one.tell(message("job one"), "a note")

        check("nothing kept", one.unsent, [])


def test_pi_answer_does_not_carry_over():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        one = QuietPi(chat, "p02", opts(tmp))

        one.feed(message("job one"))
        one.dispatch({"type": "message_end",
            "message": said("assistant", "one is done")})
        one.dispatch({"type": "agent_settled"})
        one.feed(message("job two"))
        one.dispatch({"type": "message_end",
            "message": said("assistant", "two is done")})
        one.dispatch({"type": "agent_settled"})

        check("one answer each", chat.said, ["one is done", "two is done"])


def test_claude_answer_keeps_text_before_a_tool():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch({"type": "assistant",
            "message": said("assistant", "1. the first")})
        one.dispatch({"type": "assistant",
            "message": said("assistant", "2. the second")})
        one.dispatch(result("2. the second"))

        check("all of it, the result once", chat.said,
            ["1. the first\n\n2. the second"])


def test_claude_result_says_what_messages_did_not():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.dispatch({"type": "assistant",
            "message": said("assistant", "working on it")})
        one.dispatch(result("the limit was hit"))

        check("both", chat.said, ["working on it\n\nthe limit was hit"])


def out_of(one):
    return [json.loads(x) for x in one.proc.stdin.getvalue().splitlines()]


def stats_pi(tmp):
    chat = Chat()
    one = QuietPi(chat, "p02", opts(tmp, stats=60))

    one.feed(message("job one"))

    return chat, one


def stats_asked(one):
    return [rec for rec in out_of(one)
        if rec.get("type") == "get_session_stats"]


def test_span_and_short():
    check("seconds", agent.span(45), "45s")
    check("minutes", agent.span(192), "3m12s")
    check("hours", agent.span(3840), "1h04m")
    check("few", agent.short(812), "812")
    check("thousands", agent.short(52400), "52k")
    check("millions", agent.short(1234567), "1.2M")


def test_pi_stats_come_when_due():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.check_stats(one.started + 59)
        check("not yet", stats_asked(one), [])

        one.dispatch({"type": "tool_execution_start", "toolName": "bash",
            "args": {"command": "make"}})
        one.check_stats(one.started + 61)
        check("asked", len(stats_asked(one)), 1)
        one.check_stats(one.started + 62)
        check("once", len(stats_asked(one)), 1)

        one.dispatch({"type": "response", "command": "get_session_stats",
            "id": stats_asked(one)[0]["id"], "success": True, "data": {
            "tokens": {"total": 105000}, "cost": 0.45,
            "contextUsage": {"tokens": 60000, "percent": 30}}})

        ok("the time", chat.said[-1].startswith("working "))
        ok("the tools", "1 tool, last bash" in chat.said[-1])
        ok("the context", "context 30%" in chat.said[-1])
        ok("the tokens", "105k tokens this session" in chat.said[-1])
        ok("the cost", "$0.45" in chat.said[-1])


def test_pi_stats_leave_out_what_it_does_not_know():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.check_stats(one.started + 61)
        one.dispatch({"type": "response", "command": "get_session_stats",
            "id": stats_asked(one)[0]["id"], "success": True, "data": {
            "tokens": {"total": 0}, "cost": 0,
            "contextUsage": {"tokens": None, "percent": None}}})

        check("only the time", chat.said[-1].split(" ")[0], "working")
        check("nothing else", chat.said[-1].count(","), 0)


def test_pi_stats_failing_is_no_complaint():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.check_stats(one.started + 61)
        one.dispatch({"type": "response", "command": "get_session_stats",
            "id": stats_asked(one)[0]["id"], "success": False, "error": "no"})

        check("still a line", len(chat.said), 1)
        ok("not an error", chat.said[0].startswith("working "))
        ok("not stuck", not one.stats_asked)


def test_pi_stats_when_pi_does_not_answer():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.check_stats(one.started + 61)
        check("nothing yet", chat.said, [])

        one.check_stats(one.started + 122)
        ok("says so", "pi is not answering" in chat.said[-1])
        check("asked once", len(stats_asked(one)), 1)


def test_stats_after_the_turn_are_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.check_stats(one.started + 61)
        pi_says(one, "done")
        one.dispatch({"type": "agent_settled"})
        chat.said.clear()

        one.dispatch({"type": "response", "command": "get_session_stats",
            "id": stats_asked(one)[0]["id"], "success": True, "data": {}})

        check("nothing said", chat.said, [])

        one.check_stats(one.started + 1000)
        check("nothing asked", len(stats_asked(one)), 1)


def test_stats_are_quiet_during_a_question():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.dispatch(dialog())
        one.check_stats(one.started + 61)

        check("not asked", stats_asked(one), [])


def test_stats_can_be_off():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        one = QuietPi(chat, "p02", opts(tmp, stats=0))

        one.feed(message("job one"))
        one.check_stats(one.started + 100000)

        check("not asked", stats_asked(one), [])


def test_stats_start_over_each_turn():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.dispatch({"type": "tool_execution_start", "toolName": "bash"})
        pi_says(one, "done")
        one.dispatch({"type": "agent_settled"})
        one.feed(message("job two"))

        check("no tools yet", one.tools, 0)
        check("the time too", one.stats_at, one.started)


def test_stats_show_what_waits():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = stats_pi(tmp)

        one.feed(message("job two"))
        one.check_stats(one.started + 61)
        one.dispatch({"type": "response", "command": "get_session_stats",
            "id": stats_asked(one)[0]["id"], "success": True, "data": {}})

        ok("the queue", chat.said[-1].endswith(", 1 waiting"))


def test_claude_stats_add_up_the_usage():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.opts.stats = 60

        def usage(id, output, cached):
            return {"type": "assistant", "message": {"id": id, "content": [],
                "usage": {"input_tokens": 2, "output_tokens": output,
                "cache_read_input_tokens": cached,
                "cache_creation_input_tokens": 1000}}}

        one.dispatch(usage("m1", 100, 10000))
        one.dispatch(usage("m1", 100, 10000))
        one.dispatch(usage("m2", 1500, 40000))
        one.dispatch({"type": "assistant", "message": {"id": "m3", "content": [
            {"type": "tool_use", "name": "Read", "input": {"path": "x"}}]}})

        one.check_stats(one.started + 61)

        ok("the last context", "context 41k tokens" in chat.said[-1])
        ok("each message once", "1k written" in chat.said[-1])
        ok("the tools", "1 tool, last Read" in chat.said[-1])

        one.dispatch(result("done"))
        one.feed(message("job two"))

        check("a new turn starts over", one.usages, {})


def test_claude_stats_without_usage():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.opts.stats = 60
        one.check_stats(one.started + 61)

        check("only the time", chat.said[-1].count(","), 0)


def test_claude_stats_are_quiet_during_a_question():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.opts.stats = 60
        one.dispatch(permission())
        said = len(chat.said)
        one.check_stats(one.started + 61)

        check("nothing said", len(chat.said), said)


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


def test_a_message_waits_for_its_turn():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one = running(tmp)

        one.feed(message("and also this"))

        check("not yet", len(one.out()), 1)
        check("says so", chat.said, ["message queued (1/1 pending)"])

        one.feed(message("and this"))

        check("counts", chat.said[-1], "message queued (2/2 pending)")

        chat.said.clear()
        one.dispatch(result("first"))

        check("the first answer", chat.said, ["first"])
        check("now it goes in", one.out()[-1]["message"]["content"],
            "and also this")
        check("its turn", one.running.text, "and also this")
        ok("still busy", one.busy)

        one.feed(message("and one more"))

        check("counts what is left", chat.said[-1],
            "message queued (2/2 pending)")

        chat.said.clear()
        one.dispatch(result("second"))
        one.dispatch(result("third"))
        one.dispatch(result("fourth"))
        one.dispatch(result("more"))

        check("a late one still arrives", chat.said, ["second", "third",
            "fourth", "more"])
        ok("idle", not one.busy)


def test_a_subagent_stays_out_of_the_answer():
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

        one.running = message("draw it")
        one.deliver("here it is\n[image: plot.png]\n  [image: %s]  " %
            os.path.join(root, "plot.png"))

        check("the text without the lines", chat.said, ["here it is"])
        check("both images", chat.images,
            [(0x02, os.path.join(os.path.realpath(root), "plot.png"))] * 2)


def test_image_only():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        one.running = message("draw it")
        one.deliver("[image: plot.png]")

        check("no text", chat.said, [])
        check("the image", len(chat.images), 1)


def test_image_in_a_sentence_is_text():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        one.running = message("how")
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
            one.running = message("draw it")
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
        one.running = message("draw it")
        one.deliver("[image: plot.png]")

        check("the reason", chat.said, ["cannot send plot.png: not a PNG"])


def test_image_in_a_group():
    with tempfile.TemporaryDirectory() as tmp:
        chat = Chat()
        root = os.path.join(tmp, "work")
        os.makedirs(root)

        one = Quiet(chat, "g0007", opts(tmp, dir=root))
        one.running = message("draw it", group=0x0007)
        one.deliver("[image: plot.png]")

        check("to the group", chat.images,
            [(None, os.path.join(os.path.realpath(root), "plot.png"))])


def test_brief_tells_about_images():
    with tempfile.TemporaryDirectory() as tmp:
        one, chat, root = image_agent(tmp)

        ok("the syntax", "[image: path]" in one.brief())
        ok("no more of this", "no attachments" not in one.brief())


def room(tmp, **kwargs):
    fakekyrka.install(pygospel, fakekyrka.Network())

    fields = dict(group=0, signal_all=True, liturgy=None, allow={0x02},
        group_all=False, backend="claude")
    fields.update(kwargs)

    chat = agent.AgentChat(CONFIG, "bot", opts(tmp, **fields))
    chat.said = []
    chat.fed = []
    chat.say = lambda msg, text: chat.said.append(text)
    chat.on_message = lambda msg: chat.fed.append(msg.text)
    chat.add_reader = lambda fd, callback: None
    chat.remove_reader = lambda fd: None

    return chat


def blob(media, name="note", data=b"audio", author=0x02, group=None):
    msg = message("", author=author, group=group)
    msg.media = media
    msg.name = name
    msg.data = data
    msg.mentions_me = False

    return msg


def wait_voice(chat):
    for _ in range(500):
        if chat.voices.proc is None and not chat.voices.queue:
            return

        fd = chat.voices.fd

        if select.select([fd], [], [], 0.05)[0]:
            chat.voices.on_output(fd)


def script(tmp, body):
    path = os.path.join(tmp, "script.sh")

    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")

    os.chmod(path, 0o700)

    return path


class Line:
    """A child of a call, as far as a Voice can tell."""

    def __init__(self):
        self.wrote = []
        self.gone = False
        self.closed = False

    def write(self, data, droppable=False):
        self.wrote.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class Phone:
    """A pygospel.Call, as far as a Voice can tell."""

    def __init__(self, state="ringing"):
        self.state = state
        self.peer = types.SimpleNamespace(id=0x02)
        self.queued = 0
        self.audio = []
        self.hung_up = 0

    def answer(self):
        self.state = "talking"

    def hangup(self):
        self.hung_up += 1

    def send_audio(self, pcm):
        self.audio.append(pcm)
        self.queued += len(pcm) * 20 // 1920

    def clear_audio(self):
        self.audio = []
        self.queued = 0


class CallChat:
    spi = 0x07
    group = 0
    working_sound = b"tick"

    def __init__(self):
        self.prompts = []
        self.said = []
        self.conv = types.SimpleNamespace(voice=None, busy=False,
            running=None)

    def conversation(self, msg):
        return self.conv

    def on_message(self, msg):
        self.prompts.append(msg.text)

    def send(self, peer, text):
        self.said.append(text)


def line_up(state="talking"):
    """A Voice with both of its children loaded."""

    chat = CallChat()
    call = Phone(state)
    voice = agent.Voice(chat, call, None, Line(), Line())

    voice.on_heard(b'{"ready": true}\n')
    voice.on_spoken(agent.VOICE_HEAD.pack(0, 0))

    return chat, call, voice


def spoken(voice):
    return [json.loads(line)["text"] for line in voice.speak.wrote]


def say_out(voice, ms=1000):
    """The child that speaks gets through what it was asked to say."""

    while voice.saying is not None:
        id = voice.saying[0]
        voice.on_spoken(agent.VOICE_HEAD.pack(id, ms * 96) + bytes(ms * 96) +
            agent.VOICE_HEAD.pack(id, 0))


def test_speakable():
    check("markdown goes", agent.speakable("**Done**, see `x`"), "Done, see x")
    check("a list marker goes", agent.speakable("- one thing"), "one thing")
    check("a number too", agent.speakable("2. two things"), "two things")
    check("a heading too", agent.speakable("## Results"), "Results")
    check("a link is its words", agent.speakable("[the docs](http://a.b/c)"),
        "the docs")
    check("a bare url", agent.speakable("at http://a.b/c ok"), "at a link ok")
    check("an image is not said", agent.speakable("[image: cat.png]\n"), "")
    check("nothing to say", agent.speakable("---"), "")


def test_call_answers_once_both_are_loaded():
    chat = CallChat()
    call = Phone()
    voice = agent.Voice(chat, call, None, Line(), Line())

    voice.on_heard(b'{"ready": true}\n')
    check("still loading the other", call.state, "ringing")

    voice.on_spoken(agent.VOICE_HEAD.pack(0, 0))
    check("picks up", call.state, "talking")


def test_call_loading_too_long_hangs_up():
    chat = CallChat()
    call = Phone()
    voice = agent.Voice(chat, call, None, Line(), Line())

    voice.tick(voice.deadline - 1)
    check("still ringing", call.hung_up, 0)

    voice.tick(voice.deadline + 1)
    check("hung up", call.hung_up, 1)
    ok("and said why", "load" in chat.said[0])

    voice.tick(voice.deadline + 2)
    check("only once", (call.hung_up, len(chat.said)), (1, 1))


def test_call_child_dying_hangs_up():
    chat, call, voice = line_up()

    voice.hear.gone = True

    for _ in range(3):
        voice.tick(1)

    check("hung up once", call.hung_up, 1)
    check("told once", len(chat.said), 1)
    ok("named it", "listener" in chat.said[0])


def test_call_words_become_a_marked_prompt():
    chat, call, voice = line_up()

    voice.on_heard(b'{"speech": true}\n{"text": " how is it going "}\n')

    check("one prompt", len(chat.prompts), 1)
    ok("marked", chat.prompts[0].startswith(agent.TRANSCRIPTS))
    ok("has the words", chat.prompts[0].endswith("how is it going"))
    ok("told it is a call", "short plain sentences" in chat.prompts[0])
    check("hooked to the agent", chat.conv.voice, voice)
    check("listening stops", voice.listening, False)

    voice.on_heard(b'{"text": ""}\n{"text": " ... "}\n')
    check("noise is no prompt", len(chat.prompts), 1)


def test_call_prompt_quotes_nothing():
    chat = CallChat()
    seen = []
    chat.on_message = seen.append
    voice = agent.Voice(chat, Phone(), None, Line(), Line())

    voice.on_heard(b'{"text": "hello"}\n')

    check("no hash to quote", seen[0].hash, 0)
    check("from the peer", (seen[0].peer, seen[0].author), (2, 2))
    check("not a group", seen[0].group, None)


def test_call_sentences_are_said_one_at_a_time():
    chat, call, voice = line_up()

    voice.add("First one. Sec")
    check("whole sentences only", spoken(voice), ["First one."])

    voice.add("ond one! And third")
    check("waits its turn", spoken(voice), ["First one."])

    say_out(voice)
    check("then the next", spoken(voice), ["First one.", "Second one!"])

    say_out(voice)
    voice.finish_turn()
    check("the end is a sentence too", spoken(voice)[-1], "And third")
    say_out(voice)
    check("audio reached the call", len(call.audio), 3)


def test_call_code_is_not_read_out():
    chat, call, voice = line_up()

    voice.add("Here it is:\n```python\nprint('secret')\n")
    voice.add("x = 1.\nmore()\n```\nThat is all.\n")

    for _ in range(5):
        say_out(voice)

    voice.finish_turn()
    say_out(voice)

    said = " ".join(spoken(voice))

    ok("no code", "print" not in said and "x = 1" not in said)
    ok("says where it is", agent.VOICE_CODE in said)
    ok("carries on after", "That is all." in said)


def test_call_long_answer_is_cut_short():
    chat, call, voice = line_up()

    for n in range(100):
        voice.add("Sentence number %d is here to take up some room. " % n)
        say_out(voice)

    said = spoken(voice)

    ok("well short of all of it", len(said) < 40)
    check("says so at the end", said[-1], agent.VOICE_REST)

    voice.finish_turn()
    voice.add("A new turn. ")
    say_out(voice)
    check("the next turn gets to talk", spoken(voice)[-1], "A new turn.")


def test_call_talking_over_stops_it():
    chat, call, voice = line_up()
    voice.turns.append(chat.conv)
    chat.conv.busy = True
    chat.conv.running = chat.conv
    voice.agent = chat.conv

    voice.add("One is heard. ")
    say_out(voice, ms=1000)
    voice.add("Two is not. ")
    say_out(voice, ms=1000)

    call.queued = 1000

    voice.on_heard(b'{"speech": true}\n')

    check("audio is gone", call.queued, 0)
    check("nothing else is said", voice.lines, [])

    before = len(spoken(voice))

    voice.add("This streams in afterwards. ")
    check("the rest of this turn stays quiet", len(spoken(voice)), before)

    voice.on_heard(b'{"text": "hold on"}\n')

    ok("the agent knows what was heard",
        'heard you up to: "One is heard."' in chat.prompts[0])
    ok("still marked", chat.prompts[0].startswith(agent.TRANSCRIPTS))
    ok("and what they said", chat.prompts[0].endswith("hold on"))

    voice.finish_turn()
    voice.add("The next answer. ")
    check("the next turn talks again", spoken(voice)[-1], "The next answer.")


def test_call_interrupted_after_turn_still_talks_next():
    chat, call, voice = line_up()

    voice.add("Done now. ")
    say_out(voice)
    call.queued = 500

    voice.on_heard(b'{"speech": true}\n{"text": "thanks"}\n')

    voice.add("You are welcome. ")
    check("not left hushed", spoken(voice)[-1], "You are welcome.")


def test_call_stale_audio_is_not_played():
    chat, call, voice = line_up()

    voice.add("Old sentence. ")

    old = voice.saying[0]

    voice.on_heard(b'{"speech": true}\n')
    voice.on_spoken(agent.VOICE_HEAD.pack(old, 4) + b"late" +
        agent.VOICE_HEAD.pack(old, 0))

    check("dropped", call.audio, [])


def test_call_split_chunks_reassemble():
    chat, call, voice = line_up()

    voice.add("Split up. ")

    id = voice.saying[0]
    data = agent.VOICE_HEAD.pack(id, 4) + b"abcd" + \
        agent.VOICE_HEAD.pack(id, 0)

    for n in range(len(data)):
        voice.on_spoken(data[n:n + 1])

    check("whole", call.audio[0], b"abcd")
    check("done", voice.saying, None)


def test_call_sentence_ends_on_a_whole_frame():
    chat, call, voice = line_up()

    voice.add("Short. ")

    id = voice.saying[0]

    voice.on_spoken(agent.VOICE_HEAD.pack(id, 1000) + bytes(1000) +
        agent.VOICE_HEAD.pack(id, 0))

    check("padded to a frame", sum(len(pcm) for pcm in call.audio), 1920)

    call.queued = 0
    voice.on_heard(b'{"speech": true}\n{"text": "a question"}\n')

    ok("nothing was cut off", "cut off" not in chat.prompts[0])


def test_call_working_sound():
    chat, call, voice = line_up()

    voice.tick(100)
    check("quiet when nothing runs", call.audio, [])

    voice.turns.append(chat.conv)
    voice.agent = chat.conv
    chat.conv.busy = True
    chat.conv.running = chat.conv

    voice.tick(100)
    check("a sound while it works", call.audio, [b"tick"])

    call.queued = 0
    voice.tick(101)
    check("not all the time", len(call.audio), 1)

    voice.tick(100 + agent.WORKING_EVERY)
    check("every so often", len(call.audio), 2)

    voice.listening = True
    call.queued = 0
    voice.tick(100 + agent.WORKING_EVERY * 2)
    check("not over them", len(call.audio), 2)


def voiced(backend, tmp):
    """An agent of the given backend whose turn came from a call."""

    chat = Chat()
    one = backend(chat, "p02", opts(tmp))

    voice = types.SimpleNamespace(added=[], notes=[], finished=0)
    voice.owns = lambda msg: msg is not None and msg.text.startswith("(voice")
    voice.add = voice.added.append
    voice.note = voice.notes.append

    def finish_turn():
        voice.finished += 1

    voice.finish_turn = finish_turn
    one.voice = voice

    return chat, one, voice


class QuietPi(agent.PiAgent):
    def start(self):
        self.proc = Proc()


def test_call_streams_from_claude():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one, voice = voiced(Quiet, tmp)

        ok("asks for partial messages",
            "--include-partial-messages" in one.starts[0])

        one.dispatch({"type": "system", "subtype": "init", "session_id": "s"})
        one.begin(message("(voice call, transcribed, x) hi"))

        one.dispatch({"type": "stream_event", "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hel"}}})
        one.dispatch({"type": "stream_event", "event": {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "hmm"}}})
        one.dispatch({"type": "stream_event", "parent_tool_use_id": "t",
            "event": {"type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "subagent"}}})
        one.dispatch({"type": "stream_event", "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "lo."}}})

        check("spoken as it comes", voice.added, ["Hel", "lo."])

        one.dispatch({"type": "assistant", "message": {"id": "m", "content": [
            {"type": "text", "text": "Hello."}]}})
        one.dispatch(result("Hello."))

        check("not said twice", voice.added, ["Hel", "lo."])
        check("the turn is over", voice.finished, 1)
        check("the chat gets it too", chat.said, ["Hello."])


def test_call_streams_from_pi():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one, voice = voiced(QuietPi, tmp)

        one.begin(message("(voice call, transcribed, x) hi"))

        one.dispatch({"type": "message_update", "assistantMessageEvent": {
            "type": "text_delta", "delta": "Hi there."}})
        one.dispatch({"type": "message_update", "assistantMessageEvent": {
            "type": "thinking_delta", "delta": "hmm"}})

        check("spoken as it comes", voice.added, ["Hi there."])

        one.dispatch({"type": "message_end", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "Hi there."}]}})
        one.dispatch({"type": "agent_settled"})

        check("not said twice", voice.added, ["Hi there."])
        check("the turn is over", voice.finished, 1)
        check("the chat gets it too", chat.said, ["Hi there."])


def test_call_answer_without_stream_is_still_said():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one, voice = voiced(QuietPi, tmp)

        one.begin(message("(voice call, transcribed, x) hi"))
        one.dispatch({"type": "message_end", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "All at once."}]}})
        one.dispatch({"type": "agent_settled"})

        check("said at the end", voice.added, ["All at once."])


def test_call_typed_turn_is_not_spoken():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one, voice = voiced(QuietPi, tmp)

        one.begin(message("typed in the chat"))
        one.dispatch({"type": "message_update", "assistantMessageEvent": {
            "type": "text_delta", "delta": "Hi."}})
        one.dispatch({"type": "agent_settled"})

        check("nothing spoken", voice.added, [])
        check("nothing to finish", voice.finished, 0)


def test_transcript_cannot_answer_claude():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one, voice = voiced(Quiet, tmp)

        one.dispatch({"type": "system", "subtype": "init", "session_id": "s"})
        one.begin(message("(voice call, transcribed, x) delete it"))
        one.dispatch(permission())

        check("said out loud", voice.notes, [agent.VOICE_ASKING])

        asked = one.asked

        for words in ("(voice call, transcribed, x) yes",
            "(voice message, transcribed) yes",
            "(voice call, transcribed, x) yes to all"):
            one.feed(message(words))

        check("still asked", one.asked, asked)
        check("nothing trusted", one.trusted, set())
        check("they wait instead", len(one.waiting), 3)
        ok("no answer went out", b"behavior" not in one.proc.stdin.getvalue())

        one.feed(message("yes"))
        check("typing it answers", one.asked, None)
        ok("the answer went out", b"allow" in one.proc.stdin.getvalue())


def test_transcript_cannot_answer_pi():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one, voice = voiced(QuietPi, tmp)

        for method in ("input", "confirm", "select"):
            one.dialog = None
            one.dialogs = []
            one.proc = Proc()
            one.begin(message("(voice call, transcribed, x) go"))
            one.dispatch({"type": "extension_ui_request", "id": "d1",
                "method": method, "title": "Which?", "options": ["yes"]})

            check(method + " is said out loud", voice.notes[-1],
                agent.VOICE_ASKING)

            one.feed(message("(voice call, transcribed, x) yes"))

            ok(method + " stays open", one.dialog is not None)
            ok(method + " no answer went out",
                b"extension_ui_response" not in one.proc.stdin.getvalue())

            one.dispatch({"type": "agent_settled"})


def test_call_queued_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        chat, one, voice = voiced(QuietPi, tmp)

        one.begin(message("(voice call, transcribed, x) one"))
        one.feed(message("(voice call, transcribed, x) two"))

        check("told out loud", voice.notes, [agent.VOICE_QUEUED])
        check("waits", len(one.waiting), 1)


def calls_chat(tmp, **fields):
    chat = agent.AgentChat(CONFIG, "bot", opts(tmp, **fields))
    chat.add_reader = lambda fd, callback: None
    chat.remove_reader = lambda fd: None

    return chat


def test_call_only_from_allowed():
    pygospel.opuslib = fakeopus

    with tempfile.TemporaryDirectory() as tmp:
        chat = calls_chat(tmp, hear="x", speak="x", allow={0x02})
        made = []
        real = agent.Voice
        agent.Voice = lambda *args: made.append(args) or Line()

        try:
            stranger = Phone()
            stranger.peer.id = 0x09
            chat.on_call_ring(stranger)

            check("declined", stranger.hung_up, 1)
            check("no models loaded for that", made, [])

            friend = Phone()
            chat.on_call_ring(friend)

            check("not declined", friend.hung_up, 0)
            check("models loading", len(made), 1)
        finally:
            agent.Voice = real
            chat.voice = None
            chat.close()


def test_call_takes_no_calls_without_speech():
    with tempfile.TemporaryDirectory() as tmp:
        chat = calls_chat(tmp)

        check("no call liturgy", chat.call_signaling, None)
        check("no calls", chat.take_calls, False)

        chat.close()


def test_call_cannot_start_speech():
    pygospel.opuslib = fakeopus

    with tempfile.TemporaryDirectory() as tmp:
        chat = calls_chat(tmp, hear="/nonexistent/hear",
            speak="/nonexistent/speak", allow=None)
        call = Phone()

        chat.on_call_ring(call)

        check("hung up", call.hung_up, 1)
        check("nothing left", chat.voice, None)

        chat.close()


def test_voice_becomes_a_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        seen = os.path.join(tmp, "seen")
        chat = room(tmp, transcribe=script(tmp,
            'cat "$1" > %s; ls -ld "$1" > %s.mode; echo "  draw a cat "'
            % (seen, seen)))

        chat.on_data(blob("voice", data=b"the audio"))
        wait_voice(chat)

        check("the prompt", chat.fed,
            ["(voice message, transcribed) draw a cat"])

        with open(seen, "rb") as f:
            check("it got the audio", f.read(), b"the audio")

        with open(seen + ".mode") as f:
            ok("only ours", f.read().startswith("-rw-------"))

        check("the audio is gone", chat.voices.dir, None)
        chat.close()


def test_voice_cannot_run_commands_or_answer():
    with tempfile.TemporaryDirectory() as tmp:
        for words in ("/new", "yes to all"):
            chat = room(tmp, transcribe=script(tmp, "echo '%s'" % words))

            chat.on_data(blob("voice"))
            wait_voice(chat)

            ok("not bare: " + words,
                chat.fed[0] != words and chat.fed[0].endswith(words))
            ok("no slash first", not chat.fed[0].startswith("/"))
            chat.close()


def test_voice_failures_are_told():
    with tempfile.TemporaryDirectory() as tmp:
        chat = room(tmp)
        chat.on_data(blob("voice"))
        ok("no transcriber", "--transcribe" in chat.said[0])
        chat.close()

        chat = room(tmp, transcribe=script(tmp, "exit 3"))
        chat.on_data(blob("voice"))
        wait_voice(chat)
        ok("a bad exit", "status 3" in chat.said[0])
        check("no prompt", chat.fed, [])
        chat.close()

        chat = room(tmp, transcribe=script(tmp, "echo"))
        chat.on_data(blob("voice"))
        wait_voice(chat)
        ok("no words", "words" in chat.said[0])
        check("no prompt", chat.fed, [])
        chat.close()

        chat = room(tmp, transcribe=os.path.join(tmp, "nothing"))
        chat.on_data(blob("voice"))
        ok("no such command", "could not transcribe" in chat.said[0])
        chat.close()


def test_voice_stuck_is_killed():
    with tempfile.TemporaryDirectory() as tmp:
        chat = room(tmp, transcribe=script(tmp, "exec sleep 60"))

        chat.on_data(blob("voice"))
        chat.voices.started -= agent.VOICE_MAX + 1
        chat.voices.check(agent.time.monotonic())
        wait_voice(chat)

        ok("said so", "could not transcribe" in chat.said[0])
        chat.close()


def test_voice_one_at_a_time():
    with tempfile.TemporaryDirectory() as tmp:
        chat = room(tmp, transcribe=script(tmp, 'cat "$1"'))

        chat.on_data(blob("voice", data=b"one"))
        chat.on_data(blob("voice", data=b"two"))
        wait_voice(chat)

        check("in order", [text.split()[-1] for text in chat.fed],
            ["one", "two"])
        chat.close()


def test_data_only_from_who_is_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        seen = os.path.join(tmp, "seen")
        chat = room(tmp, transcribe=script(tmp, "touch %s" % seen))

        chat.on_data(blob("voice", author=0x09))
        chat.on_data(blob("image", name="a.png", author=0x09))
        wait_voice(chat)

        check("nothing fed", chat.fed, [])
        check("nothing said", chat.said, [])
        ok("not transcribed", not os.path.exists(seen))
        ok("not kept", not os.path.exists(chat.opts.inbox))
        chat.close()


def test_data_in_a_group_needs_group_all():
    with tempfile.TemporaryDirectory() as tmp:
        seen = os.path.join(tmp, "seen")
        chat = room(tmp, transcribe=script(tmp, "touch %s" % seen))

        chat.on_data(blob("voice", group=0x0007))
        wait_voice(chat)

        ok("not transcribed", not os.path.exists(seen))
        check("nothing fed", chat.fed, [])
        chat.close()


def test_files_are_kept():
    with tempfile.TemporaryDirectory() as tmp:
        chat = room(tmp)

        chat.on_data(blob("image", name="a b.png", data=b"png"))

        ok("told about it", "image" in chat.fed[0])

        path = chat.fed[0].split("saved as ", 1)[1].rstrip(")")

        ok("under the inbox", path.startswith(chat.opts.inbox + os.sep))
        check("mode", os.stat(path).st_mode & 0o777, 0o600)
        check("mode of the folder", os.stat(chat.opts.inbox).st_mode & 0o777,
            0o700)

        with open(path, "rb") as f:
            check("contents", f.read(), b"png")

        chat.close()


def render_room(tmp, body=None, **kwargs):
    if body is not None:
        kwargs["render"] = script(tmp, body)

    chat = room(tmp, **kwargs)
    chat.sent = []

    def send_image(peer, path):
        with open(path, "rb") as f:
            chat.sent.append((peer, f.read()))

    def send_group_image(path):
        send_image(None, path)

    chat.send_image = send_image
    chat.send_group_image = send_group_image
    chat.conversation = lambda msg: types.SimpleNamespace(
        feed=lambda msg: chat.fed.append(msg.text))

    return chat


def asks(chat, text, **kwargs):
    msg = message(text, **kwargs)
    msg.mentions_me = False

    agent.AgentChat.on_message(chat, msg)


def wait_render(chat):
    for _ in range(500):
        if chat.renders.proc is None and not chat.renders.queue:
            return

        fd = chat.renders.fd

        if select.select([fd], [], [], 0.05)[0]:
            chat.renders.on_output(fd)


def test_render_sends_the_image():
    with tempfile.TemporaryDirectory() as tmp:
        seen = os.path.join(tmp, "seen")
        chat = render_room(tmp, 'cat > %s; echo "$#" > %s.argc; '
            'printf drawn > "$1"' % (seen, seen))

        asks(chat, "!render -rf a cat\non a roof")

        folder = chat.renders.dir

        ok("a private folder", os.stat(folder).st_mode & 0o777 == 0o700)
        wait_render(chat)

        check("the image", chat.sent, [(0x02, b"drawn")])
        check("no agent", chat.fed, [])
        check("nothing said", chat.said, [])

        with open(seen) as f:
            check("the prompt on stdin", f.read(), "-rf a cat\non a roof")

        with open(seen + ".argc") as f:
            check("only the path as an argument", f.read().strip(), "1")

        ok("nothing left", not os.path.exists(folder))
        chat.close()


def test_render_needs_a_prompt_and_a_renderer():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp)
        asks(chat, "!render a cat")
        ok("no renderer", "--render" in chat.said[0])
        chat.close()

        chat = render_room(tmp, 'printf drawn > "$1"')
        asks(chat, "!render")
        ok("no prompt", "say what to draw" in chat.said[0])
        check("nothing ran", chat.renders.proc, None)
        chat.close()


def test_render_only_from_who_is_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp, 'printf drawn > "$1"')

        asks(chat, "!render a cat", author=0x09)

        check("nothing ran", chat.renders.proc, None)
        check("nothing said", chat.said, [])
        chat.close()


def test_render_is_only_the_command():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp, 'printf drawn > "$1"')

        for text in ("!renderer on", "please !render a cat"):
            asks(chat, text)

            check("not a render: " + text, chat.renders.proc, None)

        check("the agent got them", chat.fed,
            ["!renderer on", "please !render a cat"])
        asks(chat, "!RENDER a cat")
        ok("any case", chat.renders.proc is not None)
        wait_render(chat)
        chat.close()


def test_render_failures_are_told():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp, "exit 3")
        asks(chat, "!render a cat")
        wait_render(chat)
        ok("a bad exit", "status 3" in chat.said[0])
        check("no image", chat.sent, [])
        chat.close()

        chat = render_room(tmp, render=os.path.join(tmp, "nothing"))
        asks(chat, "!render a cat")
        ok("no such command", "could not draw" in chat.said[0])
        chat.close()

        chat = render_room(tmp, "exit 0")
        asks(chat, "!render a cat")
        wait_render(chat)
        ok("no image written", "wrote no image" in chat.said[0])
        check("nothing sent", chat.sent, [])
        chat.close()


def test_render_stuck_is_killed():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp, "exec sleep 60")

        asks(chat, "!render a cat")
        chat.renders.started -= agent.RENDER_MAX + 1
        chat.renders.check(agent.time.monotonic())
        wait_render(chat)

        ok("said so", "could not draw" in chat.said[0])
        chat.close()


def test_render_one_at_a_time():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp, 'cat > "$1"')

        asks(chat, "!render one")
        asks(chat, "!render two")
        ok("said it waits", "1 ahead" in chat.said[0])
        wait_render(chat)

        check("in order", chat.sent, [(0x02, b"one"), (0x02, b"two")])
        chat.close()


def test_render_in_a_group():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp, 'printf drawn > "$1"', group=0x0007)

        asks(chat, "!render a cat", group=0x0007)
        wait_render(chat)

        check("to the group, no mention needed", chat.sent,
            [(None, b"drawn")])
        chat.close()


def test_render_stays_out_of_another_group():
    with tempfile.TemporaryDirectory() as tmp:
        chat = render_room(tmp, 'printf drawn > "$1"', group=0x0007)

        asks(chat, "!render a cat", group=0x0007)
        chat.group = 0x0009
        wait_render(chat)

        check("not sent", chat.sent, [])
        check("not said", chat.said, [])
        chat.close()


def test_voice_cannot_render():
    with tempfile.TemporaryDirectory() as tmp:
        chat = room(tmp, transcribe=script(tmp, "echo '!render a cat'"))

        chat.on_data(blob("voice"))
        wait_voice(chat)

        ok("marked", chat.fed[0].startswith("(voice message"))
        check("not the command", agent.RENDER.match(chat.fed[0]), None)
        chat.close()


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
