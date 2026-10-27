"""Voice mode on open speakers, the recorder's side, with a fake engine.

While voice mode is on, the mic comes through the echo-cancelling engine
(the plain stream is closed, and opened again after), and Claude's voice is
played from here: sentences from speak.jsonl, in order, with stop.
Talking over Claude stops it at once, tells the app (a barge_in row), and the
words become the next turn. Not while Claude is silent, and not without echo
cancellation. No audio is played or recorded: the engine is a fake."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbduplex-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
from importlib.machinery import SourceFileLoader

streams = []


class FakeInput(object):
    """The plain mic: silence while it runs, as a real stream delivers (none at all is a
    stalled mic, which the recorder opens again)."""
    def __init__(self, callback=None, **kw):
        self.cb, self.run, self.closed = callback, False, False
        streams.append(self)
    def start(self):
        self.run = True

        def loop():
            while self.run and not self.closed:
                if self.cb is not None:
                    self.cb(np.zeros((800, 1), "float32"), 800, None, None)
                time.sleep(0.05)
        threading.Thread(target=loop, daemon=True).start()
    def stop(self): self.run = False
    def close(self): self.closed = True


DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]))
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
sb.ROOT, sb.SESSIONS, sb.CONFIG = root, root / "sessions", root / "config.json"
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False, "voice_mode": {"duplex": True}}))
sb.load_model = lambda name: None


class Script(object):
    """Speech on demand, as in test_voice_recorder; `voiced` is how long the
    utterance still being spoken has been speech."""
    lines, talking, voiced = [], None, 0.0
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
engines = []


class FakeEngine(object):
    """The echo-cancelling engine: what it was asked to play, for how long.
    stuck: it says it is playing for ever (a route change stopped it); fail_say:
    it raises when asked to say something."""
    aec, fail_mic, stuck, fail_say, loading = True, False, False, False, False
    def __init__(self):
        self.mic, self.played, self.halts, self._until = None, [], 0, 0.0
        self.hold_next = False
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
        self._until = float("inf") if self.hold_next else time.time() + (2.5 if "long" in text else 0.3)
        self.hold_next = False
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


sb._stop.clear()
sb.VoiceIO.LEARNING = 0.0           # the echo cancelling's first seconds: their own checks below (learning)
th = threading.Thread(target=sb.run_session, args=("meet",), kwargs={"no_claude": True}, daemon=True)
th.start()
wait(lambda: (root / "daemon.ready").exists())
sdir = root / "sessions" / (root / "daemon.ready").read_text().strip()
live = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
vlive = lambda: live().get("voice") or {}
rows = lambda: [json.loads(l) for l in (sdir / "dictation.jsonl").read_text().splitlines()] \
    if (sdir / "dictation.jsonl").exists() else []
speak_id = [0]


def speak(seq=7, **row):
    if "say" in row:
        speak_id[0] += 1
        row["id"] = speak_id[0]
    with open(sdir / "speak.jsonl", "a") as fh:
        fh.write(json.dumps(dict(row, seq=seq)) + "\n")


def say(text):
    Script.lines.append(text)


first = streams[0]
(sdir / "dictate").write_text("voice 7\n")
check("voice mode: the mic comes through echo cancellation, the plain stream closed",
      wait(lambda: engines and engines[0].mic is not None) and engines[0].mic[0] == "MacBook Pro Microphone"
      and first.closed and len(streams) == 1, (engines and engines[0].mic, first.closed, len(streams)))
eng = engines[0]
check("  the recorder says it plays Claude's voice, echo cancelled",
      wait(lambda: vlive().get("speaks") and vlive().get("aec")), vlive())
eng.mic[1](np.zeros(800, "float32"))
check("  what the engine hears goes to the same transcriber", True)

speak(begin=True)
check("a new answer: the engine is told, to choose its voice", wait(lambda: eng.begun == 1), eng.begun)
check("  and Standby's voice getting ready is in the status, for the app",
      wait(lambda: (vlive().get("tts") or {}).get("state") == "downloading"), vlive())
speak(say="Orion is the cache.")
speak(say="It replaced Redis.")
check("sentences from the app are played, in order",
      wait(lambda: eng.played == ["Orion is the cache.", "It replaced Redis."]), eng.played)
check("  and the app is told what is being said, then that it is done",
      wait(lambda: vlive().get("done") == 2 and vlive().get("saying") == ""), vlive())
speak(say="A long answer that goes on.")
wait(lambda: vlive().get("saying"))
wait(lambda: vlive().get("done") == 3 and vlive().get("saying") == "")
speak(say="Another long answer that goes on.")
wait(lambda: vlive().get("saying"))
speak(say="And more after it.")
speak(stop=True)
check("stop: it stops at once, and drops what was still to say",
      wait(lambda: eng.halts >= 1 and vlive().get("done") == 5) and "And more after it." not in eng.played,
      (eng.halts, vlive(), eng.played))

# Talking over Claude.
(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")
speak(say="Another long answer, about the budget.")
wait(lambda: vlive().get("saying"))
halts = eng.halts
Script.talking, Script.voiced = "wait, I meant", 0.4
check("talking over Claude: it stops at once",
      wait(lambda: eng.halts > halts), eng.halts)
# Each a moment after the stop: the row is written, then the status, as the recorder goes on.
check("  the app is told (barge_in)", wait(lambda: any(r.get("barge_in") and r.get("seq") == 7 for r in rows())),
      rows())
check("  and the recorder is listening again, keeping those words", wait(lambda: vlive().get("mode") == "voice"),
      vlive())
(sdir / "dictate").write_text("voice 7\n")             # the app, after the interruption
Script.talking, Script.voiced = None, 0.0
say("wait, I meant the hiring budget?")
check("  they are the next turn", wait(lambda: any(r.get("end_turn") and r.get("text") == "wait, I meant the hiring budget?"
                                               for r in rows())), rows()[-3:])

# Words from before Claude took the floor, then talking over it (2026-10-12): they are
# not dropped at the barge-in either; one turn with both, asked once.
say("and the hiring plan")                     # no closing punctuation: the turn is open
check("  the words before the floor have reached the open turn",
      wait(lambda: any(r.get("text") == "and the hiring plan" and not r.get("end_turn") for r in rows())), rows()[-3:])
(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")
eng.hold_next = True                            # this answer ends by interruption, never by a wall-clock race
speak(say="The hiring plan is two engineers.")
wait(lambda: vlive().get("saying"))
check("  Claude is still speaking before the user talks over it", eng.playing)
halts = eng.halts
Script.talking, Script.voiced = "no wait", 0.4
wait(lambda: eng.halts > halts) and wait(lambda: vlive().get("mode") == "voice")
(sdir / "dictate").write_text("voice 7\n")
Script.talking, Script.voiced = None, 0.0
say("no wait, for next quarter?")
both = lambda: [r for r in rows() if r.get("end_turn") and r.get("text") == "and the hiring plan no wait, for next quarter?"]
check("words from before the floor, then talked over: one turn with both", wait(lambda: both()), rows()[-3:])
time.sleep(1.0)
check("  asked once", len(both()) == 1 and not any(r.get("end_turn") and r.get("text") == "and the hiring plan" for r in rows()),
      rows()[-3:])

(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")
n = sum(1 for r in rows() if r.get("barge_in"))
Script.talking, Script.voiced = "someone in the room", 0.6
ticks()                     # the recorder has gone round its loop with that, several times
check("no barge-in while Claude is silent (thinking): nothing to talk over",
      sum(1 for r in rows() if r.get("barge_in")) == n, rows()[-2:])
Script.talking, Script.voiced = None, 0.0
FakeEngine.aec = False
speak(say="A long answer, without echo cancellation.")
wait(lambda: vlive().get("saying"))
Script.talking, Script.voiced = "Claude's own voice from the speakers", 0.6
ticks()
check("  nor without echo cancellation: the mic would hear Claude itself",
      sum(1 for r in rows() if r.get("barge_in")) == n and vlive().get("aec") is False, vlive())
Script.talking, Script.voiced = None, 0.0
FakeEngine.aec = True
speak(stop=True)

# Words said over Claude's voice are never lost (10/25): echo cancelled, they are kept, marked
# "over", for the app to judge (a turn, or "mm-hm"). Dropped only where the mic can't tell them
# from Claude's own voice, and then the window is told, never the words. A mute still drops.
(sdir / "dictate").write_text("floor 7\n")
ticks()                                                 # the recorder has read it (it says "hold" either way)
speak(say="Do you mean gold instead of purple?")
wait(lambda: vlive().get("saying"))
say("Yes.")
check("over Claude's voice, echo cancelled: kept, marked over (not dropped as floor)",
      wait(lambda: any(r.get("text") == "Yes." and r.get("over") for r in rows())), rows()[-2:])
speak(stop=True)
lost = lambda: [e for e in (json.loads(l) for l in (sdir / "events.jsonl").read_text().splitlines())
                if e.get("kind") == "speech_lost"] if (sdir / "events.jsonl").exists() else []
(sdir / "dictate").write_text("hold 7\n")             # the app's hold for a mute or a dictation: not the floor
ticks()
say("words said while muted")
ticks()
check("  a mute's hold: dropped, as ever, and nothing said lost",
      not any(r.get("text") == "words said while muted" for r in rows()) and not lost(), (rows()[-2:], lost()))
FakeEngine.aec = False
(sdir / "dictate").write_text("floor 7\n")
speak(say="A long answer, half duplex.")
wait(lambda: vlive().get("saying"))
say("Claude's own voice, from the speakers.")
ticks()
check("  half duplex on speakers: dropped (the mic hears Claude itself)",
      not any(r.get("text") == "Claude's own voice, from the speakers." for r in rows()), rows()[-2:])
check("  and the window is told something was lost: a span, never the words",
      wait(lambda: lost()) and all(set(e) == {"kind", "from", "to", "why"} for e in lost()), lost())
speak(stop=True)
FakeEngine.aec = True
# Claude takes the floor while the user's words are still open: they began before it, theirs.
(sdir / "dictate").write_text("voice 7\n")
wait(lambda: vlive().get("mode") == "voice")
Script.talking, Script.voiced = "and one more thing about the", 0.5
ticks()
(sdir / "dictate").write_text("floor 7\n")
check("the floor taken while they still speak: what they said began before it, kept as theirs",
      wait(lambda: any(r.get("text") == "and one more thing about the" and not r.get("over") for r in rows())),
      rows()[-2:])
Script.talking, Script.voiced = None, 0.0
(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")

# Standby Dictation during the call: the engine has the mic, and a second reader of it gets near
# silence, so the call's own mic (echo cancelled) is lent while it dictates, from its last second.
lentf = sdir / "dictating.f32"
(sdir / "dictating").write_text("4242\n")
check("dictating during the call: the call's own mic lent, its last second first",
      wait(lambda: lentf.exists() and lentf.stat().st_size >= int(0.9 * 16000) * 4),
      lentf.stat().st_size if lentf.exists() else None)
check("  the user's only (0600)", oct(lentf.stat().st_mode & 0o777) == "0o600", oct(lentf.stat().st_mode & 0o777))
size = lentf.stat().st_size
check("  and it goes on as the engine hears", wait(lambda: lentf.stat().st_size > size + 3200 * 4),
      (size, lentf.stat().st_size))
(sdir / "dictating").unlink()
check("  done: no longer lent, and the file gone", wait(lambda: not lentf.exists()))

# An engine that can duck (R-conversation, Q1/Q2): talked over, the voice goes quiet and
# pauses, and the words decide: "mm-hm" and it goes on where it was, words and it stops.
FakeEngine.ducks = FakeEngine.unducks = 0
def _duck(self): FakeEngine.ducks += 1; self._paused = self._until - time.time()
def _unduck(self): FakeEngine.unducks += 1; self._until = time.time() + max(0.0, getattr(self, "_paused", 0.0))
FakeEngine.duck, FakeEngine.unduck = _duck, _unduck
FakeEngine.playing = property(lambda self: FakeEngine.stuck or time.time() < self._until
                              or (vlive().get("ducked") is True))


def talked_over(words, voiced=0.4):
    """Claude says a long sentence; the user starts talking over it."""
    speak(say="A long answer, about the hiring budget.")
    wait(lambda: vlive().get("saying"))
    Script.talking, Script.voiced = words, voiced
    return wait(lambda: vlive().get("ducked") is True)


(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")
ticks()                                           # the stop above has been played out
halts = eng.halts
check("talked over, with an engine that can pause: it ducks, it doesn't stop", talked_over("Mm-hmm"),
      (vlive(), rows()[-2:]))
check("  the app is told (an overlap row), not barge_in",
      wait(lambda: any(r.get("overlap") == "duck" for r in rows())) and eng.halts == halts, rows()[-2:])
check("  and it listens meanwhile", vlive().get("mode") == "voice", vlive())
Script.talking, Script.voiced = None, 0.0
say("Mm-hmm.")
check("\"mm-hm\": it goes on where it was", wait(lambda: FakeEngine.unducks == 1) and eng.halts == halts,
      (FakeEngine.unducks, eng.halts, rows()[-3:]))
check("  the app is told (resume), and the mic is Claude's again",
      wait(lambda: any(r.get("overlap") == "resume" and r.get("why") == "backchannel" for r in rows()))
      and wait(lambda: vlive().get("mode") == "hold"), (rows()[-2:], vlive()))
speak(stop=True)
wait(lambda: not vlive().get("saying"))

# Its own words heard back past the echo cancelling, a little off ("A long" heard as "Uh, long"):
# not the user, so it goes on (talk-20261026-195509: its greeting stopped it, and was answered).
halts, unducks = eng.halts, FakeEngine.unducks
talked_over("Uh, long answer, about the hiring")
ticks()
Script.talking, Script.voiced = None, 0.0           # ducked, the voice is quiet: so is its echo
check("its own words heard back over it: it goes on, not stopped",
      wait(lambda: FakeEngine.unducks > unducks) and eng.halts == halts
      and wait(lambda: any(r.get("overlap") == "resume" and r.get("why") == "its own words" for r in rows())),
      (FakeEngine.unducks, eng.halts, rows()[-2:]))
wait(lambda: vlive().get("mode") == "hold")
speak(stop=True)
wait(lambda: not vlive().get("saying"))

halts = eng.halts
talked_over("wait, what about the EU region")
Script.voiced = 0.8                               # a person goes on speaking under the duck (its echo would stop)
check("words of a turn over it: it stops, and the app is told why",
      wait(lambda: eng.halts > halts) and wait(lambda: any(r.get("barge_in") and r.get("why") == "words" for r in rows())),
      (eng.halts, rows()[-2:]))
Script.talking, Script.voiced = None, 0.0
(sdir / "dictate").write_text("voice 7\n")
say("wait, what about the EU region?")
wait(lambda: any(r.get("end_turn") and "EU region" in (r.get("text") or "") for r in rows()))

(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")
halts, unducks = eng.halts, FakeEngine.unducks
talked_over("")                                   # a cough: sound, no words
Script.talking, Script.voiced = None, 0.0
check("a sound with no words: it goes on, a moment later",
      wait(lambda: FakeEngine.unducks > unducks) and eng.halts == halts,
      (FakeEngine.unducks, eng.halts, rows()[-2:]))
speak(stop=True)
wait(lambda: not vlive().get("saying"))

# Its first seconds of voice (VoiceIO.LEARNING): the echo cancelling still learns it, and a
# faint echo gets through. A moment of sound doesn't duck it then (a second's gap mid-greeting,
# talk-20261026-212951); more speech over it does.
sb.VoiceIO.LEARNING = 3600.0
halts, ducks = eng.halts, FakeEngine.ducks
speak(say="A long answer, about the hiring budget.")
wait(lambda: vlive().get("saying"))
Script.talking, Script.voiced = "", 0.4
ticks()
check("its first seconds of voice: a moment of sound over it (its echo) doesn't duck it",
      FakeEngine.ducks == ducks and eng.halts == halts and vlive().get("ducked") is not True, (FakeEngine.ducks, vlive()))
Script.voiced = 1.0
check("  speech that goes on over it: it ducks, as before", wait(lambda: vlive().get("ducked") is True), vlive())
Script.talking, Script.voiced = None, 0.0
wait(lambda: vlive().get("ducked") is not True)
speak(stop=True)
wait(lambda: not vlive().get("saying"))
# Counted in voice played, never the clock: a silence before the first answer is not learnt from
# (talk-20261026-232015: "[QUIET]" said, then 13 s of quiet, and the learning was over by the answer).
p0 = vlive().get("played")
time.sleep(0.6)
check("  learning counts its voice played: a silence adds nothing", vlive().get("played") == p0, (p0, vlive().get("played")))
speak(say="Another long answer, about the hiring budget.")
wait(lambda: vlive().get("saying"))
check("    its voice playing does", wait(lambda: (vlive().get("played") or 0) >= p0 + 0.3, 3), (p0, vlive().get("played")))
speak(stop=True)
wait(lambda: not vlive().get("saying"))
sb.VoiceIO.LEARNING = 0.0
(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")

halts = eng.halts
talked_over("uh, yeah, um", 0.4)
Script.voiced = 1.6                               # they keep talking
check("talking on over it, whatever the words: it stops",
      wait(lambda: eng.halts > halts) and any(r.get("barge_in") and r.get("why") == "kept talking" for r in rows()),
      (eng.halts, rows()[-2:]))
Script.talking, Script.voiced = None, 0.0
speak(stop=True)
wait(lambda: not vlive().get("saying"))

# A duck nothing decides (the recorder's decision failed): the voice goes on by itself.
(sdir / "dictate").write_text("voice 7\n")            # the app, after the turn above
ticks()
(sdir / "dictate").write_text("hold 7\n")
wait(lambda: vlive().get("mode") == "hold")
unducks = FakeEngine.unducks
sb.VoiceIO.DUCK_MAX = 0.5
import overlap as _ov                               # noqa: E402
_decide, _ov.decide = _ov.decide, lambda *a: (None, "")
ducked = talked_over("hmm")
Script.talking, Script.voiced = None, 0.0
check("ducked, and nothing ever decides: the voice goes on by itself",
      ducked and wait(lambda: FakeEngine.unducks > unducks), (ducked, FakeEngine.unducks))
_ov.decide = _decide
sb.VoiceIO.DUCK_MAX = 3.0
speak(stop=True)
wait(lambda: not vlive().get("saying"))
del FakeEngine.duck, FakeEngine.unduck

(sdir / "dictate").write_text("off 7\n")
check("voice mode off: the plain mic again, and the engine stops",
      wait(lambda: eng.mic is None and len(streams) == 2 and streams[1].run), (eng.mic, len(streams)))
check("  and no more voice in the status", wait(lambda: "voice" not in live()), live())

FakeEngine.fail_mic = True
(sdir / "dictate").write_text("voice 8\n")
check("an engine that cannot take the mic: the plain one stays (half duplex)",
      wait(lambda: len(engines) == 2) and wait(lambda: len(streams) == 3 and streams[2].run)
      and wait(lambda: vlive().get("half")), (len(engines), len(streams), vlive()))
check("  and the recorder does not play: it would have no echo cancelled, nor a voice (the app's speaks)",
      not vlive().get("speaks"), vlive())
check("  and it says why, for the app's log (the user's -10875)", vlive().get("why") == "no voice processing here", vlive())
(sdir / "dictate").write_text("off 8\n")
wait(lambda: "voice" not in live())

# The same mic on headphones (the Mac's own kept while AirPods play): nothing played reaches
# it, so the voice plays here anyway, the plain mic stays, and talking over Claude stops it.
leaks = [False]
sb._output_leaks = lambda: leaks[0]
FakeEngine.start_output = lambda self: setattr(self, "output", True)
_stop_mic = FakeEngine.stop_mic
FakeEngine.stop_mic = lambda self: (setattr(self, "mic", None), setattr(self, "output", False))
(sdir / "dictate").write_text("voice 9\n")
wait(lambda: len(engines) == 3)
check("on headphones, the mic not the one echo cancellation takes: the recorder plays all the same",
      wait(lambda: vlive().get("speaks") and vlive().get("duplex")) and getattr(engines[2], "output", False)
      and not vlive().get("aec") and not vlive().get("half"), vlive())
check("  and the plain mic stays as it was", wait(lambda: len(streams) == 4 and streams[3].run), len(streams))
hp = engines[2]
(sdir / "dictate").write_text("hold 9\n")
wait(lambda: vlive().get("mode") == "hold")
speak(seq=9, say="A long answer on the AirPods.")
wait(lambda: vlive().get("saying"))
halts = hp.halts
Script.talking, Script.voiced = "wait, one thing", 0.4
check("  talking over Claude stops it, with no echo cancellation (none to cancel)", wait(lambda: hp.halts > halts), hp.halts)
wait(lambda: any(r.get("barge_in") and r.get("seq") == 9 for r in rows()))
Script.talking, Script.voiced = None, 0.0
(sdir / "dictate").write_text("hold 9\n")
wait(lambda: vlive().get("mode") == "hold")
speak(seq=9, say="Then the AirPods come out.")
leaks[0] = True
check("headphones off (the speakers would echo): half duplex, the voice still played here (never handed on)",
      wait(lambda: vlive().get("speaks") and not vlive().get("duplex")) and hp.output and not vlive().get("half"), vlive())
leaks[0] = False
check("  and on again: talking over it works again", wait(lambda: vlive().get("duplex")), vlive())
(sdir / "dictate").write_text("off 9\n")
check("  off: its engine stops", wait(lambda: "voice" not in live() and not hp.output), hp.output)
engines.remove(hp)                   # the rest of this test counts the engines as before
sb._output_leaks = lambda: True

# The engine stops working mid voice mode: it is made again, once; failing again, the voice is
# off for that voice mode, and the app says so (never another voice instead).
sb.VoiceIO.STUCK_BASE, sb.VoiceIO.STUCK_PER_CHAR, sb.VoiceIO.STUCK_COLD = 0.8, 0.0, 1.5
FakeEngine.fail_mic = False
(sdir / "dictate").write_text("voice 10\n")
n0 = len(engines)
wait(lambda: len(engines) == n0 + 1 and engines[-1].mic is not None and vlive().get("speaks"))
FakeEngine.stuck, FakeEngine.loading = True, True     # Standby's voice still downloading: the sentence waits for it
speak(seq=10, say="Said once the voice is there.")
time.sleep(3.0)                                       # past STUCK_BASE + STUCK_COLD, on purpose
check("a sentence waiting for Standby's voice to load is not a stopped engine: nothing made again",
      len(engines) == n0 + 1 and vlive().get("speaks") and not vlive().get("half"), (len(engines) - n0, vlive()))
# Keep the fake voice loading until the recorder has read both sentences. The
# assertion below is about sentences held by the stopped engine, not a row the
# recorder has yet to consume when a replacement starts at the end of the file.
held = threading.Event()
step = sb.VoiceIO._step
pending = speak_id[0] + 1


def observe_pending(self):
    step(self)
    with self._lock:
        if any(item[0] == pending for item in self._queue):
            held.set()


sb.VoiceIO._step = observe_pending
speak(seq=10, say="The first sentence of a voice mode, on a cold voice.")
check("  both sentences have reached the loading engine", held.wait(PATIENCE))
sb.VoiceIO._step = step
t0 = time.time()
FakeEngine.loading = False
check("a sentence that never ends (the engine stopped): its engine made again, once, and playing",
      wait(lambda: len(engines) == n0 + 2 and engines[-1].mic is not None and vlive().get("speaks"))
      and time.time() - t0 >= 2.2 and engines[n0].mic is None, (len(engines) - n0, time.time() - t0, vlive()))
check("  what the stopped one held counts as done: nothing waits on it",
      wait(lambda: vlive().get("done", 0) >= speak_id[0], 3), vlive())      # live.json is written behind
speak(seq=10, say="A sentence that never ends, again.")
check("stopping again: the voice is off for this voice mode, and it says why (the app shows it)",
      wait(lambda: vlive().get("half") and not vlive().get("speaks"))
      and "its voice stopped working" in str(vlive().get("why")), vlive())
plain = len(streams)
check("  the plain mic given back", wait(lambda: streams[-1].run and engines[-1].mic is None), (len(streams), engines[-1].mic))
speak(seq=10, say="And the next one.")
(sdir / "dictate").write_text("hold 10\n")
ticks()
check("  no engine tried again this voice mode", len(engines) == n0 + 2, len(engines) - n0)
(sdir / "dictate").write_text("off 10\n")
wait(lambda: "voice" not in live())
FakeEngine.stuck = False
for e in engines[n0:]:
    engines.remove(e)

FakeEngine.fail_say = True
n0 = len(engines)
(sdir / "dictate").write_text("voice 11\n")
wait(lambda: len(engines) == n0 + 1 and engines[-1].mic is not None and vlive().get("speaks"))
speak(seq=11, say="Orion is the cache.")
check("an engine that cannot say anything: made again, once",
      wait(lambda: len(engines) == n0 + 2 and engines[-1].mic is not None and vlive().get("speaks")), (vlive(), len(engines) - n0))
speak(seq=11, say="It replaced Redis.")
check("  and that one neither: off for this voice mode, and it says why",
      wait(lambda: vlive().get("half") and all(e.mic is None for e in engines[n0:])), (vlive(), len(engines) - n0))
(sdir / "dictate").write_text("off 11\n")
wait(lambda: "voice" not in live())
FakeEngine.fail_say = False
for e in engines[n0:]:
    engines.remove(e)

# "Let me talk over Claude" off in Settings (read when voice mode opens): the recorder still plays
# the voice, on an engine of its own; the mic is left as it is, and held while Claude speaks.
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False}))
FakeEngine.start_output = lambda self: setattr(self, "output", True)
n0, plain = len(engines), len(streams)
(sdir / "dictate").write_text("voice 12\n")
check("talking over off: the recorder plays the voice all the same, half duplex, the plain mic kept",
      wait(lambda: len(engines) == n0 + 1 and getattr(engines[-1], "output", False) and vlive().get("speaks"))
      and engines[-1].mic is None and not vlive().get("duplex") and streams[-1].run, (vlive(), len(engines) - n0))
speak(seq=12, say="Played with talking over off.")
check("  and it speaks", wait(lambda: "Played with talking over off." in engines[-1].played), engines[-1].played)
(sdir / "dictate").write_text("off 12\n")
wait(lambda: "voice" not in live())
del FakeEngine.start_output
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False, "voice_mode": {"duplex": True}}))
for e in engines[n0:]:
    engines.remove(e)

# No audio engine at all here: nothing plays the voice (nor anything else instead), and it says why.
real_backend = sb.voice_backend
sb.voice_backend = lambda voice=None: None
(sdir / "dictate").write_text("voice 14\n")
wait(lambda: (vlive().get("mode")) == "voice")
ticks()
check("no audio engine: nothing played, the plain mic, and it says why for the app",
      not vlive().get("speaks") and vlive().get("half") and "no audio engine" in str(vlive().get("why"))
      and streams[-1].run, vlive())
(sdir / "dictate").write_text("off 14\n")
wait(lambda: "voice" not in live())
sb.voice_backend = real_backend
n0 = len(engines)
(sdir / "dictate").write_text("voice 9\n")
check("  and with one again: the next voice mode has it", wait(lambda: len(engines) == n0 + 1 and engines[-1].mic is not None),
      len(engines) - n0)
(sdir / "dictate").write_text("off 9\n")
check("  off: the engine given up", wait(lambda: "voice" not in live() and engines[-1].mic is None), engines[-1].mic)

# a mic muted for the meeting is closed, and the engine must never take it. Last: this
# test's fake devices cannot open the mic again after (test_mic_mute covers unmuting).
(sdir / "mic").write_text("muted\n")
ticks()
before, plain = len(engines), len(streams)
(sdir / "dictate").write_text("voice 13\n")
wait(lambda: vlive().get("mode") == "voice")
ticks()
check("a muted mic and voice mode with talk-over on: the engine never takes it",
      all(e.mic is None for e in engines[before:]) and not vlive().get("speaks") and vlive().get("half")
      and len(streams) == plain, ([e.mic for e in engines[before:]], vlive(), len(streams) - plain))
sb._stop.set()
th.join(timeout=PATIENCE)
check("the session ending in voice mode ends cleanly", not th.is_alive()
      and all(e.mic is None for e in engines), (th.is_alive(), [e.mic for e in engines]))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
