"""The folder Claude Code works in, on the server side: the recent project
folders read from ~/.claude/projects where the agent runs (each from the cwd its
newest transcript names; never Standby's own folder, temporary ones, gone ones,
or memory-only slots), the default as an absolute path, one folder checked
(there? a CLAUDE.md?), and a new session's own folder on /api/start."""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agentpath  # noqa: E402  (never the real ~/.standby)

fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:500])


root = Path(os.environ["STANDBY_ROOT"])
home = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbprojhome-")))
place = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbprojects-")))
tmp_like = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbscratch-")))
ENV = dict(os.environ, HOME=str(home), STANDBY_TEMP_DIRS=str(tmp_like) + "/")
slot = lambda path: re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(path)))


def folder(name, claude_md=False):
    d = place / name
    d.mkdir(parents=True, exist_ok=True)
    if claude_md:
        (d / "CLAUDE.md").write_text("# rules\n")
    return d


def said(where, cwds, age, conv="c1", in_slot=None):
    s = home / ".claude" / "projects" / slot(in_slot or where)
    s.mkdir(parents=True, exist_ok=True)
    f = s / (conv + ".jsonl")
    f.write_text("".join(json.dumps({"type": "user", "cwd": str(c)}) + "\n" for c in cwds))
    t = time.time() - age
    os.utime(f, (t, t))


def agent(*args):
    r = subprocess.run([sys.executable, agentpath.AGENT] + list(args), capture_output=True, text=True, timeout=30, env=ENV)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"raw": r.stdout + r.stderr}


newest, older = folder("trips", claude_md=True), folder("notes")
moved_from, moved_to = folder("old-place"), folder("new-place")
said(newest, [newest], age=60)
said(older, [older], age=3600)
said(moved_to, [moved_from, moved_to], age=600)                 # moved with /cd: its old folder named first
said(Path(root, "agent"), [Path(root, "agent")], age=5)        # Standby's own folder
(root / "agent").mkdir(exist_ok=True)
said(tmp_like, [tmp_like], age=10)                              # a temporary folder
gone = folder("gone")
said(gone, [gone], age=20)
os.rmdir(gone)
(home / ".claude" / "projects" / slot(folder("memory-only")) / "memory").mkdir(parents=True)

got = agent("projects", "--default=~/work")
paths = [p["path"] for p in got.get("projects", [])]
check("recent projects, newest first, each from its transcript's own folder",
      paths == [str(newest), str(moved_to), str(older)], got)
check("  never Standby's own folder, a temporary one, a gone one, or a memory-only slot",
      not any(x in " ".join(paths) for x in ("agent", "sbscratch", "gone", "memory-only")), paths)
first = got["projects"][0] if got.get("projects") else {}
check("  each says whether it has a CLAUDE.md, and when it was last used",
      first.get("claude_md") is True and got["projects"][2]["claude_md"] is False and first.get("exists") is True
      and abs(first.get("at", 0) - (time.time() - 60)) < 5, got.get("projects"))
check("  the default given back as an absolute path where the agent runs", got.get("default") == str(home / "work"), got)
check("  no default: \"\"", agent("projects", "--default=").get("default") == "")
for n in range(15):
    said(folder("p%02d" % n), [place / ("p%02d" % n)], age=7200 + n, conv="c%d" % n)
check("  at most 12", len(agent("projects").get("projects", [])) == 12)

check("a folder checked: there, with a CLAUDE.md", agent("check-folder", "--", str(newest)) ==
      {"path": str(newest), "exists": True, "claude_md": True})
check("  ~ expanded where the agent runs, and a missing one says so",
      agent("check-folder", "--", "~/nope") == {"path": str(home / "nope"), "exists": False, "claude_md": False})
(newest / ".claude").mkdir()
(older / ".claude").mkdir()
(older / ".claude" / "CLAUDE.md").write_text("x")
check("  a CLAUDE.md under .claude/ counts too", agent("check-folder", "--", str(older))["claude_md"] is True)

# The app's side, with the agent as above (This Mac).
os.environ["HOME"] = str(home)
import socket  # noqa: E402
_sock = socket.socket(); _sock.bind(("127.0.0.1", 0)); os.environ["STANDBY_UI_PORT"] = str(_sock.getsockname()[1]); _sock.close()
sys.path.insert(0, os.path.join(HERE, "..", "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
import transport  # noqa: E402
ui.log = lambda m: None


class Local(object):
    def agent_cmd(self, args):
        return [sys.executable, agentpath.AGENT] + list(args)


transport.for_config = lambda cfg=None: Local()
real_settings = ui.get_settings
settings = {}
ui.get_settings = lambda: dict(real_settings(), **settings)
saved_environ = dict(os.environ)
os.environ.update(STANDBY_TEMP_DIRS=ENV["STANDBY_TEMP_DIRS"])
# A new session's own folder; a resume keeps its own.
started, real_run = [], ui.subprocess.run


class Stop(Exception):
    pass


def fake_run(cmd, **kw):
    if not (isinstance(cmd, list) and len(cmd) > 1 and cmd[1] == "start"):
        return real_run(cmd, **kw)
    started.append(cmd)
    raise Stop()


ui.subprocess.run, ui.wait_for_shutdown = fake_run, lambda: True
try:
    ui._do_start(None, "meeting", str(older))
    check("/api/start with a folder picked: the session starts there",
          started[-1][started[-1].index("--workspace") + 1] == str(older), started[-1:])
    ui._do_start(None, "talk", "  ")
    check("  none picked: no folder (there is no default folder in Settings now)", "--workspace" not in started[-1], started[-1:])
finally:
    ui.subprocess.run = real_run
threads, real_do_start = [], ui._do_start
ui._do_start = lambda *a: threads.append(a)
ui.live_session = lambda: None
ui.STARTING.update(state=None)
ui.start_session(workspace=str(older))
end = time.time() + 10
while not threads and time.time() < end:
    time.sleep(0.02)
ui._do_start = real_do_start
check("  the route's folder reaches the start", [t[:4] for t in threads[-1:]] == [(None, "meeting", str(older), None)],
      threads[-1:])

# The chip follows the conversation: a /cd in Claude Code moves the transcript to
# another folder's slot, and the session header should name that folder, not the one
# the session recorded when it started. It must never hold up the page: the
# open session is reloaded every 1.5 s, and over ssh a lookup can take seconds.
conv = "c0ffee11-0000-4000-8000-000000000001"
(root / "agent").mkdir(exist_ok=True)
(root / "agent" / "meeting-20261009-140000.uuid").write_text(conv + "\n")
(root / "live").mkdir(exist_ok=True)
(root / "live" / "meeting-20261009-140000.meta.json").write_text(json.dumps({"session": "meeting-20261009-140000", "workspace": str(older)}))


def conversation_in(folder, cwds):
    d = home / ".claude" / "projects" / slot(folder)
    d.mkdir(parents=True, exist_ok=True)
    (d / (conv + ".jsonl")).write_text("".join(json.dumps({"type": "user", "cwd": str(c)}) + "\n" for c in cwds))
    return d


def settled(sid, secs=20):
    end = time.time() + secs
    while time.time() < end:
        with ui._FOLDER_LOCK:
            st = ui._FOLDER.get(sid)
            if st and not st["busy"]:
                return True
        time.sleep(0.02)
    return False


said_in = conversation_in(moved_from, [moved_from])
ui._FOLDER.clear()
first = ui.session_folder("meeting-20261009-140000", str(older), True)
check("the chip answers at once with what the session recorded, and looks it up meanwhile",
      first == str(older) and settled("meeting-20261009-140000"), first)
check("  then names the folder the conversation lives in",
      ui.session_folder("meeting-20261009-140000", str(older), True) == str(moved_from), ui.session_folder("meeting-20261009-140000", str(older), True))
conversation_in(moved_to, [moved_from, moved_to])           # /cd: its old folder named first
(said_in / (conv + ".jsonl")).unlink()
check("  within the 10 s it trusts an answer: the same folder, no new lookup",
      ui.session_folder("meeting-20261009-140000", str(older), True) == str(moved_from))
ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0)
settled("meeting-20261009-140000")
check("  once that lapses: the folder it was moved to", ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0)
      == str(moved_to), ui.session_folder("meeting-20261009-140000", str(older), True))
check("nothing said in it yet: what the session recorded", ui.session_folder("chip-2", str(older), False) == str(older))
ui._FOLDER.clear()
ui.session_folder("chip-3", None, True)
settled("chip-3")
check("  a session whose conversation is in Standby's own folder: no chip", ui.session_folder("chip-3", None, True) is None)

# A slow or unreachable host: the page never waits for it.
asked = []
real_agent_json = ui._agent_json


def slow(args, timeout=20, machine=None):
    asked.append(args)
    time.sleep(5)
    return real_agent_json(args, timeout, machine)


ui._agent_json = slow
ui._FOLDER.clear()
t0 = time.time()
for _ in range(8):                                    # the page's reloads, while it is still asking
    ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0)
took = time.time() - t0
check("the agent taking 5 s: eight reloads answer at once, from what is known",
      took < 0.5 and ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0) == str(older), took)
end = time.time() + 2                                 # the lookup's thread, under way (its machine read first)
while not asked and time.time() < end:
    time.sleep(0.02)
check("  and only one lookup is in flight for the session", len(asked) == 1, asked)
(root / "sessions").mkdir(exist_ok=True)
sdir = root / "sessions" / "meeting-20261009-140000"
sdir.mkdir(exist_ok=True)
(sdir / "meta.json").write_text(json.dumps({"started": time.time(), "workspace": str(older), "kind": "meeting"}))
(sdir / "chat.jsonl").write_text("")                  # something was asked: it has a conversation
srv = ui.serve()
import threading, urllib.request  # noqa: E402,E401
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%s/api/session/meeting-20261009-140000" % os.environ["STANDBY_UI_PORT"]
settled("meeting-20261009-140000", 90)           # the 5 s lookup above, still in flight, would write its answer in after the clear
ui._FOLDER.clear()
asked.clear()
t0 = time.time()
info = json.loads(urllib.request.urlopen(url, timeout=10).read())["info"]
took = time.time() - t0
check("  /api/session itself answers in well under a second while the host is slow",
      took < 1.0 and info["workspace"] == str(older) and len(asked) == 1, (took, info.get("workspace"), asked))
settled("meeting-20261009-140000", 90)           # the page's slow (5 s) lookup: on a loaded Mac it outlived 20 s once, and its late
                                # answer reset the back-off checked below (two lookups, not one)
ui._agent_json = lambda args, timeout=20, machine=None: (asked.append(args), None)[1]     # the host does not answer
ui._FOLDER.clear()
asked.clear()
for _ in range(5):
    ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0)
    settled("meeting-20261009-140000")
check("a lookup that failed: not tried again for a minute, and the recorded folder stands",
      len(asked) == 1 and ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0) == str(older), asked)
ui._FOLDER.clear()
asked.clear()
# 2 s, not less: with other suites beside this one, settling the first lookup took
# longer than a shorter back-off, and the second call then asked again.
ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0, backoff=2.0)     # fails: back off 2 s
settled("meeting-20261009-140000")
ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0, backoff=2.0)
settled("meeting-20261009-140000")
during = len(asked)
time.sleep(2.1)
ui.session_folder("meeting-20261009-140000", str(older), True, ttl=0, backoff=2.0)
settled("meeting-20261009-140000")
check("  after the back-off it asks again", during == 1 and len(asked) == 2, (during, asked))


class NoAgent(object):
    pass


ui._agent_json = real_agent_json
transport.for_config = lambda cfg=None: NoAgent()      # a transport that cannot run one at all
ui._FOLDER.clear()
ui.session_folder("meeting-20261009-140000", str(older), True)
settled("meeting-20261009-140000")
check("a transport with no agent at all: the recorded folder, and no page broken",
      ui.session_folder("meeting-20261009-140000", str(older), True) == str(older))
transport.for_config = lambda cfg=None: Local()
srv.shutdown()
ui._FOLDER.clear()

os.environ.clear()
os.environ.update(saved_environ)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
