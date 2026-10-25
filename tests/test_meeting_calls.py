"""Only the push and the user's questions call the meeting agent, never twice back to back.

- A voice hand-off ([DEEP]) counts as the meeting agent's push, lines or none: the next
  automatic push waits its PUSH_MIN_GAP after it.
- A push that failed is tried again after its gap, not at every poll.
- Voice mode's own turns (an answer, a briefing) and idle time never call it.
- The agent's log counts the meeting agent's calls in the last minute, against its budget.

The stand-in Claude records which engine got which message, and when."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, re, subprocess, sys, tempfile, time
from importlib.machinery import SourceFileLoader

root = tempfile.mkdtemp(prefix="sbcalls-")
stub = os.path.join(root, "fake-claude")
seen = os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys, time
seen = %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
kind = "voice" if "--strict-mcp-config" in sys.argv else "meeting"
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
def say(text):
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": text}}})
    emit({"type": "result", "is_error": False, "result": text})
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if not line.strip(): continue
    msg = json.loads(line)
    if msg.get("type") != "user": continue
    text = msg["message"]["content"][-1]["text"]
    at = dict((h, ("\\n" + text).find("\\n[%%s]" %% h)) for h in ("TRANSCRIPT", "QUESTION", "VOICE", "BRIEFING"))
    head = min((i, h) for h, i in at.items() if i >= 0)[1] if max(at.values()) >= 0 else "other"
    words = text.rsplit("They said: ", 1)[-1]          # the user's own words in a voice turn
    open(seen, "a").write(json.dumps({"kind": kind, "head": head, "t": time.time(), "text": text}) + "\\n")
    if kind == "meeting" and head == "TRANSCRIPT" and "failpush" in text:
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Overloaded"})
    elif kind == "meeting":
        say("ANSWER." if head == "QUESTION" else "DEEP ANSWER." if head == "VOICE" else ".")
    elif head == "BRIEFING":
        say(".")
    elif "dig into" in words:
        say("[DEEP]")
    else:
        say("VOICE ANSWER.")
''' % seen)
os.chmod(stub, 0o755)

live = os.path.join(root, "live"); os.makedirs(live)
S = "meeting-20261013-090000"
json.dump({"session": S, "kind": "meeting", "mode": "mic"}, open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
tx = os.path.join(live, S + ".jsonl")
GAP = 2.0


def say(t, text):
    with open(tx, "a") as fh:
        fh.write(json.dumps({"t": t, "speaker": "Room", "text": text}) + "\n")


def got():
    return [json.loads(l) for l in open(seen)] if os.path.exists(seen) else []


def wait_for(cond, limit=10.0):
    end = time.time() + limit
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, STANDBY_PUSH_LINES="1",
           STANDBY_PUSH_SECONDS="1", STANDBY_PUSH_MIN_GAP=str(GAP), STANDBY_VOICE_BARE="0")
from agentpath import AGENT as agent
log = os.path.join(root, "serve.log")
srv = subprocess.Popen(["/usr/bin/python3", agent, "serve"], env=env, stderr=open(log, "w"))
ask = lambda q: subprocess.run(["/usr/bin/python3", agent, "q", q], env=env, capture_output=True,
                               text=True, timeout=30)
marks = {}
try:
    say(1, "line zero about the pricing")
    pushed = wait_for(lambda: any(g["kind"] == "meeting" and "line zero" in g["text"] for g in got()))
    time.sleep(GAP + 0.5)                                # the push's gap is over
    deep = ask("[VOICE] dig into the pricing")            # the voice hands it off; no lines unsent
    say(5, "line one right after the hand-off")
    wait_for(lambda: any("line one" in g["text"] for g in got() if g["kind"] == "meeting"), GAP + 3)

    marks["voice_from"] = time.time()
    own = ask("[VOICE] hello there")                      # the voice answers it itself
    names = os.path.join(root, "agent", "spool", "%d-0001" % int(time.time() * 1000))
    open(names + ".w.tmp", "w").write("voice")            # voice mode opened: the voice's briefing
    os.rename(names + ".w.tmp", names + ".w")
    wait_for(lambda: any(g["head"] == "BRIEFING" for g in got()), 5)
    time.sleep(GAP + 1)                                   # and idle, nothing new said
    marks["voice_to"] = time.time()

    typed = ask("what is Orion")
    say(9, "line two failpush")                           # this push fails, every time
    marks["fail_from"] = time.time()
    time.sleep(2 * GAP + 1)
    marks["fail_to"] = time.time()
finally:
    srv.terminate(); srv.wait(timeout=10)

fails = []


def check(label, ok, detail=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     %s" % (detail,))


rows = got()
meeting = [g for g in rows if g["kind"] == "meeting"]
check("the first line was pushed", pushed, rows)
check("the voice handed off, and the meeting agent answered", deep.stdout.strip() == "DEEP ANSWER.",
      deep.stdout + deep.stderr)
hand = [g for g in meeting if g["head"] == "VOICE"]
after = [g for g in meeting if g["head"] == "TRANSCRIPT" and "line one" in g["text"]]
check("the line said after the hand-off was pushed", bool(after), meeting)
check("that push waited its gap after the hand-off (Opus never twice back to back)",
      hand and after and after[0]["t"] - hand[0]["t"] >= GAP - 0.2,
      hand and after and round(after[0]["t"] - hand[0]["t"], 2))
check("the voice answered itself", own.stdout.strip() == "VOICE ANSWER.", own.stdout + own.stderr)
check("its answer, its briefing and idle time called no meeting agent",
      not [g for g in meeting if marks["voice_from"] <= g["t"] <= marks["voice_to"]],
      [(g["head"], round(g["t"] - marks["voice_from"], 2)) for g in meeting])
check("a typed question was answered", typed.stdout.strip() == "ANSWER.", typed.stdout + typed.stderr)
tried = [g for g in meeting if "failpush" in g["text"] and marks["fail_from"] <= g["t"] <= marks["fail_to"]]
check("a failed push is tried again after its gap, not at every poll",
      1 <= len(tried) <= 3 and all(b["t"] - a["t"] >= GAP - 0.2 for a, b in zip(tried, tried[1:])),
      [round(g["t"] - marks["fail_from"], 2) for g in tried])
heads = sorted(set(g["head"] for g in meeting))
check("only pushes and the user's questions reached the meeting agent",
      set(heads) <= {"TRANSCRIPT", "QUESTION", "VOICE"} and len(hand) == 1
      and len([g for g in meeting if g["head"] == "QUESTION"]) == 1, [(g["head"], g["text"][:40]) for g in meeting])

text = open(log).read()
counts = [(m.group(1), int(m.group(2))) for m in
          re.finditer(r"^meeting agent: (\w+), (\d+) calls? in the last minute \(budget 4\)", text, re.M)]
check("a log line per meeting agent call, with its kind",
      len(counts) == len(meeting) and {"listen", "deep", "question"} <= set(k for k, _n in counts), counts)
check("counted in the last minute: the last line has them all",
      counts and counts[-1][1] == len(meeting) and [n for _k, n in counts] == list(range(1, len(meeting) + 1)),
      (counts, len(meeting)))
check("the log is ASCII", all(ord(c) < 128 for c in text))

# The count on its own: the budget's fifth call in a minute is over it; a minute on, one.
ag = SourceFileLoader("ag", agentpath.AGENT).load_module()
import io
err, sys.stderr = sys.stderr, io.StringIO()
try:
    ns = [ag.count_meeting_call("listen", now=1000.0 + i) for i in range(5)] + \
         [ag.count_meeting_call("question", now=1064.5)]
    said = sys.stderr.getvalue()
finally:
    sys.stderr = err
check("five calls in a minute are over the budget of 4; a minute on, one",
      ns == [1, 2, 3, 4, 5, 1] and said.count("OVER BUDGET") == 1
      and "listen, 5 calls in the last minute (budget 4)  OVER BUDGET" in said, (ns, said))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
if fails:
    print(text[-3000:])
sys.exit(1 if fails else 0)
