"""native/permissions.py on a Mac: AVFoundation for the microphone, and System Audio
Recording, which meeting audio and room-only dictation need (Core Audio process taps),
with its pane in System Settings. Nothing here captures anything, but ask_sound.

The macOS parts load only when used, so this imports anywhere.
"""
import os
import threading
import time

SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"


def mic_status():
    try:
        import AVFoundation
        code = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
        return {0: "undetermined", 1: "restricted", 2: "denied", 3: "authorized"}.get(int(code))
    except Exception:
        return None


_FRESH = ("import AVFoundation as A; print({0: 'undetermined', 1: 'restricted', 2: 'denied', 3: 'authorized'}"
          ".get(int(A.AVCaptureDevice.authorizationStatusForMediaType_(A.AVMediaTypeAudio))))")


def mic_status_fresh():
    """macOS's answer now, from a process that has never asked: this one goes on being told "undetermined"
    after an Allow it didn't ask for itself (the ring's sound brought macOS's prompt up; the setup window then
    opened for a mic allowed 25 s before, VM 2026-11-02). A child of Standby is Standby to macOS, as the
    recorder is. None when it can't tell."""
    import subprocess
    import sys
    try:
        r = subprocess.run([sys.executable, "-c", _FRESH], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (r.stdout or "").strip()
    return out if out in ("undetermined", "restricted", "denied", "authorized") else None


def ask_mic(answered):
    """AVFoundation's prompt, as Standby: answered(granted) on the user's choice. Only on the
    user's click (Standby's setup window)."""
    no_devices(None)
    import AVFoundation
    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeAudio, lambda granted: answered(bool(granted)))


def sound_available():
    """(True, "") on a Mac with process taps a user can allow (macOS 14.4 and later)."""
    from native.macos import appaudio
    return appaudio.supported()


def sound_state():
    """The Mac's sound, as far as it can be told (macOS never says): "allowed", this build
    has heard it; "denied", a capture since heard only silence while an app played;
    "asked", this build has brought macOS's prompt up and nothing tells its answer yet; or
    "not_asked". Asks nothing."""
    from native import app
    from native.macos.appaudio import grant
    g, build = app.grants(), app.build_mark()

    def at(what):
        row = g.get(grant(what)) or {}
        return row.get("at", 0) if row and row.get("build") == build else None
    heard, refused = at("sound"), at("sound_refused")
    if refused is not None and (heard is None or refused > heard):
        return "denied"
    if heard is not None:
        return "allowed"
    return "asked" if at("sound_asked") is not None else "not_asked"


ASK_SECONDS = 30.0        # how long the capture that brings macOS's prompt runs (the user may take a while)


def ask_sound(done=None):
    """macOS's own prompt for System Audio Recording, the first time this build asks: there
    is no call that asks, so a capture of everything but Standby starts, and that brings it
    up. It runs ASK_SECONDS, or until it hears a sound (allowed), on a thread of its own;
    done(heard) after. Nothing heard is kept."""
    no_devices(None)
    from native import app
    from native.macos import appaudio
    app.note_grant(appaudio.grant("sound_asked"))

    def go():
        cap = appaudio.MeetingAudio()
        heard = False
        try:
            cap.start(appaudio.EVERYTHING, lambda block, t: None)
            end = time.monotonic() + ASK_SECONDS
            while time.monotonic() < end and not heard:
                time.sleep(0.25)
                heard = sound_state() == "allowed"
        finally:
            cap.stop()
        if done is not None:
            done(heard)
    threading.Thread(target=go, name="ask for the sound", daemon=True).start()


def open_sound_settings():
    """System Settings at Privacy & Security > Screen & System Audio Recording, whose
    System Audio Recording Only part holds Standby's switch."""
    no_devices(None)
    import AppKit
    AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(SETTINGS_URL))


def no_devices(what=None):
    """A test (STANDBY_NO_DEVICES) never reaches the Mac's sound, its prompt or System
    Settings; a stand-in module (no file of its own) is fine."""
    if os.environ.get("STANDBY_NO_DEVICES") and (what is None or getattr(what, "__file__", None)):
        raise RuntimeError("no real capture in a test (STANDBY_NO_DEVICES)")
