"""How often a turn is called over while the user is still talking.

A false cut-off is Standby answering mid-thought. It happens when the recorder's
0.45 s of quiet closes an utterance in a pause the user meant to speak through,
and then turn.py (punctuation plus Smart Turn) decides the turn is over. Measured
on sentences said by `say` into files, with real pauses in the middle (nothing is
played), through the recorder's own UtteranceTranscriber and the real Turns:

  held     the pause was sat through, and the whole sentence became one turn
  cut      the turn was called over in the pause: a false cut-off
  late     answered after the user finished, but more than a second later

LiveKit's benchmark puts Smart Turn v3.2 at 15-35% false cut-offs at 300-600 ms
of silence; this says what it does here, before anyone shortens 0.45 s.

Each is run twice: turn.Turns as it is (never under FLOOR), and with SURE in
place of FLOOR when Smart Turn is SURE_AT or more (SureTurns, Q5: tried and not
shipped, 2026-10-20), with the median time from the last word to the turn being over.

    ~/.standby/venv/bin/python tests/measure_cutoffs.py [--voices N] [--pause S] [--sure-at P,P]

The installed Smart Turn model is read (read-only; nothing in ~/.standby is
written, as everywhere in tests), so these are the numbers voice mode really has.
"""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import os, shutil, subprocess, sys, tempfile, time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import measure_interruptions as mi  # noqa: E402
import turn  # noqa: E402

sb = mi.sb
RATE = sb.SAMPLE_RATE

# Each line is said as its parts, with a real pause between them: the kind of
# sentence people pause inside (thinking, listing, trailing off and carrying on).
SENTENCES = [
    ["So what I want to know is", "whether the budget covers the EU region."],
    ["The thing is", "we never agreed on the deadline."],
    ["Can you check", "what Nadia said about the migration?"],
    ["I was thinking", "maybe we should push the launch."],
    ["Three things.", "The cache, the queue, and the retries."],
    ["Let me see", "how much of that is already done."],
    ["It depends on", "whether the vendor replies today."],
    ["Right, so", "the plan is to ship on Friday and tell support on Monday."],
    ["What about", "the hiring plan for next quarter?"],
    ["And then", "we need to decide who owns the rollout."],
]
WHOLE = [
    "What is the Q3 budget?",                      # finished: answering at once is right
    "Who owns the migration?",
    "Thanks, that's all I needed.",
]


def said(parts, voice, pause, d):
    """The parts said one after another with `pause` seconds of quiet between,
    then the quiet that closes the last utterance."""
    out = []
    for i, part in enumerate(parts):
        out.append(mi.speech(part, voice, d))
        out.append(np.zeros(int((pause if i < len(parts) - 1 else 1.2) * RATE), "float32"))
    return np.concatenate(out)


class SureTurns(turn.Turns):
    """Q5: wait SURE, not FLOOR, when Smart Turn is SURE_AT or more (punctuated, no
    hesitation: _wait_for gives 0.0 only then). Not shipped: on 3 h of the user's
    room audio it cut in on 10, 9 and 8 more pauses at 0.85, 0.95 and 0.97 (51 today);
    here 11 and 7 against 5 of 60 at a 0.6 s pause, at 0.85 and 0.95. At 0.99 it
    changed nothing either way (2026-10-20)."""
    SURE, SURE_AT = 0.15, 0.85

    def _wait(self):
        wait = self._wait_for()
        if wait == 0.0 and self.last_p is not None and self.last_p >= self.SURE_AT:
            return self.SURE
        return max(self.FLOOR, wait)


# None: turn.Turns as it is. A number: SureTurns at that threshold.
RULES = [("today", None), ("SURE", SureTurns.SURE_AT)]


def run(model, st, audio, sure_at=None):
    """The recorder's stream and turn.py on this speech, block by block as the mic
    feeds it. -> [(seconds from the start, the turn's text, Smart Turn's p)]."""
    stream = sb.UtteranceTranscriber(model)
    stream.end_silence = sb.Channel.VOICE_END_SILENCE
    turns = turn.Turns(st) if sure_at is None else SureTurns(st)
    if sure_at is not None:
        turns.SURE_AT = sure_at
    room = np.random.RandomState(4).randn(len(audio)).astype("float32") * 0.002
    mic = audio + room
    block, out, t = int(0.05 * RATE), [], 0.0
    for i in range(0, len(mic), block):
        stream.feed(mic[i:i + block])
        t = (i + block) / float(RATE)
        for item in stream.drain():
            done = turns.feed(item[2], item[3] if len(item) > 3 else None, t)
            if done:
                out.append((t, done, turns.last_p))
        done = turns.tick(t, stream.utt is not None)
        if done:
            out.append((t, done, turns.last_p))
    return out


def main():
    n = int(sys.argv[sys.argv.index("--voices") + 1]) if "--voices" in sys.argv else 6
    pauses = [float(x) for x in (sys.argv[sys.argv.index("--pause") + 1].split(",")
                                 if "--pause" in sys.argv else ("0.6", "0.9", "1.4"))]
    voices = mi.installed()[:n]
    if "--sure-at" in sys.argv:            # try other thresholds: --sure-at 0.9,0.95
        RULES[1:] = [("SURE", float(x)) for x in sys.argv[sys.argv.index("--sure-at") + 1].split(",")]
    mi.wait_for_quiet()
    model = sb.load_model(sb.DEFAULT_MODEL)
    # The real model, read where the installer put it: the tests' root is a temp one.
    installed = os.path.join(os.path.expanduser("~/.standby"), "models", turn.MODEL_NAME)
    st = turn.load(installed if os.path.exists(installed) else turn.MODEL, log=lambda m: print("  " + m))
    print("false cut-offs, %d voices, Smart Turn %s, utterance closes at %.2fs"
          % (len(voices), "on" if st is not None else "OFF (punctuation only)", sb.Channel.VOICE_END_SILENCE))
    d = tempfile.mkdtemp(prefix="sbcut-")
    try:
        cases = [(voice, parts, pause, said(parts, voice, pause, d))
                 for pause in pauses for voice in voices for parts in SENTENCES]
        wholes = [(voice, text, said([text], voice, 0, d)) for voice in voices for text in WHOLE]
        for rule, sure_at in RULES:
            print(" %s (%s)" % (rule, "never under FLOOR %.2fs" % turn.Turns.FLOOR if sure_at is None
                                else "SURE %.2fs at Smart Turn %.2f or more" % (SureTurns.SURE, sure_at)))
            for pause in pauses:
                tally, examples, over = defaultdict(int), [], []
                for voice, parts, p_, audio in cases:
                    if p_ != pause:
                        continue
                    mi.wait_for_quiet()
                    turns = run(model, st, audio, sure_at)
                    whole = " ".join(parts)
                    # Cut off: a turn ended before the last part was spoken.
                    cut = [t for t in turns if mi.interrupt.words(t[1]) != mi.interrupt.words(whole)
                           and len(mi.interrupt.words(t[1])) < len(mi.interrupt.words(whole))]
                    tally["n"] += 1
                    if len(turns) > 1 or cut:
                        tally["cut"] += 1
                        if len(examples) < 6:
                            examples.append((voice, whole, [(t[1], t[2]) for t in turns]))
                        continue
                    # Held: how long after the last word (the audio ends 1.2 s after it).
                    took = turns[0][0] - (len(audio) / float(RATE) - 1.2) if turns else None
                    if took is None or took > 1.0:
                        tally["late"] += 1
                    else:
                        tally["held"] += 1
                    if took is not None:
                        over.append(took)
                print("  a %.1fs pause mid-sentence: of %d, %d held, %d cut off (%.0f%%), %d late;"
                      " turn over median %.2fs"
                      % (pause, tally["n"], tally["held"], tally["cut"], 100.0 * tally["cut"] / tally["n"],
                         tally["late"], float(np.median(over)) if over else float("nan")))
                for voice, whole, got in examples:
                    print("     %-18s %r" % (voice[:18], whole))
                    for text, p in got:
                        print("        %-58r Smart Turn %s" % (text, "-" if p is None else "%.2f" % p))
            # And the other way: a finished question must be answered at once.
            slow, over = 0, []
            for voice, text, audio in wholes:
                mi.wait_for_quiet()
                turns = run(model, st, audio, sure_at)
                if not turns or mi.interrupt.words(turns[0][1]) != mi.interrupt.words(text):
                    slow += 1
                else:
                    over.append(turns[0][0] - (len(audio) / float(RATE) - 1.2))
            print("  finished sentences answered as one whole turn: %d of %d; turn over median %.2fs"
                  % (len(wholes) - slow, len(wholes), float(np.median(over)) if over else float("nan")))
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
