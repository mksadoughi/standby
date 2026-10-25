"""What the OS lets Standby do: the microphone, and recording what the computer plays
(on a Mac, Screen & System Audio Recording). Asking never shows anything unless the
function says it does.
"""
from native import impl

_os = impl("permissions")


def mic_status():
    """This process and the microphone: "authorized", "denied", "restricted",
    "undetermined", or None when the OS can't be asked."""
    return _os.mic_status()


def screen_available():
    """(True, "") if this computer can record what it plays, else (False, what to do),
    under 60 characters for a tooltip."""
    return _os.screen_available()


def screen_allowed():
    """May Standby record what the computer plays? Asks nothing: no prompt."""
    return _os.screen_allowed()


def ask_screen():
    """The OS's own prompt for it. On a Mac, access starts with the next launch."""
    return _os.ask_screen()


def open_screen_settings():
    """The OS's settings, at the switch for it."""
    return _os.open_screen_settings()


def no_devices(what=None):
    """Raises in a test (STANDBY_NO_DEVICES) before anything real is reached: the
    prompt, the settings, a real capture."""
    return _os.no_devices(what)
