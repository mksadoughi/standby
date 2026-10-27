"""Settings in a window of its own: one, made the first time it's opened, brought to the front after,
hidden (never destroyed) when it's closed, where it was left; the mini window's Settings opens it on the
Call section and leaves the call where it is. pywebview and the window calls are fakes: nothing is shown."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, sys, tempfile, threading, types
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbsetwin-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
import ui  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


class Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow(object):
    def __init__(self, title, url, **kw):
        self.title, self.url, self.kw = title, url, kw
        self.native, self.shown, self.hidden, self.js, self.destroyed = object(), 0, 0, [], False
        self.events = types.SimpleNamespace(before_show=Event(), loaded=Event(), closing=Event())
        self.x, self.y, self.width, self.height = 10, 20, kw.get("width"), kw.get("height")

    def show(self): self.shown += 1
    def hide(self): self.hidden += 1
    def evaluate_js(self, js): self.js.append(js)
    def destroy(self): self.destroyed = True


made = []
sys.modules["webview"] = types.SimpleNamespace(create_window=lambda title, url, **kw: made.append(FakeWindow(title, url, **kw)) or made[-1])
placed, fronted = [], []
ui.window = types.SimpleNamespace(titlebar=lambda native, on_fullscreen=None: types.SimpleNamespace(fullscreen=lambda: False),
                                  place=lambda native, frame: placed.append(frame), to_front=lambda native, log: fronted.append(native),
                                  frame_of=lambda native: (100, 200, 500, 640), working_screen=lambda: (0, 0, 1440, 900),
                                  visible_frames=lambda: [(0, 0, 1440, 900)], on_main=lambda fn, *a: fn(*a))


def from_a_thread(fn, *a):
    """pywebview makes windows from another thread once it runs: the callers do too."""
    out = []
    t = threading.Thread(target=lambda: out.append(fn(*a)))
    t.start()
    t.join(10)
    return out[0] if out else None


check("no app window (Standby without one): Settings says so, opens nothing",
      from_a_thread(ui.open_settings_window).get("error") and not made)
ui.APP_WINDOW["win"] = FakeWindow("Standby", "/")
r = from_a_thread(ui.open_settings_window, "call")
check("the first open makes the one Settings window, on the Call section",
      r == {"ok": True} and len(made) == 1 and made[0].title == "Standby Settings"
      and made[0].url.startswith("http://127.0.0.1:%d/settings?token=%s" % (ui.PORT, ui.TOKEN)) and made[0].url.endswith("&pane=call"), (r, made))
win = made[0]
check("  as wide as the main window, a little shorter, never too small to lay out",
      (win.kw["width"], win.kw["height"], win.kw["min_size"]) == (500, 640, (400, 480)), win.kw)
check("  its own bridge: it can hide itself", isinstance(win.kw["js_api"], ui.SettingsApi) and win.kw["js_api"].window is win)
for fn in win.events.before_show:
    fn()
check("  the first time: centred on the screen being worked on", placed[-1] == [470.0, 130.0, 500, 640], placed)

r = from_a_thread(ui.open_settings_window)
check("a second open: no copy, the same window in front, at the list, its values fresh",
      r == {"ok": True} and len(made) == 1 and win.shown == 1 and fronted[-1] is win.native and win.js[-1] == "showSettings(null)",
      (r, len(made), win.shown, win.js))
from_a_thread(ui.open_settings_window, "call")
check("  from the mini window again: on the Call section", win.js[-1] == 'showSettings("call")', win.js)
from_a_thread(ui.open_settings_window, "../../etc")
check("  a section it doesn't have: the list", win.js[-1] == "showSettings(null)", win.js)

done = threading.Event()
real_open = ui.open_settings_window
ui.open_settings_window = lambda pane=None: (done.set(), real_open(pane))[1]
try:
    r = ui.CompanionHooks().open_settings("call")
finally:
    ui.open_settings_window = real_open
check("the mini window's Settings (from the main thread): the Settings window, never the main one",
      r == {"ok": True} and done.wait(5) and ui.APP_WINDOW["win"].shown == 0, (r, ui.APP_WINDOW["win"].shown))

closes = [fn() for fn in win.events.closing]
check("closing it (its red light, Cmd-W): hidden, kept for the next open", closes == [False] and win.hidden >= 1 and not win.destroyed, closes)
check("  where it was left, remembered", ui.read_json(ui.WINDOW_FILE).get("settings") == [100, 200, 500, 640], ui.read_json(ui.WINDOW_FILE))
for fn in win.events.before_show:
    fn()
check("  and opened there next time", placed[-1] == [100, 200, 500, 640], placed)
hidden = win.hidden
check("the dialog closing in the window (the page's own close): the window hides",
      win.kw["js_api"].settings_close() == {"ok": True} and win.hidden == hidden + 1)

ui.close_settings_window()
check("the main window really closing (Standby going): the Settings window goes with it",
      win.destroyed and [fn() for fn in win.events.closing] == [True])

page, settings_page = ui.main_page(), ui.main_page(settings=True)
check("the main window's page: the app, Settings in it only as the fallback",
      'const SETWIN="0"==="1"' in page and '<script src="/agents.js"></script>' in page and ui.TOKEN in page)
check("the Settings window's page: Settings alone, no Standby view, no polling",
      'const SETWIN="1"==="1"' in settings_page and "/agents.js" not in settings_page and ui.TOKEN in settings_page
      and "if(SETWIN){refresh();showSettings(" in settings_page, settings_page[-400:])
check("  in the window, the list has no Back and Esc stays (closing is the window's own)",
      '$("setback").style.display=SETWIN?"none":""' in page and "else if(!SETWIN)settings.close()" in page and "if(SP" in page)
check("every way in goes through openSettings: the window first, the page's dialog only without it",
      "pywebview.api.open_settings(" in page and "return showSettings(pane)}" in page)

if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
