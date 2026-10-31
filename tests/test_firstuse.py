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
check("the meeting's sound: never asked asks, heard is allowed, a capture's silence is denied",
      [fu.sound_state(s) for s in ("not_asked", "allowed", "denied", None)] == ["ask", "allowed", "denied", "ask"])
check("  asked, with no sign either way, never holds a start (macOS never says its answer)",
      fu.sound_state("asked") == "allowed")
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

R = fu.ROWS
v = fu.view("call", R, {"mic": "ask", "sound": "ask"})
m, snd = v["steps"]
check("Set up Standby: its line, both rows, each its own Allow, Skip on the sound",
      (v["title"], v["sub"], m["state"], m["button"], m["do"], m["skip"], snd["state"], snd["button"], snd["do"], snd["skip"]) ==
      ("Set up Standby", "Two things for macOS to allow, once.", "open", "Allow", "allow_mic", None, "open", "Allow", "allow_sound", "Skip"), v)
check("  each row one line: what it's for", m["line"] == fu.NAMES["mic"][1] and "takes only the sound" in snd["line"]
      and not m["waiting"], v)
v = fu.view("meeting", R, {"mic": "ask", "sound": "allowed"})
check("either row first: the sound ticked, the mic still open", [x["state"] for x in v["steps"]] == ["open", "done"]
      and v["title"] == fu.TITLE, v)
v = fu.view("call", R, {"mic": "denied", "sound": "ask"})
m = v["steps"][0]
check("the mic refused before: Open Settings, waiting, how to turn it on",
      (m["button"], m["do"], m["waiting"], m["line"]) == ("Open Settings", "settings_mic", True, fu.REFUSED), m)
v = fu.view("meeting", R, {"mic": "allowed", "sound": "denied"})
snd = v["steps"][1]
check("  the sound refused the same way, Skip still there",
      (snd["button"], snd["do"], snd["waiting"], snd["line"], snd["skip"]) == ("Open Settings", "settings_sound", True, fu.REFUSED, "Skip"), snd)
v = fu.view("call", R, ALL, phase="starting")
check("all done: You're all set, no line, ticks, Starting your call…",
      (v["title"], v["sub"], [x["state"] for x in v["steps"]], v["go"]) == ("You're all set", "", ["done", "done"], "Starting your call…"), v)
v = fu.view("meeting", R, {"mic": "allowed", "sound": "ask"}, skip=True)
check("  the sound skipped: Skipped on its row, starting", v["go"] == "Starting your meeting…" and v["steps"][1]["state"] == "skipped", v)
v = fu.view("meeting", R, ALL, phase="failed", error="Your Mac didn't start")
check("a failed start: its cause, Try again from Claude Code, Close",
      (v["title"], v["error"], v["retry"], v["button"]) == ("Couldn't start your meeting", "Standby couldn't start listening.", fu.RETRY, "Close"), v)
check("a failed start's cause, as a sentence: no doubled colons, the recorder's fallback said plainly",
      (fu.cause("Standby is busy with another call or meeting: end it first"), fu.cause("Your Mac didn't start"), fu.cause(None))
      == ("Standby is busy with another call or meeting. End it first.", "Standby couldn't start listening.", "Standby couldn't start listening."))
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
ui.permissions = types.SimpleNamespace(ask_mic=lambda answered: asked.append("mic"), ask_sound=lambda done=None: asked.append("sound"))
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

ui.lite_call = lambda b: (started.append(dict(b)), time.sleep(0.5))[0] or {"session": "c1"}   # a start takes a moment
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
check("the window's view: Set up Standby, both rows, Allow on each", v["title"] == "Set up Standby"
      and [x["do"] for x in v["steps"]] == ["allow_mic", "allow_sound"], v)
check("Allow on the mic: macOS asks then, and only then", ui.setup_act({"do": "allow_mic"}) == {"ok": True} and asked == ["mic"])
check("  a step it doesn't have: refused", "error" in ui.setup_act({"do": "rm -rf"}))
ui.setup_start_after_launch()        # the app's launch finishing meanwhile (a first call is what opened it)
STATES.update(mic="allowed")
time.sleep(0.6)
check("the mic allowed: the window still offers the meeting's sound, the call waits", started == [] and
      [x["state"] for x in ui.setup_view()["steps"]] == ["done", "open"], ui.setup_view())
STATES.update(sound="allowed")
check("both allowed: Standby starts the call itself, past the window", wait(lambda: started) and started[0].get("setup_done")
      and started[0]["cc"] == "cc-1", started)
time.sleep(1.0)
check("  once, though the hold and the app's launch both asked to watch it (twice was \"already starting\")",
      len(started) == 1, started)
check("  then the window goes and nothing is held", wait(lambda: win.hidden) and ui.setup_for("cc-1") is None
      and not ui.SETUP_FILE.exists())
check("fit: the window takes the page's height, never too big", ui.SetupApi().fit(400) == {"ok": True}
      and (lambda a: (setattr(a, "window", win), a.fit(5000), win.sizes[-1])[2])(ui.SetupApi()) == (440, 720))

print("a first meeting")
STATES.update(mic="allowed", sound="ask")
listens = []
ui.lite = lambda: types.SimpleNamespace(listen=lambda b: listens.append(dict(b)) or {"session": "m1", "pass": "ask"})
r = ui.setup_hold("meeting", {"cc": "cc-1", "capture": "auto"})
check("its sound undecided: held, the mic ticked, the sound open", r == {"setup": "meeting"}
      and [x["state"] for x in ui.setup_view()["steps"]] == ["done", "open"], ui.setup_view())
check("Allow: macOS's own prompt for the sound, once", ui.setup_act({"do": "allow_sound"}) == {"ok": True}
      and asked[-1] == "sound")
check("  the held meeting kept, its row pressed", "sound" in ui.read_json(ui.SETUP_FILE).get("pressed"))
check("Skip: this meeting starts on the mic only", ui.setup_act({"do": "skip_sound"}) == {"ok": True}
      and wait(lambda: listens) and listens[0]["capture"] == "mic" and listens[0]["setup_done"], listens)
check("  not asked again for 30 days", ui.SOUND_SKIPPED.exists() and ui.setup_hold("meeting", {"cc": "cc-1", "capture": "auto"}) is None)
wait(lambda: ui.setup_for("cc-1") is None)
STATES.update(mic="ask")
ui.setup_hold("call", {"cc": "cc-1"})
check("  a first call meanwhile: the sound's row says Skipped, only the mic asked",
      [x["state"] for x in ui.setup_view()["steps"]] == ["open", "skipped"], ui.setup_view())
ui.setup_act({"do": "cancel"})
STATES.update(mic="allowed")
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
      and ui.setup_view()["error"] == "Standby couldn't start listening." and ui.setup_view()["button"] == "Close"
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
