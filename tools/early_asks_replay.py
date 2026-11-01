#!/usr/bin/env python3
"""Early asks at every pause, on the user's recorded calls: what it would cost and gain, and proof that it
never speaks or acts early. Reads ~/.standby/sessions/talk-*/trace.jsonl on this Mac; prints numbers only,
never the words (they stay here).

    python3 tools/early_asks_replay.py            the cost and the gain: asks per turn, off and on
    python3 tools/early_asks_replay.py --prove    and each call's turns replayed through the real VoiceSession

A trace has every early decode (its words, at a short pause in an open utterance), the turn verdict that
followed it (turn_likely: likely or not), the utterances the turn kept (heard, fate "kept") and the turn's end
(turn_end, its whole words). The words the voice would ask on at a pause are the turn's kept utterances so far
and the early decode's (Turns.heard_with); rebuilt so, they are checked against the words the trace recorded
for the pauses judged likely.

Today: an early ask only at a pause judged likely. Every pause: at each with an early decode. The cheaper ones:
a pause after a chunk of new words, a few a turn (POLICIES). Whichever,
the same words again ask nothing more, other words replace the early question (one more ask), and the turn's
end takes it if its words are the turn's (the answer started that much sooner), else drops it.

--prove: each turn's pauses as the recorder's rows (early_ask, or likely_turn), then its end_turn, through
voice.VoiceSession with a stand-in model that answers at once, one sentence each, and tries to act on every
answer (commit(), as the app does before a hand-off). Every sentence said, and every action, must come after
its turn's end_turn row, and only for the words of that turn; every turn's answer is said."""
import argparse
import collections
import glob
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

REAL = os.path.expanduser(os.environ.get("STANDBY_ROOT", "~/.standby"))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "mac"))


def turns_of(path):
    """A trace's turns: {turn: {"pauses": [(t, words, likely, recorded)], "end": (t, words), "timing": {...}}}, in
    order. timing (the turn's own timing event): engine, the rest of the wait to the first word, the voice's
    first sound after it, and the wait from the end of their speech to the turn's end, in seconds."""
    try:
        ev = sorted((json.loads(l) for l in open(path, encoding="utf-8") if l.strip()), key=lambda e: e.get("t", 0))
    except (OSError, ValueError):
        return {}
    turns, kept, last_early = collections.OrderedDict(), collections.defaultdict(list), None
    new = lambda: {"pauses": [], "end": None, "timing": None, "waited": None}
    for e in ev:
        k = e.get("ev")
        if k == "decode" and e.get("early"):
            last_early = e.get("text") or ""
        elif k == "heard" and e.get("fate") == "kept" and e.get("turn") is not None:
            kept[e["turn"]].append(e.get("text") or "")
        elif k == "turn_likely" and e.get("turn") is not None and last_early is not None:
            words = " ".join(w for w in kept[e["turn"]] + [last_early] if w)
            turns.setdefault(e["turn"], new())["pauses"].append((e["t"], words, bool(e.get("likely")), e.get("text")))
            last_early = None
        elif k == "turn_end" and e.get("turn") is not None:
            t = turns.setdefault(e["turn"], new())
            t["end"], t["waited"] = (e["t"], e.get("text") or ""), (e.get("waited_ms") or 0) / 1000.0
        elif k == "timing" and e.get("turn") is not None and isinstance(e.get("parts"), dict):
            parts = e["parts"]
            engine = (parts.get("engine") or 0) / 1000.0
            turns.setdefault(e["turn"], new())["timing"] = {
                "engine": engine, "rest": sum(v or 0 for v in parts.values()) / 1000.0 - engine,
                "voice": (e.get("voice_ms") or 0) / 1000.0}
    return {n: t for n, t in turns.items() if t["end"] is not None}


def words_in(text):
    return len((text or "").split())


# Which pauses ask early: policy(likely, words, last asked words or None, the turn's counts) -> ask?
POLICIES = [
    ("today: likely pauses", lambda likely, w, last, n: likely),
    ("every pause", lambda likely, w, last, n: True),
    ("likely, + 5 new words, 2 more", None),       # filled in by chunk() below
    ("5 new words, 2 a turn", None),
]


def chunk(new_words, most, keep_likely):
    """A pause after a real chunk of words: at least new_words more than the words last asked on (all of
    them, for a turn's first); at most `most` such asks a turn (keep_likely: on top of the likely pauses,
    which ask as today)."""
    def policy(likely, w, last, n):
        if keep_likely and likely:
            return True
        grown = words_in(w) - (words_in(last) if last else 0)
        if grown < new_words or n["chunk"] >= most:
            return False
        n["chunk"] += 1
        return True
    return policy


POLICIES[2] = (POLICIES[2][0], chunk(5, 2, True))
POLICIES[3] = (POLICIES[3][0], chunk(5, 2, False))


def simulate(turn, policy, is_words):
    """One turn's early asks, as voice._ask_early would make them under `policy`: (asks, taken_at or None)."""
    asks, current, since, n = 0, None, None, {"chunk": 0}
    for t, words, likely, _rec in turn["pauses"]:
        if not is_words(words) or words == current or not policy(likely, words, current, n):
            continue
        asks, current, since = asks + 1, words, t
    return asks, (since if current is not None and current == turn["end"][1] else None)


def first_sound(turn, at):
    """From the end of their speech to the voice's first sound, if the turn's answer was asked at `at` (None:
    at the turn's end): the turn's own engine time, started that much sooner (none sooner than the end)."""
    tm, end = turn["timing"], turn["end"][0]
    wait = tm["engine"] if at is None else max(0.0, at + tm["engine"] - end)
    return turn["waited"] + tm["rest"] + wait + tm["voice"]


def pct(xs, q):
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def measure(calls, is_words):
    n, rebuilt, matched, timed = 0, 0, 0, 0
    sums = [[0, 0, []] for _ in POLICIES]                 # asks, turns taken early, first sound (timed turns)
    for path in calls:
        for turn in turns_of(path).values():
            n += 1
            for _t, words, likely, rec in turn["pauses"]:
                if likely and rec is not None:
                    rebuilt += 1
                    matched += words == rec
            timed += turn["timing"] is not None
            for k, (_label, policy) in enumerate(POLICIES):
                asks, at = simulate(turn, policy, is_words)
                sums[k][0] += asks
                sums[k][1] += at is not None
                if turn["timing"] is not None:
                    sums[k][2].append(first_sound(turn, at))
    print("%d calls, %d turns (%d with timing)" % (len(calls), n, timed))
    print("  words rebuilt at the pauses judged likely: %d of %d match the trace's own" % (matched, rebuilt))
    print("  %-30s %8s %10s   end of speech to first sound: median, p75, p90" % ("", "asks/turn", "early"))
    for (label, _p), (asks, taken, fs) in zip(POLICIES, sums):
        fs.sort()
        print("  %-30s %8.2f %9.0f%%   %.2f s, %.2f s, %.2f s"
              % (label, asks / max(n, 1), 100.0 * taken / max(n, 1), pct(fs, .5), pct(fs, .75), pct(fs, .9)))
    return n


def prove(calls, is_words, limit, verbose=False):
    """Each call's turns through the real VoiceSession: the held answers never speak or act early."""
    root = tempfile.mkdtemp(prefix="sbearly-")
    os.environ["STANDBY_ROOT"] = root
    import voice
    bad, said_turns, total, n_asks = [], 0, 0, 0

    class Speaker(object):
        def __init__(self):
            self.said, self._until, self._q, self.current, self.started_at = [], 0.0, [], "", None

        def say(self, text):
            self.said.append((time.time(), text))
            self._q.append(text)

        def stop(self):
            self._q, self._until, self.current = [], 0.0, ""

        def _tick(self):
            if time.time() >= self._until and self._q:
                self.current = self._q.pop(0)
                self._until, self.started_at = time.time() + 0.02, time.time()

        @property
        def speaking(self):
            self._tick()
            return time.time() < self._until

        @property
        def busy(self):
            self._tick()
            return bool(self._q) or time.time() < self._until

    for path in calls:
        turns = list(turns_of(path).values())[:limit or None]
        if not turns:
            continue
        sid = "talk-replay-%d" % calls.index(path)          # a folder of its own: nothing of the last call's in it
        sdir = os.path.join(root, "sessions", sid)
        os.makedirs(sdir, exist_ok=True)
        rows = os.path.join(sdir, "dictation.jsonl")
        open(rows, "w").close()
        on = {"rec": True}

        def recorder():
            while on["rec"]:
                tmp = os.path.join(sdir, "live-%d.tmp" % threading.get_ident())
                with open(tmp, "w") as fh:
                    json.dump({"at": time.time(), "level": 0.0, "partial": "", "speaking": False}, fh)
                os.replace(tmp, os.path.join(sdir, "live.json"))
                time.sleep(0.03)

        threading.Thread(target=recorder, daemon=True).start()
        questions, acted = [], []          # (words, asked at); (words, at, go)

        def ask(question, on_event, on_queued, heard=None, commit=None):
            questions.append((question, time.time()))
            k = len(questions)
            on_queued("q%d" % k)
            on_event({"kind": "delta", "text": "A%d. " % k})          # its answer, ready at once
            go = commit() if commit is not None else True
            acted.append((question, time.time(), go))
            return "A%d." % k

        spk = Speaker()
        vs = voice.VoiceSession(sid, pathlib.Path(sdir), ask=ask,
                                cancel=lambda *a, **k: None, speaker=spk, log=lambda m: None)
        vs.GUARD = 0.02
        vs.start()
        ends, slow = {}, []                 # turn index -> when its end_turn row was written; turns not over in 20 s
        answer_words = lambda text: next((q for k, (q, _a) in enumerate(questions) if "A%d." % (k + 1) == text.strip()), "")

        def row(**kw):
            with open(rows, "a") as fh:
                fh.write(json.dumps(dict(kw, seq=vs.seq)) + "\n")

        for i, turn in enumerate(turns):
            t_turn = time.time()
            deadline = time.time() + 10
            while vs.status()["state"] != "listening" and time.time() < deadline:
                time.sleep(0.01)
            for _t, words, likely, _rec in turn["pauses"]:
                row(turn=i, text=words, **({"likely_turn": True} if likely else {"early_ask": True}))
                time.sleep(0.06)            # the held answer is ready long before the turn's end
            ends[i] = time.time()
            row(turn=i, end_turn=True, text=turn["end"][1], spoke_until=time.time() - 0.3)
            # the next turn only once this one's answer has been said and it listens again: their next words
            # came after it on the call, never into it (rows mid-answer are more words, or a cut)
            deadline = time.time() + 20
            said_for = lambda: any(answer_words(text).endswith(turn["end"][1]) for _at, text in spk.said)
            while is_words(turn["end"][1]) and not said_for() and time.time() < deadline:
                time.sleep(0.01)
            time.sleep(0.1)
            while vs.status()["state"] != "listening" and time.time() < deadline:
                time.sleep(0.01)
            if time.time() >= deadline:
                slow.append(i)
            if verbose:
                print("    turn %d: %d pauses, %.2f s, %s" % (i, len(turn["pauses"]), time.time() - t_turn, vs.status()["state"]))
        deadline = time.time() + 10
        while vs.status()["state"] != "listening" and time.time() < deadline:
            time.sleep(0.01)
        on["rec"] = False
        vs.stop()
        total += len(turns)
        n_asks += len(questions)
        # The turn a question was for: one whose whole words it is (after words carried from a turn they cut
        # off, in front), ended by the time of what it did (said or acted); None if it is no turn's words,
        # or none of them had ended yet: it went early.
        def turn_of(words, at):
            mine = lambda t: words == t["end"][1] or (words or "").endswith(" " + t["end"][1])  # or after carried words
            done = [i for i, t in enumerate(turns) if mine(t) and ends[i] <= at]
            return done[-1] if done else None
        answer_of = {"A%d." % (k + 1): q for k, q in enumerate(questions)}
        spoken = set()
        call = os.path.basename(os.path.dirname(path))
        for at, text in spk.said:
            words = answer_of.get(text.strip(), (None, None))[0]
            i = turn_of(words, at)
            if i is None:
                bad.append(("said", call, [k for k, t in enumerate(turns) if t["end"][1] == words], round(at, 2)))
            else:
                spoken.add(i)
        def what(words):
            """Where words that are no turn's came from, as a shape: a pause's early question (turn, pause),
            or two turns' words joined (more to a question, or carried after a cut), or neither."""
            for k, t in enumerate(turns):
                for j, pa in enumerate(t["pauses"]):
                    if pa[1] == words:
                        return "pause %d of turn %d (its end: %s)" % (j, k, "the same words" if t["end"][1] == words
                                                                      else "other words")
            for k in range(1, len(turns)):
                if (words or "").startswith(turns[k - 1]["end"][1]) and turns[k]["end"][1] in (words or ""):
                    return "turns %d and %d joined" % (k - 1, k)
            return "none of the turns' words (%d words)" % len((words or "").split())

        def where(words, at):
            """Every turn the words are a pause or the end of, with when that turn ended against `at` (s)."""
            out = []
            for k, t in enumerate(turns):
                tag = ("end" if t["end"][1] == words else "") + ("".join(" p%d" % j for j, pa in enumerate(t["pauses"])
                                                                         if pa[1] == words))
                if tag:
                    out.append("t%d %s (ended %+.2f s)" % (k, tag.strip(), (ends[k] - at) if k in ends else float("nan")))
            return ", ".join(out) or what(words)

        for words, at, go in acted:
            if go and turn_of(words, at) is None:
                bad.append(("acted", call, where(words, at)))
        lost = [i for i, t in enumerate(turns) if is_words(t["end"][1]) and i not in spoken]
        if slow:
            print("    (turns whose answer took over 20 s in this replay: %s)" % slow)
        bad += [("never said", os.path.basename(os.path.dirname(path)), i, 0) for i in lost]
        said_turns += len(spoken)
        print("  %s: %d turns, %d asks, %d answered after their end, %d problems"
              % (os.path.basename(os.path.dirname(path)), len(turns), len(questions), len(spoken),
                 sum(1 for b in bad if b[1] == os.path.basename(os.path.dirname(path)))))
    print("replayed %d turns, %d asks: %d answered, %d spoke or acted early, for the wrong words, or never"
          % (total, n_asks, said_turns, len(bad)))
    for b in bad[:20]:
        print("   ", b)
    return not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prove", action="store_true")
    ap.add_argument("--turns", type=int, default=0, help="at most this many turns per call, replaying")
    ap.add_argument("--calls", type=int, default=0, help="at most this many calls, replaying")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    import interrupt
    calls = sorted(glob.glob(os.path.join(REAL, "sessions", "talk-*", "trace.jsonl")))
    measure(calls, interrupt.is_words)
    if args.prove:
        sys.exit(0 if prove(calls[-args.calls:] if args.calls else calls, interrupt.is_words, args.turns, args.verbose) else 1)


if __name__ == "__main__":
    main()
