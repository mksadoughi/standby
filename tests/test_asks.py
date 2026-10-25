"""Asking when it is not clear what to record (the user's idea).

The clear cases just happen; when it is not clear, one calm question at a time,
never a popup: the recorder writes it (capture.ask.json), the app shows it (in
the session, and to the companion), the answer comes back (POST
/api/capture/answer), the recorder acts on it at once, and keeps it per device
and per app, so it is never asked twice. A question whose case goes away is
withdrawn. Fakes only: Core Audio, the mic, a call's capture; a real
run_session and the app's own functions."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbasks-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
import devices as dv  # noqa: E402

fails = []
PATIENCE = 30           # generous: these suites run five at a time on one Mac


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


MAC = dv.Device("BuiltInMicrophoneDevice", "MacBook Pro Microphone", dv.BUILT_IN)
POD = dv.Device("AA-BB:input", "AirPods Pro", dv.BLUETOOTH)
BOSE = dv.Device("CC-DD:input", "Bose QC Headphones", dv.BLUETOOTH)

# ---------------------------------------------------------------- the policy edge, alone
m, why, ask = dv.choose_mic([MAC, POD, BOSE], BOSE, "", POD, {POD.uid: "use"}, lambda d: False)
check("on AirPods the user said yes to, another headset becoming the default: the AirPods stay",
      m == POD and ask == BOSE, (m, why, ask))
q = dv.ask_for("mic", POD)
check("the question about a mic: one line, Switch or Keep, and what it is about",
      q["id"] == "mic:AA-BB:input" and q["text"].startswith("AirPods Pro connected: record from their mic?")
      and [a["value"] for a in q["actions"]] == ["use", "keep"] and q["about"] == {"kind": "mic", "name": "AirPods Pro"},
      q)


class System(object):
    inputs, default, listener = [MAC], MAC, None


class FakeDevices(object):
    def inputs(self): return list(System.inputs)
    def default_input(self): return System.default
    def in_use(self, d): return False                  # no call has a mic: every Bluetooth one is a question
    def output_leaks(self): return False
    def watch(self, on_change):
        System.listener = on_change
        return True


def plug(*inputs, default=None):
    System.inputs, System.default = list(inputs), default or inputs[0]
    pa_init()
    if System.listener:
        System.listener()


PA = {"list": []}


def pa_init():
    PA["list"] = [{"name": d.name, "max_input_channels": 1} for d in System.inputs]


pa_init()
streams = []


class FakeInput(object):
    def __init__(self, device=None, callback=None, blocksize=8000, **kw):
        self.name, self.cb, self.n, self.run = PA["list"][device]["name"], callback, blocksize, False
        streams.append(self)

    def start(self):
        self.run = True

        def go():
            while self.run:
                time.sleep(self.n / 16000.0)        # real time on purpose: the mic's own block rate
                if self.run:
                    self.cb(np.zeros((self.n, 1), "float32"), self.n, None, None)
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self.run = False
    def close(self): pass


sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: PA["list"] if i is None else PA["list"][i],
    default=types.SimpleNamespace(device=[0, 0]), _terminate=lambda: None, _initialize=pa_init)
from importlib.machinery import SourceFileLoader  # noqa: E402
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
logged = []
sb.log = logged.append
sb.SESSIONS, sb.CONFIG = root / "sessions", root / "config.json"
sb.load_model = lambda name: None
sb.DEVICES = FakeDevices()


class Script(object):
    def __init__(self, *a, **k): pass
    def feed(self, b): pass
    def drain(self, force=False): return []
    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Script
mic_looks = [0]                                       # the recorder's looks at which mic to record from
_decide = sb.MicFollow.decide
sb.MicFollow.decide = lambda self, current: (mic_looks.__setitem__(0, mic_looks[0] + 1), _decide(self, current))[1]
UNKNOWN = {"name": "Whereby", "bundle": "com.whereby.app", "pid": 9, "playing": True, "call": False,
           "hears": True, "confidence": 0.7}          # an app we do not know, playing with the mic open


class Call(object):
    apps_now, looks = [], 0

    def __init__(self):
        self.ok, self.error, self.name, self._run = False, None, "", False

    def apps(self):
        Call.looks += 1
        return [dict(a) for a in Call.apps_now]

    def start(self, target, feed):
        self.ok, self._run, t0 = True, True, time.monotonic()

        def go():
            k = 0
            while self._run:
                time.sleep(0.1)                     # real time on purpose: the capture's own block rate
                if self._run:
                    feed(np.zeros(1600, "float32"), t0 + k * 0.1)
                    k += 1
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self._run, self.ok = False, False


sb.MEETING_AUDIO = Call
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "auto", "speakers": False,
                                 "mic_automatic": True, "capture_automatic": True}))
ui = SourceFileLoader("ui", "mac/ui.py").load_module()


def start_session():
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("meet",), kwargs={"no_claude": True}, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    sid = (root / "daemon.ready").read_text().strip()
    ui._live_now = lambda ttl=0.5: sid               # the recorder is a thread here, not a process to find
    return th, root / "sessions" / sid, sid


def end_session(th):
    sb._stop.set()
    th.join(timeout=PATIENCE)
    wait(lambda: not (root / "daemon.ready").exists())


th, sdir, sid = start_session()
asked = lambda: json.loads((sdir / "capture.ask.json").read_text()) if (sdir / "capture.ask.json").exists() else None
cur = lambda: [s.name for s in streams if s.run]
choices = lambda: json.loads((root / "capture-choices.json").read_text()) if (root / "capture-choices.json").exists() else {}
check("nothing unclear: no question", wait(lambda: cur() == ["MacBook Pro Microphone"]) and asked() is None)

plug(MAC, POD, default=POD)
check("AirPods connected, no call has their mic: asked, in one line",
      wait(lambda: asked() is not None and asked()["id"] == "mic:" + POD.uid), asked())
check("  meanwhile the MacBook mic records (nothing changes until the user says)", cur() == ["MacBook Pro Microphone"])
info = ui.session_info(sid, live=sid)
check("  the app's session has the question, as the page shows it",
      info["ask"] and info["ask"]["text"] == q["text"] and info["ask"]["actions"] == q["actions"], info.get("ask"))
check("  not on the orb: it is the meeting's, shown in its window (the orb is for calls only)", "ask" not in ui.companion_state())

Call.apps_now = [dict(UNKNOWN)]
n = Call.looks
wait(lambda: Call.looks >= n + 2)
check("an app playing meanwhile (Whereby, its mic open): no question of its own, the mic's stays", asked()["id"] == q["id"], asked())
check("an answer to another question: refused, nothing done",
      ui.capture_answer({"id": "mic:nope", "value": "use"}) == {"error": "That question has gone."}
      and cur() == ["MacBook Pro Microphone"])
check("  an answer that is not one of its own: refused",
      ui.capture_answer({"id": q["id"], "value": "maybe"}) == {"error": "That is not one of its answers."})
check("Switch: accepted", ui.capture_answer({"id": q["id"], "value": "use"}) == {"ok": True})
check("  the recorder switches to the AirPods at once", wait(lambda: cur() == ["AirPods Pro"]), cur())
check("  and keeps the answer for that device", wait(lambda: choices().get("mic", {}).get(POD.uid) == "use"), choices())
check("  the log records the answer without copying the question",
      wait(lambda: "asked: capture choice kept for next time" in logged)
      and not any(q["text"] in m for m in logged))

check("Whereby, playing all along: joined as Others with no question (whatever the Mac plays is the meeting's)",
      wait(lambda: any("Whereby joins as Others" in m for m in logged)), [m for m in logged if "meeting:" in m][-2:])
check("  and no question left, nothing kept about it", wait(lambda: asked() is None)
      and "com.whereby.app" not in choices().get("apps", {}), (asked(), choices()))
Call.apps_now = []

# A question whose case goes away is withdrawn, and nothing is kept.
plug(MAC, POD, BOSE, default=BOSE)
check("another headset becoming the default: asked about it, the AirPods stay meanwhile",
      wait(lambda: asked() is not None and asked()["id"] == "mic:" + BOSE.uid) and cur() == ["AirPods Pro"],
      (asked(), cur()))
plug(MAC, POD, default=POD)
check("  unplugged before an answer: the question is withdrawn",
      wait(lambda: asked() is None) and any("withdrawn, no longer the case" in m for m in logged), asked())
check("  and nothing is kept about it", BOSE.uid not in choices().get("mic", {}), choices())

plug(MAC, POD, BOSE, default=BOSE)
wait(lambda: asked() is not None and asked()["id"] == "mic:" + BOSE.uid)
check("Keep this mic: accepted", ui.capture_answer({"id": "mic:" + BOSE.uid, "value": "keep"}) == {"ok": True})
check("  the AirPods stay", wait(lambda: choices().get("mic", {}).get(BOSE.uid) == "keep") and cur() == ["AirPods Pro"],
      cur())
was = len([m for m in logged if m.startswith("asking:")])
plug(MAC, POD, BOSE, default=BOSE)
n = mic_looks[0]
wait(lambda: mic_looks[0] >= n + 2)                  # it has looked, twice, with the headset the default
check("  and it is never asked about again", asked() is None
      and len([m for m in logged if m.startswith("asking:")]) == was,
      (asked(), [m for m in logged if m.startswith("asking:")][was:]))
check("an answered question never comes back, not even for a moment after the answer",
      sum(m.startswith("asking:") for m in logged) == 3,    # AirPods, Bose withdrawn, Bose kept
      [m for m in logged if m.startswith("asking:")])
end_session(th)
check("the session ends: no question left behind", not (sdir / "capture.ask.json").exists()
      and not (sdir / "capture.answer").exists())
check("  the app has nothing to show for an ended session", ui.session_info(sid, live="none")["ask"] is None)

# The next meeting: what was answered is not asked again.
plug(MAC, default=MAC)
th, sdir, sid = start_session()
wait(lambda: cur() == ["MacBook Pro Microphone"])
plug(MAC, POD, default=POD)
check("next meeting, the AirPods connect: switched to them without asking (answered before)",
      wait(lambda: cur() == ["AirPods Pro"]) and asked() is None, (cur(), asked()))

# Standby's own playing with the mic open: dictation's start cue (the user
# was asked "Record it?" at every dictation), and Jean's voice, which shows as Python. Never asked,
# never recorded, whatever listed them; a browser and a call app beside them are recorded as ever.
DICTATION = {"name": "Standby Dictation", "bundle": "dev.standby.dictation", "pid": 31, "playing": True,
             "call": False, "hears": True, "confidence": 0.7}               # no "own": the bundle id says it
JEAN = {"name": "Python", "bundle": "org.python.python", "pid": 32, "playing": True, "call": False,
        "hears": True, "confidence": 0.7, "own": True}
CHROME = {"name": "Google Chrome", "bundle": "com.google.Chrome", "pid": 33, "playing": True, "call": False,
          "hears": True, "confidence": 0.7}
ZOOM = {"name": "Zoom", "bundle": "us.zoom.xos", "pid": 34, "playing": True, "call": True, "hears": True,
        "confidence": 0.95}
SUSTAIN = sb.MeetingFollow.SUSTAIN
seen = len(logged)
Call.apps_now = [dict(DICTATION), dict(JEAN)]
n, t0 = Call.looks, time.time()
wait(lambda: Call.looks >= n + 3 and time.time() - t0 > SUSTAIN + 2)   # long enough to be asked about, if it were
check("Standby's own with the mic open (dictation's cue, Jean's voice): never asked, never recorded",
      asked() is None and not [m for m in logged[seen:] if m.startswith("asking:") or "joins as Others" in m],
      [m for m in logged[seen:] if "asking" in m or "meeting" in m])
Call.apps_now = [dict(DICTATION), dict(CHROME)]
check("  a browser beside it: recorded, and nothing asked",
      wait(lambda: any("Google Chrome joins as Others" in m for m in logged[seen:])) and asked() is None,
      [m for m in logged[seen:] if "meeting" in m][-3:])
Call.apps_now = [dict(DICTATION), dict(ZOOM)]
check("  and a call app beside it: recorded", wait(lambda: any("Zoom joins as Others" in m for m in logged[seen:])),
      [m for m in logged[seen:] if "meeting" in m][-3:])
Call.apps_now = []
end_session(th)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
