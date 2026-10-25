"""Standby Dictation's hand-over to new code (mac/relaunch.py), when it was started by hand
or from Finder: the new copy comes up even when the first launch fails (macOS refused an
`open` once, -600, and dictation stayed down), as soon as it can, and launchd's own job
is the last resort. Real processes, quick waits; no app, no launchd."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "mac")
import relaunch as R  # noqa: E402

R.WAITS, R.UP_WITHIN, R.OLD_GONE_WITHIN = (0.0, 0.2, 0.2, 0.2), 1.5, 5.0
root = tempfile.mkdtemp(prefix="sbrelaunch-")
LOCK, LOG = os.path.join(root, "dictation.lock"), os.path.join(root, "dictation.log")
fails, started = [], []


def check(label, ok, detail=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:600])


def new_copy(failing=0):
    """A launch that fails the first `failing` times, then a copy that takes the lock and stays."""
    tries = []

    def spawn(argv):
        tries.append(argv)
        if len(tries) <= failing:
            return                                    # like a refused `open`: nothing comes up
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
        started.append(p)
        open(LOCK, "w").write("%d\n" % p.pid)
    return spawn, tries


def run(spawn, old, extra=(), run_=None, keep_lock=False):
    """One hand-over: the copies of the run before gone, and the lock the old copy's,
    unless keep_lock (another copy holds it already)."""
    if not keep_lock:
        for p in started:
            p.kill()
        open(LOCK, "w").write("%d\n" % old)
    open(LOG, "w").close()
    argv = ["--old", str(old), "--lock", LOCK, "--log", LOG] + list(extra) + ["--", "/usr/bin/open", "-g", "/Apps/Dictation.app"]
    t0 = time.time()
    rc = R.main(argv, spawn=spawn, **({"run": run_} if run_ else {}))
    return rc, time.time() - t0, open(LOG).read()


def gone_soon(seconds):
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(%f)" % seconds])


old = gone_soon(0.6)
open(LOCK, "w").write("%d\n" % old.pid)
spawn, tries = new_copy()
rc, took, said = run(spawn, old.pid)
check("the old copy goes first, then one launch, and the new copy holds the lock",
      rc == 0 and len(tries) == 1 and took >= 0.5 and old.poll() is not None and "the new copy is up" in said, (rc, took, said))
check("  by the same command it was given (open -g, the app's permissions)", tries[0] == ["/usr/bin/open", "-g", "/Apps/Dictation.app"])

spawn, tries = new_copy(failing=1)
rc, took, said = run(spawn, 999999)
check("the first launch fails (macOS refused it): tried again, and the new copy is up",
      rc == 0 and len(tries) == 2 and "at try 2" in said and "try 1 of" in said, (rc, len(tries), said))

spawn, tries = new_copy(failing=3)
rc, took, said = run(spawn, 999999)
check("  three refusals in a row: still up at the fourth, waiting longer between",
      rc == 0 and len(tries) == 4 and "at try 4" in said, (rc, len(tries), said))

spawn, tries = new_copy(failing=99)
kicked = []
plist = os.path.join(root, "dev.standby.dictation.plist")
open(plist, "w").write("x")


def launchctl(argv, **kw):
    kicked.append(argv)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    started.append(p)
    open(LOCK, "w").write("%d\n" % p.pid)
    return subprocess.CompletedProcess(argv, 0, "", "")


rc, took, said = run(spawn, 999999, ["--job", "dev.standby.dictation", "--plist", plist], launchctl)
check("every launch refused: launchd's own job is started last, and that brings it up",
      rc == 0 and len(tries) == len(R.WAITS) and kicked and kicked[0][:2] == ["launchctl", "kickstart"]
      and kicked[0][2].endswith("/dev.standby.dictation") and "from launchd" in said, (rc, kicked, said))

spawn, tries = new_copy(failing=99)
rc, took, said = run(spawn, 999999, ["--job", "dev.standby.dictation", "--plist", os.path.join(root, "none.plist")])
check("  no job at login either: it gives up after the last try, and says what to do",
      rc == 1 and len(tries) == len(R.WAITS) and "gave up" in said and "Settings" in said, (rc, said))

for p in started:
    p.kill()
other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
started.append(other)
open(LOCK, "w").write("%d\n" % other.pid)
spawn, tries = new_copy()
rc, took, said = run(spawn, 999999, keep_lock=True)
check("a new copy that came by itself (the user opened it): nothing launched twice",
      rc == 0 and not tries and "started by something else" in said, (tries, said))

for p in started:
    p.kill()
old.wait()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
