"""The agent's side of a talk session: Claude without a meeting.

The same two Claude Codes and the same tools as a meeting, with talk prompts;
no listening (no transcript pushed) and no briefing; and an ended talk can be
continued by typing: a question names its session, is answered in that
session's conversation, and never retires the live meeting's Claude Codes.
Titles come from the chat. The stand-in Claude records what it was given."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, re, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbtalk-")
stub = os.path.join(root, "fake-claude")
seen = os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys, time
seen = %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
if "--fork-session" in sys.argv and "--no-session-persistence" in sys.argv and "--input-format" not in sys.argv:   # a visual's side copy
    open(seen, "a").write(json.dumps({"kind": "visual", "argv": sys.argv, "pid": os.getpid()}) + "\\n")
    if "a chart that breaks" in sys.argv[sys.argv.index("-p") + 1]:      # a side copy that comes back empty
        print(json.dumps({"result": ""})); sys.exit(0)
    time.sleep(2.0)                                   # drawing takes a while; the voice never waits for it
    print(json.dumps({"result": "```html\\n<div data-size=\\"small\\"><h1>Release</h1><script src=\\"https://x.test/a.js\\"></script>"
                                "<ol class=\\"steps\\" id=\\"r\\"><li>Build</li><li>Test</li></ol><script>sb.steps(\\"#r\\")</script></div>\\n```",
                      "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 10, "outputTokens": 40, "costUSD": 0.001}}}))
    sys.exit(0)
if "--no-session-persistence" in sys.argv and "--input-format" not in sys.argv:   # the one-shot title call
    open(seen, "a").write(json.dumps({"kind": "title", "prompt": sys.argv[sys.argv.index("-p") + 1]}) + "\\n")
    print("Caching The Map Page"); sys.exit(0)
kind = "voice" if "--strict-mcp-config" in sys.argv else "meeting"
prompt = sys.argv[sys.argv.index("--append-system-prompt") + 1]
me = {"kind": kind, "prompt": prompt, "pid": os.getpid()}
open(seen, "a").write(json.dumps(dict(me, argv=sys.argv)) + "\\n")
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
buf = b""
while True:
    r, _, _ = select.select([sys.stdin], [], [], 3600)
    c = os.read(sys.stdin.fileno(), 65536)
    if not c: break
    buf += c
    while b"\\n" in buf:
        l, buf = buf.split(b"\\n", 1)
        if not l.strip(): continue
        msg = json.loads(l)
        if msg.get("type") != "user": continue
        text = msg["message"]["content"][0]["text"]
        open(seen, "a").write(json.dumps(dict(me, text=text)) + "\\n")
        if kind == "voice" and text.startswith("[TASK]"):        # asked for the task it left out
            emit({"type": "result", "is_error": False,
                  "result": "Look through the build log for the error.\\nSUGGEST: What failed?"}); continue
        if kind == "voice" and "Held, not sent" in text and "name it notes" in text.split("They said:")[-1]:
            emit({"type": "result", "is_error": False,     # the held hand-off, updated with what they said since
                  "result": "Got it. [DEEP] Create a sample text file named notes.txt in the project folder."}); continue
        if kind == "voice" and "make the snake faster" in text.split("They said:")[-1]:   # dropped while it writes
            # Its words, then they go on: dropped (agentctl.cancel's .x and .c) while the rest is being
            # written, and the rest straight after, with no gap that would stop it (as a real voice streams)
            delta = lambda t: json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                          "delta": {"type": "text_delta", "text": t}}}) + "\\n"
            sys.stdout.write(delta("Sure, I'll make the snake faster. ")); sys.stdout.flush()
            spool, mine, end = os.path.join(os.environ["STANDBY_ROOT"], "agent", "spool"), [], time.time() + 20
            while not mine and time.time() < end:          # its question's progress, from those words
                mine = [n[:-2] for n in os.listdir(spool) if n.endswith(".p")]
                time.sleep(0.01)
            for ext in (".x", ".c"):
                open(os.path.join(spool, mine[0] + ext), "w").close()
            rest = "[DEEP] Make the snake move faster in the game."
            sys.stdout.write(delta(rest) + json.dumps({"type": "result", "is_error": False,
                             "result": "Sure, I'll make the snake faster. " + rest}) + "\\n"); sys.stdout.flush()
            continue
        if kind == "voice" and "check the free disk space" in text.split("They said:")[-1]:   # a hand-off and nothing said
            emit({"type": "result", "is_error": False, "result": "[DEEP] Check how much disk space is left."}); continue
        if kind == "voice" and "yes, put it up" in text.split("They said:")[-1]:     # their yes, and work handed on
            emit({"type": "result", "is_error": False,
                  "result": "Here it is. [SHOW VISUAL] [DEEP] Draw the release flow."}); continue
        if kind == "voice" and "chart of the outage" in text.split("They said:")[-1]:     # a visual that won't draw
            emit({"type": "result", "is_error": False, "result": "Here it is. [VISUAL: a chart that breaks]"}); continue
        if kind == "voice" and "how the release goes" in text.split("They said:")[-1]:     # words, a visual, words
            emit({"type": "result", "is_error": False,
                  "result": "Here's how it goes. [VISUAL: the release steps, build then test] Build first, then test."}); continue
        if kind == "voice" and "sample text file" in text.split("They said:")[-1]:     # as a real voice hands it on
            emit({"type": "result", "is_error": False,
                  "result": "Sure. [DEEP] Create a sample text file in the project folder."}); continue
        if kind == "voice" and "[RESULT] " in text and "the orb button" in text:     # as on a call: words, then a task
            emit({"type": "result", "is_error": False,
                  "result": "That stopped, so I'll pick it up. [DEEP] The user reports a regression: put the orb button back."}); continue
        if kind == "voice" and "start the timer" in text.split("They said:")[-1]:    # its words, [DEEP], then the task, slowly
            d = lambda t: emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                                   "delta": {"type": "text_delta", "text": t}}})
            d("Sure, I'm starting it. [DEEP] ")
            gate = os.path.join(os.environ["STANDBY_ROOT"], "timer-release")
            deadline = time.monotonic() + 30
            while not os.path.exists(gate) and time.monotonic() < deadline:
                time.sleep(0.02)
            if not os.path.exists(gate): raise RuntimeError("the test did not release the timer task")
            d("Run sleep 15 in the background.")
            emit({"type": "result", "is_error": False,
                  "result": "Sure, I'm starting it. [DEEP] Run sleep 15 in the background."}); continue
        if kind == "voice" and "the changelog instead" in text.split("They said:")[-1]:     # replace: stop #2, new work
            emit({"type": "result", "is_error": False,
                  "result": "Okay, switching. [CANCEL #2] [DEEP] Update the changelog instead."}); continue
        if kind == "voice" and "dig into" in text:
            emit({"type": "result", "is_error": False, "result": "[DEEP]"}); continue
        emit({"type": "result", "is_error": False, "result": "%%s ANSWER" %% kind.upper()})
''' % seen)
os.chmod(stub, 0o755)
live = os.path.join(root, "live"); os.makedirs(live)
TALK, MEET = "talk-20261003-221500", "meeting-20261003-223000"
json.dump({"session": TALK, "mode": "mic", "kind": "talk"}, open(os.path.join(live, TALK + ".meta.json"), "w"))
json.dump({"session": MEET, "mode": "mic"}, open(os.path.join(live, MEET + ".meta.json"), "w"))
# A talk has no transcript; should a line get there anyway, it is still not pushed.
open(os.path.join(live, TALK + ".jsonl"), "w").write(json.dumps({"t": 1, "speaker": "Room", "text": "stray words"}) + "\n")
current = os.path.join(live, "CURRENT")
open(current, "w").write(TALK + "\n")
# The voice's kept conversation (STANDBY_VOICE_LEDGER=0): a call's turns built from its record have their own suite,
# test_voice_ledger
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, STANDBY_CALL_TRACE="1", STANDBY_VOICE_LEDGER="0",
           STANDBY_PUSH_LINES="1", STANDBY_PUSH_SECONDS="999", STANDBY_PUSH_MIN_GAP="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent  # noqa: E402
PY = "/usr/bin/python3"
log = os.path.join(root, "serve.log")
fails = []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def records():
    try:
        return [json.loads(l) for l in open(seen)]
    except IOError:
        return []


def _ids(kind, session):
    """The conversation ids the agent ran `session`'s `kind` engine on (a call's voice prompt doesn't name its
    session: it's the same call after call, for the prompt cache)."""
    try:
        return {open(os.path.join(root, "agent", "%s.%s.uuid" % (session, kind))).read().strip()}
    except OSError:
        return set()


def _ours(r, kind, session):
    if r.get("kind") != kind:
        return False
    if session in r["prompt"]:
        return True
    ids = _ids(kind, session)
    pids = {x["pid"] for x in records() if "argv" in x and ids & set(x["argv"])}
    return ("argv" in r and bool(ids & set(r["argv"]))) or r.get("pid") in pids


def starts(kind, session):
    return [r for r in records() if "argv" in r and _ours(r, kind, session)]


def texts(kind, session):
    return [r["text"] for r in records() if "text" in r and _ours(r, kind, session)]


PATIENCE = 30           # generous: these suites run five at a time on one Mac


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def ask(question, *extra, ident=None):
    """The answer's text; ident: a list, given the question's id (the agent's "queued" line)."""
    p = subprocess.run([PY, agent, "q", "--json"] + list(extra) + [question], env=env,
                       capture_output=True, text=True, timeout=60)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    if ident is not None:
        ident.extend(l["id"] for l in lines if l.get("type") == "queued")
    return lines[-1].get("text") if lines else p.stderr


def dry(session):
    out = subprocess.run([PY, agent, "--session", session, "dry-run"], env=env, capture_output=True,
                         text=True).stdout
    return [l.strip(" \\") for l in out.splitlines()
            if not l.strip().startswith(("--session-id", "<new-uuid", "<"))]     # the prompt is <N chars>


check("a talk's meeting agent has every tool a meeting's has (only the prompt differs)",
      dry(TALK) == dry(MEET) and "--add-dir" in " ".join(dry(TALK)), (dry(TALK), dry(MEET)))
# Trace batches for a missing session folder are deliberately discarded. Create the
# call's folder before warming its voice, so the startup event survives even a slow run.
os.makedirs(os.path.join(root, "sessions", TALK), exist_ok=True)
srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"],
                       env=env, stderr=open(log, "w"))
try:
    subprocess.run([PY, agent, "warm"], env=env, check=True)   # the app's warm-up, once the call's voice.json is written
    check("a live talk: both Claude Codes start before anyone asks (the voice at the app's warm-up)",
          wait(lambda: starts("meeting", TALK) and starts("voice", TALK)), records())
    m, v = starts("meeting", TALK)[0], starts("voice", TALK)[0]
    check("  the deep one: the user's MCP servers, auto mode, the talk prompt",
          "--strict-mcp-config" not in m["argv"] and m["argv"][m["argv"].index("--permission-mode") + 1] == "auto"
          and "in a talk session" in m["prompt"] and "silent" not in m["prompt"] and "[TRANSCRIPT]" not in m["prompt"],
          m["prompt"][:300])
    check("  the voice: a call's prompt, no meeting in it",
          "on a voice call with them" in v["prompt"] and "meeting" not in v["prompt"], v["prompt"][:300])
    subprocess.run([PY, agent, "warm"], env=env, check=True)
    # No sleep: the questions below go through the same threads, after the warm-up, so
    # had anything been sent before them, it would be in front of them.
    check("a typed question in a live talk goes to its deep agent",
          ask("what is p99") == "MEETING ANSWER" and texts("meeting", TALK)[-1].startswith("[QUESTION]")
          and "stray words" not in texts("meeting", TALK)[-1], texts("meeting", TALK))
    check("spoken: the voice answers", ask("[VOICE] hello there") == "VOICE ANSWER"
          and texts("voice", TALK)[-1].rstrip().endswith("They said: hello there"), texts("voice", TALK))
    tpath = os.path.join(root, "sessions", TALK, "trace.jsonl")
    wait_for = time.time() + 5
    while time.time() < wait_for and not (os.path.exists(tpath) and '"ev": "model"' in open(tpath).read()):
        time.sleep(0.1)
    tr = [json.loads(x) for x in open(tpath)] if os.path.exists(tpath) else []
    w = [r for r in tr if r["ev"] == "wait"]
    m = [r for r in tr if r["ev"] == "model"]
    check("the call's trace, from the agent: how long the question waited for the voice's model, and on what",
          w and w[-1]["src"] == "agent" and w[-1]["q"] and isinstance(w[-1]["ms"], int) and isinstance(w[-1]["on"], list), w)
    check("  and the model's turn: its whole reply, its outcome, its time to the first word",
          m and m[-1]["q"] == w[-1]["q"] and m[-1]["reply"] == "VOICE ANSWER" and m[-1]["outcome"] == "answered"
          and "model_ms" in m[-1], m)
    check("  the exact message it was sent, notes and all, not only their sizes",
          (m[-1].get("sent") or "").rstrip().endswith("They said: hello there") and m[-1]["sent"].startswith("[VOICE]"),
          (m[-1].get("sent") or "")[-200:])
    vstart = [r for r in tr if r["ev"] == "voice_start"]
    check("  and, once per Claude Code it starts, its system prompt and model",
          vstart and "on a voice call with them" in (vstart[-1].get("system_prompt") or "") and vstart[-1]["model"] == "sonnet",
          [(r.get("model"), (r.get("system_prompt") or "")[:80]) for r in vstart])
    check("no listening and no briefing: nothing was sent before the user said something",
          len(texts("meeting", TALK)) == 1 and len(texts("voice", TALK)) == 1, records()[-3:])
    check("  and hands real work over, as in a meeting", ask("[VOICE] can you dig into the logs") == "MEETING ANSWER"
          and texts("meeting", TALK)[-1].startswith("[VOICE] Spoken by the user"), texts("meeting", TALK)[-1:])
    # A call from the user's Claude Code session: real work goes to that session, not to ours.
    json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    asked = len(texts("meeting", TALK))
    check("a Claude Code session's call: a bare [DEEP] (no task): the voice is asked for the task, and that goes",
          ask("[VOICE] (They went on before hearing your answer.) Okay. now dig into the build log")
          == "[HANDOFF] Look through the build log for the error."
          and len(texts("meeting", TALK)) == asked, (texts("meeting", TALK)[asked:], records()[-2:]))
    ask("[VOICE] what time is it there")
    check("  the voice remembers what it handed on, for \"how's it going?\"",
          "You took this deeper, and are working on it now: Look through the build log for the error."
          in texts("voice", TALK)[-1], texts("voice", TALK)[-1:])
    # The session finished that work: the voice is told so, as news, and answers in its own words.
    got = ask("[VOICE] [RESULT done] I created an empty file at /Users/you/code/orion/standby-test.txt.")
    last = texts("voice", TALK)[-1]
    check("a result of its own deeper work reaches the voice as information, not as the user's words",
          got == "VOICE ANSWER" and "[RESULT] Not from the user: the work you took deeper is done. What it came to:" in last
          and "They said:" not in last and "standby-test.txt" in last, last[-400:])
    check("  with no instructions on what to say: that's the voice's to decide",
          "Now tell the user" not in last and "sentences" not in last.split("<<<")[0], last[-400:])
    got = ask("[VOICE] [RESULT error] it failed; dig into the log at /tmp/x.log")
    check("  never handed on again, even if the model asks for real work", got == "" and len(texts("meeting", TALK)) == asked
          and "the work you took deeper ran into an error" in texts("voice", TALK)[-1], (got, texts("voice", TALK)[-1][-200:]))
    got = ask("[VOICE] [RESULT aborted] (the orb button work stopped)")
    check("  on a call, news it takes deeper: its words, then the work as a [HANDOFF] line for the Mac, never said (a call's four lost)",
          got == "That stopped, so I'll pick it up.\n[HANDOFF] The user reports a regression: put the orb button back."
          and len(texts("meeting", TALK)) == asked, got)
    # Back from a pause where results waited: one [RESUMED] turn, not their words; what waited as news
    ask("[VOICE] [RESUMED] They're back. While paused, they saw something was waiting.\n[RESULT done] The film is built.\n[END RESULT]")
    last = texts("voice", TALK)[-1]
    check("back from a pause: the [RESUMED] line, then what waited as news; no \"They said\"",
          "[RESUMED] They're back. While paused, they saw something was waiting.\n\n[RESULT] Not from the user: the work you took "
          "deeper is done. What it came to:\n<<<\nThe film is built.\n>>>" in last and "They said:" not in last, last[-400:])
    # What the session tells its voice (its tell_voice tool): read as it is, its short label and nothing more
    ask("[VOICE] [RESULT message] Deeper side: The tests pass, and the fix is merged.")
    last = texts("voice", TALK)[-1]
    check("the session's own message: \"Deeper side: ...\" as it is, no preamble, not the user's words",
          last.rstrip().endswith("Deeper side: The tests pass, and the fix is merged.") and "Not from the user" not in last
          and "They said:" not in last and "[RESULT" not in last.split("Deeper side:")[-1], last[-300:])
    ask("[VOICE] how is it going\n[RESULT message] Deeper side: Sam answered: nothing to hold.\n[END RESULT]")
    last = texts("voice", TALK)[-1]
    check("  one that came while they talked: their words first, then it, as it is",
          "They said: how is it going\n\nDeeper side: Sam answered: nothing to hold." in last
          and "[END RESULT]" not in last and "Background, not from the user" not in last, last[-300:])
    # Cut off as it speaks, once its hand-off has begun ([DEEP] streamed, the task still coming): the task streams
    # to its end and the work still goes, as it said it would. "They went on" (the next turn has it all) never.
    import threading
    spool = os.path.join(root, "agent", "spool")
    for how, want in (("keep\n", "[HANDOFF] Run sleep 15 in the background."), ("", None)):
        gate = os.path.join(root, "timer-release")
        if os.path.exists(gate):
            os.remove(gate)
        before = set(os.listdir(spool))
        got = []
        t = threading.Thread(target=lambda: got.append(ask("[VOICE] start the timer please")))
        t.start()
        if not wait(lambda: any(n.endswith(".p") and n not in before for n in os.listdir(spool)), 20):
            raise RuntimeError("the timer question did not create its progress file")
        ident = [n for n in os.listdir(spool) if n.endswith(".p") and n not in before][0][:-2]

        def handoff_begun():
            try:
                with open(os.path.join(spool, ident + ".p")) as fh:
                    return any(r.get("kind") == "delta" and "Sure, I'm starting it." in r.get("text", "")
                               for r in (json.loads(line) for line in fh if line.strip()))
            except (OSError, ValueError):
                return False

        # A progress file can exist while the model is still starting. This delta
        # is written only after DeepMarker consumed [DEEP]; keep the fake task
        # blocked until the cancellation has been written, on any speed of Mac.
        check("  the hand-off prefix streamed before cancellation", wait(handoff_begun))
        open(os.path.join(spool, ident + ".c"), "w").write(how)
        open(gate, "w").close()
        t.join(30)
        if want:
            check("cut off after its hand-off began: the task streams to its end and the work still goes",
                  got and got[0] == want, got)
        else:
            check("  \"they went on\" (a quiet cancel): stopped, nothing handed on (their next turn has it all)",
                  got and not str(got[0]).startswith("[HANDOFF]"), got)
    got = ask("[VOICE] skip that, do the changelog instead")
    check("a task replaced by voice: the new work handed on, with the task it stops, for the Mac to stop",
          got == "[HANDOFF] Update the changelog instead. [CANCEL #2]", got)
    # A result that came while they talked rides their next turn: their words first, the result after as
    # background, so the voice answers what they just said and raises the result when the moment fits
    ask("[VOICE] [RESULT done] The film cards are rebuilt.\n[END RESULT]\ndo you get my point about the orb window")
    last = texts("voice", TALK)[-1]
    check("a result held during their turn: their words first, then the result as background for later",
          "They said: do you get my point about the orb window\n\n[RESULT] Background, not from the user: the work you took "
          "deeper is done while they were talking. What they said comes first; this can wait for a natural moment." in last
          and last.index("They said:") < last.index("film cards") and "[END RESULT]" not in last, last[-500:])
    # Who the voice is on a call from Claude Code: a few principles, its folder, nothing step by step.
    import importlib.machinery, importlib.util
    os.environ["STANDBY_ROOT"] = root
    loader = importlib.machinery.SourceFileLoader("sa_prelude", agent)
    sa = importlib.util.module_from_spec(importlib.util.spec_from_loader("sa_prelude", loader))
    loader.exec_module(sa)
    json.dump({"handoff": True, "folder": "/Users/you/code/orion"}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    call = " ".join(sa.voice_prelude(TALK).split())
    ledger = " ".join(sa.voice_prelude(TALK, ledger=True).split())
    check("a call's voice: it IS the session, its mouth and ears, with its own deeper side; one self; its judgement; its folder",
          call.startswith("You are the user's Claude Code session in /Users/you/code/orion, on a voice call with them: its mouth and ears.")
          and "It's all you: same agent, same work, same memory, so never speak of a separate agent or helper." in call
          and "Everything else is your deeper side's: this whole Claude Code session, with every tool it has, far more "
              "context than you, and more thought." in call
          and "What follows [DEEP] is never said: reply [DEEP] and what they want, with anything you know that matters, "
              "not how to do it; what was said goes with it." in call
          and ledger.startswith(call[:1500]) and " ".join(sa.CALL_LEDGER_READING.split()) in ledger
          and " ".join(sa.CALL_LEDGER_READING.split()) not in call
          and "Whenever you take something deeper, or look at something that takes a moment, your few words to them come "
              "first, before [DEEP]: what you're about to do, never that it's done, and after a long turn, briefly what you "
              "understood, so a misreading shows at once." in call
          and "let me check" not in call
          and "Claude Code is your engine" not in call and not re.search(r"\bhand(?:s|ed|ing)?\b|hand-off", call)
          and "in /Users/you/code/orion" in call
          and "[QUIET]" in call and len(call.replace(" ".join(sa.CALL_VISUALS.split()), "").split()) < 460, (len(call.split()), call[:300]))
    check("  brief: a sentence or two, longer only when they ask it to explain",
          "You're heard, not read: a sentence or two, then let them talk, longer only when they ask you to explain" in call)
    check("  heard, so written as said aloud: a version in words, never digits with dots (the speech engine's sentence ends)",
          "write as you'd say it aloud (\"zero point two point zero\" for 0.2.0), never paths, code, markdown or lists." in call
          and "write as you'd say it aloud" in ledger)
    check("  its opening: its whole reply a hello, under ten words, naming what it's picking up; on the record too",
          "[CALL START] means the call just connected: your whole reply is a hello your way, under ten words, naming at most the one thing you're picking up." in call
          and "[CALL START] means the call just connected: your whole reply is a hello your way, under ten words, naming at most the one thing you're picking up." in ledger
          and "last message" not in ledger)
    check("  saying nothing sits with the notes, where it's mostly used", "send the new work with [DEEP]. To say nothing, reply [QUIET]." in call)
    check("  asked at their pauses, it never says nothing to wait for more (the app asks again if they go on)",
          "To say nothing, reply [QUIET]. You're asked at their pauses, and again if they go on: never reply [QUIET] to wait for more." in call)
    check("  it may handle things itself: it can read and search where its session works, and nothing forces going deeper",
          "Your reading and search tools are only for looking, where your session works and in ~/.standby, "
          "never the whole home folder." in call
          and "So whatever they ask for is yours to do through it" in call
          and "A tool refusing you only means it's your deeper side's to do, never that it can't be done." in call
          and "Never say you will look into something" not in call and "SUGGEST" not in call)
    check("  honest hand-offs: TASKS and its deeper side say what has happened; never a guess",
          "Only these and your deeper side say what has happened, so never guess: something is sent only once TASKS shows "
          "it, and a task that ended with nothing back means just that, so ask if they'd want to know." in call
          and "[SESSION]" not in call and "[RESULT]" not in call)
    check("  its deeper side never talks to them: it tells the voice, and the voice judges what they hear",
          "Your deeper side never talks to them. It tells you what it thinks they should know, as \"Deeper side: ...\", "
          "and what reaches them is yours to judge: now, later or never, the gist in your words." in call
          and "Your deeper side never talks to them. It tells you what it thinks they should know" in ledger)
    check("  the voice can stop or change a task by its number in TASKS, on the record or not",
          "To stop a task, add [CANCEL #n] with its number from TASKS; to change one, stop it and send the new work with [DEEP]." in ledger
          and "To stop a task, add [CANCEL #n] with its number from TASKS; to change one, stop it and send the new work with [DEEP]." in call)
    json.dump({}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    check("  its voice file unwritten: still a call's voice, with no folder named",
          sa.voice_prelude(TALK).startswith("You are the user's Claude Code session, on a voice call") and "{folder}" not in sa.voice_prelude(TALK))
    json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    proj = tempfile.mkdtemp(prefix="sbproj-")
    os.makedirs(os.path.join(proj, ".claude"))
    json.dump({"enabledPlugins": {"standby@standby-local": True, "other@x": True}},
              open(os.path.join(proj, ".claude", "settings.local.json"), "w"))
    vs = sa.voice_settings(proj)
    check("the voice runs with no plugin of the folder's and no hooks: no Standby tools that act on itself",
          vs["disableAllHooks"] is True and vs["enabledPlugins"].get("standby@standby-local") is False
          and vs["enabledPlugins"].get("other@x") is False, vs)
    sa.claude_caps(); sa._CAPS.update(settings=True)      # as a Claude Code that takes --settings
    argv = sa.build_argv(TALK, "haiku", True, "00000000-0000-4000-8000-000000000001", stream=False, kind="voice")
    check("  and its command line says so", "--settings" in argv and json.loads(argv[argv.index("--settings") + 1])["disableAllHooks"]
          and "--strict-mcp-config" in argv and argv[argv.index("--tools") + 1].startswith("Bash,Read,Grep"), argv)
    # Every Claude Code Standby starts is kept to its folder and ~/.standby: a Grep over the whole home
    # folder walked into Contacts and OneDrive, and macOS asked the user in Standby's name.
    perms = json.loads(argv[argv.index("--settings") + 1]).get("permissions") or {}
    dirs = [argv[i + 1] for i, x in enumerate(argv) if x == "--add-dir"]
    check("the voice reads only its folder and ~/.standby: reads elsewhere refused, no guarded folder named "
          "(Claude Code looks each one up as it starts, and macOS then asks the user)",
          perms.get("blockReadsOutsideWorkingDirectories") is True and sa.ROOT in dirs and not perms.get("deny"),
          (perms, dirs))
    sa._CAPS.update(auto=False, dont_ask=True)
    talk = sa.build_argv(TALK, "haiku", True, "00000000-0000-4000-8000-000000000002", stream=False, kind="meeting")
    tperms = json.loads(talk[talk.index("--settings") + 1]).get("permissions") or {} if "--settings" in talk else {}
    tdirs = [talk[i + 1] for i, x in enumerate(talk) if x == "--add-dir"]
    check("  the meeting's agent too: ~/.claude and ~/.standby added, nothing else readable, no guarded folder named",
          tperms.get("blockReadsOutsideWorkingDirectories") is True and not tperms.get("deny")
          and os.path.expanduser("~/.claude") in tdirs and sa.ROOT in tdirs, (tperms, tdirs))
    home = os.path.expanduser("~")
    f = sa.fence([os.path.join(home, "Documents", "projects", "orion"), sa.ROOT])
    check("  a folder inside a guarded one: nothing named, reads outside its folders still refused",
          f == {"blockReadsOutsideWorkingDirectories": True, "deny": []}, f)
    f = sa.fence([home, sa.ROOT])
    check("  a working dir that holds the home folder (never, since run_dir): its guarded folders denied",
          {"Read(~/Library/**)", "Read(~/Desktop/**)", "Read(~/Documents/**)"} <= set(f["deny"]), f)
    check("  and the home folder is never one: it runs in the agent's own", sa.run_dir(home) == sa.AGENT and sa.run_dir(os.path.join(home, "x")) == os.path.join(home, "x"))
    # A call's voice reads where its session works (the Mac lists those folders in voice.json "dirs"): the
    # snake game the session built in its own folder (talk-20261024-232831), not only the Standby repo.
    # (real paths: the agent works each one out, links resolved; the Mac hands it names only)
    snake, qa = (os.path.realpath(tempfile.mkdtemp(prefix=x)) for x in ("sbsnake-", "sbqa-"))
    json.dump({"handoff": True, "folder": "/Users/you/code/orion", "dirs": [snake, qa, "/no/such/folder"]},
              open(os.path.join(live, TALK + ".voice.json"), "w"))
    argv = sa.build_argv(TALK, "haiku", True, "00000000-0000-4000-8000-000000000003", stream=False, kind="voice")
    adds = [argv[i + 1] for i, x in enumerate(argv) if x == "--add-dir"]
    cperms = json.loads(argv[argv.index("--settings") + 1]).get("permissions") or {}
    check("a call's voice reads where its session works: each of its folders added, reads elsewhere still refused",
          snake in adds and qa in adds and "/no/such/folder" not in adds and sa.ROOT in adds
          and cperms.get("blockReadsOutsideWorkingDirectories") is True and not cperms.get("deny"), (adds, cperms))
    # Its voice was warmed up before the call said where the session works (talk-20261025-010306: "I can't open
    # the snake folder"): its next turn starts it again, with those folders, the conversation going on
    n_before = len([r for r in records() if "argv" in r])
    ask("[VOICE] can you read the snake folder?")
    later = [r for r in records() if "argv" in r][n_before:]
    check("  a voice started before the call's folders were known starts again with them, at its next turn",
          any(r.get("kind") == "voice" and snake in r["argv"] and qa in r["argv"] for r in later),
          [r["argv"][-6:] for r in later])
    json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    # Warmed up before its voice.json was written at all: a bare voice, no call prompt, no skills. Its next turn
    # starts it as the call's (the 01:03 call's voice was never the call's: slow first words, "I can't open it")
    os.remove(os.path.join(live, TALK + ".voice.json"))
    ask("[VOICE] hello")
    json.dump({"handoff": True, "folder": "/Users/you/code/orion"}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    n_before = len([r for r in records() if "argv" in r])
    ask("[VOICE] are you the call's now?")
    later = [r for r in records() if "argv" in r][n_before:]
    check("  a voice warmed up before the call's voice.json starts again as the call's: its prompt and skills",
          any(r.get("kind") == "voice" and "Skill" in " ".join(r["argv"]) and "on a voice call with them" in r.get("prompt", "")
              and "/Users/you/code/orion" in r.get("prompt", "") for r in later), [r["argv"][-6:] for r in later])
    json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    # What they pasted in the session's chat reaches the voice as an image, not a note about one; only the call's own files.
    os.makedirs(os.path.join(sa.ROOT, "sessions", TALK), exist_ok=True)
    pic = os.path.join(sa.ROOT, "sessions", TALK, "pasted-1.png")
    open(pic, "wb").write(b"\x89PNG fake")
    blocks = sa.with_images("Typed in the chat: what is this?\n[IMAGE %s]\nsee it?" % pic)
    check("a pasted image goes to the voice as an image block, after its words",
          blocks[0] == {"type": "text", "text": "Typed in the chat: what is this?\n(the image they pasted, below)\nsee it?"}
          and blocks[1]["type"] == "image" and blocks[1]["source"]["media_type"] == "image/png", blocks[:1])
    check("  never a file outside the call's own", sa.with_images("look [IMAGE /etc/hosts]") == [{"type": "text", "text": "look"}])
    src = open(agent).read()
    spawns = re.findall(r"\[CLAUDE, \"-p\"[^\n]*(?:\n[^\n]*){0,1}", src)
    bare = [x for x in spawns if '"--tools", ""' not in x and "def build_argv" not in x]
    check("  every other Claude it starts has no tools at all: nothing to read with",
          len(spawns) >= 6 and all(("--tools\", tools" in x or "ALLOWED" in x or "Bash,Read,Grep" in x) for x in bare) and len(bare) == 3, bare)
    m = sa.DeepMarker()
    spoken = "".join([m.feed("[QU"), m.feed("IET]"), m.feed("Sure [DE"), m.feed("EP]"), m.flush()])
    check("the voice's signals are never spoken, even split across pieces of the stream", spoken == "Sure ", spoken)
    m = sa.DeepMarker()
    spoken = "".join([m.feed("Sure, on it. [DE"), m.feed("EP] Create a sample"), m.feed(" text file."), m.flush()])
    check("  nor the task it writes for Claude Code after [DEEP]", spoken == "Sure, on it. ", spoken)
    m = sa.DeepMarker()
    spoken = "".join([m.feed("Okay, talk soon! [EN"), m.feed("D]"), m.flush()])
    check("  nor [END], the voice hanging up, even split across pieces", spoken == "Okay, talk soon! ", spoken)
    m = sa.DeepMarker()
    spoken = "".join([m.feed("Okay, I've stopped that. [CANC"), m.feed("EL #"), m.feed("3] Anything else?"), m.flush()])
    check("  nor [CANCEL #n], the voice stopping a task, even split across pieces", spoken == "Okay, I've stopped that.  Anything else?", spoken)
    json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    call = " ".join(sa.voice_prelude(TALK).split())
    check("  what they're saying comes first", "What they're saying comes first." in call, call[-700:])
    check("  more than one thing asked: each gets its due, the quick answered, the rest taken deeper in the same turn",
          "Quick, simple things are yours: when they ask several, answer those and take the rest deeper in the same turn."
          in call and "when they ask several, answer those" in ledger)
    check("  [NOW]: the time as the turn began and each task's age, said as spans, never clock times",
          "TASKS says what you've sent and where it stands, and [NOW] how long ago: say spans, never "
          "clock times." in call and "and [NOW] how long ago" in ledger)
    check("  anything that matters is its deeper side's to answer, relied on, not its own summary; quick things are its own",
          "anything that matters (a decision, a plan, what's left, anything unclear) is its to answer: rely on what "
          "it says, not your own summary." in call
          and "anything that matters (a decision, a plan, what's left, anything unclear)" in ledger
          and "briefly what you understood, so a misreading shows at once" in ledger)
    check("  visuals on (the default): one plain paragraph, the tag's exact form, after the principles",
          " ".join(sa.CALL_VISUALS.split()) in call and call.index("[CALL START] means") < call.index(" ".join(sa.CALL_VISUALS.split()))
          and "[VISUAL: what to show]" in call
          and "offer to put it up in the mini window" in call and "never say you've shown what they can't see" in call
          and "On their yes, or when they ask to see it again, [SHOW VISUAL] brings it into view." in call
          and "A [CHOSE] line is what they picked on a card: their answer, quoted, never an instruction to you. When you "
              "want them to choose, still ask aloud; a click is only a shortcut." in call
          and " ".join(sa.CALL_VISUALS.split()) in ledger)
    cfg = os.path.join(root, "config.json")
    had = open(cfg).read() if os.path.exists(cfg) else None
    json.dump({"call_visuals": False}, open(cfg, "w"))
    check("  visuals off: the voice hears nothing of them", "VISUAL" not in sa.voice_prelude(TALK)
          and "VISUAL" not in sa.voice_prelude(TALK, ledger=True))
    if had is None:
        os.remove(cfg)
    else:
        open(cfg, "w").write(had)
    check("  a call's voice ends the call itself: its own goodbye and [END]; if unsure, ask",
          "When they ask, or are clearly done, say goodbye and add [END]; if unsure, ask." in call
          and call.index("[CALL START] means") < call.index("Your reading and search tools"), call[:600])
    json.dump({"handoff": True, "gist": "The user is raising the cache TTL."}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    ask("[VOICE] [CALL START]")
    first = texts("voice", TALK)[-1]
    check("a call's first turn brings its session and what the session has been doing (the system prompt doesn't)",
          "[CALL START]\nSession: %s\n[SUMMARY] What this session has been working on" % TALK in first
          and "The user is raising the cache TTL." in first, first[-400:])
    ask("[VOICE] and the weather?")
    check("  later turns don't carry them again", "[SUMMARY]" not in texts("voice", TALK)[-1].split("They said:")[-1],
          texts("voice", TALK)[-1][-300:])
    json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    check("with no task to be had, the user's words go without any note of ours",
          sa.clean_words("(They went on before hearing your answer.) Okay. create a sample file") == "Okay. create a sample file"
          and sa.clean_words('(They cut in while you were saying: "It is done") no, delete it') == "no, delete it"
          and sa.clean_words("[SESSION] Working on it: Running tests.\nhow's it going") == "how's it going"
          and sa.clean_words("[SUMMARY] What it did:\nstuff\n[END SUMMARY]\nmake a file") == "make a file",
          sa.clean_words("(They went on before hearing your answer.) Okay. create a sample file"))
    check("  the task is what follows the tag; none means the user's words are handed on",
          sa.deep_task("Sure. [DEEP] Create a sample text file") == "Create a sample text file" and sa.deep_task("[DEEP]") == "")
    json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
    got = ask("[VOICE] can you ask the agent to create a new text file, just a sample text file?")
    check("a hand-off is the voice's own instruction, not the user's sentence (no \"ask the agent\")",
          got == "[HANDOFF] Create a sample text file in the project folder." and "ask the agent" not in got, got)
    check("  and the voice remembers the task it gave",
          (ask("[VOICE] what time is it there") or True) and
          "You took this deeper, and are working on it now: Create a sample text file"
          in texts("voice", TALK)[-1], texts("voice", TALK)[-1][-300:])
    # Standby Dictation lists the projects for its vocabulary: one in a folder macOS guards is never touched
    # (a stat there made macOS ask Standby Dictation for Documents).
    fake_home = tempfile.mkdtemp(prefix="sbhome-")
    open_proj, guarded_proj = os.path.join(fake_home, "code", "orion"), os.path.join(fake_home, "Documents", "secret")
    for proj in (open_proj, guarded_proj):
        os.makedirs(proj)
        slot = os.path.join(fake_home, ".claude", "projects", sa.project_slot(proj))
        os.makedirs(slot)
        open(os.path.join(slot, "s.jsonl"), "w").write(json.dumps({"type": "user", "cwd": proj}) + "\n")
    home_was, guarded_was, isdir_was, temp_was, touched = os.environ["HOME"], sa.GUARDED, os.path.isdir, sa.TEMP_DIRS, []
    os.environ["HOME"] = fake_home
    sa.GUARDED = (os.path.join(fake_home, "Documents") + os.sep,)
    sa.TEMP_DIRS = ("/nowhere/",)                 # this stand-in home is in a temporary folder, left out otherwise
    os.path.isdir = lambda x: (touched.append(x), isdir_was(x))[1]
    try:
        everything = [p["path"] for p in sa.recent_projects()]
        del touched[:]
        unguarded = [p["path"] for p in sa.recent_projects(unguarded=True)]
    finally:
        os.environ["HOME"], sa.GUARDED, os.path.isdir, sa.TEMP_DIRS = home_was, guarded_was, isdir_was, temp_was
    real = lambda x: os.path.realpath(x)
    check("projects listed for Standby Dictation: none in a guarded folder, and nothing there even looked at",
          real(guarded_proj) in everything and unguarded == [real(open_proj)]
          and not any(t.startswith(os.path.join(fake_home, "Documents")) for t in touched), (everything, unguarded, touched))
    # Standby's one-off asks (a title, a summary, a gist) run by this server, never started by Standby (each starts a
    # Claude Code, which reads its plugins as it starts: Standby was asked for ~/Documents): <id>.run, <id>.ran.
    spool_dir = os.path.join(root, "agent", "spool")

    def run_req(ident, req):
        with open(os.path.join(spool_dir, ident + ".run.tmp"), "w") as fh:
            json.dump(req, fh)
        os.replace(os.path.join(spool_dir, ident + ".run.tmp"), os.path.join(spool_dir, ident + ".run"))
        out, deadline = os.path.join(spool_dir, ident + ".ran"), time.time() + 30
        while time.time() < deadline and not os.path.exists(out):
            time.sleep(0.05)
        return json.load(open(out)) if os.path.exists(out) else None
    got = run_req("1-0001", {"argv": ["title", "--stdin"], "input": "We talked about caching the map page."})
    check("Standby's one-off ask, run by the agent server: a title, from its Claude Code", got and got["rc"] == 0
          and got["stdout"].strip() == "Caching The Map Page", got)
    got = run_req("1-0002", {"argv": ["serve"]})
    check("  only those asks: anything else is refused", got and got["rc"] == 2 and not got["stdout"], got)
    # A call's voice reads where its session works: the Mac hands it names only, and the agent (started by the
    # user's Claude Code) works out each one's project top and reads the project's own settings.
    proj = tempfile.mkdtemp(prefix="sbproj-", dir=os.path.expanduser("~")) if False else tempfile.mkdtemp(prefix="sbproj-")
    os.makedirs(os.path.join(proj, "app", ".git")); os.makedirs(os.path.join(proj, "app", "src"))
    os.makedirs(os.path.join(proj, "lib")); os.makedirs(os.path.join(proj, "call", ".claude"))
    json.dump({"permissions": {"additionalDirectories": [os.path.join(proj, "lib")]}},
              open(os.path.join(proj, "call", ".claude", "settings.json"), "w"))
    home_was = os.environ["HOME"]
    os.environ["HOME"] = proj                        # the project tops are looked for under home
    try:
        got = sa.voice_dirs_now(TALK, {"folder": os.path.join(proj, "call"),
                                       "dirs": [os.path.join(proj, "app", "src", "main.py"), os.path.join(proj, "gone")]})
    finally:
        os.environ["HOME"] = home_was
    real = os.path.realpath
    check("a call's work dirs: worked out by the agent (git top), the project's own settings read there, gone ones out",
          got == [real(os.path.join(proj, "app")), real(os.path.join(proj, "lib"))], got)
    # Show while talking: "[VISUAL: …]" is never said; a fork of the voice's conversation draws it in the
    # background, its file in the spool for the Mac (drawing, then ready), counted in Usage as its own kind.
    ids = []
    got = ask("[VOICE] just check the free disk space for me", ident=ids)
    tpath = os.path.join(root, "sessions", TALK, "trace.jsonl")
    deadline = time.time() + 5
    row = []
    while time.time() < deadline and not row:
        row = [json.loads(x) for x in open(tpath) if '"ev": "model"' in x and (ids[-1] if ids else "none") in x] if os.path.exists(tpath) else []
        time.sleep(0.05)
    check("a turn answered by a hand-off alone: flagged silent in the trace (they heard nothing: \"you didn't hear me\")",
          got.startswith("[HANDOFF]") and row and row[-1].get("outcome") == "handed" and row[-1].get("spoken") == ""
          and row[-1].get("silent") is True, (got, row[-1:]))
    ask("[VOICE] what time is it there")           # its note ("You took this deeper") goes with this turn
    got = ask("[VOICE] yes, put it up")
    check("[SHOW VISUAL] with a hand-off: the work goes, the tag after it for the Mac, never in the work",
          got == "[HANDOFF] Draw the release flow. [SHOW VISUAL]", got)
    ask('[VOICE] [CHOSE] They clicked a choice on the visual about \u00abcache times\u00bb: "One hour"')
    check("a choice clicked on a card reaches the voice as that line, never as words they said",
          texts("voice", TALK)[-1].rstrip().endswith('[CHOSE] They clicked a choice on the visual about \u00abcache times\u00bb: "One hour"')
          and "They said: [CHOSE]" not in texts("voice", TALK)[-1], texts("voice", TALK)[-1][-300:])
    m = sa.DeepMarker()
    check("the stream never says \"[SHOW VISUAL]\" either", "".join([m.feed("Here it is. [SHOW VI"), m.feed("SUAL] Look."), m.flush()])
          == "Here it is.  Look.")
    m = sa.DeepMarker()
    check("the stream never says a visual signal, even split across pieces",
          "".join([m.feed("Here's how [VIS"), m.feed("UAL: the steps, build then"), m.feed(" test] it goes."), m.flush()])
          == "Here's how  it goes.")
    ids = []
    got = ask("[VOICE] show me how the release goes", ident=ids)
    vfile = os.path.join(root, "agent", "spool", (ids[-1] if ids else "none") + ".v1.json")
    time.sleep(0.6)
    check("a visual asked for: nothing drawn until its answer is the turn's (cards came up for half-finished words)",
          not os.path.exists(vfile))
    open(os.path.join(root, "agent", "spool", (ids[-1] if ids else "none") + ".k"), "w").close()   # committed
    deadline = time.time() + 5
    while time.time() < deadline and not os.path.exists(vfile):
        time.sleep(0.02)
    t_answer = time.time()
    first = json.load(open(vfile)) if os.path.exists(vfile) else {}
    deadline = time.time() + 20
    while time.time() < deadline and not (os.path.exists(vfile) and json.load(open(vfile)).get("state") != "drawing"):
        time.sleep(0.05)
    v = json.load(open(vfile)) if os.path.exists(vfile) else {}
    side = [json.loads(l) for l in open(seen) if '"kind": "visual"' in l]
    check("a visual asked for: never in the answer, and drawn in its file, ready, by a fork of the voice's conversation",
          got == "Here's how it goes.  Build first, then test." and v.get("state") == "ready" and v.get("what") == "the release steps, build then test"
          and side and "--resume" in side[-1]["argv"] and "--tools" in side[-1]["argv"]
          and side[-1]["argv"][side[-1]["argv"].index("--tools") + 1] == "", (got, v, side[-1:]))
    told = side[-1]["argv"][side[-1]["argv"].index("-p") + 1] if side else ""
    check("  told no title or heading, and interactive only where it shows on the card",
          "no title or heading" in told and "looks like a choice" in told and "h1 (the title)" not in told, told[-900:])
    check("  a real choice, when it asks them to pick: data-choice, at most 6, and still asked aloud (clicks reach it)",
          "data-choice" in told and "at most 6" in told and "ask the question aloud" in told, told[-900:])
    check("  the side copy is told the card's real sizes and its 13 px text, so a diagram fits (one was scaled tiny)",
          all(w in told for w in ("You choose the card's size", "a width in px", "a diagram usually wants", "260", "about 750",
                                  "13 px", "flowchart TD", "(.steps)", 'data-size=\"large\"')), told[-900:])
    check("  its fragment: fences off, no <script src>, its own inline script kept, data-size on its root",
          v.get("html", "").startswith('<div data-size="small">') and "x.test" not in v.get("html", "")
          and 'sb.steps("#r")' in v.get("html", ""), v.get("html"))
    usage = os.path.join(root, "agent", "usage.jsonl")
    check("  counted in Usage as its own kind", os.path.exists(usage) and any(json.loads(l).get("kind") == "visual" for l in open(usage)))
    check("  the voice never waits: its answer came before the visual was drawn (2 s), which said so at once",
          first.get("state") == "drawing" and v.get("ms", 0) >= 1900, (first, v.get("ms")))
    ids = []
    ask("[VOICE] show me how the release goes", ident=ids)
    open(os.path.join(root, "agent", "spool", (ids[-1] if ids else "none") + ".x"), "w").close()   # they went on
    time.sleep(0.6)
    ask("[VOICE] what time is it there")
    check("  an early answer they went on from: its visual never drawn, and the next turn is told so",
          not os.path.exists(os.path.join(root, "agent", "spool", (ids[-1] if ids else "none") + ".v1.json"))
          and "Not shown: you asked for a visual on only part of their words" in texts("voice", TALK)[-1],
          texts("voice", TALK)[-1][-300:])
    # One that fails to draw: the voice may have said it's up, so its next turn is told (as with visuals off)
    ids = []
    ask("[VOICE] put up a chart of the outage", ident=ids)
    open(os.path.join(root, "agent", "spool", (ids[-1] if ids else "none") + ".k"), "w").close()   # committed
    vfile = os.path.join(root, "agent", "spool", (ids[-1] if ids else "none") + ".v1.json")
    deadline = time.time() + 10
    while time.time() < deadline and not (os.path.exists(vfile) and json.load(open(vfile)).get("state") == "failed"):
        time.sleep(0.05)
    ask("[VOICE] what time is it there")
    check("  a visual that fails to draw: its next turn is told, with why, so it never says one is up",
          os.path.exists(vfile) and json.load(open(vfile)).get("state") == "failed"
          and "Not shown: the visual you asked for couldn't be drawn (nothing came back): a chart that breaks"
          in texts("voice", TALK)[-1], texts("voice", TALK)[-1][-300:])
    json.dump({"call_visuals": False}, open(os.path.join(root, "config.json"), "w"))
    ids, n = [], len([l for l in open(seen) if '"kind": "visual"' in l])
    got = ask("[VOICE] show me how the release goes", ident=ids)
    time.sleep(1.0)
    check("  visuals off: nothing drawn, no file, and the signal still never said",
          not os.path.exists(os.path.join(root, "agent", "spool", (ids[-1] if ids else "none") + ".v1.json"))
          and len([l for l in open(seen) if '"kind": "visual"' in l]) == n and "[VISUAL" not in got, got)
    ask("[VOICE] what time is it there")
    check("    and its next turn is told nothing was drawn, so it never says it showed one",
          "Not shown: visuals are off in Settings" in texts("voice", TALK)[-1], texts("voice", TALK)[-1][-300:])
    os.remove(os.path.join(root, "config.json"))
    # An early question the user went on from: answered with a hand-off, then dropped (agentctl.cancel's
    # .x). The hand-off never went, so the voice's next turn has no note that it's working on it; it's held
    # for the turn: its own conversation still has that answer (a call's two lost hand-offs, 10/30), and its
    # answer once the turn is theirs (agentctl.kept's .k) sends what still stands, with all they said since.
    spool = os.path.join(root, "agent", "spool")
    mark = lambda ids, ext: open(os.path.join(spool, (ids[-1] if ids else "none") + ext), "w").close()
    held = lambda *tasks: (getattr(sa, "HELD", "(none) %s") % "\n".join("- " + t for t in tasks)) in texts("voice", TALK)[-1]
    ids = []                         # its question's id, as the agent queued it (never the trace, written behind)
    got = ask("[VOICE] can you ask the agent to create a new text file, just a sample text file?", ident=ids)
    task = got[len("[HANDOFF] "):]
    mark(ids, ".x")
    ids2 = []
    got2 = ask("[VOICE] and name it notes", ident=ids2)
    check("  the held note says it came from part of their words, to be judged afresh (once it anchored a misread)",
          "only part of their words" in sa.HELD and "afresh from everything" in sa.HELD and "drop it" in sa.HELD)
    check("a hand-off whose early question they went on from: held for the turn, never a note it's working on it",
          got.startswith("[HANDOFF]") and "You took this deeper" not in texts("voice", TALK)[-1] and held(task)
          and not os.path.exists(os.path.join(spool, ids[-1] + ".x")), (sorted(os.listdir(spool))[-6:], texts("voice", TALK)[-1][-400:]))
    check("  the turn's answer sends one hand-off, updated with what they said since",
          got2 == "[HANDOFF] Create a sample text file named notes.txt in the project folder.", got2)
    mark(ids2, ".k")                 # that answer was committed: the turn was theirs
    ask("[VOICE] what time is it there")
    check("  once committed, nothing is held any more", "Held, not sent" not in texts("voice", TALK)[-1]
          and not os.path.exists(os.path.join(spool, ids2[-1] + ".k")), texts("voice", TALK)[-1][-300:])
    ids = []
    got = ask("[VOICE] can you ask the agent to create a new text file, just a sample text file?", ident=ids)
    mark(ids, ".x")
    ids2 = []
    got2 = ask("[VOICE] actually never mind, forget it", ident=ids2)
    check("  a turn's answer with no [DEEP]: nothing goes deeper, though one was held",
          held(got[len("[HANDOFF] "):]) and not got2.startswith("[HANDOFF]"), (got2, texts("voice", TALK)[-1][-300:]))
    mark(ids2, ".k")
    ask("[VOICE] what time is it there")
    check("    and after it, nothing is held", "Held, not sent" not in texts("voice", TALK)[-1])
    # They went on while its answer was being written, a hand-off in it: dropped before its end (.c, .x), it never
    # goes, but its conversation has it, so it said "I've asked" for work that never went (talk-20261031-233531).
    got = ask("[VOICE] can you make the snake faster?")      # the stand-in drops it as it writes (no timing here)
    ask("[VOICE] what do you think?")
    check("a hand-off in an answer dropped while it was written: held for the turn, never a note it's working on it",
          not got.startswith("[HANDOFF]") and got != "(stopped)" and held("Make the snake move faster in the game.")
          and "You took this deeper" not in texts("voice", TALK)[-1], (got, texts("voice", TALK)[-1][-400:]))
    # They went on from an early question: only what they said since goes to the voice's conversation, which has
    # the rest (the whole turn each time filled it within an hour, compacted mid-call).
    ids = []
    ask("[VOICE] can you look at the", ident=ids)
    mark(ids, ".x")
    ask("[VOICE] can you look at the build log for me?")
    last = texts("voice", TALK)[-1]
    check("they went on from an early question: the voice gets only what they said since",
          last.endswith("goes on from their words in your last message: build log for me?") and "look at the build" not in last, last[-300:])
    ask("[VOICE] can you look at the build log for me? And the tests.")
    check("  a question they didn't go on from (answered, heard): their words in full",
          texts("voice", TALK)[-1].endswith("They said: can you look at the build log for me? And the tests."), texts("voice", TALK)[-1][-200:])
    os.remove(os.path.join(live, TALK + ".voice.json"))

    # The talk ended and a meeting is live. Typing into the talk continues it.
    open(current, "w").write(MEET + "\n")
    check("a meeting goes live: its Claude Codes start",
          wait(lambda: starts("meeting", MEET) and starts("voice", MEET)), records()[-2:])
    meet_pids = {r["pid"] for r in starts("meeting", MEET)}
    n = len(starts("meeting", TALK))
    check("typing into the ended talk is answered in the talk's own conversation",
          ask("and the p999?", "--session", TALK) == "MEETING ANSWER" and texts("meeting", TALK)[-1].endswith("and the p999?")
          and not any("p999" in t for t in texts("meeting", MEET)), texts("meeting", TALK)[-1:])
    restarted = starts("meeting", TALK)[n:]
    check("  resuming that conversation, not a new one",
          len(restarted) == 1 and "--resume" in restarted[0]["argv"], [r["argv"][-2:] for r in restarted])
    check("  and the meeting's Claude Code keeps running",
          ask("what did they say") == "MEETING ANSWER" and {r["pid"] for r in starts("meeting", MEET)} == meet_pids
          and texts("meeting", MEET)[-1].startswith("[QUESTION]"), (meet_pids, texts("meeting", MEET)[-1:]))
    os.remove(current)
    check("nothing live at all: typing into the talk still works",
          ask("one more thing", "--session", TALK) == "MEETING ANSWER" and texts("meeting", TALK)[-1].endswith("one more thing"))
    # An ended meeting is asked too: its last lines come with the question, never on their own.
    with open(os.path.join(live, MEET + ".jsonl"), "a") as fh:
        fh.write(json.dumps({"t": 90, "speaker": "Room", "text": "we decided to ship on Friday"}) + "\n")
    check("an ended meeting asked by typing: answered in its conversation, its last lines with the question",
          ask("what did we decide?", "--session", MEET) == "MEETING ANSWER"
          and texts("meeting", MEET)[-1].startswith("[QUESTION]") and "ship on Friday" in texts("meeting", MEET)[-1],
          texts("meeting", MEET)[-1:])
    spool = os.path.join(root, "agent", "spool")
    ident = "%d-0042" % int(time.time() * 1000)
    open(os.path.join(spool, ident + ".s"), "w").write("../../etc")
    open(os.path.join(spool, ident + ".q"), "w").write("odd one")
    wait(lambda: os.path.exists(os.path.join(spool, ident + ".a")))
    # That question went round the serve loop after the line was added: had the ended
    # meeting been pushed on its own, it would have been by then.
    check("  and nothing is pushed for the ended meeting on its own: it is not live",
          not any(t.startswith("[TRANSCRIPT]") for t in texts("meeting", MEET)), texts("meeting", MEET))
    check("a question naming no valid session is answered as usual, outside any talk",
          os.path.exists(os.path.join(spool, ident + ".a")) and not any("odd one" in t for t in texts("meeting", TALK)))

    ask("[WORDS] which words were misheard", "--session", MEET)
    check("Standby's word-fixes pass: to the deep agent as a question, its tag (the usage label) taken off",
          "which words were misheard" in texts("meeting", MEET)[-1] and "[WORDS]" not in texts("meeting", MEET)[-1],
          texts("meeting", MEET)[-1:])

    r = subprocess.run([PY, agent, "title", "--stdin"], input="You: how do we cache the map page?\n"
                       "Claude: Put the route in the session.", env=env, capture_output=True, text=True)
    prompt = ([x["prompt"] for x in records() if x.get("kind") == "title"] or [""])[-1]     # this one: the newest
    check("a talk's title comes from its chat", r.stdout.strip() == "Caching The Map Page"
          and "conversation" in prompt and "cache the map page" in prompt, (r.stdout, prompt[:200]))
    r = subprocess.run([PY, agent, "title", "--stdin"], input="hi", env=env, capture_output=True, text=True)
    check("  not from a word or two", r.stdout.strip() == "" and r.returncode == 0, r)
finally:
    srv.terminate()
    srv.wait(timeout=10)
print("\n" + open(log).read().strip()[-1200:])
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
