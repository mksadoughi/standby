"""This computer's audio devices, straight from the OS: which mics are connected, which is
the default (and the default output), whether another app has one open, whether the
speakers reach the mic, and a word when any of it changes. The mic's sound itself comes
through PortAudio (sounddevice), which is the same everywhere.
"""
from native import impl


def system():
    """The OS's device set: inputs() -> [devices.Device], default_input(), default_output(),
    output_leaks(), in_use(device), watch(on_change). Raises where it can't be reached."""
    return impl("mics").CoreAudio()
