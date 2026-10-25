"""The companion: a small Standby that floats over every app.

pywebview's windows are plain NSWindows. Clicking one activates Standby, so the
app the user was typing in loses the keyboard, and a regular app's window does
not show over another app's full-screen Space. So the companion is a native
non-activating NSPanel (what Spotlight and Raycast use) with a WKWebView of
its own, made on the main thread inside the app's run loop (pywebview's).
All of it was measured.

The page (mac/companion.html) runs in two windows: /companion is the orb's,
/companion#words the words', its child. It talks to the app's server like the
main window does. What only the window can do goes through a script
message handler, window.webkit.messageHandlers.sb.postMessage({...}):
  {type: "orb", w, h, glyph, inset, glass, under, ms}  the orb's window:
                           its size and the glyph's side (the window is placed
                           by the glyph, whose outer edges stay put); glass:
                           {x, y, w, h, radius} behind part of it, x from the
                           window's inner side (the screen's centre side) and y
                           from its top, or null for none; under: where
                           that glass rolls out from and back to, a circle
                           hidden under the orb's centre, in the same terms
  {type: "words", w, h, radius, material, ms}  the words' window, the orb's
                           child window, lined up with the glyph (w 0: hidden;
                           radius null: no glass; material "soft": a
                           plain frosted ground, no rim and no shadow)
  {type: "menu", x, y}     the native menu, at that point of the page
  {type: "expand"}         open the main window
  {type: "end"}            end the call, as the menu's End
  {type: "settings", pane} Standby's Settings, on that pane ("call")
and back, on the page's globals: edge(h, v), hover(on), and from the menu
say(text).

Where it sits, when it shows and its menu are companion_rules.py.
Everything here runs on the main thread (AppHelper.callAfter).
"""

import json
import os
import threading
import time

import AppKit
import Foundation
import objc
import WebKit
from PyObjCTools import AppHelper

import companion_rules as rules

LEVELS = {"floating": AppKit.NSFloatingWindowLevel,     # 3
          "status": AppKit.NSStatusWindowLevel,         # 25
          "popup": AppKit.NSPopUpMenuWindowLevel}       # 101
EVERYWHERE = (AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
              | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
              | AppKit.NSWindowCollectionBehaviorStationary
              | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
# Shown afresh while a full-screen Space is active, a panel with every Space set
# can miss it (the dictation panel did): from then on it is moved to the
# active Space instead.
TO_ACTIVE = (AppKit.NSWindowCollectionBehaviorMoveToActiveSpace
             | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
             | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
DRAG_AFTER = 3.0      # points of movement before a press becomes a drag
GROW = 0.16           # the hover controls sliding out
SNAP = 0.20           # landing in a corner
WORDS = 0.18          # the words' window, to its new text
ORB_EASE = (0.23, 1.0, 0.32, 1.0)     # the orb's curve: cubic-bezier(.23,1,.32,1)
BACK_EASE = (0.5, 0.0, 0.75, 0.0)     # the pill rolling back under the orb: ease-in
WORDS_EASE = "easeOut"
FILL = "fill"         # the glass fills the window (until the orb's page says)
REST_GLYPH = 52       # the resting orb, so it starts where its first message puts it
REST_WINDOW = 76      # its window until then: the glyph and the page's 12 pt inset all round


def _glass_available():
    try:
        objc.lookUpClass("NSGlassEffectView")
        return True
    except objc.nosuchclass_error:
        return False


def _material_view(material, rect):
    """The view behind a panel's page: "glass" (NSGlassEffectView, macOS 26),
    "vibrancy" (the HUD's dark blur, before it), or "soft" (the words: a
    frosted ground that follows light and dark, with no rim; the user saw the
    glass there as "a white box with border and shadow"). None for none."""
    if material == "glass":
        return objc.lookUpClass("NSGlassEffectView").alloc().initWithFrame_(rect)
    if material not in ("vibrancy", "soft"):
        return None
    fx = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
    fx.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow if material == "vibrancy"
                    else AppKit.NSVisualEffectMaterialPopover)
    fx.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
    fx.setState_(AppKit.NSVisualEffectStateActive)
    if material == "vibrancy":
        fx.setWantsLayer_(True)
        fx.layer().setMasksToBounds_(True)
    return fx


def _rounded_mask(r):
    """A rounded rect that stretches to any size, for a frosted view's corners:
    behind-window blur ignores a layer's corner radius, a mask image does not."""
    r = max(1.0, float(r))
    side = 2 * r + 1

    def draw(rect):
        AppKit.NSColor.blackColor().set()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, r, r).fill()
        return True
    img = AppKit.NSImage.imageWithSize_flipped_drawingHandler_(Foundation.NSMakeSize(side, side), False, draw)
    img.setCapInsets_(AppKit.NSEdgeInsets(r, r, r, r))
    img.setResizingMode_(AppKit.NSImageResizingModeStretch)
    return img


def _timing(ease):
    """A curve: four control points, or a CAMediaTimingFunction name. Looked
    up, not imported, so it needs nothing beyond AppKit."""
    if ease is None:
        return None
    cls = objc.lookUpClass("CAMediaTimingFunction")
    return cls.functionWithName_(ease) if isinstance(ease, str) else cls.functionWithControlPoints____(*ease)


def _animate(windows, seconds, ease=None, views=(), fade_in=(), done=None):
    """Windows and views to their frames, together on one curve, and the views
    in fade_in up from transparent; done() once it is over. The animator, not
    setFrame:display:animate: (that one blocks the app), and never inside
    WebKit's message callback, where a resize did not take."""
    def apply(to):
        for w, f in windows:
            to(w).setFrame_display_(Foundation.NSMakeRect(*f), True)
        for v, f in views:
            to(v).setFrame_(Foundation.NSMakeRect(*f))
        for v in fade_in:
            to(v).setAlphaValue_(1.0)

    def finished():
        for w, _f in windows:
            w.invalidateShadow()
        if done:
            done()
    if not seconds:
        apply(lambda o: o)
        return finished()

    def group(ctx):
        ctx.setDuration_(seconds)
        curve = _timing(ease)
        if curve is not None:
            ctx.setTimingFunction_(curve)
        apply(lambda o: o.animator())
    AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(group, finished)
    for w, _f in windows:
        w.invalidateShadow()


def _seconds(m, default):
    """How long a page's change takes: its ms, else default; animate false: at once."""
    seconds = default if m.get("animate", True) else 0
    if m.get("ms") is not None:
        try:
            seconds = max(0.0, float(m["ms"]) / 1000.0)
        except (TypeError, ValueError):
            pass
    return seconds


def _tuple(rect):
    return (rect.origin.x, rect.origin.y, rect.size.width, rect.size.height)


def display_key(screen):
    """A display, by name and size: stable across plugging in and arranging."""
    f = screen.frame()
    return "%s %dx%d" % (screen.localizedName(), f.size.width, f.size.height)


def displays():
    """[(display, frame, usable frame)] of every display, and the main one's name."""
    out = [(display_key(s), _tuple(s.frame()), usable(s)) for s in AppKit.NSScreen.screens()]
    return out, display_key(AppKit.NSScreen.mainScreen())


def dock_shown(screen):
    """Whether the Dock has its place on this display now: not in a full-screen
    Space there (rules.full_screen), which hides it. From the window list's layers,
    owners and bounds only: no permission asked, no window's title read."""
    try:
        import Quartz
        f = screen.frame()
        top = AppKit.NSScreen.screens()[0].frame().size.height       # the window list's y runs down from here
        display = (f.origin.x, top - f.origin.y - f.size.height, f.size.width, f.size.height)
        found = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID) or []
        windows = [{"layer": w.get("kCGWindowLayer"), "pid": w.get("kCGWindowOwnerPID"),
                    "bounds": tuple((w.get("kCGWindowBounds") or {}).get(k, -1) for k in ("X", "Y", "Width", "Height"))}
                   for w in found]
        return not rules.full_screen(windows, display, me=os.getpid())
    except Exception:
        return True


def usable(screen):
    """The screen's frame the companion goes in (rules.usable): its visible frame,
    and the screen's own edge where the Dock isn't showing."""
    return rules.usable(_tuple(screen.frame()), _tuple(screen.visibleFrame()), dock_shown(screen))


def home_frame(window=None):
    """The visible frame the companion comes up in: the main window's display
    (rules.home), else the main display. window: the main window's frame."""
    found, main = displays()
    return rules.home(window, found, main)


class CompanionPanel(AppKit.NSPanel):
    """Never key unless asked: a click must leave the keyboard where it was."""

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class CompanionWebView(WebKit.WKWebView):
    """Takes the first click even though the panel is never key (a WKWebView
    otherwise spends it on focusing the window), sees the mouse while another
    app is active, and drags the panel when a press moves."""

    def acceptsFirstMouse_(self, event):
        return True

    def hitTest_(self, point):
        """Only the orb and its buttons take a click (the page's hit shapes);
        elsewhere nil. The pointer reading already makes the panel ignore the
        mouse there: this covers the moment between two readings."""
        shapes = getattr(getattr(self, "_owner", None), "hit", None)
        if shapes and self.superview() is not None:
            local = self.convertPoint_fromView_(point, self.superview())
            y = local.y if self.isFlipped() else self.bounds().size.height - local.y
            if min(rules.shape_distance((local.x, y), sh) for sh in shapes) > 0:
                return None
        return objc.super(CompanionWebView, self).hitTest_(point)

    def updateTrackingAreas(self):
        objc.super(CompanionWebView, self).updateTrackingAreas()
        if getattr(self, "_always", None) is not None:
            self.removeTrackingArea_(self._always)
        self._always = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), AppKit.NSTrackingMouseEnteredAndExited | AppKit.NSTrackingMouseMoved
            | AppKit.NSTrackingActiveAlways | AppKit.NSTrackingInVisibleRect, self, None)
        self.addTrackingArea_(self._always)

    def mouseDown_(self, event):
        self._press = AppKit.NSEvent.mouseLocation()
        self._origin = self.window().frame().origin
        self._dragging = False
        objc.super(CompanionWebView, self).mouseDown_(event)

    def mouseDragged_(self, event):
        press = getattr(self, "_press", None)
        if press is None or not getattr(self._owner, "draggable", True):
            return objc.super(CompanionWebView, self).mouseDragged_(event)
        now = AppKit.NSEvent.mouseLocation()
        dx, dy = now.x - press.x, now.y - press.y
        if not self._dragging and abs(dx) + abs(dy) < DRAG_AFTER:
            return
        if not self._dragging:
            self._dragging = True
            self._owner.hold_followers()
        self.window().setFrameOrigin_(Foundation.NSMakePoint(self._origin.x + dx, self._origin.y + dy))

    def mouseUp_(self, event):
        objc.super(CompanionWebView, self).mouseUp_(event)
        if getattr(self, "_dragging", False):
            self._owner._dropped()
        self._press, self._dragging = None, False


def _mouse():
    """The pointer on screen, (x, y), y up. No permission needed; the tests set their own."""
    p = AppKit.NSEvent.mouseLocation()
    return p.x, p.y


def plain(body):
    """A script message (a JS object) as plain Python. It arrives as NSDictionary,
    NSNumber and NSNull, which json.dumps cannot write back to the page: through
    JSON, null is None."""
    data, _err = Foundation.NSJSONSerialization.dataWithJSONObject_options_error_(body, 0, None)
    if data is None:
        return {"type": "?"}
    try:
        out = json.loads(bytes(data).decode("utf-8"))
    except ValueError:
        return {"type": "?"}
    return out if isinstance(out, dict) else {"type": "?"}


class _Bridge(AppKit.NSObject):
    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = plain(message.body())
        # Out of WebKit's callback: a resize or a menu from inside it misbehaves.
        AppHelper.callAfter(self.owner._message, body)


class _Loaded(AppKit.NSObject):
    def webView_didFinishNavigation_(self, web, navigation):
        self.owner._loaded()


class _MenuTarget(AppKit.NSObject):
    def choose_(self, item):
        self.owner._chose(item.representedObject())


class Companion(object):
    """A panel and its web view.

    The orb keeps to a corner (or to where it was dropped), placed by its glyph
    (glyph=, then the page's), grows from it as the page reports its size, and
    snaps on release. Where it was is never kept: each time it comes up its
    owner sends it home (the bottom right of the main window's display). The words' window is the same class, not draggable, the orb's
    child (parent=): AppKit moves it, and hides it, with the orb, and its owner
    places it. follow(frame) -> [(companion, frame)] says where what follows the
    orb goes when the orb is moved to frame. on_message(dict) gets what the
    page posts that this class does not handle."""
    parent, glyph, glass, follow, _moving = None, None, FILL, None, 0
    inset, hit, _hovering, _polling, _poll_gen = None, None, False, 0, 0
    under, _glass_gen = None, 0

    def __init__(self, url=None, html=None, size=(180, 44), level="status", material="auto",
                 shadow=True, radius=None, draggable=True,
                 on_message=None, log=None, parent=None,
                 glyph=None, clicks=True):
        if material == "auto":
            material = "glass" if _glass_available() else "vibrancy"
        self.size, self.radius, self.material, self.draggable = size, radius, material, draggable
        self.parent, self.glyph = parent, glyph
        self.on_message = on_message or (lambda m: None)
        self.log = log or (lambda e: None)
        self.corner = None
        self.behaviour, self.spaces, self.reported, self.missed = EVERYWHERE, 0, -1, False
        rect = Foundation.NSMakeRect(0, 0, size[0], size[1])
        self.panel = CompanionPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        p = self.panel
        p.setFloatingPanel_(True)
        p.setHidesOnDeactivate_(False)          # a panel hides when its app is not active, by default
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setLevel_(LEVELS[level])
        p.setCollectionBehavior_(EVERYWHERE)
        p.setOpaque_(False)
        p.setBackgroundColor_(AppKit.NSColor.clearColor())
        p.setHasShadow_(bool(shadow))           # the system shadow follows the rounded shape
        p.setMovableByWindowBackground_(False)
        p.setReleasedWhenClosed_(False)
        p.setCanHide_(False)                    # Hide Standby (Cmd+H) hides the window, not the companion
        p.setIgnoresMouseEvents_(not clicks)    # clicks=False: they go to the app underneath

        config = WebKit.WKWebViewConfiguration.alloc().init()
        self._bridge = _Bridge.alloc().init()
        self._bridge.owner = self
        config.userContentController().addScriptMessageHandler_name_(self._bridge, "sb")
        web = CompanionWebView.alloc().initWithFrame_configuration_(rect, config)
        web._owner = self
        web.setValue_forKey_(False, "drawsBackground")
        web.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        self._nav = _Loaded.alloc().init()
        self._nav.owner = self
        web.setNavigationDelegate_(self._nav)
        self.web = web
        p.setContentView_(self._wrap(web, rect))
        if url:
            web.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(url)))
        else:
            web.loadHTMLString_baseURL_(html or "", Foundation.NSURL.URLWithString_("http://127.0.0.1/"))
        if draggable:
            self.home(home_frame())

    def _wrap(self, web, rect):
        """The material behind the page: glass on macOS 26, else vibrancy (HUD),
        in a clear view with the page above it, so the glass can sit behind
        part of the window or not at all (the orb's page says: _glass_at).
        Until then it fills the window, masked to the pill. The page draws no
        surface or shadow of its own."""
        self.fx = _material_view(self.material, rect)
        if self.fx is None:
            return web
        fx = self.fx
        self._set_radius(self.radius if self.radius is not None else rect.size.height / 2)
        fx.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        box = AppKit.NSView.alloc().initWithFrame_(rect)
        box.addSubview_(fx)
        web.setFrame_(box.bounds())
        box.addSubview_(web)                    # above the glass
        if self.material == "soft":
            self.panel.setHasShadow_(False)
        return box

    def _set_radius(self, r):
        if self.material == "glass":
            self.fx.setCornerRadius_(r)
        elif self.material == "soft":
            self.fx.setMaskImage_(_rounded_mask(r))
        else:
            self.fx.layer().setCornerRadius_(r)

    def _set_material(self, material):
        """The words' page asked for another ground ("soft", "glass"): the view
        behind the page swapped for one of that kind, where the old one was."""
        if material == self.material or self.fx is None or material not in ("soft", "glass", "vibrancy"):
            return
        if material == "glass" and not _glass_available():
            material = "vibrancy"
        old = self.fx
        fx = _material_view(material, old.frame())
        fx.setAutoresizingMask_(old.autoresizingMask())
        fx.setHidden_(old.isHidden())
        old.superview().replaceSubview_with_(old, fx)
        self.fx, self.material = fx, material
        self.panel.setHasShadow_(material != "soft")
    def backdrop(self, radius, material=None):
        """Glass filling the window, with these corners; None: no glass.
        material: the page's ground for it, if it says ("soft")."""
        if self.fx is None:
            return
        if material:
            self._set_material(material)
        self.fx.setHidden_(radius is None)
        if radius is not None:
            self._set_radius(float(radius))

    def _glass_at(self, frame):
        """The glass for the window going to `frame`: ([(view, rect)] to move,
        [views] to fade in, its curve, and what to do once it is there). The
        page's rect (rules.glass_rect), or none.
        glass that comes rolls out from `under` (a circle hidden under the
        orb's centre) on the orb's curve, and glass that goes rolls back under
        it on BACK_EASE and is hidden only once it is there (unless the pill
        came out again meanwhile). From an older page with no `under`,
        glass that comes fades in where its rect would be in the window as it
        is now, and glass that goes is hidden at once."""
        fx = self.fx
        if fx is None or self.glass == FILL:
            return [], [], None, None
        screen = self._screen()
        rect = rules.glass_rect(self.glass, frame[2:], self.corner, frame, screen)
        under = rules.glass_rect(self.under, frame[2:], self.corner, frame, screen) if self.under else None
        self._glass_gen += 1
        gen = self._glass_gen
        if rect is None:
            if fx.isHidden():
                return [], [], None, None
            if under is None:
                fx.setHidden_(True)
                return [], [], None, None

            def tuck():
                if gen == self._glass_gen:     # not if the pill came out again since
                    fx.setHidden_(True)
            return [(fx, under[:4])], [], BACK_EASE, tuck
        fx.setAutoresizingMask_(AppKit.NSViewNotSizable)
        self._set_radius(rect[4])
        ease = ORB_EASE if under else WORDS_EASE
        if not fx.isHidden():
            return [(fx, rect[:4])], [], ease, None
        start = under or rules.glass_rect(self.glass, _tuple(self.panel.frame())[2:], self.corner, frame, screen)
        fx.setFrame_(Foundation.NSMakeRect(*start[:4]))
        fx.setAlphaValue_(1.0 if under else 0.0)  # under the orb: nothing to fade
        fx.setHidden_(False)
        return [(fx, rect[:4])], ([] if under else [fx]), ease, None

    # -- the page --------------------------------------------------------------
    def _message(self, m):
        kind = m.get("type")
        if kind == "orb":
            try:
                w, h, glyph = float(m["w"]), float(m["h"]), float(m["glyph"])
                inset = float(m["inset"]) if m.get("inset") is not None else None
            except (KeyError, TypeError, ValueError):
                return
            glass, under = m.get("glass"), m.get("under")
            self.fit_orb(w, h, glyph, glass if isinstance(glass, dict) else None, inset=inset,
                         glass_seconds=_seconds(m, GROW), under=under if isinstance(under, dict) else None)
        elif kind == "hit":
            self.hit = rules.hit_shapes(m)        # None (null, or malformed): the whole window again
            self._pointer_tick()                  # once, now: the shapes just changed
        else:
            self.on_message(m)

    def _loaded(self):
        if self.draggable:
            self._tell_edges()

    def js(self, code):
        self.web.evaluateJavaScript_completionHandler_(code, None)

    def call(self, fn, *args):
        """A page global, if the page has it: fn(args...)."""
        self.js("window.%s&&%s(%s)" % (fn, fn, ",".join(json.dumps(a) for a in args)))

    def _screen(self, fresh=False):
        """The usable frame of the panel's screen (usable), as last worked out unless
        fresh or on another screen now: the window list is read on a move (home, a
        drop, a Space change), not on every hover."""
        screen = self.panel.screen() or AppKit.NSScreen.mainScreen()
        key = display_key(screen)
        if fresh or getattr(self, "_fitted", None) is None or getattr(self, "_fitted_on", None) != key:
            self._fitted_on, self._fitted = key, usable(screen)
        return self._fitted

    def _tell_edges(self):
        h, v = rules.edges(_tuple(self.panel.frame()), self.corner, self._screen())
        self.call("edge", h, v)

    # -- the window ------------------------------------------------------------
    def show(self):
        """In front without activating Standby, on the Space the user is on.
        Asked every second: only a hidden panel, or one left on another Space,
        is ordered in again."""
        p = self.panel
        if not (p.isVisible() and p.isOnActiveSpace()):
            self._order_in()
            p.invalidateShadow()
        self.attach()
        if getattr(self, "draggable", False) and not self._polling:
            self._poll_gen += 1                 # one chain of readings at a time
            self._polling = self._poll_gen
            self._pointer_tick(gen=self._polling)

    def _order_in(self):
        """The dictation panel's way: left on another Space, out and in
        again here; still not here, moved to the active Space from then on. The
        first show on each Space says in the log where it went."""
        p = self.panel
        if p.isVisible() and not p.isOnActiveSpace():
            p.orderOut_(None)
        p.orderFrontRegardless()
        if self.reported != self.spaces:
            self.reported = self.spaces
            self.later(0.3, self._report)
        if p.isOnActiveSpace():
            return
        if self.behaviour != TO_ACTIVE:
            self.log({"text": "not on this Space with every Space set; moving it to the active Space from now on"})
            self.behaviour = TO_ACTIVE
            p.setCollectionBehavior_(TO_ACTIVE)
            p.orderOut_(None)
            p.orderFrontRegardless()
        if not p.isOnActiveSpace() and not self.missed:
            self.missed = True
            self.log({"text": "still not on this Space (behaviour %d, level %d)" % (self.behaviour, p.level())})

    def _report(self):
        p = self.panel
        self.log({"text": "first show after %d Space change(s): on this Space %s, visible %s (behaviour %d)" % (
            self.spaces, "yes" if p.isOnActiveSpace() else "NO",
            "yes" if int(p.occlusionState()) & AppKit.NSWindowOcclusionStateVisible else "NO", self.behaviour)})

    def space_changed(self):
        """Another Space (a swipe to a full-screen app): a panel up comes along."""
        self.spaces += 1
        if self.panel.isVisible():
            self._order_in()
            self.attach()

    @staticmethod
    def later(seconds, fn):
        Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(seconds, False, lambda _t: fn())

    def hide(self):
        self.detach()
        self._polling = 0                       # any reading still due finds it stale, and stops
        if self.panel.isVisible():
            self.panel.orderOut_(None)

    def _pointer_tick(self, gen=None):
        """Where the pointer is: WebKit sees no hover over another app, so
        the panel reads the pointer itself. Over the orb or a button the panel takes
        the mouse and hover is on; elsewhere it ignores the mouse, so a click
        goes to the app beneath. A press begun on the orb keeps the mouse until
        mouse-up, wherever the pointer goes. Read 30 times a second near the
        orb, 5 when it is far, and only while the panel is up."""
        if gen is not None and (gen != self._polling or not self.panel.isVisible()):
            if gen == self._polling:
                self._polling = 0
            return
        frame = _tuple(self.panel.frame())
        shapes = rules.hit_on_screen(self.hit, frame) if self.hit else None
        inside, distance = rules.pointer(_mouse(), shapes, frame)
        pressed = getattr(self.web, "_press", None) is not None
        take = inside or pressed
        if bool(self.panel.ignoresMouseEvents()) == take:
            self.panel.setIgnoresMouseEvents_(not take)
        if inside != self._hovering and not pressed:
            self._hovering = inside
            self.call("hover", inside)
        if gen is not None:
            self.later(rules.poll_every(distance), lambda: self._pointer_tick(gen=gen))

    def attach(self):
        """A child window (the words): from now on AppKit moves it, and hides it,
        with its parent. Only onto a parent that is up."""
        if self.parent is not None and self.parent.visible and self.panel.parentWindow() is None:
            self.parent.panel.addChildWindow_ordered_(self.panel, AppKit.NSWindowAbove)

    def detach(self):
        if self.parent is not None and self.panel.parentWindow() is not None:
            self.parent.panel.removeChildWindow_(self.panel)

    @property
    def visible(self):
        return bool(self.panel.isVisible())

    def frame(self):
        return [round(v, 1) for v in _tuple(self.panel.frame())]

    def fit_orb(self, w, h, glyph, glass, inset=None, glass_seconds=0, under=None):
        """{type:"orb"}: the orb's window at its new size, placed by the glyph
        (whose outer edges stay where they are), with the glass behind the part
        of it the page says, or none. At once, never animated: animating
        the window while the page re-laid out inside it was the orb's shake.
        The page grows the orb itself, by transform, in a window that keeps
        its size. Glass that comes or goes rolls out from under the orb and
        back (under), or fades (before), over glass_seconds (the page's
        ms): a view inside the window, so nothing is laid out again."""
        was, was_inset = (self.glyph if self.glyph is not None else glyph), self.inset
        frame = rules.orb_resized(_tuple(self.panel.frame()), was, (w, h), glyph, self.corner, self._screen(),
                                  inset_was=was_inset, inset=inset)
        self.size, self.glyph, self.glass, self.inset, self.under = (w, h), glyph, glass, inset, under
        # The system shadow follows the window's alpha: around glass that fills
        # the window, it is the glass's; with less or none it would double the
        # page's own glow and shadows into a smudge.
        self.panel.setHasShadow_(rules.glass_fills(glass, w, h))
        self.move(frame, 0, glass_seconds=glass_seconds)

    def move(self, frame, seconds=0, ease=None, glass_seconds=None):
        """The window to `frame`, its glass with it, and what follows it (the
        words, placed for that frame), all on one curve; a hidden window goes at
        once. A child window is let go for the move and taken back after, as
        AppKit would also shift it by the parent's own move."""
        if not self.panel.isVisible():
            seconds = 0
        others = self.follow(frame) if self.follow else []
        for c, _f in others:
            c.detach()
        views, fade, glass_ease, settled = self._glass_at(frame)
        if not self.panel.isVisible():
            glass_seconds = 0
        if not seconds:                         # the orb and its words in one screen update
            for w in [self.panel] + [c.panel for c, _f in others]:
                getattr(w, "disableScreenUpdatesUntilFlush", lambda: None)()
        self._moving += 1
        n = self._moving

        def done():
            if n == self._moving:              # not when a newer move has started
                for c, _f in others:
                    c.attach()
        if glass_seconds and not seconds:      # the windows at once; the glass on its own curve
            _animate([(self.panel, frame)] + [(c.panel, f) for c, f in others], 0, None, done=done)
            _animate([], glass_seconds, glass_ease, views, fade, settled)
            return

        def both():
            done()
            if settled:
                settled()
        _animate([(self.panel, frame)] + [(c.panel, f) for c, f in others], seconds, ease,
                 views, fade, both)

    def hold_followers(self):
        """A drag starts: whatever follows the orb is its child again at once,
        even part way through a move, so it comes along."""
        self._moving += 1
        for c, _f in (self.follow(_tuple(self.panel.frame())) if self.follow else []):
            c.attach()

    def place(self, frame, seconds=0, ease=None):
        if not self.panel.isVisible():
            seconds = 0
        _animate([(self.panel, frame)], seconds, ease)

    # -- where it sits ---------------------------------------------------------
    def home(self, visible):
        """The bottom right of that visible frame, placed by the glyph, and
        the page told which corner it is in."""
        self.corner = rules.DEFAULT
        x, y = rules.at_corner(self.corner, self.size, visible, self._margin(self.size[1]))
        self.move((x, y) + tuple(self.size))
        self._fitted, self._fitted_on = visible, display_key(self.panel.screen() or AppKit.NSScreen.mainScreen())
        self._tell_edges()

    def refit(self):
        """Back in its corner when the screen around it changed: into a full-screen
        Space (the Dock hidden: down to the screen's edge) or out of one (above the
        Dock again). Only in a corner, only while up and not held by a drag; the
        same screen as last time moves nothing."""
        if self.corner is None or not self.panel.isVisible() or getattr(getattr(self, "web", None), "_press", None) is not None:
            return
        was = getattr(self, "_fitted", None)
        screen = self._screen(fresh=True)
        if screen == was:
            return
        x, y, w, h = _tuple(self.panel.frame())
        nx, ny = rules.at_corner(self.corner, (w, h), screen, self._margin(h))
        self.move((nx, ny, w, h), SNAP)
        self._tell_edges()

    def _margin(self, h):
        """From the screen's edges: the orb's window by its glyph, else by its own edges."""
        return rules.MARGIN if self.glyph is None else rules.orb_margin(h, self.glyph, inset=self.inset)

    def _dropped(self):
        """Released after a drag: snap within reach of a corner. Not kept: the
        next time it comes up, it is home again."""
        visible = self._screen(fresh=True)
        now = _tuple(self.panel.frame())
        frame, self.corner = rules.snap(now, visible, margin=self._margin(now[3]))
        self.move(frame, SNAP)
        self._tell_edges()

    # -- the menu ---------------------------------------------------------------
    def popup(self, items, x, y, on_choose):
        """A native menu at (x, y) of the page (CSS px from its top left; the
        web view is flipped, so those are its own coordinates). items: from
        rules.menu. on_choose(action) once the user picks one."""
        self._menu_target = _MenuTarget.alloc().init()
        self._menu_target.owner = self
        self._on_choose = on_choose
        menu = self._menu(items)
        point = Foundation.NSMakePoint(x, y if self.web.isFlipped() else self.web.bounds().size.height - y)
        menu.popUpMenuPositioningItem_atLocation_inView_(None, point, self.web)

    def _menu(self, items):
        """An NSMenu from rules.menu's items."""
        menu = AppKit.NSMenu.alloc().initWithTitle_("")
        menu.setAutoenablesItems_(False)
        for it in items:
            if it is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            title, key, enabled, checked, action = it
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "choose:", key)
            item.setTarget_(self._menu_target)
            item.setRepresentedObject_(action)
            item.setEnabled_(bool(enabled))
            item.setState_(AppKit.NSControlStateValueOn if checked else AppKit.NSControlStateValueOff)
            menu.addItem_(item)
        return menu

    def _chose(self, action):
        if self._on_choose is not None:
            self._on_choose(str(action))

# ---------------------------------------------------------------- in Standby.app

class _Watcher(AppKit.NSObject):
    def changed_(self, note):
        self.owner.update()

    def tick_(self, timer):
        self.owner.update()

    def screens_(self, note):
        self.owner.screens_changed()

    def space_(self, note):
        self.owner.space_changed()


class Presence(object):
    """The companion inside Standby.app: the orb (the pill) and its words (its
    child window), there only for a live call the user sent to it (orb_asked)
    while Standby's window is not up (rules.shown): the window or the orb, never
    both. Never for a meeting. Also the main window's minimise, close, Quit
    and Dock click: with no call, minimise goes to the Dock like any window's.
    Moving between the window and the orb never touches the call: only End, and
    Quit, end it.

    hooks, from ui.py: live_kind() ("meeting", "talk" or None), orb_asked(),
    ask_orb(), stop_recording(), stop_all(), log(msg). make and native are for
    the tests: fake panels, and none of the app-wide hooks into AppKit and pywebview."""

    ASK_QUIT = "typeof askQuit==='function'?(askQuit(),true):false"    # the page's question, if it has it

    def __init__(self, win, base, hooks, make=None, native=True):
        self.win, self.hooks = win, hooks
        self.saying = 0                     # until when (monotonic) the orb stays, saying it was stopped
        self.quit_ok = False                # the page's Quit: the next terminate goes through
        self.words_size = None              # {type:"words"}: up while set
        make = make or Companion
        # Only what the panels say in words reaches ui.log (not each mouse-down).
        say = lambda e: e.get("text") and hooks.log("companion: %s" % e["text"])
        q = "?token=" + getattr(hooks, "token", "")   # the app's token: its page needs it (ui.py)
        self.pill = make(url=base + "/companion" + q, size=(REST_WINDOW, REST_WINDOW), glyph=REST_GLYPH,
                         log=say, on_message=self._message)
        # Nothing in the words is clickable: a click on them is the app's underneath.
        self.words = make(url=base + "/companion" + q + "#words", size=(184, 44), draggable=False, clicks=False, log=say,
                          radius=14, material="soft", parent=self.pill, on_message=self._message)
        self.pill.follow = self._follow
        if native:
            self._hook_into_app()
        self.update()

    def _hook_into_app(self):
        intercept_minimise(self.win.native, self.minimise)
        add_window_menu()
        on_reopen(self.open_main)
        ask_before_quit(self.quitting, self.hooks.stop_all)
        self._watch = _Watcher.alloc().init()
        self._watch.owner = self
        center = Foundation.NSNotificationCenter.defaultCenter()
        # Which app is active no longer matters; the window coming
        # back from the Dock does, however it was brought back.
        for name in (AppKit.NSWindowDidMiniaturizeNotification, AppKit.NSWindowDidDeminiaturizeNotification):
            center.addObserver_selector_name_object_(self._watch, "changed:", name, self.win.native)
        center.addObserver_selector_name_object_(self._watch, "screens:",
                                                 AppKit.NSApplicationDidChangeScreenParametersNotification, None)
        watch_spaces(self._watch)
        # A session starts or ends without Standby noticing otherwise (a talk going
        # quiet, a meeting stopped from the terminal): look once a second.
        self._timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self._watch, "tick:", None, True)

    # -- when it shows --------------------------------------------------------
    def window_up(self):
        w = self.win.native
        return bool(w.isVisible()) and not bool(w.isMiniaturized())

    def orb_asked(self):
        """Has the user sent what is live to the orb (Mini window, the corner button, Cmd+M)?
        Until then there is no orb: a meeting or a call starting never brings it up."""
        asked = getattr(self.hooks, "orb_asked", None)
        return True if asked is None else bool(asked())

    def update(self):
        live = ("talk" if self.hooks.live_kind() == "talk" and self.orb_asked() else None) \
            or ("talk" if time.monotonic() < self.saying else None)
        if rules.shown(live, self.window_up()):
            if not self.pill.visible:
                self._go_home()                  # every time it comes up: never where it was last
            self.pill.show()
            if self.words_size:
                self._show_words()
        else:
            self.words.hide()
            self.pill.hide()

    def space_changed(self):
        for c in (self.pill, self.words):        # the orb first: the words are its child
            c.space_changed()
        self.pill.refit()                        # a full-screen Space hides the Dock, another gives it back
        self.pill.later(0.6, self.pill.refit)    # and once its windows have settled

    def screens_changed(self):
        """A display came or went: home again, on the main window's display as it is now."""
        self._go_home()
        self._place_words()

    def _go_home(self):
        """Where the orb comes up, every time (the user, 2026-10-08): the bottom
        right of the display the main window is on (rules.home), whatever
        happened last time. A drag still moves it while it is up."""
        try:
            window = _tuple(self.win.native.frame())
        except Exception:                        # no frame to go by: the main display
            window = None
        self.pill.home(home_frame(window))

    # -- the main window ------------------------------------------------------
    def into_companion(self):
        """Minimise or close while a session is live: the window goes, the pill stays."""
        self.win.native.orderOut_(None)
        self.update()

    def minimise(self):
        """Cmd+M, the yellow button: True if it went into the companion; False
        (nothing live) lets the window go to the Dock as usual."""
        if rules.on_minimise(self.hooks.live_kind()) == "companion":
            ask = getattr(self.hooks, "ask_orb", None)
            if ask is not None:
                ask()                            # minimised to the orb: it carries the session from now on
            self.into_companion()
            return True
        return False

    def to_corner(self):
        """The header's ⇲: what Cmd+M does while a session is live. With
        nothing live, nothing (the page shows it only while one is)."""
        return self.minimise()

    def open_main(self):
        w = self.win.native
        if w.isMiniaturized():                   # in the Dock since before the session began
            w.deminiaturize_(None)
        self.win.show()                          # pywebview: shows it and activates Standby
        AppHelper.callAfter(self.update)         # once it is up (pywebview shows it on the next turn)

    SHOW_LIVE = "typeof mxShowLive === 'function' && mxShowLive()"

    def open_settings(self, pane=None):
        """Standby's Settings, in its window: on `pane` ("call") or at its list."""
        self.open_main()
        js = "openSettings(%s)" % ('"%s"' % pane if pane else "")
        # pywebview's evaluate_js waits on the main thread for its answer: never from it.
        threading.Thread(target=self.win.evaluate_js, args=(js,), daemon=True).start()

    def show_live(self):
        """Standby's window, at what is live: the call, or the meeting's window on its
        agent's page (the page's mxShowLive), never a view the window no longer has."""
        self.open_main()
        # pywebview's evaluate_js waits on the main thread for its answer: never from it.
        threading.Thread(target=self.win.evaluate_js, args=(self.SHOW_LIVE,), daemon=True).start()

    def closing(self):
        """pywebview's closing event: False keeps the window (and the app)."""
        what = rules.on_close(self.hooks.live_kind())
        if what == "companion" and self.orb_asked():
            self.into_companion()
            return False
        if what == "companion":                  # live, never sent to the orb: the window just goes
            what = "hide"
        if what == "hide":                       # Standby keeps running; the Dock icon brings it back
            self.win.native.orderOut_(None)
            self.update()
            return False
        return True

    # -- Quit ---------------------------------------------------------------------
    def quitting(self):
        """Quit asked for (applicationShouldTerminate): True quits now, and
        nothing records once Standby is gone. While a meeting records it asks
        first, in Standby's window with the page's own question (one
        confirm UI): the window comes up, and the page's Quit comes back as
        quit_now(). A page that can't ask quits at once, as before."""
        # Only a recording asks: a paused meeting stays paused when Standby quits
        recording = getattr(self.hooks, "recording", None)
        live = self.hooks.live_kind() if recording is None or recording() else None
        if self.quit_ok or rules.on_quit(live, quit_by_system()) == "quit":
            self.hooks.stop_all()
            return True
        self.hooks.log("quit while recording: asking first")
        self.open_main()
        # pywebview's evaluate_js waits on the main thread for its answer: never from it.
        threading.Thread(target=self._ask_quit, daemon=True).start()
        return False

    def _ask_quit(self):
        try:
            asked = self.win.evaluate_js(self.ASK_QUIT) is True
        except Exception as exc:                 # not loaded within pywebview's wait, or gone
            self.hooks.log("quit: the page could not ask (%r)" % exc)
            asked = False
        if not asked:
            AppHelper.callAfter(self.quit_now)

    def quit_now(self):
        """The page's Quit (or no page to ask): stop everything and quit."""
        self.quit_ok = True
        self.terminate()

    @staticmethod
    def terminate():
        AppKit.NSApp.terminate_(None)           # through quitting(), like Cmd+Q

    # -- the page and the menu -------------------------------------------------
    def _message(self, m):
        kind = m.get("type")
        if kind == "expand":
            # Also how anything that needs a question is asked: the page opens
            # Standby's window and the main page asks there, so the panel never
            # puts up a modal of its own (decided 2026-10-06).
            self.show_live()
        elif kind == "settings":
            self.open_settings("call" if m.get("pane") == "call" else None)
        elif kind == "end":
            if self.hooks.live_kind() == "talk":   # the mini window's End: as the menu's End Call
                self._chose("stop")
        elif kind == "menu":
            if self.hooks.live_kind() != "talk":
                return                             # the call just ended: no menu for it
            self.pill.popup(rules.menu(), float(m.get("x", 0)), float(m.get("y", 0)), self._chose)
        elif kind == "words":
            self._words(m)

    def _chose(self, action):
        self.hooks.log("companion menu: %s" % action)
        if action == "open":
            self.show_live()
        elif action == "settings":
            self.open_settings()
        elif action == "stop":
            # End Call, from here: nothing takes the user's screen. The orb
            # says so for a moment and goes; the call's card waits in Standby's window.
            self.hooks.stop_recording()
            self.saying = time.monotonic() + rules.FAREWELL
            self.pill.call("say", rules.FAREWELL_WORDS)
            AppHelper.callLater(rules.FAREWELL + 0.05, self.update)
        elif action == "quit":
            self.terminate()

    # -- the words, by the orb ------------------------------------------------------
    def _words(self, m):
        """{type:"words", w, h, radius, material, ms}: the words' window, sized to its text
        and lined up with the orb's glyph; w 0 hides it, radius null: no glass."""
        try:
            w, h = float(m.get("w") or 0), float(m.get("h") or 0)
        except (TypeError, ValueError):
            return
        if w <= 0 or h <= 0:
            self.words_size = None
            self.words.hide()
            return
        self.words_size = self.words.size = (w, h)
        self.words.backdrop(m.get("radius"), m.get("material"))
        if self.words.visible:
            self._place_words(_seconds(m, WORDS), WORDS_EASE)
        elif self.pill.visible:
            self._show_words()               # at once, where they go

    def _words_at(self, orb):
        """Where the words go with the orb's window at `orb`: lined up with its
        glyph (rules.words_frame), above it at a bottom corner, below at a top."""
        pill = self.pill
        return rules.words_frame(tuple(orb), pill.glyph, self.words.size, pill.corner, pill._screen())

    def _place_words(self, seconds=0, ease=None):
        self.words.place(self._words_at(self.pill.frame()), seconds, ease)

    def _show_words(self):
        if not self.words.visible:
            self._place_words()              # the orb may have moved while they were down
        self.words.show()

    def _follow(self, frame):
        """What goes with the orb's window when it moves to `frame`: the words, if up."""
        return [(self.words, self._words_at(frame))] if self.words.visible else []


def watch_spaces(watcher):
    """Another Space became active: watcher.space_(note). The workspace's
    notification centre, not the app's."""
    AppKit.NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
        watcher, "space:", AppKit.NSWorkspaceActiveSpaceDidChangeNotification, None)


def attach(win, base, hooks):
    """The companion in Standby.app; on the main thread, once pywebview runs."""
    return Presence(win, base, hooks)


# ---------------------------------------------------------------- the main window

_MINIMISE = {}


def intercept_minimise(win, handler):
    """Minimise (Cmd+M, the yellow button) on this pywebview window calls
    handler instead of going to the Dock; a handler that returns False lets it
    go to the Dock after all. pywebview cannot cancel a minimise
    (its `minimized` event comes after the fact), so its window class gets a
    miniaturize: of its own; windows without a handler minimise as before."""
    cls = type(win)
    if cls not in _MINIMISE:
        def miniaturize_(self, sender):
            h = getattr(self, "sb_minimise", None)
            if h is None or h() is False:          # False: not taken, so to the Dock
                return objc.super(cls, self).miniaturize_(sender)
        # A category must carry its class's own name.
        type(cls.__name__, (objc.Category(cls),), {"miniaturize_": miniaturize_})
        _MINIMISE[cls] = True
    win.sb_minimise = handler


def add_window_menu():
    """pywebview's menu has no Window menu, so Cmd+M does nothing. The
    standard one: Minimize (Cmd+M), and macOS adds the window list."""
    app = AppKit.NSApp
    menu = AppKit.NSMenu.alloc().initWithTitle_("Window")
    menu.addItemWithTitle_action_keyEquivalent_("Minimize", "performMiniaturize:", "m")
    item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Window", None, "")
    item.setSubmenu_(menu)
    app.mainMenu().addItem_(item)
    app.setWindowsMenu_(menu)
    return menu


_REOPEN = []


def on_reopen(handler):
    """A click on the Dock icon while the main window is hidden brings it back."""
    from webview.platforms import cocoa
    if not _REOPEN:
        def reopen(self, app, visible):
            if _REOPEN[0] is not None:
                _REOPEN[0]()
            return True
        cls = cocoa.BrowserView.AppDelegate
        type(cls.__name__, (objc.Category(cls),),
             {"applicationShouldHandleReopen_hasVisibleWindows_": objc.selector(
                 reopen, selector=b"applicationShouldHandleReopen:hasVisibleWindows:", signature=b"Z@:@Z")})
        _REOPEN.append(None)
    _REOPEN[0] = handler


QUIT_REASON = int.from_bytes(b"why?", "big")     # kAEQuitReason


def quit_by_system():
    """A logout, restart or shutdown: macOS's quit event then carries a reason
    (kAEQuitReason). Cmd+Q, the Dock's Quit and terminate: carry none."""
    try:
        event = AppKit.NSAppleEventManager.sharedAppleEventManager().currentAppleEvent()
        return event is not None and event.attributeDescriptorForKeyword_(QUIT_REASON) is not None
    except Exception:
        return False


def terminate_reply(handler, stop_all):
    """applicationShouldTerminate's answer, from ask_before_quit's handler."""
    try:
        if handler() is False:
            return AppKit.NSTerminateCancel
    except Exception:
        try:
            stop_all()
        except Exception:
            pass
    return AppKit.NSTerminateNow


def ask_before_quit(handler, stop_all):
    """Quit (Cmd+Q, the Dock, the menu) asks handler() first: False cancels it
    (the handler asks the user, and quits later itself); anything else quits.
    pywebview's own asks every window's `closing`, and close-while-recording
    says no, so Quit would do nothing. A handler that fails still quits, after
    stop_all(): Standby must never become impossible to quit, nor leave a
    recording behind."""
    from webview.platforms import cocoa

    def should_terminate(self, app):
        return terminate_reply(handler, stop_all)
    cls = cocoa.BrowserView.AppDelegate
    type(cls.__name__, (objc.Category(cls),),
         {"applicationShouldTerminate_": objc.selector(
             should_terminate, selector=b"applicationShouldTerminate:", signature=b"I@:@")})
