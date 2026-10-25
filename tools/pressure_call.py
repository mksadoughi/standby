#!/usr/bin/env python3
"""How fast a call from Claude Code starts, and how fast its band's state answers, on a busy Mac.

    python3 tools/pressure_call.py              idle, then under the standard load
    python3 tools/pressure_call.py --load 0     idle only
    python3 tools/pressure_call.py --runs 5

The standard load is 2 x the CPU count of busy processes, all the time it measures. It runs
the app's own code (mac/ui.py) in a scratch STANDBY_ROOT and HOME, never the user's: a Mac
with a long history (sessions, Claude Code projects), and a stand-in recorder the "standby
start" command launches at once, so what's timed is the app's own path: from the request
(Accept on the band) to "the call is live" (its voice mode on), and the band's state poll.
The real recorder's model load comes on top of that, and isn't measured here."""
import argparse
import json
import multiprocessing
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CC = "0f3c2a77-1111-4222-8333-944455556666"


def busy(stop):
    while not stop.is_set():
        sum(i * i for i in range(10000))


def scratch():
    root = Path(tempfile.mkdtemp(prefix="sbpressure-"))
    home = root / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    for i in range(200):                         # a Claude Code with many projects
        d = home / ".claude" / "projects" / ("-Users-you-code-project-%03d" % i)
        d.mkdir()
        for j in range(5):
            (d / ("%08x-1111-4222-8333-%012x.jsonl" % (i, j))).write_text('{"type":"user"}\n' * 50)
    proj = home / ".claude" / "projects" / "-Users-you-code-orion"
    proj.mkdir()
    (proj / (CC + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in [
        {"type": "user", "message": {"role": "user", "content": "Can we talk this through on a call?"}, "cwd": "/tmp"}] * 200))
    sessions = root / "standby" / "sessions"
    sessions.mkdir(parents=True)
    for i in range(300):                         # a long history of meetings and calls
        s = sessions / ("meeting-2026%04d-100000" % i)
        s.mkdir()
        (s / "meta.json").write_text(json.dumps({"kind": "meeting", "started": 1.7e9 + i}))
        (s / "transcript.jsonl").write_text('{"t": 1, "text": "hello"}\n' * 100)
    # "standby start": a stand-in recorder, live at once (pidfile and ready file, as the real one writes)
    cli = root / "standby-cli"
    cli.write_text("""#!/bin/bash
ROOT="%s"
sid="talk-$(date +%%Y%%m%%d-%%H%%M%%S)-$$"
mkdir -p "$ROOT/sessions/$sid"
printf '{"kind": "talk", "started": %%s, "mic": true}' "$(date +%%s)" > "$ROOT/sessions/$sid/meta.json"
nohup "%s" -c "import time; time.sleep(3600)" "fake standby.py run" >/dev/null 2>&1 &
echo $! > "$ROOT/daemon.pid"
echo "$sid" > "$ROOT/daemon.ready"
""" % (root / "standby", sys.executable))
    cli.chmod(0o755)
    return root, home, cli


def measure(runs):
    """[(call start s, state poll s)] for `runs` calls, in this process (the app's code)."""
    sys.path.insert(0, str(HERE / "mac"))
    import ui
    ui.log = lambda m: None
    ui.CLI = os.environ["SB_PRESSURE_CLI"]
    ui.start_voice = lambda sid: {"ok": True, "state": {"state": "listening"}}     # the voice's own start is measured elsewhere
    ui.lite_gist_refresh = (lambda real: lambda cc, then=None: real(cc, then=None))(ui.lite_gist_refresh)
    out = []
    for _ in range(runs):
        t0 = time.time()
        r = ui.LITE_POST["/api/lite/call"]({"cc": CC, "cwd": "/tmp"})
        took = time.time() - t0
        if "session" not in r:
            raise SystemExit("the call didn't start: %r" % r)
        t1 = time.time()
        st = ui.LITE_GET["/api/lite/state"]({"cc": CC})
        poll = time.time() - t1
        if (st.get("live") or {}).get("kind") != "call":
            raise SystemExit("the state didn't show the call: %r" % st)
        ui.LITE_CALLS.pop(r["session"], None)
        pid = int((Path(os.environ["STANDBY_ROOT"]) / "daemon.pid").read_text())
        os.kill(pid, 9)
        os.waitpid(pid, os.WNOHANG) if False else None
        (Path(os.environ["STANDBY_ROOT"]) / "daemon.ready").unlink()
        time.sleep(1.0)                           # the recorder gone before the next call
        out.append((took, poll))
    return out


def child(runs, q):
    q.put(measure(runs))


def run(runs, load, background=False):
    root, home, cli = scratch()
    os.environ.update(STANDBY_ROOT=str(root / "standby"), HOME=str(home), STANDBY_NO_DEVICES="1",
                      STANDBY_CLAUDE="/usr/bin/false", SB_PRESSURE_CLI=str(cli),
                      STANDBY_RECORDER_MATCH="fake standby[.]py run")
    stop = multiprocessing.Event()
    burners = [multiprocessing.Process(target=busy, args=(stop,), daemon=True) for _ in range(load)]
    for b in burners:
        b.start()
    time.sleep(1.0 if load else 0)
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=child, args=(runs, q))       # a fresh app each time: nothing cached
    p.start()
    if background:                               # the app alone at background priority, the load at the usual
        subprocess.run(["taskpolicy", "-b", "-p", str(p.pid)], check=False)
    got = q.get(timeout=600)
    p.join()
    stop.set()
    for b in burners:
        b.join(2)
    shutil.rmtree(root, ignore_errors=True)
    return got


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--load", type=int, default=None, help="busy processes (default: 2 x the CPU count; 0: idle only)")
    ap.add_argument("--background", action="store_true",
                    help="run the app's code at macOS background priority, as an app in the background can get")
    a = ap.parse_args()
    load = 2 * (os.cpu_count() or 4) if a.load is None else a.load
    for label, n in ([("idle", 0)] + ([("load %d busy processes" % load, load)] if load else [])):
        got = run(a.runs, n, a.background)
        label += " (app in background)" if a.background else ""
        calls, polls = [g[0] for g in got], [g[1] for g in got]
        print("%-46s call start (request to live): median %.2f s, max %.2f s | state poll: median %.2f s, max %.2f s"
              % (label, statistics.median(calls), max(calls), statistics.median(polls), max(polls)))


if __name__ == "__main__":
    main()
