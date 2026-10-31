"""The meeting-audio module's own side (mac/native/macos/appaudio.py): who is playing
and how sure we are it is a call, the interface the recorder depends on, and that a
refusal is reported rather than raised. A real tap needs a call (and on a new build,
macOS's prompt), so it is tried by hand on a test Mac; here the tap is a stand-in, and
everything around it (the feed, the refusal on its evidence, the watch) is tested."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, and no child may crash)
import os, re, sys, threading, time

MAC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac")
STANDBY = os.path.join(MAC, "standby.py")
sys.path.insert(0, MAC)
import numpy as np  # noqa: E402
from native.macos import appaudio as ma  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:400])


# ---------------------------------------------------------------- how sure it is a call
sure = ma._confidence
check("a call app with the microphone open is a call, beyond doubt", sure("us.zoom.xos", True, True) >= 0.95)
check("  playing with the mic shut (muted, or not joined yet) still counts",
      0.75 <= sure("us.zoom.xos", True, False) < 0.95, sure("us.zoom.xos", True, False))
check("something unknown playing WITH the mic open is probably a call",
      0.4 <= sure("com.example.newapp", True, True) < 0.75, sure("com.example.newapp", True, True))
check("  the same app merely playing is not (music, a video)", sure("com.example.newapp", True, False) < 0.4,
      sure("com.example.newapp", True, False))
check("silence is never a call", sure("us.zoom.xos", False, True) == 0.0 and sure("x", False, False) == 0.0)
check("the usual call apps are known", all(b in ma.CALL_APPS for b in (
    "us.zoom.xos", "com.microsoft.teams2", "com.tinyspeck.slackmacgap", "com.apple.FaceTime")))
check("  and no browser is one: a browser playing is far more often a video",
      not set(ma.BROWSERS) & set(ma.CALL_APPS) and "com.google.Chrome" in ma.BROWSERS)

# Against the recorder's own rule (mac/devices.py choose_meeting: whatever plays is recorded).
import devices  # noqa: E402


def decided(bundle, hears):
    app = {"bundle": bundle, "pid": 7, "playing": True, "hears": hears, "call": bundle in ma.CALL_APPS,
           "name": ma.CALL_APPS.get(bundle) or ma.BROWSERS.get(bundle) or bundle,
           "confidence": sure(bundle, True, hears)}
    got, why, ask = devices.choose_meeting([app], "auto", {}, None)
    return "recorded" if got else "asked" if ask else "ignored"


check("Chrome playing a video, mic shut (YouTube): recorded, nothing asked", decided("com.google.Chrome", False) == "recorded",
      sure("com.google.Chrome", True, False))
check("  Safari, Arc, Edge, Brave and Firefox the same",
      all(decided(b, False) == "recorded" for b in ma.BROWSERS))
check("Chrome playing with the mic open (Meet): recorded", decided("com.google.Chrome", True) == "recorded",
      sure("com.google.Chrome", True, True))
check("Slack's huddle (a call app, the mic open): recorded", decided("com.tinyspeck.slackmacgap", True) == "recorded")
check("Zoom, mic open or muted: recorded", decided("us.zoom.xos", True) == decided("us.zoom.xos", False) == "recorded")
check("the log names the browser, not its bundle id",
      devices.choose_meeting([{"bundle": "com.google.Chrome", "pid": 7, "playing": True, "hears": True,
                               "call": False, "name": ma.BROWSERS["com.google.Chrome"], "confidence": 0.7}],
                             "auto", {}, None)[1].startswith("Google Chrome is playing"))

# ---------------------------------------------------------------- who is playing (this Mac)
if sys.platform == "darwin":
    apps = ma.audio_apps()
    # A headless runner need not have any call/music apps open. App grouping and
    # filtering are exercised with explicit process fixtures below.
    check("Core Audio's app rows have the fields the recorder needs",
          isinstance(apps, list) and all(set(a) >= {"bundle", "pid", "playing", "hears", "call", "name", "confidence"}
                                for a in apps), apps[:1])
    check("  a process with no bundle id is kept, named by what it is",
          all(a["name"] for a in apps), [a for a in apps if not a["name"]][:2])
    # How fast that call is: tests/test_core_audio_speed.py, in the full run, alone.
    check("nothing here captures or asks for a permission", ma.audio_apps() is not None)
else:
    print("  (the Core Audio part runs on macOS only)")

# ---------------------------------------------------------------- the interface the recorder uses
for name in ("apps", "start", "stop"):
    check("MeetingAudio and its fake both have %s()" % name,
          callable(getattr(ma.MeetingAudio, name)) and callable(getattr(ma.FakeMeetingAudio, name)))
real, fake = ma.MeetingAudio(), ma.FakeMeetingAudio()
check("and both start not-ok, with nothing to report yet",
      (real.ok, real.error, fake.ok, fake.error) == (False, None, False, None))
check("  with the fields the recorder reads", all(hasattr(x, f) for x in (real, fake)
                                                  for f in ("ok", "error", "name", "note", "target", "state", "cause",
                                                            "reason", "detail", "past")))


# ---------------------------------------------------------------- what the user is told
check("every cause says paused (mends itself) or error (the user must act)",
      all(v[0] in ("paused", "error") for v in ma.CAUSES.values()), ma.CAUSES)
words = [ma.describe(c, "Microsoft Teams", "it said no.") for c in ma.CAUSES]
check("  each with a reason, a detail and a past form, filled in, nothing left in braces",
      all(w["reason"] and w["detail"] and w["past"] and "{" not in "".join(w.values()) for w in words), words)
check("  a reason short enough for a chip, even with a long app name",
      all(len(w["reason"]) <= 40 for w in words), [w["reason"] for w in words])
check("  the past form says what WAS, for the transcript after the pause",
      not any(re.search(r"\b(is|are)\b", w["past"]) for w in words), [w["past"] for w in words])
check("  all of it ASCII: it reaches logs, and launchd gives an app no locale",
      all(all(ord(ch) < 128 for ch in "".join(v)) for v in ma.CAUSES.values()))
check("an unknown cause still says something true", ma.describe("nonsense")["cause"] == "failed")
check("the stall rule is the recorder's own (STALL in mac/standby.py), so they never drift",
      re.search(r"^STALL = ([\d.]+)", open(STANDBY).read(), re.M).group(1) == repr(ma.STALL), ma.STALL)


class Refusing(ma.MeetingAudio):
    exc = ma.Stop("app_closed", "us.zoom.xos is not running")

    def _start(self):
        raise self.exc


r = Refusing()
r.start("us.zoom.xos")
check("a refusal is reported, never raised: the recorder reads .error and retries",
      r.ok is False and "not running" in r.error, (r.ok, r.error))
check("  the app gone: paused, by its name, not its bundle id",
      (r.state, r.cause, r.reason) == ("paused", "app_closed", "Zoom is not running"), vars(r))
r.stop()
check("  and stopping it keeps why, for the page to show", r.state == "paused" and r.reason)
Refusing.exc = RuntimeError("the tap was refused ('!obj')")
r.start("us.zoom.xos")
check("anything else: an error, in its own words", (r.state, r.cause) == ("error", "failed")
      and "'!obj'" in r.detail, (r.cause, r.detail))
check("the words for a refusal: calm, what for, where to allow it, and only the sound",
      ma.describe("permission", "Zoom")["reason"] == "allow Standby to hear it"
      and "System Audio Recording Only" in ma.describe("permission", "Zoom")["detail"]
      and "only the sound" in ma.describe("permission", "Zoom")["detail"])
check("  and no sleeping screen any more: a tap needs no display", "screen_asleep" not in ma.CAUSES)
plain = ma.MeetingAudio()
plain.start("us.zoom.xos")
check("a test never reaches a real tap (STANDBY_NO_DEVICES)", plain.ok is False and "STANDBY_NO_DEVICES" in plain.error,
      plain.error)
check("the floor is macOS 14.4, where a user can allow a tap", ma.FLOOR == (14, 4))

# ---------------------------------------------------------------- the tap's own callback, read with ctypes
import ctypes  # noqa: E402


def callback_input(samples, channels=1, host=0):
    data = np.asarray(samples, dtype=np.float32)
    raw = ctypes.create_string_buffer(data.tobytes(), data.nbytes)
    bl = ma._BufferList(1, (ma._Buffer * 1)(ma._Buffer(channels, data.nbytes, ctypes.cast(raw, ctypes.c_void_p))))
    ts = ma._TimeStamp(0.0, host, 1.0, 0, (ctypes.c_byte * 24)(), 2, 0)
    return raw, bl, ts


want = np.linspace(-0.5, 0.5, 480).astype(np.float32)
keep = callback_input(want, host=int(1234.5 / ma._TICK))
x, at = ma._read(ctypes.addressof(keep[1]), ctypes.addressof(keep[2]))
check("a callback's buffer: its samples copied, and the host time of the first, in seconds",
      np.array_equal(x, want) and abs(at - 1234.5) < 1e-6, (x[:3], at))
stereo = np.array([0.2, 0.4, -0.2, -0.4], dtype=np.float32)
keep = callback_input(stereo, channels=2)
x, at = ma._read(ctypes.addressof(keep[1]), 0)
check("  two channels: mixed to one; no time: now", np.allclose(x, [0.3, -0.3]) and abs(at - ma._host_now()) < 1,
      (x, at))
check("  nothing in it: nothing", ma._read(0, 0) is None)
check("the host clock is the monotonic one (the recorder places blocks by it)",
      abs(ma._host_now() - time.monotonic()) < 0.05, (ma._host_now(), time.monotonic()))

# ---------------------------------------------------------------- the capture around a stand-in tap
from native import app as native_app  # noqa: E402


class FakeTap(object):
    made = []

    def __init__(self, objects, exclude, block):
        self.objects, self.exclude, self.block, self.closed = list(objects), exclude, block, False
        self.rate, self.output = 48000.0, OUTPUT[0]
        FakeTap.made.append(self)

    def close(self):
        self.closed = True


OUTPUT = ["BuiltInSpeakerDevice"]
PROCS = [(101, 500, "us.zoom.xos", True), (102, 501, "com.apple.Music", True), (103, os.getpid(), "", False)]
real = (ma._Tap, ma.processes, ma._output_uid, ma.heard_before, ma._owner)
ma._Tap, ma.processes, ma._output_uid = FakeTap, lambda: list(PROCS), lambda: OUTPUT[0]
HEARD = [False]
ma.heard_before = lambda: HEARD[0]
ma._owner = lambda pid, bundle: None


class Tapping(ma.MeetingAudio):
    def _start(self):                       # as the real one, less the test guard and the OS check
        self.name = ""
        objects, exclude = self._objects()
        self._tap_now(objects, exclude)


def until(cond, secs=3.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


try:
    fed = []
    c = Tapping()
    c.start("us.zoom.xos", lambda block, t: fed.append((block.copy(), t)))
    tap = FakeTap.made[-1]
    check("Zoom's own processes tapped, mixed to one, nothing left out",
          (tap.objects, tap.exclude, c.name, c.ok, c.state) == ([101], False, "Zoom", True, "recording"),
          (tap.objects, tap.exclude, c.name, c.state))
    host = ma._host_now()
    tap.block(np.full(480, 0.25, np.float32), host, 48000.0)
    check("a block from the tap: resampled to 16 kHz and fed on, at its own time",
          until(lambda: fed) and len(fed[0][0]) in (159, 160) and abs(fed[0][1] - host) < 0.05,
          [(len(b), t - host) for b, t in fed])
    check("  a sound heard: this build is noted as allowed",
          until(lambda: (native_app.grants().get(ma.grant("sound")) or {}).get("build", 0) == native_app.build_mark()),
          native_app.grants())
    c.stop()
    check("  stopped: its tap closed", tap.closed and not c.ok)

    # macOS refused: a tap starts all the same and hands over silence
    zeros = []
    c = Tapping()
    c.start("us.zoom.xos", lambda block, t: zeros.append(block))
    tap = FakeTap.made[-1]
    tap.block(np.zeros(480, np.float32), ma._host_now(), 48000.0)
    until(lambda: zeros)
    c._watch()
    check("silence just after the start: nothing said yet", c.state == "recording", c.state)
    c.opened, c._looked = time.monotonic() - ma.REFUSED_AFTER - 0.1, 0.0
    c._watch()
    check("Zoom playing, only silence for REFUSED_AFTER s on a build never heard: not allowed, said so",
          (c.state, c.cause, c.ok) == ("error", "permission", False) and "System Audio Recording" in c.error, vars(c))
    check("  and noted, so the setup window and the band say it too",
          native_app.grants().get(ma.grant("sound_refused")) is not None)
    before = len(FakeTap.made)
    c._looked, c._retap_at = 0.0, 0.0
    c._watch()
    check("  while refused, the tap is made again (a grant in System Settings then takes, nothing asked)",
          len(FakeTap.made) == before + 1 and tap.closed, len(FakeTap.made) - before)
    FakeTap.made[-1].block(np.full(480, 0.1, np.float32), ma._host_now(), 48000.0)
    check("  sound at last: recording again", until(lambda: c.state == "recording" and c.ok), vars(c))
    c.stop()

    c = Tapping()
    c.start("us.zoom.xos", lambda block, t: None)
    c.opened, c._looked = time.monotonic() - ma.REFUSED_AFTER - 0.1, 0.0
    HEARD[0] = True
    c._watch()
    check("silence on a build that has heard before is only silence", c.state == "recording", c.state)
    HEARD[0] = False
    PROCS[0] = (101, 500, "us.zoom.xos", False)
    c._looked = 0.0
    c._watch()
    check("  nor while Zoom plays nothing (its output not running)", c.state == "recording", c.state)
    PROCS[0] = (101, 500, "us.zoom.xos", True)
    c.opened = time.monotonic()

    before = len(FakeTap.made)
    PROCS.append((104, 502, "us.zoom.xos.helper", True))
    c._looked = 0.0
    c._watch()
    check("a helper of Zoom's starts playing: tapped too, the tap made again",
          len(FakeTap.made) == before + 1 and FakeTap.made[-1].objects == [101, 104], FakeTap.made[-1].objects)
    PROCS.pop()
    OUTPUT[0] = "AirPodsPro"
    c._looked = 0.0
    c._watch()
    check("  the output device changes (AirPods): the tap made again on it",
          len(FakeTap.made) == before + 2 and FakeTap.made[-1].output == "AirPodsPro", len(FakeTap.made) - before)
    c._looked = 0.0
    c._watch()
    check("  and nothing changed: nothing done", len(FakeTap.made) == before + 2)
    c.stop()

    class StoppedMeanwhile(FakeTap):
        def __init__(self, *args):
            FakeTap.__init__(self, *args)
            c.stop()                      # stopped while the tap was being made, from another thread
    ma._Tap = StoppedMeanwhile
    c._tap_now((101,), False)
    ma._Tap = FakeTap
    check("a tap made after a stop is closed at once", FakeTap.made[-1].closed and c._tap is None)

    e = Tapping()
    e.start(ma.EVERYTHING, lambda block, t: None)
    check("everything but Standby: a global tap, Standby's own left out",
          (FakeTap.made[-1].exclude, FakeTap.made[-1].objects, e.name) == (True, [103], "the Mac"),
          (FakeTap.made[-1].exclude, FakeTap.made[-1].objects))
    e.stop()
    PROCS[:] = [p for p in PROCS if p[2] != "us.zoom.xos"]
    gone = Tapping()
    gone.start("us.zoom.xos", lambda block, t: None)
    check("the app not running: paused, nothing tapped", (gone.state, gone.cause) == ("paused", "app_closed"))
finally:
    ma._Tap, ma.processes, ma._output_uid, ma.heard_before, ma._owner = real

# ---------------------------------------------------------------- one row per app, its helpers in it
# As Core Audio listed Chrome playing YouTube: three Chrome processes, four helpers; the sound
# comes from a helper (its audio service).
CHROME = [{"bundle": "com.google.Chrome", "pid": p, "playing": False, "hears": False} for p in (80909, 80752, 70480)]
HELPERS = [{"bundle": "com.google.Chrome.helper", "pid": p, "playing": p == 80983, "hears": False}
           for p in (80983, 80989, 33385, 33394)]
got = ma.by_app(CHROME + HELPERS + [{"bundle": "us.zoom.xos", "pid": 11, "playing": False, "hears": True},
                                    {"bundle": "", "pid": 401, "playing": True, "hears": False}])
check("Chrome playing through its helper: one row, Chrome's, playing, with Chrome's own pid",
      [(r["bundle"], r["pid"], r["playing"]) for r in got if "Chrome" in r["bundle"]] == [("com.google.Chrome", 80909, True)], got)
check("  the other apps as they are, a process with no bundle id by its pid",
      [(r["bundle"], r["pid"], r["playing"], r["hears"]) for r in got if "Chrome" not in r["bundle"]]
      == [("us.zoom.xos", 11, False, True), ("", 401, True, False)], got)
got = ma.by_app(list(reversed(HELPERS)) + list(reversed(CHROME)))
check("  in any order: still Chrome's pid, not a helper's", [(r["bundle"], r["pid"], r["playing"]) for r in got]
      == [("com.google.Chrome", 70480, True)], got)
got = ma.by_app([{"bundle": "com.example.App.helper.Renderer", "pid": 5, "playing": True, "hears": False}])
check("  a helper whose app isn't listed: counted as that app", [r["bundle"] for r in got] == ["com.example.App"], got)
got = ma.by_app([dict(c, playing=True) for c in CHROME])
check("  two Chrome processes playing: still one Chrome (it never joins twice)", len(got) == 1 and got[0]["playing"], got)
from AppKit import NSRunningApplication  # noqa: E402
listed = [a for a in ma.audio_apps() if not a["own"]]
check("only apps the user knows: each listed is an app in the Dock, never a system process",
      all(NSRunningApplication.runningApplicationWithProcessIdentifier_(a["pid"]) is not None
          and NSRunningApplication.runningApplicationWithProcessIdentifier_(a["pid"]).activationPolicy() == 0
          for a in listed) and not any("systemsoundserverd" in a["bundle"] for a in listed), listed)
check("  launchd, which no app is responsible for, is no app", ma._owner(1, "") is None)
check("the apps on this Mac, one row each", len(set(a["bundle"] for a in ma.audio_apps() if a["bundle"]))
      == len([a for a in ma.audio_apps() if a["bundle"]]))

# ---------------------------------------------------------------- the fake, which both sides test against
f = ma.FakeMeetingAudio()
f.apps_now = [{"bundle": "us.zoom.xos", "name": "Zoom", "pid": 11, "playing": True, "call": True,
               "confidence": 0.95, "hears": True}]
check("the fake reports apps as copies, so a test cannot corrupt it",
      f.apps()[0]["bundle"] == "us.zoom.xos" and f.apps()[0] is not f.apps_now[0])
got = []
f.start("us.zoom.xos", feed=lambda b, t: got.append((t, b)))
deadline = time.monotonic() + 5
while len(got) < 4 and time.monotonic() < deadline:
    time.sleep(0.01)
f.stop()
check("it feeds mono float32 blocks and names the app", len(got) > 3 and got[0][1].dtype == np.float32
      and len(got[0][1]) == 320 and f.name == "Zoom", (len(got), f.name))
gaps = [got[i + 1][0] - (got[i][0] + len(got[i][1]) / 16000.0) for i in range(len(got) - 1)]
check("  with times that follow the audio, not the clock it ran on", max(abs(g) for g in gaps) < 1e-9, max(gaps))
check("  and it stops when told", not f.ok and len(got) == len(got))
f2 = ma.FakeMeetingAudio()
f2.fail_with = "Standby is not allowed to record this Mac's audio"
f2.start("us.zoom.xos", feed=lambda b, t: None)
check("the fake can refuse, like the real one", f2.ok is False and f2.error == f2.fail_with
      and f2.state == "error")
f2.fail_with, f2.fail_cause = None, "permission"
f2.start("us.zoom.xos", feed=lambda b, t: None)
check("  by cause, with the same words", (f2.state, f2.cause, f2.reason) == ("error", "permission",
                                                                             ma.describe("permission")["reason"]))
f4 = ma.FakeMeetingAudio()
f4.apps_now = f.apps_now
fed = []
f4.start("us.zoom.xos", feed=lambda b, t: fed.append(b))
time.sleep(0.1)
f4.lose("app_closed")
n = len(fed)
time.sleep(0.1)
check("and it can stop mid-call as the real one does: the app closed",
      len(fed) == n and not f4.ok and (f4.state, f4.reason) == ("paused", "Zoom is not running"), vars(f4))
f3 = ma.FakeMeetingAudio()
sent = []
f3.start("us.zoom.xos", feed=lambda b, t: sent.append(b))
time.sleep(0.1)
f3.stalled = True
n = len(sent)
time.sleep(0.15)
check("and it can go quiet without a word, which is what a dead stream looks like",
      len(sent) == n and f3.ok, (n, len(sent)))
f3.stop()

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
