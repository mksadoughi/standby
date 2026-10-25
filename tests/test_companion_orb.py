"""The orb's window: {type:"orb"} sizes it and places it by its glyph,
whose outer edges stay put; the glass sits behind the part of it the page says
(x from the inner side, mirrored here), or is hidden; the words' window, its
child, is let go for each move and taken back after (AppKit would also shift
it by the parent's move), unless a newer move or a drag has started.

First with fake windows. Then on real panels that are never shown, in a process
that can never be active, so nothing reaches the screen: the glass is a sibling
under the page, and lands where the page says."""
import os, signal, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentpath  # noqa: E402,F401  (never the real ~/.standby)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
try:
    from native.macos import companion  # noqa: E402  (AppKit: macOS only)
except ImportError as exc:
    print("SKIP (needs PyObjC on macOS: %s)" % exc)
    sys.exit(0)
import companion_rules as rules  # noqa: E402

signal.alarm(60)                      # never left running
fails = []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


SCREEN = (0.0, 0.0, 1512.0, 945.0)


class Rect(object):
    def __init__(self, x, y, w, h):
        self.origin = type("P", (), {"x": x, "y": y})()
        self.size = type("S", (), {"width": w, "height": h})()


class FakeWindow(object):
    def __init__(self, frame, visible=True):
        self.f, self.up, self.parent, self.children = tuple(frame), visible, None, []

    def frame(self):
        return Rect(*self.f)

    def screen(self):
        return None                              # the main display, as for a panel not yet placed

    def isVisible(self):
        return self.up

    def parentWindow(self):
        return self.parent

    def addChildWindow_ordered_(self, child, order):
        child.parent = self
        self.children.append(child)

    def removeChildWindow_(self, child):
        child.parent = None
        self.children.remove(child)

    def invalidateShadow(self):
        pass

    def setHasShadow_(self, on):
        self.shadow = bool(on)


class FakeGlass(object):
    def __init__(self):
        self.hidden, self.alpha, self.frame, self.radius, self.mask = False, 1.0, None, None, None

    def isHidden(self):
        return self.hidden

    def setHidden_(self, on):
        self.hidden = bool(on)

    def setAlphaValue_(self, a):
        self.alpha = a

    def setFrame_(self, r):
        self.frame = (r.origin.x, r.origin.y, r.size.width, r.size.height)

    def setAutoresizingMask_(self, m):
        self.mask = m

    def setCornerRadius_(self, r):
        self.radius = r


moves = []


def fake_animate(windows, seconds, ease=None, views=(), fade_in=(), done=None):
    moves.append({"windows": [(w, tuple(f)) for w, f in windows], "seconds": seconds, "ease": ease,
                  "views": [(v, tuple(f)) for v, f in views], "fade_in": list(fade_in), "done": done})


real_animate, companion._animate = companion._animate, fake_animate


def last():
    """The last move: its windows' call, with the glass's own call folded in when
    there is one (the windows at once, the glass fading over the page's ms)."""
    i = max(k for k, mv in enumerate(moves) if mv["windows"])
    m = dict(moves[i])
    if i + 1 < len(moves) and not moves[i + 1]["windows"]:
        g = moves[i + 1]
        m.update(views=g["views"], fade_in=g["fade_in"], glass_seconds=g["seconds"], glass_ease=g["ease"], glass_done=g["done"])
    return m


def orb(frame, corner="bottom-right", visible=True):
    c = companion.Companion.__new__(companion.Companion)
    c.panel, c.fx, c.material, c.radius, c.corner = FakeWindow(frame, visible), FakeGlass(), "glass", None, corner
    c._screen = lambda fresh=False: SCREEN
    return c


rest = rules.orb_at_corner("bottom-right", 44, 26, SCREEN) + (44.0, 44.0)
c = orb(rest)
c.glyph, c.glass = 26.0, {"x": 0, "y": 0, "w": 44, "h": 44, "radius": 22}
companion.Companion._message(c, {"type": "orb", "w": 96, "h": 96, "glyph": 72, "glass": None, "ms": 200})
voice = rules.orb_at_corner("bottom-right", 96, 72, SCREEN) + (96.0, 96.0)
m = last()
check("rest to voice: placed by the glyph, at once: the window never animates (that was the shake)",
      m["windows"] == [(c.panel, voice)] and m["seconds"] == 0, m)
check("  glass null: none at all", c.fx.hidden and m["views"] == [] and c.glass is None)
check("  and no system shadow, which would double the page's own glow", c.panel.shadow is False)
c.panel.f = voice
companion.Companion._message(c, {"type": "orb", "w": 208, "h": 96, "glyph": 72, "ms": 160,
                                 "glass": {"x": 0, "y": 30, "w": 104, "h": 36, "radius": 18}})
m = last()
hovered = rules.orb_at_corner("bottom-right", (208, 96), 72, SCREEN) + (208.0, 96.0)
check("voice, hovered: wider inwards, the glyph still 16 pt in", m["windows"] == [(c.panel, hovered)], m["windows"])
check("  the buttons' glass on the inner side (the left, at a right corner), y from the bottom",
      m["views"] == [(c.fx, (0.0, 30.0, 104.0, 36.0))] and c.fx.radius == 18 and not c.fx.hidden, (m["views"], c.fx.radius))
check("  the window at once; only the glass fades in, over the page's 160 ms",
      m["seconds"] == 0 and m.get("glass_seconds") == 0.16, (m["seconds"], m.get("glass_seconds")))
check("  it was hidden: it fades in, from where it sits in the window as it is now",
      m["fade_in"] == [c.fx] and c.fx.alpha == 0.0 and c.fx.frame == (0.0, 30.0, 104.0, 36.0), (m["fade_in"], c.fx.frame))
check("  and stops following the window's size", c.fx.mask == companion.AppKit.NSViewNotSizable)
check("  glass behind the buttons only: still no system shadow (the page draws the pill's)", c.panel.shadow is False)
c.fit_orb(146, 44, 26, {"x": 0, "y": 0, "w": 146, "h": 44, "radius": 22})
check("  glass filling the window (rest hovered): the system shadow, around the glass", c.panel.shadow is True)

left = orb(rules.orb_at_corner("bottom-left", 96, 72, SCREEN) + (96.0, 96.0), corner="bottom-left")
left.glyph, left.glass = 72.0, None
left.fx.hidden = True
left.fit_orb(208, 96, 72, {"x": 0, "y": 30, "w": 104, "h": 36, "radius": 18})
m = last()
check("at a left corner: mirrored, so the glass is on the inner side (the right)",
      m["views"] == [(left.fx, (104.0, 30.0, 104.0, 36.0))], m["views"])
check("  and slides out from under the orb: it starts at the inner edge of the narrow window",
      left.fx.frame == (96.0 - 104.0, 30.0, 104.0, 36.0), left.fx.frame)

# one 360 x 112 window, the glyph 52 at rest (88 in voice mode) pinned 12 in from
# the corner, and the pill rolling out from under the orb when pointed at: the window
# never moves; the glass goes from `under` (a 40 pt circle under the orb's centre) to the
# pill on the orb's curve, and back on an ease-in, hidden only once it is there.
W3, H3, I3 = 360, 112, 12
under = {"x": W3 - I3 - 26 - 20, "y": H3 - I3 - 26 - 20, "w": 40, "h": 40, "radius": 20}
pill3 = {"x": W3 - (I3 + 52 + 10) - 132, "y": H3 - I3 - 26 - 20, "w": 132, "h": 40, "radius": 20}
at = rules.orb_at_corner("bottom-right", (W3, H3), 52, SCREEN, inset=I3) + (float(W3), float(H3))
t = orb(at)
t.glyph, t.glass, t.inset = 52.0, None, 12.0
t.fx.hidden = True
companion.Companion._message(t, {"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": None, "under": under, "ms": 260})
check("at rest: the window by the glyph, no glass", last()["windows"] == [(t.panel, at)] and t.fx.hidden)
t.panel.f = at
companion.Companion._message(t, {"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": pill3, "under": under, "ms": 340})
m = last()
want_under = (float(under["x"]), H3 - under["y"] - 40.0, 40.0, 40.0)
want_pill = (float(pill3["x"]), H3 - pill3["y"] - 40.0, 132.0, 40.0)
check("  pointed at: the window stays exactly where it was (the orb doesn't grow)", m["windows"] == [(t.panel, at)], m["windows"])
check("  the pill's glass starts at the circle under the orb, shown at once (it is hidden there)",
      t.fx.frame == want_under and t.fx.alpha == 1.0 and not t.fx.hidden and m["fade_in"] == [], (t.fx.frame, t.fx.alpha))
check("  and rolls out to the pill over the page's 340 ms, on the orb's curve",
      m["views"] == [(t.fx, want_pill)] and m["glass_seconds"] == 0.34 and m["glass_ease"] == companion.ORB_EASE,
      (m["views"], m.get("glass_seconds"), m.get("glass_ease")))
check("  the pill's own round corners", t.fx.radius == 20)
companion.Companion._message(t, {"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": None, "under": under, "ms": 260})
m = last()
check("  the pointer gone: it rolls back under the orb, 260 ms on an ease-in",
      m["views"] == [(t.fx, want_under)] and m["glass_seconds"] == 0.26 and m["glass_ease"] == companion.BACK_EASE, m)
check("  still shown while it rolls", not t.fx.hidden and callable(m["glass_done"]))
tuck = m["glass_done"]
companion.Companion._message(t, {"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": pill3, "under": under, "ms": 340})
tuck()
check("  pointed at again before it was in: it stays out (the old roll-back's end hides nothing)", not t.fx.hidden)
check("  and goes from where it is, not from under the orb again", last()["views"] == [(t.fx, want_pill)] and last()["fade_in"] == [])
companion.Companion._message(t, {"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": None, "under": under, "ms": 260})
last()["glass_done"]()
check("  once it is back under the orb: hidden", t.fx.hidden)
companion.Companion._message(t, {"type": "orb", "w": W3, "h": H3, "glyph": 88, "inset": I3, "glass": None, "under": under, "ms": 260})
check("voice mode, the glyph 88: the same window, not moved (it grows inside it)",
      last()["windows"] == [(t.panel, at)] and t.fx.hidden)
lt = orb(rules.orb_at_corner("bottom-left", (W3, H3), 52, SCREEN, inset=I3) + (float(W3), float(H3)), corner="bottom-left")
lt.glyph, lt.glass, lt.inset = 52.0, None, 12.0
lt.fx.hidden = True
lt.fit_orb(W3, H3, 52, pill3, inset=I3, glass_seconds=0.34, under=under)
check("  at a left corner: rolls out from under the orb on the right, to the pill's mirrored place",
      lt.fx.frame == (W3 - want_under[0] - 40.0, want_under[1], 40.0, 40.0)
      and last()["views"] == [(lt.fx, (W3 - want_pill[0] - 132.0, want_pill[1], 132.0, 40.0))], (lt.fx.frame, last()["views"]))
old = orb(at)
old.glyph, old.glass, old.inset = 52.0, pill3, 12.0
old.fit_orb(W3, H3, 52, None, inset=I3, glass_seconds=0.2)
check("  an older page with no `under`: the glass goes at once, as before", old.fx.hidden)

hidden = orb(rest, visible=False)
hidden.fit_orb(96, 96, 72, None)
check("a hidden window: at once, no animation", moves[-1]["seconds"] == 0)
first = orb(rest)
first.fit_orb(96, 96, 72, None)
check("the first orb message (the page before had no glyph): placed by the glyph from then on",
      moves[-1]["windows"][0][1] == voice and first.glyph == 72.0, moves[-1]["windows"])

# The words follow the orb: let go for the move, taken back after.
c = orb(voice)
c.glyph, c.glass = 72.0, None
w = companion.Companion.__new__(companion.Companion)
w.panel, w.parent = FakeWindow((1300.0, 100.0, 200.0, 37.0)), c
w.attach()
check("the words: the orb's child once both are up", w.panel.parent is c.panel)
target = (1200.0, 110.0, 200.0, 37.0)
c.follow = lambda frame: [(w, target)]
c.fit_orb(208, 96, 72, None)
m = last()
check("an orb move: the words let go, and sent to their place in the same update",
      w.panel.parent is None and (w.panel, target) in m["windows"] and m["seconds"] == 0, m["windows"])
m["done"]()
check("  taken back once it is over", w.panel.parent is c.panel)
c.fit_orb(96, 96, 72, None)
older = moves[-1]["done"]
c.fit_orb(208, 96, 72, None)
newer = moves[-1]["done"]
older()
check("a newer move under way: the older one's end does not take the words back", w.panel.parent is None)
newer()
check("  the newer one's does", w.panel.parent is c.panel)
c.fit_orb(96, 96, 72, None)
c.hold_followers()
check("a drag starting part way through: the words are its child again at once", w.panel.parent is c.panel)
c.panel.up = False
w.detach()
w.attach()
check("never onto an orb that is down", w.panel.parent is None)
c.panel.up = True
w.panel.up = True
w.attach()
w.hide = companion.Companion.hide.__get__(w)
w.panel.orderOut_ = lambda sender: setattr(w.panel, "up", False)
w.hide()
check("words hidden: let go first, then out", w.panel.parent is None and w.panel.up is False)

# Dropped near a corner: it snaps so the glyph, not the window, is 16 pt in.
c = orb(voice)
del c._screen                                       # the real one: the main display's usable frame
c.glyph, c.glass = 72.0, None
c.panel.screen = lambda: None
c.call = lambda *a: None
main_frame = companion._tuple(companion.AppKit.NSScreen.mainScreen().frame())
vis = companion._tuple(companion.AppKit.NSScreen.mainScreen().visibleFrame())
real_dock_shown, companion.dock_shown = companion.dock_shown, lambda screen: True    # whatever is open on this Mac
vis = rules.usable(main_frame, vis, True)
c.panel.f = (vis[0] + vis[2] - 96 - 30, vis[1] + 20, 96.0, 96.0)
c._dropped()
landed = moves[-1]["windows"][0][1]
check("dropped near the bottom right: snaps with the glyph 16 pt in (the window 4)",
      landed == rules.orb_at_corner("bottom-right", 96, 72, vis) + (96.0, 96.0) and c.corner == "bottom-right"
      and moves[-1]["seconds"] == companion.SNAP, (landed, c.corner))

# A full-screen Space hides the Dock (the user, 2026-10-12): the orb goes down to the
# screen's edge, the same 16 pt from the bottom as from the right, and back above the
# Dock when it shows again. Only in a corner, only when the screen changed, never mid-drag.
bottom_dock = (main_frame[0], main_frame[1] + 85, main_frame[2], main_frame[3] - 85 - 33)
real_usable = companion.usable
companion.usable = lambda screen: rules.usable(main_frame, bottom_dock, companion.dock_shown(screen))
c._fitted, c._fitted_on, c.corner = None, None, "bottom-right"
c.panel.f = rules.orb_at_corner("bottom-right", 96, 72, bottom_dock) + (96.0, 96.0)
c._screen(fresh=True)
n = len(moves)
c.refit()
check("refit: the same screen moves nothing", len(moves) == n, moves[n:])
companion.dock_shown = lambda screen: False
c.refit()
landed = moves[-1]["windows"][0][1]
gx, gy, gw, gh = rules.glyph_rect(landed, 72, "bottom-right", rules.usable(main_frame, bottom_dock, False))
check("  a full-screen app (the Dock hidden): the glyph 16 from the right and 16 from the bottom",
      (main_frame[0] + main_frame[2] - (gx + gw), gy - main_frame[1]) == (16, 16) and moves[-1]["seconds"] == companion.SNAP,
      (landed, gx, gy))
companion.dock_shown = lambda screen: True
c.web = type("W", (), {"_press": (1, 2)})()
n = len(moves)
c.refit()
check("  held by a drag: left alone", len(moves) == n)
c.web._press = None
c.refit()
landed = moves[-1]["windows"][0][1]
check("  back on a desktop: above the Dock again", landed == rules.orb_at_corner("bottom-right", 96, 72, bottom_dock) + (96.0, 96.0),
      landed)
c.corner, n = None, len(moves)
companion.dock_shown = lambda screen: False
c.refit()
check("  dropped away from a corner: stays where it was put", len(moves) == n)
companion.dock_shown, companion.usable = real_dock_shown, real_usable

# The real dock_shown, on a window list of our own: another app's window filling the
# display is a full-screen Space (no Dock); Standby's own, or a smaller one, is not.
import Quartz  # noqa: E402
real_list, scr = Quartz.CGWindowListCopyWindowInfo, companion.AppKit.NSScreen.mainScreen()
sf = scr.frame()
top = companion.AppKit.NSScreen.screens()[0].frame().size.height
whole = {"X": sf.origin.x, "Y": top - sf.origin.y - sf.size.height, "Width": sf.size.width, "Height": sf.size.height}
listed = lambda pid, b: (lambda opts, wid: [{"kCGWindowLayer": 0, "kCGWindowOwnerPID": pid, "kCGWindowBounds": b}])
try:
    Quartz.CGWindowListCopyWindowInfo = listed(os.getpid() + 1, whole)
    full = companion.dock_shown(scr)
    Quartz.CGWindowListCopyWindowInfo = listed(os.getpid(), whole)
    mine = companion.dock_shown(scr)
    Quartz.CGWindowListCopyWindowInfo = listed(os.getpid() + 1, dict(whole, Height=whole["Height"] - 40))
    small = companion.dock_shown(scr)
finally:
    Quartz.CGWindowListCopyWindowInfo = real_list
check("the real dock_shown: another app filling the display hides the Dock; Standby's own or a smaller one doesn't",
      (full, mine, small) == (False, True, True), (full, mine, small))

# Home, where it comes up every time: the bottom right of a visible frame, by the glyph.
said = []
h = orb(rest)
h.glyph, h.glass, h.size, h.inset, h.corner = 52.0, None, (360.0, 112.0), 12.0, "top-left"
h.call = lambda fn, *a: said.append((fn,) + a)
studio = (1512.0, 0.0, 2560.0, 1415.0)
h.home(studio)
check("home: the bottom right of that display, the glyph 16 pt from its edges, at once",
      moves[-1]["windows"][0][1] == rules.orb_at_corner("bottom-right", (360, 112), 52, studio, inset=12) + (360.0, 112.0)
      and h.corner == "bottom-right" and moves[-1]["seconds"] == 0, (moves[-1]["windows"], h.corner))
check("  and the page told it is at a right, bottom corner", said[-1] == ("edge", "right", "bottom"), said[-1:])

# Real panels, never shown, in a process that can never be active.
companion._animate = real_animate
AppKit = companion.AppKit
app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
real = companion.Companion(html="", size=(44, 44), draggable=False)
words = companion.Companion(html="", size=(184, 44), draggable=False, clicks=False, parent=real)
check("native: the words' panel lets clicks through, the orb's takes them",
      bool(words.panel.ignoresMouseEvents()) and not real.panel.ignoresMouseEvents())
words.panel.close()
box = real.panel.contentView()
check("native: the page above the glass, both in a plain clear view",
      type(box) is AppKit.NSView and list(box.subviews()) == [real.fx, real.web], list(box.subviews()))
check("  until the page says, the glass fills the window", tuple(companion._tuple(real.fx.frame())) == (0, 0, 44, 44)
      and real.fx.autoresizingMask() == AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
real.corner = "bottom-right"
real.fit_orb(208, 96, 72, {"x": 0, "y": 4, "w": 104, "h": 30, "radius": 15})
check("  the orb's glass: at the page's rect, y from the bottom, at once while down",
      companion._tuple(real.fx.frame()) == (0.0, 62.0, 104.0, 30.0) and not real.fx.isHidden()
      and abs(real.fx.alphaValue() - 1.0) < 1e-6, (companion._tuple(real.fx.frame()), real.fx.alphaValue()))
main = real._screen()
want = rules.orb_at_corner("bottom-right", (208, 96), 72, main) + (208.0, 96.0)
check("  and the window placed by the glyph, 16 pt in on this display",
      tuple(real.frame()) == tuple(round(v, 1) for v in want), (real.frame(), want))
# The animated path (the snap after a drag, the words to new text, the glass fading)
# moves on the display's refresh: with the display asleep (a locked Mac at night) no
# frame comes, the move never ends, and nothing here could tell. So it is skipped then.
import ctypes, ctypes.util  # noqa: E402,E401
_cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
_cg.CGMainDisplayID.restype = ctypes.c_uint32
_cg.CGDisplayIsAsleep.argtypes, _cg.CGDisplayIsAsleep.restype = [ctypes.c_uint32], ctypes.c_bool
if _cg.CGDisplayIsAsleep(_cg.CGMainDisplayID()):
    print("  an animated move: SKIPPED, the display is asleep (windows animate on its refresh)")
else:
    ended = []
    to = (want[0], want[1] + 10, 208.0, 96.0)
    companion._animate([(real.panel, to)], 0.05, companion.ORB_EASE, done=lambda: ended.append(True))
    # Until it is over, not for a length of time: idle, the completion comes in about
    # 0.06 s, but under load (five sessions and a full suite on one Mac) it can take
    # longer than any fixed wait, and this failed twice on a 0.5 s one.
    deadline = time.monotonic() + 20
    while not ended and time.monotonic() < deadline:
        companion.Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
            companion.Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05))
    check("  an animated move, on the orb's curve: lands, and says when it is over",
          ended == [True] and tuple(real.frame()) == tuple(round(v, 1) for v in to), (ended, real.frame()))
check("  part-window glass: the real panel's system shadow off", not real.panel.hasShadow())
real.fit_orb(96, 96, 72, None)
check("  glass null: hidden, no system shadow", bool(real.fx.isHidden()) and not real.panel.hasShadow())
real.fit_orb(44, 44, 26, {"x": 0, "y": 0, "w": 44, "h": 44, "radius": 22})
check("  resting, the glass the whole window: the shadow back", bool(real.panel.hasShadow()) and not real.fx.isHidden())
check("  never ordered in: nothing on the screen", not real.panel.isVisible())
# The menu, built as AppKit will show it (never opened here).
target = companion._MenuTarget.alloc().init()
real._menu_target = target
menu = real._menu(companion.rules.menu())
titles = [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())]
check("native menu on a call: Full window, End, Settings, Quit",
      titles == ["Full window", "End", "", "Settings\u2026", "", "Quit Standby"], titles)
end = menu.itemWithTitle_("End")
check("  each row choosing its action", end.target() is target and end.action() == "choose:"
      and end.representedObject() == "stop" and menu.itemWithTitle_("Quit Standby").keyEquivalent() == "q")
# while the orb peeks, the only glass is the buttons' pill, and the window itself
# draws nothing (the user saw a faint rectangle the size of the window). The page's
# own message: a fixed 340 x 96 window, the 40 pt peek, a 120 pt pill beside it.
W, H, INSET, g, pw = 340, 96, 12, 40, 120
pill = {"x": W - (INSET + g + 8) - pw, "y": H - INSET - g / 2 - 18, "w": pw, "h": 36, "radius": 18}
real.corner = "bottom-right"
real._message({"type": "orb", "w": W, "h": H, "glyph": 26, "inset": INSET, "glass": None, "ms": 200})
check("at rest: no glass at all, and no window shadow", bool(real.fx.isHidden()) and not real.panel.hasShadow())
real._message({"type": "orb", "w": W, "h": H, "glyph": g, "inset": INSET, "ms": 200,
               "glass": {"x": pill["x"], "y": pill["y"], "w": pw, "h": 36, "radius": 18}})
companion.Foundation.NSRunLoop.currentRunLoop().runUntilDate_(companion.Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.3))
got = companion._tuple(real.fx.frame())
check("  peeking: the glass is the pill's rect, nothing wider",
      not real.fx.isHidden() and got == (pill["x"], H - pill["y"] - 36, pw, 36.0), (got, pill))
box = real.panel.contentView()
check("  and the window draws nothing of its own: no shadow, clear, no background behind the page",
      not real.panel.hasShadow() and not real.panel.isOpaque() and real.panel.backgroundColor().alphaComponent() == 0
      and (box.layer() is None or box.layer().backgroundColor() is None) and not real.web.valueForKey_("drawsBackground"))
real._message({"type": "orb", "w": W, "h": H, "glyph": 26, "inset": INSET, "glass": None, "ms": 200})
check("  back at rest: the glass gone again", bool(real.fx.isHidden()) and not real.panel.hasShadow())
# on the real panel: out from under the orb, and back, hidden once it is in.
real._message({"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": None, "under": under, "ms": 260})
real._message({"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": pill3, "under": under, "ms": 340})
companion.Foundation.NSRunLoop.currentRunLoop().runUntilDate_(companion.Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.5))
check("a real panel: the pill's glass out at its rect", not real.fx.isHidden()
      and companion._tuple(real.fx.frame()) == want_pill and not real.panel.hasShadow(), companion._tuple(real.fx.frame()))
real._message({"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": I3, "glass": None, "under": under, "ms": 260})
deadline = time.monotonic() + 10
while not real.fx.isHidden() and time.monotonic() < deadline:
    companion.Foundation.NSRunLoop.currentRunLoop().runUntilDate_(companion.Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05))
check("  back under the orb, then hidden", bool(real.fx.isHidden()) and companion._tuple(real.fx.frame()) == want_under,
      companion._tuple(real.fx.frame()))
real.panel.close()
# the words' soft ground: a frosted view that follows light and dark, rounded by a
# mask (a behind-window blur ignores a layer's corners), and no window shadow.
soft = companion.Companion(html="", size=(184, 44), draggable=False, clicks=False, radius=14, material="soft")
fx = soft.fx
check("words: a frosted view (popover material, behind the window, always active)",
      isinstance(fx, AppKit.NSVisualEffectView) and fx.material() == AppKit.NSVisualEffectMaterialPopover
      and fx.blendingMode() == AppKit.NSVisualEffectBlendingModeBehindWindow and fx.state() == AppKit.NSVisualEffectStateActive)
check("  its 14 pt corners from a mask that stretches; no window shadow",
      fx.maskImage() is not None and tuple(fx.maskImage().capInsets()) == (14.0, 14.0, 14.0, 14.0)
      and not soft.panel.hasShadow(), (fx.maskImage(), soft.panel.hasShadow()))
soft.backdrop(14, "soft")
check("  the page saying soft again: the same view", soft.fx is fx)
if companion._glass_available():
    soft.backdrop(14, "glass")
    check("  a page asking for glass: swapped in where it was, with the shadow",
          soft.fx is not fx and isinstance(soft.fx, companion.objc.lookUpClass("NSGlassEffectView")) and soft.panel.hasShadow()
          and list(soft.panel.contentView().subviews()) == [soft.fx, soft.web])
    soft.backdrop(14, "soft")
    check("  and back to soft", isinstance(soft.fx, AppKit.NSVisualEffectView) and not soft.panel.hasShadow())
soft.backdrop(None, "soft")
check("  radius null: hidden", bool(soft.fx.isHidden()))
check("  never ordered in: nothing on the screen", not soft.panel.isVisible())
soft.panel.close()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
