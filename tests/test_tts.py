"""Standby's own voice (tts.py) without the model or a speaker.

The voice process for real, with its fake engine (STANDBY_TTS_ENGINE=fake):
ready, each sentence's sound in order as it comes, stopped mid-sentence,
gone on close and when whoever started it goes, a first download told, a
model that fails, a process that dies mid-answer. Then what plays it
(Speech): in order, the next made while one plays, stopped at once, a stuck
process given up, a sentence given while it loads waiting for it.
RecorderSpeaker: a call's voice from the recorder, never another instead;
VoiceSession opening and closing it. Settings > Voice and the "Get one" link.
Nothing is played and no model is loaded."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, plistlib, subprocess, sys, tempfile, threading, time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import numpy as np  # noqa: E402
import tts  # noqa: E402
import voice  # noqa: E402

os.environ["STANDBY_TTS_ENGINE"] = "fake"
os.environ.pop("STANDBY_TTS_PYTHON", None)
root = Path(tempfile.mkdtemp(prefix="sbtts-"))
fails = []
PATIENCE = 30          # seconds: generous, a loaded Mac starts a python slowly; each wait ends as soon as it is true


def check(label, ok, detail=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:500])


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def fake(**opts):
    os.environ["STANDBY_TTS_FAKE"] = json.dumps(opts)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- the pieces
check("the text: a comma first, so the first word is not swallowed",
      tts.lead_in("  Sure, yes. ") == ", Sure, yes.")
check("the voice says \"Standby\" as two words (the engine misreads it as one): only as heard",
      tts.say_as("Standby is listening. Open standby, or STANDBY mode.") == "Stand by is listening. Open Stand by, or Stand by mode."
      and tts.say_as("Standbyness and bystanders stay.") == "Standbyness and bystanders stay.", tts.say_as("Standby is listening."))
x = tts.limit(np.array([0.5, 0.95, -2.0, 3.0], "float32"))
check("peaks rounded off under 1, the rest untouched",
      x[0] == 0.5 and 0.9 < x[1] < 0.95 and -0.991 < x[2] < -0.9 and 0.9 < x[3] <= 0.991, x)
import io  # noqa: E402
buf = io.BytesIO()
tts.write_frame(buf, b"PCM ", 7, np.arange(3, dtype="<f4").tobytes())
tts.write_frame(buf, b"END ", 7)
buf.seek(0)
a, b = tts.read_frame(buf), tts.read_frame(buf)
check("frames: kind, id and body, back as written; then the end",
      a[:2] == (b"PCM ", 7) and np.frombuffer(a[2], "<f4").tolist() == [0, 1, 2] and b == (b"END ", 7, b"")
      and tts.read_frame(buf) is None, (a, b))
check("the voice: automatic is Alba, a Pocket one by name (Jean, chosen, stays Jean), a macOS one is not ours",
      tts.voice_for(None, installed=True) == "alba" and tts.voice_for("pocket:jean", installed=True) == "jean"
      and tts.voice_for("pocket:marius", installed=True) == "marius"
      and tts.voice_for("com.apple.voice.premium.en-US.Ava", installed=True) is None
      and tts.voice_for("pocket:nobody", installed=True) is None)
check("  not installed: macOS's, as before", tts.voice_for(None, installed=False) is None)
os.environ["HF_HUB_OFFLINE"] = "1"               # as the recorder sets it, once its speech model is cached
check("the voice process is not kept offline by the recorder's own setting: its first download can happen",
      "HF_HUB_OFFLINE" not in tts._env() and tts._env()["PYTHONUTF8"] == "1")
os.environ.pop("HF_HUB_OFFLINE")
check("Settings lists Standby's voices, Alba (the default) first",
      [v["id"] for v in tts.voices()][:1] == ["pocket:alba"] and len(tts.voices()) == 8
      and all(v["quality"] == "standby" for v in tts.voices()))
check("getting ready: said while it first downloads, with how far",
      tts.getting_ready("downloading", True, 60, 240) == "Getting the voice ready… 25%"
      and tts.getting_ready("loading", True) == "Getting the voice ready…")
check("  nothing on an ordinary start, or once ready",
      tts.getting_ready("loading", False) == "" and tts.getting_ready("ready", True, 1, 1) == "")
check("  how far, as a number for a progress line: 0-99, None once loading or without a total",
      tts.getting_ready_pct("downloading", True, 60, 240) == 25 and tts.getting_ready_pct("downloading", True, 240, 240) == 99
      and tts.getting_ready_pct("downloading", True, 0, 240) == 0 and tts.getting_ready_pct("loading", True, 1, 1) is None
      and tts.getting_ready_pct("downloading", True, 5, 0) is None and tts.getting_ready_pct("downloading", False, 1, 4) is None)

# The user's hiss (2026-10-11): Pocket's own floor in every pause, and its DC offset,
# lifted by the gain to macOS's level. A made-up sentence like the measured ones: a
# floor of -55 dBFS above 200 Hz with a 0.03 offset, two words with a short gap, a
# long pause, a third word, a tail. Nothing is played.
from scipy.signal import butter, lfilter, sosfilt  # noqa: E402
R, rng = tts.RATE, np.random.RandomState(7)
ms = lambda t: int(t * R / 1000)
def level(x):
    return 10 * np.log10(max(float(np.mean(np.asarray(x, "float64") ** 2)), 1e-24))
floor = lfilter([1.0], [1.0, -0.6], rng.randn(ms(2000))).astype("float32")
floor *= 10 ** (-55 / 20.0) / np.sqrt(np.mean(sosfilt(butter(2, 200, "highpass", fs=R, output="sos"), floor) ** 2))
sig = floor + 0.03
words = [(400, 650), (710, 960), (1360, 1610)]           # ms: a 60 ms gap, then a 400 ms pause
for a, b in words:
    t = np.arange(ms(b) - ms(a))
    sig[ms(a):ms(b)] += (0.25 * np.sin(2 * np.pi * 180 * t / R) * np.sin(np.pi * t / len(t)) ** 0.3
                         + 0.03 * rng.randn(len(t))).astype("float32")
sig = sig[:ms(1910)]
def cleaned(x, size):
    c = tts.Clean()
    return np.concatenate([c.feed(x[i:i + size]) for i in range(0, len(x), size)] + [c.end()])
out = cleaned(sig, 1920)
ref = sosfilt(butter(2, tts.Clean.HIGHPASS, "highpass", fs=R, output="sos"), sig)   # the high-pass alone
check("Standby's voice cleaned: as long as it came, nothing lost or added", len(out) == len(sig), (len(out), len(sig)))
quiet = [(100, 350), (960 + 150, 1360 - 40), (1610 + 170, 1900)]
check("  silent in its pauses: under -70 dBFS (the floor was -55)",
      all(level(out[ms(a):ms(b)]) < -70 for a, b in quiet), [round(level(out[ms(a):ms(b)]), 1) for a, b in quiet])
check("  its words untouched, their starts too", all(abs(level(out[ms(a):ms(b)]) - level(ref[ms(a):ms(b)])) < 0.1 for a, b in words)
      and abs(level(out[ms(400):ms(410)]) - level(ref[ms(400):ms(410)])) < 0.5,
      [round(level(out[ms(a):ms(b)]) - level(ref[ms(a):ms(b)]), 2) for a, b in words])
check("  a short gap between words left as it is (no chopping)",
      abs(level(out[ms(650):ms(710)]) - level(ref[ms(650):ms(710)])) < 0.5)
check("  no offset, and it ends at zero (no tick at its end)", abs(float(out.mean())) < 1e-4 and abs(float(out[-1])) < 1e-4,
      (float(out.mean()), float(out[-1])))
check("  the same however its pieces come", np.allclose(cleaned(sig, 1000), out, atol=1e-6)
      and np.allclose(cleaned(sig, 7), out, atol=1e-6))
cut = cleaned(sig[:ms(1500)], 1920)                   # a sentence that stops mid-word
check("  one that stops mid-word ends at zero too", abs(float(cut[-1])) < 1e-6 and abs(float(sig[ms(1499)])) > 0.01,
      float(cut[-1]))
check("  20 ms later than it came, at most: the first sound barely waits",
      tts.Clean.AHEAD * tts.Clean.BLOCK / float(R) <= 0.02)


# ---------------------------------------------------------------- the voice process, for real
class Got(object):
    def __init__(self, p):
        self.audio, self.ends, self.gone = [], [], 0
        p.on_audio = lambda sid, s: self.audio.append((sid, s.copy()))
        p.on_end = self.ends.append
        p.on_gone = lambda: setattr(self, "gone", self.gone + 1)


logs = []
fake(load=0.2)
p = tts.VoiceProcess("jean", log=logs.append)
g = Got(p)
p.open()
check("open returns at once, the process starting", p.state in ("starting", "loading") and not p.ready, p.state)
check("  then ready", wait(lambda: p.ready), (p.state, p.error, logs))
pid = p._proc.pid
p.say(1, "Orion is the cache.")
p.say(2, "It replaced Redis.")
check("each sentence's sound comes in pieces, in order, then its end",
      wait(lambda: g.ends == [1, 2]) and [s for s, _ in g.audio] == [1, 1, 1, 1, 2, 2, 2]
      and all(len(x) == 1920 and x.dtype == np.float32 for _, x in g.audio), ([s for s, _ in g.audio], g.ends))
check("  ready said in the log, in ASCII", any("ready (Pocket TTS, Jean)" in m for m in logs)
      and all(m == m.encode("ascii", "backslashreplace").decode("ascii") for m in logs), logs)
p.close()
check("close: the process is gone, and with it all its memory", wait(lambda: not alive(pid)) and p.state == "off")
check("  it quit by itself, never by a signal (a crash would show \"Python quit unexpectedly\")", p.exit_code == 0,
      p.exit_code)
check("  and it is not taken for a crash", g.gone == 0)

fake(piece=0.05)
p = tts.VoiceProcess("jean", log=logs.append)
g = Got(p)
p.open()
wait(lambda: p.ready)
p.say(1, "one two three four five six seven eight nine ten eleven twelve")
p.say(2, "the next one")
wait(lambda: len(g.audio) >= 2)
p.stop(2)
check("stop: the sentence ends at once, and the next one is never made",
      wait(lambda: g.ends == [1, 2]) and len(g.audio) < 6 and all(s == 1 for s, _ in g.audio), ([s for s, _ in g.audio], g.ends))
p.say(3, "after it")
check("  and the next one after the stop is said as usual", wait(lambda: 3 in g.ends) and any(s == 3 for s, _ in g.audio))
p.close()
check("  and quit cleanly", p.exit_code == 0, p.exit_code)

fake()
p = tts.VoiceProcess("jean", log=logs.append)
g = Got(p)
p.open()
wait(lambda: p.ready)
pid2 = p._proc.pid
p.say(1, "one two")
p.use("alba")                                   # Settings changed the voice during a call
p.say(2, "three four")
check("another voice from Settings mid-call: the next sentence is in it, the one before it isn't",
      wait(lambda: g.ends == [1, 2]) and all(x[0] > 0 for s, x in g.audio if s == 1)
      and all(x[0] < 0 for s, x in g.audio if s == 2), [(s, float(x[0])) for s, x in g.audio])
check("  the same process (no restart, no load), and opened again it starts in the new voice",
      p.voice == "alba" and p._proc.pid == pid2 and "--voice alba" in " ".join(tts.command(p.voice)), p.voice)
p.close()

fake()
w = subprocess.Popen(tts.command("jean"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
first = tts.read_frame(w.stdout)
w.stdin.close()
check("its stdin closing (the app quit, or crashed): the process ends too, cleanly",
      wait(lambda: w.poll() is not None) and first[0] == b"STAT" and w.returncode == 0, (first, w.returncode))
if w.poll() is None:
    w.kill()                     # failed: never left running

logs[:] = []
fake(download=4000000, load=0.3)
p = tts.VoiceProcess("jean", log=logs.append)
p.open()
seen = []
wait(lambda: seen.append(tts.getting_ready(p.state, p.downloading, p.done, p.total)) or p.ready)
check("the first time: downloading told, how far, and ready after",
      p.ready and p.downloading and any(s.startswith("Getting the voice ready") for s in seen)
      and any("downloading Standby's voice" in m for m in logs), (seen[:5], logs))
p.close()

logs[:] = []
fake(fail="load")
p = tts.VoiceProcess("jean", log=logs.append)
g = Got(p)
p.open()
check("a model that does not load: failed, and why, in the log",
      wait(lambda: p.state == "failed") and "no model here" in (p.error or "")
      and any("macOS's voice speaks" in m for m in logs), (p.state, p.error, logs))
check("  its process ends by itself, with an error code, never by a signal",
      wait(lambda: p.exit_code is not None) and p.exit_code == 1, p.exit_code)
p.close()

p = tts.VoiceProcess("jean", log=logs.append, argv=["/nonexistent/python"])
p.open()
check("  one that cannot even start: failed at once, nothing raised", p.state == "failed" and not p.ready)


# ---------------------------------------------------------------- what plays it
class FakeOut(object):
    def __init__(self):
        self.queued, self.played, self.halts, self.opened, self.closed = [], [], 0, 0, 0
        self.lock = threading.Lock()
    def open(self): self.opened += 1
    def close(self): self.closed += 1
    def play(self, samples, done):
        with self.lock:
            self.queued.append((samples, done))
    def play_one(self):
        with self.lock:
            s, done = self.queued.pop(0)
        self.played.append(s)
        done()
    def play_all(self):
        while self.queued:
            self.play_one()
    def halt(self):
        self.halts += 1
        with self.lock:
            q, self.queued = self.queued, []
        for _s, done in q:
            done()


class FakeProcess(object):
    """The voice process's interface, driven by hand."""
    def __init__(self, ready=True):
        self.state = "ready" if ready else "loading"
        self.downloading, self.done, self.total, self.error = False, 0, 0, None
        self.said, self.stops, self.closed, self.opened = [], [], 0, 0
        self.on_audio = self.on_end = self.on_gone = None
    @property
    def ready(self): return self.state == "ready"
    def open(self): self.opened += 1
    def say(self, sid, text): self.said.append((sid, text)); return True
    def stop(self, upto): self.stops.append(upto)
    def close(self): self.closed += 1; self.state = "off"


piece = np.zeros(1920, "float32")
fp, out = FakeProcess(), FakeOut()
sp = tts.Speech(fp, out, log=logs.append)
sp.say("Orion is the cache.")
sp.say("It replaced Redis.")
check("say: each sentence goes to the process at once (the next is made while one plays)",
      fp.said == [(1, "Orion is the cache."), (2, "It replaced Redis.")] and sp.busy and not sp.speaking)
fp.on_audio(1, piece)
check("  its first piece is held (80 ms): not speaking yet", not sp.speaking and out.queued == [] and sp.busy)
fp.on_audio(1, piece)
fp.on_audio(1, piece)
check("  with PREROLL held, made faster than real time: it plays, speaking, what is said",
      sp.speaking and sp.current == "Orion is the cache." and sp.started_at and len(out.queued) == 3)
fp.on_audio(1, piece)
check("  the player busy: the rest plays as it comes", len(out.queued) == 4)
fp.on_end(1)
fp.on_audio(2, piece)
check("  the next, while the first plays: queued after it, not held", len(out.queued) == 5)
for _ in range(4):
    check("  still on the first while its sound plays", sp.current == "Orion is the cache.")
    out.play_one()
check("  then the next", sp.current == "It replaced Redis." and sp.speaking)
fp.on_end(2)
out.play_all()
check("  then done", not sp.busy and not sp.speaking and sp.current == "")

sp.say("A long one.")
fp.on_audio(3, piece)
sp.say("And more.")
sp.stop()
check("stop: dropped at once, the process told up to the last", out.halts == 1 and out.queued == [] and fp.stops[-1] == 4
      and not sp.busy)
fp.on_audio(3, piece)
fp.on_audio(4, piece)
check("  sound still on its way for them never plays", out.queued == [] and not sp.busy)

sp.say("Heard in part.")
fp.on_audio(5, piece)
sp.say("Never made.")
sp.say("Nor this.")
fp.on_gone()
check("the process dies: what started plays out, the rest is lost (for macOS to say)",
      sp.lost == ["Never made.", "Nor this."] and sp.busy and sp.speaking)
out.play_all()
check("  and then it is done", not sp.busy and sp.take_lost() == ["Never made.", "Nor this."] and sp.lost == [])

# Made slower than real time (the GPU busy), it is held until it won't run dry.
fp, out = FakeProcess(), FakeOut()
sp = tts.Speech(fp, out, log=logs.append)
line = "Hi, I'm Jean. This is how I'll sound in voice mode."      # guessed 3.5 s
sp.say(line)
clock = [1000.0]
real_time = tts.time.time
tts.time.time = lambda: clock[0]
try:
    for _ in range(3):                    # 80 ms of sound every 160 ms: half real time
        fp.on_audio(1, piece)
        clock[0] += 0.16
    check("slow: PREROLL held is not enough, nothing plays yet", out.queued == [] and not sp.speaking)
    n = 3
    while not out.queued and n < 40:
        fp.on_audio(1, piece)
        clock[0] += 0.16
        n += 1
    rest = len(tts.lead_in(line)) / sp.CHARS_PER_S - n * 0.08
    check("  it starts once what is held covers the rest at that rate, all of it at once",
          len(out.queued) == n and n * 0.08 >= rest * 1.0 - 1e-9 and 4 < n < 30 and sp.speaking, (n, rest))
    out.play_all()
    check("  played out before the next piece: not speaking", not sp.speaking and sp.busy)
    fp.on_audio(1, piece)
    check("  run dry: the next piece is held again", out.queued == [])
    fp.on_end(1)
    check("  its end plays what is held", len(out.queued) == 1)
    out.play_all()
    check("  then done", not sp.busy)
    sp.say("Held.")
    fp.on_audio(2, piece)
    sp.stop()
    check("stop drops what is held, and the next starts from a fresh count",
          out.queued == [] and sp._ahead == 0 and not sp.busy)
    sp.say("Heard, held.")
    fp.on_audio(3, piece)
    fp.on_gone()
    check("the process dies: what was held of a sentence begun plays", len(out.queued) == 1 and sp.busy)
    out.play_all()
finally:
    tts.time.time = real_time

fp2 = FakeProcess()
sp2 = tts.Speech(fp2, FakeOut(), log=logs.append)
sp2.STALL = 0.2
sp2.say("Stuck.")
time.sleep(0.25)          # past STALL, on purpose: no sound came for it
check("a sentence with no sound for too long: the process is given up, the sentence lost",
      not sp2.busy and fp2.closed == 1 and sp2.lost == ["Stuck."] and any("stuck" in m for m in logs))
fp3 = FakeProcess(ready=False)                    # still loading (the first time, a download)
sp3 = tts.Speech(fp3, FakeOut(), log=logs.append)
sp3.STALL = 0.2
sp3.say("Given while it loads.")
time.sleep(0.25)
check("a sentence given while the voice loads: never counted as stuck, it waits for it",
      sp3.busy and fp3.closed == 0 and sp3.lost == [])
fp3.state, fp3.ready_at = "ready", time.time()
check("  its time counts once the voice is ready", sp3.busy and fp3.closed == 0)
time.sleep(0.25)
check("  and no sound by STALL after that: given up", not sp3.busy and fp3.closed == 1 and sp3.lost == ["Given while it loads."])

# The whole chain: the real process (fake engine) through Speech.
fake()
pr, out = tts.VoiceProcess("jean", log=logs.append), FakeOut()
sp = tts.Speech(pr, out, log=logs.append)
pr.open()
wait(lambda: pr.ready)
sp.say("one two")
sp.say("three")
check("the real process through Speech: every piece queued to play, in order",
      wait(lambda: len(out.queued) == 3) and [round(float(s[0]), 2) for s, _ in out.queued] == [0.01, 0.02, 0.01])
out.play_all()
check("  done once all has played", wait(lambda: not sp.busy))
sp.close()
check("  close ends the process, cleanly", pr.state == "off" and pr.exit_code == 0, pr.exit_code)


# ---------------------------------------------------------------- a speaker, for VoiceSession
class FakeSpeaker(object):
    def __init__(self): self.said, self.stops, self.speaking, self.current, self.started_at = [], 0, False, "", None
    voice = "Samantha"
    def say(self, t): self.said.append(t)
    def stop(self): self.stops += 1
    @property
    def busy(self): return False



# ---------------------------------------------------------------- RecorderSpeaker and VoiceSession
class Opens(FakeSpeaker):
    def __init__(self): FakeSpeaker.__init__(self); self.opened, self.closed, self.begun = 0, 0, 0
    def open(self): self.opened += 1
    def close(self): self.closed += 1
    def begin(self): self.begun += 1
    getting_ready = "Getting the voice ready…"
    preparing_pct = None


sdir = root / "sessions" / "s1"
sdir.mkdir(parents=True)
rs2 = voice.RecorderSpeaker(sdir)
rs2.bind(7)
(sdir / "live.json").write_text(json.dumps({"at": time.time(), "voice": {
    "speaks": True, "tts": {"state": "downloading", "downloading": True, "done": 1, "total": 4}}}))
rs2.begin()
rows = [json.loads(l) for l in (sdir / "speak.jsonl").read_text().splitlines()]
check("a call's voice, the recorder's: an answer begun, the recorder is told", rows[-1] == {"begin": True, "seq": 7})
check("  and Standby's voice getting ready shows, and how far", rs2.getting_ready == "Getting the voice ready… 25%"
      and rs2.preparing_pct == 25, (rs2.getting_ready, rs2.preparing_pct))
# The call's greeting, a second in: the recorder is up but hasn't taken up Claude's voice yet:
# the greeting waits for it, so the recorder reads it.
logs9 = []
rs9 = voice.RecorderSpeaker(sdir, log=logs9.append)
rs9.bind(9)
(sdir / "live.json").write_text(json.dumps({"at": time.time()}))          # up, not playing yet
rs9.begin()
threading.Timer(0.2, lambda: (sdir / "live.json").write_text(json.dumps({"at": time.time(), "voice": {"speaks": True}}))).start()
rs9.say("Hi, what can I do for you?")
rows = [json.loads(l) for l in (sdir / "speak.jsonl").read_text().splitlines() if '"seq": 9' in l]
check("a greeting before the recorder plays: it waits for the recorder, which says it",
      rows[-2:] == [{"begin": True, "seq": 9}, {"id": 1, "say": "Hi, what can I do for you?", "seq": 9}], rows)
rs10 = voice.RecorderSpeaker(sdir)
rs10.FIRST_WAIT = 0.2
rs10.bind(10)
(sdir / "live.json").write_text(json.dumps({"at": time.time() + 5}))     # up, and not playing yet
rs10.begin()
rs10.say("Still the recorder's.")
rows = [json.loads(l) for l in (sdir / "speak.jsonl").read_text().splitlines() if '"seq": 10' in l]
check("  a recorder that doesn't take it up in time: still given to it, no other voice says it",
      rows[-1] == {"id": 1, "say": "Still the recorder's.", "seq": 10}, rows)
(sdir / "live.json").write_text(json.dumps({"at": time.time(), "voice": {"half": True, "why": "engine failed"}}))
rs10.say("Its engine failed.")
check("  its engine failing for good: nothing said by anyone, and the app shows why",
      rs10.voice_failed == "engine failed" and not rs10.busy
      and '"Its engine failed."' not in (sdir / "speak.jsonl").read_text(), rs10.voice_failed)

vdir = root / "sessions" / "v1"
vdir.mkdir(parents=True)
(vdir / "live.json").write_text(json.dumps({"at": time.time()}))
spk = Opens()
answered = threading.Event()


def ask(q, on_event, on_queued):
    on_queued("q1")
    on_event({"kind": "delta", "text": "Sure, it is."})
    answered.set()
    return "Sure, it is."


vs = voice.VoiceSession("v1", vdir, ask=ask, cancel=lambda q: None, speaker=spk)
vs.start()
check("voice mode on: the voice opened, and the screen says it is getting ready",
      spk.opened == 1 and vs.status().get("preparing") == "Getting the voice ready…" and "preparing_pct" in vs.status()
      and vs.status()["preparing_pct"] is None, vs.status())
spk.preparing_pct = 42
check("  how far, for its progress line", vs.status().get("preparing_pct") == 42, vs.status())
with open(vdir / "dictation.jsonl", "a") as fh:
    fh.write(json.dumps({"seq": vs.seq, "end_turn": True, "text": "is it?"}) + "\n")
check("  a turn: its answer begun on the voice, then said", wait(lambda: spk.said == ["Sure, it is."]) and spk.begun == 1,
      (spk.said, spk.begun))
vs.stop()
check("voice mode off: the voice closed", spk.closed == 1)


# ---------------------------------------------------------------- the real AVFoundation, silently
# Its buffers through a real engine in offline rendering: no device opened, nothing heard.
try:
    import AVFoundation as avf
except ImportError:
    avf = None
if avf is None:
    print("  (AVFoundation part skipped: needs pyobjc)")
else:
    fmt = tts.pcm_format(avf)
    eng = avf.AVAudioEngine.alloc().init()
    ok_manual, _err = eng.enableManualRenderingMode_format_maximumFrameCount_error_(0, fmt, 4096, None)   # 0: offline
    node = avf.AVAudioPlayerNode.alloc().init()
    eng.attachNode_(node)
    eng.connect_to_format_(node, eng.mainMixerNode(), fmt)
    eng.startAndReturnError_(None)
    tone = (0.3 * np.sin(2 * np.pi * 440 * np.arange(1920) / 24000.0)).astype("float32")
    played = []
    node.scheduleBuffer_completionHandler_(tts.pcm_buffer(avf, fmt, tone), lambda: played.append(1))
    node.play()
    rendered = avf.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(eng.manualRenderingFormat(), 4096)
    eng.renderOffline_toBuffer_error_(1920, rendered, None)
    back = np.frombuffer(rendered.floatChannelData()[0].as_buffer(int(rendered.frameLength())), dtype="float32")
    check("its sound as AVFoundation buffers: 24 kHz float, played exactly as made (rendered offline)",
          ok_manual and fmt.sampleRate() == 24000 and len(back) == 1920 and float(np.abs(back - tone).max()) < 1e-6
          and wait(lambda: played == [1], 5), (ok_manual, len(back), played))
    eng.stop()


# ---------------------------------------------------------------- Settings
os.environ.setdefault("STANDBY_ROOT", str(root))
import ui  # noqa: E402
anchors = root / "anchors.plist"
anchors.write_bytes(plistlib.dumps({"AX_FEATURE_SPOKENCONTENT": {"x": 1}, "AX_SPEECH_VOICES": {}}))
old = root / "old.plist"
old.write_bytes(plistlib.dumps({"SpeakableItems": {}}))
check("Get one: Read & Speak where this macOS has that anchor",
      ui.spoken_content_url(anchors).endswith("universalaccess?AX_FEATURE_SPOKENCONTENT"))
check("  the one from before macOS 26 where it does not, or there is no such file",
      ui.spoken_content_url(old).endswith("universalaccess?SpeakableItems")
      and ui.spoken_content_url(root / "missing.plist").endswith("universalaccess?SpeakableItems"))
real_available = tts.available
tts.available = lambda: True
vi = ui.voices_info()
check("Settings > Voice: Standby's voices, automatic is Alba, with its credit",
      vi["standby"][0]["id"] == "pocket:alba" and vi["current"]["id"] == "pocket:alba" and vi["chosen"] is None
      and "Pocket TTS by Kyutai" in vi["credit"], vi)
r = ui.voices_action({"id": "pocket:alba"})
check("  choosing one of them saves it", r.get("ok") and r["voices"]["current"]["name"] == "Alba", r)
r = ui.voices_action({"action": "try", "id": "pocket:alba"})
check("  Try in a test: nothing played, nothing started", r.get("ok") and r.get("tried") == "Alba", r)
ui.voices_action({"id": None})

# Try, on its own process, as it runs outside a test (the speakers and the process faked).
class AutoProcess(FakeProcess):
    made = []
    def __init__(self, voice, log=None):
        FakeProcess.__init__(self, ready=False)
        self.voice, self.plan = voice, AutoProcess.plan
        AutoProcess.made.append(self)
    def open(self):
        self.opened += 1
        self.state, self.downloading = self.plan, self.plan == "downloading"
        if self.plan == "failed":
            self.error = "no model here"
    def say(self, sid, text):
        self.said.append((sid, text))
        threading.Timer(0.05, lambda: (self.on_audio(sid, piece), self.on_end(sid))).start()
        return True


class AutoOut(FakeOut):
    def __init__(self, log=None): FakeOut.__init__(self)
    def play(self, samples, done): threading.Timer(0.05, done).start()


real = (tts.speakers, tts.VoiceProcess, os.environ.pop("STANDBY_NO_DEVICES", None))
tts.speakers, tts.VoiceProcess = AutoOut, AutoProcess


def got(fn, secs=PATIENCE):
    """What fn gives once it gives something (None if it never does)."""
    end = time.time() + secs
    while time.time() < end:
        x = fn()
        if x:
            return x
        time.sleep(0.01)
    return fn()


SEEN = []                         # every state a Try is put in, as it is set: (n, state)
_real_try_set = ui._try_set


def _seen_try_set(n, **kw):
    if kw.get("state"):
        SEEN.append((n, kw["state"]))
    return _real_try_set(n, **kw)


ui._try_set = _seen_try_set


def tried(r):
    """The states a Try went through, to its end, as they were set: not sampled the way
    Settings polls, which on a busy Mac can miss one that lasts a moment (playing, with
    the stand-ins here)."""
    n = (r.get("trying") or {}).get("n")
    wait(lambda: any(m == n and st in ("done", "failed", "skipped") for m, st in SEEN))
    states = []
    for m, st in SEEN:
        if m == n and (not states or states[-1] != st):
            states.append(st)
    return ["starting"] + [st for st in states if st != "starting"]


try:
    AutoProcess.plan = "ready"
    r = ui.voices_action({"action": "try", "id": "pocket:alba"})
    check("Try: it answers at once, and goes on in the background", r.get("ok") and r["trying"]["voice"] == "Alba"
          and r["trying"]["state"] in ("starting", "playing"), r)
    states = tried(r)
    pr = AutoProcess.made[-1]
    check("  Playing only once it is ready, then done: the line in that voice, its process gone",
          states[-2:] == ["playing", "done"] and pr.voice == "alba" and "I'm Alba" in pr.said[0][1]
          and wait(lambda: pr.closed == 1), (states, pr.said))

    AutoProcess.plan = "downloading"
    r = ui.voices_action({"action": "try", "id": "pocket:alba"})
    pr = got(lambda: AutoProcess.made[-1] if AutoProcess.made[-1].plan == "downloading" and AutoProcess.made[-1].opened else None)
    pr.done, pr.total = 100, 400
    check("  the first time: downloading, and how far, not Playing",
          wait(lambda: (ui.voices_info().get("trying") or {}).get("pct") == 25)
          and ui.voices_info()["trying"]["state"] == "downloading", ui.voices_info().get("trying"))
    vi = ui.voices_info()
    check("  Settings meanwhile: getting ready, and how far",
          vi.get("preparing") == "Getting the voice ready… 25%" and vi.get("preparing_pct") == 25, vi)
    r2 = ui.voices_action({"action": "try", "id": "pocket:jean"})
    check("  Try again during it: the same one goes on, no second download",
          r2["trying"]["n"] == r["trying"]["n"] and AutoProcess.made[-1] is pr, r2)
    pr.state = "ready"
    check("  ready: it plays by itself, no second click", tried(r)[-2:] == ["playing", "done"] and pr.said, pr.said)
    check("  its process gone, and Settings no longer says it is getting ready",
          wait(lambda: pr.closed == 1) and "preparing" not in ui.voices_info())

    AutoProcess.plan = "downloading"
    r = ui.voices_action({"action": "try", "id": "pocket:alba"})
    pr = got(lambda: AutoProcess.made[-1] if AutoProcess.made[-1] is not pr and AutoProcess.made[-1].opened else None)
    real_gone, ui.TRY_GONE = ui.TRY_GONE, 0.2
    time.sleep(0.4)                       # Settings closed: nobody asks
    pr.state = "ready"
    check("  ready after Settings closed: downloaded for next time, not played",
          wait(lambda: ui.TRY.get("state") == "skipped") and pr.said == [] and wait(lambda: pr.closed == 1), ui.TRY)
    ui.TRY_GONE = real_gone

    AutoProcess.plan = "downloading"
    r = ui.voices_action({"action": "try", "id": "pocket:alba"})
    pr = got(lambda: AutoProcess.made[-1] if AutoProcess.made[-1] is not pr and AutoProcess.made[-1].opened else None)
    ui.voices_action({"id": "pocket:jean"})            # another voice picked during it
    pr.state = "ready"
    check("  ready after another voice was picked: downloaded, not played",
          wait(lambda: ui.TRY.get("state") == "skipped") and pr.said == [], ui.TRY)
    ui.voices_action({"id": None})

    AutoProcess.plan = "failed"
    r = ui.voices_action({"action": "try", "id": "pocket:alba"})
    t = got(lambda: (lambda x: x if x and x["state"] == "failed" else None)(ui.voices_info().get("trying")))
    check("  a voice that fails: why, in a few words, and nothing left running",
          t and t["error"] == "no model here" and t["downloaded"] is False and wait(lambda: AutoProcess.made[-1].closed == 1), t)
    real_keep, ui.TRY_KEEP = ui.TRY_KEEP, 0.0
    check("  and a while later Settings no longer shows it", wait(lambda: "trying" not in ui.voices_info()))
    ui.TRY_KEEP = real_keep
finally:
    tts.speakers, tts.VoiceProcess = real[0], real[1]
    if real[2] is not None:
        os.environ["STANDBY_NO_DEVICES"] = real[2]

seen = [(0.0, 0), (1.0, 10), (2.0, 20), (3.0, 30)]
check("time left: from the rate over the last seconds", ui._time_left(seen, 30, 130) == 10)
check("  not before 3 s of it, nor while nothing moves, nor at its very end (no wrong guess)",
      ui._time_left(seen[:3], 20, 120) is None and ui._time_left([(0, 5), (3, 5)], 5, 120) is None
      and ui._time_left(seen, 129, 130) is None)
check("why it failed, in a few words",
      ui._short_reason("LocalEntryNotFoundError: An error happened while trying to locate the file on the Hub") == "no connection to Hugging Face"
      and ui._short_reason("OSError: [Errno 28] No space left on device") == "the disk is full"
      and ui._short_reason("RuntimeError: no model here") == "no model here" and ui._short_reason(None) == "no reason given"
      and len(ui._short_reason("ValueError: " + "x" * 200)) == 78)
tts.available = lambda: False
vi = ui.voices_info()
check("  not installed: no Standby voices, a macOS one speaks, no credit",
      vi["standby"] == [] and vi["current"]["quality"] != "standby" and vi["credit"] == "", vi)
tts.available = real_available

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
