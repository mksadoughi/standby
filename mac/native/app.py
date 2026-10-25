"""The running app's own identity, as the OS knows it: which app bundle this process runs
as, if any. The OS's permission lists name an app by these.
"""
import json
import os
import time

from native import impl

_os = impl("app")

# What the OS let this build of Standby do, for the app to say exactly why the call's sound
# can't be recorded. The OS can keep a grant per build (on a Mac, per ad-hoc signature),
# and some grants can't be read back without asking. So: the build's mark, when its capture
# last heard the computer (heard) and when the screen was last allowed (screen). In
# ROOT/capture-grants.json; never raises.
GRANTS = "capture-grants.json"


def bundle_id():
    """The id of the app bundle this process runs as, or None when it isn't one (a plain
    python, a test)."""
    return _os.bundle_id()


def name():
    """The name the OS's settings list this app by, or None when it isn't an app bundle."""
    return _os.name()


def path():
    """Where this app's bundle is, or None when it isn't one."""
    return _os.path()


def build_mark():
    """Which build of Standby is installed, as the OS keys its permissions, or None (no
    app, a test)."""
    return _os.build_mark()


def _grants_path():
    root = os.path.expanduser(os.environ.get("STANDBY_ROOT", "~/.standby"))
    return os.path.join(root, GRANTS)


def grants():
    """What each build was seen allowed: {what: {"at", "build"}}."""
    try:
        with open(_grants_path()) as fh:
            return json.load(fh)
    except Exception:
        return {}


def note_grant(what):
    """This build was just seen allowed `what` ("heard": captured the computer's sound;
    "screen": allowed to record the screen)."""
    try:
        g = grants()
        g[what] = {"at": time.time(), "build": build_mark()}
        tmp = _grants_path() + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(g, fh)
        os.replace(tmp, _grants_path())
    except Exception:
        pass
