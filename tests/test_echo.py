"""The laptop's own sound heard again by its mic, and the doubling.

On open speakers the mic hears the call, so other people's words got into the
user's dictation and voice turns, and the transcript had them twice. The
recorder now holds a mic line until the call's channel has heard as far, and
drops it only if it is the call coming back: overlapping in time, most of its
words the call's, in order, and long enough to tell. On headphones nothing is
checked at all. Fakes only: Core Audio, the mic, the call's capture.

First the judge on its own (mac/echo.py), then a real run_session."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbecho-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
import echo  # noqa: E402
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


# ---------------------------------------------------------------- the judge alone
CALL = [(10.0, 14.0, "The routing cache moved to Orion last week, so Redis is gone."),
        (15.0, 18.0, "Can someone own the migration doc?")]
j = lambda start, end, text, others=CALL: echo.judge((start, end, text), others)

check("the call's words, heard again by the mic, at the same time: dropped",
      j(10.2, 14.3, "the routing cache moved to orion last week so redis is gone")[0])
check("  the mic's copy is rougher (a few words heard wrong): still dropped",
      j(10.3, 14.1, "the check out cash moved to orion last week so redis is gone")[0],
      j(10.3, 14.1, "the check out cash moved to orion last week so redis is gone"))
check("  part of one of their sentences (the transcribers cut them differently): dropped",
      j(15.1, 16.6, "can someone own the migration")[0])
check("the same words, but long after: kept (someone saying them again, maybe the user)",
      not j(40.0, 44.0, "the routing cache moved to orion last week so redis is gone")[0])
check("the user's own words during the call: kept",
      not j(16.0, 19.0, "I can take the migration doc, give me until Friday")[0])
check("  a short 'yes, exactly' over the call: kept (too short to tell who said it)",
      not j(14.0, 14.8, "yes exactly")[0])
drop, match, overlap = j(10.5, 14.0, "right, the routing cache moved, but I still think we keep Redis around")
check("  the user picking up their words in a sentence of their own: kept (a near miss, logged)",
      not drop and match >= echo.NEAR, (drop, match, overlap))
check("  crosstalk, the user over the call, half and half: kept",
      not j(10.0, 14.0, "the routing cache wait sorry I have a question about the budget first")[0])
check("against what the call is saying right now (its utterance still open): dropped",
      j(30.1, 32.0, "we should ship it on friday then", [(28.0, 32.4, "So we should ship it on Friday then")])[0])
check("nothing from the call near it: kept, nothing to compare", j(5.0, 7.0, "hello everyone") == (False, 0.0, 0.0))


# ---------------------------------------------------------------- the recorder, with fakes
MAC = dv.Device("BuiltInMicrophoneDevice", "MacBook Pro Microphone", dv.BUILT_IN)


class System(object):
    leaks = True          # the Mac's sound can reach its mic (speakers), or not (headphones)
    looks = 0


class FakeDevices(object):
    def inputs(self): return [MAC]
    def default_input(self): return MAC
    def in_use(self, d): return False
    def watch(self, on_change): return True
    def output_leaks(self):
        System.looks += 1
        return System.leaks


class FakeInput(object):
    def __init__(self, device=None, callback=None, blocksize=8000, **kw):
        self.cb, self.n, self.run = callback, blocksize, False

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


PA = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
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
MARK = 0.25                                          # the call's audio, told from the mic's zeros


class Script(object):
    """A line on demand, on the mic or the call, spanning the last two seconds of
    its own stream: said now, on the session clock."""
    say = {"mic": [], "meeting": []}

    def __init__(self, *a, **k):
        self.n, self.kind = 0, None

    def feed(self, b):
        self.n += len(b)
        if self.kind is None and len(b):
            self.kind = "meeting" if abs(float(b[0]) - MARK) < 1e-6 else "mic"

    def drain(self, force=False):
        if self.kind is None or not Script.say[self.kind]:
            return []
        at = self.n / 16000.0
        return [(max(0.0, at - 2.0), at, Script.say[self.kind].pop(0))]

    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Script


class Call(object):
    """The call's capture (the agreed interface): Zoom playing, blocks at its rate."""
    def __init__(self):
        self.ok, self.error, self.name, self._run, self.quiet = False, None, "", False, False

    def apps(self):
        return [{"name": "Zoom", "bundle": "us.zoom.xos", "pid": 1, "playing": True, "call": True, "confidence": 0.95}]

    def start(self, target, feed):
        self.ok, self._run, t0 = True, True, time.monotonic()

        def go():
            k = 0
            while self._run:
                time.sleep(0.1)                     # real time on purpose: the capture's own block rate
                if self._run and not self.quiet:          # quiet: a stall, no word from macOS
                    feed(np.full(1600, MARK, "float32"), t0 + k * 0.1)
                    k += 1
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self._run, self.ok = False, False


made = []
sb.MEETING_AUDIO = lambda: made.append(Call()) or made[-1]
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "auto", "speakers": False,
                                 "mic_automatic": True, "capture_automatic": True}))


def start_session():
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("meet",), kwargs={"no_claude": True}, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    return th, root / "sessions" / (root / "daemon.ready").read_text().strip()


def end_session(th):
    sb._stop.set()
    th.join(timeout=PATIENCE)
    wait(lambda: not (root / "daemon.ready").exists())


THEIRS = "The routing cache moved to Orion last week so Redis is gone"
ECHOED = "the routing cache moved to orion last week so redis is gone"
MINE = "I can take the migration doc and have it by Friday"

th, sdir = start_session()
rows = lambda: [json.loads(l) for l in (sdir / "transcript.jsonl").read_text().splitlines() if l.strip()]
drows = lambda: [json.loads(l) for l in (sdir / "dictation.jsonl").read_text().splitlines()] \
    if (sdir / "dictation.jsonl").exists() else []
check("on speakers: the call is recorded as Others", wait(lambda: any("Zoom joins as Others" in m for m in logged)))
time.sleep(2.2)        # real time: both streams need two seconds of audio for a two-second line
Script.say["meeting"].append(THEIRS)
Script.say["mic"].append(ECHOED)
check("their words are the call's (Others) in the transcript",
      wait(lambda: [r["text"] for r in rows() if r["speaker"] == "Others"] == [THEIRS]), rows())
check("  and the mic's copy is dropped: not twice (doubling)",
      wait(lambda: any("DROPPED from the mic" in m for m in logged))
      and not [r for r in rows() if r["speaker"] in ("Me", "Room")], rows())
drop_log = [m for m in logged if "DROPPED from the mic" in m]
check("  the drop is logged with both texts and the scores, to tune from",
      drop_log and ECHOED in drop_log[-1] and THEIRS in drop_log[-1] and "match" in drop_log[-1], drop_log)

Script.say["mic"].append(MINE)
check("the user's own words during the call: kept, as Me",
      wait(lambda: [r["text"] for r in rows() if r["speaker"] == "Me"] == [MINE]), rows())

# Double-talk: the user talks over the call on speakers, so the mic has both voices.
time.sleep(2.2)        # real time, as above
Script.say["meeting"].append("We need the security review before we can launch the new trip planner")
Script.say["mic"].append("we need the wait I have not even seen the security review doc yet who owns it")
check("double-talk on speakers (both voices in the mic): kept, the doubled words too, not the user's lost",
      wait(lambda: any(r["speaker"] == "Me" and "who owns it" in r["text"] for r in rows())), rows()[-3:])

# Dictation during the call, on speakers: their words never in it; the user's are,
# even when dictation is over before the held line is let through.
(sdir / "dictate").write_text("on 5\n")
wait(lambda: any("dictation 5: on" in m for m in logged))
time.sleep(2.2)        # real time, as above: a two-second line needs two seconds of audio
Script.say["meeting"].append("Can someone own the migration doc for the cache")
Script.say["mic"].append("can someone own the migration doc for the cache")
check("dictating during the call: the call's words are not dictated",
      wait(lambda: sum("DROPPED from the mic" in m for m in logged) == 2)
      and not [r for r in drows() if r.get("text")], drows())
Script.say["mic"].append("Remind me to send the budget to Alex")
(sdir / "dictate").write_text("off 5\n")
check("  the user's dictated words are, in this dictation, even if it ended first",
      wait(lambda: [(r.get("text"), r.get("seq")) for r in drows() if r.get("text")]
           == [("Remind me to send the budget to Alex", 5)]), drows())
check("  and not in the transcript", "Remind me" not in json.dumps(rows()))

# Voice mode during the call, on speakers: the same, for the turns Claude answers.
(sdir / "dictate").write_text("voice 6\n")
wait(lambda: any("voice 6: listening" in m for m in logged))
time.sleep(2.2)        # real time, as above
Script.say["meeting"].append("I think the budget review moved to Thursday afternoon")
Script.say["mic"].append("i think the budget review moved to thursday afternoon")
check("voice mode during the call: their words are not the user's turn",
      wait(lambda: sum("DROPPED from the mic" in m for m in logged) == 3)
      and not [r for r in drows() if r.get("seq") == 6 and r.get("text")], drows()[-3:])
Script.say["mic"].append("What did they decide about the budget")
check("  the user's question is, in this voice turn",
      wait(lambda: [r.get("text") for r in drows() if r.get("seq") == 6 and r.get("text")]
           == ["What did they decide about the budget"]), drows()[-3:])
(sdir / "dictate").write_text("off 6\n")
wait(lambda: any("dictation 6: off" in m for m in logged))

# The call's audio pauses (it goes quiet: a stall) while a mic line is held: there is
# nothing to compare it with for that stretch, so it is kept, even on speakers.
sb.STALL = 1.0         # this check only: the pause must come before the held line's own 3 s are up
time.sleep(2.2)        # real time, as above
Script.say["meeting"].append("The launch date stays the fourteenth of November")
wait(lambda: any("fourteenth of November" in r["text"] for r in rows()))
made[-1].quiet = True
Script.say["mic"].append("the launch date stays the fourteenth of november")   # the call's words, tempting
check("the call's audio pausing while a mic line is held: kept, not judged against half a picture",
      wait(lambda: any("kept, the call's audio is paused" in m for m in logged)
           and any(r["speaker"] == "Me" and "fourteenth of november" in r["text"] for r in rows())),
      [m for m in logged if "echo" in m][-2:])
n = len([m for m in logged if m.startswith("echo:")])
Script.say["mic"].append("while it is paused this goes straight through")
check("  while it stays paused, the mic's lines are not even held",
      wait(lambda: any("goes straight through" in r["text"] for r in rows()))
      and len([m for m in logged if m.startswith("echo:")]) == n, [m for m in logged if m.startswith("echo:")][n:])
made[-1].quiet = False
sb.STALL = 2.0
wait(lambda: any("audio back after" in m for m in logged))

# Headphones: the laptop's sound cannot reach the mic, so nothing is checked or held.
looks = System.looks
System.leaks = False
wait(lambda: System.looks > looks + 1)
n = sum("echo:" in m and "DROPPED" in m for m in logged)
time.sleep(2.2)        # real time, as above
Script.say["meeting"].append("Let us move on to the hiring plan for next quarter")
Script.say["mic"].append("let us move on to the hiring plan for next quarter")
check("on headphones: nothing is dropped, even words just like the call's (it cannot be the laptop)",
      wait(lambda: "let us move on to the hiring plan for next quarter" in [r["text"] for r in rows()])
      and sum("DROPPED" in m for m in logged) == n, [m for m in logged if "echo" in m][-3:])
check("  and it says when the sound stopped being able to reach the mic",
      any("cannot reach the mic now (headphones)" in m for m in logged))
end_session(th)
check("the session ends cleanly", not th.is_alive())
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
