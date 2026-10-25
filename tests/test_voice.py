"""Voice mode without a mic, a speaker or Claude: when a turn is over (turn.py),
what is spoken and when (voice.py), and the whole conversation loop against a
fake recorder, agent and speaker: listen, think, speak, listen; interrupt,
mute, the recorder stopping. With the Smart Turn model and onnxruntime
present, also its features against Whisper's own and real speech from `say`."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, re, shutil, subprocess, sys, tempfile, threading, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbvoice-"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import numpy as np  # noqa: E402
import turn  # noqa: E402
import voice  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-62s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:500])


def wait(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


# ---------------------------------------------------------------- end of turn
class FakeModel(object):
    def __init__(self, p): self.p, self.calls = p, 0
    def complete(self, audio): self.calls += 1; return self.p


A = np.zeros(1600, "float32")
check("punctuation: finished vs mid-thought", turn.punctuated("about the budget?") and turn.punctuated('he said "no."')
      and not turn.punctuated("what we need to figure out is") and not turn.punctuated("look at the"))
t = turn.Turns(FakeModel(0.9))
check("finished and punctuated: over after the floor, not at once (the user went on 0.0 and 0.2 s after such pauses)",
      t.feed("What is Orion?", A, 100.0) is None and t.tick(100.0 + turn.Turns.FLOOR - 0.05, False) is None
      and t.tick(100.0 + turn.Turns.FLOOR, False) == "What is Orion?" and turn.Turns.FLOOR == 0.4)
t = turn.Turns(FakeModel(0.9))
t.feed("What is Orion?", A, 100.0)
check("  the user going on within it keeps the turn open", t.tick(100.2, True) is None
      and t.feed("And who owns it?", A, 101.0) is None and t.tick(101.4, False) == "What is Orion? And who owns it?")
# A hesitation at the end is mid-thought, whatever the full stop says (2026-10-12).
for words in ("Mm, yeah. You know, uh.", "We should move it to the.", "And then, um.", "I mean.", "Hmm."):
    t = turn.Turns(FakeModel(0.99))
    check("hesitating at the end (%r): the long wait, and no early ask" % words.split()[-1],
          turn.hesitating(words) and t.feed(words, A, 100.0) is None and t.tick(101.9, False) is None
          and t.tick(102.0, False) == words and not turn.Turns(FakeModel(0.99)).likely(words, A))
check("  not a real ending: I think so, you know?, yeah",
      not any(turn.hesitating(w) for w in ("I think so.", "It's hard, you know?", "Yeah.", "We ship Friday.")))
t = turn.Turns(FakeModel(0.2))
check("punctuated but Smart Turn unsure: a short wait", t.feed("Okay.", A, 100.0) is None
      and t.tick(100.9, False) is None and t.tick(101.0, False) == "Okay.")
t = turn.Turns(FakeModel(0.99))
check("no closing punctuation: they are mid-thought, a long wait",
      t.feed("so what I want is", A, 100.0) is None and t.tick(101.9, False) is None and t.tick(102.0, False) == "so what I want is")
t = turn.Turns(FakeModel(0.99))
t.feed("so what I want is", A, 100.0)
check("speaking again holds the turn open", t.tick(105.0, True) is None and t.tick(106.0, True) is None)
check("  and the next utterance decides, over the whole turn",
      t.feed("the deadline.", A, 107.0) is None and t.tick(107.0 + turn.Turns.FLOOR, False) == "so what I want is the deadline.")
t = turn.Turns(FakeModel(0.2))
t.feed("so what I want is", A, 100.0)
t.tick(100.5, True)
check("speech that came to nothing restarts the wait", t.tick(101.0, False) is None
      and t.tick(101.0 + t._wait() - 0.05, False) is None and t.tick(101.0 + t._wait(), False) == "so what I want is")
# The wait is variable (10/25): mid-thought words wait longer the surer Smart Turn is that they
# go on ("One issue is that", p 0.09, was cut after 2.5 s, and they went on, talk-20261023-200116).
waits = {p: turn.Turns(FakeModel(p)) for p in (0.99, 0.5, 0.25, 0.0)}
for p, t in waits.items():
    t.feed("One issue is that", A, 100.0)
check("unpunctuated: OPEN when Smart Turn hears an end, longer the surer it goes on, at most OPEN_MAX",
      [round(t.due - 100.0, 2) for t in waits.values()] == [turn.Turns.OPEN, turn.Turns.OPEN, 2.5, turn.Turns.OPEN_MAX],
      [round(t.due - 100.0, 2) for t in waits.values()])
# likely-final words, still in an open utterance: would they end the turn at once?
check("likely: punctuated, and Smart Turn says finished", turn.Turns(FakeModel(0.9)).likely("We ship Friday.", A))
check("  eager: an end Smart Turn is unsure of (EAGER, below THRESHOLD) is asked early too, and still waits to end",
      turn.Turns(FakeModel(0.35)).likely("We ship Friday.", A) and turn.Turns.EAGER < turn.Turns.THRESHOLD)
check("  a question, even with Smart Turn unsure (it waits only a moment)",
      turn.Turns(FakeModel(0.2)).likely("And who owns it?", A))
check("  not a statement Smart Turn is unsure of (it waits a second, and often goes on)",
      not turn.Turns(FakeModel(0.2)).likely("Okay.", A))
check("  not words with no closing punctuation", not turn.Turns(FakeModel(0.99)).likely("so what I want is", A))
check("  punctuated, with no model: the punctuation is trusted", turn.Turns(None).likely("We ship Friday.", None))
# What a model with no punctuation costs the voice (2026-10-11): every turn
# waits OPEN, Smart Turn is never asked, and the early ask never fires. Measured on the
# user's own log: 2.57 s from their last word to the turn ending, against 0.56 s, and not
# one early ask in those turns. Hence speechmodel.OFFERED.
m = FakeModel(0.99)
t = turn.Turns(m)
lower = "okay sure go ahead and ask"
check("a model that writes no punctuation: every turn waits the long OPEN, however finished",
      t.feed(lower, A, 100.0) is None and t.tick(100.0 + turn.Turns.OPEN - 0.1, False) is None
      and t.tick(100.0 + turn.Turns.OPEN, False) == lower, turn.Turns.OPEN)
check("  Smart Turn is asked, and only ever lengthens it", m.calls == 1, m.calls)
check("  and the early ask never fires, whatever it heard", not turn.Turns(FakeModel(0.99)).likely(lower, A))
_t = turn.Turns(FakeModel(0.99))
_t.feed("Okay, sure. Go ahead and ask.", A, 100.0)
check("  the same words punctuated: over after the floor, and asked early",
      _t.tick(100.0 + turn.Turns.FLOOR, False) is not None
      and turn.Turns(FakeModel(0.99)).likely("Okay, sure. Go ahead and ask.", A))
t = turn.Turns(FakeModel(0.99))
t.feed("so what I want is", A, 100.0)
check("  over the whole turn: what came before, then these words",
      t.likely("the deadline.", A) and t.heard_with("the deadline.") == "so what I want is the deadline."
      and t.heard == "so what I want is")
check("  and it changes nothing: the turn still ends as it would", t.feed("the deadline.", A, 101.0) is None
      and t.tick(101.0 + turn.Turns.FLOOR, False) == "so what I want is the deadline.")
t = turn.Turns(None)
check("no model: trust the punctuation, briefly", t.feed("Thanks.", A, 100.0) is None and t.tick(100.5, False) == "Thanks.")
m = FakeModel(0.9)
t = turn.Turns(m)
t.feed("so the", A, 100.0)
check("Smart Turn is asked when the text is mid-thought too (it sets how long)", m.calls == 1, m.calls)
check("an empty turn is never sent", turn.Turns(FakeModel(1)).tick(200.0, False) is None)
t = turn.Turns(FakeModel(0.2))
check("a question mark: a finished question, even if Smart Turn is unsure (the floor, then)",
      t.feed("Why can't you reach the wikis?", A, 100.0) is None and t.tick(100.35, False) is None
      and t.tick(100.0 + turn.Turns.FLOOR, False) == "Why can't you reach the wikis?")
check("  and the turn remembers when the speech ended", t.last_closed == 100.0)
t = turn.Turns(None)
check("  also without the model", t.feed("Is it ready?", A, 100.0) is None and t.tick(100.0 + turn.Turns.FLOOR, False) == "Is it ready?")

from importlib.machinery import SourceFileLoader
sb = SourceFileLoader("sb", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac", "standby.py")).load_module()
sb.log = lambda m: None


def closes_after(end_silence, quiet):
    ut = sb.UtteranceTranscriber(None, decode=lambda a: "hello there")
    ut.end_silence = end_silence
    rs = np.random.RandomState(2)
    ut.feed(np.zeros(8000, "float32"))
    ut.feed((rs.randn(16000) * 0.2).astype("float32"))
    ut.feed(np.zeros(int(quiet * 16000), "float32"))
    return bool(ut.drain())
check("voice mode: 0.45 s of quiet closes an utterance, 0.6 s otherwise",
      closes_after(0.45, 0.5) and not closes_after(0.6, 0.5))
x = np.concatenate([np.zeros(8000), np.random.RandomState(0).randn(16000) * 0.2, np.zeros(9600)]).astype("float32")
check("trim_tail keeps 0.2 s after the speech", abs(len(turn.trim_tail(x)) - (24000 + 3200)) <= 480, len(turn.trim_tail(x)))
p = turn.prepare(np.ones(16000, "float32"))
check("prepare: 8 s, zero-padded at the start, normalised",
      len(p) == 128000 and abs(float(p.mean())) < 1e-4 and abs(float(p.std()) - 1) < 1e-3 and p[0] < p[-1])
check("features are (80, 800)", turn.log_mel(p).shape == (80, 800))
check("no model file: load says so and returns None", turn.load(root / "missing.onnx") is None)

try:
    import mlx_whisper
    ref = np.load(os.path.join(os.path.dirname(mlx_whisper.__file__), "assets", "mel_filters.npz"))["mel_80"]
    check("mel filters match Whisper's own", float(np.abs(turn.mel_filters() - ref).max()) < 1e-6)
    import mlx.core as mx
    from mlx_whisper.audio import log_mel_spectrogram
    a = turn.prepare(np.random.RandomState(1).randn(40000).astype("float32") * 0.1)
    theirs = np.array(log_mel_spectrogram(mx.array(a), n_mels=80, padding=0)).T[:, :800]
    check("log-mel features match Whisper's (mlx_whisper)", float(np.abs(turn.log_mel(a) - theirs).max()) < 1e-3)
except ImportError:
    print("  (Whisper comparison skipped: no mlx_whisper)")

st = turn.load()
if st is None or not (shutil.which("say") and shutil.which("afconvert")):
    print("  (real-speech Smart Turn part skipped: needs the model, onnxruntime, say and afconvert)")
else:
    def speech(text, name):
        aiff, wav = str(root / (name + ".aiff")), str(root / (name + ".wav"))
        subprocess.run(["say", "-o", aiff, text], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True)
        import wave
        w = wave.open(wav)
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0
        return np.concatenate([a, np.zeros(9600, "float32")])     # the recorder's 0.6 s of quiet
    for i, text in enumerate(("What time is the design review tomorrow?", "Thanks, that's all I needed.")):
        audio = speech(text, "s%d" % i)
        t0 = time.time()
        pr = st.complete(audio)
        check("Smart Turn: %r sounds finished (%.2f, %.0f ms)" % (text, pr, (time.time() - t0) * 1000), pr > 0.5)
    t = turn.Turns(st)
    check("a real finished question ends the turn at once",
          t.feed("Can you summarize what Nadia said about the budget?",
                 speech("Can you summarize what Nadia said about the budget?", "s3"), 100.0) is not None)

# ---------------------------------------------------------------- what is spoken
S = voice.speakable
check("speech: markdown and links become plain words",
      S("**Orion** is the [routing cache](https://x.io/a) — see `cache.py`.") == "Orion is the routing cache — see cache.py.",
      S("**Orion** is the [routing cache](https://x.io/a) — see `cache.py`."))
check("speech: file references and URLs are not read out",
      S("It moved in Q2 (notes/orion.md). Details: https://wiki/x") == "It moved in Q2. Details:", S("It moved in Q2 (notes/orion.md). Details: https://wiki/x"))
check("speech: bullets and headings lose their marks", S("## Plan\n- ship Friday\n2. test") == "Plan ship Friday test")
check("speech: nothing worth saying is nothing", S("---") == "" and S("  ** ") == "")


def stream(pieces):
    ss, out = voice.SentenceStream(), []
    for piece in pieces:
        out += ss.feed(piece)
    return out + ss.flush()


check("sentences come out as soon as each is whole",
      voice.SentenceStream().feed("Orion is the cache. It repl") == ["Orion is the cache."])
check("pieces split mid-word still make whole sentences",
      stream(["Or", "ion is the ca", "che. It re", "placed Redis!", " Done"]) == ["Orion is the cache.", "It replaced Redis!", "Done"])
check("numbers and abbreviations do not split",
      stream(["Opus 5.5 is out, e.g. in Claude Code. Ask Dr. Lee."]) == ["Opus 5.5 is out, e.g. in Claude Code.", "Ask Dr. Lee."])
check("a line break ends a sentence", stream(["First line\nSecond line"]) == ["First line", "Second line"])
check("speech stops at a --- line: the rest is for reading",
      stream(["Drafted it below.\n", "--", "-\nDear team, ", "the plan is..."]) == ["Drafted it below."])
check("and at a code fence", stream(["Here it is.\n```py\nprint(1)\n```"]) == ["Here it is."])
check("a dash that is only an aside is spoken", stream(["Yes - mostly."]) == ["Yes - mostly."])
# the answer's first clause goes before its first sentence is whole.
check("first clause first: said at its comma, before the sentence is written",
      voice.SentenceStream().feed("The migration is on track, the cache") == ["The migration is on track,"])
check("  then the rest a sentence at a time, never cut again",
      stream(["The migration is on track: ", "the cache moved on Monday, and the old cluster goes Friday. ",
              "Next, the runbook, which nobody owns."]) ==
      ["The migration is on track:", "the cache moved on Monday, and the old cluster goes Friday.",
       "Next, the runbook, which nobody owns."])
check("  not after a word or two: \"Sure,\" waits for its sentence",
      voice.SentenceStream().feed("Sure, the short answer") == [] and stream(["Sure, the short answer is yes."]) ==
      ["Sure, the short answer is yes."])
check("  a number's comma is no clause", voice.SentenceStream().feed("It costs about 4,500 per") == [])
check("  a whole first sentence comes out as before", stream(["Opus 5.5 is out, e.g. in Claude Code. Ask Dr. Lee."])
      == ["Opus 5.5 is out, e.g. in Claude Code.", "Ask Dr. Lee."])
check("  and a dash with spaces counts as a clause", voice.SentenceStream().feed(
      "There are three options \u2014 the first is") == ["There are three options \u2014"])

# ---------------------------------------------------------------- what Claude is doing, in plain words
F = voice.friendly_step
steps = {
    'Running curl -s -m 8 "https://wttr.in/?format=%l:+%C,+%t"': "Looking it up online",
    "Running standby-ctx search 'pricing'": "Checking the transcript",
    "Running ls -la ~/code": "Running a command",
    "Reading ~/work/notes/orion-rollout.md": "Reading orion-rollout.md",
    "Searching for 'Orion' in ~/work": "Searching your files",
    # MCP tools: from the tool's own name, whatever the server (no list of tools).
    "Using mcp__github__search_code": "Searching code",
    "Using mcp__docs-mcp__ReadDocument": "Reading document",
    "Using mcp__calendar__list_events": "Listing events",
    "Using mcp__mail__email_search": "Email search",
    "Using mcp__tracker__TrackerGetIssue": "Getting issue",
    "Using mcp__notes__search": "Searching notes",
    "Using mcp__some-new-mcp__frobnicate": "Using some new",
    "Using WebSearch": "Searching the web",
    "Loading skill meeting-standby": "Using a skill",
    "Asking the meeting agent": "",
}
got = {k: F(k) for k in steps}
check("tool steps become a few plain words (the full step stays in the chat)", got == steps,
      {k: v for k, v in got.items() if v != steps[k]})

# ---------------------------------------------------------------- the conversation
class FakeSpeaker(object):
    """Speaks each sentence for 0.15 s; records them."""
    def __init__(self):
        self.said, self.stops, self._until, self.current, self._q = [], 0, 0.0, "", []
        self.started_at = None
    def say(self, text):
        self.said.append(text); self._q.append(text)
    def stop(self):
        self.stops += 1; self._q = []; self._until = 0.0; self.current = ""
    def _tick(self):
        if time.time() >= self._until and self._q:
            self.current = self._q.pop(0); self._until = time.time() + 0.15; self.started_at = time.time()
    @property
    def speaking(self):
        self._tick(); return time.time() < self._until
    @property
    def busy(self):
        self._tick(); return bool(self._q) or time.time() < self._until


sdir = root / "sessions" / "m1"
sdir.mkdir(parents=True)
rows_path = sdir / "dictation.jsonl"
rows_path.write_text(json.dumps({"seq": 1, "text": "older dictation"}) + "\n")
recorder = {"on": True, "partial": "", "level": 0.2}


def fake_recorder():
    while recorder["on"]:
        # Match run_session's atomic status publication. An in-place write can
        # expose an empty file and invent a moment when the user is silent.
        status = sdir / ("live-%d.tmp" % threading.get_ident())
        status.write_text(json.dumps({"at": time.time(), "level": recorder["level"],
                                      "partial": recorder["partial"],
                                      "speaking": recorder.get("speaking", False)}))
        status.replace(sdir / "live.json")
        time.sleep(0.05)


threading.Thread(target=fake_recorder, daemon=True).start()
asked, cancelled, logged, heards = [], [], [], []
mode = {"slow": False, "think": 0}


handed = []


def fake_ask(question, on_event, on_queued, heard=None):
    # How this answer goes, read before it shows in `asked`. A test changes `mode` as soon
    # as it sees the ask (mode["think"] = 0 right after "thinking"), and this thread could
    # reach a later read after that: the answer then came at once, nothing was left to
    # cancel, and "more to the question" failed (rare: a worker 60 ms slower fails it every time).
    slow, think, hand = mode["slow"], mode["think"], mode.get("handoff")
    asked.append(question)
    heards.append(heard)
    qid = "q%d" % len(asked)
    on_queued(qid)
    if slow:
        on_event({"kind": "delta", "text": "Let me look into it. "})
        end = time.time() + 5
        while time.time() < end and qid not in cancelled:
            time.sleep(0.02)
        on_event({"kind": "delta", "text": "Here is more. And more."})      # arrives after the interrupt
        return "(stopped)"
    on_event({"kind": "tool", "text": "Reading ~/notes/orion.md"})
    end = time.time() + 0.15 + think
    while time.time() < end and qid not in cancelled:      # as the agent: a cancelled question is dropped
        time.sleep(0.02)
    if qid in cancelled:
        return "(stopped)"
    if hand:                                   # as a call's ask: its words, then the work handed to the session
        on_event({"kind": "delta", "text": "Sure, on it. "})
        handed.append(question)
        return ""
    for piece in ("Orion is the routing ", "cache. It replaced Redis in Q2.\n", "---\n", "```py\ncode()\n```"):
        on_event({"kind": "delta", "text": piece})
        time.sleep(0.05)
    return "Orion is the routing cache. It replaced Redis in Q2.\n---\ncode"


spk = FakeSpeaker()
vs = voice.VoiceSession("m1", sdir, ask=fake_ask, cancel=cancelled.append, speaker=spk, log=logged.append)
vs.GUARD = 0.1
vs.start()
ctl = lambda: (sdir / "dictate").read_text().split()
check("start: the recorder is told to listen, with this conversation's number",
      ctl() == ["voice", str(vs.seq)] and vs.status()["state"] == "listening")


def row(**kw):
    with open(rows_path, "a") as fh:
        fh.write(json.dumps(dict(kw, seq=vs.seq)) + "\n")


row(turn=0, text="So what is")
recorder["partial"] = "Orion"
check("listening: captions show what was heard, words in progress too",
      wait(lambda: vs.status()["you"] == "So what is Orion", 2), vs.status())
check("  and the orb follows the mic", vs.status()["level"] == 0.2)
check("  an older dictation's rows are not ours", "older" not in vs.status()["you"])
recorder["partial"] = ""
row(turn=0, end_turn=True, text="So what is Orion?")
check("the turn is over: Claude gets it, and the mic stays the user's while it thinks",
      wait(lambda: asked == ["So what is Orion?"], 2) and ctl() == ["voice", str(vs.seq)], (asked, ctl()))
check("  thinking, saying in plain words what Claude is doing", wait(lambda: vs.status()["state"] == "thinking", 1)
      and wait(lambda: vs.status()["step"] == "Reading orion.md", 1) and vs.status()["claude"] == "", vs.status())
check("  speaking, each sentence as it is whole", wait(lambda: vs.status()["state"] == "speaking", 2))
check("  and the mic is held from Claude's first word, as its floor (words over it are kept)",
      ctl() == ["floor", str(vs.seq)], ctl())
check("  then the mic is the user's again", wait(lambda: vs.status()["state"] == "listening", 4)
      and ctl() == ["voice", str(vs.seq)], (vs.status(), ctl()))
check("  only the spoken part was spoken: not the written part after ---",
      spk.said == ["Orion is the routing cache.", "It replaced Redis in Q2."], spk.said)
check("  and the captions are cleared for the next turn", vs.status()["you"] == "" and vs.status()["claude"] == ""
      and vs.status()["step"] == "")

mode["slow"] = True
spk.said = []
row(turn=1, end_turn=True, text="Dig into the outage.")
wait(lambda: vs.status()["state"] == "speaking", 3)
vs.interrupt()
check("interrupt: speech stops and the question is cancelled in the agent",
      wait(lambda: cancelled == ["q2"], 2) and spk.stops >= 1, (cancelled, spk.stops))
check("  it listens again right away", wait(lambda: vs.status()["state"] == "listening", 2) and ctl()[0] == "voice")
time.sleep(0.4)
check("  words still arriving from the cancelled answer are never spoken",
      spk.said == ["Let me look into it."], spk.said)
mode["slow"] = False

check("each turn logs where the time went", any("voice timing:" in m and "Claude's first word" in m
                                                 for m in logged), logged)
check("  with no marks from the agent (an older one): its parts said as unknown, and no early ask",
      any(m.startswith("voice timing:") and "| ask: - | early: none" in m for m in logged),
      [m for m in logged if m.startswith("voice timing:")][-1:])

# the ask in its parts. agentctl's marks beside the agent's: the question goes through the
# agent's spool from this process (no command started on the turn's path), the answer back.
import agentctl  # noqa: E402
spool_root = root / "spool-root"


class _T(object):
    root = spool_root
    def agent_alive(self): return True


def fake_server(answers):
    """The agent's server, for one question: its timing, a delta, then the answer."""
    spool = spool_root / "agent" / "spool"
    end = time.time() + 10
    while time.time() < end:
        qs = [p for p in spool.glob("*") if p.suffix in (".q", ".vq")] if spool.exists() else []
        if qs:
            q = qs[0]
            ident = q.stem
            answers.append((q.suffix, q.read_text()))
            q.unlink()
            with open(spool / (ident + ".p"), "a") as fh:
                fh.write(json.dumps({"kind": "timing", "queued": 4999.99, "taken": 5000.02, "sent": 5000.03,
                                     "word": 5000.75}) + "\n")
                fh.write(json.dumps({"kind": "delta", "text": "Yes."}) + "\n")
            time.sleep(0.05)
            (spool / (ident + ".a.tmp")).write_text("Yes.")      # as the agent's _answer: all at once
            (spool / (ident + ".a.tmp")).rename(spool / (ident + ".a"))
            return
        time.sleep(0.01)


got_q = []
threading.Thread(target=fake_server, args=(got_q,), daemon=True).start()
seen_ev = []
a0 = time.time()
ans, _err = agentctl._ask(_T(), "[VOICE] x", None, None, seen_ev.append, lambda qid: None)
kinds = [e.get("kind") for e in seen_ev]
spawn = next((e for e in seen_ev if "app_spawned" in e), {})
queued = next((e for e in seen_ev if "app_queued" in e), {})
agent_t = next((e for e in seen_ev if e.get("kind") == "timing" and "word" in e), {})
check("a voice question through the agent's spool, from this process: its own file kind, answered",
      ans == "Yes." and got_q == [(".vq", "[VOICE] x")], (ans, got_q))
check("  agentctl marks when it asked and queued it, on this Mac's clock",
      a0 <= spawn.get("app_spawned", 0) <= queued.get("app_queued", 0) <= time.time(), seen_ev)
check("  the agent's marks passed on, with when they arrived here", agent_t.get("sent") == 5000.03
      and agent_t.get("app_at", 0) >= queued.get("app_queued", 1e12), agent_t)
check("  all before the first word", kinds.index("delta") > max(i for i, k in enumerate(kinds) if k == "timing"), kinds)
check("  and the answer's files tidied", not list((spool_root / "agent" / "spool").glob("*.a"))
      and not list((spool_root / "agent" / "spool").glob("*.p")))
typed_ev = []
threading.Thread(target=fake_server, args=(got_q,), daemon=True).start()
agentctl._ask(_T(), "what is this", "talk-1", None, typed_ev.append, lambda qid: None)
check("  a typed question: a file of its own kind, and none of the app's marks (its askers expect text in every event)",
      got_q[-1] == (".q", "what is this") and not any("app_spawned" in e or "app_queued" in e for e in typed_ev), typed_ev)


class _Gone(_T):
    def agent_alive(self): return False


# The agent ends its answer between the client's two looks: its last words, then the answer
# file, just after the client read the progress so far. Those words come all the same.
import pathlib  # noqa: E402
spool = spool_root / "agent" / "spool"
looks, real_exists = [], pathlib.Path.exists


def racing_exists(self):
    """The agent at work between the client's looks: its first words after the client's first
    look at the progress; its last words and its answer just after the second."""
    if self.suffix == ".a" and self.parent == spool:
        looks.append(self.stem)
        with open(spool / (self.stem + ".p"), "a") as fh:
            fh.write(json.dumps({"kind": "delta", "text": "Hi. " if len(looks) == 1 else "First, do you want mock three?"}) + "\n")
        if len(looks) == 2:
            (spool / (self.stem + ".a")).write_text("Hi. First, do you want mock three?")
    return real_exists(self)


pathlib.Path.exists = racing_exists
race_ev = []
ans, _ = agentctl._ask(_T(), "[VOICE] greet", None, None, race_ev.append, None)
pathlib.Path.exists = real_exists
for leftover in spool.glob("*.vq"):
    leftover.unlink()
check("the agent's last words written just before its answer: never skipped",
      "".join(e.get("text", "") for e in race_ev if e.get("kind") == "delta") == "Hi. First, do you want mock three?",
      race_ev)
# A progress line read while it is still being written: taken once whole, not skipped.
half = []


def halves():
    end = time.time() + 5
    while time.time() < end:
        qs = [p for p in spool.glob("*.vq")]
        if qs:
            ident = qs[0].stem
            qs[0].unlink()
            line = json.dumps({"kind": "delta", "text": "All of it."}) + "\n"
            with open(spool / (ident + ".p"), "a") as fh:
                fh.write(line[:9])
            time.sleep(0.2)                        # several of the client's looks, the line still half there
            with open(spool / (ident + ".p"), "a") as fh:
                fh.write(line[9:])
            time.sleep(0.1)
            (spool / (ident + ".a.tmp")).write_text("All of it.")
            (spool / (ident + ".a.tmp")).rename(spool / (ident + ".a"))
            return
        time.sleep(0.005)


threading.Thread(target=halves, daemon=True).start()
agentctl._ask(_T(), "[VOICE] half", None, None, half.append, None)
check("  a line half written when read: taken once whole", [e.get("text") for e in half if e.get("kind") == "delta"]
      == ["All of it."], half)
agentctl.GONE_FOR = 0.2
ans, why = agentctl._ask(_Gone(), "[VOICE] anyone?", None, None, None, None)
check("  the agent's server gone: no answer, and why", ans is None and "not running" in why, (ans, why))
agentctl.GONE_FOR = 10.0

marks = {"app_spawned": 10.05, "app_queued": 10.40, "agent_queued": 5000.0, "taken": 5000.02, "sent": 5000.03,
         "word": 5000.75}
parts = voice.ask_parts(10.0, marks, 11.20)
check("ask_parts: app, start, queue, prep, engine, back, each on one clock",
      parts == "app 0.05s, start 0.35s, queue 0.02s, prep 0.01s, engine 0.72s, back 0.05s", parts)
check("  and they add up to the first word's wait (1.20 s)",
      abs(sum(float(x.split()[1].rstrip("s")) for x in parts.split(", ")) - 1.20) < 0.011, parts)
check("  whatever the two clocks say: the agent's an hour off, the same parts",
      voice.ask_parts(10.0, dict(marks, agent_queued=1400.0, taken=1400.02, sent=1400.03, word=1400.75), 11.20) == parts)
check("  a mark missing (an older agent): unknown, not a guess",
      voice.ask_parts(10.0, dict(marks, sent=None), 11.20) == "-" and voice.ask_parts(10.0, {}, 11.2) == "-")
mode["think"] = 2.2
row(turn=2, end_turn=True, text="Think hard about it.")
# Waits for the answer, not for a length of time, and says so if it never came: on a
# loaded Mac the 2.2 s of thinking can run well past a fixed 5 s window.
answered = wait(lambda: vs.status()["state"] == "speaking", 20) and wait(lambda: vs.status()["state"] == "listening", 20)
check("a slow answer: answered once it comes", answered, answered)
mode["think"] = 0
vs.mute(True)
check("mute: the recorder holds the mic", ctl() == ["hold", str(vs.seq)] and vs.status()["state"] == "muted")
vs.mute(False)
check("unmute: listening again", ctl() == ["voice", str(vs.seq)] and vs.status()["state"] == "listening")
mode["slow"] = True
spk.said, stops = [], spk.stops
row(turn=3, end_turn=True, text="Dig into the outage again.")
wait(lambda: vs.status()["state"] == "speaking", 3)
vs.mute(True)
check("mute while the voice speaks (the orb's pause mid-sentence): it stops at once, and stays paused",
      spk.stops > stops and wait(lambda: vs.status()["state"] == "muted", 2) and ctl() == ["hold", str(vs.seq)],
      (spk.stops, vs.status(), ctl()))
time.sleep(0.4)
check("  nothing more of that answer is spoken while paused", spk.said == ["Let me look into it."], spk.said)
mode["slow"] = False
vs.mute(False)
check("  unpaused: listening again", wait(lambda: vs.status()["state"] == "listening", 2) and ctl()[0] == "voice")
# Between two sentences (the first said, the next not come yet: nothing playing, it still has the floor).
mode["slow"] = True
spk.said, stops, n = [], spk.stops, len(cancelled)
row(turn=4, end_turn=True, text="And the one before?")
check("  (the gap: the first sentence said, nothing playing, \"thinking\" while it keeps the floor)",
      wait(lambda: spk.said and not spk.busy and vs.state == "thinking" and getattr(vs._q, "floor", False), 3), (vs.state, spk.said))
vs.mute(True)
check("mute between two sentences of an answer: stopped too, not left to say the rest",
      spk.stops > stops and wait(lambda: len(cancelled) > n, 2) and wait(lambda: vs.status()["state"] == "muted", 3),
      (spk.stops, cancelled, vs.status()))
mode["slow"] = False
vs.mute(False)
wait(lambda: vs.status()["state"] == "listening", 2)
# Paused while it's still working out its reply (thinking, nothing said yet): that reply answers words from
# before the pause, so it's dropped, never said after Resume; the next turn says they never heard it.
mode["think"] = 1.0
spk.said, n = [], len(cancelled)
row(turn=5, end_turn=True, text="Okay, good. I'm going to do it right now.")
check("  (thinking, nothing said yet)", wait(lambda: vs.state == "thinking" and vs._q is not None, 3), vs.state)
vs.mute(True)
check("mute while it's still thinking: the turn runs on, silenced (never cancelled)",
      wait(lambda: vs._q is None, 4) and len(cancelled) == n and wait(lambda: vs.status()["state"] == "muted", 3),
      (cancelled[n:], vs.status()))
vs.mute(False)
time.sleep(0.6)
check("  its reply is never said, nor after Resume", spk.said == [] and vs.status()["state"] == "listening", (spk.said, vs.status()))
check("  the next turn says they never heard it", vs._cut == "" and voice.with_cut("q", vs._cut).startswith(
      "(They went on before hearing your answer.)"), vs._cut)
vs._cut = None
# The same, on a turn whose answer hands work on ("build the film", then Pause): the work still goes
mode["handoff"] = True
spk.said, n, h = [], len(cancelled), len(handed)
row(turn=6, end_turn=True, text="Build the film.")
check("  (thinking)", wait(lambda: vs.state == "thinking" and vs._q is not None, 3), vs.state)
vs.mute(True)
check("mute while it thinks on a hand-off: the hand-off still reaches the session",
      wait(lambda: len(handed) > h, 4) and handed[-1] == "Build the film." and len(cancelled) == n, (handed[h:], cancelled[n:]))
wait(lambda: vs._q is None, 3)
vs.mute(False)
time.sleep(0.6)
check("  and nothing of it is said, nor after Resume", spk.said == [], spk.said)
mode["handoff"] = False
mode["think"] = 0

# News announced but not said in time (a turn of theirs came first): a moment gone, dropped, its teller told.
dropped_news = []
vs.ANNOUNCE_STALE = 0.3
vs._told.append(("Late news.", time.time() - 1.0, lambda: dropped_news.append(1)))
spk.said = []
time.sleep(0.5)
check("news not said in time is dropped, never said late, and its teller is told", spk.said == [] and dropped_news == [1]
      and not vs._told, (spk.said, dropped_news))
vs.ANNOUNCE_STALE = voice.VoiceSession.ANNOUNCE_STALE

row(done=True)
check("the recording stopped: voice mode ends and says why",
      wait(lambda: vs.status()["state"] == "off", 2) and vs.status().get("error") == "recording stopped", vs.status())

vs2 = voice.VoiceSession("m1", sdir, ask=fake_ask, cancel=cancelled.append, speaker=spk)
vs2.STALE = 0.6
vs2.start()
recorder["on"] = False
check("a recorder that goes quiet ends voice mode", wait(lambda: vs2.status()["state"] == "off", 3), vs2.status())
vs3 = voice.VoiceSession("m1", sdir, ask=fake_ask, cancel=cancelled.append, speaker=spk)
recorder["on"] = True
threading.Thread(target=fake_recorder, daemon=True).start()
vs3.start()
vs3.stop()
check("stop: the recorder is told, voice mode is off", (sdir / "dictate").read_text().split() == ["off", str(vs3.seq)]
      and vs3.status()["state"] == "off" and "error" not in vs3.status())

# A talk: it ends after a while with nothing said, and tells the app.
ended, flags = [], {"busy": False, "dictating": False}


def talk(idle=0.8):
    t = voice.VoiceSession("t1", sdir, ask=fake_ask, cancel=cancelled.append, speaker=spk, idle_end=idle,
                           busy=lambda: flags["busy"], on_end=ended.append, dictating=lambda: flags["dictating"])
    t.start()
    return t


t0 = time.time()
t = talk()
check("talk: nothing said for a while, the conversation ends", wait(lambda: t.status()["state"] == "off", 3)
      and 0.7 < time.time() - t0 < 2.0 and "nothing said" in t.status().get("error", ""), (time.time() - t0, t.status()))
check("  and the app is told, to end the talk", ended and "nothing said" in (ended[-1] or ""), ended)
t = talk()
t.mute(True)
time.sleep(1.5)
check("  muted (the user paused the call, typing to Claude Code meanwhile): it goes on past the quiet while",
      t.status()["state"] == "muted" and voice.VoiceSession.MUTED_IDLE >= 1800, t.status())
t.MUTED_IDLE = 1.5
check("  muted time still counts, up to its own longer while", wait(lambda: t.status()["state"] == "off", 3), t.status())
recorder["speaking"] = True
t = talk()
time.sleep(1.5)
check("  speaking keeps it going", t.status()["state"] == "listening", t.status())
recorder["speaking"] = False
check("  until it goes quiet", wait(lambda: t.status()["state"] == "off", 3), t.status())
recorder["speaking"] = True
t = talk()
t.mute(True)
t.MUTED_IDLE = 1.0
check("  talking while muted is not for Claude: it still ends", wait(lambda: t.status()["state"] == "off", 3), t.status())
recorder["speaking"] = False
flags["busy"] = True
t = talk(idle=0.6)
time.sleep(1.2)
check("  a typed answer being written pauses the clock", t.status()["state"] == "listening", t.status())
flags["busy"] = False
check("  and it ends once that is done", wait(lambda: t.status()["state"] == "off", 3), t.status())
n = len(ended)
t = talk(idle=None)
t.stop()
check("talk: ended by the user, the app is told too", len(ended) == n + 1 and ended[-1] is None, ended[n:])
t = talk(idle=None)
row_seq = t.seq
with open(rows_path, "a") as fh:
    fh.write(json.dumps({"seq": row_seq, "done": True}) + "\n")
wait(lambda: t.status()["state"] == "off", 3)
check("  but not when the recording stopped under it (the app did that)", len(ended) == n + 1, ended[n:])
t = talk(idle=None)
t.stop(by_app=True)
check("  nor when the app stops it (a meeting starting ends the talk itself)", len(ended) == n + 1, ended[n:])

t = talk(idle=None)
flags["dictating"] = True
check("Standby Dictation listening: voice mode holds the mic, paused",
      wait(lambda: t.status()["state"] == "paused", 2) and (sdir / "dictate").read_text().split() == ["hold", str(t.seq)],
      t.status())
flags["dictating"] = False
check("  and listens again once it is done", wait(lambda: t.status()["state"] == "listening", 2)
      and (sdir / "dictate").read_text().split() == ["voice", str(t.seq)], t.status())
t.mute(True)
flags["dictating"] = True
time.sleep(0.3)
flags["dictating"] = False
check("  muted before: still muted after", wait(lambda: t.status()["state"] == "muted", 2)
      and (sdir / "dictate").read_text().split() == ["hold", str(t.seq)], t.status())
t.stop()
recorder["on"] = False

# Settings > Voice: the installed English voices fit to talk in, the best first.
ATTRS = {"com.apple.voice.compact.en-US.Samantha": ("Samantha", "en_US"),
         "com.apple.voice.premium.en-GB.Malcolm": ("Malcolm", "en_GB"),
         "com.apple.voice.premium.en-US.Ava": ("Ava", "en_US"),
         "com.apple.voice.enhanced.en-AU.Lee": ("Lee", "en_AU"),
         "com.apple.speech.synthesis.voice.Bells": ("Bells", "en_US"),
         "com.apple.eloquence.en-US.Grandpa": ("Grandpa (English (US))", "en_US"),
         "com.apple.voice.premium.fr-FR.Amelie": ("Amelie", "fr_FR")}
attrs = lambda vid: {"VoiceName": ATTRS[vid][0], "VoiceLocaleIdentifier": ATTRS[vid][1]}
vs = voice.english_voices(list(ATTRS), attrs)
check("voices: English ones fit to talk in, Premium first, then Enhanced, US first",
      [(v["name"], v["quality"]) for v in vs] == [("Ava", "premium"), ("Malcolm", "premium"), ("Lee", "enhanced"),
                                                  ("Samantha", "standard")] and vs[0]["lang"] == "en-US", vs)
check("  no novelty or retro voices, no other languages", not any(v["name"] in ("Bells", "Amelie") or "Grandpa" in v["name"]
                                                                  for v in vs))
check("voice in use: the one chosen, by id or name", voice.voice_in_use(vs, "com.apple.voice.enhanced.en-AU.Lee")["name"] == "Lee"
      and voice.voice_in_use(vs, "Malcolm")["name"] == "Malcolm" and voice.voice_in_use(vs, "ava (premium)")["name"] == "Ava")
check("  automatic: the best installed", voice.voice_in_use(vs)["name"] == "Ava"
      and voice.voice_in_use(vs, "Not Installed")["name"] == "Ava")
check("  none better than standard: the system's own voice",
      voice.voice_in_use([v for v in vs if v["quality"] == "standard"]) ==
      {"id": None, "name": "System voice", "quality": "standard", "lang": ""})

# A call's voice: the recorder plays it, the one voice of a call. Never another voice instead
# (the user's rule): when it can't play, the answers are shown, and the app says why.
ddir = root / "sessions" / "d1"
ddir.mkdir(parents=True)
rs = voice.RecorderSpeaker(ddir)
rs.bind(42)


def recorder_says(**v):
    (ddir / "live.json").write_text(json.dumps({"at": time.time(), "voice": dict(v)}))


recorder_says(speaks=True, aec=True, saying="", done=0)
rs.say("Orion is the cache.")
rs.say("It replaced Redis.")
sent = [json.loads(l) for l in (ddir / "speak.jsonl").read_text().splitlines()]
check("recorder speaker: sentences go to the recorder, numbered, for this conversation (its voice chosen first)",
      sent == [{"begin": True, "seq": 42}, {"id": 1, "say": "Orion is the cache.", "seq": 42}, {"id": 2, "say": "It replaced Redis.", "seq": 42}],
      sent)
check("  busy until the recorder has said them all", rs.busy and not rs.speaking)
recorder_says(speaks=True, aec=True, saying="It replaced Redis.", since=time.time(), done=1)
check("  speaking: what the recorder is saying now", rs.speaking and rs.current == "It replaced Redis." and rs.busy)
recorder_says(speaks=True, aec=True, saying="", done=2)
check("  done", not rs.busy and not rs.speaking and rs.voice_failed == "")
rs.stop()
sent = [json.loads(l) for l in (ddir / "speak.jsonl").read_text().splitlines()]
check("  and stop goes there too", sent[-1:] == [{"stop": True, "seq": 42}], sent[-1:])
recorder_says(speaks=True, aec=True, saying="", done=2, tts={"state": "failed", "failed": "Standby's voice failed (no model)"})
check("Standby's voice failed in the recorder: the app is told why, for the orb and the window",
      rs.voice_failed == "Standby's voice failed (no model)", rs.voice_failed)
recorder_says(speaks=True, aec=True, saying="", done=2, tts={"state": "starting", "retrying_since": 1792600000.5})
check("Standby's voice coming back in the recorder: the app is told since when", rs.voice_retrying_since == 1792600000.5
      and rs.voice_failed == "", (rs.voice_retrying_since, rs.voice_failed))
recorder_says(speaks=True, aec=True, saying="", done=2, tts={"state": "ready"})
check("  back: nothing", rs.voice_retrying_since is None)
(ddir / "live.json").write_text(json.dumps({"at": time.time(), "voice": {"mode": "voice", "half": True, "why": "no audio engine here"}}))
n = len((ddir / "speak.jsonl").read_text().splitlines())
rs.begin()
rs.say("Nobody to say it.")
check("a recorder that can't play: nothing said, by anyone, and not waited on",
      not rs.busy and len((ddir / "speak.jsonl").read_text().splitlines()) == n + 1, rs.busy)
check("  and it says why", rs.voice_failed == "no audio engine here", rs.voice_failed)

# The recorder's engine stops mid-answer: what it took and never said is dropped, never said by
# another voice; the recorder makes its engine again, or says it can't.
dd = root / "sessions" / "d2"
dd.mkdir(parents=True)
rs2 = voice.RecorderSpeaker(dd)
rs2.bind(43)
playing = lambda **v: (dd / "live.json").write_text(json.dumps({"at": time.time(), "voice": dict(v)}))
playing(speaks=True, aec=True, saying="", done=0)
rs2.begin()
for text in ("One.", "Two.", "Three."):
    rs2.say(text)
playing(speaks=True, aec=True, saying="Two.", since=time.time(), done=1)
check("the recorder plays them", rs2.busy)
playing(mode="voice", half=True, why="its voice stopped working")
check("  its engine stops, and says so: the two it never said are dropped, nothing waits",
      not rs2.busy and rs2.voice_failed == "its voice stopped working", (rs2.busy, rs2.voice_failed))
rs3 = voice.RecorderSpeaker(dd)
rs3.bind(44)
rs3.GONE_AFTER = 0.6
playing(speaks=True, aec=True, saying="", done=0)
rs3.begin()
rs3.say("Uno.")
rs3.say("Dos.")
(dd / "live.json").write_text(json.dumps({"at": time.time() - 10, "voice": {"speaks": True}}))
check("a recorder that just went quiet (a long decode, say): still busy, nothing dropped yet", rs3.busy)
time.sleep(0.7)
check("  quiet for a while: dropped, never said by another voice", not rs3.busy)

# Talking over Claude: the recorder heard it (barge_in); the words after are the next turn.
recorder["on"] = True
threading.Thread(target=fake_recorder, daemon=True).start()
asked.clear(); cancelled.clear()
mode["slow"] = True
spk2 = FakeSpeaker()
b = voice.VoiceSession("m1", sdir, ask=fake_ask, cancel=cancelled.append, speaker=spk2)
b.GUARD = 0.1
b.start()
with open(rows_path, "a") as fh:
    fh.write(json.dumps({"seq": b.seq, "turn": 0, "end_turn": True, "text": "Dig into the outage."}) + "\n")
# Talking over it only counts while it speaks, so wait for that, not for a length of
# time: under the whole suite the first sentence can take seconds to arrive.
check("  (it is speaking before anyone talks over it)", wait(lambda: b.status()["state"] == "speaking", 20),
      b.status())
mode["slow"] = False
with open(rows_path, "a") as fh:
    fh.write(json.dumps({"seq": b.seq, "barge_in": True, "turn": 1}) + "\n")
    fh.write(json.dumps({"seq": b.seq, "turn": 1, "t": 9, "end": 10, "text": "wait, the hiring budget?"}) + "\n")
    fh.write(json.dumps({"seq": b.seq, "turn": 1, "end_turn": True, "text": "wait, the hiring budget?"}) + "\n")
check("talked over: Claude stops, and the question is cancelled", wait(lambda: cancelled == ["q1"], 3)
      and spk2.stops >= 1, (cancelled, spk2.stops))
check("  the words said over it are the next question, not lost",
      wait(lambda: asked == ["Dig into the outage.", "wait, the hiring budget?"], 4), asked)
b.stop()
recorder["on"] = False

# A question asked before the turn is surely over: its answer is kept until the
# turn is, then said; the turn takes it instead of asking again. Or it is dropped.
asked.clear(); cancelled.clear()
spk3 = FakeSpeaker()
e = voice.VoiceSession("m1", sdir, ask=fake_ask, cancel=cancelled.append, speaker=spk3)
e.GUARD = 0.1
early = e._start_ask("So what is Orion?", hold=True)
early.worker.join(5)
check("asked early, held: Claude answers, nothing is said yet",
      asked == ["So what is Orion?"] and spk3.said == [] and early.held == ["Orion is the routing cache.", "It replaced Redis in Q2."],
      (asked, spk3.said, early.held))
e._converse("So what is Orion?", ask=early)
check("  the turn is over: it takes that answer, says all of it, and asks nothing again",
      asked == ["So what is Orion?"] and spk3.said == ["Orion is the routing cache.", "It replaced Redis in Q2."],
      (asked, spk3.said))
mode["slow"] = True
early = e._start_ask("Dig into the outage.", hold=True)
wait(lambda: early.got["qid"], 2)
e._cancel_ask(early)
check("  the turn went on to say something else: the early one is cancelled, never said",
      wait(lambda: cancelled == ["q2"], 2) and early.worker.join(3) is None and spk3.said[2:] == [], (cancelled, spk3.said))
mode["slow"] = False

# The recorder's likely_turn rows drive it: asked early, then taken or dropped.
recorder["on"] = True
threading.Thread(target=fake_recorder, daemon=True).start()
asked.clear(); cancelled.clear()
spk4, timings = FakeSpeaker(), []
f = voice.VoiceSession("m1", sdir, ask=fake_ask, cancel=cancelled.append, speaker=spk4,
                       log=lambda m: timings.append(m))
f.GUARD = 0.1
f.start()


def frow(**kw):
    with open(rows_path, "a") as fh:
        fh.write(json.dumps(dict(kw, seq=f.seq)) + "\n")


frow(turn=0, likely_turn=True, text="So what is Orion?")
check("likely-final words: Claude is asked at once, before the turn is over, and nothing is said",
      wait(lambda: asked == ["So what is Orion?"], 2) and f.status()["state"] == "listening" and spk4.said == [],
      (asked, f.status(), spk4.said))
frow(turn=0, likely_turn=True, text="So what is Orion?")
time.sleep(0.3)        # on purpose: a second ask would have gone in this time
check("  the same words again ask nothing more", asked == ["So what is Orion?"], asked)
frow(turn=0, end_turn=True, text="So what is Orion?", spoke_until=time.time() - 0.5)
check("  the turn ends with those words: its answer is said, and nothing is asked again",
      wait(lambda: spk4.said == ["Orion is the routing cache.", "It replaced Redis in Q2."], 3)
      and asked == ["So what is Orion?"], (asked, spk4.said))
check("  the timing line says it was asked early, its first word never below 0",
      wait(lambda: any("asked" in m and "early" in m and "voice timing" in m for m in timings), 3)
      and not any("-0." in m for m in timings if "voice timing" in m), [m for m in timings if "timing" in m])
check("  and the early ask's outcome: one asked, taken",
      re.search(r"\| early: 1 asked, taken \d\.\d\ds early$", [m for m in timings if m.startswith("voice timing")][-1]),
      [m for m in timings if m.startswith("voice timing")][-1:])
check("  (listening again before the next turn)", wait(lambda: f.status()["state"] == "listening", 10), f.status())
frow(turn=1, likely_turn=True, text="And the budget.")
check("  (its first words asked early)", wait(lambda: len(asked) == 2, 5), asked)
frow(turn=1, end_turn=True, text="And the budget for hiring.")
check("they went on: the early question is cancelled, and the whole turn asked",
      wait(lambda: asked == ["So what is Orion?", "And the budget.", "And the budget for hiring."], 3)
      and wait(lambda: "q2" in cancelled, 2), (asked, cancelled))
check("  only its answer is said", wait(lambda: spk4.said[2:] == ["Orion is the routing cache.", "It replaced Redis in Q2."], 3),
      spk4.said)
check("  the timing line says so, and why", wait(lambda: any(
    m.startswith("voice timing") and m.endswith("| early: 1 asked, 1 dropped (went on), none taken") for m in timings), 5),
      [m for m in timings if m.startswith("voice timing")][-1:])
check("  counted: 2 asked early, 1 dropped: 3 calls for 2 turns, not 4",
      wait(lambda: f.early_asks == 2 and f.early_dropped == 1 and len(asked) == 3, 5),
      (f.early_asks, f.early_dropped, asked))
check("  (listening again before the next turn)", wait(lambda: f.status()["state"] == "listening", 10), f.status())
frow(turn=2, likely_turn=True, text="Mm-hm.")
time.sleep(0.3)        # on purpose: an early ask would have gone in this time
check("  likely-final words that are only \"mm-hm\": nothing asked", len(asked) == 3, asked)

# While Claude thinks, the user goes on: likely-final words that are words.
mode["think"] = 6.0                  # Claude thinks, and has said nothing yet (long: the suite runs under load)
frow(turn=2, end_turn=True, text="What changed in Q2?")
check("  (Claude is thinking on the next question before the user goes on)",
      wait(lambda: len(asked) == 4 and f.status()["state"] == "thinking", 5), (asked, f.status()))
mode["think"] = 0
frow(turn=3, likely_turn=True, text="Mm-hm.")
time.sleep(0.3)        # on purpose: a cut or an early ask would have come in this time
check("while Claude thinks, an \"mm-hm\" in likely-final words changes nothing",
      len(asked) == 4 and "q4" not in cancelled and f.status()["state"] == "thinking", (asked, cancelled, f.status()))
frow(turn=3, likely_turn=True, text="For the budget.")
# All three in the one wait: the ask's thread shows in `asked` a moment before the voice
# session counts it (_ask_early starts it, then counts), so a count read after the wait
# could still be 2 (a flake seen 2026-10-11).
check("  real words: more to the question, asked early with what it carries, once",
      wait(lambda: "q4" in cancelled and asked[4:] == ["What changed in Q2? For the budget."] and f.early_asks == 3, 5),
      (asked, cancelled, f.early_asks))
said_before = len(spk4.said)
frow(turn=3, end_turn=True, text="For the budget.")
# Its own answer, counted: the last two sentences are q3's too, so matching them alone
# passed before q5 had said a word, and the next step began mid-answer (a flake: the
# user's "One more thing." then became more to q5's question, not a new turn).
check("  the turn ends on those words: the early one is taken, nothing asked again",
      wait(lambda: spk4.said[said_before:] == ["Orion is the routing cache.", "It replaced Redis in Q2."], 10)
      and len(asked) == 5, (asked, spk4.said[said_before:]))
check("  (and it is listening again before the user goes on)",
      wait(lambda: f.status()["state"] == "listening", 10), f.status())
frow(turn=4, likely_turn=True, text="One more thing.")
check("  (the new turn's words are asked early)",
      wait(lambda: len(asked) == 6 and f._early is not None
           and f._early.got["qid"] == "q6" and f.early_asks == 4, 5), asked)
f.mute(True)
check("  muting drops an early question", wait(lambda: "q6" in cancelled, 2) and f.early_dropped == 2, cancelled)
check("  and says why, in a line of its own (a drop outside any timed turn counts too)",
      any(m == "voice %d: the early question is dropped (muted)" % f.seq for m in timings), timings[-3:])
check("  and the turn keeps it, for its own timing line", f._early_turn["dropped"][-1:] == ["muted"], f._early_turn)
f.mute(False)
f.stop()
recorder["on"] = False

# No cap (10/25): early questions go however many were dropped; each turn's line says what came of them.
c = voice.VoiceSession.__new__(voice.VoiceSession)
c._early_turn = {"asked": 0, "dropped": []}
check("no cap: nothing in the session counts dropped early questions against the next",
      not hasattr(c, "_early_capped") and not hasattr(voice.VoiceSession, "EARLY_WINDOW"))
check("  a turn with none asked says so", c._early_said(None) == "none")
c._early_turn = {"asked": 3, "dropped": ["went on", "went on"]}
check("  and one with some: how many, which were dropped and why",
      c._early_said(0.4) == "3 asked, 2 dropped (went on, went on), taken 0.40s early", c._early_said(0.4))

shutil.rmtree(root, ignore_errors=True)
print()
if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
