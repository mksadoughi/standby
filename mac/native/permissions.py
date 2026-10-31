"""What the OS lets Standby do: the microphone, and recording what the computer plays
(on a Mac, System Audio Recording). Asking never shows anything unless the
function says it does.
"""
from native import impl

_os = impl("permissions")


def mic_status():
    """This process and the microphone: "authorized", "denied", "restricted",
    "undetermined", or None when the OS can't be asked."""
    return _os.mic_status()


def ask_mic(answered):
    """The OS's own microphone prompt: answered(granted) once the user chooses (at once when
    it asked before)."""
    return _os.ask_mic(answered)


def sound_available():
    """(True, "") if this computer can record what it plays, else (False, what to do),
    under 60 characters for a tooltip."""
    return _os.sound_available()


def sound_state():
    """May Standby record what the computer plays: "allowed", "denied", "asked" (the OS
    asked, its answer unknown) or "not_asked". Asks nothing: no prompt."""
    return _os.sound_state()


def ask_sound(done=None):
    """The OS's own prompt for it, brought up by a short capture; done(heard) after."""
    return _os.ask_sound(done)


def open_sound_settings():
    """The OS's settings, at the switch for it."""
    return _os.open_sound_settings()


def no_devices(what=None):
    """Raises in a test (STANDBY_NO_DEVICES) before anything real is reached: the
    prompt, the settings, a real capture."""
    return _os.no_devices(what)
