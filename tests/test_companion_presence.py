"""The companion inside Standby.app (companion.Presence), with fake panels and a
fake main window, so no window opens. It exists only while a session is live,
and then shows whenever Standby's window is not up: the window or the orb,
never both, and switching apps changes nothing. It goes when the session
ends. Minimise, close and the header's corner button while live shrink into
it; with nothing live, minimise goes to the Dock and close quits. Stop from the
orb leaves the screen alone, the orb saying so as it goes; Quit while a meeting
records asks first, in Standby's window. None of it touches the recording. The
menu is the rules' menu at the page's point, and its choices reach the app; the
words' window is the orb's child, lined up with its glyph.
It never puts up a modal of its own: anything to ask opens Standby's window."""
import os, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
try:
    from native.macos import companion  # noqa: E402  (AppKit: macOS only)
except ImportError as exc:
    print("SKIP (needs PyObjC on macOS: %s)" % exc)
    sys.exit(0)
import companion_rules as rules  # noqa: E402

companion.AppHelper.callAfter = lambda f, *a: f(*a)      # no run loop in a test: at once
later = []
companion.AppHelper.callLater = lambda delay, f, *a: later.append((delay, f))


class Clock(object):
    t = 1000.0

    def monotonic(self):
        return self.t


clock = companion.time = Clock()
# Two displays, faked: the built-in, and a Studio Display to its right (frame, then
# the visible frame below the menu bar and above the Dock). Never the real ones.
BUILTIN = ("Built-in 1512x982", (0.0, 0.0, 1512.0, 982.0), (0.0, 0.0, 1512.0, 945.0))
STUDIO = ("Studio 2560x1440", (1512.0, 0.0, 2560.0, 1440.0), (1512.0, 0.0, 2560.0, 1415.0))
main_display = {"name": BUILTIN[0]}
companion.displays = lambda: ([BUILTIN, STUDIO], main_display["name"])
fails = []


def check(label, ok, d=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


SCREEN = (0, 0, 1512, 945)


class FakePanel(object):
    def __init__(self, **kw):
        self.kw, self.visible, self.calls, self.placed, self.menus = kw, False, [], [], []
        self.glyph, self.glassed = kw.get("glyph"), kw.get("radius")
        self.size, self.corner = kw.get("size"), "bottom-right"
        self._frame = [1300.0, 16.0, 196.0, 44.0]

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def call(self, fn, *args):
        self.calls.append((fn,) + args)

    def frame(self):
        return list(self._frame)

    def _screen(self):
        return SCREEN

    def backdrop(self, radius, material=None):
        self.glassed, self.ground = radius, material

    def place(self, frame, seconds=0, ease=None):
        self.placed.append(tuple(frame))
        self.moved = (seconds, ease)

    def popup(self, items, x, y, on_choose):
        self.menus.append((items, x, y))
        self.choose = on_choose

    def home(self, visible):
        self.homes = getattr(self, "homes", []) + [visible]
        self.corner = "bottom-right"
        self._frame = [visible[0] + visible[2] - 76.0, visible[1], 76.0, 76.0]

    def _tell_edges(self):
        pass

    def space_changed(self):
        self.calls.append(("space",))

    def refit(self):
        self.calls.append(("refit",))

    def later(self, seconds, fn):
        self.calls.append(("later", seconds))
        fn()


class FakeNative(object):
    def __init__(self):
        self.up, self.minimised = True, False
        self.at = (100.0, 100.0, 1100.0, 760.0)          # its frame, kept while minimised or closed

    def frame(self):
        return companion.Foundation.NSMakeRect(*self.at)

    def isVisible(self):
        return self.up

    def isMiniaturized(self):
        return self.minimised

    def orderOut_(self, sender):
        self.up = False

    def deminiaturize_(self, sender):
        self.minimised = False


class FakeWin(object):
    def __init__(self):
        self.native, self.js, self.answer = FakeNative(), [], None

    def show(self):
        self.native.up = True

    def evaluate_js(self, code):
        self.js.append(code)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Hooks(object):
    token = "tok-launch-1"               # the app's token (ui.CompanionHooks): the pages are loaded with it
    def __init__(self):
        self.kind, self.calls = None, []

    def live_kind(self):
        return self.kind

    def stop_recording(self):
        self.calls.append(("stop",))
        self.kind = None

    def stop_all(self):
        self.calls.append(("stop_all",))

    def log(self, msg):
        self.logged = getattr(self, "logged", []) + [msg]


panels = []
make = lambda **kw: (panels.append(FakePanel(**kw)), panels[-1])[1]
win, hooks = FakeWin(), Hooks()
p = companion.Presence(win, "http://127.0.0.1:8765", hooks, make=make, native=False)
pill, cap = panels
quits = []
p.terminate = lambda: quits.append(p.quit_ok)
check("two windows: the orb's from /companion, the words' from /companion#words, each with the app's token",
      pill.kw["url"].endswith("/companion?token=tok-launch-1") and cap.kw["url"].endswith("/companion?token=tok-launch-1#words")
      and cap.kw["draggable"] is False)
check("  the orb starts placed by the resting glyph, so its first message moves nothing",
      pill.kw["glyph"] == companion.REST_GLYPH == 52 and pill.kw["size"] == (76, 76))
check("the words on the soft ground (no rim, no shadow), 14 pt corners",
      cap.kw.get("material") == "soft" and cap.kw.get("radius") == 14, cap.kw)
RECORDING = ("stop", "stop_all")
touched = lambda: [c for c in hooks.calls if c[0] in RECORDING]

# Nothing live: no companion, wherever the window is.
for up in (True, False):
    win.native.up = up
    p.update()
    check("nothing live: no companion (window %s)" % ("up" if up else "closed"), pill.visible is False)
win.native.up = True
check("  minimise: not taken (False), so the window goes to the Dock like any other",
      p.minimise() is False and win.native.up is True)
check("  the corner button: nothing either", p.to_corner() is False and win.native.up is True and pill.visible is False)
check("  close (the red button, Cmd+W): the window hides, Standby keeps running, no orb",
      p.closing() is False and win.native.up is False and pill.visible is False)
win.native.up = True                       # the Dock icon brings it back

# The orb is for calls only (the user): a meeting never has one, whatever the window does.
hooks.orb_asked = lambda: hooks.asked
hooks.ask_orb = lambda: setattr(hooks, "asked", True)
hooks.asked, hooks.kind, win.native.up = False, "meeting", False
p.update()
check("a meeting records, Standby's window closed: no orb", pill.visible is False)
win.native.up = True
check("  minimise (Cmd+M): to the Dock like any window (not taken), never sent to the orb",
      p.minimise() is False and hooks.asked is False and pill.visible is False)
hooks.asked = True                         # even one that says it was
p.update()
check("  no orb for a meeting even so", pill.visible is False)
hooks.asked = False
check("  the corner button: nothing", p.to_corner() is False and pill.visible is False)
check("  close: the window just goes, no orb, the meeting records on",
      p.closing() is False and win.native.up is False and pill.visible is False and touched() == [])
win.native.up = True
# A call starts with the window closed (from Claude Code): no orb, until the user sends it there.
hooks.kind, win.native.up = "talk", False
p.update()
check("a call starts, Standby's window closed: the orb stays hidden (never sent to it)", pill.visible is False)
win.native.up = True
check("  close, never sent to the orb: the window just goes, still no orb",
      p.closing() is False and win.native.up is False and pill.visible is False)
win.native.up = True
check("  minimise (Cmd+M): sent to the orb, the window goes and the orb carries it",
      p.minimise() is True and hooks.asked is True and win.native.up is False and pill.visible is True)
p.open_main()
del hooks.orb_asked, hooks.ask_orb
hooks.kind = None
p.update()
win.native.up = True

# A call: the orb is its, and the window or the orb, never both.
hooks.kind = "talk"
p.update()
check("a call is live, Standby's window up: the orb stays down", pill.visible is False)
check("which app is active is not asked at all, so switching apps changes nothing",
      not hasattr(p, "active"))
p.update()
check("  the window behind another app (still up): no orb, the window where it was",
      pill.visible is False and win.native.up is True)
check("  minimise (Cmd+M, the yellow button): taken, the window goes, the companion comes",
      p.minimise() is True and win.native.up is False and pill.visible is True)
p._message({"type": "expand"})
check("  Open from the companion: the window is back, the companion down", win.native.up is True and pill.visible is False)
check("the header's corner button: as Cmd+M, the window goes and the orb comes",
      p.to_corner() is True and win.native.up is False and pill.visible is True)
p.open_main()
kept = p.closing()
check("close on a call: kept (False), into the orb, as minimise",
      kept is False and win.native.up is False and pill.visible is True)
p.open_main()
win.native.up, win.native.minimised = False, True          # in the Dock from before the call
p.open_main()
check("  Open with the window in the Dock: brought back from it, the orb down",
      win.native.minimised is False and win.native.up is True and pill.visible is False)
for move in (p.minimise, p.open_main, p.to_corner, p.open_main, p.closing, p.space_changed, p.screens_changed, p.open_main):
    move()
check("moving between the window and the orb never touches the call", touched() == [], touched())

# The session ends on its own (a talk going quiet, a stop elsewhere): the companion goes with it.
p.minimise()
hooks.kind = None
p.update()
check("the session ends: the companion goes at once (the next look)", pill.visible is False)
check("  and the window stays where it was (the Dock icon brings it back)", win.native.up is False)
win.show(); p.update()

hooks.kind = "talk"
win.native.up = False
p.update()
p._message({"type": "menu", "x": 150, "y": 44})
items, x, y = pill.menus[-1]
check("⋯ in a call: the rules' menu, at the page's point", items == rules.menu() and (x, y) == (150.0, 44.0), pill.menus[-1])
pill.choose("stop")
check("End Call from the orb: asks the app, and nothing takes the screen",
      ("stop",) in hooks.calls and win.native.up is False, (hooks.calls, win.native.up))
check("  the orb says so, with the page's own say()", pill.calls[-1] == ("say", "Call ended"), pill.calls[-1:])
p.update()
check("  and stays up while it does, though the talk has ended", pill.visible is True and hooks.kind is None)
check("  then a look once it has said it", later and later[-1][0] >= rules.FAREWELL, later)
clock.t += rules.FAREWELL + 0.1
later[-1][1]()
check("  which puts it away; the window still where the user left it", pill.visible is False and win.native.up is False)
hooks.kind = "talk"
win.show(); p.update()
p.minimise()
clock.t += rules.FAREWELL + 0.1
win.native.up = False
hooks.kind = "talk"
p._message({"type": "menu", "x": 150, "y": 44, "targets": [{"kind": "display", "id": "d1"}], "full": False})
check("an older page sending what a snapshot could take: the same menu, no Show Claude…", pill.menus[-1][0] == rules.menu())
hooks.kind = None
n = len(pill.menus)
p._message({"type": "menu", "x": 1, "y": 1})
check("  a menu asked for just after the session ended: none", len(pill.menus) == n)
p._message({"type": "settings", "pane": "call"})
time.sleep(0.2)
check("the mini window's Settings: Standby's window, Settings on the Call pane", win.native.up is True
      and win.js[-1:] == ['openSettings("call")'], win.js[-3:])
hooks.calls[:] = []
p._message({"type": "end"})
check("the mini window's End just after the call ended: nothing", ("stop",) not in hooks.calls, hooks.calls)
hooks.kind = "talk"
p._message({"type": "end"})
check("  on a call: as End Call, and the mini window says so", ("stop",) in hooks.calls and pill.calls[-1] == ("say", "Call ended"),
      (hooks.calls, pill.calls[-1:]))
clock.t += rules.FAREWELL + 0.1
later[-1][1]()
hooks.kind = "meeting"
pill.choose("settings")
time.sleep(0.2)
check("Settings…: the window, with its settings open (off the main thread)",
      win.native.up is True and win.js[-1:] == ["openSettings()"] and companion.Presence.SHOW_LIVE not in win.js[-1:], win.js[-2:])
win.native.up = False
p.update()

# Quit: while a meeting records it asks first, in Standby's window.
def asked(answer):
    win.js.clear()
    win.answer = answer
    n = len(quits)
    ok = p.quitting()
    time.sleep(0.2)                          # the question goes from a thread of its own
    return ok, win.js, quits[n:]


hooks.calls.clear()
hooks.kind, win.native.up = "meeting", False
ok, js, quit = asked(True)
check("Quit while a meeting records: not yet (False), the window up, the page asked",
      ok is False and win.native.up is True and js == [p.ASK_QUIT] and quit == [] and touched() == [], (ok, js, quit))
check("  the page asks with its own askQuit(), if it has one", "askQuit()" in p.ASK_QUIT and "typeof askQuit" in p.ASK_QUIT)
p.quit_now()
check("  its Quit: Standby quits, and this time it goes through", quits[-1] is True and p.quitting() is True)
check("  stopping the recording on the way", touched() == [("stop_all",)], touched())
p.quit_ok = False
hooks.calls.clear()
ok, js, quit = asked(False)
check("  a page with no askQuit (an older one): quits at once, as before", ok is False and quit == [True], (ok, quit))
p.quit_ok = False
ok, js, quit = asked(RuntimeError("not loaded"))
check("  a page that can't answer: quits at once too", quit == [True], quit)
p.quit_ok = False
hooks.calls.clear()
for kind in ("talk", None):
    hooks.kind = kind
    win.js.clear()
    check("  Quit %s: at once, nothing asked" % ("in a talk" if kind else "with nothing live"),
          p.quitting() is True and win.js == [] and touched() == [("stop_all",)], (win.js, touched()))
    hooks.calls.clear()
hooks.kind = "meeting"
was, companion.quit_by_system = companion.quit_by_system, lambda: True
try:
    check("  a logout, restart or shutdown: at once, never asked", p.quitting() is True and touched() == [("stop_all",)])
finally:
    companion.quit_by_system = was
check("  no quit event being handled (Cmd+Q, the menu): not the system's", companion.quit_by_system() is False)
# Quit asks only while a meeting records: a paused one stays paused, and Standby just quits.
p.quit_ok = False
hooks.calls.clear()
hooks.kind = "meeting"
hooks.recording = lambda: False
win.js.clear()
check("Quit with only a paused meeting: at once, nothing asked", p.quitting() is True and win.js == [], win.js)
del hooks.recording
p.saying = 0
p.quit_ok = False
n = len(quits)
pill.choose("quit")
check("  Quit Standby from the orb's menu: the same Quit", quits[n:] == [False], quits[n:])
stops = []
NOW, CANCEL = companion.AppKit.NSTerminateNow, companion.AppKit.NSTerminateCancel
check("  the app's answer: Cancel only when the handler says False",
      companion.terminate_reply(lambda: False, None) == CANCEL and companion.terminate_reply(lambda: True, None) == NOW)


def broken():
    raise ValueError("a bug")


check("  a handler that fails still quits, after stopping everything: never impossible to quit",
      companion.terminate_reply(broken, lambda: stops.append(1)) == NOW and stops == [1])
hooks.calls.clear()
win.native.up = False
p.update()

# The panel never asks anything itself (decided 2026-10-06): what needs a
# question (the snapshot's permission warning) opens Standby's window, and the
# main page asks there. So no modal from a panel, and no Space surprise.
win.native.up = False
n, m = len(pill.calls), len(pill.menus)
p._message({"type": "confirm", "text": "Standby will quit and reopen for Screen Recording.", "ok": "Continue"})
check("a confirm message from an older page: ignored, no modal, nothing to the page",
      win.native.up is False and len(pill.calls) == n and len(pill.menus) == m, (pill.calls[n:], pill.menus[m:]))
check("  and the panel has no alert of its own left", not hasattr(companion, "confirm") and not hasattr(p, "ask"))
p._message({"type": "expand"})
check("  what needs asking comes as expand: Standby's window, which asks there", win.native.up is True)
time.sleep(0.2)
check("  and the page shows what's live on its agent's page, never the old session view",
      win.js and win.js[-1] == companion.Presence.SHOW_LIVE and "mxShowLive()" in win.js[-1] and "select(" not in win.js[-1], win.js[-1:])
win.native.up = False
p.update()

# Where it comes up (the user, 2026-10-08): every time, the bottom right of the
# display the main window is on, the window's own and not the pointer's, whatever
# happened last time. A drag still moves it while it is up; nothing keeps it.
hooks.kind, win.native.at = "talk", (200.0, 120.0, 1100.0, 760.0)      # on the built-in display
win.show(); p.update()
pill.homes = []
p.minimise()
check("comes up: at home, the bottom right of the main window's display (the built-in)",
      pill.visible and pill.homes == [BUILTIN[2]] and pill.corner == "bottom-right", pill.homes)
p.update(); p.update()
check("  up already: not sent home again each second", pill.homes == [BUILTIN[2]], pill.homes)
pill.corner, pill._frame = "top-left", [1528.0, 1323.0, 76.0, 76.0]       # dragged to the Studio's top left
p.update()
check("  dragged while up: it stays where it was dropped", pill.corner == "top-left" and pill.homes == [BUILTIN[2]])
p.open_main(); p.minimise()
check("  up again: home again, not where it was dragged",
      pill.homes == [BUILTIN[2], BUILTIN[2]] and pill.corner == "bottom-right", pill.homes)
p.open_main()
win.native.at = (2100.0, 400.0, 1100.0, 760.0)                            # the window moved to the Studio
main_display["name"] = BUILTIN[0]
p.minimise()
check("  the window on the Studio Display: it comes up there, bottom right", pill.homes[-1] == STUDIO[2], pill.homes)
p.open_main()
main_display["name"] = STUDIO[0]
win.native.at = (100.0, 100.0, 1100.0, 760.0)
p.closing()
check("  the window's display, not the main one (the menu bar's): the built-in", pill.homes[-1] == BUILTIN[2], pill.homes)
n = len(pill.homes)
p.screens_changed()
check("  a display came or went while up: home again, by the window", len(pill.homes) == n + 1 and pill.homes[-1] == BUILTIN[2])
check("  and nothing is saved anywhere: the app has no hooks for it", not hasattr(hooks, "save_where")
      and "on_moved" not in pill.kw and "where" not in pill.kw and "last" not in pill.kw, pill.kw)
main_display["name"] = BUILTIN[0]
hooks.kind = None
p.update()
win.native.up = False                      # as the section before left it
p.update()

# the words' window is the orb's child, lined up with its glyph once the
# orb's page says what the glyph is ({type:"orb"}); {type:"words"} sizes it.
check("the words' window is the orb's child, and follows the orb",
      cap.kw.get("parent") is pill and pill.follow == p._follow)
check("  and lets clicks through to the app underneath (nothing in it is clickable); the orb takes them",
      cap.kw.get("clicks") is False and pill.kw.get("clicks", True) is True, (cap.kw.get("clicks"), pill.kw.get("clicks")))
hooks.kind = "talk"
p.update()
pill.glyph, pill.corner = 72.0, "bottom-right"
pill._frame = list(rules.orb_at_corner("bottom-right", 96, 72, SCREEN)) + [96.0, 96.0]
p._message({"type": "words", "w": 120, "h": 37, "radius": 12, "material": "soft", "ms": 240})
want = rules.words_frame(tuple(pill._frame), 72.0, (120.0, 37.0), "bottom-right", SCREEN)
check("  words: placed by the glyph at once, with the page's corners and ground, and up",
      cap.placed[-1] == want and cap.moved == (0, None) and cap.glassed == 12 and cap.ground == "soft" and cap.visible,
      (cap.placed[-1:], want, cap.moved))
p._message({"type": "words", "w": 200, "h": 37, "radius": 12, "ms": 240})
want = rules.words_frame(tuple(pill._frame), 72.0, (200.0, 37.0), "bottom-right", SCREEN)
check("  new words while up: to their new width on the words' curve, still by the glyph",
      cap.placed[-1] == want and cap.moved == (0.24, companion.WORDS_EASE), (cap.placed[-1:], want, cap.moved))
grown = tuple(rules.orb_at_corner("bottom-right", (208, 96), 72, SCREEN)) + (208.0, 96.0)
check("  the orb moving (hover, a snap): the words go with it, still by the glyph",
      p._follow(grown) == [(cap, rules.words_frame(grown, 72.0, (200.0, 37.0), "bottom-right", SCREEN))], p._follow(grown))
p._message({"type": "words", "w": 200, "h": 37, "radius": None})
check("  radius null: no glass behind them", cap.glassed is None and cap.visible)
p._message({"type": "words", "w": 0, "h": 0})
check("  w 0: hidden, and nothing follows the orb", cap.visible is False and p._follow(grown) == [])
p._message({"type": "words", "w": 160, "h": 37})
win.show()
p.update()
check("  in Standby's window: the words go with the orb", cap.visible is False and pill.visible is False)
pill._frame = list(rules.orb_at_corner("top-left", 96, 72, SCREEN)) + [96.0, 96.0]
pill.corner = "top-left"
win.native.up = False
p.update()
check("  back out, after the orb was dragged: the orb home again, the words placed afresh by it, then up",
      cap.visible and pill.corner == "bottom-right"
      and cap.placed[-1] == rules.words_frame(tuple(pill._frame), 72.0, (160.0, 37.0), "bottom-right", SCREEN),
      cap.placed[-1:])
p._message({"type": "words", "w": 0})
pill.glyph, pill.corner, pill._frame = 26.0, "bottom-right", [1300.0, 16.0, 196.0, 44.0]
win.show()
p.update()

p.space_changed()
check("another Space: both panels told (each comes along if it is up)",
      ("space",) in pill.calls and ("space",) in cap.calls)
check("  and the orb fits the screen it is on now (a full-screen Space hides the Dock), then again once settled",
      pill.calls[-3:] == [("refit",), ("later", 0.6), ("refit",)], pill.calls[-4:])
hooks.logged = []
pill.kw["log"]({"ev": "mousedown"})
pill.kw["log"]({"text": "first show after 1 Space change(s): on this Space yes"})
check("  in ui.log only what the panels say in words, not each mouse-down",
      getattr(hooks, "logged", []) == ["companion: first show after 1 Space change(s): on this Space yes"], getattr(hooks, "logged", None))

# The app-wide hooks too (native=True), with the AppKit and pywebview parts
# faked: the slip of 2026-10-04 (a name from __init__ used in _hook_into_app)
# stopped the real companion half way through starting, past every test above.
hooked = {}
saved = {n: getattr(companion, n) for n in ("intercept_minimise", "add_window_menu", "on_reopen", "ask_before_quit", "watch_spaces")}
companion.intercept_minimise = lambda win, handler: hooked.update(minimise=(win, handler))
companion.add_window_menu = lambda: hooked.update(window_menu=True)
companion.on_reopen = lambda handler: hooked.update(reopen=handler)
companion.ask_before_quit = lambda handler, stop_all: hooked.update(quit=(handler, stop_all))
companion.watch_spaces = lambda watcher: hooked.update(spaces=watcher)
try:
    panels.clear()
    q = companion.Presence(FakeWin(), "http://127.0.0.1:8765", hooks, make=make)
    check("in the app: starts all the way, every hook in place, and the once-a-second look",
          hooked.get("minimise", (None, None))[1] == q.minimise and hooked.get("window_menu")
          and hooked.get("reopen") == q.open_main and hooked.get("quit") == (q.quitting, hooks.stop_all)
          and getattr(q, "_timer", None) is not None and hooked.get("spaces") is q._watch, hooked)
except Exception as exc:
    check("in the app: starts all the way, every hook in place, and the once-a-second look", False, repr(exc))
finally:
    for n, f in saved.items():
        setattr(companion, n, f)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
