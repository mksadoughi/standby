"""The mini window's buttons each say what they do on hover, as a Mac app's tooltip does (the user, 2026-11-02):
the page sends each button's place and description with its hit shapes; the panel, which reads the pointer
itself, shows the description once the pointer rests on a button, under it (above it at the screen's foot),
and takes it away as the pointer leaves or presses. No window is shown: the panel is never brought up and the
tooltip is a stand-in that records what it was asked to show."""
import os, signal, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agentpath  # noqa: E402,F401  (never the real ~/.standby)
sys.path.insert(0, os.path.join(HERE, "..", "mac"))
import companion_rules as rules  # noqa: E402

signal.alarm(60)                      # never left running
fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:500])


HIT = {"type": "hit", "areas": [{"x": 300, "y": 60, "r": 26}, {"x": 40, "y": 40, "w": 260, "h": 40, "radius": 20}],
       "tips": [{"x": 50, "y": 46, "w": 28, "h": 28, "text": "Hide the window. The call goes on"},
                {"x": 80, "y": 46, "w": 28, "h": 28, "text": "End the call"}]}
FRAME = (1000.0, 100.0, 340.0, 120.0)          # the panel on screen: x, y (its bottom), w, h
SCREEN = (0.0, 0.0, 1440.0, 875.0)
tips = rules.tip_shapes(HIT)
check("the page's tips: each button's place and its description", [t[4] for t in tips] == [
      "Hide the window. The call goes on", "End the call"] and tips[0][:4] == (50.0, 46.0, 28.0, 28.0), tips)
check("  none, or malformed: no tips (the panel shows none)", rules.tip_shapes({"type": "hit"}) == []
      and rules.tip_shapes({"tips": [{"x": 1}]}) == [])
over_hide = (1000 + 60, 100 + 120 - 60)        # on screen, y up: inside the first button
at = rules.tip_at(over_hide, tips, FRAME)
check("the pointer on Hide: its words, and the button's place on screen", at == ("Hide the window. The call goes on",
      (1050.0, 146.0, 28.0, 28.0)), at)
check("  between the buttons, or off them: none", rules.tip_at((1000 + 79, 160), tips, FRAME) is None
      and rules.tip_at((10, 10), tips, FRAME) is None)
x, y, w, h = rules.tip_frame((1050.0, 146.0, 28.0, 28.0), (200.0, 20.0), SCREEN)
check("the tooltip: centred under its button, a little below it", (x, y) == (1050 + 14 - 100, 146 - rules.TIP_GAP - 20), (x, y))
x, y, w, h = rules.tip_frame((1400.0, 10.0, 28.0, 28.0), (200.0, 20.0), SCREEN)
check("  at the screen's foot it goes above; never past the screen's side",
      y == 10 + 28 + rules.TIP_GAP and x + w <= 1440 - 4, (x, y))

try:
    from native.macos import companion  # noqa: E402  (AppKit: macOS only)
except ImportError as exc:
    print("SKIP the panel (needs PyObjC on macOS: %s)" % exc)
    print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
    sys.exit(1 if fails else 0)
AppKit = companion.AppKit
AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)


class Shown(object):
    """The tooltip, recorded instead of drawn."""
    def __init__(self):
        self.text, self.shows, self.hides = None, [], 0

    def show(self, text, button, screen):
        self.text = text
        self.shows.append((text, button))

    def hide(self):
        self.hides += 1
        self.text = None


pointer = {"p": (0.0, 0.0)}
companion._mouse = lambda: pointer["p"]
c = companion.Companion(html="", size=(340, 120), draggable=False, glyph=52)      # never shown
c._tip = Shown()
c._message(HIT)
check("the panel keeps the page's tips with its hit shapes", [t[4] for t in c.tips] == [t[4] for t in tips], c.tips)
pointer["p"] = over_hide
c._tip_tick(FRAME, False)
check("on a button: nothing at once", c._tip.shows == [])
c._tip_state["since"] -= rules.TIP_DELAY
c._tip_tick(FRAME, False)
check("  resting there a moment: its description, under it",
      c._tip.shows == [("Hide the window. The call goes on", (1050.0, 146.0, 28.0, 28.0))], c._tip.shows)
c._tip_tick(FRAME, False)
check("  shown once, not again on every reading", len(c._tip.shows) == 1, c._tip.shows)
pointer["p"] = (1000 + 94, 160)
c._tip_tick(FRAME, False)
check("on to End: Hide's goes at once, End's waits its moment", c._tip.text is None and len(c._tip.shows) == 1)
c._tip_state["since"] -= rules.TIP_DELAY
c._tip_tick(FRAME, False)
check("  then End's", c._tip.shows[-1][0] == "End the call", c._tip.shows)
c._tip_tick(FRAME, True)
check("a press: it goes", c._tip.text is None)
c._tip_state["since"] -= rules.TIP_DELAY
c._tip_tick(FRAME, False)
check("  and doesn't come back on that button", len(c._tip.shows) == 2, c._tip.shows)
pointer["p"] = (10, 10)
c._tip_tick(FRAME, False)
pointer["p"] = (1000 + 94, 160)
c._tip_state["since"] -= rules.TIP_DELAY
c._tip_tick(FRAME, False)
c._tip_state["since"] -= rules.TIP_DELAY
c._tip_tick(FRAME, False)
check("  away and back: it shows again", len(c._tip.shows) == 3, c._tip.shows)
c.hide()
check("the mini window hidden: its tooltip goes too", c._tip.text is None)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
