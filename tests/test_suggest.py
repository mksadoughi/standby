"""Suggested questions, the app's side (mac/ui.py), with the agent and the
transport faked: /api/session carries the newest set, without anything already
asked; an answer's follow-ups arrive in its own stream (never as a progress step,
never to the voice); the agent's file is read directly on this Mac when the page
asks; an ended meeting adds the starters, a talk doesn't."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, and no child may crash)
import json, os, shutil, socket, sys, tempfile, threading, time, urllib.request
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbsuggestui-"))
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
os.environ.update(STANDBY_ROOT=str(root), STANDBY_UI_PORT=str(PORT))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
import agentctl  # noqa: E402
import transport  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:500])


def wait(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


B = "http://127.0.0.1:%d" % PORT


def call(path, body=None):
    req = urllib.request.Request(B + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


class FakeTransport(object):
    mode = "local"
    reads = []
    files = {}

    def read(self, rel):
        self.reads.append(rel)
        return self.files.get(rel)

    def ensure_agent(self, model=None):
        return True, "ok"


ft = FakeTransport()
transport.for_config = lambda cfg=None: ft
live = {"sid": None}
ui.live_session = lambda: live["sid"]
ui.title_talk = lambda sid: None
seen_events, voice_events = [], []


def fake_ask(question, err=None, on_progress=None, on_queued=None, session=None,
             where=None):
    on_progress({"kind": "tool", "text": "Reading the transcript"})
    on_progress({"kind": "suggest", "items": ["How long do routes live?", "What invalidates a route?"], "from": "answer"})
    return "The cache is session-scoped."


agentctl.ask = fake_ask
server = ui.serve()
threading.Thread(target=server.serve_forever, daemon=True).start()


def session(sid, kind="meeting", transcript=True):
    (ui.SESSIONS / sid).mkdir(parents=True, exist_ok=True)
    (ui.SESSIONS / sid / "meta.json").write_text(json.dumps({"started": time.time(), "kind": kind}))
    if transcript:
        (ui.SESSIONS / sid / "transcript.jsonl").write_text(json.dumps({"t": 1, "text": "hello"}) + "\n")
    return sid


def agent_file(sid, items, at, source="listen"):
    data = json.dumps({"at": at, "from": source, "items": items}).encode()
    (root / "live").mkdir(exist_ok=True)
    (root / "live" / (sid + ".suggest.json")).write_bytes(data)
    FakeTransport.files["live/%s.suggest.json" % sid] = data


MEET = live["sid"] = session("meeting-20261004-130000")
check("nothing suggested yet: null", call("/api/session/" + MEET)["suggest"] is None)
agent_file(MEET, ["Who owns the cache work?", "What's the risk for guest riders?"], 1000.0)
s = call("/api/session/" + MEET)["suggest"]
check("on this Mac, the agent's file is read when the page asks", s and s["items"] == ["Who owns the cache work?",
      "What's the risk for guest riders?"] and s["from"] == "listen" and s["session"] == MEET, s)

ui.ask_spoken(MEET, "who owns the cache work", lambda ev: None, lambda qid: None)
s = call("/api/session/" + MEET)["suggest"]
check("an answer's follow-ups replace them: newest wins", s and s["from"] == "answer"
      and s["items"] == ["How long do routes live?", "What invalidates a route?"], s)
pending_steps = [m.get("steps") for m in ui.read_jsonl(ui.SESSIONS / MEET / "chat.jsonl") if m["role"] == "ai"][-1]
check("  never a progress step", pending_steps == ["Reading the transcript"], pending_steps)
agent_file(MEET, ["Who owns the cache work?", "When is the load test?"], 1000.0)
s = call("/api/session/" + MEET)["suggest"]
check("an unchanged agent file (same 'at') doesn't undo the newer answer set", s["from"] == "answer", s)
agent_file(MEET, ["Who owns the cache work?", "When is the load test?"], 1020.0)
s = call("/api/session/" + MEET)["suggest"]
check("a new listen set wins again, minus what was asked (any case or punctuation)",
      s["from"] == "listen" and s["items"] == ["When is the load test?"], s)

answers = []
ui.ask_spoken(MEET, "does it cover guests", lambda ev: answers.append(ev), lambda qid: None)
check("a voice answer's follow-ups never reach the voice", answers and all(e.get("kind") != "suggest" for e in answers)
      and call("/api/session/" + MEET)["suggest"]["from"] == "answer", answers)

agent_file(MEET, ["What did finance say?"], 1040.0)
check("a newer listen set, read when the page asks", call("/api/session/" + MEET)["suggest"]["items"]
      == ["What did finance say?"])
TALK = session("talk-20261004-131500", kind="talk", transcript=False)
live["sid"] = None

# after: an ended meeting adds the starters; a talk doesn't
s = call("/api/session/" + MEET)["suggest"]
check("an ended meeting: the starters first, then the last suggestions", s["from"] == "end" and s["items"] ==
      ["Summarize", "Action items", "Draft a follow-up", "What did finance say?"], s)
ui.append_chat(MEET, "me", "Summarize")
s = call("/api/session/" + MEET)["suggest"]
check("  a starter asked drops out, so a live one steps into the first three",
      s["items"][:3] == ["Action items", "Draft a follow-up", "What did finance say?"], s)
ui.keep_suggest(TALK, ["What else should I check?"], "answer")
s = call("/api/session/" + TALK)["suggest"]
check("an ended talk keeps its follow-ups, no starters", s["items"] == ["What else should I check?"]
      and s["from"] == "answer", s)
ui.SUGGEST.clear()
s = call("/api/session/" + TALK)["suggest"]
check("  kept in the session folder, across launches", s and s["items"] == ["What else should I check?"], s)
old = session("meeting-20261001-090000")
check("an ended meeting with nothing suggested: only the starters",
      call("/api/session/" + old)["suggest"]["items"] == ui.STARTERS)

# the real transport reads
lt = transport.LocalTransport({"model": "opus", "voice_model": "sonnet"})
lt.root = root
check("the local transport reads the agent's file, or None", lt.read("live/%s.suggest.json" % MEET)
      and lt.read("live/nope.json") is None)

server.shutdown()
shutil.rmtree(root, ignore_errors=True)
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
