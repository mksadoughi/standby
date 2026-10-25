"""The meeting-audio module's own side (mac/native/macos/appaudio.py): who is playing
and how sure we are it is a call, the interface the recorder depends on, and that a
refusal is reported rather than raised. The macOS capture itself needs a screen and
a call, so it is exercised by tools/meeting_audio_probe.py by hand, not here; what
is testable without any of that is tested."""
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
    exc = ma.Stop("screen_asleep", "the screen is asleep or locked, so macOS offers nothing to capture")

    def _start(self):
        raise self.exc


r = Refusing()
r.start("us.zoom.xos")
check("a refusal is reported, never raised: the recorder reads .error and retries",
      r.ok is False and "asleep or locked" in r.error, (r.ok, r.error))
check("  a sleeping screen: paused, in words for the chip, the tooltip and the transcript",
      (r.state, r.cause, r.reason, r.past) == ("paused", "screen_asleep", "the screen is asleep",
                                              "the screen was asleep") and "wakes" in r.detail, vars(r))
r.stop()
check("  and stopping it keeps why, for the page to show", r.state == "paused" and r.reason)
Refusing.exc = ma.Stop("app_closed", "us.zoom.xos is not running")
r.start("us.zoom.xos")
check("the app gone: paused, by its name, not its bundle id",
      r.state == "paused" and r.reason == "Zoom is not running", r.reason)
declined = RuntimeError("The user declined TCCs for application, window, display capture")
declined.code = -3801
Refusing.exc = declined
r.start("us.zoom.xos")
check("ScreenCaptureKit's own refusal code: the permission, an error with a way out",
      (r.state, r.cause) == ("error", "permission") and "System Settings" in r.detail, (r.state, r.cause))
class Counting(Refusing):
    tries = 0

    def _start(self):
        Counting.tries += 1
        raise self.exc


c = Counting()
c.exc = declined
real_preflight = ma.preflight
del ma._REFUSED[:]                       # the refusals above were other checks'
try:
    ma.preflight = lambda: True          # never this Mac's own Screen Recording setting: the test's answers only
    c.start("us.zoom.xos")
    ma.preflight = lambda: False
    c.start("us.zoom.xos")
    c.start("us.zoom.xos")
    check("refused once: ScreenCaptureKit is not asked again (each ask is macOS's prompt)",
          Counting.tries == 1 and (c.state, c.cause) == ("error", "permission"), (Counting.tries, c.state, c.cause))
    ma.preflight = lambda: None
    c.start("us.zoom.xos")
    check("  nor when the preflight cannot tell", Counting.tries == 1, Counting.tries)
    ma.preflight = lambda: True
    c.exc = ma.Stop("app_closed", "us.zoom.xos is not running")
    c.start("us.zoom.xos")
    check("  allowed (the preflight, which asks nothing, says so): asked again", Counting.tries == 2, Counting.tries)
finally:
    ma.preflight = real_preflight
    del ma._REFUSED[:]
# One pyobjc's Quartz lacked CGPreflightScreenCaptureAccess: its lazy lookup raised KeyError, and
# the preflight with it (ui.log). The framework's own function is used then (no prompt).
if sys.platform == "darwin":
    from native.macos import permissions as mac_permissions

    class NoPreflight(object):
        def __getattr__(self, name):
            raise KeyError(name)
    saved = sys.modules.get("Quartz")
    sys.modules["Quartz"] = NoPreflight()
    try:
        got = mac_permissions.screen_allowed()
        check("the preflight when Quartz lacks it: CoreGraphics's own, a yes or no, no KeyError",
              isinstance(got, bool), got)
    except KeyError as exc:
        check("the preflight when Quartz lacks it: CoreGraphics's own, a yes or no, no KeyError", False, repr(exc))
    finally:
        if saved is None:
            sys.modules.pop("Quartz", None)
        else:
            sys.modules["Quartz"] = saved
check("the words: calm, what for, what to do, and that no screen is recorded",
      ma.describe("permission", "Zoom")["reason"] == "allow Standby to hear it"
      and "Screen & System Audio Recording" in ma.describe("permission", "Zoom")["detail"]
      and "No screen is recorded" in ma.describe("permission", "Zoom")["detail"])
other = RuntimeError("something else")
other.code = -3805
Refusing.exc = other
r.start("us.zoom.xos")
check("  any other code is not blamed on the permission", (r.state, r.cause) == ("error", "failed")
      and "something else" in r.detail, (r.cause, r.detail))
Refusing.exc = ma.Stop("stalled", "ScreenCaptureKit did not answer")
r.start("us.zoom.xos")
check("  no answer at all: paused and tried again, not an error", (r.state, r.cause) == ("paused", "stalled"))


class Working(ma.MeetingAudio):
    def _start(self):
        self.name = "Zoom"


w = Working()
w.start("us.zoom.xos")
check("a start that works says so", w.ok and w.error is None and w.name == "Zoom")
check("  recording, with nothing to explain", (w.state, w.cause, w.reason, w.detail) == ("recording", None, "", ""))
w.complain("Failed to find any displays or windows to capture")
check("a complaint just after starting, before any audio is due, only notes it",
      w.ok and w.note and w.state == "recording", (w.ok, w.state))
w.last_frame = time.monotonic() - ma.STALL - 0.1
w.complain("Failed to find any displays or windows to capture")
check("  silent for STALL s: not stopped at once (a busy Mac holds callbacks up)", w.ok and w.state == "recording")
w.confirming.join(10)
check("  still silent CONFIRM s later: stopped, paused, in macOS's words",
      w.ok is False and (w.state, w.cause) == ("paused", "stopped") and "displays" in w.detail, (w.state, w.detail))
w2 = Working()
w2.start("us.zoom.xos")
w2.last_frame = time.monotonic()
w2.complain("Failed to find any displays or windows to capture")
check("the same complaint while audio still arrives only notes it, and carries on",
      w2.ok and w2.note and w2.error is None, (w2.ok, w2.note, w2.error))
w2.last_frame = time.monotonic() - ma.STALL - 0.1
w2.complain("Failed to find any displays or windows to capture")
w2.last_frame = time.monotonic()                   # the late audio arrives after all
w2.confirming.join(10)
check("  audio that was only late: carries on", w2.ok and w2.state == "recording", (w2.ok, w2.state))
w2.last_frame = time.monotonic() - ma.STALL - 0.1
w2.complain("Failed to find any displays or windows to capture")
late = w2.confirming
w2.start("us.zoom.xos")
w2.last_frame = time.monotonic() - ma.STALL - 0.1
late.join(10)
check("  a complaint about an earlier start never stops a newer one", w2.ok and w2.state == "recording")
w2.stop()
check("a capture stopped while fine has nothing to say", w2.state is None and not w2.ok)

# ---------------------------------------------------------------- a buffer read with no pyobjc object made
# The recorder crashed (SIGSEGV, objc_msgSend under pyobjc's id_to_python) when a CoreMedia call
# in the capture's callback returned an object for pyobjc to convert, on ScreenCaptureKit's
# thread. The callback reads a buffer by its address alone. Here a real CoreMedia sample buffer,
# made through ctypes as ScreenCaptureKit hands them on (mono float32, 16 kHz), goes through it.
import ctypes  # noqa: E402
import inspect  # noqa: E402


def sample_buffer(samples, pts_seconds):
    asbd = ma._ASBD(16000.0, int.from_bytes(b"lpcm", "big"), 9, 4, 1, 4, 1, 32, 0)   # float, packed
    fmt, block, sbuf = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    ma.cm.CMAudioFormatDescriptionCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ma._ASBD), ctypes.c_size_t,
                                                     ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                                     ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    ma.cm.CMBlockBufferCreateWithMemoryBlock.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                                         ctypes.c_size_t, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    ma.cm.CMBlockBufferReplaceDataBytes.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
    ma.cm.CMAudioSampleBufferCreateReadyWithPacketDescriptions.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ma._CMTime, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    data = np.asarray(samples, dtype=np.float32).tobytes()
    assert not ma.cm.CMAudioFormatDescriptionCreate(None, ctypes.byref(asbd), 0, None, 0, None, None, ctypes.byref(fmt))
    assert not ma.cm.CMBlockBufferCreateWithMemoryBlock(None, None, len(data), None, None, 0, len(data), 1,
                                                        ctypes.byref(block))
    assert not ma.cm.CMBlockBufferReplaceDataBytes(data, block, 0, len(data))
    assert not ma.cm.CMAudioSampleBufferCreateReadyWithPacketDescriptions(
        None, block, fmt, len(samples), ma._CMTime(int(pts_seconds * 1e9), 1000000000, 1, 0), None, ctypes.byref(sbuf))
    ma.cf.CFRelease(fmt)
    ma.cf.CFRelease(block)
    return sbuf.value


cap = ma.MeetingAudio()
heard = []
cap.feed = lambda block, t: heard.append((block.copy(), t))
want = np.linspace(-0.5, 0.5, 320).astype(np.float32)
addr = sample_buffer(want, 1234.5)
cap.audio(addr)
# Through the callback itself, called as ScreenCaptureKit calls it (objc_msgSend on the stream
# output, from C): pyobjc hands it an objc.PyObjCPointer, which pyobjc_id refuses (every frame
# lost, 2026-10-23: "not an Objective-C object").
# The stream output's own selector and signature (its class needs ScreenCaptureKit's protocol,
# which a test never loads): what pyobjc hands the callback depends on the signature alone.
import objc  # noqa: E402
from AppKit import NSObject  # noqa: E402


class TestOutput(NSObject):
    stream_didOutputSampleBuffer_ofType_ = objc.selector(lambda self, stream, sbuf, kind: self.owner.audio(sbuf),
                                                         selector=ma._AUDIO_SELECTOR, signature=ma._AUDIO_SIGNATURE)


out = TestOutput.alloc().init()
out.owner = cap
libobjc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
libobjc.sel_registerName.restype = ctypes.c_void_p
send = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(
    ("objc_msgSend", libobjc))
capture_failures = []
cap.fail = lambda why, cause="failed": capture_failures.append(why)
send(objc.pyobjc_id(out), libobjc.sel_registerName(ma._AUDIO_SELECTOR), None, addr, 1)
ma.cf.CFRelease(addr)
check("a real sample buffer, read by its address, and through the callback as ScreenCaptureKit calls it",
      not capture_failures and len(heard) == 2 and np.array_equal(heard[1][0], want) and np.array_equal(heard[0][0], want) and abs(heard[0][1] - 1234.5) < 1e-6, heard[:1])
check("  and its format, as CoreMedia states it", cap.format == "16000 Hz, 1 channel(s), 32 bits", cap.format)
cap.audio(0)
check("  no buffer: nothing, no crash", len(heard) == 2)
said = []
cap.log, cap._described = said.append, False          # as after a start: its first buffer, described once
addr = sample_buffer(want, 1300.0)
cap.audio(addr)
cap.audio(addr)
ma.cf.CFRelease(addr)
check("  the first buffer after a start, in the log once: samples, bytes, format, peak",
      said == ["meeting audio: %s's first buffer: 320 samples, 1280 bytes, 16000 Hz, 1 channel(s), 32 bits, peak 0.5000"
               % cap.name], said)

# Chrome's sound is its audio service's, a helper ScreenCaptureKit doesn't list (measured,
# macOS 14.7: it lists two Chrome pids; the sound is pid 33385's). A filter of Chrome alone
# heard only zeros, so then the capture is everything but the other apps.
check("an app's helpers are the app's", ma.is_of("com.google.Chrome", "com.google.Chrome.helper")
      and ma.is_of("com.google.Chrome", "com.google.Chrome") and not ma.is_of("com.google.Chrome", "com.google.ChromeX")
      and not ma.is_of("com.google.Chrome", None))
check("  its sound from a process not listed: that process, so the capture is everything but the other apps",
      ma.unlisted_sound([70480, 80752], [80909, 70480, 80752, 33385]) == [33385, 80909])
check("  all of it listed (Zoom plays itself): nothing, so the capture is the app alone",
      ma.unlisted_sound([11, 12], [11]) == [])
reads = inspect.getsource(ma.MeetingAudio.audio) + inspect.getsource(ma.MeetingAudio._when) \
    + inspect.getsource(ma.MeetingAudio._describe)
check("  nothing in the callback goes through pyobjc's CoreMedia (an object made there is what crashed)",
      "CoreMedia" not in reads and "CM." not in reads)

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
f4.lose("screen_asleep")
n = len(fed)
time.sleep(0.1)
check("and it can stop mid-call as the real one does: the screen goes to sleep",
      len(fed) == n and not f4.ok and (f4.state, f4.reason) == ("paused", "the screen is asleep"), vars(f4))
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
