"""The user's own voice: so a call can tell the user from someone else talking
near the mic.

    mine = MyVoice(ROOT / "voices.json")
    mine.learn(emb, seconds)      a call's turn: surely the user
    mine.match(emb) -> 0..1, or None until it has heard enough (TRUST)
    who(match, seconds) -> "you" | "other" | "unsure" | "short"

A print is the running mean of TitaNet embeddings (SpeakerID's), weighted by
seconds of speech. It's learned only from calls: just the user and Claude. Each mic
is trusted on its own (inputs), since a headset's close mic sounds different.

It's kept in voices.json under its own key, "me", beside the voices the user named
("voices"): load_voices() never names a transcript line with it, and the app can
list it, and forget it, on its own. Only while remember_voices is on. The app
writes the same file, so both take voices.json.lock around a change.

The thresholds were measured on the user's own past meetings (read-only, scores only): their voice-mode turns against a print
from their other sessions scored median 0.57 but a tenth below 0.27, while a call's
far side never went above 0.34. So "other" is kept for a voice clearly not theirs.
"""
import json
import os
import time

YOU = 0.45          # this close to the user's print: the user
OTHER = 0.25        # below this: someone else (measured)
SHORT = 0.5         # seconds: too short for a print (SpeakerID's own floor)
# seconds of speech, not of the clip, before a print may say an utterance is someone else's.
# A clip is the words plus the quiet around them: "Thanks so much." was 1.9 s of clip and
# 0.69 s of speech, its print mostly the room's, and it was ruled someone else's (10/25);
# so were 12 more of the user's short words on 10/23-24.
TELL = 1.0
TRUST = 30.0        # seconds of the user's speech before the print is used
LEARN_MIN = 1.0     # seconds: a shorter utterance's print is too noisy to learn from
# Calls ignore someone else's voice only on an input where the user's own voice is known to
# score as theirs: this many seconds of their speech there matched YOU or better. A mic that
# makes them sound unlike their print (a headset, where the print was learnt on the laptop's)
# never gets there, and nobody is dropped on it.
INPUT_TRUST = 8.0


def who(match, seconds):
    """What an utterance is, from its match (None: no print to match, or none made)."""
    if seconds < SHORT:
        return "short"
    if match is None:
        return None
    if match >= YOU:
        return "you"
    if match < OTHER:
        return "other"
    return "unsure"


def _unit(v):
    import numpy as np
    v = np.asarray(v, dtype="float64")
    return v / (float(np.linalg.norm(v)) + 1e-9)


class MyVoice(object):

    def __init__(self, path, log=lambda m: None):
        self.path = path
        self.log = log
        self.emb, self.seconds = None, 0.0
        me = self._read().get("me") or {}
        if me.get("emb"):
            self.emb, self.seconds = _unit(me["emb"]), float(me.get("seconds") or 0.0)
        # per input (a mic's name): seconds of the user's speech there that matched them
        self.inputs = {str(k): float(v) for k, v in (me.get("inputs") or {}).items()
                       if isinstance(v, (int, float))}

    @property
    def trusted(self):
        return self.emb is not None and self.seconds >= TRUST

    def trusted_on(self, name):
        """Is the print to be relied on for this input: the user's voice there known to match?"""
        return self.trusted and self.inputs.get(str(name or "?"), 0.0) >= INPUT_TRUST

    def note_input(self, name, match, seconds):
        """An utterance on input `name` with its match: one that is the user's (YOU or better)
        counts towards trusting that input. Saved once it reaches INPUT_TRUST."""
        if not self.trusted or match is None or match < YOU or seconds < SHORT:
            return False
        name = str(name or "?")
        before = self.trusted_on(name)
        self.inputs[name] = round(self.inputs.get(name, 0.0) + seconds, 1)
        if self.trusted_on(name) and not before:
            self.log("my voice: the user's voice known on %s: calls ignore other voices there" % name)
            self._save()
        return True

    def match(self, emb):
        """Against the user's print."""
        if not self.trusted or emb is None:
            return None
        return round(max(0.0, float(_unit(emb) @ self.emb)), 3)

    def learn(self, emb, seconds):
        """One of the user's utterances, from a talk. Once the print is trusted, one
        that doesn't sound like it (someone else in the room after all) is left out."""
        if emb is None or seconds < LEARN_MIN:
            return False
        e = _unit(emb)
        if self.trusted and float(e @ self.emb) < OTHER:
            self.log("my voice: an utterance that doesn't sound like the user, not learnt")
            return False
        if self.emb is None or len(self.emb) != len(e):
            self.emb, self.seconds = e, 0.0
        else:
            self.emb = _unit(self.emb * self.seconds + e * seconds)
        before = self.trusted
        self.seconds += seconds
        self._save()
        if self.trusted and not before:
            self.log("my voice: learnt (%.0f s of speech): voice mode can now tell the user in a room" % self.seconds)
        return True

    # -- voices.json, shared with the app ---------------------------------------------
    def _read(self):
        try:
            with open(str(self.path)) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self):
        import fcntl
        with open(str(self.path) + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            lib = self._read()
            lib["me"] = {"emb": [round(float(x), 6) for x in self.emb], "seconds": round(self.seconds, 1),
                         "inputs": self.inputs, "updated": time.time()}
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(lib, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, str(self.path))
