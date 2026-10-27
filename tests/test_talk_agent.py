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


def ask(question, *extra):
    p = subprocess.run([PY, agent, "q", "--json"] + list(extra) + [question], env=env,
                       capture_output=True, text=True, timeout=60)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
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
    check("  a result answered with words and a task after [DEEP]: only the words are said, never the task",
          got == "That stopped, so I'll pick it up." and "regression" not in got and len(texts("meeting", TALK)) == asked, got)
    # Back from a pause where results waited: one [RESUMED] turn, not their words; what waited as news
    ask("[VOICE] [RESUMED] They're back. While paused, they saw something was waiting.\n[RESULT done] The film is built.\n[END RESULT]")
    last = texts("voice", TALK)[-1]
    check("back from a pause: the [RESUMED] line, then what waited as news; no \"They said\"",
          "[RESUMED] They're back. While paused, they saw something was waiting.\n\n[RESULT] Not from the user: the work you took "
          "deeper is done. What it came to:\n<<<\nThe film is built.\n>>>" in last and "They said:" not in last, last[-400:])
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
    check("a call's voice: it IS the session, its mouth and ears, with its own deeper side; one self; its judgement; its folder",
          call.startswith("You are the user's Claude Code session in /Users/you/code/orion, on a voice call with them: its mouth and ears.")
          and "It's all you: same agent, same work, same memory, so never speak of a separate agent or helper." in call
          and "Anything that does something, like changing a file, running a command or searching the web, is your deeper side's" in call
          and "reply [DEEP] and what to do, in your words, with what it needs to know." in call
          and "Answer them first, then take it deeper; it works while you keep talking." in call
          and "let me check" not in call
          and "Claude Code is your engine" not in call and not re.search(r"\bhand(?:s|ed|ing)?\b|hand-off", call)
          and "in /Users/you/code/orion" in call
          and "[QUIET]" in call and "[RESULT]" in call and len(call.split()) < 300, (len(call.split()), call[:300]))
    check("  its opening: only a hello, under ten words in all, naming what it's picking up",
          "[CALL START] means the call just connected: reply with only a hello your way, under ten words in all, naming anything you're picking up." in call)
    check("  it may handle things itself: it can read and search where its session works, and nothing forces going deeper",
          "Your own tools are for looking: you can read and search where your session works, and ~/.standby." in call
          and "So whatever they ask for is yours to do" in call
          and "Don't try it yourself first; a tool refusing you only means it's your deeper side's to do, never that it can't be done." in call
          and "this whole Claude Code session, with all its context and every tool and connection it has." in call
          and "Never say you will look into something" not in call and "SUGGEST" not in call)
    check("  honest hand-offs: nothing started until the session says it has it; never a guess at why not",
          "Only your session's notes say what has happened, so never guess: nothing has started until a [SESSION] note says "
          "your session has it (till then, you're sending it), and a [RESULT] says what it came to." in call)
    ledger = " ".join(sa.voice_prelude(TALK, ledger=True).split())
    check("  on the record, the voice can stop or change a task by its number in TASKS",
          "To stop a task, add [CANCEL #n] with its number from TASKS; to change one, stop it and send the new work with [DEEP]." in ledger)
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
    check("the voice reads only its folder and ~/.standby: reads elsewhere refused, the guarded folders denied",
          perms.get("blockReadsOutsideWorkingDirectories") is True and sa.ROOT in dirs
          and {"Read(~/Library/**)", "Read(~/Desktop/**)", "Read(~/Downloads/**)", "Read(~/Pictures/**)"} <= set(perms.get("deny") or []),
          (perms, dirs))
    sa._CAPS.update(auto=False, dont_ask=True)
    talk = sa.build_argv(TALK, "haiku", True, "00000000-0000-4000-8000-000000000002", stream=False, kind="meeting")
    tperms = json.loads(talk[talk.index("--settings") + 1]).get("permissions") or {} if "--settings" in talk else {}
    tdirs = [talk[i + 1] for i, x in enumerate(talk) if x == "--add-dir"]
    check("  the meeting's agent too: ~/.claude and ~/.standby added, nothing else readable, Library denied",
          tperms.get("blockReadsOutsideWorkingDirectories") is True and "Read(~/Library/**)" in (tperms.get("deny") or [])
          and os.path.expanduser("~/.claude") in tdirs and sa.ROOT in tdirs, (tperms, tdirs))
    home = os.path.expanduser("~")
    f = sa.fence([os.path.join(home, "Documents", "projects", "orion"), sa.ROOT])
    check("  a folder inside a guarded one: that one isn't denied (the fence still keeps the rest of it out)",
          "Read(~/Documents/**)" not in f["deny"] and "Read(~/Library/**)" in f["deny"] and "Read(~/Desktop/**)" in f["deny"], f)
    # A call's voice reads where its session works (the Mac lists those folders in voice.json "dirs"): the
    # snake game the session built in its own folder (talk-20261024-232831), not only the Standby repo.
    snake, qa = tempfile.mkdtemp(prefix="sbsnake-"), tempfile.mkdtemp(prefix="sbqa-")
    json.dump({"handoff": True, "folder": "/Users/you/code/orion", "dirs": [snake, qa, "/no/such/folder"]},
              open(os.path.join(live, TALK + ".voice.json"), "w"))
    argv = sa.build_argv(TALK, "haiku", True, "00000000-0000-4000-8000-000000000003", stream=False, kind="voice")
    adds = [argv[i + 1] for i, x in enumerate(argv) if x == "--add-dir"]
    cperms = json.loads(argv[argv.index("--settings") + 1]).get("permissions") or {}
    check("a call's voice reads where its session works: each of its folders added, reads elsewhere still refused, guarded ones denied",
          snake in adds and qa in adds and "/no/such/folder" not in adds and sa.ROOT in adds
          and cperms.get("blockReadsOutsideWorkingDirectories") is True and "Read(~/Library/**)" in (cperms.get("deny") or []), (adds, cperms))
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
          len(spawns) >= 6 and all(("--tools\", tools" in x or "ALLOWED" in x) for x in bare) and len(bare) == 2, bare)
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
    check("  a result: background, what they're saying first; at a natural moment, the gist, then they ask; with a lot, it asks first",
          "A result is background; what they're saying comes first. At a natural moment, give the gist and let them ask; "
          "with a lot, ask first if they want it." in call, call[-700:])
    check("  a call's voice ends the call itself: its own goodbye and [END]; if unsure, ask",
          "Ending the call is yours: when they ask, or are clearly done, say goodbye and add [END]; if unsure, ask." in call, call[-400:])
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
    # An early question the user went on from: answered with a hand-off, then dropped (agentctl.cancel's
    # .x). The hand-off never went, so the voice's next turn has no note that it's working on it.
    models = lambda: [r for r in map(json.loads, open(tpath)) if r["ev"] == "model"]
    n_models = len(models())
    got = ask("[VOICE] can you ask the agent to create a new text file, just a sample text file?")
    wait(lambda: len(models()) > n_models, 10)
    qid = models()[-1]["q"]
    open(os.path.join(root, "agent", "spool", qid + ".x"), "w").close()
    ask("[VOICE] what time is it there")
    check("a hand-off whose early question they went on from: the voice keeps no note it's working on it",
          got.startswith("[HANDOFF]") and "You took this deeper" not in texts("voice", TALK)[-1]
          and not os.path.exists(os.path.join(root, "agent", "spool", qid + ".x")), (qid, sorted(os.listdir(os.path.join(root, "agent", "spool")))[-6:], texts("voice", TALK)[-1][-300:]))
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
    prompt = next((x["prompt"] for x in records() if x.get("kind") == "title"), "")
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
