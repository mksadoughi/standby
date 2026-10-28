"""Standby Dictation without a microphone, a keyboard or permissions.

Everywhere: the shortcut gesture, word fixes, the meeting guard, the decoding
thread, the LaunchAgent. On macOS also: clipboard save and restore on a private
pasteboard, the whole controller path from synthetic key events to inserted
text (mic, panel and paste faked), and, when the speech model is cached, a real
decode of speech made with `say`."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, shutil, subprocess, sys, tempfile, threading, time, types
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbdict-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import numpy as np  # noqa: E402
import dictation as d  # noqa: E402
from native.macos import dictation_app as da  # noqa: E402  (its Mac parts: the app, keys, pasting)

fails = []


def check(label, ok, detail=""):
    print("  %-60s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:400])


# ---------------------------------------------------------------- the note's capsule
w, tx, tw, bx, cx = da.note_layout(14, 12, 180, 130, 20, 12, 500)
check("note: as wide as the text, button and ✕, 12 apart",
      (w, tx, tw, bx, cx) == (14 + 180 + 12 + 130 + 12 + 20 + 12, 14, 180, 14 + 180 + 12, 14 + 180 + 12 + 130 + 12),
      (w, tx, tw, bx, cx))
w, tx, tw, bx, cx = da.note_layout(14, 12, 400, 130, 20, 12, 500)
check("note: too long, the text gives way, the button and ✕ stay whole",
      w == 500 and cx + 20 + 12 == 500 and bx == 14 + tw + 12 and tw < 400, (w, tw, bx, cx))
check("note: the words name what you can do now",
      "⌘V" in da.NO_POST and da.NO_POST_BUTTON == "Let Standby paste" and "Standby Dictation" not in da.NO_POST + da.GRANTED)


# ---------------------------------------------------------------- gesture
def run(g, events):
    """events: (t, kind, key, down); returns the commands produced."""
    out = []
    for t, kind, key, down in events:
        c = g.feed(kind, t, key, down) if kind != "poll" else g.poll(t)
        if c:
            out.append(c)
    return out


CTRL, RCTRL, RCMD, LCMD, FN = 59, 62, 54, 55, 63


def tap(t, key, hold=0.08):
    return [(t, "mod", key, True), (t + hold, "mod", key, False)]


ctl = (59, 62)                                 # a double tap of Control: the gesture itself
# `standby dictation on` says the setup window's line only while something is missing.
d.STATUS.write_text(json.dumps({"pid": 4242, "permissions": {"listen": True, "post": True, "mic": "allowed"}}))
check("dictation on, all allowed: no word about a setup window", d.permissions_note(4242, wait=0.2) is None)
d.STATUS.write_text(json.dumps({"pid": 4242, "permissions": {"listen": True, "post": False, "mic": "allowed"}}))
check("  one missing: the setup window's line", d.permissions_note(4242, wait=0.2) == d.SETUP_NOTE)
d.STATUS.write_text(json.dumps({"pid": 4242, "permissions": {"listen": False, "post": True, "mic": "allowed"}}))
check("  only Input Monitoring, with F5: nothing is missing, no line", d.permissions_note(4242, wait=0.2) is None)
check("  the app not yet saying: the line, to be safe", d.permissions_note(999, wait=0.2) == d.SETUP_NOTE)
d.STATUS.unlink()
check("Control twice is not offered: Apple Dictation's own, macOS asks to turn that on",
      "control2" not in d.SHORTCUTS and all("Control" not in label for label, _k in d.SHORTCUTS.values()))
check("Control twice toggles", run(d.Gesture(ctl), tap(0, CTRL) + tap(0.25, CTRL)) == ["toggle"])
check("left then right Control count as twice", run(d.Gesture(ctl), tap(0, CTRL) + tap(0.25, RCTRL)) == ["toggle"])
check("taps too far apart do nothing", run(d.Gesture(ctl), tap(0, CTRL) + tap(0.9, CTRL)) == [])
check("a long press is not a tap", run(d.Gesture(ctl), tap(0, CTRL, hold=0.6) + tap(0.8, CTRL)) == [])
check("Control+key in between is a chord, not a tap",
      run(d.Gesture(ctl), [(0, "mod", CTRL, True), (0.05, "key", None, None), (0.1, "mod", CTRL, False)]
          + tap(0.3, CTRL)) == [])
check("Control-click is a chord too",
      run(d.Gesture(ctl), [(0, "mod", CTRL, True), (0.05, "mouse", None, None), (0.1, "mod", CTRL, False)]
          + tap(0.3, CTRL)) == [])
check("typing between the taps resets", run(d.Gesture(ctl), tap(0, CTRL) + [(0.15, "key", None, None)]
                                            + tap(0.3, CTRL)) == [])
check("another modifier between the taps resets",
      run(d.Gesture(ctl), tap(0, CTRL) + tap(0.12, 56) + tap(0.3, CTRL)) == [])
check("four taps: start, then stop",
      run(d.Gesture(ctl), tap(0, CTRL) + tap(0.25, CTRL) + tap(2.0, CTRL) + tap(2.25, CTRL)) == ["toggle", "toggle"])
check("three quick taps toggle once", run(d.Gesture(ctl), tap(0, CTRL) + tap(0.2, CTRL) + tap(0.4, CTRL)) == ["toggle"])
rc = d.SHORTCUTS["rcommand2"][1]
check("Right Command twice ignores the left one",
      run(d.Gesture(rc), tap(0, LCMD) + tap(0.25, LCMD)) == [] and
      run(d.Gesture(rc), tap(0, RCMD) + tap(0.25, RCMD)) == ["toggle"])
check("Fn twice", run(d.Gesture(d.SHORTCUTS["fn2"][1]), tap(0, FN) + tap(0.25, FN)) == ["toggle"])
g = d.Gesture(d.SHORTCUTS["fn2"][1], hold=True)
check("hold: nothing before the hold time", run(g, [(0, "mod", FN, True), (0.2, "poll", None, None)]) == [])
check("hold: starts once held long enough", run(g, [(0.4, "poll", None, None), (0.5, "poll", None, None)]) == ["hold_start"])
check("hold: release stops", run(g, [(3.0, "mod", FN, False)]) == ["hold_end"])
g = d.Gesture(d.SHORTCUTS["fn2"][1], hold=True)
check("hold: a key during the hold cancels (it was a chord)",
      run(g, [(0, "mod", FN, True), (0.4, "poll", None, None), (0.6, "key", None, None),
              (0.7, "mod", FN, False)]) == ["hold_start", "cancel"])
g = d.Gesture(d.SHORTCUTS["fn2"][1], hold=True)
check("hold: fn+arrow never starts", run(g, [(0, "mod", FN, True), (0.1, "key", None, None),
                                          (0.5, "poll", None, None), (0.6, "mod", FN, False)]) == [])
check("hold off: holding does nothing",
      run(d.Gesture(ctl), [(0, "mod", CTRL, True), (0.5, "poll", None, None), (1.0, "mod", CTRL, False)]) == [])
check("key_down reads the left/right bits",
      da.key_down(59, 0x40001) is True and da.key_down(62, 0x40001) is False and da.key_down(62, 0x42000) is True
      and da.key_down(63, 0x800000) is True and da.key_down(57, 0x10000) is None)

check("a key let go between the taps is no chord",
      run(d.Gesture(ctl), tap(0, CTRL) + [(0.1, "key", 0, False)] + tap(0.25, CTRL)) == ["toggle"])

# ---------------------------------------------------------------- the 🎤 key
F18 = d.F18_KEY
mic = d.make_gesture({"shortcut": "mickey", "hold": False})
check("the 🎤 key is the default shortcut, a key of its own",
      d.DEFAULTS["shortcut"] == "mickey" and isinstance(mic, d.KeyPress))
check("🎤: a press starts, the next press stops",
      run(mic, [(0, "key", F18, True), (0.1, "key", F18, False), (3, "key", F18, True), (3.1, "key", F18, False)])
      == ["press", "press"])
check("🎤: auto-repeat while held is one press",
      run(mic, [(0, "key", F18, True), (0.5, "key", F18, True), (0.6, "key", F18, True), (2, "key", F18, False)])
      == ["press"])
check("🎤: other keys do nothing", run(mic, [(0, "key", 0, True), (0.1, "mod", CTRL, True)] + tap(0.3, CTRL)) == [])
held = d.make_gesture({"shortcut": "mickey", "hold": True})
check("🎤 with hold: a tap still toggles, a hold is push-to-talk",
      run(held, [(0, "key", F18, True), (0.1, "key", F18, False)]) == ["press"] and
      run(held, [(5, "key", F18, True), (7, "key", F18, False)]) == ["press", "release_held"])


class FakeHid(object):
    """hidutil, as far as remap_mic_key uses it."""
    def __init__(self, pairs=(), fail=False):
        self.pairs, self.fail, self.sets = list(pairs), fail, []

    def __call__(self, *args):
        import json as _j
        R = type("R", (), {})
        r = R()
        r.returncode = 1 if self.fail else 0
        if args[:2] == ("property", "--get"):
            r.stdout = "(null)\n" if not self.pairs else "(\n" + "".join(
                "        {\n        HIDKeyboardModifierMappingDst = %d;\n"
                "        HIDKeyboardModifierMappingSrc = %d;\n    }\n" % (b, a) for a, b in self.pairs) + ")\n"
        else:
            body = _j.loads(args[2])["UserKeyMapping"]
            self.sets.append(body)
            if not self.fail:
                self.pairs = [(m["HIDKeyboardModifierMappingSrc"], m["HIDKeyboardModifierMappingDst"]) for m in body]
            r.stdout = ""
        return r


CAPS_TO_ESC = (0x700000039, 0x700000029)
MINE = (d.MIC_USAGE, d.F18_USAGE)
h = FakeHid()
check("remap: 🎤 sent as F18 when there were no remaps", da.remap_mic_key(True, h) and h.pairs == [MINE], h.pairs)
check("  set once: asking again changes nothing", da.remap_mic_key(True, h) and len(h.sets) == 1, h.sets)
check("  and taken off again", da.remap_mic_key(False, h) and h.pairs == [], h.pairs)
h = FakeHid([CAPS_TO_ESC])
check("remap: someone else's remap is kept", da.remap_mic_key(True, h) and h.pairs == [CAPS_TO_ESC, MINE], h.pairs)
check("  when adding ours and when removing it", da.remap_mic_key(False, h) and h.pairs == [CAPS_TO_ESC], h.pairs)
h = FakeHid([(d.MIC_USAGE, 0x700000044)])
check("remap: a different 🎤 remap is replaced by ours", da.remap_mic_key(True, h) and h.pairs == [MINE], h.pairs)
check("remap: hidutil failing says so", da.remap_mic_key(True, FakeHid(fail=True)) is False)
check("read_mapping parses hidutil's output", da.read_mapping(FakeHid([CAPS_TO_ESC, MINE])) == [CAPS_TO_ESC, MINE])

# ---------------------------------------------------------------- when macOS keeps keyboard input private
import plistlib as _pl  # noqa: E402
ioreg = lambda users: (lambda args, **kw: types.SimpleNamespace(stdout=_pl.dumps({"IOConsoleUsers": users})))
check("secure input: who holds it, from ioreg's console users",
      da.secure_input_holder(ioreg([{"kCGSSessionOnConsoleKey": True, "kCGSSessionSecureInputPID": 611}])) == 611)
check("  nobody, when no one does", da.secure_input_holder(ioreg([{"kCGSSessionOnConsoleKey": True}])) is None)
check("  and ioreg failing is nobody, not a crash",
      da.secure_input_holder(lambda args, **kw: types.SimpleNamespace(stdout=b"not a plist")) is None)
what, fix = da.secure_input_words("loginwindow")
check("loginwindow (stuck after a restart or wake): a password prompt to finish, or log out and back in",
      "log out and back in" in fix and "password prompt" in fix and "can't be heard" in what
      and "Lock" not in fix, fix)
what, fix = da.secure_input_words("Terminal")
check("  another app: named, and what to do in it", what.startswith("Terminal is keeping") and "Secure Keyboard Entry" in fix)
check("  all of it ASCII: it goes to logs under launchd", all(ord(ch) < 128 for ch in "".join(
    da.secure_input_words("loginwindow") + da.secure_input_words(None))))
ok_perm = {"listen": True, "post": True, "mic": "allowed"}
check("the menu's first line says so while it lasts",
      d.menu_head(ok_perm, "mickey", {"on": True, "heard": False}).startswith("Can't hear the shortcut")
      and d.menu_head(ok_perm, "mickey", {"on": True, "heard": True}) == "Ready: press F5"
      and d.menu_head(ok_perm, "mickey") == "Ready: press F5")
check("  Input Monitoring missing still comes first", d.menu_head({"listen": False}, "rcommand2", {"on": True})
      == "Needs Input Monitoring to see the shortcut")


# ---------------------------------------------------------------- a shortcut of the user's own
plist = root / "symbolichotkeys.plist"
plist.write_bytes(_pl.dumps({"AppleSymbolicHotKeys": {
    "64": {"enabled": False, "value": {"parameters": [32, 49, 1048576], "type": "standard"}},      # Spotlight off
    "60": {"enabled": True, "value": {"parameters": [32, 49, 786432], "type": "standard"}},        # moved to ⌃⌥ Space
    "999": {"enabled": True, "value": {"parameters": [100, 2, 1179648], "type": "standard"}}}}))  # someone's ⌘⇧ D
taken = d.macos_taken(plist)
check("macOS's shortcuts: its defaults, as the user left them",
      not any(k == 49 and m == {"meta"} for m, k, _w in taken)                       # Spotlight turned off
      and ({"ctrl", "alt"}, 49, "switches the input source") in taken                 # moved, and named
      and not any(k == 49 and m == {"ctrl"} for m, k, _w in taken)                    # its old place is free
      and ({"meta", "shift"}, 2, "is one of macOS's shortcuts") in taken              # one it has no name for
      and any(w == "takes a screenshot" for _m, _k, w in taken), taken)
check("  an unreadable list is macOS's defaults, never a crash", any(
    w == "is Spotlight's" for _m, _k, w in d.macos_taken(root / "nope.plist")))
J = lambda code, key, *mods: d.judge_shortcut({"code": code, "key": key, "mods": list(mods)}, taken)
ok = J("Space", " ", "alt")
check("judge: ⌥ Space is fine, as a hotkey with Carbon's Option bit",
      ok["ok"] and ok["custom"] == {"kind": "combo", "keycode": 49, "carbon": 0x0800, "mods": ["alt"],
                                    "label": "⌥ Space", "ascii": "Option+Space"}, ok)
check("  ⌘ Space is free here (Spotlight is off), ⌃⌥ Space is not (input source moved there)",
      J("Space", " ", "meta")["ok"] and J("Space", " ", "ctrl", "alt")["why"] == "⌃⌥ Space switches the input source")
REFUSED = ((("KeyQ", "q", "meta"), "⌘ Q is an app shortcut everywhere"),
                  (("KeyD", "D", "shift"), "⇧ D types a capital"),
                  (("KeyE", "e", "alt"), "⌥ E types a special character"),
                  (("KeyA", "a"), "Needs a modifier: a key alone would type"),
                  (("Escape", ""), "Esc cancels: press another key"),
                  (("AltRight", ""), "Pick a key with the modifiers"),
                  (("Fn", ""), "That key can't be used"),
                  (("Digit3", "#", "meta", "shift"), "⇧⌘ # is an app shortcut everywhere"),
                  (("KeyQ", "q", "ctrl", "meta"), "⌃⌘ Q locks the Mac"),
                  (("Tab", "", "meta"), "⌘ Tab switches apps"))
for spec, why in REFUSED:
    check("  refused: %s" % why, J(*spec).get("why") == why, J(*spec))
check("  F6 alone is fine; the labels follow the keyboard's own letters (AZERTY's A is on Q)",
      J("F6", "")["ok"] and J("KeyQ", "a", "ctrl", "alt")["custom"]["label"] == "⌃⌥ A")
dbl = d.judge_shortcut({"double": "AltRight"})
check("  a modifier twice: its key, in words", dbl["ok"] and dbl["custom"] == {
    "kind": "double", "keys": [61], "label": "Right Option twice", "ascii": "AltRight twice"}, dbl)
check("  not a modifier, not a shortcut", not d.judge_shortcut({"double": "KeyA"})["ok"]
      and not d.judge_shortcut("⌥ Space")["ok"])
check("  every reason short enough for one line under the field",
      all(len(why) <= 60 for _spec, why in REFUSED) and len(d.judge_shortcut({"double": "x"})["why"]) <= 60)
mine = dict(d.DEFAULTS, shortcut="custom", custom=ok["custom"])
check("labels: a recorded one in words, and in ASCII for the log",
      d.shortcut_label(mine) == "Press ⌥ Space" and d.shortcut_kind(mine) == "combo"
      and d.shortcut_ascii(mine) == "your own, Option+Space" and all(ord(c) < 128 for c in d.shortcut_ascii(mine)))
check("  its gesture: the hotkey's press and release, hold to talk included",
      isinstance(d.make_gesture(mine), d.KeyPress) and d.make_gesture(mine).keys == {d.HOTKEY}
      and isinstance(d.make_gesture(dict(d.DEFAULTS, shortcut="custom", custom=dbl["custom"], hold=True)), d.Gesture))
check("  a hotkey needs no Input Monitoring, and private input does not stop it",
      d.menu_head({"listen": False, "post": True, "mic": "allowed"}, mine, {"on": True}) == "Ready: press ⌥ Space")
check("  a broken saved one falls back to the 🎤 key", not d.valid_custom({"kind": "combo"})
      and not d.valid_custom({"kind": "double", "keys": [999], "label": "x"}))


# ---------------------------------------------------------------- where the text goes
class AXWorld(object):
    """A made-up desktop for Focus: Notes (pid 101: field 11 in window 12) and a
    dialog (pid 202: field 21 in window 22). Accessibility's calls, as Focus makes
    them through ctypes, read and change it; NSWorkspace and NSRunningApplication too."""
    INVALID = da.Focus.INVALID

    def __init__(self):
        self.front, self.focused, self.trusted, self.obey = 101, 11, True, True
        self.owner = {11: 101, 12: 101, 21: 202, 22: 202}
        self.window_of = {11: 12, 21: 22}
        self.bundles = {101: "com.apple.Notes", 202: "com.example.dialog"}
        self.subrole = {11: "AXTextArea", 21: "AXTextField", 12: "AXStandardWindow", 22: "AXDialog"}
        self.gone, self.quit, self.calls, self.released, self.strings = set(), set(), [], [], {}

    # ApplicationServices
    def AXIsProcessTrusted(self): return self.trusted
    def AXUIElementCreateSystemWide(self): return 1
    def AXUIElementCreateApplication(self, pid): return 1000 + pid
    def AXUIElementSetMessagingTimeout(self, el, t): return 0
    def _out(self, ref, value): ref._obj.value = value

    def AXUIElementCopyAttributeValue(self, el, attr, out):
        if el in self.gone:
            return self.INVALID
        if el == 1 and attr == "AXFocusedUIElement":
            value = self.focused
        elif attr == "AXWindow":
            value = self.window_of.get(el)
        elif attr == "AXFocusedWindow" and el > 1000:
            value = next((w for w in self.window_of.values() if self.owner.get(w) == el - 1000), None)
        elif attr == "AXSubrole" and el in self.subrole:
            value = 900 + el
            self.strings[value] = self.subrole[el]
        else:
            return -25212                                   # no value
        self._out(out, value)
        return 0 if value else -25212

    def AXUIElementSetAttributeValue(self, el, attr, value):
        self.calls.append((attr, el))
        if el in self.gone:
            return self.INVALID
        if self.obey and attr == "AXFrontmost":
            self.front = el - 1000
        if self.obey and attr == "AXFocused":
            self.focused = el
        return 0

    def AXUIElementPerformAction(self, el, action):
        self.calls.append((action, el))
        return 0

    def AXUIElementGetPid(self, el, out):
        if el not in self.owner:
            return self.INVALID
        self._out(out, self.owner[el])
        return 0

    # CoreFoundation
    def CFEqual(self, a, b): return a == b
    def CFRelease(self, ref): self.released.append(ref)
    def CFGetTypeID(self, ref): return 7 if ref in self.strings else 1
    def CFStringGetTypeID(self): return 7
    def CFStringGetCString(self, ref, buf, n, enc):
        buf.value = self.strings[ref].encode()
        return True


class FakeApp(object):
    def __init__(self, world, pid): self.world, self.pid = world, pid
    def isTerminated(self): return self.pid in self.world.quit
    def activateWithOptions_(self, opts): self.world.calls.append(("activate", self.pid))


class WorldFocus(da.Focus):
    """The real Focus, on the made-up desktop."""
    WAIT = 0.1

    def __init__(self, world):
        da.Focus.__init__(self)
        self.world = world
        self._libs = (world, world, {a: a for a in self.ATTRS}, 1, "true")

    def _front(self): return self.world.front, self.world.bundles.get(self.world.front, "")
    def _app(self, pid): return None if pid in self.world.quit and pid not in self.world.owner.values() else FakeApp(self.world, pid)


w = AXWorld()
f = WorldFocus(w)
t = f.now()
check("focus: where a dictation starts, the app, its field and that field's window",
      (t.pid, t.bundle, t.element, t.window, t.secure) == (101, "com.apple.Notes", 11, 12, False), t)
check("  the same place a moment later is the same", f.same(f.now(), t))
w.front, w.focused = 202, 21
check("  a dialog in front, its own field focused: not the same", not f.same(f.now(), t))
ok = f.restore(t)
check("brought back: the app frontmost, its window raised and made main, the field focused, and confirmed",
      ok == (True, "") and (w.front, w.focused) == (101, 11) and ("activate", 101) in w.calls
      and ("AXRaise", 12) in w.calls and ("AXMain", 12) in w.calls and ("AXFocused", 11) in w.calls, (ok, w.calls))
w.front, w.focused, w.obey = 202, 21, False
check("  it will not come back in time: said so, not pretended", f.restore(t) == (False, "moved"))
w.obey = True
w.gone.add(11)
before = len(w.calls)
check("the field gone (its window closed): gone, and nothing is set on it", f.restore(t) == (False, "gone")
      and w.calls[before:] == [], w.calls[before:])
w.gone.discard(11)
w.quit.add(101)
w.owner = {k: v for k, v in w.owner.items() if v != 101}
check("  the app quit: gone", f.restore(Target_quit := da.Target(101, "com.apple.Notes", 11, 12)) == (False, "gone"))
w = AXWorld()
f = WorldFocus(w)
w.subrole[11] = "AXSecureTextField"
check("a password field is known as one", f.now().secure is True)
w = AXWorld()
f = WorldFocus(w)
w.owner[11] = 303                                         # the focused element is another app's overlay
check("  an element that is not the front app's is not its field: only the app and its window",
      (f.now().element, f.now().window) == (None, 12))
w = AXWorld()
f = WorldFocus(w)
w.trusted = False
check("no Accessibility: nothing known, and the text goes where the focus is, as before", f.now() is None)
w.trusted = True
t = f.now()
f.release(t)
check("what it held is let go", 11 in w.released and 12 in w.released and t.element is None and t.window is None)


class FakeCarbon(object):
    """Carbon's hotkey calls CarbonHotKey makes, recorded; never the real ones in a test."""
    def __init__(self, taken=()):
        self.calls, self.taken, self.kind = [], set(taken), 5
    def GetEventDispatcherTarget(self): return 77
    def InstallEventHandler(self, target, cb, n, kinds, data, out):
        self.calls.append(("handler", n, [(k.eventClass, k.eventKind) for k in kinds[:n]])); return 0
    def RegisterEventHotKey(self, key, mods, hkid, target, opts, out):
        self.calls.append(("register", key, mods))
        return -9878 if (key, mods) in self.taken else 0
    def UnregisterEventHotKey(self, ref): self.calls.append("unregister"); return 0
    def GetEventKind(self, event): return self.kind
    def GetEventTime(self, event): return getattr(self, "when", 0.0)


real_lib = da.CarbonHotKey._lib
try:
    lib = FakeCarbon(taken={(49, 0x0800)})
    da.CarbonHotKey._lib = staticmethod(lambda: lib)
    edges = []
    hk = da.CarbonHotKey(lambda down, when: edges.append((down, when)))
    check("hotkey: registered, with one handler for press and release",
          hk.register(79, 0) == (True, "") and hk.keys == (79, 0) and lib.calls[0] ==
          ("handler", 2, [(hk.KEYBOARD, 5), (hk.KEYBOARD, 6)]) and lib.calls[1] == ("register", 79, 0), lib.calls)
    check("  asked again for the same keys: nothing to do", hk.register(79, 0) == (True, "") and len(lib.calls) == 2)
    check("  the handler and its C callback are kept, so a press can never call freed memory",
          hk.handler is not None and hk.callback is not None)
    lib.kind, lib.when = 5, 1234.5
    hk._event(None, 1, None)
    lib.kind, lib.when = 6, 1234.6
    hk._event(None, 1, None)
    check("  a press and a release come back as edges, with when each happened, and count as handled",
          edges == [(True, 1234.5), (False, 1234.6)] and hk._event(None, 1, None) == 0, edges)
    check("other keys in their place: the old ones let go first",
          hk.register(2, 0x1800) == (True, "") and lib.calls[-2:] == ["unregister", ("register", 2, 0x1800)])
    check("  keys another app already uses: refused, and said so",
          hk.register(49, 0x0800) == (False, "another app already uses it") and hk.keys is None)
    check("  only one handler, whatever happens", sum(1 for c in lib.calls if c[0] == "handler") == 1)
    gone = lib.calls.count("unregister")
    hk.unregister()
    hk.unregister()
    check("  letting go with nothing held is harmless", hk.keys is None and lib.calls.count("unregister") == gone)
finally:
    da.CarbonHotKey._lib = real_lib

# ---------------------------------------------------------------- word fixes
wf = d.WordFixes(root / "words.txt")
check("word fixes: default file fixes Claude Code", wf.apply("Ask Cloud Code, then cloud  code.") ==
      "Ask Claude Code, then Claude Code.", wf.apply("Ask Cloud Code, then cloud  code."))
check("  and leaves other words alone", wf.apply("cloudy code and the cloud") == "cloudy code and the cloud")
check("  the file explains itself", "what it hears => what you meant" in (root / "words.txt").read_text())
time.sleep(0.01)
(root / "words.txt").write_text("get hub => GitHub\nkate => git\nback => C:\\new\n")
os.utime(root / "words.txt", (time.time() + 5, time.time() + 5))
check("  edits apply to the next dictation", wf.apply("push to get hub with Kate") == "push to GitHub with git")
check("  replacements are literal (no regex escapes)", wf.apply("go back") == "go C:\\new")

# ---------------------------------------------------------------- meeting guard
mg = d.MeetingGuard(root)
mg.begin(); mg.end()
check("guard: no meeting, nothing written", not list(root.glob("sessions/*/dictate")))
sdir = root / "sessions" / "meeting-1"; sdir.mkdir(parents=True)
(root / "daemon.ready").write_text("meeting-1\n")
mg.begin()
on = (sdir / "dictate").read_text().split()
check("guard: a meeting's recorder is told to keep it out", on[0] == "on" and on[1] == str(mg.seq), on)
mg.end()
check("  and told when it is over", (sdir / "dictate").read_text().split() == ["off", str(mg.seq)])
(sdir / "dictate").write_text("on 42\n")
mg.begin(); mg.end()
check("guard: leaves the chat box's dictation alone", (sdir / "dictate").read_text() == "on 42\n")
for held in ("voice 43\n", "hold 43\n", "floor 43\n"):          # floor: Claude speaking
    (sdir / "dictate").write_text(held)
    mg.begin()
    told = (sdir / "dictating").read_text().split() if (sdir / "dictating").exists() else None
    mg.end()
    check("guard: leaves voice mode's switch alone (%s)" % held.split()[0], (sdir / "dictate").read_text() == held)
    check("  but tells it we are dictating, so it holds, until done",
          told == [str(os.getpid())] and not (sdir / "dictating").exists(), told)
check("  where the call's mic is lent, while voice mode is told: beside the told file",
      (lambda g: (g.begin(), g.lent(), g.end())[1])(d.MeetingGuard(root)) == sdir / "dictating.f32")
(sdir / "dictate").write_text("on 99\n")
g = d.MeetingGuard(root)
g.begin()
check("  and none when it isn't (the chat box's dictation, or no call)", g.lent() is None)
g.end()

# During a call the mic is the call's (macOS gives a second reader near silence): the call's own,
# lent by the recorder in dictating.f32, is read instead, from its last second on.
cap = d.Capture()
lent = sdir / "dictating.f32"
cap.blocks = [np.zeros(1600, "float32")]                 # what the device gave meanwhile: near silence
cap.borrow(lent)
said = (np.sin(np.arange(32000) / 8.0) * 0.2).astype("float32")
raw = said.tobytes()


def recorder_writes():
    with open(lent, "wb") as fh:
        for i in range(0, len(raw), 1601):               # odd sizes: a sample split across writes
            fh.write(raw[i:i + 1601])
            fh.flush()
            time.sleep(0.005)


w = threading.Thread(target=recorder_writes)
w.start()
time.sleep(0.2)
w.join()
got = cap.stop()
check("dictation in a call: the call's own mic, every sample, the device's set aside",
      len(got) == len(said) and np.array_equal(got, said) and cap.in_call and cap.from_call, (len(got), len(said)))
check("  and the file is gone (the user's words on disk only while dictating)", not lent.exists())
cap = d.Capture()
cap.blocks = [np.zeros(1600, "float32")]
cap.borrow(lent)
got = cap.stop()
check("  a recorder that lends nothing (from before this): the device's, and it can say why it heard nothing",
      len(got) == 1600 and cap.in_call and not cap.from_call
      and d.CALL_HAS_MIC == "A call has the mic, so dictation couldn't hear you")
cap = d.Capture()
cap.borrow(None)
cap.stop()
check("  no call: nothing borrowed, nothing said of one", not cap.in_call and not cap.from_call)
(root / "daemon.ready").write_text("gone-session\n")
mg.begin(); mg.end()
check("guard: a stale session is harmless", True)
(root / "daemon.ready").unlink()

# ---------------------------------------------------------------- engine
got = []
loads = []


def fake_load(name):
    loads.append((name, threading.current_thread().name))
    return "MODEL"


def fake_decode(model, audio):
    if len(audio) == 1:
        raise ValueError("boom")
    return "%s heard %d samples on %s" % (model, len(audio), threading.current_thread().name)


e = d.Engine("m", unload_after=1.0, load=fake_load, transcribe=fake_decode)
e.start()
e.warm()
ev = threading.Event()
e.submit(np.zeros(16000, "float32"), lambda t, err: (got.append((t, err)), ev.set()))
ev.wait(10)
check("engine: loads once, on its own thread", loads == [("m", "asr")], loads)
check("engine: decodes on that same thread (MLX needs it)", got and got[0] == ("MODEL heard 16000 samples on asr", None), got)
ev.clear()
e.submit(np.zeros(1, "float32"), lambda t, err: (got.append((t, err)), ev.set()))
ev.wait(10)
check("engine: a failed decode reports an error", got[-1][0] is None and "boom" in got[-1][1], got[-1])
deadline = time.time() + 8
while e.model is not None and time.time() < deadline:
    time.sleep(0.2)
check("engine: drops the model when idle", e.model is None)
ev.clear()
e.submit(np.zeros(8, "float32"), lambda t, err: (got.append((t, err)), ev.set()))
ev.wait(10)
check("  and loads it again when needed", len(loads) == 2 and got[-1][1] is None, loads)
bad = d.Engine("m", load=lambda n: (_ for _ in ()).throw(RuntimeError("no weights")), transcribe=fake_decode)
bad.start()
ev.clear()
bad.submit(np.zeros(8, "float32"), lambda t, err: (got.append((t, err)), ev.set()))
ev.wait(10)
check("engine: a model that will not load reports it", got[-1][0] is None and "no weights" in got[-1][1], got[-1])
for eng in (e, bad):
    eng.stop()
    eng.join(10)
check("engine: stop ends its thread", not e.is_alive() and not bad.is_alive())

# ---------------------------------------------------------------- through the recorder
# While a meeting records, its recorder decodes dictation with its own model
# (standby.serve_decodes on its main loop): one copy in memory, not two.
_hf_was = os.environ.get("HF_HUB_CACHE")
os.environ["HF_HUB_CACHE"] = tempfile.mkdtemp(prefix="sbhf-")       # no model here: Settings' choice by its id
import speechmodel  # noqa: E402
import standby as sbrec  # noqa: E402
sbrec.log = lambda m: None
sid = "meeting-20261009-120000"
sess = root / "sessions" / sid
(sess / "decode").mkdir(parents=True)


def live(fresh=True, decodes=True):
    (root / "daemon.ready").write_text(sid + "\n")
    st = {"at": time.time() - (0 if fresh else 30), "speaking": False}
    if decodes:
        st["decodes"] = True
    tmp = sess / ("live.%d.tmp" % threading.get_ident())     # as the recorder writes it: whole, never half
    tmp.write_text(json.dumps(st))
    tmp.replace(sess / "live.json")


class Recorder(threading.Thread):
    """The recorder's main loop, as far as dictation sees it: live.json kept fresh, and
    serve_decodes each turn with its model, on its own thread."""

    def __init__(self, decode=None):
        threading.Thread.__init__(self, daemon=True)
        self.halt, self.decode, self.perms = threading.Event(), decode, []

    def run(self):
        while not self.halt.is_set():
            live()
            for n in os.listdir(str(sess / "decode")):
                if n.endswith(".f32"):
                    self.perms.append(os.stat(str(sess / "decode" / n)).st_mode & 0o777)
            sbrec.serve_decodes(sess / "decode", "RECORDER'S", decode=self.decode or fake_decode)
            time.sleep(0.05)

    def stop(self):
        self.halt.set()
        self.join(5)


def say(eng, audio):
    ev.clear()
    eng.submit(audio, lambda t, err: (got.append((t, err)), ev.set()))
    ev.wait(20)
    return got[-1]


def leftovers():
    return sorted(os.listdir(str(sess / "decode")))


loads[:] = []
rec = Recorder()
rec.start()
live()
eng = d.Engine(None, load=fake_load, transcribe=fake_decode)
eng.start()
eng.warm()
text, err = say(eng, np.zeros(16000, "float32"))
check("a meeting records: the recorder decodes it, with its model", text == "RECORDER'S heard 16000 samples on %s"
      % rec.name and err is None, (text, err))
check("  no model loaded here, not even to warm up", loads == [] and eng.model is None, loads)
check("  the recording passed this user's only, and nothing left behind",
      rec.perms and set(rec.perms) == {0o600} and leftovers() == [], (rec.perms, leftovers()))
rec.stop()
(root / "daemon.ready").unlink()
text, err = say(eng, np.zeros(16000, "float32"))
check("no meeting: decoded here, loading the model Settings > Speech chose",
      text == "MODEL heard 16000 samples on asr" and [n for n, _ in loads] == [speechmodel.MODELS[speechmodel.chosen({})][0]],
      (text, loads))
rec = Recorder()
rec.start()
live()
text, err = say(eng, np.zeros(800, "float32"))
check("then a meeting: the recorder's, and the copy here is let go", text.startswith("RECORDER'S")
      and eng.model is None, (text, eng.model))
rec.stop()
live()                                                     # live.json fresh, but no one takes it
eng.recorder.TAKE_WAIT = 0.4
t0 = time.time()
text, err = say(eng, np.zeros(800, "float32"))
check("a recorder that doesn't take it: decoded here after a short wait, and taken back",
      text.startswith("MODEL") and time.time() - t0 < 5 and leftovers() == [], (text, leftovers()))
live(fresh=False)
t0 = time.time()
text, err = say(eng, np.zeros(800, "float32"))
check("a recorder gone quiet (live.json old): here at once", text.startswith("MODEL") and time.time() - t0 < 0.3
      and leftovers() == [], time.time() - t0)
live(decodes=False)
check("  an older recorder that doesn't decode for dictation: here", d.RecorderDecode(root).folder() is None)
live()
check("  and one that does", d.RecorderDecode(root).folder() == sess / "decode")


def failing(model, audio):
    raise RuntimeError("GPU busy")


rec = Recorder(decode=failing)
rec.start()
text, err = say(eng, np.zeros(800, "float32"))
check("its decode failed: decoded here instead, its answer cleared", text.startswith("MODEL")
      and leftovers() == [], (text, leftovers()))
rec.stop()
rec = Recorder()
rec.start()
eng.recorder.MAX_SECONDS = 0.04
text, err = say(eng, np.zeros(800, "float32"))
check("a long dictation decodes here, not to hold the meeting's own lines up", text.startswith("MODEL"), text)
eng.recorder.MAX_SECONDS = d.RecorderDecode.MAX_SECONDS
own = d.Engine("someone/their-model", load=fake_load, transcribe=fake_decode)
own.start()
text, err = say(own, np.zeros(800, "float32"))
check("a model set by hand for dictation: always its own", text.startswith("MODEL") and loads[-1][0] == "someone/their-model",
      (text, loads[-1]))
rec.stop()
for x in (eng, own):
    x.stop()
    x.join(10)
(root / "daemon.ready").unlink()
shutil.rmtree(str(root / "sessions"))
fdir = Path(tempfile.mkdtemp())
np.zeros(4, "<f4").tofile(str(fdir / "1-1.f32"))
check("serve_decodes: a decode that fails is answered as one",
      sbrec.serve_decodes(fdir, None, decode=failing) == 1
      and json.loads((fdir / "1-1.json").read_text()) == {"error": "GPU busy"} and not (fdir / "1-1.f32").exists())
check("  a folder that isn't there: nothing", sbrec.serve_decodes(fdir / "gone", None) == 0)
if _hf_was is None:                     # the real decode below looks where the model really is
    os.environ.pop("HF_HUB_CACHE")
else:
    os.environ["HF_HUB_CACHE"] = _hf_was

check("silence and blips are not decoded", not d.heard_something(np.zeros(16000, "float32"))
      and not d.heard_something(np.full(1000, 0.1, "float32"))
      and d.heard_something((np.random.RandomState(0).randn(16000) * 0.05).astype("float32")))
check("level meter: quiet is empty, loud is full", d.meter(0.0) == 0 and d.meter(1e-4) == 0 and d.meter(0.5) == 1.0
      and 0.3 < d.meter(0.01) < 0.5)

# ---------------------------------------------------------------- no locale
# launchd and Finder start the app with ASCII stdio: a "🎤" in a log line or the
# status killed it at launch ("Launch error" from py2app).
r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import dictation as d; "
                    "d.log('the 🎤 key'); sys.exit(d.main(['status']))" % str(Path(d.__file__).parent)],
                   env={"HOME": str(root), "PATH": "/usr/bin:/bin", "LC_ALL": "C", "PYTHONIOENCODING": "ascii",
                        "STANDBY_ROOT": str(root), "STANDBY_LAUNCH_AGENTS": str(root / "LaunchAgents")},
                   capture_output=True, timeout=60)
# ...and files opened without an encoding (parakeet-mlx's config.json) read as UTF-8.
probe = root / "utf8.json"
probe.write_text('{"name": "caf\u00e9 \u2013 ok"}', encoding="utf-8")
r2 = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, %r); import dictation; "
                     "print(json.load(open(%r))['name'] == 'caf\u00e9 \u2013 ok')" % (str(Path(d.__file__).parent), str(probe))],
                    env={"HOME": str(root), "PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C",
                         "STANDBY_ROOT": str(root)}, capture_output=True, timeout=60)
check("no locale: files open as UTF-8 (the speech model's config)", r2.stdout.strip() == b"True",
      (r2.returncode, r2.stdout[-100:], r2.stderr[-300:]))
check("no locale: logs and status with 🎤 don't raise", r.returncode == 0 and "🎤".encode() in r.stderr
      and b"UnicodeEncodeError" not in r.stderr + r.stdout, (r.returncode, r.stderr[-300:]))

# ---------------------------------------------------------------- login item
from native.macos import service as mac_service  # noqa: E402
mac_service.AGENTS = root / "LaunchAgents"            # never the user's own login items
PLIST = mac_service.AGENTS / (d.LABEL + ".plist")
d.APP = root / "Standby Dictation.app"
check("login item: not set to start at login before it's written", not d.at_login())
d.write_agent()
import plistlib  # noqa: E402
pl = plistlib.load(open(PLIST, "rb"))
check("login item: starts at login, restarts after a crash, not after Quit",
      pl["RunAtLoad"] is True and pl["KeepAlive"] == {"SuccessfulExit": False} and pl["Label"] == d.LABEL, pl)
check("  runs the script when the app is not built", pl["ProgramArguments"][-2:] == [str(d.HERE / "dictation.py"), "run"])
exe = d.APP / "Contents" / "MacOS" / "Standby Dictation"
exe.parent.mkdir(parents=True); exe.write_text("")
d.write_agent()
check("  and the app when it is", plistlib.load(open(PLIST, "rb"))["ProgramArguments"] == [str(exe)] and d.at_login())

holder = subprocess.Popen([sys.executable, "-c", """
import fcntl, sys, time
fh = open(sys.argv[1], "a+"); fcntl.flock(fh, fcntl.LOCK_EX); fh.seek(0); fh.truncate()
fh.write("4242\\n"); fh.flush(); print("held", flush=True); time.sleep(30)
""", str(d.LOCK)], stdout=subprocess.PIPE, text=True)
holder.stdout.readline()
check("running_pid: the lock holder is running", d.running_pid() == 4242, d.running_pid())
holder.kill(); holder.wait()
check("  and nobody once it exits", d.running_pid() is None)

# ---------------------------------------------------------------- running the current code
files = d.code_files()
check("code files: dictation.py, what it imports from the repo, and the speech model and mics (loaded later)",
      str(d.HERE / "dictation.py") in files and str(d.HERE / "config.py") in files
      and str(d.HERE / "speechmodel.py") in files and str(d.HERE / "devices.py") in files
      and not any("numpy" in f for f in files), files)
# dictation as launchd starts it: a fresh python, nothing else of the repo imported first
import subprocess  # noqa: E402
fresh = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import dictation, speechmodel, devices; "
                        "print(sorted(m for m in sys.modules if m in ('standby', 'transport', 'sbclaude')))" % str(d.HERE)],
                       capture_output=True, text=True, timeout=120, env=dict(os.environ, STANDBY_NO_DEVICES="1"))
check("  never the recorder or the code that reaches Claude Code: a change there can't stop dictation",
      fresh.returncode == 0 and fresh.stdout.strip().splitlines()[-1:] == ["[]"], (fresh.stdout + fresh.stderr)[-400:])

src = root / "code"; src.mkdir()
a, b = src / "a.py", src / "b.py"
a.write_text("A = 1\n"); b.write_text("B = 1\n")
listed = [str(a), str(b)]
verdicts, asked = [(True, "")], []


def verify():
    asked.append(1)
    return verdicts[-1]


w = d.CodeWatch(files=lambda: list(listed), verify=verify)
check("watch: nothing changed, nothing to do", not w.check(100.0) and w.changed == () and not asked)
a.write_text("A = 2\n")
check("watch: a change is not acted on at once (a merge writes files one by one)",
      not w.check(101.0) and not w.check(102.0) and not asked)
check("  once it settles and the new code imports: ready", w.check(103.5) and asked == [1] and w.names() == "a.py",
      (w.ready, asked, w.names()))
check("  checked once, not on every look", w.check(104.0) and w.check(110.0) and asked == [1])
os.utime(a, (time.time() + 9, time.time() + 9))
check("watch: a touch without new content is no change", w.check(111.0) and w.changed[0][0] == str(a))
a.write_text("A = 1\n")
check("watch: put back as it was, nothing to restart for", not w.check(112.0) and w.changed == ())
verdicts.append((False, "SyntaxError: invalid syntax"))
b.write_text("B = (\n")
w.check(120.0)
check("watch: new code that does not import is not used, and says why",
      not w.check(123.0) and w.problem == "SyntaxError: invalid syntax", (w.ready, w.problem))
check("  asked once for that version", not w.check(130.0) and len(asked) == 2, asked)
verdicts.append((True, ""))
b.write_text("B = 2\n")
check("  fixed: the problem clears while it settles", not w.check(131.0) and w.problem is None)
check("  then it is ready", w.check(134.0) and len(asked) == 3)
c_py = src / "c.py"; c_py.write_text("C = 1\n")
listed.append(str(c_py))
check("watch: a module first seen later counts as loaded, no restart for it",
      w.check(135.0) and w.names() == "b.py", w.names())
b.unlink()
w.check(140.0)
check("watch: a deleted file is a change too", w.changed and w.changed[0] == (str(b), None), w.changed)

ok, why = d.imports_ok()
check("imports_ok: this repo's code starts", ok, why)
bad_dir = root / "badcode"; bad_dir.mkdir()
(bad_dir / "dictation.py").write_text("def broken(:\n")
for name in ("speechmodel.py", "devices.py"):
    (bad_dir / name).write_text("")
ok, why = d.imports_ok(where=bad_dir)
check("  half-merged code does not, and says why", not ok and "SyntaxError" in why, why)
(bad_dir / "dictation.py").write_text("import a_module_nobody_installed\n")
ok, why = d.imports_ok(where=bad_dir)
check("  nor code that needs a dependency not installed yet", not ok and "a_module_nobody_installed" in why, why)

check("restart: never mid-dictation, mid-decode, or right after a paste",
      not d.safe_to_restart(True, 0, 0, 100) and not d.safe_to_restart(False, 1, 0, 100)
      and not d.safe_to_restart(False, 0, 99, 100) and d.safe_to_restart(False, 0, 90, 100))
check("  nor again right after the last restart, should the files keep changing",
      not d.safe_to_restart(False, 0, 0, 100, launched=80) and d.safe_to_restart(False, 0, 0, 100, launched=60))

# A crash at launch must not become a relaunch loop (launchd's KeepAlive).
t = 1000.0
check("launches: a first launch follows no failed ones", d.launch_begins(now=t) == 0 and d.launch_backoff(0) == 0)
d.launch_failed("ImportError: no module named x")
seen = [d.launch_begins(now=t + 10 * i) for i in (1, 2, 3)]
check("  each launch that never got going counts", seen == [1, 2, 3], seen)
check("  backing off, then giving up after a few",
      [d.launch_backoff(n) for n in (1, 2, 3)] == [5, 10, None])
check("  and says why it stopped", d.gave_up(now=t + 40) == "ImportError: no module named x", d.gave_up(now=t + 40))
check("  long ago is forgotten: the next login tries again",
      d.gave_up(now=t + 40 + d.CRASH_WINDOW + 1) is None and d.launch_begins(now=t + 40 + d.CRASH_WINDOW + 1) == 0)
d.launch_went_fine()
check("  a launch that runs a while clears it", not d.STARTING.exists() and d.launch_begins(now=t + 2000) == 0)
d.launch_went_fine()


check("relaunch under launchd: exit non-zero, KeepAlive starts the new code",
      d.relaunch_plan(77, True, "/Apps/Standby Dictation.app") == (d.RESTART_EXIT, None) and d.RESTART_EXIT != 0)
code, helper = d.relaunch_plan(77, False, "/Apps/Standby Dictation.app")
check("relaunch by hand: exit 0, relaunch.py opens the app (its permissions) once this copy is gone, until it is up",
      code == 0 and helper[1].endswith("relaunch.py") and helper[helper.index("--old") + 1] == "77"
      and helper[helper.index("--lock") + 1] == str(d.LOCK) and helper[helper.index("--job") + 1] == d.LABEL
      and helper[-4:] == ["--", "/usr/bin/open", "-g", "/Apps/Standby Dictation.app"], helper)
code, helper = d.relaunch_plan(77, False, None, python="/venv/python")
check("  no app: the script runs again, the helper on the same python",
      code == 0 and helper[0] == "/venv/python" and helper[-3:] == ["/venv/python", str(d.HERE / "dictation.py"), "run"],
      helper)

d.note_restart(True)
note = d.take_restart_note()
check("restart note: the next copy knows it is a restart, and that setup was open",
      note and note["setup"] is True and not d.RESTART.exists(), note)
check("  a normal launch has none", d.take_restart_note() is None)
d.note_restart(False)
check("  and an old one is ignored", d.take_restart_note(now=time.time() + 600) is None)
d.COMMAND.write_text("setup\n")
check("command from Settings: read once", d.take_command() == ["setup"] and d.take_command() is None)
d.send_command("reload")
d.send_command("edit_words")
check("  two before the app looks: both, in order, neither written over",
      d.take_command() == ["reload", "edit_words"] and d.take_command() is None)

# ---------------------------------------------------------------- setup and status model
none_yet = {"listen": False, "post": False, "mic": "ask"}
rows = d.setup_rows(none_yet, "rcommand2")
check("setup: every permission, in the order the app asks",
      [r["key"] for r in rows] == ["mic", "listen", "post"] and d.missing(none_yet) == ["mic", "listen", "post"]
      and d.missing(none_yet, "rcommand2") == ["mic", "listen", "post"])
check("  F5 is a hotkey: no Input Monitoring asked for it, nor for a recorded combination (a VM run)",
      [r["key"] for r in d.setup_rows(none_yet)] == ["mic", "post"] and d.missing(none_yet, "mickey") == ["mic", "post"]
      and d.missing(none_yet, {"shortcut": "custom", "custom": {"kind": "combo", "keycode": 49, "carbon": 2048, "label": "⌥ Space"}}) == ["mic", "post"]
      and d.missing({"listen": False, "post": True, "mic": "allowed"}, d.DEFAULTS) == []
      and d.menu_head({"listen": False, "post": True, "mic": "allowed"}, "mickey") == "Ready: press F5", d.setup_rows(none_yet))
check("  the mic, not asked yet, by asking (macOS lists the app only after)",
      rows[0]["action"] == "ask" and rows[0]["button"] == "Allow", rows[0])
check("  the others from their System Settings pane",
      rows[1]["action"] == "open" and rows[1]["url"].endswith("Privacy_ListenEvent")
      and rows[2]["action"] == "open" and rows[2]["url"].endswith("Privacy_Accessibility")
      and rows[2]["button"] == "Open Settings…", rows)
check("  in plain words, naming the shortcut",
      rows[1]["why"] == "notices the shortcut" and rows[2]["why"] == "pastes the text", rows)
denied = d.setup_rows({"listen": True, "post": True, "mic": "denied"}, "rcommand2")
check("  a denied mic opens its pane", denied[0]["action"] == "open" and denied[0]["url"].endswith("Privacy_Microphone")
      and denied[1]["ok"] and denied[1]["button"] is None, denied)
check("  all granted: nothing missing", d.missing({"listen": True, "post": True, "mic": "allowed"}) == [])
check("  Accessibility missing: a second way, for a switch that is on but an older build's",
      rows[2]["alt"] == d.STALE_BUTTON and rows[0]["alt"] is None and rows[1]["alt"] is None
      and denied[2]["alt"] is None, rows)
check("the key is named F5 in words, never the mic emoji, wherever the app says it",
      all("🎤" not in t for t in [d.SHORTCUTS["mickey"][0], d.how_to_start("mickey"), d.ready_text("mickey"),
                                   d.menu_head({"listen": True, "post": True, "mic": "allowed"}, "mickey")]
          + [r["why"] for r in d.setup_rows(none_yet)]) and d.SHORTCUTS["mickey"][0] == "Press F5")
check("  no emoji in the new words", all(ord(ch) < 0x2190 for ch in d.STALE_BUTTON + d.STALE_HINT + d.stale_steps()
                                         + da.NO_POST.replace("⌘", "") + da.NO_POST_BUTTON))
check("first success: says what to press",
      d.ready_text("mickey") == "Dictation is ready. Press F5 in any app"
      and d.ready_text("rcommand2") == "Dictation is ready. Press Right Command twice in any app")
preview = (d.HERE.parent / "tools" / "dictation_preview.py").read_text()
check("  the panel's pictures for the website draw the app's own line, no emoji either",
      "d.ready_text(" in preview and "🎤" not in preview.split('"""', 2)[2].split("def huds", 1)[1].split("def ", 1)[0])
check("  when it just became ready, or the first time ever",
      d.should_welcome(True, True, True) and d.should_welcome(True, False, False)
      and not d.should_welcome(True, False, True) and not d.should_welcome(False, True, False))

# ---------------------------------------------------------------- macOS only
try:
    import AppKit  # noqa: F401
    mac = sys.platform == "darwin"
except ImportError:
    mac = False

if not mac:
    print("  (clipboard, controller and real-speech parts skipped: need macOS)")
else:
    from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString
    pb = NSPasteboard.pasteboardWithUniqueName()          # never the user's clipboard
    item = NSPasteboardItem.alloc().init()
    item.setString_forType_("ORIGINAL", NSPasteboardTypeString)
    item.setString_forType_("custom payload", "dev.standby.test")
    pb.clearContents(); pb.writeObjects_([item])
    seen, later = [], []

    def fake_post(kind, text=None):
        seen.append((kind, text, pb.stringForType_(NSPasteboardTypeString),
                     "org.nspasteboard.TransientType" in (pb.types() or [])))
    ins = da.Inserter(pasteboard=pb, post=fake_post, later=lambda secs, fn: later.append((secs, fn)))
    ins.insert("hello world ", "paste")
    check("paste: Cmd+V is sent while the clipboard holds the text", seen == [("paste", None, "hello world ", True)], seen)
    check("  marked transient, so clipboard managers skip it", seen and seen[0][3])
    later[0][1]()
    check("  then the clipboard is put back as it was",
          pb.stringForType_(NSPasteboardTypeString) == "ORIGINAL" and
          pb.stringForType_("dev.standby.test") == "custom payload")
    seen.clear(); later.clear()
    ins.insert("second ", "paste")
    pb.clearContents(); pb.setString_forType_("copied meanwhile", NSPasteboardTypeString)
    later[0][1]()
    check("  unless something new was copied in the meantime",
          pb.stringForType_(NSPasteboardTypeString) == "copied meanwhile")
    seen.clear()
    ins.insert("typed text ", "type")
    check("type: keystrokes, clipboard untouched", seen == [("type", "typed text ", "copied meanwhile", False)], seen)
    seen.clear(); later.clear()
    ins.insert_kept("private text ", "paste")
    check("kept (keyboard input private): Cmd+V sent, the text left on the clipboard, nothing put back",
          seen == [("paste", None, "private text ", False)] and later == []
          and pb.stringForType_(NSPasteboardTypeString) == "private text ", (seen, later))
    seen.clear()
    ins.insert_kept("typed private ", "type")
    check("  typed: the keystrokes, and the text on the clipboard as well",
          seen == [("type", "typed private ", "typed private ", False)], seen)
    empty = NSPasteboard.pasteboardWithUniqueName(); empty.clearContents()
    later.clear()
    da.Inserter(pasteboard=empty, post=lambda *a: None, later=lambda s, fn: later.append(fn)).insert("x", "paste")
    later[0]()
    check("  an empty clipboard stays empty", not (empty.pasteboardItems() or []))
    pb.releaseGlobally(); empty.releaseGlobally()

    # The controller, from synthetic key events to inserted text.
    import Quartz
    from AppKit import NSApplication
    # Prohibited: never in the Dock, never active, so a run can't take the user's
    # focus or flash an icon (the team runs this suite many times a day).
    NSApplication.sharedApplication().setActivationPolicy_(2)

    class FakeCapture(object):
        def __init__(self): self.on, self.lent, self.in_call, self.from_call = False, None, False, False
        def start(self, room=True): self.on, self.room = True, room
        def borrow(self, path): self.lent = path
        def stop(self):
            self.on = False
            return (np.random.RandomState(1).randn(16000) * 0.05).astype("float32")
        def take_level(self): return 0.02

    class FakeEngine(object):
        def __init__(self): self.warmed = 0; self.audio = None
        def warm(self): self.warmed += 1
        def submit(self, audio, done): self.audio = audio; done("Ask cloud code to run it.", None)

    class FakeHUD(object):
        def __init__(self): self.shown, self.note = [], None
        def show(self, text, level=False, spin=False, front=True): self.note = None; self.shown.append(text)
        def show_note(self, key, text, button, target, on_button, on_close):
            self.note, self.button = key, (button, on_button, on_close)
            self.shown.append(text)
        def set_level(self, v): pass
        def hide(self): self.note = None; self.shown.append(None)

    class FakeInserter(object):
        def __init__(self): self.got = []
        def insert(self, text, how="paste"): self.got.append(("insert", text, how))
        def put(self, text, transient=False): self.got.append(("put", text))
        def insert_kept(self, text, how="paste"): self.got.append(("kept", text, how))
        def press_return(self): self.got.append(("return",))

    class FakeGuard(object):
        def __init__(self): self.calls = []
        def begin(self): self.calls.append("begin")
        def end(self): self.calls.append("end")
        def lent(self): return None

    d.save_setting("sounds", False)
    d.save_setting("shortcut", "control2")
    check("a Control twice saved before: F5 instead", d.settings()["shortcut"] == "mickey")
    d.save_setting("shortcut", "rcommand2")
    c = da.Controller.alloc().initHeadless_(True)
    c.capture, c.engine, c.hud, c.inserter, c.guard = FakeCapture(), FakeEngine(), FakeHUD(), FakeInserter(), FakeGuard()
    c.next_check = time.time() + 3600               # no permission polling in a test

    class NoFocus(object):
        """Where the focus is: not known, as without Accessibility. The real one reads the
        app in front (an editor, a terminal), which differs by machine; tests that need a
        focus bring their own."""
        def now(self): return None
        def release(self, target): pass
        def field_text(self): return None
    c.focus = NoFocus()

    def flags_event(key, down):
        ev = Quartz.CGEventCreate(None)
        Quartz.CGEventSetType(ev, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGKeyboardEventKeycode, key)
        Quartz.CGEventSetFlags(ev, (0x40000 | d.KEY_BIT[key]) if down else 0)
        return ev

    class TestClock(object):
        """The clock the controller reads while a test feeds it key events: each event
        0.05 s after the last, however busy the machine is. A shortcut is judged by its
        timing (a tap is under 0.35 s), and a loaded run once stretched a real sleep(0.05)
        past that: the second tap of a stop was never seen."""
        def __init__(self): self.t, self.m = time.time(), time.monotonic()
        def time(self): return self.t
        def monotonic(self): return self.m
        def step(self, s=0.05): self.t += s; self.m += s
        def __getattr__(self, name): return getattr(time, name)

    def on_clock(events):
        """Feeds (etype, event) pairs to the controller on a TestClock, then gives it the real one back."""
        clock, real = TestClock(), da.time
        da.time = clock
        try:
            for etype, event in events:
                c.on_event(etype, event)
                clock.step()
        finally:
            da.time = real

    def double_tap(key):
        on_clock([(Quartz.kCGEventFlagsChanged, flags_event(key, down)) for down in (True, False, True, False)])
    double_tap(RCMD)
    check("controller: Right Command twice starts listening",
          c.listening and c.capture.on and c.engine.warmed == 1 and c.guard.calls == ["begin"])
    check("  and shows the listening panel", c.hud.shown[-1] == "Listening", c.hud.shown)
    key = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
    c.on_event(Quartz.kCGEventKeyDown, key)
    double_tap(RCMD)
    check("controller: Right Command twice again stops, and the audio is decoded",
          not c.listening and not c.capture.on and c.engine.audio is not None and c.guard.calls == ["begin", "end"])
    c.tick_(None)
    text = [g for g in c.inserter.got]
    check("  the text, with word fixes, goes where the cursor is",
          text and text[-1][1] == "Ask Claude Code to run it. " and text[-1][0] in ("insert", "put"), text)
    check("  and Copy Last Dictation has it", c.last_text == "Ask Claude Code to run it. ")
    menu = c.build_menu()
    titles = [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())]
    check("menu: shortcut, insert mode, word fixes, login, quit",
          all(t in titles for t in ("Shortcut", "Insert By", "Edit Word Fixes…", "Start at Login",
                                    "Quit Standby Dictation")), titles)
    titles = [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())]
    pick = lambda key: c.pickShortcut_(type("S", (), {"representedObject": lambda self: key})())
    core_missing = lambda c: d.missing(c.perm, c.settings)
    pick("mickey")
    check("menu: the 🎤 key works on its own, no Apple Dictation warning",
          isinstance(c.gesture, d.KeyPress) and not any("Apple Dictation" in t for t in
          [c.build_menu().itemAtIndex_(i).title() for i in range(c.build_menu().numberOfItems())]))
    c.engine.audio = None
    f18 = lambda: [(Quartz.kCGEventKeyDown if down else Quartz.kCGEventKeyUp, Quartz.CGEventCreateKeyboardEvent(None, F18, down))
                   for down in (True, False)]
    on_clock(f18())
    check("controller: the 🎤 key (as F18) starts listening", c.listening and c.mode == "press")
    on_clock(f18())
    check("  and again stops, and the audio is decoded", not c.listening and c.engine.audio is not None)
    c.tick_(None)
    check("controller: the headless app never touches hidutil", c.remapped is False)

    # -- Return while dictating finishes it, pastes, and presses Return in the app
    check("Return: the headless app never takes the Return key (a test must not take the user's)",
          c.return_key is None)

    class FakeHotKey(object):
        def __init__(self): self.keys, self.calls = None, []
        def register(self, keycode, mods): self.keys = (keycode, mods); self.calls.append(("on", keycode)); return True, ""
        def unregister(self): self.keys = None; self.calls.append(("off",))

    class FakeField(object):
        """The focused field, as Accessibility reads it: what it holds now, or None."""
        def __init__(self, text=""): self.text = text
        def field_text(self): return self.text
        def now(self): return None
        def release(self, target): pass

    pressed = []
    c.inserter.press_return = lambda: pressed.append(time.time())
    real_can_post, real_focus, real_back_to = da.can_post, c.focus, c.back_to
    da.can_post = lambda: True
    c.back_to = lambda target: None
    c.return_key = FakeHotKey()

    def dictate(field, end="return", moved=None):
        """One dictation ended by Return (or the shortcut), with the field as given."""
        del pressed[:]
        c.focus, c.sending = field, None
        c.back_to = (lambda target: moved) if moved else (lambda target: None)
        c.engine.audio = None
        c.on_command("press")                       # start, as the 🎤 key does
        if end == "return":
            c.on_return(True)
        else:
            c.on_command("press")
        c.tick_(None)                               # the transcript arrives and is pasted
        if field.text == "":
            field.text = "Earlier words. Ask Claude Code to run it."     # the paste lands a moment later
        c.tick_(None)

    field = FakeField("")
    c.return_key.calls = []
    dictate(field)
    check("Return while dictating: taken only while listening, given back as it stops",
          c.return_key.calls == [("on", da.RETURN_KEY), ("off",)] and c.return_key.keys is None, c.return_key.calls)
    check("  the text is pasted where the dictation started", c.inserter.got[-1][1] == "Ask Claude Code to run it. ",
          c.inserter.got[-1:])
    check("  and Return is pressed once the text is seen in the field, not before", len(pressed) == 1, pressed)

    c.return_key.calls = []
    dictate(FakeField("Something else entirely."))
    check("the text never seen in the field: Return is not pressed yet", pressed == [] and c.sending is not None)
    c.sending["until"] = time.time() - 1
    c.tick_(None)
    check("  and in the end not at all: the panel says to press it, nothing half-sent",
          pressed == [] and c.sending is None and c.hud.shown[-1] == "Pasted: press Return to send", c.hud.shown[-1:])

    dictate(FakeField(None))
    c.sending and c.sending.update(until=time.time() - 1)
    c.tick_(None)
    check("an app whose field can't be read: never pressed, the same plain line", pressed == [], pressed)

    dictate(FakeField(""), end="shortcut")
    check("ended with the shortcut as ever: no Return", pressed == [] and c.sending is None)

    dictate(FakeField(""), moved="Not pasted: the window it started in is gone")
    check("the focus moved away (no paste): no Return either", pressed == [] and c.sending is None, pressed)

    real_stop = c.capture.stop
    c.capture.stop = lambda: np.zeros(16000, "float32")          # nothing heard
    dictate(FakeField(""))
    c.capture.stop = real_stop
    check("Return with nothing heard: nothing pasted, no Return", pressed == [] and c.sending is None)

    c.return_key.calls = []
    c.on_command("press")
    c.cancel_listening()
    check("a cancelled dictation gives Return back too", c.return_key.calls == [("on", da.RETURN_KEY), ("off",)],
          c.return_key.calls)
    c.on_return(True)
    check("  and Return when not listening does nothing", not c.listening and pressed == [])
    da.can_post, c.focus, c.return_key, c.back_to = real_can_post, real_focus, None, real_back_to

    # -- no Accessibility: the note stays, with its button, until it is dealt with
    real_can_post, real_open = da.can_post, da.open_url
    opened = []
    da.can_post, da.open_url = (lambda: False), opened.append
    c.perm["post"] = False
    c.inserter.got, c.hud.shown = [], []
    c.engine.audio = None
    c.on_command("press")
    c.on_command("press")
    c.tick_(None)
    check("no Accessibility: the text is copied, not pasted", c.inserter.got[-1][0] == "put", c.inserter.got)
    check("  the note says what to do now, with its button",
          c.hud.note == "post" and c.hud.shown[-1] == da.NO_POST
          and c.hud.button == (da.NO_POST_BUTTON, "noteAllow:", "noteDismiss:"), (c.hud.shown, c.hud.note))
    check("  and it stays: no time to hide it", c.hud_hide_at is None)
    c.hud_hide_at = None
    for _ in range(3):
        c.tick_(None)
    check("  still up after ticks", c.hud.note == "post" and c.hud.shown[-1] == da.NO_POST, c.hud.shown[-3:])
    c.noteAllow_(None)
    check("Let Standby paste: opens the Accessibility pane, by URL",
          opened == [d.PANE + "Accessibility"] and c.next_check == 0.0, opened)
    check("  and the note stays while you're there", c.hud.note == "post")
    c.next_check = time.time() + 3600
    c.note_granted()
    check("  not granted yet: nothing changes", c.hud.note == "post")
    c.perm["post"] = True
    c.note_granted()
    check("granted: the note goes, and says so briefly",
          c.hud.note is None and c.hud.shown[-1] == da.GRANTED and c.hud_hide_at is not None, c.hud.shown[-2:])
    c.note_granted()
    check("  once", c.hud.shown.count(da.GRANTED) == 1, c.hud.shown)
    c.perm["post"] = False
    c.note_no_post()
    c.noteDismiss_(None)
    check("✕ dismisses it", c.hud.note is None and c.hud.shown[-1] is None, c.hud.shown[-2:])
    c.note_no_post()
    c.on_command("press")
    check("the next dictation replaces it", c.hud.note is None and c.hud.shown[-1] == "Listening", c.hud.shown[-2:])
    c.cancel_listening()
    c.on_command("press")
    c.note_no_post()
    check("  and while listening it isn't shown at all", c.hud.note is None)
    c.cancel_listening()
    da.can_post, da.open_url = real_can_post, real_open

    import Quartz as Q
    real_pre, real_ax = Q.CGPreflightPostEventAccess, da.ax_trusted
    Q.CGPreflightPostEventAccess = lambda: False
    da.ax_trusted = lambda: True
    check("a grant is seen without a restart: AXIsProcessTrusted counts too", da.can_post() is True)
    da.ax_trusted = lambda: False
    check("  and neither: no", da.can_post() is False)
    Q.CGPreflightPostEventAccess, da.ax_trusted = real_pre, real_ax
    check("ax_trusted answers a plain bool here", isinstance(da.ax_trusted(), bool))

    # The hotkeys' handler sees every hotkey: each answers only for its own.
    edges = {1: [], 2: []}
    keys = {n: da.CarbonHotKey(lambda down, at, n=n: edges[n].append(down), ident=n) for n in (1, 2)}
    class FakeCarbon(object):
        def GetEventKind(self, event): return da.CarbonHotKey.PRESSED
        def GetEventTime(self, event): return 1.0
    for hk in keys.values():
        hk._lib = lambda: FakeCarbon()
    for which in ((da.CarbonHotKey.SIGNATURE, 2), (da.CarbonHotKey.SIGNATURE, 1), None):
        for hk in keys.values():
            hk._which = lambda lib, event, w=which: w
        for n in (2, 1):                             # the newer handler is asked first
            if keys[n]._event(None, None, None) == 0:
                break
    check("hotkeys: Return's press goes to Return's, the shortcut's to the shortcut's",
          edges[2] == [True] and edges[1][:1] == [True], edges)
    check("  one that can't be read goes to the shortcut, as before there were two", edges[1] == [True, True]
          and edges[2] == [True], edges)

    # -- the 🎤 key heard twice (event tap and hotkey), secure input, every press logged
    said, real_log = [], d.log
    d.log = said.append
    c.engine.audio = None
    t0 = time.monotonic()                  # a press's own time is on this clock
    c.edges = {}
    cmd = c.mic_key(True, "tap", t0)
    check("the 🎤 key: a press heard by the tap starts", cmd == "press", cmd)
    check("  heard again through the hotkey a moment later: the same press, not a second one",
          c.mic_key(True, "hotkey", t0 + 0.01) is None and c.mic_key(False, "hotkey", t0 + 0.08) is None
          and c.mic_key(False, "tap", t0 + 0.09) is None)
    check("  logged once, with who heard it", sum("the mic key down" in m for m in said) == 1
          and "the mic key down (event tap)" in said and "the mic key up (hotkey)" in said, said)
    c.gesture = d.make_gesture(c.settings)
    c.edges = {}
    t1 = time.time() + 10
    c.gesture = d.make_gesture(c.settings)
    c.edges = {}
    t2 = time.monotonic()
    check("the hotkey's copy handled 1.2 s late (the main thread busy opening the mic): still the same press",
          c.mic_key(True, "tap", t2) == "press" and c.mic_key(False, "tap", t2 + 0.15) is None
          and c.mic_key(True, "hotkey", t2 + 0.004) is None and c.mic_key(False, "hotkey", t2 + 0.152) is None)
    check("  but a real second press 1.2 s later is one", c.mic_key(True, "tap", t2 + 1.2) == "press"
          and c.mic_key(True, "hotkey", t2 + 1.203) is None)
    c.mic_key(False, "tap", t2 + 1.3)
    # One clock for the gesture: the press's own (monotonic) time is moved onto the wall
    # clock that tick_'s poll and every other feed use, at real-sized values.
    for hold in (False, True):
        c.settings = dict(c.settings, hold=hold)
        c.gesture, c.edges = d.make_gesture(c.settings), {}
        pressed = time.monotonic()
        down_cmd = c.mic_key(True, "hotkey", pressed)
        polled = c.gesture.poll(time.time())
        fed = c.gesture.down_at
        up_cmd = c.mic_key(False, "hotkey", pressed + 0.6)
        check("  hold %s: the gesture sees the press on the wall clock (%.0f, not %.0f), polls sanely" % (
            "on" if hold else "off", time.time(), time.monotonic()),
            down_cmd == "press" and polled is None and abs(fed - time.time()) < 5
            and up_cmd == ("release_held" if hold else None), (down_cmd, polled, fed, up_cmd))
        if c.listening:
            c.cancel_listening()
    c.settings = dict(c.settings, hold=False)
    c.gesture = d.make_gesture(c.settings)
    c.edges = {}
    t1 = time.monotonic() + 10
    check("the hotkey alone (the tap blind): presses still work",
          c.mic_key(True, "hotkey", t1) == "press" and c.mic_key(False, "hotkey", t1 + 0.1) is None
          and c.mic_key(True, "hotkey", t1 + 0.5) == "press")
    c.mic_key(False, "hotkey", t1 + 0.6)
    c.gesture = d.make_gesture(c.settings)
    c.edges = {}
    n = len(said)
    for down in (True, False):
        c.on_event(Quartz.kCGEventKeyDown if down else Quartz.kCGEventKeyUp,
                   Quartz.CGEventCreateKeyboardEvent(None, F18, down))
    check("a press through the tap goes the same way, and the commands are logged",
          c.listening and "shortcut: press, idle" in said[n:] and "listening (press)" in said[n:], said[n:])
    rep = Quartz.CGEventCreateKeyboardEvent(None, F18, True)
    Quartz.CGEventSetIntegerValueField(rep, Quartz.kCGKeyboardEventAutorepeat, 1)
    n = len(said)
    c.on_event(Quartz.kCGEventKeyDown, rep)
    check("  the key held (macOS repeating it): nothing, not even a log line", said[n:] == [] and c.listening)
    time.sleep(0.35)
    c.edges = {}
    for down in (True, False):
        c.on_hotkey(down)
    check("  stopped through the hotkey, and logged", not c.listening
          and any(m.startswith("stopped after ") for m in said), said[-4:])
    c.tick_(None)

    holder = {"on": True}
    real = (da.secure_input_on, da.secure_input_holder, da._app_name)
    da.secure_input_on, da.secure_input_holder, da._app_name = (lambda: holder["on"], lambda: 611,
                                                            lambda pid: "loginwindow")
    try:
        c.check_secure_input()
        check("secure input on: who holds it, and what to do, in the log",
              c.secure == {"on": True, "pid": 611, "app": "loginwindow", "heard": False}
              and any("keyboard input is private now (held by loginwindow, pid 611)" in m
                      and "log out and back in" in m for m in said), said[-2:])
        status = json.loads(d.STATUS.read_text())
        check("  and in the status, for Standby's Settings", status["secure_input"]["on"] is True
              and status["secure_input"]["app"] == "loginwindow", status.get("secure_input"))
        c.perm = {"listen": True, "post": True, "mic": "allowed"}
        m = c.build_menu()
        head = [str(m.itemAtIndex_(i).title()) for i in range(2)]
        check("  and the menu says it, with the fix", head == ["Can't hear the shortcut: keyboard input is private",
                                                              "Finish any password prompt; if there is none, log out and back in"], head)
        n = len(said)
        c.check_secure_input()
        check("  said once, not every second", len(said) == n)
        c.edges = {}
        c.on_hotkey(True)
        c.on_hotkey(False)
        check("the 🎤 key heard through the hotkey meanwhile: it still works, and the menu stops warning",
              c.secure["heard"] and c.listening and str(c.build_menu().itemAtIndex_(0).title()).startswith("Ready"),
              (c.secure, str(c.build_menu().itemAtIndex_(0).title())))
        c.cancel_listening()
        c.pending, c.listening = 1, False
        real_post, da.can_post = da.can_post, lambda: True
        try:
            c.deliver("what I said", None)
        finally:
            da.can_post = real_post
        check("text delivered while input is private: pasted, and left on the clipboard, and said so",
              c.inserter.got[-1][0] == "kept" and c.inserter.got[-1][1].startswith("what I said")
              and c.hud.shown[-1] == "Also copied: press Command-V if it didn't paste", c.inserter.got[-1:])
        holder["on"] = False
        c.check_secure_input()
        check("  and when it ends, that is logged too", c.secure["on"] is False
              and "keyboard input is no longer private: the shortcut is heard again" in said)
    finally:
        da.secure_input_on, da.secure_input_holder, da._app_name = real
        d.log = real_log
    c.sync_hotkey()
    check("the headless app never registers a hotkey", c.hotkey.keys is None)

    # -- a recorded shortcut: the hotkey it needs, its presses, and a reload
    rec = d.judge_shortcut({"code": "Space", "key": " ", "mods": ["alt"]}, [])["custom"]
    d.save_setting("custom", rec)
    d.save_setting("shortcut", "custom")
    said, real_log = [], d.log
    d.log = said.append
    try:
        if (d.ROOT / "dictation.cmd").exists():
            (d.ROOT / "dictation.cmd").unlink()
        d.COMMAND.write_text("reload\n")
        c.next_cmd = 0
        c.tick_(None)
        check("a reload from Settings: the recorded shortcut, at once",
              c.settings["shortcut"] == "custom" and c.gesture.keys == {d.HOTKEY}
              and any(m.startswith("settings reloaded: your own, Option+Space") for m in said), said[-3:])
        check("  the hotkey it needs is its own keys", c.wanted_hotkey() == (49, 0x0800))
        c.on_hotkey(True)
        c.on_hotkey(False)
        check("  pressed: listening, and logged", c.listening and "the shortcut down (hotkey)" in said, said[-4:])
        time.sleep(0.05)
        c.on_hotkey(True)
        c.on_hotkey(False)
        check("  pressed again: stopped", not c.listening)
        menu = c.build_menu()
        shortcut_menu = [menu.itemAtIndex_(i) for i in range(menu.numberOfItems())
                         if str(menu.itemAtIndex_(i).title()) == "Shortcut"][0].submenu()
        items = [(str(shortcut_menu.itemAtIndex_(i).title()), shortcut_menu.itemAtIndex_(i).state())
                 for i in range(shortcut_menu.numberOfItems())]
        check("  the menu offers it, ticked", items[-1] == ("Press ⌥ Space", 1), items[-2:])
        c.hotkey_state = {"label": "Press ⌥ Space", "ok": False, "why": "another app already uses it", "at": 1}
        head = [str(c.build_menu().itemAtIndex_(i).title()) for i in range(2)]
        check("  and says plainly when another app holds its keys",
              head[1] == "Press ⌥ Space can't be used: another app already uses it", head)
        c.hotkey_state = None
        d.save_setting("shortcut", "rcommand2")
        c.reload_settings()
        check("a double tap again: no hotkey needed", c.wanted_hotkey() is None and isinstance(c.gesture, d.Gesture))
        d.save_setting("shortcut", "mickey")
        c.reload_settings()
        check("  the 🎤 key: F18 is the hotkey", c.wanted_hotkey() == (d.F18_KEY, 0))
    finally:
        d.log = real_log

    # -- where the text goes: started in Notes, pasted in Notes, or nowhere
    said, real_log, real_post = [], d.log, da.can_post
    d.log, da.can_post = said.append, lambda: True
    try:
        def dictate(during=None, start=None):
            """Start in the made-up desktop's Notes, let `during` move things, stop, deliver."""
            world = AXWorld()
            if start:
                start(world)
            c.focus, c.inserter = WorldFocus(world), FakeInserter()
            c.hud.shown = []
            c.start_listening("press")
            if during:
                during(world)
            c.stop_listening()
            c.tick_(None)
            return world, c.inserter.got, c.hud.shown

        world, got, shown = dictate()
        check("the focus never moved: pasted where it is, and nothing else touched",
              got and got[-1][0] == "insert" and not [x for x in world.calls if x[0] != "AXFrontmost"], (got, world.calls))
        check("  the start says where", any(m.startswith("listening (press), in com.apple.Notes") for m in said), said[-6:])

        def crash_dialog(world):
            world.front, world.focused = 202, 21
        world, got, shown = dictate(crash_dialog)
        check("a dialog took the focus mid-dictation: Notes is brought back, then the text pasted there",
              got and got[-1][0] == "insert" and (world.front, world.focused) == (101, 11)
              and any("paste: back in com.apple.Notes" in m for m in said), (got, world.front, world.focused))

        def closed(world):
            crash_dialog(world)
            world.gone.update({11, 12})
        world, got, shown = dictate(closed)
        check("the window it started in closed: never pasted into the dialog; copied, and said so",
              got == [("put", "Ask Claude Code to run it. ")] and shown[-1] == da.PASTE_GONE
              and world.focused == 21, (got, shown[-1:]))

        def quit_(world):
            crash_dialog(world)
            world.quit.add(101)
            world.owner = {k: v for k, v in world.owner.items() if v != 101}
        world, got, shown = dictate(quit_)
        check("  the app quit: the same", got == [("put", "Ask Claude Code to run it. ")] and shown[-1] == da.PASTE_GONE)

        def stuck(world):
            crash_dialog(world)
            world.obey = False
        world, got, shown = dictate(stuck)
        check("  it would not come back: copied, and said so", got == [("put", "Ask Claude Code to run it. ")]
              and shown[-1] == da.PASTE_MOVED, (got, shown[-1:]))

        def password(world):
            world.subrole[11] = "AXSecureTextField"
        world, got, shown = dictate(start=password)
        check("started in a password field: never typed there; copied, and said so",
              got == [("put", "Ask Claude Code to run it. ")] and shown[-1] == da.PASTE_SECURE
              and not world.calls, (got, world.calls))

        def no_ax(world):
            world.trusted = False
        world, got, shown = dictate(start=no_ax)
        check("nothing known at the start (no Accessibility then): pasted where the focus is, as before",
              got and got[-1][0] == "insert", got)
        world, got, shown = dictate(crash_dialog)
        check("what it held is let go once the text is delivered", {11, 12} <= set(world.released), world.released)
        check("the panel's words, ASCII in the log", all(all(ord(ch) < 128 for ch in m) for m in said
                                                       if m.startswith(("The window", "Couldn't", "Not typed"))))
        check("  each one line beside the panel's others", all(len(x) <= 55 for x in (da.PASTE_GONE, da.PASTE_MOVED,
                                                                                      da.PASTE_SECURE)))
    finally:
        d.log, da.can_post = real_log, real_post
    from AppKit import NSEvent
    def system_event(subtype):
        return NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            d.SYSTEM_DEFINED, (0, 0), 0, 0, 0, None, subtype, 0x10a00, -1).CGEvent()
    before = c.unusual_keys
    c.on_event(d.SYSTEM_DEFINED, system_event(d.MOUSE_BUTTONS))
    check("log: mouse buttons are not special keys", c.unusual_keys == before)
    c.on_event(d.SYSTEM_DEFINED, system_event(8))
    check("  media keys still are (first few, to tell what a key sends)", c.unusual_keys == before + 1)
    pick("rcommand2")
    check("menu: picking a shortcut saves it and takes effect",
          d.settings()["shortcut"] == "rcommand2" and c.gesture.keys == {54})
    c.perm = {"listen": True, "post": True, "mic": "allowed"}
    pick("mickey")
    m = c.build_menu()
    titles = [("—" if m.itemAtIndex_(i).isSeparatorItem() else str(m.itemAtIndex_(i).title())) for i in range(m.numberOfItems())]
    check("menu: calm words, a clear order",
          titles == ["Ready: press F5", "Copy Last Dictation", "Fix Last Dictation…", "—", "Shortcut", "Insert By", "Sounds", "Edit Word Fixes…",
                     "—", "Start at Login", "Open Standby", "—", "Quit Standby Dictation"], titles)
    sub = m.itemAtIndex_(titles.index("Shortcut")).submenu()
    check("  Hold to Talk first in Shortcut, Pasting and Typing in Insert By",
          str(sub.itemAtIndex_(0).title()) == "Hold to Talk" and sub.itemAtIndex_(1).isSeparatorItem()
          and [str(x.title()) for x in m.itemAtIndex_(titles.index("Insert By")).submenu().itemArray()] == ["Pasting", "Typing"])
    c.perm = {"listen": False, "post": False, "mic": "denied"}
    m = c.build_menu()
    titles = [("—" if m.itemAtIndex_(i).isSeparatorItem() else str(m.itemAtIndex_(i).title())) for i in range(m.numberOfItems())]
    check("  with permissions missing: says which, then each Allow…, then Set Up…",
          titles[0] == "Needs Accessibility to paste" and
          titles[titles.index("Edit Word Fixes…") + 1:titles.index("Start at Login")] ==
          ["—", "Allow Microphone…", "Allow Accessibility…", "Set Up…", "—"], titles)
    check("  the header is a line, not a button", not m.itemAtIndex_(0).isEnabled())
    c.perm = {"listen": True, "post": True, "mic": "allowed"}

    # what was dictated reaches the learner (only queued), Fix Last Dictation…
    # learns the words changed, and suggestions come and go in the menu. The editor
    # itself (an alert) is never opened here.
    import vocabulary
    item = lambda m, t: next((m.itemAtIndex_(i) for i in range(m.numberOfItems()) if str(m.itemAtIndex_(i).title()) == t), None)
    queued, real_heard = [], c.learner.heard
    c.learner.heard = queued.append
    c.pending += 1
    c.deliver("Tell or ion the plan.", None, None)
    check("a dictation's text, after the word fixes, goes to the learner", queued == ["Tell or ion the plan. "], queued)
    c.learner.heard = real_heard
    check("  the headless app's learner never started: no thread, nothing read", c.learner.jobs is None)
    check("  Fix Last Dictation… is there once something was dictated",
          item(c.build_menu(), "Fix Last Dictation…") is not None and item(c.build_menu(), "Fix Last Dictation…").isEnabled())
    c.hud.shown.clear()
    private_logs, real_log = [], d.log
    d.log = private_logs.append
    try:
        c.learn_from("Tell or ion the plan.", "Tell Orion the plan.")
    finally:
        d.log = real_log
    check("  Learn: the changed words become a learned rule, and the panel says so",
          ("or ion", "Orion") in vocabulary.rules_in(d.WORDS) and c.hud.shown[-1:] == ["Learned: or ion → Orion"], c.hud.shown)
    check("  its private word-fix notification isn't copied into dictation.log",
          private_logs and not any("or ion" in m or "Orion" in m for m in private_logs), private_logs)
    check("  the user's own lines stay first, as they were", d.WORDS.read_text().startswith(d.WORDS_TEMPLATE.rstrip("\n")))
    check("  the next dictation is fixed by it", c.fixes.apply("ask or ion") == "ask Orion")
    check("  and Copy Last Dictation copies the corrected text", c.last_text == "Tell Orion the plan. ")
    c.learn_from("ask the cloud", "ask the Claude")
    check("  a lone everyday word: not learned, and the panel says why",
          c.hud.shown[-1:] == ["Not learned: “cloud” is an everyday word"] and "the cloud" not in d.WORDS.read_text(), c.hud.shown[-1:])
    check("  no suggestions yet: no Suggested Fixes", item(c.build_menu(), "Suggested Fixes") is None)
    c.learner.idx = vocabulary.index([("Kubernetes", 3), ("pywebview", 3)])
    c.learner.heard_now("deploy it to cooper nettys"); c.learner.heard_now("pie web view, then cooper nettys")
    c.learner.heard_now("pie web view")
    tips = item(c.build_menu(), "Suggested Fixes")
    rows = [str(x.title()) for x in tips.submenu().itemArray()] if tips is not None else []
    check("  heard twice: Suggested Fixes, then Dismiss All (a weak likeness, cooper nettys, not offered)",
          rows[-2:] == ["", "Dismiss All"] and "pie web view → pywebview" in rows and not any("cooper" in r for r in rows), rows)
    pick_tip = next((x for x in tips.submenu().itemArray() if str(x.title()) == "pie web view → pywebview"), None) if tips else None
    if pick_tip is not None:
        check("  its tooltip says how often", "2 times" in str(pick_tip.toolTip()), pick_tip.toolTip())
        c.acceptFix_(pick_tip)
        check("  accepted: learned, said, and gone from the menu",
              ("pie web view", "pywebview") in vocabulary.rules_in(d.WORDS) and c.hud.shown[-1] == "Learned: pie web view → pywebview"
              and "pie web view → pywebview" not in [str(x.title()) for x in (item(c.build_menu(), "Suggested Fixes").submenu().itemArray()
                                                       if item(c.build_menu(), "Suggested Fixes") else [])])
    c.dismissFixes_(None)
    check("  Dismiss All: the menu has none left", item(c.build_menu(), "Suggested Fixes") is None)

    # The panel, built for real but never shown: it is as wide as what it says.
    hud = da.HUD()
    check("panel: as wide as its content, within 140 to 360",
          hud.width(120, True, False) == 14 + 23 + 10 + 120 + 18 and hud.width(10, False, False) == 140
          and hud.width(900, False, False) == 360 and hud.width(150, False, True) == 14 + 16 + 10 + 150 + 18)
    check("  the bars follow the level, 4 to 16 pt, by the pattern",
          hud.heights(0) == [4.0] * 5 and max(hud.heights(1)) == 16.0 and hud.heights(1)[0] == 4 + 12 * 0.6)
    import AppKit as _AK
    glass = getattr(_AK, "NSGlassEffectView", None) is not None
    check("  a shadow only with the glass (macOS 26): before it, AppKit drew a box around the pill",
          bool(hud.panel.hasShadow()) == glass and not hud.panel.isOpaque(), (hud.panel.hasShadow(), glass))
    hud.show("Listening", level=True, front=False)
    check("  listening: the bars, no spinner", not hud.bars[0].isHidden() and hud.spinner.isHidden())
    hud.show("Transcribing…", spin=True, front=False)
    check("  transcribing: three dots rising in turn (the orb's thinking), not the mic's bars, no spinner",
          hud.bars[0].isHidden() and hud.spinner.isHidden() and not hud.work[0].isHidden() and hud._ripple is not None
          and len(set(round(u, 2) for u, _ in hud.dots(0.3))) == 3 and all(0 <= u <= 3 and 0.4 <= a <= 1 for u, a in hud.dots(0.7))
          and hud.work[0].frame().size.width == hud.DOT == hud.work[0].frame().size.height)
    hud.show("Dictation is ready. Press 🎤 in any app", front=False)
    check("  a note: the text alone", hud.bars[0].isHidden() and hud.spinner.isHidden() and hud.work[0].isHidden() and hud._ripple is None)

    # it showed on the first full-screen Space only. Built for real (never shown): the
    # companion's settings, all set before it is ever ordered in.
    p = hud.panel
    check("panel: borderless, non-activating, status level, as the companion",
          int(p.styleMask()) == 1 << 7 and int(p.level()) == 25 and p.isFloatingPanel()
          and p.becomesKeyOnlyIfNeeded() and not p.hidesOnDeactivate() and not p.canHide()
          and p.ignoresMouseEvents(), (p.styleMask(), p.level()))
    check("  every Space, stationary, not in Cmd-`, over full-screen apps (337), before it is ever shown",
          int(p.collectionBehavior()) == 337 and not p.isVisible(), p.collectionBehavior())

    class Spaces(object):
        active = 1

    class FakePanel(object):
        """The window server, as the user saw it (stuck: it stays on the first Space
        it was ordered into, unless moved to the active one) or as it should be."""
        def __init__(self, stuck):
            self.stuck, self.space, self.visible, self.calls, self.behaviour = stuck, None, False, [], 337
        def isVisible(self): return self.visible
        def isOnActiveSpace(self): return self.visible and self.space in (Spaces.active, "all")
        def orderFrontRegardless(self):
            self.calls.append("front")
            if not self.stuck:
                self.space = "all"
            elif self.space is None or self.behaviour == da.HUD.TO_ACTIVE:
                self.space = Spaces.active
            self.visible = True
        def orderOut_(self, _): self.calls.append("out"); self.visible = False
        def setCollectionBehavior_(self, b): self.behaviour = b
        def occlusionState(self): return 2 if self.isOnActiveSpace() else 0
        def level(self): return 25
        def animator(self): return self
        def effectiveAppearance(self): return self
        def bestMatchFromAppearancesWithNames_(self, _names): return "NSAppearanceNameAqua"
        def setAlphaValue_(self, _a): pass
        def setFrameOrigin_(self, at): self.at = at
        def setContentSize_(self, _s): pass
        def invalidateShadow(self): pass

    logged, real_log = [], d.log
    d.log = logged.append

    def fake_hud(stuck):
        h = da.HUD()
        h.panel, h.later = FakePanel(stuck), (lambda _s, fn: fn())
        return h

    def gone(h):                                   # hide(), its fade done (no run loop here)
        h.hide()
        h.panel.orderOut_(None)

    Spaces.active = 1
    h = fake_hud(stuck=False)
    h.show("Listening", level=True)
    h.show("Transcribing…", spin=True)
    check("panel: every show orders it in front, regardless (also already showing)",
          h.panel.calls == ["front", "front"], h.panel.calls)
    gone(h)
    Spaces.active = 2
    h.space_changed()
    h.show("Listening", level=True)
    check("  on another Space, where every Space works: shown there, the behaviour kept",
          h.panel.isOnActiveSpace() and h.behaviour == 337 and not any("moving it" in m for m in logged), logged)
    check("  the first show after a Space change says where it went (for the hands-on check)",
          any("after 1 Space change(s): on this Space yes, visible yes (behaviour 337)" in m for m in logged), logged)

    logged[:] = []
    Spaces.active = 1
    h = fake_hud(stuck=True)
    h.show("Listening", level=True)
    gone(h)
    Spaces.active = 2
    h.space_changed()
    h.show("Listening", level=True)
    check("stuck on the first Space, as the user saw: moved to the active Space, and shown here",
          h.panel.isOnActiveSpace() and h.behaviour == da.HUD.TO_ACTIVE and h.panel.behaviour == da.HUD.TO_ACTIVE,
          h.panel.space)
    check("  the log says so, once", sum("moving it to the active Space" in m for m in logged) == 1, logged)
    gone(h)
    Spaces.active = 3
    h.space_changed()
    h.show("Listening", level=True)
    check("  the next Space: shown there straight away", h.panel.isOnActiveSpace()
          and sum("moving it" in m for m in logged) == 1 and not any("still not" in m for m in logged), logged)
    Spaces.active = 4
    h.space_changed()
    check("a swipe to another Space while it shows: it comes along", h.panel.isOnActiveSpace(), h.panel.space)
    gone(h)
    n = len(h.panel.calls)
    Spaces.active = 5
    h.space_changed()
    check("  not while it is hidden", len(h.panel.calls) == n, h.panel.calls[n:])
    h.show("Listening", level=True, front=False)
    check("  front=False (the headless self-test) never orders it in", len(h.panel.calls) == n, h.panel.calls[n:])
    d.log = real_log

    class R(object):
        def __init__(self, x, y, w, ht):
            self.origin, self.size = types.SimpleNamespace(x=x, y=y), types.SimpleNamespace(width=w, height=ht)

    class Scr(object):
        def __init__(self, *r): self.r = R(*r)
        def frame(self): return self.r

    left, right = Scr(0, 0, 1512, 982), Scr(1512, 0, 2560, 1440)
    pt = lambda x, y: types.SimpleNamespace(x=x, y=y)
    check("panel: on the screen with the pointer, not the main one", da.screen_at(pt(2000, 500), [left, right], left) is right)
    check("  the pointer on none of them: the main screen", da.screen_at(pt(-50, 9999), [left, right], left) is left)
    pick("mickey")

    # Setup: the menu offers it, the window shows what is missing, its buttons fix it.
    c.perm = {"listen": True, "post": False, "mic": "ask"}
    menu = c.build_menu()
    check("menu: Set Up… while a permission is missing",
          "Set Up…" in [menu.itemAtIndex_(i).title() for i in range(menu.numberOfItems())])
    # The setup window, one step at a time (the user: "very unclear what you expect the user to
    # take action on... maybe sequential"): only the current step has a button.
    class Tag(object):
        def __init__(self, n): self.n = n
        def tag(self): return self.n
    c.perm = {"listen": False, "post": False, "mic": "ask"}
    c.setup_sent, c.setup_tried, c.setup_failed = set(), None, None
    m = c.setup_steps()
    check("setup with F5: three steps, no Input Monitoring (F5 is a hotkey: a VM run dictated without it)",
          m["count"] == "Step 1 of 3" and [x["key"] for x in m["steps"]] == ["mic", "post", "try"]
          and [x["n"] for x in m["steps"]] == [1, 2, 3], m)
    c.perm = {"listen": False, "post": True, "mic": "allowed"}
    check("  Microphone and Accessibility allowed: on to trying it, and nothing missing in the menu",
          c.setup_steps()["count"] == "Step 3 of 3" and core_missing(c) == [] and c.build_menu().itemAtIndex_(0).title() == "Ready: press F5"
          and "Allow Input Monitoring…" not in [c.build_menu().itemAtIndex_(i).title() for i in range(c.build_menu().numberOfItems())])
    pick("rcommand2")                         # a double-tap: Input Monitoring is a step
    c.perm = {"listen": False, "post": False, "mic": "ask"}
    m = c.setup_steps()
    check("setup: step 1 of 4, the Microphone, its one button and what macOS will ask",
          m["count"] == "Step 1 of 4" and [x["state"] for x in m["steps"]] == ["current", "later", "later", "later"]
          and m["steps"][0]["button"] == "Allow Microphone" and m["steps"][0]["note"] == "macOS will ask: choose Allow."
          and [x["button"] for x in m["steps"][1:]] == [None, None, None], m)
    sw = da.SetupWindow(c)
    sw.update(m)
    check("  drawn: one button for the step, and Not now", sorted(sw.buttons) == ["later", "mic"]
          and str(sw.buttons["mic"].title()) == "Allow Microphone" and str(sw.buttons["mic"].keyEquivalent()) == "\r",
          sorted(sw.buttons))
    check("  never shown in a test", not sw.visible)
    opened = []
    da.open_url = opened.append
    c.capture.on = None
    c.setupAction_(Tag(1))
    check("  Allow Microphone opens it for a moment, so macOS asks", c.capture.on is False and "mic" in c.asked)
    check("    without hearing the Mac's sound (that is for dictating)", c.capture.room is False)
    c.setupAction_(Tag(3))
    check("  a later step's button does nothing", opened == [], opened)
    c.perm["mic"] = "allowed"
    m = c.setup_steps()
    check("mic allowed: ticked, on to step 2, Input Monitoring", m["count"] == "Step 2 of 4"
          and [x["state"] for x in m["steps"]] == ["done", "current", "later", "later"]
          and m["steps"][1]["button"] == "Open Settings" and m["steps"][1]["how"] == d.SETUP_HOW
          and m["steps"][1]["why"] == "So Standby notices your shortcut." and not m["steps"][1]["waiting"], m["steps"][1])
    # macOS lists the app in a pane only once it has asked (a VM run: Open Settings… showed a list
    # without Standby Dictation in it): the ask comes first, then the pane.
    real_ask_post, real_ask_listen, order = da.ask_post, da.ask_listen, []
    da.ask_post, da.ask_listen = (lambda: order.append("ask post")), (lambda: order.append("ask listen"))
    da.open_url = lambda url: (opened.append(url), order.append("open"))
    c.asked.discard("listen")
    real_log, logged = d.log, []
    d.log = logged.append
    c.setupAction_(Tag(2))
    d.log = real_log
    check("  Open Settings: asked first, so Standby Dictation is in that list, then the pane",
          order == ["ask listen", "open"] and opened == [d.PANE + "ListenEvent"], (order, opened))
    check("    and the log says it asked (a VM run's log could not tell)",
          "asking macOS for Input Monitoring" in logged, logged)
    m = c.setup_steps()
    check("  then it waits for them, saying so", m["steps"][1]["waiting"] and m["steps"][1]["note"] == d.SETUP_WAITING)
    check("    and says where the app is, to add it by hand (the + picker opens /Applications)",
          m["steps"][1]["fallback"] == d.SETUP_FALLBACK and "Shift-Command-H" in d.SETUP_FALLBACK
          and "~/Applications" in Path(d.__file__).read_text() and m["steps"][0]["fallback"] is None)
    c.setupAction_(Tag(2))
    check("    asked once: a second click only opens the pane", order == ["ask listen", "open", "open"], order)
    c.perm["listen"] = True
    c.asked.discard("post")
    c.setupAction_(Tag(3))
    m = c.setup_steps()
    check("step 3, Accessibility: the same, and its reset once they've been there",
          m["count"] == "Step 3 of 4" and order[-2:] == ["ask post", "open"] and opened[-1] == d.PANE + "Accessibility"
          and m["steps"][2]["stale"] and not m["steps"][1]["stale"], (order, m["steps"][2]))
    da.ask_post, da.ask_listen, da.open_url = real_ask_post, real_ask_listen, opened.append
    sw.update(m)
    check("  drawn: the reset beside it, with its hint", "stale" in sw.buttons
          and str(sw.buttons["stale"].title()) == d.SETUP_STALE and str(sw.buttons["stale"].toolTip()) == d.STALE_HINT)
    del opened[:]
    real_tcc, real_ask, asked_post = da.TCCUTIL, da.ask_post, []
    da.ask_post = lambda: asked_post.append(1)
    c.setup = sw
    da.TCCUTIL = ["/usr/bin/true"]
    c.next_check = time.time() + 3600
    c.setupReset_(sw.buttons["stale"])
    check("  reset: this app's entry reset, macOS lists it again, the pane opens",
          asked_post == [1] and opened == [d.PANE + "Accessibility"] and c.next_check == 0.0
          and sw._said[0].startswith("Reset. Now turn ") and "again in the list" in sw._said[0], (opened, sw._said))
    da.TCCUTIL, asked_post[:] = ["/usr/bin/false"], []
    c.setupReset_(sw.buttons["stale"])
    check("  macOS refuses the reset: the steps to do it by hand, nothing asked",
          asked_post == [] and sw._said[0].startswith("macOS didn't reset it. ")
          and "remove it with the minus button" in sw._said[0], sw._said)
    da.TCCUTIL, da.ask_post, c.setup = real_tcc, real_ask, None
    c.next_check = time.time() + 3600
    check("  the real tccutil is never run from a test",
          os.environ.get("STANDBY_NO_DEVICES") is None or da.reset_post()[0] is False)
    c.perm["post"] = True
    m = c.setup_steps()
    check("all three allowed: step 4, try it, the box waiting for words", m["count"] == "Step 4 of 4"
          and m["steps"][3]["state"] == "current" and m["steps"][3]["box"] == d.SETUP_TRY_EMPTY
          and m["steps"][3]["why"] == "Press Right Command twice, say a few words, then the same again." and m["steps"][3]["button"] is None, m["steps"][3])
    check("  with F5: says the key", d.try_how(d.DEFAULTS) == "Press F5, say a few words, then press F5 again.")
    check("  holding to talk: says so", d.try_how(dict(d.DEFAULTS, hold=True)) == "Hold F5, say a few words, let go.")

    class OpenSetup(object):                  # the window, as if open on the screen
        visible, model = True, None
        def update(self, m): self.model = m
        def close(self): self.visible = False
        def show(self): pass
    c.setup = OpenSetup()
    pasted = len(c.inserter.got)
    c.pending = 1
    c._deliver("", None, None)
    check("  nothing caught: said in the box, with Try again", c.setup.model["steps"][3]["box"] == "Didn't catch that. Try again."
          and c.setup.model["steps"][3]["button"] == "Try again", c.setup.model["steps"][3])
    c.setupAction_(Tag(4))
    check("    Try again: the box waits again", c.setup.model["steps"][3]["box"] == d.SETUP_TRY_EMPTY)
    c.pending = 1
    c._deliver("testing one two", None, None)
    m = c.setup.model
    check("  words: in the box, never pasted into an app, and it's done",
          m["title"] == d.SETUP_DONE and m["count"] == "All done" and m["steps"][3]["box"] == "testing one two" and m["steps"][3]["button"] == "Done"
          and m["steps"][3]["note"] == "Dictation is on. Use Right Command twice in any app." and len(c.inserter.got) == pasted, m["steps"][3])
    c.setupAction_(Tag(4))
    check("  Done closes the window", c.setup.visible is False)
    c.setup, c.setup_tried = None, None
    pick("mickey")
    c.perm = {"listen": True, "post": True, "mic": "allowed"}
    check("menu: no Set Up… once all are granted",
          "Set Up…" not in [c.build_menu().itemAtIndex_(i).title() for i in range(c.build_menu().numberOfItems())])

    # Settings asks for the setup window through a file.
    shown = []
    c.show_setup = lambda: shown.append(1)
    c.check_permissions = lambda ask=False: None
    d.COMMAND.write_text("setup\n")
    c.next_cmd = 0.0
    c.tick_(None)
    check("command: Standby's Settings brings up the setup window", shown == [1] and not d.COMMAND.exists())
    words_opened, real_open_words = [], d.open_words
    d.open_words = lambda *a, **k: words_opened.append(1)      # never TextEdit in a test
    d.send_command("edit_words")
    c.next_cmd = 0.0
    c.tick_(None)
    d.open_words = real_open_words
    check("  and the word fixes, when Settings > Dictation asks", words_opened == [1] and not d.COMMAND.exists(),
          words_opened)

    # New code: restart when idle, under launchd by exiting for KeepAlive.
    class FakeWatch(object):
        ready, problem = True, None
        def names(self): return "dictation.py"
    exits = []
    d.exit_now = exits.append
    d.launchd_owns = lambda pid: True
    c.watch = FakeWatch()
    c.launched = time.time() - 2 * d.MIN_UP          # up a while: restarts are allowed
    c.listening = True
    c.tick_(None)
    check("restart: never while you are dictating", exits == [] and not c.restarting)
    c.listening, c.last_used = False, time.time()
    c.tick_(None)
    check("  nor right after a dictation (its clipboard is still being put back)", exits == [])
    st = json.loads(d.STATUS.read_text())
    check("  the status says new code is waiting, for Settings",
          st.get("code") == {"changed": "dictation.py", "pending": True, "problem": None}
          and "remapped" in st and st.get("started"), st)
    c.last_used = time.time() - 10
    c.tick_(None)
    check("  once idle: exits for launchd to start the new code", exits == [d.RESTART_EXIT] and c.restarting, exits)
    check("  and leaves a note for the next copy", d.take_restart_note() is not None)
    ran = root / "helper-ran"
    d.launchd_owns = lambda pid: False
    d.relaunch_plan = lambda pid, launchd, bundle=None, python=None: (
        0, [sys.executable, "-c", "open(%r, 'w').write('ok')" % str(ran)])
    c.restart()
    deadline = time.time() + 10
    while not ran.exists() and time.time() < deadline:
        time.sleep(0.1)
    check("restart by hand: starts the helper, then exits 0", exits[-1] == 0 and ran.exists(), exits)
    d.launch_begins()
    c.launched, c.healthy = time.time() - d.HEALTHY_AFTER - 1, False
    c.watch = None
    c.tick_(None)
    check("launch: up a while, it counts as a good one", c.healthy and not d.STARTING.exists())

    # The launch itself: under launchd, after a run of failed launches, it stops asking.
    d.launchd_owns = lambda pid: True
    d.STARTING.write_text(json.dumps({"at": time.time(), "n": d.GIVE_UP_AFTER, "error": "RuntimeError: boom"}))
    made = []
    real_controller = da.Controller
    da.Controller = type("NoController", (), {"alloc": staticmethod(lambda: made.append(1))})
    rc = da.cmd_run()
    da.Controller = real_controller
    check("launch: gives up after %d failed launches, exit 0 so KeepAlive stops" % d.GIVE_UP_AFTER,
          rc == 0 and made == [] and d.gave_up() == "RuntimeError: boom", (rc, made, d.gave_up()))
    d.AGENTS = root / "LaunchAgents"
    d.launch_went_fine()

    # Real speech, if the model is already here (never downloaded by a test).
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    cached = (hub / "models--mlx-community--parakeet-tdt-0.6b-v3").is_dir()
    if not (cached and shutil.which("say") and shutil.which("afconvert")):
        print("  (real-speech part skipped: needs the cached speech model, say and afconvert)")
    else:
        import wave
        aiff, wav = str(root / "s.aiff"), str(root / "s.wav")
        subprocess.run(["say", "-o", aiff, "Please ask Claude Code to open the git repository, "
                        "then run the tests on Opus five point five."], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True)
        w = wave.open(wav)
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0
        os.environ["HF_HUB_OFFLINE"] = "1"
        import speechmodel
        model = d.load_model(speechmodel.DEFAULT_MODEL)
        t0 = time.time()
        raw = d.transcribe_audio(model, audio)
        took = time.time() - t0
        fixed = d.WordFixes(root / "fresh-words.txt").apply(raw)
        print("     heard: %r (%.2fs)" % (raw, took))
        check("real speech: decoded in well under a second", took < 1.0, took)
        check("  git and Opus 5.5 come out right", "Git repository" in raw and "Opus 5.5" in raw, raw)
        check("  and the default word fix makes it Claude Code", "Claude Code" in fixed, fixed)
        chunked = d.transcribe_audio(model, audio, chunk_seconds=3.0, overlap_seconds=1.0)
        print("     in 3 s chunks: %r" % chunked)
        check("  long recordings: chunks merge back into the same words",
              "Git repository" in chunked and "tests on Opus" in chunked, chunked)

# The mic granted while the app runs: AVCaptureDevice's status stays what it read first (the setup
# window kept Microphone unticked until a restart: a VM run, macOS 26). macOS's answer to an ask
# is what it is now, and mic_status follows it.
class StaleDevice(object):
    status, granted, asks, held = 0, True, 0, False
    @classmethod
    def authorizationStatusForMediaType_(cls, kind):
        return cls.status
    @classmethod
    def requestAccessForMediaType_completionHandler_(cls, kind, done):
        cls.asks += 1
        if not cls.held:
            done(cls.granted)
real_device = list(da._capture_device)
da._capture_device[:] = [StaleDevice]
da._mic_answer.update(said=None, pending=False)
check("mic: not decided yet, and nothing asked: ask", da.mic_status() == "ask")
StaleDevice.held = True                           # the user hasn't answered macOS's ask yet
da.mic_access()
da.mic_access()
check("  asked once while macOS waits on the user: still ask, one ask", da.mic_status() == "ask" and StaleDevice.asks == 1,
      (da.mic_status(), StaleDevice.asks))
da._mic_answer["pending"], StaleDevice.held = False, False
da.mic_access()
check("  allowed, its status still the old one: allowed", da.mic_status() == "allowed", da.mic_status())
da._mic_answer.update(said=None, pending=False)
StaleDevice.status, StaleDevice.granted = 2, False
da.mic_access()
check("  denied: denied", da.mic_status() == "denied")
StaleDevice.granted = True                        # turned on in System Settings, the app still running
da.mic_access()
check("  then turned on in System Settings: allowed, no restart", da.mic_status() == "allowed")
StaleDevice.status = 3
check("  its own status allowed: allowed", da.mic_status() == "allowed")
da._capture_device[:] = real_device
da._mic_answer.update(said=None, pending=False)
check("  the real AVFoundation is never asked from a test", da.mic_access() is False and da._mic_answer["pending"] is False)

shutil.rmtree(root, ignore_errors=True)
print()
if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
