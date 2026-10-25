"""Speech to text, on this computer: the runtime that loads a speech model and decodes with
it. Which model to use, and where it comes from, is speechmodel.py's.

A model is whatever open() returns; it is only ever handed back to the functions here.
"""
from native import impl


def _os():
    return impl("speech")


def prepare():
    """Load the runtime itself (its first import is the slow part of a first load)."""
    return _os().prepare()


def hold_cache(limit, log=lambda m: None):
    """Keep the runtime's freed memory to `limit` bytes; logs, never raises, where it can't."""
    return _os().hold_cache(limit, log)


def open(name):
    """The model `name` (a folder, or a repo id the runtime downloads). Raises if it can't."""
    return _os().open(name)


def warm(model, sample_rate):
    """One decode of silence, so the first real one is not slowed by compiling. Raises if it fails."""
    return _os().warm(model, sample_rate)


def stream_decode(model, audio):
    """An utterance's text and its words with their times: (text, [(start, end, piece)] or None)."""
    return _os().stream_decode(model, audio)


def decode(model, audio, chunk_seconds=120.0, overlap_seconds=15.0):
    """A whole recording in one pass, the model seeing every word in context; long ones in
    overlapping chunks, merged."""
    return _os().decode(model, audio, chunk_seconds, overlap_seconds)


def release():
    """Give back the memory a model that was let go held."""
    return _os().release()


def whisper(audio, model, prompt=None):
    """The archival re-pass's Whisper on a recording: {"segments": [{"start", "end", "text"}]}.
    prompt: the user's own names, for every window."""
    return _os().whisper(audio, model, prompt)
