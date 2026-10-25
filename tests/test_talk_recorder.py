"""Recorder side of a talk session: `--kind talk` records only the user's
turns in voice mode. Its meta says kind talk; it keeps no audio and labels no
speakers (text only), Claude is always on, and speech outside a turn is not kept
anywhere. A talk resumed stays a talk."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbtalkrec-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
from importlib.machinery import SourceFileLoader


class FakeInput(object):
    def __init__(self, callback=None, **kw): self.cb = callback; self.run = False
    def start(self):
        self.run = True
        def loop():
            while self.run:
                self.cb(np.full((800, 1), 0.1, "float32"), 800, None, None); time.sleep(0.05)   # the mic's own rate
        threading.Thread(target=loop, daemon=True).start()
    def stop(self): self.run = False
    def close(self): pass


DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]))
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
sb.ROOT, sb.SESSIONS, sb.CONFIG = root, root / "sessions", root / "config.json"
# No mic_device: "System default microphone" in Settings, and a fresh install.
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": True}))
sb.load_model = lambda name: None


class Script(object):
    """Speech on demand: said() lines come out on the next drain; `talking`
    is an utterance still in progress, closed only by a forced drain."""
    lines, talking = [], None
    def __init__(self, *a, **k): pass
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


PATIENCE = 30           # generous: these suites run five at a time on one Mac


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def say(text):
    """Said, and heard: the recorder has taken the line (and done with it what it does)
    before the test goes on, so what comes next is not mixed up with it."""
    Script.lines.append(text)
    wait(lambda: text not in Script.lines)


def texts(path):
    return [json.loads(l).get("text") for l in path.read_text().splitlines()] if path.exists() else []




made = []
class NoSpeakers(object):
    def __init__(self, *a, **k): made.append(1)
    def identify(self, audio): return None
    def save(self): pass
sb.SpeakerID = NoSpeakers                      # would load TitaNet: a talk must not


def start(**kw):
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("meeting",), kwargs=kw, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    return th, (root / "daemon.ready").read_text().strip()


def stop(th):
    sb._stop.set()
    th.join(timeout=15)
    try:
        (root / "daemon.ready").unlink()
    except OSError:
        pass


th, sid = start(kind="talk", no_claude=True)
sdir = root / "sessions" / sid
meta = json.loads((sdir / "meta.json").read_text())
check("a talk: its own id, and its meta says so", sid.startswith("talk-") and meta.get("kind") == "talk"
      and meta.get("speakers") == ["Room"], (sid, meta))
check("  Claude is on, even when asked to start without it", not (sdir / "claude.off").exists())
check("  no speaker labels (no voice model loaded)", made == [], made)
say("words before voice mode is on")
(sdir / "dictate").write_text("voice 3\n")
status = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
wait(lambda: (status().get("voice") or {}).get("mode") == "voice")
say("what is a p99?")
rows = lambda: [json.loads(l) for l in (sdir / "dictation.jsonl").read_text().splitlines()] \
    if (sdir / "dictation.jsonl").exists() else []
check("in voice mode, what you say is a turn", wait(lambda: any(r.get("end_turn") and r.get("text") == "what is a p99?"
                                                                for r in rows())), rows())
(sdir / "dictate").write_text("off 3\n")
wait(lambda: any(r.get("done") for r in rows()))
say("words after voice mode")
local = sdir / "transcript.jsonl"
check("speech outside a turn is kept nowhere: no transcript at all", texts(local) == [], texts(local))
events = sdir / "events.jsonl"
check("  a talk is all voice mode: no \"You talked to Claude\" span",
      not events.exists() or not any(json.loads(l).get("kind") == "voice" for l in events.read_text().splitlines()
                                     if l.strip()), events.read_text() if events.exists() else "")
check("  and never in the turns either", not any("before voice mode" in (r.get("text") or "") or
                                                  "after voice mode" in (r.get("text") or "") for r in rows()), rows())
stop(th)
check("text only: no audio file", list(sdir.glob("*.wav")) == [], list(sdir.glob("*")))
th, again = start(resume=sid)
check("a talk resumed is still a talk, still without audio",
      again == sid and json.loads((sdir / "meta.json").read_text()).get("kind") == "talk"
      and list(sdir.glob("*.wav")) == [], (again, json.loads((sdir / "meta.json").read_text())))
stop(th)
th, meeting = start()
check("a meeting is still a meeting: kind, labels, audio",
      json.loads((root / "sessions" / meeting / "meta.json").read_text()).get("kind") == "meeting"
      and made == [1] and list((root / "sessions" / meeting).glob("*.wav")), (meeting, made))
stop(th)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
