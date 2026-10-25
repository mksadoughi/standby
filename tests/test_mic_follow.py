"""The mic that is actually in use, and a recorder that stays connected.

Fakes all the way: Core Audio (what is connected, the system's default, a call
holding a mic), PortAudio (whose device list is stale until it starts again,
as the real one), and streams that deliver at real time, stall, or refuse to
open. A real run_session follows the Mac: AirPods in a call are switched to in
the same recording, under a second, with the gap filled so the clock stays
true; AirPods with no call are not (asked about later); a virtual default is
ignored; a stalled stream and a mic that will not open are opened again; voice
mode's engine moves to the new mic; a pinned mic is honoured while connected.
And choose_mic, the policy, on its own."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbmic-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
import devices as dv  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


# Waits are generous: five people run these suites at once, and a loaded Mac
# makes every real-time step slower. Nothing waits the whole time when it passes.
PATIENCE = 30


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- the policy alone
MAC = dv.Device("BuiltInMicrophoneDevice", "MacBook Pro Microphone", dv.BUILT_IN)
POD = dv.Device("AA-BB:input", "AirPods Pro", dv.BLUETOOTH)
ZOOM = dv.Device("zoom.us.zoomaudiodevice.001", "ZoomAudioDevice", dv.VIRTUAL)
YETI = dv.Device("usb-yeti", "Yeti Stereo Microphone", dv.USB)
busy = set()
use = lambda d: d.uid in busy
pick = lambda inputs, default, pinned="", current=None, choices=None: dv.choose_mic(
    inputs, default, pinned, current, choices or {}, use)

check("policy: Automatic follows the Mac's default", pick([MAC, YETI], YETI)[0] == YETI)
check("  a virtual default (Zoom's) is never a mic: the one recording stays",
      pick([MAC, ZOOM, YETI], ZOOM, current=YETI)[0] == YETI)
check("  nothing recording yet: the built-in mic first", pick([ZOOM, YETI, MAC], ZOOM)[0] == MAC)
check("  pinned and connected: that one, whatever the default", pick([MAC, YETI], MAC, pinned="Yeti Stereo Microphone")[0] == YETI)
check("  pinned by part of its name too", pick([MAC, YETI], MAC, pinned="Yeti")[0] == YETI)
m, why, ask = pick([MAC], MAC, pinned="Yeti")
check("  pinned and gone: the Mac's default, and it says why", m == MAC and "Yeti is not connected" in why, why)
m, why, ask = pick([MAC, POD], POD, current=MAC)
check("AirPods the default, no call: kept on the MacBook mic, and they are to be asked about",
      m == MAC and ask == POD and "call quality" in why, (m, why, ask))
busy.add(POD.uid)
m, why, ask = pick([MAC, POD], POD, current=MAC)
check("  a call has their mic open: switched to, no question", m == POD and ask is None and "a call" in why, why)
busy.clear()
check("  answered 'use': switched to", pick([MAC, POD], POD, current=MAC, choices={POD.uid: "use"})[:3:2] == (POD, None))
m, why, ask = pick([MAC, POD], POD, current=MAC, choices={POD.uid: "keep"})
check("  answered 'keep': not switched, not asked again", m == MAC and ask is None, (m, ask))
check("  already recording from them: kept (no flip back and forth)", pick([MAC, POD], POD, current=POD)[0] == POD)
check("  the only mic there is: used", pick([POD], POD)[0] == POD)
check("  pinned AirPods: the user's choice, no question", pick([MAC, POD], MAC, pinned="AirPods")[:3:2] == (POD, None))
check("no mic at all: none, and why", pick([ZOOM], ZOOM)[0] is None and pick([], None)[1] == "no mic connected")
# Headphones, for the echo check and a call's voice: AirPods list their mic as a Bluetooth
# input of its own, with the output's address ("AA-BB:output", "AA-BB:input").
POD_OUT = dv.Device("AA-BB:output", "AirPods Pro", dv.BLUETOOTH)
SPEAKER_OUT = dv.Device("CC-DD:output", "JBL Flip", dv.BLUETOOTH)
check("a Bluetooth output with its own mic among the inputs: a headset (no echo)",
      dv.headset_mic(POD_OUT, [MAC, POD]) == POD and dv.headset_mic(POD_OUT, [MAC]) is None)
check("  a Bluetooth speaker with no mic of its own, or a built-in output: not one",
      dv.headset_mic(SPEAKER_OUT, [MAC, POD]) is None
      and dv.headset_mic(dv.Device("BuiltInSpeakerDevice", "MacBook Pro Speakers", dv.BUILT_IN), [MAC, POD]) is None)


# ---------------------------------------------------------------- fake Core Audio and PortAudio
class System(object):
    """What is really connected (Core Audio sees it at once)."""
    inputs = [MAC, ZOOM]
    default = MAC
    busy = set()
    listener = None


class FakeDevices(object):
    def inputs(self): return list(System.inputs)
    def default_input(self): return System.default
    def in_use(self, d): return d.uid in System.busy
    def watch(self, on_change):
        System.listener = on_change
        return True


def plug(*inputs, default=None):
    System.inputs = list(inputs)
    System.default = default or inputs[0]
    if System.listener:
        System.listener()


PA = {"list": [], "inits": 0, "refuse": set()}


def pa_init():
    PA["list"] = [{"name": d.name, "max_input_channels": 1} for d in System.inputs]
    PA["inits"] += 1


pa_init()
streams = []


class FakeInput(object):
    """A PortAudio stream: blocks at real time, from its own thread, until stopped or stalled."""
    def __init__(self, device=None, callback=None, blocksize=8000, **kw):
        name = PA["list"][device]["name"]
        if name in PA["refuse"] or name not in [d.name for d in System.inputs]:
            raise RuntimeError("Error opening InputStream: Internal PortAudio error [PaErrorCode -9986]")
        self.name, self.cb, self.n, self.run, self.closed, self.stalled = name, callback, blocksize, False, False, False
        streams.append(self)

    def start(self):
        self.run = True

        def go():                                    # a block once it has been captured, as a real one
            while self.run:
                time.sleep(self.n / 16000.0)         # real time on purpose: this IS the capture rate
                if self.run and not self.stalled:
                    self.cb(np.zeros((self.n, 1), "float32"), self.n, None, None)
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self.run = False
    def close(self): self.closed = True


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
sb.STALL = 1.0


class Script(object):
    def __init__(self, *a, **k): pass
    def feed(self, b): pass
    def drain(self, force=False): return []
    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Script
engines = []
# How many times the recorder has asked the policy what to record from. A check that
# nothing changed waits for a look to have happened, instead of sleeping and hoping.
looks = [0]
_decide = sb.MicFollow.decide
sb.MicFollow.decide = lambda self, current: (looks.__setitem__(0, looks[0] + 1), _decide(self, current))[1]


def looked(n=1):
    """Wait until the recorder has looked n more times (with its mics as they are now)."""
    was = looks[0]
    return wait(lambda: looks[0] >= was + n)


class FakeEngine(object):
    aec = True
    def __init__(self):
        self.mic, self.starts = None, []
        engines.append(self)
    def start_mic(self, device, feed):
        self.mic = (device, feed)
        self.starts.append(device)

        def go():
            while self.mic is not None and self.mic[0] == device:
                time.sleep(0.5)                      # as above: the engine's own block rate
                if self.mic is not None and self.mic[0] == device:
                    feed(np.zeros(8000, "float32"))
        threading.Thread(target=go, daemon=True).start()
    def stop_mic(self): self.mic = None
    def say(self, text): pass
    playing = False
    def halt(self): pass


sb.VOICE_BACKEND = FakeEngine
# The user's config today: the MacBook mic pinned by the old setup.
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False,
                                 "mic_device": "MacBook Pro Microphone", "voice_mode": {"duplex": True}}))


def start_session():
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("meet",), kwargs={"no_claude": True}, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    return th, root / "sessions" / (root / "daemon.ready").read_text().strip()


th, sdir = start_session()
live = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
src = lambda: (live().get("sources") or [{}])[0]
cur = lambda: [s for s in streams if s.run]
cfg = json.loads(sb.CONFIG.read_text())
check("the pinned MacBook mic: Automatic now, once, and said so",
      "mic_device" not in cfg and cfg.get("mic_automatic") is True
      and any("was pinned; Automatic now" in m for m in logged), cfg)
check("recording from the Mac's mic", wait(lambda: [s.name for s in cur()] == ["MacBook Pro Microphone"]),
      [s.name for s in cur()])
check("  the app is told: one mic source, live", wait(lambda: src().get("name") == "MacBook Pro Microphone"
                                                       and src().get("ok") is True and src().get("kind") == "mic"), live())

# AirPods connect, in a Zoom call: macOS makes them the default, the call has their mic.
looked()
inits = PA["inits"]
System.busy = {POD.uid}
t0 = time.time()
plug(MAC, POD, ZOOM, default=POD)
check("AirPods in a call: switched to them, in the same recording",       # the log line comes just after
      wait(lambda: [s.name for s in cur()] == ["AirPods Pro"] and any("now AirPods Pro" in m for m in logged), 3),
      [s.name for s in cur()])
gap = [m for m in logged if "now AirPods Pro" in m]
check("  the gap is under a second, and PortAudio's list was refreshed first",
      gap and float(gap[-1].rsplit("gap ", 1)[1].rstrip("s")) < 1.0 and PA["inits"] == inits + 1, gap)
check("  the app sees the new name", wait(lambda: src().get("name") == "AirPods Pro"), live())
check("  the old stream closed: one mic at a time", [s.name for s in cur()] == ["AirPods Pro"]
      and all(s.closed for s in streams if s.name == "MacBook Pro Microphone"))

counted = [m for m in logged if "now AirPods Pro" in m]
check("  (the switch is logged with why: the call has their mic)", counted and "a call has it open" in counted[-1], counted)

# AirPods go: back to the MacBook mic.
System.busy = set()
plug(MAC, ZOOM, default=MAC)
check("AirPods disconnected: the MacBook mic again", wait(lambda: [s.name for s in cur()] == ["MacBook Pro Microphone"]),
      [s.name for s in cur()])

# AirPods again, no call: kept on the MacBook mic, told once.
plug(MAC, POD, ZOOM, default=POD)
looked(2)                                    # it has seen them, twice, and still did not switch
check("AirPods with no call: the MacBook mic stays (their sound would drop to call quality)",
      [s.name for s in cur()] == ["MacBook Pro Microphone"], [s.name for s in cur()])
check("  logged once, to be asked about", sum("would drop to call quality (not asked yet)" in m for m in logged) == 1,
      [m for m in logged if "call quality" in m])
(root / "capture-choices.json").write_text(json.dumps({"mic": {POD.uid: "use"}}))
check("  answered 'use': switched to them at the next look", wait(lambda: [s.name for s in cur()] == ["AirPods Pro"]),
      [s.name for s in cur()])

# Zoom's virtual device made the default: never a mic.
plug(MAC, POD, ZOOM, default=ZOOM)
looked(2)
check("a virtual default (ZoomAudioDevice): ignored, the AirPods stay", [s.name for s in cur()] == ["AirPods Pro"],
      [s.name for s in cur()])

# The stream goes quiet (a device gone without a word): opened again.
stalled = cur()[0]
stalled.stalled = True
check("a stream that stalls: opened again", wait(lambda: any("no audio from AirPods Pro" in m for m in logged))
      and wait(lambda: cur() and cur()[0] is not stalled), [m for m in logged if "no audio" in m])

# A mic that will not open: reconnecting, with back-off, then back.
PA["refuse"].add("MacBook Pro Microphone")
System.busy = set()
plug(MAC, ZOOM, default=MAC)
check("a mic that will not open: reconnecting, and the app says so",
      wait(lambda: src().get("state") == "reconnecting" and src().get("ok") is False), live())
check("  the session goes on", th.is_alive() and wait(lambda: any("trying again in" in m for m in logged)))
PA["refuse"].clear()
check("  it opens on a retry, live again", wait(lambda: src().get("state") == "live" and len(cur()) == 1
                                                 and any("recording again from MacBook Pro Microphone" in m for m in logged)),
      live())

# Voice mode on: the echo-cancelling engine has the mic, and moves with it.
(sdir / "dictate").write_text("voice 7\n")
check("voice mode: the engine has the mic, no PortAudio stream", wait(lambda: engines and engines[0].mic is not None)
      and wait(lambda: not cur()), (engines and engines[0].mic, [s.name for s in cur()]))
System.busy = {POD.uid}
plug(MAC, POD, ZOOM, default=POD)
check("  AirPods in a call meanwhile: the engine moves to them, still no second stream",
      wait(lambda: engines[0].mic is not None and engines[0].mic[0] == "AirPods Pro") and not cur(),
      (engines[0].starts, [s.name for s in cur()]))
(sdir / "dictate").write_text("off 7\n")
check("  voice mode off: the plain stream, on the AirPods", wait(lambda: [s.name for s in cur()] == ["AirPods Pro"]),
      [s.name for s in cur()])
check("the app's listening bar still has its level and the source",
      wait(lambda: "level" in live() and src().get("name") == "AirPods Pro"), live())
sb._stop.set()
th.join(timeout=PATIENCE)
check("the session ends cleanly", not th.is_alive())
first = sdir

# A mic picked in Settings after the migration stays pinned.
cfg = json.loads(sb.CONFIG.read_text())
cfg["mic_device"] = "Yeti Stereo Microphone"
sb.CONFIG.write_text(json.dumps(cfg))
System.busy = set()
plug(MAC, YETI, ZOOM, default=MAC)
pa_init()
th, sdir = start_session()
check("a mic picked in Settings: recorded from, the migration done already",
      wait(lambda: [s.name for s in cur()] == ["Yeti Stereo Microphone"])
      and json.loads(sb.CONFIG.read_text()).get("mic_device") == "Yeti Stereo Microphone", [s.name for s in cur()])
plug(MAC, ZOOM, default=MAC)
check("  unplugged: the Mac's default meanwhile", wait(lambda: [s.name for s in cur()] == ["MacBook Pro Microphone"]
                                                       and any("Yeti Stereo Microphone is not connected" in m for m in logged)),
      [s.name for s in cur()])
plug(MAC, YETI, ZOOM, default=MAC)
check("  plugged back in: back to it", wait(lambda: [s.name for s in cur()] == ["Yeti Stereo Microphone"]),
      [s.name for s in cur()])
sb._stop.set()
th.join(timeout=PATIENCE)

import wave  # noqa: E402


def lengths(d):
    w = wave.open(str(d / "room.wav"))
    runs = [json.loads(l) for l in (d / "runs.jsonl").read_text().splitlines()]
    starts = [r["start"] for r in runs if "start" in r]
    ends = [r["end"] for r in runs if "end" in r]
    return round(w.getnframes() / float(w.getframerate()), 2), round(ends[-1] - starts[0], 2)


for name, d in (("the first session (switches, a stall, seconds reconnecting)", first),
                ("the second (unplugged and back)", sdir)):
    secs, span = lengths(d)
    check("recording as long as %s: gaps filled with silence" % name, abs(secs - span) < 1.0, (secs, span))
# The Mac asleep mid-meeting: an hour is skipped over, not filled with an hour of silence.
gdir = root / "gap"
gdir.mkdir()
ch = sb.Channel("Room", None, None, lambda *a: None, gdir / "room.wav", 300)
ch._stream, ch.base = Script(), 5.0
ch._wav = wave.open(str(gdir / "room.wav"), "wb")
ch._wav.setnchannels(1)
ch._wav.setsampwidth(2)
ch._wav.setframerate(16000)
ch.clock0 = time.time() - 10.0
ch._feed(np.zeros(160000, "float32"))          # 10 s recorded
ch.pump()
ch.clock0 -= 3600                              # then an hour with no audio
ch._fill()
check("the Mac asleep an hour: no hour of silence queued, later lines moved on by it",
      ch.audio.qsize() == 0 and abs(ch.offset - 3600) < 1.0, (ch.audio.qsize(), ch.offset))
check("  the recording goes on in a new wav, placed where it resumes",
      (gdir / "room.2.wav").exists() and abs(float((gdir / "room.2.wav.offset").read_text()) - (5 + 3600 + 10)) < 1.0,
      sorted(p.name for p in gdir.iterdir()))
ch.clock0 -= 1.0                               # a second's gap (a switch): filled, as before
ch._fill()
queued = sum(len(b) for b in list(ch.audio.queue))
check("  a short gap after it is still filled with silence", 15000 <= queued <= 17000, queued)
ch.close()

# The app's side: the sources for the page, the migration, a pick that stays.
import transport  # noqa: E402
c = {"mic_device": "MacBook Pro Microphone"}
check("migration: a pin from before goes, once", transport.migrate_mic(c) and "mic_device" not in c
      and not transport.migrate_mic(dict(c, mic_device="Yeti")))
ui = SourceFileLoader("ui", "mac/ui.py").load_module()
(first / "live.json").write_text(json.dumps({"at": time.time(), "sources": [
    {"kind": "mic", "name": "AirPods Pro", "level": 0.2, "ok": True, "state": "live"}]}))
info = ui.session_info(first.name, live=first.name)
check("session info: the live session's sources, without levels (the page notes a switch or a drop)",
      info["sources"] == [{"kind": "mic", "name": "AirPods Pro", "ok": True, "state": "live"}], info.get("sources"))
check("  an ended session: none", ui.session_info(first.name, live="other")["sources"] == [])
r = ui.set_settings({"mic_device": "Yeti Stereo Microphone"})
saved = json.loads(sb.CONFIG.read_text())
check("a mic picked in Settings is the user's: kept, never migrated",
      saved.get("mic_device") == "Yeti Stereo Microphone" and saved.get("mic_automatic") is True
      and not transport.migrate_mic(saved), saved)
check("  Settings without devices (a test): no current mic named, the list from PortAudio",
      ui.mic_default() == "" and "MacBook Pro Microphone" in ui.list_mics(), ui.list_mics())
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
