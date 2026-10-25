"""The OS starting a program for the user (native/service.py), on a Mac a LaunchAgent, with
launchctl as a stand-in: written, off then on cleanly, kicked when still loaded, refused for
good, and whose job a process is. And the running app's identity (native/app.py), as the
interpreter running the tests has it. Never the user's own login items: a scratch folder, and launchctl never runs."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os
import plistlib
import sys
import tempfile
import time
import types
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbservice-"))
os.environ["STANDBY_LAUNCH_AGENTS"] = str(root / "LaunchAgents")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
from native import app, service  # noqa: E402
from native.macos import service as mac  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-500:])


LABEL = "dev.standby.test"
check("a login item lives in the folder the tests point at, never the user's", mac.AGENTS == root / "LaunchAgents")
check("  not there to start with", not service.installed(LABEL))
service.install(LABEL, ["/bin/echo", "hi"], root / "x.log")
pl = plistlib.load(open(root / "LaunchAgents" / (LABEL + ".plist"), "rb"))
check("installed: starts at login, restarts after a crash, not after Quit, its log",
      service.installed(LABEL) and pl == {"Label": LABEL, "ProgramArguments": ["/bin/echo", "hi"], "RunAtLoad": True,
                                          "KeepAlive": {"SuccessfulExit": False}, "ProcessType": "Interactive",
                                          "StandardErrorPath": str(root / "x.log")}, pl)
service.uninstall(LABEL)
service.uninstall(LABEL)
check("  uninstalled, and again: harmless", not service.installed(LABEL))


# ---------------------------------------------------------------- launchd: off, then on, cleanly
class FakeLaunchd(object):
    """launchctl as `standby dictation on|off` use it. bootout takes `lag` prints to
    let go; bootstrap fails with 5 while the job is still there, as launchctl does."""
    def __init__(self, lag=0, loaded=True):
        self.loaded, self.lag, self.calls = loaded, lag, []

    def __call__(self, args, **kw):
        self.calls.append(args[1])
        ok = lambda: types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[1] == "bootout":
            self.going = self.lag
            return ok()
        if args[1] == "print":
            if self.loaded and getattr(self, "going", None) is not None:
                if self.going <= 0:
                    self.loaded, self.going = False, None
                else:
                    self.going -= 1
            return types.SimpleNamespace(returncode=0 if self.loaded else 113, stdout="", stderr="")
        if args[1] == "bootstrap":
            if self.loaded:
                return types.SimpleNamespace(returncode=5, stdout="", stderr="Bootstrap failed: 5: Input/output error")
            self.loaded = True
            return ok()
        if args[1] == "kickstart":
            return ok()
        raise AssertionError(args)


lc = FakeLaunchd(lag=3)
check("off: waits until launchd has really let go", mac.stop(LABEL, run=lc, sleep=lambda s: None, dom="gui/501")
      and lc.loaded is False and lc.calls.count("print") >= 3, lc.calls)
check("  so on straight after loads it first time", mac.start(LABEL, run=lc, sleep=lambda s: None, dom="gui/501") == (True, "")
      and lc.calls[-1] == "bootstrap", lc.calls[-3:])
lc = FakeLaunchd(loaded=True)
check("on while it is still loaded: kickstarted, still under launchd, no fallback",
      mac.start(LABEL, run=lc, sleep=lambda s: None, dom="gui/501") == (True, "") and lc.calls[-1] == "kickstart", lc.calls)
lc = FakeLaunchd(lag=10 ** 6)
check("a bootout that never finishes: off says so after its wait",
      mac.stop(LABEL, wait=0.05, run=lc, sleep=lambda s: time.sleep(0.01), dom="gui/501") is False)
nope = lambda args, **kw: types.SimpleNamespace(returncode=5 if args[1] == "bootstrap" else 113, stdout="",
                                                stderr="Bootstrap failed: 5: Input/output error")
check("launchctl refusing for good (ssh): not started, with its words",
      mac.start(LABEL, run=nope, sleep=lambda s: None, dom="gui/501") == (False, "Bootstrap failed: 5: Input/output error"))
check("  loaded asks launchctl, and a launchctl that isn't there is \"no\"",
      mac.loaded(LABEL, run=FakeLaunchd(loaded=True), dom="gui/501")
      and not mac.loaded(LABEL, run=lambda *a, **k: (_ for _ in ()).throw(OSError("none")), dom="gui/501"))


check("kickstart: started again in place, with launchd's words when it can't",
      mac.kickstart(LABEL, run=FakeLaunchd(), dom="gui/501") == (True, "")
      and mac.kickstart(LABEL, run=lambda a, **k: types.SimpleNamespace(returncode=113, stdout="", stderr="Could not find service"),
                        dom="gui/501") == (False, "Could not find service")
      and mac.kickstart(LABEL, run=lambda *a, **k: (_ for _ in ()).throw(OSError("no launchctl")), dom="gui/501")[0] is False)
check("its file, where a helper can look for it itself", service.definition_path(LABEL) == root / "LaunchAgents" / (LABEL + ".plist"))

# ---------------------------------------------------------------- whose job a process is
def launchctl(out=None, fail=False):
    def run(argv, **kw):
        if fail:
            raise OSError("no launchctl")
        return type("R", (), {"stdout": out or ""})()
    return run


check("launchd: our job, by the name launchd gives it",
      mac.started_by_service(LABEL, 1, {"XPC_SERVICE_NAME": LABEL}, launchctl(fail=True)))
check("  or by launchctl's pid for the job",
      mac.started_by_service(LABEL, 321, {"XPC_SERVICE_NAME": "0"}, launchctl("\tstate = running\n\tpid = 321\n"))
      and not mac.started_by_service(LABEL, 322, {}, launchctl("\tpid = 321\n"))
      and not mac.started_by_service(LABEL, 5, {}, launchctl(fail=True)))

# ---------------------------------------------------------------- the running app, as the OS knows it
# A framework python runs inside Python.app (org.python.python): the interpreter's bundle, as
# the OS sees it, so a dictation run from python is listed in System Settings as Python.
bid, name, where = app.bundle_id(), app.name(), app.path()
check("the running app: an id, a name and a place together, or none of them",
      (bid is None and name is None and where is None) or (bool(bid) and where and os.path.isdir(where)), (bid, name, where))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
