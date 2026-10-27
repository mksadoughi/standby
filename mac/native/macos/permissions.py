"""native/permissions.py on a Mac: AVFoundation for the microphone, CoreGraphics for
Screen & System Audio Recording, which meeting audio (ScreenCaptureKit) and dictation's
checks need, and its pane in System Settings. Nothing here captures anything.

The macOS parts load only when used, so this imports anywhere.
"""
import os

SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"


def mic_status():
    try:
        import AVFoundation
        code = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
        return {0: "undetermined", 1: "restricted", 2: "denied", 3: "authorized"}.get(int(code))
    except Exception:
        return None


def ask_mic(answered):
    """AVFoundation's prompt, as Standby: answered(granted) on the user's choice. Only on the
    user's click (Standby's setup window)."""
    no_devices(None)
    import AVFoundation
    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeAudio, lambda granted: answered(bool(granted)))


def screen_available():
    import platform
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except ValueError:
        major = 0
    if major < 14:
        return False, "Needs macOS 14 or later"
    try:
        import ScreenCaptureKit  # noqa: F401
    except ImportError:
        return False, "Run ./install.sh again"
    return True, ""


def _cg(name):
    """A CoreGraphics function, from Quartz, or from the framework itself: one pyobjc's
    Quartz lacked CGPreflightScreenCaptureAccess (a KeyError from its lazy lookup)."""
    try:
        import Quartz
        return getattr(Quartz, name)
    except Exception:
        import ctypes
        lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = ctypes.c_bool, []
        return fn


def screen_allowed():
    return bool(_cg("CGPreflightScreenCaptureAccess")())


def no_devices(what=None):
    """A test (STANDBY_NO_DEVICES) never reaches the real screen, its prompt or System
    Settings; a stand-in ScreenCaptureKit (no file of its own) is fine."""
    if os.environ.get("STANDBY_NO_DEVICES") and (what is None or getattr(what, "__file__", None)):
        raise RuntimeError("no real screen capture in a test (STANDBY_NO_DEVICES)")


def ask_screen():
    """macOS's own prompt, which offers System Settings. After the user allows it,
    macOS asks to quit and reopen Standby: access starts with the next launch."""
    no_devices(None)
    _cg("CGRequestScreenCaptureAccess")()


def open_screen_settings():
    """System Settings at Privacy & Security > Screen & System Audio Recording."""
    no_devices(None)
    import AppKit
    AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(SETTINGS_URL))
