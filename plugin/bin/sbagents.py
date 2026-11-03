"""Agents: Claude Code conversations, on this Mac.

  holders()          {conversation id: registry entry} for each conversation a Claude
                     Code holds now (its own registry, ~/.claude/sessions), read fresh
  opened_where(e)    where a running one is, in the user's words
  note_created(...)  a conversation Standby made (a call's voice, a copy): the only kind
  delete(sid)        it ever deletes, for good

Standby never resumes or writes into a conversation of the user's: it reads the registry
to know which are open, and tidies away only the ones it made itself.

It runs with the host's python3, so 3.7 is enough.
"""

import fcntl
import re
import shutil
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import sbtimeline  # noqa: E402

REGISTRY = os.path.expanduser("~/.claude/sessions")
ROOT = os.path.expanduser(os.environ.get("STANDBY_ROOT", "~/.standby"))
NAME = "Standby"                    # the name Standby's own postmen ran under, never a holder


def log(msg):
    try:
        os.makedirs(os.path.join(ROOT, "agent"), exist_ok=True)
        with open(os.path.join(ROOT, "agent", "agents.log"), "a") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


# ---------------------------------------------------------------- who holds what

def running_claudes(registry=None):
    """Claude Code's registry of running sessions: [{"pid", "sessionId", ...}], live
    processes only (one that crashed can leave its file behind)."""
    out = []
    for path in glob.glob(os.path.join(registry or REGISTRY, "*.json")):
        try:
            with open(path) as fh:
                entry = json.load(fh)
            pid = int(entry.get("pid"))
            os.kill(pid, 0)
        except (IOError, OSError, ValueError, TypeError, AttributeError):
            continue
        out.append(dict(entry, pid=pid))
    return out


def opened_where(entry):
    """Where a running Claude Code is, in the user's words, and whether it is busy:
    {"where", "busy", "name"}: its kind first (a background job says so, wherever it was
    started from), then where it was started."""
    point, kind = str(entry.get("entrypoint") or ""), str(entry.get("kind") or "")
    if kind in ("bg", "background"):
        where = "a background session"
    elif "vscode" in point:
        where = "VS Code"
    elif point == "cli":
        where = "Terminal"
    else:
        where = "another app"
    name = entry.get("name")
    return {"where": where, "busy": entry.get("status") == "busy",
            "name": name if isinstance(name, str) and name else None}


def holders(registry=None):
    """{conversation id: entry} for every live Claude Code but Standby's own postmen.
    Several for one id: a busy one wins."""
    out = {}
    for entry in running_claudes(registry):
        sid = entry.get("sessionId")
        if not isinstance(sid, str) or not sid or entry.get("name") == NAME:
            continue
        if sid not in out or (entry.get("status") == "busy" and out[sid].get("status") != "busy"):
            out[sid] = entry
    return out


def unreadable(registry=None):
    """Live entries the registry has that don't say which conversation they hold (a
    Claude Code that changed its format): then nothing can be known to be free."""
    return [e for e in running_claudes(registry) if not isinstance(e.get("sessionId"), str) or not e["sessionId"]]


# ---------------------------------------------------------------- what it's doing

def describe_tool(name, inp):
    """One short, human line for a tool call, shown as live progress."""
    inp = inp if isinstance(inp, dict) else {}

    def short(v, n=90):
        v = str(v).replace("\n", " ")
        return v if len(v) <= n else v[:n - 1] + "…"
    home = os.path.expanduser("~")

    def path(v):
        return short(str(v).replace(home, "~"))
    if name == "Read":
        return "Reading %s" % path(inp.get("file_path", ""))
    if name == "Grep":
        where = inp.get("path") or inp.get("glob") or ""
        return "Searching for %r%s" % (short(inp.get("pattern", ""), 50),
                                       (" in " + path(where)) if where else "")
    if name == "Glob":
        return "Finding files %s" % short(inp.get("pattern", ""))
    if name == "Bash":
        return "Running %s" % short(inp.get("command", ""))
    if name == "Skill":
        return "Loading skill %s" % short(inp.get("skill") or inp.get("name") or "")
    return "Using %s" % (name or "a tool")


# ---------------------------------------------------------------- what's left of the outbox (from before)

def _outbox(session):
    return os.path.join(ROOT, "agent", "outbox", session)


MODEL_ID = re.compile(r"^(haiku|sonnet|opus|claude-[a-z0-9][a-z0-9.\-]{2,60})$")


def valid_model(model):
    """A model Standby may pass to `--model`: an alias, or a full id (never anything else)."""
    return bool(isinstance(model, str) and MODEL_ID.match(model))


def health(registry=None):
    """(ok, detail) for `standby check`: can Standby tell which conversations are open?"""
    reg = registry or REGISTRY
    if not os.path.isdir(reg):
        return True, "no Claude Code open now"
    bad = unreadable(reg)
    if bad:
        return False, "%d open Claude Code(s) don't say which conversation they hold: update Standby" % len(bad)
    live = running_claudes(reg)
    if any(not e.get("messagingSocketPath") for e in live):
        # Messages to that window wait until it's closed: worth saying, not a fault.
        return True, "%d open; one takes no messages from Standby until it's closed (update Claude Code)" % len(live)
    return True, "%d open" % len(live)


# ---------------------------------------------------------------- deleting one Standby started

def _created_path():
    return os.path.join(ROOT, "agent", "created.txt")


def note_created(sid, kind="agent", session=None):
    """A conversation Standby started here: the only kind it will ever delete. `kind`: "agent"
    (one the user asked for), or one of Standby's own helpers ("voice", a call's voice or one of its
    turns; "copy", a call's or a meeting's copy; "engine", a session's own Claude Code), never offered
    as an agent. `session`: the Standby session it was made for, so it goes when that call does."""
    os.makedirs(os.path.dirname(_created_path()), exist_ok=True)
    with open(_created_path(), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write("%s %s%s\n" % (sid, kind, " " + session if session else ""))


def _created_rows():
    try:
        with open(_created_path()) as fh:
            return [x.split() for x in fh if x.strip()]
    except (IOError, OSError):
        return []


def created():
    return set(row[0] for row in _created_rows())


def made_for(session):
    """The conversations Standby noted making for that Standby session, oldest first."""
    return [row[0] for row in _created_rows() if len(row) > 2 and row[1] != "agent" and row[2] == session]


def made_sessions():
    """The Standby sessions that have conversations noted as made for them."""
    return sorted(set(row[2] for row in _created_rows() if len(row) > 2 and row[1] != "agent"))


def helpers():
    """Conversations Standby made for its own use (voices, copies): never an agent to add."""
    return set(row[0] for row in _created_rows() if len(row) > 1 and row[1] != "agent")


def delete(session, registry=None):
    """deletes a conversation Standby started here, for good: its transcript and Claude Code's
    folder for it, and what is left of an outbox for it from before. {"ok", "deleted": [what]}
    or {"error"}. Never one Standby didn't start, and never while a window holds it."""
    if session not in created():
        return {"error": "Standby didn't start this conversation, so it won't delete it"}
    h = holders(registry).get(session)
    if h is not None:
        return {"error": "open", "where": opened_where(h)["where"]}
    gone = []
    path = sbtimeline.find(session)
    for target in ([path, path[:-len(".jsonl")]] if path else []):
        try:
            if os.path.isdir(target):
                shutil.rmtree(target)
                gone.append(target)
            elif os.path.isfile(target):
                os.remove(target)
                gone.append(target)
        except OSError as exc:
            return {"error": "Couldn't delete %s (%s)" % (os.path.basename(target), exc.__class__.__name__)}
    shutil.rmtree(_outbox(session), ignore_errors=True)
    for extra in (_outbox(session) + ".lock",):
        try:
            os.remove(extra)
        except OSError:
            pass
    with open(_created_path(), "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        keep = [x for x in fh.read().splitlines() if x.strip() and x.split()[0] != session]
        fh.seek(0)
        fh.truncate()
        fh.write("".join(x + "\n" for x in keep))
    log("deleted %s: %s" % (session[:8], ", ".join(os.path.basename(g) for g in gone) or "no transcript"))
    return {"ok": True, "deleted": gone}
