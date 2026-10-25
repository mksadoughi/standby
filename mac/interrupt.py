"""What the user's words mean when they come while Claude thinks or speaks.

Voice mode listens the whole time. Words that arrive over Claude are one of:

    backchannel   "mm-hm", "yeah", "right", "okay": listening, not taking the turn
    noise         nothing, a cough, "um": nothing was said
    cancel        "never mind", "stop", "forget it": no answer
    wait          "hold on", "one sec": not now; say it when they are back
    resume        "go on", "I'm back": carry on (after a wait)
    short         one word, none of these: over Claude most likely a backchannel
                  misheard ("Sugar." for "Sure."), carry on; while Claude thinks,
                  more to the question
    real          anything else: an interruption, or more to the question

Only whole utterances count: "wait" is a wait, "wait, what about Q4?" is real.
Words of politeness around a command are allowed ("okay, never mind",
"sorry, go on"). Parakeet's words are what is matched, as it writes them:
capitals, punctuation and its spellings of "mm-hm" are all the same here.
"""

import re

BACKCHANNEL, NOISE, CANCEL, WAIT, RESUME, SHORT, REAL = ("backchannel", "noise", "cancel", "wait", "resume",
                                                         "short", "real")

_PHRASES = {
    BACKCHANNEL: """mhm | mm | hmm | uh huh | yeah | yes | yep | yup | yah | right | okay | ok | sure |
        got it | gotcha | i see | cool | nice | great | true | exactly | totally | interesting | wow |
        oh | ah | alright | all right | thanks | thank you | makes sense | that makes sense | fair |
        fair enough | good | perfect | indeed | of course | definitely | absolutely | uh | um |
        ha | ha ha | haha | hah | heh | hehe | hm | hum | m | mm mm | aha | aja | ajá | make sense |
        really | well | ooh | huh uh""",
    CANCEL: """never mind | nevermind | forget it | forget about it | forget that | cancel | cancel that |
        cancel it | stop | stop it | stop that | stop talking | scratch that | that's enough | enough |
        skip it | skip that | don't worry about it | no need | quiet | be quiet | shush | shh""",
    WAIT: """hold on | hold on a second | hold on a sec | hold on a minute | hold that thought | wait |
        wait a second | wait a sec | wait a minute | wait a moment | one sec | one second | one moment |
        one minute | just a sec | just a second | just a moment | just a minute | hang on | hang on a sec |
        hang on a second | give me a second | give me a sec | give me a moment | give me a minute |
        a sec | a second | sec | pause | pause that""",
    RESUME: """go on | go ahead | continue | carry on | keep going | you can continue | you can go on |
        please continue | i'm back | im back | back | ready | i'm ready | im ready | as you were saying |
        you were saying | where were we | okay go on | resume""",
}
# Around a command: "okay, never mind", "sorry, go on", "oh wait", "never mind then".
_POLITE = {"oh", "okay", "ok", "sorry", "actually", "um", "uh", "please", "just", "no", "yeah", "so",
           "then", "thanks", "alright", "all", "right", "hey", "claude", "fine", "well", "now"}
_FILLER = {"um", "uh", "er", "erm", "ah", "hm", "hmm", "mm", "the", "a", "i", "it"}
# One word that does mean something over Claude: these stop it.
_ONE_WORD_REAL = {"no", "nope", "nah", "what", "why", "how", "who", "when", "where", "which", "sorry", "pardon",
                  "huh", "excuse", "actually", "but", "and", "also", "or", "plus", "so", "then", "except"}
# Parakeet's spellings of mm-hm and uh-huh, and "OK": one form each.
_SPELLINGS = [(re.compile(r"\bm+-?h+m+\b|\bm+ hm+\b|\bmh+m+\b|\bhm+ m+\b"), "mhm"),
              (re.compile(r"\buh-?h+uh\b|\buh huh\b|\bunh-?unh\b"), "uh huh"),
              (re.compile(r"\bo\.?k\.?(?=\s|$)"), "ok")]


def _parse(block):
    return sorted({tuple(p.split()) for p in (x.strip() for x in block.split("|")) if p}, key=len, reverse=True)


_TABLE = {kind: _parse(block) for kind, block in _PHRASES.items()}


def words(text):
    """The utterance as plain lowercase words, the way the tables are written."""
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^\w'\s-]", " ", t)
    for pattern, said in _SPELLINGS:
        t = pattern.sub(said, t)
    t = t.replace("-", " ")
    return [w.strip("'") for w in t.split() if w.strip("'")]


def _covers(ws, phrases, extra=frozenset()):
    """Do these phrases (and the extra single words) make up all of ws, with at
    least one phrase? Greedy, longest phrase first: enough for these tables."""
    i, found = 0, False
    while i < len(ws):
        for p in phrases:
            if tuple(ws[i:i + len(p)]) == p:
                i, found = i + len(p), True
                break
        else:
            if ws[i] in extra:
                i += 1
                continue
            return False
    return found


def classify(text):
    """One of BACKCHANNEL, NOISE, CANCEL, WAIT, RESUME, REAL for an utterance."""
    ws = words(text)
    if not ws or all(w in _FILLER for w in ws):
        return NOISE
    for kind in (CANCEL, WAIT, RESUME):
        if _covers(ws, _TABLE[kind], _POLITE):
            return kind
    if _covers(ws, _TABLE[BACKCHANNEL]):
        return BACKCHANNEL
    if len(ws) == 1 and ws[0] not in _ONE_WORD_REAL:
        return SHORT
    return REAL


def is_words(text):
    """Words for Claude, not a backchannel, a noise or a command: REAL or SHORT."""
    return classify(text) in (REAL, SHORT)


def heard_of(sentence, seconds, words_per_second=2.6):
    """How much of a sentence was said in `seconds` of it: its first words, and
    "..." where it was cut. All of it once the time is up."""
    ws = sentence.split()
    n = int(seconds * words_per_second)
    if n >= len(ws):
        return sentence
    return (" ".join(ws[:n]) + "...") if n > 0 else ""
