"""A call's voice turns built from the call's record (plugin/bin/sbledger.py), each in a Claude Code of
its own: what the voice knows of the call is what Standby kept (what was said, and what of each reply was
heard), never the model's own memory. An answer to an early ask that was dropped never reaches a later
turn (the joke said to nobody, 10/24 10:21:14; "it's editing the game file now", 10:12:04). Notes the Mac
put with their words are facts of their own, never inside them. Each turn's input begins with the
previous one's (the prompt cache). A spare Claude Code is started ahead, so a turn never waits for one.

The stand-in claude records how it was started and the blocks of each turn it was given. The app's
side (VoiceSession._record, ui._lite_tell) is played by the test: it writes the record as they do."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, subprocess, sys, tempfile, time

root = os.path.realpath(tempfile.mkdtemp(prefix="sbledger-"))
stub, seen = os.path.join(root, "fake-claude"), os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys
seen = %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>\\n-n, --name <name>')
    sys.exit(0)
kind = "voice" if "--strict-mcp-config" in sys.argv else "meeting"
open(seen, "a").write(json.dumps({"start": kind, "pid": os.getpid(), "argv": sys.argv[1:]}) + "\\n")
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init", "model": "claude-haiku-5-5"})
for line in sys.stdin:
    m = json.loads(line)
    if m.get("type") != "user":
        continue
    blocks = [b["text"] for b in m["message"]["content"]]
    open(seen, "a").write(json.dumps({"turn": kind, "pid": os.getpid(), "blocks": blocks}) + "\\n")
    last = blocks[-1]
    reply = ("Hi, I'm here." if last == "[CALL START]" else
             "Taking it deeper. [DEEP]" if "dig into" in last else
             "Look into why the build fails." if last.startswith("[TASK]") else
             "That's done. [DEEP] redo it" if last.startswith("RESULT") else
             "Okay, here's a joke about snakes." if "joke" in last else
             "Got it, the board is ten cells." if "board" in last else "Sure.")
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}})
    emit({"type": "result", "is_error": False, "result": reply,
          "modelUsage": {"claude-haiku-5-5": {"inputTokens": 10, "outputTokens": 5}}})
''' % seen)
os.chmod(stub, 0o755)
live = os.path.join(root, "live")
os.makedirs(live)
TALK = "talk-20261025-090000"
os.makedirs(os.path.join(root, "sessions", TALK))
json.dump({"session": TALK, "mode": "mic", "kind": "talk"}, open(os.path.join(live, TALK + ".meta.json"), "w"))
json.dump({"handoff": True, "name": "Claude Code · voice", "folder": root, "gist": "They are building a snake game."},
          open(os.path.join(live, TALK + ".voice.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(TALK + "\n")
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, STANDBY_CALL_TRACE="1")
env.pop("STANDBY_VOICE_LEDGER", None)
json.dump({"voice": {"ledger": True}}, open(os.path.join(root, "config.json"), "w"))   # off by default: on here
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "bin"))
os.environ["STANDBY_ROOT"] = root
import sbledger  # noqa: E402
sbledger.ROOT = root
from agentpath import AGENT as agent  # noqa: E402
PY = "/usr/bin/python3"
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


def records():
    try:
        return [json.loads(l) for l in open(seen)]
    except IOError:
        return []


def turns():
    return [r for r in records() if r.get("turn") == "voice"]


def voice_starts():
    return [r for r in records() if r.get("start") == "voice"]


def wait(pred, secs=60):               # generous: the suites run five at a time; each wait ends as soon as it holds
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def ask(question):
    p = subprocess.run([PY, agent, "q", "--json", "[VOICE] " + question], env=env, capture_output=True, text=True,
                       timeout=60)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return lines[-1].get("text") if lines else p.stderr


def start_of(turn):
    return next((r for r in voice_starts() if r["pid"] == turn["pid"]), {})


def sid_of(turn):
    a = start_of(turn).get("argv") or []
    return a[a.index("--session-id") + 1] if "--session-id" in a else None


def parent_of(r):
    a = (r.get("argv") if "argv" in r else start_of(r).get("argv")) or []
    return a[a.index("--resume") + 1] if "--resume" in a else None


def said(question, heard, cut=False):
    """The app, at the turn's end (VoiceSession._record): what was asked, and what of the reply they heard."""
    sbledger.append(TALK, "asked", question)
    sbledger.append(TALK, "you", heard, cut=cut, unsaid=not heard)


# The renderer: a turn's question as a block is the same before and after it's committed
check("a question's block: their words as THEM, with the Mac's notes and the cut-off note out",
      sbledger.asked_block('(They went on before hearing your answer.) [RESULT done] built\n[END RESULT]\nok, next')
      == "THEM: ok, next" and sbledger.folded("[RESULT done] built\n[END RESULT]\nok") == ["[RESULT done] built\n[END RESULT]"])
check("  a result told alone is a RESULT, never their words", sbledger.asked_block("[RESULT done] The film is built.")
      == "RESULT (done): The film is built.")
check("  a reply they heard part of: YOU, cut where it stopped; one they heard none of: no line",
      sbledger.block({"kind": "you", "text": "Here's the plan, first", "cut": True}) == "YOU: Here's the plan, first [cut here]"
      and sbledger.block({"kind": "you", "text": "", "unsaid": True}) is None)

srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "haiku", "serve"], env=env,
                       stderr=open(os.path.join(root, "serve.log"), "w"))
try:
    # The spare is started as the server warms the call up: under a loaded Mac (suites run five at a time) that
    # can take a while, so it's waited for, never assumed; the greeting starts one in any case
    ahead = wait(lambda: voice_starts(), 90)
    check("a call: its first turn's Claude Code is started before the call's first word (a spare)", ahead,
          records()[-3:])
    if not ahead:
        ask("[CALL START]")                       # what the rest needs: a first turn's Claude Code
        wait(lambda: voice_starts(), 60)
    v = (voice_starts() or [{"argv": []}])[0]["argv"]
    check("  the call's first: a conversation of its own, named (no title call for each turn)",
          "--session-id" in v and "--resume" not in v and "-n" in v and "--no-session-persistence" not in v, v)
    sp = v[v.index("--append-system-prompt") + 1]
    check("  the call's prompt says how to read the blocks", "THEM is what they said" in sp and "[cut here]" in sp, sp[:300])

    hello = ask("[CALL START]\nTheir last message to you, before the call:\nlet's add a score")
    t1 = turns()[-1]
    check("the greeting: the opening (session, summary, their last message) as a fact, then [CALL START]",
          hello == "Hi, I'm here." and len(t1["blocks"]) == 2 and t1["blocks"][0].startswith("[CALL] Session: " + TALK)
          and "snake game" in t1["blocks"][0] and "let's add a score" in t1["blocks"][0] and t1["blocks"][1] == "[CALL START]",
          t1["blocks"])
    first = sid_of(t1)
    said("[CALL START]", "Hi, I'm here.")
    check("  committed: its conversation is the call's now, and the next turn's fork is started from it, ahead",
          wait(lambda: any(parent_of(r) == first for r in voice_starts())), [(r.get("argv") or [])[-4:] for r in voice_starts()])
    # An early ask on likely-final words, answered on a fork, then dropped (they went on): never committed
    early = ask("I want you to tell me a joke")
    t2 = turns()[-1]
    check("an early ask: a fork of the call's conversation, given only what it doesn't have: their words",
          early == "Okay, here's a joke about snakes." and parent_of(t2) == first
          and t2["blocks"] == ["THEM: I want you to tell me a joke"], (parent_of(t2), t2["blocks"]))
    dropped = sid_of(t2)
    # What they really said (the turn went on): another fork of the same conversation; its reply cut where they stopped it
    full = "I want you to tell me a joke and then I'll say pause, okay?"
    ask(full)
    t3 = turns()[-1]
    check("  what they really said: a fork of the same conversation, never of the early one",
          parent_of(t3) == first and t3["blocks"] == ["THEM: " + full], (parent_of(t3), t3["blocks"]))
    kept = sid_of(t3)
    said(full, "Okay, go ahead and say", cut=True)
    ask("[RESULT done] The board is ten cells now.\n[END RESULT]\nAnd the board?")
    t4 = turns()[-1]
    check("the committed turn's fork is the conversation now; the early one is gone (10:21:14: no joke said to nobody)",
          parent_of(t4) == kept and parent_of(t4) != dropped
          and open(os.path.join(root, "agent", TALK + ".voice.uuid")).read().strip() == kept, (parent_of(t4), kept, dropped))
    check("  what they heard of its reply, cut where it stopped, then a result the Mac put with their words as a fact of its own",
          t4["blocks"] == ["YOU, as they heard it: Okay, go ahead and say [cut here]",
                           "RESULT (done, while they were talking): The board is ten cells now.", "THEM: And the board?"],
          t4["blocks"])
    check("  a Claude Code per turn, each started ahead", len(set(r["pid"] for r in turns())) == len(turns()))
    # Tasks from the session (the app's tasks.txt): an entry when they change, just before their words
    open(os.path.join(root, "sessions", TALK, "tasks.txt"), "w").write("#1 running 20s: Make the board ten cells\n")
    said("And the board?", "Got it, the board is ten cells.")
    ask("How's it going?")
    t5 = turns()[-1]["blocks"]
    check("tasks: the call's tasks as they stand, once, right before their new words",
          t5[-2] == "TASKS:\n#1 running 20s: Make the board ten cells" and t5[-1] == "THEM: How's it going?"
          and sum(b.startswith("TASKS") for b in t5) == 1, t5[-3:])
    check("  carried: a task line in a [TASKS] entry has reached the voice (the told-when-idle check reads it)",
          sbledger.carried(TALK, "#1 running 20s: Make the board ten cells")
          and not sbledger.carried(TALK, "#2 done: Something else"))
    said("How's it going?", "Sure.")
    ask("Anything else?")
    t6 = turns()[-1]["blocks"]
    check("  unchanged, not again: the conversation has it", t6 == ["THEM: Anything else?"], t6)
    tpath = os.path.join(root, "sessions", TALK, "trace.jsonl")
    wait(lambda: "THEM: Anything else?" in open(tpath).read(), 5)          # written every half second
    tr = [json.loads(x) for x in open(tpath)]
    said("Anything else?", "Sure.")
    got = ask("can you dig into the logs")
    last = turns()[-2:]
    check("a bare [DEEP] (no task): the voice is asked for it in the same turn's Claude Code, and that goes",
          got == "[HANDOFF] Look into why the build fails." and last[-1]["blocks"][-1].startswith("[TASK]")
          and last[-1]["pid"] == last[-2]["pid"], (got, last))
    said("can you dig into the logs", "Taking it deeper.")
    got = ask("[RESULT done] The build passes now.")
    check("a result told alone: a RESULT block, never their words, and never handed on again",
          turns()[-1]["blocks"][-1] == "RESULT (done): The build passes now." and got == "That's done.", (got, turns()[-1]["blocks"][-2:]))
    vt = [r for r in tr if r["ev"] == "voice_turn"]
    check("the trace: each turn's Claude Code, whether it was the spare, its age and how long taking it took",
          vt and all("spare" in r and "age_ms" in r and "take_ms" in r for r in vt) and vt[-1]["spare"], vt[-2:])
    mt = [r for r in tr if r["ev"] == "model"]
    check("  and each turn's whole input, blocks and all, and its tokens (the cache numbers)",
          mt and "THEM: Anything else?" in mt[-1]["sent"] and mt[-1].get("tokens") is not None, mt[-1:])
finally:
    srv.terminate()
    srv.wait(10)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
