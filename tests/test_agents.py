"""Agents (plugin/bin/sbagents.py, mac/agents.py): who holds a conversation, the ones Standby
made for itself, and nothing of Standby's ever written into a conversation of the user's.

Claude Code's registry is files in a throwaway HOME, written the way Claude Code writes them."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="sbagents-home-"))
os.environ["HOME"] = str(home)                       # Claude Code's registry and transcripts: fakes
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "plugin" / "bin"))
sys.path.insert(0, str(REPO / "mac"))
import sbagents as sa  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-78s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


def wait(pred, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


REG = home / ".claude" / "sessions"
PROJ = home / ".claude" / "projects" / "-Users-you-code-orion"
REG.mkdir(parents=True)
PROJ.mkdir(parents=True)
ROOT = Path(os.environ["STANDBY_ROOT"])
FOLDER = home / "code" / "orion"
FOLDER.mkdir(parents=True)

def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def register(pid, sid, status="idle", entrypoint="claude-vscode", name="orion-0", sock="/tmp/cc-socks/x.sock"):
    path = REG / ("%d-%s.json" % (pid, sid[:4]))
    path.write_text(json.dumps({"pid": pid, "sessionId": sid, "kind": "interactive", "entrypoint": entrypoint,
                                "status": status, "name": name, "messagingSocketPath": sock,
                                "peerProtocol": 1}))
    return path


S1 = "11111111-1111-4111-8111-111111111111"     # open in VS Code
S2 = "22222222-2222-4222-8222-222222222222"     # open nowhere
S3 = "33333333-3333-4333-8333-333333333333"     # open in a Terminal, busy

# ---------------------------------------------------------------- the registry
print("who holds a conversation: Claude Code's own registry")
me = os.getpid()
register(me, S1, "idle", "claude-vscode", sock="/tmp/cc-socks/101.sock")
register(dead_pid(), S2, "busy", "claude-vscode")                 # a window that crashed, file left behind
register(me, S3, "busy", "cli", name="orion-1", sock="/tmp/cc-socks/103.sock")
register(me, S2, "busy", "sdk-cli", name="Standby")              # a postman: never a holder
h = sa.holders(str(REG))
check("live windows only; a crashed one's file and Standby's postman don't count",
      set(h) == {S1, S3}, sorted(h))
check("where, in the user's words: VS Code, Terminal, and whether it's busy",
      sa.opened_where(h[S1]) == {"where": "VS Code", "busy": False, "name": "orion-0"}
      and sa.opened_where(h[S3])["where"] == "Terminal" and sa.opened_where(h[S3])["busy"] is True)
register(me, S1, "busy", "cli", sock="/tmp/cc-socks/104.sock")   # the same conversation, busy elsewhere
check("two for one conversation: the busy one", sa.holders(str(REG))[S1]["status"] == "busy")
(REG / ("%d-%s.json" % (me, S1[:4]))).unlink()
register(me, S1, "idle", "claude-vscode", sock="/tmp/cc-socks/101.sock")

print("  a registry Standby can't read: health says so")
check("health: readable now", sa.health(str(REG))[0] is True, sa.health(str(REG)))
odd = REG / "odd.json"
odd.write_text(json.dumps({"pid": me, "session": S2, "status": "idle"}))      # a format Standby doesn't know
check("  an open Claude Code that doesn't say what it holds: health says so, for standby check",
      sa.health(str(REG))[0] is False and "update Standby" in sa.health(str(REG))[1])
odd.unlink()
check("health: no registry at all is fine (no Claude Code open)", sa.health(str(home / "none")) == (True, "no Claude Code open now"))

# ---------------------------------------------------------------- nothing written into the user's conversations
print("nothing of Standby's goes into a conversation of the user's")
check("no delivery, outbox or postman left in sbagents",
      not any(hasattr(sa, f) for f in ("deliver", "run_outbox", "hand_over", "postman", "enqueue", "outbox_busy")))
AGENT = str(REPO / "plugin" / "bin" / "standby-agent")
for cmd in (["deliver", "--id", S1, "--text", "eA=="], ["deliver-run", "--id", S1], ["delete-agent", "--id", S1]):
    p = subprocess.run([sys.executable, AGENT] + cmd, capture_output=True, text=True, timeout=30)
    check("  standby-agent %s: no such command" % cmd[0], p.returncode != 0 and "invalid choice" in p.stderr, p.stderr[-300:])
check("deleting a conversation Standby didn't start: refused", "won't delete it" in sa.delete(S1, str(REG)).get("error", ""))

print("  the conversations Standby made for itself are told apart from agents")
VX, CX = "a1a1a1a1-1111-4111-8111-111111111111", "c1c1c1c1-1111-4111-8111-111111111111"
sa.note_created(VX, "voice")
sa.note_created(CX, "copy")
check("a voice and a copy are helpers; an agent Standby started is not",
      {VX, CX} <= sa.helpers() and VX in sa.created() and not (sa.helpers() & {nadia for nadia in sa.created()
                                                                                if nadia not in (VX, CX)}), sa.helpers())
with open(sa._created_path(), "a") as fh:
    fh.write("d1d1d1d1-1111-4111-8111-111111111111\n")          # a line from before kinds: an agent
check("  a line from before kinds counts as an agent (deletable, not hidden)",
      "d1d1d1d1-1111-4111-8111-111111111111" in sa.created()
      and "d1d1d1d1-1111-4111-8111-111111111111" not in sa.helpers())

# ---------------------------------------------------------------- the Mac side
print("the Mac side: no hooks into a conversation, the files from before tidied away")
import messenger as mm  # noqa: E402
import agents  # noqa: E402


class FakeUI(object):
    MSG = {}
    MSG_POST = {}


ui = FakeUI()
m = mm.Messenger(Path(tempfile.mkdtemp(prefix="sbagents-ws-")), ui=ui)
m.attach()
clock = [time.time()]
ag = agents.Agents(ui, m, clock=lambda: clock[0])
ag.install(prune=False)
check("no delivery or voice hooks, no route that keeps a card",
      not any(k in ui.MSG for k in ("deliver", "voice_end", "voice_forget", "on_delivered", "on_dropped"))
      and "/api/msg/memory" not in ui.MSG_POST)
old = ag.messages_dir() / "a-00000001"
old.mkdir(parents=True)
(old / "note.txt").write_text("from before")
for f in (old / "note.txt", old):
    os.utime(str(f), (clock[0] - agents.FILES_KEEP - 86400,) * 2)
gone = ag.messages_dir() / "m-from-before"                      # a folder of its own from then, untouched since
gone.mkdir(parents=True)
os.utime(str(gone), (clock[0] - agents.FILES_KEEP - 86400,) * 2)
fresh = ag.messages_dir() / "a-00000002"
fresh.mkdir(parents=True)
(fresh / "new.txt").write_text("recent")
for f in (fresh / "new.txt", fresh):
    os.utime(str(f), (clock[0] - 60, clock[0] - 60))
ag.prune()
check("a message's files from before: gone once older than FILES_KEEP; newer ones stay",
      not (old / "note.txt").exists() and not gone.exists() and (fresh / "new.txt").exists())

w = m.add_workspace({"machine": "local", "folder": str(FOLDER), "agents": [{"session": S1}]})["workspace"]
aid = w["agents"][0]
print("  one conversation, one agent")
taken = m.store.agent(aid)["session"]
other_ws = m.add_workspace({"machine": "local", "folder": str(FOLDER), "agents": []})["workspace"]["id"]
r = m.add_agent({"workspace": other_ws, "session": taken})
check("adding a conversation that's already an agent in another workspace: refused, by name",
      r.get("error", "").startswith("That conversation is already "), r)


print()
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
