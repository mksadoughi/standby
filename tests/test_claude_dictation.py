"""Recorder side of the Claude switch and dictation, with the real shipper
delivering locally:
- Claude off: the transcript stays on the Mac, nothing is delivered.
- Claude on later: it catches up on everything, speaker names included.
- Off again, on again: nothing lost, nothing duplicated.
- Dictation: mic speech goes to dictation.jsonl, not the transcript; speech
  already in progress when it starts stays in the meeting; the last words
  before it stops still make it into the dictation.
"""
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
sb.log = lambda m: None
sb.ROOT, sb.SESSIONS, sb.CONFIG = root, root / "sessions", root / "config.json"
# No mic_device: "System default microphone" in Settings, and a fresh install.
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False}))
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


def wait(pred, secs=6):
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
sdir, live = root / "sessions" / sid, root / "live"
local, delivered = sdir / "transcript.jsonl", live / (sid + ".jsonl")

say("first words")
check("records from the system default mic when none is chosen", texts(local) == ["first words"], texts(local))
check("Claude off: the line is in the transcript", texts(local) == ["first words"], texts(local))
time.sleep(0.6)
check("Claude off: nothing delivered, no live session announced",
      not delivered.exists() and not (live / "CURRENT").exists(), list(live.glob("*")) if live.exists() else [])
(sdir / "speakers.names.json").write_text(json.dumps({"names": {"1": "Alex"}}))

(sdir / "claude.off").unlink()
check("Claude on: catches up on everything said so far",
      wait(lambda: texts(delivered) == ["first words"]), texts(delivered))
check("  the session is announced to the agent", (live / "CURRENT").exists()
      and (live / "CURRENT").read_text().strip() == sid and (live / (sid + ".meta.json")).exists())
check("  speaker names delivered too", (live / (sid + ".speakers.names.json")).exists())
check("  delivery progress recorded for the app",
      wait(lambda: (sdir / "delivered").exists()
           and int((sdir / "delivered").read_text()) == local.stat().st_size))

(sdir / "claude.off").write_text("")
wait(lambda: not (live / "CURRENT").exists())
say("off the record for now")
time.sleep(0.6)
check("Claude off again: the agent's live pointer is cleared", not (live / "CURRENT").exists())
check("  new lines stay on this Mac", "off the record for now" not in texts(delivered), texts(delivered))
(sdir / "claude.off").unlink()
check("on again: caught up with no duplicates",
      wait(lambda: texts(delivered) == ["first words", "off the record for now"]), texts(delivered))

Script.talking = "still the meeting"
live_st = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
check("listening status: level, speech in progress and its words so far",
      wait(lambda: live_st().get("partial") == "still the meeting" and live_st().get("speaking")
           and live_st().get("level", 0) > 0 and time.time() - live_st().get("at", 0) < 2), live_st())
(sdir / "dictate").write_text("on 1\n")
time.sleep(0.5)
say("what did Alex say about pricing")
Script.talking = "and the date"
(sdir / "dictate").write_text("off 1\n")
dict_rows = lambda: [json.loads(l) for l in (sdir / "dictation.jsonl").read_text().splitlines()] \
    if (sdir / "dictation.jsonl").exists() else []
check("dictation finishes with a done marker", wait(lambda: any(r.get("done") for r in dict_rows())), dict_rows())
said = [r["text"] for r in dict_rows() if r.get("seq") == 1 and "text" in r]
check("dictated words go to the chat box, in order",
      said == ["what did Alex say about pricing", "and the date"], said)
check("  and not into the meeting transcript",
      "what did Alex say about pricing" not in texts(local) and "and the date" not in texts(local), texts(local))
check("speech in progress when dictation started stays in the meeting", "still the meeting" in texts(local))
check("dictated rows carry their time, for the re-pass",
      all("t" in r and "end" in r for r in dict_rows() if "text" in r))
say("back to the meeting")
check("after dictation, speech goes to the meeting again", texts(local)[-1] == "back to the meeting", texts(local))
(sdir / "dictate").write_text("off 2\n")
check("a stop with no start still answers done (no hung chat box)",
      wait(lambda: any(r.get("seq") == 2 and r.get("done") for r in dict_rows())), dict_rows())

sb._stop.set()
th.join(timeout=15)
check("session ends cleanly and clears the dictation switch",
      not th.is_alive() and not (sdir / "dictate").exists())
check("  and its listening status", not (sdir / "live.json").exists())
runs = [json.loads(l) for l in (sdir / "runs.jsonl").read_text().splitlines()]
check("the recording run is logged with wall-clock start and end",
      len(runs) == 2 and "start" in runs[0] and runs[0]["t"] == 0 and "end" in runs[1]
      and runs[1]["end"] >= runs[0]["start"] and runs[1]["t"] > 0, runs)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
