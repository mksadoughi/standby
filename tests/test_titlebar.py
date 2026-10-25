"""The hidden title bar, without a window: a double-click does what System
Settings says (zoom, minimise or nothing; the older setting too), a drag is
handed to macOS only while the button is held and only for the press that
started it, and the app's window_drag / window_zoom reach the title bar on the
main thread (and do nothing before it exists), as the header's corner button
and the page's Quit reach the companion."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
from native.macos import titlebar as tb  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-62s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


check("double-click, never set: zoom", tb.double_click({}) == "zoom")
check("  Maximize, or Fill (macOS 15): zoom", tb.double_click({"AppleActionOnDoubleClick": "Maximize"}) == "zoom"
      and tb.double_click({"AppleActionOnDoubleClick": "Fill"}) == "zoom")
check("  Minimize: minimise", tb.double_click({"AppleActionOnDoubleClick": "Minimize"}) == "minimise")
check("  None (Do Nothing): nothing", tb.double_click({"AppleActionOnDoubleClick": "None"}) == "none")
check("  only the older setting, minimise on double-click", tb.double_click({"AppleMiniaturizeOnDoubleClick": True}) == "minimise")

check("drag: the button held, the press in this window, just now", tb.may_drag(True, (100.0, 7), 100.2, 7))
check("  the button already up: no drag", not tb.may_drag(False, (100.0, 7), 100.2, 7))
check("  a press in another window: no drag", not tb.may_drag(True, (100.0, 3), 100.2, 7))
check("  a call long after the press: no drag", not tb.may_drag(True, (100.0, 7), 103.0, 7))
check("  no press seen yet: no drag", not tb.may_drag(True, None, 100.2, 7))

# The app's side: pywebview calls these on its own thread; they must reach the main thread.
root = __import__("tempfile").mkdtemp(prefix="sbtb-")
open(os.path.join(root, "config.json"), "w").write(json.dumps({"agent": {"mode": "local"}}))
os.environ["STANDBY_ROOT"] = root
import ui  # noqa: E402
queued = []
import PyObjCTools.AppHelper as AH  # noqa: E402
AH.callAfter = lambda f, *a: queued.append(f)


class FakeTitlebar(object):
    def drag(self):
        pass

    def zoom(self):
        pass


api = ui.Api()
api.window_drag()
api.window_zoom()
check("before the window has its title bar: nothing happens", queued == [])
api.titlebar = FakeTitlebar()
api.window_drag()
api.window_zoom()
check("window_drag and window_zoom: queued for the main thread",
      [f.__name__ for f in queued] == ["drag", "zoom"], queued)
# the header's corner button and the page's Quit, the same way, to the companion.
queued.clear()
check("to_corner before the companion is up: an error the page can show",
      api.to_corner() == {"error": "no companion"} and queued == [])


class FakeCompanion(object):
    def to_corner(self):
        pass

    def quit_now(self):
        pass


api.companion = FakeCompanion()
check("  to_corner and quit, once it is: queued for the main thread",
      api.to_corner() == {"ok": True} and api.quit() == {"ok": True}
      and [f.__name__ for f in queued] == ["to_corner", "quit_now"], queued)
queued.clear()
# On a real window that is never shown, in a process that is never active and
# has no Dock icon: the style is applied, and clicks in the top 52 pt still
# reach the page (only the traffic lights take their own).
try:
    import AppKit, Foundation, WebKit
except ImportError:
    AppKit = None
if AppKit is not None:
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    rect = Foundation.NSMakeRect(0, 0, 1100, 760)
    mask = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
    w = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(rect, mask, AppKit.NSBackingStoreBuffered, True)
    w.setContentView_(WebKit.WKWebView.alloc().initWithFrame_configuration_(rect, WebKit.WKWebViewConfiguration.alloc().init()))
    seen = []
    t = tb.Titlebar(w, on_fullscreen=seen.append)
    w.layoutIfNeeded()
    frame = w.contentView().superview()
    H = frame.frame().size.height
    hit = lambda x, y: frame.hitTest_(Foundation.NSMakePoint(x, H - y)).className()
    check("window: title hidden, bar transparent, content under it, unified strip",
          w.titleVisibility() == AppKit.NSWindowTitleHidden and w.titlebarAppearsTransparent()
          and w.styleMask() & AppKit.NSWindowStyleMaskFullSizeContentView
          and w.toolbarStyle() == AppKit.NSWindowToolbarStyleUnified)
    close = w.standardWindowButton_(AppKit.NSWindowCloseButton)
    r = close.convertRect_toView_(close.bounds(), None)
    centre = (r.origin.x + r.size.width / 2, H - r.origin.y - r.size.height / 2)
    check("  the traffic lights centred in the 52 pt strip", abs(centre[1] - 26) <= 2, centre)
    zoom = w.standardWindowButton_(AppKit.NSWindowZoomButton)
    zr = zoom.convertRect_toView_(zoom.bounds(), None)
    check("  and inside the page's 80 pt corner", zr.origin.x + zr.size.width <= 80, zr.origin.x + zr.size.width)
    band = ((120, 10), (300, 26), (1000, 26), (600, 45))
    if AppKit.NSProcessInfo.processInfo().operatingSystemVersion()[0] >= 26:
        check("  a click on the header's buttons reaches the page (top band, middle and right)",
              all(hit(x, y) == "WKWebView" for x, y in band), [hit(x, y) for x, y in band])
    else:
        # Before macOS 26, hitTest on this inactive, never-shown window names the
        # toolbar's own view, yet in the app a real click there reaches the page
        # (checked by hand on 14.7.1). What holds everywhere: the toolbar is empty
        # and the page runs under the whole strip.
        page = w.contentView().convertRect_toView_(w.contentView().bounds(), None)
        check("  the toolbar is empty and the page runs under the whole strip",
              len(w.toolbar().items()) == 0 and page.origin.y + page.size.height >= H
              and page.origin.x <= 0 and page.size.width >= frame.frame().size.width,
              (len(w.toolbar().items()), page))
    check("  a click on a traffic light is the light's", "Close" in hit(*centre), hit(*centre))
    check("  not in full screen", t.fullscreen() is False)
    center = Foundation.NSNotificationCenter.defaultCenter()
    center.postNotificationName_object_(AppKit.NSWindowWillEnterFullScreenNotification, w)
    center.postNotificationName_object_(AppKit.NSWindowDidEnterFullScreenNotification, w)
    check("full screen: the strip's toolbar goes, so no opaque bar covers the page's top; the page is told",
          w.toolbar() is None and seen[-1] is True, (w.toolbar(), seen))
    center.postNotificationName_object_(AppKit.NSWindowDidExitFullScreenNotification, w)
    w.layoutIfNeeded()
    check("  out of full screen: the strip is back, title hidden and bar clear; the page is told",
          w.toolbar() is not None and w.toolbarStyle() == AppKit.NSWindowToolbarStyleUnified
          and w.titlebarAppearsTransparent() and seen[-1] is False, (w.toolbar(), seen))
    check("and nothing came to the front: the test was never the active app",
          not app.isActive() and AppKit.NSWorkspace.sharedWorkspace().frontmostApplication() == front)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
