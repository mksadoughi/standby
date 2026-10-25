"""Recorder side of voice mode: "voice N" gathers mic speech into turns (out of
the meeting transcript) and says when each is over; "hold N" drops what the mic
hears while Claude speaks; an utterance open across a switch is dropped too;
"off N" gives the mic back to the meeting. No Smart Turn model here, so turns
end on punctuation and pauses (test_voice.py covers the model)."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbcd-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
from importlib.machinery import SourceFileLoader


class FakeInput(object):
    def __init__(self, callback=None, **kw): self.cb = callback; self.run = False
    def start(self):
        self.run = True
        def loop():
            while self.run:
                self.cb(np.full((800, 1), 0.1, "float32"), 800, None, None); time.sleep(0.05)
        threading.Thread(target=loop, daemon=True).start()
    def stop(self): self.run = False
    def close(self): pass


DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]))
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
logged = []
sb.log = logged.append
# Heartbeats all through: every voice turn here spans some, as a real meeting's do
# (every 10 s). None did before, and the heartbeat's own "parts" replaced item 11's
# "parts" dict: a Room meeting's first voice turn after one crashed the channel (2026-10-08).
sb.HEARTBEAT = 0.3
sb.ROOT, sb.SESSIONS, sb.CONFIG = root, root / "sessions", root / "config.json"
# No mic_device: "System default microphone" in Settings, and a fresh install.
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False}))
sb.load_model = lambda name: None


class Script(object):
    """Speech on demand: said() lines come out on the next drain; `talking`
    is an utterance still in progress, closed only by a forced drain."""
    lines, talking, early, instances = [], None, None, []
    def __init__(self, *a, **k): Script.instances.append(self); self.early_after = None
    def take_early(self):
        early, Script.early = Script.early, None
        return early
    def feed(self, b): pass
    def drain(self, force=False):
        out = [(1.0, 2.0, t) for t in Script.lines]
        Script.lines = []
        if force and Script.talking:
            out.append((2.0, 3.0, Script.talking))
            Script.talking = None
        return out
    def rotate(self): return []
    def _close(self): pass
    @property
    def utt(self): return Script.talking
    def partial(self): return Script.talking or ""


sb.UtteranceTranscriber = Script
fails = []


def check(label, ok, d=""):
    print("  %-60s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:500])


def wait(pred, secs=20):          # returns as soon as it holds: generous for a loaded Mac
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def say(text):
    Script.lines.append(text)
    time.sleep(0.4)


def texts(path):
    return [json.loads(l).get("text") for l in path.read_text().splitlines()] if path.exists() else []



sb._stop.clear()
th = threading.Thread(target=sb.run_session, args=("meet",), kwargs={"no_claude": True}, daemon=True)
th.start()
wait(lambda: (root / "daemon.ready").exists())
sid = (root / "daemon.ready").read_text().strip()
sdir = root / "sessions" / sid
local = sdir / "transcript.jsonl"
live_st = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
rows = lambda: [json.loads(l) for l in (sdir / "dictation.jsonl").read_text().splitlines()] \
    if (sdir / "dictation.jsonl").exists() else []
turns_over = lambda: [r for r in rows() if r.get("seq") == 7 and r.get("end_turn")]
spans = lambda: [r for r in (json.loads(l) for l in (sdir / "events.jsonl").read_text().splitlines() if l.strip())
                 if r.get("kind") == "voice"] if (sdir / "events.jsonl").exists() else []

say("the meeting before voice mode")
(sdir / "dictate").write_text("voice 7\n")
check("voice mode: the recorder says it is listening",
      wait(lambda: (live_st().get("voice") or {}).get("mode") == "voice"), live_st())
stamps, end = set(), time.time() + 0.6
while time.time() < end:
    stamps.add(live_st().get("at")); time.sleep(0.02)
check("  and reports the mic often, for the orb (not every 0.5 s)", len(stamps) >= 3, sorted(stamps))
check("  in a meeting, a span opens where the user talks to Claude, in session time",
      len(spans()) == 1 and spans()[0]["to"] is None and spans()[0]["from"] > 0, spans())
t0 = time.time()
say("what is the plan?")
check("a finished question ends the turn quickly",
      wait(lambda: turns_over(), 3) and time.time() - t0 < 1.6, (time.time() - t0, rows()))
crashed = lambda: [m.splitlines()[0] for m in logged if "channel died" in m or "main loop crashed" in m]
check("  the recording goes on through the heartbeats (no channel or loop crash)",
      not crashed() and (root / "daemon.ready").exists(), crashed())
check("  the turn carries what was said", turns_over()[:1] and turns_over()[0]["text"] == "what is the plan?", turns_over())
check("  its words are tagged with the turn", any(r.get("turn") == 0 and r.get("text") == "what is the plan?"
                                                  and not r.get("end_turn") for r in rows()), rows())
check("  and stay out of the meeting transcript", "what is the plan?" not in texts(local), texts(local))

say("so the thing I want is")
time.sleep(1.0)
check("mid-thought (no closing punctuation): the turn stays open", len(turns_over()) == 1, turns_over())
check("  the words so far are in the status, for captions",
      (live_st().get("voice") or {}).get("heard") == "so the thing I want is", live_st())
Script.talking = "still going"
time.sleep(2.5)
check("  speaking again keeps it open past the pause", len(turns_over()) == 1, turns_over())
Script.talking = None
say("the budget.")
check("  and the next words close it, as one turn",
      wait(lambda: len(turns_over()) == 2) and turns_over()[1]["text"] == "so the thing I want is the budget.", turns_over())
check("turns are numbered", [r["turn"] for r in turns_over()] == [0, 1], turns_over())

# words at a short pause, before their utterance closes, that would end the turn.
likely = lambda: [r for r in rows() if r.get("seq") == 7 and r.get("likely_turn")]
check("voice mode: the mic decodes early, at a short pause", Script.instances and
      Script.instances[0].early_after == sb.Channel.VOICE_EARLY, [i.early_after for i in Script.instances])
Script.talking, Script.early = "and who owns it?", (1.0, 2.0, "and who owns it?", None)
check("likely-final words (a question): Claude may start on them, before the turn is over",
      wait(lambda: likely()) and likely()[0]["text"] == "and who owns it?" and likely()[0]["turn"] == 2
      and len(turns_over()) == 2, (likely(), turns_over()))
Script.talking = None
say("and who owns it?")
check("  the turn then ends as ever, with the same words", wait(lambda: len(turns_over()) == 3)
      and turns_over()[2]["text"] == "and who owns it?", turns_over())
Script.talking, Script.early = "so what I mean is", (1.0, 2.0, "so what I mean is", None)
time.sleep(0.5)        # on purpose: a likely row would have been written in this time
check("  words that go on (no closing punctuation) are not likely", len(likely()) == 1, likely())
Script.talking = None
say("so what I mean is the plan.")
check("  and closes as one turn, before Claude speaks", wait(lambda: len(turns_over()) == 4)
      and turns_over()[3]["text"] == "so what I mean is the plan.", turns_over())

(sdir / "dictate").write_text("hold 7\n")
wait(lambda: (live_st().get("voice") or {}).get("mode") == "hold")
say("Claude is speaking through the laptop speakers.")
time.sleep(0.4)
check("hold: what the mic hears while Claude speaks is dropped",
      "Claude is speaking through the laptop speakers." not in texts(local)
      and not any("laptop speakers" in (r.get("text") or "") for r in rows()), (texts(local), rows()))
Script.talking = "the tail of Claude's voice"
time.sleep(0.3)
(sdir / "dictate").write_text("voice 7\n")
wait(lambda: (live_st().get("voice") or {}).get("mode") == "voice")
time.sleep(0.3)
check("an utterance open when listening resumes is dropped (Claude's echo)",
      "the tail of Claude's voice" not in texts(local)
      and not any("tail" in (r.get("text") or "") for r in rows()), (texts(local), rows()))
say("next question?")
check("listening again: the next turn", wait(lambda: len(turns_over()) == 5)
      and turns_over()[4]["text"] == "next question?", turns_over())

# Words said while the meeting agent works, and Claude takes the floor before their turn
# closes (talk-20261012-201955, turns 10 and 12): kept through the hold, asked when the
# turn closes, once. They were dropped at the switch back, never asked.
mode_is = lambda m: wait(lambda: (live_st().get("voice") or {}).get("mode") == m)
n = len(turns_over())
say("and what about Friday")                  # no closing punctuation: the turn is open
(sdir / "dictate").write_text("hold 7\n")     # Claude has the floor before it closes
mode_is("hold")
say("Jean's answer through the laptop speakers.")
time.sleep(2.6)                                # longer than the turn's own wait
check("words while Claude works, the floor taken: their turn isn't over meanwhile", len(turns_over()) == n, turns_over()[n:])
check("  and the speakers' words aren't in it (the echo guard, as before)",
      not any("speakers" in (r.get("text") or "") for r in rows()), rows()[-3:])
(sdir / "dictate").write_text("voice 7\n")
mode_is("voice")
back = time.time()
check("listening again: their words are still the turn's, for the orb",
      (live_st().get("voice") or {}).get("heard") == "and what about Friday", live_st())
check("  the turn closes with them", wait(lambda: len(turns_over()) == n + 1, 6)
      and turns_over()[-1]["text"] == "and what about Friday", turns_over()[n:])
check("  its wait counted from listening again, not from before the answer", time.time() - back >= 1.5, time.time() - back)
time.sleep(1.0)
check("  and asked once", len(turns_over()) == n + 1, turns_over()[n:])
n = len(turns_over())
say("so the plan for the launch")
(sdir / "dictate").write_text("hold 7\n")
mode_is("hold")
say("Jean says the launch is on Friday.")
(sdir / "dictate").write_text("voice 7\n")
mode_is("voice")
say("can we move it to Monday?")              # they go on before it closes
check("they go on after the answer: one turn with all of it", wait(lambda: len(turns_over()) == n + 1, 6)
      and turns_over()[-1]["text"] == "so the plan for the launch can we move it to Monday?", turns_over()[n:])
time.sleep(1.0)
check("  asked once", len(turns_over()) == n + 1, turns_over()[n:])

before_off = len(turns_over())
Script.talking = "an unfinished thought"     # still being said when off comes
(sdir / "dictate").write_text("off 7\n")
check("off: done", wait(lambda: any(r.get("seq") == 7 and r.get("done") for r in rows())), rows())
Script.talking = None
check("  the span closes, one for the whole of voice mode (holding and listening are not new ones)",
      wait(lambda: len(spans()) == 1 and spans()[0]["to"] is not None)
      and spans()[0]["to"] > spans()[0]["from"], spans())
check("  words cut off by off are not a question", len(turns_over()) == before_off, turns_over()[before_off:])
check("  and the status no longer says voice", wait(lambda: "voice" not in live_st()), live_st())
say("back to the meeting")
check("after voice mode, speech goes to the meeting again", texts(local)[-1] == "back to the meeting", texts(local))
check("the meeting before voice mode is untouched", "the meeting before voice mode" in texts(local))

(sdir / "dictate").write_text("voice 8\n")
wait(lambda: (live_st().get("voice") or {}).get("mode") == "voice")
# A voice turn spanning heartbeats, in a Room meeting: it ends as a turn, the channel alive.
beats = lambda: sum(m.startswith("heartbeat ") for m in logged)
turns8 = lambda: [r for r in rows() if r.get("seq") == 8 and r.get("end_turn")]
say("so before the plan")
n = beats()
check("heartbeats while a voice turn is open", wait(lambda: beats() >= n + 2), beats())
say("what is the plan?")
check("  the turn still ends, as one turn", wait(lambda: turns8())
      and turns8()[0]["text"] == "so before the plan what is the plan?", turns8())
check("  and the recording goes on, the Room channel alive", not crashed() and (root / "daemon.ready").exists()
      and all(c.get("state") != "dead" for c in live_st().get("sources", [])), crashed())
sb._stop.set()
th.join(timeout=15)
check("the session ending in voice mode ends it cleanly",
      not th.is_alive() and any(r.get("seq") == 8 and r.get("done") for r in rows()), rows()[-2:])
check("the session records no machine: Claude Code runs on this Mac",
      "where" not in json.loads((sdir / "meta.json").read_text()), json.loads((sdir / "meta.json").read_text()))
check("  a second voice mode is a second span, closed when the session ends",
      len(spans()) == 2 and all(r["to"] is not None for r in spans()) and spans()[1]["from"] >= spans()[0]["to"], spans())
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
