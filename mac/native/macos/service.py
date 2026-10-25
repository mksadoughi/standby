"""native/service.py on a Mac: a LaunchAgent in ~/Library/LaunchAgents, run by launchd in the
user's login session (gui/<uid>). launchctl runs as a process: nothing here imports a framework.
run and sleep are the tests' way in: launchctl as a stand-in, no waiting."""
import os
import plistlib
import re
import subprocess
import time
from pathlib import Path

# Tests point this elsewhere: the login items are the user's.
AGENTS = Path(os.environ.get("STANDBY_LAUNCH_AGENTS", Path.home() / "Library" / "LaunchAgents"))


def _plist(label):
    return AGENTS / (label + ".plist")


def _domain():
    return "gui/%d" % os.getuid()


def install(label, argv, log_path):
    """Start at login, and again if it crashes (not after Quit)."""
    path = _plist(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump({"Label": label, "ProgramArguments": list(argv), "RunAtLoad": True,
                       "KeepAlive": {"SuccessfulExit": False}, "ProcessType": "Interactive",
                       "StandardErrorPath": str(log_path)}, fh)


def definition_path(label):
    return _plist(label)


def installed(label):
    return _plist(label).exists()


def uninstall(label):
    try:
        _plist(label).unlink()
    except OSError:
        pass


def loaded(label, run=subprocess.run, dom=None):
    """Does launchd still have the login item in this session?"""
    try:
        return run(["launchctl", "print", "%s/%s" % (dom or _domain(), label)], capture_output=True).returncode == 0
    except OSError:
        return False


def stop(label, wait=5.0, run=subprocess.run, sleep=time.sleep, dom=None):
    """launchd lets go of the login item, and this waits until it has. bootout returns
    before the service is gone, and a bootstrap straight after it fails with "5:
    Input/output error": an off and an on straight after did that on 2026-10-07, and the
    app then ran outside launchd. True if gone."""
    dom = dom or _domain()
    try:
        run(["launchctl", "bootout", "%s/%s" % (dom, label)], capture_output=True)
    except OSError:
        return True
    end = time.time() + wait
    while loaded(label, run, dom):
        if time.time() >= end:
            return False
        sleep(0.1)
    return True


def start(label, run=subprocess.run, sleep=time.sleep, dom=None):
    """(ok, what launchctl said): the login item loaded and started. Already loaded (a
    bootout still finishing, or another start): kickstarted instead, so it still runs
    under launchd and is restarted if it crashes."""
    dom = dom or _domain()
    for attempt in range(3):
        r = run(["launchctl", "bootstrap", dom, str(_plist(label))], capture_output=True, text=True)
        if r.returncode == 0:
            return True, ""
        if loaded(label, run, dom):
            k = run(["launchctl", "kickstart", "%s/%s" % (dom, label)], capture_output=True, text=True)
            if k.returncode == 0:
                return True, ""
        sleep(0.5 * (attempt + 1))
    said = (r.stderr or r.stdout or "").strip().splitlines()
    return False, said[0] if said else "launchctl failed"


def kickstart(label, run=subprocess.run, dom=None):
    try:
        r = run(["launchctl", "kickstart", "%s/%s" % (dom or _domain(), label)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    said = (r.stderr or r.stdout or "").strip().splitlines()
    return r.returncode == 0, "" if r.returncode == 0 else (said[0] if said else "launchctl failed")


def started_by_service(label, pid, env=None, run=subprocess.run):
    """launchd names the job in XPC_SERVICE_NAME; else ask launchctl for the job's pid."""
    env = os.environ if env is None else env
    if env.get("XPC_SERVICE_NAME") == label:
        return True
    try:
        out = run(["launchctl", "print", "%s/%s" % (_domain(), label)],
                  capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"^\s*pid = (\d+)", out or "", re.M)
    return bool(m) and int(m.group(1)) == pid
