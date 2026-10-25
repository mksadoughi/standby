"""The agent's side of voice mode: two Claude Codes per meeting.

The meeting agent (deep model, the user's MCP servers, auto mode) listens, takes
typed questions, and leaves notes. The voice (quick model, no MCP) answers voice
questions from what is new since its last turn: transcript, notes, chat. It
hands real work to the meeting agent with [DEEP], never shown or spoken, and
never stops the meeting agent's listening otherwise. Questions can be cancelled.

The stand-in Claude speaks the stream protocol as measured (see test_interrupt)
and records which engine got which message."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, threading, time

root = tempfile.mkdtemp(prefix="sbvoice-")
stub = os.path.join(root, "fake-claude")
seen = os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys, time
seen = %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
kind = "voice" if "--strict-mcp-config" in sys.argv else "meeting"
open(seen, "a").write(json.dumps({"kind": kind, "argv": sys.argv}) + "\\n")
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
def say(text):
    for word in text.split(" "):
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
              "delta": {"type": "text_delta", "text": word + " "}}})
emit({"type": "system", "subtype": "init"})
buf = b""
def lines(timeout):
    global buf
    end = time.time() + timeout
    while time.time() < end:
        while b"\\n" in buf:
            l, buf = buf.split(b"\\n", 1)
            if l.strip(): yield json.loads(l)
        r, _, _ = select.select([sys.stdin], [], [], 0.05)
        if r:
            c = os.read(sys.stdin.fileno(), 65536)
            if not c: return
            buf += c
def slow(seconds, first):
    if first: say(first)
    for m in lines(seconds):
        if m.get("type") == "control_request":
            emit({"type": "control_response", "response": {"subtype": "success"}})
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
            return True
    return False
pushes = 0
while True:
    msg = next(lines(3600), None)
    if msg is None: break
    if msg.get("type") != "user": continue
    text = msg["message"]["content"][0]["text"]
    open(seen, "a").write(json.dumps({"kind": kind, "text": text}) + "\\n")
    if text.startswith("[TRANSCRIPT]"):
        pushes += 1
        if "slowlisten" in text:
            if not slow(6, None): emit({"type": "result", "is_error": False, "result": "."})
            continue
        emit({"type": "result", "is_error": False, "result": "Decision: they ship on Friday." if pushes == 1 else "."}); continue
    if text.startswith("[BRIEFING]"):
        if "slowbrief" in text:                  # a long brief: stops only when interrupted
            if not slow(8, None): emit({"type": "result", "is_error": False, "result": "."})
            continue
        emit({"type": "result", "is_error": False, "result": "."}); continue
    if kind == "voice":
        if "dig into" in text:
            for piece in ("[DE", "EP]"):           # split as a model streams it
                emit({"type": "stream_event", "event": {"type": "content_block_delta",
                      "delta": {"type": "text_delta", "text": piece}}})
            emit({"type": "result", "is_error": False, "result": "[DEEP]"}); continue
        if "slow one" in text:
            if not slow(20, "Let me think."): emit({"type": "result", "is_error": False, "result": "SLOW"})
            continue
        say("VOICE ANSWER.")
        emit({"type": "result", "is_error": False, "result": "VOICE ANSWER."}); continue
    if text.startswith("[VOICE] Spoken by the user"):
        say("The outage was a cache bug.")
        emit({"type": "result", "is_error": False, "result": "The outage was a cache bug.\\n---\\nDetails here."}); continue
    emit({"type": "result", "is_error": False, "result": "ANSWER"})
''' % seen)
os.chmod(stub, 0o755)
live = os.path.join(root, "live"); os.makedirs(live)
S = "voice-test"
json.dump({"session": S, "mode": "mic"}, open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
transcript = os.path.join(live, S + ".jsonl")


def said(text, t=[0]):
    t[0] += 3
    with open(transcript, "a") as fh:
        fh.write(json.dumps({"t": t[0], "speaker": "Room", "text": text}) + "\n")


said("we ship on Friday")
open(os.path.join(root, "prompt.md"), "w").write("You are a terse meeting helper. Session {session}.")
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub,
           STANDBY_PUSH_LINES="1", STANDBY_PUSH_SECONDS="999", STANDBY_PUSH_MIN_GAP="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent  # noqa: E402
PY = "/usr/bin/python3"
log = os.path.join(root, "serve.log")
srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"],
                       env=env, stderr=open(log, "w"))
fails = []


def check(label, ok, d=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def records():
    """What the stand-in Claudes wrote so far: whole lines only (one may be half written)."""
    out = []
    try:
        for line in open(seen):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except IOError:
        pass
    return out


def opt(args, name):
    return args[args.index(name) + 1] if name in args[:-1] else None


def to(kind):
    return [r["text"] for r in records() if r.get("kind") == kind and "text" in r]


def argv(kind):
    return next((r["argv"] for r in records() if r.get("kind") == kind and "argv" in r), [])


def wait(pred, secs=10):
    """Until pred() holds. secs is about what it takes on an idle Mac; under the
    whole suite (or two at once) it takes several times that, so allow 4x: it
    returns the moment the condition holds, so a pass costs no more."""
    end = time.time() + secs * 4
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def ask(question, out):
    p = subprocess.Popen([PY, agent, "q", "--json", question], env=env, stdout=subprocess.PIPE, text=True)
    for line in p.stdout:
        out.append(json.loads(line))
    p.wait()
    return out


def deltas(events):
    return "".join(e["text"] for e in events if e.get("type") == "progress" and e.get("kind") == "delta")


try:
    # A loaded Mac (the whole suite, or two at once) starts them in well over 10 s:
    # wait for them, however long, rather than for a length of time.
    check("both Claude Codes are started before anyone asks",
          wait(lambda: argv("meeting") and argv("voice"), 15), records())
    m, v = argv("meeting"), argv("voice")
    check("the meeting agent: deep model, the user's MCP servers, auto mode",
          opt(m, "--model") == "opus" and "--strict-mcp-config" not in m
          and opt(m, "--permission-mode") == "auto", m)
    check("the voice: quick model, no MCP, its own instructions",
          opt(v, "--model") == "sonnet" and "--strict-mcp-config" in v
          and "on a voice call with them" in (opt(v, "--append-system-prompt") or ""), v)
    check("listening: the meeting agent hears the transcript",
          wait(lambda: any("we ship on Friday" in t for t in to("meeting")), 10), to("meeting"))
    notes = os.path.join(live, S + ".notes.md")
    check("  and what it found worth noting goes to the notes",
          wait(lambda: os.path.exists(notes) and "ship on Friday" in open(notes).read(), 5))

    subprocess.run([PY, agent, "warm"], env=env, check=True)
    check("voice mode opened: the voice is briefed, with the meeting so far",
          wait(lambda: any(t.startswith("[BRIEFING]") and "we ship on Friday" in t and "[NOTES" in t
                           for t in to("voice")), 10), to("voice"))

    ev = ask("what is this", [])
    check("a typed question goes to the meeting agent", ev[-1:] == [{"type": "answer", "text": "ANSWER"}]
          and to("meeting")[-1].startswith("[QUESTION]"), (ev[-1:], to("meeting")[-1:]))

    said("the budget is tight")
    t0 = time.time()
    ev = ask("[VOICE] when do we ship", [])
    msg = to("voice")[-1]
    check("q --json reports the question's id first", ev and ev[0].get("type") == "queued", ev[:1])
    check("a voice question goes to the voice, not the meeting agent",
          msg.startswith("[VOICE]") and msg.rstrip().endswith("They said: when do we ship")
          and not any("when do we ship" in t for t in to("meeting")), msg)
    check("  with what is new since its last turn: transcript, chat",
          "the budget is tight" in msg and "[CHAT]" in msg and "Typed in the chat: what is this" in msg
          and "we ship on Friday" not in msg, msg)
    check("  answered by the voice", ev[-1] == {"type": "answer", "text": "VOICE ANSWER."}, ev[-1:])
    # where the wait goes. The agent's times, on its own clock, in one event before
    # the first word: queued, taken by the voice thread, handed to Claude Code, first word.
    q0 = ev[0]
    tim = [e for e in ev if e.get("type") == "progress" and e.get("kind") == "timing"]
    first = next((i for i, e in enumerate(ev) if e.get("kind") == "delta"), None)
    check("queued says when, on the agent's clock", isinstance(q0.get("at"), float) and t0 - 1 <= q0["at"] <= time.time(), q0)
    check("  one timing event, just before the first word", len(tim) == 1 and first is not None
          and ev.index(tim[0]) == first - 1, [e.get("kind") for e in ev])
    if tim:
        m = tim[0]
        check("  its marks in order: queued <= taken <= handed to Claude Code <= first word",
              m["queued"] <= m["taken"] <= m["sent"] <= m["word"] <= time.time() and m["sent"] > m["taken"] - 1e-3, m)
        check("  numbers only: no text in it", set(m) <= {"type", "kind", "queued", "taken", "sent", "word", "t"}, m)
    check("  never in a typed question's progress (only the voice's)",
          not any(e.get("kind") in ("timing", "sent") for e in ask("what is this again", [])))

    ev = ask("[VOICE] can you dig into the outage", [])
    check("real work: the voice hands it to the meeting agent",
          any(t.startswith("[VOICE] Spoken by the user") and "dig into the outage" in t for t in to("meeting")),
          to("meeting")[-1:])
    rule = next((t for t in to("meeting") if t.startswith("[VOICE] Spoken by the user")), "")
    check("  told plainly: the whole reply one short conversational message, no lists, headings or tables",
          "whole reply a short, clean conversational message" in rule and "one to three short sentences" in rule
          and "no bullet points or numbered lists, no headings, no tables" in rule and "Nowhere in the reply" in rule,
          rule[:400])
    check("  the --- part only when something truly needs reading, and short; SUGGEST lines and the speech fix kept",
          "Most replies need nothing more" in rule and "Only when they truly" in rule and "Keep that part short too" in rule
          and "never a report" in rule and "SUGGEST: <question>" in rule and "fix misheard words" in rule, rule[:600])
    check("  the user hears the meeting agent's answer", ev[-1]["text"].startswith("The outage was a cache bug."), ev[-1:])
    check("  [DEEP] is never shown or spoken", "DEEP" not in deltas(ev) and "DEE" not in deltas(ev), deltas(ev))
    check("  the wait shows as a step", any(e.get("kind") == "tool" and "meeting agent" in e.get("text", "")
                                             for e in ev), ev)
    ask("[VOICE] and then what", [])
    check("  and the voice knows what the meeting agent said, next turn",
          "You handed this to the meeting agent" in to("voice")[-1] and "cache bug" in to("voice")[-1]
          and "Details here" not in to("voice")[-1], to("voice")[-1])

    said("slowlisten now")                                   # the meeting agent goes off to digest
    wait(lambda: any("slowlisten" in t for t in to("meeting")), 5)
    t0 = time.time()
    ev = ask("[VOICE] quick one", [])
    took = time.time() - t0
    time.sleep(0.3)
    check("a voice question does not wait behind the meeting agent's listening",
          ev[-1]["text"] == "VOICE ANSWER." and took < 3, took)
    check("  and does not stop it either", "INTERRUPTED" not in open(log).read().split("slowlisten")[-1]
          if "slowlisten" in open(log).read() else True, open(log).read()[-400:])

    events = []
    th = threading.Thread(target=ask, args=("[VOICE] slow one please", events))
    t0 = time.time()
    th.start()
    wait(lambda: events, 10)
    qid = events[0]["id"] if events else "none"
    wait(lambda: "Let me think" in deltas(events), 5)
    subprocess.run([PY, agent, "cancel", qid], env=env, check=True)
    th.join(15)
    check("a running voice question is cancelled: (stopped), promptly",
          events[-1:] == [{"type": "answer", "text": "(stopped)"}] and time.time() - t0 < 8,
          (events[-1:], time.time() - t0))
    ev = ask("[VOICE] are you still there", [])
    check("  the next question works", ev[-1:] == [{"type": "answer", "text": "VOICE ANSWER."}], ev[-1:])

    # Opening voice mode briefs the voice; a question never waits for that.
    said("slowbrief: the budget review moved to Thursday")
    n_brief = len([t for t in to("voice") if t.startswith("[BRIEFING]")])
    subprocess.run([PY, agent, "warm"], env=env, check=True)
    wait(lambda: len([t for t in to("voice") if t.startswith("[BRIEFING]")]) > n_brief, 5)
    time.sleep(0.5)
    t0 = time.time()
    ev = ask("[VOICE] quick one", [])
    took = time.time() - t0
    check("a question 0.5 s into a slow briefing is answered at once, not after it",
          ev[-1:] == [{"type": "answer", "text": "VOICE ANSWER."}] and took < 3, (took, ev[-1:]))
    check("  the briefing stopped for it, and the wait is logged",
          "STOPPED for a question" in open(log).read() and "question waited" in open(log).read(), open(log).read()[-600:])
    check("  what the briefing sent is not sent again", "slowbrief" not in to("voice")[-1], to("voice")[-1][:300])
    said("a line said while voice mode was closed")
    spool = os.path.join(root, "agent", "spool")
    base = int(time.time() * 1000)
    for ident, ext, text in (("%d-0002" % base, ".vq", "[VOICE] what moved?"), ("%d-0001" % base, ".w", "voice")):
        open(os.path.join(spool, ident + ext + ".tmp"), "w").write(text)
    n_brief = len([t for t in to("voice") if t.startswith("[BRIEFING]")])
    os.rename(os.path.join(spool, "%d-0002.vq.tmp" % base), os.path.join(spool, "%d-0002.vq" % base))
    os.rename(os.path.join(spool, "%d-0001.w.tmp" % base), os.path.join(spool, "%d-0001.w" % base))
    wait(lambda: os.path.exists(os.path.join(spool, "%d-0002.a" % base)), 10)
    check("a question already waiting: no briefing at all, and the question brings what is new",
          len([t for t in to("voice") if t.startswith("[BRIEFING]")]) == n_brief
          and "a line said while voice mode was closed" in to("voice")[-1] and "no briefing" in open(log).read(),
          (to("voice")[-1][:300], open(log).read()[-300:]))

    up = os.path.join(root, "hold-up")
    hold = subprocess.Popen([PY, agent, "hold", "--session", S, "--", PY, "-c",
                             "import time; open(%r, 'w'); time.sleep(30)" % up], env=env)
    try:
        wait(lambda: os.path.exists(up), 10)
        n = len(to("meeting"))
        ev = ask("[VOICE] quick one", [])
        check("open in Claude Code: the voice still answers on its own",
              ev[-1:] == [{"type": "answer", "text": "VOICE ANSWER."}], ev[-1:])
        ev = ask("[VOICE] can you dig into the outage", [])
        check("  real work says the meeting agent is paused, and never reaches it",
              "paused while Claude Code is open in Terminal" in ev[-1]["text"] and len(to("meeting")) == n,
              (ev[-1:], to("meeting")[n:]))
    finally:
        hold.terminate()
        hold.wait(timeout=10)

    spool = os.path.join(root, "agent", "spool")
    ident = "%d-0001" % (int(time.time() * 1000) + 99999)
    open(os.path.join(spool, ident + ".c"), "w").close()
    n = len(records())
    open(os.path.join(spool, ident + ".vq"), "w").write("[VOICE] never mind")
    a = os.path.join(spool, ident + ".a")
    wait(lambda: os.path.exists(a), 10)
    check("a voice question cancelled while waiting is skipped, unseen",
          os.path.exists(a) and open(a).read() == "(stopped)" and not any(
              "never mind" in r.get("text", "") for r in records()[n:]), records()[n:])
    r = subprocess.run([PY, agent, "cancel", "../../etc"], env=env, capture_output=True, text=True)
    check("cancel refuses anything but a question id", r.returncode != 0, r)
finally:
    srv.terminate()
    srv.wait(timeout=10)
print("\n" + open(log).read().strip()[-1500:])
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
