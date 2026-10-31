"""native/appaudio.py on a Mac (Core Audio: its list of processes, and process taps).

The other side of a call: the meeting's own audio, so Standby hears everyone.

On a call with headphones the microphone hears only the user, so the people on the
other side never reach the transcript. This takes the call app's own output through
a Core Audio process tap (macOS 14.4 and later), as the recorder's "Others" channel,
with no setup from the user beyond allowing it once. macOS asks for System Audio
Recording only: never the screen.

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

Three things to know:
* macOS never says whether it allows this. A refused tap starts like any other and
  hands over exact zeros, so a refusal is told by its evidence: the app is playing
  (Core Audio says so) and only silence arrives (REFUSED_AFTER), on a build that has
  never heard anything. The first start of a build is what brings macOS's prompt.
* A tap needs no display: a sleeping or locked screen doesn't stop it.
* Standby's own audio is left out, so Claude's spoken answers are never captured back.

Where pyobjc stops and ctypes starts, learned the hard way here (assume it is the
rule, not the exception): an "out" C array is unreadable through pyobjc (the audio
process list raises); a C struct can arrive as a plain tuple; and a Python exception
on Core Audio's own thread aborts the process rather than raising, so the I/O
callback here catches everything and does as little as it can.
"""
import collections
import ctypes
import ctypes.util
import os
import threading
import time
import uuid

import numpy as np

from native import app

# ---- which apps are playing audio (read-only) ----------------------------------
#
# Core Audio through ctypes: pyobjc's bindings cannot read these array properties
# ("converting to a C array"), and the product would hit the same wall.

ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))


class _ASBD(ctypes.Structure):
    _fields_ = [("mSampleRate", ctypes.c_double), ("mFormatID", ctypes.c_uint32), ("mFormatFlags", ctypes.c_uint32),
                ("mBytesPerPacket", ctypes.c_uint32), ("mFramesPerPacket", ctypes.c_uint32),
                ("mBytesPerFrame", ctypes.c_uint32), ("mChannelsPerFrame", ctypes.c_uint32),
                ("mBitsPerChannel", ctypes.c_uint32), ("mReserved", ctypes.c_uint32)]


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
OUTPUT_DEVICE = _fourcc("sOut")       # kAudioHardwarePropertyDefaultSystemOutputDevice
DEVICE_UID = _fourcc("uid ")          # kAudioDevicePropertyDeviceUID
TAP_FORMAT = _fourcc("tfmt")          # kAudioTapPropertyFormat
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
    own (its apps, or a process of this checkout's, as its voice: it shows as
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


def processes():
    """Every process Core Audio knows, as it lists them: (its object, pid, bundle id, playing)."""
    out = []
    for obj in _get(SYSTEM_OBJECT, PROCESS_LIST, ctypes.c_uint32, count=0) or []:
        out.append((int(obj), int(_get(obj, PROCESS_PID, ctypes.c_int32) or 0), _get_string(obj, BUNDLE_ID) or "",
                    bool(_get(obj, IS_RUNNING_OUTPUT, ctypes.c_uint32))))
    return out


def sound_objects(target):
    """Every process whose sound is this app's (_owner), as (object, pid, bundle, playing):
    target an app's bundle id (its helpers too: Chrome plays through its audio service),
    or a pid (that process, and those macOS holds it responsible for)."""
    out = []
    for obj, pid, own, playing in processes():
        if not pid:
            continue
        owner = _owner(pid, own)
        if isinstance(target, int):
            mine = pid == target or (owner is not None and owner[1] == target)
        else:
            mine = is_of(target, own) or (owner is not None and owner[0] == target)
        if mine:
            out.append((obj, pid, own, playing))
    return out


def sound_pids(bundle):
    """The pids of every process whose sound is this app's (_owner), as Core Audio lists them."""
    return [pid for _obj, pid, _own, _playing in sound_objects(bundle)]


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
# "Meeting audio paused for 4 min: Zoom was closed". "paused" mends itself and
# the recorder keeps trying; "error" needs the user. cause is for code and tests and
# is never shown. {app} is the app's name, {why} what went wrong. ASCII only: these
# reach logs, and a non-ASCII log line crashes an app started by launchd.
CAUSES = {
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
                   "Recording, under System Audio Recording Only, to hear {app}'s side of the call. "
                   "Standby takes only the sound, never the screen, and hears it as soon as it is allowed.",
                   "Standby was not allowed to hear it"),
    "failed": ("error", "the capture failed",
               "Standby could not capture {app}'s audio: {why}.",
               "the capture failed"),
}
# Silent this long: the capture has stopped. The same rule as the recorder's own
# watchdog (STALL in mac/standby.py, which imports this module, so the two are kept
# equal by test_meeting_capture rather than by an import).
STALL = 2.0
# The app playing (Core Audio says its output runs), only exact zeros this long, on a
# build that has never heard a sound: macOS has not allowed it. A refusal says nothing
# else: every call succeeds and the tap hands over silence.
REFUSED_AFTER = 3.0
RETAP = 10.0          # while refused, the tap is made again this often (it asks macOS nothing)
WATCH = 1.0           # how often the tapped app's processes and the output device are looked at again
FLOOR = (14, 4)       # process taps that a user can allow: macOS 14.4 and later


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


DICTATION_APP = "dev.standby.dictation"


def grant(what):
    """The key a grant is kept under (app.grants): macOS allows each app on its own, so
    Standby Dictation has its own ("sound@dictation"). Standby's are its processes' too: its
    recorder and voice are plain pythons it started, which macOS counts as Standby."""
    return what + ("@dictation" if app.bundle_id() == DICTATION_APP else "")


def heard_before():
    """This build of this app has heard the Mac's sound through a tap: macOS allows it,
    so silence from now on is only silence."""
    g = app.grants().get(grant("sound")) or {}
    return bool(g) and g.get("build") == app.build_mark()


EVERYTHING = "*"      # start(EVERYTHING): all the Mac plays but Standby's own
STANDBY_APPS = "dev.standby."     # Standby's apps' bundle identifiers start with this


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
    """What to call the app before its processes are looked at."""
    if target == EVERYTHING:
        return "the Mac"
    if isinstance(target, str):
        return CALL_APPS.get(target) or BROWSERS.get(target, "")
    return ""


# ---- the call's audio, through a Core Audio process tap ------------------------------
#
# A tap (CATapDescription, AudioHardwareCreateProcessTap) mixes what some processes play
# into one mono stream; Core Audio reads it as the input of a private aggregate device,
# which an I/O callback runs. Private: no device shows in the user's lists. Unmuted: the
# user still hears the call. The aggregate device is built on the default output device,
# whose clock it runs on (AudioCap's way, Apple's sample's too).

class _Buffer(ctypes.Structure):
    _fields_ = [("mNumberChannels", ctypes.c_uint32), ("mDataByteSize", ctypes.c_uint32),
                ("mData", ctypes.c_void_p)]


class _BufferList(ctypes.Structure):
    _fields_ = [("mNumberBuffers", ctypes.c_uint32), ("mBuffers", _Buffer * 1)]


class _TimeStamp(ctypes.Structure):
    _fields_ = [("mSampleTime", ctypes.c_double), ("mHostTime", ctypes.c_uint64),
                ("mRateScalar", ctypes.c_double), ("mWordClockTime", ctypes.c_uint64),
                ("mSMPTETime", ctypes.c_byte * 24), ("mFlags", ctypes.c_uint32), ("mReserved", ctypes.c_uint32)]


_IOPROC = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
ca.AudioHardwareCreateProcessTap.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
ca.AudioHardwareDestroyProcessTap.argtypes = [ctypes.c_uint32]
ca.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
ca.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
ca.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, _IOPROC, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
ca.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
ca.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
ca.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
for _fn in (ca.AudioHardwareCreateProcessTap, ca.AudioHardwareDestroyProcessTap, ca.AudioHardwareCreateAggregateDevice,
            ca.AudioHardwareDestroyAggregateDevice, ca.AudioDeviceCreateIOProcID, ca.AudioDeviceDestroyIOProcID,
            ca.AudioDeviceStart, ca.AudioDeviceStop):
    _fn.restype = ctypes.c_int32


class _Timebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


_libc = ctypes.CDLL(None)
_libc.mach_absolute_time.restype = ctypes.c_uint64
_tb = _Timebase()
_libc.mach_timebase_info(ctypes.byref(_tb))
_TICK = _tb.numer / float(_tb.denom) / 1e9          # seconds per host tick


def _host_now():
    return _libc.mach_absolute_time() * _TICK


def supported():
    """(True, "") on a Mac that has process taps a user can allow, else (False, why)."""
    import platform
    try:
        version = tuple(int(x) for x in (platform.mac_ver()[0] or "0").split(".")[:2])
    except ValueError:
        version = (0,)
    if version < FLOOR:
        return False, "Needs macOS %d.%d or later" % FLOOR
    try:
        import CoreAudio
        CoreAudio.CATapDescription
    except (ImportError, AttributeError):
        return False, "Run ./install.sh again"
    return True, ""


def _output_uid():
    """The default output device's UID (where the user hears the Mac), or ""."""
    return _get_string(_get(SYSTEM_OBJECT, OUTPUT_DEVICE, ctypes.c_uint32) or 0, DEVICE_UID) or ""


def _read(data, when):
    """One I/O callback's input (an AudioBufferList's address) and its time (an
    AudioTimeStamp's): (mono float32 samples, the host time of the first, in seconds), or
    None. Copies the samples: Core Audio's buffer is gone once the callback returns."""
    if not data:
        return None
    bl = ctypes.cast(data, ctypes.POINTER(_BufferList)).contents
    if not bl.mNumberBuffers or not bl.mBuffers[0].mData or not bl.mBuffers[0].mDataByteSize:
        return None
    b = bl.mBuffers[0]
    x = np.frombuffer(ctypes.string_at(b.mData, b.mDataByteSize), dtype=np.float32)
    if b.mNumberChannels > 1:
        x = x[:len(x) - len(x) % b.mNumberChannels].reshape(-1, b.mNumberChannels).mean(axis=1).astype(np.float32)
    stamp = ctypes.cast(when, ctypes.POINTER(_TimeStamp)).contents if when else None
    return x, (stamp.mHostTime * _TICK if stamp is not None and stamp.mHostTime else _host_now())


def _osstatus(err):
    """A Core Audio error code, as its four characters when it is one ('!obj')."""
    raw = int(err) & 0xFFFFFFFF
    text = raw.to_bytes(4, "big")
    return "'%s'" % text.decode() if all(32 <= b < 127 for b in text) else str(int(err))


class _Tap(object):
    """One tap and its aggregate device, running: block(samples, host_seconds, rate) from
    Core Audio's own thread, as little as it can be (the samples copied, handed on)."""

    def __init__(self, objects, exclude, block):
        import objc
        import CoreAudio
        from Foundation import NSDictionary
        self.tap, self.device, self.proc, self._cb = 0, 0, None, None
        if exclude:
            desc = CoreAudio.CATapDescription.alloc().initMonoGlobalTapButExcludeProcesses_(objects)
        else:
            desc = CoreAudio.CATapDescription.alloc().initMonoMixdownOfProcesses_(objects)
        desc.setPrivate_(True)
        desc.setMuteBehavior_(0)                  # CATapUnmuted: the user still hears it
        desc.setName_("Standby")
        tap = ctypes.c_uint32(0)
        err = ca.AudioHardwareCreateProcessTap(objc.pyobjc_id(desc), ctypes.byref(tap))
        if err:
            raise RuntimeError("the tap was refused (%s)" % _osstatus(err))
        self.tap = tap.value
        try:
            fmt = _get(self.tap, TAP_FORMAT, _ASBD)
            self.rate = float(fmt.mSampleRate) if fmt is not None and fmt.mSampleRate else 48000.0
            self.output = _output_uid()
            spec = {"name": "Standby", "uid": str(uuid.uuid4()), "private": True, "tapautostart": True,
                    "taps": [{"uid": str(desc.UUID().UUIDString()), "drift": True}]}
            if self.output:
                spec.update(master=self.output, subdevices=[{"uid": self.output}])
            spec = NSDictionary.dictionaryWithDictionary_(spec)
            dev = ctypes.c_uint32(0)
            err = ca.AudioHardwareCreateAggregateDevice(objc.pyobjc_id(spec), ctypes.byref(dev))
            if err:
                raise RuntimeError("its device was refused (%s)" % _osstatus(err))
            self.device = dev.value

            def took(_dev, _now, data, when, _out, _out_when, _client):
                try:
                    got = _read(data, when)
                    if got is not None:
                        block(got[0], got[1], self.rate)
                except Exception:
                    pass
                return 0
            self._cb = _IOPROC(took)                 # kept: Core Audio holds only its address
            proc = ctypes.c_void_p(0)
            err = ca.AudioDeviceCreateIOProcID(self.device, self._cb, None, ctypes.byref(proc))
            if err:
                raise RuntimeError("its callback was refused (%s)" % _osstatus(err))
            self.proc = proc
            err = ca.AudioDeviceStart(self.device, self.proc)      # macOS's prompt, the first time
            if err:
                raise RuntimeError("it did not start (%s)" % _osstatus(err))
        except Exception:
            self.close()
            raise

    def close(self):
        """Stopped and gone, every part of it. Never raises."""
        try:
            if self.device and self.proc is not None:
                ca.AudioDeviceStop(self.device, self.proc)
                ca.AudioDeviceDestroyIOProcID(self.device, self.proc)
            if self.device:
                ca.AudioHardwareDestroyAggregateDevice(self.device)
            if self.tap:
                ca.AudioHardwareDestroyProcessTap(self.tap)
        except Exception:
            pass
        self.device, self.tap, self.proc = 0, 0, None


class MeetingAudio(_Said):
    """One app's output audio as mono float32 blocks, through a Core Audio process tap.

    The interface the recorder uses: apps(), start(target,
    feed), stop(), ok, error, name, and what the user is told (state, cause, reason,
    detail, past). feed(block, t) gets each block with the monotonic time of its
    first sample. Our own audio is left out, so Claude's spoken answers are never
    captured back. start() never raises: a failure is .error with .ok false.

    Core Audio's thread only copies a block and queues it; a thread of this capture's own
    resamples it to 16 kHz, feeds it on, and watches: the app's processes (a helper that
    starts playing later is tapped too), the output device (moved to the new one), and
    the evidence of a refusal (REFUSED_AFTER).
    """

    def __init__(self, sample_rate=16000, include_mine=False, log=None):
        self.rate, self.include_mine = sample_rate, include_mine
        self.log = log or (lambda msg: None)      # the recorder's log; silent by default
        self.ok, self.error, self.name, self.target = False, None, "", None
        self.blocks, self.first_at, self.started_at, self.format = [], None, None, None
        self.note, self.last_frame = None, None       # what went wrong while running; when audio last arrived
        self.opened, self.run = None, 0     # when the last start began (monotonic); which start it is
        self.lock = threading.Lock()
        self.feed, self._tap, self._tapped, self._output = None, None, (), None
        self._queue, self._wake = collections.deque(), threading.Event()
        self._resampler, self._thread = None, None
        self._heard = False                 # a sound (not zeros) since the last start
        self._described = True              # its first block described in the log
        self._looked = 0.0                  # when the processes were last looked at (monotonic)

    def apps(self):
        return [a for a in audio_apps() if not a.get("own")]      # Standby's own is never a meeting

    def start(self, target=None, feed=None):
        self.stop()
        self.target, self.feed = target or self.target, feed
        self.run += 1
        self.opened, self.last_frame, self.note = time.monotonic(), None, None
        self._heard, self._described = False, False
        app.build_mark()                     # once per process, off Core Audio's thread
        try:
            self._start()
            self._recording()
        except Exception as exc:
            self._stopped(getattr(exc, "cause", "failed"), str(exc))
            self.stop()
            return
        self._thread = threading.Thread(target=self._go, args=(self.run,), name="meeting audio", daemon=True)
        self._thread.start()

    def _objects(self):
        """(the process objects to tap, exclude): this app's, or everything but Standby's."""
        if self.target == EVERYTHING:
            self.name = "the Mac"
            mine = [] if self.include_mine else [o for o, pid, bundle, _p in processes() if is_standby(pid, bundle)]
            return tuple(sorted(mine)), True
        found = sound_objects(self.target)
        if not found:
            raise Stop("app_closed", "%s is not running" % self.target)
        if not self.name:
            self.name = app_name(found[0][1], found[0][2]) if isinstance(self.target, int) \
                else (CALL_APPS.get(self.target) or BROWSERS.get(self.target) or app_name(found[0][1], self.target))
        return tuple(sorted(o for o, _pid, _b, _p in found)), False

    def _start(self):
        if os.environ.get("STANDBY_NO_DEVICES"):
            # A test never captures for real: it would hear the Mac's sound, and on a build
            # that macOS has never asked, bring its prompt up.
            raise Stop("failed", "no real capture in a test (STANDBY_NO_DEVICES)")
        ok, why = supported()
        if not ok:
            raise Stop("failed", why)
        self.name = ""
        objects, exclude = self._objects()
        self._tap_now(objects, exclude)
        self.started_at = time.time()

    def _tap_now(self, objects, exclude):
        old, self._tap = self._tap, None
        if old is not None:
            old.close()
        run = self.run
        tap = _Tap(list(objects), exclude, lambda x, at, rate: self._took(run, x, at, rate))
        if run != self.run:                    # stopped meanwhile, from another thread
            tap.close()
            return
        self._tap = tap
        self._tapped, self._output, self._looked = (objects, exclude), self._tap.output, time.monotonic()
        with self.lock:
            self._resampler = None            # a new tap may run at another rate
        self.format = "%.0f Hz, 1 channel, 32-bit float" % self._tap.rate
        self.log("meeting audio: tapping %s (%d process%s%s, %s, on %s)" % (
            self.name or _app_name(self.target), len(objects), "" if len(objects) == 1 else "es",
            " left out" if exclude else "", self.format, self._output or "no output device"))

    def _took(self, run, x, at, rate):
        """On Core Audio's thread: queued, nothing more."""
        if run == self.run:
            self._queue.append((x, at, rate))
            self._wake.set()

    def _go(self, run):
        """This capture's own thread: each block resampled and fed on, and the watch."""
        while run == self.run:
            self._wake.wait(0.25)
            self._wake.clear()
            while self._queue and run == self.run:
                try:
                    self._block(*self._queue.popleft())
                except Exception as exc:
                    self.log("meeting audio: a block could not be read: %r" % exc)
            if run == self.run:
                try:
                    self._watch()
                except Exception as exc:
                    self.log("meeting audio: the watch failed: %r" % exc)

    def _block(self, x, at, rate):
        from native.macos.duplex import Resampler
        with self.lock:
            if self._resampler is None or self._resampler.rate != float(rate):
                self._resampler = Resampler(rate, self.rate)
            samples = self._resampler.feed(x)
        if not len(samples):
            return
        t = at + (time.monotonic() - _host_now())          # the host clock, on the monotonic one
        if not self._described:
            self._described = True
            self.log("meeting audio: %s's first block: %d samples at %.0f Hz, peak %.4f" % (
                self.name, len(x), rate, float(np.abs(x).max())))
        sound = bool(np.any(samples))
        with self.lock:
            self.last_frame = time.monotonic()
            if self.first_at is None:
                self.first_at = time.time()
            if self.feed is None:
                self.blocks.append((t, samples))
        if sound and not self._heard:
            self._heard = True
            if not heard_before():
                app.note_grant(grant("sound"))     # this build is allowed: silence is only silence from now
            if self.cause == "permission":
                self.log("meeting audio: sound from %s: allowed now" % self.name)
                self._recording()
        if self.feed is not None:
            self.feed(samples, t)

    def _watch(self):
        """Once a WATCH: a refusal told by its evidence, the tap made again while refused,
        and the processes and output device looked at again."""
        now = time.monotonic()
        if now - self._looked < WATCH:
            return
        self._looked = now
        refused = self.cause == "permission"
        if not self._heard and not refused and now - self.opened >= REFUSED_AFTER and not heard_before() \
                and self._playing():
            self._stopped("permission", "%s is playing and only silence arrives: macOS has not allowed "
                          "System Audio Recording" % (self.name or "the app"))
            app.note_grant(grant("sound_refused"))     # what the setup window and the band say from now
            self._retap_at = now + RETAP
            return
        objects, exclude = self._objects_or_none()
        output = _output_uid()
        if refused and now >= getattr(self, "_retap_at", 0):
            self._retap_at = now + RETAP
            self._tap_now(objects or self._tapped[0], exclude)       # a grant in System Settings takes then
        elif objects is not None and (objects, exclude) != self._tapped:
            self._tap_now(objects, exclude)                          # a helper started (or ended) playing
        elif output and output != self._output:
            self.log("meeting audio: the output device changed: tapping on it")
            self._tap_now(*self._tapped)

    def _objects_or_none(self):
        try:
            return self._objects()
        except Stop:
            return None, self._tapped[1]

    def _playing(self):
        """Is what is tapped playing now (its output running), as Core Audio says?"""
        objects, exclude = self._tapped
        if exclude:
            return any(p for o, _pid, _b, p in processes() if o not in objects)
        return any(p for o, _pid, _b, p in processes() if o in objects)

    def complain(self, why):
        """The capture failed while running: said, and the recorder starts it again."""
        self.note = why
        self._stopped("stopped", why)

    def fail(self, why, cause="failed"):
        self._stopped(cause, why)

    def take(self):
        with self.lock:
            out, self.blocks = self.blocks, []
        return out

    def stop(self):
        """Ends the capture. Why it stopped, if it did, stays for the page to show; a
        capture that was fine has nothing more to say."""
        tap, self._tap = self._tap, None
        self.ok, self.run = False, self.run + 1
        self._wake.set()
        self._queue.clear()
        if self.state == "recording":
            self.state = None
        if tap is not None:
            tap.close()


class FakeMeetingAudio(_Said):
    """The same interface with no macOS in it, for tests on either side.

    Set apps_now to what apps() should report; fail_with (what went wrong) and
    fail_cause (one of CAUSES, "failed" if unset) to make start() refuse; stalled to
    make it go quiet without saying so (what a dead stream looks like); and call
    lose(cause) to have it stop mid-call the way the real one does (macOS refusing it
    on the evidence: lose("permission")). feed() gets blocks of `block_samples` zeros at
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
