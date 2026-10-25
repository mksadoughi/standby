"""Talking over Claude: does the voice give way, or carry on?

The recorder used to stop Claude's voice on any 0.3 s of sound over it, and
only then look at the words: an "um" or a "yeah" stopped it, and it started
the sentence again a second later (40% of the user's talk-overs, 2026-10-20).
Now the voice ducks on the sound, and the words decide:

    a command ("stop", "never mind", "hold on")       give way at once
    a question, or three or more words of a turn        give way
    one word that means something ("no", "what")        give way
    speech that goes on for SUSTAINED                   give way, whatever it is
    a backchannel, a filler, a cough, nothing           carry on where it was

as products and papers do (Vapi's three words, Voice-Light's 0.9 s, LiveKit's
resume after a false interruption). Plain functions, for the recorder's loop
and for replaying logged talk-overs.
"""

import interrupt

COMMIT, RESUME = "commit", "resume"

SUSTAINED = 0.9     # seconds of speech since the duck: a turn, whatever the words (Voice-Light commits at 0.9 s)
NO_WORDS = 0.8      # ducked this long, nothing said and nothing being said: a noise
MAX_DUCK = 1.6      # never ducked longer: still talking is a turn, quiet is not
MIN_WORDS = 3       # words of a turn that give way while still talking (Vapi's recommended numWords)


def decide(text, since, voiced, speaking):
    """What a duck becomes, now: (COMMIT or RESUME, why), or (None, "") to wait.

    text: what was said since the duck, closed utterances and the open one's
    words so far; since: seconds since the duck; voiced: seconds of speech in
    them; speaking: an utterance is still open."""
    ws = interrupt.words(text)
    kind = interrupt.classify(text) if ws else None
    if kind in (interrupt.CANCEL, interrupt.WAIT):
        return COMMIT, kind
    if kind == interrupt.REAL:
        if text.rstrip().rstrip("\"')]}").endswith("?"):
            return COMMIT, "a question"
        core = [w for w in ws if w not in interrupt._FILLER]     # "Yeah, I", "Sit, um": one word, really
        if len(core) >= MIN_WORDS:
            return COMMIT, "words"
        kind = interrupt.classify(" ".join(core)) if core else interrupt.NOISE
        if kind == interrupt.REAL and (len(core) == 1 or not speaking):
            return COMMIT, "words"                    # one word that means something, or said and stopped
    if speaking and voiced >= SUSTAINED:
        return COMMIT, "kept talking"
    if since >= MAX_DUCK:
        return (COMMIT, "kept talking") if speaking else (RESUME, kind or "nothing said")
    if not speaking and (kind is not None or since >= NO_WORDS):
        return RESUME, kind or "nothing said"         # "mm-hm", "um", a cough: over
    return None, ""
