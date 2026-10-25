"""What Standby needs from the operating system, one small interface per area.

Each native/<area>.py says what the rest of Standby may ask of the OS there, and asks it
of this OS's implementation: native/macos/<area>.py on a Mac, the only one so far. Only
those implementations import the OS's own frameworks (AppKit, ScreenCaptureKit, Core
Audio, ...); tests/test_native_imports.py keeps it so.

An area is here only where another OS would really do it differently. A piece that is
the Mac's own UI through and through (the orb, the dictation panel) is not wrapped: it
lives under native/macos/ whole, and another OS would write its own.
"""
import importlib
import sys


def impl(area):
    """This OS's implementation of `area` (a module with the functions native/<area>.py
    lists). Raises ImportError where Standby has none yet."""
    if sys.platform == "darwin":
        return importlib.import_module("native.macos." + area)
    raise ImportError("Standby's %s has no implementation for %s yet" % (area, sys.platform))
