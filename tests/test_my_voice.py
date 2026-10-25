"""The user's own voice: learnt in calls only, kept in voices.json under "me" (only with
remember_voices on), and once it is known on a mic, a call there ignores other voices.
Fakes only: a stand-in voice print, no model, no mic."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, stat, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbmine-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
import myvoice  # noqa: E402

fails = []
PATIENCE = 30


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


# Stand-in voice prints: the user, someone else, and one in between.
rng = np.random.default_rng(4)
USER = rng.standard_normal(192)
OTHER = rng.standard_normal(192)
OTHER -= (OTHER @ USER) / (USER @ USER) * USER                  # nothing like the user's
BETWEEN = USER / np.linalg.norm(USER) * 0.35 + OTHER / np.linalg.norm(OTHER) * 0.937     # between 0.25 and 0.45
# The user in a room that makes them sound unlike their print (measured: whole meetings
# at 0.26-0.32): like their print only a little, and nothing like anyone else.
_w = rng.standard_normal(192)
for _v in (USER, OTHER):
    _w -= (_w @ _v) / (_v @ _v) * _v
ROOMED = USER / np.linalg.norm(USER) * 0.30 + _w / np.linalg.norm(_w) * 0.954


def noisy(v, k=0.15):
    return v / np.linalg.norm(v) + rng.standard_normal(len(v)) * k / np.sqrt(len(v))


# ---------------------------------------------------------------- the rules
check("who: the user's (>= 0.45), someone else's (< 0.25), between, too short, no print",
      [myvoice.who(0.8, 2), myvoice.who(0.2, 2), myvoice.who(0.35, 2), myvoice.who(0.9, 0.3), myvoice.who(None, 2)]
      == ["you", "other", "unsure", "short", None])

lib = root / "voices.json"
lib.write_text(json.dumps({"voices": {"Alex": {"emb": list(OTHER), "n": 3, "updated": 1}}}))
me = myvoice.MyVoice(lib)
check("no print yet: nothing to match", not me.trusted and me.match(USER) is None)
for _ in range(16):
    me.learn(noisy(USER), 2.0)
check("learnt from %.0f s of the user's speech: trusted" % me.seconds, me.trusted and me.seconds >= myvoice.TRUST, me.seconds)
check("  the user matches it, someone else doesn't, one in between is in between",
      me.match(noisy(USER)) >= myvoice.YOU and me.match(noisy(OTHER)) < myvoice.OTHER
      and myvoice.OTHER <= me.match(BETWEEN) < myvoice.YOU,
      (me.match(noisy(USER)), me.match(noisy(OTHER)), me.match(BETWEEN)))
before = me.seconds
check("  once trusted, someone else in a talk is not learnt", not me.learn(noisy(OTHER), 3.0) and me.seconds == before)
check("  nor an utterance under a second (its print is too noisy)", not me.learn(noisy(USER), 0.6))
saved = json.loads(lib.read_text())
check("kept in voices.json under \"me\", the named voices untouched",
      "me" in saved and len(saved["me"]["emb"]) == 192 and saved["me"]["seconds"] == me.seconds
      and list(saved["voices"]) == ["Alex"], list(saved))
check("  the file is the user's alone (0600)", stat.S_IMODE(os.stat(lib).st_mode) == 0o600)
check("  and a new session reads it back", myvoice.MyVoice(lib).trusted)
check("the user in a room that makes them sound unlike their print: unsure",
      myvoice.who(me.match(noisy(ROOMED)), 2) == "unsure", me.match(noisy(ROOMED)))
# ---------------------------------------------------------------- the recorder, with fakes
MARKS = {0.1: USER, 0.3: OTHER, 0.2: BETWEEN, 0.4: ROOMED}


class FakeInput(object):
    def __init__(self, callback=None, **kw): self.cb, self.run = callback, False
    def start(self):
        self.run = True

        def loop():
            while self.run:
                self.cb(np.full((800, 1), 0.01, "float32"), 800, None, None)
                time.sleep(0.05)
        threading.Thread(target=loop, daemon=True).start()
    def stop(self): self.run = False
    def close(self): pass


DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]), _terminate=lambda: None, _initialize=lambda: None)
from importlib.machinery import SourceFileLoader  # noqa: E402
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
logged = []
sb.log = logged.append
sb.ROOT, sb.SESSIONS, sb.CONFIG = root, root / "sessions", root / "config.json"
sb.load_model = lambda name: None
sb.CONFIG.write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic", "speakers": False}))
made_prints = []


def fake_embed(audio):
    """The stand-in print of an utterance: whose it is, from its samples' value."""
    made_prints.append(len(audio))
    v = MARKS.get(round(float(audio[0]), 2))
    return None if v is None or len(audio) < 8000 else noisy(v)


class Script(object):
    lines = []                     # (text, mark, seconds)
    early = None                   # (start, end, text, audio): words at a short pause, still being said
    talking = False                # an utterance still open
    def __init__(self, *a, **k): self.early_after, self.kind = None, None
    def take_early(self):
        early, Script.early = Script.early, None
        return early
    def feed(self, b):
        if self.kind is None and len(b):
            self.kind = "call" if abs(float(b[0]) - 0.25) < 1e-6 else "mic"
    def drain(self, force=False):
        if self.kind != "mic":                 # the call's channel says nothing here
            return []
        out = [(1.0, 1.0 + s, t, np.full(int(s * 16000), m, "float32")) for t, m, s in Script.lines]
        Script.lines = []
        return out
    def rotate(self): return []
    def _close(self): pass
    @property
    def utt(self): return "open" if Script.talking else None
    def partial(self): return ""


sb.UtteranceTranscriber = Script


def session(kind, mine):
    sb.MY_VOICE, sb.EMBED = mine, fake_embed
    sb._stop.clear()
    th = threading.Thread(target=sb.run_session, args=("t",), kwargs={"no_claude": True, "kind": kind}, daemon=True)
    th.start()
    wait(lambda: (root / "daemon.ready").exists())
    sdir = root / "sessions" / (root / "daemon.ready").read_text().strip()
    return th, sdir


def end(th):
    sb._stop.set()
    th.join(PATIENCE)
    wait(lambda: not (root / "daemon.ready").exists())


def rows(sdir):
    p = sdir / "dictation.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def say(text, mark, seconds=2.0):
    Script.lines.append((text, mark, seconds))


# A call: the user's voice is learnt, and the rows say nothing of whose they are.
(root / "voices.json").unlink()
mine = myvoice.MyVoice(root / "voices.json")
th, sdir = session("talk", mine)
(sdir / "dictate").write_text("voice 3\n")
wait(lambda: any("voice 3: listening" in m for m in logged))
for i in range(8):
    say("Here is something I want to ask about number %d." % i, 0.1, 4.0)
    wait(lambda i=i: any(r.get("text", "").endswith("number %d." % i) for r in rows(sdir)))
check("in a talk, the user's voice is learnt (%.0f s)" % mine.seconds, wait(lambda: mine.trusted), mine.seconds)
check("  and a talk's rows say nothing of whose voice they are",
      not any("who" in r or "match" in r for r in rows(sdir)), rows(sdir)[-2:])
end(th)

# A talk: the voice's own words heard back (its echo, not the user's voice) are never a turn.
th, sdir = session("talk", mine)
(sdir / "dictate").write_text("voice 4\n")
wait(lambda: any("voice 4: listening" in m for m in logged))
sb.VOICE_SAID.append((time.time(), "Sure."))
say("Sure.", 0.3, 1.2)
check("a talk: the voice's own \"Sure.\" heard back, in a voice not the user's: not a turn",
      wait(lambda: any("its own words heard back, not a turn: Sure." in m for m in logged))
      and not any(r.get("text") == "Sure." for r in rows(sdir)), rows(sdir)[-2:])
say("Sure.", 0.1, 1.2)
check("  the user saying it themselves: their turn", wait(lambda: any(r.get("text") == "Sure." for r in rows(sdir))), rows(sdir)[-2:])
say("Let's ship on Friday.", 0.3, 1.5)
check("  someone else's words the voice never said: kept, as before",
      wait(lambda: any(r.get("text") == "Let's ship on Friday." for r in rows(sdir))), rows(sdir)[-2:])
check("  only what it said lately counts, as whole words in order",
      sb.echoes_voice("sure", said=[(time.time(), "Sure.")]) and not sb.echoes_voice("sure", said=[(time.time() - 60, "Sure.")])
      and not sb.echoes_voice("ensure it", said=[(time.time(), "Sure.")]) and sb.echoes_voice("talk soon", said=[(time.time(), "Okay, talk soon!")]))
sb.VOICE_SAID.clear()
end(th)

# A call: once the user's voice is known on this mic too, someone else's words are dropped.
mic = "MacBook Pro Microphone"
check("a call: the user's voice not yet known on this mic (%.1f s of %.0f)" % (mine.inputs.get(mic, 0), myvoice.INPUT_TRUST),
      mine.trusted and not mine.trusted_on(mic), mine.inputs)
th, sdir = session("talk", mine)
(sdir / "dictate").write_text("voice 11\n")
wait(lambda: any("voice 11: listening" in m for m in logged))
for i in range(3):
    say("And one more thing about the plan, part %d." % i, 0.1, 3.0)
    wait(lambda i=i: any(r.get("text", "").endswith("part %d." % i) for r in rows(sdir)))
check("  the user talking on it: known there", wait(lambda: mine.trusted_on(mic)), mine.inputs)
check("  and not on another mic, where it was never heard", not mine.trusted_on("AirPods Pro"))
say("Let's ship on Friday.", 0.3, 2.0)
check("  someone else's words, on it now: not a turn, not shown, logged",
      wait(lambda: any("someone else heard, ignored: Let's ship on Friday." in m for m in logged))
      and not any(r.get("text") == "Let's ship on Friday." for r in rows(sdir)), rows(sdir)[-2:])
Script.talking, Script.early = True, (1.0, 3.0, "We should ship it Friday.", np.full(32000, 0.3, "float32"))
wait(lambda: Script.early is None)                     # the recorder took it, at its short pause
time.sleep(0.3)
check("  and never asked early for either: the same ruling as its words get",
      not any(r.get("likely_turn") and "ship it Friday" in r.get("text", "") for r in rows(sdir)), rows(sdir)[-2:])
Script.talking = False
say("Maybe we should check with the team.", 0.2)
check("  words it can't tell apart: kept (never drop the user)",
      wait(lambda: any(r.get("text") == "Maybe we should check with the team." for r in rows(sdir))), rows(sdir)[-2:])
say("Yes.", 0.3, 0.3)
check("  too short to tell: kept", wait(lambda: any(r.get("text") == "Yes." for r in rows(sdir))), rows(sdir)[-2:])
say("Hello, um, can you hear me?", 0.3, 15.0)          # a TV on: no quiet, so it runs to MAX_UTT
check("  an utterance cut at 15 s (the room never went quiet): kept, though it scores as someone else",
      wait(lambda: any(r.get("text") == "Hello, um, can you hear me?" for r in rows(sdir)))
      and not any("ignored: Hello, um" in m for m in logged), rows(sdir)[-2:])
end(th)
# Whose an utterance is, by its speech alone, and by the user as heard in this call too.
# "Thanks so much." (10/25): 1.9 s of clip, 0.69 s of speech, ruled someone else's.
class Unlike(object):                          # a lasting print the utterance is nothing like
    trusted = True
    def trusted_on(self, name): return True
    def match(self, emb): return 0.1 if emb is not None else None


vi = sb.VoiceIdentity(sdir, {}, "talk", mics=lambda: [types.SimpleNamespace(speaker="Me", device_name=mic)],
                      speakers=lambda: [], seq=lambda: 1, talking=lambda: False)
vi.made, vi.v = True, Unlike()
was_embed, sb.EMBED = sb.EMBED, lambda audio: noisy(OTHER) if len(audio) >= 8000 else None
thanks = np.concatenate([np.zeros(9600), np.full(11200, 0.1), np.zeros(9600)]).astype("float32")   # 1.9 s, 0.7 s spoken
check("its speech, not the quiet around it: 1.9 s of clip, 0.7 s spoken",
      abs(len(sb.spoken_part(thanks)) / 16000.0 - 0.7) < 0.04, len(sb.spoken_part(thanks)) / 16000.0)
check("  too little speech to tell whose: kept (\"Thanks so much.\")", not vi.someone_else(thanks, "Me"))
long_other = np.full(32000, 0.1, "float32")                                                     # 2 s spoken
check("  2 s of speech unlike the user, and nothing of them heard in this call yet: someone else's",
      vi.someone_else(long_other, "Me"))
vi.here[mic] = [np.asarray(OTHER) * 5.0, 5.0]
check("  unlike their lasting print, but like them as heard in this call, on this mic: theirs, kept",
      not vi.someone_else(long_other, "Me") and vi.like_here(noisy(OTHER), "Me") > 0.9)
vi.here[mic] = [np.asarray(USER) * 5.0, 5.0]
check("  unlike both: someone else's", vi.someone_else(long_other, "Me"))
sb.EMBED = was_embed
check("  what it knows of each mic is kept", json.loads((root / "voices.json").read_text())["me"]["inputs"].get(mic, 0) >= myvoice.INPUT_TRUST)
# The app: the user's own voice listed and forgotten on its own; voices.json locked by both.
import socket  # noqa: E402
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); os.environ["STANDBY_UI_PORT"] = str(sock.getsockname()[1]); sock.close()
import ui  # noqa: E402
ui.log = lambda m: None
ui.VOICES = root / "voices.json"
lib = json.loads(ui.VOICES.read_text())
lib["voices"] = {"Alex": {"emb": list(OTHER), "n": 1, "updated": 1}}
ui.VOICES.write_text(json.dumps(lib))
st = ui.get_settings()
check("Settings: the user's own voice listed on its own, not among the named",
      st["my_voice"] and st["my_voice"] >= myvoice.TRUST and st["voices"] == ["Alex"], (st["my_voice"], st["voices"]))
ui.forget_voices(me_only=True)
after = json.loads(ui.VOICES.read_text())
check("  forgotten on its own: \"me\" gone, the named voices kept", "me" not in after and list(after["voices"]) == ["Alex"],
      list(after))
check("  and voice matching starts over", not myvoice.MyVoice(ui.VOICES).trusted)
check("both writers take voices.json.lock", (root / "voices.json.lock").exists())

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
