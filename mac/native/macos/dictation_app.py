"""Standby Dictation's own Mac parts: the menu-bar app (its menu, the listening panel, the
setup window), the shortcut's keys (the event tap, the Carbon hotkey, the mic key's remap),
pasting into the app in front (CGEvent, the pasteboard, Accessibility) and the permissions
these need. Everything else is dictation.py's, reached here as core: looked up when used,
so a stand-in a test puts there is the one used.
"""
import ctypes
import json
import os
import plistlib
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import config
import vocabulary

import dictation as core

# --------------------------------------------------------------------------
# When macOS keeps keyboard input private
# --------------------------------------------------------------------------

def event_time(event):
    """When a tap's event happened, in seconds on time.monotonic()'s clock (since
    startup). Through NSEvent: a CGEvent's own stamp is in machine ticks on Apple
    silicon, not nanoseconds. A made-up event has none: now, then."""
    try:
        from AppKit import NSEvent
        t = NSEvent.eventWithCGEvent_(event).timestamp()
        if t and t > 0:
            return float(t)
    except Exception:
        pass
    return time.monotonic()


def secure_input_on():
    """Is macOS's Secure Event Input on? While it is, no event tap sees a key press:
    only system-defined events (volume, media) get through, so no key shortcut can be
    heard by the tap. A password field turns it on while it has the focus; Terminal's
    Secure Keyboard Entry while that is ticked. loginwindow can keep it after a restart
    or a wake: on 2026-10-07 the 🎤 key was dead with the screen unlocked, the volume
    keys still logged, and loginwindow holding it. False if it cannot be told."""
    try:
        if "carbon" not in _LIBS:
            _LIBS["carbon"] = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        return bool(_LIBS["carbon"].IsSecureEventInputEnabled())
    except Exception:
        return False


_LIBS = {}                      # loaded once: secure_input_on runs every second


def secure_input_holder(run=subprocess.run):
    """The pid holding Secure Event Input, or None: the console user's entry in the
    IORegistry (about 25 ms). Only asked when it turns on."""
    try:
        out = run(["ioreg", "-a", "-d1", "-k", "IOConsoleUsers"], capture_output=True, timeout=5).stdout
        users = plistlib.loads(out).get("IOConsoleUsers") or []
    except Exception:
        return None
    for user in users:
        pid = user.get("kCGSSessionSecureInputPID")
        if pid:
            return int(pid)
    return None


def secure_input_words(app):
    """(what is wrong, what to do), for the menu, the status and the log."""
    if app == "loginwindow":
        # Locking and unlocking did not clear it on 2026-10-07; logging out does.
        return ("macOS is keeping keyboard input private, so the shortcut can't be heard",
                "Finish any password prompt; if there is none, log out and back in")
    return ("%s is keeping keyboard input private, so the shortcut can't be heard" % (app or "Another app"),
            "Leave its password field, or turn off its Secure Keyboard Entry")


def _app_name(pid):
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None and app.localizedName():
            return str(app.localizedName())
    except Exception:
        pass
    return "pid %d" % pid


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class CarbonHotKey(object):
    """A system-wide hotkey: Carbon's RegisterEventHotKey, through ctypes (pyobjc has
    no bindings for it). The WindowServer matches it itself, before any app or event
    tap: macOS takes the keys, so the app in front never sees them (⌥ Space types no
    space), and they should still be matched while keyboard input is private, the way
    ⌘ Space opens Spotlight from a password field. Press and release both come, for
    hold to talk. No permission. Another app holding the same keys is refused.

    on_edge(down) runs on the main run loop, inside Carbon's handler, so it only feeds
    the controller, as the tap's callback does. The C callback and the handler are
    kept for the app's life: freed, the first press would crash the app."""

    SIGNATURE = int.from_bytes(b"SbDc", "big")
    KEYBOARD = int.from_bytes(b"keyb", "big")       # kEventClassKeyboard
    PRESSED, RELEASED = 5, 6                         # kEventHotKeyPressed, kEventHotKeyReleased
    TAKEN = -9878                                    # eventHotKeyExistsErr
    NOT_MINE = -9874                                 # eventNotHandledErr: the next handler gets it
    DIRECT, HOTKEY_ID = int.from_bytes(b"----", "big"), int.from_bytes(b"hkid", "big")

    def __init__(self, on_edge, ident=1):
        """ident: this hotkey's number. Every handler sees every hotkey of the app,
        so each answers only for its own (the shortcut is 1, Return 2)."""
        self.on_edge, self.handler, self.callback, self.ref, self.keys = on_edge, None, None, None, None
        self.ident = ident

    @staticmethod
    def _lib():
        if "carbon_hotkeys" in _LIBS:
            return _LIBS["carbon_hotkeys"]
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        vp, i32, u32 = ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint32
        for name, res, args in (("GetEventDispatcherTarget", vp, []),
                                ("InstallEventHandler", i32, [vp, vp, u32, ctypes.POINTER(_EventTypeSpec), vp,
                                                              ctypes.POINTER(vp)]),
                                ("RegisterEventHotKey", i32, [u32, u32, _EventHotKeyID, vp, u32, ctypes.POINTER(vp)]),
                                ("UnregisterEventHotKey", i32, [vp]),
                                ("GetEventKind", u32, [vp]),
                                ("GetEventTime", ctypes.c_double, [vp]),
                                ("GetEventParameter", i32, [vp, u32, u32, ctypes.POINTER(u32), ctypes.c_size_t,
                                                            ctypes.POINTER(ctypes.c_size_t), vp])):
            fn = getattr(carbon, name)
            fn.restype, fn.argtypes = res, args
        _LIBS["carbon_hotkeys"] = carbon
        return carbon

    def register(self, keycode, mods):
        """(ok, why): this hotkey now, in place of any it had. Never raises."""
        if self.keys == (keycode, mods):
            return True, ""
        self.unregister()
        try:
            lib = self._lib()
            if self.handler is None:
                self.callback = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.c_void_p)(self._event)
                kinds = (_EventTypeSpec * 2)(_EventTypeSpec(self.KEYBOARD, self.PRESSED),
                                             _EventTypeSpec(self.KEYBOARD, self.RELEASED))
                handler = ctypes.c_void_p()
                err = lib.InstallEventHandler(lib.GetEventDispatcherTarget(), ctypes.cast(self.callback, ctypes.c_void_p),
                                              2, kinds, None, ctypes.byref(handler))
                if err:
                    return False, "macOS refused the hotkey's handler (%d)" % err
                self.handler, self._kinds = handler, kinds
            ref = ctypes.c_void_p()
            err = lib.RegisterEventHotKey(keycode, mods, _EventHotKeyID(self.SIGNATURE, self.ident),
                                          lib.GetEventDispatcherTarget(), 0, ctypes.byref(ref))
            if err:
                return False, "another app already uses it" if err == self.TAKEN else "macOS refused it (%d)" % err
            self.ref, self.keys = ref, (keycode, mods)
            return True, ""
        except Exception as exc:
            return False, "could not set it up (%r)" % exc

    def unregister(self):
        if self.ref is None:
            return
        try:
            self._lib().UnregisterEventHotKey(self.ref)
        except Exception as exc:
            core.log("hotkey: could not let go of it: %r" % exc)
        self.ref, self.keys = None, None

    def _which(self, lib, event):
        """The pressed hotkey's (signature, id), or None if it can't be read."""
        try:
            got = _EventHotKeyID()
            err = lib.GetEventParameter(event, self.DIRECT, self.HOTKEY_ID, None, ctypes.sizeof(got), None,
                                        ctypes.byref(got))
            return None if err else (got.signature, got.id)
        except Exception:
            return None

    def _event(self, _next, event, _data):
        try:
            lib = self._lib()
            which = self._which(lib, event)
            # Another hotkey's press goes on to its own handler. If it can't be told, only
            # the shortcut's (1) takes it, as before there was more than one.
            if which != (self.SIGNATURE, self.ident) and not (which is None and self.ident == 1):
                return self.NOT_MINE
            # When the key was pressed, not when this ran: seconds since startup, the
            # clock time.monotonic() reads (measured: the same to the microsecond).
            self.on_edge(lib.GetEventKind(event) == self.PRESSED, lib.GetEventTime(event) or time.monotonic())
        except Exception as exc:
            core.log("hotkey: %r" % exc)
        return 0                                     # noErr: handled


def _hidutil(*args):
    return subprocess.run(["hidutil"] + list(args), capture_output=True, text=True, timeout=10)


def read_mapping(run=_hidutil):
    """[(src, dst)] of the keyboard remaps in effect (hidutil prints them as an
    old-style plist, or "(null)" for none)."""
    pairs = []
    for block in re.findall(r"\{(.*?)\}", run("property", "--get", "UserKeyMapping").stdout, re.S):
        src = re.search(r"HIDKeyboardModifierMappingSrc\s*=\s*(\d+)", block)
        dst = re.search(r"HIDKeyboardModifierMappingDst\s*=\s*(\d+)", block)
        if src and dst:
            pairs.append((int(src.group(1)), int(dst.group(1))))
    return pairs


def remap_mic_key(on, run=_hidutil):
    """Add or remove our 🎤 -> F18 remap, keeping everyone else's. True if the
    remap is now as asked."""
    try:
        pairs = read_mapping(run)
        mine = (core.MIC_USAGE, core.F18_USAGE)
        if (mine in pairs) == on and all(p[0] != core.MIC_USAGE or p == mine for p in pairs):
            return True
        keep = [p for p in pairs if p[0] != core.MIC_USAGE] + ([mine] if on else [])
        body = json.dumps({"UserKeyMapping": [{"HIDKeyboardModifierMappingSrc": a,
                                               "HIDKeyboardModifierMappingDst": b} for a, b in keep]})
        return run("property", "--set", body).returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        core.log("hidutil: %r" % exc)
        return False


def key_down(key, flags):
    """Is modifier `key` down according to these event flags? None if we do not
    know that key (Caps Lock, or something new)."""
    bit = core.KEY_BIT.get(key)
    return None if bit is None else bool(flags & bit)


# --------------------------------------------------------------------------
# Typing into other apps
# --------------------------------------------------------------------------

V_KEY = 9       # kVK_ANSI_V: where V is on QWERTY, QWERTZ and AZERTY alike
RETURN_KEY = 36  # kVK_Return
SEND_WAIT = 0.8  # how long the text may take to appear in the field before Return
SEND_TAIL = 24   # its last characters are what is looked for (an app may change the start)


def post_keys(kind, text=None):
    """Send Cmd+V, or type `text`, to the app with keyboard focus. Needs
    Accessibility permission; without it macOS drops the events silently."""
    import Quartz
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    if kind == "paste":
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(src, V_KEY, down)
            Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        return
    if kind == "return":                   # what was dictated is sent, as if Return was pressed
        for down in (True, False):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(src, RETURN_KEY, down))
        return
    for i in range(0, len(text), 16):
        chunk = text[i:i + 16]
        units = len(chunk.encode("utf-16-le")) // 2
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(src, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(ev, units, chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.004)


def snapshot(pb):
    """Everything on a pasteboard, every type of every item, as raw data."""
    return [[(t, item.dataForType_(t)) for t in item.types()]
            for item in (pb.pasteboardItems() or [])]


def restore(pb, saved):
    from AppKit import NSPasteboardItem
    pb.clearContents()
    items = []
    for pairs in saved:
        item = NSPasteboardItem.alloc().init()
        for t, data in pairs:
            if data is not None:
                item.setData_forType_(data, t)
        items.append(item)
    if items:
        pb.writeObjects_(items)


# --------------------------------------------------------------------------
# Where the text goes: the field the dictation started in
# --------------------------------------------------------------------------
#
# On 2026-10-07 a crash dialog took the focus during a long dictation, and the stop
# press pasted the text into the dialog. Now the start press remembers where it
# started, and the text goes back there or nowhere: if the focus moved, that app,
# window and field are brought back first, and if they are gone it stays on the
# clipboard and the panel says so. Never into a password field.

# What the panel says when the text did not go where it started: it is on the
# clipboard. The words: what happened, then the same way out, one
# line each beside "Didn't catch that". Logged in ASCII by flash().
PASTE_GONE = "The window closed. Copied: ⌘V to paste"
PASTE_MOVED = "Couldn't switch back. Copied: ⌘V to paste"
PASTE_SECURE = "Not typed into a password field. Copied: ⌘V to paste"
# no Accessibility. The note stays, with its button, until it is dismissed, the next
# dictation starts, or the permission comes (then GRANTED, for a moment).
NO_POST = "Copied. Press ⌘V to paste."
NO_POST_BUTTON = "Let Standby paste"
GRANTED = "✓ Standby can paste now"


class Target(object):
    """Where a dictation started: the front app (pid, bundle id), its focused element
    and that element's window, as Accessibility references held until
    Focus.release. secure: a password field (AXSecureTextField)."""

    def __init__(self, pid, bundle, element=None, window=None, secure=False):
        self.pid, self.bundle, self.element, self.window, self.secure = pid, bundle, element, window, secure

    def __repr__(self):
        return "Target(%s, pid %s%s)" % (self.bundle or "?", self.pid, ", a password field" if self.secure else "")


class Focus(object):
    """The few Accessibility calls this needs, through ctypes: pyobjc's
    ApplicationServices bindings are not installed, and these are not worth a new
    dependency. Needs Accessibility, which pasting needs anyway; without it now()
    is None, and the text goes wherever the focus is, as it always did."""

    INVALID = -25202                 # kAXErrorInvalidUIElement: the element is gone
    WAIT = 0.5                       # for the focus to land after bringing it back
    TIMEOUT = 0.5                    # an Accessibility call to a hung app gives up after this
    ATTRS = ("AXFocusedUIElement", "AXFocusedWindow", "AXWindow", "AXSubrole", "AXFrontmost", "AXMain",
             "AXFocused", "AXRaise", "AXValue")

    def __init__(self):
        self._libs = None

    def _l(self):
        if self._libs is not None:
            return self._libs
        ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        vp, i32 = ctypes.c_void_p, ctypes.c_int32
        for lib, name, res, args in ((ax, "AXIsProcessTrusted", ctypes.c_bool, []),
                                     (ax, "AXUIElementCreateSystemWide", vp, []),
                                     (ax, "AXUIElementCreateApplication", vp, [i32]),
                                     (ax, "AXUIElementCopyAttributeValue", i32, [vp, vp, ctypes.POINTER(vp)]),
                                     (ax, "AXUIElementSetAttributeValue", i32, [vp, vp, vp]),
                                     (ax, "AXUIElementPerformAction", i32, [vp, vp]),
                                     (ax, "AXUIElementGetPid", i32, [vp, ctypes.POINTER(i32)]),
                                     (ax, "AXUIElementSetMessagingTimeout", i32, [vp, ctypes.c_float]),
                                     (cf, "CFEqual", ctypes.c_bool, [vp, vp]),
                                     (cf, "CFRelease", None, [vp]),
                                     (cf, "CFStringCreateWithCString", vp, [vp, ctypes.c_char_p, ctypes.c_uint32]),
                                     (cf, "CFStringGetCString", ctypes.c_bool, [vp, ctypes.c_char_p, ctypes.c_long,
                                                                                ctypes.c_uint32]),
                                     (cf, "CFGetTypeID", ctypes.c_ulong, [vp]),
                                     (cf, "CFStringGetTypeID", ctypes.c_ulong, [])):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = res, args
        names = {a: cf.CFStringCreateWithCString(None, a.encode(), 0x08000100) for a in self.ATTRS}  # kept
        system = ax.AXUIElementCreateSystemWide()
        ax.AXUIElementSetMessagingTimeout(system, self.TIMEOUT)     # the default for every element
        true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue")
        self._libs = (ax, cf, names, system, true)
        return self._libs

    # -- reading ------------------------------------------------------------
    def _copy(self, element, attr):
        """(error, a retained reference or None)."""
        ax, _cf, names, _sys, _t = self._l()
        out = ctypes.c_void_p()
        err = ax.AXUIElementCopyAttributeValue(element, names[attr], ctypes.byref(out))
        return err, (out.value if not err and out.value else None)

    def _string(self, ref):
        _ax, cf, _n, _s, _t = self._l()
        if not ref or cf.CFGetTypeID(ref) != cf.CFStringGetTypeID():
            return ""
        buf = ctypes.create_string_buffer(256)
        return buf.value.decode("utf-8", "replace") if cf.CFStringGetCString(ref, buf, 256, 0x08000100) else ""

    def _text(self, ref, most=1 << 16):
        """A CFString's text, however long (up to `most` characters), or ""."""
        _ax, cf, _n, _s, _t = self._l()
        if not ref or cf.CFGetTypeID(ref) != cf.CFStringGetTypeID():
            return ""
        size = 4 * most + 1
        buf = ctypes.create_string_buffer(size)
        return buf.value.decode("utf-8", "replace") if cf.CFStringGetCString(ref, buf, size, 0x08000100) else ""

    def field_text(self):
        """What the focused text field holds now, or None if it can't be read (no
        Accessibility, an app that doesn't say). To see a paste land."""
        try:
            ax, cf, _n, system, _t = self._l()
            err, el = self._copy(system, "AXFocusedUIElement")
            if el is None:
                return None
            try:
                err, val = self._copy(el, "AXValue")
                if val is None:
                    return None
                try:
                    return self._text(val)
                finally:
                    cf.CFRelease(val)
            finally:
                cf.CFRelease(el)
        except Exception:
            return None

    def _pid(self, element):
        ax = self._l()[0]
        pid = ctypes.c_int32(0)
        return pid.value if element and not ax.AXUIElementGetPid(element, ctypes.byref(pid)) else None

    def _front(self):
        """(pid, bundle id) of the app in front, from NSWorkspace."""
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return (int(app.processIdentifier()), str(app.bundleIdentifier() or "")) if app is not None else (None, "")

    def _app(self, pid):
        """The running app with that pid (NSRunningApplication), or None."""
        from AppKit import NSRunningApplication
        return NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)

    def now(self):
        """Where the focus is: a Target, or None if it can't be told (no
        Accessibility, nothing in front)."""
        try:
            ax, cf, _n, system, _t = self._l()
            if not ax.AXIsProcessTrusted():
                return None
            pid, bundle = self._front()
            if pid is None:
                return None
            _e, element = self._copy(system, "AXFocusedUIElement")
            if element and self._pid(element) != pid:           # an overlay of another app's: not the field
                cf.CFRelease(element)
                element = None
            window, secure = None, False
            if element:
                _e, window = self._copy(element, "AXWindow")
                _e, sub = self._copy(element, "AXSubrole")
                secure = self._string(sub) == "AXSecureTextField"
                if sub:
                    cf.CFRelease(sub)
            if window is None:
                app = ax.AXUIElementCreateApplication(pid)
                _e, window = self._copy(app, "AXFocusedWindow")
                cf.CFRelease(app)
            return Target(pid, bundle, element, window, secure)
        except Exception as exc:
            core.log("focus: could not tell where it is (%r)" % exc)
            return None

    def same(self, a, b):
        """The same app, and the same field (or no field known on either side)."""
        if a is None or b is None or a.pid != b.pid:
            return False
        if a.element is None or b.element is None:
            return a.element is None and b.element is None
        return bool(self._l()[1].CFEqual(a.element, b.element))

    def gone(self, t):
        """Has the app quit, or the field or its window gone away?"""
        app = self._app(t.pid)
        if app is None or app.isTerminated():
            return True
        for ref in (t.element, t.window):
            if ref is not None:
                err, sub = self._copy(ref, "AXSubrole")
                if sub:
                    self._l()[1].CFRelease(sub)
                if err == self.INVALID:
                    return True
        return False

    # -- bringing it back ---------------------------------------------------
    def restore(self, t, sleep=time.sleep):
        """(ok, why): that app in front again, its window raised and the field
        focused, and confirmed. why: "gone" (quit, or the window or field closed)
        or "moved" (it would not come back in time)."""
        try:
            if self.gone(t):
                return False, "gone"
            ax, cf, names, _sys, true = self._l()
            self._app(t.pid).activateWithOptions_(1 << 1)        # ignoring other apps
            element = ax.AXUIElementCreateApplication(t.pid)
            ax.AXUIElementSetAttributeValue(element, names["AXFrontmost"], true)
            cf.CFRelease(element)
            if t.window is not None:
                ax.AXUIElementSetAttributeValue(t.window, names["AXMain"], true)
                ax.AXUIElementPerformAction(t.window, names["AXRaise"])
            if t.element is not None:
                ax.AXUIElementSetAttributeValue(t.element, names["AXFocused"], true)
            end = time.time() + self.WAIT
            while True:
                now = self.now()
                landed = self.same(now, t)
                self.release(now)
                if landed:
                    return True, ""
                if time.time() >= end:
                    return False, "moved"
                sleep(0.025)
        except Exception as exc:
            core.log("focus: could not bring it back (%r)" % exc)
            return False, "moved"

    def release(self, t):
        if t is None:
            return
        try:
            cf = self._l()[1]
            for ref in (t.element, t.window):
                if ref:
                    cf.CFRelease(ref)
        except Exception:
            pass
        t.element = t.window = None


class Inserter(object):
    """Puts text where the cursor is, in whatever app has focus.

    paste (default): the text goes on the clipboard, Cmd+V is sent, and the
    clipboard is put back as it was, unless something new was copied meanwhile.
    Works in terminals and Electron apps (VS Code, Slack) as well as native ones,
    and is instant for long text. Clipboard managers are told to skip it
    (nspasteboard.org markers).
    type: the text arrives as keystrokes and the clipboard is never touched,
    but it is slower, and some apps drop characters that come that fast.
    """

    RESTORE_AFTER = 0.6       # the target app reads the clipboard on Cmd+V

    def __init__(self, pasteboard=None, post=post_keys, later=None):
        if pasteboard is None:
            from AppKit import NSPasteboard
            pasteboard = NSPasteboard.generalPasteboard()
        self.pb = pasteboard
        self.post = post
        if later is None:
            from PyObjCTools import AppHelper
            later = AppHelper.callLater
        self.later = later

    def press_return(self):
        self.post("return")

    def insert(self, text, how="paste"):
        if how == "type":
            self.post("type", text)
        else:
            self.paste(text)

    def insert_kept(self, text, how="paste"):
        """Insert, and leave the text on the clipboard whatever happens. While macOS
        keeps keyboard input private, whether a posted key still lands is unproven,
        and putting the clipboard back would then lose the text."""
        self.put(text)
        if how == "type":
            self.post("type", text)
        else:
            self.post("paste")

    def paste(self, text):
        saved = snapshot(self.pb)
        mine = self.put(text, transient=True)
        self.post("paste")

        def put_back():
            if self.pb.changeCount() == mine:
                restore(self.pb, saved)
        self.later(self.RESTORE_AFTER, put_back)

    def put(self, text, transient=False):
        from AppKit import NSPasteboardItem, NSPasteboardTypeString
        self.pb.clearContents()
        item = NSPasteboardItem.alloc().init()
        item.setString_forType_(text, NSPasteboardTypeString)
        if transient:
            for marker in ("org.nspasteboard.TransientType", "org.nspasteboard.AutoGeneratedType"):
                item.setString_forType_("", marker)
        self.pb.writeObjects_([item])
        return self.pb.changeCount()


def can_listen():
    import Quartz
    return bool(Quartz.CGPreflightListenEventAccess())
def can_post():
    """Accessibility, to paste. AXIsProcessTrusted as well: it follows the switch in
    System Settings at once, so a grant is seen without a restart."""
    import Quartz
    return bool(Quartz.CGPreflightPostEventAccess()) or ax_trusted()
# Tests put a stand-in here: the real one resets the user's own permission.
TCCUTIL = (os.environ.get("STANDBY_TCCUTIL") or "/usr/bin/tccutil").split()
def reset_post():
    """Clear this app's own Accessibility entry (tccutil, its bundle id, nothing else) and
    have macOS list it again, for this build. (ok, what tccutil said). Only on the user's
    click; the switch then turned on is seen at once, without a restart."""
    if os.environ.get("STANDBY_NO_DEVICES") and TCCUTIL == ["/usr/bin/tccutil"]:
        return False, "not in a test: it would reset the user's own permission"
    try:
        r = subprocess.run(TCCUTIL + ["reset", "Accessibility", core.bundle_id()], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=15)
        ok, said = r.returncode == 0, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        ok, said = False, repr(exc)
    core.log("accessibility: reset this app's entry: %s (%s)" % ("done" if ok else "failed", said[:200]))
    return ok, said
def ask_post():
    import Quartz
    Quartz.CGRequestPostEventAccess()
def ax_trusted():
    try:
        ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        ax.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(ax.AXIsProcessTrusted())
    except Exception:
        return False
_capture_device = []
def mic_status():
    """"allowed", "denied" or "ask" (not decided yet), for this app."""
    try:
        if not _capture_device:
            import objc
            objc.loadBundle("AVFoundation", {}, bundle_path="/System/Library/Frameworks/AVFoundation.framework")
            _capture_device.append(objc.lookUpClass("AVCaptureDevice"))
        st = _capture_device[0].authorizationStatusForMediaType_("soun")    # AVMediaTypeAudio
        return {0: "ask", 3: "allowed"}.get(int(st), "denied")
    except Exception:
        return "ask"
def apple_dictation_on():
    try:
        from CoreFoundation import CFPreferencesAppSynchronize, CFPreferencesCopyAppValue
        CFPreferencesAppSynchronize("com.apple.HIToolbox")     # else a long-lived app sees stale values
        return bool(CFPreferencesCopyAppValue("AppleDictationAutoEnable", "com.apple.HIToolbox"))
    except Exception:
        return False
def open_url(url):
    from AppKit import NSURL, NSWorkspace
    NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))
# --------------------------------------------------------------------------
# The menu-bar app
# --------------------------------------------------------------------------

try:
    import objc
    from Foundation import NSObject
    python_method = objc.python_method
except ImportError:                     # Linux, for the tests of the parts above
    objc, NSObject = None, object

    def python_method(f):
        return f


def _symbol(name):
    from AppKit import NSImage
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, "Standby Dictation")
    if img is not None:
        img.setTemplate_(True)
    return img


def screen_at(point, screens, main):
    """The screen you are working on: the one with the pointer. The main
    screen, for an app with no window of its own, can be another display."""
    for s in screens:
        f = s.frame()
        if f.origin.x <= point.x < f.origin.x + f.size.width and f.origin.y <= point.y < f.origin.y + f.size.height:
            return s
    return main or (screens[0] if screens else None)


_NOTE_BUTTON = []


def note_button_class():
    """The note's buttons: one click works without taking focus from the app
    you are in, the pointer over them, and a hover that the pill's fill follows."""
    if _NOTE_BUTTON:
        return _NOTE_BUTTON[0]
    from AppKit import NSButton, NSCursor, NSTrackingArea

    class StandbyNoteButton(NSButton):
        def acceptsFirstMouse_(self, _event):
            return True

        def resetCursorRects(self):
            self.addCursorRect_cursor_(self.bounds(), NSCursor.pointingHandCursor())

        def updateTrackingAreas(self):
            for area in list(self.trackingAreas()):
                self.removeTrackingArea_(area)
            # entered/exited, always, in the visible rect
            self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), 0x01 | 0x80 | 0x200, self, None))
            objc.super(StandbyNoteButton, self).updateTrackingAreas()

        def mouseEntered_(self, _event):
            hover = getattr(self, "on_hover", None)
            hover and hover(self, True)

        def mouseExited_(self, _event):
            hover = getattr(self, "on_hover", None)
            hover and hover(self, False)

    _NOTE_BUTTON.append(StandbyNoteButton)
    return StandbyNoteButton


def note_layout(pad_l, pad_r, text_w, button_w, close_w, gap, most):
    """The note's capsule: the text, the button, the ✕, `gap` apart. (width, text x,
    text width, button x, close x): as wide as all of it, up to `most`, the text
    giving way first."""
    w = pad_l + text_w + gap + button_w + gap + close_w + pad_r
    if w > most:
        text_w -= w - most
        w = most
    bx = pad_l + text_w + gap
    return int(round(w)), pad_l, text_w, bx, bx + button_w + gap


class HUD(object):
    """The small panel at the bottom of the screen, in the companion's look: a
    glass capsule (macOS 26; a material that follows light and dark before
    that), as wide as what it says. Listening (five red bars that follow the
    mic: your voice is being captured), transcribing (Standby's three bars, in a ripple), or a short
    note. It never takes focus, so the text still goes to the app you were in.
    It fades in, rising a little, and fades out; nothing else moves."""

    H, R = 44, 22
    PAD_L, PAD_R, GAP = 14, 18, 10
    MIN_W, MAX_W = 140, 360
    BAR_W, BAR_GAP, LOW, HIGH = 3, 2, 4.0, 16.0
    DOT, DOT_GAP = 4.0, 2.0     # transcribing: three dots in the spinner's slot
    PATTERN = (0.6, 1.0, 0.8, 0.5, 0.9)          # as the transcript's meter
    SPIN = 16
    RISE, FADE, EASE = 6, 0.15, 0.2
    # The companion's: every Space, stationary, not in
    # Cmd-` or Expose, over full-screen apps. Else: moved to the Space you are on.
    EVERYWHERE = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)
    TO_ACTIVE = (1 << 1) | (1 << 6) | (1 << 8)

    def __init__(self):
        import AppKit
        from AppKit import (NSBackingStoreBuffered, NSColor, NSFont, NSMakeRect, NSPanel, NSProgressIndicator,
                            NSStatusWindowLevel, NSTextField, NSView, NSVisualEffectView)
        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.MIN_W, self.H), 1 << 7, NSBackingStoreBuffered, False)   # borderless, non-activating
        p.setFloatingPanel_(True)                 # the companion's settings, all of them
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setLevel_(NSStatusWindowLevel)          # after setFloatingPanel_, which sets the floating level
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setIgnoresMouseEvents_(True)
        p.setHasShadow_(True)
        p.setHidesOnDeactivate_(False)
        p.setCanHide_(False)
        p.setReleasedWhenClosed_(False)
        p.setCollectionBehavior_(self.EVERYWHERE)
        self.behaviour, self.missed, self.spaces, self.reported = self.EVERYWHERE, False, 0, -1
        rect = NSMakeRect(0, 0, self.MIN_W, self.H)
        glass = getattr(AppKit, "NSGlassEffectView", None)
        if glass is not None:                     # macOS 26: the companion's glass
            fx = glass.alloc().initWithFrame_(rect)
            fx.setCornerRadius_(self.R)
            inner = NSView.alloc().initWithFrame_(rect)
            fx.setContentView_(inner)
        else:
            fx = inner = NSVisualEffectView.alloc().initWithFrame_(rect)
            fx.setMaterial_(6)                    # popover: follows light and dark (not the dark HUD one)
            fx.setBlendingMode_(0)                # behind the window
            fx.setState_(1)                       # always active
            fx.setWantsLayer_(True)
            fx.layer().setCornerRadius_(self.R)
            fx.layer().setMasksToBounds_(True)
        self.bars = []
        for _ in self.PATTERN:
            bar = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.BAR_W, self.LOW))
            bar.setWantsLayer_(True)
            bar.layer().setCornerRadius_(self.BAR_W / 2.0)
            inner.addSubview_(bar)
            self.bars.append(bar)
        # Transcribing: three indigo dots rising in turn, the orb's thinking (the user, 2026-10-11),
        # where a spinner was; the stock spinner stays, never shown.
        self.work = []
        for _ in range(3):
            bar = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.BAR_W, self.LOW))
            bar.setWantsLayer_(True)
            bar.layer().setCornerRadius_(self.BAR_W / 2.0)
            bar.setHidden_(True)
            inner.addSubview_(bar)
            self.work.append(bar)
        self._ripple = None
        self.spinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, self.SPIN, self.SPIN))
        self.spinner.setStyle_(1)                 # spinning
        self.spinner.setControlSize_(1)           # small
        self.spinner.setDisplayedWhenStopped_(False)
        inner.addSubview_(self.spinner)
        self.label = NSTextField.labelWithString_("")
        self.label.setFont_(NSFont.systemFontOfSize_weight_(13.0, 0.23))       # medium
        self.label.setTextColor_(NSColor.labelColor())
        inner.addSubview_(self.label)
        p.setContentView_(fx)
        # The window's shadow only with the glass (macOS 26), which shapes it to the capsule.
        # Before it, AppKit drew it around the whole window: a light box with an edge around
        # the pill (macOS 14). The material's own edge is enough there.
        p.setHasShadow_(glass is not None)
        self.panel, self.fx, self.inner, self.on, self.w = p, fx, inner, False, self.MIN_W
        self.note, self.note_views = None, []      # a note with a button, until dismissed
        self._watch_spaces()

    def _watch_spaces(self):
        """Another Space while it shows (talking, then a swipe to another
        full-screen app): the panel comes along."""
        try:
            from AppKit import NSWorkspace
            from Foundation import NSOperationQueue
            self._spaces = NSWorkspace.sharedWorkspace().notificationCenter().addObserverForName_object_queue_usingBlock_(
                "NSWorkspaceActiveSpaceDidChangeNotification", None, NSOperationQueue.mainQueue(),
                lambda _note: self.space_changed())
        except Exception as exc:
            core.log("panel: Space changes not followed (%r)" % exc)

    def space_changed(self):
        self.spaces += 1
        if self.on:
            self.panel.setFrameOrigin_(self._origin(self.w))
            self._order_in()

    def _report(self):
        p = self.panel
        core.log("panel: first show after %d Space change(s): on this Space %s, visible %s (behaviour %d)" % (
            self.spaces, "yes" if p.isOnActiveSpace() else "NO",
            "yes" if int(p.occlusionState()) & 2 else "NO", self.behaviour))   # NSWindowOcclusionStateVisible

    @staticmethod
    def later(seconds, fn):
        from Foundation import NSTimer
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(seconds, False, lambda _t: fn())

    def _screen(self):
        from AppKit import NSEvent, NSScreen
        return screen_at(NSEvent.mouseLocation(), NSScreen.screens() or [], NSScreen.mainScreen())

    def _origin(self, w):
        f = self._screen().visibleFrame()
        return (f.origin.x + (f.size.width - w) / 2.0, f.origin.y + 90)

    def _order_in(self):
        """In front, on the Space you are on, at every show. With every Space
        (the companion's) it showed on the first full-screen Space only:
        left on another Space, it goes out and comes in again here; still not
        here, it is moved to the active Space from then on, and the log says."""
        p = self.panel
        if p.isVisible() and not p.isOnActiveSpace():
            p.orderOut_(None)
        p.orderFrontRegardless()
        if self.reported != self.spaces:             # the first show on each Space: where it went
            self.reported = self.spaces
            self.later(0.3, self._report)
        if p.isOnActiveSpace():
            return
        if self.behaviour != self.TO_ACTIVE:
            core.log("panel: not on this Space with every Space set; moving it to the active Space from now on")
            self.behaviour = self.TO_ACTIVE
            p.setCollectionBehavior_(self.TO_ACTIVE)
            p.orderOut_(None)
            p.orderFrontRegardless()
        if not p.isOnActiveSpace() and not self.missed:
            self.missed = True                        # once: what the hands-on check reads
            core.log("panel: still not on this Space (behaviour %d, level %d)" % (self.behaviour, p.level()))

    def width(self, text_w, level, spin):
        """As wide as what it shows: padding, the bars or the spinner, the text."""
        w = self.PAD_L + text_w + self.PAD_R
        if level:
            w += len(self.PATTERN) * self.BAR_W + (len(self.PATTERN) - 1) * self.BAR_GAP + self.GAP
        if spin:
            w += self.SPIN + self.GAP
        return int(max(self.MIN_W, min(self.MAX_W, round(w))))

    def _indigo(self):
        """Standby's indigo, lighter in dark mode (the window's accent)."""
        import Quartz
        name = str(self.panel.effectiveAppearance().bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]) or "")
        r, g, b = (0x8b, 0x87, 0xff) if "Dark" in name else (0x50, 0x46, 0xe5)
        return Quartz.CGColorCreateGenericRGB(r / 255.0, g / 255.0, b / 255.0, 1.0)

    def dots(self, t):
        """The three dots at t seconds: each rises 3 pt and brightens in turn, the
        companion's thinking: (lift, alpha) for each."""
        import math
        out = []
        for i in range(3):
            p = ((t - i * 0.16) % 1.25) / 1.25
            up = math.sin(math.pi * p / 0.7) if p < 0.7 else 0.0
            out.append((3.0 * up, 0.4 + 0.6 * up))
        return out

    def _work(self, on, x=0):
        """The three dots in the spinner's slot, about 30 times a second while on (a
        second or two of transcribing); stopped and hidden otherwise."""
        import time
        from AppKit import NSMakeRect
        from Foundation import NSTimer
        if self._ripple is not None:
            self._ripple.invalidate()
            self._ripple = None
        for bar in self.work:
            bar.setHidden_(not on)
        if not on:
            return
        left = x + (self.SPIN - (3 * self.DOT + 2 * self.DOT_GAP)) / 2.0
        color, t0 = self._indigo(), time.time()

        def place(_timer=None):
            for i, (bar, (up, alpha)) in enumerate(zip(self.work, self.dots(time.time() - t0))):
                bar.setFrame_(NSMakeRect(left + i * (self.DOT + self.DOT_GAP), (self.H - self.DOT) / 2.0 + up, self.DOT, self.DOT))
                bar.setAlphaValue_(alpha)
        for bar in self.work:
            bar.layer().setBackgroundColor_(color)
            bar.layer().setCornerRadius_(self.DOT / 2.0)
        place()
        self._ripple = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1 / 30.0, True, place)

    def _drop_note(self):
        for v in self.note_views:
            v.removeFromSuperview()
        self.note, self.note_views = None, []
        self.panel.setIgnoresMouseEvents_(True)

    def _pill(self, button, hover):
        """The button's fill: white in dark, black in light, a little more on hover."""
        import Quartz
        dark = "Dark" in str(self.panel.effectiveAppearance().bestMatchFromAppearancesWithNames_(
            ["NSAppearanceNameAqua", "NSAppearanceNameDarkAqua"]) or "")
        a = (0.24 if hover else 0.16) if dark else (0.12 if hover else 0.08)
        button.layer().setBackgroundColor_(Quartz.CGColorCreateGenericGray(1.0 if dark else 0.0, a))

    def show_note(self, key, text, button, target, on_button, on_close):
        """A note that stays: the text, a button, a ✕. The panel takes clicks
        while it shows, never focus: a click doesn't take the app you are in."""
        from AppKit import (NSAttributedString, NSColor, NSFont, NSFontAttributeName,
                            NSForegroundColorAttributeName, NSImage, NSImageSymbolConfiguration, NSMakeRect)
        self.show(text)                            # the capsule, its fade in, the text
        Button = note_button_class()
        font = NSFont.systemFontOfSize_weight_(13.0, 0.3)          # semibold
        title = NSAttributedString.alloc().initWithString_attributes_(
            button, {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.labelColor()})
        b = Button.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 22))
        b.setBordered_(False)
        b.setAttributedTitle_(title)
        b.setWantsLayer_(True)
        b.layer().setCornerRadius_(11.0)
        b.setFrameSize_((title.size().width + 24, 22))              # 12 on each side
        b.setTarget_(target)
        b.setAction_(on_button)
        b.setAccessibilityLabel_(button)
        b.on_hover = self._pill
        self._pill(b, False)
        x = Button.alloc().initWithFrame_(NSMakeRect(0, 0, 20, 20))
        x.setBordered_(False)
        x.setImagePosition_(1)                     # image only
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_("xmark", "Dismiss")
        if img is not None:
            x.setImage_(img.imageWithSymbolConfiguration_(
                NSImageSymbolConfiguration.configurationWithPointSize_weight_(11.0, 0)))
        else:                                      # before SF Symbols (macOS 10.15)
            x.setTitle_("✕")
        x.setContentTintColor_(NSColor.secondaryLabelColor())
        x.on_hover = lambda v, on: v.setContentTintColor_(NSColor.labelColor() if on
                                                          else NSColor.secondaryLabelColor())
        x.setTarget_(target)
        x.setAction_(on_close)
        x.setAccessibilityLabel_("Dismiss")
        most = self.MAX_W + b.frame().size.width + 2 * self.GAP + 20
        w, tx, tw, bx, cx = note_layout(self.PAD_L, self.PAD_R - 6, self.label.fittingSize().width,
                                        b.frame().size.width, 20, 12, most)
        self.label.setFrame_(NSMakeRect(tx, (self.H - 18) / 2.0, tw + 2, 18))
        b.setFrameOrigin_((bx, (self.H - 22) / 2.0))
        x.setFrameOrigin_((cx, (self.H - 20) / 2.0))
        for v in (b, x):
            self.inner.addSubview_(v)
        self.w = w
        self.panel.setContentSize_((w, self.H))
        self.panel.setFrameOrigin_(self._origin(w))
        self.panel.invalidateShadow()
        self.note, self.note_views = key, [b, x]
        self.panel.setIgnoresMouseEvents_(False)
        self.panel.invalidateCursorRectsForView_(self.inner)

    def show(self, text, level=False, spin=False, front=True):
        from AppKit import NSAnimationContext, NSMakeRect
        if self.note is not None:                 # anything else shown replaces the note
            self._drop_note()
        self.label.setStringValue_(text)
        text_w = self.label.fittingSize().width
        w = self.width(text_w, level, spin)
        x = self.PAD_L
        indigo = self._indigo()                    # your voice in Standby's indigo, as the orb has it (the user)
        for i, bar in enumerate(self.bars):
            bar.setHidden_(not level)
            bar.layer().setBackgroundColor_(indigo)
            bar.setFrame_(NSMakeRect(x + i * (self.BAR_W + self.BAR_GAP), (self.H - self.LOW) / 2.0, self.BAR_W, self.LOW))
        if level:
            x += len(self.PATTERN) * self.BAR_W + (len(self.PATTERN) - 1) * self.BAR_GAP + self.GAP
        self.spinner.setHidden_(True)
        self._work(spin, x)
        if spin:
            x += self.SPIN + self.GAP
        self.label.setFrame_(NSMakeRect(x, (self.H - 18) / 2.0, w - x - self.PAD_R + 2, 18))
        at, self.w = self._origin(w), w
        self.panel.setContentSize_((w, self.H))
        if not front:
            self.panel.setFrameOrigin_(at)
            return
        if not self.on:                           # in: a fade and a 6 pt rise
            self.panel.setAlphaValue_(0.0)
            self.panel.setFrameOrigin_((at[0], at[1] - self.RISE))
            self._order_in()

            def grow(ctx):
                ctx.setDuration_(self.FADE)
                self.panel.animator().setAlphaValue_(1.0)
                self.panel.animator().setFrameOrigin_(at)
            NSAnimationContext.runAnimationGroup_completionHandler_(grow, None)
        else:
            self.panel.setFrameOrigin_(at)
            self._order_in()                      # e.g. Transcribing, after a swipe to another Space
        self.panel.invalidateShadow()
        self.on = True

    def heights(self, value):
        """The bars for a level 0..1: 4 to 16 pt, each by its share of the pattern."""
        v = max(0.0, min(1.0, float(value)))
        return [self.LOW + (self.HIGH - self.LOW) * v * k for k in self.PATTERN]

    def set_level(self, value):
        from AppKit import NSAnimationContext, NSMakeRect
        hs = self.heights(value)

        def move(ctx):
            ctx.setDuration_(self.EASE)
            for bar, h in zip(self.bars, hs):
                f = bar.frame()
                bar.animator().setFrame_(NSMakeRect(f.origin.x, (self.H - h) / 2.0, self.BAR_W, h))
        NSAnimationContext.runAnimationGroup_completionHandler_(move, None)

    def hide(self):
        from AppKit import NSAnimationContext
        if not self.on:
            return
        self.on = False
        if self.note is not None:
            self._drop_note()

        def fade(ctx):
            ctx.setDuration_(self.FADE)
            self.panel.animator().setAlphaValue_(0.0)

        def gone():
            if not self.on:                       # not shown again meanwhile
                self.panel.orderOut_(None)
                self._work(False)
        NSAnimationContext.runAnimationGroup_completionHandler_(fade, gone)


def edit_menu():
    """A menu-bar app has no main menu, and a text view gets Cmd-A, -C, -V and -Z
    only through one: an Edit menu, never shown (once)."""
    from AppKit import NSApp, NSMenu, NSMenuItem
    main = NSApp.mainMenu()
    if main is None:
        main = NSMenu.alloc().init()
        NSApp.setMainMenu_(main)
    if main.itemWithTitle_("Edit") is not None:
        return
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"),
                               ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
        edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
    item.setSubmenu_(edit)
    main.addItem_(item)


def ask_fix(text):
    """Fix Last Dictation…'s editor: the text, to correct by hand. The
    corrected text, or None if cancelled. Standby Dictation comes to the front
    for it, since the user asked for it from the menu."""
    from AppKit import NSAlert, NSApp, NSFont, NSMakeRect, NSScrollView, NSTextView
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Fix Last Dictation")
    alert.setInformativeText_("Correct the words it got wrong. They're fixed by themselves from then on.")
    alert.addButtonWithTitle_("Learn")
    alert.addButtonWithTitle_("Cancel")
    scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 380, 96))
    scroll.setHasVerticalScroller_(True)
    scroll.setBorderType_(2)                 # NSBezelBorder
    view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 380, 96))
    view.setFont_(NSFont.systemFontOfSize_(13))
    view.setRichText_(False)
    view.setAutomaticQuoteSubstitutionEnabled_(False)
    view.setAllowsUndo_(True)
    view.setString_(text)
    scroll.setDocumentView_(view)
    alert.setAccessoryView_(scroll)
    alert.window().setInitialFirstResponder_(view)
    edit_menu()
    NSApp.activateIgnoringOtherApps_(True)
    if alert.runModal() != 1000:             # NSAlertFirstButtonReturn: Learn
        return None
    return str(view.string())


class SetupWindow(object):
    """What is missing, where it cannot be missed. The menu-bar menu said so
    too, but nobody opens it, so the app never got Accessibility. Shown at
    launch while a permission is missing: each permission with a live
    checkmark and the button that fixes it. The app closes it once all are
    granted. Buttons send setupAction: to `target`, tagged with the row's
    index, and Later sends setupLater:."""

    W, M, ROW = 440, 20, 44

    def __init__(self, target, optional=False):
        from AppKit import (NSBackingStoreBuffered, NSButton, NSColor, NSFont, NSImageView, NSMakeRect,
                            NSTextField, NSView, NSWindow)
        shown = core.PERMISSIONS + (core.OPTIONAL if optional else ())
        self.optional = optional
        rows = len(shown) * self.ROW
        h = 16 + 28 + 12 + rows + 14 + 18 + 4 + 22 + 18 - 22      # the title, then the rows: no paragraph between
        w = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.W, h), 1 | 2, NSBackingStoreBuffered, False)   # titled, closable
        w.setTitle_("Standby Dictation")
        w.setReleasedWhenClosed_(False)
        w.setCollectionBehavior_(1 << 1)      # moves to the Space you are on
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.W, h))

        def label(text, size, x, y, width, weight=0.0, muted=False):
            f = NSTextField.labelWithString_(text)
            f.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
            f.setTextColor_(NSColor.secondaryLabelColor() if muted else NSColor.labelColor())
            f.setFrame_(NSMakeRect(x, y, width, size + 6))
            view.addSubview_(f)
            return f

        def button(title, action, tag=0):
            b = NSButton.buttonWithTitle_target_action_(title, target, action)
            b.setControlSize_(1)                                   # small
            b.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
            b.setTag_(tag)
            view.addSubview_(b)
            return b
        top = h - 18
        label("Allow dictation in any app", 15, self.M, top - 22, self.W - 2 * self.M, weight=0.3)
        self.icons, self.whys, self.buttons, self.alts = [], [], [], []
        y = 16 + 28 + 12 + rows - self.ROW
        for i, (_key, name, why, _pane) in enumerate(shown):
            icon = NSImageView.alloc().initWithFrame_(NSMakeRect(self.M, y + 13, 18, 18))
            view.addSubview_(icon)
            label(name, 13, self.M + 28, y + 22, 220, weight=0.23)
            self.whys.append(label(why, 11, self.M + 28, y + 6, 260, muted=True))     # its line, beside Open Settings…
            self.icons.append(icon)
            self.buttons.append(button("Open Settings…", "setupAction:", i))
            self._place(self.buttons[-1], y + 10)
            self.alts.append(button(core.STALE_BUTTON, "setupReset:", i))
            self._place(self.alts[-1], y + 10, beside=self.buttons[-1])
            self.alts[-1].setHidden_(True)
            self.alts[-1].setToolTip_(core.STALE_HINT)
            y -= self.ROW
        self.footer = label("", 11, self.M, 8, 320, muted=True)       # empty until a reset has something to say
        self.footer.setFrame_(NSMakeRect(self.M, 8, 320, 30))
        self.footer.setMaximumNumberOfLines_(2)
        self.footer.cell().setWraps_(True)
        self._place(button("Later", "setupLater:"), 16)
        w.setContentView_(view)
        self.window = w

    def _place(self, b, y, beside=None):
        b.sizeToFit()
        width = max(72.0, b.frame().size.width)
        right = beside.frame().origin.x - 6 if beside is not None else self.W - self.M
        b.setFrame_(((right - width, y), (width, b.frame().size.height)))

    def say(self, text, strong=False):
        """The footer: the hint, or how a reset went and what to do next."""
        from AppKit import NSColor
        self.footer.setStringValue_(text)
        self.footer.setTextColor_(NSColor.labelColor() if strong else NSColor.secondaryLabelColor())

    def update(self, rows):
        from AppKit import NSColor, NSImage
        for row, icon, why, b, alt in zip(rows, self.icons, self.whys, self.buttons, self.alts):
            # the optional row, not allowed yet: a dashed circle, so it reads apart from the three needed
            opt = row.get("optional") and not row["ok"]
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "checkmark.circle.fill" if row["ok"] else "circle.dashed" if opt else "circle",
                "Allowed" if row["ok"] else "Optional, not allowed" if opt else "Not allowed yet")
            icon.setImage_(img or NSImage.imageWithSystemSymbolName_accessibilityDescription_("circle", "Not allowed yet"))
            icon.setContentTintColor_(NSColor.systemGreenColor() if row["ok"] else NSColor.tertiaryLabelColor())
            why.setStringValue_(row["why"])
            b.setHidden_(row["ok"])
            if row["button"] and b.title() != row["button"]:
                b.setTitle_(row["button"])
                self._place(b, b.frame().origin.y)
                self._place(alt, b.frame().origin.y, beside=b)
            alt.setHidden_(not row.get("alt"))

    @property
    def visible(self):
        return bool(self.window.isVisible())

    def show(self):
        """In front, with the keyboard. An accessory app isn't let take the front by
        itself on macOS 14 and later (it opened behind other windows): so it floats
        above them while it is open, and asks for the front the new way, then the old."""
        from AppKit import NSApplication, NSFloatingWindowLevel
        if not self.visible:
            self.window.center()
        self.window.setLevel_(NSFloatingWindowLevel)
        app = NSApplication.sharedApplication()
        if app.respondsToSelector_("activate"):
            app.activate()
        app.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()

    def close(self):
        self.window.orderOut_(None)


class Controller(NSObject):
    """Owns everything and runs on the main thread: the event tap and the timer
    feed it, and results from the decoding thread come back through a queue."""

    def initHeadless_(self, headless):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.headless = headless
        self.settings = core.settings()
        self.gesture = core.make_gesture(self.settings)
        self.remapped = False
        self.next_remap = 0.0
        self.unusual_keys = 0
        self.engine = core.Engine(self.settings.get("model"),          # None: Settings > Speech
                             float(self.settings["unload_minutes"]) * 60.0)
        self.capture = core.Capture()
        self.guard = core.MeetingGuard()
        self.fixes = core.WordFixes()
        # sound-alikes of the user's own terms, counted on a thread of its own once started.
        self.learner = vocabulary.Learner(core.WORDS, on_change=self.suggestions_changed)
        self.results = queue.Queue()
        self.inserter = None
        self.listening, self.mode, self.started = False, None, 0.0
        self.pending = 0
        self.last_text = ""
        self.hud = HUD()
        self.hud_hide_at = None
        self.tap = None
        self.focus = Focus()              # where a dictation started, and getting back there
        self.target = None                # the listening one's: a Target, or None
        self.hotkey = CarbonHotKey(self.on_hotkey)   # F18 (the 🎤 key), or a recorded combination
        # Return while listening finishes the dictation and sends it. Only while
        # listening, so the app in front never gets that Return before the text; never
        # in the headless app (a test must not take the user's Return key).
        self.return_key = None if headless else CarbonHotKey(self.on_return, ident=2)
        self.submit_next = False          # this dictation ends with Return: press it after the paste
        self.sending = None               # {"text", "until"}: waiting for the paste to land, then Return
        self.hotkey_state = None          # how registering it went: {"label", "ok", "why", "at"}
        self.edges = {}                   # (source, down) -> when: one press heard twice is one
        # Who keeps keyboard input private (secure_input_on), and whether the 🎤 key was
        # heard through the hotkey meanwhile, which means the shortcut still works.
        self.secure = {"on": False, "pid": None, "app": None, "heard": False}
        self.next_secure = 0.0
        self.perm = {"listen": False, "post": False, "mic": "ask"}
        self.asked = set()
        self.next_check = 0.0
        self.item = None
        self.setup = None                 # the setup window, made when first needed
        self.was_missing = False          # a permission was missing earlier in this run
        self.watch = None                 # CodeWatch, from start()
        self.restarting = False
        self.launched = time.time()
        self.last_used = 0.0              # the last dictation's end, for a safe restart
        self.code_state = None            # what write_status last said about new code
        self.next_cmd = 0.0
        self.healthy = False              # up long enough that this launch went fine
        self.close_setup_at = None        # all granted: the setup window goes at this time
        self.welcome_at = None            # and the panel says it is ready at this time
        return self

    # -- startup ------------------------------------------------------------
    @python_method
    def start(self):
        from AppKit import NSRunLoop, NSRunLoopCommonModes, NSStatusBar, NSTimer, NSVariableStatusItemLength
        self.inserter = Inserter()
        self.engine.start()
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.refresh_icon()
        self.apply_remap()
        self.sync_hotkey()
        from AppKit import NSWorkspace, NSWorkspaceDidWakeNotification
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, "didWake:", NSWorkspaceDidWakeNotification, None)
        note = core.take_restart_note()
        self.check_permissions(ask=True)
        # Missing permissions: say so up front, not only in the menu. A restart
        # into new code brings the window back only if it was open.
        if core.missing(self.perm) and (note is None or note.get("setup")):
            self.show_setup()
        self.rebuild_menu()
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(0.05, self, "tick:", None, True)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self.timer = timer
        self.learner.start()
        self.watch = core.CodeWatch()
        threading.Thread(target=self.watch_code, name="code-watch", daemon=True).start()
        core.log("ready: %s%s%s" % (core.shortcut_ascii(self.settings),
                               ", hold to talk" if self.settings["hold"] else "",
                               " (restarted into new code)" if note else ""))

    @python_method
    def watch_code(self):
        """Off the main thread: the checks read files and may run a python."""
        while not self.restarting:
            try:
                self.watch.check(time.time())
            except Exception as exc:
                core.log("code watch: %r" % exc)
            time.sleep(2.0)

    @python_method
    def apply_remap(self):
        """The 🎤 key remap, on exactly while it is the shortcut."""
        if self.headless:
            return
        want = self.settings["shortcut"] in core.KEY_SHORTCUTS
        if not want and not self.remapped:
            return
        ok = remap_mic_key(want)
        if want and not ok:
            core.log("could not remap the F5 key (hidutil failed); pick another shortcut")
        elif ok and want != self.remapped:
            core.log("F5 key %s" % ("now starts Standby Dictation (sent as F18)" if want else "back to macOS"))
        self.remapped = want and ok
        self.next_remap = time.time() + 300

    def didWake_(self, _note):
        # A keyboard that reconnects after sleep can come back without the remap; a
        # wake can leave macOS keeping keyboard input private; and a tap macOS turned
        # off while asleep is turned on again.
        core.log("woke")
        self.apply_remap()
        self.check_secure_input()
        self.revive_tap()

    @python_method
    def revive_tap(self):
        if self.tap is None:
            return
        import Quartz
        if not Quartz.CGEventTapIsEnabled(self.tap):
            Quartz.CGEventTapEnable(self.tap, True)
            core.log("the keyboard watch was off: on again")

    @python_method
    def check_secure_input(self):
        """Notices macOS keeping keyboard input private, and says so: in the log, the
        menu and the status. Checked every second (a cheap call); who holds it is only
        looked up when it turns on."""
        on = secure_input_on()
        if on == self.secure["on"]:
            return
        if on:
            pid = secure_input_holder()
            app = _app_name(pid) if pid else None
            self.secure = {"on": True, "pid": pid, "app": app, "heard": False}
            what, fix = secure_input_words(app)
            core.log("keyboard input is private now (held by %s): %s. %s" % (
                "%s, pid %d" % (app, pid) if pid else "an unknown app", what, fix))
        else:
            self.secure = {"on": False, "pid": None, "app": None, "heard": False}
            core.log("keyboard input is no longer private: the shortcut is heard again")
        self.rebuild_menu()
        self.refresh_icon()
        self.write_status()

    @python_method
    def wanted_hotkey(self):
        """(keycode, Carbon modifiers) the shortcut needs as a hotkey, or None: F18
        while the 🎤 key is the shortcut, a recorded combination's own keys."""
        s = self.settings
        if s["shortcut"] in core.KEY_SHORTCUTS:
            return core.F18_KEY, 0
        if core.shortcut_kind(s) == "combo":
            return s["custom"]["keycode"], s["custom"]["carbon"]
        return None

    @python_method
    def sync_hotkey(self):
        """The hotkey the shortcut needs, and no other. Never in the headless app.
        How it went is in the status ("hotkey_state"): Standby's Settings waits
        for it after recording a shortcut, since only registering it can tell
        whether another app holds those keys."""
        want = None if self.headless else self.wanted_hotkey()
        if want == self.hotkey.keys:
            if want is not None and self.hotkey_state:
                self.hotkey_state = dict(self.hotkey_state, at=time.time())    # still ours: say so again
                self.write_status()
            return
        what = core.shortcut_ascii(self.settings)
        if want is None:
            self.hotkey.unregister()
            self.hotkey_state = None
            core.log("no hotkey now")
        else:
            ok, why = self.hotkey.register(*want)
            self.hotkey_state = {"label": core.shortcut_label(self.settings), "ok": ok, "why": why, "at": time.time()}
            core.log("hotkey for %s: %s" % (what, "on" if ok else why))
        self.write_status()

    @python_method
    def on_hotkey(self, down, when=None):
        if self.settings["shortcut"] not in core.KEY_SHORTCUTS:
            if core.shortcut_kind(self.settings) == "combo":
                core.log("the shortcut %s (hotkey)" % ("down" if down else "up"))
                cmd = self.gesture.feed("key", time.time(), core.HOTKEY, down)
                if cmd:
                    self.on_command(cmd)
            return
        if self.secure["on"] and not self.secure["heard"]:
            self.secure["heard"] = True
            core.log("the mic key is heard through the hotkey while keyboard input is private: it still works")
            self.rebuild_menu()
            self.write_status()
        cmd = self.mic_key(down, "hotkey", when if when is not None else time.monotonic())
        if cmd:
            self.on_command(cmd)

    @python_method
    def mic_key(self, down, source, now):
        """One edge of the 🎤 key, from the event tap (as F18) or the hotkey. If both
        hear the same press, the second within EDGE_SAME s is that press again, not a
        new one. Every press is logged, never silent again.

        `now` is when the key was pressed (the event's own time, on time.monotonic's
        clock), never when it was handled: on 2026-10-07 the hotkey's copy of a press
        was handled a second late, while the main thread opened the mic, and counted
        as a new press."""
        heard = [t for (src, dn), t in self.edges.items() if src != source and dn == down]
        self.edges[(source, down)] = now
        if any(abs(now - t) <= core.EDGE_SAME for t in heard):    # either may be handled first
            return None
        core.log("the mic key %s (%s)" % ("down" if down else "up", "event tap" if source == "tap" else source))
        # The gesture runs on the wall clock, as every other feed and tick_'s poll do:
        # the press's own time, moved onto it.
        return self.gesture.feed("key", time.time() - (time.monotonic() - now), core.F18_KEY, down)

    @python_method
    def check_permissions(self, ask=False):
        """Ask for one permission at a time, in the setup window's order. Called
        every couple of seconds (every second while the setup window is open),
        so a grant in System Settings takes effect without a restart."""
        before = dict(self.perm)
        self.perm["listen"] = can_listen()
        self.perm["post"] = can_post()
        self.perm["mic"] = mic_status()
        self.perm["apple"] = apple_dictation_on()    # its menu item comes and goes
        if config.load_config().get("room_only", False):     # offered, never asked here
            self.perm["hear_mac"] = core.SCREEN["allowed"] = core.screen_allowed()
            self.perm["hear_mac_asked"] = core.ASKED_SCREEN.exists()
            try:
                import roomonly
                roomonly.warm()            # off the key press: Core Audio's first answers are slow
            except Exception:
                pass
        else:
            self.perm.pop("hear_mac", None)
            self.perm.pop("hear_mac_asked", None)
        if self.tap is None and self.perm["listen"]:
            self.install_tap()
        if ask and not self.headless:
            import Quartz
            if self.perm["mic"] == "ask" and "mic" not in self.asked and not self.listening:
                self.asked.add("mic")
                self.ask_mic()
            elif not self.perm["listen"] and "listen" not in self.asked:
                self.asked.add("listen")
                Quartz.CGRequestListenEventAccess()
            elif self.perm["listen"] and not self.perm["post"] and "post" not in self.asked:
                self.asked.add("post")
                Quartz.CGRequestPostEventAccess()
        if self.perm != before or not core.STATUS.exists():
            core.log("permissions: input monitoring %s, accessibility %s, microphone %s"
                % ("yes" if self.perm["listen"] else "NO", "yes" if self.perm["post"] else "NO",
                   self.perm["mic"]))
            self.rebuild_menu()
            self.refresh_icon()
            self.write_status()
        self.after_permissions()

    @python_method
    def after_permissions(self):
        """The setup window follows the permissions live, and goes once all are
        granted; the first time it all works, the panel says so."""
        lacking = core.missing(self.perm)
        now = time.time()
        open_ = self.setup is not None and self.setup.visible
        if self.setup is not None:
            self.setup.update(core.setup_rows(self.perm, self.settings))
        if open_ and not lacking and not core.optional_missing(self.perm) and self.close_setup_at is None:
            self.close_setup_at = now + 0.8          # all three checkmarks, for a moment
        ready = not lacking and (self.remapped or self.settings["shortcut"] not in core.KEY_SHORTCUTS)
        if not self.headless and core.should_welcome(ready, self.was_missing, core.WELCOMED.exists()):
            self.welcome_at = self.close_setup_at or now
            try:
                core.WELCOMED.touch()
            except OSError:
                pass
        self.was_missing = bool(lacking)

    @python_method
    def show_setup(self):
        if self.headless:
            return
        if self.setup is None or self.setup.optional != core.offered(self.perm):   # the optional row came or went
            if self.setup is not None:
                self.setup.close()
            self.setup = SetupWindow(self, optional=core.offered(self.perm))
        self.setup.update(core.setup_rows(self.perm, self.settings))
        self.close_setup_at = None
        self.setup.show()
        self.write_status()

    def setupReset_(self, _sender):
        """It's on already: the switch is an older build's. Clear this app's own entry,
        have macOS list it again, and open the pane to turn it on: seen at once."""
        ok, _said = reset_post()
        if ok:
            ask_post()                             # lists this build again
            open_url(core.PANE + "Accessibility")
            said = "Reset. Now turn %s on again in the list." % core.app_name()
        else:
            said = "macOS didn't reset it. " + core.stale_steps()
        if self.setup is not None:
            self.setup.say(said, strong=True)
        self.next_check = 0.0

    def setupLater_(self, _sender):
        if self.setup is not None:
            self.setup.close()
        self.write_status()

    def setupAction_(self, sender):
        rows = core.setup_rows(self.perm, self.settings)
        row = rows[int(sender.tag())] if 0 <= int(sender.tag()) < len(rows) else None
        if row is None or row["ok"]:
            return
        if row["action"] == "ask" and row["key"] == "hear_mac":
            core.ask_screen()                   # macOS's own prompt, on the user's click only
        elif row["action"] == "ask":
            self.asked.add(row["key"])
            self.ask_mic()
        else:
            open_url(row["url"])
        self.next_check = 0.0              # look again at once

    def showSetup_(self, _sender):
        self.show_setup()

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
        """Opened again from Finder or Spotlight while running: show what is
        missing, if anything (there is no other window)."""
        if core.missing(self.perm):
            self.show_setup()
        else:
            self.flash(core.ready_text(self.settings))
        return False

    @python_method
    def ask_mic(self):
        """Opening the mic for a moment is what makes macOS ask, and it is better
        asked now than on the first dictation, which would record silence."""
        try:
            self.capture.start(room=False)
            self.capture.stop()
        except Exception as exc:
            core.log("mic check: %r" % exc)

    @python_method
    def install_tap(self):
        import Quartz
        mask = 0
        for t in (Quartz.kCGEventFlagsChanged, Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp,
                  Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown, Quartz.kCGEventOtherMouseDown,
                  core.SYSTEM_DEFINED):
            mask |= 1 << t
        controller = self

        def callback(proxy, etype, event, refcon):
            try:
                controller.on_event(etype, event)
            except Exception:
                import traceback
                core.log("event handler:\n%s" % traceback.format_exc())
            return event
        tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                      Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
        if tap is None:
            core.log("could not watch the keyboard yet (Input Monitoring)")
            return False
        src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        self.tap, self._tap_src, self._tap_cb = tap, src, callback
        core.log("watching for the shortcut")
        return True

    # -- input ----------------------------------------------------------------
    @python_method
    def on_event(self, etype, event):
        import Quartz
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)       # macOS turns slow taps off
            return
        now = time.time()
        if etype == Quartz.kCGEventFlagsChanged:
            key = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            cmd = self.gesture.feed("mod", now, key, key_down(key, Quartz.CGEventGetFlags(event)))
        elif etype in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            key = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            down = etype == Quartz.kCGEventKeyDown
            if key == core.F18_KEY:
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    return                    # held: macOS repeats it; the hotkey does not
                cmd = self.mic_key(down, "tap", event_time(event))
            else:
                if down and key >= 0x80:
                    self.note_unusual("key code %d" % key)
                cmd = self.gesture.feed("key", now, key, down)
        elif etype == core.SYSTEM_DEFINED:
            from AppKit import NSEvent
            ns = NSEvent.eventWithCGEvent_(event)
            if ns.subtype() != core.MOUSE_BUTTONS:      # every click sends one: not worth a log line
                self.note_unusual("system key, subtype %d data %#x" % (ns.subtype(), ns.data1()))
            return
        else:
            cmd = self.gesture.feed("mouse", now)
        if cmd:
            self.on_command(cmd)

    @python_method
    def note_unusual(self, what):
        """Special keys (media, 🎤, F-row oddities), never ordinary typing: the
        first few go to the log, to tell what a key sends if the 🎤 remap misses."""
        if self.unusual_keys < 12:
            self.unusual_keys += 1
            core.log("special key seen: %s" % what)

    @python_method
    def on_command(self, cmd):
        core.log("shortcut: %s, %s" % (cmd, "listening" if self.listening else "idle"))
        if cmd == "toggle":
            if self.listening:
                self.stop_listening()
            else:
                self.start_listening("toggle")
        elif cmd == "hold_start" and not self.listening:
            self.start_listening("hold")
        elif cmd == "hold_end" and self.listening and self.mode == "hold":
            self.stop_listening()
        elif cmd == "cancel" and self.listening and self.mode == "hold":
            self.cancel_listening()
        elif cmd == "press":
            if self.listening:
                self.stop_listening()
            else:
                self.start_listening("press")
        elif cmd == "release_held" and self.listening and self.mode == "press":
            self.stop_listening()

    # -- a dictation ----------------------------------------------------------
    @python_method
    def start_listening(self, mode):
        try:
            self.capture.start()
        except Exception as exc:
            core.log("mic failed: %r" % exc)
            self.flash("Microphone unavailable")
            return
        self.listening, self.mode, self.started = True, mode, time.time()
        self.submit_next = False
        self.hold_return(True)
        # Where this dictation started: the text goes back there, or nowhere.
        self.focus.release(self.target)
        self.target = self.focus.now() if can_post() else None
        core.log("listening (%s)%s" % (mode, ", in %s" % self.target.bundle if self.target else ""))
        self.engine.warm()                 # load the model while you talk
        self.guard.begin()
        self.capture.borrow(self.guard.lent())      # a call has the mic: its own, lent by the recorder
        self.sound("Tink")
        self.hud.show("Listening", level=True)
        self.hud_hide_at = None
        self.refresh_icon()
        self.write_status()

    @python_method
    def hold_return(self, on):
        """Return is ours while listening, and the app's again the moment it ends,
        whichever way it ends."""
        if self.return_key is None:
            return
        if on:
            ok, why = self.return_key.register(RETURN_KEY, 0)
            if not ok:
                core.log("return: not taken while listening (%s): it reaches the app as ever" % why)
        else:
            self.return_key.unregister()

    @python_method
    def on_return(self, down, when=None):
        """Return pressed while listening: finish, paste, then press Return in the app."""
        if not down or not self.listening:
            return
        core.log("return: finish the dictation, and send it")
        self.submit_next = True
        self.stop_listening()

    @python_method
    def stop_listening(self):
        self.hold_return(False)
        submit, self.submit_next = self.submit_next, False
        audio = self.capture.stop()
        self.listening, self.last_used = False, time.time()
        core.log("stopped after %.1fs" % (self.last_used - self.started))
        self.guard.end()
        self.sound("Pop")
        if not core.heard_something(audio):
            self.focus.release(self.target)
            self.target = None
            self.refresh_icon()
            # A call holding the mic, and its own not lent (a recorder from before this): say so,
            # not a vague "didn't hear"
            self.flash(core.CALL_HAS_MIC if self.capture.in_call and not self.capture.from_call else "Didn't hear anything")
            return
        self.pending += 1
        self.hud.show("Transcribing…", spin=True)
        self.hud_hide_at = None
        self.refresh_icon()
        results, target, self.target = self.results, self.target, None
        self.engine.submit(audio, lambda text, err: results.put((text, err, target, submit)))
        self.write_status()

    @python_method
    def cancel_listening(self):
        self.hold_return(False)
        self.submit_next = False
        self.focus.release(self.target)
        self.target = None
        self.capture.stop()
        self.listening, self.last_used = False, time.time()
        core.log("cancelled after %.1fs" % (self.last_used - self.started))
        self.guard.end()
        if not self.pending:
            self.hud.hide()
        self.refresh_icon()
        self.write_status()

    @python_method
    def deliver(self, text, err, target=None, submit=False):
        try:
            self._deliver(text, err, target, submit)
        finally:
            self.focus.release(target)

    @python_method
    def _deliver(self, text, err, target, submit=False):
        self.pending -= 1
        self.last_used = time.time()
        self.refresh_icon()
        if err:
            self.flash("Could not transcribe")
            return
        text = self.fixes.apply(text or "").strip()
        if not text:
            self.flash("Didn't catch that")
            return
        text += " "       # so the next dictation does not run into this one
        self.last_text = text
        self.learner.heard(text)           # only queued: matched on the learner's thread
        self.rebuild_menu()
        if not can_post():
            self.inserter.put(text)
            self.note_no_post()
            return
        instead = self.back_to(target)
        if instead:                        # not where it started: never into whatever is in front
            self.inserter.put(text)
            self.flash(instead, seconds=4.0)
            return
        if self.secure["on"]:
            self.inserter.insert_kept(text, self.settings["insert"])
            self.flash("Also copied: press Command-V if it didn't paste")
            return
        self.inserter.insert(text, self.settings["insert"])
        if submit:
            # Return only once the text is seen in the field: in a web or Electron app a
            # paste can land after a Return sent at once, and a half message would go.
            self.sending = {"text": text.strip(), "until": time.time() + SEND_WAIT}
        if not self.listening and not self.pending:
            self.hud.hide()

    @python_method
    def send_when_landed(self, now):
        """From the tick: the dictated text is in the field, so Return sends it all.
        Not seen in time (an app that doesn't say, or the paste didn't land): nothing is
        pressed, and the panel says so; the text is there to send by hand."""
        if self.sending is None:
            return
        field = self.focus.field_text()
        tail = self.sending["text"][-SEND_TAIL:]
        if field is not None and tail and tail in field:
            self.sending = None
            core.log("return: the text is in the field: sent")
            self.inserter.press_return()
            return
        if now >= self.sending["until"]:
            self.sending = None
            core.log("return: the text wasn't seen in the field (%s): not sent" % ("can't read it" if field is None
                                                                                   else "not there"))
            self.flash("Pasted: press Return to send")

    @python_method
    def back_to(self, target):
        """None if the text can be pasted where the focus is now: where it started,
        or brought back there. Else what the panel says instead, and the text stays
        on the clipboard. Never into a password field, even the one it started in."""
        if target is None:
            return None                    # not known (no Accessibility then): as it always was
        if target.secure:
            core.log("paste: not into a password field (%s); copied instead" % target.bundle)
            return PASTE_SECURE
        now = self.focus.now()
        try:
            if now is not None and self.focus.same(now, target):
                return None                # the fast path: the focus never moved
            core.log("paste: the focus moved (%s to %s); bringing back %s" % (
                target.bundle, now.bundle if now else "?", target.bundle))
        finally:
            self.focus.release(now)
        ok, why = self.focus.restore(target)
        if ok:
            core.log("paste: back in %s" % target.bundle)
            return None
        core.log("paste: %s is %s; copied instead" % (target.bundle, "gone" if why == "gone" else "out of reach"))
        return PASTE_GONE if why == "gone" else PASTE_MOVED

    @python_method
    def note_no_post(self):
        """The text is on the clipboard and can't be pasted for you. The note stays
        until it is dismissed, the next dictation starts or Accessibility comes."""
        core.log("copied: no Accessibility to paste it")
        if self.listening:
            return                         # the next dictation already has the panel
        self.hud.show_note("post", NO_POST, NO_POST_BUTTON, self, "noteAllow:", "noteDismiss:")
        self.hud_hide_at = None

    def noteAllow_(self, _sender):
        core.log("note: Let Standby paste")
        self.openPost_(None)
        self.next_check = 0.0              # look again at once, then every second meanwhile

    def noteDismiss_(self, _sender):
        if getattr(self.hud, "note", None) is not None:
            self.hud.hide()

    @python_method
    def note_granted(self):
        """The note was up and Accessibility just came: say so, briefly."""
        if getattr(self.hud, "note", None) == "post" and self.perm.get("post"):
            core.log("note: Accessibility is on: pasting from now on")
            self.flash(GRANTED, seconds=1.5)

    @python_method
    def flash(self, text, seconds=1.8):
        core.log("panel: status shown")             # a word-fix notification can contain private words
        if self.listening:
            return                         # the next dictation already has the panel
        self.hud.show(text)
        self.hud_hide_at = time.time() + seconds

    @python_method
    def sound(self, name):
        if self.settings["sounds"]:
            from AppKit import NSSound
            s = NSSound.soundNamed_(name)
            if s is not None:
                s.play()

    def tick_(self, _timer):
        now = time.time()
        cmd = self.gesture.poll(now)
        if cmd:
            self.on_command(cmd)
        if self.listening:
            self.hud.set_level(core.meter(self.capture.take_level()))
            if now - self.started > float(self.settings["max_seconds"]):
                core.log("stopping at the %ss limit" % self.settings["max_seconds"])
                self.stop_listening()
        while True:
            try:
                text, err, target, submit = self.results.get_nowait()
            except queue.Empty:
                break
            self.deliver(text, err, target, submit)
        self.send_when_landed(now)
        if self.hud_hide_at and now >= self.hud_hide_at:
            self.hud_hide_at = None
            if not self.listening and not self.pending:
                self.hud.hide()
        if now >= self.next_check:
            watching = (self.setup is not None and self.setup.visible) or getattr(self.hud, "note", None)
            self.next_check = now + (1.0 if watching else 2.0)
            self.check_permissions(ask=True)
            self.note_granted()
        if now >= self.next_secure and not self.headless:
            self.next_secure = now + 1.0
            self.check_secure_input()
        if self.close_setup_at and now >= self.close_setup_at:
            self.close_setup_at = None
            if self.setup is not None:
                self.setup.close()
            self.write_status()
        if self.welcome_at and now >= self.welcome_at:
            self.welcome_at = None
            self.flash(core.ready_text(self.settings), seconds=2.5)
        if self.remapped and now >= self.next_remap:
            self.apply_remap()
        if now >= self.next_cmd:
            self.next_cmd = now + 0.5
            for cmd in core.take_command() or []:
                if cmd == "reload":                # Standby's Settings changed the shortcut or hold
                    self.reload_settings()
                elif cmd == "setup":               # from Standby's Settings: a fix button
                    self.apply_remap()
                    self.check_permissions()
                    if self.perm["mic"] == "ask":
                        self.ask_mic()
                    self.show_setup()
                elif cmd == "edit_words":          # Settings > Dictation > Word fixes
                    self.editWords_(None)
        if self.watch is not None:
            state = (self.watch.ready, self.watch.problem)
            if state != self.code_state:
                self.code_state = state
                self.write_status()
            if self.watch.ready and core.safe_to_restart(self.listening, self.pending, self.last_used, now,
                                                    self.launched):
                self.restart()
        if not self.healthy and now - self.launched >= core.HEALTHY_AFTER:
            self.healthy = True
            core.launch_went_fine()

    @python_method
    def restart(self):
        """Into the new code. The 🎤 remap stays on, so the key never falls back
        to Apple's dictation in between; the next copy sets it again anyway."""
        self.restarting = True
        core.log("restarting into the new code (%s)" % self.watch.names())
        core.note_restart(self.setup is not None and self.setup.visible)
        code, helper = core.relaunch_plan(os.getpid(), core.launchd_owns(os.getpid()), core.main_bundle())
        if helper:
            with open(core.ROOT / "dictation.log", "a") as out:
                subprocess.Popen(helper, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 start_new_session=True)
        core.log("handing over: %s" % ("exit %d, launchd starts the new copy" % code if helper is None
                                  else "the new copy opens once this one exits, tried until it is up"))
        core.exit_now(code)

    # -- menu -----------------------------------------------------------------
    @python_method
    def refresh_icon(self):
        self.set_icon("listening" if self.listening else "busy" if self.pending else "idle")

    @python_method
    def set_icon(self, state):
        if self.item is None:
            return
        if not (self.perm["listen"] and self.perm["post"]) and state == "idle":
            state = "blocked"
        # The user's "C: Wave": Standby's mark, the sound in a circle; today's mic if
        # this macOS lacks the symbol.
        name, old = {"idle": ("waveform.circle", "mic"), "listening": ("waveform.circle.fill", "mic.fill"),
                     "busy": ("ellipsis.circle", "ellipsis.circle"), "blocked": ("waveform.slash", "mic.slash")}[state]
        img = _symbol(name) or _symbol(old)
        button = self.item.button()
        if img is not None:
            button.setImage_(img)
        else:
            button.setTitle_("Dictation")

    @python_method
    def rebuild_menu(self):
        menu = self.build_menu()
        if self.item is not None:
            self.item.setMenu_(menu)
        return menu

    @python_method
    def build_menu(self):
        from AppKit import NSMenu, NSMenuItem

        def add(menu, title, action=None, state=None, rep=None, enabled=True):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            if action:
                item.setTarget_(self)
            if state is not None:
                item.setState_(1 if state else 0)
            if rep is not None:
                item.setRepresentedObject_(rep)
            item.setEnabled_(enabled and action is not None)
            menu.addItem_(item)
            return item
        s = self.settings
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        sep = lambda m: m.addItem_(NSMenuItem.separatorItem())
        add(menu, core.menu_head(self.perm, s, self.secure))
        if self.hotkey_state and not self.hotkey_state["ok"]:
            add(menu, "%s can't be used: %s" % (self.hotkey_state["label"], self.hotkey_state["why"]))
        if self.secure["on"] and not self.secure["heard"] and self.perm.get("listen"):
            add(menu, secure_input_words(self.secure["app"])[1])
        add(menu, "Copy Last Dictation", "copyLast:", enabled=bool(self.last_text))
        add(menu, "Fix Last Dictation…", "fixLast:", enabled=bool(self.last_text))
        tips = self.learner.suggestions()
        if tips:
            sub = NSMenu.alloc().init()
            sub.setAutoenablesItems_(False)
            for heard, meant, seen in tips:
                item = add(sub, "%s → %s" % (heard, meant), "acceptFix:", rep="%s\t%s" % (heard, meant))
                item.setToolTip_("Heard %d times. Click to fix it from now on." % seen)
            sep(sub)
            add(sub, "Dismiss All", "dismissFixes:")
            add(menu, "Suggested Fixes").setSubmenu_(sub)
        sep(menu)
        sub = NSMenu.alloc().init()
        sub.setAutoenablesItems_(False)
        add(sub, "Hold to Talk", "toggleHold:", s["hold"])
        sep(sub)
        for key, (label, _keys) in core.SHORTCUTS.items():
            add(sub, label, "pickShortcut:", key == s["shortcut"], key)
        if core.valid_custom(s.get("custom")):          # recorded in Standby's Settings
            add(sub, "Press %s" % s["custom"]["label"], "pickShortcut:", s["shortcut"] == "custom", "custom")
        add(menu, "Shortcut").setSubmenu_(sub)
        sub = NSMenu.alloc().init()
        sub.setAutoenablesItems_(False)
        add(sub, "Pasting", "pickInsert:", s["insert"] == "paste", "paste")
        add(sub, "Typing", "pickInsert:", s["insert"] == "type", "type")
        add(menu, "Insert By").setSubmenu_(sub)
        add(menu, "Sounds", "toggleSounds:", s["sounds"])
        add(menu, "Edit Word Fixes…", "editWords:")
        fixes = []
        if self.perm["mic"] == "denied":
            fixes.append(("Allow Microphone…", "openMic:", None))
        if not self.perm["listen"]:
            fixes.append(("Allow Input Monitoring…", "openListen:", None))
        if not self.perm["post"]:
            fixes.append(("Allow Accessibility…", "openPost:", None))
        if apple_dictation_on() and s["shortcut"] not in core.KEY_SHORTCUTS:
            fixes.append(("Turn Off Apple Dictation…", "openKeyboard:", "Or both start at once"))
        if core.missing(self.perm):
            fixes.append(("Set Up…", "showSetup:", None))
        elif core.optional_missing(self.perm):           # where the optional row is
            fixes.append(("Leave Out What the Mac Plays…", "showSetup:", "Optional. No screen is recorded"))
        if fixes:
            sep(menu)
            for title, action, tip in fixes:
                item = add(menu, title, action)
                if tip:
                    item.setToolTip_(tip)
        sep(menu)
        add(menu, "Start at Login", "toggleLogin:", core.at_login())
        add(menu, "Open Standby", "openStandby:")
        sep(menu)
        add(menu, "Quit Standby Dictation", "quit:")
        return menu

    @python_method
    def reload_settings(self):
        """The shortcut and hold to talk as Standby's Settings saved them. The same
        is read at startup, so a change during a restart is never lost."""
        self.settings = core.settings()
        self.gesture = core.make_gesture(self.settings)
        self.apply_remap()
        self.sync_hotkey()
        core.log("settings reloaded: %s%s" % (core.shortcut_ascii(self.settings), ", hold to talk" if self.settings["hold"] else ""))
        self.rebuild_menu()
        self.refresh_icon()
        self.write_status()

    @python_method
    def _changed(self, key, value):
        core.save_setting(key, value)
        self.settings = core.settings()
        self.gesture = core.make_gesture(self.settings)
        if key == "shortcut":
            self.apply_remap()
            self.sync_hotkey()
        self.rebuild_menu()
        self.write_status()

    def pickShortcut_(self, sender):
        self._changed("shortcut", str(sender.representedObject()))

    def toggleHold_(self, _sender):
        self._changed("hold", not self.settings["hold"])

    def pickInsert_(self, sender):
        self._changed("insert", str(sender.representedObject()))

    def toggleSounds_(self, _sender):
        self._changed("sounds", not self.settings["sounds"])

    def copyLast_(self, _sender):
        if self.last_text:
            self.inserter.put(self.last_text.strip())

    def fixLast_(self, _sender):
        """Fix Last Dictation…: the last dictation, to correct; the words
        changed are learned. It never pastes anything."""
        if not self.last_text:
            return
        edited = ask_fix(self.last_text.strip())
        if edited is not None:
            self.learn_from(self.last_text.strip(), edited)

    @python_method
    def learn_from(self, before, after):
        rules, alone = vocabulary.diff_fixes(before, after)
        rules, refused = vocabulary.guard_learned(rules)
        written = self.learner.learn(rules)
        if after.strip():
            self.last_text = after.strip() + " "     # Copy Last Dictation copies the fixed one
        self.rebuild_menu()
        self.flash(vocabulary.said(written or rules, alone, refused), seconds=2.5)
        return written

    def acceptFix_(self, sender):
        heard, _tab, meant = str(sender.representedObject()).partition("\t")
        if heard and meant:
            self.learner.accept(heard, meant)
            self.rebuild_menu()
            self.flash(vocabulary.said([(heard, meant)]), seconds=2.5)

    def dismissFixes_(self, _sender):
        self.learner.dismiss_all()
        self.rebuild_menu()

    @python_method
    def suggestions_changed(self):
        """From the learner's thread: a suggestion came up, so the menu shows it now."""
        self.performSelectorOnMainThread_withObject_waitUntilDone_("suggestionsChanged:", None, False)

    def suggestionsChanged_(self, _arg):
        self.rebuild_menu()

    def editWords_(self, _sender):
        core.open_words(self.fixes)

    def openListen_(self, _sender):
        open_url(core.PANE + "ListenEvent")

    def openPost_(self, _sender):
        open_url(core.PANE + "Accessibility")

    def openMic_(self, _sender):
        open_url(core.PANE + "Microphone")

    def openKeyboard_(self, _sender):
        open_url(core.KEYBOARD_PANE)

    def toggleLogin_(self, _sender):
        # Only the file: loading or unloading the agent from here would stop this app.
        from native import service
        if core.at_login():
            service.uninstall(core.LABEL)
        else:
            core.write_agent()
        self.rebuild_menu()

    def openStandby_(self, _sender):
        if core.STANDBY_APP.exists():
            subprocess.Popen(["open", str(core.STANDBY_APP)])
        else:
            subprocess.Popen([str(core.HERE / "standby"), "app"])

    def quit_(self, _sender):
        self.shutdown()

    @python_method
    def shutdown(self):
        if self.listening:
            self.cancel_listening()
        if self.remapped:
            remap_mic_key(False)
            self.remapped = False
        self.hotkey.unregister()          # with the remap: a quit leaves no hotkey behind
        self.engine.stop()
        core.launch_went_fine()                # quitting early is no failed launch
        try:
            core.STATUS.unlink()
        except OSError:
            pass
        from PyObjCTools import AppHelper
        AppHelper.stopEventLoop()

    def applicationShouldTerminate_(self, _app):
        """Quit from outside (logout, "Quit & Reopen" after a permission
        change, a script) takes the same way out as the menu's Quit."""
        self.shutdown()
        return 0                          # NSTerminateCancel: cmd_run exits by itself

    @python_method
    def write_status(self):
        st = {"pid": os.getpid(), "at": time.time(), "shortcut": self.settings["shortcut"],
              "hold": self.settings["hold"], "insert": self.settings["insert"],
              "listening": self.listening, "permissions": self.perm,
              "watching": self.tap is not None, "apple_dictation": apple_dictation_on(),
              "remapped": self.remapped, "started": self.launched,
              "secure_input": self.secure, "hotkey": self.hotkey.keys is not None,
              "hotkey_state": self.hotkey_state, "shortcut_label": core.shortcut_label(self.settings),
              "setup_open": self.setup is not None and self.setup.visible}
        if self.watch is not None:            # new code on disk: waiting to restart, or why not
            st["code"] = {"changed": self.watch.names(), "pending": self.watch.ready,
                          "problem": self.watch.problem}
        try:
            tmp = core.STATUS.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            tmp.replace(core.STATUS)
        except OSError:
            pass


def cmd_run(headless=False):
    """The app. headless: build the menu and the panel, never show them, exit:
    catches a broken Cocoa call without a mic, a keyboard or permissions."""
    import fcntl
    if sys.platform != "darwin" or objc is None:
        raise SystemExit("Standby Dictation runs on macOS")
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    from PyObjCTools import AppHelper
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    if headless:
        controller = Controller.alloc().initHeadless_(True)
        menu = controller.build_menu()
        controller.hud.show("Listening", level=True, front=False)
        controller.hud.set_level(0.5)
        SetupWindow(controller).update(core.setup_rows(controller.perm, controller.settings))
        print("selftest OK: menu of %d items, panel, setup window, %d shortcuts"
              % (menu.numberOfItems(), len(core.SHORTCUTS)))
        return 0
    core.ROOT.mkdir(parents=True, exist_ok=True)
    lock = open(core.LOCK, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        core.log("already running (pid %s)" % core.running_pid())
        return 0                          # exit 0: launchd must not restart us
    lock.seek(0)
    lock.truncate()
    lock.write("%d\n" % os.getpid())
    lock.flush()
    if core.launchd_owns(os.getpid()):
        failed = core.launch_begins()
        wait = core.launch_backoff(failed)
        if wait is None:
            core.log("not starting: the last %d launches stopped right after starting (%s). Fix it, then "
                "run: standby dictation on" % (failed, core.gave_up() or "no reason recorded"))
            return 0                      # exit 0: launchd's KeepAlive does not start it again
        if wait:
            core.log("the last %d launch(es) stopped right after starting: waiting %ds" % (failed, wait))
            time.sleep(wait)
    else:
        core.launch_begins()                   # started by hand: nothing would start it again anyway
    controller = Controller.alloc().initHeadless_(False)
    app.setDelegate_(controller)
    try:
        controller.start()
    except Exception as exc:
        import traceback
        core.log("could not start:\n%s" % traceback.format_exc())
        core.launch_failed("%s: %s" % (exc.__class__.__name__, exc))
        core.exit_now(1)
    signal.signal(signal.SIGTERM, lambda *_a: AppHelper.callAfter(controller.shutdown))
    AppHelper.runEventLoop(installInterrupt=True)
    core.log("quit")
    # Straight out, skipping interpreter teardown: MLX and Cocoa can crash in
    # there, and launchd would take the non-zero exit for a crash and restart
    # an app you just quit.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
