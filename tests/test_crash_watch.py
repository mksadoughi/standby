"""The guard every suite imports first (tests/agentpath.py) fails a suite whose child
crashed (SIGABRT, SIGSEGV...), which would otherwise show only as macOS's "Python quit
unexpectedly" dialog on the user's screen. Nothing here really crashes: a finished
child is handed the exit status a SIGABRT leaves, so no dialog can appear."""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(label, ok, detail=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:600])


def suite(body):
    """A stand-in suite that imports the guard, then does `body`."""
    code = "import sys; sys.path.insert(0, %r)\nimport agentpath, subprocess, signal\n%s\nprint('ALL PASS')" % (HERE, body)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)


r = suite("p = subprocess.Popen(['true']); p.wait()\n"
          "p.returncode = None; p._handle_exitstatus(signal.SIGABRT)   # the status a SIGABRT leaves")
check("a child that died by SIGABRT fails the suite, whatever it printed",
      r.returncode == 1 and "a child died by SIGABRT" in r.stdout and "true" in r.stdout, (r.returncode, r.stdout))
r = suite("p = subprocess.Popen(['true']); p.wait()\n"
          "p.returncode = None; p._handle_exitstatus(signal.SIGSEGV)")
check("  SIGSEGV too", r.returncode == 1 and "SIGSEGV" in r.stdout, r.stdout)
r = suite("p = subprocess.Popen(['sleep', '30']); p.terminate(); p.wait()\n"
          "q = subprocess.Popen(['sleep', '30']); q.kill(); q.wait()")
check("a child the test stopped itself (SIGTERM, SIGKILL) is fine", r.returncode == 0 and "FAIL" not in r.stdout,
      (r.returncode, r.stdout))
r = suite("subprocess.run(['true']); subprocess.run(['false'])")
check("children that exit, well or badly, are fine", r.returncode == 0, r.stdout)
r = suite("p = subprocess.Popen(['true'])\n"
          "import time; time.sleep(0.3)")
check("a child never waited for is still looked at, at exit", r.returncode == 0 and r.stdout.strip().endswith("ALL PASS"),
      r.stdout)

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
