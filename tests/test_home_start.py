"""A call's Claude Codes never start in the home folder (2026-10-27): started there, Claude Code
reads into the folders macOS guards (Desktop, Documents) as it starts, and macOS asks the user
in Standby's name while the call waits. They start in the agent's own folder instead, each
conversation they resume copied into its slot: a fork's source afresh every time, never back."""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import json
import os
import sys
import tempfile
from importlib.machinery import SourceFileLoader

HOME = os.path.realpath(tempfile.mkdtemp(prefix="sbhome-"))
os.environ["HOME"] = HOME                      # never the user's ~/.claude
AGENT_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "bin", "standby-agent")
sa = SourceFileLoader("standby_agent", AGENT_PY).load_module()
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-500:])


def transcript(folder, sid, lines):
    slot = os.path.join(HOME, ".claude", "projects", sa.project_slot(folder))
    os.makedirs(slot, exist_ok=True)
    path = os.path.join(slot, sid + ".jsonl")
    with open(path, "w") as fh:
        fh.write("".join(json.dumps({"cwd": folder, "n": n}) + "\n" for n in lines))
    return path


PROJECT = os.path.join(HOME, "work", "orion")
os.makedirs(PROJECT)
USER = "11111111-2222-4333-8444-555555555555"
OWN = "66666666-7777-4888-9999-000000000000"
mine = lambda sid: os.path.join(HOME, ".claude", "projects", sa.project_slot(sa.AGENT), sid + ".jsonl")

check("a project folder: started there, as before", sa.start_dir(PROJECT, ["claude", "--resume", USER]) == PROJECT)
check("  so is a folder in Desktop: its prompt is the user's own project (Standby says why)",
      sa.start_dir(os.path.join(HOME, "Desktop", "app"), []) == os.path.join(HOME, "Desktop", "app"))
check("the home folder: the agent's own folder instead", sa.start_dir(HOME, ["claude", "--session-id", OWN]) == sa.AGENT)
check("  and a folder above it (/Users, /) too", sa.start_dir(os.path.dirname(HOME), []) == sa.AGENT and sa.start_dir("/", []) == sa.AGENT)

src = transcript(HOME, USER, [1, 2])
argv = ["claude", "--resume", USER, "--fork-session", "--session-id", OWN]
check("a fork of the user's conversation in the home folder: started in the agent's folder, its source copied there",
      sa.start_dir(HOME, argv) == sa.AGENT and open(mine(USER)).read() == open(src).read())
transcript(HOME, USER, [1, 2, 3])
sa.start_dir(HOME, argv)
check("  the next call's fork: copied afresh, never an earlier call's copy",
      [json.loads(x)["n"] for x in open(mine(USER))] == [1, 2, 3])
with open(mine(USER), "a") as fh:
    fh.write(json.dumps({"n": "agent's"}) + "\n")
check("  nothing is ever written back into the user's slot",
      [json.loads(x)["n"] for x in open(src)] == [1, 2, 3])

own = transcript(HOME, OWN, ["old"])
with open(mine(OWN), "w") as fh:
    fh.write(json.dumps({"n": "newer, the agent's"}) + "\n")
sa.start_dir(HOME, ["claude", "--resume", OWN])
check("the agent's own conversation, resumed: copied only once, its newer turns kept",
      json.loads(open(mine(OWN)).read())["n"] == "newer, the agent's")

# A read macOS refuses in a guarded folder (Don't Allow, once): noted for the band, in the session's folder
SID = "talk-20261027-050000"
sdir = os.path.join(sa.ROOT, "sessions", SID)
os.makedirs(sdir, exist_ok=True)
refused = lambda: json.load(open(os.path.join(sdir, "refused.json"))) if os.path.exists(os.path.join(sdir, "refused.json")) else None
ev = lambda text: {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": text}]}}
sa.note_refused(SID, ev("EPERM: operation not permitted, scandir '/tmp/x'"))
check("a read refused outside the guarded folders: nothing said", refused() is None)
sa.note_refused(SID, ev([{"type": "text", "text": "Error: EPERM: operation not permitted, open '%s/Desktop/app/README.md'" % HOME}]))
check("one refused in Desktop: noted, by the folder's name", (refused() or {}).get("folder") == "Desktop", refused())
sa.note_refused(SID, ev("File does not exist: %s/Documents/x" % HOME))
check("  another error there (not a refusal): the note stays as it was", (refused() or {}).get("folder") == "Desktop")

print("ALL PASS" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
