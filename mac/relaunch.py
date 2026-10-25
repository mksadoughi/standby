#!/usr/bin/env python3
"""Brings Standby Dictation back after it exits to restart into new code, when nothing
else would: it was started by hand or from Finder, not by launchd (launchd starts its
own job again by itself).

    relaunch.py --old PID --lock PATH --log PATH [--job LABEL --plist PATH] -- COMMAND...

It waits for the old copy to go, then runs COMMAND (`open -g` on the app, so it has the
app's permissions; or the script), and waits for the new copy to hold the dictation lock.
A launch that fails or doesn't come up is tried again, waiting longer each time: macOS
once refused the first `open` (-600, procNotFound) while Standby itself was restarting,
and dictation stayed down. After the last try, launchd's own job is started, where the
user has dictation start at login.

There can't be two copies at once (the lock), so the old copy can't wait for the new one:
it exits, and this makes sure the new one comes. Standard library only, so it runs
whatever state the rest of the code is in.
"""
import argparse
import os
import subprocess
import sys
import time

WAITS = (0.0, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0)   # s before each launch
UP_WITHIN = 15.0          # s for a launch to take the lock
OLD_GONE_WITHIN = 30.0    # s for the old copy to exit


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def holder(lock):
    """The pid the dictation lock names, if that process is alive, else None."""
    try:
        pid = int(open(lock).read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return pid if alive(pid) else None


def main(argv=None, spawn=None, run=subprocess.run, sleep=time.sleep, now=time.time):
    ap = argparse.ArgumentParser(prog="relaunch.py")
    ap.add_argument("--old", type=int, required=True)
    ap.add_argument("--lock", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--job")
    ap.add_argument("--plist")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command

    def log(msg):
        try:
            with open(a.log, "a") as fh:
                fh.write("[dictation] %s relaunch: %s\n" % (time.strftime("%H:%M:%S"), msg))
        except OSError:
            pass

    def launch(argv_):
        if spawn is not None:
            return spawn(argv_)
        with open(a.log, "a") as out:
            subprocess.Popen(argv_, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)

    def came_up():
        end = now() + UP_WITHIN
        while now() < end:
            pid = holder(a.lock)
            if pid is not None and pid != a.old:
                return pid
            sleep(0.25)
        return None

    end = now() + OLD_GONE_WITHIN
    while alive(a.old) and now() < end:
        sleep(0.2)
    for n, wait in enumerate(WAITS, 1):
        sleep(wait)
        pid = holder(a.lock)
        if pid is not None and pid != a.old:
            log("the new copy is up (pid %d), started by something else" % pid)
            return 0
        try:
            launch(cmd)
        except OSError as exc:
            log("try %d of %d: could not run %s (%r)" % (n, len(WAITS), cmd[0], exc))
            continue
        pid = came_up()
        if pid is not None:
            log("the new copy is up (pid %d)%s" % (pid, "" if n == 1 else ", at try %d" % n))
            return 0
        log("try %d of %d: no new copy within %.0f s: again" % (n, len(WAITS), UP_WITHIN))
    if a.job and a.plist and os.path.exists(a.plist):
        log("starting launchd's own job (%s)" % a.job)
        try:
            # The same as native.service's kickstart; kept here so the hand-over needs none of
            # Standby's code (it runs while that code is being replaced).
            run(["launchctl", "kickstart", "gui/%d/%s" % (os.getuid(), a.job)],
                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            log("launchctl: %r" % exc)
        pid = came_up()
        if pid is not None:
            log("the new copy is up (pid %d), from launchd" % pid)
            return 0
    log("gave up: Standby Dictation is not running; start it from Standby's Settings or Applications")
    return 1


if __name__ == "__main__":
    sys.exit(main())
