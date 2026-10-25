"""A call, once the user's voice is known on the mic: someone else talking over Claude's voice
doesn't stop it, and their words don't show; the user talking over it does, once half a
second of their speech has been told apart. A meeting is unchanged (test_voice_duplex). The
engine and the voice prints are stand-ins: no audio is played or recorded."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbothers-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
from importlib.machinery import SourceFileLoader

streams = []


class FakeInput(object):
    def __init__(self, callback=None, **kw):
        self.cb, self.run, self.closed = callback, False, False
        streams.append(self)
    def start(self): self.run = True
    def stop(self): self.run = False
    def close(self): self.closed = True


DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]))
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
sb.ROOT, sb.SESSIONS, sb.CONFIG = root, root / "sessions", root / "config.json"
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False,
                                 "voice_mode": {"duplex": True}}))     # "Let me talk over Claude" on
sb.load_model = lambda name: None


class Script(object):
    """Speech on demand, as in test_voice_recorder; `voiced` is how long the
    utterance still being spoken has been speech."""
    lines, talking, voiced = [], None, 0.0
    frames, utt_start = None, 0          # the open utterance's audio, and which one it is
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
    def utt(self): return Script.frames if Script.frames is not None and Script.talking else Script.talking
    def partial(self): return Script.talking or ""


sb.UtteranceTranscriber = Script
engines = []


class FakeEngine(object):
    """The echo-cancelling engine: what it was asked to play, for how long.
    stuck: it says it is playing for ever (a route change stopped it); fail_say:
    it raises when asked to say something."""
    aec, fail_mic, stuck, fail_say = True, False, False, False
    def __init__(self):
        self.mic, self.played, self.halts, self._until = None, [], 0, 0.0
        self.begun = 0
        engines.append(self)
    def start_mic(self, device, feed):
        if FakeEngine.fail_mic:
            raise RuntimeError("no voice processing here")
        self.mic = (device, feed)

        def stream():                          # a real engine delivers audio all the time
            while self.mic is not None:
                feed(np.zeros(800, "float32"))
                time.sleep(0.05)               # real time on purpose: the engine's own block rate
        threading.Thread(target=stream, daemon=True).start()
    def stop_mic(self): self.mic = None
    def say(self, text):
        if FakeEngine.fail_say:
            raise RuntimeError("no synthesizer")
        self.played.append(text)
        self._until = time.time() + (2.5 if "long" in text else 0.3)
    @property
    def playing(self): return FakeEngine.stuck or time.time() < self._until
    def begin(self): self.begun += 1
    def tts_status(self): return {"state": "downloading", "downloading": True, "done": 1, "total": 4}
    def halt(self):
        self.halts += 1
        self._until = 0.0


sb.VOICE_BACKEND = FakeEngine
fails = []


def check(label, ok, d=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:500])


# Generous: five people run these suites at once, and a loaded Mac slows every
# real-time step. Nothing waits that long when it passes.
PATIENCE = 30


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def ticks(n=5):
    """Wait until the recorder has written its status n more times (every 0.1 s in voice
    mode): it has gone round its loop, so a check that nothing happened means something."""
    seen = []

    def more():
        at = live().get("at")
        if at is not None and (not seen or seen[-1] != at):
            seen.append(at)
        return len(seen) > n
    return wait(more)




class Mine(object):
    """The user's voice, known on this mic: a print's match is its first sample."""
    trusted = True
    def __init__(self, on_this_mic=True): self.on = on_this_mic
    def trusted_on(self, name): return self.on
    def match(self, emb): return emb
    def note_input(self, *a): return False
    def learn(self, *a): return False


sb.EMBED = lambda audio: float(audio[0]) if len(audio) else None


def call(mine):
    sb.MY_VOICE = mine
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("call",), kwargs={"no_claude": True, "kind": "talk"}, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    return th, root / "sessions" / (root / "daemon.ready").read_text().strip()


def over(sdir, seq):
    """Claude speaking a long answer, the user's mic held: what talking over it would stop."""
    live = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
    (sdir / "dictate").write_text("voice %d\n" % seq)
    wait(lambda: engines and engines[-1].mic is not None)
    (sdir / "dictate").write_text("hold %d\n" % seq)
    wait(lambda: (live().get("voice") or {}).get("mode") == "hold")
    with open(sdir / "speak.jsonl", "a") as fh:
        fh.write(json.dumps({"seq": seq, "id": 1, "say": "A long answer about the release plan."}) + "\n")
    wait(lambda: (live().get("voice") or {}).get("saying"))
    return engines[-1], live


def talks(audio_mark, words, start, voiced=0.8):
    Script.frames, Script.utt_start = [np.full(16000, audio_mark, "float32")], start
    Script.talking, Script.voiced = words, voiced


def quiet():
    Script.talking, Script.frames, Script.voiced = None, None, 0.0


th, sdir = call(Mine())
eng, live = over(sdir, 21)
halts = eng.halts
talks(0.1, "anyone related to the breakout team", 1)           # 0.1: not the user
ticks()
check("a call: someone else talking over Claude doesn't stop it", eng.halts == halts, eng.halts)
check("  their words don't show, and they aren't the user going on",
      live().get("partial") == "" and (live().get("voice") or {}).get("you_speaking") is False, live())
quiet(); ticks()
talks(0.9, "wait, stop that", 2, voiced=0.3)                     # the user, not yet half a second
# Not yet told apart, it acts at once: this engine can't duck, so it stops (a short "Yes." was lost
# waiting for 0.5 s).
check("  the user talking over it, not yet told apart: it stops at once", wait(lambda: eng.halts > halts), eng.halts)
quiet()
sb._stop.set(); th.join(timeout=PATIENCE)

# Not known on this mic (another mic, a new user): talking over stops it on sound, as before.
th, sdir = call(Mine(on_this_mic=False))
eng, live = over(sdir, 22)
halts = eng.halts
talks(0.1, "someone near the mic", 3, voiced=0.4)
check("not known on this mic: anyone talking over stops it, as before", wait(lambda: eng.halts > halts), eng.halts)
quiet()
sb._stop.set(); th.join(timeout=PATIENCE)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
