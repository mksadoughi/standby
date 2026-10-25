"""The orb and the pointer, on a real panel never shown, in a process that
can never be active, with the pointer's position set by the test. Over another
app WebKit sees no hover, so the panel reads the pointer itself:

- over the orb's circle the panel takes the mouse and hover is on; anywhere else
  in its window it ignores the mouse, so a click there goes to the app beneath
  (that is what ignoresMouseEvents means to macOS), at rest and in voice mode;
- a press begun on the orb keeps the mouse until mouse-up, wherever it goes;
- the pointer is read 30 times a second near the orb and 5 when it is far,
  only while the panel is up, one chain of readings at a time;
- the web view's hitTest refuses a click outside the circle too;
- the orb's window changes size at once, never animated (that was the shake)."""
import os, resource, signal, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agentpath  # noqa: E402,F401  (never the real ~/.standby)
sys.path.insert(0, os.path.join(HERE, "..", "mac"))
try:
    from native.macos import companion  # noqa: E402  (AppKit: macOS only)
except ImportError as exc:
    print("SKIP (needs PyObjC on macOS: %s)" % exc)
    sys.exit(0)
import companion_rules as rules  # noqa: E402

signal.alarm(120)                     # never left running
fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:500])


AppKit, Foundation = companion.AppKit, companion.Foundation
AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
loop = Foundation.NSRunLoop.currentRunLoop()


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        loop.runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.01))


at = {"p": (0.0, 0.0)}
companion._mouse = lambda: at["p"]
c = companion.Companion(html="", size=(96, 96), draggable=False, glyph=26)
c.draggable = True                    # the orb (not restored: no saved place is read)
c.corner = "bottom-right"
said = []
c.js = lambda code: said.append(code)
c.panel.isVisible = lambda: True      # as if up; never ordered in, so never on screen
c.panel.isOnActiveSpace = lambda: True
scr = c._screen()

# The page: one window, 96 pt, the glyph pinned 12 in from the outer corner.
c._message({"type": "orb", "w": 96, "h": 96, "glyph": 26, "inset": 12, "glass": None})
frame = companion._tuple(c.panel.frame())
want = rules.orb_at_corner("bottom-right", 96, 26, scr, inset=12) + (96.0, 96.0)
check("the orb's window: 96 pt, 4 pt in from the screen's corner, at once",
      frame == want, (frame, want))
c._message({"type": "hit", "x": 71, "y": 71, "r": 13})
wx, wy, ww, wh = frame
orb_centre = (wx + 71, wy + wh - 71)
corner_spot = (wx + 8, wy + wh - 8)                  # the window's empty inner corner

at["p"] = (scr[0] + 10, scr[1] + scr[3] - 10)        # far away
c.show()
check("far away: the panel ignores the mouse (a click anywhere is the app's beneath)",
      bool(c.panel.ignoresMouseEvents()) and c._polling, c.panel.ignoresMouseEvents())
at["p"] = corner_spot
pump(0.3)
check("at rest, the pointer in the window's empty corner: still ignored, so it passes through",
      bool(c.panel.ignoresMouseEvents()) and not any("hover" in s for s in said), said[-2:])
at["p"] = orb_centre
pump(0.3)
check("  over the orb: the panel takes the mouse, and hover is on",
      not c.panel.ignoresMouseEvents() and said and said[-1] == "window.hover&&hover(true)", said[-2:])
n = len(said)
pump(0.3)
check("  still over it: no repeated hover calls", len(said) == n, said[n:])
at["p"] = corner_spot
pump(0.3)
check("  off it again: ignored again, and hover off",
      bool(c.panel.ignoresMouseEvents()) and said[-1] == "window.hover&&hover(false)", said[-2:])

# Voice mode: the same window, the glyph 72, the circle bigger.
c._message({"type": "orb", "w": 96, "h": 96, "glyph": 72, "inset": 12, "glass": None})
check("voice mode: the window keeps its size and place (nothing moves, so nothing shakes)",
      companion._tuple(c.panel.frame()) == frame, companion._tuple(c.panel.frame()))
c._message({"type": "hit", "x": 48, "y": 48, "r": 36})
at["p"] = (wx + 3, wy + wh - 3)                      # the very corner, outside the circle
pump(0.3)
check("  the window's empty corner, outside the voice orb: ignored, it passes through",
      bool(c.panel.ignoresMouseEvents()))
at["p"] = (wx + 48, wy + wh - 48)
pump(0.3)
check("  over the voice orb: taken, hover on", not c.panel.ignoresMouseEvents() and said[-1].endswith("hover(true)"))

# The orb keeps three buttons beside it (the user's choice): the hit region is shapes.
c._message({"type": "hit", "shapes": [{"x": 48, "y": 48, "r": 36},
                                      {"x": 2, "y": 84, "w": 30, "h": 10, "radius": 5}]})
button = (wx + 17, wy + wh - 89)                     # inside the rounded rect
gap = (wx + 13, wy + wh - 74)                        # between that button and the orb
at["p"] = button
pump(0.3)
check("a button beside the orb: the panel takes the click, and it counts as hover",
      not c.panel.ignoresMouseEvents() and said[-1].endswith("hover(true)"), said[-1:])
at["p"] = gap
pump(0.3)
check("  the gap between the orb and the button: ignored, so a click there passes through",
      bool(c.panel.ignoresMouseEvents()) and said[-1].endswith("hover(false)"), said[-1:])
box0 = c.web.superview()
check("  hitTest agrees: the button takes it, the gap does not",
      c.web.hitTest_(box0.convertPoint_fromView_(Foundation.NSMakePoint(17, 89), c.web)) is not None
      and c.web.hitTest_(box0.convertPoint_fromView_(Foundation.NSMakePoint(13, 74), c.web)) is None)
c._message({"type": "hit", "x": 48, "y": 48, "r": 36})
at["p"] = (wx + 48, wy + wh - 48)
pump(0.3)

# A press begun on the orb keeps the mouse until mouse-up, wherever the pointer goes.
c.web._press = Foundation.NSMakePoint(*at["p"])
at["p"] = (wx - 300, wy + 300)
pump(0.3)
check("dragging, the pointer far off the orb: the panel keeps the mouse, hover unchanged",
      not c.panel.ignoresMouseEvents() and said[-1].endswith("hover(true)"), said[-1:])
c.web._press = None
pump(0.4)
check("  released: ignored again, hover off", bool(c.panel.ignoresMouseEvents()) and said[-1].endswith("hover(false)"))

# How often it reads the pointer, and that it stops.
waits = []
real_later = c.later
c.later = lambda seconds, fn: (waits.append(seconds), real_later(seconds, fn))[1]
at["p"] = (wx + 48, wy + wh - 48)
pump(0.25)
near = waits[-1] if waits else None
at["p"] = (scr[0] + 10, scr[1] + scr[3] - 10)
pump(0.5)
far = waits[-1] if waits else None
check("near the orb it reads 30 times a second; far away, 5", near == rules.FAST and far == rules.SLOW, (near, far))
c.hide = companion.Companion.hide.__get__(c)
c.panel.orderOut_ = lambda sender: None
c.hide()
waits.clear()
pump(0.6)
check("the panel down: no more readings", waits == [], waits)
c.show()
c.hide()
c.show()
waits.clear()
pump(1.2)
check("  down and up again quickly: one chain of readings, not two",
      0 < len(waits) <= 7, waits)       # 5 a second, for 1.2 s

# The web view's own hitTest, for the moment between two readings.
box = c.web.superview()
outside = box.convertPoint_fromView_(Foundation.NSMakePoint(3, 3), c.web)   # the web view's flipped corner
inside = box.convertPoint_fromView_(Foundation.NSMakePoint(48, 48), c.web)
check("hitTest: nothing outside the circle, the web view inside it",
      c.web.hitTest_(outside) is None and c.web.hitTest_(inside) is not None, (c.web.hitTest_(outside), c.web.hitTest_(inside)))
c._message({"type": "hit", "x": None})
check("  a hit of null: the whole window again (an older page)",
      c.hit is None and c.web.hitTest_(outside) is not None)

# An older page sends no circle: the whole window is the orb, as before.
at["p"] = (wx + 3, wy + wh - 3)
c._pointer_tick()
check("no circle: the pointer anywhere in the window takes the mouse, as before",
      not c.panel.ignoresMouseEvents())

# An hour of idle reading, from the measured cost of real timers: the run loop left
# to sleep between them, and the same wait with the readings off subtracted.
c.later = real_later
c.hit = rules.hit_shapes({"x": 48, "y": 48, "r": 36})


def cpu_over(seconds):
    r0, t0 = resource.getrusage(resource.RUSAGE_SELF), time.monotonic()
    while time.monotonic() - t0 < seconds:
        loop.runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(seconds))
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    return (r1.ru_utime + r1.ru_stime - r0.ru_utime - r0.ru_stime) / (time.monotonic() - t0)


c.hide()
pump(0.3)
base = cpu_over(5)
cost = {}
for name, p in (("far", (scr[0] + 10, scr[1] + scr[3] - 10)), ("near", (wx - 100, wy + 48))):
    at["p"] = p
    c.show()
    pump(0.3)
    cost[name] = max(0.0, cpu_over(5) - base)
    c.hide()
    pump(0.3)
print("  measured, beyond an idle run loop (%.3f%% of a core): %.3f%% with the pointer far, %.3f%% near"
      % (base * 100, cost["far"] * 100, cost["near"] * 100))
print("    an idle hour (the pointer away from the orb): %.1f s of CPU" % (cost["far"] * 3600))
check("  an idle hour costs little: under 0.2% of a core with the pointer far", cost["far"] < 0.002, cost)
c.hide()

# one 360 x 112 window, the orb 52 at rest pinned 12 in, the pill (40 tall, 10 from
# the orb's edge) a hit area only while it is out.
c.later = real_later
W3, H3 = 360, 112
c._message({"type": "orb", "w": W3, "h": H3, "glyph": 52, "inset": 12, "glass": None})
f3 = companion._tuple(c.panel.frame())
check("the bigger window, the glyph's outer edges still 16 pt from the screen's",
      f3 == rules.orb_at_corner("bottom-right", (W3, H3), 52, scr, inset=12) + (float(W3), float(H3)), f3)
x3, y3, _w3, h3 = f3
cx, cy = W3 - 12 - 26, H3 - 12 - 26                  # the orb's centre, page px
c._message({"type": "hit", "x": cx, "y": cy, "r": 26})
spot = lambda px, py: (x3 + px, y3 + h3 - py)
c.show()
at["p"] = spot(cx, cy)
pump(0.3)
check("  over the bigger orb: taken, hover on", not c.panel.ignoresMouseEvents() and said[-1].endswith("hover(true)"))
at["p"] = spot(cx - 60, cy)                          # where the pill would be, while it is in
pump(0.3)
check("  where the pill would be, while it is in: passes through", bool(c.panel.ignoresMouseEvents()))
pill = {"x": cx - 26 - 10 - 132, "y": cy - 20, "w": 132, "h": 40, "radius": 20}
c._message({"type": "hit", "shapes": [{"x": cx, "y": cy, "r": 26}, pill]})
at["p"] = spot(pill["x"] + 66, cy)
pump(0.3)
check("  the pill out: a click on it is the panel's", not c.panel.ignoresMouseEvents() and said[-1].endswith("hover(true)"))
at["p"] = spot(cx - 26 - 5, cy)                      # the 10 pt between the pill and the orb
pump(0.3)
check("  the gap between the pill and the orb: passes through", bool(c.panel.ignoresMouseEvents()))
at["p"] = spot(cx, cy)
pump(0.3)
c.web._press = Foundation.NSMakePoint(*at["p"])
at["p"] = (x3 - 300, y3 + 300)
pump(0.3)
check("  a drag begun on the bigger orb keeps the mouse, far off", not c.panel.ignoresMouseEvents())
c.web._press = None
pump(0.4)
check("  released: ignored again", bool(c.panel.ignoresMouseEvents()))
c.hide()
c.panel.close()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
