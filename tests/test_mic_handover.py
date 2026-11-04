"""Voice mode's engine takes the mic made before the plain stream goes, so the mic is never idle
between them: AirPods idle for 2 s leave call mode, and an engine started as they come back can
play into a link macOS left set up for the codec before, with nothing heard (a call 2026-11-02).

borrow(): the plain stream is still open when the engine starts, its audio dropped meanwhile (one
stream's audio at a time), closed once the engine has the mic; an engine that won't start leaves
the plain mic going as it was. The handover adds no wait of its own to the engine's start. The
engine made again on a mic (a switch, a reopen) gets a keeper stream on it across the restart.
Echo cancelling still comes up. Fakes only: no audio device is opened."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbhandover-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
from importlib.machinery import SourceFileLoader  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


events = []
DEV = [{"name": "AirPods Pro", "max_input_channels": 1}, {"name": "MacBook Pro Microphone", "max_input_channels": 1}]


class FakeInput(object):
    """A PortAudio stream; the test calls its callback by hand."""
    def __init__(self, device=None, callback=None, **kw):
        self.name, self.cb, self.run, self.closed = DEV[device]["name"], callback, False, False
        events.append(("open", self.name))
    def start(self): self.run = True
    def stop(self): self.run = False
    def close(self):
        self.closed = True
        events.append(("close", self.name))


sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]), _terminate=lambda: None, _initialize=lambda: None)
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
logged = []
sb.log = logged.append
sb.SESSIONS, sb.CONFIG = root / "sessions", root / "config.json"


class Engine(object):
    """The echo-cancelling engine: start_mic takes `setup` seconds, as voice processing does, and
    meanwhile the plain stream's driver still delivers (the test's `during`)."""
    aec = True

    def __init__(self, setup=0.0, refuse=False, during=None):
        self.setup, self.refuse, self.during = setup, refuse, during
        self.mic, self.seen, self.took = None, [], None

    def start_mic(self, name, feed):
        t0 = time.time()
        self.seen.append([(s.name, s.run, s.closed) for s in plains])   # the plain streams, as the engine started
        events.append(("engine start", name))
        if self.during:
            self.during()
        time.sleep(self.setup)
        if self.refuse:
            raise RuntimeError("voice processing did not start")
        self.mic = name
        feed(np.full(800, 0.5, "float32"))               # the engine's own audio
        self.took = time.time() - t0                     # its own start, as long as the runner made it

    def stop_mic(self):
        events.append(("engine stop", self.mic))
        self.mic = None

    def open_voice(self):
        events.append(("voice opened", None))


plains = []


def channel(name="AirPods Pro"):
    ch = sb.Channel("Room", 0, None, lambda *a: None, None, None)
    ch.device_name, ch.device = name, [d["name"] for d in DEV].index(name)
    ch._open_input()
    plains.append(ch._input)
    ch.fed = []
    ch._feed = lambda block: ch.fed.append(round(float(block[0]), 3))     # what reaches the transcriber
    return ch


def plain_block(ch):
    """The plain stream's driver delivers a block (of 0.1s: told apart from the engine's 0.5s)."""
    if ch._input is not None or plains[-1].run:
        plains[-1].cb(np.full((800, 1), 0.1, "float32"), 800, None, None)


# -------------------------------------------------------------- borrow: made before the plain goes
ch = channel()
plain = ch._input
eng = Engine(setup=0.3, during=lambda: plain_block(ch))


class Clock(object):
    """The recorder's clock, as itself, but every wait of the recorder's own is counted (the engine's is not)."""
    def __init__(self, real):
        self.real, self.slept = real, []
    def __getattr__(self, name):
        return getattr(self.real, name)
    def sleep(self, secs):
        self.slept.append(secs)
        self.real.sleep(secs)


sb.time = Clock(time)
clock = sb.time
t0 = time.time()
ok = ch.borrow(eng)
took = time.time() - t0
sb.time = time
check("borrow: the engine has the mic", ok and ch.borrowed is eng and eng.mic == "AirPods Pro", (ok, ch.borrowed))
check("  the plain stream was still open on the same mic as the engine started",
      eng.seen == [[("AirPods Pro", True, False)]], eng.seen)
check("  ...and closed once the engine had it, never before",
      events.index(("engine start", "AirPods Pro")) < events.index(("close", "AirPods Pro"))
      and plain.closed and ch._input is None, events)
check("  its audio meanwhile is dropped: only the engine's reaches the transcriber (no doubled audio)",
      ch.fed == [0.5], ch.fed)
check("  the handover adds no wait of its own to the engine's start: the recorder waits on nothing",
      clock.slept == [] and eng.took is not None and took >= eng.took, (clock.slept, took, eng.took))
check("  no longer handing over: a straggling plain block after it is the engine's mic, not the plain's",
      ch._handover is False)
said = [float(m.rsplit(" in ", 1)[1].rstrip("s")) for m in logged if m.startswith("Room: echo cancelling has the mic in ")]
check("  the log says how long the engine took: its own start, up to the whole handover (to the log's 0.01 s)",
      len(said) == 1 and eng.took - 0.006 <= said[0] <= took + 0.006, (said, eng.took, took))

# ---------------------------------------------------------------- an engine that won't start
ch = channel()
plain = ch._input
del events[:]
eng = Engine(refuse=True, during=lambda: plain_block(ch))
ok = ch.borrow(eng)
check("an engine that won't start: the plain mic goes on as it was, never closed or opened again",
      not ok and ch.borrowed is None and ch._input is plain and not plain.closed
      and events == [("engine start", "AirPods Pro")], (ok, events))
check("  why, for the app", "voice processing did not start" in (ch.borrow_error or ""), ch.borrow_error)
check("  dropped only while the engine was being made", ch.fed == [], ch.fed)
plain_block(ch)
check("  its audio reaches the transcriber again", ch.fed == [0.1], ch.fed)

# ---------------------------------------------------------------- echo cancelling still comes up
ch = channel()
vio = sb.VoiceIO(root, Engine(), log=logged.append, leaks=lambda: True)
del events[:]
vio.start(7, [ch])
check("voice mode: Standby's voice starts loading before the engine is made, not after it",
      events[:2] == [("voice opened", None), ("engine start", "AirPods Pro")], events)
check("voice mode: echo cancelled, talking over Claude stops it", vio.aec and vio.duplex and ch.borrowed is vio.backend,
      (vio.aec, vio.duplex))
check("  said so in the log", any("echo cancelled: talking over Claude stops it" in m for m in logged), logged[-3:])
vio._on.clear()

# ---------------------------------------------------------------- the engine made again on a mic
del events[:]
ch = channel()
eng = Engine()
ch.borrow(eng)
del events[:]
ch._start("AirPods Pro", refresh=False)
check("made again on the same mic: a keeper stream opened before the old engine lets go, closed after",
      events == [("open", "AirPods Pro"), ("engine stop", "AirPods Pro"), ("engine start", "AirPods Pro"),
                 ("close", "AirPods Pro")], events)
check("  the engine has it, no plain stream left", ch.borrowed is eng and eng.mic == "AirPods Pro" and ch._input is None)
del events[:]
ch._start("MacBook Pro Microphone", refresh=False)
check("  a switch: the keeper on the new mic", events[0] == ("open", "MacBook Pro Microphone")
      and events[-1] == ("close", "MacBook Pro Microphone") and eng.mic == "MacBook Pro Microphone", events)
del events[:]
ch._start("Gone Mic", refresh=False)          # not in PortAudio's list: no keeper, the engine still tried
check("  no keeper possible: the engine is made again all the same",
      events == [("engine stop", "MacBook Pro Microphone"), ("engine start", "Gone Mic")], events)

print("\n%s" % ("ALL PASS" if not fails else "FAILED: %d\n  %s" % (len(fails), "\n  ".join(fails))))
sys.exit(1 if fails else 0)
