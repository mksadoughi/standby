"""Who's speaking, from a diarization model run on this computer: a stream that says,
every 10 ms, which of a few voices is talking. Optional: where it can't run, load()
gives None and speakers are told apart by voice prints alone.
"""
from native import impl


def load(log=lambda m: None):
    """The model, or None where it can't run here (not installed, too old an OS)."""
    try:
        mod = impl("diarizer")
    except ImportError:
        return None
    return mod.load(log=log)


def stream(model, log=lambda m: None):
    """A stream on `model` for one channel: feed it audio, read who spoke when."""
    return impl("diarizer").Stream(model, log=log)
