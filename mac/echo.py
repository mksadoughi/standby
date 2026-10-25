"""The laptop's own sound, heard again by its mic.

On open speakers the mic hears the call: the other people's words come back
through it, into the user's dictation, their voice turns, and the transcript's
mic side ("doubling"). The call's own audio is recorded too, as Others,
with true timestamps, so a mic line that says the same words at the same time
as an Others line is the room hearing the laptop, not the user.

This decides it from the two transcripts, not from the audio: no signal
processing, nothing for the user to hear. It errs towards keeping the user's
words. A line is dropped only when it overlaps the call's speech in time AND
most of its words are the call's, in order, and it is long enough to tell
("yes" and "okay" are anyone's). Everything it drops, and every near miss, is
said in the log with both texts, so the thresholds can be tuned from real use.
"""

import difflib
import re

MATCH = 0.7          # share of the mic line's words found, in order, in the call's words then
OVERLAP = 0.5        # share of the mic line's time the call was speaking for
MIN_WORDS = 3        # shorter lines are kept: too little to tell who said them
NEAR = 0.3           # a near miss: kept, but logged, to tune MATCH from (logging only)
SLACK = 0.6          # seconds either side: the two transcribers cut utterances differently


def words(text):
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def judge(mic, others):
    """Is this mic line the call coming back? mic: (start, end, text); others:
    [(start, end, text)], the call's lines near it (an utterance still being
    spoken may be among them, with what it has said so far).

    Returns (drop, match, overlap): drop True only on a strong overlap and a
    close match, for a line long enough to tell."""
    start, end, text = mic
    mine = words(text)
    near = sorted((o for o in others if o[1] >= start - SLACK and o[0] <= end + SLACK), key=lambda o: o[0])
    if not mine or not near:
        return False, 0.0, 0.0
    theirs = [w for o in near for w in words(o[2])]
    found = sum(b.size for b in difflib.SequenceMatcher(None, mine, theirs, autojunk=False).get_matching_blocks())
    match = found / float(len(mine))
    span = max(end - start, 0.1)
    covered = 0.0
    edge = start
    for o_start, o_end, _t in near:                       # the union of their spans, inside this line's
        lo, hi = max(o_start - SLACK, edge), min(o_end + SLACK, end)
        if hi > lo:
            covered += hi - lo
            edge = hi
    overlap = min(1.0, covered / span)
    drop = len(mine) >= MIN_WORDS and match >= MATCH and overlap >= OVERLAP
    return drop, round(match, 2), round(overlap, 2)
