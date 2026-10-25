#!/usr/bin/env python3
"""Can Standby hear the other people in a call, with no setup?

    python3 tools/meeting_audio_probe.py apps
    python3 tools/meeting_audio_probe.py listen [--bundle us.zoom.xos] [--seconds 20] [--mine]
    python3 tools/meeting_audio_probe.py both [--bundle ...] [--seconds 20]

`apps` reads which processes are playing audio (Core Audio, read-only: no capture,
no permission). `listen` captures one app's audio through ScreenCaptureKit and
reports how it behaves. `both` captures that app and the microphone at once, to
measure how much of the call the mic hears as well (the doubling on open speakers).

It never writes audio anywhere, never transcribes, and prints no content: only
counts, timings, formats, and a loudness number per second. Nothing it prints can
be listened to or read back.

Needs (a scratch venv, not install.sh): pyobjc-framework-ScreenCaptureKit,
pyobjc-framework-CoreMedia, sounddevice (for `both`), numpy.

Where pyobjc stops and ctypes starts, learned the hard way (every one of these cost a
crash or an hour, so assume it is the rule here, not the exception):

* An "out" C array is not readable through pyobjc. The audio process list raises
  "converting to a C array", and a block buffer's data comes back as a tuple whose
  first value is an address. Both are read with ctypes below.
* A C struct can arrive as a plain tuple. CMAudioFormatDescriptionGetStreamBasic-
  Description hands back a tuple, not an object with .mSampleRate, so reading it as
  an object throws.
* A pyobjc function takes exactly the arguments it declares. Passing the "out"
  argument of a function that has none (that same call) is a TypeError.
* A callback takes exactly the arguments ObjC declares: two for shareable content,
  one for startCapture, three for the audio delegate. A Python exception on
  ScreenCaptureKit's thread does not raise in your code, it aborts the process, so
  every callback here catches everything.
"""
import argparse
import ctypes
import ctypes.util
import sys
import threading
import time

import numpy as np

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
             "com.google.Chrome": "Google Chrome", "com.apple.Safari": "Safari",
             "company.thebrowser.Browser": "Arc", "com.microsoft.edgemac": "Microsoft Edge",
             "com.brave.Browser": "Brave", "org.mozilla.firefox": "Firefox",
             "com.apple.FaceTime": "FaceTime", "com.hnc.Discord": "Discord",
             "com.ringcentral.glip": "RingCentral", "com.bluejeans.mac": "BlueJeans"}


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

    A process with no bundle id is kept: some apps play through a helper process, and
    dropping those would hide the thing that is actually making sound."""
    out = []
    for obj in _get(SYSTEM_OBJECT, PROCESS_LIST, ctypes.c_uint32, count=0) or []:
        bundle = _get_string(obj, BUNDLE_ID) or ""
        pid = _get(obj, PROCESS_PID, ctypes.c_int32)
        playing = bool(_get(obj, IS_RUNNING_OUTPUT, ctypes.c_uint32))
        hears = bool(_get(obj, IS_RUNNING_INPUT, ctypes.c_uint32))
        if not include_silent and not playing:
            continue
        out.append({"bundle": bundle, "pid": pid, "playing": playing, "hears": hears,
                    "call": bundle in CALL_APPS, "name": CALL_APPS.get(bundle) or bundle or _name_for_pid(pid),
                    "confidence": _confidence(bundle, playing, hears)})
    return out


def _name_for_pid(pid):
    """What to call a process with no bundle id of its own."""
    if not pid:
        return "(unknown)"
    try:
        import AppKit
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None and app.localizedName():
            return app.localizedName()
    except Exception:
        pass
    return "pid %d" % pid


def _confidence(bundle, playing, hears):
    """How sure we are that this app's audio is a meeting, 0 to 1. The strong signal
    is the microphone: an app that plays AND listens is in a conversation, where one
    that only plays may be a video. A call app is a call either way."""
    if not playing:
        return 0.0
    if bundle in CALL_APPS and hears:
        return 0.95                  # Zoom with the mic open: certainly a call
    if bundle in CALL_APPS:
        return 0.8                   # Zoom playing, mic muted or not open yet
    if hears:
        return 0.7                   # something else in a conversation: probably a call app we do not know
    return 0.3                       # just playing sound: music, a video


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
        raise RuntimeError("ScreenCaptureKit did not answer")
    if box.get("err") is not None or box.get("value") is None:
        raise RuntimeError(str(box.get("err") or "nothing came back"))
    return box["value"]


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


class MeetingAudio(object):
    """One app's output audio as mono float32 blocks, through ScreenCaptureKit.

    The interface the recorder uses: apps(), start(target,
    feed), stop(), ok, error, name. feed(block, t) gets each block with the monotonic
    time of its first sample. Our own audio is left out, so Claude's spoken answers
    are never captured back. start() never raises: a failure is .error with .ok false.
    """

    def __init__(self, sample_rate=16000, include_mine=False):
        self.rate, self.include_mine = sample_rate, include_mine
        self.ok, self.error, self.name, self.target = False, None, "", None
        self.blocks, self.first_at, self.started_at, self.format = [], None, None, None
        self.note, self.last_frame = None, None       # what ScreenCaptureKit said; when audio last arrived
        self.lock = threading.Lock()
        self.stream, self._out, self._delegate, self.feed = None, None, None, None

    def apps(self):
        return audio_apps()

    def start(self, target=None, feed=None):
        self.target, self.feed = target or self.target, feed
        try:
            self._start()
            self.ok, self.error = True, None
        except Exception as exc:
            self.ok, self.error = False, str(exc)
            self.stop()

    def _start(self):
        import ScreenCaptureKit as SC
        content = _wait(lambda cb: SC.SCShareableContent.
                        getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                            False, False, cb), args=2)
        # Audio through ScreenCaptureKit hangs off a display, and macOS offers none while
        # the screen is asleep or locked: measured, 0 displays with 42 apps still listed.
        # So a sleeping screen stops this capture, and the recorder has to start it again
        # on wake. Core Audio process taps need no display, which is the way round it.
        if not content.displays():
            raise RuntimeError("the screen is asleep or locked, so macOS offers nothing to capture")
        apps = [a for a in content.applications() if a.bundleIdentifier() == self.target]
        if not apps:
            raise RuntimeError("%s is not running" % self.target)
        self.name = apps[0].applicationName() or self.target
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
        """One buffer, on ScreenCaptureKit's thread."""
        import CoreMedia as CM
        if self.format is None:
            self.format = self._describe(sbuf) or "as configured: %d Hz, 1 channel, 32-bit float" % self.rate
        block = CM.CMSampleBufferGetDataBuffer(sbuf)
        if block is None:
            return
        # The samples through ctypes: pyobjc returns a tuple of garbage for this
        # out void**, the same wall as the process list. objc.pyobjc_id gives the
        # buffer's address, and string_at copies the bytes while they are retained.
        import objc
        at, total, data = ctypes.c_size_t(0), ctypes.c_size_t(0), ctypes.POINTER(ctypes.c_char)()
        err = cm.CMBlockBufferGetDataPointer(ctypes.c_void_p(objc.pyobjc_id(block)), 0, ctypes.byref(at),
                                             ctypes.byref(total), ctypes.byref(data))
        if err or not total.value:
            return
        samples = np.frombuffer(ctypes.string_at(data, total.value), dtype=np.float32)
        at = self._when(sbuf, len(samples))
        with self.lock:
            self.last_frame = time.monotonic()
            if self.first_at is None:
                self.first_at = time.time()
            if self.feed is None:
                self.blocks.append((at, samples))        # the buffer's own time, not arrival
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
        import CoreMedia as CM
        try:
            pts = CM.CMSampleBufferGetPresentationTimeStamp(sbuf)
            value, scale = (pts[0], pts[1]) if isinstance(pts, (tuple, list)) else (pts.value, pts.timescale)
            if scale:
                return value / float(scale)
        except Exception:
            pass
        return time.monotonic() - count / float(self.rate)

    def _describe(self, sbuf):
        """The format as ScreenCaptureKit states it, or None. Never raises: pyobjc
        hands this back as a plain tuple, not a struct, and reading it as a struct is
        what silently killed every frame in the first version."""
        import CoreMedia as CM
        try:
            asbd = CM.CMAudioFormatDescriptionGetStreamBasicDescription(
                CM.CMSampleBufferGetFormatDescription(sbuf))
            if asbd is None:
                return None
            if hasattr(asbd, "mSampleRate"):
                return "%.0f Hz, %d channel(s), %d bits" % (asbd.mSampleRate, asbd.mChannelsPerFrame,
                                                            asbd.mBitsPerChannel)
            rate = asbd[0] if isinstance(asbd, (tuple, list)) and asbd else None
            return "%.0f Hz (as ScreenCaptureKit reports it)" % rate if rate else None
        except Exception:
            return None

    def complain(self, why):
        """ScreenCaptureKit says it stopped. Measured: it said "Failed to find any
        displays or windows to capture" as the screen went to sleep, while the audio
        kept arriving perfectly for seconds afterwards. So this is a failure only if
        the audio has stopped too; otherwise it is noted and the capture carries on.
        Treating it as fatal would have the recorder tear the channel down and build it
        again for ever."""
        self.note = why
        if self.last_frame is None or time.monotonic() - self.last_frame > 1.0:
            self.fail("the capture stopped: %s" % why)
        else:
            print("    meeting audio: %s (audio is still arriving, so carrying on)" % why)

    def fail(self, why):
        self.ok, self.error = False, why
        print("    meeting audio stopped: %s" % why)

    def take(self):
        with self.lock:
            out, self.blocks = self.blocks, []
        return out

    def stop(self):
        stream, self.stream = self.stream, None
        self.ok = False
        if stream is None:
            return
        done = threading.Event()
        try:
            stream.stopCaptureWithCompletionHandler_(lambda err=None: done.set())
            done.wait(5)
        except Exception:
            pass


def pump(seconds):
    """Let ScreenCaptureKit's callbacks run while we wait."""
    import AppKit
    app = AppKit.NSApplication.sharedApplication()
    end = time.time() + seconds
    while time.time() < end:
        ev = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
            AppKit.NSEventMaskAny, AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.05),
            AppKit.NSDefaultRunLoopMode, True)
        if ev:
            app.sendEvent_(ev)


def loudness(samples):
    """One number for how loud a second was, in dB below full scale. No content."""
    if not len(samples):
        return None
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return -99.0 if rms <= 1e-9 else round(20 * np.log10(rms), 1)


def screen_permission():
    """Whether this process may already record the screen. Audio-only capture may
    sit under a permission of its own on macOS 15+, which is what the run finds out."""
    import Quartz
    return bool(Quartz.CGPreflightScreenCaptureAccess())


ASK_THE_USER = """
-- please note these three things, they are what the team needs --
1. Did macOS show a dialog? Its exact words, and which button you pressed.
2. In System Settings > Privacy & Security, which list has a new entry: "Screen &
   System Audio Recording", or "System Audio Recording Only"? Under which name?
3. Did macOS ask you to quit and reopen anything?
"""


def cpu():
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


# ---- commands --------------------------------------------------------------------

def cmd_apps(a):
    audio_apps()                                  # the first read costs ~60 ms; warm it
    t0 = time.perf_counter()
    apps = audio_apps()
    ms = (time.perf_counter() - t0) * 1000
    print("%d processes known to Core Audio, read in %.1f ms once warm (no capture, no permission)" % (len(apps), ms))
    rounds = max(1, int(a.watch))
    for i in range(rounds):
        if i:
            time.sleep(1.0)
            apps = audio_apps()
        playing = [x for x in apps if x["playing"]]
        print("%splaying right now: %d" % ("%2ds  " % i if rounds > 1 else "", len(playing)))
        for x in playing:
            print("    %-24s %-32s pid %-7s mic %-4s %s (%.2f)"
                  % (x["name"][:24], x["bundle"][:32] or "(no bundle id)", x["pid"], "yes" if x["hears"] else "no",
                     "a call app" if x["call"] else "something else", x["confidence"]))
        if not playing and rounds == 1:
            print("    (nothing. Start a call or a video and run this again, or pass --watch 20 and start it then)")


def cmd_listen(a):
    import AppKit
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyProhibited)              # never take the user's focus
    bundle = a.bundle or next((x["bundle"] for x in audio_apps() if x["playing"] and x["call"]), None)
    if not bundle:
        raise SystemExit("no call app is playing; pass --bundle, or start the call first")
    print("capturing the audio of %s only, for %g s (our own audio %s)"
          % (bundle, a.seconds, "included" if a.mine else "left out"))
    print("screen recording allowed for this process beforehand: %s" % screen_permission())
    probe = MeetingAudio(include_mine=a.mine, log=lambda m: print("    " + m))
    t0 = time.time()
    probe.start(bundle)                      # never raises: a refusal is probe.error
    if not probe.ok:
        print("the stream did NOT start: %s" % probe.error)
        print("screen recording allowed now: %s" % screen_permission())
        print(ASK_THE_USER)
        return 1
    print("the stream started in %.2f s" % (time.time() - t0))
    c0, levels, frames, nblocks, times = cpu(), [], 0, 0, []
    while time.time() - t0 < a.seconds:
        pump(1.0)
        blocks = probe.take()
        frames += sum(len(s) for _t, s in blocks)
        nblocks += len(blocks)
        times += [(t, len(x)) for t, x in blocks]
        if blocks:
            levels.append(loudness(np.concatenate([s for _t, s in blocks])))
    wall = time.time() - t0
    probe.stop()
    used = cpu() - c0
    print("format: %s" % (probe.format or "unknown"))
    if probe.first_at:
        print("first audio after %.2f s" % (probe.first_at - probe.started_at))
    else:
        print("NO audio arrived, though the stream started.")
        print("That is what a permission granted but not yet in force looks like: if macOS asked you to")
        print("quit and reopen, or you have just allowed it in System Settings, run this command again.")
    # The honest rate is over the audio's own span: the wall clock also counts the
    # start-up and the tail still sitting in the buffer when the loop ends.
    span = (times[-1][0] + times[-1][1] / float(probe.rate) - times[0][0]) if len(times) > 1 else wall
    print("%d samples over %.1f s of audio: %.0f/s against %d/s asked (%.1f%% off), %.0f blocks a second"
          % (frames, span, frames / max(span, 1e-6), probe.rate,
             abs(frames / max(span, 1e-6) - probe.rate) / probe.rate * 100, nblocks / max(wall, 1)))
    print("CPU %.1f%% of one core" % (used / wall * 100))
    print("loudness per second, dB below full scale (-99 is silence):")
    print("    " + (" ".join("%.0f" % l if l is not None else "-" for l in levels) or "(nothing arrived)"))
    print("screen recording allowed after the run: %s" % screen_permission())
    print(ASK_THE_USER)


def cmd_both(a):
    """The call's audio and the microphone at once: how much of the call does the mic
    hear as well? That is the doubling on open speakers."""
    import AppKit
    import sounddevice as sd
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyProhibited)
    bundle = a.bundle or next((x["bundle"] for x in audio_apps() if x["playing"] and x["call"]), None)
    if not bundle:
        raise SystemExit("no call app is playing; pass --bundle, or start the call first")
    probe = MeetingAudio(log=lambda m: print("    " + m))
    mic_blocks, mic_lock = [], threading.Lock()

    def on_mic(indata, _frames, _t, _status):
        with mic_lock:
            mic_blocks.append((time.time(), indata[:, 0].copy()))

    print("the call's audio (%s) and the microphone together, for %g s" % (bundle, a.seconds))
    probe.start(bundle)
    if not probe.ok:
        raise SystemExit("the stream did not start: %s" % probe.error)
    stream = sd.InputStream(channels=1, samplerate=16000, blocksize=1600, callback=on_mic)
    stream.start()
    t0 = time.time()
    rows = []
    while time.time() - t0 < a.seconds:
        pump(1.0)
        meet = np.concatenate([s for _t, s in probe.take()] or [np.zeros(1, np.float32)])
        with mic_lock:
            mine = np.concatenate([s for _t, s in mic_blocks] or [np.zeros(1, np.float32)])
            mic_blocks.clear()
        rows.append((loudness(meet), loudness(mine), meet, mine))
    stream.stop()
    stream.close()
    probe.stop()
    print("per second: the call, then the microphone, dB below full scale")
    for i, (m, k, _a1, _a2) in enumerate(rows):
        print("    %2ds  call %6s   mic %6s" % (i, m, k))
    # How much of the call is in the mic: the best correlation of their envelopes,
    # and the lag at which it happens. High correlation on speakers, low on headphones.
    meet = np.concatenate([r[2] for r in rows])
    mine = np.concatenate([r[3] for r in rows])
    n = min(len(meet), len(mine))
    if n > 16000:
        def envelope(x):
            w = 160                                        # 10 ms
            e = np.sqrt(np.convolve(np.square(x[:n], dtype=np.float64), np.ones(w) / w, "same"))
            return (e - e.mean()) / (e.std() or 1)
        a1, a2 = envelope(meet), envelope(mine)
        lags = range(-4800, 4801, 160)                     # ±300 ms, 10 ms steps
        best = max(((float(np.corrcoef(a1[max(0, l):n + min(0, l)], a2[max(0, -l):n - max(0, l)])[0, 1]), l)
                    for l in lags), key=lambda x: x[0] if x[0] == x[0] else -1)
        print("the mic hears the call: best match %.2f at %+d ms (1.0 means the same sound, 0 means unrelated)"
              % (best[0], best[1] / 16.0))
        print("    on open speakers expect a high number: both channels then carry the same voices.")
    else:
        print("too little audio to compare; was anything playing?")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_apps = sub.add_parser("apps")
    ap_apps.add_argument("--watch", type=float, default=1, metavar="SECONDS",
                         help="keep looking once a second, so you can start something and see it appear")
    ap_apps.set_defaults(func=cmd_apps)
    for name, fn in (("listen", cmd_listen), ("both", cmd_both)):
        p = sub.add_parser(name)
        p.add_argument("--bundle")
        p.add_argument("--seconds", type=float, default=20.0)
        if name == "listen":
            p.add_argument("--mine", action="store_true", help="include our own audio (to prove it is excluded)")
        p.set_defaults(func=fn)
    a = ap.parse_args()
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
