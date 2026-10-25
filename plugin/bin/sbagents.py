"""Agents: Claude Code conversations, on this Mac.

  holders()          {conversation id: registry entry} for each conversation a Claude
                     Code holds now (its own registry, ~/.claude/sessions), read fresh
  open_in(entry)     where it's open: "vscode" | "terminal" | None
  deliver(...)       a call's or a meeting's summary to one conversation

One conversation, one writer: a summary never makes a second process resume a
conversation another one holds, and never forks it.

- Held by a Claude Code (a VS Code tab, a Terminal): the message goes to that process,
  which runs the turn itself. A "postman" hands it over: a short claude -p on Haiku whose
  one tool is SendMessage, Claude Code's own cross-session message. Standby never speaks
  that channel's wire format, and the receiver's own checks (its approval hold included)
  apply as to any other sender.
- Held by nobody: Standby runs the turn, claude -p --resume, one at a time per
  conversation (its outbox), so two messages never run at once either.

It runs with the host's python3, so 3.7 is enough.
"""

import fcntl
import re
import shutil
import signal
import glob
import json
import os
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import sbtimeline  # noqa: E402

REGISTRY = os.path.expanduser("~/.claude/sessions")
ROOT = os.path.expanduser(os.environ.get("STANDBY_ROOT", "~/.standby"))
NAME = "Standby"                    # the postman's name, as the receiver sees it (claude -n)
POSTMAN_MODEL = "haiku"
POSTMAN_WAIT = 90                   # s: it takes 3-7
# What a Claude Code started inside another one inherits and must not: it would take the
# parent's messaging identity and session.
INHERITED = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_MESSAGING_SOCKET",
             "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
             "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_NAME", "CLAUDE_CODE_AGENT",
             "CLAUDE_CODE_EXECPATH", "CLAUDE_PID", "CLAUDE_JOB_DIR")
# The modes a conversation can be resumed in as it was left; never bypassPermissions,
# which needs the user's own flag.
MODES = ("acceptEdits", "auto", "manual", "dontAsk", "plan")

def log(msg):
    try:
        os.makedirs(os.path.join(ROOT, "agent"), exist_ok=True)
        with open(os.path.join(ROOT, "agent", "agents.log"), "a") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def clean_env():
    env = dict(os.environ)
    for k in INHERITED:
        env.pop(k, None)
    return env


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


def open_in(entry):
    """"vscode" or "terminal" for a window the user has open; None for anything else
    (a claude -p, Standby's own or another app's, says sdk-cli)."""
    point = str((entry or {}).get("entrypoint") or "")
    if "vscode" in point:
        return "vscode"
    if point == "cli":
        return "terminal"
    return None


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


# ---------------------------------------------------------------- the outbox (held by nobody)

def _outbox(session):
    return os.path.join(ROOT, "agent", "outbox", session)


def _lock(session, wait=False):
    """The outbox's lock, held while its runner runs: an open file, or None if taken."""
    os.makedirs(os.path.dirname(_outbox(session)), exist_ok=True)
    fh = open(_outbox(session) + ".lock", "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except (IOError, OSError):
        fh.close()
        return None
    return fh


def outbox_busy(session):
    """A turn of Standby's for it is running, or waiting to."""
    if glob.glob(os.path.join(_outbox(session), "*.json")):
        return True
    if not os.path.exists(_outbox(session) + ".lock"):
        return False
    fh = _lock(session)
    if fh is None:
        return True
    fh.close()
    return False


MODEL_ID = re.compile(r"^(haiku|sonnet|opus|claude-[a-z0-9][a-z0-9.\-]{2,60})$")


def valid_model(model):
    """A model Standby may pass to `--model`: an alias, or a full id (never anything else)."""
    return bool(isinstance(model, str) and MODEL_ID.match(model))


def enqueue(session, text, folder, dirs=(), model=None):
    os.makedirs(_outbox(session), exist_ok=True)
    name = "%.6f-%s.json" % (time.time(), uuid.uuid4().hex[:8])
    tmp = os.path.join(_outbox(session), name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump({"text": text, "folder": folder, "dirs": list(dirs), "at": time.time(),
                   "model": model if valid_model(model) else None}, fh)
    os.rename(tmp, os.path.join(_outbox(session), name))


def spawn_runner(session, agent):
    """The outbox's runner, in the background: `standby-agent deliver-run`."""
    subprocess.Popen([sys.executable, agent, "deliver-run", "--id", session],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=clean_env())


def last_mode(session):
    """The permission mode the conversation was last in, if it may be resumed in it."""
    path = sbtimeline.find(session)
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 262144))
            lines = fh.read().splitlines()
    except (IOError, OSError):
        return None
    for line in reversed(lines):
        try:
            r = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("type") == "user" and r.get("permissionMode"):
            return r["permissionMode"] if r["permissionMode"] in MODES else None
    return None


# ---------------------------------------------------------------- a turn Standby runs, word by word
#
# Its words reach the transcript only once each block is written whole; the reply the user is waiting
# for can show as it is written: the turn runs with Claude Code's partial messages, and the block being
# written goes to drafts/<session>.json, which the timeline reader lays over the turn's draft.

DRAFT_EVERY = 0.25                  # s between writes of the block being written
DRAFT_STALE = 60.0                  # s: a draft nobody has written to for this long is not live


def _draft_path(session):
    return os.path.join(ROOT, "agent", "drafts", session + ".json")


def write_draft(session, text):
    path = _draft_path(session)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with _private(path + ".tmp") as fh:
        json.dump({"text": text, "at": time.time()}, fh)
    os.rename(path + ".tmp", path)


def clear_draft(session):
    try:
        os.remove(_draft_path(session))
    except OSError:
        pass


def live_draft(session, now=None):
    """The text of the block a turn Standby runs is writing for the user right now, or None."""
    try:
        with open(_draft_path(session)) as fh:
            d = json.load(fh)
    except (IOError, OSError, ValueError):
        return None
    if not isinstance(d, dict) or (now or time.time()) - float(d.get("at") or 0) > DRAFT_STALE:
        return None
    return d.get("text") if isinstance(d.get("text"), str) else None


def stream_turn(cmd, text, folder, session, clock=time.time):
    """Run one turn, its partial messages read as they come: the text block being written is kept in
    the session's draft (DRAFT_EVERY apart), cleared when the turn is over. Returns the exit status."""
    cmd = list(cmd)
    if "--output-format" in cmd:
        cmd[cmd.index("--output-format") + 1] = "stream-json"
    else:
        cmd += ["--output-format", "stream-json"]
    cmd += ["--verbose", "--include-partial-messages"]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         cwd=folder, env=clean_env(), universal_newlines=True, encoding="utf-8", errors="replace")
    block, wrote = "", 0.0
    try:
        p.stdin.write(text)
        p.stdin.close()
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            e = ev.get("event") if isinstance(ev, dict) and ev.get("type") == "stream_event" else None
            if not isinstance(e, dict):
                continue
            if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") == "text":
                block = ""                        # a new block: the draft is the latest one
            elif e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                block += e["delta"].get("text") or ""
                if clock() - wrote >= DRAFT_EVERY:
                    write_draft(session, block)
                    wrote = clock()
            elif e.get("type") == "content_block_stop" and block:
                write_draft(session, block)       # the block whole: the transcript has it, or soon will
                wrote = clock()
        return p.wait()
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        clear_draft(session)


def run_outbox(session, claude, registry=None):
    """deliver-run: each waiting message, oldest first, as a turn. If a window opened it
    meanwhile, the message goes to that window instead (one writer). Exits when empty."""
    lock = _lock(session)
    if lock is None:
        return 0                                   # its runner is already at it
    lock.truncate(0)                               # who runs it, so deleting the agent can stop it
    lock.write("%d\n" % os.getpid())
    lock.flush()
    try:
        while True:
            files = sorted(glob.glob(os.path.join(_outbox(session), "*.json")))
            if not files:
                return 0
            try:
                with open(files[0]) as fh:
                    item = json.load(fh)
            except (IOError, OSError, ValueError):
                os.remove(files[0])
                continue
            h = holders(registry).get(session)
            if h is not None:
                got = hand_over(h, session, item["text"], claude)
                log("outbox %s: open in %s now, handed over: %s" % (session[:8], open_in(h), got))
            else:
                cmd = [claude, "-p", "--resume", session, "--output-format", "json"]
                if valid_model(item.get("model")):          # the agent's own choice: this turn's model
                    cmd += ["--model", item["model"]]
                mode = last_mode(session)
                if mode:
                    cmd += ["--permission-mode", mode]
                for d in item.get("dirs") or []:            # the message's files: that folder only
                    if os.path.isdir(d):
                        cmd += ["--add-dir", d]
                folder = item.get("folder") if item.get("folder") and os.path.isdir(item["folder"]) else ROOT
                try:
                    rc = stream_turn(cmd, item["text"], folder, session)
                    log("outbox %s: turn ran, rc=%d" % (session[:8], rc))
                except OSError as exc:
                    log("outbox %s: %r" % (session[:8], exc))
            os.remove(files[0])
    finally:
        lock.close()


# ---------------------------------------------------------------- the postman (held by a window)

TOKENS_KEEP = 30 * 86400            # s: a token unused this long is dropped


def _tokens_path():
    return os.path.join(ROOT, "agent", "tokens.json")


def _private(path):
    """A file only its owner can read or write (on a shared host, nobody else)."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w")


def _with_tokens(change):
    """Read, change and write the token table under its lock; change(table) -> result.
    Its folder 0700 and the table 0600: nobody else on the host reads a token. Used tokens
    are kept for good (about 100 bytes a message), so a transcript read again from the
    start is still told apart; only tokens never used are dropped, after TOKENS_KEEP."""
    path = _tokens_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    fd = os.open(path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path) as fh:
                table = json.load(fh)
        except (IOError, OSError, ValueError):
            table = {}
        if not isinstance(table, dict):
            table = {}
        out = change(table)
        now = time.time()
        table = {k: v for k, v in table.items()
                 if isinstance(v, dict) and (v.get("used") or now - v.get("at", 0) < TOKENS_KEEP)}
        with _private(path + ".tmp") as fh:
            json.dump(table, fh)
        os.chmod(path + ".tmp", 0o600)
        os.rename(path + ".tmp", path)
    return out


def new_token(session, words):
    """A one-time token for `words` to `session`, recorded before they go, so the
    timeline knows them for Standby's however fast the receiver is read. Bound to the
    words: a token read off this machine can carry only the user's own text."""
    token = uuid.uuid4().hex
    _with_tokens(lambda t: t.__setitem__(token, {"session": session, "words": sbtimeline.words_key(words),
                                                 "at": time.time(), "used": None}))
    return token


def tokens():
    """{token: {"session", "at", "used"}}: what sbtimeline checks a Standby [MESSAGE] against."""
    try:
        with open(_tokens_path()) as fh:
            table = json.load(fh)
        return table if isinstance(table, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def mark_used(used):
    """{token: msg_id}: tokens a timeline read took, each bound to its message from now on."""
    if used:
        def change(t):
            for token, msg_id in used.items():
                if token in t and t[token].get("used") in (None, msg_id):
                    t[token]["used"] = msg_id
        _with_tokens(change)


def split_tag(text):
    """(tag, words) for a text Standby sends tagged ([MESSAGE], a summary, any tag the
    timeline hides), else (None, text)."""
    for tag in (sbtimeline.MESSAGE_TAG,) + sbtimeline.STANDBY_TAGS:
        if text.startswith(tag):
            return tag, text[len(tag):].strip("\n").lstrip(" ")
    return None, text


def peer_body(text, token=None):
    """What the receiver gets: the tag (with its one-time token, "[MESSAGE <token>]"), a
    line saying how to answer (SendMessage back would reach a postman that has already
    gone), then the words."""
    tag, words = split_tag(text)
    if tag is None:
        return text
    note = sbtimeline.MESSAGE_NOTE if tag == sbtimeline.MESSAGE_TAG else sbtimeline.SUMMARY_NOTE
    shown = "[%s %s]" % (tag[1:-1], token) if token else tag
    return "%s %s\n\n%s" % (shown, note, words)


def hand_over(entry, session, text, claude):
    """A text to the window in `entry`: a tagged one's token recorded first, for those
    exact words, then the postman."""
    tag, words = split_tag(text)
    return postman(entry, peer_body(text, new_token(session, words) if tag else None), claude)


def postman_prompt(to, body):
    fence = "=====" + uuid.uuid4().hex
    return ("Call the SendMessage tool exactly once, with to set to %s and message set to the text "
            "between the two %s lines, exactly as it is: change nothing, add nothing. Then reply with "
            "only the word done.\n%s\n%s\n%s" % (json.dumps(to), fence, fence, body, fence))


def parse_postman(out):
    """(msg_id or None, the text it sent or None, an error or None) from its stream-json."""
    sent, msg_id, error = None, None, None
    for line in (out or "").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if r.get("type") == "assistant":
            for b in (r.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SendMessage":
                    sent = (b.get("input") or {}).get("message")
        elif r.get("type") == "user":
            res = r.get("tool_use_result") or r.get("toolUseResult")
            if isinstance(res, dict):
                if res.get("success") and res.get("msg_id"):
                    msg_id = res["msg_id"]
                elif res.get("success") is False:
                    error = str(res.get("message") or res.get("error") or "it wasn't accepted")
        elif r.get("type") == "result" and r.get("is_error"):
            error = error or str(r.get("result") or "the postman failed")
    if msg_id is None and error is None:
        error = "the message wasn't handed over"
    return msg_id, sent, error


_HELP = {}


def accepts(claude, flag):
    """Does this Claude Code take `flag` (read once from its --help)?"""
    if claude not in _HELP:
        try:
            _HELP[claude] = subprocess.run([claude, "--help"], stdin=subprocess.DEVNULL, capture_output=True,
                                           text=True, timeout=20).stdout or ""
        except (OSError, subprocess.SubprocessError):
            _HELP[claude] = ""
    return flag in _HELP[claude]


def tidy_postman():
    """Conversations postmen saved before they ran without one (or on a Claude Code that keeps
    them anyway): Claude Code's folder for the postman's folder, which holds only theirs. They
    would show in the user's list of sessions as "Standby"."""
    slot = os.path.join(os.path.expanduser("~/.claude/projects"),
                        re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.path.join(ROOT, "agent", "postman"))))
    if os.path.isdir(slot):
        shutil.rmtree(slot, ignore_errors=True)


def postman(entry, body, claude):
    """Hands `body` to the Claude Code in `entry` (a registry entry): {"msg_id"} or {"error"}."""
    sock = entry.get("messagingSocketPath")
    if not sock:
        return {"error": "That Claude Code doesn't take messages (update Claude Code)"}
    here = os.path.join(ROOT, "agent", "postman")
    os.makedirs(here, exist_ok=True)
    cmd = [claude, "-p", "--model", POSTMAN_MODEL, "-n", NAME, "--output-format", "stream-json", "--verbose",
           "--tools", "SendMessage", "--allowedTools", "SendMessage"]
    if accepts(claude, "--no-session-persistence"):
        cmd.append("--no-session-persistence")      # a one-off: nothing saved, nothing in the user's list
    try:
        r = subprocess.run(cmd, input=postman_prompt("uds:" + sock, body), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=here, env=clean_env(), timeout=POSTMAN_WAIT)
    except subprocess.TimeoutExpired:
        return {"error": "Delivering took too long"}
    except OSError as exc:
        return {"error": "Couldn't run Claude Code (%s)" % exc.__class__.__name__}
    tidy_postman()
    msg_id, sent, error = parse_postman(r.stdout)
    if error:
        log("postman to %s: %s" % (entry.get("pid"), error))
        return {"error": error}
    if sent is None or sent.rstrip("\n") != body.rstrip("\n"):
        log("postman to %s: the text sent differs from the one given" % entry.get("pid"))
    return {"msg_id": msg_id}


# ---------------------------------------------------------------- the two entry points

FILES_KEEP = 90 * 86400            # s: a message's files are kept this long where the agent runs


def prune_files(now=None):
    """Message files copied here (live/messages/<agent>/, one folder per agent) more than
    FILES_KEEP ago: gone, one by one; a folder left empty goes too."""
    now = now or time.time()
    for d in glob.glob(os.path.join(ROOT, "live", "messages", "*")):
        try:
            for f in glob.glob(os.path.join(d, "*")):
                if now - os.path.getmtime(f) > FILES_KEEP:
                    os.remove(f)
            if not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass


def deliver(session, text, folder, claude, agent, registry=None, model=None):
    """{"delivery": "sent" | "queued" | "failed", "msg_id"?, "open_in"?, "error"?}.
    sent: it's with the agent; queued: it reads it when it finishes what it's doing."""
    text = (text or "").rstrip("\n")
    if not text.strip():
        return {"delivery": "failed", "error": "Nothing to send"}
    prune_files()
    h = holders(registry).get(session)
    if outbox_busy(session):                   # Standby's turn for it runs: in line after it
        enqueue(session, text, folder, model=model)
        spawn_runner(session, agent)
        return {"delivery": "queued"}
    if h is not None:
        got = hand_over(h, session, text, claude)
        if got.get("error"):
            return {"delivery": "failed", "error": got["error"], "open_in": open_in(h)}
        return {"delivery": "queued" if h.get("status") == "busy" else "sent", "msg_id": got["msg_id"],
                "open_in": open_in(h)}
    if unreadable(registry):
        # Some open Claude Code doesn't say what it holds: running a turn here could make
        # two writers of one conversation. Never guessed.
        log("deliver %s: the registry has entries without a sessionId: not run" % session[:8])
        return {"delivery": "failed", "error": "Standby can't tell whether this agent is open elsewhere "
                                               "(update Standby)"}
    enqueue(session, text, folder, model=model)
    spawn_runner(session, agent)
    return {"delivery": "sent"}


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


def note_created(sid, kind="agent"):
    """A conversation Standby started here: the only kind it will ever delete. `kind`: "agent"
    (one the user asked for), or one of Standby's own helpers ("voice", an agent's voice;
    "copy", a call's or a meeting's copy), never offered as an agent."""
    os.makedirs(os.path.dirname(_created_path()), exist_ok=True)
    with open(_created_path(), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write("%s %s\n" % (sid, kind))


def _created_rows():
    try:
        with open(_created_path()) as fh:
            return [x.split() for x in fh if x.strip()]
    except (IOError, OSError):
        return []


def created():
    return set(row[0] for row in _created_rows())


def helpers():
    """Conversations Standby made for its own use (voices, copies): never an agent to add."""
    return set(row[0] for row in _created_rows() if len(row) > 1 and row[1] != "agent")


def _stop_runner(session, wait=5.0):
    """The outbox's runner for it, and the turn it runs (its own process group): stopped."""
    path = _outbox(session) + ".lock"
    if not os.path.exists(path) or _lock_free(session):
        return
    try:
        with open(path) as fh:
            pid = int(fh.read().strip() or 0)
        if pid > 0 and os.getpgid(pid) == pid:     # started in a session of its own: the runner itself
            os.killpg(pid, signal.SIGTERM)
    except (IOError, OSError, ValueError):
        pass
    end = time.time() + wait
    while time.time() < end and not _lock_free(session):
        time.sleep(0.1)


def _lock_free(session):
    fh = _lock(session)
    if fh is None:
        return False
    fh.close()
    return True


def delete(session, registry=None):
    """deletes a conversation Standby started here, for good: Standby's runner for it
    stopped, its transcript and Claude Code's folder for it, Standby's outbox and tokens
    for it. {"ok", "deleted": [what]} or {"error"}. Never one Standby didn't start, and
    never while a window holds it."""
    if session not in created():
        return {"error": "Standby didn't start this conversation, so it won't delete it"}
    h = holders(registry).get(session)
    if h is not None:
        return {"error": "open", "where": opened_where(h)["where"]}
    _stop_runner(session)
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
    _with_tokens(lambda t: [t.pop(k) for k in [k for k, v in t.items() if v.get("session") == session]])
    with open(_created_path(), "r+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        keep = [x for x in fh.read().splitlines() if x.strip() and x.split()[0] != session]
        fh.seek(0)
        fh.truncate()
        fh.write("".join(x + "\n" for x in keep))
    log("deleted %s: %s" % (session[:8], ", ".join(os.path.basename(g) for g in gone) or "no transcript"))
    return {"ok": True, "deleted": gone}
