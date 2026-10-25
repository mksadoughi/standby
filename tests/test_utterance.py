"""Silence-based utterance segmentation, replayed on synthetic audio that
mimics the real run: speech, pauses, room noise, clicks, a long monologue.
The decoder is faked, so this checks segmentation and commit behaviour only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import sys
import numpy as np
from importlib.machinery import SourceFileLoader
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: print("     log: " + m)
SR = sb.SAMPLE_RATE
rng = np.random.default_rng(0)

def noise(sec, amp=0.002):
    return (rng.standard_normal(int(sec * SR)) * amp).astype("float32")
def speech(sec, amp=0.2):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype("float32")

def run(audio, decode, block=0.5):
    tr = sb.UtteranceTranscriber(None, decode=decode)
    out = []
    step = int(block * SR)
    for i in range(0, len(audio), step):
        tr.feed(audio[i:i + step])
        out += tr.drain()
    out += tr.drain(force=True)
    return out, tr

fails = []
def check(label, ok, detail=""):
    print("  %-58s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); detail and print("     " + str(detail))

calls = []
def fake(audio):
    calls.append(len(audio) / SR)
    return "sentence %d" % len(calls)

print("=== 1. three sentences with pauses (the real test script) ===")
audio = np.concatenate([noise(1.0), speech(2.0), noise(1.0), speech(1.5), noise(1.0),
                        speech(2.5), noise(1.5)])
out, tr = run(audio, fake)
check("exactly three utterances", len(out) == 3, out)
check("each decoded once (no duplicates)", len(calls) == 3, calls)
check("timestamps monotonic", all(out[i][0] < out[i + 1][0] for i in range(len(out) - 1)), out)
check("first starts near 1.0s, includes pre-roll", 0.6 <= out[0][0] <= 1.0, out[0])
check("first ends ~0.6s after speech stops (3.0s)", 3.4 <= out[0][1] <= 3.9, out[0])

print("\n=== 2. silence and room noise produce nothing ===")
calls.clear()
out, _ = run(noise(10.0, amp=0.004), fake)
check("no utterances from 10s of noise", out == [] and calls == [], (out, calls))

print("\n=== 3. a click (too short to be speech) is discarded ===")
calls.clear()
out, tr = run(np.concatenate([noise(1.0), speech(0.1, amp=0.5), noise(1.5)]), fake)
check("click never decoded", calls == [], calls)

print("\n=== 4. punctuation-only decodes ('?') are dropped ===")
out, tr = run(np.concatenate([noise(1.0), speech(1.0), noise(1.0)]), lambda a: "?")
check("'?' never emitted", out == [], out)
check("counted as dropped", tr.dropped == 1, tr.dropped)

print("\n=== 5. a 40s monologue is force-split, nothing lost ===")
calls.clear()
out, _ = run(np.concatenate([noise(0.5), speech(40.0), noise(1.0)]), fake)
check("split into >= 3 chunks of <= 15s", len(out) >= 3 and max(c for c in calls) <= 15.05, calls)
check("chunks are contiguous", all(abs(out[i][1] - out[i + 1][0]) < 0.05 for i in range(len(out) - 1)), out)

print("\n=== 6. stop mid-sentence still flushes it ===")
calls.clear()
tr = sb.UtteranceTranscriber(None, decode=fake)
tr.feed(np.concatenate([noise(1.0), speech(2.0)]))
check("held while still talking", tr.drain() == [])
check("force flush emits it", len(tr.drain(force=True)) == 1)

# Partial words while someone is still talking, for the app's listening bar.
calls = []
tr = sb.UtteranceTranscriber(None, decode=lambda a: calls.append(len(a)) or "so far %d" % len(calls))
tr.feed(noise(1.0)); tr.feed(speech(1.2))
p1 = tr.partial(every=10)
check("partial: words of the utterance still being spoken", p1 == "so far 1", p1)
check("partial: not re-decoded more often than asked", tr.partial(every=10) == "so far 1" and len(calls) == 1)
tr.feed(speech(0.4)); tr.feed(noise(1.0))
check("partial: cleared once the utterance closes", tr.partial() == "" and len(tr.drain()) == 1)

print("\n=== voice mode: the words at a short pause, decoded early ===")
calls = []
tr = sb.UtteranceTranscriber(None, decode=fake)
tr.end_silence, tr.early_after = 0.45, 0.2
tr.feed(speech(1.0)); tr.feed(noise(0.15))
check("  no early decode before 0.2 s of quiet", calls == [] and tr.take_early() is None, calls)
tr.feed(noise(0.1))
e = tr.take_early()
check("  at 0.2 s of quiet: decoded once, with its audio, before the utterance closes",
      len(calls) == 1 and e and e[2] == "sentence 1" and len(e[3]) > SR and tr.utt is not None, (calls, e and e[:3]))
check("  taken once", tr.take_early() is None)
tr.feed(noise(0.3))
out = tr.drain()
check("  closed with no speech since: those words, no second decode", len(out) == 1 and out[0][2] == "sentence 1"
      and len(calls) == 1, (out, calls))
calls = []
tr = sb.UtteranceTranscriber(None, decode=fake)
tr.end_silence, tr.early_after = 0.45, 0.2
tr.feed(speech(1.0)); tr.feed(noise(0.25))
check("  they pause, then go on:", len(calls) == 1 and tr.early is not None)
tr.feed(speech(0.6))
check("    what came early is dropped", tr.take_early() is None)
tr.feed(noise(0.6))
check("    their next pause: early words again, the rest of the block held for the next feed",
      tr.early is not None and tr.held and tr.utt is not None)
tr.feed(np.zeros(0, "float32"))                 # what pump does first: the held audio
out = tr.drain()
check("    their next pause decodes it whole, and the close keeps that (two decodes, not three)",
      len(out) == 1 and out[0][2] == "sentence 2" and len(calls) == 2 and calls[1] > calls[0], (out, calls))
calls = []
tr = sb.UtteranceTranscriber(None, decode=fake)
tr.feed(speech(1.0)); tr.feed(noise(1.0))
check("  off outside voice mode: one decode, at the close", len(calls) == 1 and tr.take_early() is None, calls)

# The audio comes in 0.5 s blocks, and the close is only 0.25 s after the early point: one block
# nearly always held both, and the utterance closed over its early words before the app saw them.
calls = []
tr = sb.UtteranceTranscriber(None, decode=fake)
tr.end_silence, tr.early_after = 0.45, 0.2
tr.feed(speech(1.0)); tr.feed(noise(0.5))       # one block: the early point and the close both in it
e = tr.take_early()
check("  early point and close in one block: the early words are there, the utterance still open",
      e and e[2] == "sentence 1" and tr.utt is not None and tr.held and tr.early_lost == 0, (e and e[:3], tr.early_lost))
tr.feed(np.zeros(0, "float32"))
out = tr.drain()
check("    then the held audio closes it: the same words, one decode, nothing late",
      len(out) == 1 and out[0][2] == "sentence 1" and len(calls) == 1 and not tr.held, (out, calls))
tr = sb.UtteranceTranscriber(None, decode=fake)
tr.end_silence, tr.early_after = 0.45, 0.2
tr.feed(speech(1.0)); tr.feed(noise(0.5)); tr.feed(np.zeros(0, "float32"))
check("  early words never taken before the close: counted as lost", tr.early_lost == 1 and tr.take_early() is None,
      tr.early_lost)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
