"""The main window without a title bar, as modern Mac apps have it.

The window keeps its title-bar style (resizing, full screen, Mission Control
and the traffic lights stay the system's), but the title is hidden, the bar is
transparent and the page fills it. An empty unified toolbar makes that strip
52 pt tall with the traffic lights centred in it; that survives resizing and
full screen, which moving the buttons by hand does not.

The web view gets every click, so the page says where the window may be
dragged: on a mouse-down in one of its drag regions it calls the app's
window_drag() (or window_zoom() on a double-click), and the mouse-down is
handed to macOS (performWindowDragWithEvent:): the system's own drag, edge
snapping and tiling included.

hide_title and the drag run on the main thread.
"""

import time

DRAG_WITHIN = 1.0      # seconds: the page's call comes a moment after the press


def double_click(prefs):
    """What a double-click on the title bar does, as set in System Settings >
    Desktop & Dock: "zoom", "minimise" or "none". prefs: a dict-like with
    AppleActionOnDoubleClick ("Maximize", "Fill", "Minimize", "None", or
    unset) and the older AppleMiniaturizeOnDoubleClick."""
    action = prefs.get("AppleActionOnDoubleClick")
    if action is None:
        return "minimise" if prefs.get("AppleMiniaturizeOnDoubleClick") else "zoom"
    action = str(action).lower()
    if action.startswith("mini"):
        return "minimise"
    if action in ("none", "do nothing", "nothing"):
        return "none"
    return "zoom"


def may_drag(button_down, press, now, window_number):
    """Hand the press to macOS only while the button is still held, and only
    the press that started it: a late call must not drag on a later click."""
    if not button_down or press is None:
        return False
    at, number = press
    return number == window_number and now - at <= DRAG_WITHIN


_WATCHER = []


def _watcher():
    """The observer class, made once: an Objective-C class can be defined only once."""
    if not _WATCHER:
        import AppKit

        class FullScreenWatcher(AppKit.NSObject):
            def entering_(self, note):
                self.entering()

            def entered_(self, note):
                self.changed(True)

            def exited_(self, note):
                self.changed(False)
        _WATCHER.append(FullScreenWatcher)
    return _WATCHER[0]


class Titlebar(object):
    """The hidden title bar of one window, and its drags. on_fullscreen(bool):
    the traffic lights go in full screen and come back after, for the page."""

    def __init__(self, window, on_fullscreen=None):
        import AppKit
        import Foundation
        self.AppKit, self.window = AppKit, window
        self.press = None              # (when, window number) of the last left mouse-down
        self.event = None

        def pressed(event):
            if event.window() is not None and event.window().windowNumber() == window.windowNumber():
                self.event, self.press = event, (time.time(), window.windowNumber())
            return event
        self._monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown, pressed)
        # Full screen: the toolbar that makes the 52 pt strip goes, and comes back after. In full
        # screen macOS moves the title bar into a window of its own that is painted opaque,
        # over the page's top (every header, the file viewer's bar too); without a toolbar it
        # stays hidden and slides down with the menu bar, as in any full-screen app.
        self._full = _watcher().alloc().init()
        self._full.entering = self.leave_strip
        self._full.changed = lambda full: (None if full else self.hide_title(), on_fullscreen and on_fullscreen(full))
        center = Foundation.NSNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(self._full, "entering:", AppKit.NSWindowWillEnterFullScreenNotification, window)
        center.addObserver_selector_name_object_(self._full, "entered:", AppKit.NSWindowDidEnterFullScreenNotification, window)
        center.addObserver_selector_name_object_(self._full, "exited:", AppKit.NSWindowDidExitFullScreenNotification, window)
        self.hide_title()

    def fullscreen(self):
        return bool(self.window.styleMask() & self.AppKit.NSWindowStyleMaskFullScreen)

    def hide_title(self):
        AppKit, w = self.AppKit, self.window
        w.setStyleMask_(w.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
        w.setTitlebarAppearsTransparent_(True)
        w.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_("standby.titlebar")
        w.setToolbar_(toolbar)
        w.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnified)       # a 52 pt strip, the lights centred
        w.setTitlebarSeparatorStyle_(AppKit.NSTitlebarSeparatorStyleNone)
        # pywebview paints the title bar's container in the window colour; clear it,
        # or a band of it covers the page's top.
        frame = w.contentView().superview()
        for view in (frame.subviews() if frame is not None else []):
            if "Titlebar" in view.className() and view.respondsToSelector_("setBackgroundColor:"):
                view.setBackgroundColor_(AppKit.NSColor.clearColor())

    def leave_strip(self):
        self.window.setToolbar_(None)

    def drag(self):
        AppKit = self.AppKit
        down = bool(AppKit.NSEvent.pressedMouseButtons() & 1)
        if may_drag(down, self.press, time.time(), self.window.windowNumber()):
            self.window.performWindowDragWithEvent_(self.event)

    def zoom(self):
        AppKit = self.AppKit
        defaults = AppKit.NSUserDefaults.standardUserDefaults()
        prefs = {k: defaults.objectForKey_(k) for k in ("AppleActionOnDoubleClick", "AppleMiniaturizeOnDoubleClick")}
        action = double_click({k: v for k, v in prefs.items() if v is not None})
        if action == "zoom":
            self.window.performZoom_(None)
        elif action == "minimise":
            self.window.performMiniaturize_(None)
