"""Only the room: what the Mac plays taken back out of the mic while dictating
or in voice mode, so a video on the speakers never lands in what the user says.

First the canceller on made-up mixes (a talker-like video through a made-up room,
the user over it), then the pipeline that lines the mic up with the Mac's sound
(mac/roomonly.py: Reference, RoomOnly) on a made-up clock, then the recorder's mic
channels with fakes. Fakes only: no ScreenCaptureKit, no mic, nothing played."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbroom-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
import roomonly  # noqa: E402
from native.macos import appaudio as meeting_audio  # noqa: E402  (the Mac's capture itself)
import devices as dv  # noqa: E402

# Never the real capture, even from a mutant of this test (2026-10-08: one reached it).
REAL = []


class NoRealCapture(object):
    def __init__(self, *a, **k):
        REAL.append(a)
        raise AssertionError("a test made the real ScreenCaptureKit capture")


REAL_MEETING_AUDIO, meeting_audio.MeetingAudio = meeting_audio.MeetingAudio, NoRealCapture

fails = []
PATIENCE = 30           # generous: these suites run five at a time on one Mac
R = roomonly.RATE


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- made-up sound
def talker(seconds, f0, seed, formants=(700, 1200, 2600)):
    """Speech-like: a pulse train with a drifting pitch, through three formants, in
    syllables of 0.12-0.3 s with gaps between."""
    g = np.random.default_rng(seed)
    n = int(seconds * R)
    t = np.arange(n) / R
    pitch = f0 * (1 + 0.1 * np.sin(2 * np.pi * 0.7 * t + g.uniform(0, 6)))
    src = (np.diff(np.floor(np.cumsum(pitch / R)), prepend=0) > 0).astype(float) + 0.02 * g.standard_normal(n)
    f = np.fft.rfftfreq(n, 1.0 / R)
    shape = sum(1 / (1 + ((f - fc) / (80 + fc * 0.08)) ** 2) for fc in formants)
    out = np.fft.irfft(np.fft.rfft(src) * shape, n)
    env, k = np.zeros(n), 0
    while k < n:
        size, gap = int(g.uniform(0.12, 0.3) * R), int(g.uniform(0.04, 0.2) * R)
        env[k:k + size] = np.hanning(size)[:len(env[k:k + size])]
        k += size + gap
    out = out * env
    return out / (np.max(np.abs(out)) + 1e-9) * 0.5


rng = np.random.default_rng(1)


def room_ir(rt60=0.35):
    n = int(0.25 * R)
    t = np.arange(n) / R
    ir = rng.standard_normal(n) * np.exp(-6.9 * t / rt60)
    ir[:int(0.003 * R)] = 0
    ir[int(0.003 * R)] = 3.0                       # the direct sound, then the room
    return ir / np.sqrt(np.sum(ir ** 2))


def through_room(played, delay, gain, ir):
    """A laptop speaker (a little nonlinear), the room, and the delay to the mic."""
    wet = np.convolve(np.tanh(1.8 * played) / 1.8, ir)[:len(played)] * gain
    return np.concatenate([np.zeros(int(delay * R)), wet])[:len(played)]


def db(a):
    return 10 * np.log10(np.mean(np.asarray(a) ** 2) + 1e-20)


def run(mic, ref, ec=None, block=800):
    """The mic through a canceller, block by block, put back in line with the mic."""
    ec = ec or roomonly.EchoCanceller()
    out = np.concatenate([ec.process(mic[i:i + block], ref[i:i + block]) for i in range(0, len(mic), block)])
    return np.concatenate([out[ec.LATENCY:], np.zeros(ec.LATENCY)]), ec


def user_loss(out, usr, at):
    """How much quieter each 32 ms frame of the user's is in the output, in dB."""
    loss = []
    for k in range(at, at + int(4 * R) - 512, 512):
        if db(usr[k:k + 512]) > -45:
            loss.append(db(usr[k:k + 512]) - db(out[k:k + 512]))
    return np.array(loss)


VIDEO = talker(10, 180, 1, (500, 1500, 2500))     # someone talking in a video
USER = talker(4, 120, 2)                          # the user
N = len(VIDEO)
IR = room_ir()
ECHO = through_room(VIDEO, 0.045, 0.5, IR)

# ---------------------------------------------------------------- the canceller
mic = ECHO + 0.002 * rng.standard_normal(N)
c0 = time.process_time()
out, ec = run(mic, VIDEO)
cpu = (time.process_time() - c0) / (N / float(R))
check("a video alone on the speakers: taken out of the mic (>= 10 dB down once learnt)",
      db(mic[2 * R:]) - db(out[2 * R:]) >= 10, (db(mic[2 * R:]), db(out[2 * R:])))
check("  it found the delay to the mic (45 ms, less a frame), and that the mic hears it",
      abs(ec.delay - (int(0.045 * R) - roomonly.HOP)) < 64 and ec.heard > 0.5, (ec.delay, ec.heard))
check("  and costs little: under 5%% of one core (%.1f%% here)" % (100 * cpu), cpu < 0.05, cpu)

usr = np.zeros(N)
usr[5 * R:5 * R + len(USER)] = USER * 0.6
mic = ECHO + usr + 0.002 * rng.standard_normal(N)
out, ec = run(mic, VIDEO)
loss = user_loss(out, usr, 5 * R)
check("the user over the video: every word of theirs kept (no 32 ms frame cut by 10 dB)",
      len(loss) > 30 and np.max(loss) < 10 and np.median(loss) < 3, (len(loss), np.median(loss), np.max(loss)))
seg = slice(5 * R, 5 * R + len(USER))
both = np.stack([usr[seg], ECHO[seg]], 1)
coef = np.linalg.lstsq(both, out[seg], rcond=None)[0]
check("  and the video under them is still taken out (>= 10 dB down; %.0f dB here)"
      % (-20 * np.log10(abs(coef[1]) + 1e-9)), abs(coef[1]) < 10 ** (-10 / 20.0) and coef[0] > 0.5, coef)
after = slice(5 * R + len(USER) + R // 10, N)
check("  the video before and after them too", db(mic[2 * R:5 * R]) - db(out[2 * R:5 * R]) >= 10
      and db(mic[after]) - db(out[after]) >= 10, (db(out[2 * R:5 * R]), db(out[after])))
check("  and never louder than the mic (a filter that learnt the user would add to it)",
      all(db(out[k:k + R // 2]) <= db(mic[k:k + R // 2]) + 0.5 for k in range(R, N - R // 2, R // 2)))

mic = np.zeros(N)
mic[R:R + len(USER)] = USER * 0.6
out, ec = run(mic, VIDEO)
check("something plays but the mic does not hear it (headphones): the user comes through whole",
      np.max(np.abs(out - mic)) < 1e-3, np.max(np.abs(out - mic)))
out, ec = run(mic, np.zeros(N))
check("nothing plays: the mic exactly as it was, only 32 ms later", np.max(np.abs(out - mic)) < 1e-6,
      np.max(np.abs(out - mic)))

# What it learnt is kept for the next dictation in the same room (canceller_for).
roomonly.CANCELLERS.clear()
first = roomonly.canceller_for("speakers")
run(ECHO[:4 * R] + 0.002 * rng.standard_normal(4 * R), VIDEO[:4 * R], ec=first)
again = roomonly.canceller_for("speakers")
check("the next dictation on the same speakers: the same canceller, what it learnt kept",
      again is first and np.any(again.W != 0) and again.frames == 0)
check("  another output: its own", roomonly.canceller_for("display") is not first)
later = slice(4 * R, 4 * R + R // 2)
mic = ECHO + 0.002 * rng.standard_normal(N)
warm, _ = run(mic[later], VIDEO[later], ec=again)
cold, _ = run(mic[later], VIDEO[later])
first = slice(0, R // 2 - roomonly.EchoCanceller.LATENCY)
check("  so a video already playing is taken out from its first half second (warm %.0f dB, cold %.0f)"
      % (db(warm[first]), db(cold[first])), db(mic[later][first]) - db(warm[first]) >= 6
      and db(warm[first]) < db(cold[first]) - 3, (db(mic[later][first]), db(warm[first]), db(cold[first])))


# ---------------------------------------------------------------- the pipeline, on a made-up clock
class Clock(object):
    now = 1000.0

    def __call__(self):
        return self.now


def pipeline(capture=None, clock=None, ec=None, logs=None):
    clock = clock or Clock()
    capture = capture or roomonly.FakeCapture()
    ref = roomonly.Reference(capture, log=(logs.append if logs is not None else lambda m: None), clock=clock)
    ref.start().thread.join(PATIENCE)
    room = roomonly.RoomOnly(ref, ec or roomonly.EchoCanceller(), log=(logs.append if logs is not None
                                                                       else lambda m: None), clock=clock)
    return room, ref, capture, clock


def simulate(mic, played, echo_delay=0.0, ref_lag=(0.0, 0.06), mic_block=800, drain_every=0.1, room=None,
             clock=None, capture=None, ref_from=0.0):
    """A dictation in time: the Mac plays `played` from t=0 (ScreenCaptureKit hands it
    on in 320-sample blocks, each late by ref_lag), the mic's blocks come as captured,
    and the loop drains every drain_every. Returns what the room handed back, in order."""
    if room is None:
        room, _ref, capture, clock = pipeline()
    t0, events = clock.now, []
    g = np.random.default_rng(3)
    for i in range(0, len(played), 320):
        pts = t0 + i / float(R)
        if pts - t0 >= ref_from:
            events.append((pts + 320 / float(R) + g.uniform(*ref_lag), 0, ("ref", played[i:i + 320], pts)))
    for i in range(0, len(mic), mic_block):
        events.append((t0 + (i + mic_block) / float(R) + 0.004 + g.uniform(0, 0.003), 1, ("mic", mic[i:i + mic_block])))
    k = t0
    while k < t0 + len(mic) / float(R) + 1:
        events.append((k, 2, ("drain",)))
        k += drain_every
    out = []
    for at, _order, ev in sorted(events, key=lambda e: (e[0], e[1])):
        clock.now = at
        if ev[0] == "ref":
            capture.play(ev[1], ev[2])
        elif ev[0] == "mic":
            out.append(room.clean(ev[1]))
        else:
            out.append(room.drain())
    out.append(room.flush())
    return np.concatenate(out), room


# The echo reaches the mic 45 ms after ScreenCaptureKit stamps it (speakers, air, the mic's buffer).
mic = through_room(VIDEO, 0.045, 0.5, IR) + usr + 0.002 * rng.standard_normal(N)
out, room = simulate(mic, VIDEO)
check("in time, as it comes: every sample of the mic back, in order", len(out) == len(mic), (len(out), len(mic)))
check("  the video taken out (>= 10 dB), lined up by the samples' own count and stamps",
      db(mic[2 * R:5 * R]) - db(out[2 * R:5 * R]) >= 10, (db(mic[2 * R:5 * R]), db(out[2 * R:5 * R])))
loss = user_loss(out, usr, 5 * R)
check("  and the user over it kept", len(loss) > 30 and np.max(loss) < 10 and np.median(loss) < 3,
      (np.median(loss), np.max(loss)))
check("  its cost counted (%.1f%% of a core)" % (100 * room.cpu / (N / float(R))),
      0 < room.cpu < 0.05 * N / float(R), room.cpu)

out, room = simulate(mic, VIDEO, mic_block=8000, drain_every=0.1)
check("the recorder's half-second blocks, drained by its loop: the same", len(out) == len(mic)
      and db(mic[2 * R:5 * R]) - db(out[2 * R:5 * R]) >= 10, (len(out), db(out[2 * R:5 * R])))

# Nothing has come from ScreenCaptureKit yet (it takes 0.1-0.3 s to start): no waiting.
room, ref, capture, clock = pipeline()
clock.now += 0.05
got = room.clean(np.ones(800, "float32") * 0.1)
check("before the Mac's sound has come: the mic is not held (all but the canceller's 32 ms)",
      len(got) == 800 - roomonly.EchoCanceller.LATENCY, len(got))
clock.now += 0.05
got = room.clean(np.ones(800, "float32") * 0.1)
check("  and is the mic as it was", len(got) == 800 and np.allclose(got, 0.1, atol=1e-4), got[:4])

# The Mac's sound of a block's moment has not come yet: the block waits for it.
room, ref, capture, clock = pipeline()
capture.play(np.zeros(1600), clock.now)              # 0.1 s of it, up to now
clock.now += 0.1
room.clean(np.zeros(1600, "float32"))                # the mic's first 0.1 s
clock.now += 0.05
got = room.clean(np.zeros(800, "float32"))           # the next 50 ms: the Mac's sound of them still to come
check("the Mac's sound of this moment still to come: the block waits (nothing handed back)", len(got) == 0,
      len(got))
check("  nor on the loop's drain, while it is not late", len(room.drain()) == 0)
capture.play(np.zeros(800), clock.now - 0.05)
got = room.drain()
check("  it comes: the block goes on, at the next drain", len(got) == 800, len(got))
clock.now += 0.05
got = room.clean(np.zeros(800, "float32"))
check("  later than HOLD and still none: it goes on without", len(got) == 0 and (
    setattr(clock, "now", clock.now + roomonly.HOLD + 0.01) or len(room.drain()) == 800))
clock.now += roomonly.GONE + 0.1
check("the Mac's sound stopped coming: the mic waits for none",
      len(room.clean(np.zeros(800, "float32"))) == 800)
rest = room.flush()
check("flush(): the rest (the canceller's last 32 ms), so the last word is not cut",
      len(rest) == roomonly.EchoCanceller.LATENCY, len(rest))
check("  after it, the mic as it is, and flush again gives nothing",
      len(room.clean(np.ones(800, "float32"))) == 800 and len(room.flush()) == 0)

# Anything wrong in it: the mic as it is, never an exception on the audio thread.
logs = []


class Broken(roomonly.EchoCanceller):
    def process(self, mic, ref):
        raise ValueError("boom")


room, ref, capture, clock = pipeline(ec=Broken(), logs=logs)
try:
    got = room.clean(np.ones(800, "float32") * 0.2)
except Exception as exc:
    got = exc
check("the canceller failing: the block as it came, no exception",
      not isinstance(got, Exception) and len(got) == 800 and np.allclose(got, 0.2), got)
check("  said once, then the mic as it is", room.broken and sum("room only: stopped" in m for m in logs) == 1
      and len(room.clean(np.ones(800, "float32"))) == 800 and len(room.flush()) == 0, logs)

# The reference: starts without holding anything up, stops even mid-start.
class Slow(roomonly.FakeCapture):
    def start(self, target=None, feed=None):
        time.sleep(0.3)
        roomonly.FakeCapture.start(self, target, feed)


slow = Slow()
t = time.time()
ref = roomonly.Reference(slow).start()
check("the reference starts at once (ScreenCaptureKit's start is on its own thread)", time.time() - t < 0.1)
ref.stop()
ref.thread.join(PATIENCE)
check("  stopped while it was starting: stopped once it has", slow.stops == 1 and slow.target == meeting_audio.EVERYTHING,
      (slow.stops, slow.target))
logs = []
refused = roomonly.FakeCapture()
refused.fail_with = "not allowed"
ref = roomonly.Reference(refused, log=logs.append).start()
ref.thread.join(PATIENCE)
check("  refused: said once in the log; the mic goes as it is", any("not heard (not allowed)" in m for m in logs)
      and ref.ready(1e12, 0), logs)

real_allowed = roomonly.allowed
roomonly.allowed = lambda: False
logs = []
check("not allowed in System Settings: no reference, nothing asked, one calm line",
      roomonly.start("speakers", log=logs.append) is None and any("not allowed" in m for m in logs), logs)
roomonly.allowed = lambda: None
env = os.environ.pop("STANDBY_NO_DEVICES", None)     # this check alone: the allowed() rule, not the test's
try:                                                 # (the real capture is still NoRealCapture here)
    try:
        unknown = roomonly.start("speakers")
    except AssertionError as exc:
        unknown = exc
finally:
    if env is not None:
        os.environ["STANDBY_NO_DEVICES"] = env
check("  unknown whether it is: the same (never a prompt)", unknown is None and not REAL, (unknown, REAL))
del REAL[:]
roomonly.allowed = lambda: True
check("  allowed, in a test: still never the real capture", roomonly.start("speakers") is None and not REAL, REAL)
roomonly.allowed = real_allowed

# A process that exits with the Mac's sound still coming crashes in ScreenCaptureKit's
# callback (2026-10-08): whatever is still capturing is stopped at exit.
import subprocess  # noqa: E402
code = """import sys
sys.path.insert(0, "mac")
import roomonly
class Cap(roomonly.FakeCapture):
    def stop(self):
        print("stopped")
        sys.stdout.flush()
ref = roomonly.Reference(Cap()).start()
ref.thread.join()
"""
p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=PATIENCE)
check("exiting with the Mac's sound still coming: it is stopped first", p.returncode == 0 and "stopped" in p.stdout,
      (p.returncode, p.stdout, p.stderr[-300:]))

# Every real capture path refuses in a test, for the children a test starts too (they
# inherit STANDBY_NO_DEVICES, not the tripwire). Checked without reaching any of them.
import inspect  # noqa: E402
from native import permissions  # noqa: E402
from native.macos import permissions as mac_permissions  # noqa: E402
cap = REAL_MEETING_AUDIO()
cap.start(meeting_audio.EVERYTHING, lambda b, t: None)
check("in a test: meeting_audio's real capture refuses (before ScreenCaptureKit)",
      not cap.ok and "STANDBY_NO_DEVICES" in (cap.error or ""), (cap.ok, cap.error))
try:
    permissions.no_devices(None)
    shot = "allowed"
except RuntimeError as exc:
    shot = str(exc)
check("  Screen Recording's prompt and System Settings refuse",
      "STANDBY_NO_DEVICES" in shot and all("no_devices(" in inspect.getsource(f) for f in
                                           (mac_permissions.ask_screen, mac_permissions.open_screen_settings)), shot)

# And the suites' tripwire (tests/agentpath.py): reaching the real ScreenCaptureKit fails a suite.
code = """import sys
sys.path.insert(0, "tests")
import agentpath
import ScreenCaptureKit as SC
try:
    SC.SCShareableContent
except RuntimeError:
    pass
"""
p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=PATIENCE)
check("a suite that reaches the real ScreenCaptureKit fails, even when it catches the error",
      p.returncode == 1 and "reached the real ScreenCaptureKit: SCShareableContent" in p.stdout, (p.returncode, p.stdout))


# ---------------------------------------------------------------- the recorder's mic channels, with fakes
MAC = dv.Device("BuiltInMicrophoneDevice", "MacBook Pro Microphone", dv.BUILT_IN)
SPEAKERS = dv.Device("BuiltInSpeakerDevice", "MacBook Pro Speakers", dv.BUILT_IN)


class System(object):
    leaks = True          # speakers (the Mac's sound reaches the mic), or headphones


class FakeDevices(object):
    def inputs(self): return [MAC]
    def default_input(self): return MAC
    def default_output(self): return SPEAKERS
    def in_use(self, d): return False
    def watch(self, on_change): return True
    def output_leaks(self): return System.leaks


sent = [0]


class FakeInput(object):
    def __init__(self, device=None, callback=None, blocksize=8000, **kw):
        self.cb, self.n, self.run = callback, blocksize, False

    def start(self):
        self.run = True

        def go():
            while self.run:
                time.sleep(self.n / 16000.0)        # real time on purpose: the mic's own block rate
                if self.run:
                    sent[0] += self.n
                    self.cb(np.full((self.n, 1), 0.01, "float32"), self.n, None, None)
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self.run = False
    def close(self): pass


PA = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: PA if i is None else PA[i],
    default=types.SimpleNamespace(device=[0, 0]), _terminate=lambda: None, _initialize=lambda: None)
from importlib.machinery import SourceFileLoader  # noqa: E402
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
logged = []
sb.log = logged.append
sb.SESSIONS, sb.CONFIG = root / "sessions", root / "config.json"
sb.load_model = lambda name: None
sb.DEVICES = FakeDevices()
fed = [0]


class Script(object):
    def __init__(self, *a, **k): pass
    def feed(self, b): fed[0] += len(b)
    def drain(self, force=False): return []
    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Script
check("in a test, and with no factory put in: nothing hears the Mac's sound", sb.room_only() is None)
made = []


class Playing(roomonly.FakeCapture):
    """The Mac's sound as ScreenCaptureKit hands it on: 20 ms blocks, in real time,
    each 120 ms after its own stamp (late, but within HOLD). So each of the mic's
    blocks waits a moment for it, longer than the fake mic's own drift."""

    def start(self, target=None, feed=None):
        roomonly.FakeCapture.start(self, target, feed)
        self.run = True

        def go():
            while self.run:
                time.sleep(0.02)
                self.play(np.zeros(320), time.monotonic() - 0.12 - 0.02)
        threading.Thread(target=go, daemon=True).start()

    def stop(self):
        self.run = False
        roomonly.FakeCapture.stop(self)


def make(output):
    cap = Playing()
    ref = roomonly.Reference(cap, log=sb.log).start()
    made.append((output, cap, ref))
    return ref


sb.ROOM_ONLY = make
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False,
                                 "mic_automatic": True}))
sb._stop.clear()
th = threading.Thread(target=sb.run_session, args=("roomonly",), kwargs={"no_claude": True}, daemon=True)
th.start()
check("a session starts", wait(lambda: (root / "daemon.ready").exists()))
sdir = root / "sessions" / (root / "daemon.ready").read_text().strip()
check("  no dictation: the Mac's sound is not heard", not made and wait(lambda: fed[0] > 0))

(sdir / "dictate").write_text("on 5\n")
check("dictating on speakers: the Mac's sound taken out of the mic",
      wait(lambda: any("room only: on" in m for m in logged)) and len(made) == 1, logged[-5:])
check("  per output device and mic, all apps but Standby's", made and made[0][0] == SPEAKERS.uid
      and made[0][1].target == meeting_audio.EVERYTHING, made)
wait(lambda: made[0][1].starts == 1)
at = sent[0]
check("  each block of the mic waits for the Mac's sound of its time, and goes on as it comes (not a block late)",
      wait(lambda: sent[0] > at and fed[0] == sent[0] - roomonly.EchoCanceller.LATENCY, 5), (fed[0], sent[0]))
time.sleep(1.2)                                       # real time: two of the mic's blocks through it
(sdir / "dictate").write_text("off 5\n")
check("dictation over: the reference stops with it", wait(lambda: made[0][1].stops == 1), made[0][1].stops)
import re  # noqa: E402
wait(lambda: any("dictation 5: off" in m for m in logged))
said = [float(m.group(1)) for m in (re.match(r"room only: ([0-9.]+)s of the mic", x) for x in logged) if m]
check("  the mic went through it while dictating (%s s of it)" % (said[-1] if said else "none"),
      said and said[-1] >= 0.9, said)

System.leaks = False
time.sleep(2.1)                                       # output_leaks is looked at every 2 s
(sdir / "dictate").write_text("on 6\n")
wait(lambda: any("dictation 6: on" in m for m in logged))
check("dictating on headphones: nothing to take out, nothing heard", len(made) == 1)
(sdir / "dictate").write_text("off 6\n")
wait(lambda: any("dictation 6: off" in m for m in logged))

System.leaks = True
time.sleep(2.1)
(sdir / "dictate").write_text("voice 7\n")
check("voice mode on speakers: the same", wait(lambda: len(made) == 2), len(made))
(sdir / "dictate").write_text("on 8\n")
time.sleep(0.6)
check("  starting again for the next dictation keeps its one reference", len(made) == 2, len(made))
(sdir / "dictate").write_text("off 7\n")
check("  stopped with it", wait(lambda: len(made) > 1 and made[1][1].stops == 1))

sb._stop.set()
th.join(timeout=PATIENCE)
wait(lambda: not (root / "daemon.ready").exists())
check("every sample the mic sent was transcribed, the same count (the clock is the mic's)",
      fed[0] == sent[0] and sent[0] > 0, (fed[0], sent[0]))

# ---------------------------------------------------------------- Standby Dictation's own mic
class Out(object):
    def __init__(self, leaks): self.leaks = leaks
    def output_leaks(self): return self.leaks
    def default_output(self): return SPEAKERS


logs = []
check("Standby Dictation on headphones: nothing to take out, nothing captured",
      roomonly.for_dictation(logs.append, capture=roomonly.FakeCapture(), devices=Out(False)) is None)
cap = roomonly.FakeCapture()
droom = roomonly.for_dictation(logs.append, capture=cap, devices=Out(True))
droom.ref.thread.join(PATIENCE)
check("  on speakers: its own reference, all apps but Standby's, its own canceller for the room",
      isinstance(droom, roomonly.RoomOnly) and droom.owns and cap.target == meeting_audio.EVERYTHING
      and (SPEAKERS.uid, "dictation") in roomonly.CANCELLERS, (droom, cap.target, list(roomonly.CANCELLERS)))

looked = []
real_allowed, roomonly.allowed = roomonly.allowed, lambda: (looked.append(1), True)[1]
check("  the permission as the app last saw it: not looked at again on the key press (~8 ms)",
      roomonly.for_dictation(devices=Out(True), permitted=False) is None and looked == [], looked)
roomonly.allowed = real_allowed

import dictation  # noqa: E402
from native.macos import dictation_app as da  # noqa: E402  (its Mac parts: the app, keys, pasting)
c = dictation.Capture()
c.np, c.room = np, droom
sent_d = 0
for k in range(10):                                   # 0.5 s, in PortAudio's 50 ms blocks
    c._callback(np.full((800, 1), 0.05, "float32"), 800, None, None)
    sent_d += 800
check("its blocks go through the room: the canceller's 32 ms held back, not lost",
      sum(len(b) for b in c.blocks) == sent_d - roomonly.EchoCanceller.LATENCY and c.level > 0,
      (sum(len(b) for b in c.blocks), c.level))
audio = c.stop()
check("  at stop, the rest: every sample of the mic, the end of the last word too",
      len(audio) == sent_d and np.allclose(audio[-1000:], 0.05, atol=1e-3), (len(audio), audio[-3:]))
check("  and the reference stops with it (on its own thread: the text is not held up)",
      wait(lambda: cap.stops == 1, 5) and c.room is None, cap.stops)


class Raising(object):
    """clean() raises; what it held still comes back at flush (raw, the way RoomOnly's break does)."""
    broken = None
    def clean(self, block): raise ValueError("boom")
    def flush(self): return np.full(300, 0.07, "float32")
    def stop(self): raise RuntimeError("can't start a thread")


c = dictation.Capture()
c.np, c.room = np, Raising()
try:
    c._callback(np.full((800, 1), 0.05, "float32"), 800, None, None)
    raised = None
except Exception as exc:
    raised = exc
check("the room failing on PortAudio's thread: no exception there, the raw block kept, the mic as it is",
      raised is None and c.room_failed and len(c.blocks) == 1 and len(c.blocks[0]) == 800, (raised, c.blocks))
try:
    audio = c.stop()
except Exception as exc:
    audio = exc
check("  at stop, what it held still comes back (the last word), and a stop that fails loses nothing",
      not isinstance(audio, Exception) and len(audio) == 1100 and np.allclose(audio[-300:], 0.07), audio)
stopped = []
c = dictation.Capture()
c.np, c.room = np, Raising()
c.room.stop = lambda: stopped.append(1)
c._callback(np.full((800, 1), 0.05, "float32"), 800, None, None)
c.stop()
check("  and its reference is still stopped at the end", stopped == [1], stopped)

made_d = []
dictation.ROOM_ONLY = lambda: made_d.append(1) or None
check("  Standby Dictation asks its factory when a dictation starts", dictation.room_for_dictation() is None
      and made_d == [1])
dictation.ROOM_ONLY = None
check("  and in a test, with none put in: none", dictation.room_for_dictation() is None)
dictation.ROOM_ONLY = lambda: made_d.append(2) or None
c = dictation.Capture()
c.start(room=False)                                   # the mic opened only so macOS asks for it
c.stop()
check("  opening the mic only so macOS asks for it: the Mac's sound is not captured", made_d == [1], made_d)
c.start()
c.stop()
check("  a dictation: it is", made_d == [1, 2], made_d)
dictation.ROOM_ONLY = None

# Standby Dictation's setup window: the optional row, only with room_only.
ok3 = {"listen": True, "post": True, "mic": "allowed"}
check("the setup row: not offered unless room_only is on (today's three rows)",
      [r["key"] for r in dictation.setup_rows(ok3)] == ["mic", "listen", "post"] and not dictation.offered(ok3))
room_perm = dict(ok3, hear_mac=False, hear_mac_asked=False)
rows = dictation.setup_rows(room_perm)
check("  offered: a fourth row, optional, its button Allow (macOS asks once), then Open Settings",
      [r["key"] for r in rows] == ["mic", "listen", "post", "hear_mac"] and rows[3]["optional"]
      and rows[3]["button"] == "Allow" and rows[3]["why"] == "leaves out the Mac's sound, never the screen"
      and dictation.setup_rows(dict(room_perm, hear_mac_asked=True))[3]["button"] == "Open Settings…"
      and rows[3]["url"].endswith("Privacy_ScreenCapture"), rows[3])
check("  optional: nothing is missing for it (the window never opens by itself for it)",
      dictation.missing(room_perm) == [] and dictation.optional_missing(room_perm) == ["hear_mac"]
      and dictation.optional_missing(dict(room_perm, hear_mac=True)) == [])
cfg_path = root / "config.json"
dc = da.Controller.alloc().initHeadless_(True)
# Nothing real: no permission read, no keyboard tap, whatever this Mac allows the test.
da.can_listen = da.can_post = lambda: True
da.mic_status, da.apple_dictation_on = (lambda: "allowed"), (lambda: False)
dc.install_tap = lambda: None
for on in (False, True):
    cfg_path.write_text(json.dumps({"room_only": on}))
    dictation.screen_allowed, dictation.SCREEN["allowed"] = (lambda: False), None
    dc.check_permissions()
    if not on:
        check("  the app looks only while room_only is on", "hear_mac" not in dc.perm and dictation.SCREEN["allowed"] is None,
              dc.perm)
check("  and then without asking: its last look kept for the key press", dc.perm.get("hear_mac") is False
      and dictation.SCREEN["allowed"] is False, dc.perm)
asked_screen = []
dictation.ask_screen = lambda: asked_screen.append(1)
da.open_url = lambda url: asked_screen.append(url)
sw = da.SetupWindow(dc, optional=True)
sw.update(dictation.setup_rows(dc.perm, dc.settings))
dc.setupAction_(sw.buttons[3])
check("  its button, clicked: macOS's own prompt, that once", asked_screen == [1], asked_screen)
check("  the window says one more is optional, and is never shown in a test",
      len(sw.buttons) == 4 and not sw.visible, len(sw.buttons))
cfg_path.write_text(json.dumps({}))

# The apps switched off in a meeting: each its own reference, on that app alone, added together.
a_cap, b_cap = roomonly.FakeCapture(), roomonly.FakeCapture()
a = roomonly.Reference(a_cap, target="us.zoom.xos").start()
b = roomonly.Reference(b_cap, target="com.apple.Music").start()
a.thread.join(2)
b.thread.join(2)
check("one app's reference captures that app alone", a_cap.target == "us.zoom.xos" and b_cap.target == "com.apple.Music",
      (a_cap.target, b_cap.target))
mixed = roomonly.Mixed()
check("  none: ready, silent", mixed.ready(5.0, 5.0) and not mixed.window(0.0, 160).any())
a_cap.play(np.full(1600, 0.25), 10.0)
b_cap.play(np.full(800, 0.5), 10.05)
mixed.refs = {"us.zoom.xos": a, "com.apple.Music": b}
w = mixed.window(10.0, 1600)
check("  two: their sound added, each at its own time", abs(w[0] - 0.25) < 1e-9 and abs(w[1000] - 0.75) < 1e-9
      and abs(w[1599] - 0.75) < 1e-9, (w[0], w[1000]))
check("  ready only once each has come that far", mixed.ready(10.1, 10.1) and not mixed.ready(10.2, 10.2))
a.stop()
b.stop()

# room_only() itself: off until it is turned on, like voice mode's duplex was.
if sys.platform == "darwin":
    sb.ROOM_ONLY, env = None, os.environ.pop("STANDBY_NO_DEVICES", None)
    try:
        check("on the Mac, not turned on in config.json: off", sb.room_only() is None)
        sb.CONFIG.write_text(json.dumps({"room_only": True}))
        check("  turned on: it makes one (made only when dictation starts)", callable(sb.room_only()))
    finally:
        if env is not None:
            os.environ["STANDBY_NO_DEVICES"] = env

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
