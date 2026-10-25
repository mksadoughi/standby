"""run_session's closures and its own names (tools/closure_names.py): a name a nested
function reads that run_session binds more than once, not checked by hand, fails here.
On 2026-10-08 the heartbeat's `parts = []` replaced item 11's `parts` dict, and a Room
meeting's first voice turn after a heartbeat crashed the channel and the recording."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, subprocess, sys, tempfile

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:800])


def lint(path, name="run_session"):
    r = subprocess.run([sys.executable, "tools/closure_names.py", path, name], capture_output=True, text=True)
    return r.returncode, r.stdout


rc, out = lint("mac/standby.py")
check("run_session: every name its closures share and it binds twice is checked", rc == 0, out)

# The lint itself, on the shape of the bug it is here for.
bug = os.path.join(tempfile.mkdtemp(prefix="sblint-"), "bug.py")
with open(bug, "w") as fh:
    fh.write('''def run_session():
    parts = {}

    def end_turn(turn):
        for done in [k for k in parts if k <= turn]:
            parts.pop(done)

    while True:
        parts = []
        for c in [1, 2]:
            parts.append("status %d" % c)
        end_turn(3)
''')
rc, out = lint(bug)
check("  it finds a closure's name the main loop binds again (the heartbeat's parts)",
      rc == 1 and "parts" in out and "end_turn" in out, out)
with open(bug, "w") as fh:
    fh.write('''def run_session():
    turn_parts = {}

    def mics(channels):
        return [c for c in channels if c]

    def end_turn(turn):
        for done in [k for k in turn_parts if k <= turn]:
            turn_parts.pop(done)

    for c in [1, 2]:
        pass
    while True:
        parts = []
        end_turn(3)
''')
rc, out = lint(bug)
check("  and not a comprehension's own name, nor one no closure reads", rc == 0, out)

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
