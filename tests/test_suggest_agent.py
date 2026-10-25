"""The agent's side of suggested questions: replies Claude gives anyway end
with up to 3 "SUGGEST:" lines. They are cut out of the notes, the answer and what
the voice says (even split across streamed pieces), kept in
live/<session>.suggest.json (at most 3, at most 8 words, no repeats, none already
asked), sent with an answer in its own stream, and an edited meeting prompt still
gets the rule. The stand-in Claude replies with SUGGEST lines and records what it
was given."""
import json, os, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbsuggest-")
stub = os.path.join(root, "fake-claude")
seen = os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys
seen = %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
kind = "voice" if "--strict-mcp-config" in sys.argv else "meeting"
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
def stream(pieces):
    for p in pieces:
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": p}}})
    emit({"type": "result", "is_error": False, "result": "".join(pieces)})
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
        text = "".join(b.get("text", "") for b in msg["message"]["content"] if b["type"] == "text")
        open(seen, "a").write(json.dumps({"kind": kind, "text": text}) + "\\n")
        if text.startswith("[TRANSCRIPT]") and "quiet" in text:
            emit({"type": "result", "is_error": False, "result": "."})
        elif text.startswith("[TRANSCRIPT]"):
            emit({"type": "result", "is_error": False, "result":
                  "Orion is 40%% of the budget.\\nSUGGEST: Who owns the cache work?\\n"
                  "- SUGGEST: What's the risk for guest riders?\\n"
                  "SUGGEST: Could you please explain every single part of that plan\\n"
                  "SUGGEST: who owns the cache work\\nSUGGEST: When is the load test?"})
        elif kind == "voice":
            stream(["Sure. The cache ", "covers logged-in users.\\nSUG", "GEST: What about ", "guests?\\nSuggest: Who decides?"])
        else:
            emit({"type": "result", "is_error": False, "result":
                  "The cache is session-scoped.\\n\\nSUGGEST: How long do routes live?\\nSUGGEST: What invalidates a route?"})
''' % seen)
os.chmod(stub, 0o755)
live = os.path.join(root, "live")
os.makedirs(live)
MEET = "meeting-20261004-120000"
json.dump({"session": MEET, "mode": "mic"}, open(os.path.join(live, MEET + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(MEET + "\n")
transcript = os.path.join(live, MEET + ".jsonl")
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub,
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


def wait(pred, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def suggest():
    try:
        return json.load(open(os.path.join(live, MEET + ".suggest.json")))
    except (IOError, ValueError):
        return {}


def say(text, t):
    with open(transcript, "a") as fh:
        fh.write(json.dumps({"t": t, "speaker": "Room", "text": text}) + "\n")


def ask(question):
    p = subprocess.run([PY, agent, "q", "--json", question], env=env, capture_output=True, text=True, timeout=60)
    return [json.loads(l) for l in p.stdout.splitlines() if l.strip()]


srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"], env=env, stderr=open(log, "w"))
try:
    wait(lambda: "serving" in open(log).read())
    say("the routing call to orion is most of it", 5)
    check("a listen reply's suggestions are kept for the app", wait(lambda: suggest().get("items")), suggest())
    s = suggest()
    check("  at most 3, at most 8 words (longer dropped, not cut), no repeats",
          s["items"] == ["Who owns the cache work?", "What's the risk for guest riders?", "When is the load test?"]
          and s["from"] == "listen", s)
    notes = open(os.path.join(live, MEET + ".notes.md")).read()
    check("  and cut out of the notes", "Orion is 40%" in notes and "SUGGEST" not in notes, notes)
    first = records()[-1]["text"]
    check("  the default prompt has the rule, so the push doesn't repeat it", "SUGGEST" not in first, first)

    say("quiet now", 9)
    wait(lambda: len([r for r in records() if "quiet" in r["text"]]) >= 1)
    time.sleep(0.5)
    check("a reply without suggestions keeps the last ones (for 5 minutes)", suggest().get("items") == s["items"], suggest())
    data = suggest()
    data["at"] -= 301
    json.dump(data, open(os.path.join(live, MEET + ".suggest.json"), "w"))
    say("quiet again", 12)
    check("  and clears them once they are older", wait(lambda: suggest().get("items") == []), suggest())

    events = ask("Who owns the cache work?")
    answer = events[-1]["text"]
    sug = [e for e in events if e.get("kind") == "suggest"]
    check("an answer's follow-ups come back in its stream, before the answer",
          sug and sug[0]["items"] == ["How long do routes live?", "What invalidates a route?"]
          and events.index(sug[0]) < len(events) - 1, events)
    check("  cut out of the answer the user sees", answer == "The cache is session-scoped." and "SUGGEST" not in answer, answer)
    logged = [json.loads(l) for l in open(os.path.join(root, "agent", "answers.log"))][-1]
    check("  and out of the answers log (the bug from the test pass)", logged["a"] == "The cache is session-scoped."
          and "SUGGEST" not in logged["a"], logged["a"])
    check("  and kept as the newest set, from the answer", suggest().get("from") == "answer"
          and suggest().get("items") == sug[0]["items"], suggest())
    say("orion again", 15)
    wait(lambda: suggest().get("from") == "listen")
    check("a question already asked is never suggested again",
          "Who owns the cache work?" not in suggest().get("items", []) and suggest().get("items"), suggest())

    events = ask("[VOICE] does the cache cover guests")
    spoken = "".join(e.get("text", "") for e in events if e.get("kind") == "delta")
    check("the voice: SUGGEST lines never reach what is spoken, even split across pieces",
          spoken.startswith("Sure. The cache covers logged-in users.") and "SUG" not in spoken.upper().replace("SURE", ""),
          spoken)
    check("  nor the answer", "SUGGEST" not in events[-1]["text"].upper(), events[-1]["text"])
    check("  and its follow-ups are kept", [e for e in events if e.get("kind") == "suggest"]
          and suggest().get("items") == ["What about guests?", "Who decides?"], suggest())
finally:
    srv.terminate()
    srv.wait(10)

# an edited meeting prompt still gets the rule, in the push and with questions
open(os.path.join(root, "prompt.md"), "w").write("You are my meeting helper. Session {session}.\n")
srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"], env=env, stderr=open(log, "a"))
try:
    wait(lambda: open(log).read().count("serving") >= 2)
    n = len(records())
    say("a new line for the edited prompt", 20)
    wait(lambda: any(r["text"].startswith("[TRANSCRIPT]") for r in records()[n:]))
    push = [r for r in records()[n:] if r["text"].startswith("[TRANSCRIPT]")][-1]["text"]
    check("an edited prompt without the rule: the push carries it in one line",
          push.count("SUGGEST") == 1 and "They show as buttons" in push, push[-300:])
    ask("what changed?")
    q = [r for r in records() if r["kind"] == "meeting" and "what changed?" in r["text"]][-1]["text"]
    check("  and so does a typed question", "SUGGEST: <question>" in q, q[:300])
finally:
    srv.terminate()
    srv.wait(10)
    if fails:
        print(open(log).read()[-2500:])

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
