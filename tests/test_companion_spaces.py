"""The companion on every Space, without a window (a fake panel): shown afresh,
it is ordered in on the Space the user is on; left on another Space, it comes
out and in again here; if every Space still misses (the dictation panel's
case, a full-screen Space), it is moved to the active Space from then on, and
the log says, once. The first show on each Space reports where it went; a
Space change while it is up brings it along; asked every second, a panel
already where it should be is left alone."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentpath  # noqa: E402,F401  (never the real ~/.standby)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
try:
    from native.macos import companion  # noqa: E402  (AppKit: macOS only)
except ImportError as exc:
    print("SKIP (needs PyObjC on macOS: %s)" % exc)
    sys.exit(0)

fails = []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


class FakePanel(object):
    """lands(behaviour) says whether an order-in lands on the active Space."""

    def __init__(self, lands):
        self.lands, self.visible, self.here, self.behaviour, self.calls = lands, False, False, companion.EVERYWHERE, []

    def isVisible(self):
        return self.visible

    def isOnActiveSpace(self):
        return self.visible and self.here

    def orderOut_(self, sender):
        self.calls.append("out")
        self.visible = False

    def orderFrontRegardless(self):
        self.calls.append("in")
        self.visible, self.here = True, self.lands(self.behaviour)

    def setCollectionBehavior_(self, b):
        self.calls.append(("behaviour", b))
        self.behaviour = b

    def invalidateShadow(self):
        pass

    def occlusionState(self):
        return 2 if self.isOnActiveSpace() else 0

    def level(self):
        return 25


def panel(lands):
    c = companion.Companion.__new__(companion.Companion)
    c.panel, logged = FakePanel(lands), []
    c.log = lambda e: logged.append(e.get("text", ""))
    c.later = lambda seconds, fn: fn()
    c.behaviour, c.spaces, c.reported, c.missed = companion.EVERYWHERE, 0, -1, False
    return c, logged


c, logged = panel(lambda b: True)
c.show()
check("shown afresh, it lands here: ordered in once, every Space kept",
      c.panel.calls == ["in"] and c.behaviour == companion.EVERYWHERE, c.panel.calls)
check("  the first show on this Space says where it went",
      logged == ["first show after 0 Space change(s): on this Space yes, visible yes (behaviour %d)" % companion.EVERYWHERE], logged)
c.panel.calls.clear()
for _ in range(5):
    c.show()
check("asked every second while it is where it should be: left alone", c.panel.calls == [], c.panel.calls)

c.panel.here = False
c.space_changed()
check("a Space change while it is up, left on the old one: out and in again here",
      c.panel.calls == ["out", "in"] and c.panel.isOnActiveSpace(), c.panel.calls)
check("  and the first show on the new Space reports too", logged[-1].startswith("first show after 1 Space change(s): on this Space yes"), logged)

c, logged = panel(lambda b: b == companion.TO_ACTIVE)          # every Space misses: a full-screen Space
c.show()
check("shown afresh on a full-screen Space every Space misses: moved to the active Space",
      ("behaviour", companion.TO_ACTIVE) in c.panel.calls and c.panel.isOnActiveSpace() and c.behaviour == companion.TO_ACTIVE,
      c.panel.calls)
check("  and the log says so, once", sum("moving it to the active Space" in l for l in logged) == 1, logged)
c.panel.visible = False
c.show()
check("  next time it goes straight to the active Space (no second switch)",
      c.panel.calls.count(("behaviour", companion.TO_ACTIVE)) == 1 and c.panel.isOnActiveSpace(), c.panel.calls)

c, logged = panel(lambda b: False)                               # nothing lands: the case to hear about
c.show()
c.panel.visible = False
c.show()
check("it never lands: 'still not on this Space' in the log, once",
      sum("still not on this Space" in l for l in logged) == 1, logged)

c, logged = panel(lambda b: True)
c.space_changed()
c.space_changed()
check("Space changes while it is down: only counted, nothing ordered in", c.panel.calls == [] and c.spaces == 2)
c.show()
check("  its next show reports the Space it landed on", logged[-1].startswith("first show after 2 Space change(s)"), logged)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
