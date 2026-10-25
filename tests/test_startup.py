"""Standby.app starts all the way, with pywebview faked and no window.

ui.main() is run as the app runs it: the window made, before_show and loaded
fired, webview.start's callback called, closing asked. It must serve, and the
companion and the title bar must really come up. Both are wrapped in
try/except and only logged, so a missing name at startup used to reach the
user's launch instead of a test: a NameError (hooks instead of
self.hooks) and a missing suggestions poller. Here either
one fails this test.
"""
import json, os, sys, threading, time, types, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)

ROOT = os.environ["STANDBY_ROOT"]
os.makedirs(os.path.join(ROOT, "sessions"), exist_ok=True)
open(os.path.join(ROOT, "config.json"), "w").write(json.dumps({"agent": {"mode": "local"}}))
import socket  # noqa: E402
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
os.environ["STANDBY_UI_PORT"] = str(PORT)

fails = []


def check(label, ok, d=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:500])


# -- pywebview, and the AppKit work, faked: no window, nothing on screen -------
class Event(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self):
        return [fn() for fn in self.handlers]


class Native(object):
    """Enough of an NSWindow for main() to wire things to."""

    def isVisible(self):
        return True

    def isMiniaturized(self):
        return False

    def orderOut_(self, sender):
        pass


class Win(object):
    def __init__(self):
        self.native, self.js = Native(), []
        self.events = types.SimpleNamespace(**{n: Event() for n in
                                               ("before_show", "loaded", "closing", "closed", "shown")})

    def show(self):
        pass

    def evaluate_js(self, code):
        self.js.append(code)


made, started = [], []
win = Win()
webview = types.ModuleType("webview")
webview.create_window = lambda *a, **kw: (made.append((a, kw)), win)[1]
webview.start = lambda func=None, *a, **kw: (started.append(True), func and func())[0]
sys.modules["webview"] = webview

helper = types.ModuleType("PyObjCTools.AppHelper")
helper.callAfter = lambda fn, *a: fn(*a)          # the main thread, here and now
pyobjctools = types.ModuleType("PyObjCTools")
pyobjctools.AppHelper = helper
sys.modules.setdefault("PyObjCTools", pyobjctools)
sys.modules["PyObjCTools.AppHelper"] = helper

attached, titlebars = [], []


class FakePresence(object):
    """What companion.attach gives back: main() asks it before a close."""

    def __init__(self):
        self.asked = 0
        self.keep = False

    def closing(self):
        self.asked += 1
        return False if self.keep else None


presence = FakePresence()
companion = types.ModuleType("companion")
companion.attach = lambda w, base, hooks: (attached.append((w, base, hooks)), presence)[1]
sys.modules["native.macos.companion"] = companion      # where the app reaches the orb (native.window)


class FakeTitlebar(object):
    def __init__(self, native, on_fullscreen=None):
        titlebars.append((native, on_fullscreen))
        self.on_fullscreen = on_fullscreen

    def fullscreen(self):
        return False


titlebar = types.ModuleType("titlebar")
titlebar.Titlebar = FakeTitlebar
sys.modules["native.macos.titlebar"] = titlebar

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)

logged = []
ui.log = lambda m: logged.append(str(m))
ui.adopt_login_path = lambda: None                 # no login shell in a test

sys.argv = ["ui.py", "--window"]
rc = ui.main()
check("main() returns cleanly in window mode", rc == 0, rc)
check("  the window was made on 127.0.0.1 with the js bridge",
      made and made[0][0][1] == "http://127.0.0.1:%d/?token=%s" % (PORT, ui.TOKEN) and "js_api" in made[0][1], made)
check("  and webview.start was given the callback it runs once the GUI is up", started == [True], started)

# The companion: attached for real, not swallowed into the log.
check("the companion comes up (not 'not available')",
      attached and not [l for l in logged if "companion: not available" in l],
      [l for l in logged if "companion" in l])
check("  with the window, this Mac's address and the app's hooks",
      bool(attached) and attached[0][0] is win and attached[0][1] == "http://127.0.0.1:%d" % PORT
      and hasattr(attached[0][2], "live_kind") and hasattr(attached[0][2], "stop_all"), attached[:1])

# The title bar: made when the window is about to show, and told about the lights.
win.events.before_show.fire()
check("before_show hides the title bar (not 'kept the standard one')",
      titlebars and not [l for l in logged if "title bar: kept" in l],
      [l for l in logged if "title bar" in l])
check("  on the window itself, with a full-screen handler",
      bool(titlebars) and titlebars[0][0] is win.native and callable(titlebars[0][1]), titlebars[:1])
win.events.loaded.fire()
time.sleep(0.3)
check("the page loaded: the traffic lights' corner is kept clear",
      any("classList.add('lights')" in j for j in win.js), win.js)
if titlebars:
    titlebars[0][1](True)
time.sleep(0.3)
check("  full screen: no lights, so the page gets its corner back",
      any("classList.remove('lights')" in j for j in win.js), win.js)

# Closing is wired, and asks the companion first.
check("closing is wired to a handler", win.events.closing.handlers)
check("  it asks the companion first, and lets the window go when nothing is live",
      win.events.closing.fire() == [None] and presence.asked == 1, presence.asked)
presence.keep = True
check("  a live session: the companion keeps the window (False)", win.events.closing.fire() == [False])
presence.keep = False

# It serves.
base = "http://127.0.0.1:%d" % PORT


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.read().decode("utf-8")


check("the server answers /api/state", "sessions" in json.loads(get("/api/state")))
check("  /api/companion, for the companion's page", "live" in json.loads(get("/api/companion")))
check("  and serves the page itself", "<!doctype html>" in get("/").lower())
check("nothing else failed at startup", not [l for l in logged if "not available" in l or "kept the standard" in l],
      [l for l in logged if "not available" in l or "kept the standard" in l])
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
