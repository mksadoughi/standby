"""The push cadence is per session: meta.json overrides the agent default."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile
root = tempfile.mkdtemp(); os.makedirs(os.path.join(root, "live"))
os.environ["STANDBY_ROOT"] = root
from importlib.machinery import SourceFileLoader
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT
ag = SourceFileLoader("ag", AGENT).load_module()
ag.sys.stderr = open(os.devnull, "w")
def meta(sid, **kw):
    json.dump(dict(session=sid, **kw), open(os.path.join(root, "live", sid + ".meta.json"), "w"))
fails = []
def check(label, ok, d=""):
    print("  %-52s %s" % (label, "OK" if ok else "FAIL")); ok or (fails.append(label), print("     " + str(d)))
meta("a"); f = ag.Feeder("a")
check("default is 10 sentences / 60 s", (f.push_lines, f.push_seconds) == (10, 60.0), (f.push_lines, f.push_seconds))
meta("b", push_lines=25, push_seconds=180); f = ag.Feeder("b")
check("session meta overrides the default", (f.push_lines, f.push_seconds) == (25, 180.0), (f.push_lines, f.push_seconds))
now = __import__("time").time()
f.last_push = now - 25
check("24 lines do not trigger at 25", not f.due([{}] * 24))
check("25 lines trigger", f.due([{}] * 25))
# The budget: whatever a session asks, never two pushes within 20 s.
meta("c", push_lines=1, push_seconds=5); f = ag.Feeder("c")
f.last_push = now - 10
check("budget: not within 20 s of the last push", not f.due([{}] * 40))
f.last_push = now - 21
check("  and due once 20 s have passed", f.due([{}]))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails)); sys.exit(1 if fails else 0)
