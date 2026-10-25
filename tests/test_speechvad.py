"""Talking over Claude's voice is judged by a speech model (Q3, speechvad): a burst of
noise, clicks or a tone loud enough for the energy VAD don't stop the voice, speech
does; without the model, loudness decides as before. Silence, noise, clicks and a tone
are made here; speech by `say` into a file (nothing is played). The engine is a fake,
as in test_call_others: no audio is played or recorded.

The model is never downloaded here: SB_SILERO (a path), else the installed one in
~/.standby/models (read only, copied into this test's own root). Without either, or
without onnxruntime, say or afconvert, the model's checks are skipped."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, shutil, subprocess, sys, tempfile, threading, time, types, wave
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbspeechvad-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
from importlib.machinery import SourceFileLoader
import speechvad  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:500])


RATE = 16000
rs = np.random.RandomState(5)
room = lambda s: (rs.randn(int(s * RATE)) * 0.002).astype("float32")      # a quiet room


def noise(s=0.4, amp=0.1):
    return (rs.randn(int(s * RATE)) * amp).astype("float32")


def clicks(s=0.4):
    a = room(s)
    for at in np.arange(0.02, s, 0.06):                                    # typing: a click every 60 ms
        i = int(at * RATE)
        a[i:i + 80] += (rs.randn(80) * 0.5 * np.exp(-np.arange(80) / 15.0)).astype("float32")
    return a


def tone(s=0.4):
    return (0.2 * np.sin(2 * np.pi * 440 * np.arange(int(s * RATE)) / RATE)).astype("float32")


def speech(text):
    aiff, wav = str(root / "s.aiff"), str(root / "s.wav")
    subprocess.run(["say", "-o", aiff, text], check=True)                 # a file: nothing is played
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True)
    w = wave.open(wav)
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0


def spoken(a, s=0.4):
    """`s` seconds of it from where the speech starts."""
    loud = np.nonzero(np.abs(a) > 0.02)[0]
    start = int(loud[0]) if len(loud) else 0
    return a[start:start + int(s * RATE)]


def frames(audio):
    """As the recorder holds an open utterance: 0.3 s of pre-roll, then 30 ms frames."""
    a = np.concatenate([room(0.3), audio])
    return [a[i:i + 480] for i in range(0, len(a) - 479, 480)]


# ---------------------------------------------------------------- the module alone
check("no model file: load says so and returns None", speechvad.load(root / "missing.onnx") is None)
said = []
speechvad.load(root / "missing.onnx", log=said.append)
check("  and the log says what judges instead", said and "loudness" in said[0], said)

src = os.environ.get("SB_SILERO") or os.path.join(os.path.expanduser("~/.standby"), "models", speechvad.MODEL_NAME)
vad = speechvad.load(src) if os.path.exists(src) else None
have = vad is not None and bool(shutil.which("say") and shutil.which("afconvert"))
if not have:
    print("  (the model's checks skipped: needs the Silero model (SB_SILERO or installed), onnxruntime, say and afconvert)")
else:
    import hashlib
    check("the model is the pinned file (sha256)",
          hashlib.sha256(open(src, "rb").read()).hexdigest() == speechvad.MODEL_SHA256)

    def score(audio):
        st = vad.stream()
        st.feed(np.concatenate([room(0.3), audio]))
        return st

    real = speech("Wait, what about the hiring budget for next quarter?")
    t0 = time.time()
    st = score(real)
    per = 1000 * (time.time() - t0) / len(st.probs)
    check("speech scores high: %.2f s of %.2f s (%.2f ms a 32 ms call)" % (st.speechy, len(real) / RATE, per),
          st.speechy >= 1.0 and max(st.probs) > 0.9)
    check("  a call costs well under a millisecond", per < 2.0, per)
    for name, audio in (("silence", np.zeros(RATE, "float32")), ("white noise", noise(1.0)),
                        ("clicks", clicks(1.0)), ("a tone", tone(1.0))):
        st = score(audio)
        check("%s scores low: %.2f s speech, top %.2f" % (name, st.speechy, max(st.probs)), st.speechy < 0.1)
    one = vad.stream()
    one.feed(real)
    bits = vad.stream()
    for i in range(0, len(real), 480):                                     # the recorder's 30 ms frames
        bits.feed(real[i:i + 480])
    check("fed in 30 ms frames, the same as all at once (a part chunk waits)",
          len(one.probs) == len(bits.probs) and np.allclose(one.probs, bits.probs, atol=1e-5))

# ---------------------------------------------------------------- barge_in in the recorder
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
    """The recorder's stream, an utterance on demand: frames is its audio, voiced what the
    energy VAD says of it (0.4 s: loud enough to stop Claude by loudness)."""
    talking, voiced, frames, utt_start = None, 0.0, None, 0
    def __init__(self, *a, **k): pass
    def feed(self, b): pass
    def drain(self, force=False):
        if force and Script.talking:
            Script.talking = None
        return []
    def rotate(self): return []
    def _close(self): pass
    @property
    def utt(self): return Script.frames if Script.talking else None
    def partial(self): return ""


sb.UtteranceTranscriber = Script
engines = []


class FakeEngine(object):
    """The echo-cancelling engine, as in test_call_others: what it played, and its halts."""
    aec = True
    def __init__(self):
        self.mic, self.played, self.halts, self._until = None, [], 0, 0.0
        engines.append(self)
    def start_mic(self, device, feed):
        self.mic = (device, feed)

        def stream():
            while self.mic is not None:
                feed(np.zeros(800, "float32"))
                time.sleep(0.05)
        threading.Thread(target=stream, daemon=True).start()
    def stop_mic(self): self.mic = None
    def say(self, text):
        self.played.append(text)
        self._until = time.time() + 30.0
    @property
    def playing(self): return time.time() < self._until
    def begin(self): pass
    def tts_status(self): return {}
    def halt(self):
        self.halts += 1
        self._until = 0.0


sb.VOICE_BACKEND = FakeEngine
PATIENCE = 30


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def session():
    """A call where the user's voice isn't known on the mic: anyone talking over the voice counts."""
    sb.MY_VOICE = types.SimpleNamespace(trusted=False, trusted_on=lambda n: False, match=lambda e: None,
                                        note_input=lambda *a: False, learn=lambda *a: False)
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("call",), kwargs={"no_claude": True, "kind": "talk"}, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    return th, root / "sessions" / (root / "daemon.ready").read_text().strip()


def speaking(sdir, seq):
    """Claude saying a long answer, the user's mic held."""
    live = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
    (sdir / "dictate").write_text("voice %d\n" % seq)
    wait(lambda: engines and engines[-1].mic is not None)
    (sdir / "dictate").write_text("hold %d\n" % seq)
    wait(lambda: (live().get("voice") or {}).get("mode") == "hold")
    with open(sdir / "speak.jsonl", "a") as fh:
        fh.write(json.dumps({"seq": seq, "id": 1, "say": "A long answer about the release plan."}) + "\n")
    wait(lambda: (live().get("voice") or {}).get("saying"))
    return engines[-1], live


def over(eng, audio, n, why, secs=1.5):
    """`audio` talked over the voice: does it stop? -> stopped. Waits `secs`: one that should
    stop gets the suite's patience, since a busy Mac can be slow to get there."""
    halts = eng.halts
    Script.frames, Script.utt_start, Script.voiced, Script.talking = frames(audio), n, 0.4, why
    stopped = wait(lambda: eng.halts > halts, secs)
    Script.talking, Script.frames, Script.voiced = None, None, 0.0
    return stopped


# Without the model: loudness, as today.
th, sdir = session()
eng, live = speaking(sdir, 31)
check("no model: 0.4 s of loud noise over the voice stops it, as before", over(eng, noise(), 1, "noise", PATIENCE))
sb._stop.set(); th.join(timeout=PATIENCE)
check("  the recorder has no speech model then", sb.Channel.speech_vad is None)

if have:
    (root / "models").mkdir(exist_ok=True)
    shutil.copy(src, str(speechvad.MODEL))
    th, sdir = session()
    eng, live = speaking(sdir, 32)
    check("with the model, voice mode loads it", wait(lambda: sb.Channel.speech_vad is not None, 5))
    check("  0.4 s of loud noise over the voice: it carries on", not over(eng, noise(), 2, "noise"))
    check("  0.4 s of typing: it carries on", not over(eng, clicks(), 3, "clicks"))
    check("  0.4 s of a tone: it carries on", not over(eng, tone(), 4, "tone"))
    check("  0.4 s of speech: it stops", over(eng, spoken(speech("No, I meant the other project.")), 5, "speech", PATIENCE))
    sb._stop.set(); th.join(timeout=PATIENCE)
    th, sdir = session()                      # the voice again, after that stop
    eng, live = speaking(sdir, 33)
    halts = eng.halts
    Script.frames, Script.utt_start, Script.voiced, Script.talking = frames(spoken(real, 0.6)), 6, 0.1, "quiet speech"
    check("  speech the energy VAD hardly hears (0.1 s voiced) stops it too", wait(lambda: eng.halts > halts))
    Script.talking, Script.frames, Script.voiced = None, None, 0.0
    sb._stop.set(); th.join(timeout=PATIENCE)
    sb.Channel.speech_vad = None

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
