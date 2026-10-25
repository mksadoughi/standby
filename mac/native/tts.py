"""Standby's own voice, as the OS runs it: the speech engine (in the voice process, tts.py's
serve), and the speakers where the recorder does not play it (Settings' Try).
"""
from native import impl

_os = impl("tts")


def engine(voice):
    """The speech engine for one of Standby's voices (tts.VOICES): load(tell) gets it ready,
    telling its state; stream(text) gives the sentence's sound, float32 mono at its rate, in
    pieces as they are made."""
    return _os.Engine(voice)


def speakers(rate, log=print):
    """Sound to the speakers, output only (never the mic): open(); play(samples, done), each
    piece after the ones before it, done() once played or dropped; halt(); close()."""
    return _os.Speakers(rate=rate, log=log)
