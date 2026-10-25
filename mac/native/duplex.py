"""A call's audio engine: the mic through the OS's echo cancellation (so the user can talk over
the voice on open speakers) and the voice played on the same engine, or, where echo
cancellation can't take the mic, on headphones, the voice alone. VoiceIO (standby.py) drives it.
"""
from native import impl

try:
    _os = impl("duplex")
except ImportError:
    _os = None                       # an OS without one: no engine, the call says its voice can't play


def available():
    """An engine can run here."""
    return bool(_os and _os.available())


def engine(voice=None, neural=None, log=print):
    """A new engine, for one voice mode: voice, a system voice the user chose (or None); neural,
    one of Standby's own (tts.VOICES), which it opens with the mic. What VoiceIO asks of it:
    start_mic(device, feed) (aec says whether echo cancellation took), start_output(),
    stop_mic(); begin(), say(text), halt(), duck(), unduck(); playing, loading, failed,
    tts_status(); idle(seconds) for the recorder's main loop."""
    return _os.engine(voice=voice, neural=neural, log=log)
