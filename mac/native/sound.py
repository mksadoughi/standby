"""A short sound file played on the system output, the way the OS plays its own alerts: it never waits
for the sound, and never raises. Where there is no implementation, or with STANDBY_NO_DEVICES (the tests),
it plays nothing.

    from native import sound
    sound.play("/path/to/ending.wav")      # True if it started
"""
import os

from native import impl

try:
    _os = impl("sound")
except ImportError:
    _os = None                       # an OS without one: nothing is raised, nothing played


def play(path):
    """Starts `path` playing and returns at once. True if it started."""
    if os.environ.get("STANDBY_NO_DEVICES"):
        return False
    try:
        return bool(_os and _os.play(path))
    except Exception:
        return False
