"""Talking over Claude: what a duck becomes (overlap.decide). No audio, no recorder:
the words, the time and the speech so far, as the recorder's loop passes them."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import sys
sys.path.insert(0, "mac")
import overlap  # noqa: E402

C, R = overlap.COMMIT, overlap.RESUME
fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:300])


def act(text, since=0.5, voiced=0.4, speaking=False):
    return overlap.decide(text, since, voiced, speaking)[0]


for said in ("Mm-hmm.", "Yeah.", "Right.", "Okay.", "Uh-huh.", "Got it.", "Um", "Uh.", "Ha ha."):
    check("%r said and over: it goes on" % said, act(said) == R, overlap.decide(said, 0.5, 0.4, False))
check("one stray word (a misheard \"sure\"): it goes on", act("Sugar.") == R)
check("a sound with no words, once quiet a moment: it goes on", act("", since=overlap.NO_WORDS) == R)
check("  but not at once: the words may be on their way", act("", since=0.3) is None)

for said in ("Stop.", "Never mind.", "Hold on.", "Wait.", "Okay, never mind."):
    check("%r: it stops at once, even mid-word" % said, act(said, speaking=True) == C)
check("a question: it stops", act("What about the EU region?", speaking=True) == C)
check("three words of a turn, still talking: it stops", act("wait what about", speaking=True) == C)
check("one word that means something (\"No.\"): it stops", act("No.", speaking=True) == C)
check("two words, still talking: it waits for more", act("Yeah, I", speaking=True) is None)
check("  two words, and they stopped: they said something, it stops", act("Not that.") == C)
check("a backchannel and a filler, and they stopped (\"Yeah, I\"): it goes on", act("Yeah, I") == R)
check("one word and a filler (\"Sit, um\"): a stray word, it goes on", act("Sit, um") == R)
check("\"yeah\" while still talking: waits", act("Yeah", speaking=True) is None)
check("talking on, whatever the words: it stops", act("uh so", voiced=overlap.SUSTAINED, speaking=True) == C)
check("ducked too long, still talking: it stops", act("", since=overlap.MAX_DUCK, voiced=0.5, speaking=True) == C)
check("ducked too long, quiet: it goes on", act("", since=overlap.MAX_DUCK, voiced=0.3) == R)
check("words of a backchannel run together: it goes on", act("Yeah, yeah. Okay.") == R)

# Words with next to no speech since the duck: the voice's own words heard back, its echo stopped by
# the duck (talk-20261026-232015: cut twice, at 0.06 and 0.15 s of speech).
check("echo heard as words, 0.06 s of speech since the duck: it goes on",
      act("Somewhere you're reading is a", since=0.93, voiced=0.06) == R, overlap.decide("Somewhere you're reading is a", 0.93, 0.06, False))
check("  0.15 s, still open: it waits, not stops", act("Did check one of the", since=0.49, voiced=0.15, speaking=True) is None)
check("  a question heard back the same way: it goes on", act("Can you hear me?", since=0.6, voiced=0.1) == R)
check("  the same words with a person's speech under them (0.69 s): it stops",
      act("Somewhere you're reading is a", since=1.0, voiced=0.69, speaking=True) == C)
check("  \"stop\" or \"wait\" with no speech since: it stops, as ever", act("Stop.", voiced=0.0) == C and act("Wait.", voiced=0.0) == C)
check("  one word that means something (\"No.\"), said before the duck: it stops, as ever", act("No.", voiced=0.0) == C)

if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
