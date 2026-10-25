"""Where the tests find standby-agent.

Local mode runs the agent straight out of the repo (see transport.LOCAL_AGENT);
only an SSH host has it linked into ~/.local/bin. Prefer the repo copy so the
suites pass wherever the checkout lives, and fall back to the installed link.
"""
import os
import sys
import tempfile

_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(_repo, "plugin", "bin", "standby-agent")
if not os.path.exists(AGENT):
    AGENT = os.path.expanduser("~/.local/bin/standby-agent")

# The safety net: no test runs in the user's real ~/.standby. Every test
# imports this first, before anything that reads STANDBY_ROOT. One that never
# sets its own gets a throwaway root here; one pointed at the real one stops.
REAL = os.path.realpath(os.path.expanduser("~/.standby"))


def guard(root=None):
    root = root or os.environ.get("STANDBY_ROOT")
    if root and os.path.realpath(os.path.expanduser(str(root))) == REAL:
        sys.stderr.write("refusing to run a test in the real %s\n" % REAL)
        sys.exit(2)


if not os.environ.get("STANDBY_ROOT"):
    os.environ["STANDBY_ROOT"] = tempfile.mkdtemp(prefix="sbtest-")
guard()
# Nor the real mic or speakers: voice mode's engine stays a fake (standby.voice_backend).
os.environ["STANDBY_NO_DEVICES"] = "1"
# Nor the user's login items: a login item a test writes goes in its own scratch folder.
if not os.environ.get("STANDBY_LAUNCH_AGENTS"):
    os.environ["STANDBY_LAUNCH_AGENTS"] = os.path.join(os.environ["STANDBY_ROOT"], "LaunchAgents")

# No child may crash (2026-10-07): a child that dies by SIGABRT, SIGSEGV and the like
# puts macOS's "Python quit unexpectedly" on the user's screen; twelve did in one
# morning, a voice process exiting while a daemon thread held its stderr. Every
# suite imports this first, so every child it starts is watched here, and one that
# crashed fails the suite: a FAIL in the run, not only a dialog. A child the test
# stops itself (SIGTERM, SIGKILL, SIGINT) is fine. Grandchildren are not seen.
import atexit  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import weakref  # noqa: E402

CRASH_SIGNALS = {signal.SIGABRT, signal.SIGSEGV, signal.SIGBUS, signal.SIGILL, signal.SIGFPE, signal.SIGTRAP}
CRASHED = []                      # (signal name, the child's command)
_CHILDREN = weakref.WeakSet()
_init, _status = subprocess.Popen.__init__, subprocess.Popen._handle_exitstatus


def _started(self, *args, **kwargs):
    _init(self, *args, **kwargs)
    _CHILDREN.add(self)


def _ended(self, *args, **kwargs):
    _status(self, *args, **kwargs)
    rc = self.returncode
    if rc is not None and rc < 0 and -rc in CRASH_SIGNALS and not getattr(self, "_crash_noted", False):
        self._crash_noted = True
        CRASHED.append((signal.Signals(-rc).name, self.args))


def _report():
    """At exit, last of all: any child that crashed fails the suite."""
    for child in list(_CHILDREN):
        try:
            child.poll()
        except Exception:
            pass
    try:
        subprocess._cleanup()
    except Exception:
        pass
    if CRASHED:
        for name, cmd in CRASHED:
            shown = " ".join(str(a) for a in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
            print("  a child died by %s (a crash dialog on the user's screen): %s  FAIL" % (name, shown[:200]))
        print("%d FAILED" % len(CRASHED))
        sys.stdout.flush()
        os._exit(1)


subprocess.Popen.__init__, subprocess.Popen._handle_exitstatus = _started, _ended
atexit.register(_report)

# Nor the real ScreenCaptureKit (2026-10-08): these sessions run where Screen Recording
# is allowed, so a test that reached it captured the Mac's sound for real, with no
# prompt, and crashed as it exited with the stream running. Here `import
# ScreenCaptureKit` gives a stand-in whose every SC class fails, and touching one fails
# the suite. A test with its own stand-in puts it in sys.modules, as before. The
# modules check STANDBY_NO_DEVICES too, for the children a test starts.
import importlib.abc  # noqa: E402
import importlib.machinery  # noqa: E402
import types  # noqa: E402

TOUCHED = []                      # what a test reached for in ScreenCaptureKit


class _NoScreenCapture(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        TOUCHED.append(name)
        raise RuntimeError("a test reached the real ScreenCaptureKit (%s): STANDBY_NO_DEVICES" % name)


class _Tripwire(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name == "ScreenCaptureKit" and os.environ.get("STANDBY_NO_DEVICES"):
            return importlib.machinery.ModuleSpec(name, self)
        return None

    def create_module(self, spec):
        return _NoScreenCapture(spec.name)

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _Tripwire())


def _report_capture():
    if TOUCHED:
        print("  a test reached the real ScreenCaptureKit: %s  FAIL" % ", ".join(sorted(set(TOUCHED))))
        print("1 FAILED")
        sys.stdout.flush()
        os._exit(1)


atexit.register(_report_capture)

# Nor a real host: tests write hosts from before into their configs ("devbox", "x"),
# which the user's own ssh config may well know. Here ssh and scp refuse every host
# but localhost.
import shutil  # noqa: E402

_REAL_SSH = {name: shutil.which(name) or "/usr/bin/" + name for name in ("ssh", "scp")}
_NO_HOSTS = tempfile.mkdtemp(prefix="sbnohosts-")
for _name, _real in _REAL_SSH.items():
    with open(os.path.join(_NO_HOSTS, _name), "w") as _fh:
        _fh.write('#!/bin/sh\n'
                  'for a in "$@"; do case "$a" in localhost|localhost:*|*@localhost|*@localhost:*) '
                  'exec %s "$@";; esac; done\n'
                  'echo "%s: a test never reaches a real host (tests/agentpath.py)" >&2\n'
                  'exit 255\n' % (_real, _name))
    os.chmod(os.path.join(_NO_HOSTS, _name), 0o755)
os.environ["PATH"] = _NO_HOSTS + os.pathsep + os.environ.get("PATH", "")
atexit.register(shutil.rmtree, _NO_HOSTS, True)
