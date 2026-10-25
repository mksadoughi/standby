"""One agent server per root.

Two servers started at the same moment (two app requests both starting the
agent, as after a restart in test_ui) both passed the pidfile check before
either wrote it, and both served: every turn twice, and `stop` (by the
pidfile) stopped one. A leftover served on. Now the second one exits at
once. Only the servers this test starts are signalled, by their Popen."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbone-")
agentpath.guard(root)
env = dict(os.environ, STANDBY_ROOT=root)
cmd = ["/usr/bin/python3", agentpath.AGENT, "--engine", "mock", "serve"]
fails, started = [], []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


def start(n):
    procs = [subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
             for _ in range(n)]
    started.extend(procs)
    return procs


def settle(procs, secs=3.0):
    end = time.time() + secs
    while time.time() < end and sum(p.poll() is None for p in procs) > 1:
        time.sleep(0.05)
    time.sleep(0.5)                                  # a second one would have exited by now
    return [p for p in procs if p.poll() is None]


try:
    procs = start(4)
    alive = settle(procs)
    gone = [p for p in procs if p not in alive]
    check("four servers started at once: one serves, the others exit", len(alive) == 1, [p.poll() for p in procs])
    said = [(p.returncode, p.stderr.read()) for p in gone]
    check("  the others say so, and exit cleanly", all(rc == 0 and "already serving" in e for rc, e in said),
          [(rc, e[-300:]) for rc, e in said])
    pid = open(os.path.join(root, "agent", "server.pid")).read().strip()
    check("  the pidfile names the one serving", alive and pid == str(alive[0].pid), (pid, alive and alive[0].pid))
    r = subprocess.run(["/usr/bin/python3", agentpath.AGENT, "stop"], env=env, capture_output=True, text=True, timeout=20)
    check("stop: none left", alive and alive[0].wait(timeout=10) is not None and all(p.poll() is not None for p in procs),
          r.stdout)

    # The way two served: the second read the first's pidfile empty, mid-write, removed
    # it as stale and went on. Here it is simply gone: the lock still says no.
    first = start(1)[0]
    time.sleep(1.0)
    os.remove(os.path.join(root, "agent", "server.pid"))
    second = start(1)[0]
    try:
        second.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass                                         # it serves too: two at once
    check("a server whose pidfile went missing still keeps a second one out",
          second.returncode == 0 and "already serving" in second.stderr.read() and first.poll() is None,
          "the second one is %s" % ("serving too" if second.poll() is None else "gone, rc %s" % second.returncode))
    first.terminate(); first.wait(timeout=10)
    pf = os.path.join(root, "agent", "server.pid")
    open(pf, "w").close()                            # as read mid-write
    subprocess.run(["/usr/bin/python3", agentpath.AGENT, "alive"], env=env, capture_output=True, timeout=20)
    check("  and a check never removes a pidfile it cannot read yet", os.path.exists(pf))
    os.remove(pf)

    # One killed outright (no cleanup, the pidfile stays): the lock goes with it.
    first = start(1)[0]
    time.sleep(1.0)
    first.kill(); first.wait()
    again = start(1)[0]
    time.sleep(1.0)
    check("a server killed outright: the next one starts (the lock went with it)", again.poll() is None)
    again.terminate(); again.wait(timeout=10)
finally:
    for p in started:
        if p.poll() is None:
            p.kill()
            p.wait()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
