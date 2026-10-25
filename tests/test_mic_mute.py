"""Mute my mic for the meeting: the user's mic is closed while the call and the
rest go on, the session's clock runs on, nothing of the mic is heard or kept, and
events.jsonl gets a span for "Your mic was muted · 3 min". Talking to Claude while
muted is refused with a line. Fakes only: the mic, the call, Core Audio."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbmute-"))
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


# ---------------------------------------------------------------- the recorder, with fakes
MAC = dv.Device("BuiltInMicrophoneDevice", "MacBook Pro Microphone", dv.BUILT_IN)
USB = dv.Device("USBMic", "Blue Yeti", dv.USB)
now_default, changed = [MAC], []


class FakeDevices(object):
    def inputs(self): return [MAC, USB]
    def default_input(self): return now_default[0]
    def in_use(self, d): return False
    def watch(self, on_change):
        changed.append(on_change)
        return True
    def output_leaks(self): return False       # headphones: no echo check in the way


streams = {"open": 0, "started": 0, "stopped": 0}


class FakeInput(object):
    def __init__(self, device=None, callback=None, blocksize=8000, **kw):
        self.cb, self.n, self.run = callback, blocksize, False
        streams["open"] += 1

    def start(self):
        self.run = True
        streams["started"] += 1

        def go():
            while self.run:
                time.sleep(self.n / 16000.0)        # real time on purpose: the mic's own block rate
                if self.run:
                    self.cb(np.full((self.n, 1), 0.01, "float32"), self.n, None, None)
        threading.Thread(target=go, daemon=True).start()

    def stop(self):
        self.run = False
        streams["stopped"] += 1

    def close(self): pass


PA = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}, {"name": "Blue Yeti", "max_input_channels": 1}]
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
MARK = 0.25                                          # the call's audio, told from the mic's
fed = {"mic": 0, "meeting": 0}


class Script(object):
    """Counts what each stream is fed: the mic's, or the call's."""
    def __init__(self, *a, **k): self.kind = None
    def feed(self, b):
        if self.kind is None and len(b) and float(np.max(np.abs(b))) > 0:
            self.kind = "meeting" if abs(float(b[0]) - MARK) < 1e-6 else "mic"
        if self.kind is not None:
            fed[self.kind] += len(b)
    def drain(self, force=False): return []
    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Script


class Call(object):
    """The call's capture: Zoom playing, blocks at its rate."""
    def __init__(self):
        self.ok, self.error, self.name, self._run = False, None, "", False

    def apps(self):
        return [{"name": "Zoom", "bundle": "us.zoom.xos", "pid": 1, "playing": True, "call": True, "confidence": 0.95}]

    def start(self, target, feed):
        self.ok, self._run, t0 = True, True, time.monotonic()

        def go():
            k = 0
            while self._run:
                time.sleep(0.1)                     # real time on purpose: the capture's own block rate
                if self._run:
                    feed(np.full(1600, MARK, "float32"), t0 + k * 0.1)
                    k += 1
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self._run, self.ok = False, False


sb.MEETING_AUDIO = lambda: Call()
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "auto", "speakers": False,
                                 "mic_automatic": True, "capture_automatic": True}))
sb._stop.clear()
th = threading.Thread(target=sb.run_session, args=("mute",), kwargs={"no_claude": True}, daemon=True)
th.start()
check("a meeting starts, with the call", wait(lambda: (root / "daemon.ready").exists())
      and wait(lambda: any("Zoom joins as Others" in m for m in logged)), logged[-5:])
sdir = root / "sessions" / (root / "daemon.ready").read_text().strip()
began = json.loads((sdir / "runs.jsonl").read_text().splitlines()[0])["start"]     # the session clock's 0
live = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
spans = lambda: [r for r in (json.loads(l) for l in (sdir / "events.jsonl").read_text().splitlines() if l.strip())
                 if r.get("kind") == "mic_muted"] if (sdir / "events.jsonl").exists() else []
check("  both heard", wait(lambda: fed["mic"] > 0 and fed["meeting"] > 0), fed)
check("  the status says the mic is not muted", wait(lambda: live().get("mic_muted") is False), live())

(sdir / "mic").write_text("muted\n")
check("muted: the mic is closed (macOS's orange dot goes out)",
      wait(lambda: any("muted, " in m and "closed" in m for m in logged)) and streams["stopped"] >= 1, streams)
check("  a span opens in events.jsonl", wait(lambda: len(spans()) == 1 and spans()[0]["to"] is None), spans())
check("  the status says so, and the mic's source too",
      wait(lambda: live().get("mic_muted") is True
           and any(s.get("kind") == "mic" and s.get("muted") and s.get("state") == "muted" for s in live()["sources"])),
      live())
time.sleep(0.3)
mic_then, call_then = fed["mic"], fed["meeting"]
time.sleep(1.5)                                       # real time: three of the mic's blocks would have come
check("  nothing of the mic is heard while muted", fed["mic"] == mic_then, (fed["mic"], mic_then))
check("  the call goes on", fed["meeting"] > call_then, (fed["meeting"], call_then))
now_default[0] = USB                                  # a mic plugged in while muted, made the Mac's
changed[0]()
time.sleep(1.0)                                       # on purpose: a switch would have opened it in this time
check("  and a mic plugged in meanwhile is not opened while muted", streams["started"] == 1, streams)

(sdir / "mic").unlink()
check("unmuted: the mic opens again, the Mac's own now", wait(lambda: any(m.endswith(": unmuted") for m in logged))
      and wait(lambda: streams["started"] == 2) and any("Blue Yeti" in m for m in logged[-6:]), (streams, logged[-6:]))
check("  the span closes, with how long", wait(lambda: len(spans()) == 1 and spans()[0]["to"] is not None
                                                 and spans()[0]["to"] - spans()[0]["from"] >= 1.5), spans())
check("  logged in words", any(m.startswith("mic: unmuted after ") for m in logged))
check("  the mic heard again", wait(lambda: fed["mic"] > mic_then + 8000), fed)

(sdir / "mic").write_text("muted\n")
check("muted again, and the meeting ends while muted", wait(lambda: len(spans()) == 2))
sb._stop.set()
th.join(timeout=PATIENCE)
wait(lambda: not (root / "daemon.ready").exists())
ended = time.time()
check("  the span closes with the session", len(spans()) == 2 and spans()[1]["to"] is not None, spans())
import wave  # noqa: E402
with wave.open(str(sdir / "room.wav")) as w:
    kept = w.getnframes() / 16000.0
heard = sum(min(r["to"], 1e9) - r["from"] for r in spans())
check("the clock ran on: the mic's recording is as long as the meeting, the muted part silence (%.1fs of %.1fs)"
      % (kept, ended - began), abs(kept - (ended - began)) < 1.5, (kept, ended - began, heard))

# ---------------------------------------------------------------- the app's side
import socket  # noqa: E402
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
os.environ["STANDBY_UI_PORT"] = str(port)
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
ui.log = lambda m: None
sid = "meet-20261008-120000"
udir = ui.SESSIONS / sid
udir.mkdir(parents=True)
(udir / "meta.json").write_text(json.dumps({"started": time.time() - 60, "kind": "meeting"}))
live_sid = {"sid": sid}
ui.live_session = lambda: live_sid["sid"]
ui._live_now = lambda: live_sid["sid"]

check("the app: not muted to start with; the meeting is no orb's (the orb is for calls only)",
      ui.mic_muted(sid) is False and ui.companion_state()["live"] is None, ui.companion_state()["live"])
r = ui.set_mic(sid, True)
check("  mute: the recorder is told (sdir/mic)", r == {"ok": True, "mic_muted": True}
      and (udir / "mic").read_text().strip() == "muted", r)
check("  the app says so (/api/state's mic_muted, for the meeting's window)", ui.mic_muted(sid) is True)
(udir / "meta.json").write_text(json.dumps({"started": time.time() - 60, "kind": "talk"}))    # voice mode is a call's
r = ui.start_voice(sid)
(udir / "meta.json").write_text(json.dumps({"started": time.time() - 60, "kind": "meeting"}))
check("  a call's voice mode while muted: refused, with a line, nothing started",
      r.get("error") == ui.MUTED_LINE and r.get("mic_muted") is True and sid not in ui.VOICE, r)
r = ui.set_mic(sid, False)
check("  unmute", r == {"ok": True, "mic_muted": False} and not (udir / "mic").exists() and ui.mic_muted(sid) is False, r)
check("  unmuting twice is fine", ui.set_mic(sid, False) == {"ok": True, "mic_muted": False})
(udir / "dictate").write_text("on 5\n")
r = ui.set_mic(sid, True)
check("  muting while dictating: refused (talking to Claude needs the mic)", "error" in r and not (udir / "mic").exists(), r)
(udir / "dictate").write_text("off 5\n")
check("  not the live session: refused", "error" in ui.set_mic("meet-20261001-090000", True))
(udir / "meta.json").write_text(json.dumps({"started": time.time() - 60, "kind": "talk"}))
check("  a talk (all voice): refused", "error" in ui.set_mic(sid, True))
(udir / "meta.json").write_text(json.dumps({"started": time.time() - 60, "kind": "meeting"}))

# Over HTTP, as the companion calls it: POST /api/mic {id, muted}, and GET /api/state.
from http.server import ThreadingHTTPServer  # noqa: E402
import urllib.request  # noqa: E402
server = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % server.server_address[1]


def call(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=PATIENCE) as resp:
        return json.loads(resp.read())


r = call("/api/mic", {"id": sid, "muted": True})
check("POST /api/mic {id, muted}: muted", r == {"ok": True, "mic_muted": True}, r)
check("  GET /api/state says mic_muted", call("/api/state").get("mic_muted") is True)
check("  GET /api/companion has no meeting in it: the orb is for calls only", call("/api/companion")["live"] is None)
r = call("/api/mic", {"id": sid, "muted": False})
check("  and unmuted", r == {"ok": True, "mic_muted": False} and call("/api/state").get("mic_muted") is False, r)
try:
    call("/api/mic", {"id": "../../etc", "muted": True})
    bad = "accepted"
except urllib.error.HTTPError as exc:
    bad = exc.code
check("  a bad id: refused", bad == 404, bad)
server.shutdown()
print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
