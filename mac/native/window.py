"""Standby's own window and what sits beside it: the screens it may go on, bringing it to
the front, its frame, the UI thread, and quitting. The orb (companion) and the window's
title bar are the Mac's own UI through and through: they are not wrapped here, only
reached, by the entry points the app has always used.

Frames are (x, y, width, height) in the OS's global screen points; on a Mac y counts from
the bottom, as NSWindow.frame() has them.
"""
from native import impl

_os = impl("window")


def visible_frames():
    """Each screen's visible part (without the menu bar and the Dock), as frames."""
    return _os.visible_frames()


def working_screen():
    """The visible part of the screen the person is working on (the one with the pointer)."""
    return _os.working_screen()


def on_main(fn, *args):
    """fn(*args) on the UI thread, soon: window work must not run on a server thread."""
    return _os.on_main(fn, *args)


def frame_of(native):
    """The frame of the window's own object (pywebview's win.native), or None."""
    return _os.frame_of(native)


def place(native, frame):
    """The window at `frame`, at once."""
    return _os.place(native, frame)


def to_front(native, log):
    """Standby and its window in front, with the keyboard (opened from another app, it would
    otherwise come up behind it), on the UI thread. A failure is said with log, never raised."""
    return _os.to_front(native, log)


def redraw(win, log):
    """The window's web view laid out and drawn again at the window's size."""
    return _os.redraw(win, log)


def quit_app():
    """Standby quits, as Quit in its menu does (the app's own handlers run)."""
    return _os.quit_app()


def titlebar(native, on_fullscreen=None):
    """The window without a title bar (native.macos.titlebar.Titlebar on a Mac): its drag(),
    zoom() and fullscreen()."""
    return _os.titlebar(native, on_fullscreen)


def companion(win, base, hooks):
    """The orb, for a call, beside the window (native.macos.companion.attach on a Mac)."""
    return _os.companion(win, base, hooks)
