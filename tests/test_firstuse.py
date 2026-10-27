"""Standby's setup window before a first call or meeting: a start that needs the mic (or an online meeting's
sound) still undecided is held, the window asks one step at a time, and once all is allowed Standby starts it
itself; allowed already, nothing is held. The window, macOS and the start are fakes: nothing is shown or asked."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, sys, tempfile, threading, time, types
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbfirst-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
import firstuse as fu  # noqa: E402
import ui  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


print("the rules")
check("a call needs the mic; a meeting as it records", fu.steps_for("call", "auto") == ["mic"]
      and fu.steps_for("meeting", "auto") == ["mic", "sound"] and fu.steps_for("meeting", "mic") == ["mic"]
      and fu.steps_for("meeting", "meeting") == ["sound"])
check("the mic: undetermined asks, refused is denied, unknown never holds a start",
      [fu.mic_state(s) for s in ("undetermined", "denied", "restricted", "authorized", None)] == ["ask", "denied", "denied", "allowed", "allowed"])
check("  heard by this build: allowed, whatever this process is told", fu.mic_state("undetermined", heard=True) == "allowed")
check("the meeting's sound: never asked asks, asked and still off is denied, heard is allowed",
      (fu.sound_state(False), fu.sound_state(False, asked=True), fu.sound_state(False, heard=True), fu.sound_state(True, asked=True))
      == ("ask", "denied", "allowed", "allowed"))
ALL = {"mic": "allowed", "sound": "allowed"}
check("all allowed: no window, no wait", fu.needed("meeting", "auto", ALL) == [] and fu.needed("call", None, {"mic": "allowed"}) == [])
check("a call with the mic undecided, or refused: its one step", fu.needed("call", None, {"mic": "ask"}) == ["mic"]
      and fu.needed("call", None, {"mic": "denied"}) == ["mic"])
check("a meeting with only its sound undecided: both steps, the mic ticked",
      fu.needed("meeting", "auto", {"mic": "allowed", "sound": "ask"}) == ["mic", "sound"])
now = 1e9
check("  skipped within 30 days: not asked again", fu.needed("meeting", "auto", {"mic": "allowed", "sound": "ask"}, now - 29 * 86400, now) == [])
check("  skipped longer ago: asked again", fu.needed("meeting", "auto", {"mic": "allowed", "sound": "ask"}, now - 31 * 86400, now) == ["mic", "sound"])
check("  a meeting on its sound alone can't skip it", fu.needed("meeting", "meeting", {"sound": "ask"}, now - 86400, now) == ["sound"])

v = fu.view("call", ["mic"], {"mic": "ask"})
s = v["steps"][0]
check("a first call: its title, no count, Allow Microphone with macOS's line",
      (v["title"], v["count"], v["sub"], s["state"], s["button"], s["do"], s["note"]) ==
      ("Before your first call", "", "Your call starts as soon as this is done.", "current", "Allow Microphone", "allow_mic",
       "macOS will ask: choose Allow."), v)
v = fu.view("call", ["mic"], {"mic": "denied"}, pressed=["mic"])
s = v["steps"][0]
check("the mic refused before: Open System Settings, waiting once pressed, how to turn it on",
      (s["button"], s["do"], s["waiting"], s["note"], s["how"]) ==
      ("Open System Settings", "settings_mic", True, fu.WAITING, "In the list, turn on **Standby**, then come back here."), s)
v = fu.view("meeting", ["mic", "sound"], {"mic": "ask", "sound": "ask"})
check("a first meeting: Step 1 of 2, the sound later, no button on it",
      (v["title"], v["count"], v["steps"][1]["state"], v["steps"][1]["button"]) == ("Set up meetings", "Step 1 of 2", "later", None), v)
v = fu.view("meeting", ["mic", "sound"], {"mic": "allowed", "sound": "ask"})
s = v["steps"][1]
check("step 2: Allow, Skip beside it, and the Quit & Reopen how-to",
      (v["count"], v["steps"][0]["state"], s["button"], s["do"], s["skip"]) == ("Step 2 of 2", "done", "Allow", "allow_sound", fu.SKIP)
      and "Quit & Reopen" in s["how"] and s["how"].endswith("Your meeting starts right after."), v)
v = fu.view("meeting", ["mic", "sound"], {"mic": "allowed", "sound": "denied"}, pressed=["sound"])
check("  asked already: Open System Settings, waiting", v["steps"][1]["do"] == "settings_sound" and v["steps"][1]["waiting"], v)
v = fu.view("meeting", ["mic", "sound"], ALL)
check("all done: You're all set, All done, ticks, Starting your meeting…",
      (v["title"], v["count"], [x["state"] for x in v["steps"]], v["go"]) == ("You're all set", "All done", ["done", "done"], "Starting your meeting…"), v)
v = fu.view("meeting", ["mic", "sound"], {"mic": "allowed", "sound": "ask"}, skip=True)
check("  skipped: the sound marked skipped, still starting", v["go"] and v["steps"][1]["state"] == "skipped", v)
check("complete: all allowed, or the sound skipped", fu.complete(["mic", "sound"], {"mic": "allowed", "sound": "ask"}, True)
      and not fu.complete(["mic", "sound"], {"mic": "allowed", "sound": "ask"}))

print("the held start")


class Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow(object):
    def __init__(self, title, url, **kw):
        self.title, self.url, self.kw = title, url, kw
        self.native, self.shown, self.hidden, self.js, self.sizes = object(), 0, 0, [], []
        self.events = types.SimpleNamespace(before_show=Event(), shown=Event(), closing=Event())

    def show(self): self.shown += 1
    def hide(self): self.hidden += 1
    def evaluate_js(self, js): self.js.append(js)
    def resize(self, w, h): self.sizes.append((w, h))
    def destroy(self): pass


made = []
sys.modules["webview"] = types.SimpleNamespace(create_window=lambda title, url, **kw: made.append(FakeWindow(title, url, **kw)) or made[-1])
ui.window = types.SimpleNamespace(titlebar=lambda native, on_fullscreen=None: object(), place=lambda native, frame: None,
                                  to_front=lambda native, log: None, on_main=lambda fn, *a: fn(*a))
ui._working_screen = lambda: (0, 0, 1440, 900)
real_states = ui.setup_states
check("a test (no devices): all allowed, so no other suite is ever held", real_states(["mic", "sound"]) == ALL)
STATES = {"mic": "ask", "sound": "ask"}
ui.setup_states = lambda steps: {k: STATES[k] for k in steps}
asked, opened, started = [], [], []
ui.permissions = types.SimpleNamespace(ask_mic=lambda answered: asked.append("mic"), ask_screen=lambda: asked.append("screen"))
ui.OPEN = ["true"]
real_call = ui.lite_call


def wait(fn, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


check("no app window (Standby without one): nothing held, macOS asks as before",
      ui.setup_hold("call", {"cc": "cc-1"}) is None)
ui.APP_WINDOW["win"] = FakeWindow("Standby", "/")
STATES.update(mic="allowed")
check("the mic allowed already: a call starts at once, no window", ui.setup_hold("call", {"cc": "cc-1"}) is None and not made)
STATES.update(mic="ask")

ui.lite_call = lambda b: started.append(dict(b)) or {"session": "c1"}
r = real_call({"cc": "cc-1", "cwd": "/tmp"})
check("a first call: held, the setup window up (Standby's own, 440 wide, its page)",
      r == {"setup": "call"} and wait(lambda: made) and made[0].kw["width"] == 440
      and made[0].url.startswith("http://127.0.0.1:%d/setup?token=%s" % (ui.PORT, ui.TOKEN)), (r, made))
win = made[0]
check("  nothing asked of macOS by itself", asked == [])
check("  kept for a Quit & Reopen", ui.read_json(ui.SETUP_FILE).get("kind") == "call")
check("  the band's state says it's waiting on setup", ui.setup_for("cc-1") == {"kind": "call", "phase": "open"}
      and ui.setup_for("cc-2") is None)
check("  another session's start meanwhile: refused, in words", "error" in real_call({"cc": "cc-2"}))
check("  this session's again: the same window to the front", real_call({"cc": "cc-1"}) == {"setup": "call"} and len(made) == 1)
v = ui.setup_view()
check("the window's view: Before your first call, Allow Microphone", v["title"] == "Before your first call" and v["steps"][0]["do"] == "allow_mic", v)
check("Allow Microphone: macOS asks then, and only then", ui.setup_act({"do": "allow_mic"}) == {"ok": True} and asked == ["mic"])
check("  a step it doesn't have: refused", "error" in ui.setup_act({"do": "rm -rf"}))
STATES.update(mic="allowed")
check("allowed: Standby starts the call itself, past the window", wait(lambda: started) and started[0].get("setup_done")
      and started[0]["cc"] == "cc-1", started)
check("  then the window goes and nothing is held", wait(lambda: win.hidden) and ui.setup_for("cc-1") is None
      and not ui.SETUP_FILE.exists())
check("fit: the window takes the page's height, never too big", ui.SetupApi().fit(400) == {"ok": True}
      and (lambda a: (setattr(a, "window", win), a.fit(5000), win.sizes[-1])[2])(ui.SetupApi()) == (440, 720))

print("a first meeting")
STATES.update(mic="allowed", sound="ask")
listens = []
ui.lite = lambda: types.SimpleNamespace(listen=lambda b: listens.append(dict(b)) or {"session": "m1", "pass": "ask"})
r = ui.setup_hold("meeting", {"cc": "cc-1", "capture": "auto"})
check("its sound undecided: held, Step 2 of 2", r == {"setup": "meeting"} and ui.setup_view()["count"] == "Step 2 of 2", ui.setup_view())
check("Allow: macOS's own prompt for the screen, once", ui.setup_act({"do": "allow_sound"}) == {"ok": True} and asked[-1] == "screen"
      and ui.SNAP_ASKED.exists())
check("  the held meeting kept, marked for the reopen", "sound" in ui.read_json(ui.SETUP_FILE).get("pressed"))
check("Skip: this meeting starts on the mic only", ui.setup_act({"do": "skip_sound"}) == {"ok": True}
      and wait(lambda: listens) and listens[0]["capture"] == "mic" and listens[0]["setup_done"], listens)
check("  not asked again for 30 days", ui.SOUND_SKIPPED.exists() and ui.setup_hold("meeting", {"cc": "cc-1", "capture": "auto"}) is None)
ui.SOUND_SKIPPED.unlink()

print("cancel, and Quit & Reopen")
wait(lambda: ui.setup_for("cc-1") is None)
r = ui.setup_hold("meeting", {"cc": "cc-1", "capture": "auto"})
check("Cancel: nothing held, the window goes, nothing starts",
      ui.setup_act({"do": "cancel"}) == {"ok": True} and ui.setup_for("cc-1") is None and not ui.SETUP_FILE.exists() and len(listens) == 1)
r = ui.setup_hold("meeting", {"cc": "cc-1", "capture": "auto"})
check("its red light is Cancel too", [fn() for fn in win.events.closing] == [False] and ui.setup_for("cc-1") is None)

ui.write_json(ui.SETUP_FILE, {"kind": "meeting", "cc": "cc-1", "body": {"cc": "cc-1", "capture": "auto"}, "steps": ["mic", "sound"],
                              "pressed": ["sound"], "skip": False, "at": time.time(), "id": "x"})
ui.SETUP.pop("held", None)
check("reopened after Allow on the meeting's sound: taken up again", ui.setup_resume() and ui.setup_for("cc-1") == {"kind": "meeting", "phase": "open"})
STATES.update(sound="allowed")
ui.setup_start_after_launch()
check("  and it starts by itself once the sound is allowed", wait(lambda: len(listens) == 2) and listens[1]["capture"] == "auto", listens)
wait(lambda: ui.setup_for("cc-1") is None)
ui.write_json(ui.SETUP_FILE, {"kind": "call", "cc": "cc-1", "body": {"cc": "cc-1"}, "steps": ["mic"], "pressed": ["mic"], "at": time.time()})
check("Standby quit by hand with a call waiting: not taken up, dropped", not ui.setup_resume() and not ui.SETUP_FILE.exists())
ui.write_json(ui.SETUP_FILE, {"kind": "meeting", "cc": "cc-1", "body": {}, "steps": ["sound"], "pressed": ["sound"], "at": time.time() - 3600})
check("  nor one from long ago", not ui.setup_resume())

STATES.update(mic="ask")
ui.lite = lambda: types.SimpleNamespace(listen=lambda b: {"error": "Your Mac didn't start"})
ui.setup_hold("meeting", {"cc": "cc-1", "capture": "mic"})
STATES.update(mic="allowed")
check("a start that fails after setup: the window says so, with Close", wait(lambda: (ui.setup_for("cc-1") or {}).get("phase") == "failed")
      and ui.setup_view()["error"] == "The meeting didn't start: Your Mac didn't start" and ui.setup_view()["button"] == "Close"
      and ui.setup_view()["retry"] == "Try again from Claude Code.", ui.setup_view())
check("  Close: gone", ui.setup_act({"do": "close"}) == {"ok": True} and ui.setup_for("cc-1") is None)

page = ui.setup_page()
check("the page: this launch's token, its steps from /api/setup, the buttons there", ui.TOKEN in page and '"/api/setup"' in page
      and "data-do" in page and "__SB_TOKEN__" not in page)
ui.lite_call = real_call

if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
