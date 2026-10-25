"""Settings' Test recording, the meeting's half.

In Automatic and Meeting audio only, the test recorded the mic alone, so the
meeting's half was never tested. Now `standby selftest` also records the call
that would be recorded, for the same few seconds, and transcribes it, in the
mic channel's own printed format, so the page needs no change: read here
through ui.mic_test_state, exactly as the page gets it. No call playing, no
permission, silence, no meeting audio at all: one problem line each. Fakes
only: the capture, the transcriber."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import io, json, os, sys, tempfile, threading, time, types
from contextlib import redirect_stdout
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbtest-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


sys.modules["sounddevice"] = types.SimpleNamespace(
    query_devices=lambda i=None: [{"name": "MacBook Pro Microphone", "max_input_channels": 1}] if i is None
    else {"name": "MacBook Pro Microphone", "max_input_channels": 1},
    default=types.SimpleNamespace(device=[0, 0]),
    rec=lambda n, **k: np.full((n, 1), 0.2, "float32"), wait=lambda: None)
from importlib.machinery import SourceFileLoader  # noqa: E402
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
sb.CONFIG = root / "config.json"
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "auto"}))
sb.load_model = lambda name: None


class Script(object):
    """Says "hello from the call" for the call's audio, "testing my mic" for the mic's."""
    def __init__(self, *a, **k): self.audio = []
    def feed(self, b): self.audio.append(np.asarray(b))
    def drain(self, force=False):
        a = np.concatenate(self.audio) if self.audio else np.zeros(0)
        if not len(a) or float(np.abs(a).max()) < 1e-4:
            return []
        return [(0.5, 1.5, "hello from the call" if abs(float(a[0]) - 0.3) < 1e-6 else "testing my mic")]
    def rotate(self): return []
    def _close(self): pass


sb.UtteranceTranscriber = Script
ZOOM = {"name": "Zoom", "bundle": "us.zoom.xos", "pid": 1, "playing": True, "call": True, "hears": True,
        "confidence": 0.95}


class Capture(object):
    """The agreed interface; level is what it plays (0: a silent call)."""
    apps_now, refuse, level, started = [], None, 0.3, []

    def __init__(self):
        self.ok, self.error, self.reason, self._run = False, None, None, False

    def apps(self):
        return [dict(a) for a in Capture.apps_now]

    def start(self, target, feed):
        Capture.started.append(target)
        if Capture.refuse:
            self.ok, self.error, self.reason = False, "SCStream refused (-3801)", Capture.refuse
            return
        self.ok, self._run, t0 = True, True, time.monotonic()

        def go():
            k = 0
            while self._run:
                time.sleep(0.05)                    # real time on purpose: the capture's own block rate
                if self._run:
                    feed(np.full(800, Capture.level, "float32"), t0 + k * 0.05)
                    k += 1
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self._run, self.ok = False, False


sb.MEETING_AUDIO = Capture


def run(seconds=1):
    lines = []
    sb.selftest_meeting(seconds, None, out=lines.append)
    return lines


Capture.apps_now = [dict(ZOOM)]
lines = run()
check("a call playing: its own channel, recorded and transcribed like the mic",
      lines[1].startswith("--- Others (device Zoom): recording 1s") and "  00:00  hello from the call" in lines
      and any(l.startswith("  peak level: 0.3") for l in lines), lines)
Capture.apps_now = []
lines = run()
check("no call playing: the channel, no level, and what to do", lines[1].startswith("--- Others (device ): recording")
      and lines[-1] == "  " + sb.NO_CALL, lines)
Capture.apps_now, Capture.refuse = [dict(ZOOM)], "Screen Recording is not allowed"
lines = run()
check("no permission: the capture's own reason, as it says it", lines[-1] == "  Screen Recording is not allowed", lines)
Capture.refuse, Capture.level = None, 0.0
lines = run()
check("a call with nobody speaking: says so", lines[-1].startswith("  SILENCE from Zoom"), lines)
Capture.level = 0.3
CHROME_MEET = {"name": "Google Chrome", "bundle": "com.google.Chrome", "pid": 2, "playing": True, "call": False,
               "hears": True, "confidence": 0.7}
Capture.apps_now, Capture.started = [dict(CHROME_MEET)], []
lines = run()
check("a Meet call in a browser: tested",
      Capture.started == ["com.google.Chrome"] and "  00:00  hello from the call" in lines, (Capture.started, lines))
Capture.apps_now, Capture.started = [dict(CHROME_MEET, hears=False, confidence=0.3)], []
lines = run()
check("  a video in a browser: tested too (a meeting records whatever the Mac plays)",
      Capture.started == ["com.google.Chrome"] and "  00:00  hello from the call" in lines, (Capture.started, lines))
sb.MEETING_AUDIO = lambda: None
lines = run()
check("no meeting audio on this Mac: says so", lines[-1] == "  Meeting audio isn't available on this Mac.", lines)
sb.MEETING_AUDIO = Capture
Capture.apps_now = [dict(ZOOM)]


def selftest(mode):
    buf = io.StringIO()
    with redirect_stdout(buf):
        sb.cmd_selftest(types.SimpleNamespace(mode=mode, seconds=1))
    return buf.getvalue()


auto = selftest("auto")
check("Automatic: the mic, then the meeting", auto.index("--- Room") < auto.index("--- Others"), auto)
only = selftest("meeting")
check("Meeting audio only: the meeting alone, no mic", "--- Others" in only and "--- Room" not in only, only)
check("Mic only: the mic alone", "--- Others" not in selftest("mic"))

# What the page gets: the same parser, from the same log.
import transport  # noqa: E402,F401
ui = SourceFileLoader("ui", "mac/ui.py").load_module()
(root / "mic-test.log").write_text(auto)
ui.MICTEST["state"] = "done"
st = ui.mic_test_state()
chans = {c["channel"]: c for c in st["channels"]}
check("the page gets both: the mic, and the meeting as its own channel, with level and words",
      set(chans) == {"Room", "Others"} and chans["Others"]["device"] == "Zoom" and chans["Others"]["peak"] > 0
      and chans["Others"]["heard"] == ["hello from the call"] and not chans["Others"]["problem"], st)
Capture.apps_now = []
(root / "mic-test.log").write_text(selftest("auto"))
o = {c["channel"]: c for c in ui.mic_test_state()["channels"]}["Others"]
check("  no call: the meeting's channel with peak 0 and the one line to show",
      o["peak"] == 0.0 and o["problem"] == [sb.NO_CALL] and not o["heard"], o)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
