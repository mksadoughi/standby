"""A call's trace (sessions/<sid>/trace.jsonl, plugin/bin/sbtrace.py): a fake call, the app's voice session
and the recorder's VoiceIO with a fake engine, a greeting then one turn of theirs, and its trace read back.
Each kind of event is there, with its turn; nothing is written from the engine's audio side; the file is
the session's own (0600) and nowhere else; tools/call_trace.py makes the page, each turn's breakdown first.
No audio, no model."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, stat, subprocess, sys, tempfile, threading, time, types
from pathlib import Path
from importlib.machinery import SourceFileLoader

root = Path(tempfile.mkdtemp(prefix="sbtrace-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
sys.modules.setdefault("sounddevice", types.SimpleNamespace(
    InputStream=None, query_devices=lambda i=None: [], default=types.SimpleNamespace(device=[0, 0])))
sb = SourceFileLoader("sb", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac", "standby.py")).load_module()
import voice  # noqa: E402
import sbtrace  # noqa: E402

os.environ.pop("STANDBY_CALL_TRACE", None)
(root / "sessions" / "talk-off").mkdir(parents=True)
for i in range(50):
    sbtrace.event("talk-off", "say", i=i)
sbtrace.flush()
OFF = (not (root / "sessions" / "talk-off" / "trace.jsonl").exists(), sbtrace._thread is None, not sbtrace._rows)
os.environ["STANDBY_CALL_TRACE"] = "1"           # on for the call below, as a debugging Mac has it

fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:600])


def wait(pred, secs=10.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


class Engine(object):
    """The voice's engine (duplex.AVFBackend's interface). Its voice loads for COLD s from the
    start: a sentence given meanwhile waits for it (playing, and loading), then plays SAY s."""
    COLD, SAY = 0.3, 0.2

    def __init__(self):
        self.ready_at = time.time() + self.COLD
        self.heard, self._until, self.lock = [], 0.0, threading.Lock()

    def start_output(self):
        pass

    def stop_mic(self):
        pass

    def begin(self):
        pass

    @property
    def loading(self):
        return time.time() < self.ready_at

    def say(self, text):
        with self.lock:
            start = max(time.time(), self.ready_at)
            self.heard.append((start, text))
            self._until = start + self.SAY

    def halt(self):
        with self.lock:
            self._until = 0.0

    @property
    def playing(self):
        return time.time() < self._until


sdir = root / "sessions" / "talk-1"
sb.TRACE["sid"] = "talk-1"
sdir.mkdir(parents=True)
(sdir / "dictation.jsonl").write_text("")
eng = Engine()
vio = sb.VoiceIO(sdir, eng, log=lambda m: None, leaks=lambda: True, talk_over=False)
on = {"rec": True}


def recorder():
    """The recorder's loop, as far as the voice goes: its status in live.json."""
    while on["rec"]:
        st = {"at": time.time(), "level": 0.0, "partial": "", "voice": vio.status()}
        tmp = sdir / "live.json.tmp"
        tmp.write_text(json.dumps(st))
        tmp.replace(sdir / "live.json")
        time.sleep(0.03)


GREETING = ["Hi, we're connected.", "Two things are open from earlier.",
            "First, do you want mock three to replace the real page?", "Second, the film's caption."]


def ask(question, on_event, on_queued, heard=None):
    """A quick voice: the greeting's words as they are written, over about a second."""
    on_queued("q1")
    for s in GREETING:
        for word in s.split(" "):
            on_event({"kind": "delta", "text": word + " "})
            time.sleep(0.03)
    return " ".join(GREETING)


logged = []
vs = voice.VoiceSession("talk-1", sdir, ask=ask, cancel=lambda *a, **k: None, speaker=voice.RecorderSpeaker(sdir),
                        log=logged.append)
vs.GUARD = 0.05
vs.ledger = True                                  # a call: its turns kept in its record, as the app sets it
vs.start()
vio.start(vs.seq, [])
threading.Thread(target=recorder, daemon=True).start()
vs.open_with("[CALL START]")
wait(lambda: [t for _at, t in eng.heard] == GREETING, 15)
wait(lambda: vs.status()["state"] == "listening", 5)
with open(sdir / "dictation.jsonl", "a") as fh:
    fh.write(json.dumps({"turn": 0, "text": "What's next?", "seq": vs.seq}) + "\n")
    fh.write(json.dumps({"turn": 0, "end_turn": True, "text": "What's next?", "p": 0.9,
                         "spoke_until": time.time() - 0.3, "seq": vs.seq}) + "\n")
wait(lambda: len(eng.heard) >= 2 * len(GREETING), 15)
wait(lambda: vs.status()["state"] == "listening", 5)
on["rec"] = False
vs.stop()
vio.stop()
sbtrace.flush()

check("off by default (no debug.call_trace, no STANDBY_CALL_TRACE): no trace.jsonl, no writer thread, nothing kept",
      all(OFF), OFF)
path = sdir / "trace.jsonl"
rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
kinds = {r["ev"] for r in rows}
check("a call's trace: one file in the session's folder, each line an event", rows and all(
      set(r) >= {"t", "src", "turn", "ev"} for r in rows), rows[:3])
check("  only the session's own can read it (0600)", path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600,
      oct(stat.S_IMODE(path.stat().st_mode)) if path.exists() else None)
check("  nothing of it anywhere else under the root", sorted(str(p.relative_to(root)) for p in root.rglob("trace.jsonl"))
      == ["sessions/talk-1/trace.jsonl"])
for ev in ("floor", "ask", "say", "said", "timing", "turn_done", "end"):
    check("  has its %s events" % ev, ev in kinds, sorted(kinds))
asks = [r for r in rows if r["ev"] == "ask"]
check("the greeting is turn \"open\", their words turn 0", [r["turn"] for r in asks] == ["open", 0]
      and asks[1]["words"] == "What's next?", [(r["turn"], r.get("words")) for r in asks])
says = [r for r in rows if r["ev"] == "say"]
check("  each sentence said carries the turn it answers, to the recorder and back",
      [r["turn"] for r in says] == ["open"] * len(GREETING) + [0] * len(GREETING), [r["turn"] for r in says])
check("  every event says which process wrote it (one process here: the test's)", all("src" in r for r in rows))
done = [r for r in rows if r["ev"] == "turn_done"]
check("  each turn's outcome", [(r["turn"], r["outcome"]) for r in done] == [("open", "answered"), (0, "answered")], done)
tm = [r for r in rows if r["ev"] == "timing" and r["turn"] == 0]
check("  and its timing, in ms: their words to the turn over, to the first word, to the voice",
      tm and tm[0]["turn_over_ms"] is not None and tm[0]["first_word_ms"] is not None and tm[0]["total_ms"] is not None, tm)
t0 = time.time()
for i in range(2000):
    sbtrace.event("talk-1", "load", i=i)
took = time.time() - t0
check("an event costs next to nothing: 2000 in under 0.1 s, never a wait for the disk", took < 0.1, took)
sbtrace.flush()
sbtrace.event("talk-nope", "x")
sbtrace.flush()
check("  a session whose folder isn't there: nothing written, nothing made", not (root / "sessions" / "talk-nope").exists())
del os.environ["STANDBY_CALL_TRACE"]
(root / "config.json").write_text(json.dumps({"debug": {"call_trace": True}}))
(root / "sessions" / "talk-cfg").mkdir()
sbtrace.event("talk-cfg", "floor", who="listening")
sbtrace.flush()
check("  on from config.json's debug.call_trace too", (root / "sessions" / "talk-cfg" / "trace.jsonl").exists())
os.environ["STANDBY_CALL_TRACE"] = "0"
(root / "sessions" / "talk-env0").mkdir()
sbtrace.event("talk-env0", "floor", who="listening")
sbtrace.flush()
check("  and STANDBY_CALL_TRACE=0 turns it off whatever the config says",
      not (root / "sessions" / "talk-env0" / "trace.jsonl").exists())
os.environ["STANDBY_CALL_TRACE"] = "1"

led = [json.loads(x) for x in (sdir / "ledger.jsonl").read_text().splitlines()] if (sdir / "ledger.jsonl").exists() else []
check("the call's record, from the app: each turn once it's over, what was asked and what they heard",
      [(e["kind"], e["text"]) for e in led] == [("asked", "[CALL START]"), ("you", " ".join(GREETING)),
                                              ("asked", "What's next?"), ("you", " ".join(GREETING))]
      and not any(e.get("cut") or e.get("unsaid") for e in led), led)
check("  only the session's own can read it (0600)", stat.S_IMODE((sdir / "ledger.jsonl").stat().st_mode) == 0o600)

# The page: each turn's breakdown first; the model's exact input folded under its turn
with open(path, "a") as fh:
    for e in ({"t": time.time(), "src": "agent", "turn": None, "ev": "voice_start", "model": "haiku", "fresh": True,
               "system_prompt": "You are the voice.", "tools": "Bash,Read"},
              {"t": time.time(), "src": "agent", "turn": None, "ev": "model", "q": asks[1].get("q") or "q1", "model": "haiku",
               "outcome": "answered", "reply": "Next is the film.", "sent": "[VOICE]\n[RESULT done] built\nThey said: What's next?"}):
        fh.write(json.dumps(e) + "\n")
out = root / "trace.html"
r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "call_trace.py"),
                    "talk-1", str(out)], capture_output=True, text=True, timeout=60, env=dict(os.environ))
page = out.read_text() if out.exists() else ""
check("the trace page: made from trace.jsonl, its turns there, each with its breakdown",
      r.returncode == 0 and '"turn": "0"' in page and '"b": {"parts"' in page, (r.returncode, r.stderr[-400:]))
check("  only theirs to read too (0600)", out.exists() and stat.S_IMODE(out.stat().st_mode) == 0o600)
check("  the model's exact input and the system prompt, there to unfold",
      "What the voice's model was sent" in page and "[RESULT done] built" in page and "You are the voice." in page)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
