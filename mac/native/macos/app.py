"""native/app.py on a Mac: the main NSBundle. A plain python has one too, with no
identifier: that is "not an app". The build mark is Standby.app's ad-hoc signature."""
import os

APP = os.path.expanduser("~/Applications/Standby.app")
_BUILD = []


def _bundle():
    try:
        from Foundation import NSBundle
        b = NSBundle.mainBundle()
        return b if b is not None and str(b.bundleIdentifier() or "") else None
    except Exception:
        return None


def bundle_id():
    b = _bundle()
    return str(b.bundleIdentifier()) if b is not None else None


def name():
    b = _bundle()
    if b is None:
        return None
    try:
        name = (b.infoDictionary() or {}).get("CFBundleName")
    except Exception:
        return None
    return str(name) if name else None


def path():
    b = _bundle()
    return str(b.bundlePath()) if b is not None else None


def build_mark():
    """This install's Standby.app: its signature's CDHash, or None (no app, a test). Read
    once per process: codesign is a process of its own."""
    if not _BUILD:
        mark = None
        if not os.environ.get("STANDBY_NO_DEVICES") and os.path.isdir(APP):
            try:
                import subprocess
                out = subprocess.run(["codesign", "-dvvv", APP], capture_output=True, text=True,
                                     timeout=10).stderr
                mark = next((l.split("=", 1)[1].strip() for l in out.splitlines() if l.startswith("CDHash=")), None)
            except Exception:
                mark = None
        _BUILD.append(mark)
    return _BUILD[0]
