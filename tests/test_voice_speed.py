"""Voice mode's speed, on the user's own recorded turns: end of
speech to first sound before and after asking on likely-final words and saying
the first clause first, through the real VoiceSession (tools/voice_speed.py:
a stand-in recorder, Claude and voice; nothing played). Seven of the quick
turns (Claude's first word under 1.5 s): the median is quicker by a clear
margin, and the voice calls grow only by the turns that went on after a likely
pause, never doubled. The full replay (all 21) is the tool's own run."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, statistics, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import voice_speed  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:500])


quick = [t for t in voice_speed.recorded() if t[1] <= 1.5][:voice_speed.GO_ON]
before, calls_before, _ = voice_speed.run(quick, after=False)
after, calls_after, dropped = voice_speed.run(quick, after=True)
b, a = statistics.median(before), statistics.median(after)
print("  quick turns, end of speech to first sound: before %.2fs, after %.2fs (%d turns)" % (b, a, len(quick)))
check("every turn answered, both ways", len(before) == len(after) == len(quick), (before, after))
check("asking on likely-final words, and the first clause first: quicker by 0.3 s or more",
      a <= b - 0.3, (b, a))
check("  the turn that went on after its likely pause: its early question dropped, the whole turn asked",
      dropped == 1 and calls_after == calls_before + 1, (dropped, calls_before, calls_after))
check("  the voice calls not doubled: one more for that turn, none for the rest",
      calls_before == len(quick) and calls_after <= len(quick) * 1.2 + 1, (calls_before, calls_after))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
