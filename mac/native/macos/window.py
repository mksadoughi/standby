"""native/window.py on a Mac: AppKit's screens and app, PyObjCTools' run loop, and the
orb (companion.py) and title bar (titlebar.py), which live here whole.

AppKit loads only when used, so this imports anywhere.
"""


def visible_frames():
    import AppKit
    return [tuple(map(float, (v.origin.x, v.origin.y, v.size.width, v.size.height)))
            for v in (sc.visibleFrame() for sc in AppKit.NSScreen.screens())]


def working_screen():
    import AppKit
    at = AppKit.NSEvent.mouseLocation()
    for sc in AppKit.NSScreen.screens():
        if AppKit.NSPointInRect(at, sc.frame()):
            v = sc.visibleFrame()
            return v.origin.x, v.origin.y, v.size.width, v.size.height
    v = AppKit.NSScreen.mainScreen().visibleFrame()
    return v.origin.x, v.origin.y, v.size.width, v.size.height


def on_main(fn, *args):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn, *args)


def frame_of(native):
    f = native.frame() if hasattr(native, "setFrame_display_") else None
    return (f.origin.x, f.origin.y, f.size.width, f.size.height) if f is not None else None


def place(native, frame):
    import AppKit
    native.setFrame_display_(AppKit.NSMakeRect(*map(float, frame)), True)


def to_front(native, log):
    def front():
        try:
            import AppKit
            app = AppKit.NSApplication.sharedApplication()
            if app.respondsToSelector_("activate"):
                app.activate()
            app.activateIgnoringOtherApps_(True)
            if native is not None:
                if native.isMiniaturized():
                    native.deminiaturize_(None)
                native.makeKeyAndOrderFront_(None)
                native.orderFrontRegardless()
        except Exception as exc:
            log("Standby's window: couldn't bring it to the front (%r)" % exc)
    try:
        on_main(front)
    except Exception:
        pass


def redraw(win, log):
    def go():
        try:
            from webview.platforms import cocoa
            bv = cocoa.BrowserView.instances.get(win.uid)
            wv = bv.webview if bv is not None else None
            if wv is not None and wv.superview() is not None:
                wv.setFrame_(wv.superview().bounds())
                wv.setNeedsLayout_(True)
                wv.setNeedsDisplay_(True)
        except Exception as exc:
            log("Standby's window: couldn't redraw it (%r)" % exc)
    try:
        on_main(go)
    except Exception:
        pass


def quit_app():
    import AppKit
    on_main(AppKit.NSApp.terminate_, None)


def titlebar(native, on_fullscreen=None):
    from native.macos import titlebar as tb
    return tb.Titlebar(native, on_fullscreen=on_fullscreen)


def companion(win, base, hooks):
    from native.macos import companion as cp
    return cp.attach(win, base, hooks)
