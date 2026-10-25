"""Where each transcript line came from (the user's ask).

Each row carries "src", an id in a small per-session table (sources.json): a
device or an app, under one name, as it was when the line was spoken. A switch
mid-meeting is a new id, and a line being said when the mic switches keeps the
old one. The table is kept with the session (the app shows it for ended ones),
shipped to the agent like the speaker names, and the agent names the sources
once per [TRANSCRIPT] block, tagging a line only where its channel's source
changed. Rows from before have no src and read as they did. Fakes only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbsrc-"))
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


class System(object):
    inputs, default, busy, listener = [MAC], MAC, set(), None


class FakeDevices(object):
    def inputs(self): return list(System.inputs)
    def default_input(self): return System.default
    def in_use(self, d): return d.uid in System.busy
    def output_leaks(self): return False             # headphones: no echo check holding lines
    def watch(self, on_change):
        System.listener = on_change
        return True


PA = {"list": []}


def pa_init():
    PA["list"] = [{"name": d.name, "max_input_channels": 1} for d in System.inputs]


def plug(*inputs, default=None):
    System.inputs, System.default = list(inputs), default or inputs[0]
    pa_init()
    if System.listener:
        System.listener()


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

# ---------------------------------------------------------------- the table alone
shipped = []
t = sb.Sources(root / "t.json", on_change=shipped.append)
a, b = t.id_for("mic", "MacBook Pro Microphone", "builtin"), t.id_for("mic", "AirPods Pro", "bluetooth")
check("the table: one id per source, in the order they were heard",
      (a, b) == ("s1", "s2") and t.id_for("mic", "MacBook Pro Microphone", "builtin") == "s1", t.rows)
z = t.id_for("meeting", "Zoom", bundle="us.zoom.xos")
check("  an app: its bundle, no transport", z == "s3" and t.rows[2] == {"id": "s3", "kind": "meeting", "name": "Zoom",
                                                                       "bundle": "us.zoom.xos"}, t.rows)
check("  written whole, and shipped as it grows (not on a lookup)",
      json.loads((root / "t.json").read_text()) == t.rows and len(shipped) == 3, (len(shipped), t.rows))
broken = sb.Sources(root / "t2.json", on_change=lambda text: (_ for _ in ()).throw(AttributeError("no ship_file")))
check("  shipping it failing never costs the line: the id is still given",
      broken.id_for("mic", "MacBook Pro Microphone", "builtin") == "s1")
check("  read back on resume: the same ids go on",
      sb.Sources(root / "t.json").id_for("mic", "AirPods Pro", "bluetooth") == "s2"
      and sb.Sources(root / "t.json").id_for("mic", "Yeti", "usb") == "s4")


class Script(object):
    """A line on demand; held: a line still being said, closed when the mic switches."""
    say, held = [], None

    def __init__(self, *a, **k): self.n = 0
    def feed(self, b): self.n += len(b)
    def drain(self, force=False):
        out = [(max(0.0, self.n / 16000.0 - 1), self.n / 16000.0, Script.say.pop(0))] if Script.say else []
        if force and Script.held:
            out.append((max(0.0, self.n / 16000.0 - 1), self.n / 16000.0, Script.held))
            Script.held = None
        return out
    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Script
MARK = 0.25


class Others(Script):
    """The call's transcriber: says what the call says."""
    say = []

    def drain(self, force=False):
        return [(max(0.0, self.n / 16000.0 - 1), self.n / 16000.0, Others.say.pop(0))] if Others.say else []


class Pick(object):
    """The mic's lines from Script, the call's from Others, told apart by their audio."""
    def __init__(self, *a, **k): self.inner = None
    def feed(self, b):
        if self.inner is None and len(b):
            self.inner = Others() if abs(float(b[0]) - MARK) < 1e-6 else Script()
        if self.inner is not None:
            self.inner.feed(b)
    def drain(self, force=False): return self.inner.drain(force) if self.inner else []
    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Pick


class Call(object):
    apps_now = []

    def __init__(self): self.ok, self.error, self.name, self._run = False, None, "", False
    def apps(self): return [dict(a) for a in Call.apps_now]

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


sb.MEETING_AUDIO = Call


class FakeShipper(object):
    """What the recorder hands the agent: here, kept to look at."""
    files = {}

    def __init__(self, *a, **k):
        self.offset, self.enabled, self.connected, self.meta = 0, True, True, None
    def start(self): pass
    def ship_file(self, rel, data): FakeShipper.files[rel] = data
    def ship_log(self): pass
    def join(self, timeout=None): pass
    def finish(self, failures=None): pass


sb.Shipper = FakeShipper
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "auto", "speakers": False,
                                 "mic_automatic": True, "capture_automatic": True}))


def start_session(resume=None):
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("meet",), kwargs={"no_claude": True, "resume": resume},
                          daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    sid = (root / "daemon.ready").read_text().strip()
    return th, root / "sessions" / sid, sid


def end_session(th):
    sb._stop.set()
    th.join(timeout=PATIENCE)
    wait(lambda: not (root / "daemon.ready").exists())


th, sdir, sid = start_session()
rows = lambda: [json.loads(l) for l in (sdir / "transcript.jsonl").read_text().splitlines() if l.strip()]
table = lambda: json.loads((sdir / "sources.json").read_text()) if (sdir / "sources.json").exists() else []
by = lambda: dict((s["id"], s) for s in table())
live = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
cur = lambda: [s.name for s in streams if s.run]
wait(lambda: cur() == ["MacBook Pro Microphone"])
Script.say.append("hello from the MacBook mic")
check("a line on the MacBook mic: its source in the row, and in the table",
      wait(lambda: rows() and rows()[-1].get("src") == "s1")
      and by().get("s1") == {"id": "s1", "kind": "mic", "name": "MacBook Pro Microphone", "transport": "builtin"},
      (rows()[-1:], table()))
check("  the live chip has the same id, and its transport",
      wait(lambda: any(x.get("id") == "s1" and x.get("transport") == "builtin" for x in live().get("sources") or [])),
      live().get("sources"))

# Standby Dictation's recordings, decoded on the recorder's main loop with its model.
import dictation  # noqa: E402
dictation.transcribe_audio = lambda model, audio: "decoded %d samples" % len(audio)
check("the recorder says it decodes for dictation, in a folder that is this user's only",
      live().get("decodes") is True and (sdir / "decode").is_dir()
      and (sdir / "decode").stat().st_mode & 0o777 == 0o700, (live(), oct((sdir / "decode").stat().st_mode)))
np.zeros(1600, "<f4").tofile(str(sdir / "decode" / "7-1.f32"))
check("  a recording there is taken and answered", wait(lambda: (sdir / "decode" / "7-1.json").exists())
      and json.loads((sdir / "decode" / "7-1.json").read_text()) == {"text": "decoded 1600 samples"}
      and not (sdir / "decode" / "7-1.f32").exists())

# The AirPods, in a call: the mic switches while a line is still being said.
Script.held = "and this was said on the MacBook"
System.busy = {POD.uid}
plug(MAC, POD, default=POD)
check("the mic switches to the AirPods", wait(lambda: cur() == ["AirPods Pro"]), cur())
check("  the line being said as it switched keeps the MacBook as its source",
      wait(lambda: any(r["text"] == "and this was said on the MacBook" for r in rows()))
      and [r for r in rows() if r["text"] == "and this was said on the MacBook"][0]["src"] == "s1", rows()[-2:])
Script.say.append("now on the AirPods")
check("  the next is the AirPods', a new id (bluetooth)",
      wait(lambda: rows()[-1]["text"] == "now on the AirPods")
      and by()[rows()[-1]["src"]] == {"id": rows()[-1]["src"], "kind": "mic", "name": "AirPods Pro",
                                       "transport": "bluetooth"}, (rows()[-1:], table()))

Call.apps_now = [{"name": "Zoom", "bundle": "us.zoom.xos", "pid": 1, "playing": True, "call": True,
                  "hears": True, "confidence": 0.95}]
wait(lambda: any("Zoom joins as Others" in m for m in logged))
Others.say.append("hello from the call")
check("the call's lines: Others, with the app as their source (its bundle, no transport)",
      wait(lambda: any(r["speaker"] == "Others" for r in rows()))
      and by()[[r for r in rows() if r["speaker"] == "Others"][0]["src"]]
      == {"id": [r for r in rows() if r["speaker"] == "Others"][0]["src"], "kind": "meeting", "name": "Zoom",
          "bundle": "us.zoom.xos"}, (rows()[-1:], table()))
check("  the table went to the agent as it grew (like the speaker names)",
      json.loads(FakeShipper.files.get("sources.json", "[]")) == table(), FakeShipper.files.get("sources.json"))
Call.apps_now = []
end_session(th)
check("the session over: no dictation audio left (its decode folder gone)", not (sdir / "decode").exists())

ui = SourceFileLoader("ui", "mac/ui.py").load_module()
check("the app: an ended session still has its table, for the marks on its lines",
      ui.session_info(sid, live="none")["sources_seen"] == table(), ui.session_info(sid, live="none")["sources_seen"])
plug(MAC, default=MAC)
before = len(table())
th, sdir2, sid2 = start_session(resume=sid)
Script.say.append("back on the MacBook after a resume")
check("resumed: the same ids go on (the MacBook is s1 again, nothing added)",
      wait(lambda: rows()[-1]["text"] == "back on the MacBook after a resume") and rows()[-1]["src"] == "s1"
      and len(table()) == before, (rows()[-1:], table()))
end_session(th)

# ---------------------------------------------------------------- the agent's side
sys.path.insert(0, os.path.dirname(agentpath.AGENT))
ag = SourceFileLoader("ag", agentpath.AGENT).load_module()
live_dir = Path(ag.LIVE)
live_dir.mkdir(parents=True, exist_ok=True)
S = "meeting-20261007-100000"
(live_dir / (S + ".sources.json")).write_text(json.dumps([
    {"id": "s1", "kind": "mic", "name": "MacBook Pro Microphone", "transport": "builtin"},
    {"id": "s2", "kind": "mic", "name": "AirPods Pro", "transport": "bluetooth"},
    {"id": "s3", "kind": "meeting", "name": "Zoom", "bundle": "us.zoom.xos"}]))
block = ag.transcript_block([
    {"t": 5, "speaker": "Me", "text": "can you hear me", "src": "s2"},
    {"t": 7, "speaker": "Others", "spk": 1, "text": "yes, loud and clear", "src": "s3"},
    {"t": 9, "speaker": "Me", "text": "my AirPods died", "src": "s1"}], S)
check("the agent: the sources named once, at the top of the block",
      "Sources: You = AirPods Pro; the call = Zoom" in block, block)
check("  a line is tagged only where its channel's source changed in the block",
      "You (MacBook Pro Microphone): my AirPods died" in block and "You: can you hear me" in block
      and "Zoom)" not in block.split("Sources:")[1].split("\n", 1)[1], block)
old = ag.transcript_block([{"t": 5, "speaker": "Room", "text": "a line from before sources"}], S)
check("rows from before (no src): no sources line, read as they always were",
      "Sources:" not in old and "a line from before sources" in old, old)
check("  and a session with no table: the same", "Sources:" not in ag.transcript_block(
    [{"t": 5, "speaker": "Me", "text": "x", "src": "s9"}], "meeting-20261007-110000"))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
