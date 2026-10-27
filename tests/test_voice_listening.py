"""Voice mode listens the whole time, and reacts to what it hears.
Against a fake recorder (its rows, its live status, its mic switch), a fake agent
and a fake speaker: more to the question while Claude thinks asks again with all
of it; "mm-hm" changes nothing; "never mind" drops the answer; "hold on" keeps it
until "go on"; Claude's first word waits for the user to finish; talking over
Claude pauses it, then a backchannel or a cough and it carries on from the
sentence it was in, real words and it stops, and the next turn tells Claude what
was heard. And the words themselves (interrupt.classify). Nothing is audible."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, shutil, sys, tempfile, threading, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sblisten-"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import interrupt  # noqa: E402
import voice  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:700])


def wait(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


# ---------------------------------------------------------------- the words
I = interrupt
for text, want in [("Mm-hmm.", I.BACKCHANNEL), ("Mhm.", I.BACKCHANNEL), ("Uh-huh.", I.BACKCHANNEL),
                   ("Yeah, yeah.", I.BACKCHANNEL), ("OK.", I.BACKCHANNEL), ("Right, right.", I.BACKCHANNEL),
                   ("Oh, interesting.", I.BACKCHANNEL), ("Got it, thanks.", I.BACKCHANNEL),
                   ("", I.NOISE), ("Um.", I.NOISE), ("Uh, the", I.NOISE),
                   ("Never mind.", I.CANCEL), ("Okay, never mind.", I.CANCEL), ("Stop.", I.CANCEL),
                   ("Actually, forget it.", I.CANCEL),
                   ("Hold on.", I.WAIT), ("Wait, wait.", I.WAIT), ("One sec.", I.WAIT), ("Hang on a second.", I.WAIT),
                   ("Go on.", I.RESUME), ("Sorry, go ahead.", I.RESUME), ("I'm back.", I.RESUME),
                   ("Wait, what about Q4?", I.REAL), ("No.", I.REAL), ("What?", I.REAL),
                   ("Yeah, but what about the budget?", I.REAL), ("Stop the deployment.", I.REAL),
                   ("And for the EU region.", I.REAL),
                   # parakeet's own spellings, from tests/measure_interruptions.py
                   ("Ajá.", I.BACKCHANNEL), ("Hum.", I.BACKCHANNEL), ("Ha ha", I.BACKCHANNEL),
                   ("Make sense.", I.BACKCHANNEL), ("M. M. Hum.", I.BACKCHANNEL),
                   ("Sugar.", I.SHORT), ("Tuesday.", I.SHORT)]:
    got = interrupt.classify(text)
    check("words: %-36r is %s" % (text, want), got == want, got)
check("what was heard of a cut sentence: its first words, then ...",
      interrupt.heard_of("Orion is the routing cache that replaced Redis.", 1.2) == "Orion is the..."
      and interrupt.heard_of("Short one.", 5.0) == "Short one." and interrupt.heard_of("Not yet.", 0.1) == "")
check("Claude is told what was heard, or that nothing was",
      voice.with_cut("q?", None) == "q?" and voice.with_cut("q?", "") == "(They went on before hearing your answer.) q?"
      and voice.with_cut("q?", 'It said "x"') == "(They cut in while you were saying: \"It said 'x'\") q?")


# ---------------------------------------------------------------- the fakes
class Speaker(object):
    """Says each sentence for `secs`; records them. stop() is the recorder's halt too."""
    def __init__(self, secs=0.15):
        self.secs, self.said, self.stops, self._until, self.current, self._q = secs, [], 0, 0.0, "", []
        self.started_at, self.played = None, []
    def say(self, text):
        self.said.append(text); self._q.append(text)
    def stop(self):
        self.stops += 1; self._q = []; self._until = 0.0; self.current = ""
    def _tick(self):
        if time.time() >= self._until:
            self.current = ""
            if self._q:
                self.current = self._q.pop(0); self._until = time.time() + self.secs; self.started_at = time.time()
                self.played.append(self.current)
    @property
    def speaking(self):
        self._tick(); return time.time() < self._until
    @property
    def busy(self):
        self._tick(); return bool(self._q) or time.time() < self._until


class Agent(object):
    """Answers with `pieces`, each after `gap` seconds; stops when cancelled."""
    def __init__(self):
        self.asked, self.heards, self.cancelled, self.pieces, self.gap = [], [], [], [], 0.05
        self.release = None     # an optional test gate: controls when the reply may begin
        self.quieted = []        # cancelled because the user went on: the chat keeps none of it
        self.said = {}           # ...but what the voice had said of its answer, if any
    def ask(self, question, on_event, on_queued, heard=None):
        release = self.release
        self.asked.append(question); self.heards.append(heard)
        qid = "q%d" % len(self.asked)
        on_queued(qid)
        if release is not None and not release.wait(10):
            raise RuntimeError("the test did not release the fake reply")
        for p in list(self.pieces):
            end = time.time() + self.gap
            while time.time() < end:
                if qid in self.cancelled:
                    return "(stopped)"
                time.sleep(0.01)
            on_event({"kind": "delta", "text": p})
        return "".join(self.pieces)
    def cancel(self, qid, quiet=False, said=""):
        if qid not in self.cancelled:
            self.cancelled.append(qid)
        if quiet:
            self.quieted.append(qid)
        if said:
            self.said[qid] = said


class Recorder(object):
    """The recorder as voice.py sees it: rows in dictation.jsonl, live.json, the
    dictate switch. old: an older one, which says only whether any channel
    is speaking (in a meeting, someone always is)."""
    says_heard = False       # the turn's words in its status, as the real one: off for the older tests
    def __init__(self, sdir, old=False):
        self.sdir, self.you, self.on, self.old = sdir, False, True, old
        self.heard, self._was = "", ""
        threading.Thread(target=self._run, daemon=True).start()
    def _run(self):
        while self.on:
            mode = self.mode()       # a turn open across a hold keeps its words (standby.py's switch)
            self._was = mode
            st = {"at": time.time(), "level": 0.1, "partial": "", "speaking": self.you or self.old,
                  "voice": {"mode": mode}}
            if self.says_heard:
                st["voice"]["heard"] = self.heard
            if not self.old:
                st["voice"]["you_speaking"] = self.you
            # The real recorder publishes via rename. Readers must never see a
            # truncated JSON file and mistake a speaking user for silence.
            status = self.sdir / "live.tmp"
            status.write_text(json.dumps(st))
            status.replace(self.sdir / "live.json")
            time.sleep(0.03)
    def mode(self):
        try:
            return (self.sdir / "dictate").read_text().split()[0]
        except (OSError, IndexError):
            return ""
    def row(self, vs, **kw):
        if kw.get("end_turn"):
            self.heard = ""          # the turn is over: the next one starts empty
        elif kw.get("text") and not kw.get("likely_turn") and self.mode() == "voice":
            self.heard = (self.heard + " " + kw["text"]).strip()
        with open(self.sdir / "dictation.jsonl", "a") as fh:
            fh.write(json.dumps(dict(kw, seq=vs.seq)) + "\n")


made = []


def session(secs=0.15, old_recorder=False, old_app=False, agent=None):
    sdir = root / ("s%d" % len(made))
    sdir.mkdir(parents=True)
    rec, agent, spk = Recorder(sdir, old=old_recorder), agent or Agent(), Speaker(secs)
    logged = []
    # An older app: its ask takes three arguments, no heard=.
    ask = (lambda q, ev, qd: agent.ask(q, ev, qd)) if old_app else agent.ask
    vs = voice.VoiceSession("m1", sdir, ask=ask, cancel=agent.cancel, speaker=spk, log=logged.append)
    vs.GUARD, vs.DECIDE, vs.DECIDE_MAX = 0.05, 0.6, 2.5
    made.append((vs, rec))
    vs.start()
    return vs, rec, agent, spk, logged


def done(vs, rec):
    vs.stop()
    rec.on = False


ANSWER = ["Orion is the routing cache. ", "It replaced Redis in Q2. ", "The team is four people.\n"]
S1, S2, S3 = "Orion is the routing cache.", "It replaced Redis in Q2.", "The team is four people."

# ---------------------------------------------------------------- while Claude thinks
vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.6
rec.row(vs, turn=0, end_turn=True, text="What's the Q3 budget?")
check("listening while Claude thinks: the mic stays the user's",
      wait(lambda: agent.asked == ["What's the Q3 budget?"], 2) and rec.mode() == "voice"
      and vs.status()["state"] == "thinking", (agent.asked, rec.mode(), vs.status()))
rec.row(vs, turn=1, t=5, end=6, text="And for Q4 too.")
check("more to the question: the one in flight is cancelled, nothing said",
      wait(lambda: agent.cancelled == ["q1"], 2) and spk.said == [], (agent.cancelled, spk.said))
check("  listening again, the question so far on screen, and the words after it",
      wait(lambda: vs.status()["state"] == "listening", 2)
      and vs.status()["you"] == "What's the Q3 budget? And for Q4 too.", vs.status())
rec.row(vs, turn=1, end_turn=True, text="And for Q4 too.")
check("  quietly: the chat keeps only the question with all of it", agent.quieted == ["q1"], agent.quieted)
check("  and when they are done, asked again with all of it",
      wait(lambda: len(agent.asked) == 2, 3) and agent.asked[1] == "What's the Q3 budget? And for Q4 too."
      and agent.heards[1] is None, (agent.asked, agent.heards))
check("  that answer is spoken, whole", wait(lambda: spk.said == [S1, S2, S3], 5), spk.said)
wait(lambda: vs.status()["state"] == "listening", 3)
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.3
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
wait(lambda: agent.asked, 2)
time.sleep(0.1)
rec.row(vs, turn=1, t=5, end=6, text="Mm-hmm.")
rec.row(vs, turn=1, end_turn=True, text="Mm-hmm.")
check("a backchannel while Claude thinks: nothing changes, the answer is spoken",
      wait(lambda: spk.said == [S1, S2, S3], 5) and agent.cancelled == [], (spk.said, agent.cancelled))
time.sleep(0.4)
check("  and \"mm-hm\" is never a question", agent.asked == ["What is Orion?"], agent.asked)
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.5
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
wait(lambda: agent.asked, 2)
rec.row(vs, turn=1, t=5, end=6, text="Okay, never mind.")
rec.row(vs, turn=1, end_turn=True, text="Okay, never mind.")
check("\"never mind\" while Claude thinks: cancelled, nothing said",
      wait(lambda: agent.cancelled == ["q1"], 2) and wait(lambda: vs.status()["state"] == "listening", 2), agent.cancelled)
time.sleep(0.6)
check("  nor asked as a question", spk.said == [] and agent.asked == ["What is Orion?"], (spk.said, agent.asked))
check("  and it stays in the chat, as what happened (not a quiet drop)", agent.quieted == [], agent.quieted)
early = vs._start_ask("And who owns it?", hold=True)
early.worker.join(5)
vs._early = early
vs._take_early("And who owns the migration?")
check("an early question answered already, then the user went on: the app is told, quietly",
      agent.quieted == ["q2"] and "q2" in agent.cancelled, (agent.quieted, agent.cancelled))
check("  nothing of it was said: nothing for the chat to keep", "q2" not in agent.said, agent.said)
q = vs._start_ask("What else?", hold=True)
q.worker.join(5)
q.spoken, q.playing, q.play_since = [S1, S2], 1, time.time() - 30
vs._cancel_ask(q, quiet=True)
check("a quiet drop after some of its answer was said: the app is told what was said, to keep it in the chat",
      agent.said.get(q.got["qid"]) == S1 + " " + S2, agent.said)
done(vs, rec)

# An answer whose deltas stopped short of it (the greeting cut at "First, do"): the rest of the
# answer, which came whole at the end, is said too, all of it.
class ShortDeltas(Agent):
    def ask(self, question, on_event, on_queued, heard=None):
        self.asked.append(question); self.heards.append(heard)
        on_queued("q%d" % len(self.asked))
        on_event({"kind": "delta", "text": "Hi, we're connected. Two things are open. First, do"})
        time.sleep(0.05)
        return "Hi, we're connected. Two things are open. First, do you want mock three?"


vs, rec, agent, spk, logged = session(agent=ShortDeltas())
rec.row(vs, turn=0, end_turn=True, text="[CALL START]")
check("an answer whose last words never came as deltas: said whole, to its end",
      wait(lambda: spk.said[-1:] == ["First, do you want mock three?"], 5)
      and spk.said == ["Hi, we're connected.", "Two things are open.", "First, do you want mock three?"], spk.said)
check("  and the log says so", any("came only with it" in m for m in logged), logged[-3:])
done(vs, rec)

# The answer went quiet mid-way (Claude still at work, the mic the user's again): words that are
# likely a turn interrupt it, as their own row would; never "more", which drops what was said.
vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces = [S1 + " "] + [""] * 10 + ["It replaced Redis.\n"]     # a long pause after the first sentence
agent.gap = 0.25
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
wait(lambda: spk.said == [S1], 5)
wait(lambda: rec.mode() == "voice", 5)                  # quiet past REOPEN: listening meanwhile
rec.row(vs, turn=1, likely_turn=True, text="Wait, who owns it?")
check("likely words after part of the answer was said: an interruption, not \"more\" (the chat keeps what was said)",
      wait(lambda: any("interrupted" in m for m in logged), 4) and agent.quieted == [], (agent.quieted, logged[-3:]))
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.05
rec.you = True                       # still saying something when the turn was called over
check("  the recorder has published the user's speech before the reply",
      wait(vs.user_speaking, 5))
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
wait(lambda: agent.asked, 2)
time.sleep(0.6)
check("Claude's first word waits while the user is still talking", spk.said == [] and rec.mode() == "voice",
      (spk.said, rec.mode()))
rec.you = False
check("  and comes once they stop, the mic held from it (its floor: words over it are kept)",
      wait(lambda: spk.said[:1] == [S1], 1) and rec.mode() == "floor", (spk.said, rec.mode()))
wait(lambda: vs.status()["state"] == "listening", 3)
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.05
rec.you = True
check("  the user is speaking before the answer is written", wait(vs.user_speaking, 5))
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
wait(lambda: agent.asked, 2)
check("  the answer is fully written while they are still talking",
      wait(lambda: vs._q is not None and not vs._q.worker.is_alive(), 5))
rec.row(vs, turn=1, t=5, end=6, text="For the EU region.")
check("more to the question after the answer was written: dropped, unheard",
      wait(lambda: agent.quieted == ["q1"] and vs.status()["state"] == "listening", 5)
      and spk.said == [], (vs.status(), spk.said))
rec.you = False
rec.row(vs, turn=1, end_turn=True, text="For the EU region.")
check("  and Claude is told the user heard none of it",
      wait(lambda: len(agent.asked) == 2, 3) and agent.asked[1] == "What is Orion? For the EU region."
      and agent.heards[1] == "", (agent.asked, agent.heards))
wait(lambda: vs.status()["state"] == "listening" and len(spk.said) == 3, 4)
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.05
agent.release = threading.Event()
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
wait(lambda: agent.asked, 2)
rec.row(vs, turn=1, t=5, end=6, text="Hold on.")
rec.row(vs, turn=1, end_turn=True, text="Hold on.")
check("  hold on reaches the thinking voice before the fake reply",
      wait(lambda: vs.status().get("holding") and vs.status()["state"] == "listening", 5))
agent.release.set()
check("  the held answer has finished arriving",
      wait(lambda: vs._q is not None and not vs._q.worker.is_alive(), 5))
check("\"hold on\" while Claude thinks: the answer waits, the screen says so",
      spk.said == [] and vs.status().get("holding") and vs.status()["state"] == "listening", (spk.said, vs.status()))
rec.row(vs, turn=2, t=8, end=9, text="Okay, go on.")
rec.row(vs, turn=2, end_turn=True, text="Okay, go on.")
check("  \"go on\": it is said, all of it", wait(lambda: spk.said == [S1, S2, S3], 3) and agent.cancelled == [],
      (spk.said, agent.cancelled))
time.sleep(0.4)
check("  and neither was a question", agent.asked == ["What is Orion?"] and not vs.status().get("holding"),
      agent.asked)
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.4
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
wait(lambda: agent.asked, 2)
vs.mute(True)
check("muted while Claude thinks: the mic is held", wait(lambda: rec.mode() == "hold", 1), rec.mode())
vs.mute(False)
check("  unmuted: the user's again", wait(lambda: rec.mode() == "voice", 1) and not spk.said, rec.mode())
wait(lambda: vs.status()["state"] == "listening" and len(spk.said) == 3, 5)
done(vs, rec)


# ---------------------------------------------------------------- while Claude speaks
def talked_over(vs, rec, spk, turn):
    """The recorder heard the user over Claude: it stops the voice, and says so."""
    spk.stop()
    rec.row(vs, barge_in=True, turn=turn)


def speaking(vs, spk, sentence, into=0.25):
    ok = wait(lambda: spk.current == sentence, 5)
    time.sleep(into)
    return ok


vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S2)
talked_over(vs, rec, spk, 1)
check("talked over: Claude pauses, and listens", wait(lambda: rec.mode() == "voice", 1)
      and wait(lambda: vs.status()["state"] == "listening", 1) and agent.cancelled == [], (rec.mode(), vs.status()))
rec.row(vs, turn=1, t=5, end=6, text="Mm-hmm.")
rec.row(vs, turn=1, end_turn=True, text="Mm-hmm.")
check("  a backchannel: it carries on, from the sentence it paused in",
      wait(lambda: spk.said == [S1, S2, S3, S2, S3], 3) and rec.mode() == "floor", (spk.said, rec.mode()))
check("  the question is not cancelled, nor \"mm-hm\" asked",
      wait(lambda: vs.status()["state"] == "listening", 4) and agent.cancelled == [] and agent.asked == ["What is Orion?"],
      (agent.cancelled, agent.asked))
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1)
spk.stop()                         # the recorder halts the voice, and says why a moment later
time.sleep(0.2)
rec.row(vs, barge_in=True, turn=1)
rec.row(vs, turn=1, t=5, end=6, text="Mm-hmm.")
rec.row(vs, turn=1, end_turn=True, text="Mm-hmm.")
check("the voice goes quiet before the row says why: the answer waits for it, and carries on",
      wait(lambda: spk.said[3:] == [S1, S2, S3], 3) and agent.asked == ["What is Orion?"], (spk.said, agent.asked))
wait(lambda: vs.status()["state"] == "listening", 5)
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1)
spk.stop()                         # the voice simply stopped (it failed): no row ever comes
check("  the voice stops for no reason: the turn ends, and says so in the log",
      wait(lambda: vs.status()["state"] == "listening", 3)
      and any("sentence(s) left" in m for m in logged), (vs.status(), logged[-3:]))
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1)
talked_over(vs, rec, spk, 1)
rec.row(vs, turn=1, t=5, end=6, text="Sugar.")          # "Sure.", misheard
check("one stray word over Claude: a misheard backchannel, it carries on",
      wait(lambda: spk.said[3:4] == [S1], 2) and agent.cancelled == [], (spk.said, agent.cancelled))
wait(lambda: vs.status()["state"] == "listening", 4)
done(vs, rec)

# A recorder whose voice ducks (R-conversation, Q1/Q2): it says so (overlap rows), keeps
# playing underneath, and decides from the words itself. The app waits, and repeats nothing.
vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S2)
rec.row(vs, overlap="duck", turn=1)
rec.row(vs, turn=1, t=5, end=6, text="Mm-hmm.")
rec.row(vs, overlap="resume", turn=1, why="backchannel")
rec.row(vs, turn=1, end_turn=True, text="Mm-hmm.")
check("ducked, then \"mm-hm\": it goes on where it was, nothing said twice",
      wait(lambda: vs.status()["state"] == "listening", 4) and spk.said == [S1, S2, S3] and agent.cancelled == [],
      (spk.said, agent.cancelled))
check("  nor asked, and the mic was never the app's to give back", agent.asked == ["What is Orion?"]
      and rec.mode() == "hold" or rec.mode() == "voice", (agent.asked, rec.mode()))
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1)
rec.row(vs, overlap="duck", turn=1)
rec.row(vs, turn=1, t=5, end=6, text="Sugar.")          # "Sure.", misheard
time.sleep(0.3)
check("ducked, one stray word: no cut", agent.cancelled == [] and not any("interrupted" in m for m in logged),
      (agent.cancelled, logged[-3:]))
rec.row(vs, overlap="resume", turn=1, why="short")
wait(lambda: vs.status()["state"] == "listening", 4)
check("  and it went on, nothing repeated", spk.said == [S1, S2, S3], spk.said)
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1)
rec.row(vs, overlap="duck", turn=1)
spk.stop()                                              # the recorder gives way: words of a turn
rec.row(vs, barge_in=True, turn=1, why="words")
rec.row(vs, turn=1, t=5, end=6, text="Wait, what about the EU region?")
check("ducked, then words of a turn: Claude stops, and is told what was heard",
      wait(lambda: any("interrupted" in m for m in logged), 3), logged[-3:])
done(vs, rec)


def stopped_on_words(why, *texts):
    """Ducked, then the recorder stops it on the words (overlap: give_way, with "after")."""
    vs, rec, agent, spk, logged = session(secs=0.6)
    agent.pieces, agent.gap = ANSWER, 0.02
    rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
    speaking(vs, spk, S1)
    rec.row(vs, overlap="duck", turn=1)
    spk.stop()
    rec.row(vs, barge_in=True, turn=1, why=why, after=0.9)
    for text in texts:
        rec.row(vs, turn=1, t=5, end=6, text=text)
        rec.row(vs, turn=1, end_turn=True, text=text)
    return vs, rec, agent, spk, logged


# Not an interruption after all: it goes on from the sentence it was in, whoever stopped it,
# never silence ("Continue, continue." over a stopped punchline left 20 s of it, 10:22:10 in
# talk-20261024-101030).
vs, rec, agent, spk, logged = stopped_on_words("kept talking", "Mm-hmm.")
check("stopped by the recorder on its words, then only \"mm-hm\" closes: it carries on from the sentence it was in",
      wait(lambda: spk.said == [S1, S2, S3, S1, S2, S3], 3) and agent.asked == ["What is Orion?"]
      and not any("interrupted" in m for m in logged), (spk.said, agent.asked, logged[-3:]))
done(vs, rec)

vs, rec, agent, spk, logged = stopped_on_words("kept talking", "Continue, continue.")
check("  \"continue, continue\": the same, and nothing asked", wait(lambda: spk.said == [S1, S2, S3, S1, S2, S3], 3)
      and agent.asked == ["What is Orion?"], (spk.said, agent.asked, logged[-3:]))
done(vs, rec)

vs, rec, agent, spk, logged = stopped_on_words("kept talking")
t0 = time.time()
check("  no words to follow it: it carries on after a moment, never left silent",
      wait(lambda: spk.said == [S1, S2, S3, S1, S2, S3], 3) and time.time() - t0 < 2.5
      and any("no words over Claude" in m for m in logged), (spk.said, time.time() - t0, logged[-3:]))
done(vs, rec)

vs, rec, agent, spk, logged = stopped_on_words("cancel", "Never mind.")
check("  \"never mind\": no answer, and not asked", wait(lambda: any("never mind" in m for m in logged), 3)
      and agent.asked == ["What is Orion?"] and spk.said == [S1, S2, S3], (agent.asked, spk.said, logged[-3:]))
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.6
rec.row(vs, turn=0, end_turn=True, text="What's on my calendar?")
wait(lambda: agent.asked, 2)
rec.row(vs, turn=1, t=5, end=6, text="Tuesday.")
rec.row(vs, turn=1, end_turn=True, text="Tuesday.")
check("  but while Claude thinks, one word is more to the question",
      wait(lambda: len(agent.asked) == 2, 3) and agent.asked[1] == "What's on my calendar? Tuesday.", agent.asked)
wait(lambda: vs.status()["state"] == "listening" and len(spk.said) == 3, 5)
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1)
talked_over(vs, rec, spk, 1)
t0 = time.time()
check("a cough over Claude (no words): it carries on after a moment",
      wait(lambda: spk.said[3:4] == [S1], 3) and 0.5 < time.time() - t0 < 1.5 and agent.cancelled == [],
      (spk.said, time.time() - t0))
wait(lambda: vs.status()["state"] == "listening", 4)
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER + ["And more. ", "And more. "], 0.5     # still writing when cut off
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S2, into=0.4)
talked_over(vs, rec, spk, 1)
rec.row(vs, turn=1, t=5, end=6, text="Wait, what about the hiring budget?")
check("real words over Claude: it stops, and the question is cancelled",
      wait(lambda: agent.cancelled == ["q1"], 2) and wait(lambda: vs.status()["state"] == "listening", 2),
      (agent.cancelled, vs.status()))
rec.row(vs, turn=1, end_turn=True, text="Wait, what about the hiring budget?")
check("  their words are the next question, and Claude is told what was heard",
      wait(lambda: len(agent.asked) == 2, 3) and agent.asked[1:] == ["Wait, what about the hiring budget?"]
      and agent.heards[1].startswith(S1 + " It") and agent.heards[1].endswith("..."), (agent.asked, agent.heards))
check("  the rest was never said", spk.played[:2] == [S1, S2] and S3 not in spk.played[:3], spk.played)
wait(lambda: vs.status()["state"] == "listening" and len(spk.said) >= 5, 5)
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.4
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1)
talked_over(vs, rec, spk, 1)
rec.row(vs, turn=1, t=5, end=6, text="Stop.")
rec.row(vs, turn=1, end_turn=True, text="Stop.")
check("\"stop\" over Claude: it stops, cancelled", wait(lambda: agent.cancelled == ["q1"], 2), agent.cancelled)
time.sleep(1.0)
check("  and nothing is asked or said after", agent.asked == ["What is Orion?"] and spk.played == [S1]
      and vs.status()["state"] == "listening", (agent.asked, spk.played))
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S2)
talked_over(vs, rec, spk, 1)
rec.row(vs, turn=1, t=5, end=6, text="Hold on a second.")
rec.row(vs, turn=1, end_turn=True, text="Hold on a second.")
time.sleep(vs.DECIDE + 0.5)
check("\"hold on\" over Claude: it waits, for longer than a pause",
      spk.said == [S1, S2, S3] and vs.status().get("holding"), (spk.said, vs.status()))
rec.row(vs, turn=2, t=8, end=9, text="Sorry, go on.")
rec.row(vs, turn=2, end_turn=True, text="Sorry, go on.")
check("  \"go on\": from the sentence it was in", wait(lambda: spk.said == [S1, S2, S3, S2, S3], 3), spk.said)
wait(lambda: vs.status()["state"] == "listening", 4)
check("  no question asked", agent.asked == ["What is Orion?"] and agent.cancelled == [], agent.asked)
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.4
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1, into=0.5)
vs.interrupt()
check("a tap on the orb: it stops at once", wait(lambda: agent.cancelled == ["q1"], 2) and spk.played == [S1],
      (agent.cancelled, spk.played))
rec.row(vs, turn=1, t=5, end=6, text="What does it cost?")
rec.row(vs, turn=1, end_turn=True, text="What does it cost?")
check("  the next question says what was heard", wait(lambda: len(agent.asked) == 2, 3)
      and agent.heards[1] == "Orion...", agent.heards)
wait(lambda: vs.status()["state"] == "listening", 4)
check("the log lines are ASCII (launchd has no locale)", all(all(ord(c) < 128 for c in m) for m in logged), logged)
done(vs, rec)

# ---------------------------------------------------------------- "Taking actions · N"
class Worker(Agent):
    """Claude at work before it answers: tools, the handoff to the meeting agent, more tools."""
    def ask(self, question, on_event, on_queued, heard=None):
        self.asked.append(question)
        on_queued("q%d" % len(self.asked))
        for step in ("Turning Claude on: catching up on the transcript", "Reading ~/notes/orion.md",
                     "Asking the meeting agent", "Running standby-ctx search \"budget\"",
                     "Using mcp__notes__search"):
            on_event({"kind": "tool", "text": step})
            self.seen.append(dict(vs.status()))
            time.sleep(0.15)
        on_event({"kind": "delta", "text": "It is in the notes.\n"})
        return "It is in the notes."


vs, rec, agent, spk, logged = session()
worker = Worker()
worker.seen = []
vs.ask = worker.ask
before = vs.status()
rec.row(vs, turn=0, end_turn=True, text="What did we decide on the budget?")
wait(lambda: len(worker.seen) == 5, 3)
time.sleep(0.2)
during = vs.status()
check("actions: none before the turn", before["actions"] == 0 and before["action"] == "", before)
check("  Claude's tools counted as they come, the handoff and catching up not",
      [s["actions"] for s in worker.seen] == [0, 1, 1, 2, 3] and during["actions"] == 3, (worker.seen, during))
check("  the latest in plain words", during["action"] == "Searching notes"
      and worker.seen[1]["action"] == "Reading orion.md" and worker.seen[3]["action"] == "Checking the transcript",
      (during, worker.seen))
wait(lambda: vs.status()["state"] == "listening", 3)
check("  and none once the turn is over", vs.status()["actions"] == 0 and vs.status()["action"] == "", vs.status())
done(vs, rec)

# ---------------------------------------------------------------- real work
class Deep(Agent):
    """The quick voice hands the question to the meeting agent, which works, then answers."""
    work = 2.5
    def ask(self, question, on_event, on_queued, heard=None):
        self.asked.append(question); self.heards.append(heard)
        qid = "q%d" % len(self.asked)
        on_queued(qid)
        on_event({"kind": "tool", "text": "Asking the meeting agent"})
        on_event({"kind": "tool", "text": "Reading ~/notes/budget.md"})
        end = time.time() + self.work
        while time.time() < end:
            if qid in self.cancelled:
                return "(stopped)"
            time.sleep(0.02)
        for p in ("The budget is final. ", "It was signed on Monday.\n"):
            on_event({"kind": "delta", "text": p})
        return "The budget is final. It was signed on Monday."


def deep_session():
    vs, rec, agent, spk, logged = session(secs=0.3)
    deep = Deep()
    vs.ask, vs.cancel = deep.ask, deep.cancel
    return vs, rec, deep, spk, logged


B1, B2 = "The budget is final.", "It was signed on Monday."
vs, rec, deep, spk, logged = deep_session()
rec.row(vs, turn=0, end_turn=True, text="Is the budget final?")
check("handed to the meeting agent: one short line in Standby's voice first, no model call",
      wait(lambda: spk.played[:1] and spk.played[0] in voice.HANDOFF_LINES, 2) and len(deep.asked) == 1, spk.played)
check("  then, while it works, the mic is the user's again",
      wait(lambda: rec.mode() == "voice" and vs.status()["state"] == "thinking", 2.5), (rec.mode(), vs.status()))
check("  and the answer, held from its first word", wait(lambda: spk.played[1:] == [B1, B2], 4)
      and wait(lambda: vs.status()["state"] == "listening", 3), spk.played)
first = spk.played[0]
rec.row(vs, turn=1, end_turn=True, text="And the hiring plan?")
check("  the next handoff says another line", wait(lambda: len(spk.played) >= 4, 3)
      and spk.played[3] in voice.HANDOFF_LINES and spk.played[3] != first, spk.played)
wait(lambda: vs.status()["state"] == "listening" and len(spk.played) == 6, 6)
done(vs, rec)

vs, rec, deep, spk, logged = deep_session()
rec.row(vs, turn=0, end_turn=True, text="Is the budget final?")
wait(lambda: rec.mode() == "voice" and spk.played, 3)
time.sleep(0.2)
rec.row(vs, turn=1, t=5, end=6, text="And what about Friday?")
check("words while the meeting agent works: it keeps working, and the voice says so",
      wait(lambda: voice.VoiceSession.AFTER_THIS in spk.played, 2) and deep.cancelled == [], (spk.played, deep.cancelled))
rec.row(vs, turn=1, end_turn=True, text="And what about Friday?")
check("  the answer comes, whole", wait(lambda: spk.played[-2:] == [B1, B2], 5), spk.played)
check("  then their words are the next question", wait(lambda: len(deep.asked) == 2, 4)
      and deep.asked[1] == "And what about Friday?" and deep.heards[1] is None, (deep.asked, deep.heards))
wait(lambda: vs.status()["state"] == "listening" and spk.played[-1:] == [B2] and len(spk.played) >= 6, 8)
done(vs, rec)

vs, rec, deep, spk, logged = deep_session()
rec.row(vs, turn=0, end_turn=True, text="Is the budget final?")
wait(lambda: rec.mode() == "voice" and spk.played, 3)
rec.row(vs, turn=1, t=5, end=6, text="Never mind.")
rec.row(vs, turn=1, end_turn=True, text="Never mind.")
check("\"never mind\" while the meeting agent works: the work is cancelled",
      wait(lambda: deep.cancelled == ["q1"], 2) and wait(lambda: vs.status()["state"] == "listening", 2), deep.cancelled)
time.sleep(0.5)
check("  and nothing more is said or asked", B1 not in spk.played and deep.asked == ["Is the budget final?"],
      (spk.played, deep.asked))
done(vs, rec)

vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ANSWER, 0.05
rec.row(vs, turn=0, t=1, end=2, text="Um.")
rec.row(vs, turn=0, end_turn=True, text="Um.")
time.sleep(0.6)
check("a turn that is only a sound (\"Um.\"): nothing is asked", agent.asked == [] and spk.played == [],
      (agent.asked, spk.played))
rec.row(vs, turn=1, end_turn=True, text="What is Orion?")
check("  a quick answer: no handoff line", wait(lambda: spk.played == [S1, S2, S3], 4), spk.played)
done(vs, rec)

# ------------------------------------------- an early question and real work together
# Voice mode asks on likely-final words and holds the answer, then says a line at the
# handoff. Both go through the same Question, so check they do not trip over each other.
vs, rec, deep, spk, logged = deep_session()
early = vs._start_ask("Is the budget final?", hold=True)
wait(lambda: deep.asked, 2)
time.sleep(0.3)                      # the handoff happens while the question is still held
check("asked early, then handed to the meeting agent: its line waits with it, unsaid",
      spk.said == [] and early.held and early.held[0] in voice.HANDOFF_LINES and early.deep,
      (spk.said, early.held, early.deep))
rec.row(vs, turn=0, end_turn=True, text="Is the budget final?")
vs._converse("Is the budget final?", ask=early)
check("  the turn takes it: the line first, then the answer, and nothing asked twice",
      spk.said[:1] and spk.said[0] in voice.HANDOFF_LINES and spk.said[1:] == [B1, B2]
      and deep.asked == ["Is the budget final?"], (spk.said, deep.asked))
done(vs, rec)

vs, rec, deep, spk, logged = deep_session()
early = vs._start_ask("Is the budget final?", hold=True)
wait(lambda: deep.asked, 2)
time.sleep(0.3)
vs._cancel_ask(early)
check("  an early question dropped after its handoff: cancelled, and its line never said",
      wait(lambda: deep.cancelled == ["q1"], 2) and spk.said == [], (deep.cancelled, spk.said))
done(vs, rec)

# ---------------------------------------------------------------- mixed versions
# The app, the recorder and the agent restart at different times: each of them may
# be older than the others for a while.
vs, rec, agent, spk, logged = session(old_recorder=True)
agent.pieces, agent.gap = ANSWER, 0.05
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
t0 = time.time()
check("an older recorder (in a meeting, someone is always speaking): no wait for a first word",
      wait(lambda: spk.played[:1] == [S1], 1.0), (spk.played, time.time() - t0))
wait(lambda: vs.status()["state"] == "listening", 3)
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6, old_app=True)
agent.pieces, agent.gap = ANSWER, 0.4
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S1, into=0.5)
vs.interrupt()
wait(lambda: vs.status()["state"] == "listening", 2)
rec.row(vs, turn=1, t=5, end=6, text="What does it cost?")
rec.row(vs, turn=1, end_turn=True, text="What does it cost?")
check("an older app: the turn after a cut-off is asked as before, not told (and no error)",
      wait(lambda: len(agent.asked) == 2, 3) and agent.asked[1] == "What does it cost?" and agent.heards[1] is None
      and wait(lambda: spk.played[-1:] == [S3], 4), (agent.asked, agent.heards, spk.played))
done(vs, rec)
check("takes(): which asks are told what was heard",
      voice.takes(lambda q, ev, qd, heard=None: 0, "heard") and voice.takes(lambda q, ev, qd, **kw: 0, "heard")
      and not voice.takes(lambda q, ev, qd: 0, "heard") and not voice.takes(len, "heard"))

# ---------------------------------------------------------------- the orb's words: only what's current
# The user, 2026-10-12: "it is still showing me the previous messages… like it is stuck".
# Words said while the meeting agent worked, whose turn hadn't closed when Claude took
# the floor: the recorder let them go when it listened again, and the orb kept them.
# Now the recorder keeps that turn (standby.py's switch): they are the turn still, on
# the orb as such, and asked when it closes, once.
vs, rec, deep, spk, logged = deep_session()
rec.says_heard = True
rec.row(vs, turn=0, end_turn=True, text="Is the budget final?")
wait(lambda: rec.mode() == "voice" and spk.played, 3)
time.sleep(0.2)
rec.row(vs, turn=1, t=5, end=6, text="And what about Friday?")       # its turn not closed when Claude speaks
check("orb: words while the meeting agent works, their turn still open", wait(lambda: voice.VoiceSession.AFTER_THIS in spk.played, 2))
check("  the answer comes", wait(lambda: spk.played[-2:] == [B1, B2], 6), spk.played)
check("  listening again: the orb shows them, the turn they still are, and nothing of the answer",
      wait(lambda: vs.status()["state"] == "listening" and rec.mode() == "voice", 3)
      and wait(lambda: vs.status()["you"] == "And what about Friday?", 1.5) and vs.status()["claude"] == "", vs.status())
rec.row(vs, turn=1, end_turn=True, text="And what about Friday?")     # the recorder closes it
check("  the turn closes: they are asked, as the voice said", wait(lambda: len(deep.asked) == 2, 3)
      and deep.asked[1] == "And what about Friday?", deep.asked)
wait(lambda: vs.status()["state"] == "listening" and len(spk.played) >= 6, 8)
check("  once", deep.asked.count("And what about Friday?") == 1, deep.asked)
done(vs, rec)

vs, rec, deep, spk, logged = deep_session()                # turn 12's shape: they go on after the answer
rec.says_heard = True
rec.row(vs, turn=0, end_turn=True, text="Is the budget final?")
wait(lambda: rec.mode() == "voice" and spk.played, 3)
time.sleep(0.2)
rec.row(vs, turn=1, t=5, end=6, text="So the plan for the launch")
wait(lambda: spk.played[-2:] == [B1, B2], 6)
wait(lambda: vs.status()["state"] == "listening" and rec.mode() == "voice", 3)
rec.row(vs, turn=1, t=9, end=10, text="can we move it to Monday?")
check("orb: they go on after the answer: the orb has all of it",
      wait(lambda: vs.status()["you"] == "So the plan for the launch can we move it to Monday?", 1.5), vs.status())
rec.row(vs, turn=1, end_turn=True, text="So the plan for the launch can we move it to Monday?")
check("  asked with all of it, once", wait(lambda: len(deep.asked) == 2, 3)
      and deep.asked[1] == "So the plan for the launch can we move it to Monday?" and not time.sleep(0.5)
      and len(deep.asked) == 2, deep.asked)
done(vs, rec)

vs, rec, agent, spk, logged = session()
rec.says_heard = True
agent.pieces, agent.gap = ANSWER, 0.05
rec.row(vs, turn=0, t=1, end=2, text="Um.")
check("orb: a turn on its way shows its words", wait(lambda: vs.status()["you"] == "Um.", 1.5), vs.status())
rec.row(vs, turn=0, end_turn=True, text="Um.")
check("  dropped (only a sound): nothing asked, and the orb clears", wait(lambda: vs.status()["you"] == "", 1.5)
      and agent.asked == [] and vs.status()["state"] == "listening", (vs.status(), agent.asked))
done(vs, rec)

vs, rec, agent, spk, logged = session()
rec.says_heard = True
agent.pieces, agent.gap = ANSWER, 0.6
rec.row(vs, turn=0, end_turn=True, text="What's the Q3 budget?")
wait(lambda: agent.asked == ["What's the Q3 budget?"], 2)
rec.row(vs, turn=1, t=5, end=6, text="And for Q4 too.")
check("orb: re-asked (they went on): the question so far and the words after it",
      wait(lambda: vs.status()["state"] == "listening", 2)
      and wait(lambda: vs.status()["you"] == "What's the Q3 budget? And for Q4 too.", 1.5), vs.status())
rec.row(vs, turn=1, end_turn=True, text="And for Q4 too.")
check("  asked again with all of it, the orb showing that question", wait(lambda: len(agent.asked) == 2, 3)
      and vs.status()["you"] == "What's the Q3 budget? And for Q4 too.", (agent.asked, vs.status()))
check("  the answer ends: neither its words nor the question stay",
      wait(lambda: spk.said == [S1, S2, S3], 5) and wait(lambda: vs.status()["state"] == "listening", 3)
      and wait(lambda: vs.status()["you"] == "" and vs.status()["claude"] == "", 1.5), vs.status())
rec.row(vs, turn=2, t=12, end=13, text="One more thing")
wait(lambda: vs.status()["you"] == "One more thing", 1.5)
vs.stop()
check("orb: voice mode off: nothing left on it", vs.status()["state"] == "off" and vs.status()["you"] == ""
      and vs.status()["claude"] == "", vs.status())
rec.on = False

# ---------------------------------------------------------------- news said into a quiet moment
vs, rec, agent, spk, logged = session()
wait(lambda: vs.state == "listening")
rec.you = True                                   # the user is talking: news waits
wait(lambda: json.loads((vs.sdir / "live.json").read_text()).get("speaking"))
vs.announce("  Claude Code is done.   The tests pass. ")
time.sleep(0.4)
check("announce: nothing said over the user", "Claude Code is done. The tests pass." not in spk.said, spk.said)
rec.you = False
check("  said once they stop, as one line, spaces tidied",
      wait(lambda: "Claude Code is done. The tests pass." in spk.said) and agent.asked == [], (spk.said, agent.asked))
check("  then the mic is theirs again", wait(lambda: vs.state == "listening"), vs.state)
for i in range(5):
    vs._told.append("news %d" % i)             # more than it keeps
check("  no more than three wait: the newest", list(vs._told) == ["news 2", "news 3", "news 4"], list(vs._told))
vs.announce("")
check("  nothing to say is not queued", len(vs._told) == 3)
done(vs, rec)

# ---------------------------------------------------------------- Claude speaks first (a call's greeting)
vs, rec, agent, spk, logged = session()
agent.pieces = ["Hi, what can I do for you?"]
vs.open_with("[CALL START]")
check("greeting: asked once, on its own, the words for the agent never on the orb",
      wait(lambda: agent.asked == ["[CALL START]"], 3) and vs.status()["you"] == "", (agent.asked, vs.status()))
check("  said, then the mic is theirs", wait(lambda: "Hi, what can I do for you?" in spk.said, 3)
      and wait(lambda: vs.state == "listening", 3), (spk.said, vs.state))
time.sleep(0.2)
check("  only once", agent.asked == ["[CALL START]"], agent.asked)
done(vs, rec)
vs, rec, agent, spk, logged = session()
wait(lambda: vs.state == "listening")
rec.you = True                                   # they're already talking
wait(lambda: json.loads((vs.sdir / "live.json").read_text()).get("voice", {}).get("you_speaking"))
vs.open_with("[CALL START]")
time.sleep(0.3)
rec.you = False
time.sleep(0.3)
check("  they spoke first: no greeting", agent.asked == [] and vs._opening is None, agent.asked)
done(vs, rec)
# They talk while the greeting is still being thought of: their words are a question of their own.
# "[CALL START]" was carried into it ("[CALL START] This is the test, one, two, three.").
vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ["Hi there. ", "What can I do for you?"], 0.6
vs.open_with("[CALL START]")
wait(lambda: agent.asked == ["[CALL START]"], 3)
rec.row(vs, turn=1, t=1, end=3, text="This is the test, one, two, three.")
rec.row(vs, turn=1, end_turn=True, text="This is the test, one, two, three.")
check("  they talk over the greeting being thought of: their words asked on their own, never with [CALL START]",
      wait(lambda: len(agent.asked) >= 2, 5) and agent.asked[-1] == "This is the test, one, two, three."
      and vs.status()["you"] in ("", "This is the test, one, two, three."), (agent.asked, vs.status()))
done(vs, rec)

# A reply that's only a signal brings no deltas (the agent's stream holds the signal back), so it's said
# from the answer: "[QUIET]" was said aloud as a call's greeting, and kept in the record as said.
class OnlyAnswer(Agent):
    def ask(self, question, on_event, on_queued, heard=None):
        self.asked.append(question); self.heards.append(heard)
        on_queued("q%d" % len(self.asked))
        time.sleep(0.05)
        return self.pieces.pop(0)


vs, rec, agent, spk, logged = session(agent=OnlyAnswer())
agent.pieces = ["[QUIET]"]
vs.open_with("[CALL START]")
check("a greeting that's only [QUIET]: nothing said, and the mic is theirs",
      wait(lambda: agent.asked == ["[CALL START]"], 3) and wait(lambda: vs.state == "listening", 3)
      and time.sleep(0.3) is None and spk.said == [], (spk.said, vs.state))
done(vs, rec)
vs, rec, agent, spk, logged = session(agent=OnlyAnswer())
agent.pieces = ["Talk soon. Bye for now. [END]"]
rec.row(vs, turn=0, end_turn=True, text="Okay, bye.")
check("  a goodbye with [END], no deltas: all of it said (only its last sentence was), never the signal",
      wait(lambda: spk.said == ["Talk soon.", "Bye for now."], 5), (spk.said, agent.asked))
done(vs, rec)

# ---------------------------------------------------------------- the voice can't be heard: the orb says so
vs, rec, agent, spk, logged = session()
wait(lambda: vs.state == "listening")
spk.voice_failed = "Standby's voice failed (no model)"
check("Standby's voice isn't working (never another voice instead): the orb and the window say so, and the answers show",
      vs.status().get("notice") == "Standby's voice isn't working (Standby's voice failed (no model)). Answers are shown here.",
      vs.status())
spk.voice_failed = ""
check("  working again: the notice goes", "notice" not in vs.status(), vs.status())
spk.voice_retrying_since = 1792600000.5
check("the voice coming back (the recorder reopens it): since when, for the band, and no notice yet",
      vs.status().get("retrying_since") == 1792600000.5 and "notice" not in vs.status(), vs.status())
spk.voice_failed = "Standby's voice failed (no model)"
check("  given up: the notice alone, not both", "retrying_since" not in vs.status() and vs.status().get("notice"), vs.status())
spk.voice_retrying_since, spk.voice_failed = None, ""
done(vs, rec)

# ---------------------------------------------------------------- words over Claude: never lost (the traced calls)
# The recorder now keeps what's said over Claude's voice (echo-cancelled, or on headphones) as
# rows marked "over": a turn, or a backchannel, judged here, never dropped as "floor".
GOLD = ["Got it, gold. ", "Do you mean the snake should turn gold instead of purple? ",
        "I'll wait for the arrow key edit to finish first, so we don't clash.\n"]
G1, G2, G3 = [g.strip() for g in GOLD]

# talk-20261024-232831, 23:29:39: "Yes." said to the voice's own question while it went on.
vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = GOLD, 0.02
rec.row(vs, turn=3, end_turn=True, text="How about doing the gold colour?")
speaking(vs, spk, G3)
rec.row(vs, turn=4, t=62.07, end=63.45, text="Yes.", over=True)
check("23:29:39: \"Yes.\" said over it, to the question it asked: a turn, it stops",
      wait(lambda: any("interrupted" in m for m in logged), 3), logged[-3:])
rec.row(vs, turn=4, end_turn=True, text="Yes.")
check("  and \"Yes.\" is asked, Claude told what they heard, the question with it",
      wait(lambda: len(agent.asked) == 2, 3) and agent.asked[1] == "Yes." and G2 in (agent.heards[1] or ""),
      (agent.asked, agent.heards))
done(vs, rec)

vs, rec, agent, spk, logged = session(secs=0.6)
agent.pieces, agent.gap = ANSWER, 0.02
rec.row(vs, turn=0, end_turn=True, text="What is Orion?")
speaking(vs, spk, S2)
rec.row(vs, turn=1, t=5, end=6, text="Mm-hmm.", over=True)
rec.row(vs, turn=1, end_turn=True, text="Mm-hmm.")
check("  \"mm-hm\" over it with nothing asked: it goes on, nothing asked",
      wait(lambda: vs.status()["state"] == "listening", 4) and spk.said == [S1, S2, S3]
      and agent.asked == ["What is Orion?"] and not any("interrupted" in m for m in logged), (spk.said, agent.asked))
done(vs, rec)

# talk-20261024-061950, 06:23:48-55: the answer was ready while they still spoke; at 6 s the
# voice took the floor over them, and the sentence was lost. Now it waits while they talk, and
# their words are the turn: asked again with all of it.
vs, rec, agent, spk, logged = session()
agent.pieces, agent.gap = ["Great. ", "I'll let you know when the stars are in.\n"], 0.5
rec.row(vs, turn=12, end_turn=True, text="I'm good.")
wait(lambda: agent.asked, 2)
rec.you = True                       # they go on before its first word is ready
time.sleep(6.5)
check("06:23:48: still talking 6.5 s after the answer was ready: it hasn't spoken over them",
      spk.said == [] and rec.mode() == "voice", (spk.said, rec.mode()))
rec.row(vs, turn=13, t=232.53, end=239.49, text="N not finished yet. You just said not finished?")
rec.you = False
check("  their sentence is more to say: the answer unsaid is dropped, quietly",
      wait(lambda: agent.cancelled == ["q1"], 2) and agent.quieted == ["q1"] and spk.said == [], (agent.cancelled, spk.said))
rec.row(vs, turn=13, end_turn=True, text="N not finished yet. You just said not finished?")
check("  and asked with all of it", wait(lambda: len(agent.asked) == 2, 3)
      and agent.asked[1] == "I'm good. N not finished yet. You just said not finished?", agent.asked)
done(vs, rec)

shutil.rmtree(root, ignore_errors=True)
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
