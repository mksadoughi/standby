"""native/appaudio.py on a Mac (ScreenCaptureKit, and Core Audio's list of processes).

The other side of a call: the meeting's own audio, so Standby hears everyone.

On a call with headphones the microphone hears only the user, so the people on the
other side never reach the transcript. This captures the call app's own output
through ScreenCaptureKit, as the recorder's "Others" channel, with no setup from
the user beyond allowing it once.

What the recorder uses:

    audio = MeetingAudio()
    audio.apps()                    who is playing, whether it is a call, how sure
    audio.start(bundle_or_pid, feed)  feed(block, t): mono float32 at 16 kHz, and the
                                      monotonic time of the block's first sample
    audio.stop()
    audio.ok, audio.error, audio.name, audio.note
    audio.state, .cause, .reason, .detail, .past     what the user is told (CAUSES)

start() never raises: a refusal is .ok False, .error what went wrong (for the log),
and .state "paused" (it mends itself: keep trying) or "error" (the user has to act),
with the words for the page and the session's events from one table, CAUSES.
Measured on an M4 Pro, macOS 26.6: first audio 0.2 s after start, 320-sample blocks
about 49 a second, exactly 16,000 samples a second with blocks contiguous to 42
microseconds, 1-2% of one core, and continuous blocks (zeros) through silence.

Two things to know:
* A sleeping or locked screen stops this. ScreenCaptureKit's audio hangs off a
  display and macOS offers none then, so the capture fails with a plain reason and
  the recorder shows the channel as paused until the screen wakes. Core Audio
  process taps need no display and are the way round it.
* Standby's own audio is excluded, so Claude's spoken answers are never captured back.

Where pyobjc stops and ctypes starts, learned the hard way here (assume it is the
rule, not the exception): an "out" C array is unreadable through pyobjc (the audio
process list raises, a block buffer's data comes back as a tuple of garbage); a C
struct can arrive as a plain tuple (the stream format does); a callback takes
exactly the arguments ObjC declares, and a Python exception on ScreenCaptureKit's
thread aborts the process rather than raising, so every callback here catches
everything.
"""
import ctypes
import ctypes.util
import os
import threading
import time

import numpy as np

from native import app

# pyobjc warns once about the sample-buffer pointer it hands us; it is expected.
import warnings
warnings.filterwarnings("ignore", message=".*PyObjCPointer created.*")

# ---- which apps are playing audio (read-only) ----------------------------------
#
# Core Audio through ctypes: pyobjc's bindings cannot read these array properties
# ("converting to a C array"), and the product would hit the same wall.

ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
cm = ctypes.CDLL(ctypes.util.find_library("CoreMedia"))
cm.CMBlockBufferGetDataPointer.restype = ctypes.c_int32
cm.CMBlockBufferGetDataPointer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
                                           ctypes.POINTER(ctypes.c_size_t),
                                           ctypes.POINTER(ctypes.POINTER(ctypes.c_char))]


def _address(sbuf):
    """A sample buffer's address: the callback gets an objc.PyObjCPointer (measured: its
    declared type, ^{opaqueCMSampleBuffer=}, is opaque to pyobjc), which pyobjc_id refuses
    ("not an Objective-C object"); a proxy, or the number itself, also do."""
    if sbuf is None or isinstance(sbuf, int):
        return sbuf or 0
    addr = getattr(sbuf, "pointerAsInteger", None)
    if addr is not None:
        return addr
    import objc
    return objc.pyobjc_id(sbuf)


class _CMTime(ctypes.Structure):
    _fields_ = [("value", ctypes.c_int64), ("timescale", ctypes.c_int32), ("flags", ctypes.c_uint32),
                ("epoch", ctypes.c_int64)]


class _ASBD(ctypes.Structure):
    _fields_ = [("mSampleRate", ctypes.c_double), ("mFormatID", ctypes.c_uint32), ("mFormatFlags", ctypes.c_uint32),
                ("mBytesPerPacket", ctypes.c_uint32), ("mFramesPerPacket", ctypes.c_uint32),
                ("mBytesPerFrame", ctypes.c_uint32), ("mChannelsPerFrame", ctypes.c_uint32),
                ("mBitsPerChannel", ctypes.c_uint32), ("mReserved", ctypes.c_uint32)]


# A sample buffer is read by its address alone, never through pyobjc: on ScreenCaptureKit's
# thread, pyobjc turning a returned CoreMedia object into a Python one crashed the recorder
# (2026-10-23, macOS 14.7.1, Chrome: SIGSEGV in objc_msgSend under id_to_python, from a
# CoreMedia call in this callback). Plain pointers and structs here make no objects at all.
cm.CMSampleBufferGetDataBuffer.restype = ctypes.c_void_p
cm.CMSampleBufferGetDataBuffer.argtypes = [ctypes.c_void_p]
cm.CMSampleBufferGetPresentationTimeStamp.restype = _CMTime
cm.CMSampleBufferGetPresentationTimeStamp.argtypes = [ctypes.c_void_p]
cm.CMSampleBufferGetFormatDescription.restype = ctypes.c_void_p
cm.CMSampleBufferGetFormatDescription.argtypes = [ctypes.c_void_p]
cm.CMAudioFormatDescriptionGetStreamBasicDescription.restype = ctypes.POINTER(_ASBD)
cm.CMAudioFormatDescriptionGetStreamBasicDescription.argtypes = [ctypes.c_void_p]
SYSTEM_OBJECT = 1


def _fourcc(s):
    return int.from_bytes(s.encode(), "big")


GLOBAL = _fourcc("glob")
# The four-character codes of the properties used, from CoreAudio's AudioHardware.h
# (kAudioHardwarePropertyProcessObjectList, kAudioProcessPropertyIsRunningOutput and
# so on). test_meeting_audio checks them against pyobjc's own constants, because a
# wrong code reads nothing and silently looks like silence: that cost me an hour.
PROCESS_LIST = _fourcc("prs#")
IS_RUNNING_OUTPUT = _fourcc("piro")
IS_RUNNING_INPUT = _fourcc("piri")
IS_RUNNING = _fourcc("pir?")
BUNDLE_ID = _fourcc("pbid")
PROCESS_PID = _fourcc("ppid")
# Apps whose audio is a meeting. Anything else playing is the user's business.
CALL_APPS = {"us.zoom.xos": "Zoom", "com.microsoft.teams2": "Microsoft Teams",
             "com.microsoft.teams": "Microsoft Teams", "com.tinyspeck.slackmacgap": "Slack",
             "com.webex.meetingmanager": "Webex", "com.cisco.webexmeetingsapp": "Webex",
             "com.apple.FaceTime": "FaceTime", "com.hnc.Discord": "Discord",
             "com.ringcentral.glip": "RingCentral", "com.bluejeans.mac": "BlueJeans"}
# Browsers are not call apps: one playing is far more often a video or music than a
# call. A call in one (Meet, or Teams or Zoom on the web) has the microphone open, so
# only then is it scored like an unknown app in a conversation: asked about, never
# recorded unasked. Named here only so the question says "Google Chrome".
BROWSERS = {"com.google.Chrome": "Google Chrome", "com.apple.Safari": "Safari",
            "company.thebrowser.Browser": "Arc", "com.microsoft.edgemac": "Microsoft Edge",
            "com.brave.Browser": "Brave", "org.mozilla.firefox": "Firefox"}


class _Addr(ctypes.Structure):
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]


ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32,
                                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Addr), ctypes.c_uint32, ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFRelease.argtypes = [ctypes.c_void_p]


def _get(obj, selector, ctype=None, count=1):
    """One property, or None. count > 1 returns a list."""
    size = ctypes.c_uint32(0)
    a = _Addr(selector, GLOBAL, 0)
    if ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(a), 0, None, ctypes.byref(size)):
        return None
    buf = (ctypes.c_byte * size.value)()
    n = ctypes.c_uint32(size.value)
    if ca.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(n), buf):
        return None
    if ctype is None:
        return buf, n.value
    items = ctypes.cast(buf, ctypes.POINTER(ctype))
    if count == 1:
        return items[0]
    return [items[i] for i in range(n.value // ctypes.sizeof(ctype))]


def _get_string(obj, selector):
    ref = _get(obj, selector, ctypes.c_void_p)
    if not ref:
        return None
    out = ctypes.create_string_buffer(512)
    ok = cf.CFStringGetCString(ref, out, 512, 0x08000100)      # UTF-8
    cf.CFRelease(ref)
    return out.value.decode() if ok else None


def audio_apps(include_silent=True):
    """Every process Core Audio knows: what it is, whether it is playing output right
    now, whether it also has the microphone open, and how sure we are that it is a
    meeting. Read-only, no permission. About 6 ms once warm; the first call costs
    ~60 ms, so a caller that polls should make one call early.

    Only apps the user knows: each process counts as the app macOS holds responsible
    for it (Chrome's audio service is Chrome's; Safari's WebKit process, Safari's; a
    program run in Terminal, Terminal's), and one no app is responsible for, a system
    process (systemsoundserverd's alert sounds), is not listed. "own": one of Standby's
    own (its apps, or a process of this checkout's, as Jean's voice: it shows as
    Python), which is never a meeting."""
    rows = []
    for obj in _get(SYSTEM_OBJECT, PROCESS_LIST, ctypes.c_uint32, count=0) or []:
        row = {"bundle": _get_string(obj, BUNDLE_ID) or "", "pid": _get(obj, PROCESS_PID, ctypes.c_int32),
               "playing": bool(_get(obj, IS_RUNNING_OUTPUT, ctypes.c_uint32)),
               "hears": bool(_get(obj, IS_RUNNING_INPUT, ctypes.c_uint32))}
        if not is_standby(row["pid"], row["bundle"]):      # Standby's own stay themselves, and are never a meeting
            owner = _owner(row["pid"], row["bundle"])
            if owner is None:
                continue
            row["bundle"], row["pid"] = owner
        rows.append(row)
    out = []
    for r in by_app(rows):
        bundle, pid, playing, hears = r["bundle"], r["pid"], r["playing"], r["hears"]
        if not include_silent and not playing:
            continue
        out.append({"bundle": bundle, "pid": pid, "playing": playing, "hears": hears,
                    "call": bundle in CALL_APPS, "name": app_name(pid, bundle),
                    "own": is_standby(pid, bundle),
                    "confidence": _confidence(bundle, playing, hears)})
    return out


def is_of(app, bundle):
    """This bundle id is the app's, or one of its helpers' ("<app>.helper")."""
    bundle = bundle or ""
    return bundle == app or bundle.startswith(app + ".")


def unlisted_sound(listed_pids, sounding):
    """The processes whose sound is an app's (sounding) that ScreenCaptureKit doesn't list:
    a filter of the app alone wouldn't hear them."""
    return sorted(set(sounding) - set(listed_pids))


def sound_pids(bundle):
    """The pids of every process whose sound is this app's (_owner), as Core Audio lists them."""
    out = []
    for obj in _get(SYSTEM_OBJECT, PROCESS_LIST, ctypes.c_uint32, count=0) or []:
        pid = _get(obj, PROCESS_PID, ctypes.c_int32)
        own = _get_string(obj, BUNDLE_ID) or ""
        owner = _owner(pid, own)
        if pid and (own == bundle or own.startswith(bundle + ".") or (owner and owner[0] == bundle)):
            out.append(int(pid))
    return out


def by_app(rows):
    """Core Audio's processes ({bundle, pid, playing, hears}), one row per app: an app is
    several processes, and the one making the sound is often a helper. Chrome plays
    through its audio service, com.google.Chrome.helper (measured: 3 Chrome pids and
    4 helpers for one Chrome). A helper counts as its app (the bundle it extends,
    "<app>.helper..." when that app isn't listed), and an app is playing or hearing when
    any of its processes is; its pid is the app's own, playing first. A process with no
    bundle id stays on its own, told apart by its pid."""
    bundles = set(r["bundle"] for r in rows if r["bundle"])

    def app_of(bundle):
        parents = [b for b in bundles if bundle.startswith(b + ".")]
        if parents:
            return max(parents, key=len)
        if ".helper" in bundle.lower():
            return bundle[:bundle.lower().index(".helper")]
        return bundle

    out, index = [], {}
    for r in rows:
        if not r["bundle"]:
            out.append(dict(r))
            continue
        app = app_of(r["bundle"])
        own = app == r["bundle"]
        if app not in index:
            index[app] = len(out)
            out.append(dict(r, bundle=app, own_pid=own))
            continue
        a = out[index[app]]
        if own and (not a["own_pid"] or (r["playing"] and not a["playing"])):
            a["pid"], a["own_pid"] = r["pid"], True
        a["playing"], a["hears"] = a["playing"] or r["playing"], a["hears"] or r["hears"]
    for a in out:
        a.pop("own_pid", None)
    return out


_OWNERS = {}                    # (pid, bundle) -> (app's bundle, app's pid), or None: once per process
_responsible = ctypes.CDLL(None).responsibility_get_pid_responsible_for_pid
_responsible.restype, _responsible.argtypes = ctypes.c_int, [ctypes.c_int]


def _owner(pid, bundle):
    """The app a process is the user's as: (its bundle id, its pid), the app macOS holds
    responsible for it, when that is an app with a place in the Dock (regular), else None.
    Measured: Chrome's audio service (com.google.Chrome.helper) -> Chrome; systemsoundserverd
    -> no app at all."""
    key = (pid, bundle)
    if key not in _OWNERS:
        if len(_OWNERS) > 512:
            _OWNERS.clear()
        _OWNERS[key] = None
        try:
            from AppKit import NSRunningApplication
            boss = _responsible(pid) if pid else -1
            a = NSRunningApplication.runningApplicationWithProcessIdentifier_(boss if boss > 0 else pid)
            if a is not None and a.activationPolicy() == 0 and a.bundleIdentifier():
                _OWNERS[key] = (str(a.bundleIdentifier()), int(a.processIdentifier()))
        except Exception:
            pass
    return _OWNERS[key]


# Standby's own processes: its apps by bundle id (dev.standby.*), and, for a process
# with none or only Python's (org.python.python: measured, a child of Standby playing
# sound shows as that), one running this checkout's code. Dictation playing its cue
# with the mic open looked like a call, and was asked about at every dictation.
REPO_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_KNOWN = {}                     # (pid, bundle) -> (own, name): once per process
PYTHON_APPS = ("", "org.python.python")


def is_standby(pid, bundle):
    if (bundle or "").startswith(STANDBY_APPS) or pid == os.getpid():
        return True
    return _known(pid, bundle)[0]


def app_name(pid, bundle):
    """What to call an app in the user's words, never a bundle id: the call app's
    or browser's name we know, else its own (macOS's), else its bundle's last part
    ("Python"), else its program's ("afplay"), else "An app"."""
    return CALL_APPS.get(bundle) or BROWSERS.get(bundle) or _known(pid, bundle)[1]


def _known(pid, bundle):
    key = (pid, bundle)
    if key not in _KNOWN:
        if len(_KNOWN) > 512:
            _KNOWN.clear()
        args = process_args(pid) if pid and bundle in PYTHON_APPS else []
        own = any(a.startswith(os.sep) and os.path.realpath(a).startswith(REPO_DIR + os.sep) for a in args)
        _KNOWN[key] = (own, _readable(pid, bundle, args))
    return _KNOWN[key]


def _readable(pid, bundle, args):
    name = _name_for_pid(pid, fallback=None) if pid else None
    if name and name != bundle:
        return name
    if bundle:
        last = bundle.rsplit(".", 1)[-1]
        return last[:1].upper() + last[1:] if last else "An app"
    if args:
        return os.path.basename(args[0]) or "An app"
    return "An app"


def process_args(pid):
    """A process's program and arguments, as the kernel has them (KERN_PROCARGS2, the
    user's own processes only): [] if they can't be read."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, int(pid))           # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
            return []
        raw = buf.raw[:size.value]
        argc = int.from_bytes(raw[:4], "little")
        parts = raw[4:].split(b"\0")
        program, i = parts[0], 1
        while i < len(parts) and parts[i] == b"":           # the padding after the program's path
            i += 1
        return [x.decode("utf-8", "replace") for x in [program] + parts[i:i + argc]]
    except (OSError, ValueError, TypeError):
        return []


def _name_for_pid(pid, fallback="pid"):
    """What macOS calls a process (its app's name), or "pid N" (fallback None: None)."""
    if not pid:
        return "(unknown)" if fallback else None
    try:
        import AppKit
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None and app.localizedName():
            return app.localizedName()
    except Exception:
        pass
    return "pid %d" % pid if fallback else None


def _confidence(bundle, playing, hears):
    """How sure we are that this app's audio is a meeting, 0 to 1. The strong signal
    is the microphone: an app that plays AND listens is in a conversation, where one
    that only plays may be a video. A call app is a call either way; a browser only
    with the mic open (BROWSERS)."""
    if not playing:
        return 0.0
    if bundle in CALL_APPS and hears:
        return 0.95                  # Zoom with the mic open: certainly a call
    if bundle in CALL_APPS:
        return 0.8                   # Zoom playing, mic muted or not open yet
    if hears:
        return 0.7                   # a browser in Meet, or a call app we do not know: asked about
    return 0.3                       # just playing sound: music, a video


# ---- what the user is told when the capture stops ---------------------------------
#
# Every word the user sees about
# this channel lives here, so nobody else writes or shortens them: the page shows
# reason (the chip) and detail (its tooltip) as they are, and the recorder keeps past
# in the session's events (sessions/<id>/events.jsonl), so the transcript can say
# "Meeting audio paused for 4 min: the screen was asleep". "paused" mends itself and
# the recorder keeps trying; "error" needs the user. cause is for code and tests and
# is never shown. {app} is the app's name, {why} what macOS said. ASCII only: these
# reach logs, and a non-ASCII log line crashes an app started by launchd.
CAUSES = {
    "screen_asleep": ("paused", "the screen is asleep",
                      "The screen is asleep or locked, so macOS offers nothing to capture. "
                      "It resumes by itself when the screen wakes.",
                      "the screen was asleep"),
    "app_closed": ("paused", "{app} is not running",
                   "{app} is not running, so there is no call audio to capture. "
                   "It resumes if {app} plays again.",
                   "{app} was closed"),
    "stopped": ("paused", "macOS stopped the capture",
                "macOS stopped capturing {app}'s audio, saying: {why}. Standby is starting it again.",
                "macOS stopped the capture"),
    "stalled": ("paused", "no audio is arriving",
                "{app} stopped sending audio without saying why. Standby is starting it again.",
                "no audio arrived"),
    "permission": ("error", "allow Standby to hear it",
                   "Allow Standby in System Settings, Privacy & Security, Screen & System Audio "
                   "Recording, to hear {app}'s side of the call. No screen is recorded: only the sound. "
                   "It starts by itself once allowed.",
                   "Standby was not allowed to hear it"),
    "failed": ("error", "the capture failed",
               "Standby could not capture {app}'s audio: {why}.",
               "the capture failed"),
}
# Silent this long after macOS complains: the capture has stopped. The same rule as the
# recorder's own watchdog (STALL in mac/standby.py, which imports this module, so the
# two are kept equal by test_meeting_capture rather than by an import). Then looked at
# again CONFIRM later, because a long decode on the recorder's main thread holds this
# module's callbacks up just as it holds the recorder's: late audio, not gone.
STALL = 2.0
CONFIRM = 0.5
_DECLINED = -3801     # SCStreamErrorUserDeclined: the user has not allowed capture


def describe(cause, app="", why=""):
    """The words for one cause: {"state", "cause", "reason", "detail", "past"}. The
    recorder uses this for a stall it finds itself ("stalled"), so its words come from
    the same table."""
    state, reason, detail, past = CAUSES.get(cause) or CAUSES["failed"]
    fill = {"app": app or "the meeting app", "why": (why or "no reason given").rstrip(".")}
    return {"state": state, "cause": cause if cause in CAUSES else "failed", "reason": reason.format(**fill),
            "detail": detail.format(**fill), "past": past.format(**fill)}


class Stop(Exception):
    """A start that cannot go on, and why, as one of CAUSES."""

    def __init__(self, cause, why):
        Exception.__init__(self, why)
        self.cause = cause


def _cause_of(exc):
    """Which of CAUSES a failure to start is. ScreenCaptureKit's own refusal code is
    the sure sign of a missing permission; Screen Recording's preflight is only asked
    when there is no code, because on macOS 15+ "System Audio Recording Only" is
    enough for audio and the preflight may well say no to it. Unmeasured: the real
    refusal on a Mac that has not allowed it."""
    if isinstance(exc, Stop):
        return exc.cause
    code = getattr(exc, "code", None)
    if code == _DECLINED:
        return "permission"
    if code is None and not _permitted():
        return "permission"
    return "failed"


def _permitted():
    """Screen & System Audio Recording, without asking (no prompt). True when it
    cannot be told."""
    allowed = preflight()
    return True if allowed is None else allowed


def preflight():
    """The same, or None when it cannot be told: True, False or None. No prompt."""
    try:
        from native import permissions
        return permissions.screen_allowed()
    except Exception:
        return None


EVERYTHING = "*"      # start(EVERYTHING): all the Mac plays but Standby's own
STANDBY_APPS = "dev.standby."     # Standby's apps' bundle identifiers start with this

# Refused once in this process: ScreenCaptureKit is not asked again (each time it is
# asked, macOS shows its prompt again: the user saw it over and over in a meeting)
# until the preflight, which asks nothing, says it is allowed.
_REFUSED = []


class _Said(object):
    """What the user is told, shared by the capture and its fake: state, cause,
    reason, detail and past, all from CAUSES. None before the first start."""
    state = cause = None
    reason = detail = past = ""

    def _recording(self):
        self.ok, self.error, self.state, self.cause = True, None, "recording", None
        self.reason = self.detail = self.past = ""

    def _stopped(self, cause, why):
        said = describe(cause, self.name or _app_name(self.target), why)
        self.ok, self.error = False, why
        self.state, self.cause = said["state"], said["cause"]
        self.reason, self.detail, self.past = said["reason"], said["detail"], said["past"]
        self.log("meeting audio %s (%s): %s" % (self.state, self.cause, why))


def _app_name(target):
    """What to call the app before ScreenCaptureKit has named it."""
    if target == EVERYTHING:
        return "the Mac"
    if isinstance(target, str):
        return CALL_APPS.get(target) or BROWSERS.get(target, "")
    return ""


# ---- the call's audio, through ScreenCaptureKit -----------------------------------

def _wait(start, timeout=10.0, args=2):
    """Run a ScreenCaptureKit call that answers on a callback, and wait for it.

    args is how many arguments that callback takes: shareable content hands back two
    (the content and an error), startCapture one (an error alone). This has to match:
    pyobjc calls the block with exactly what ObjC declares, and a Python callable that
    wants a different number raises into ObjC, which aborts the whole process. The
    first version of this probe crashed that way in the user's hands."""
    box, done = {}, threading.Event()

    def one(err=None):
        box.update(value=True, err=err)
        done.set()

    def two(value=None, err=None):
        box.update(value=value, err=err)
        done.set()

    start(two if args == 2 else one)
    if not done.wait(timeout):
        raise Stop("stalled", "ScreenCaptureKit did not answer")
    err = box.get("err")
    if err is not None or box.get("value") is None:
        exc = RuntimeError(_said(err) if err is not None else "nothing came back")
        try:
            exc.code = err.code()            # the refusal's code says whether it is the permission
        except Exception:
            pass
        raise exc
    return box["value"]


def _said(err):
    """An NSError in words, never raising."""
    try:
        return str(err.localizedDescription())
    except Exception:
        return str(err)


# The audio delegate, declared once with the selector and signature ScreenCaptureKit
# itself declares (SCStreamOutput), so nothing is guessed from the method name.
_AUDIO_SELECTOR = b"stream:didOutputSampleBuffer:ofType:"
_AUDIO_SIGNATURE = b"v@:@^{opaqueCMSampleBuffer=}q"
_OUTPUT_CLASS = []


_DELEGATE_CLASS = []


def _delegate_class():
    """The stream's own delegate, so a capture macOS ends says why: the laptop slept,
    the app quit, the display changed. Without it a dead stream merely goes quiet, and
    the recorder can only guess from the silence."""
    if not _DELEGATE_CLASS:
        import AppKit
        import objc

        def stopped(self, stream, err):
            try:
                self.owner.complain(err.localizedDescription() if err else "no reason given")
            except Exception:
                pass

        class _Delegate(AppKit.NSObject, protocols=[objc.protocolNamed("SCStreamDelegate")]):
            stream_didStopWithError_ = objc.selector(stopped, selector=b"stream:didStopWithError:",
                                                     signature=b"v@:@@")
        _DELEGATE_CLASS.append(_Delegate)
    return _DELEGATE_CLASS[0]


def _output_class():
    """A class whose instances take audio buffers for their .owner. Everything is
    caught: this runs on ScreenCaptureKit's thread, where a Python exception would
    abort the process (in the product, that is the user's recorder mid-meeting)."""
    if not _OUTPUT_CLASS:
        import AppKit
        import objc
        import ScreenCaptureKit as SC

        def took(self, stream, sbuf, kind):
            try:
                if kind == SC.SCStreamOutputTypeAudio:
                    self.owner.audio(sbuf)
            except Exception as exc:
                try:
                    self.owner.fail("a frame could not be read: %r" % exc)
                except Exception:
                    pass

        class _Output(AppKit.NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
            stream_didOutputSampleBuffer_ofType_ = objc.selector(took, selector=_AUDIO_SELECTOR,
                                                                 signature=_AUDIO_SIGNATURE)
        _OUTPUT_CLASS.append(_Output)
    return _OUTPUT_CLASS[0]


class MeetingAudio(_Said):
    """One app's output audio as mono float32 blocks, through ScreenCaptureKit.

    The interface the recorder uses: apps(), start(target,
    feed), stop(), ok, error, name, and what the user is told (state, cause, reason,
    detail, past). feed(block, t) gets each block with the monotonic time of its
    first sample. Our own audio is left out, so Claude's spoken answers are never
    captured back. start() never raises: a failure is .error with .ok false.
    """

    def __init__(self, sample_rate=16000, include_mine=False, log=None):
        self.rate, self.include_mine = sample_rate, include_mine
        self.log = log or (lambda msg: None)      # the recorder's log; silent by default
        self.ok, self.error, self.name, self.target = False, None, "", None
        self.blocks, self.first_at, self.started_at, self.format = [], None, None, None
        self.note, self.last_frame = None, None       # what ScreenCaptureKit said; when audio last arrived
        self.opened, self.run = None, 0     # when the last start began (monotonic); which start it is
        self.confirming = None              # the look again after a complaint, while one is due
        self.lock = threading.Lock()
        self.stream, self._out, self._delegate, self.feed = None, None, None, None
        self._noted = True                  # its first audio after each start is noted (note_grant)
        self._described = True              # and its first buffer described in the log

    def apps(self):
        return [a for a in audio_apps() if not a.get("own")]      # Standby's own is never a meeting

    def start(self, target=None, feed=None):
        self.target, self.feed = target or self.target, feed
        self.run += 1
        self.opened, self.last_frame, self.note = time.monotonic(), None, None
        self._noted, self._described = False, False
        app.build_mark()                     # once per process, off ScreenCaptureKit's thread
        if _REFUSED and preflight() is not True:
            self._stopped("permission", "not allowed yet: waiting for it, without asking macOS again")
            return
        try:
            self._start()
            self._recording()
            del _REFUSED[:]
        except Exception as exc:
            self._stopped(_cause_of(exc), str(exc))
            if self.cause == "permission" and not _REFUSED:
                _REFUSED.append(time.time())
            self.stop()

    def _start(self):
        if os.environ.get("STANDBY_NO_DEVICES"):
            # A test never captures for real: the sessions here run where Screen Recording is
            # allowed, so it would capture with no prompt, and a process that exits with it
            # running crashes (2026-10-08).
            raise Stop("failed", "no real capture in a test (STANDBY_NO_DEVICES)")
        import ScreenCaptureKit as SC
        content = _wait(lambda cb: SC.SCShareableContent.
                        getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                            False, False, cb), args=2)
        # Audio through ScreenCaptureKit hangs off a display, and macOS offers none while
        # the screen is asleep or locked: measured, 0 displays with 42 apps still listed.
        # So a sleeping screen stops this capture, and the recorder has to start it again
        # on wake. Core Audio process taps need no display, which is the way round it.
        if not content.displays():
            raise Stop("screen_asleep", "the screen is asleep or locked, so macOS offers nothing to capture")
        if self.target == EVERYTHING:
            # All the Mac plays but Standby (what to take back out of the mic). Claude's
            # voice is voice mode's engine's to take out, so it is never taken out twice.
            self.name = "the Mac"
            mine = [a for a in content.applications() if (a.bundleIdentifier() or "").startswith(STANDBY_APPS)]
            filt = SC.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
                content.displays()[0], mine, [])
        else:
            listed = content.applications()
            apps = [a for a in listed if is_of(self.target, a.bundleIdentifier())]
            if not apps:
                raise Stop("app_closed", "%s is not running" % self.target)
            self.name = next((a.applicationName() for a in apps if a.bundleIdentifier() == self.target), None) \
                or apps[0].applicationName() or self.target
            unlisted = unlisted_sound([int(a.processID()) for a in listed], sound_pids(self.target))
            if unlisted:
                # Its sound comes from a process ScreenCaptureKit doesn't list: Chrome's audio
                # service, a helper (measured, macOS 14.7: the filter with Chrome alone heard
                # only zeros). All the Mac plays then, but every other app listed: this app's
                # helpers are in it, and so may be a system sound.
                self.log("meeting audio: %s plays through a process not listed (pid %s): everything "
                         "but the other apps" % (self.name, ", ".join(str(p) for p in unlisted)))
                others = [a for a in listed if not is_of(self.target, a.bundleIdentifier())]
                filt = SC.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
                    content.displays()[0], others, [])
            else:
                # This app's audio only: include it and nothing else, so music and notifications
                # stay out. ScreenCaptureKit wants a display filter even for audio alone, so a
                # 2x2 frame is configured and never read.
                filt = SC.SCContentFilter.alloc().initWithDisplay_includingApplications_exceptingWindows_(
                    content.displays()[0], apps, [])
        cfg = SC.SCStreamConfiguration.alloc().init()
        cfg.setCapturesAudio_(True)
        cfg.setExcludesCurrentProcessAudio_(not self.include_mine)
        cfg.setSampleRate_(self.rate)
        cfg.setChannelCount_(1)
        cfg.setWidth_(2)
        cfg.setHeight_(2)
        self._delegate = _delegate_class().alloc().init()
        self._delegate.owner = self
        self._out = _output_class().alloc().init()
        self._out.owner = self
        self.stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, self._delegate)
        ok, err = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._out, SC.SCStreamOutputTypeAudio, None, None)
        if not ok:
            raise RuntimeError("the audio output was refused: %s" % err)
        self.started_at = time.time()
        _wait(lambda cb: self.stream.startCaptureWithCompletionHandler_(cb), args=1)

    def audio(self, sbuf):
        """One buffer, on ScreenCaptureKit's thread. sbuf: the sample buffer as pyobjc
        hands it to the callback (an objc.PyObjCPointer, its type being opaque here), or
        its address."""
        addr = _address(sbuf)
        if not addr:
            return
        if self.format is None:
            self.format = self._describe(addr) or "as configured: %d Hz, 1 channel, 32-bit float" % self.rate
        block = cm.CMSampleBufferGetDataBuffer(addr)       # the sample buffer's own: alive while it is
        if not block:
            return
        # The samples through ctypes (pyobjc returns a tuple of garbage for this out
        # void**): string_at copies the bytes while the buffer is retained.
        at, total, data = ctypes.c_size_t(0), ctypes.c_size_t(0), ctypes.POINTER(ctypes.c_char)()
        err = cm.CMBlockBufferGetDataPointer(block, 0, ctypes.byref(at), ctypes.byref(total), ctypes.byref(data))
        if err or not total.value:
            return
        samples = np.frombuffer(ctypes.string_at(data, total.value), dtype=np.float32)
        at = self._when(addr, len(samples))
        if not self._described:
            self._described = True
            cm.CMSampleBufferGetNumSamples.restype, cm.CMSampleBufferGetNumSamples.argtypes = ctypes.c_long, [ctypes.c_void_p]
            self.log("meeting audio: %s's first buffer: %d samples, %d bytes, %s, peak %.4f" % (
                self.name, cm.CMSampleBufferGetNumSamples(addr), total.value, self.format, float(np.abs(samples).max())
                if len(samples) else 0.0))
        with self.lock:
            self.last_frame = time.monotonic()
            if self.first_at is None:
                self.first_at = time.time()
            noted, self._noted = self._noted, True
            if self.feed is None:
                self.blocks.append((at, samples))        # the buffer's own time, not arrival
        if not noted:                                    # this build hears the Mac: once per start
            app.note_grant("heard")
        if self.feed is not None:
            self.feed(samples, at)

    def _when(self, sbuf, count):
        """When this block's first sample was played, on the monotonic clock.

        Always the buffer's own stamp, never when this callback ran. ScreenCaptureKit
        stamps every buffer from the same clock as time.monotonic() (nanoseconds, 20 ms
        apart), and it delivers in bursts of about 50 a second: measured on this Mac,
        arrival time leaves gaps between blocks of up to +57 ms and -20 ms, while the
        buffer's own stamps are contiguous to within 42 microseconds. The recorder
        places the other side of the call on the session clock from this, so arrival
        time is only the fallback for a buffer that somehow carries no stamp."""
        try:
            pts = cm.CMSampleBufferGetPresentationTimeStamp(sbuf)
            if pts.timescale and pts.flags & 1:           # kCMTimeFlags_Valid
                return pts.value / float(pts.timescale)
        except Exception:
            pass
        return time.monotonic() - count / float(self.rate)

    def _describe(self, sbuf):
        """The format as ScreenCaptureKit states it, or None (sbuf: the sample buffer's
        address). Never raises."""
        try:
            desc = cm.CMSampleBufferGetFormatDescription(sbuf)
            asbd = cm.CMAudioFormatDescriptionGetStreamBasicDescription(desc) if desc else None
            if not asbd:
                return None
            a = asbd.contents
            return "%.0f Hz, %d channel(s), %d bits" % (a.mSampleRate, a.mChannelsPerFrame, a.mBitsPerChannel)
        except Exception:
            return None

    def complain(self, why):
        """ScreenCaptureKit says it stopped. Measured: it said "Failed to find any
        displays or windows to capture" as the screen went to sleep, while the audio
        kept arriving perfectly for seconds afterwards. So this is a stop only if the
        audio has stopped too, for STALL s and still CONFIRM s later; otherwise it is
        noted and the capture carries on. Treating it as fatal would have the recorder
        tear the channel down and build it again for ever."""
        self.note = why
        if not self._silent():
            self.log("meeting audio: %s (audio is still arriving, so carrying on)" % why)
            return
        self.confirming = threading.Timer(CONFIRM, self._confirm, (why, self.run))   # tests join it
        self.confirming.daemon = True
        self.confirming.start()

    def _silent(self):
        last = self.last_frame if self.last_frame is not None else self.opened
        return last is None or time.monotonic() - last > STALL

    def _confirm(self, why, run):
        """CONFIRM s after a complaint with no audio behind it: still none, from the
        same start, and it has stopped. A newer start or a stop() makes this a no-op."""
        try:
            if run != self.run or not self.ok:
                return
            if self._silent():
                self._stopped("stopped", why)
            else:
                self.log("meeting audio: %s (the audio was late, not gone: carrying on)" % why)
        except Exception:
            pass

    def fail(self, why, cause="failed"):
        self._stopped(cause, why)

    def take(self):
        with self.lock:
            out, self.blocks = self.blocks, []
        return out

    def stop(self):
        """Ends the capture. Why it stopped, if it did, stays for the page to show; a
        capture that was fine has nothing more to say."""
        stream, self.stream = self.stream, None
        self.ok, self.run = False, self.run + 1
        if self.state == "recording":
            self.state = None
        if stream is None:
            return
        done = threading.Event()
        try:
            stream.stopCaptureWithCompletionHandler_(lambda err=None: done.set())
            done.wait(5)
        except Exception:
            pass


class FakeMeetingAudio(_Said):
    """The same interface with no macOS in it, for tests on either side.

    Set apps_now to what apps() should report; fail_with (what went wrong) and
    fail_cause (one of CAUSES, "failed" if unset) to make start() refuse; stalled to
    make it go quiet without saying so (what a dead stream looks like); and call
    lose(cause) to have it stop mid-call the way the real one does (the screen going
    to sleep: lose("screen_asleep")). feed() gets blocks of `block_samples` zeros at
    `rate`, with times from a clock that starts at start(), so a late join is visible.
    """

    def __init__(self, rate=16000, block_samples=320, interval=0.02, log=None):
        self.rate, self.block_samples, self.interval = rate, block_samples, interval
        self.log = log or (lambda msg: None)
        self.ok, self.error, self.name, self.note, self.target = False, None, "", None, None
        self.apps_now, self.fail_with, self.fail_cause, self.stalled = [], None, None, False
        self.fed, self._run, self._thread = 0, False, None

    def apps(self):
        return [dict(a) for a in self.apps_now]

    def start(self, target=None, feed=None):
        self.target = target or self.target
        self.name = next((a.get("name", "") for a in self.apps_now if a.get("bundle") == self.target), "")
        if self.fail_with or self.fail_cause:
            self._stopped(self.fail_cause or "failed", self.fail_with or describe(self.fail_cause)["detail"])
            return
        self._recording()
        if feed is None:
            return
        self._run = True
        started, sent = time.monotonic(), [0]

        def go():
            while self._run:
                time.sleep(self.interval)
                if self._run and not self.stalled:
                    at = started + sent[0] / float(self.rate)
                    sent[0] += self.block_samples
                    self.fed += 1
                    feed(np.zeros(self.block_samples, "float32"), at)
        self._thread = threading.Thread(target=go, daemon=True)
        self._thread.start()

    def lose(self, cause, why=""):
        """Stops mid-call as the real capture would: no more audio, and why."""
        self._run = False
        self._stopped(cause, why or describe(cause, self.name)["detail"])

    def stop(self):
        self._run, self.ok = False, False
        if self.state == "recording":
            self.state = None
        if self._thread is not None:
            self._thread.join(1)
            self._thread = None
