"""The agent's session names are never paths (S1, on the agent's
side): a name goes into agent/<name>.uuid, .hold, .fork and live/<name>.*, so "..",
"../x", "a/b" or ".x" from --session, from live/CURRENT or from a queued question
are refused, and nothing is written outside the agent's own folders. Real names, short
or as the Mac makes them, work as before. A throwaway STANDBY_ROOT; no Claude."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import os, subprocess, sys, tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

root = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbagentname-")))
os.environ["STANDBY_ROOT"] = str(root / "standby")
(root / "standby" / "agent" / "spool").mkdir(parents=True)
(root / "standby" / "live").mkdir()
AGENT = agentpath.AGENT
agent = SourceFileLoader("standby_agent_names", AGENT).load_module()
fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


BAD = ["..", ".", "../x", "../../escape", "a/b", "/tmp/x", ".hidden", "-rf", "x" * 101, "meeting-20261013-090000/..",
       "name with space", "", "tab\tname"]
GOOD = ["none", "s1", "meeting-20261013-090000", "talk-20260930-1420", "meeting-20261013-090000-2"]
check("good names pass: short ones, the Mac's, and \"none\"", all(agent.session_name(n) == n for n in GOOD))
check("  never \".\", \"..\", a path, a dot name or anything else", not [n for n in BAD if agent.session_name(n)],
      [n for n in BAD if agent.session_name(n)])

before = sorted(str(p) for p in root.rglob("*"))
refused = []
for bad in [b for b in BAD if b]:
    for cmd in (["held"], ["copy"], ["where"], ["resume-cmd"], ["title"], ["q", "hello"]):
        r = subprocess.run([sys.executable, AGENT] + cmd + ["--session=" + bad], capture_output=True, text=True,
                           timeout=60, env=dict(os.environ))
        if r.returncode == 0 or "not a session name" not in r.stderr:
            refused.append((cmd[0], bad, r.returncode, r.stderr[-120:]))
check("every command refuses a --session that isn't a name, before doing anything", not refused, refused[:4])
after = sorted(str(p) for p in root.rglob("*"))
check("  and nothing was written anywhere", before == after, sorted(set(after) - set(before))[:5])

(root / "standby" / "live" / "CURRENT").write_text("../../escape\n")
check("a live/CURRENT that isn't a name: no session", agent.current_session() == "none")
(root / "standby" / "live" / "CURRENT").write_text("meeting-20261013-090000\n")
check("  a real one, as before", agent.current_session() == "meeting-20261013-090000")
spool = root / "standby" / "agent" / "spool"
(spool / ("1-0001" + agent.ASKED_FOR)).write_text("../x")
(spool / ("1-0002" + agent.ASKED_FOR)).write_text("talk-20261013-090000")
check("a queued question naming a session that isn't a name: the live one instead",
      agent.asked_session("1-0001") is None and agent.asked_session("1-0002") == "talk-20261013-090000")

if fails:
    for label in fails:
        print("  FAILED: %s" % label.strip())
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
