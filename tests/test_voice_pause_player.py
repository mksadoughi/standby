"""Pause on a call, end to end on the one player: the app's voice session (voice.py)
writes its sentences to speak.jsonl through RecorderSpeaker, and the recorder's VoiceIO
(standby.py) plays them on its engine. A pause (the orb, the band and the window all
mute the session) must silence it mid-sentence and drop the rest of that answer: while
the turn is on, and while it says a line of Standby's own outside a turn (news from
work handed on). No audio: the engine is a fake that "plays" each sentence for a while."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
from importlib.machinery import SourceFileLoader

root = Path(tempfile.mkdtemp(prefix="sbpause-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
sys.modules.setdefault("sounddevice", types.SimpleNamespace(
    InputStream=None, query_devices=lambda i=None: [], default=types.SimpleNamespace(device=[0, 0])))
sb = SourceFileLoader("sb", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac", "standby.py")).load_module()
import voice  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:600])


def wait(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


class Engine(object):
    """The voice's engine (duplex.AVFBackend's interface): each sentence "plays" for SAY s."""
    SAY = 1.0

    def __init__(self):
        self.started, self.halts, self._until, self.lock = [], 0, 0.0, threading.Lock()

    def start_output(self):
        pass

    def stop_mic(self):
        pass

    def begin(self):
        pass

    def say(self, text):
        with self.lock:
            self.started.append(text)
            self._until = time.time() + self.SAY

    def halt(self):
        with self.lock:
            self.halts += 1
            self._until = 0.0

    @property
    def playing(self):
        return time.time() < self._until


sdir = root / "sessions" / "talk-1"
sdir.mkdir(parents=True)
rows_path = sdir / "dictation.jsonl"
rows_path.write_text("")
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


ANSWER = ["First, the cache.", "Second, the queue.", "Third, the fares.", "Fourth, the tests.", "Fifth, the end."]
asked, cancelled = [], []


def ask(question, on_event, on_queued, heard=None):
    asked.append(question)
    on_queued("q%d" % len(asked))
    for s in ANSWER:                                     # the whole answer at once, as a quick voice gives it
        on_event({"kind": "delta", "text": s + " "})
    return " ".join(ANSWER)


logged = []
vs = voice.VoiceSession("talk-1", sdir, ask=ask, cancel=cancelled.append, speaker=voice.RecorderSpeaker(sdir),
                        log=logged.append)
vs.GUARD = 0.05
vs.start()
vio.start(vs.seq, [])
threading.Thread(target=recorder, daemon=True).start()


def row(**kw):
    with open(rows_path, "a") as fh:
        fh.write(json.dumps(dict(kw, seq=vs.seq)) + "\n")


def said():
    with eng.lock:
        return list(eng.started)


# ---------------------------------------------------------------- mid-turn
row(turn=0, end_turn=True, text="What changed?")
check("the answer plays on the recorder's engine", wait(lambda: said()[:1] == ANSWER[:1] and eng.playing, 5), said())
halts = eng.halts
vs.mute(True)
check("pause mid-sentence: the engine is halted at once", wait(lambda: eng.halts > halts and not eng.playing, 0.5),
      (eng.halts, eng.playing))
time.sleep(Engine.SAY * 3)
check("  and nothing more of that answer is said", said() == ANSWER[:1], said())
check("  the session is paused, the mic held", vs.status()["state"] == "muted" and vs.muted)
vs.mute(False)
wait(lambda: vs.status()["state"] == "listening", 3)

# ---------------------------------------------------------------- a line of Standby's own, outside a turn
# Work handed to Claude Code came back: the voice says how it went (announce), into a quiet moment,
# with no question asked: no turn is on while it speaks.
with eng.lock:
    eng.started = []
REPORT = "The tests pass now. I fixed the flaky one in the fares client and pushed the branch."
vs.announce(REPORT)
check("news from handed-on work is said, outside any turn",
      wait(lambda: said() == [REPORT] and eng.playing and vs._q is None and vs.status()["state"] == "speaking", 5),
      (said(), vs._q, vs.status()["state"]))
halts = eng.halts
vs.mute(True)
check("pause while it says it: the engine is halted at once, mid-sentence",
      wait(lambda: eng.halts > halts and not eng.playing, 0.5), (eng.halts, eng.playing))
check("  and the session is paused, the mic held", wait(lambda: vs.status()["state"] == "muted", 2) and vs.muted,
      vs.status())
time.sleep(Engine.SAY * 2)
check("  nothing of it is said again", said() == [REPORT], said())
vs.mute(False)
check("  unpaused: listening", wait(lambda: vs.status()["state"] == "listening", 2), vs.status())

on["rec"] = False
vs.stop()
vio.stop()

# What they'd heard at a cut: from the sound the engine played of the sentence (heard()), taken when the voice
# ducks or stops for them, before anything is dropped; the app uses it for that sentence, else the old guess.
class Counted(Engine):
    def heard(self):
        return {"text": "Then we move the cache to the new region next week.", "played": 1.0, "made": 4.0,
                "ended": True}

vio2 = sb.VoiceIO(sdir, Counted(), log=lambda m: None, leaks=lambda: True, talk_over=False)
vio2.duck()
cut = vio2.status().get("cut") or {}
check("ducked for them: what they'd heard, from its sound played (a quarter of it)",
      cut.get("heard") == "Then we move..." and cut.get("played_ms") == 1000 and cut.get("made_ms") == 4000, cut)
vio2.unduck()
check("  it went on: no cut", vio2.status().get("cut") is None, vio2.status().get("cut"))
vio2.halt()
check("  stopped for them: taken then", (vio2.status().get("cut") or {}).get("heard") == "Then we move...")
q = voice.Question("go on", False)
line = "Then we move the cache to the new region next week."
q.spoken, q.playing, q.play_since = [line], 0, 100.0
mine = {"text": line, "heard": "Then we move...", "at": 101.0}
check("the app: what was heard of the sentence playing is the recorder's, from its sound",
      q.heard(103.0, mine) == "Then we move...", q.heard(103.0, mine))
check("  another sentence's, or one from before it started: the time's guess, as before",
      q.heard(103.0, dict(mine, text="Other words.")) == voice.interrupt.heard_of(line, 3.0)
      and q.heard(103.0, dict(mine, at=99.0)) == voice.interrupt.heard_of(line, 3.0))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
