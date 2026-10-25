"""The meeting's audio as the Others channel, with a fake capture.

The capture is meeting_audio, faked with exactly its interface:
apps() (who is playing, a known call app or not, how sure), start(target,
feed), stop(), ok, error, name; feed(block, t). A real run_session in
Automatic: the mic alone until a call plays, then the call's audio joins as
Others on the session clock, the mic becomes the user's own (Me), and it
leaves when the call app goes. A browser playing is not recorded unless the
user says so (asked about later); a capture that errs or stalls is opened
again. Mic only never looks; Meeting audio only has no mic. And the policy,
choose_meeting, on its own; and Automatic as the setting, once."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbmeet-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
import devices as dv  # noqa: E402
from native.macos import appaudio as ma  # noqa: E402  (the Mac's capture: its CAUSES table, the words the user sees)

fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


# Generous: five people run these suites at once, and a loaded Mac slows every
# real-time step. Nothing waits the whole time when it passes.
PATIENCE = 30


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- the policy alone
ZOOM = {"name": "Zoom", "bundle": "us.zoom.xos", "pid": 11, "playing": True, "call": True}
TEAMS = {"name": "Microsoft Teams", "bundle": "com.microsoft.teams2", "pid": 12, "playing": True, "call": True}
CHROME = {"name": "Google Chrome", "bundle": "com.google.Chrome", "pid": 13, "playing": True, "call": False,
          "confidence": 0.5}
MUSIC = {"name": "Music", "bundle": "com.apple.Music", "pid": 14, "playing": True, "call": False, "confidence": 0.1}
quiet = lambda a: dict(a, playing=False)
choose = lambda apps, capture="auto", choices=None, current=None: dv.choose_meeting(apps, capture, choices or {}, current)

check("policy: a known call app playing is recorded", choose([MUSIC, ZOOM])[0] == ZOOM)
check("  Mic only: never", choose([ZOOM], "mic")[0] is None)
check("  Meeting audio only: the same choice", choose([ZOOM], "meeting")[0] == ZOOM)
check("  whatever the Mac plays is the meeting's (the user's choice): music alone is recorded",
      choose([MUSIC]) == (MUSIC, "Music is playing", None))
VIDEO = dict(CHROME, hears=False, confidence=0.3)          # YouTube in Chrome: playing, no mic
a, why, ask = choose([VIDEO])
check("  a video in a browser (YouTube), no mic: recorded, and nothing asked", a == VIDEO and ask is None, (a, why, ask))
check("  answered no: never", choose([VIDEO], choices={"com.google.Chrome": "no"})[:3:2] == (None, None))
check("  a call app and a video playing: the call first", choose([VIDEO, ZOOM])[0] == ZOOM)
check("  the call paused: still the call", choose([quiet(ZOOM)], current=ZOOM)[0]["bundle"] == ZOOM["bundle"])
check("  another call playing while this one is quiet: that one",
      choose([quiet(ZOOM), TEAMS], current=ZOOM)[0] == TEAMS)
check("  but music or a video while the call is quiet: still the call (it takes one app at a time)",
      choose([quiet(ZOOM), MUSIC, VIDEO], current=ZOOM)[0]["bundle"] == ZOOM["bundle"])
check("  a video gone quiet, music playing: the music", choose([quiet(VIDEO), MUSIC], current=VIDEO)[0] == MUSIC)
check("  both playing: the one recorded stays", choose([ZOOM, TEAMS], current=ZOOM)[0]["bundle"] == ZOOM["bundle"])
check("  the call app gone, a video still playing: the video", choose([VIDEO], current=ZOOM)[0] == VIDEO)
check("  the call app gone, nothing playing: nothing", choose([quiet(VIDEO)], current=ZOOM)[0] is None)
MEET = dict(CHROME, hears=True, confidence=0.7)
check("  a browser with the mic open (a Meet call): recorded", choose([MEET])[0] == MEET)
check("  a known call app playing, the mic not open (muted): recorded",
      choose([dict(ZOOM, hears=False, confidence=0.8)])[0]["bundle"] == ZOOM["bundle"])
HELPER1 = {"name": "pid 401", "bundle": "", "pid": 401, "playing": True, "call": False, "hears": True, "confidence": 0.7}
HELPER2 = dict(HELPER1, name="pid 402", pid=402)
check("  apps without a bundle id are told apart by pid", choose([quiet(HELPER1), HELPER2], current=HELPER1)[0]["pid"] == 402)
check("  and the one recorded is still known by its pid", choose([HELPER1, HELPER2], current=HELPER2)[0]["pid"] == 402)
check("  their answers are kept by name (a pid changes every run)", dv.answer_key(HELPER1) == "name:pid 401"
      and dv.app_key(HELPER1) == "pid:401" and dv.answer_key(ZOOM) == dv.app_key(ZOOM) == "us.zoom.xos")
check("  no confidence given: a call app is a call, another app plays as itself",
      dv.confidence({"call": True}) >= dv.CAPTURE_AT > dv.confidence({"call": False}))


# ---------------------------------------------------------------- fakes: PortAudio, the transcriber, the capture
class FakeInput(object):
    def __init__(self, callback=None, blocksize=8000, **kw):
        self.cb, self.n, self.run = callback, blocksize, False

    def start(self):
        self.run = True

        def go():
            while self.run:
                time.sleep(self.n / 16000.0)
                if self.run:
                    self.cb(np.zeros((self.n, 1), "float32"), self.n, None, None)
        threading.Thread(target=go, daemon=True).start()

    def stop(self): self.run = False
    def close(self): pass


DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]), _terminate=lambda: None, _initialize=lambda: None)
from importlib.machinery import SourceFileLoader  # noqa: E402
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
# The 2 s rule, before the policy (MeetingFollow.sustained): a ping, from any app, starts nothing.
follow = sb.MeetingFollow(None, "auto")
seen = lambda t, apps=(VIDEO,), current=None: choose(follow.sustained(list(apps), current, now=t), current=current)[0]
check("  a video playing for under 2 s: not yet (a ping is never recorded)",
      seen(100.0) is None and seen(101.0) is None and seen(101.9) is None)
check("  and once it has played for 2 s: recorded", seen(102.1) == VIDEO)
follow = sb.MeetingFollow(None, "auto")
check("  a 1 s ping that stops: nothing, and a later sound waits its own 2 s",
      seen(200.0) is None and seen(201.0, (quiet(VIDEO),)) is None and seen(202.5) is None and seen(204.6) == VIDEO)
logged = []
sb.log = logged.append
sb.SESSIONS, sb.CONFIG = root / "sessions", root / "config.json"
sb.load_model = lambda name: None
sb.STALL = 1.0
MARK = 0.25                                       # the meeting's audio, told from the mic's zeros


class Script(object):
    """Says a line on demand, on whichever channel the line is for: the mic
    (zeros) or the meeting (MARK), at its own stream position."""
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
        return [(at - 0.5, at, Script.say[self.kind].pop(0))]

    def rotate(self): return []
    def _close(self): pass
    utt = None
    def partial(self): return ""


sb.UtteranceTranscriber = Script
# How many times the recorder has asked which app's audio is the meeting's. A check
# that nothing was recorded waits for a look, instead of sleeping and hoping.
looks = [0]
_decide = sb.MeetingFollow.decide_all
sb.MeetingFollow.decide_all = lambda self, *a: (looks.__setitem__(0, looks[0] + 1), _decide(self, *a))[1]


def looked(n=1):
    """Wait until the recorder has looked n more times (with the apps as they are now)."""
    was = looks[0]
    return wait(lambda: looks[0] >= was + n)


class FakeMeetingAudio(object):
    """The module's interface, exactly, with its paused/error fields:
    on a refusal, fail_as sets state, cause, reason, detail, past (and confirm,
    needs_restart for an error); words(cause) gives the wording for a cause the
    recorder found itself (a stall). Without fail_as, only .error, as the module
    was before: the recorder must cope with that too."""
    apps_now, calls, fail_with, first_after, fail_as = [], [], None, 0.6, {}
    starts = []                          # every start() it was asked for, kept: target goes back to None at stop()
    first_block = []                     # the wall time of the first block's own timestamp

    def __init__(self):
        self.ok, self.error, self.name, self.target, self.stalled, self._run = False, None, "", None, False, False

    def apps(self):
        FakeMeetingAudio.calls.append(time.time())
        return [dict(a) for a in FakeMeetingAudio.apps_now]

    def start(self, target, feed):
        FakeMeetingAudio.starts.append(target)
        for key in ("state", "cause", "reason", "detail", "past", "confirm", "needs_restart"):
            setattr(self, key, None)
        if FakeMeetingAudio.fail_with:
            self.error, self.ok = FakeMeetingAudio.fail_with, False
            for key, value in FakeMeetingAudio.fail_as.items():
                setattr(self, key, value)
            return
        self.error, self.ok, self.target, self._run = None, True, target, True
        self.name = next((a["name"] for a in FakeMeetingAudio.apps_now if a["bundle"] == target), "?")

        def go():                                # first audio a while after asking, as the real one
            first = time.monotonic() + FakeMeetingAudio.first_after
            time.sleep(FakeMeetingAudio.first_after)      # real time: it is what is being tested
            k = 0
            while self._run and self.target == target:
                time.sleep(0.32)                          # real time on purpose: the capture's rate
                if self._run and self.target == target and not self.stalled:
                    t = first + k * 0.32                   # t: the buffer's first sample, not now
                    if not k:                                  # the same instant, in wall time
                        FakeMeetingAudio.first_block.append(time.time() - (time.monotonic() - t))
                    feed(np.full(5120, MARK, "float32"), t)
                    k += 1
        threading.Thread(target=go, daemon=True).start()

    def stop(self):
        self._run, self.ok, self.target = False, False, None


made = []
sb.MEETING_AUDIO = lambda: made.append(FakeMeetingAudio()) or made[-1]
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False}))


def start_session(mode=None):
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("meet",), kwargs={"no_claude": True, "mode": mode}, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    sid = (root / "daemon.ready").read_text().strip()
    return th, root / "sessions" / sid


_ui = []


def ui_info(d):
    """The app's session info for an ended session (ui.py, on this test's root)."""
    if not _ui:
        _ui.append(SourceFileLoader("ui", "mac/ui.py").load_module())
    return _ui[0].session_info(d.name, live="none")


def end_session(th):
    sb._stop.set()
    th.join(timeout=PATIENCE)
    wait(lambda: not (root / "daemon.ready").exists())


th, sdir = start_session()
live = lambda: json.loads((sdir / "live.json").read_text()) if (sdir / "live.json").exists() else {}
sources = lambda: live().get("sources") or []
rows = lambda: [json.loads(l) for l in (sdir / "transcript.jsonl").read_text().splitlines() if l.strip()]
meta = lambda: json.loads((sdir / "meta.json").read_text())
cfg = json.loads(sb.CONFIG.read_text())
check("the setting: Mic only becomes Automatic, once, and said so",
      cfg.get("mode") == "auto" and cfg.get("capture_automatic") is True
      and any("capture: mic -> auto" in m for m in logged), cfg)
check("the apps are looked at once at the start, before any polling (the first read is the slow one)",
      len(FakeMeetingAudio.calls) > looks[0], (len(FakeMeetingAudio.calls), looks[0]))
check("Automatic, no call: the mic alone, the meeting waiting for one",
      wait(lambda: [(s["kind"], s["state"]) for s in sources()] == [("mic", "live"), ("meeting", "waiting")]),
      sources())
check("  the mic is the room's (speaker labels), as before", meta()["speakers"] == ["Room"], meta())
Script.say["mic"].append("before the call")
check("  a line from the mic: Room", wait(lambda: [r["speaker"] for r in rows()] == ["Room"]), rows())

looked()
# A Slack ping: a call app plays for a moment. It is not a call: nothing joins, and the mic
# stays the room's. (The recorder looks once a second, so a ping is seen at most once.)
SLACK = {"name": "Slack", "bundle": "com.tinyspeck.slackmacgap", "pid": 15, "playing": True, "call": True,
         "confidence": 0.8}
FakeMeetingAudio.apps_now = [dict(SLACK)]
looked()
FakeMeetingAudio.apps_now = [dict(SLACK, playing=False)]
looked(2)
check("a Slack ping (a call app playing a moment): nothing joins, the mic stays the room's",
      not any("Slack joins" in m for m in logged) and meta()["speakers"] == ["Room"], meta())
FakeMeetingAudio.apps_now = [dict(MUSIC), dict(ZOOM)]
joined_at = time.time()
check("Zoom and music play: each joins as Others, on its own capture",
      wait(lambda: any("Zoom joins as Others" in m for m in logged) and any("Music joins as Others" in m for m in logged))
      and "us.zoom.xos" in FakeMeetingAudio.starts, [m for m in logged if "meeting:" in m])
check("  only once it has played for 2 s (a ping does not)", time.time() - joined_at >= sb.MeetingFollow.SUSTAIN,
      round(time.time() - joined_at, 2))
check("  the app is told: the meeting's source, Zoom, recording",
      wait(lambda: ("meeting", "Zoom", True, "recording") in [(s["kind"], s["name"], s["ok"], s["state"]) for s in sources()]),
      sources())
check("  the mic is the user's own now (Me), and the agent's meta says so",
      meta()["speakers"] == ["Me", "Others", "Others"], meta())
Script.say["meeting"].append("hello from the call")
check("  the call's words: Others, on the session clock (when it joined, plus where in its audio)",
      wait(lambda: any(r["speaker"] == "Others" for r in rows())), rows())
others = [r for r in rows() if r["speaker"] == "Others"][0]
start0 = json.loads((sdir / "runs.jsonl").read_text().splitlines()[0])["start"]
check("  its time is when it was said, not counted from 0", abs(others["end"] - (time.time() - start0)) < 1.2
      and others["t"] > 1.5, (others, round(time.time() - start0, 2)))
joined = float((sdir / "others.wav.offset").read_text())
want = FakeMeetingAudio.first_block[0] - start0        # when its first sample was really heard
check("  placed by its first buffer's own time, not by when capture was asked for or when the block arrived",
      abs(joined - want) < 0.1, (joined, round(want, 2)))
Script.say["mic"].append("my reply")
check("  the mic's words now: Me", wait(lambda: [r["speaker"] for r in rows()][-1:] == ["Me"]), rows())
check("  its own recording, with where it starts on the session clock",
      (sdir / "others.wav.offset").exists() and float((sdir / "others.wav.offset").read_text()) > 1.0)

FakeMeetingAudio.apps_now = [dict(MUSIC), quiet(ZOOM)]
looked(2)
check("the call paused: still recorded (no stop), and the music goes on",
      not any("audio stops" in m for m in logged) and set(FakeMeetingAudio.starts) == {"us.zoom.xos", "com.apple.Music"},
      (FakeMeetingAudio.starts, [m for m in logged if "audio stops" in m]))

events = lambda: [json.loads(l) for l in (sdir / "events.jsonl").read_text().splitlines()] \
    if (sdir / "events.jsonl").exists() else []
meet = lambda name=None: next((x for x in sources() if x["kind"] == "meeting" and name in (None, x["name"])), {})
capture_of = lambda bundle: next(m for m in made if m.target == bundle)
FakeMeetingAudio.fail_with = "nothing to capture"           # so a stall stays a stall for a moment
zoom = capture_of("us.zoom.xos")
zoom.stalled = True
STALLED = ma.describe("stalled", "Zoom")        # the words come from the module's one table
check("its audio goes quiet with no word: paused, stalled, in meeting_audio's own words",
      wait(lambda: meet().get("state") == "paused" and meet().get("cause") == "stalled"
           and meet().get("reason") == STALLED["reason"] and meet().get("detail") == STALLED["detail"]), meet())
check("  a pause is on record as it starts (a crash mid-pause leaves it there), in the past tense",
      wait(lambda: [(e["kind"], e["cause"], e["reason"], e["to"]) for e in events()]
           == [("meeting", "stalled", STALLED["past"], None)]), events())
check("  not in the transcript: the agent reads that as what was said",
      not any(STALLED["past"] in json.dumps(r) for r in rows()))
FakeMeetingAudio.fail_with = None
zoom.stalled = False
check("  back: recording, and the pause closed with when it ended",
      wait(lambda: meet().get("state") == "recording" and events() and events()[-1]["to"] is not None)
      and events()[-1]["to"] >= events()[-1]["from"] and len(events()) == 1, (meet(), events()))
check("  the logs say so", any("audio paused: %s" % STALLED["reason"] in m for m in logged)
      and any("audio back after" in m for m in logged), [m for m in logged if "audio paused" in m][-2:])

# The live meeting's list: every source heard, each with its switch (the window's "hearing").
hearing = lambda: [(h["id"], h["kind"], h["name"], h["on"]) for h in live().get("hearing") or []]
check("the list: your mic, then each app recorded, all on",
      wait(lambda: hearing() == [("mic", "mic", "Your mic", True), ("us.zoom.xos", "app", "Zoom", True),
                                 ("com.apple.Music", "app", "Music", True)]), hearing())
mic_detail = live()["hearing"][0]["detail"]
check("  the mic's device, for its tooltip", mic_detail not in ("", "?"), mic_detail)
FakeMeetingAudio.apps_now = [dict(MUSIC), dict(ZOOM)]       # the call talking again
_ui_mod = ui_info(sdir) and _ui[0]
_ui_mod.live_session = lambda fresh=False: sdir.name
zoom_starts = FakeMeetingAudio.starts.count("us.zoom.xos")
roomed = set()                       # the channels with the Mac's sound taken out, now
_room_on, _room_off = sb.Channel.room_on, sb.Channel.room_off
sb.Channel.room_on = lambda self, r: (roomed.add(self), _room_on(self, r))[1]
sb.Channel.room_off = lambda self: (roomed.discard(self), _room_off(self))[1]
said = len(rows())
check("the music switched off (the window's POST /api/live/source): ok",
      _ui_mod.set_source(sdir.name, "com.apple.Music", False) == {"ok": True})
check("  its audio stops at once, said why", wait(lambda: any("Music's audio stops (off for this meeting)" in m for m in logged)),
      [m for m in logged if "Music" in m][-3:])
check("  Zoom goes on untouched: not restarted, still recording",
      FakeMeetingAudio.starts.count("us.zoom.xos") == zoom_starts and meet("Zoom").get("state") == "recording",
      (FakeMeetingAudio.starts, meet("Zoom")))
check("  the list keeps it, off, so it can come back on",
      wait(lambda: ("com.apple.Music", "app", "Music", False) in hearing()), hearing())
check("  a span opens for the transcript's \"Music was off\"",
      wait(lambda: [(e["kind"], e["name"], e["to"]) for e in events() if e["kind"] == "source_off"]
           == [("source_off", "Music", None)]), events())
time.sleep(1.5)                      # real time: proving it isn't taken again needs looks to pass
check("  still playing, it is not recorded again", sum("Music joins as Others" in m for m in logged) == 1
      and [c["name"] for c in sources() if c["kind"] == "meeting"] == ["Zoom"], sources())
Script.say["meeting"].append("still the call")
check("  the call's words still come", wait(lambda: any(r["text"] == "still the call" for r in rows()[said:])), rows()[said:])
check("  on open speakers its sound is still heard, only to take it out of the mic (never a channel, nothing kept)",
      wait(lambda: any("Music off, its sound taken out of the mic" in m for m in logged))
      and "com.apple.Music" in FakeMeetingAudio.starts[zoom_starts:],
      [m for m in logged if "Music" in m][-3:])
check("  the mic with it taken out", wait(lambda: len(roomed) == 1), [m for m in logged if "room only" in m][-3:])
Script.say["mic"].append("my words with the music off")
check("  and the user's words still come", wait(lambda: any(r["text"] == "my words with the music off" for r in rows()[said:])),
      rows()[said:])
check("an app nobody hears any more can't be switched: said in words",
      "error" in _ui_mod.set_source(sdir.name, "com.example.gone", False))
check("the music on again: recorded again at once (it has been playing all along)",
      _ui_mod.set_source(sdir.name, "com.apple.Music", True) == {"ok": True}
      and wait(lambda: sum("Music joins as Others" in m for m in logged) == 2), [m for m in logged if "Music" in m][-3:])
check("  no longer taken out of the mic: it is a channel again",
      wait(lambda: any("Music no longer taken out of the mic" in m for m in logged))
      and wait(lambda: not roomed), [m for m in logged if "Music" in m][-3:])
check("  the span closed, with when it ended",
      wait(lambda: [e for e in events() if e["kind"] == "source_off"][0]["to"] is not None), events())
check("  and on, in the list", wait(lambda: ("com.apple.Music", "app", "Music", True) in hearing()), hearing())
check("your mic's switch is its mute", _ui_mod.set_source(sdir.name, "mic", False) == {"ok": True}
      and wait(lambda: hearing()[:1] == [("mic", "mic", "Your mic", False)] and live().get("mic_muted")), hearing())
check("  and on again", _ui_mod.set_source(sdir.name, "mic", True) == {"ok": True}
      and wait(lambda: hearing()[:1] == [("mic", "mic", "Your mic", True)]), hearing())

FakeMeetingAudio.apps_now = [dict(MUSIC)]
check("Zoom quits while music plays: its audio stops, the music goes on",
      wait(lambda: any("Zoom's audio stops" in m for m in logged))
      and wait(lambda: any("Music joins as Others" in m for m in logged)), [m for m in logged if "meeting:" in m][-3:])
FakeMeetingAudio.apps_now = []
check("  and the music stops: the meeting waiting again",
      wait(lambda: [(s["kind"], s["state"]) for s in sources()] == [("mic", "live"), ("meeting", "waiting")]),
      sources())

# A Meet call in Chrome (the mic open): recorded, nothing asked.
FakeMeetingAudio.apps_now = [dict(CHROME, hears=True, confidence=0.7)]
check("a browser in a call (its mic open): joins, nothing asked",
      wait(lambda: any("Google Chrome joins as Others" in m for m in logged))
      and not any("maybe" in m for m in logged), [m for m in logged if "Chrome" in m])
FakeMeetingAudio.apps_now = []
wait(lambda: any("Google Chrome's audio stops" in m for m in logged))
# A video in Chrome, YouTube (no mic): whatever the Mac plays in a meeting is recorded.
FakeMeetingAudio.apps_now = [dict(CHROME, hears=False, confidence=0.3)]
check("  a video in Chrome (no mic open): joins too, once it has played 2 s",
      wait(lambda: sum("Google Chrome joins as Others" in m for m in logged) == 2), [m for m in logged if "Chrome" in m][-3:])
FakeMeetingAudio.apps_now = []
wait(lambda: sum("Google Chrome's audio stops" in m for m in logged) == 2)

FakeMeetingAudio.apps_now = [dict(HELPER1, confidence=0.95)]
# Waits on what the recorder did (it joined, and asked the capture for that pid), not on
# the capture's target now: under load it restarts after a stall, and target is None
# between a stop and the next start.
check("an app playing through a helper (no bundle id): recorded by its pid",
      wait(lambda: any("pid 401 joins as Others" in m for m in logged) and 401 in FakeMeetingAudio.starts),
      {"starts": FakeMeetingAudio.starts[-4:], "looks": looks[0], "apps": FakeMeetingAudio.apps_now,
       "meeting log": [m for m in logged if m.startswith("meeting:") or "Others" in m][-6:]})
FakeMeetingAudio.apps_now = []
wait(lambda: any("pid 401's audio stops" in m for m in logged))

# A capture that says only .error (the module as it was): paused, its words as they are.
FakeMeetingAudio.fail_with = "Screen & System Audio Recording is off for Standby"
FakeMeetingAudio.apps_now = [dict(TEAMS)]
check("a capture refused, saying only .error: paused, the error as the reason",
      wait(lambda: meet().get("state") == "paused" and meet().get("ok") is False
           and meet().get("reason") == "Screen & System Audio Recording is off for Standby"), meet())
FakeMeetingAudio.fail_with = None
check("  allowed again: recorded on a retry", wait(lambda: meet().get("state") == "recording"), meet())
FakeMeetingAudio.apps_now = []
wait(lambda: meet().get("state") == "waiting")

# With the agreed fields: an error the user must act on, with what to do.
PERM = {"state": "error", "cause": "permission", "reason": "Screen Recording is off",
        "detail": "Allow Standby in System Settings > Privacy & Security > Screen Recording.",
        "past": "Screen Recording was off", "confirm": "Open Privacy & Security", "needs_restart": True}
FakeMeetingAudio.fail_with, FakeMeetingAudio.fail_as = "refused", PERM
FakeMeetingAudio.apps_now = [dict(TEAMS)]
check("the permission off: error (not paused), the capture's own words, confirm and needs_restart",
      wait(lambda: all(meet().get(k) == PERM[k] for k in ("state", "cause", "reason", "detail", "confirm", "needs_restart"))),
      meet())
check("  on record, past tense", wait(lambda: events()[-1]["cause"] == "permission"
                                           and events()[-1]["reason"] == "Screen Recording was off"), events())
asked = len(FakeMeetingAudio.starts)
time.sleep(2.0)        # on purpose: with the mic's backoff (0.5 s, 1 s...) it would have been started twice by now
check("  waiting for the permission, it is not started again (each start was macOS's prompt, over and over)",
      len(FakeMeetingAudio.starts) == asked and any("without asking again" in m for m in logged),
      (asked, len(FakeMeetingAudio.starts), [m for m in logged if "allowed" in m][-2:]))
check("  and the waiting is in the log once, not every try", len([m for m in logged if "without asking again" in m]) == 1,
      [m for m in logged if "without asking again" in m])
SLEEP = {"state": "paused", "cause": "screen_asleep", "reason": "the screen is asleep",
         "detail": "The screen is asleep or locked, so macOS offers nothing to capture. It resumes by itself when the screen wakes.",
         "past": "the screen was asleep"}
FakeMeetingAudio.fail_as = SLEEP
check("the screen asleep: paused (it comes back by itself), no confirm or restart offered",
      wait(lambda: meet().get("cause") == "screen_asleep" and meet().get("state") == "paused")
      and "confirm" not in meet() and "needs_restart" not in meet(), meet())
FakeMeetingAudio.apps_now = []
end_session(th)
check("the session ends cleanly, the capture stopped", not th.is_alive() and all(not m._run for m in made))
check("  every pause on record is closed, the last when its call app went",
      events() and all(e["to"] is not None for e in events()), events())

# Mic only: meeting audio never even asked.
n, looks[0] = len(FakeMeetingAudio.calls), 0
FakeMeetingAudio.apps_now = [dict(ZOOM)]
th, sdir = start_session("mic")
wait(lambda: [s["kind"] for s in sources()] == ["mic"])
time.sleep(1.5)                      # real time: proving a poll never happens needs time to pass
check("Mic only: the meeting's audio is never looked at, no meeting source",
      len(FakeMeetingAudio.calls) == n and looks[0] == 0
      and [s["kind"] for s in sources()] == ["mic"], (len(FakeMeetingAudio.calls) - n, looks[0], sources()))
end_session(th)

# Meeting audio only: no mic at all, the call's audio when it plays.
FakeMeetingAudio.apps_now = []
th, sdir = start_session("meeting")
check("Meeting audio only: no mic, waiting for a call", wait(lambda: [(s["kind"], s["state"]) for s in sources()]
                                                             == [("meeting", "waiting")]), sources())
FakeMeetingAudio.fail_with, FakeMeetingAudio.fail_as = None, {}
FakeMeetingAudio.apps_now = [dict(ZOOM)]
check("  Zoom plays: recorded", wait(lambda: [(s["kind"], s["name"]) for s in sources()] == [("meeting", "Zoom")]),
      sources())
made[-1].stalled = True
FakeMeetingAudio.fail_with, FakeMeetingAudio.fail_as = "asleep", SLEEP
wait(lambda: events() and events()[-1]["to"] is None)
end_session(th)
check("the session ending mid-pause: the pause ends with it", events() and events()[-1]["to"] is not None
      and events()[-1]["cause"] == "screen_asleep", events())
FakeMeetingAudio.fail_with, FakeMeetingAudio.fail_as = None, {}
info = ui_info(sdir)
check("  the app's session info carries it, for the page to show the gap",
      info["events"] and info["events"][-1]["reason"] == "the screen was asleep", info.get("events"))

# The module's own fake (mac/native/macos/appaudio.py): the real module's interface, not ours.
theirs = []


def their_capture():
    cap = ma.FakeMeetingAudio(log=logged.append)
    cap.apps_now = [dict(ZOOM, confidence=0.95, hears=True)]
    theirs.append(cap)
    return cap


sb.MEETING_AUDIO = their_capture
th, sdir = start_session()
check("with the module's own fake: the call joins and is recorded",
      wait(lambda: ("meeting", "recording") in [(x["kind"], x["state"]) for x in sources()]
           and len(theirs) > 1 and theirs[-1].fed > 0), (sources(), [c.fed for c in theirs]))
theirs[-1].stalled = True
check("  it goes quiet: paused, stalled; without words from it, still a reason to show",
      wait(lambda: meet().get("state") == "paused" and meet().get("cause") == "stalled" and meet().get("reason")), meet())
waits = lambda: [float(m.rsplit("trying again in ", 1)[1].rstrip("s")) for m in logged
                 if "still no audio" in m and "trying again in" in m]
check("  restarting it works but still nothing comes: still paused, never 'recording', and it waits longer each time",
      wait(lambda: len(waits()) >= 2) and waits()[1] > waits()[0]
      and meet().get("state") == "paused", (waits(), meet()))
theirs[-1].stalled = False
check("  and back", wait(lambda: meet().get("state") == "recording"), meet())
end_session(th)

# Without meeting audio (not there yet): Automatic is the mic alone.
sb.MEETING_AUDIO = None
th, sdir = start_session()
check("no meeting audio on this Mac (yet): Automatic records the mic, nothing waiting",
      wait(lambda: [s["kind"] for s in sources()] == ["mic"]) and th.is_alive(), sources())
end_session(th)

# The setting, once.
import transport  # noqa: E402
c = {"mode": "both", "loopback_device": "BlackHole 2ch", "capture_automatic": True}
check("migration: BlackHole set up and kept until now: Automatic, the device forgotten",
      transport.migrate_capture(c) and c["mode"] == "auto" and "loopback_device" not in c, c)
c = {"mode": "system", "capture_automatic": True}
check("  BlackHole's system audio alone: Automatic too", transport.migrate_capture(c) and c["mode"] == "auto", c)
c = {"mode": "both"}
check("  'both' without BlackHole (never worked): Automatic", transport.migrate_capture(c) and c["mode"] == "auto")
c = {"mode": "mic", "capture_automatic": True}
check("  Mic only picked in Settings since: stays", not transport.migrate_capture(c) and c["mode"] == "mic")
# ---------------------------------------------------------------- Standby's own is never a meeting
# Its apps by bundle id; a process of this checkout's by its arguments (Jean's
# voice: measured, a Python child playing shows as org.python.python, with no name of its own).
import subprocess  # noqa: E402
check("Standby's apps, by bundle id, are its own", ma.is_standby(1, "dev.standby.dictation")
      and ma.is_standby(1, "dev.standby.app") and not ma.is_standby(1, "us.zoom.xos"))
fake_repo = Path(tempfile.mkdtemp(prefix="sbrepo-"))
(fake_repo / "mac").mkdir()
(fake_repo / "mac" / "tts.py").write_text("import time\ntime.sleep(30)\n")
mine = subprocess.Popen([sys.executable, str(fake_repo / "mac" / "tts.py"), "--voice", "jean"])
other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
time.sleep(0.5)
args = ma.process_args(mine.pid)
check("  a process's arguments, from the kernel", str(fake_repo / "mac" / "tts.py") in args and "--voice" in args, args)
real_repo, ma.REPO_DIR = ma.REPO_DIR, os.path.realpath(str(fake_repo))
ma._KNOWN.clear()
check("  one running this checkout's code (Jean's voice) is Standby's own, though it shows as Python",
      ma.is_standby(mine.pid, "org.python.python") and not ma.is_standby(other.pid, "org.python.python"))
ma.REPO_DIR = real_repo
for p_ in (mine, other):
    p_.kill(); p_.wait()
ma._KNOWN.clear()
listed = [{"bundle": "dev.standby.dictation", "pid": 31, "playing": True, "hears": True, "own": True},
          {"bundle": "us.zoom.xos", "pid": 34, "playing": True, "hears": True, "own": False}]
real_apps, ma.audio_apps = ma.audio_apps, lambda include_silent=True: [dict(a) for a in listed]
check("  the capture's apps() never lists them", [a["bundle"] for a in ma.MeetingAudio().apps()] == ["us.zoom.xos"])
ma.audio_apps = real_apps
check("names in the user's words, never a bundle id: Python, the program's, the bundle's last part",
      ma._readable(0, "org.python.python", []) == "Python" and ma._readable(0, "", ["/usr/bin/afplay", "x"]) == "afplay"
      and ma._readable(0, "com.example.callapp", []) == "Callapp" and ma._readable(0, "", []) == "An app")
check("  and the question too, for an app listed by its bundle id",
      dv.ask_for("meeting", {"bundle": "com.example.meetly", "name": "com.example.meetly"})["text"].startswith("Meetly is playing"))
own = [{"bundle": "dev.standby.dictation", "playing": True, "hears": True, "confidence": 0.7},
       {"bundle": "org.python.python", "playing": True, "hears": True, "confidence": 0.7, "own": True}]
check("the pick: Standby's own playing with the mic open is never recorded nor asked about, even after a yes",
      dv.choose_meeting(own, "auto", {"org.python.python": "yes", "dev.standby.dictation": "yes"}, None)[0::2]
      == (None, None))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
