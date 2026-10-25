"""The sound other apps play on this computer: which apps are playing, and one app's sound
(or everything but Standby's own) as a stream, for a meeting's "Others" channel and for
taking the computer's own sound back out of the mic.
"""
from native import impl

EVERYTHING = "*"          # capture(...).start(EVERYTHING): all the computer plays but Standby's own


def _os():
    return impl("appaudio")


def apps(include_silent=True):
    """Every app the OS knows that may play: [{"bundle", "pid", "playing", "hears" (has the
    mic open), "call" (a known call app), "name", "own" (one of Standby's), "confidence"}]."""
    return _os().audio_apps(include_silent)


def capture(sample_rate=16000, include_mine=False, log=None):
    """A capture: apps(), start(target, feed) with an app's id or EVERYTHING, stop(), and its
    state (ok, error, cause, name); feed(block, t) gets mono float32 at sample_rate, t the
    monotonic time of the block's first sample."""
    return _os().MeetingAudio(sample_rate=sample_rate, include_mine=include_mine, log=log)


def preflight():
    """May Standby record what the computer plays, now, without asking? True, False, or
    None when it can't tell."""
    return _os().preflight()


def describe(cause, app="", why=""):
    """The words for why a capture stopped or paused: {"reason", "detail", "past"}, or None."""
    return _os().describe(cause, app, why)
