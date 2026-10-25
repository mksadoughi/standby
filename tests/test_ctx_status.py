"""standby-ctx status and sessions name the meeting this Mac is recording now, as Standby itself
reads it (daemon.ready and a running recorder), even when it isn't delivered to live/ (a meeting
from Claude Code runs with Standby's own Claude off): not an older delivered one, not "ended"."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json
import os
import subprocess
import sys
import tempfile
import time

CTX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "bin", "standby-ctx")
root = tempfile.mkdtemp(prefix="sbctx-")
MATCH = "sbctxfake%d[.]py run" % os.getpid()        # a recorder of this test's own: never the real ones
ENV = dict(os.environ, STANDBY_ROOT=root, STANDBY_RECORDER_MATCH=MATCH)
fails = []


def check(label, ok, detail=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     %s" % (detail,))


def ctx(*args):
    return subprocess.run([sys.executable, CTX] + list(args), capture_output=True, text=True, env=ENV, timeout=60).stdout


def row(i):
    return json.dumps({"t": i, "end": i + 1, "speaker": "Room", "spk": 1, "text": "Line %d." % i}) + "\n"


live = os.path.join(root, "live")
os.makedirs(live)
OLD, NOW = "meeting-20261020-104446", "meeting-20261020-105443"
open(os.path.join(live, OLD + ".jsonl"), "w").write(row(1))           # an older meeting, delivered
open(os.path.join(live, NOW + ".jsonl"), "w").write(row(1))           # this one: delivered at first, then not
time.sleep(0.05)
os.utime(os.path.join(live, OLD + ".jsonl"))                          # the older file touched last
sdir = os.path.join(root, "sessions", NOW)
os.makedirs(sdir)
open(os.path.join(sdir, "transcript.jsonl"), "w").write("".join(row(i) for i in range(1, 5)))
json.dump({"kind": "meeting", "started": time.time() - 120}, open(os.path.join(sdir, "meta.json"), "w"))
open(os.path.join(root, "daemon.ready"), "w").write(NOW + "\n")

fake = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", MATCH.replace("[.]", ".")])
try:
    time.sleep(0.3)
    out = ctx("status")
    check("status: the meeting recording now, live, though not delivered", "session : %s" % NOW in out and "state   : LIVE" in out, out)
    check("  its lines from this Mac's own transcript, not the delivered copy behind it", "lines   : 4" in out, out)
    check("  sessions: it's listed, marked live", any(l.startswith(NOW) and "LIVE" in l for l in ctx("sessions").splitlines()), ctx("sessions"))
finally:
    fake.kill()
    fake.wait()
time.sleep(0.2)
json.dump({"agent": "lite:x", "paused_at": time.time()}, open(os.path.join(sdir, "agent.json"), "w"))
out = ctx("status")
check("no recorder, the meeting paused: still that one, paused", "session : %s" % NOW in out and "state   : paused" in out, out)
json.dump({"agent": "lite:x", "ended_at": time.time()}, open(os.path.join(sdir, "agent.json"), "w"))
out = ctx("status")
check("  ended: the latest meeting, by its own transcript, ended", "session : %s" % NOW in out and "state   : ended" in out, out)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
