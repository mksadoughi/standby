#!/usr/bin/env python3
"""Standby Dictation: speak anywhere on this Mac, with Standby's on-device model.

A small menu-bar app that replaces macOS Dictation. Press the shortcut (the 🎤
key by default, like Apple's), talk, press it again: the words land where the
cursor is, in any app. Speech-to-text is the parakeet model the
meeting recorder uses, on this Mac. Audio never leaves it and is not kept: the
recording lives in memory until it is transcribed. While a meeting records, the
recorder transcribes it with its own loaded model (RecorderDecode): it passes
through the session's folder, this user's only, and is deleted as it is read.

    dictation.py run        the menu-bar app (Standby Dictation.app runs this)
    dictation.py on         start it now and at every login
    dictation.py off        stop it, and no more at login
    dictation.py status     running? shortcut? permissions?
    dictation.py selftest   build the menu and the panel, then exit

Permissions, each asked for once: Microphone, Input Monitoring (to notice the
shortcut in other apps) and Accessibility (to paste into them). While one is
missing, a setup window says which, and how to fix it.

It runs the code in this folder, and restarts itself into new code when the
files change (a merge to main), once no dictation is in progress.
"""

import hashlib
import json
import locale
import os
import plistlib
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# Started by launchd or Finder, the app has no language settings, so Python's
# default file encoding is ASCII. parakeet-mlx opens its config.json without an
# encoding, failed on its first non-ASCII byte, and dictation said "Could not
# transcribe" (as a misleading FileNotFoundError). The same fix as Standby.app's.
os.environ["PYTHONUTF8"] = "1"
os.environ.setdefault("LANG", "en_US.UTF-8")
if locale.getpreferredencoding(False).lower().replace("-", "") != "utf8":
    for _name in ("en_US.UTF-8", "UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, _name)
            break
        except locale.Error:
            pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config  # noqa: E402  (config.json, shared with the app; never the code that reaches Claude Code)
import vocabulary  # noqa: E402  (the user's words, learned on this Mac)

ROOT = config.ROOT
WORDS = ROOT / "dictation-words.txt"
STATUS = ROOT / "dictation.status.json"
LOCK = ROOT / "dictation.lock"
LABEL = "dev.standby.dictation"           # its login item's name, and its app's bundle id
APP = Path.home() / "Applications" / "Standby Dictation.app"
STANDBY_APP = Path.home() / "Applications" / "Standby.app"

SAMPLE_RATE = 16000

DEFAULTS = {"shortcut": "mickey", "hold": False, "sounds": True, "insert": "paste",
            "max_seconds": 300, "unload_minutes": 15, "custom": None}

# The 🎤 key (F5 on Apple Silicon keyboards) is Dictation at the HID level:
# macOS acts on it before any app sees it. While it is the shortcut, hidutil
# remaps it to F18, which nothing uses, so the press reaches us instead and
# Apple's dictation never starts. Undone on Quit, Off, or another shortcut.
MIC_USAGE = 0xC000000CF         # consumer page 0x0C, usage 0xCF: Voice Command
F18_USAGE = 0x70000006D         # keyboard page 0x07, usage 0x6D: F18
F18_KEY = 79                    # kVK_F18
# The event tap goes blind to every key press while macOS keeps keyboard input private
# (secure_input_on), so F18 is also a system-wide hotkey (CarbonHotKey), which the
# WindowServer matches itself. Listening at the HID level instead was tried and
# measured (2026-10-07): the keyboard reports a plain F5, 0xCF exists only above the
# device, and even the raw F5 is withheld from IOHIDManager under secure input.
EDGE_SAME = 0.3                 # two ears hearing one press: this close, by when it was pressed

# Apple's own presets: the 🎤 key, and "Press ... twice" for a modifier, plus a
# few that rarely clash with anything. Keycodes are from HIToolbox/Events.h. Not Control
# twice: it is Apple Dictation's own, and pressing it brought up macOS's "Do you want to
# enable Dictation?" (a VM run). A saved one falls back to F5 (settings).
SHORTCUTS = {
    "mickey":    ("Press F5", (F18_KEY,)),       # the key with the mic on it, named by its F-key (the user)
    "fn2":       ("Press Fn (Globe) twice", (63,)),
    "rcommand2": ("Press Right Command twice", (54,)),
    "lcommand2": ("Press Left Command twice", (55,)),
    "command2":  ("Press either Command twice", (54, 55)),
    "roption2":  ("Press Right Option twice", (61,)),
}
KEY_SHORTCUTS = {"mickey"}      # a key of its own; the rest are double-taps
# The flag bit that says a modifier key is down. The device-dependent bits tell
# left from right; Fn has only the one.
KEY_BIT = {59: 0x1, 62: 0x2000, 56: 0x2, 60: 0x4, 55: 0x8, 54: 0x10,
           58: 0x20, 61: 0x40, 63: 0x800000}

SYSTEM_DEFINED = 14              # NSEventTypeSystemDefined: media and special keys
MOUSE_BUTTONS = 7                # its subtype for mouse buttons (NX_SUBTYPE_AUX_MOUSE_BUTTONS): no key
PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
KEYBOARD_PANE = "x-apple.systempreferences:com.apple.Keyboard-Settings.extension"


def log(msg):
    line = "[dictation] %s %s\n" % (time.strftime("%H:%M:%S"), msg)
    # launchd and Finder start the app with no locale, so stderr is ASCII and a
    # "🎤" in a message raised at launch. The log file is UTF-8 either way.
    buf = getattr(sys.stderr, "buffer", None)
    if buf is not None:
        buf.write(line.encode("utf-8", "replace"))
        buf.flush()
    else:
        sys.stderr.write(line)
    sys.stderr.flush()


def settings():
    d = dict(DEFAULTS)
    d.update(config.load_config().get("dictation") or {})
    if d["shortcut"] not in SHORTCUTS and not (d["shortcut"] == "custom" and valid_custom(d.get("custom"))):
        d["shortcut"] = DEFAULTS["shortcut"]
    return d


def save_setting(key, value):
    cfg = config.load_config()          # fresh: the app writes this file too
    cfg.setdefault("dictation", {})[key] = value
    config.save_config(cfg)


# --------------------------------------------------------------------------
# A shortcut of the user's own: recorded in Standby's Settings
# --------------------------------------------------------------------------
#
# Either a combination, a key with modifiers ("⌥ Space"), which is a system-wide
# hotkey (CarbonHotKey: macOS takes it, so nothing is typed, and it works where the
# event tap is blind); or one modifier pressed twice ("Right Option twice"), which is
# the event tap's, as the presets are. Saved as settings["shortcut"] = "custom" and
# settings["custom"] = {"kind": "combo", "keycode", "mods", "label", "ascii"} or
# {"kind": "double", "keys", "label", "ascii"}. The page sends what the browser calls
# the key (KeyboardEvent.code: the physical key, like macOS's key codes) and what it
# types (KeyboardEvent.key), only for the label, so a French keyboard says "A".

HOTKEY = -1                     # the key a KeyPress waits for when the shortcut is a hotkey
CODES = {"KeyA": 0, "KeyS": 1, "KeyD": 2, "KeyF": 3, "KeyH": 4, "KeyG": 5, "KeyZ": 6, "KeyX": 7, "KeyC": 8,
         "KeyV": 9, "IntlBackslash": 10, "KeyB": 11, "KeyQ": 12, "KeyW": 13, "KeyE": 14, "KeyR": 15,
         "KeyY": 16, "KeyT": 17, "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21, "Digit6": 22,
         "Digit5": 23, "Equal": 24, "Digit9": 25, "Digit7": 26, "Minus": 27, "Digit8": 28, "Digit0": 29,
         "BracketRight": 30, "KeyO": 31, "KeyU": 32, "BracketLeft": 33, "KeyI": 34, "KeyP": 35, "Enter": 36,
         "KeyL": 37, "KeyJ": 38, "Quote": 39, "KeyK": 40, "Semicolon": 41, "Backslash": 42, "Comma": 43,
         "Slash": 44, "KeyN": 45, "KeyM": 46, "Period": 47, "Tab": 48, "Space": 49, "Backquote": 50,
         "Backspace": 51, "Escape": 53, "F17": 64, "NumpadDecimal": 65, "NumpadMultiply": 67, "NumpadAdd": 69,
         "NumpadDivide": 75, "NumpadEnter": 76, "NumpadSubtract": 78, "F18": 79, "F19": 80, "NumpadEqual": 81,
         "Numpad0": 82, "Numpad1": 83, "Numpad2": 84, "Numpad3": 85, "Numpad4": 86, "Numpad5": 87,
         "Numpad6": 88, "Numpad7": 89, "F20": 90, "Numpad8": 91, "Numpad9": 92, "F5": 96, "F6": 97, "F7": 98,
         "F3": 99, "F8": 100, "F9": 101, "F11": 103, "F13": 105, "F16": 106, "F14": 107, "F10": 109,
         "F12": 111, "F15": 113, "Home": 115, "PageUp": 116, "Delete": 117, "F4": 118, "End": 119, "F2": 120,
         "PageDown": 121, "F1": 122, "ArrowLeft": 123, "ArrowRight": 124, "ArrowDown": 125, "ArrowUp": 126}
NAMES = {"Space": "Space", "Enter": "Return", "Tab": "Tab", "Backspace": "Delete", "Delete": "Forward Delete",
         "Home": "Home", "End": "End", "PageUp": "Page Up", "PageDown": "Page Down", "ArrowLeft": "←",
         "ArrowRight": "→", "ArrowUp": "↑", "ArrowDown": "↓", "NumpadEnter": "Enter"}
MODS = (("ctrl", "⌃", "Control", 0x1000, 0x40000), ("alt", "⌥", "Option", 0x0800, 0x80000),
        ("shift", "⇧", "Shift", 0x0200, 0x20000), ("meta", "⌘", "Command", 0x0100, 0x100000))
# name, symbol, word, Carbon's modifier bit (RegisterEventHotKey), NSEvent's (macOS's own shortcut list)
DOUBLES = {"ControlLeft": (59, "Left Control"), "ControlRight": (62, "Right Control"),
           "AltLeft": (58, "Left Option"), "AltRight": (61, "Right Option"),
           "MetaLeft": (55, "Left Command"), "MetaRight": (54, "Right Command"),
           "ShiftLeft": (56, "Left Shift"), "ShiftRight": (60, "Right Shift")}
# macOS's own shortcuts, as they are until the user changes them (com.apple.symbolichotkeys
# then says, by these ids): (mods, keycode, what it does).
MAC_DEFAULTS = {64: ({"meta"}, 49, "is Spotlight's"), 65: ({"meta", "alt"}, 49, "opens Finder search"),
                60: ({"ctrl"}, 49, "switches the input source"), 61: ({"ctrl", "alt"}, 49, "switches the input source"),
                28: ({"meta", "shift"}, 20, "takes a screenshot"), 29: ({"meta", "shift", "ctrl"}, 20, "takes a screenshot"),
                30: ({"meta", "shift"}, 21, "takes a screenshot"), 31: ({"meta", "shift", "ctrl"}, 21, "takes a screenshot"),
                184: ({"meta", "shift"}, 23, "takes a screenshot"), 32: ({"ctrl"}, 126, "opens Mission Control"),
                33: ({"ctrl"}, 125, "shows the app's windows"), 79: ({"ctrl"}, 123, "moves between Spaces"),
                81: ({"ctrl"}, 124, "moves between Spaces"), 36: (set(), 103, "shows the desktop"),
                52: ({"meta", "alt"}, 2, "hides the Dock"), 7: ({"ctrl"}, 120, "moves to the menu bar"),
                8: ({"ctrl"}, 99, "moves to the Dock")}
# Taken everywhere, not in that list: the app switcher, Force Quit, lock, full screen, emoji.
ALWAYS_TAKEN = [({"meta"}, 48, "switches apps"), ({"meta", "shift"}, 48, "switches apps"),
                ({"meta", "alt"}, 53, "is Force Quit"), ({"ctrl", "meta"}, 12, "locks the Mac"),
                ({"ctrl", "meta"}, 3, "is full screen"), ({"ctrl", "meta"}, 49, "opens Emoji & Symbols")]
SYMBOLIC = Path.home() / "Library" / "Preferences" / "com.apple.symbolichotkeys.plist"


def combo_label(code, key, mods):
    """"⌃⌥ D", "⌥ Space", "F6": the modifiers in Apple's order, then the key as
    the keyboard shows it."""
    if code in NAMES:
        name = NAMES[code]
    elif re.match(r"^F\d+$", code):
        name = code
    elif key and len(key) == 1 and key.strip():
        name = key.upper()
    else:
        name = re.sub(r"^(Key|Digit|Numpad)", "", code) or code
    marks = "".join(sym for m, sym, _w, _c, _n in MODS if m in mods)
    return ("%s %s" % (marks, name)) if marks else name


def combo_ascii(code, mods):
    """The same, in ASCII, for logs (a log line goes out under launchd with no locale)."""
    return "+".join([word for m, _s, word, _c, _n in MODS if m in mods] + [code])


def macos_taken(path=None):
    """[(mods, keycode, what)] macOS uses now: its defaults, as the user left them
    in System Settings > Keyboard > Keyboard Shortcuts, plus the ones always taken."""
    taken = dict(MAC_DEFAULTS)
    try:
        with open(str(path or SYMBOLIC), "rb") as fh:
            table = plistlib.load(fh).get("AppleSymbolicHotKeys") or {}
    except Exception:
        table = {}
    out = []
    for ident, entry in table.items():
        try:
            ident = int(ident)
        except ValueError:
            continue
        default = taken.pop(ident, None)
        params = ((entry.get("value") or {}).get("parameters") or [])
        if not entry.get("enabled") or len(params) < 3 or params[1] == 0xFFFF:
            continue
        flags = int(params[2])
        mods = {m for m, _s, _w, _c, ns in MODS if flags & ns}
        out.append((mods, int(params[1]), default[2] if default else "is one of macOS's shortcuts"))
    return out + list(taken.values()) + ALWAYS_TAKEN


def judge_shortcut(spec, taken=None):
    """A shortcut the user recorded, judged: {"ok": True, "custom": {...}} to save, or
    {"ok": False, "why": "<one short line>"}. spec: {"code", "key", "mods"} from the
    page, or {"double": "AltRight"}."""
    if not isinstance(spec, dict):
        return {"ok": False, "why": "That isn't a shortcut"}
    if "double" in spec:
        if spec["double"] not in DOUBLES:
            return {"ok": False, "why": "Only Control, Option, Command or Shift can be pressed twice"}
        key, word = DOUBLES[spec["double"]]
        return {"ok": True, "custom": {"kind": "double", "keys": [key], "label": "%s twice" % word,
                                       "ascii": "%s twice" % spec["double"]}}
    code, key = str(spec.get("code") or ""), str(spec.get("key") or "")
    mods = {m for m in (spec.get("mods") or []) if m in {n for n, _s, _w, _c, _x in MODS}}
    if code == "Escape" and not mods:
        return {"ok": False, "why": "Esc cancels: press another key"}
    if code not in CODES:
        return {"ok": False, "why": "Pick a key with the modifiers" if re.sub(r"(Left|Right)$", "", code) in
                ("Control", "Alt", "Shift", "Meta", "OS") else "That key can't be used"}
    keycode, label = CODES[code], combo_label(code, key, mods)
    fkey = bool(re.match(r"^F\d+$", code))
    printable = not fkey and code not in NAMES or code == "Space"
    if not mods and not fkey:
        return {"ok": False, "why": "Needs a modifier: a key alone would type"}
    if mods == {"shift"} and printable:
        return {"ok": False, "why": "%s %s" % (label, "types a capital" if code.startswith("Key") else "would type")}
    if mods <= {"meta", "shift"} and "meta" in mods and printable and code != "Space":
        return {"ok": False, "why": "%s is an app shortcut everywhere" % label}
    if mods <= {"alt", "shift"} and "alt" in mods and printable and code != "Space":
        return {"ok": False, "why": "%s types a special character" % label}
    for tmods, tkey, what in (macos_taken() if taken is None else taken):
        if tkey == keycode and set(tmods) == mods:
            return {"ok": False, "why": "%s %s" % (label, what)}
    carbon = sum(c for m, _s, _w, c, _n in MODS if m in mods)
    return {"ok": True, "custom": {"kind": "combo", "keycode": keycode, "carbon": carbon,
                                   "mods": sorted(mods), "label": label, "ascii": combo_ascii(code, mods)}}


def valid_custom(c):
    if not isinstance(c, dict) or not isinstance(c.get("label"), str):
        return False
    if c.get("kind") == "combo":
        return isinstance(c.get("keycode"), int) and isinstance(c.get("carbon"), int)
    return c.get("kind") == "double" and isinstance(c.get("keys"), list) and all(k in KEY_BIT for k in c["keys"])


def shortcut_label(s):
    """What the shortcut is, in words: a preset's, or the user's own."""
    if s.get("shortcut") == "custom" and valid_custom(s.get("custom")):
        c = s["custom"]
        return "Press %s" % c["label"]
    return SHORTCUTS.get(s.get("shortcut"), SHORTCUTS[DEFAULTS["shortcut"]])[0]


def shortcut_ascii(s):
    """The shortcut for a log line: ASCII (launchd gives the app no locale)."""
    if s.get("shortcut") == "custom" and valid_custom(s.get("custom")):
        return "your own, %s" % s["custom"]["ascii"]
    return {"mickey": "the mic key (F5)"}.get(s.get("shortcut")) or shortcut_label(s)


def shortcut_kind(s):
    if s.get("shortcut") == "custom" and valid_custom(s.get("custom")):
        return s["custom"]["kind"]
    return "key" if s.get("shortcut") in KEY_SHORTCUTS else "double"


# --------------------------------------------------------------------------
# The shortcut
# --------------------------------------------------------------------------

class Gesture(object):
    """Modifier-key events in, dictation commands out. No Cocoa, so it is
    tested with plain numbers.

    A tap is the key pressed and released alone, quickly. Two taps close
    together give "toggle": start, or stop and type. Any other key, modifier or
    click in between means it was a chord (Control-click, Command-C), not a tap.
    With hold on, keeping the key down alone starts ("hold_start") and letting
    go stops ("hold_end"); another key during the hold means it was a chord
    after all ("cancel").
    """

    TAP_MAX = 0.35      # a press held longer than this is not a tap
    GAP_MAX = 0.5       # from the first release to the second press
    HOLD_MIN = 0.35     # held alone this long: push-to-talk

    def __init__(self, keys, hold=False):
        self.keys = set(keys)
        self.hold = hold
        self.down_at = None      # when the key went down, while it is down
        self.clean = False       # nothing else happened since it went down
        self.last_tap = None     # release time of the previous clean tap
        self.holding = False

    def feed(self, kind, t, key=None, down=None):
        """kind: "mod" (a modifier changed; key, down) or "key" / "mouse"."""
        if kind == "mod" and key in self.keys and down is not None:
            if down:
                if self.down_at is None:
                    self.down_at, self.clean = t, True
                return None
            pressed, clean = self.down_at, self.clean
            self.down_at, self.clean = None, False
            if self.holding:
                self.holding = False
                return "hold_end"
            if pressed is None or not clean or t - pressed > self.TAP_MAX:
                self.last_tap = None
                return None
            if self.last_tap is not None and pressed - self.last_tap <= self.GAP_MAX:
                self.last_tap = None
                return "toggle"
            self.last_tap = t
            return None
        if kind == "key" and down is False:
            return None                  # a key let go is no chord
        # Anything else: a chord, typing, a click.
        self.clean = False
        self.last_tap = None
        if self.holding:
            self.holding = False
            return "cancel"
        return None

    def poll(self, t):
        """Called on a timer: notices a key held long enough for hold-to-talk."""
        if (self.hold and self.down_at is not None and self.clean and not self.holding
                and t - self.down_at >= self.HOLD_MIN):
            self.holding = True
            return "hold_start"
        return None


class KeyPress(object):
    """A key of its own (the 🎤 key, which arrives as F18): a press starts, the
    next press stops. With hold on, letting go after holding it also stops, so
    holding it is push-to-talk. Auto-repeat while held is ignored."""

    def __init__(self, keys, hold=False):
        self.keys = set(keys)
        self.hold = hold
        self.down_at = None

    def feed(self, kind, t, key=None, down=None):
        if kind != "key" or key not in self.keys:
            return None
        if down:
            if self.down_at is not None:
                return None              # auto-repeat
            self.down_at = t
            return "press"
        held = t - self.down_at if self.down_at is not None else 0.0
        self.down_at = None
        return "release_held" if self.hold and held >= Gesture.HOLD_MIN else None

    def poll(self, t):
        return None


def make_gesture(s):
    if s["shortcut"] == "custom" and valid_custom(s.get("custom")):
        c = s["custom"]
        if c["kind"] == "combo":
            return KeyPress((HOTKEY,), s["hold"])        # CarbonHotKey's press and release
        return Gesture(tuple(c["keys"]), s["hold"])
    keys = SHORTCUTS[s["shortcut"]][1]
    return (KeyPress if s["shortcut"] in KEY_SHORTCUTS else Gesture)(keys, s["hold"])


# --------------------------------------------------------------------------
# Words
# --------------------------------------------------------------------------

WORDS_TEMPLATE = """\
# Standby Dictation fixes these after every dictation, for words the speech
# model hears wrong (names, products, acronyms). One per line:
#
#     what it hears => what you meant
#
# Case does not matter and only whole words match. Saved changes apply to the
# next dictation. Lines starting with # are ignored.

Cloud Code => Claude Code
"""


class WordFixes(object):
    def __init__(self, path=WORDS):
        self.path = Path(path)
        self.mtime = None
        self.rules = []

    def _load(self):
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(WORDS_TEMPLATE)
        mtime = self.path.stat().st_mtime
        if mtime == self.mtime:
            return
        # The same rules the transcript view uses: a learned one that isn't safe to type
        # (vocabulary.safe_fix) never is, the user's own as they wrote them.
        rules = [(vocabulary.compile_rule(heard), meant) for heard, meant in vocabulary.rules_in(self.path)]
        self.rules, self.mtime = rules, mtime

    def apply(self, text):
        try:
            self._load()
        except OSError as exc:
            log("word fixes unreadable: %s" % exc)
        for rx, meant in self.rules:
            text = rx.sub(lambda _m, s=meant: s, text)
        return text


# --------------------------------------------------------------------------
# Speech to text
# --------------------------------------------------------------------------

def transcribe_audio(model, audio, chunk_seconds=120.0, overlap_seconds=15.0):
    """Decode a whole recording in one pass (native.speech.decode): the model sees every
    word in context, unlike the recorder's pause-by-pause decode. 5 s of speech: 0.1 s."""
    from native import speech
    return speech.decode(model, audio, chunk_seconds, overlap_seconds)


def load_model(name):
    import speechmodel                    # the recorder's loader too: offline once cached, warm-up
    return speechmodel.load_model(name, log=log)


class RecorderDecode(object):
    """While a meeting records, its recorder decodes what you dictate: it has
    the speech model loaded, so a meeting with dictation holds one copy, not two
    (until one app holds it for both). The recording goes to the session's
    `decode` folder as `<n>.f32`, this user's only. The recorder takes it (deletes
    it) on its main loop, decodes it with transcribe_audio and answers `<n>.json`
    (standby.serve_decodes). decode() returns None for: decode it here. No
    recorder that does this is live, it didn't take the recording within TAKE_WAIT
    (the recording is taken back), its answer took too long, or it failed."""

    FRESH = 3.0            # s: live.json is written every 0.5 s while the recorder runs
    TAKE_WAIT = 2.0        # s for its main loop (0.1 s a turn, an utterance's decode at most) to take it
    MAX_SECONDS = 120.0    # one decode pass; longer ones here, not to hold the meeting's own lines up

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else ROOT
        self.n = 0

    def folder(self):
        """The live session's decode folder, if its recorder decodes for dictation."""
        try:
            sid = (self.root / "daemon.ready").read_text().strip()
            st = json.loads((self.root / "sessions" / sid / "live.json").read_text())
        except (OSError, ValueError):
            return None
        if not sid or not st.get("decodes") or abs(time.time() - float(st.get("at") or 0)) > self.FRESH:
            return None
        folder = self.root / "sessions" / sid / "decode"
        return folder if folder.is_dir() else None

    def decode(self, audio):
        seconds = len(audio) / float(SAMPLE_RATE)
        folder = self.folder() if seconds <= self.MAX_SECONDS else None
        if folder is None:
            return None
        self.n += 1
        name = "%d-%d" % (os.getpid(), self.n)
        path, answer = folder / (name + ".f32"), folder / (name + ".json")
        part = folder / (name + ".part")
        try:
            fd = os.open(str(part), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(audio.astype("<f4").tobytes())
            os.replace(str(part), str(path))
        except OSError as exc:
            log("decode: could not hand it to the recorder (%r): here instead" % exc)
            return None
        t0 = time.time()
        deadline = t0 + self.TAKE_WAIT
        taken = False
        while time.time() < deadline:
            if not taken and not path.exists():
                taken = True              # its turn now: as long as a decode of this length can take
                deadline = time.time() + 5.0 + seconds * 0.25
            if answer.exists():
                break
            time.sleep(0.02)
        try:
            got = json.loads(answer.read_text())
            answer.unlink()
        except (OSError, ValueError):
            got = None
        if got is None:
            try:
                path.unlink()             # taken back: the recorder skips it
                log("decode: the recorder didn't take it within %.0f s: here instead" % self.TAKE_WAIT)
            except OSError:
                log("decode: the recorder took it but gave no answer within %.0f s: here instead"
                    % (time.time() - t0))
            return None
        if got.get("error") is not None or not isinstance(got.get("text"), str):
            log("decode: the recorder's decode failed (%s): here instead" % str(got.get("error"))[:120])
            return None
        return got["text"]


class Engine(threading.Thread):
    """Loads the model and decodes on this one thread: MLX streams are
    thread-local (see Channel in standby.py), so loading and decoding must share
    a thread. The model loads when a dictation starts, while you speak (half a
    second once cached), and is dropped when unused for a while, so an idle
    menu-bar app holds no GPU memory. While a meeting records, the recorder
    decodes instead, with its model (RecorderDecode): none is loaded here then,
    unless a model was set by hand for dictation alone."""

    def __init__(self, model_name=None, unload_after=900.0,
                 load=load_model, transcribe=transcribe_audio, recorder=None):
        threading.Thread.__init__(self, name="asr", daemon=True)
        self.model_name, self.unload_after = model_name, unload_after
        self._load, self._transcribe = load, transcribe
        self.recorder = recorder if recorder is not None else RecorderDecode()
        self.jobs = queue.Queue()
        self.model = None
        self.used = 0.0

    STOP = "stop"

    @staticmethod
    def chosen():
        """Settings > Speech, as it is now: each load reads it, so a new choice
        is used from the next dictation that loads the model."""
        import speechmodel
        return speechmodel.for_load(config.load_config(), log)

    def warm(self):
        self.jobs.put(None)

    def submit(self, audio, done):
        """done(text, error) is called on this thread."""
        self.jobs.put((audio, done))

    def stop(self):
        self.jobs.put(self.STOP)

    def run(self):
        while True:
            try:
                job = self.jobs.get(timeout=max(1.0, min(30.0, self.unload_after)))
            except queue.Empty:
                if self.model is not None and time.time() - self.used > self.unload_after:
                    self._unload()
                continue
            if job == self.STOP:
                self.model = None
                return
            if self.model_name is None:            # Settings > Speech: the recorder's, if a meeting records
                if job is None and self.recorder.folder() is not None:
                    continue                       # a warm-up: nothing to load here
                if job is not None and self._through_recorder(*job):
                    continue
            if self.model is None:
                try:
                    self.model = self._load(self.model_name or self.chosen())
                except Exception as exc:
                    import traceback
                    # The whole trace: a bare FileNotFoundError said nothing about which file.
                    log("model load failed: %r\n%s" % (exc, traceback.format_exc()))
                    if job is not None:
                        job[1](None, "could not load the speech model (%s)" % exc)
                    continue
            self.used = time.time()
            if job is None:
                continue
            audio, done = job
            try:
                t0 = time.time()
                text = self._transcribe(self.model, audio)
                log("decoded %.1fs of audio in %.2fs" % (len(audio) / float(SAMPLE_RATE), time.time() - t0))
                done(text, None)
            except Exception as exc:
                import traceback
                log("decode failed:\n%s" % traceback.format_exc())
                done(None, str(exc))
            self.used = time.time()

    def _through_recorder(self, audio, done):
        t0 = time.time()
        try:
            text = self.recorder.decode(audio)
        except Exception as exc:
            log("decode: through the recorder failed (%r): here instead" % exc)
            return False
        if text is None:
            return False
        log("decoded %.1fs of audio through the recorder in %.2fs" % (len(audio) / float(SAMPLE_RATE), time.time() - t0))
        if self.model is not None:
            self._unload("the recorder has one")     # one copy: the recorder's
        done(text, None)
        return True

    def _unload(self, why=None):
        self.model = None
        import gc
        gc.collect()
        from native import speech
        speech.release()
        log("model unloaded: %s" % (why or "%.0f min unused" % (self.unload_after / 60.0)))


# --------------------------------------------------------------------------
# Microphone
# --------------------------------------------------------------------------

# makes what takes the Mac's own sound out of the mic while dictating
# (roomonly.for_dictation: a RoomOnly, or None). Tests put a fake here; else the
# real one, only once turned on ("room_only" in config.json, until the hands-on),
# and never in a test.
ROOM_ONLY = None
SCREEN = {"allowed": None}        # Screen & System Audio Recording, as check_permissions last saw it


def room_for_dictation():
    """The Mac's own sound, to take out of this dictation, or None. Returns at once."""
    if ROOM_ONLY is not None:
        return ROOM_ONLY()
    if os.environ.get("STANDBY_NO_DEVICES") or not config.load_config().get("room_only", False):
        return None
    try:
        import roomonly
        return roomonly.for_dictation(log, permitted=SCREEN["allowed"])
    except Exception as exc:
        log("room only: %r: the mic as it is" % exc)
        return None


class Capture(object):
    """The microphone, open only while dictating (so is macOS's orange dot).
    While dictating on speakers, what the Mac plays is taken out of it."""

    def __init__(self):
        self.blocks = []
        self.level = 0.0
        self._stream = None
        self.room = None                  # roomonly.RoomOnly while dictating, or None
        self.room_failed = False          # it raised on the audio thread: the mic as it is, stopped at stop()
        # During a call: the call's own mic, lent by the recorder (borrow), read instead of the device
        self.lent, self.lent_blocks, self.lending, self._lent_stop = None, [], False, None
        self.in_call = self.from_call = False   # the last dictation: during a call, and from its mic

    def start(self, room=True):
        """room: take the Mac's own sound out (a dictation); not for the moment the
        mic is opened only so macOS asks for it."""
        import numpy as np
        import sounddevice as sd
        import devices
        self.np = np                      # for the audio thread, without an import there
        # A long-running app: re-read the device list, or a headset plugged in
        # after launch is invisible (PortAudio lists devices once, at init).
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        wanted = config.load_config().get("mic_device")
        device = devices.resolve_device(wanted if isinstance(wanted, str) else None, "Me", log=log)
        self.blocks, self.level, self.room_failed = [], 0.0, False
        self.room = room_for_dictation() if room else None
        self._stream = sd.InputStream(device=device, channels=1, samplerate=SAMPLE_RATE,
                                      dtype="float32", blocksize=int(SAMPLE_RATE * 0.05),
                                      callback=self._callback)
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        block = indata[:, 0].copy()
        room = self.room
        if room is not None and not self.room_failed:
            # PortAudio's thread: never an exception here, never a wait. Cleaned, a block may
            # come back shorter or empty (it waits for the Mac's sound of its moment); the rest
            # comes with the next ones, and at stop.
            try:
                block = room.clean(block)
            except Exception:
                self.room_failed = True
        if len(block):
            self.blocks.append(block)
            self.level = max(self.level, float(self.np.sqrt(self.np.mean(block * block))))

    LENT_SETTLE = 0.15    # s with nothing more in the lent mic: its last words are in
    LENT_WAIT = 0.6       # s at most for them, at stop

    def borrow(self, path):
        """A call has the mic (path: its dictating.f32, or None): macOS gives a second reader
        of it near silence, so the call's own (echo cancelled) is read from path once the
        recorder writes it, and the device's audio is set aside. It starts with the call's
        last second, so nothing said since the key is lost. An older recorder never writes
        it, and the device's audio stands."""
        self.lent, self.lent_blocks, self.lending = (Path(path) if path else None), [], False
        if self.lent is None:
            return
        stop = self._lent_stop = threading.Event()
        threading.Thread(target=self._read_lent, args=(self.lent, stop), name="dictation-lent", daemon=True).start()

    def _read_lent(self, path, stop):
        import numpy as np
        fh, carry = None, b""
        while not stop.is_set():
            if fh is None:
                try:
                    fh = open(path, "rb")
                except OSError:
                    stop.wait(0.03)
                    continue
                self.lending = True
            data = fh.read()
            if data:
                data, carry = carry + data, b""
                whole = len(data) - len(data) % 4      # whole samples only: the rest waits for its bytes
                data, carry = data[:whole], data[whole:]
                block = np.frombuffer(data, dtype="float32").copy()
                self.lent_blocks.append(block)
                self.level = max(self.level, float(np.sqrt(np.mean(block * block))))
                self._lent_at = time.time()
            else:
                stop.wait(0.03)
        if fh is not None:
            fh.close()

    def take_level(self):
        level, self.level = self.level, 0.0
        return level

    def stop(self):
        import numpy as np
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log("closing the mic: %r" % exc)
        room, self.room = self.room, None
        if room is not None:              # the mic is closed: what the room still held, the last word
            try:                          # even after a failure: what it held comes back, raw if need be
                rest = room.flush()
                if len(rest):
                    self.blocks.append(rest)
            except Exception as exc:
                log("room only: %r" % exc)
            if self.room_failed or getattr(room, "broken", None):
                log("room only: it stopped during the dictation (%s): the mic as it was"
                    % (getattr(room, "broken", None) or "an error on the audio thread"))
            try:                          # a reference left running is a smaller loss than the dictation
                room.stop()
            except Exception as exc:
                log("room only: not stopped: %r" % exc)
        blocks, self.blocks = self.blocks, []
        lent, self.lent = self.lent, None
        self.in_call, self.from_call = lent is not None, bool(lent is not None and self.lending)   # for what the user is told
        if lent is not None:
            if self.lending:                  # its last words: until nothing more comes, briefly
                end = time.time() + self.LENT_WAIT
                while time.time() < end and time.time() - getattr(self, "_lent_at", 0.0) < self.LENT_SETTLE:
                    time.sleep(0.02)
            self._lent_stop.set()
            try:
                lent.unlink()                 # the user's words, on disk only while dictating
            except OSError:
                pass
            if self.lending:
                blocks, self.lent_blocks = self.lent_blocks, []
                log("dictation: from the call's own mic (%.1fs)" % (sum(len(b) for b in blocks) / float(SAMPLE_RATE)))
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype="float32")


CALL_HAS_MIC = "A call has the mic, so dictation couldn't hear you"


def heard_something(audio):
    """Enough sound to be worth decoding: 0.3 s, not dead quiet."""
    import numpy as np
    if len(audio) < 0.3 * SAMPLE_RATE:
        return False
    return float(np.sqrt(np.mean(audio * audio))) > 0.002


def meter(rms):
    """RMS to 0..1 on a dB scale: -60 dB is empty, -10 dB full."""
    import math
    if rms <= 0:
        return 0.0
    return max(0.0, min(1.0, (20 * math.log10(rms) + 60) / 50.0))


# --------------------------------------------------------------------------
# A meeting in progress
# --------------------------------------------------------------------------

class MeetingGuard(object):
    """While a meeting records the mic, keep what you dictate out of its
    transcript. It flips the same switch as the chat box's dictation (the
    session's `dictate` file) with its own number, so the chat box ignores the
    words. It never touches the switch while the chat box dictates or voice
    mode is on. Voice mode owns the switch, so it is told instead (the
    session's `dictating` file, our pid): it holds, and what you dictate into
    another app is no turn for Claude."""

    DICTATING = "dictating"

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.path = None
        self.seq = None
        self.told = None                 # the voice mode we told we are dictating

    def begin(self):
        self.path = None
        try:
            sid = (self.root / "daemon.ready").read_text().strip()
        except OSError:
            return                       # no meeting recording
        path = self.root / "sessions" / sid / "dictate"
        try:
            ctl = path.read_text().split()[:1]
        except OSError:
            ctl = []
        if ctl in (["voice"], ["hold"], ["floor"]):       # voice mode: listening, held, or Claude speaking
            try:
                told = path.with_name(self.DICTATING)
                told.write_text("%d\n" % os.getpid())
                self.told = told
            except OSError:
                pass
            return
        if ctl == ["on"]:
            return                       # the chat box is dictating
        self.seq = int(time.time() * 1000)
        try:
            path.write_text("on %d\n" % self.seq)
            self.path = path
        except OSError:
            pass

    def lent(self):
        """Where the call's own mic is lent while voice mode is told we dictate, or None."""
        return self.told.with_name(self.told.name + ".f32") if self.told is not None else None

    def end(self):
        if self.told is not None:
            try:
                if self.told.read_text().split() == [str(os.getpid())]:
                    self.told.unlink()
            except OSError:
                pass
            self.told = None
        if self.path is None:
            return
        try:
            if self.path.read_text().split() == ["on", str(self.seq)]:
                self.path.write_text("off %d\n" % self.seq)
        except OSError:
            pass
        self.path = None


# --------------------------------------------------------------------------
# Permissions and the Apple Dictation clash
# --------------------------------------------------------------------------


# macOS keeps a permission per build of an ad-hoc signed app, so after a reinstall
# the switch that is on in System Settings can be an older build's, and does nothing.
STALE_BUTTON = "It's on already"
STALE_HINT = "Switched on in System Settings, but still not allowed here? Click It's on already."


def bundle_id():
    """The bundle this runs as (Standby Dictation.app's now; Standby.app's once they are one)."""
    from native import app
    return app.bundle_id() or LABEL


def app_name():
    """The name System Settings lists this app by."""
    from native import app
    return app.name() or "Standby"


def stale_steps():
    return ("In System Settings > Privacy & Security > Accessibility, select %s, remove it with the minus button, "
            "then click Open Settings… and turn it on again." % app_name())


def screen_allowed():
    """Screen & System Audio Recording, for Standby Dictation, asking nothing."""
    try:
        from native import permissions
        return bool(permissions.screen_allowed())
    except Exception:
        return False


def ask_screen():
    """macOS's own prompt for it, once; after that its pane in System Settings."""
    try:
        ASKED_SCREEN.touch()
        from native import permissions
        permissions.ask_screen()
    except Exception as exc:
        log("screen permission: %r" % exc)


# What the setup window (and Standby's Settings) list, in the order the app asks.
PERMISSIONS = (
    ("mic", "Microphone", "hears you", "Microphone"),
    ("listen", "Input Monitoring", "notices the shortcut", "ListenEvent"),
    ("post", "Accessibility", "pastes the text", "Accessibility"),
)
# offered beside them only while room_only is on, and optional: it never holds the
# setup window open, nor the menu's first line, and macOS is asked only on its button.
OPTIONAL = (
    ("hear_mac", "Screen & System Audio Recording", "leaves out the Mac's sound, never the screen", "ScreenCapture"),
)
ASKED_SCREEN = ROOT / "dictation.asked-screen"   # macOS asked once: later, its pane in System Settings
WELCOMED = ROOT / "dictation.welcomed"     # the "Dictation is on" note was shown once


def granted(perm, key):
    return perm.get("mic") == "allowed" if key == "mic" else bool(perm.get(key))


def missing(perm):
    return [p[0] for p in PERMISSIONS if not granted(perm, p[0])]


def _shortcut(shortcut):
    """(its id, its words, its kind) from a preset's id or the whole settings (a
    recorded shortcut needs them: its words are its own)."""
    if isinstance(shortcut, dict):
        return shortcut.get("shortcut"), shortcut_label(shortcut), shortcut_kind(shortcut)
    preset = SHORTCUTS.get(shortcut, SHORTCUTS[DEFAULTS["shortcut"]])
    return shortcut, preset[0], "key" if shortcut in KEY_SHORTCUTS else "double"


def offered(perm):
    """The optional row is offered: room_only is on (check_permissions looked)."""
    return perm.get("hear_mac") is not None


def setup_rows(perm, shortcut="mickey"):
    """Each permission, granted or not, and what its button does: "ask" when
    macOS has not asked yet (it lists the app in System Settings only after
    asking), else "open" its pane in System Settings. Then the optional one,
    when offered."""
    rows = []
    shortcut = _shortcut(shortcut)[0]
    for key, name, why, pane in PERMISSIONS:
        ok = granted(perm, key)
        ask = key == "mic" and perm.get("mic") == "ask"
        if key == "listen" and shortcut in KEY_SHORTCUTS:
            why = "notices the F5 key"
        rows.append({"key": key, "name": name, "why": why, "ok": ok,
                     "action": None if ok else ("ask" if ask else "open"),
                     "button": None if ok else ("Allow" if ask else "Open Settings…"),
                     # switched on in System Settings yet not allowed: an older copy's entry
                     "alt": STALE_BUTTON if key == "post" and not ok else None,
                     "url": PANE + pane})
    if offered(perm):
        for key, name, why, pane in OPTIONAL:
            ok, ask = bool(perm.get(key)), not perm.get(key + "_asked")
            rows.append({"key": key, "name": name, "why": why, "ok": ok, "optional": True,
                         "action": None if ok else ("ask" if ask else "open"),
                         "button": None if ok else ("Allow" if ask else "Open Settings…"),
                         "url": PANE + pane})
    return rows


def optional_missing(perm):
    return [r["key"] for r in setup_rows(perm) if r.get("optional") and not r["ok"]]


def how_to_start(shortcut):
    """"Press F5", "Press Control twice", "Press ⌥ Space": the shortcut, in a sentence."""
    ident, label, _kind = _shortcut(shortcut)
    return "Press F5" if ident in KEY_SHORTCUTS else label


def menu_head(perm, shortcut, secure=None):
    """The menu's first line: ready and how to start, or what is missing. secure:
    who keeps keyboard input private now (Controller.secure), if anyone. A recorded
    combination is a hotkey: it needs no Input Monitoring, and private input does
    not stop it."""
    ident, label, kind = _shortcut(shortcut)
    hotkey = kind == "combo"
    if not perm.get("listen") and not hotkey:
        return "Needs Input Monitoring to see the shortcut"
    if secure and secure.get("on") and not secure.get("heard") and not hotkey:
        return "Can't hear the shortcut: keyboard input is private"
    if not perm.get("post"):
        return "Needs Accessibility to paste"
    if perm.get("mic") == "denied":
        return "Needs the microphone"
    if ident in KEY_SHORTCUTS:
        return "Ready: press F5"
    return "Ready: " + label[0].lower() + label[1:]


def ready_text(shortcut):
    return "Dictation is ready. %s in any app" % how_to_start(shortcut)


def should_welcome(ready, was_missing, welcomed):
    """Say "Dictation is on" when it just became ready: the last permission
    granted while the app watched, or the very first launch that needed nothing."""
    return bool(ready and (was_missing or not welcomed))


def _app():
    """The Mac's own parts (the menu-bar app, its keys, pasting, permissions): native/macos/dictation_app.py."""
    from native import impl
    return impl("dictation_app")


# --------------------------------------------------------------------------
# Running the current code
# --------------------------------------------------------------------------

REPO = HERE.parent
LAZY = ("speechmodel.py", "devices.py", "native/macos/dictation_app.py")   # imported later, not at launch
RESTART = ROOT / "dictation.restart"    # a note for the next copy: it is a restart, and what was open
RESTART_EXIT = 75                       # non-zero, so launchd's KeepAlive starts the program again
COMMAND = ROOT / "dictation.cmd"        # from Standby's Settings: "setup" brings up the setup window
IDLE_FOR = 3.0                          # after a dictation; its clipboard is put back 0.6 s after pasting
MIN_UP = 30.0                           # never restart into new code more often than this
STARTING = ROOT / "dictation.starting"  # launches that have not run long enough to count as started
HEALTHY_AFTER = 20.0                    # up this long, a launch went fine
CRASH_WINDOW = 600.0                    # failed launches closer together than this are one run of them
GIVE_UP_AFTER = 3                       # and after this many in a row, launchd is not asked again


def code_files(modules=None):
    """The source this app runs: dictation.py, what it imports later, and every
    module it has imported from this repo."""
    modules = sys.modules if modules is None else modules
    prefix = str(REPO) + os.sep
    out = {str(HERE / name) for name in ("dictation.py",) + LAZY}
    for m in list(modules.values()):
        f = getattr(m, "__file__", None)
        if isinstance(f, str) and f.endswith(".py") and f.startswith(prefix):
            out.add(f)
    return sorted(out)


def imports_ok(where=HERE, python=None, timeout=60):
    """(ok, why not): does the code on disk start? Imported in a fresh python
    first, so a half-merged file or a dependency that is not installed yet keeps
    this copy running, instead of swapping it for one that dies at launch."""
    code = "import sys; sys.path.insert(0, %r); import dictation, speechmodel, devices, native.macos.dictation_app" % str(where)
    try:
        r = subprocess.run([python or sys.executable, "-c", code], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "could not check it (%s)" % exc.__class__.__name__
    if r.returncode == 0:
        return True, ""
    lines = (r.stderr or r.stdout or "").strip().splitlines()
    return False, lines[-1] if lines else "exit status %d" % r.returncode


class CodeWatch(object):
    """Has the code under this app changed since it started?

    The app runs straight from the repo, so a merge to main changes the code
    beneath it. Nothing used to notice: the user's copy ran day-old code (no 🎤
    key) for a day, and Apple's dictation kept answering. check() runs every
    couple of seconds, off the main thread. Once the changed files have stopped
    changing (a merge writes them one by one) and the new code imports, `ready`
    is set, and the app restarts into it when idle. `problem` says why new code
    on disk is not being used.
    """

    SETTLE = 2.0

    def __init__(self, files=code_files, verify=imports_ok):
        self.files, self.verify = files, verify
        self._stat = {}
        self.loaded = self._digests()
        self.changed, self.since = (), None
        self.ready, self.problem = False, None
        self._verdicts = {}

    def _digests(self):
        """{path: sha1 of its bytes, or None if missing}. Read again only when
        its size or mtime moved."""
        out = {}
        for path in self.files():
            try:
                st = os.stat(path)
            except OSError:
                out[path] = None
                continue
            key = (st.st_mtime_ns, st.st_size)
            if self._stat.get(path, (None, None))[0] != key:
                try:
                    with open(path, "rb") as fh:
                        self._stat[path] = (key, hashlib.sha1(fh.read()).hexdigest())
                except OSError:
                    self._stat[path] = (key, None)
            out[path] = self._stat[path][1]
        return out

    def check(self, now):
        current = self._digests()
        for path, digest in current.items():
            self.loaded.setdefault(path, digest)      # first seen: what this copy runs
        changed = tuple(sorted((p, h) for p, h in current.items() if h != self.loaded[p]))
        if changed != self.changed:
            self.changed, self.since, self.ready, self.problem = changed, now, False, None
        elif changed and not self.ready and now - self.since >= self.SETTLE:
            if changed not in self._verdicts:
                self._verdicts[changed] = self.verify()
                ok, why = self._verdicts[changed]
                log("new code in %s%s" % (self.names(), "" if ok else ", but it does not start: %s" % why))
            ok, why = self._verdicts[changed]
            self.ready, self.problem = ok, (None if ok else why)
        return self.ready

    def names(self):
        return ", ".join(os.path.basename(p) for p, _h in self.changed)


def safe_to_restart(listening, pending, last_used, now, launched=0.0):
    """Never in the middle of a dictation, its decode, or its paste, and not
    again right after a restart, should the files keep changing."""
    return not listening and not pending and now - last_used >= IDLE_FOR and now - launched >= MIN_UP


# A crash at launch must not become a relaunch loop. Under launchd, KeepAlive
# starts the app again after every crash, so new code that dies at start (or an
# old bug) would be started again and again, forever. Each launch is noted here
# until it has run HEALTHY_AFTER seconds; a run of failed ones backs off, then
# stops, and says why.

def launch_begins(now=None, path=STARTING):
    """Note this launch. Returns how many launches right before it never got
    going (0: the last one went fine, or was long ago)."""
    now = time.time() if now is None else now
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError):
        prev = {}
    if not isinstance(prev, dict) or now - float(prev.get("at", 0)) > CRASH_WINDOW:
        prev = {}
    failed = int(prev.get("n", 0))
    try:
        path.write_text(json.dumps({"at": now, "n": failed + 1, "error": prev.get("error")}))
    except OSError:
        pass
    return failed


def launch_failed(error, path=STARTING):
    """Why this launch died, for the next one, Settings and `standby dictation`."""
    try:
        note = json.loads(path.read_text())
        note["error"] = str(error)[:300]
        path.write_text(json.dumps(note))
    except (OSError, ValueError):
        pass


def launch_went_fine(path=STARTING):
    try:
        path.unlink()
    except OSError:
        pass


def launch_backoff(failed):
    """Seconds to wait before trying again after `failed` failed launches in a
    row, or None: stop trying."""
    if failed >= GIVE_UP_AFTER:
        return None
    return 0 if not failed else 5 * 2 ** (failed - 1)


def gave_up(now=None, path=STARTING):
    """The reason it stopped trying, if it did (recently), else None."""
    now = time.time() if now is None else now
    try:
        note = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if int(note.get("n", 0)) <= GIVE_UP_AFTER or now - float(note.get("at", 0)) > CRASH_WINDOW:
        return None
    return note.get("error") or "it stopped right after starting"


def launchd_owns(pid):
    """Was this copy started by our login item (`standby dictation on`)?"""
    from native import service
    return service.started_by_service(LABEL, pid)


def relaunch_plan(pid, launchd, bundle=None, python=None):
    """How this copy hands over to new code: (exit status, argv of a helper to
    start first, or None).

    Started by launchd: exit non-zero, and KeepAlive starts the program again.
    Started by hand, nobody would: relaunch.py waits for this copy to exit, then
    opens the app the way Finder does (the permissions belong to the app bundle),
    or runs the script again when there is no app, trying again until the new
    copy holds the lock, and last of all starting launchd's job."""
    if launchd:
        return RESTART_EXIT, None
    py = python or sys.executable
    launch = ["/usr/bin/open", "-g", str(bundle)] if bundle else [py, str(HERE / "dictation.py"), "run"]
    return 0, [py, str(HERE / "relaunch.py"), "--old", str(pid), "--lock", str(LOCK),
               "--log", str(ROOT / "dictation.log"), "--job", LABEL, "--plist", str(_login_item()), "--"] + launch


def main_bundle():
    """This app's bundle when it runs as Standby Dictation.app, else None."""
    from native import app
    return app.path() if app.bundle_id() == LABEL else None


def note_restart(setup_open, path=RESTART):
    try:
        path.write_text(json.dumps({"at": time.time(), "setup": bool(setup_open)}))
    except OSError:
        pass


def take_restart_note(now=None, path=RESTART):
    """The note a restarting copy left, {"at", "setup"}, or None: a normal launch."""
    try:
        note = json.loads(path.read_text())
        path.unlink()
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict) or (now or time.time()) - float(note.get("at", 0)) > 120:
        return None
    return note


def take_command(path=COMMAND):
    """What Standby's Settings asked for since the last look, the oldest first (one
    per line: two clicks in half a second are both done), or None."""
    try:
        cmds = path.read_text().split()
        path.unlink()
    except OSError:
        return None
    return cmds or None


def send_command(cmd, path=COMMAND):
    """From Standby's Settings: added after any not taken yet, never over them."""
    with open(path, "a") as fh:
        fh.write(cmd + "\n")


def open_words(fixes=None, opener=("open", "-t")):
    """The words file in a text editor, made first with its instructions if new
    (Edit Word Fixes…, and Standby's Settings when this app is not running)."""
    (fixes or WordFixes()).apply("")
    subprocess.Popen(list(opener) + [str(WORDS)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def exit_now(code):
    """Straight out, skipping interpreter teardown: MLX and Cocoa can crash in
    there, and launchd would take that for a crash."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def running_pid():
    """Pid of the running instance, from the lock only it can hold."""
    import fcntl
    try:
        fh = open(LOCK, "a+")
    except OSError:
        return None
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh, fcntl.LOCK_UN)
        return None
    except OSError:
        fh.seek(0)
        try:
            return int(fh.read().strip() or 0) or None
        except ValueError:
            return None
    finally:
        fh.close()


def agent_program():
    exe = APP / "Contents" / "MacOS" / "Standby Dictation"
    if exe.exists():
        return [str(exe)]
    return [str(ROOT / "venv" / "bin" / "python"), str(HERE / "dictation.py"), "run"]


def _login_item():
    from native import service
    return service.definition_path(LABEL)


def at_login():
    """Is Standby Dictation set to start at login?"""
    from native import service
    return service.installed(LABEL)


def write_agent():
    """The login item: start at login, and again if it crashes (not after Quit)."""
    from native import service
    service.install(LABEL, agent_program(), ROOT / "dictation.log")


def cmd_on():
    from native import service
    launch_went_fine()                     # the user asks: forget earlier failed launches
    write_agent()
    if not service.stop(LABEL):
        log("launchctl: the login item was still going after 5 s; starting it anyway")
    pid = running_pid()
    if pid:                                # started by hand: make way for launchd's
        os.kill(pid, signal.SIGTERM)
    for _ in range(50):                    # the new one exits at once if the old holds the lock
        if not running_pid():
            break
        time.sleep(0.1)
    ok, said = service.start(LABEL)
    if not ok:
        # launchctl refuses outside the login session (ssh, some sandboxes). The
        # login item is written either way; start this once through Finder's way.
        log("launchctl bootstrap: %s; opening the app instead" % said)
        if APP.exists():
            subprocess.run(["open", "-g", str(APP)])
        else:
            with open(ROOT / "dictation.log", "a") as out:
                subprocess.Popen(agent_program(), stdout=out, stderr=out, start_new_session=True)
    for _ in range(100):
        if running_pid():
            break
        time.sleep(0.1)
    else:
        raise SystemExit("Standby Dictation did not start. Open it from ~/Applications (it also "
                         "starts at every login from now on); details in %s" % (ROOT / "dictation.log"))
    print("Standby Dictation is on: %s. It starts at login too." % shortcut_label(settings()))
    note = permissions_note(running_pid())
    if note:
        print(note)
    return 0


SETUP_NOTE = "A small window walks you through what macOS needs to allow, one step at a time."


def permissions_note(pid, wait=3.0):
    """`standby dictation on`'s line about permissions: while one is missing (or the app
    hasn't said yet), the setup window's; once all are allowed, None. The app writes
    them to STATUS as it starts."""
    end = time.time() + wait
    while True:
        try:
            st = json.loads(STATUS.read_text())
        except (OSError, ValueError):
            st = {}
        if pid and st.get("pid") == pid and isinstance(st.get("permissions"), dict):
            return SETUP_NOTE if missing(st["permissions"]) else None
        if time.time() >= end:
            return SETUP_NOTE
        time.sleep(0.1)


def cmd_off():
    from native import service
    service.stop(LABEL)                    # waited for, so an `on` straight after is clean
    service.uninstall(LABEL)
    pid = running_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not running_pid():
                break
            time.sleep(0.1)
    if sys.platform == "darwin" and (MIC_USAGE, F18_USAGE) in _app().read_mapping():
        _app().remap_mic_key(False)               # left behind by a crash: give the 🎤 key back
    print("Standby Dictation is off, and will not start at login.")
    return 0


def cmd_status():
    pid = running_pid()
    s = settings()
    print("Standby Dictation: %s" % (("running (pid %d)" % pid) if pid else "not running"))
    why = None if pid else gave_up()
    if why:
        print("  stopped:   it failed to start %d times in a row (%s)." % (GIVE_UP_AFTER, why))
        print("             Fixed? Run: standby dictation on")
    print("  shortcut:  %s%s" % (shortcut_label(s), ", or hold to talk" if s["hold"] else ""))
    print("  at login:  %s" % ("yes" if at_login() else "no"))
    try:
        st = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        st = {}
    if pid and st.get("pid") == pid:
        p = st.get("permissions", {})
        print("  input monitoring: %s   accessibility: %s   microphone: %s"
              % ("yes" if p.get("listen") else "NO", "yes" if p.get("post") else "NO", p.get("mic", "?")))
        if st.get("apple_dictation") and s["shortcut"] not in KEY_SHORTCUTS:
            print("  Apple Dictation is on too: turn it off in System Settings > Keyboard > Dictation,")
            print("  or give it another shortcut, or both start at once.")
        code = st.get("code") or {}
        if st.get("started"):
            print("  code:      %s" % (
                "new code in %s does not start, still on the old: %s" % (code["changed"], code["problem"])
                if code.get("problem") else "restarting into new code (%s)" % code["changed"]
                if code.get("pending") else "current, loaded %s" % time.strftime(
                    "%b %d %H:%M", time.localtime(st["started"]))))
        else:
            print("  code:      an older version, which does not update itself. Restart it once:")
            print("             standby dictation off && standby dictation on")
    elif pid:
        print("  status: none from this copy yet (an old version, or just starting).")
        print("  Quit it from its menu-bar mic, or: standby dictation off && standby dictation on")
    if s["shortcut"] in KEY_SHORTCUTS and sys.platform == "darwin":
        remapped = (MIC_USAGE, F18_USAGE) in _app().read_mapping()
        print("  F5 key:    %s" % ("goes to Standby Dictation" if remapped else "macOS's (not remapped)"))
    if sys.platform == "darwin" and _app().secure_input_on():
        pid = _app().secure_input_holder()
        what, fix = _app().secure_input_words(_app()._app_name(pid) if pid else None)
        print("  keyboard:  private now%s: %s." % (" (held by pid %d)" % pid if pid else "", what))
        print("             %s." % fix)
    print("  word fixes: %s" % WORDS)
    return 0


def main(argv=None):
    # Started by launchd, Finder or Standby's Settings, there is no locale, and
    # printing the 🎤 shortcut raised: the app died at launch.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "run":
        return _app().cmd_run()
    if cmd == "selftest":
        return _app().cmd_run(headless=True)
    if cmd == "on":
        return cmd_on()
    if cmd == "off":
        return cmd_off()
    if cmd == "status":
        return cmd_status()
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 1


if __name__ == "__main__":
    sys.exit(main())
