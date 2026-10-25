"""Voice mode's speed: the user's recorded turns replayed through the real
VoiceSession, before and after asking early, end of speech to first sound.

    python tools/voice_speed.py [--turns N]

Each recorded `voice timing` line (tests/fixtures/voice_timing.txt: turn over,
Claude's first word, sound, in all, from the user's ui.log) becomes one turn:
a stand-in recorder writes its rows, a stand-in Claude answers after that
turn's own first-word time, a stand-in voice starts 30 ms after each sentence
(Pocket TTS, measured). What changes between the two runs is only the early asking:

- before: the end_turn row at the recorded "turn over"; the first sentence
  said once it is whole.
- after: the likely_turn row at VOICE_EARLY of quiet plus one decode, so Claude
  is asked then; the end_turn row a decode sooner (the early words are kept);
  the first clause said first.

A share of the turns go on after a likely pause (GO_ON): the early question is
then dropped and the whole turn asked, so the voice calls are counted too (an
early start must not double them). Nothing is played; no Claude, no recorder.
First words over CAP seconds (real work, [DEEP]) are capped to keep the run
short: they are above the median either way.
"""

import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "mac"))
os.environ.setdefault("STANDBY_ROOT", tempfile.mkdtemp(prefix="sbspeed-"))
import voice  # noqa: E402

DECODE = 0.15          # parakeet on one utterance, as the recorder logs it
QUIET = 0.45           # Channel.VOICE_END_SILENCE: the quiet that closes an utterance
EARLY = 0.2            # Channel.VOICE_EARLY
TTS_FIRST = 0.03       # Pocket TTS: first sound after a sentence's text
SENTENCE = 0.15        # how long the stand-in voice says each sentence
WPS = 55.0             # words a second as the answer streams (the recorded "sound" step, ~0.2 s)
CAP = 3.0
GO_ON = 7              # every 7th turn goes on after its likely pause (~15%)
ANSWER = "The migration is on track, and the cache moved to Orion on Monday. The old cluster goes on Friday."


def recorded(path=HERE.parent / "tests" / "fixtures" / "voice_timing.txt"):
    return [tuple(float(x) for x in line.split()) for line in open(path) if line.strip()]


class Voice(object):
    """The stand-in voice: each sentence starts TTS_FIRST after it is given."""

    def __init__(self):
        self.q, self.lock, self.said = [], threading.Lock(), []
        self.current, self.started_at, self._until = "", None, 0.0

    def say(self, text):
        with self.lock:
            self.q.append((time.time() + TTS_FIRST, text))
            self.said.append(text)

    def stop(self):
        with self.lock:
            self.q, self._until, self.current = [], 0.0, ""

    def _tick(self):
        with self.lock:
            now = time.time()
            if now >= self._until and self.q and now >= self.q[0][0]:
                at, self.current = self.q.pop(0)
                self.started_at, self._until = at, now + SENTENCE

    @property
    def speaking(self):
        self._tick()
        return time.time() < self._until

    @property
    def busy(self):
        self._tick()
        with self.lock:
            return bool(self.q) or time.time() < self._until


def run(turns, after, log=lambda m: None):
    """Replay `turns` (turn over, first word, sound, in all): the 'in all' times
    VoiceSession logs, and how many questions Claude got."""
    voice.SentenceStream.FIRST_WORDS = 4 if after else 10 ** 6
    sdir = Path(tempfile.mkdtemp(prefix="sbspeed-"))
    rows = sdir / "dictation.jsonl"
    rows.write_text("")
    alive = {"on": True}

    def beat():
        while alive["on"]:
            (sdir / "live.json").write_text(json.dumps({"at": time.time(), "level": 0.0}))
            time.sleep(0.05)
    threading.Thread(target=beat, daemon=True).start()
    calls, cancelled, lines, first = [], set(), [], {}

    def ask(question, on_event, on_queued):
        calls.append(question)
        qid = "q%d" % len(calls)
        on_queued(qid)
        end = time.time() + min(first["now"], CAP)
        while time.time() < end:
            if qid in cancelled:
                return "(stopped)"
            time.sleep(0.005)
        words = ANSWER.split()
        for i, w in enumerate(words):
            if qid in cancelled:
                return "(stopped)"
            on_event({"kind": "delta", "text": w + ("" if i == len(words) - 1 else " ")})
            time.sleep(1.0 / WPS)
        return ANSWER

    spk = Voice()
    vs = voice.VoiceSession("speed", sdir, ask=ask, cancel=cancelled.add, speaker=spk,
                            log=lambda m: (lines.append(m), log(m)))
    vs.GUARD = 0.05
    vs.start()

    def row(**kw):
        with open(rows, "a") as fh:
            fh.write(json.dumps(dict(kw, seq=vs.seq)) + "\n")

    for i, (turn_over, first_word, _sound, _all) in enumerate(turns):
        while vs.status()["state"] != "listening":
            time.sleep(0.01)
        first["now"] = first_word
        goes_on = (i % GO_ON) == GO_ON - 1
        t_end = time.time()                       # the user stops speaking (for a moment, if they go on)
        text = "So what is the plan for the cache?"
        if goes_on:                               # a pause, likely-final words, then more
            if after:
                time.sleep(EARLY + DECODE)
                row(turn=i, likely_turn=True, text="So what is the plan?")
            time.sleep(max(0.0, t_end + 1.0 - time.time()))
            t_end, text = time.time(), "So what is the plan? For the cache, I mean."
        if after:
            time.sleep(max(0.0, t_end + EARLY + DECODE - time.time()))
            row(turn=i, likely_turn=True, text=text)
            time.sleep(max(0.0, t_end + max(QUIET, turn_over - DECODE) - time.time()))
        else:
            time.sleep(max(0.0, t_end + turn_over - time.time()))
        row(turn=i, end_turn=True, text=text, spoke_until=t_end)
        n = len([m for m in lines if m.startswith("voice timing")])
        while len([m for m in lines if m.startswith("voice timing")]) == n:
            time.sleep(0.01)
    while vs.status()["state"] != "listening":
        time.sleep(0.01)
    vs.stop()
    alive["on"] = False
    totals = []
    for m in lines:
        if m.startswith("voice timing"):
            part = m.split("|")[1].strip().split("s in all")[0]
            totals.append(float(part))
    return totals, len(calls), vs.early_dropped


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--turns", type=int, default=0, help="only the first N recorded turns")
    a = p.parse_args(argv)
    turns = recorded()
    turns = turns[:a.turns] if a.turns else turns
    result = {}
    for name, after in (("before", False), ("after", True)):
        totals, calls, dropped = run(turns, after)
        result[name] = {"median": round(statistics.median(totals), 2), "turns": len(totals),
                        "calls": calls, "dropped": dropped,
                        "quick": round(statistics.median([t for t, (_o, f, _s, _a) in zip(totals, turns) if f <= 1.5]), 2)}
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    main()
