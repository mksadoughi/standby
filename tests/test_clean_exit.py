"""The recorder exits without a crash report: its threads waited for and its exit hooks
run (Core Audio's listeners removed, the captures stopped), then the process ends without
tearing Python down, so a callback from Core Audio's or ScreenCaptureKit's own thread
never lands in an interpreter that is going ("Python quit unexpectedly" on the user's
screen as a call ended, 2026-10-26). No audio is opened: listeners on the device list only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import os
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.join(HERE, "..", "mac")
fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     %s" % (detail,))


def run(code):
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=60,
                          cwd=MAC, env=dict(os.environ, PYTHONPATH=MAC))


r = run("""
    import atexit, threading, time, sys
    from importlib.machinery import SourceFileLoader
    sb = SourceFileLoader("sb", "standby.py").load_module()
    def late():
        time.sleep(0.3)
        print("thread done", flush=True)
    threading.Thread(target=late).start()                  # not a daemon: Python waits for it
    atexit.register(lambda: print("exit hook ran", flush=True))
    print("bye", end="")                                      # unflushed: flushed on the way out
    sb.exit_now(3)
    print("never")
""")
check("exit_now: its threads waited for, the exit hooks run, output flushed, the code kept",
      r.returncode == 3 and "thread done" in r.stdout and "exit hook ran" in r.stdout and "bye" in r.stdout
      and "never" not in r.stdout, (r.returncode, r.stdout, r.stderr[-300:]))
r = run("""
    from importlib.machinery import SourceFileLoader
    sb = SourceFileLoader("sb", "standby.py").load_module()
    sb.exit_now("standby: no capture device")
""")
check("  a message for the user: said, and exit 1", r.returncode == 1 and "no capture device" in r.stderr, (r.returncode, r.stderr))
r = subprocess.run([sys.executable, os.path.join(MAC, "standby.py"), "--help"], capture_output=True, text=True, timeout=60)
check("  the recorder's own command line ends through it, its code kept", r.returncode == 0 and "usage" in r.stdout.lower(),
      (r.returncode, r.stderr[-300:]))

if sys.platform == "darwin":
    r = run("""
        import atexit
        from native.macos import mics
        c = mics.CoreAudio()
        before = atexit._ncallbacks()
        ok = c.watch(lambda: None)
        print("watching", ok, len(c._listeners), atexit._ncallbacks() - before)
        c.watch(lambda: None)
        print("again", atexit._ncallbacks() - before)
    """)
    check("Core Audio's listeners: removed at exit, the hook registered once",
          r.returncode == 0 and "watching True 2 1" in r.stdout and "again 1" in r.stdout, (r.stdout, r.stderr[-300:]))
    r = run("""
        from native.macos import mics
        c = mics.CoreAudio()
        c.watch(lambda: None)
        c.unwatch()
        print("left", len(c._listeners))
        c.unwatch()                                           # twice: nothing to do
    """)
    check("  unwatch removes every one, and twice is harmless", r.returncode == 0 and "left 0" in r.stdout, (r.stdout, r.stderr))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
raise SystemExit(1 if fails else 0)
