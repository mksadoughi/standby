#!/usr/bin/env python3
"""Standby app: sessions, chat and transcript for your Claude Code meeting companion.

    ui.py --window      native app window (pywebview), the normal way
    ui.py               serve http://127.0.0.1:8765 only (for a browser, or tests)

The page is served on 127.0.0.1 only, so nothing is reachable from the network.
It drives the existing pieces instead of duplicating them:

* start / stop / resume  -> the `standby` CLI (same recorder as the terminal)
* questions, titles       -> the agent through transport.py (this Mac or SSH)
* sessions and transcript -> files under ~/.standby/sessions/<id>/

Closing the window stops any recording, so capture never outlives the app.
"""

import datetime
import json
import locale
import math
import os
import queue
import shlex
import re
import hmac
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Opened from Finder, the app gets no language settings, so Python falls back to
# ASCII and fails on the first non-ASCII character in an answer or a transcript.
# Use UTF-8 here and in everything this app starts.
os.environ["PYTHONUTF8"] = "1"
os.environ.setdefault("LANG", "en_US.UTF-8")
if locale.getpreferredencoding(False).lower().replace("-", "") != "utf8":
    for _name in ("en_US.UTF-8", "UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, _name)
            break
        except locale.Error:
            pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import transport  # noqa: E402
from native import app, permissions, window, workspace  # noqa: E402  (this build, Screen & System Audio Recording, the window, an app's icon)
import vocabulary  # noqa: E402  (the words the meetings teach dictation)
import addmac  # noqa: E402  (Add your Mac: an agent's meeting)
import forget  # noqa: E402  (a session gone for good: the window's delete and `standby purge`)
import watchdog  # noqa: E402  (a freeze leaves its stack in ui.log)
from native import qos  # noqa: E402  (a call's threads ahead of a busy Mac's other work)
from calltrace import trace  # noqa: E402  (a call's trace.jsonl: hand-offs, pauses)
import calltrace  # noqa: E402
calltrace.source("app")
import sbledger  # noqa: E402  (plugin/bin, on the path calltrace sets: a call's record)

ROOT = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby"))
SESSIONS = ROOT / "sessions"
READY = ROOT / "daemon.ready"
CLI = os.environ.get("STANDBY_CLI", str(HERE / "standby"))
PORT = int(os.environ.get("STANDBY_UI_PORT", "8765"))
# Every request carries this launch's token (X-Standby-Token, or ?token= where a header can't go: the page
# itself, EventSource, a download link), and names 127.0.0.1 or localhost on this port as its Host: a web page
# elsewhere, even one that rebinds its own name to 127.0.0.1, reaches nothing. The token is written to
# ~/.standby/ui.token (owner only) for Standby's own clients: the mod in Claude Code reads it there.
TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Standby-Token"
OPEN_PATHS = ("/agents.js", "/agents.css")     # the page's own code: no data, loaded by <script> and <link>
MAX_BODY = 32 * 1024 * 1024                    # a request body larger than this is refused (413)
TEST_ROUTES = os.environ.get("STANDBY_TEST_ROUTES") == "1"     # routes for tests only (/api/start)
TITLE_AFTER_LINES = 12

_lock = threading.Lock()
PENDING = {}                     # sid -> {"question", "started", "progress": [...]}
STARTING = {"state": None}       # {"state": "starting"|"error", "error", "since", "resume"}
TITLED = set()                   # sessions we already asked the agent to title


def log(msg):
    # the time on each line, so a call's pause, timers and resume read on one clock with the daemon's
    sys.stderr.write("[standby-app] %s %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


# ---------------------------------------------------------------- state

_PYTHON = re.compile(r"[Pp]ython[0-9.]*$")


# Tests narrow this to their own fake, so a real recording elsewhere on the Mac
# doesn't look like their recorder still shutting down.
RECORDER_MATCH = os.environ.get("STANDBY_RECORDER_MATCH", "standby[.]py run")


_RECORDERS = {"at": 0.0, "pids": [], "pidfile": None, "not_ours": None, "thread": None}
_RECORDERS_LOCK = threading.Lock()
RECORDERS_EVERY = 2.0         # s between the background looks for recorders (each a pgrep)
PIDFILE = ROOT / "daemon.pid"  # the recorder the CLI started (mac/standby writes it)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _pgrep_recorders():
    """One real look: pids of every python running standby.py. The pattern is in every
    pgrep's own argv, so a bare `pgrep -f` also caught the CLI's pgrep, or this app's
    from another thread, and a start was refused over a process already gone. Keep in
    step with sb_recorders in mac/standby."""
    r = subprocess.run(["pgrep", "-lf", RECORDER_MATCH], capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = (line.split(None, 2) for line in r.stdout.splitlines())
    return [f[0] for f in rows if len(f) > 1 and _PYTHON.search(f[1])]


def _took_look(pids):
    """A real look's answer kept, and what it says of the pidfile's pid: alive but not a
    recorder (a pid used again) is left out until the pidfile changes."""
    pidfile = _pidfile_pid()
    with _RECORDERS_LOCK:
        _RECORDERS.update(at=time.time(), pids=list(pids),
                          not_ours=pidfile if pidfile and pidfile not in pids and _alive(pidfile) else None)


def _pidfile_pid():
    try:
        return PIDFILE.read_text().strip() or None
    except OSError:
        return None


def _watch_recorders():
    while True:
        try:
            _took_look(_pgrep_recorders())
        except Exception as exc:
            log("recorders: a look failed (%r)" % exc)
        time.sleep(RECORDERS_EVERY)


def recorders(fresh=False):
    """Pids of every live recorder, answered without starting a process: the pidfile's
    recorder, checked in this process, and any other the background look (a pgrep every
    RECORDERS_EVERY s) has seen that is still alive. A request never waits on a pgrep, so
    a busy Mac doesn't stall the window or the orb. fresh: a real look now, for anything
    that must catch a stray recorder before starting one."""
    if fresh:
        pids = _pgrep_recorders()
        _took_look(pids)
        return pids
    with _RECORDERS_LOCK:
        if _RECORDERS["thread"] is None:
            _RECORDERS["thread"] = threading.Thread(target=_watch_recorders, name="recorders", daemon=True)
            _RECORDERS["thread"].start()
        seen, not_ours = list(_RECORDERS["pids"]), _RECORDERS["not_ours"]
    pidfile = _pidfile_pid()
    out = [p for p in seen if _alive(p)]
    if pidfile and pidfile != not_ours and pidfile not in out and _alive(pidfile):
        out.insert(0, pidfile)
    return out


def live_session(fresh=False):
    if not recorders(fresh):
        return None
    try:
        return READY.read_text().strip() or None
    except OSError:
        return None


def read_jsonl(path):
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, data):
    """Whole or not at all: a reader polling it never sees half a file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def default_title(started, kind="meeting"):
    t = time.localtime(started)
    return "%s · %s %d, %02d:%02d" % ("Talk" if kind == "talk" else "Meeting", time.strftime("%b", t),
                                      t.tm_mday, t.tm_hour, t.tm_min)


_ASK = object()


def _read_list(path):
    try:
        out = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return out if isinstance(out, list) else []


# A session that isn't live, as session_info last made it, with what its folder held then:
# the sessions list asks for every session each poll, and an ended one's files don't change.
_INFO = {}
_INFO_LOCK = threading.Lock()


def _size_time(st):
    return st.st_size, st.st_mtime_ns


def _folder_mark(sdir):
    """What changes when anything in a session's folder does: each file's name, size and time."""
    try:
        return tuple(sorted((e.name,) + _size_time(e.stat()) for e in os.scandir(sdir)))
    except OSError:
        return None


def _same_start(path, hit, st):
    """Is the file still the one read before, only longer: same inode, no shorter, and the
    bytes just before where that read stopped are the same (a rewrite in place reads again)."""
    if not hit or hit["ino"] != st.st_ino or hit["offset"] > st.st_size:
        return False
    if not hit["offset"]:
        return True
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, hit["offset"] - 64))
            return fh.read(min(64, hit["offset"])) == hit["tail"]
    except OSError:
        return False


def _tail(path, offset):
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, offset - 64))
            return fh.read(min(64, offset))
    except OSError:
        return b""


# A transcript's line count and last end, read on from where the last look stopped: the live
# one grows a line at a time, and reading all of it each poll was most of a poll's time.
_LINES = {}


def transcript_stats(path):
    """(lines, the last line's end) of a transcript.jsonl."""
    try:
        st = os.stat(path)
    except OSError:
        return 0, 0
    key = str(path)
    with _INFO_LOCK:
        hit = _LINES.get(key)
    if _same_start(path, hit, st):
        n, end, offset = hit["lines"], hit["end"], hit["offset"]
    else:
        n, end, offset = 0, 0, 0
    if offset < st.st_size:
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read(st.st_size - offset)
        except OSError:
            chunk = b""
        whole = chunk[:chunk.rfind(b"\n") + 1]              # a line being written waits for the next look
        for line in whole.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            n += 1
            end = row.get("end", 0) if isinstance(row, dict) else end
        offset += len(whole)
    with _INFO_LOCK:
        _LINES[key] = {"ino": st.st_ino, "offset": offset, "lines": n, "end": end, "tail": _tail(path, offset)}
    return n, end


# The rows of the transcripts being looked at (the live one, one opened), read on from where
# the last look stopped: the window asks for its session's whole transcript each poll.
_ROWS = {}
ROWS_KEEP = 4                     # transcripts kept read; the one asked longest ago goes first


def transcript_rows(path):
    """A transcript.jsonl's rows, as read_jsonl gives them."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = str(path)
    with _INFO_LOCK:
        hit = _ROWS.pop(key, None)
    if not _same_start(path, hit, st):
        hit = {"ino": st.st_ino, "offset": 0, "rows": []}
    if hit["offset"] < st.st_size:
        try:
            with open(path, "rb") as fh:
                fh.seek(hit["offset"])
                chunk = fh.read(st.st_size - hit["offset"])
        except OSError:
            chunk = b""
        whole = chunk[:chunk.rfind(b"\n") + 1]              # a line being written waits for the next look
        for line in whole.splitlines():
            line = line.strip()
            if line:
                try:
                    hit["rows"].append(json.loads(line))
                except ValueError:
                    pass
        hit["offset"] += len(whole)
    hit["tail"] = _tail(path, hit["offset"])
    with _INFO_LOCK:
        _ROWS[key] = hit
        while len(_ROWS) > ROWS_KEEP:
            _ROWS.pop(next(iter(_ROWS)))
    return list(hit["rows"])


def session_info(sid, live=_ASK):
    """live: the live session's id when the caller already knows it. One that isn't live
    comes from _INFO while nothing in its folder has changed."""
    sdir = SESSIONS / sid
    if live is _ASK:
        live = live_session()
    mark = _folder_mark(sdir) if sid != live else None
    if mark is not None:
        with _INFO_LOCK:
            hit = _INFO.get(sid)
        if hit and hit[0] == mark:
            return dict(hit[1])
    info = _session_info(sid, sdir, live)
    if mark is not None:
        with _INFO_LOCK:
            _INFO[sid] = (mark, info)
        info = dict(info)
    return info


def _session_info(sid, sdir, live):
    meta = read_json(sdir / "meta.json")
    started = meta.get("started") or sdir.stat().st_mtime
    user_title = _read_text(sdir / "title.txt")
    auto_title = _read_text(sdir / "title.auto.txt")
    lines, last_end = transcript_stats(sdir / "transcript.jsonl")
    kind = meta.get("kind") or "meeting"
    return {"id": sid, "title": user_title or auto_title or default_title(started, kind),
            "title_source": "user" if user_title else ("ai" if auto_title else "default"),
            "kind": kind, "started": started, "workspace": meta.get("workspace"),
            "duration": last_end if lines else 0, "lines": lines,
            "live": sid == live, "claude": claude_on(sid),
            # the machine its Claude Code runs on, and, until Claude has joined, why not yet.
            "where": session_place(sid, meta), "machine_name": machine_name(meta.get("where") or "local"),
            "claude_wait": claude_wait(sid) if sid == live else None,
            "runs": runs(sdir), "ended": ended_at(sdir),
            # A live session with Claude on has its conversation from the first second.
            "joined": joined(sdir) or (sid == live and claude_on(sid)),
            # the user's own Claude Code conversation it runs on, or a copy; paused
            # while that conversation is open in another Claude Code.
            "attached": attached_info(sdir, meta, sid == live),
            "mic": any(ch in ("Room", "Me") for ch in meta.get("speakers") or ["Room"]),
            # Where its lines came from: the table their "src" ids point into, kept after it ends.
            "sources_seen": _read_list(sdir / "sources.json"),
            # A question about what to record, one at a time, or None.
            "ask": (read_json(sdir / "capture.ask.json") or None) if sid == live else None,
            # What happened to the recording: the meeting's audio paused, and why.
            "events": read_jsonl(sdir / "events.jsonl"),
            # What the recorder hears, for the page to note a switch or a drop.
            "sources": with_screen_why([dict((k, v) for k, v in src.items() if k != "level")
                                        for src in (read_json(sdir / "live.json").get("sources") or [])])
            if sid == live else []}


def attached_info(sdir, meta, live):
    a = meta.get("attach")
    if not isinstance(a, dict) or not a.get("from"):
        return None
    if live and sdir.name not in ATTACH_WATCH:
        ATTACH_WATCH.add(sdir.name)
        threading.Thread(target=watch_attached, args=(sdir.name,), daemon=True).start()
    out = {"id": a["from"], "title": a.get("title") or "", "copy": bool(a.get("copy")) or (sdir / ATTACH_COPY).exists()}
    if live and (sdir / ATTACH_PAUSED).exists():
        out["paused"] = PAUSED_ELSEWHERE
    return out


def joined(sdir):
    """Has Claude had any of this session (transcript, or a question)? Then
    there is a conversation to continue in Claude Code."""
    try:
        if int(_read_text(sdir / "delivered") or 0) > 0:
            return True
    except ValueError:
        pass
    return (sdir / "chat.jsonl").exists()


def runs(sdir):
    """Recording runs, [{"start", "t", "end", "t_end"}]: wall-clock times and
    their place on the session clock. One per start or Resume."""
    out = []
    for r in read_jsonl(sdir / "runs.jsonl"):
        if "start" in r:
            out.append({"start": r["start"], "t": r.get("t", 0), "end": None, "t_end": None})
        elif "end" in r and out and out[-1]["end"] is None:
            out[-1].update(end=r["end"], t_end=r.get("t"))
    return out


def ended_at(sdir):
    """When the last recording ended (wall clock), for the header."""
    rs = runs(sdir)
    if rs and rs[-1]["end"]:
        return rs[-1]["end"]
    try:
        return (sdir / "transcript.jsonl").stat().st_mtime
    except OSError:
        return None


def claude_on(sid):
    """Is Claude on for this session? Off means nothing leaves this Mac."""
    return not (SESSIONS / sid / "claude.off").exists()


def _read_text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def list_sessions(live=_ASK):
    if not SESSIONS.is_dir():
        return []
    if live is _ASK:
        live = live_session()      # once, not once per session: each is a pgrep
    items = [session_info(p.name, live=live) for p in SESSIONS.iterdir()
             if p.is_dir() and (p / "meta.json").exists()]
    return sorted(items, key=lambda s: -s["started"])


# A session id as Standby makes it (standby.new_session_id): a label, the day and the time
# (to the second, or the minute for older ones), maybe -2, -3... Never "." or "..", never a
# slash: an id that resolved to SESSIONS itself, or above it, let /api/delete remove every
# session, or all of ~/.standby.
SESSION_ID = forget.SESSION_ID                 # one pattern, for the window and `standby purge`


def session_dir(sid):
    """Session sid's folder: only for an id of that shape, and only a real folder right
    inside SESSIONS (never a link, never anywhere else); else None."""
    return forget.folder(SESSIONS.parent, sid)


def valid_id(sid):
    return session_dir(sid) is not None


def speakers(sid):
    sdir = SESSIONS / sid
    return {"alias": read_json(sdir / "speakers.auto.json").get("alias", {}),
            "names": read_json(sdir / "speakers.names.json").get("names", {}),
            "known": read_json(sdir / "speakers.known.json").get("known", {})}


def append_chat(sid, role, text, steps=None, voice=False, more=None):
    """One chat row. more: other fields (a call's hand-off to Claude Code and what
    came back). Rows from before may carry "snaps", "snap" or "files"."""
    row = dict(more or {}, role=role, text=text, at=time.time())
    if steps:
        row["steps"] = steps
    if voice:
        row["voice"] = True
    with _lock:
        with open(SESSIONS / sid / "chat.jsonl", "a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def mark_chat_row(sid, row, **fields):
    """One row of the chat with fields added (a voice reply cut off: "cut", "heard"; or "unsaid"). Written
    whole, then swapped in."""
    path = SESSIONS / sid / "chat.jsonl"
    with _lock:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return False
        out, done = [], False
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            if not done and r.get("role") == row.get("role") and r.get("at") == row.get("at"):
                line, done = json.dumps(dict(r, **fields), ensure_ascii=False), True
            out.append(line)
        if done:
            tmp = path.with_suffix(".tmp")
            tmp.write_text("".join(l + "\n" for l in out))
            tmp.replace(path)
        return done


def _voice_heard(sid, qid, heard, cut, unsaid):
    """A call's reply, once its turn is over (VoiceSession._record): its chat row says what of it was heard,
    so the window shows only what was said: "cut" and "heard" when stopped partway, "unsaid" when none of
    it played. Said in full: the row as it is."""
    if not (cut or unsaid):
        return
    with _lock:
        rec = RECENT_VOICE.get(sid) or {}
    rows = rec.get("rows") or []
    if rec.get("qid") != qid or len(rows) < 2:
        return                                     # its row isn't written yet, or it's another turn's
    mark_chat_row(sid, rows[1], **({"unsaid": True} if unsaid else {"cut": True, "heard": heard}))


def drop_chat_row(sid, row, text=None):
    """Take one row back out of the chat (a voice question the user went on from:
    the next one has all of it), or with text, keep it saying that (what the voice had
    said of an answer before they went on). Written whole, then swapped in."""
    path = SESSIONS / sid / "chat.jsonl"
    with _lock:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return
        keep, gone = [], False
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if not gone and r.get("role") == row.get("role") and r.get("at") == row.get("at"):
                gone = True
                if text:
                    keep.append(json.dumps(dict(r, text=text)))
                continue
            keep.append(line)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(l + "\n" for l in keep))
        tmp.replace(path)


# ---------------------------------------------------------------- settings

# Apple's echo cancellation takes the Mac's default mic only (native.duplex).
TALK_OVER_MIC = "Needs the Mac's default microphone (Settings > Recording)"
DEFAULTS = {"push_lines": 10, "speakers": True, "capture": "auto",
            "remember_voices": True, "lite_transcript": "ask",
            "auto_stop_min": 15, "auto_accept": False}
LITE_PASSES = ("ask", "live")     # a meeting from Claude Code: its transcript with each prompt, or pushed as it goes
VOICES = ROOT / "voices.json"
# the speech model being downloaded, if one is: {"key", "state", "done", "total", "error"}.
SPEECH_DL = {"key": None, "state": None, "done": 0, "total": 0, "error": None}
SPEECH_MOVED = [None]        # a size that is no longer offered, moved on start: for Settings to say


def speech_migrate():
    """A stored speech model that is no longer offered, moved to this Mac's default
    (speechmodel.migrate): once, at start, before any session."""
    import speechmodel
    cfg = transport.load_config()
    moved = speechmodel.migrate(cfg)
    if moved is None:
        return None
    transport.save_config(cfg)
    was, now = speechmodel.MODELS[speechmodel.chosen({"speech_model": moved[0], "english_only": True})], \
        speechmodel.MODELS[speechmodel.chosen(cfg)]
    SPEECH_MOVED[0] = ("%s wrote no capitals or full stops, so it is no longer offered: %s now."
                       % (was.words, now.words))
    log("speech: %s is no longer offered (no punctuation): %s now" % (was.words, now.words))
    return SPEECH_MOVED[0]


def speech_status(cfg=None):
    """Settings > Speech: the choice, what it means, and whether it's here yet."""
    import speechmodel
    cfg = transport.load_config() if cfg is None else cfg
    size, english = speechmodel.choice(cfg)
    key = speechmodel.chosen(cfg)
    with _lock:
        dl = dict(SPEECH_DL) if SPEECH_DL["key"] == key else None
    return {"size": size, "english_only": english, "default": speechmodel.default_size(),
            "picked": cfg.get("speech_model") in speechmodel.SIZES,   # the user's, not this Mac's default
            "memory": speechmodel.memory_label(),
            "model": speechmodel.MODELS[key].words, "bytes": speechmodel.MODELS[key].bytes,
            "ready": speechmodel.local(key) is not None, "download": dl,
            "sizes": list(speechmodel.OFFERED),          # Large is not offered (no punctuation)
            "punctuates": speechmodel.MODELS[key].punctuates,
            "moved": SPEECH_MOVED[0]}                    # a stored size that is gone, said once


def ensure_speech_model():
    """The chosen model downloaded in the background if it isn't here (from Hugging
    Face, pinned): asked for by a new choice, and by each session start, which uses
    the best one here meanwhile. One download at a time; never in a test."""
    import speechmodel
    key = speechmodel.chosen(transport.load_config())
    if speechmodel.local(key) is not None or os.environ.get("STANDBY_NO_DEVICES"):
        return False
    with _lock:
        if SPEECH_DL["state"] == "downloading":
            return False
        SPEECH_DL.update(key=key, state="downloading", done=0, total=speechmodel.MODELS[key].bytes, error=None)

    def tell(st):
        with _lock:
            SPEECH_DL.update(st)

    def run():
        log("speech: downloading %s (%.1f GB)" % (speechmodel.MODELS[key].words, speechmodel.MODELS[key].bytes / 1e9))
        try:
            speechmodel.fetch(key, tell)
            log("speech: %s is ready, from the next session" % speechmodel.MODELS[key].words)
        except Exception as exc:
            with _lock:
                SPEECH_DL.update(state="error", error=str(exc)[:200])
            log("speech: download failed: %r" % exc)
    threading.Thread(target=run, name="speech-model", daemon=True).start()
    return True


AUTO_STOP_MAX = 600              # minutes: the longest auto_stop that can be set


def _auto_stop_min(cfg):
    """Minutes with nothing said after which a meeting stops itself (0: never)."""
    try:
        return max(0, min(AUTO_STOP_MAX, int(cfg.get("auto_stop_min", DEFAULTS["auto_stop_min"]))))
    except (TypeError, ValueError):
        return DEFAULTS["auto_stop_min"]


def get_settings():
    cfg = transport.load_config()
    a = transport.agent_settings(cfg)
    return {"model": a["model"], "voice_model": a["voice_model"],
            "push_lines": int(cfg.get("push_lines", DEFAULTS["push_lines"])),
            "speakers": bool(cfg.get("speakers", DEFAULTS["speakers"])),
            "remember_voices": bool(cfg.get("remember_voices", DEFAULTS["remember_voices"])),
            # Claude starts calls and listening without the incoming panel (the user's Always accept)
            "auto_accept": bool(cfg.get("auto_accept", DEFAULTS["auto_accept"])),
            "lite_transcript": cfg.get("lite_transcript") if cfg.get("lite_transcript") in LITE_PASSES else DEFAULTS["lite_transcript"],
            "auto_stop_min": _auto_stop_min(cfg),
            "voices": sorted(read_json(VOICES).get("voices", {})),
            # seconds of the user's own voice learnt (talks), or None: listed on its own
            "my_voice": (read_json(VOICES).get("me") or {}).get("seconds"),
            "capture": cfg.get("mode", DEFAULTS["capture"]),
            "mic_device": cfg.get("mic_device") if isinstance(cfg.get("mic_device"), str) else "",
            # Settings > Voice, "Let me talk over Claude": the mic through echo cancellation, so
            # talking over the voice stops it (voice_mode.duplex, read at each voice mode). Off,
            # the recorder still plays the voice, and the mic is held while it speaks.
            "talk_over": bool((cfg.get("voice_mode") or {}).get("duplex", False)),
            "talk_over_note": talk_over_note(cfg),
            "speech": speech_status(cfg)}


def talk_over_note(cfg):
    """Settings' hint under "Let me talk over Claude": echo cancelling takes the
    Mac's default microphone only, so it is said when another one is chosen. Not
    when the one chosen is the default anyway; said when that cannot be told."""
    chosen = cfg.get("mic_device") if isinstance(cfg.get("mic_device"), str) else ""
    if not chosen:
        return ""
    default = default_mic()
    return "" if default is not None and chosen == default else TALK_OVER_MIC


DEFAULT_MIC_FOR = 10.0       # seconds Core Audio's answer is kept: Settings asks on every open
_DEFAULT_MIC = {"at": None, "name": None}


def default_mic():
    """The name of the Mac's default microphone now (Core Audio, as the echo
    cancelling uses it: native.macos.duplex._default_input), kept a few seconds; None when
    it cannot be told (no Core Audio, a test)."""
    now = time.time()
    if _DEFAULT_MIC["at"] is not None and now - _DEFAULT_MIC["at"] < DEFAULT_MIC_FOR:
        return _DEFAULT_MIC["name"]
    name = None
    ca = _core_audio()
    if ca is not None:
        try:
            d = ca.default_input()
            name = d.name if d is not None else None
        except Exception:
            name = None
    _DEFAULT_MIC.update(at=now, name=name)
    return name


def models_info():
    """The models this Claude Code runs another in place of (the agent finds out,
    agent/models.json where it runs): Settings marks them not available, and
    the agent asks for Sonnet instead. A saved one is kept, should that change."""
    raw = transport.for_config().read("agent/models.json")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    out = {"unavailable": sorted(m for m, v in data.items() if isinstance(v, dict) and v.get("blocked"))}
    note = model_switch_note()
    if note:
        out["switch"] = note
    return out


MODEL_NAMES_SHOWN = {"haiku": "Haiku", "sonnet": "Sonnet", "opus": "Opus"}


def model_switch_note():
    """Settings' line on the voice's model in the call going on: switched at its last turn, or not,
    and why (the agent's switch.json, for this call only): {text, wanted}, or None."""
    live = live_session()
    if not live:
        return None
    try:
        sw = json.loads(transport.for_config().read("agent/switch.json") or "{}")
    except ValueError:
        return None
    if not isinstance(sw, dict) or sw.get("session") != live:
        return None
    name = lambda m: MODEL_NAMES_SHOWN.get(m, m or "another model")
    if sw.get("ok"):
        text = "This call switched to %s." % name(sw.get("wanted"))
    else:
        text = "This call couldn't switch to %s, so it stays on %s. Claude Code said: %s" % (
            name(sw.get("wanted")), name(sw.get("was")), sw.get("error") or "no reason")
    return {"text": text, "wanted": sw.get("wanted")}


USAGE_RANGES = ("3h", "today", "7d", "30d")
BUCKETS = {"3h": "10 minutes", "today": "hour", "7d": "day", "30d": "day"}


def usage_edges(rng, now=None):
    """The buckets' edges in the Mac's local time, the last one at `now`: nothing
    is counted into the future, and the bucket in progress is partial (the user
    asked). 3h: ten-minute buckets back from now. today: this day's hours so far.
    7d and 30d: local midnights, today's partial. mktime counts across months,
    month ends and DST."""
    now = int(now or time.time())
    d = time.localtime(now)
    if rng == "3h":
        start = now - 3 * 3600
        start -= start % 600                            # on a ten-minute mark, so the buckets are tidy
        edges = list(range(start, now, 600))
    elif rng == "today":
        edges = [int(time.mktime((d.tm_year, d.tm_mon, d.tm_mday, h, 0, 0, 0, 0, -1)))
                 for h in range(d.tm_hour + 1)]
    else:
        days = 7 if rng == "7d" else 30
        edges = [int(time.mktime((d.tm_year, d.tm_mon, d.tm_mday - days + 1 + i, 0, 0, 0, 0, 0, -1)))
                 for i in range(days)]
    edges = sorted(set(e for e in edges if e <= now))
    # The last edge is a second past now: a bucket is counted as [from, to), so ending
    # it exactly at now would leave out a call made in this very second.
    return BUCKETS[rng], edges + [now + 1]


def usage_info(rng):
    """Settings > Usage: the agent's usage.jsonl, summed here."""
    if rng not in USAGE_RANGES:
        return {"error": "The range is 3h, today, 7d or 30d."}
    bucket, edges = usage_edges(rng)
    sys.path.insert(0, str(HERE.parent / "plugin" / "bin"))
    import sbusage
    try:
        out = sbusage.summary(edges, str(ROOT / "agent" / "usage.jsonl"))
    except (OSError, ValueError):
        return {"error": "Couldn't read usage on this Mac."}
    out.update(range=rng, bucket=bucket, partial=True)      # the last bucket ends now, not at its end
    return out


def set_settings(b):
    cfg = transport.load_config()
    a = transport.agent_settings(cfg)
    a.pop("host", None)                       # an SSH host an older Standby saved: Claude Code runs on this Mac
    models = (a.get("model"), a.get("voice_model"))
    if b.get("model") in ("sonnet", "opus", "haiku"):
        a["model"] = b["model"]
    if b.get("voice_model") in ("sonnet", "opus", "haiku"):
        a["voice_model"] = b["voice_model"]
    cfg["agent"] = a
    for legacy in ("host", "remote_dir", "workspace", "claude_on_start"):
        cfg.pop(legacy, None)
    if "push_lines" in b:
        try:
            cfg["push_lines"] = max(1, min(200, int(b["push_lines"])))
        except (TypeError, ValueError):
            return {"error": "AI update must be a number of sentences."}
    if "speakers" in b:
        cfg["speakers"] = bool(b["speakers"])
    if "remember_voices" in b:
        cfg["remember_voices"] = bool(b["remember_voices"])
    if "auto_accept" in b:
        cfg["auto_accept"] = bool(b["auto_accept"])
    if b.get("lite_transcript") in LITE_PASSES:
        cfg["lite_transcript"] = b["lite_transcript"]
    if "auto_stop_min" in b:
        try:
            cfg["auto_stop_min"] = max(0, min(AUTO_STOP_MAX, int(b["auto_stop_min"])))
        except (TypeError, ValueError):
            return {"error": "auto_stop is a number of minutes, 0 (never) to %d." % AUTO_STOP_MAX}
    if "talk_over" in b:
        cfg.setdefault("voice_mode", {})["duplex"] = bool(b["talk_over"])
    import speechmodel
    speech_was = speechmodel.chosen(cfg)
    if b.get("speech_model") in speechmodel.OFFERED:     # only what Settings offers, and only if picked
        cfg["speech_model"] = b["speech_model"]
    if "english_only" in b:
        cfg["english_only"] = bool(b["english_only"])
    if b.get("capture") in ("auto", "mic", "meeting"):
        cfg["mode"] = b["capture"]
    cfg["mic_automatic"] = cfg["capture_automatic"] = True     # what is picked here from now on is the user's
    if "mic_device" in b:
        if b["mic_device"]:
            cfg["mic_device"] = b["mic_device"]
        else:
            cfg.pop("mic_device", None)       # system default microphone
    transport.save_config(cfg)
    if speechmodel.chosen(cfg) != speech_was:
        log("settings: speech model %s" % speechmodel.MODELS[speechmodel.chosen(cfg)].words)
        ensure_speech_model()
    if a.get("model") != models[0]:
        threading.Thread(target=_restart_agent, daemon=True).start()
    elif a.get("voice_model") != models[1]:            # the agent reads it at each voice turn: a call goes on
        log("settings: the voice's model is %s, from its next turn" % a["voice_model"])
    log("settings saved")
    return {"ok": True, "settings": get_settings()}


# Settings by name, for Claude Code (/standby settings, /standby set, the settings tool): what
# each one is, its choices, and how it's saved: through set_settings or voices_action, as the
# Settings window saves it, so there's one place that says what a value may be.
ON_OFF = ("on", "off")
MODEL_NAMES = ("haiku", "sonnet", "opus")


def _named_settings():
    """[(name, what it is, value now, choices, the key it's saved under)]."""
    import speechmodel
    st = get_settings()
    onoff = lambda v: "on" if v else "off"
    vs = voices_info()
    voice_now = "automatic" if not vs.get("chosen") else (vs.get("current") or {}).get("name") or vs["chosen"]
    return [
        ("transcript", "how a meeting's transcript reaches Claude Code: with your prompts, or as it goes",
         st["lite_transcript"], list(LITE_PASSES), "lite_transcript"),
        ("capture", "what Standby records", st["capture"], ["auto", "mic", "meeting"], "capture"),
        ("mic", "the microphone", st["mic_device"] or "default", ["default"] + list_mics(), "mic_device"),
        ("speech_model", "the on-device speech-to-text model", st["speech"]["size"], list(speechmodel.OFFERED), "speech_model"),
        ("english_only", "speech-to-text for English only", onoff(st["speech"]["english_only"]), list(ON_OFF), "english_only"),
        ("voice", "the voice Standby speaks in", voice_now,
         ["automatic"] + [v["name"] for v in vs.get("standby", []) + vs.get("voices", [])], "voice"),
        ("voice_model", "the model the voice talks with", st["voice_model"], list(MODEL_NAMES), "voice_model"),
        ("model", "the model of Standby's meeting agent", st["model"], list(MODEL_NAMES), "model"),
        ("talk_over", "talk over the voice to interrupt it", onoff(st["talk_over"]), list(ON_OFF), "talk_over"),
        ("speakers", "tell the speakers apart", onoff(st["speakers"]), list(ON_OFF), "speakers"),
        ("remember_voices", "remember voices, yours and the ones you name", onoff(st["remember_voices"]), list(ON_OFF), "remember_voices"),
        ("auto_accept", "Claude starts calls and listening without asking", onoff(st["auto_accept"]), list(ON_OFF), "auto_accept"),
        ("ai_update", "sentences between the meeting agent's updates", str(st["push_lines"]), ["1 to 200"], "push_lines"),
        ("auto_stop", "minutes with nothing said before a meeting stops itself (0: never)", str(st["auto_stop_min"]),
         ["0 to %d" % AUTO_STOP_MAX], "auto_stop_min"),
    ]


def settings_named():
    """GET /api/settings/named: the settings that matter from Claude Code, with values and choices."""
    return {"settings": [{"name": n, "about": a, "value": v, "choices": c} for n, a, v, c, _k in _named_settings()]}


def set_named(name, value):
    """POST /api/settings {name, value}: one setting by its name, its value checked against its choices."""
    table = {n: (c, k) for n, _a, _v, c, k in _named_settings()}
    name, value = str(name or "").strip().lower().replace("-", "_"), " ".join(str(value or "").split())
    if name not in table:
        return {"error": "There's no setting called %s. The settings: %s." % (name or "that", ", ".join(table))}
    choices, key = table[name]
    if key == "push_lines":
        if not value.isdigit() or not 1 <= int(value) <= 200:
            return {"error": "ai_update is a number of sentences, 1 to 200."}
        picked = int(value)
    elif key == "auto_stop_min":
        if not value.isdigit() or int(value) > AUTO_STOP_MAX:
            return {"error": "auto_stop is a number of minutes, 0 (never) to %d." % AUTO_STOP_MAX}
        picked = int(value)
    else:
        picked = next((c for c in choices if c.lower() == value.lower()), None)
        if picked is None:
            return {"error": "%s can be %s." % (name, ", ".join(choices[:12]) + (", ..." if len(choices) > 12 else ""))}
    if key == "voice":
        vs = voices_info()
        vid = None if picked == "automatic" else next(v["id"] for v in vs.get("standby", []) + vs.get("voices", [])
                                                     if v["name"] == picked)
        r = voices_action({"id": vid})
    else:
        r = set_settings({key: (picked == "on") if choices == list(ON_OFF) else ("" if key == "mic_device" and picked == "default" else picked)})
    if r.get("error"):
        return {"error": r["error"]}
    return {"ok": True, "name": name, "value": next(v for n, _a, v, _c, _k in _named_settings() if n == name)}


def _core_audio():
    """The Mac's devices now (devices.py): this app runs for days, and
    PortAudio's list is the one from its start. None in a test."""
    if os.environ.get("STANDBY_NO_DEVICES"):
        return None
    try:
        import devices
        return devices.real_devices()
    except Exception:
        return None


def list_mics():
    ca = _core_audio()
    if ca is not None:
        try:
            return [d.name for d in ca.inputs()]
        except Exception as exc:
            log("devices: %r" % exc)
    try:
        import sounddevice as sd
        return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception as exc:
        log("devices: %r" % exc)
        return []


def mic_default():
    """The Mac's mic now, for "Automatic (follows your Mac: ...)"."""
    ca = _core_audio()
    try:
        d = ca.default_input() if ca is not None else None
    except Exception:
        d = None
    return d.name if d is not None and not d.virtual else ""


SPEAKER_MODEL = ROOT / "models" / "nemo_en_titanet_small.onnx"     # where install.sh and the recorder have it


def agent_label():
    try:
        t = transport.for_config()
        return {"mode": t.mode, "label": t.label}
    except ValueError as exc:
        return {"mode": "unset", "label": str(exc)}


# ---------------------------------------------------------------- workspaces, agents and their timeline

_MESSENGER = []


def messenger():
    """The one Messenger (mac/messenger.py): workspaces, agents and the timeline. Made on
    first use, and hooked to calls and meetings (MSG) then."""
    if not _MESSENGER:
        import messenger as mm
        m = mm.Messenger(ROOT, ui=sys.modules[__name__])
        m.attach()
        import agents                     # start, send, status: the agents layer
        agents.install(sys.modules[__name__], m)
        _MESSENGER.append(m)
    return _MESSENGER[0]


def machine_keys():
    return ["local"]


MODEL_ID = re.compile(r"^(haiku|sonnet|opus|claude-[a-z0-9][a-z0-9.\-]{2,60})$")


def valid_model(model):
    """A Claude model Standby may pass to `--model`: an alias, or a full id."""
    return bool(isinstance(model, str) and MODEL_ID.match(model))


def agent_model(agent_id):
    """The Claude model an agent's own turns run on: its choice, else the one it last ran, else
    None (the default). For a call, a meeting and a message Standby runs itself."""
    try:
        model = messenger().agent_choice(agent_id)["model"]
    except Exception:
        return None
    return model if valid_model(model) else None


def valid_voice(voice):
    """A voice id Standby may hand the recorder: a short line of text (it goes as one argument, never
    through a shell)."""
    return bool(isinstance(voice, str) and 0 < len(voice) <= 80 and voice.isprintable())


def agent_voice(agent_id):
    """The voice an agent speaks in during a call: its choice, else None (Settings' voice)."""
    try:
        voice = messenger().agent_choice(agent_id)["voice"]
    except Exception:
        return None
    return voice if valid_voice(voice) else None


def default_voice():
    """The voice Settings has chosen ("" for automatic): what an agent with no choice of its own speaks in."""
    return (transport.load_config().get("voice_mode") or {}).get("voice") or ""


def past_sessions(m):
    """GET /api/msg/past: every meeting and call, newest first, as their cards, each with the names of
    the people a meeting heard (named, or recognized from before), and its topic: the title the user gave it,
    else the short one Claude made (title.auto.txt), else "" (the list then says plainly what it was)."""
    out = []
    for c in m.cards():
        if c.get("kind") not in ("meeting", "call"):
            continue
        sdir = SESSIONS / c["session"]
        c["topic"] = (_read_text(sdir / "title.txt") or _read_text(sdir / "title.auto.txt")).strip()[:80]
        if c["kind"] == "meeting":
            sp, names = speakers(c["session"]), []
            for n in list(sp["names"].values()) + list(sp["known"].values()):
                if isinstance(n, str) and n.strip() and n.strip() not in names:
                    names.append(n.strip())
            c["names"] = names
        out.append(c)
    return sorted(out, key=lambda c: -(c.get("at") or 0))


def messenger_get(path, q):
    """GET /api/msg/<...>: workspaces (read only) and the past calls and meetings. (The event stream is the handler's own.)"""
    m = messenger()
    if path == "/api/msg/workspaces":
        return m.workspaces()
    if path == "/api/msg/past":
        return {"items": past_sessions(m)}
    return {"error": "unknown request"}


MSG_POST = {}      # path -> f(body): routes the other parts add


DELETE_LIVE = "That one is still live: end it first"
DELETE_GONE = "No such meeting or call"


def delete_past(b):
    """POST /api/msg/delete {session}: a past meeting or call, gone for good: its folder (the
    recording, transcript, chat, card and agent.json), and what it left in live/ and agent/.
    A call that went on in more than one session goes whole: they are one card. Never one
    that's live, paused or starting, never anything but a session's own folder. Every open
    window is told ("deleted", {session}), and Claude Code's band too, for a card it holds."""
    sid = b.get("session") if isinstance(b, dict) else None
    if session_dir(sid) is None:
        return {"error": DELETE_GONE}
    first = call_first(sid) if session_dir(call_first(sid)) is not None else sid
    parts = [p for p in dict.fromkeys([first] + list(call_sessions(first) or []) + [sid]) if session_dir(p) is not None]
    live = live_session()
    with _lock:
        starting = STARTING.get("state") == "starting" and STARTING.get("resume") in parts
    for p in parts:
        if starting or forget.why_not(SESSIONS.parent, p, live=live or ""):
            return {"error": DELETE_LIVE}
    ccs = set()
    for p in parts:
        sdir = session_dir(p)                     # checked again, right before it goes
        if sdir is None:
            continue
        data = read_json(sdir / addmac.AGENT_FILE)
        if str(data.get("agent") or "").startswith("lite:"):
            ccs.add(str(data["agent"])[len("lite:"):])
        forget.forget(SESSIONS.parent, p, log=log)     # the one routine `standby purge` uses too
        with _INFO_LOCK:
            _INFO.pop(p, None)
            for cache in (_LINES, _ROWS):
                for key in [k for k in cache if k.startswith(str(sdir) + os.sep)]:
                    cache.pop(key, None)
    log("deleted %s" % ", ".join(parts))
    try:
        messenger().hub.publish("deleted", {"session": first})
    except Exception as exc:
        log("delete: the windows weren't told (%r)" % exc)
    for cc in ccs:
        try:
            lite().publish(cc, {"deleted": True, "session": first})
        except Exception as exc:
            log("delete: Claude Code's band wasn't told (%r)" % exc)
    return {"ok": True, "session": first}


MSG_POST["/api/msg/delete"] = delete_past


# ---------------------------------------------------------------- dictation in any app

# Opens System Settings panes. Tests put a stand-in here: the real one opens windows.
OPEN = shlex.split(os.environ.get("STANDBY_OPEN") or "open")
# Tests put a stand-in here: the real one starts and stops the user's login item.
DICTATION_CLI = (shlex.split(os.environ["STANDBY_DICTATION_CLI"]) if os.environ.get("STANDBY_DICTATION_CLI")
                 else [sys.executable, str(HERE / "dictation.py")])


def dictation_app():
    """Standby Dictation as Settings shows it: on (at login or running), the
    shortcut, what is missing, and whether it is moving to new code. From what
    the running app last wrote, and the lock only it holds: the same truth as
    `standby dictation`."""
    import dictation as dct
    pid = dct.running_pid()
    st = read_json(dct.STATUS) if pid else {}
    s = dct.settings()
    mine = bool(pid) and st.get("pid") == pid
    code = st.get("code") or {}
    out = {"on": dct.at_login() or bool(pid), "running": bool(pid),
           # The shortcut in use, every preset (and a recorded one), and hold to talk.
           "shortcut": {"id": s["shortcut"], "label": dct.shortcut_label(s), "kind": dct.shortcut_kind(s)},
           "shortcuts": [{"id": k, "label": label, "kind": "key" if k in dct.KEY_SHORTCUTS else "double"}
                         for k, (label, _keys) in dct.SHORTCUTS.items()]
                        + ([{"id": "custom", "label": dct.shortcut_label(dict(s, shortcut="custom")),
                             "kind": s["custom"]["kind"]}] if dct.valid_custom(s.get("custom")) else []),
           "hold": bool(s["hold"]),
           # Running, but from before it wrote this much: it never updates itself.
           "old": bool(pid) and not (mine and st.get("started")),
           "updating": bool(mine and code.get("pending")),
           # New code that does not start, or why it stopped trying to start at all.
           "problem": (mine and code.get("problem") or None) if pid else dct.gave_up(),
           "missing": []}
    import vocabulary
    words = vocabulary.counts(dct.WORDS)          # Word fixes: numbers only, never the words
    if words is not None:
        out["words"] = words
    hk = st.get("hotkey_state") if mine else None
    if hk and not hk.get("ok") and hk.get("label") == out["shortcut"]["label"]:
        out["shortcut"]["problem"] = hk.get("why")          # e.g. another app already uses those keys
    if mine and isinstance(st.get("permissions"), dict):
        out["missing"] = [{k: r[k] for k in ("key", "name", "why", "action", "button")}
                          for r in dct.setup_rows(st["permissions"], s["shortcut"])
                          if not r["ok"] and not r.get("optional")]      # the optional one: dictation's own window
        if s["shortcut"] in dct.KEY_SHORTCUTS and st.get("remapped") is False:
            out["missing"].append({"key": "remap", "name": "F5 key", "why": "isn't remapped yet",
                                   "action": "setup", "button": "Fix"})
    return out


DICTATION_CONFIRM = 2.0     # how long a recorded shortcut waits for the app to register it


def dictation_shortcut(b, wait=DICTATION_CONFIRM):
    """POST /api/dictation-app {"shortcut": <preset id> | {"code", "key", "mods"} |
    {"double": "AltRight"}} or {"hold": bool}: judged, saved, and the running
    app told to reload. A recorded combination is a hotkey, and only registering it
    tells whether another app already holds those keys (⌥ Space is Alfred's and
    Raycast's): so this waits up to `wait` s for the app's answer, and on a refusal
    puts the previous shortcut back. Answers {"ok", "shortcut", "status"} or {"error"}."""
    import dictation as dct
    s = dct.settings()
    if "hold" in b and "shortcut" not in b:
        dct.save_setting("hold", bool(b["hold"]))
        _tell_dictation(dct)
        log("dictation: hold to talk %s" % ("on" if b["hold"] else "off"))
        return {"ok": True, "status": dictation_app()}
    spec = b.get("shortcut")
    before = (s["shortcut"], s.get("custom"))
    if isinstance(spec, str):
        if spec not in dct.SHORTCUTS and not (spec == "custom" and dct.valid_custom(s.get("custom"))):
            return {"error": "No such shortcut"}
        dct.save_setting("shortcut", spec)
        custom = s.get("custom") if spec == "custom" else None
    else:
        judged = dct.judge_shortcut(spec)
        if not judged["ok"]:
            log("dictation: a recorded shortcut refused")
            return {"error": judged["why"]}
        custom = judged["custom"]
        dct.save_setting("custom", custom)
        dct.save_setting("shortcut", "custom")
    now = dct.settings()
    asked_at = time.time()
    running = _tell_dictation(dct)
    log("dictation: shortcut now %s" % dct.shortcut_ascii(now))
    if running and custom and custom.get("kind") == "combo":
        label = dct.shortcut_label(now)
        end = time.time() + wait
        state = None
        while time.time() < end:
            state = (read_json(dct.STATUS) or {}).get("hotkey_state")
            if state and state.get("label") == label and state.get("at", 0) > asked_at:   # this answer, not an old one
                break
            state = None
            time.sleep(0.1)
        if state is not None and not state.get("ok"):
            dct.save_setting("shortcut", before[0])
            dct.save_setting("custom", before[1])
            _tell_dictation(dct)
            log("dictation: that shortcut could not be registered; the previous one is back")
            return {"error": "%s: %s" % (custom["label"], state.get("why") or "macOS refused it")}
    return {"ok": True, "shortcut": {"id": now["shortcut"], "label": dct.shortcut_label(now),
                                     "kind": dct.shortcut_kind(now)}, "status": dictation_app()}


def _tell_dictation(dct):
    """The running app reloads its settings; True if it is running (if not, it
    reads them when it starts)."""
    if not dct.running_pid():
        return False
    dct.send_command("reload")
    return True


def dictation_action(b):
    """on / off: the login item and the app, as `standby dictation on|off`.
    setup: the running app brings up its setup window (and asks for the mic);
    not running, it starts, and shows the window itself if anything is missing.
    open: one permission's pane in System Settings."""
    import dictation as dct
    if "shortcut" in b or "hold" in b:
        return dictation_shortcut(b)
    action = b.get("action")
    if action == "setup" and dct.running_pid():
        dct.send_command("setup")
        return {"ok": True, "status": dictation_app()}
    if action == "edit_words":                    # Settings > Dictation > Word fixes
        if dct.running_pid():
            dct.send_command("edit_words")        # the running app opens its Edit Word Fixes…
            return {"ok": True}
        try:                                      # not running: the same file, opened from here
            dct.open_words(opener=OPEN + ["-t"])
        except OSError as exc:
            return {"error": "Couldn't open the word fixes (%s)" % exc.__class__.__name__}
        return {"ok": True}
    if action == "open":
        urls = {r["key"]: r["url"] for r in dct.setup_rows({})}
        if b.get("key") not in urls:
            return {"error": "unknown permission"}
        subprocess.run(OPEN + [urls[b["key"]]], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        return {"ok": True}
    if action not in ("on", "off", "setup"):
        return {"error": "unknown dictation action"}
    try:
        r = subprocess.run(DICTATION_CLI + ["off" if action == "off" else "on"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": "Could not run Standby Dictation (%s)." % exc.__class__.__name__}
    log("dictation %s: rc=%d" % (action, r.returncode))
    if r.returncode != 0:
        lines = (r.stderr or r.stdout).strip().splitlines()
        return {"error": lines[-1] if lines else "Standby Dictation did not start.", "status": dictation_app()}
    return {"ok": True, "status": dictation_app()}


# ---------------------------------------------------------------- actions

def slug(title):
    s = re.sub(r"[^A-Za-z0-9]+", "-", title.strip()).strip("-").lower()
    return (s or "meeting")[:40]


def wait_for_shutdown(seconds=15):
    """A stopped recorder can take a few seconds to exit. Starting inside that
    window used to be refused, which looked like 'cannot start after Stop'."""
    end = time.time() + seconds
    while time.time() < end:
        if live_session():
            return False
        if not recorders():
            return True
        time.sleep(0.3)
    return not recorders()


def where_claude_runs():
    return "this Mac"


def _agent_json(args, timeout=20, machine=None):
    """An agent command's JSON answer, from wherever the agent runs (machine: one of
    the user's, else the default); None if it gives none (an agent from before the
    command, or the host unreachable).

    Nothing it asks is worth a failed page: these answers only fill in the folder
    picker and the session's folder, so anything at all going wrong here is None,
    the transport included (test_suggest caught one that has no agent_cmd)."""
    try:
        t = transport.for_config()
        cmd = t.agent_cmd(args)
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    return out if isinstance(out, dict) else None


_FOLDER = {}               # sid -> {"found", "at", "retry", "busy"}, under _FOLDER_LOCK
_FOLDER_LOCK = threading.Lock()
_UNKNOWN = object()
FOLDER_TTL = 10.0          # how long a folder found is trusted before it is asked again
FOLDER_BACKOFF = 60.0      # after a lookup that failed (a slow or unreachable host)


def session_folder(sid, meta_workspace, joined, ttl=FOLDER_TTL, backoff=FOLDER_BACKOFF):
    """The folder to name for this session: the one its conversation actually lives
    in, which a /cd in Claude Code moves (the agent's `where`), else what the
    session recorded. None for Standby's own neutral folder, which is no project.

    Answers at once, from what is known: the page reloads the open session every
    1.5 s, and over ssh a lookup is a round trip that a slow host can make last
    seconds, which would stop the transcript and the chat from updating. So the
    lookup runs in the background, one at a time per session, at most every
    `ttl` seconds, and after one that failed not again for `backoff` seconds."""
    if not joined:
        return meta_workspace or None           # nothing said yet: nothing has a folder
    now = time.time()
    with _FOLDER_LOCK:
        st = _FOLDER.setdefault(sid, {"found": _UNKNOWN, "at": 0.0, "retry": 0.0, "busy": False})
        found = st["found"]
        due = not st["busy"] and now >= st["retry"] and now - st["at"] > ttl
        if due:
            st["busy"] = True
        for old in [k for k, v in _FOLDER.items() if k != sid and now - max(v["at"], v["retry"]) > 600]:
            _FOLDER.pop(old, None)
    if due:
        threading.Thread(target=_look_up_folder, args=(sid, backoff), daemon=True).start()
    return (meta_workspace or None) if found is _UNKNOWN else found


def _look_up_folder(sid, backoff):
    got = _agent_json(["where", "--session", sid], timeout=10, machine="local")
    now = time.time()
    with _FOLDER_LOCK:
        st = _FOLDER.setdefault(sid, {"found": _UNKNOWN, "at": 0.0, "retry": 0.0, "busy": False})
        st["busy"] = False
        if got:
            st.update(found=None if got.get("own") else (got.get("path") or None), at=now, retry=0.0)
        else:                                   # an older agent, or a host that didn't answer
            st["retry"] = now + backoff


# ---------------------------------------------------------------- attaching
#
# An agent's meeting runs on its Claude Code conversation (the original, transcript and
# all, 2026-10-07), or on a copy of it when the original is open in Claude Code. The
# agent guards it: it never writes while another Claude Code has it open, and pauses
# until it closes.

PAUSED_ELSEWHERE = "Paused: this conversation is open in Claude Code. The transcript catches up when it's closed."
ATTACH_PAUSED = "attach-paused"   # in a session dir: its conversation is open elsewhere, so Standby waits
ATTACH_COPY = "attach.copy"       # in a session dir: switched to a copy while it waited, as the recorder reads


def watch_attached(sid, every=3.0, waiting=1.0):
    """While an attached session is live: is its conversation open elsewhere (the
    agent then holds it)? The session says so while it is, once in the log; while it
    waits, it looks every `waiting` s, so it connects the moment that window closes."""
    mark = SESSIONS / sid / ATTACH_PAUSED
    while live_session() == sid:
        try:
            r = subprocess.run(transport.for_config().agent_cmd(["held", "--session", sid]),
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            elsewhere = r.returncode == 0 and "elsewhere" in r.stdout
            where = r.stdout.split("elsewhere", 1)[-1].strip() if elsewhere else ""
        except (OSError, ValueError, subprocess.SubprocessError):
            elsewhere = None
        if elsewhere and not mark.exists():
            mark.write_text(json.dumps({"at": time.time(), "where": where or "another app"}))
            log("attached: %s is open in another Claude Code; the meeting waits" % sid)
        elif elsewhere is False and mark.exists():
            mark.unlink()
            log("attached: %s is back; the transcript catches up" % sid)
        time.sleep(waiting if mark.exists() else every)
    try:
        mark.unlink()
    except OSError:
        pass
    ATTACH_WATCH.discard(sid)


ATTACH_WATCH = set()


# ---------------------------------------------------------------- starting a session

def machine_name(key):
    return "this Mac"


# Where a probe's kind stops the line: the step it fails, the ones before it passed.


# Starting from the window. The recording starts at once; Claude joins when the
# checks pass (as the user chose): until then the session has claude.wait, the recorder
# sends nothing, and a question waits a moment for it. A check that fails says why on
# the session's own line, with the fix, and the switch for Claude tries again.

                                # well under a second unless the Mac or the host is very busy)


def claude_wait(sid):
    """While Claude waits to join this session, why, else None: {why, name, machine,
    where}. why: "open_elsewhere" (its conversation is open in another Claude Code,
    where: "VS Code", ...: it joins when that closes, or on a copy)."""
    sdir = SESSIONS / sid
    out = {"machine": "local", "name": machine_name("local")}
    if (sdir / ATTACH_PAUSED).exists():
        return dict(out, why="open_elsewhere", where=read_json(sdir / ATTACH_PAUSED).get("where") or "another app")
    return None


def session_place(sid, meta):
    """Where a session lives, for its ⋯ menu: {machine, name, folder,
    session_title, copy}. session_title: the Claude session it runs on, else None (a
    new one)."""
    machine = meta.get("where") or "local"
    a = meta.get("attach") if isinstance(meta.get("attach"), dict) else None
    return {"machine": machine, "name": machine_name(machine), "folder": meta.get("workspace") or "",
            "session_title": (a.get("title") or None) if a else None,
            "copy": bool(a and (a.get("copy") or (SESSIONS / sid / ATTACH_COPY).exists()))}


def start_session(resume=None, kind="meeting", workspace=None,
                  where=None, no_claude=False, capture=None,
                  agent=None, model=None, voice_id=None):
    """Start (or resume) recording in the background and return at once; the
    page shows 'Starting…' and switches to the session when it is live. A
    meeting never waits behind a talk: starting one ends a live talk.
    workspace: a new session's own folder, instead of the default.
    where: the machine its Claude Code runs on, else the default.
    no_claude: started without Claude (its machine didn't answer).
    capture: this session's recording mode, else Settings' (Add your Mac's switches);
    agent: an agent's meeting, which its agent always hears (Claude on, whatever Settings says).
    model: the Claude model its Claude Code runs on (the agent's own choice), else the default.
    voice_id: the voice its agent speaks in (the agent's own choice), else Settings'."""
    ensure_speech_model()          # the chosen model, if it isn't here, for the next session
    live = live_session()
    if live and kind == "meeting" and session_info(live, live=live)["kind"] == "talk":
        log("start: a meeting ends the live talk %s" % live)
        stop_session()
    with _lock:
        if STARTING["state"] == "starting":
            return {"error": "already starting"}
        if live_session():
            return {"error": "a session is already recording; stop it first"}
        STARTING.update(state="starting", error=None, since=time.time(), resume=resume, kind=kind)
    threading.Thread(target=_do_start, args=(resume, kind, workspace, where, no_claude,
                                             capture, agent, model, voice_id), daemon=True).start()
    return {"started": True}


def _do_start(resume, kind="meeting", workspace=None, where=None, no_claude=False,
              capture=None, agent=None, model=None, voice_id=None):
    s = get_settings()
    try:
        if not wait_for_shutdown():
            raise RuntimeError("the previous recorder is still shutting down; try again")
        cmd = [CLI, "start", "--mode", capture or s["capture"], "--push-lines", str(s["push_lines"])]
        workspace = (workspace or "").strip()
        if kind == "talk":
            cmd += ["--kind", "talk"]            # mic only and Claude on, whatever the settings say
        if resume:
            # Its own folder, never today's default: its conversation lives there.
            workspace = session_info(resume).get("workspace")
            cmd += ["--resume", resume]
        if no_claude and kind != "talk":
            cmd.append("--no-claude")
        if workspace:
            cmd += ["--workspace", workspace]
        if valid_model(model):
            cmd += ["--claude-model", model]
        if valid_voice(voice_id):
            cmd += ["--claude-voice", voice_id]
        cmd.append("meeting")
        log("start: %s" % " ".join(cmd))
        # Output to a file, never a pipe: a background child holding a pipe
        # would block this call until the whole meeting ended.
        out_path = ROOT / "ui-start.log"
        with open(out_path, "w") as out:
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out,
                           stderr=subprocess.STDOUT, timeout=180)
        sid = live_session(fresh=True)        # the recorder just started: not a look from before it
        _LIVE_SEEN.update(at=time.time(), sid=sid)   # nor the orb's and the messenger's cached one
        if not sid:
            pids = recorders()
            if pids:   # which process blocked the start, for the log
                ps = subprocess.run(["ps", "-o", "pid,ppid,lstart,command", "-p", ",".join(pids)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                log("start: recorders present:\n%s" % ps.stdout.strip())
            tail = out_path.read_text().strip().splitlines()[-8:]
            raise RuntimeError("the recorder did not start:\n" + "\n".join(tail))
        log("start: live session %s" % sid)
        with _lock:
            STARTING.update(state=None, error=None)
    except Exception as exc:
        log("start failed: %s" % exc)
        with _lock:
            STARTING.update(state="error", error=str(exc))


def stop_session(detach=False):
    log("stop")
    ending = was = _live_now(0)
    stop_voice()
    if detach:
        subprocess.Popen([CLI, "stop"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        mac_stopped(was)
        return {"ok": True}
    subprocess.run([CLI, "stop"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=60)
    if ending:
        call_ended(ending)            # a call's session stopped (End, Escape, quiet, Quit, a meeting started)
        lite_ended(ending)            # a Claude Code session's call: its voice kept, the mod told
    mac_stopped(was)
    return {"ok": True}


# -- Add your Mac: today's recorder, as an agent's meeting -------------------------------

MAC_WAIT = 90.0                   # /api/msg/mac answers once the recorder is live


MAC_CLOSING_WAIT = 15.0           # a meeting ended or paused whose recorder is still exiting: waited for this long


def msg_agent(agent_id):
    """(agent, workspace) from the timeline's records, as calls look them up, or (None, None)."""
    find = MSG.get("agent")
    agent = find(agent_id) if find is not None and agent_id else None
    if not agent:
        return None, None
    return agent, {"machine": agent.get("machine") or "local", "folder": agent.get("folder") or ""}


def _closing(sid):
    """The live session is on its way out: its meeting was ended or paused (its recorder is
    finishing a long recording), or its folder is gone. It holds the Mac only until it exits."""
    sdir = SESSIONS / sid
    if not sdir.is_dir():
        return True
    bound = addmac.bound(sdir) or {}
    return bool(bound.get("ended_at") or bound.get("paused_at"))


def mac_with(wait=None):
    """Who has the Mac's one recorder now, in words, or None: nobody. Asked fresh, never from
    the recorders' cache; a session that's closing (ended, paused, gone) is waited for, so a
    meeting started right after another was stopped isn't refused over it."""
    recorders(True)                              # a fresh look: live_session() then reads it
    live = live_session()
    if live and _closing(live):
        end = time.time() + (MAC_CLOSING_WAIT if wait is None else wait)
        while live and _closing(live) and time.time() < end:
            time.sleep(0.25)
            recorders(True)
            live = live_session()
        if live and _closing(live):
            log("mac: %s is still closing after %.0f s: the Mac is still with it" % (live, MAC_CLOSING_WAIT if wait is None else wait))
    if not live:
        with _lock:
            return "a meeting that is starting" if STARTING["state"] == "starting" else None
    bound = addmac.bound(SESSIONS / live)
    if bound:
        agent, _ws = msg_agent(bound["agent"])
        if agent and agent.get("name"):
            return agent["name"]
    return session_info(live, live=live).get("title") or "another meeting"


def _wait_live(wait):
    """The new live session's id once its recorder runs, or {error}."""
    end = time.time() + wait
    while time.time() < end:
        with _lock:
            st = dict(STARTING)
        if st.get("state") == "error":
            return {"error": "Your Mac didn't start: %s" % (st.get("error") or "the recorder failed")}
        live = live_session()
        if live and st.get("state") is None:
            return live
        time.sleep(0.2)
    return {"error": "Your Mac didn't start in time"}


def mac_stopped(sid, now=None):
    """The recorder stopped: for an agent's meeting that is a pause, whoever stopped it (the
    window's Pause, the companion's Stop, Quit). The meeting stays open to Resume; End, or
    two hours paused, makes its card. Quick, and on the caller's thread: Quit doesn't wait."""
    sdir = SESSIONS / sid if sid else None
    data = addmac.bound(sdir) if sdir else None
    if data is None or data.get("call") or is_talk(sid):
        return None                               # not an agent's meeting; a call's card, even one that
                                                  # got the Mac, is the calls': their stop hook makes it
    if data.get("ended_at"):
        return data
    addmac.save(sdir, addmac.paused(data, now))
    log("mac: %s paused" % sid)
    _msg_event("meeting", mac_event(sid))
    return data


def _mac_meeting(sid):
    """(folder, agent.json) of an agent's meeting `sid`, or (None, None)."""
    if not isinstance(sid, str) or not valid_id(sid):
        return None, None
    sdir = SESSIONS / sid
    data = addmac.bound(sdir)
    if data is None or data.get("call") or is_talk(sid):
        return None, None
    return sdir, data


def call_muted(sid):
    """A live call's voice paused (muted), or None when `sid` is no call with a voice now."""
    vs = VOICE.get(sid) if isinstance(sid, str) and is_talk(sid) else None
    return None if vs is None else bool(vs.muted)


def mac_pause(sid):
    """POST /api/msg/mac/pause {session}: the recorder stops, the meeting stays open. A call
    (its window's Pause) pauses as the orb and the band do: the voice stops now, the mic is held."""
    if call_muted(sid) is not None:
        return voice_action(sid, "mute", "full window")
    sdir, data = _mac_meeting(sid)
    if data is None:
        return {"error": "No such meeting"}
    if data.get("ended_at"):
        return {"error": "That meeting has ended"}
    if live_session() == sid:
        stop_session()                            # its hook pauses it
    else:
        mac_stopped(sid)                          # already stopped (Quit, a crash): paused all the same
    return {"ok": True}


def mac_resume(sid):
    """POST /api/msg/mac/resume {session}: recording again into the same meeting, with the
    same switches and the agent listening. {session} once the recorder is live. A call: unpaused."""
    if call_muted(sid) is not None:
        return voice_action(sid, "unmute", "full window")
    sdir, data = _mac_meeting(sid)
    if data is None:
        return {"error": "No such meeting"}
    if data.get("ended_at"):
        return {"error": "That meeting has ended"}
    if live_session() == sid:
        return {"session": sid}
    who = mac_with()
    if who:
        return {"error": "Your Mac is with %s: stop it there first" % who}
    mode = addmac.capture_mode(data.get("meeting_audio", True), data.get("mic", True)) or "auto"
    lite_owned = str(data.get("agent") or "").startswith("lite:")     # its Claude Code session listens, not ours
    r = start_session(resume=sid, kind="meeting", capture=mode, agent=None if lite_owned else data.get("agent"),
                      no_claude=lite_owned)
    if not r.get("started"):
        return {"error": r.get("error") or "Your Mac didn't start"}
    live = _wait_live(MAC_WAIT)
    if isinstance(live, dict):
        return live
    addmac.save(sdir, addmac.resumed(addmac.bound(sdir) or data))
    log("mac: %s resumed" % sid)
    _msg_event("meeting", mac_event(sid))
    return {"session": live}


def mac_end(sid, now=None, background=True):
    """POST /api/msg/mac/end {session}: End meeting, from the window's menu (or two hours
    paused): stopped if it records, over for good, and its one card made."""
    sdir, data = _mac_meeting(sid)
    if data is None:
        return {"error": "No such meeting"}
    if data.get("ended_at"):
        return {"ok": True}
    if live_session() == sid:
        stop_session()                            # pauses it: the stretch closes
    data = addmac.ended(addmac.bound(sdir) or data, now)
    addmac.save(sdir, data)
    log("mac: %s ended (%.0f s recorded)" % (sid, addmac.recorded_s(data)))
    _msg_event("meeting", mac_event(sid))

    def card():
        try:
            mac_card(sid)
        except Exception as exc:
            log("mac: %s's card: %r" % (sid, exc))
    if background:
        threading.Thread(target=card, name="mac-card", daemon=True).start()
    else:
        card()
    return {"ok": True}


def mac_sweep(now=None):
    """Agent meetings left behind: one whose recorder is gone without a pause (a crash, the
    app quit before it could say) is paused; one paused two hours ends, with its card."""
    now = now or time.time()
    live = live_session()
    try:
        dirs = [d for d in SESSIONS.iterdir() if (d / addmac.AGENT_FILE).exists()]
    except OSError:
        return
    for sdir in dirs:
        sid, data = sdir.name, addmac.bound(sdir)
        if data is None or data.get("ended_at") or data.get("call") or sid == live or is_talk(sid):
            continue
        if not data.get("paused_at"):
            try:
                last = max(p.stat().st_mtime for p in (sdir / "transcript.jsonl", sdir / addmac.AGENT_FILE) if p.exists())
            except ValueError:
                last = now
            mac_stopped(sid, now=min(now, last))
            data = addmac.bound(sdir)
        if addmac.expired(data, now):
            log("mac: %s paused for two hours: ending it" % sid)
            mac_end(sid, now=now)


AUTO_STOPPED = "auto_stop.json"      # in a meeting's folder: it stopped itself, {at, minutes}


def silence_stop(now=None):
    """A meeting left recording with nothing said for auto_stop_min minutes (one ran 639 minutes
    overnight) stops as Stop does, its summary made, and says so in its own record. "Said" is a
    transcribed line, the recorder's own word that it heard speech, so a quiet room or a fan
    never keeps it going. Calls end on their own sooner (TALK_IDLE); paused meetings have the sweep."""
    minutes = get_settings().get("auto_stop_min") or 0
    live = live_session()
    if not minutes or not live or is_talk(live):
        return None
    now = now or time.time()
    sdir = SESSIONS / live
    times = [p.stat().st_mtime for p in (sdir / "transcript.jsonl", READY) if p.exists()]
    last = max(times) if times else now           # the last line, or when this run of the recorder began
    if now - last < minutes * 60:
        return None
    log("meeting %s: nothing said for %d minutes: it stops itself" % (live, minutes))
    write_json(sdir / AUTO_STOPPED, {"at": now, "minutes": minutes})
    import lite as lite_mod
    data = addmac.bound(sdir)
    if lite_mod.is_lite(data):
        lite().stop({"session": live})            # ends it, its card made; the session's band is told
    elif data:
        mac_end(live, now=now)
    else:
        stop_session()
    return live


def auto_stopped(sid):
    """"Stopped after N minutes of silence" for a meeting that stopped itself, or None."""
    a = read_json(SESSIONS / str(sid) / AUTO_STOPPED) if sid and valid_id(str(sid)) else {}
    return "Stopped after %d minutes of silence" % a["minutes"] if isinstance(a.get("minutes"), int) else None


def _mac_sweep_forever(every=60.0):
    while True:
        try:
            mac_sweep()
        except Exception as exc:
            log("mac: sweep: %r" % exc)
        try:
            silence_stop()
        except Exception as exc:
            log("meeting: silence stop: %r" % exc)
        time.sleep(every)


def mac_card(sid, now=None):
    """card.json for an agent's meeting: its points, the time recorded and the span beside
    it, and memory "pending". Nothing goes to the agent until the user keeps it."""
    sdir = SESSIONS / sid
    data = addmac.bound(sdir)
    if data is None:
        return None
    maker = globals().get("make_card")            # the calls': standby-agent decisions on the transcript and chat
    if maker:
        maker(sid)
    card_path = sdir / "card.json"
    written = read_json(card_path) or {}
    ended = float(data.get("ended_at") or now or time.time())
    c = addmac.card(sid, data, written.get("title") or data.get("name") or session_info(sid, live=None).get("title"), ended,
                    written.get("points") or [])
    if not card_path.exists():                     # no generator yet: the card, with nothing decided
        written = {"points": [], "at": c["started"], "copy": c["copy"], "delivered_at": None}
    written["duration_s"] = c["duration_s"]        # recorded time, pauses left out
    written["started_at"], written["ended_at"] = addmac.span(data, ended)    # "10:02-10:47" beside the time
    # the user keeps or forgets it, never decided here; from Claude Code, saved in Standby with no choice
    written.setdefault("memory", "saved" if str(data.get("agent") or "").startswith("lite:") else "pending")
    card_path.write_text(json.dumps(written))
    log("mac: %s's card (%d points, %s)" % (sid, len(c["points"]), written["memory"]))
    _msg_event("card", {"session": sid})
    return c


def mac_event(sid, live=_ASK):
    """The `meeting` event for an agent's meeting: {session, agent, live, paused, ended,
    started, recorded_s}, or None. recorded_s: the time recorded so far, pauses left out."""
    data = addmac.bound(SESSIONS / sid) if sid else None
    if data is None:
        return None
    st = addmac.state(data, (live_session() if live is _ASK else live) == sid)
    return {"session": sid, "agent": data["agent"], "live": st == "live", "paused": st == "paused",
            "ended": st == "ended", "started": data.get("started"),
            "recorded_s": round(addmac.recorded_s(data), 1)}


def mac_event_recent(sid):
    """mac_event with the recent look at which session is live: for the timeline's many reads."""
    return mac_event(sid, _live_now())

APP_WINDOW = {}                   # "win": Standby's pywebview window, once it exists


def set_claude(sid, on):
    """Turn Claude on or off for a session. The recorder follows the switch:
    off stops delivery, on catches up on everything said so far."""
    marker = SESSIONS / sid / "claude.off"
    if on:
        try:
            marker.unlink()
        except OSError:
            pass
        if sid == live_session():
            threading.Thread(target=_ensure_agent, args=(sid,), daemon=True).start()
    else:
        marker.write_text("")
    log("claude %s for %s" % ("on" if on else "off", sid))
    return {"ok": True, "claude": bool(on)}


def _restart_agent():
    """A model changed: the agent runs with its models fixed, so restart it. The
    meeting's conversations are on disk and continue where they were."""
    live = live_session()
    try:
        subprocess.run(transport.for_config().agent_cmd(["stop"]), stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=30)
        log("agent restarted for new models")
    except Exception as exc:
        log("agent restart failed: %r" % exc)
    if live:
        _ensure_agent(live)


def _ensure_agent(sid=None):
    try:
        t = transport.for_config()
        ok, msg = t.ensure_agent(transport.agent_settings(transport.load_config()).get("model"))
        log("agent: %s" % (msg if not ok else "ready on %s" % t.label))
    except Exception as exc:
        log("agent start failed: %r" % exc)


def wait_delivered(sid, seconds=30):
    """Until the recorder has delivered the whole transcript, so a question
    asked right after turning Claude on sees everything."""
    sdir = SESSIONS / sid
    end = time.time() + seconds
    while time.time() < end:
        try:
            if int(_read_text(sdir / "delivered") or 0) >= (sdir / "transcript.jsonl").stat().st_size:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    return False


def is_talk(sid):
    return read_json(SESSIONS / sid / "meta.json").get("kind") == "talk"


def _begin_ask(sid, question, voice=False, own=False):
    # An ended session (a meeting or a talk) is asked by typing, like a chat:
    # no recorder, no mic. Voice mode is the live session's only.
    # own: a turn of Claude's own (a call's greeting): no words of the user's, so no row and nothing shown as asked
    if voice and sid != live_session():
        return {"error": "voice mode works in the live session"}
    with _lock:
        if sid in PENDING:
            return {"error": "still working on the previous question"}
        PENDING[sid] = {"question": "" if own else question, "started": time.time(), "progress": [], "voice": voice}
    if own:
        return None
    row = append_chat(sid, "me", question, voice=voice)
    with _lock:
        PENDING[sid]["row"] = row
    return None


def pending_shown(sid):
    """The question being answered, as the window draws it, or None: none for a turn of Claude's own."""
    with _lock:
        pending = dict(PENDING[sid]) if sid in PENDING else None
        if pending:
            pending["progress"] = list(pending["progress"])
    return pending if pending and pending.get("question") else None


# The last voice question answered in each session, {"qid", "rows"}: dropped
# from the chat if voice mode says the user went on after all (an early question).
RECENT_VOICE = {}


def cancel_voice(sid, qid, quiet=False, said=""):
    """Voice mode stops a question. quiet: the user went on, and the next question
    has all of it: this one's question leaves the chat (no "(stopped)"). Its answer goes
    too, unless the voice had said some of it already (said): then the answer's row stays,
    saying exactly that, so the window shows what was heard."""
    import agentctl
    with _lock:
        p = PENDING.get(sid)
        running = p is not None and p.get("voice") and p.get("qid") in (None, qid)
        if quiet and running:
            p["quiet"] = True
        done = RECENT_VOICE.get(sid) if quiet and not running else None
        if done is not None and done.get("qid") == qid:
            RECENT_VOICE.pop(sid, None)
        else:
            done = None
    if quiet:                                      # answered already: nothing of it stays with the voice
        agentctl.cancel(qid, dropped=True)
    if done is not None:
        rows = done["rows"]
        for row in rows:
            drop_chat_row(sid, row, said.strip() if said and row is rows[-1] and row.get("role") == "ai" else None)
        log("ask dropped from the chat: the user went on (voice, answered already%s)" % (", what it had said kept" if said else ""))
        return
    # cut off (not "they went on", whose next question has all of it): a hand-off its whole reply made still goes
    if not quiet:
        agentctl.cancel(qid, keep=True)


def deliver_ended(sid, progress=None):
    """An ended session asked something: no recorder is sending its
    transcript, so what the agent does not have yet goes from here, once: all
    of it if Claude was off (asking turns it on), or the last lines if the
    meeting ended before they went. The agent then answers in the session's
    own conversation, and pushes nothing of its own: it listens only to a live
    one."""
    sdir = SESSIONS / sid
    if not claude_on(sid):
        if progress is not None:
            progress({"kind": "tool", "text": "Turning Claude on: catching up on the transcript"})
        try:
            (sdir / "claude.off").unlink()
        except OSError:
            pass
        log("claude on for %s, asked after it ended" % sid)
    local = sdir / "transcript.jsonl"
    data = local.read_bytes() if local.exists() else b""
    try:
        done = int(_read_text(sdir / "delivered") or -1)
    except ValueError:
        done = -1
    if done >= len(data):
        return True                                   # it has all of it
    t = transport.for_config()
    ok = t.write("live/%s.meta.json" % sid, json.dumps(read_json(sdir / "meta.json")))
    ok = ok and t.write("live/%s.jsonl" % sid, data)
    for name in ("speakers.auto.json", "speakers.names.json", "speakers.known.json"):
        if (sdir / name).exists():
            t.write("live/%s.%s" % (sid, name), (sdir / name).read_text())
    if ok:
        (sdir / "delivered").write_text("%d\n" % len(data))
    else:
        log("ask: could not send %s's transcript to %s" % (sid, t.label))
    return ok


def ask_spoken(sid, question, on_event, on_queued, heard=None, commit=None):
    """A voice mode question, answered before returning: the answer text. It
    shows in the chat like a typed one, and streams to on_event as it comes.
    heard: what the user had heard of the answer they cut off, which
    Claude is told with it (voice.with_cut); not shown in the chat.
    commit: a question asked early, before the turn was surely over (voice.Question.committed):
    what its answer does waits on it (_run_ask)."""
    if sid in LITE_CALLS:                          # a call from a Claude Code session: its own rules
        return lite_ask(sid, question, on_event, on_queued, heard, commit=commit)
    refused = _begin_ask(sid, question, voice=True)
    if refused:
        return "I'm still on your last question." if "previous" in refused["error"] else refused["error"]
    return _run_ask(sid, question, voice=True, on_event=on_event, on_queued=on_queued, heard=heard, commit=commit)


def _run_ask(sid, question, voice=False, on_event=None, on_queued=None, heard=None, commit=None):
    import agentctl
    t0 = time.time()
    written = []

    def queued(qid):
        with _lock:
            if sid in PENDING:
                PENDING[sid]["qid"] = qid
        if on_queued is not None:
            on_queued(qid)

    def progress(ev):
        if ev.get("kind") == "suggest":           # follow-ups to this answer, for the chips
            keep_suggest(sid, ev.get("items") or [], ev.get("from") or "answer")
            return
        if ev.get("kind") == "timing":            # for the voice's timing line only
            if on_event is not None:
                on_event(ev)
            return
        with _lock:
            if sid in PENDING:
                PENDING[sid]["progress"].append(ev)
        if ev.get("kind") == "delta":
            written.append(ev.get("text") or "")
        if on_event is not None:
            on_event(ev)
    talk, live = is_talk(sid), sid == live_session()
    try:
        if not live:
            deliver_ended(sid, progress)
        elif not claude_on(sid):
            progress({"kind": "tool", "text": "Turning Claude on: catching up on the transcript"})
            set_claude(sid, True)
            if not wait_delivered(sid):
                log("ask: transcript not fully delivered yet, asking anyway")
        # A typed question names its session: it may have ended, or another may be live.
        if voice:
            import voice as voice_mod
            question_for_claude = VOICE_TAG + voice_mod.with_cut(question, heard)
        else:
            question_for_claude = question
        answer = agentctl.ask(question_for_claude, err=None,
                              on_progress=progress, on_queued=queued,
                              session=None if voice else sid)
    except Exception as exc:
        answer = "Could not reach the agent (%r). Try again; if it keeps happening, run `standby check` in Terminal." % exc
    after_ask(answer, "local")   # can Claude be reached, on its machine
    if answer.strip() == "(stopped)":
        # Interrupted: keep what Claude had said, so the chat matches what was heard.
        so_far = "".join(written).strip()
        answer = (so_far + " …\n\n(stopped)") if so_far else "(stopped)"
    elif voice:
        import voice as voice_mod
        answer = voice_mod.spoken_error(answer)   # an error is never read out as it is
    log("ask %.1fs%s" % (time.time() - t0, " (voice)" if voice else ""))
    # Asked early: its words may have been ready, but what it does ([HANDOFF], [CANCEL #n], [END])
    # waits until the turn is surely theirs. They went on: none of it goes.
    went_on = commit is not None and not commit()
    with _lock:
        pending = PENDING.get(sid, {})
        steps = [e["text"] for e in pending.get("progress", []) if e.get("kind") == "tool"]
        quiet = bool(pending.get("quiet")) or went_on
    if quiet:
        # The user went on: the next question has all of it, so this one leaves no
        # trace, and does nothing.
        if pending.get("row") is not None:
            drop_chat_row(sid, pending["row"])
        with _lock:
            PENDING.pop(sid, None)
        log("ask dropped from the chat: the user went on (voice)")
        trace(sid, "early", step="held", why="went on", acts=bool(CANCEL_RE.search(answer) or END_TAG in answer
                                                               or answer.strip().startswith(HANDOFF_TAG)))
        return ""
    more = None
    if sid in LITE_CALLS and END_TAG in answer:    # the voice ends the call after this goodbye
        answer = answer.replace(END_TAG, "").strip()
        LITE_CALLS[sid]["ending"] = True
    cancels = "".join(" [CANCEL #%s]" % n for n in CANCEL_RE.findall(answer)) if sid in LITE_CALLS else ""
    if cancels:                                    # the voice stops a task ([CANCEL #n]): the call does it (lite_ask);
        answer = _no_cancel(answer)                # never in the row
    if sid in LITE_CALLS and answer.strip().startswith(HANDOFF_TAG):    # the row says what went, as heard
        more = {"handoff": answer.strip()[len(HANDOFF_TAG):].strip() or question}
        answer = HANDOFF_TAG + " " + more["handoff"]
    if sid in LITE_CALLS and answer.strip() in (QUIET_TAG, ""):
        ai_row = None                              # the voice chose to say nothing: nothing to show
    else:
        # a hand-off's row: exactly what the voice said before taking it deeper, or nothing; never a line of the app's
        spoken = " ".join("".join(written).split())
        ai_row = append_chat(sid, "ai", spoken if more else answer.strip(), steps, voice=voice, more=more)
    answer += cancels
    with _lock:
        if voice and pending.get("qid") and pending.get("row") is not None and ai_row is not None:
            RECENT_VOICE[sid] = {"qid": pending["qid"], "rows": [pending["row"], ai_row]}
        PENDING.pop(sid, None)
    if talk:
        title_talk(sid)
    return answer.strip()


# ---------------------------------------------------------------- Screen Recording
#
# Meeting audio is captured with ScreenCaptureKit, so it needs Screen & System Audio
# Recording. macOS asks once, ever; granting it ends in Quit & Reopen.

SNAP_ASKED = ROOT / "screen-permission-asked"     # macOS shows its prompt once, ever
SNAP_WARNING = ("macOS will then ask to quit and reopen Standby. That pauses this session; "
                "Resume continues it")
ICONS = {}                # bundle id -> PNG bytes, or None for no such app: kept while the app runs (a meeting's app)
BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,199}$")
_SNAP_CHECKS = {}                 # cached: available(), and permission for 2 s (the companion polls fast)
SETTINGS_OPENED = []              # System Settings opened by itself this launch: once, then just the line
# Why the screen can't be recorded, in words. macOS can't be
# read without asking, so from what this build did (native.appaudio.grants): its capture
# heard the Mac, so it has the sound only; or an earlier build had the screen, so macOS
# kept that old one in the list; or neither: it was never allowed.
SCREEN_WHY = {
    "audio_only": ("Standby can hear the Mac but not see the screen: turn it on under "
                   "Screen & System Audio Recording, the top list"),
    "stale": ("Standby is switched on, but for an older copy of the app. Reset it: run "
              "tccutil reset ScreenCapture dev.standby.app, then allow it again"),
    "never": "Allow Screen Recording, then quit and reopen Standby",
}


def _snap_available():
    if "available" not in _SNAP_CHECKS:
        _SNAP_CHECKS["available"] = permissions.screen_available()
    return _SNAP_CHECKS["available"]


def _snap_permitted(fresh=False):
    at, ok = _SNAP_CHECKS.get("permission", (0.0, False))
    if fresh or time.time() - at > 2.0:
        ok = permissions.screen_allowed()
        _SNAP_CHECKS["permission"] = (time.time(), ok)
        if ok and not _SNAP_CHECKS.get("noted"):
            _SNAP_CHECKS["noted"] = True
            from native import appaudio
            app.note_grant("screen")             # this build may record the screen
    return ok


# Standby.app's own bundle id (install.sh writes it): the one entry the reset touches.
APP_ID = "dev.standby.app"
# Tests put a stand-in here: the real one resets the user's own permission.
TCCUTIL = shlex.split(os.environ.get("STANDBY_TCCUTIL") or "/usr/bin/tccutil")
RESET_CMD = "tccutil reset ScreenCapture %s" % APP_ID


def screen_reset():
    """Standby is switched on in Screen & System Audio Recording, yet macOS says no:
    the switch is for an older copy (macOS keeps one per build, and a rebuild makes a
    new one). Reset Standby's own entry, and only that (tccutil, its bundle id), then
    ask again: macOS's prompt, then Quit & Reopen. The user asks for it (a button, after
    a confirm); never by itself."""
    if os.environ.get("STANDBY_NO_DEVICES") and TCCUTIL == ["/usr/bin/tccutil"]:
        return {"state": "failed", "error": "not in a test: it would reset the user's own permission"}
    try:
        r = subprocess.run(TCCUTIL + ["reset", "ScreenCapture", APP_ID], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=15)
        ok, said = r.returncode == 0, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        ok, said = False, repr(exc)
    log("screen: reset Standby's Screen Recording entry: %s (%s)" % ("done" if ok else "failed", said[:200]))
    if not ok:
        return {"state": "failed", "error": "macOS didn't reset it. Run this in Terminal, then allow Standby again: "
                + RESET_CMD}
    SNAP_ASKED.parent.mkdir(parents=True, exist_ok=True)
    SNAP_ASKED.write_text("%d\n" % time.time())          # asked now: the next refusal opens System Settings
    _SNAP_CHECKS.pop("permission", None)
    try:
        permissions.ask_screen()                           # a test never reaches the real prompt
    except Exception as exc:
        log("screen: could not ask macOS again (%r)" % exc)
    return {"state": "asked", "reason": "Allow Standby when macOS asks, then quit and reopen it"}


def with_screen_why(sources):
    """The meeting's sound refused (cause "permission"): marked "stale" when this build
    is known to be newer than the one macOS allowed (screen_why), so the page says so."""
    out = []
    for src in sources or []:
        if src.get("kind") == "meeting" and src.get("cause") == "permission":
            src = dict(src, stale=screen_why() == "stale")
        out.append(src)
    return out


MIC_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"


def mic_permission():
    """POST /api/mic/permission: the recorder hears only silence from a mic macOS refuses
    (its source says cause "permission"): System Settings opens at Microphone, on the
    user's click. The recorder tries the mic again by itself, so allowing it is enough."""
    try:
        subprocess.run(OPEN + [MIC_PANE], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": "Couldn't open System Settings (%s)" % exc.__class__.__name__}
    log("mic permission: System Settings opened")
    return {"ok": True}


SCREEN_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
FOLDERS_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"
_MIC_CHECK = {}                   # what macOS says of the mic, kept 2 s (the band asks every few seconds)


def mic_access():
    """What macOS says of Standby and the mic: "authorized", "denied", "restricted",
    "undetermined", or None. Asks nothing. A test (no devices) is "authorized"; tests of
    the band put a stand-in here."""
    if os.environ.get("STANDBY_NO_DEVICES"):
        return "authorized"
    at, status = _MIC_CHECK.get("status", (0.0, None))
    if time.time() - at > 2.0:
        status = permissions.mic_status()
        _MIC_CHECK["status"] = (time.time(), status)
    return status


def access(sid, capture):
    """What macOS lets a live meeting or call hear, for the band in Claude Code: "mic" when it
    records the mic, and "meeting_audio" when it records the Mac's sound (capture auto or
    meeting; a call never does). Each is "allowed"; "denied", refused (the band says where to
    allow it); or "ask", macOS is about to ask (the band says so). A mic the recorder has heard
    sound from is allowed, whatever this process is told. The meeting's sound is
    "not_asked" while no app plays: macOS asks only once one does (the recorder then opens its
    source), so a meeting in the room says nothing of it. The recorder's own finding (a source
    with cause "permission") comes first."""
    sources = (live_status(sid) or {}).get("sources") or []
    refused = {src.get("kind") for src in sources if src.get("cause") == "permission"}
    heard = any(src.get("kind") == "mic" and src.get("heard") for src in sources)
    playing = any(src.get("kind") == "meeting" for src in sources)
    out = {}
    if capture in ("auto", "mic"):
        status = mic_access()
        # sound from the mic is the answer: this process's own question to macOS may still say
        # "not asked yet" after the recorder's was answered
        out["mic"] = ("denied" if "mic" in refused else "allowed" if heard
                      else "denied" if status in ("denied", "restricted")
                      else "ask" if status == "undetermined" else "allowed")
    if capture in ("auto", "meeting"):
        out["meeting_audio"] = ("denied" if "meeting" in refused else "allowed" if _snap_permitted()
                                else "denied" if SNAP_ASKED.exists() else "ask" if playing else "not_asked")
    # a folder macOS refuses its Claude (the agent notes a read refused there): its name, for the band
    folder = (read_json(SESSIONS / sid / "refused.json") or {}).get("folder")
    if isinstance(folder, str) and folder in ("Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music"):
        out["folder_denied"] = folder
    return out


def live_capture(sid):
    """What a live session records, as access() takes it: a call the mic; a meeting as it was
    started (agent.json), else the Settings' recording mode."""
    if session_info(sid).get("kind") == "call":
        return "mic"
    data = addmac.bound(SESSIONS / sid)
    if data is not None:
        return addmac.capture_mode(data.get("meeting_audio"), data.get("mic"))
    return get_settings().get("capture") or DEFAULTS["capture"]


def lite_permission(b):
    """POST /api/lite/permission {session}: the band's Open Settings, on the user's click, while
    macOS refuses the live session something: System Settings at that pane (the mic first)."""
    sid = str(b.get("session") or "")
    if not valid_id(sid) or sid != live_session():
        return {"error": "Nothing is live"}
    now = access(sid, live_capture(sid))
    pane = (MIC_PANE if now.get("mic") == "denied" else SCREEN_PANE if now.get("meeting_audio") == "denied"
            else FOLDERS_PANE if now.get("folder_denied") else None)
    if pane is None:
        return {"ok": True, "allowed": True}
    try:
        subprocess.run(OPEN + [pane], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": "Couldn't open System Settings (%s)" % exc.__class__.__name__}
    log("lite permission: System Settings opened at %s" % {MIC_PANE: "Microphone", SCREEN_PANE: "Screen Recording"}.get(pane, "Files & Folders"))
    return {"ok": True}


def screen_why():
    """Why this build can't record the screen: "audio_only", "stale" or "never" (SCREEN_WHY)."""
    from native import appaudio
    g, build = app.grants(), app.build_mark()
    heard, screen = g.get("heard") or {}, g.get("screen") or {}
    if build and heard.get("build") == build:
        return "audio_only"                   # its capture hears the Mac: the sound list only
    if build and screen.get("build") and screen["build"] != build:
        return "stale"                        # an earlier build had it: macOS keeps that one
    return "never"


def screen_permission(confirm=False):
    """Screen Recording, asked for: (state, reason), for the meeting audio's Allow
    (POST /api/screen/permission).

    "granted"; "confirm": a session is live and macOS has never been asked, so the
    page shows SNAP_WARNING first (granting ends in Quit & Reopen, which pauses the
    session) and asks again with confirm; "asked": macOS's own prompt, once ever;
    "settings": after that, System Settings at the right pane."""
    if _snap_permitted(fresh=True):
        return "granted", ""
    if live_session() and not confirm and not SNAP_ASKED.exists():
        return "confirm", ""
    if not SNAP_ASKED.exists():
        SNAP_ASKED.parent.mkdir(parents=True, exist_ok=True)
        SNAP_ASKED.write_text("%d\n" % time.time())
        log("screen: asking macOS for Screen Recording")
        permissions.ask_screen()
        return "asked", "Allow Screen Recording in System Settings"
    why = screen_why()
    if not SETTINGS_OPENED:                    # by itself once a launch; after that, just the line
        SETTINGS_OPENED.append(time.time())
        log("screen: Screen Recording still not allowed (%s); opening System Settings" % why)
        permissions.open_screen_settings()
    else:
        log("screen: Screen Recording still not allowed (%s)" % why)
    return "settings", SCREEN_WHY[why]


def snap_app_icon(bundle):
    """An app's icon for the list, PNG bytes, or None. Made once per app."""
    with _lock:
        if bundle in ICONS:
            return ICONS[bundle]
    try:
        data = workspace.app_icon(bundle)
    except Exception as exc:
        log("snapshot: no icon for an app: %r" % exc)
        data = None
    with _lock:
        ICONS[bundle] = data
    return data


# ---------------------------------------------------------------- suggestions
#
# Up to 3 short questions the user might ask next, from what Claude is hearing (a
# meeting's listen replies) or as follow-ups to its last answer. The agent cuts
# them out of its replies and keeps the newest set in live/<sid>.suggest.json;
# answers bring theirs back in the question's own stream. The newest set wins, by
# this Mac's clock (the host's may differ). Never one already asked. After a
# meeting, the obvious ones join them. No Claude calls of their own.

SUGGEST = {}                  # sid -> {"items", "from", "at"}, also kept in sessions/<sid>/suggest.json
SUGGEST_SEEN = {}             # sid -> the agent file's own "at", last taken
STARTERS = ["Summarize", "Action items", "Draft a follow-up"]


def _norm_q(q):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (q or "").lower()).split())


def keep_suggest(sid, items, source):
    """A new set from the agent: it replaces the old one (an empty one clears it)."""
    data = {"items": [i for i in items if isinstance(i, str)][:3], "from": source, "at": time.time()}
    with _lock:
        SUGGEST[sid] = data
    try:
        (SESSIONS / sid / "suggest.json").write_text(json.dumps(data, ensure_ascii=False))
    except OSError:
        pass


def take_agent_suggest(sid, raw):
    """The agent's live/<sid>.suggest.json (bytes): kept if it changed since last read."""
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return
    if not isinstance(data, dict) or "at" not in data or SUGGEST_SEEN.get(sid) == data["at"]:
        return
    SUGGEST_SEEN[sid] = data["at"]
    keep_suggest(sid, data.get("items") or [], data.get("from") or "listen")


# ---------------------------------------------------------------- can Claude be reached
#
# One source of truth: the agent's last engine error, live/claude-error.json (it
# can't sign in, it stopped, it isn't installed), written and cleared by its
# turns; and what this Mac saw itself: no answer at all from where Claude runs.
# The newest wins. The session, the sidebar and the companion all show it, and
# it goes by itself with the next call that works.

CLAUDE_ERRS = {}            # machine -> {"mac", "agent", "asked"}: each machine's own
AGENT_ERRORS = ("Agent not available", "Could not reach the agent")


def default_machine():
    """The machine a plain New meeting uses (Settings' agent)."""
    try:
        return "local"
    except Exception:
        return "local"


def _errs(machine=None):
    return CLAUDE_ERRS.setdefault(machine or default_machine(), {"mac": None, "agent": None, "asked": 0.0})


def page_machine(sid=_ASK):
    """The machine a page speaks for: a session's own; else (asked for none) the live
    session's; else the default."""
    sid = live_session() if sid is _ASK else sid
    return "local" if sid else default_machine()


def _is_local(machine):
    """Does Claude Code run on this Mac for that machine (its transport says so)?"""
    try:
        return transport.for_config().mode == "local"
    except Exception:
        return False


def _ascii_tail(text, lines=15):
    """The raw error for Details: ASCII, its last few lines, so it never opens a wall."""
    text = str(text or "").encode("ascii", "backslashreplace").decode("ascii")
    return "\n".join(text.strip().splitlines()[-lines:])[:2000]


def read_agent_error(machine=None):
    """The agent's live/claude-error.json, on this Mac."""
    try:
        raw = (ROOT / "live" / "claude-error.json").read_bytes()
    except OSError:
        raw = None
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    _errs(machine)["agent"] = data if isinstance(data, dict) and data.get("kind") else None
    return _errs(machine)["agent"]


def note_unreachable(detail, machine=None):
    """No answer at all from Claude Code here (the agent didn't start)."""
    machine = machine or default_machine()
    _errs(machine)["mac"] = {"kind": "unreachable", "text": "Can't reach Claude Code.",
                             "fix": "If it keeps happening, run `standby check` in Terminal; then try again.",
                             "detail": str(detail or ""), "at": time.time()}


def note_reached(machine=None):
    """A call that worked: whatever was wrong is over, here and in the agent's file."""
    machine = machine or default_machine()
    _errs(machine)["mac"] = None
    if _errs(machine)["agent"] is not None and not _is_local(machine):
        _errs(machine)["agent"] = None            # the agent removed its file with that turn


def after_ask(answer, machine=None):
    """What an answer says about reaching Claude on its machine: the Mac's own
    record, and (over ssh) the agent's file read again at once, not at the next poll."""
    machine = machine or default_machine()
    _errs(machine)["asked"] = time.time()
    text = (answer or "").strip()
    if text.startswith(AGENT_ERRORS):
        note_unreachable(text, machine)
    elif text and text != "(stopped)" and not text.startswith(("engine error", "Engine exited", "I can't reach Claude")):
        note_reached(machine)
    if not _is_local(machine):
        try:
            read_agent_error(machine)
        except Exception:
            pass


def claude_error(machine=None):
    """Can Claude be reached on a machine (a session's own, else the default):
    None, or {kind, text, fix, detail, at, machine, name} (kind: signin,
    unreachable, engine, missing), for /api/state, /api/session and /api/companion."""
    machine = machine or default_machine()
    if _is_local(machine):
        try:
            read_agent_error(machine)
        except Exception:
            pass
    errs = _errs(machine)
    found = [e for e in (errs["mac"], errs["agent"]) if e]
    if not found:
        return None
    e = max(found, key=lambda x: float(x.get("at") or 0))
    return {"kind": e.get("kind"), "text": str(e.get("text") or "Claude can't be reached."),
            "fix": str(e.get("fix") or "Try again."), "detail": _ascii_tail(e.get("detail")), "at": e.get("at"),
            "machine": machine, "name": machine_name(machine)}


def transport_mode():
    try:
        return transport.for_config().mode
    except Exception:
        return "unset"


def suggest_for(sid, chat):
    """What /api/session gives the page: the newest set, without anything already
    asked in the chat; for an ended meeting, the starters first. None for nothing."""
    if sid == live_session():
        try:
            take_agent_suggest(sid, (ROOT / "live" / (sid + ".suggest.json")).read_bytes())
        except OSError:
            pass
    with _lock:
        data = SUGGEST.get(sid)
    if data is None:
        data = read_json(SESSIONS / sid / "suggest.json")
    asked = {_norm_q(m.get("text")) for m in chat if m.get("role") == "me"}
    items = [i for i in (data or {}).get("items") or [] if _norm_q(i) not in asked]
    source = (data or {}).get("from")
    if sid != live_session() and not is_talk(sid) and (SESSIONS / sid / "transcript.jsonl").exists():
        # The starters first: the page shows three, and each one asked lets a live one step in.
        items = [s for s in STARTERS if _norm_q(s) not in asked] + [i for i in items if i not in STARTERS]
        source = "end"
    if not items:
        return None
    return {"session": sid, "items": items, "from": source, "at": (data or {}).get("at")}


# ---------------------------------------------------------------- voice mode

VOICE_TAG = "[VOICE] "       # standby-agent answers these to be heard (see voice.py)
WORDS_TAG = "[WORDS] "       # Standby's own word-fixes pass: standby-agent counts it as "words" in Settings > Usage
VOICE = {}                    # sid -> voice.VoiceSession


def start_voice(sid):
    """Voice mode in the live call: the recorder listens for turns, Claude's
    answers are spoken. Only a call has it."""
    import agentctl
    import voice
    if sid != live_session():
        return {"error": "voice mode works in the live session"}
    if not is_talk(sid):
        return {"error": "voice mode is only for calls"}
    if not session_info(sid)["mic"]:
        return {"error": "this session is not recording a microphone"}
    if mic_muted(sid):                            # refused, said plainly; unmuting is the user's
        return {"error": MUTED_LINE, "mic_muted": True}
    current = VOICE.get(sid)
    if current is not None and current.state != "off":
        return {"ok": True, "state": current.status()}
    ctl = (_read_text(SESSIONS / sid / "dictate") or "").split()
    if ctl[:1] == ["on"]:
        return {"error": "finish dictating first"}
    # A voice or hold switch left by an app that quit mid-conversation is stale.
    if ctl[:1] in (["voice"], ["hold"], ["floor"]) and len(ctl) == 2:
        (SESSIONS / sid / "dictate").write_text("off %s\n" % ctl[1])
        time.sleep(0.3)
    talk = is_talk(sid)
    # The recorder plays Claude's voice, the one voice of a call (echo cancelled where it can:
    # the user can talk over it); never one of this process's, nor the Mac's, instead.
    vs = voice.VoiceSession(sid, SESSIONS / sid,
                            ask=lambda q, ev, qd, heard=None, commit=None: ask_spoken(sid, q, ev, qd, heard=heard, commit=commit),
                            cancel=lambda qid, quiet=False: cancel_voice(sid, qid, quiet),
                            speaker=voice.RecorderSpeaker(SESSIONS / sid, log=log),
                            log=log,
                            dictating=lambda: dictating(sid),
                            # A talk is its conversation: it ends with it, or after a quiet while.
                            idle_end=TALK_IDLE if talk else None, busy=lambda: sid in PENDING or lite_busy(sid),
                            on_end=(lambda why: end_talk(sid, why)) if talk else None)
    vs.ledger = bool(talk)                       # a call: its turns kept in its record (sbledger)
    vs.on_heard = lambda qid, heard, cut, unsaid: _voice_heard(sid, qid, heard, cut, unsaid)
    VOICE[sid] = vs
    _warm_voice(sid)                              # asked first (a file, at once): the voice is ready before its first turn
    vs.start()
    return {"ok": True, "state": vs.status()}


def _warm_voice(sid):
    import agentctl
    try:
        agentctl.warm_voice()
    except Exception as exc:              # an older agent: the first question briefs it instead
        log("voice: warm-up failed: %r" % exc)


PAUSED_FROM = ("mini window", "full window", "band")    # where a pause or resume may say it came from


def voice_action(sid, action, src=None):
    """src: where a mute (pause) or unmute (resume) came from, one of PAUSED_FROM, for the log."""
    vs = VOICE.get(sid)
    if action == "start":
        return start_voice(sid)
    if vs is None:
        return {"error": "voice mode is not on"}
    if action == "stop":
        vs.stop()
    elif action == "interrupt":
        vs.interrupt()
    elif action in ("mute", "unmute"):
        was = bool(vs.muted)
        vs.mute(action == "mute")
        log("lite: %s: %s (%s)%s" % (sid, "paused" if action == "mute" else "resumed", src if src in PAUSED_FROM else "unknown",
                                     "" if was != (action == "mute") else ", as it already was"))
        trace(sid, "pause" if action == "mute" else "resume", getattr(vs, "turn", None),
              by=src if src in PAUSED_FROM else "unknown", already=was == (action == "mute"))
    else:
        return {"error": "unknown voice action"}
    return {"ok": True, "state": vs.status()}


TALK_IDLE = float(os.environ.get("STANDBY_TALK_IDLE", 120))   # seconds with nothing said


def end_talk(sid, why):
    """Voice mode in a talk ended (End, Escape, or the quiet while): so does the
    talk. Stopped here and now, not in the background: a meeting started right
    after must not find a stop still running that would take it down too."""
    if sid == live_session():
        log("talk %s ends: %s" % (sid, why or "ended by the user"))
        stop_session()


def dictating(sid):
    """Is Standby Dictation listening (for another app) during this session's
    voice mode? It leaves its pid in the session's `dictating` file."""
    path = SESSIONS / sid / "dictating"
    try:
        pid = int(path.read_text().split()[0])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


def start_talk(wait=90, workspace=None, where=None, voice=None, model=None, voice_id=None):
    """A call's session in voice mode: returns {"id"} once its voice mode is on."""
    live = live_session()
    if live:
        if session_info(live, live=live)["kind"] != "talk":
            return {"error": "a meeting is recording"}
        r = start_voice(live)
        return r if r.get("error") else {"id": live, "state": r.get("state")}
    r = start_session(kind="talk", workspace=workspace, where=where, model=model, voice_id=voice_id)
    if r.get("error"):
        return r
    end = time.time() + wait
    while time.time() < end:
        with _lock:
            st = dict(STARTING)
        if st.get("state") == "error":
            return {"error": st.get("error")}
        live = live_session()
        if live and st.get("state") is None:
            if voice:                             # an agent's voice: told before voice mode starts it
                voice_file(live, where, voice)
            r = start_voice(live)
            return r if r.get("error") else {"id": live, "state": r.get("state")}
        time.sleep(0.2)
    return {"error": "the talk did not start"}


# An agent's voice is one conversation, call after call (the agents layer keeps which):
# live/<session>.voice.json tells standby-agent what to fork it from, its name, and what
# happened since its last call; agent/<session>.voice.uuid is what it ran as.
def voice_file(sid, where, voice):
    try:
        transport.for_config().write("live/%s.voice.json" % sid, json.dumps(voice))
    except Exception as exc:
        log("voice: %s's voice file: %r" % (sid, exc))


def voice_ran(sid):
    """The conversation session sid's voice ran as, on its machine, or None."""
    try:
        raw = transport.for_config().read("agent/%s.voice.uuid" % sid)
        value = raw.decode("utf-8", "replace").strip() if raw else ""
    except Exception:
        return None
    return value if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", value) else None


def title_talk(sid):
    """A talk is titled from its chat, after its first answer: it has no
    transcript. The text goes with the call, so it works over SSH too."""
    if sid in TITLED or session_info(sid)["title_source"] != "default":
        return
    chat = read_jsonl(SESSIONS / sid / "chat.jsonl")
    if not any(m.get("role") == "ai" for m in chat):
        return
    TITLED.add(sid)
    text = "\n".join("%s: %s" % ("You" if m.get("role") == "me" else "Claude", (m.get("text") or "")[:600])
                     for m in [m for m in chat if said_row(m)][:8])

    def run():
        try:
            r = subprocess.run(transport.for_config().agent_cmd(["title", "--stdin"]), input=text,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            title = (r.stdout or "").strip().splitlines()
            if title and title[0] and not (SESSIONS / sid / "title.txt").exists():
                (SESSIONS / sid / "title.auto.txt").write_text(title[0][:80] + "\n")
                log("titled %s" % sid)
        except Exception as exc:
            log("title failed: %r" % exc)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- calls with an agent
# A call is today's talk, one to one with an agent: voice mode on that agent's own Claude
# Code conversation, with everything voice mode has (talking over Claude, the companion).
# It always runs on a fork of that conversation, made at the call's start, whether or not
# anything else holds it: nothing reaches the real agent until the user keeps the call
# (card_memory), as one [CALL SUMMARY] message.
# In the session's folder: agent.json {agent, name, copy, meeting_audio, mic, call} from the
# start, and card.json {points, at, duration_s, copy, delivered_at} once it ends.

MSG = {"agent": None,       # the timeline: agent id -> {id, name, session, machine, folder} or None
       "event": None,       # the timeline: (event, data) to the page's stream
       "deliver": None}     # the agents layer: (agent id, text) -> True once it reached the real agent
CALL_SUMMARY = "[CALL SUMMARY]"


# ---------------------------------------------------------------- Standby inside Claude Code (mac/lite.py)

_LITE = []


def lite():
    """The one Lite (mac/lite.py): a meeting for a Claude Code session the Lite mod drives."""
    if not _LITE:
        import lite as lite_mod
        _LITE.append(lite_mod.Lite(sys.modules[__name__]))
    return _LITE[0]


def lite_get(path, q):
    """GET /api/lite/<...> for meetings (the mod's). Calls register theirs in LITE_GET."""
    if path in LITE_GET:
        return LITE_GET[path](q)
    return {"error": "unknown request"}


LITE_GET = {
    "/api/lite/state": lambda q: lite().state(q.get("cc")),
    "/api/lite/lines": lambda q: lite().lines(q.get("session"), q.get("after")),
    "/api/lite/batch": lambda q: lite().batch(q.get("session"), everything=q.get("all") in ("1", "true")),
    "/api/lite/card": lambda q: lite().card(q.get("session")),
    "/api/lite/transcript": lambda q: lite().transcript(q.get("cc"), q.get("session")),
}
LITE_POST = {
    "/api/lite/listen": lambda b: lite().listen(b),
    "/api/lite/batch/sent": lambda b: lite().batch_sent(b),
    "/api/lite/pause": lambda b: lite().pause(b),
    "/api/lite/resume": lambda b: lite().resume(b),
    "/api/lite/stop": lambda b: lite().stop(b),
    "/api/lite/permission": lambda b: lite_permission(b),
    "/api/lite/pass": lambda b: lite().set_pass(b),
}


def lite_publish(cc, event):
    """An event for the Lite mod of one Claude Code session (calls publish theirs here too)."""
    lite().publish(cc, event)


def _msg_event(event, data):
    if event == "card" and _LITE:                 # a Lite meeting's card: to its session's stream too
        try:
            info = _call_info((data or {}).get("session"))
            import lite as lite_mod
            if lite_mod.is_lite(info):
                lite().card_event(data["session"])        # once per change, whoever wrote it
        except Exception as exc:
            log("lite: card event: %r" % exc)
    f = MSG.get("event")
    if f is not None:
        try:
            f(event, data)
        except Exception as exc:
            log("calls: %s event: %r" % (event, exc))


def _call_info(sid):
    """agent.json of a session, if it is one of a call's (or a meeting's for an agent)."""
    return read_json(SESSIONS / sid / "agent.json") if sid and valid_id(sid) else {}


def call_first(sid):
    """The first session of the call `sid` is part of (itself, unless the call moved)."""
    seen = set()
    while sid and sid not in seen:
        seen.add(sid)
        prev = _call_info(sid).get("call")
        if not prev or prev == sid:
            return sid
        sid = prev
    return sid


def call_sessions(sid):
    first = call_first(sid)
    return [first] + list(_call_info(first).get("moved") or [])


def end_call(sid):
    """POST /api/msg/call/end {session}: as End on the orb, or Escape: voice mode off,
    and with it the talk."""
    if not valid_id(str(sid or "")) or sid != live_session():
        return {"error": "That call has ended"}
    vs = VOICE.get(sid)
    if vs is not None and vs.state != "off":
        vs.stop()                           # its on_end ends the talk (end_talk), as End does today
    if live_session() == sid:
        stop_session()
    return {"ok": True}


def call_ended(sid):
    """A session stopped: if it was a call's last, the call is over and its card is made."""
    info = _call_info(sid)
    if not info.get("agent") or not is_talk(call_first(sid)):
        return
    if call_sessions(sid)[-1] != sid:
        return                              # it went on in another session
    first = call_first(sid)
    _msg_event("call", {"session": first, "agent": info["agent"], "phase": "ended", "copy": bool(info.get("copy")),
                        "started": _call_info(first).get("started")})
    threading.Thread(target=make_card, args=(first,), name="call-card", daemon=True).start()
    if MSG.get("voice_end"):                      # the agent's voice is now this call's: it remembers it
        threading.Thread(target=lambda: MSG["voice_end"](info["agent"], first, voice_ran(sid)),
                         name="call-voice", daemon=True).start()


def _card_text(sessions, name, me="You"):
    """What the points are made from: each session's chat (a call's words, and the steps its voice ran
    itself), and a meeting's transcript too. name: the voice's, me: the user's, as the lines call them."""
    out = []
    for s in sessions:
        sdir = SESSIONS / s
        for r in read_jsonl(sdir / "transcript.jsonl") if not is_talk(s) else []:
            out.append("%s: %s" % (r.get("speaker") or "Room", (r.get("text") or "").strip()))
        chat = read_jsonl(sdir / "chat.jsonl")
        lost = set(str(m.get("result") or "") for m in chat if m.get("reason") == "undelivered")
        for m in chat:
            text = (m.get("text") or "").strip()
            if m.get("handoff") or text.startswith(HANDOFF_TAG):     # work sent to the Claude Code session
                work = m.get("handoff") or text[len(HANDOFF_TAG):].strip()
                out.append("%s: %s" % ("Never reached Claude Code, so not done" if work in lost else "Handed to Claude Code",
                                       work[:1500]))
            elif m.get("reason") == "undelivered":
                continue                                             # said with its hand-off, above
            elif "result" in m:                                      # and what it came back with
                out.append("Claude Code came back: %s" % (" ".join(str(m["result"] or text).split())[:600]
                                                           if m.get("reason") == "done" else text))
            else:
                if is_talk(s) and m.get("role") != "me":             # what the voice did itself, before it said this
                    out += ["Voice ran: %s" % re.sub(r"^Running ", "", str(step))[:300] for step in m.get("steps") or []]
                out.append("%s: %s" % (me if m.get("role") == "me" else name, text[:1500]))
    return "\n".join(x for x in out if x.split(": ", 1)[-1])


# The model can fail for a moment (it worked on the same words minutes later, 2026-10-23): one more
# try after this many seconds before the card says it couldn't. Tests set it to 0.
DECISIONS_RETRY_S = 5.0


def _ask_decisions(first, text):
    """`standby-agent decisions` on `text` once: (its lines, why it gave none, or "")."""
    try:
        r = subprocess.run(transport.for_config().agent_cmd(["decisions", "--session", first]), input=text,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150)
    except subprocess.TimeoutExpired:
        return [], "timed out after 150 s"
    except Exception as exc:
        return [], repr(exc)
    lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    if r.returncode == 0 and lines:
        return lines, ""
    err = (r.stderr or "").strip().splitlines()
    return [], "rc %d, %s" % (r.returncode, err[-1][:300] if err else "no output")


def _decisions(first, text):
    """(points, gist, failed) for the card, from `standby-agent decisions` on `text`: up to 3
    decisions or key points, or, when nothing was decided, one line on what it was about. A card
    always has one of the two: a call too short to say anything, or one the model couldn't be
    asked about, says so in the gist, and failed is then True (Summarize again can make it anew)."""
    what = "call" if is_talk(first) else "meeting"
    if not text.strip():
        return [], "No one spoke on this %s." % what, False
    lines, why = _ask_decisions(first, text)
    if why:
        log("call: points for %s: %s; asking once more" % (first, why))
        time.sleep(DECISIONS_RETRY_S)
        lines, why = _ask_decisions(first, text)
        if why:
            log("call: points for %s: %s again; the card says it couldn't" % (first, why))
    gist = next((x[len("GIST:"):].strip() for x in lines if x.upper().startswith("GIST:")), "")
    points = [x for x in lines if not x.upper().startswith("GIST:")][:3]
    if points:
        return points, "", False
    if gist:
        return [], gist, False
    return [], "Standby couldn't summarize this %s." % what, True


def make_card(sid):
    """card.json for the call or meeting `sid` belongs to (its first session): when, how long,
    and its points (or, if nothing was decided, a gist) from `standby-agent decisions`. Nothing goes to the agent: the card waits,
    memory "pending", for the user to keep it or forget it (card_memory)."""
    first = call_first(sid)
    sessions, info = call_sessions(first), _call_info(first)
    started = info.get("started") or session_info(first).get("started") or time.time()
    last = session_info(sessions[-1])
    ended = last.get("ended") or time.time()
    kept = read_json(SESSIONS / first / "card.json")
    card = {"points": None, "at": started, "duration_s": round(max(0.0, ended - started), 1),
            "copy": bool(info.get("copy")), "memory": "pending", "delivered_at": None}
    for key in ("memory", "delivered_at", "queued"):         # a card made again keeps what the user chose
        if kept.get(key):
            card[key] = kept[key]
    if str(info.get("agent") or "").startswith("lite:"):
        card["memory"] = "saved"       # from Claude Code: kept in Standby, never sent to Claude, nothing to choose
    if not is_talk(first):
        card["title"] = session_info(first).get("title")
    card["points"], gist, failed = _decisions(first, _card_text(sessions, info.get("name") or "Claude"))
    card["gist"] = gist or None                   # only when nothing was decided: what it was about
    if failed:
        card["failed"] = True                     # the model couldn't be asked: Summarize again
    note = auto_stopped(first)
    if note:
        card["note"] = note                       # it stopped itself: "Stopped after 15 minutes of silence"
    write_json(SESSIONS / first / "card.json", card)
    log("call: card for %s, %d point(s)%s" % (first, len(card["points"]), "" if card["points"] else ", a gist"))
    _msg_event("card", {"session": first})
    return card


AGAIN = set()                    # the cards being made again now: a double click asks once


def summarize_again(b):
    """POST /api/msg/card/again {session}: a card the model couldn't be asked for (its "failed"),
    made again from the same words, in the background; the card event says when it's there.
    Until then the card says so ("again"), as right after it ended."""
    sid = b.get("session") if isinstance(b, dict) else None
    if session_dir(sid) is None:
        return {"error": "No such card"}
    first = call_first(sid)
    card = read_json(SESSIONS / first / "card.json")
    if not card.get("failed"):
        return {"error": "That summary is there already"}
    with _lock:
        if first in AGAIN:
            return {"ok": True}
        AGAIN.add(first)
    card.pop("failed", None)
    card["again"] = True                          # a made card has none: written anew
    write_json(SESSIONS / first / "card.json", card)
    _msg_event("card", {"session": first})

    def run():
        try:
            if addmac.bound(SESSIONS / first) is not None:
                mac_card(first)                   # a meeting's: its time and span with it
            else:
                make_card(first)
        except Exception as exc:
            log("call: %s's card again: %r" % (first, exc))
            left = read_json(SESSIONS / first / "card.json")
            if left.pop("again", None):           # never made: it still couldn't, and says so
                left["failed"] = True
                write_json(SESSIONS / first / "card.json", left)
                _msg_event("card", {"session": first})
        finally:
            with _lock:
                AGAIN.discard(first)
    threading.Thread(target=run, name="card-again", daemon=True).start()
    log("call: %s's card, made again" % first)
    return {"ok": True}


MSG_POST["/api/msg/card/again"] = summarize_again


# Keep or forget (the user's): what a call or a meeting heard goes to the real agent only if
# the user keeps it, once, and then it stays kept. Forgotten can still be kept later: nothing
# was sent. card.json's memory is pending, kept or forgotten.
MEMORY_LOCK = threading.Lock()


def _card_for(session):
    """(first session, card.json, agent.json) for a call's or a meeting's card, or an error line."""
    if not valid_id(str(session or "")):
        return None, None, None, "No such card"
    first = call_first(session)
    card, info = read_json(SESSIONS / first / "card.json"), _call_info(first)
    if not card or not info.get("agent"):
        return None, None, None, "That card isn't ready yet"
    return first, card, info, None


def card_text(first, card, info):
    """The message the real agent gets when a call or a meeting is kept: the tag, what it was,
    and its points, with the card's session so the agent can look it up."""
    if is_talk(first):
        mins = max(1, int(round(float(card.get("duration_s") or 0) / 60.0)))
        lines = ["%s Call with the user (%d min), on a copy of your conversation, recorded by Standby. "
                 "Standby session: %s" % (CALL_SUMMARY, mins, first)]
        if card.get("points"):
            lines += ["- " + p for p in card["points"]]
        else:
            lines.append("Gist: " + (card.get("gist") or "Nothing was decided."))
        return "\n".join(lines)
    return addmac.card_message({"title": card.get("title") or "Meeting", "duration_s": card.get("duration_s") or 0,
                                "session": first, "points": card.get("points") or [], "gist": card.get("gist")})


def card_memory(session, keep):
    """POST /api/msg/memory {session, keep}: Keep sends the card to the real agent, once, then it
    is kept for good; Forget sends nothing, and a forgotten card can still be kept. A second Keep,
    a retry or a double click never sends twice. {memory, delivered_at, queued?} or {error}."""
    import lite as lite_mod
    if isinstance(session, str) and valid_id(session) and lite_mod.is_lite(addmac.bound(SESSIONS / session)):
        return {"error": lite_mod.SAVED}
    with MEMORY_LOCK:
        first, card, info, why = _card_for(session)
        if why:
            return {"error": why}
        memory = card.get("memory") or "pending"
        if memory == "kept":
            return {k: card[k] for k in ("memory", "delivered_at", "queued") if card.get(k) is not None} if keep else \
                {"error": "It's in memory now"}
        if not keep:
            card["memory"] = "forgotten"
            forget = MSG.get("voice_forget")
            note = forget(info["agent"], first) if forget and is_talk(first) else None
            if note:                              # the voice can't drop a call later ones build on: says so
                card["memory_note"] = note
        else:
            find, deliver = MSG.get("agent"), MSG.get("deliver")
            if deliver is None or (find is not None and not find(info["agent"])):
                return {"error": "%s isn't there any more" % (info.get("name") or "That agent")}
            try:
                sent = deliver(info["agent"], card_text(first, card, info), first)
            except Exception as exc:
                log("card: delivering %s: %r" % (first, exc))
                return {"error": "Couldn't reach %s: try again" % (info.get("name") or "the agent")}
            card["memory"] = "kept"
            if sent:
                card["delivered_at"] = time.time()
            else:
                # Held in the agents layer's outbox, tried at every app start, every 5 minutes, and as
                # soon as the agent or its machine is back, once each. When it goes, card_delivered
                # sets delivered_at; if the agent leaves Standby first, card_dropped puts it back.
                card["queued"] = True
        write_json(SESSIONS / first / "card.json", card)
        log("card: %s %s" % (first, card["memory"]))
    _msg_event("card", {"session": first})
    return {k: card.get(k) for k in ("memory", "delivered_at", "queued") if card.get(k) is not None}


CARD_SESSION = re.compile(r"(?:Standby session: |standby-ctx --session )(\S+)")


def _card_of(which):
    """A held summary's card: its session as given, or found in its text (a summary kept
    before the session went with it). None if there's no such card."""
    which = str(which or "")
    m = None if valid_id(which) else CARD_SESSION.search(which)
    sid = m.group(1) if m else which
    if not valid_id(sid) or not (SESSIONS / call_first(sid) / "card.json").is_file():
        return None
    return call_first(sid)


def card_delivered(agent, which):
    """MSG["on_delivered"]: a kept card's summary held in the outbox has reached the agent."""
    with MEMORY_LOCK:
        first = _card_of(which)
        if first is None:
            return
        card = read_json(SESSIONS / first / "card.json")
        card["delivered_at"] = time.time()
        card.pop("queued", None)
        card.pop("memory_note", None)
        write_json(SESSIONS / first / "card.json", card)
        log("card: %s delivered from the outbox" % first)
    _msg_event("card", {"session": first})


def card_dropped(agent, which, why):
    """MSG["on_dropped"]: a held summary had nowhere to go (its agent left Standby). The card
    goes back to pending, saying why, rather than "in memory" for something never sent."""
    with MEMORY_LOCK:
        first = _card_of(which)
        if first is None:
            return
        card = read_json(SESSIONS / first / "card.json")
        card["memory"] = "pending"
        card.pop("queued", None)
        card["memory_note"] = str(why or "It couldn't be sent")[:120]
        write_json(SESSIONS / first / "card.json", card)
        log("card: %s back to pending: %s" % (first, why))
    _msg_event("card", {"session": first})


MSG["on_delivered"], MSG["on_dropped"] = card_delivered, card_dropped


# ---------------------------------------------------------------- a call from a Claude Code session
# Standby Lite: the user's own Claude Code session is where real work happens. The call is
# today's voice mode on a talk, with the voice that remembers this session's earlier calls; when
# the voice would hand work to Standby's own agent ([DEEP]), standby-agent answers [HANDOFF] and
# the user's words go to the Claude Code session instead, through the mod: a "handoff" event it
# submits, and an "abort" when the user stops the session's work. The mod reports the session's
# progress back (lite_progress), and the voice says how it ended when the conversation is quiet.

LITE_CALLS = {}           # live call session -> {cc, cwd, running, doing, said, turn}
LITE_VOICES = ROOT / "lite" / "voices.json"   # Claude Code session id -> its voice's conversation
HANDOFF_TAG = "[HANDOFF]"                     # standby-agent's answer for work the session does
CANCEL_RE = re.compile(r"\[CANCEL\s*#?(\d+)\]")    # the voice stops a task of the call's, by its number


def _no_cancel(answer):
    """The voice's answer without its [CANCEL #n] signals, spaced as it was."""
    if not CANCEL_RE.search(str(answer or "")):
        return answer
    return re.sub(r"[ \t]{2,}", " ", CANCEL_RE.sub("", answer)).strip()
LITE_FROM_CALL = "From your call: "             # what Claude Code reads before work the call's voice hands it
QUIET_TAG = "[QUIET]"                          # the voice's answer when it chooses to say nothing
END_TAG = "[END]"                              # after its goodbye: the voice ends the call
LITE_END_WAIT = 60.0                           # the longest a goodbye may take before the call ends anyway
LITE_OPENING = "[CALL START]"                 # the voice's first turn, before the user speaks: never shown
LITE_ORB = set()                               # sessions the user sent to the orb (minimize to orb, Cmd+M): only these have one
LITE_RESULT_MAX = 4000                        # what came back from the session, kept with the call
LITE_DELIVER_WAIT = 20.0                      # a hand-off the mod hasn't confirmed by then never reached the session
LITE_NO_WORD = "no word from Claude Code"


def _lite_out(cc, event):
    """One event on the session's stream (/api/lite/stream, the meetings' and the calls')."""
    f = globals().get("lite_publish")
    if f is not None:
        try:
            f(cc, event)
        except Exception as exc:
            log("lite: publishing %s: %r" % (event.get("call"), exc))


def lite_busy(sid):
    """The call is working (handed-on work running): the quiet while it does is not idle."""
    c = LITE_CALLS.get(sid)
    return bool(c and c.get("running"))


# ---------------------------------------------------------------- a call's tasks, as facts
# Every piece of work on a call, a hand-off of the voice's or a prompt typed in the session's chat, has one
# record: sent (handed on, no word back yet), queued (the session busy: it starts when that ends), running,
# stalled (no progress for LITE_STALL), done, failed or stopped. Made only from what happened (handed,
# received, queued, the session's steps, its turn's end) and a watchdog. The voice is told these facts, in
# sessions/<sid>/tasks.txt and with its turns, and claims nothing else.
LITE_LIVE = ("sent", "queued", "running", "stalled")
LITE_ENDED = ("done", "failed", "stopped")
LITE_STALL = 90.0           # running with no step for this long: stalled
LITE_RECENT = 120.0         # an ended task stays on the list this long
LITE_QUIET_GAP = 1.5        # both sides quiet this long before the voice tells what came back on its own


def _lite_task(c, kind, what, state, source="voice"):
    """A new task on the call: its id is the call's own number for it (#n)."""
    c["handed"] = c.get("handed", 0) + 1
    now = time.time()
    t = {"id": "%d" % c["handed"], "what": " ".join(str(what or "").split()), "state": state, "since": now,
         "last": now, "doing": "", "result": "", "seen": False, "source": source, "kind": kind}
    c.setdefault("tasks", {})[t["id"]] = t
    return t


def _lite_set(sid, c, t, state, result=None):
    """A task's new state, the call's "working", the file the voice reads, and the record's trace."""
    if t["state"] == state and result is None:
        return
    t["state"], t["since"] = state, time.time()
    if result is not None:
        t["result"] = " ".join(str(result).split())
    if state in LITE_ENDED:
        t["ended"] = t["since"]
    c["running"] = any(x["state"] in LITE_LIVE for x in c.get("tasks", {}).values())
    trace(sid, "task", id=t["id"], state=state)
    _lite_tasks_file(sid, c)


def _lite_task_line(t, now=None):
    """One task as a fact: its number, its state (and since when, by the clock, so a line stays the same until
    the task changes), and what it is; a result when it ended."""
    what = ("(typed) " if t["source"] == "typed" else "") + (t["what"][:160] or "(nothing said)")
    st, at = t["state"], lambda x: time.strftime("%H:%M:%S", time.localtime(x))
    if st == "running":
        head = "running since %s" % at(t["since"])
        if t["doing"]:
            head += ", last step %s (%s)" % (at(t["last"]), t["doing"][:80])
    elif st == "stalled":
        head = "stalled, no step since %s" % at(t["last"])
    elif st in LITE_ENDED:
        head = "%s at %s" % (st, at(t.get("ended", t["since"])))
    else:
        head = "%s since %s" % (st, at(t["since"]))
    line = "#%s %s: %s" % (t["id"], head, what)
    if st in LITE_ENDED and t["result"]:
        line += " \u2192 " + t["result"][:300]
    return line


def lite_tasks(c, now=None):
    """The tasks worth showing: every one not ended, and ended ones of the last LITE_RECENT."""
    now = now or time.time()
    return [t for t in (c or {}).get("tasks", {}).values()
            if t["state"] in LITE_LIVE or now - t.get("ended", now) <= LITE_RECENT or not t["seen"]]


def lite_tasks_text(c, now=None):
    now = now or time.time()
    lines = []
    for t in lite_tasks(c, now):
        t["line"] = _lite_task_line(t)             # as the voice is shown it: what "carried" looks for
        lines.append(t["line"])
    return "\n".join(lines)


def _lite_tasks_file(sid, c):
    """sessions/<sid>/tasks.txt: the call's tasks as facts, for the voice's turns (rewritten on each change; the
    agent puts it in the call's record as a [TASKS] entry when it has changed)."""
    try:
        (SESSIONS / sid / "tasks.txt").write_text(lite_tasks_text(c) + "\n" if c.get("tasks") else "")
    except OSError:
        pass


def _lite_tasks_note(sid, c):
    """The tasks, for a turn of the voice's: the block it reads before their words. What it shows that has
    ended is the voice's now: never told on its own after."""
    shown = lite_tasks(c)
    if not shown:
        return ""
    for t in shown:
        if t["state"] in LITE_ENDED:
            t["seen"] = True
    return "[TASKS]\n%s\n[END TASKS]" % lite_tasks_text(c)


def lite_task_list(sid):
    """For the call window, the mini window and the band: the tasks, short."""
    c = LITE_CALLS.get(sid)
    return [{"id": t["id"], "state": t["state"], "label": t["what"][:60], "since": t["since"], "last": t["last"]}
            for t in lite_tasks(c)] if c else []


def _lite_watchdog(sid, c):
    """Running with no step for LITE_STALL: stalled (a session that did one step and stopped shows its turn's
    end as done; one that goes quiet mid-turn shows here)."""
    now = time.time()
    for t in list(c.get("tasks", {}).values()):
        if t["state"] == "running" and now - t["last"] > LITE_STALL:
            _lite_set(sid, c, t, "stalled")


def _lite_voices():
    return read_json(LITE_VOICES) or {}


def _cc_work_dirs(cc, folder=None, limit=12):
    """Where a Claude Code session works: the folders its transcript shows it in (its cwd, and the projects of
    the files it read or changed), each as its project's top (the git root, else the file's folder), and the
    additional directories its settings give it. Not the home folder itself, nor the folder the call runs in."""
    home = os.path.realpath(os.path.expanduser("~"))
    found = []

    def top(path):
        d = os.path.realpath(os.path.expanduser(path))
        d = d if os.path.isdir(d) else os.path.dirname(d)
        probe = d
        while probe.startswith(home + os.sep) and probe != home:
            if os.path.isdir(os.path.join(probe, ".git")):
                return probe
            probe = os.path.dirname(probe)
        return d

    def add(path):
        if not isinstance(path, str) or not path.startswith(("/", "~")):
            return
        d = top(path)
        if d in (home, "/") or not os.path.isdir(d) or (folder and d == os.path.realpath(folder)):
            return
        if d not in found:
            found.append(d)

    t = _cc_transcript(cc)
    try:
        lines = t.read_text(errors="replace").splitlines()[-3000:] if t is not None else []
    except OSError:
        lines = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        add(r.get("cwd"))
        for blk in ((r.get("message") or {}).get("content") or []) if r.get("type") == "assistant" else []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use" and isinstance(blk.get("input"), dict):
                add(blk["input"].get("file_path") or blk["input"].get("notebook_path") or blk["input"].get("path"))
    for f in [os.path.expanduser("~/.claude/settings.json")] + ([os.path.join(folder, ".claude", n)
              for n in ("settings.json", "settings.local.json")] if folder else []):
        for d in ((read_json(Path(f)).get("permissions") or {}).get("additionalDirectories") or []):
            add(d)
    return found[-limit:]


def lite_call(b):
    """POST /api/lite/call {cc, cwd?}: voice mode on a talk for that Claude Code session, in its
    folder, with its own voice from earlier calls. {session} or {error}."""
    cc = str(b.get("cc") or "").strip()
    if not re.match(r"^[0-9A-Za-z._-]{1,80}$", cc):
        return {"error": "No Claude Code session"}
    live = live_session()
    if live:
        c = LITE_CALLS.get(live)
        if c and c["cc"] == cc:
            return {"session": live, "already": True}     # this session's call, already on
        return {"error": "Standby is busy with another call or meeting: end it first", "busy": {"session": live}}
    cwd = str(b.get("cwd") or "").strip()
    folder = cwd if cwd and os.path.isdir(os.path.expanduser(cwd)) else None
    # Who the voice is and what Claude Code is to it: standby-agent's call prelude, with the folder.
    voice = {"from": _lite_voices().get(cc), "name": "Claude Code · voice", "handoff": True, "folder": folder}
    gist, gist_at, current = lite_gist(cc)
    if gist:                                       # what the session has been working on: known from the start
        voice["gist"], voice["gist_at"] = gist, gist_at
        if not current:
            voice["gist_stale"] = True             # said so, if the new one isn't made before the hello
    # The session moved on since: a new gist is made now, in the background; the hello uses what's prepared,
    # and the new one comes with a next turn
    fresh = {}
    dirs = _cc_work_dirs(cc, folder)               # the voice reads where the session works, as the session does
    if dirs:
        voice["dirs"] = dirs
    lite_gist_refresh(cc, then=lambda text: fresh.update(text=text))
    r = start_talk(workspace=folder, where="local", voice=voice)
    if r.get("error"):
        return r
    sid = r["id"]
    LITE_CALLS[sid] = {"cc": cc, "cwd": folder, "running": False, "doing": "", "said": "", "turn": None, "gist": None,
                       "fresh": fresh, "voice": voice, "since": time.time()}
    started = session_info(sid).get("started") or time.time()
    # Bound like a Lite meeting ("lite:<cc>"), as a call: the card (keep or forget) is served as theirs.
    write_json(SESSIONS / sid / "agent.json", {"agent": "lite:" + cc, "kind": "call", "name": "Claude Code",
                                              "copy": True, "meeting_audio": False, "mic": True, "started": started})
    vs = VOICE.get(sid)
    if vs is not None and hasattr(vs, "open_with"):
        vs.open_with(LITE_OPENING)                 # the voice says hello first
    threading.Thread(target=_lite_watch, args=(sid,), name="lite-call", daemon=True).start()
    messenger().lite_session(cc, folder)           # listed in Standby's past meetings and calls
    log("lite: call %s for Claude Code session %s" % (sid, cc))
    return {"session": sid}


def _lite_watch(sid):
    """The call's captions and state on the stream, as they change, until it ends."""
    seen = {}
    while sid in LITE_CALLS:
        c = LITE_CALLS[sid]
        vs = VOICE.get(sid)
        st = vs.status() if vs is not None else {"state": "off"}
        phase = st.get("state") or "off"
        if phase == "off" and live_session() != sid:
            break
        cap = (st.get("you") or "", st.get("claude") or "")
        if cap != seen.get("cap"):
            seen["cap"] = cap
            _lite_out(c["cc"], {"call": "caption", "session": sid, "you": cap[0], "said": cap[1]})
        _lite_watchdog(sid, c)
        # what the band says under the call: the voice's phase, work taken deeper, the voice failing
        state = (phase, bool(c.get("running")), st.get("notice") or "", st.get("retrying_since"), lite_waiting(sid, st))
        if state != seen.get("state"):
            seen["state"] = state
            _lite_out(c["cc"], dict({"call": "state", "session": sid, "phase": phase, "working": state[1]},
                                    **({"notice": state[2]} if state[2] else {}),
                                    **({"retrying_since": state[3]} if state[3] else {}),
                                    **({"waiting": True} if state[4] else {})))
        time.sleep(0.15)


def lite_ask(sid, question, on_event, on_queued, heard=None, commit=None):
    """A turn in a Claude Code session's call. "Stop" while the session works stops the
    session; work the voice hands on goes to the session; anything else is today's voice."""
    import interrupt
    c = LITE_CALLS[sid]
    # the greeting's hidden question, carried onto the user's words when they spoke before it: theirs only
    if question != LITE_OPENING and question.startswith(LITE_OPENING):
        question = question[len(LITE_OPENING):].strip()
        if not question:
            return ""
    if c.get("running") and interrupt.classify(question) == interrupt.CANCEL:
        for t in [x for x in c.get("tasks", {}).values() if x["state"] in LITE_LIVE]:
            t["seen"] = True                       # they stopped it: nothing to tell them
            _lite_set(sid, c, t, "stopped", result="stopped, as the user asked")
        c["running"] = False
        _lite_out(c["cc"], {"call": "abort", "session": sid})
        append_chat(sid, "me", question, voice=True)
        append_chat(sid, "ai", "Stopped it.", voice=True)
        log("lite: %s: stopped the session's work" % sid)
        trace(sid, "handoff", step="aborted")
        return "Stopped it."
    if question == LITE_OPENING:                   # the greeting: Claude's own turn, no words of the user's
        _lite_fresh_hello(sid, c)
        if _begin_ask(sid, question, voice=True, own=True):
            return ""
        return _run_ask(sid, question, voice=True, on_event=on_event, on_queued=on_queued)
    # What the voice knows before their words, for it alone: never part of the user's line (chat, window, export)
    notes = []
    fresh = c.get("fresh") or {}
    if not fresh.get("text") and "voice" in c:     # one made by a refresh that was already running
        text, _at, current = lite_gist(c["cc"])
        if current and text and text != c["voice"].get("gist"):
            fresh["text"] = text
    if fresh.get("text") and fresh["text"] != c.get("greeted") and not fresh.get("told"):
        c["gist"], fresh["told"] = fresh["text"], True          # made after the hello: with this turn
    gist, rang = c.get("gist"), False
    if gist:                                       # newer than what the voice started with: once
        notes.append("[SUMMARY] What this session has been working on, now:\n%s\n[END SUMMARY]" % gist)
        c["gist"] = None
    vs = VOICE.get(sid)
    tasks = "" if getattr(vs, "ledger", False) else _lite_tasks_note(sid, c)    # on the record: the agent places it
    if tasks:                                      # the call's work as facts (what ended, the voice has now)
        notes.append(tasks)
    if c.pop("rang", False):                       # they're back from a pause where something waited
        notes.append(LITE_RESUMED)
        rang = True
    typed_rows = list(c.get("typed") or [])
    typed = _lite_typed_words(sid, c)              # what they typed or pasted in the chat: their words too
    if typed:
        question = "%s\n%s" % (typed, question)
    told = "\n".join(notes + [question])
    refused = _begin_ask(sid, question, voice=True)
    if refused:
        return "I'm still on your last question." if "previous" in refused["error"] else refused["error"]
    answer = _run_ask(sid, told, voice=True, on_event=on_event, on_queued=on_queued, heard=heard, commit=commit)
    if commit is not None and not commit():        # asked early, and they went on: it did nothing, and
        if gist and not c.get("gist"):             # what it carried once goes with the turn that is theirs
            c["gist"] = gist
        if rang:
            c["rang"] = True
        c["typed"] = typed_rows + (c.get("typed") or [])
        return ""
    for tid in CANCEL_RE.findall(str(answer or "")):    # the voice stops a task: [CANCEL #n]
        _lite_cancel(sid, c, tid)
    answer = _no_cancel(answer)
    if c.pop("ending", False):                     # its goodbye is said in full, then the call ends
        c["hanging_up"] = True                     # nothing more is said unless they go on
        threading.Thread(target=_lite_hang_up, args=(sid,), name="lite-hang-up", daemon=True).start()
    if str(answer or "").strip() == QUIET_TAG:
        return ""                                  # the voice chose to say nothing
    if not str(answer or "").startswith(HANDOFF_TAG):
        return answer
    work = answer[len(HANDOFF_TAG):].strip() or question
    _lite_hand(sid, c, work)
    return ""                                      # its own words (before [DEEP]) were said as they came: nothing more


def _lite_hand(sid, c, work):
    """New work to the session, as a task. New work while the last runs goes with it (Claude Code takes it into
    the running turn): nothing is stopped for it. The mod says when the session really has it, or that it never
    got there. The voice's own instruction, marked as such: Claude Code reads it as work from the call (never as
    the user asking it to "ask the agent"), done in its own folder."""
    busy = bool(c.get("running"))
    t = _lite_task(c, "handoff", work, "sent")
    _lite_set(sid, c, t, "sent", result="")
    c.update(doing="", said="")
    _lite_out(c["cc"], {"call": "handoff", "session": sid, "id": "%s-%s" % (sid, t["id"]), "text": LITE_FROM_CALL + work})
    log("lite: %s: handed to the session (#%s)" % (sid, t["id"]))
    trace(sid, "handoff", id=t["id"], step="handed", work=work, session_busy=busy)
    late = threading.Timer(LITE_DELIVER_WAIT, _lite_lost, args=(sid, t["id"], LITE_NO_WORD))
    late.daemon = True
    late.start()
    return t


def _lite_cancel(sid, c, tid):
    """The voice stops a task ([CANCEL #n]): the session is told, and its record says stopped. Running alone,
    the session's turn is stopped; else (others in the same turn, or not started) it's told to drop it."""
    t = c.get("tasks", {}).get(str(tid))
    if t is None or t["state"] in LITE_ENDED:
        return False
    alone = t["state"] in ("running", "stalled") and not any(
        x is not t and x["state"] in ("running", "stalled") for x in c["tasks"].values())
    if alone:
        _lite_out(c["cc"], {"call": "abort", "session": sid})
    else:
        _lite_out(c["cc"], {"call": "handoff", "session": sid, "id": "%s-x%s" % (sid, t["id"]),
                            "text": LITE_FROM_CALL + "Never mind the earlier request, stop it and drop it: " + t["what"]})
    t["seen"] = True                               # the voice did it: nothing to tell it
    _lite_set(sid, c, t, "stopped", result="stopped by the voice, as the user asked")
    log("lite: %s: task #%s stopped by the voice (%s)" % (sid, t["id"], "its turn stopped" if alone else "told to drop it"))
    return True


# Back from a pause where something waited (the bell showed): one line, a fact for the voice's own judgement
LITE_RESUMED = "[RESUMED] They're back. While paused, they saw something was waiting."


def _lite_of(c, hid):
    """A task by the id the mod has for it (<call>-<n>), or its number."""
    return (c or {}).get("tasks", {}).get(str(hid or "").rsplit("-", 1)[-1])


def _lite_lost(sid, hid, why):
    """A hand-off that never reached the session: failed, told as news, so the voice never says it went."""
    c = LITE_CALLS.get(sid)
    t = _lite_of(c, hid)
    if t is None or t["state"] != "sent" and not (t["state"] == "queued" and why != LITE_NO_WORD):
        return                                     # it arrived, waits its turn (word came), or was already told
    log("lite: %s: a hand-off never reached the session (#%s)" % (sid, t["id"]))
    trace(sid, "handoff", id=t["id"], step="lost", why=why)
    t["lost"] = True
    _lite_set(sid, c, t, "failed", result="never reached the session, so none of it was done")
    _lite_announce(sid)


def _lite_fresh_hello(sid, c):
    """Before the hello (the band still says Connecting): the gist being made for the latest work, waited
    for no longer: what's prepared is used at once, and a newer one comes with a next turn."""
    cc = c["cc"]                                   # what's prepared, at once: a newer one comes with a next turn
    text, at, current = lite_gist(cc)
    if text and current and text != c["voice"].get("gist"):
        c["voice"] = dict(c["voice"], gist=text, gist_at=at)
        c["voice"].pop("gist_stale", None)
        voice_file(sid, None, c["voice"])
        c["greeted"] = text
        log("lite: %s: the hello knows the latest work (a gist made %.1fs into the call)" % (sid, time.time() - c["since"]))
    elif c["voice"].get("gist_stale"):
        log("lite: %s: the hello's gist is from before the latest work (a new one wasn't ready)" % sid)


def lite_progress(b):
    """POST /api/lite/progress {session?, turnId, kind: tool|text|done, text}: how the Claude Code
    session is getting on with handed-on work, from the mod. "done" ends it, and the voice says
    how it ended when the conversation is quiet."""
    sid = b.get("session") or live_session()
    c = LITE_CALLS.get(sid) if sid else None
    if c is None:
        return {"error": "No call"}
    kind, text = b.get("kind"), " ".join(str(b.get("text") or "").split())
    if b.get("turnId"):
        c["turn"] = b["turnId"]
    if kind != "tool" or text:
        trace(sid, "handoff", id=b.get("id"), step=kind, text=None if kind == "typed" else text[:400] or None)
    t = _lite_of(c, b.get("id")) if b.get("id") else None
    if kind == "received":                         # the session has it: running from now
        if t is not None and t["state"] in ("sent", "queued"):
            t["last"] = time.time()
            _lite_set(sid, c, t, "running")
        return {"ok": True}
    if kind == "undelivered":                      # Claude Code turned it away
        _lite_lost(sid, b.get("id"), text or "refused")
        return {"ok": True}
    if kind == "queued":                           # the session is busy: Claude Code starts it once that's done
        if t is not None and t["state"] == "sent":
            log("lite: %s: a hand-off waits its turn in the session (#%s)" % (sid, t["id"]))
            _lite_set(sid, c, t, "queued")
        return {"ok": True}
    if kind == "typed":                            # they typed (or pasted) in the session's chat during the call
        t = _lite_task(c, "typed", b.get("text") or "(an image)", "running", source="typed")
        c.setdefault("typed", []).append({"text": str(b.get("text") or ""), "images": int(b.get("images") or 0)
                                          if str(b.get("images") or "0").isdigit() else 0, "at": time.time()})
        _lite_set(sid, c, t, "running", result="")
        return {"ok": True}
    now = time.time()
    working = [x for x in c.get("tasks", {}).values() if x["state"] in ("running", "stalled")]
    if kind == "tool" and text:
        c["doing"] = text[:120]
        for x in working:
            x.update(last=now, doing=text[:120])
            if x["state"] == "stalled":
                _lite_set(sid, c, x, "running")
        _lite_tasks_file(sid, c)
    elif kind == "text" and text:
        c["said"] = text
    elif kind == "done":
        # The session's turn ended: what it was on ended with it, in what it said last (it may have done one
        # step of it and stopped: that's what the record says). Queued work starts next.
        final = text or c.get("said") or ""
        c.update(doing="", said="")
        reason = b.get("reason")
        for x in working:
            _lite_set(sid, c, x, "stopped" if reason == "aborted" else "failed" if reason == "error" else "done",
                      result=final or ("stopped" if reason == "aborted" else ""))
        if working:
            _lite_announce(sid)
    return {"ok": True}


# What the voice says when its own model can't be asked: never the session's words.
LITE_FALLBACK = {"done": "That's done. It's all in the chat.", "aborted": "I stopped that.",
                 "error": "I ran into a problem with that. The details are in the chat.",
                 "undelivered": "That didn't reach the session, so it wasn't done.",
                 "queued": "That starts once the session finishes what it's on."}
_PATH = re.compile(r"(?:~|\.{0,2})/(?:[\w.@+-]+/)+[\w.@+-]+")


def _lite_announce(sid):
    """Tasks ended: the voice tells them on its own once both sides have been quiet LITE_QUIET_GAP, worded at
    that moment from the records, unless a turn of theirs has carried them first. One teller per call."""
    c = LITE_CALLS.get(sid)
    if c is None or c.get("telling"):
        return
    c["telling"] = True
    threading.Thread(target=_lite_tell, args=(sid,), name="lite-tell", daemon=True).start()


def _lite_untold(c, sid=None):
    """Ended tasks the voice hasn't had: told on its own, or carried by a turn ([TASKS] in the call's record)."""
    out = []
    for t in c.get("tasks", {}).values():
        if t["state"] in LITE_ENDED and not t["seen"]:
            if sid and t.get("line") and sbledger.carried(sid, t["line"]):
                t["seen"] = True                   # a turn of theirs carried it
                continue
            out.append(t)
    return out


def _lite_tell(sid):
    """The voice's own turn for what came back: while they talk, or a turn is answered or said, it waits (a
    turn of theirs then carries it); paused, it waits in silence and the pause shows something is waiting
    (voice_status's "waiting"); quiet a while, the voice is asked, then, still quiet, what it says is said.
    The session's own text stays in Claude Code's chat and the call's log."""
    import agentctl
    import voice as voice_mod
    c = LITE_CALLS.get(sid)
    try:
        quiet = None
        while sid in LITE_CALLS and _lite_untold(c, sid):
            if call_muted(sid):
                c["rang"], quiet = True, None      # the bell shows: they'll know it's there
            elif _lite_user_busy(sid):
                quiet = None
            else:
                quiet = quiet or time.time()
                if time.time() - quiet >= LITE_QUIET_GAP:
                    break
            time.sleep(LITE_QUIET_STEP)
        news = _lite_untold(c, sid) if sid in LITE_CALLS else []
        if not news:
            return                                 # a turn of theirs carried it
        for t in news:
            t["seen"] = True
        if c.get("hanging_up"):                    # the voice said goodbye: kept in the call's log, not said
            _lite_came_back(sid, news)
            log("lite: %s: what came back after the goodbye: kept, not said" % sid)
            return
        resumed = c.pop("rang", False)
        typed = _lite_typed_words(sid, c)
        now = time.time()
        facts = "\n".join(_lite_task_line(t, now) for t in news)
        how = "error" if any(t["state"] == "failed" for t in news) else "aborted" if all(
            t["state"] == "stopped" for t in news) else "done"
        t0 = time.time()
        trace(sid, "told", how=how, alone=True, after_pause=bool(resumed), tasks=[t["id"] for t in news])
        body = "[RESULT %s] %s%s" % (how, ("%s\n" % typed) if typed else "", facts)
        asked = "%s\n%s\n[END RESULT]" % (LITE_RESUMED, body) if resumed else body
        try:
            answer = agentctl.ask(VOICE_TAG + asked, err=None)
        except Exception as exc:
            log("lite: telling the voice how it went: %r" % exc)
            answer = None
        failed = answer is None or answer.strip() == "(stopped)" or voice_mod.spoken_error(answer) != answer
        if not failed:
            answer = answer.replace(END_TAG, "")   # news isn't the user asking to hang up
        line = LITE_FALLBACK.get(how, LITE_FALLBACK["done"]) if failed else _lite_spoken("" if answer.strip() == QUIET_TAG else answer)
        trace(sid, "told_done", how=how, ms=int((time.time() - t0) * 1000), said=line or None, failed=bool(failed))
        if sid not in LITE_CALLS or c.get("hanging_up"):
            return                                 # the call ended, or it said goodbye, while this was worded
        _lite_came_back(sid, news)
        if line and (_lite_user_busy(sid) or call_muted(sid)):
            log("lite: %s: what came back: not said, they spoke (or paused) while it was worded" % sid)
            line = ""                              # a moment that's gone: the tasks are on their next turn
            for t in news:
                t["seen"] = False
        log("lite: %s: what came back (%s, %.1fs): the voice %s" % (sid, how, time.time() - t0,
            "says it (%d words)" % len(line.split()) if line else "says nothing now"))
        if not failed:                             # the call's record: told, and what of it the voice said
            sbledger.append(sid, "asked", asked)
            sbledger.append(sid, "you", line, unsaid=not line)
        if line:
            append_chat(sid, "ai", line, voice=True)
            vs = VOICE.get(sid)
            if vs is not None:                     # not said in time (a turn came first): their next turn has the tasks
                vs.announce(line, dropped=lambda: ([t.update(seen=False) for t in news], _lite_announce(sid)))
    finally:
        if c is not None:
            c["telling"] = False
        if c is not None and sid in LITE_CALLS and _lite_untold(c, sid) and not c.get("hanging_up"):
            _lite_announce(sid)                    # more came back meanwhile


def _lite_came_back(sid, tasks):
    """What came back, in the call's log as Claude Code's (a hand-off that never got there by its work, so the
    call's summary says so)."""
    for t in tasks:
        lost = bool(t.get("lost"))
        append_chat(sid, "ai", "", voice=True, more={
            "result": (t["what"] if lost else (t["result"] or t["state"]))[:LITE_RESULT_MAX],
            "reason": "undelivered" if lost else {"failed": "error", "stopped": "aborted"}.get(t["state"], "done")})


def _lite_typed_words(sid, c):
    """What they typed or pasted in the session's chat since the voice last heard them: their words, with
    the images themselves (read from the session's own transcript), once."""
    typed, c["typed"] = c.get("typed") or [], []
    if not typed:
        return ""
    images = _lite_pasted_images(sid, c, min(x["at"] for x in typed)) if any(x["images"] for x in typed) else []
    lines = ["Typed in the chat: %s" % (" ".join(x["text"].split()) or "(an image)") for x in typed]
    return "\n".join(lines + ["[IMAGE %s]" % path for path in images])


def _lite_pasted_images(sid, c, since):
    """The images of the prompts they sent the session since `since`, saved with the call (newest 4)."""
    import base64
    path = _cc_transcript(c["cc"])
    out = []
    try:
        lines = path.read_text(errors="replace").splitlines()[-400:] if path is not None else []
    except OSError:
        lines = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        content = (r.get("message") or {}).get("content") if r.get("type") == "user" else None
        if not isinstance(content, list) or (r.get("origin") or {}).get("kind") not in (None, "human", "composer", "bridge"):
            continue
        try:
            at = datetime.datetime.strptime(r.get("timestamp", "")[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
        if at < since - 5 or at <= c.get("pasted_upto", 0):
            continue                               # before these prompts, or already given to the voice
        c["pasted_upto"] = max(c.get("pasted_upto", 0), at)
        for b in content:
            src = b.get("source") or {} if isinstance(b, dict) and b.get("type") == "image" else {}
            if src.get("type") == "base64" and src.get("data"):
                ext = {"image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(src.get("media_type"), "jpg")
                f = SESSIONS / sid / ("pasted-%d.%s" % (len(list((SESSIONS / sid).glob("pasted-*"))) + 1, ext))
                try:
                    f.write_bytes(base64.b64decode(src["data"]))
                    out.append(str(f))
                except (OSError, ValueError):
                    pass
    return out[-4:]


LITE_QUIET_STEP = 0.1


def _lite_user_busy(sid):
    """The user is talking, or their turn is being answered or said: not the moment for news of its own."""
    with _lock:
        if sid in PENDING:
            return True
    vs = VOICE.get(sid)
    st = vs.status() if vs is not None else {}
    phase = st.get("state") or "off"
    speaking = getattr(vs, "user_speaking", None) if vs is not None else None
    return phase in ("thinking", "speaking") or (phase == "listening" and bool(st.get("you"))) or bool(speaking and speaking())


def _lite_spoken(text):
    """The voice model's answer as it may be said: the spoken part (before any ---), with no
    markdown and no path, should the model have repeated one (a file by its own name)."""
    import voice as voice_mod
    text = (text or "").split("\n---", 1)[0]
    text = "\n".join(l for l in text.splitlines() if not l.strip().upper().startswith("SUGGEST:"))
    text = _PATH.sub(lambda m: m.group(0).rstrip("/").rsplit("/", 1)[-1], text)
    text = re.sub(r"[`*_#>]+", "", text)
    return voice_mod.speakable(" ".join(text.split()))


def _lite_hang_up(sid):
    """The voice said goodbye with [END]: once its goodbye has been said in full and the call
    is quiet, the call ends as End ends it (its card, the band, the window). If the user
    goes on talking first, the call stays. Work in Claude Code goes on either way."""
    t0, spoke, quiet = time.time(), False, None
    while time.time() - t0 < LITE_END_WAIT:
        if sid not in LITE_CALLS:
            return                                 # ended meanwhile
        vs = VOICE.get(sid)
        st = vs.status() if vs is not None else {"state": "off"}
        phase = st.get("state") or "off"
        # They went on: words heard while it listens, or a new turn after the goodbye. Not "you" while it
        # thinks or speaks: that's the question it's answering, shown, not someone talking.
        if (phase == "listening" and st.get("you")) or (spoke and phase == "thinking"):
            log("lite: %s: they went on talking: the call stays" % sid)
            LITE_CALLS.get(sid, {}).pop("hanging_up", None)
            return
        if phase == "speaking":
            spoke, quiet = True, None
        elif phase in ("listening", "muted", "off"):
            quiet = quiet or time.time()
            if time.time() - quiet >= 0.5 and (spoke or time.time() - t0 > 3.0):
                break
        time.sleep(0.05)
    log("lite: %s: the voice ended the call" % sid)
    trace(sid, "end", by="the voice")
    lite_call_end({"session": sid})


def lite_call_end(b):
    """POST /api/lite/call/end {session}: the call ends as End does; its voice is kept for this
    Claude Code session's next call, and its card (keep or forget) follows."""
    sid = b.get("session")
    c = LITE_CALLS.get(sid) if sid else None
    if c is None:
        return {"error": "That call has ended"}
    if sid == live_session():
        return end_call(sid)                       # its stop runs lite_ended
    lite_ended(sid)
    return {"ok": True}


def lite_ended(sid):
    """A Claude Code session's call stopped, however (End, Escape, the orb, a quiet while, Quit):
    its voice is kept for that session's next call, and the mod is told."""
    c = LITE_CALLS.pop(sid, None) if sid else None
    if c is None:
        return
    ran = voice_ran(sid)
    if ran:
        voices = _lite_voices()
        voices[c["cc"]] = ran
        LITE_VOICES.parent.mkdir(parents=True, exist_ok=True)
        write_json(LITE_VOICES, voices)
    _lite_out(c["cc"], {"call": "state", "session": sid, "phase": "ended"})
    log("lite: call %s ended%s" % (sid, ", its voice kept" if ran else ""))


def lite_mute(b):
    """POST /api/lite/mute {session, muted}: the call's mic, as Mute in voice mode."""
    sid = b.get("session")
    if sid not in LITE_CALLS:
        return {"error": "No call"}
    return voice_action(sid, "mute" if b.get("muted") else "unmute", "band")


def lite_call_live(cc):
    """This Claude Code session's call, as /api/lite/state's `live` shows it, or None."""
    for sid, c in list(LITE_CALLS.items()):
        if c["cc"] == cc and sid == live_session():
            st = voice_status(sid)
            return dict({"kind": "call", "session": sid, "started": session_info(sid).get("started"),
                         "muted": bool(st.get("muted")), "phase": st.get("state") or "off"}, **access(sid, "mic"),
                        **({"waiting": True} if st.get("waiting") else {}))
    return None


# Standby's one window, one size (the user, 2026-10-21: one layout, no wide one): narrow, at the right of
# the screen the first time, then where it was left. Opened from Claude Code (/standby window, or 3 in its
# row) it comes to the front on that session's call or meeting, above other windows while one is live.
WINDOW = (500, 720)
WINDOW_MIN = (360, 480)
SETTINGS_SIZE = (500, 640)          # the Settings window: as wide as the main one, so its rows lay out the same
SETTINGS_MIN = (400, 480)


_CC_PATHS = {}                          # a Claude Code session -> where its transcript was found
_CC_MISSES = {}                         # ... or when it last wasn't found anywhere
CC_MISS_FOR = 10.0                      # s a session with no transcript isn't looked for again


def _cc_transcript(cc):
    """A Claude Code session's own transcript (~/.claude/projects/<folder>/<id>.jsonl), or None.
    Where it was found is kept: every project folder is looked through only when it's new, or
    has moved (the navigator asks for every agent on each poll)."""
    hit = _CC_PATHS.get(cc)
    if hit is not None and hit.exists():
        return hit
    if time.time() - _CC_MISSES.get(cc, 0) < CC_MISS_FOR:
        return None
    found = next(iter((Path.home() / ".claude" / "projects").glob("*/%s.jsonl" % cc)), None)
    if found is not None:
        _CC_PATHS[cc] = found
        _CC_MISSES.pop(cc, None)
    else:
        _CC_MISSES[cc] = time.time()
    return found


# A Claude Code session's name, as Claude Code shows it: the user's own (/rename), else Claude's
# title, else the last prompt; "" while it has none. Read from the transcript's tail, kept until the
# transcript changes (the navigator asks for every session each poll).
_CC_TITLES = {}


def cc_title(cc):
    path = _cc_transcript(cc) if cc else None
    try:
        st = path.stat() if path is not None else None
    except OSError:
        st = None
    if st is None:
        return ""
    mark = (str(path), st.st_mtime, st.st_size)
    hit = _CC_TITLES.get(cc)
    if hit and hit[0] == mark:
        return hit[1]
    custom = ai = prompt = None
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, st.st_size - 262144))
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = r.get("type") if isinstance(r, dict) else None
        if t == "custom-title" and custom is None:
            custom = r.get("customTitle")
        elif t == "ai-title" and ai is None:
            ai = r.get("aiTitle")
        elif t == "last-prompt" and prompt is None:
            prompt = r.get("lastPrompt")
        if custom:
            break
    title = " ".join(str(custom or ai or prompt or "").split())[:80]
    _CC_TITLES[cc] = (mark, title)
    return title


# What the Claude Code session has been working on, about a page, for the voice of a call started
# from it (standby-agent cc-gist): made in the background when a call starts, never waited for,
# and kept per session until the session moves on. A call uses the one on hand at once (its voice
# knows it from the start); one that's newer is given at the voice's next turn.
LITE_GISTS = ROOT / "lite" / "gists"
_GIST_LOCK = threading.Lock()
_GISTS_MAKING = set()


def lite_gist(cc):
    """(gist, made at, still current) for a Claude Code session, as cached: ("", None, False) if none."""
    g = read_json(LITE_GISTS / ("%s.json" % cc))
    path = _cc_transcript(cc)
    mark = path.stat().st_size if path is not None and path.exists() else None
    text = g.get("text") if isinstance(g.get("text"), str) else ""
    return text, g.get("at"), bool(text) and mark is not None and g.get("mark") == mark


def lite_gist_refresh(cc, then=None):
    """Make the session's gist again if it moved on since the cached one (in the background:
    `then(gist)` once it's made and new). One at a time per session."""
    _text, _at, current = lite_gist(cc)
    path = _cc_transcript(cc)
    if current or path is None:
        return False
    with _GIST_LOCK:
        if cc in _GISTS_MAKING:
            return False
        _GISTS_MAKING.add(cc)

    def make():
        t0 = time.time()
        try:
            mark = path.stat().st_size
            r = subprocess.run(transport.for_config().agent_cmd(["cc-gist", "--transcript", str(path)]),
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150)
            text = (r.stdout or "").strip()
            if text:
                LITE_GISTS.mkdir(parents=True, exist_ok=True)
                write_json(LITE_GISTS / ("%s.json" % cc), {"text": text, "at": time.time(), "mark": mark})
                log("lite: %s's gist made in %.1fs (%d words)" % (cc[:8], time.time() - t0, len(text.split())))
                if then is not None:
                    then(text)
            else:
                log("lite: %s's gist: nothing made (%s)" % (cc[:8], (r.stderr or "").strip()[-160:] or "empty"))
        except Exception as exc:
            log("lite: %s's gist: %r" % (cc[:8], exc))
        finally:
            with _GIST_LOCK:
                _GISTS_MAKING.discard(cc)
    threading.Thread(target=make, name="lite-gist", daemon=True).start()
    return True


def _cc_folder(cc):
    """The folder a Claude Code session runs in, from its own transcript (~/.claude/projects): found
    where _cc_transcript keeps it, never every project folder looked through again."""
    for f in filter(None, [_cc_transcript(cc)]):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh):
                    if n > 200:
                        break
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and isinstance(d.get("cwd"), str):
                        return d["cwd"]
        except OSError:
            pass
    return None


WINDOW_FILE = ROOT / "window.json"         # where the window was left


# Frames are macOS's own: [x, y, width, height] in global screen points, y from the bottom, as
# NSWindow.frame() has them. pywebview's move() counts from the window's current screen, so with a
# second display (an external one above the MacBook, say) its numbers put the window off the edge.
def _visible_frames():
    return window.visible_frames()


def _on_a_screen(f, screens=None):
    """The frame's top strip (where it's dragged from) is on one of the Mac's screens: a frame
    saved on a display that's gone, or off an edge, would open a window that can't be reached."""
    try:
        screens = _visible_frames() if screens is None else screens
        px, py = f[0] + 60, f[1] + f[3] - 12
        return any(sx <= px <= sx + sw and sy <= py <= sy + sh for sx, sy, sw, sh in screens)
    except Exception:
        return True


def _frame(key, default):
    f = read_json(WINDOW_FILE).get(key)
    ok = isinstance(f, list) and len(f) == 4 and all(isinstance(x, (int, float)) for x in f)
    return f if ok and _on_a_screen(f) else default


def _working_screen():
    """The visible part of the screen the person is working on (the one with the pointer)."""
    return window.working_screen()


def _window_default():
    try:
        x, y, w, h = _working_screen()
        height = min(WINDOW[1], h - 24)
        return [x + w - WINDOW[0] - 24, y + h - height - 12, WINDOW[0], height]
    except Exception:
        return [40, 120, WINDOW[0], WINDOW[1]]


def _save_frame(win, key="window"):
    try:
        f = window.frame_of(getattr(win, "native", None))
        frame = list(f) if f is not None else [win.x, win.y, win.width, win.height]
        d = read_json(WINDOW_FILE)
        d[key] = frame
        write_json(WINDOW_FILE, d)
    except Exception:
        pass


def _to_front(w):
    """Standby and its window in front, with the keyboard: opened from VS Code or a terminal,
    the window would otherwise come up behind the app the person clicked in."""
    window.to_front(w.native, log)


def _redraw(win):
    """The window's web view laid out and drawn again at the window's size (after a show and a resize)."""
    window.redraw(win, log)


def main_page(settings=False):
    """The page, with this launch's token: the main window's, or the Settings window's (settings), which
    opens Settings at once and runs nothing else of the app (no Standby view, no polling)."""
    page = PAGE.replace("__SB_SETWIN__", "1" if settings else "0")
    if settings:
        page = page.replace('<script src="/agents.js"></script>', "")
    return page.replace("__SB_TOKEN__", TOKEN)


# Settings in a window of its own: one, made the first time it's opened and hidden when it's closed, so
# opening it again brings it to the front. Neither the main window nor the mini one moves for it: a call
# or a meeting stays where it is.
SETTINGS_WIN = {}                  # "win": the Settings window, once made
SETTINGS_PANES = ("call", "recording", "dictation", "usage")
_SETTINGS_LOCK = threading.Lock()


def _settings_default():
    """The first time: centred on the screen the person is working on."""
    try:
        x, y, w, h = _working_screen()
        width, height = SETTINGS_SIZE[0], min(SETTINGS_SIZE[1], h - 24)
        return [x + (w - width) / 2.0, y + (h - height) / 2.0, width, height]
    except Exception:
        return [80, 120, SETTINGS_SIZE[0], SETTINGS_SIZE[1]]


def open_settings_window(pane=None):
    """Standby's Settings in its own window, on `pane` ("call") or at its list: made the first time, in
    front after. {ok}, or {error} with no window to open it in (Standby running without one)."""
    pane = pane if pane in SETTINGS_PANES else None
    if APP_WINDOW.get("win") is None:
        return {"error": "Settings needs the Standby app open"}
    if threading.current_thread() is threading.main_thread():
        # pywebview makes a window only from another thread once it's running (and evaluate_js waits on this one)
        threading.Thread(target=open_settings_window, args=(pane,), name="settings-window", daemon=True).start()
        return {"ok": True}
    import webview
    with _SETTINGS_LOCK:
        win = SETTINGS_WIN.get("win")
        if win is None:
            api = SettingsApi()
            url = "http://127.0.0.1:%d/settings?token=%s%s" % (PORT, TOKEN, "&pane=" + pane if pane else "")
            win = webview.create_window("Standby Settings", url, js_api=api, width=SETTINGS_SIZE[0],
                                        height=SETTINGS_SIZE[1], min_size=SETTINGS_MIN)
            api.window = win
            SETTINGS_WIN["win"] = win
            _dress_settings(win, api)
            log("settings: its window, opened%s" % (" on %s" % pane if pane else ""))
            return {"ok": True}
    win.show()
    _to_front(win)
    win.evaluate_js("showSettings(%s)" % json.dumps(pane))      # its values fresh, at that pane or the list
    log("settings: its window, in front%s" % (" on %s" % pane if pane else ""))
    return {"ok": True}


def _dress_settings(win, api):
    """The Settings window as the main one: no title bar (the page's header drags it, its corner kept
    clear of the traffic lights), where it was left, and closing only hides it."""
    def lights(on):
        js = "document.documentElement.classList.%s('lights')" % ("add" if on else "remove")
        threading.Thread(target=win.evaluate_js, args=(js,), daemon=True).start()

    def before_show():
        try:
            api.titlebar = window.titlebar(win.native, on_fullscreen=lambda full: lights(not full))
        except Exception as exc:
            log("settings: kept the standard title bar (%r)" % exc)
        try:
            window.place(win.native, _frame("settings", _settings_default()))
        except Exception as exc:
            log("settings: couldn't place its window (%r)" % exc)
    win.events.before_show += before_show
    win.events.loaded += lambda: api.titlebar is not None and lights(not api.titlebar.fullscreen())

    def closing():
        if SETTINGS_WIN.get("quitting"):
            return True
        _save_frame(win, "settings")
        win.hide()
        return False                             # kept: the next open is at once, where it was
    win.events.closing += closing


def close_settings_window():
    """The app's own window really closes (Standby quits): the hidden Settings window goes with it."""
    win = SETTINGS_WIN.get("win")
    if win is not None:
        SETTINGS_WIN["quitting"] = True
        try:
            win.destroy()
        except Exception:
            pass


def open_window(b):
    """POST /api/window {cc, session?}: Standby focused on that Claude Code session's call or meeting
    (what's live there, else its last), by its session id, in front: an ordinary window, never kept above
    other apps (only the orb is). {ok} or {error} in words."""
    import lite as lite_mod
    cc, sid = str(b.get("cc") or ""), b.get("session")
    if not cc and isinstance(sid, str) and valid_id(sid):
        cc = lite_mod.cc_of(addmac.bound(SESSIONS / sid)) or ""
    if not lite_mod.CC_ID.match(cc):
        return {"error": "Open Standby from Claude Code: /standby window"}
    win = APP_WINDOW.get("win")
    if win is None:
        return {"error": "Standby's window needs the Standby app open"}
    call, meeting = lite_call_live(cc), lite()._live_of(cc)[0]
    if call or meeting:
        focus = {"session": call["session"] if call else meeting, "kind": "call" if call else "meeting", "live": True}
    else:
        last = lite().last_of(cc)
        focus = dict(last, live=False) if last else None      # none ever: the past meetings and calls
    win.show()
    _to_front(win)
    _redraw(win)
    js = "window.mxFocusSession&&mxFocusSession(%s)" % json.dumps(focus)
    threading.Thread(target=win.evaluate_js, args=(js,), daemon=True).start()
    log("Standby for Claude Code session %s: focused" % cc[:8])
    return {"ok": True}


def lite_orb(b):
    """POST /api/lite/orb {cc}: this Claude Code session's call into the orb in the corner, as the window's
    orb button does (Api.to_orb). Calls only: never a meeting."""
    if lite_call_live(str(b.get("cc") or "")) is None:
        return {"error": "The mini window is for a call, and none is on"}
    api = APP_WINDOW.get("api")
    if api is None:
        return {"error": "The mini window needs the Standby app open"}
    return api.to_orb()


_meeting_state = LITE_GET["/api/lite/state"]


def _lite_state(q):
    out = _meeting_state(q)
    if isinstance(out, dict) and not out.get("live") and not out.get("error"):
        out["live"] = lite_call_live(str(q.get("cc") or ""))
    return out


LITE_GET["/api/lite/state"] = _lite_state


# After a call, its summary card on Claude Code's band offers "Add to Claude Code": a note of what the
# call discussed that the session doesn't know yet (it only ever had what was handed to it, and its own
# replies), written then by standby-agent call-summary, goes into that session, once.
_ADDING = threading.Lock()


def lite_card_add(b):
    """POST /api/lite/card/add {session}: the call's note into the Claude Code session it came from.
    {ok}, {ok, already} (it went before), or {error} in words."""
    import lite as lite_mod
    sid = b.get("session")
    data = addmac.bound(SESSIONS / sid) if isinstance(sid, str) and valid_id(sid) else {}
    if not lite_mod.is_lite(data) or data.get("kind") != "call":
        return {"error": "That isn't a call from Claude Code"}
    first, cc = call_first(sid), lite_mod.cc_of(data)
    with _ADDING:                                  # a double click: the second finds it added
        card = read_json(SESSIONS / first / "card.json")
        if not card:
            return {"error": "That call's summary isn't ready yet"}
        if card.get("added_at"):
            return {"ok": True, "already": True}
        if not any(c == cc for c, _q in lite().subs):
            return {"error": "That Claude Code session isn't open"}
        words = _card_text(call_sessions(first), "Voice", me="User")
        try:
            r = subprocess.run(transport.for_config().agent_cmd(["call-summary", "--session", first]), input=words,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150)
        except Exception as exc:
            log("lite: %s: call summary: %r" % (first, exc))
            r = None
        if r is None or r.returncode != 0:
            return {"error": "Couldn't summarize that call: try again"}
        said = (r.stdout or "").strip()
        if not said:
            return {"error": "There was nothing new for Claude Code in that call"}
        start = float(card.get("at") or session_info(first).get("started") or time.time())
        when = "%s-%s" % (time.strftime("%H:%M", time.localtime(start)),
                          time.strftime("%H:%M", time.localtime(start + float(card.get("duration_s") or 0))))
        text = "[Standby · call summary · %s]\n%s" % (when, said)
        if not lite().publish(cc, {"call": "summary", "session": first, "text": text}):
            return {"error": "That Claude Code session isn't open"}
        card["added_at"] = time.time()
        write_json(SESSIONS / first / "card.json", card)
    log("lite: %s: its summary added to Claude Code session %s" % (first, cc[:8]))
    return {"ok": True}


# Claude starting a call or listening by itself (its tools, not the user's /standby): it rings on Claude
# Code's band (Accept, Decline, Always accept), and nothing starts without the user's Accept, unless they
# chose Always accept (Settings: auto_accept). The plugin raises it, shows it, and waits here for the
# answer in one held request (a hook's own time is short: a wait inside a request costs it nothing);
# unanswered, it expires, as Decline.
INCOMING_WAIT = 30.0
INCOMING = {}                                  # id -> {id, kind, cc, at, expires_at, answer, expired?, done}
_INCOMING_LOCK = threading.Lock()


def _incoming_due(now=None):
    """Requests past their time: declined; settled ones long gone, forgotten."""
    now = now or time.time()
    with _INCOMING_LOCK:
        for i, r in list(INCOMING.items()):
            if r["answer"] is None and now >= r["expires_at"]:
                r.update(answer="decline", expired=True)
                r["done"].set()
                log("lite: Claude's %s request %s: no answer, so nothing started" % (r["kind"], r["id"]))
            elif r["answer"] is not None and now - r["at"] > 300:
                INCOMING.pop(i, None)


def lite_incoming(b):
    """POST /api/lite/incoming {cc, kind: call | listen}: Claude asks to start one. {answer: "accept"} at once
    when the user said Always accept; else {id}, to wait on for the answer the band gives."""
    import lite as lite_mod
    cc, kind = str(b.get("cc") or ""), b.get("kind")
    if not lite_mod.CC_ID.match(cc) or kind not in ("call", "listen"):
        return {"error": "No such request"}
    if transport.load_config().get("auto_accept"):
        return {"answer": "accept"}
    now = time.time()
    r = {"id": uuid.uuid4().hex[:12], "kind": kind, "cc": cc, "at": now, "expires_at": now + INCOMING_WAIT,
         "answer": None, "done": threading.Event()}
    with _INCOMING_LOCK:
        INCOMING[r["id"]] = r
    log("lite: Claude asks to start a %s (%s): the band asks the user" % (kind, r["id"]))
    return {"id": r["id"]}


def lite_incoming_get(q):
    """GET /api/lite/incoming?id=[&wait=s]: that request's answer, None while it waits. With wait, it's held
    until the answer comes or the request expires (at most that long). Without an id: the request waiting
    now, {incoming: {id, kind, at, expires_at} | null}."""
    _incoming_due()
    if q.get("id"):
        with _INCOMING_LOCK:
            r = INCOMING.get(q["id"])
        if r is None:
            return {"error": "No such request"}
        try:
            wait = max(0.0, min(float(q.get("wait") or 0), INCOMING_WAIT + 1))
        except (TypeError, ValueError):
            wait = 0.0
        if wait and r["answer"] is None:
            r["done"].wait(max(0.0, min(wait, r["expires_at"] - time.time() + 0.1)))
            _incoming_due()
        return dict({"answer": r["answer"]}, **({"expired": True} if r.get("expired") else {}))
    with _INCOMING_LOCK:
        waiting = sorted((r for r in INCOMING.values() if r["answer"] is None), key=lambda r: r["at"])
        r = waiting[-1] if waiting else None
    return {"incoming": {k: r[k] for k in ("id", "kind", "at", "expires_at")} if r else None}


def lite_incoming_answer(b):
    """POST /api/lite/incoming/answer {id, answer: accept | decline | always}: the band's answer. Always
    accept saves the setting (Claude starts them without asking from now on) and accepts this one."""
    _incoming_due()
    answer = b.get("answer")
    if answer not in ("accept", "decline", "always"):
        return {"error": "Accept, decline or always"}
    with _INCOMING_LOCK:
        r = INCOMING.get(b.get("id"))
        if r is None or r["answer"] is not None:
            return {"error": "That request has expired"}
        r["answer"] = "decline" if answer == "decline" else "accept"
    if answer == "always":
        cfg = transport.load_config()
        cfg["auto_accept"] = True
        transport.save_config(cfg)
    r["done"].set()
    log("lite: Claude's %s request %s: %s" % (r["kind"], r["id"], {"always": "always accept, saved"}.get(answer, answer)))
    return {"ok": True}


LITE_GET["/api/lite/incoming"] = lite_incoming_get
LITE_POST.update({"/api/lite/call": lite_call, "/api/lite/progress": lite_progress,
                  "/api/lite/call/end": lite_call_end, "/api/lite/mute": lite_mute,
                  "/api/lite/card/add": lite_card_add, "/api/lite/incoming": lite_incoming,
                  "/api/lite/incoming/answer": lite_incoming_answer, "/api/lite/orb": lite_orb})


# ---------------------------------------------------------------- the voice voice mode speaks in

# Where macOS's voices are downloaded: Accessibility > Read & Speak on macOS 26 (the
# Accessibility pane keeps its old id), Spoken Content before it.
SPOKEN_CONTENT = "x-apple.systempreferences:com.apple.preference.universalaccess?AX_FEATURE_SPOKENCONTENT"
SPOKEN_CONTENT_OLD = "x-apple.systempreferences:com.apple.preference.universalaccess?SpeakableItems"
AX_ANCHORS = Path("/System/Library/ExtensionKit/Extensions/AccessibilitySettingsExtension.appex"
                  "/Contents/Resources/en.lproj/UniversalAccess.searchTerms")


def spoken_content_url(anchors=AX_ANCHORS):
    """The Read & Speak anchor if this macOS's Accessibility pane lists it
    (its search terms are keyed by anchor), else the one from before."""
    import plistlib
    try:
        with open(str(anchors), "rb") as fh:
            if "AX_FEATURE_SPOKENCONTENT" in plistlib.load(fh):
                return SPOKEN_CONTENT
    except Exception:
        pass
    return SPOKEN_CONTENT_OLD

REGIONS = {"US": "US", "GB": "UK", "AU": "Australia", "IE": "Ireland", "IN": "India", "ZA": "South Africa",
           "NZ": "New Zealand", "CA": "Canada", "SG": "Singapore"}


def voices_info():
    """Settings > Voice: Standby's own voices (when installed) and the
    installed English macOS ones, the one chosen (None: automatic, Alba, else
    the best macOS one), the one that speaks now, whether a Premium or
    Enhanced macOS voice is there at all, and the credit Pocket TTS asks for."""
    import tts
    import voice
    voices = [dict(v, region=REGIONS.get(v["lang"].split("-")[-1], v["lang"].split("-")[-1]))
              for v in voice.english_voices()]
    chosen = (transport.load_config().get("voice_mode") or {}).get("voice") or None
    standby = tts.voices() if tts.available() else []
    neural = tts.voice_for(chosen, installed=bool(standby))
    current = (next(v for v in standby if v["id"] == "pocket:" + neural) if neural else
               voice.voice_in_use(voices, None if str(chosen or "").startswith("pocket:") else chosen))
    info = {"current": current, "chosen": chosen, "voices": voices, "standby": standby,
            "better": any(v["quality"] != "standard" for v in voices), "credit": tts.CREDIT if standby else ""}
    preparing, pct = _voice_preparing()
    if preparing:                          # Standby's voice is first downloading, for Try or voice mode
        info["preparing"], info["preparing_pct"] = preparing, pct
    trying = try_status()
    if trying:
        info["trying"] = trying
    return info


def _voice_preparing():
    """Standby's voice getting ready (its first download), as the voice screen
    says it, and how far (0-99, None: not known): from Try's download, or
    voice mode's."""
    import tts
    p = TRY.get("proc")
    if p is not None:
        said = tts.getting_ready(p.state, p.downloading, p.done, p.total)
        if said:
            return said, tts.getting_ready_pct(p.state, p.downloading, p.done, p.total)
    for vs in list(VOICE.values()):
        if vs.state != "off":
            st = vs.status()
            if st.get("preparing"):
                return st["preparing"], st.get("preparing_pct")
    return "", None


def voices_action(b):
    """{id}: the voice from its next sentence, a call in progress too (None: automatic again).
    {"action": "try", id}: a sample line in that voice, here and now, the
    answer when it has been said. {"action": "get"}: System Settings, where
    Premium voices are downloaded."""
    import voice
    info = voices_info()
    ids = {v["id"]: v for v in info["voices"] + info["standby"]}
    if b.get("action") == "get":
        subprocess.run(OPEN + [spoken_content_url()], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        return {"ok": True}
    if b.get("action") == "try":
        v = ids.get(b.get("id")) or info["current"]
        if v.get("quality") == "standby" and not (os.environ.get("STANDBY_SAY") or os.environ.get("STANDBY_NO_DEVICES")):
            return {"ok": True, "trying": try_standby(v)}       # in the background: the page follows it
        why = _try_voice(v)
        return {"error": why} if why else {"ok": True, "tried": v["name"]}
    vid = b.get("id") or None
    if vid is not None and vid not in ids:
        return {"error": "that voice is not installed"}
    cfg = transport.load_config()
    vm = cfg.setdefault("voice_mode", {})
    if vid:
        vm["voice"] = vid
    else:
        vm.pop("voice", None)
    transport.save_config(cfg)
    log("voice mode's voice: %s" % (vid or "automatic"))
    return {"ok": True, "voices": voices_info()}


def _try_voice(v):
    """One line in voice `v`, said before returning; why not, if it could not
    be. Tests have a stand-in for `say` (STANDBY_SAY), and never the speakers."""
    line = "Hi, I'm %s. This is how I'll sound in voice mode." % v["name"]
    if v.get("quality") == "standby":
        return None                # in a test: Standby's own voice runs in the background otherwise (try_standby)
    fake = os.environ.get("STANDBY_SAY")
    if fake:
        subprocess.run([fake] + (["-v", v["name"]] if v.get("id") else []) + ["-f", "-"], input=line,
                       text=True, capture_output=True, timeout=30)
        return
    if os.environ.get("STANDBY_NO_DEVICES"):
        return
    done = threading.Event()

    def speak():
        try:
            from native import voices as os_voices
            os_voices.say(v.get("id"), line, 15.0)
        except Exception as exc:
            log("voice: try: %r" % exc)
        finally:
            done.set()
    threading.Thread(target=speak, daemon=True).start()
    done.wait(20)


# Try in one of Standby's voices: its first time is a 240 MB download, about a
# minute, more than a request should wait. So Try starts it and returns; the page
# follows it here (GET /api/voices, "trying") and it plays by itself once ready.
TRY = {}                 # the one Try going on, or just over: see try_status
TRY_LOCK = threading.Lock()
TRY_GONE = 6.0           # Settings not asking this long: it was closed, so the line is not played
TRY_KEEP = 8.0           # an ended Try is still told this long, for the page to show how it went
TRY_SAY = 90.0           # the line itself may take this long (held while a busy Mac makes it)


def try_status():
    """Try's state for the page: {n, voice, state, pct, left, error, downloaded}.
    state: starting, downloading (pct, left: seconds, None until known), loading,
    playing, done, failed (error: a short reason; downloaded: it was downloading),
    skipped (ready after Settings closed, or another voice was picked meanwhile)."""
    with TRY_LOCK:
        if not TRY or (TRY["state"] in ("done", "failed", "skipped") and time.time() - TRY["ended"] > TRY_KEEP):
            return None
        TRY["seen"] = time.time()
        return {k: TRY[k] for k in ("n", "voice", "state", "pct", "left", "error", "downloaded")}


def _time_left(seen, done, total):
    """Seconds left of a download, from its rate over the last 5 s of (time,
    bytes) samples; None until 3 s of them, while nothing moves, or at its very
    end (the count stops a byte short until it is all in): no wrong guess."""
    now = seen[-1][0] if seen else 0
    recent = [x for x in seen if now - x[0] <= 5.0]
    if not recent or now - recent[0][0] < 3.0 or done >= total - 1:
        return None
    rate = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
    return int(round((total - done) / rate)) if rate > 0 else None


def try_standby(v):
    """Starts Try in Standby's voice `v`, unless one is going on: then that one
    goes on (one download at a time). Returns its state."""
    with TRY_LOCK:
        if TRY and TRY["state"] in ("starting", "downloading", "loading", "playing"):
            pass
        else:
            TRY.clear()
            TRY.update(n=int(time.time() * 1000), voice=v["name"], state="starting", pct=None, left=None,
                       error=None, downloaded=False, seen=time.time(), ended=0.0, proc=None)
            threading.Thread(target=_try_standby, args=(v, TRY["n"]), name="voice-try", daemon=True).start()
    return try_status()


def _short_reason(error):
    """Why Standby's voice could not start, in a few words for Settings (the log has all of it)."""
    e = str(error or "")
    if any(w in e for w in ("Connection", "connection", "resolve", "Timeout", "LocalEntryNotFound", "offline", "Max retries")):
        return "no connection to Hugging Face"
    if "No space" in e or "Errno 28" in e:
        return "the disk is full"
    e = e.split(": ", 1)[-1].strip() or "no reason given"
    return e if len(e) <= 80 else e[:77].rstrip() + "…"


def _try_set(n, **kw):
    with TRY_LOCK:
        if TRY.get("n") == n:
            TRY.update(kw)
            if kw.get("state") in ("done", "failed", "skipped"):
                TRY["ended"] = time.time()


def _try_standby(v, n):
    """The line in one of Standby's voices: its process for just as long, then
    gone. The first time, it downloads first, telling how far."""
    import tts
    line = "Hi, I'm %s. This is how I'll sound in voice mode." % v["name"]
    out, proc = tts.speakers(log=log), tts.VoiceProcess(v["id"].split(":", 1)[1], log=log)
    _try_set(n, proc=proc)
    seen = []
    try:
        proc.open()
        speech = tts.Speech(proc, out, log=log)
        while not proc.ready and proc.state != "failed":
            if proc.state == "downloading" and proc.total:
                seen.append((time.time(), proc.done))
                _try_set(n, state="downloading", downloaded=True, left=_time_left(seen, proc.done, proc.total),
                         pct=tts.getting_ready_pct(proc.state, True, proc.done, proc.total))
            elif proc.state == "loading":
                _try_set(n, state="loading", pct=None, left=None)
            time.sleep(0.1)
        if not proc.ready:
            log("voice: try: Standby's voice did not start (%s)" % (proc.error or "no reason given"))
            _try_set(n, state="failed", error=_short_reason(proc.error), downloaded=proc.downloading)
            return
        with TRY_LOCK:
            gone = time.time() - TRY.get("seen", 0) > TRY_GONE
        if gone or voices_info()["current"]["id"] != v["id"]:
            # Downloaded for next time; nobody is there to hear it, or they chose another voice meanwhile.
            log("voice: try: ready after %s: not played" % ("Settings closed" if gone else "another voice was picked"))
            _try_set(n, state="skipped")
            return
        _try_set(n, state="playing", pct=None, left=None)
        out.open()
        speech.say(line)
        end = time.time() + TRY_SAY
        while speech.busy and time.time() < end:
            time.sleep(0.05)
        _try_set(n, state="done")
    except Exception as exc:
        log("voice: try: %r" % exc)
        _try_set(n, state="failed", error=_short_reason(repr(exc)))
    finally:
        _try_set(n, proc=None)
        proc.close()
        out.close()


def voice_status(sid):
    """The voice's state, and on a paused call whether something waits to be said ("waiting": a red dot on
    the orb, the window's Resume and the band's)."""
    vs = VOICE.get(sid)
    st = vs.status() if vs is not None else {"state": "off"}
    if sid in LITE_CALLS:                          # the call's tasks, for the windows' busy cue
        st = dict(st, tasks=lite_task_list(sid))
    return dict(st, waiting=True) if lite_waiting(sid, st) else st


def lite_waiting(sid, st):
    """A paused call with what came back held for Resume: news (a task ended), never bookkeeping."""
    c = LITE_CALLS.get(sid)
    return bool(st.get("muted")) and c is not None and bool(_lite_untold(c, sid))


def stop_voice():
    for vs in list(VOICE.values()):
        vs.stop(by_app=True)            # the recording is being stopped: a talk is not ended twice


# ---------------------------------------------------------------- the companion

COMPANION_PAGE = HERE / "companion.html"       # the companion page, served as it is
COMPANION_MISSING = ('<!doctype html><meta charset="utf-8"><body style="margin:0;background:transparent">'
                     '<!-- mac/companion.html is not there -->')
_LIVE_SEEN = {"at": 0.0, "sid": None}


def _live_now(ttl=0.5):
    """The live session, looked up at most twice a second: the companion polls
    about eight times a second in voice mode, and each look is a pgrep."""
    now = time.time()
    if now - _LIVE_SEEN["at"] > ttl and not _off_main(_LIVE_SEEN, lambda: _LIVE_SEEN.update(at=time.time(), sid=live_session())):
        _LIVE_SEEN.update(at=now, sid=live_session())
    return _LIVE_SEEN["sid"]


live_session_recent = _live_now        # what the timeline asks: it looks many times a second


def _off_main(seen, refresh):
    """On the main thread (the orb asks every second), a look that is merely stale is
    answered from the last one, and taken again on a thread of its own: a pgrep or a
    folder scan never runs where AppKit draws. True when it did that. A look that was
    cleared (at 0: something just changed) is taken where it is asked, so it's right."""
    if not seen["at"] or threading.current_thread() is not threading.main_thread():
        return False
    if not seen.get("busy"):
        seen["busy"] = True

        def run():
            try:
                refresh()
            finally:
                seen["busy"] = False
        threading.Thread(target=run, name="look", daemon=True).start()
    return True


def capture_answer(b):
    """An answer to the recorder's question about what to record:
    {id, value}. Only to the question being asked now, with one of its answers;
    the recorder acts on it and keeps it, so it is not asked again."""
    live = _live_now()
    ask = read_json(SESSIONS / live / "capture.ask.json") if live else {}
    if not ask or ask.get("id") != b.get("id"):
        return {"error": "That question has gone."}
    if b.get("value") not in [a.get("value") for a in ask.get("actions") or []]:
        return {"error": "That is not one of its answers."}
    path = SESSIONS / live / "capture.answer"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": ask["id"], "value": b["value"]}))
    tmp.replace(path)
    log("capture: answered")
    return {"ok": True}


def companion_state():
    """Everything the orb shows, small enough to poll often: the live call (its agent,
    when it started, a mic macOS refuses), a start under way, and its voice's state.
    A meeting has no orb: while one records, live is null."""
    live = _live_now()
    out = {"live": None, "starting": None, "voice": {"state": "off"},
           "claude_error": claude_error(page_machine(live))}           # live known: no second lookup
    if live and (SESSIONS / live).is_dir() and is_talk(live):
        sdir = SESSIONS / live
        out["live"] = {"id": live, "kind": "talk"}
        info = _call_info(live)
        if info.get("agent"):                                          # the orb names the agent
            # a call from Claude Code: its session's own name (its /rename, else its title), not "Claude Code"
            name = (str(info["agent"]).startswith("lite:") and cc_title(str(info["agent"])[5:])) or info.get("name") or "Claude"
            out["live"]["call"] = {"agent": info["agent"], "name": name,
                                   "copy": bool(info.get("copy")), "started": _call_info(call_first(live)).get("started")}
        # macOS refuses the mic: the recorder's source for it says cause "permission" (see POST /api/mic/permission)
        out["live"]["mic_denied"] = any(src.get("kind") == "mic" and src.get("cause") == "permission"
                                        for src in read_json(sdir / "live.json").get("sources") or [])
        out["voice"] = voice_status(live)
    with _lock:
        if STARTING["state"] == "starting":
            out["starting"] = {"kind": STARTING.get("kind") or "meeting"}
    return out


class CompanionHooks(object):
    token = TOKEN                         # the mini window loads its page with it (companion.py)

    """What the companion's panel (companion.Presence) asks of the app."""

    def live_kind(self):
        """What is recording: "meeting", "talk" (a call), or None. Asked every second (the
        lookups are cached): the orb is for a call only, and Quit asks while a meeting records."""
        sid = _live_now()
        if not sid:
            return None
        if sid in LITE_CALLS and sid not in LITE_ORB:
            return None                  # a call from Claude Code: its band says it; the orb only when asked for
        return read_json(SESSIONS / sid / "meta.json").get("kind") or "meeting"

    def orb_asked(self):
        """The orb only once the user sends a call to it (Mini window, the corner button,
        Cmd+M): a call starting never brings it up, and a meeting never has one."""
        sid = _live_now()
        return bool(sid) and sid in LITE_ORB

    def ask_orb(self):
        """Minimised to the orb (Cmd+M, the corner button): the orb carries this call from now on.
        Never a meeting: the orb is for calls only."""
        sid = _live_now()
        if sid and is_talk(sid):
            LITE_ORB.add(sid)

    def recording(self):
        """Is anything recording now? Not for a paused meeting: Quit needn't ask about it
        (quitting pauses, and it is paused already)."""
        return bool(_live_now())

    def stop_recording(self):
        stop_voice()
        threading.Thread(target=stop_session, daemon=True).start()     # it waits for the recorder

    def open_settings(self, pane=None):
        """The mini window's Settings: the Settings window, on that pane; the call stays in the mini window."""
        return open_settings_window(pane)

    def stop_all(self):
        """Quit (asked first while a meeting records): nothing records once Standby is gone."""
        stop_voice()
        if live_session():
            log("quit while recording: stopping the recording")
            stop_session(detach=True)

    def log(self, msg):
        log(msg)


def maybe_title(sid, listener=False):
    """Once a live session has enough transcript and no title, ask the agent
    for one (a single cheap call). A title the user typed always wins.
    listener: a meeting another Claude Code session listens to (Standby inside Claude Code),
    whose own Claude is off: titled the same way, from its transcript as it is here."""
    if sid in TITLED:
        return
    info = session_info(sid)
    if (info["title_source"] != "default" or info["lines"] < TITLE_AFTER_LINES or not (info["claude"] or listener)
            or info["kind"] == "talk"):           # titled from its chat instead
        return
    TITLED.add(sid)

    def run():
        try:
            t = transport.for_config()
            if listener:                       # nothing pushed it to the agent's side: the titler reads it there
                local = SESSIONS / sid / "transcript.jsonl"
                t.write("live/%s.jsonl" % sid, local.read_bytes() if local.exists() else b"")
            r = subprocess.run(t.agent_cmd(["title", "--session", sid]), stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            title = (r.stdout or "").strip().splitlines()
            if title and title[0] and not (SESSIONS / sid / "title.txt").exists():
                (SESSIONS / sid / "title.auto.txt").write_text(title[0][:80] + "\n")
                log("titled %s" % sid)
        except Exception as exc:
            log("title failed: %r" % exc)
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- what the meeting's Claude taught dictation
#
# The user's idea (2026-10-09): the session's own Claude, which knows the
# domain, lists what the speech recognition misheard, at the end of each meeting
# and talk and every 30 minutes in a long meeting. Its fixes, guarded, go into the
# word list dictation uses, and the session's view and exports show them fixed;
# the saved transcript keeps what was heard.

LEARN_EVERY = 30 * 60          # s: a pass this often in a long meeting
LEARN_MIN_LINES = 20           # a meeting with less to go by isn't asked; a talk needs two questions
LEARN_MIN_NEW = 50             # nor asked again mid-meeting before this many more lines
LEARN_POLL = 10.0              # s between the watcher's looks
LEARN_AFTER_END = 60.0         # s after a session ends, so a Summarize asked at once goes first
LEARNED = "learned.json"       # in the session's folder: {"at", "size", "words": [[heard, meant]...]}
_LEARNING = set()              # sessions with a pass under way
_LEARN_SEEN = {"live": None, "ended": {}}      # ended: {sid: when it ended}, waiting for its last pass
_FIXERS = {}


def words_file():
    import dictation as dct
    return dct.WORDS


def fixer():
    """The words file's fixes, for showing transcript text (vocabulary.Fixer)."""
    path = str(words_file())
    if path not in _FIXERS:
        _FIXERS[path] = vocabulary.Fixer(path)
    return _FIXERS[path]


def learned_words(sid):
    data = read_json(SESSIONS / sid / LEARNED)
    return data if isinstance(data, dict) else {}


def learn_size(sid, info):
    """How much a pass has to go by: a meeting's transcript lines, a talk's questions."""
    if info["kind"] != "talk":
        return info["lines"]
    return sum(1 for m in read_jsonl(SESSIONS / sid / "chat.jsonl") if m.get("role") == "me")


def learn_due(sid, why, now=None):
    """Whether to ask: Claude on (with it off, nothing ever left the Mac, and
    asking would send it all), enough said, something new since the last pass,
    and mid-meeting only every LEARN_EVERY."""
    now = time.time() if now is None else now
    if sid in _LEARNING:
        return False
    info = session_info(sid)
    if not info["claude"]:
        return False
    size, done = learn_size(sid, info), learned_words(sid)
    if size < (2 if info["kind"] == "talk" else LEARN_MIN_LINES) or size <= int(done.get("size") or 0):
        return False
    if why == "end":
        return True
    return (info["kind"] != "talk" and now - float(done.get("at") or info["started"] or now) >= LEARN_EVERY
            and size - int(done.get("size") or 0) >= LEARN_MIN_NEW)


def learn_words(sid, why):
    """One pass, in the session's own conversation: vocabulary.WORDS_QUESTION,
    asked like a typed question but never shown as one. The fixes it lists,
    guarded (a lone everyday word never alone), go to the words file; what was
    learned is kept with the session, for the quiet line in its chat. The log
    has counts only, never the words."""
    _LEARNING.add(sid)
    try:
        import agentctl
        info = session_info(sid)
        size = learn_size(sid, info)
        answer = agentctl.ask(WORDS_TAG + vocabulary.WORDS_QUESTION, err=None, session=sid) or ""
        kept, dropped = vocabulary.guard_terms(vocabulary.parse_terms(answer))
        written = vocabulary.add_learned(words_file(), kept)
        done = learned_words(sid)
        words = list(done.get("words") or [])
        words += [[h, m] for h, m in written if [h, m] not in words]
        tmp = SESSIONS / sid / (LEARNED + ".tmp")
        tmp.write_text(json.dumps({"at": time.time(), "size": size, "words": words}, ensure_ascii=False))
        tmp.replace(SESSIONS / sid / LEARNED)
        log("learned for dictation from %s (%s): %d new, %d left out (an everyday word alone, or not a spelling fix)"
            % (sid, why, len(written), len(dropped)))
    except Exception as exc:
        log("learning words from %s: %r" % (sid, exc))
    finally:
        _LEARNING.discard(sid)


def learn_tick(now=None, start=None):
    """The watcher's look: a session that ended gets its pass LEARN_AFTER_END
    later, once no question of the user's is under way in it (theirs go first),
    and a long live meeting one every LEARN_EVERY. start(fn, *args) runs a
    pass (a thread)."""
    now = time.time() if now is None else now
    start = start or (lambda fn, *a: threading.Thread(target=fn, args=a, daemon=True).start())
    live, was, ended = live_session(), _LEARN_SEEN["live"], _LEARN_SEEN["ended"]
    _LEARN_SEEN["live"] = live
    if was and was != live:
        ended[was] = now
    for sid, at in list(ended.items()):
        with _lock:
            asking = sid in PENDING
        if now - at < LEARN_AFTER_END or asking:
            continue
        del ended[sid]
        if learn_due(sid, "end", now):
            _LEARNING.add(sid)
            start(learn_words, sid, "end")
    if live and learn_due(live, "periodic", now):
        _LEARNING.add(live)
        start(learn_words, live, "periodic")


def _learn_words_forever():
    while True:
        time.sleep(LEARN_POLL)
        try:
            learn_tick()
        except Exception as exc:
            log("learning words: %r" % exc)


def fixed_rows(rows):
    """Transcript rows as shown: the word fixes applied to their text, what was
    heard kept beside it when it changed."""
    fx, out = fixer(), []
    for r in rows:
        text = r.get("text")
        fixed = fx.apply(text) if isinstance(text, str) else text
        out.append(dict(r, text=fixed, heard=text) if fixed != text else r)
    return out


def rename_session(sid, title):
    title = (title or "").strip()
    if not title:
        return {"error": "empty title"}
    (SESSIONS / sid / "title.txt").write_text(title[:120] + "\n")
    return {"ok": True}


def rename_speaker(sid, spk, name):
    spk = str(spk).strip()
    if not re.match(r"^(\d+|me)$", spk):
        return {"error": "bad speaker id"}
    sdir = SESSIONS / sid
    data = read_json(sdir / "speakers.names.json")
    names = data.get("names", {})
    name = (name or "").strip()[:40]
    if name:
        names[spk] = name
    else:
        names.pop(spk, None)
    blob = json.dumps({"names": names})
    (sdir / "speakers.names.json").write_text(blob)
    if name and spk != "me":
        remember_voice(sid, spk, name)
    if claude_on(sid):   # so the agent uses the new name too
        try:
            transport.for_config().write("live/%s.speakers.names.json" % sid, blob)
        except Exception as exc:
            log("speaker names not delivered to agent: %r" % exc)
    return {"ok": True}


def remember_voice(sid, spk, name):
    """Teach the voice library: this speaker's voice is `name`, so later
    sessions can recognize them. Only names the user gives teach it."""
    if not get_settings()["remember_voices"]:
        return
    state = read_json(SESSIONS / sid / "speakers.state.json")
    alias, cent, count = state.get("alias", {}), state.get("cent", {}), state.get("count", {})
    key, seen = str(spk), 0
    while key in alias and seen < 20:
        key, seen = str(alias[key]), seen + 1
    v = cent.get(key)
    if not v:
        log("voices: no fingerprint yet for speaker %s in %s" % (spk, sid))
        return
    unit = lambda x: [a / (math.sqrt(sum(b * b for b in x)) or 1.0) for a in x]
    v, n = unit(v), int(count.get(key, 1))
    with _lock, voices_lock():
        lib = read_json(VOICES)
        voices = lib.setdefault("voices", {})
        old = voices.get(name)
        if old and len(old.get("emb", [])) == len(v):
            w = int(old.get("n", 1))
            v, n = unit([a * w + b * n for a, b in zip(old["emb"], v)]), w + n
        voices[name] = {"emb": v, "n": n, "updated": time.time()}
        tmp = VOICES.with_suffix(".tmp")
        tmp.write_text(json.dumps(lib))
        os.chmod(str(tmp), 0o600)
        tmp.replace(VOICES)
    log("voices: remembered a voice (%d utterances)" % n)


def voices_lock():
    """voices.json is written by the recorder too (the user's own voice, myvoice.py):
    each takes this lock around a change, so neither loses the other's."""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        with open(str(VOICES) + ".lock", "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
    return held()


def forget_voices(me_only=False):
    """All remembered voices, the user's own with them; or only the user's own (me_only)."""
    with _lock, voices_lock():
        if me_only:
            lib = read_json(VOICES)
            if lib.pop("me", None) is not None:
                tmp = VOICES.with_suffix(".tmp")
                tmp.write_text(json.dumps(lib))
                os.chmod(str(tmp), 0o600)
                tmp.replace(VOICES)
        else:
            try:
                VOICES.unlink()
            except OSError:
                pass
    log("voices: forgot %s" % ("the user's own voice" if me_only else "all remembered voices"))
    return {"ok": True}


def live_status(sid):
    """What the recorder hears now: level, speech in progress, partial words."""
    st = read_json(SESSIONS / sid / "live.json")
    if st:
        st["age"] = round(time.time() - st.get("at", 0), 1)
        if st.get("sources"):
            st["sources"] = with_screen_why(st["sources"])
    return st


def mic_muted(sid):
    """The user's mic is muted for this meeting (the recorder has closed it)."""
    return bool(sid) and _read_text(SESSIONS / sid / "mic").split()[:1] == ["muted"]


def set_mic(sid, muted):
    """Mute the user's mic for the meeting, or not. The recorder closes it (and
    opens it again): the call and the rest go on, and the clock runs on. Not while
    talking to Claude: that needs the mic."""
    if sid != live_session():
        return {"error": "the mic can be muted in the live meeting only"}
    if not session_info(sid)["mic"]:
        return {"error": "this session is not recording a microphone"}
    if is_talk(sid):
        return {"error": "a talk is all voice: end it instead"}
    path = SESSIONS / sid / "mic"
    if muted:
        vs = VOICE.get(sid)
        if (vs is not None and vs.state != "off") or _read_text(SESSIONS / sid / "dictate").split()[:1] == ["on"]:
            return {"error": "Finish talking to Claude first"}
        path.write_text("muted\n")
    else:
        try:
            path.unlink()
        except OSError:
            pass
    log("mic: %s for %s" % ("muted" if muted else "unmuted", sid))
    return {"ok": True, "mic_muted": bool(muted)}


def set_source(sid, source, on):
    """One of the live meeting's sources on or off, from its window's list (live.json's
    "hearing"). The mic's switch is its mute. An app off is not recorded until it is on
    again or the meeting ends: the recorder reads sources.off ({app_key: name}), in the
    meeting's own folder, so the next meeting starts with everything on."""
    if not valid_id(sid or "") or sid != live_session():
        return {"error": "Only the live meeting's sources can be switched"}
    if source == "mic":
        r = set_mic(sid, not on)
        return {"ok": True} if r.get("ok") else r
    path = SESSIONS / sid / "sources.off"
    off = read_json(path)
    off = off if isinstance(off, dict) else {}
    hearing = read_json(SESSIONS / sid / "live.json").get("hearing") or []
    name = next((h.get("name") for h in hearing if h.get("id") == source), None) or off.get(source)
    if not name:
        return {"error": "Standby isn't hearing that any more"}
    if on:
        off.pop(source, None)
    else:
        off[source] = name
    write_json(path, off)
    log("source: %s %s for %s" % (name, "on" if on else "off", sid))
    return {"ok": True}


MUTED_LINE = "Your mic is muted. Unmute it to talk to Claude"


def get_prompt():
    """Claude's instructions, read from wherever the agent runs."""
    t = transport.for_config()
    try:
        r = subprocess.run(t.agent_cmd(["prompt"]), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return json.loads(r.stdout)
    except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
        return {"error": "Could not read the instructions from %s. Check that it answers: New meeting shows its checks. (%s)"
                % (t.label, exc.__class__.__name__)}


def save_prompt(b):
    t = transport.for_config()
    if b.get("reset"):
        t.remove("prompt.md")
        log("instructions reset to default")
        return {"ok": True}
    text = b.get("text") or ""
    if not text.strip():
        return {"error": "The instructions are empty. Use Reset to default instead."}
    if not t.write("prompt.md", text):
        return {"error": "Could not save the instructions to %s." % t.label}
    log("instructions saved (%d chars)" % len(text))
    return {"ok": True}


MICTEST = {"state": None}


def start_mic_test():
    """Record a few seconds from the configured inputs and transcribe them."""
    if live_session() or STARTING["state"] == "starting":
        return {"error": "Stop the recording first. While recording, the live transcript is the test."}
    with _lock:
        if MICTEST["state"] == "running":
            return {"error": "A test is already running."}
        MICTEST["state"] = "running"
    threading.Thread(target=_run_mic_test, daemon=True).start()
    return {"started": True}


def _run_mic_test():
    s = get_settings()
    try:
        with open(ROOT / "mic-test.log", "w") as out:
            p = subprocess.Popen([CLI, "selftest", "--mode", s["capture"], "--seconds", "5"],
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                 env=dict(os.environ, PYTHONUNBUFFERED="1"))
            try:
                p.wait(timeout=180)
            except subprocess.TimeoutExpired:
                p.kill()
    except Exception as exc:
        log("mic test failed: %r" % exc)
    with _lock:
        MICTEST["state"] = "done"


def mic_test_state():
    """The test's progress, per channel: level, what was heard, or the problem."""
    text = _read_text(ROOT / "mic-test.log") if MICTEST["state"] else ""
    chans, out = [], {"state": MICTEST["state"], "channels": [], "error": ""}
    for ln in text.splitlines():
        m = re.match(r"--- (\w+) \(device (.*)\): recording", ln)
        if m:
            chans.append({"channel": m.group(1), "device": m.group(2), "peak": None,
                          "heard": [], "problem": []})
            continue
        if not chans or ln.startswith("[standby]"):
            continue
        c = chans[-1]
        m = re.match(r"\s+peak level: ([\d.]+)", ln)
        if m:
            c["peak"] = float(m.group(1))
            continue
        m = re.match(r"\s+\d\d:\d\d\s+(.*)", ln)
        if m:
            c["heard"].append(m.group(1))
        elif ln.startswith("  ") and ln.strip():
            c["problem"].append(ln.strip())
    out["channels"] = chans
    if MICTEST["state"] == "done" and not chans:
        out["error"] = "\n".join(l for l in text.splitlines() if l.strip())[-600:] or "The test did not run."
    return out


def said_row(m):
    """A chat row that was said, as the window shows it: not a result row (what work brought back is never
    shown; its "text" is empty), not an older call's "[HANDOFF]" marker, not the app's old stock "I'm on it."
    on a hand-off, and nothing empty: they came out as blank "Claude:" lines (talk-20261023-200116)."""
    text = (m.get("text") or "").strip()
    if not text or text.startswith("[HANDOFF]"):
        return False
    return not (m.get("handoff") and text == "I'm on it.")


def export_text(sid, fmt="md"):
    """The whole session as text: title, transcript with speakers, and chat."""
    sys.path.insert(0, str(HERE.parent / "plugin" / "bin"))
    import sbspeakers
    info = session_info(sid)
    sp = speakers(sid)
    rows = fixed_rows(read_jsonl(SESSIONS / sid / "transcript.jsonl"))       # as shown: the word fixes applied
    chat = read_jsonl(SESSIONS / sid / "chat.jsonl")
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(info["started"]))
    lines = ["# %s" % info["title"], "", "_%s_" % started, ""] if fmt == "md" else [info["title"], started, ""]
    if info["kind"] != "talk":               # a talk has no transcript, only its chat
        lines += ["## Transcript", ""] if fmt == "md" else ["TRANSCRIPT", ""]
    for r in rows:
        t = int(r.get("t", 0))
        who = sbspeakers.label(r, sp["alias"], dict(sp["known"], **sp["names"]))
        ts = "%02d:%02d" % (t // 60, t % 60)
        if fmt == "md":
            lines.append("**%s**%s %s  " % (ts, (" " + who + ":") if who else "", r.get("text", "")))
        else:
            lines.append("%s %s%s" % (ts, (who + ": ") if who else "", r.get("text", "")))
    chat = [m for m in chat if said_row(m)]
    if chat:
        lines += (["", "## Chat with Claude" if fmt == "md" else "CHAT", ""] if info["kind"] != "talk" else [])
        for m in chat:
            role = "You" if m.get("role") == "me" else "Claude"
            n = len(m.get("snaps") or ([1] if m.get("snap") else []))
            text = m.get("text", "") + (" (snapshot attached)" if n == 1 else " (%d snapshots attached)" % n if n else "")
            if m.get("files"):
                text += " (attached: %s)" % ", ".join(f.get("name") or "a file" for f in m["files"])
            lines.append(("**%s:** %s" if fmt == "md" else "%s: %s") % (role, text))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def copy_to_clipboard(text):
    try:
        subprocess.run(["pbcopy"], input=text, text=True, encoding="utf-8", errors="replace", timeout=10, check=True)
        return {"ok": True}
    except Exception as exc:
        return {"error": "clipboard unavailable (%s)" % exc.__class__.__name__}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, body, ctype, code=200, extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"):        # never framed by another page
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj), "application/json", code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(min(n, MAX_BODY)) or b"{}")
        except ValueError:
            return {}

    def _gate(self):
        """This request may go on: its Host is this server's own (127.0.0.1 or localhost, on its port), it
        carries this launch's token, and its body isn't too large. Else it's answered here: 403, 401 or 413."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
            self._json({"error": "forbidden host"}, 403)
            return False
        path, _, query = self.path.partition("?")
        if path not in OPEN_PATHS:
            given = self.headers.get(TOKEN_HEADER) or urllib.parse.parse_qs(query).get("token", [""])[-1]
            if not hmac.compare_digest(str(given).encode("utf-8"), TOKEN.encode("utf-8")):
                self._send(json.dumps({"error": "this needs Standby's token"}), "application/json", 401,
                           {"X-Standby": "1"})
                return False
        try:
            if int(self.headers.get("Content-Length") or 0) > MAX_BODY:
                self._json({"error": "too large"}, 413)
                return False
        except ValueError:
            self._json({"error": "bad length"}, 400)
            return False
        return True

    def _lite_stream(self, cc):
        """GET /api/lite/stream?cc=: the Lite mod's live events, one JSON line each, until it goes."""
        import lite as lite_mod
        if not lite_mod.CC_ID.match(str(cc or "")):
            return self._json({"error": "No such Claude Code session"})
        q = lite().subscribe(cc)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b'{"hello": true}\n')
            self.wfile.flush()
            for frame in lite_mod.Lite.frames(q):
                self.wfile.write(frame)
                self.wfile.flush()
        except (OSError, ValueError):
            pass                                      # the mod went away
        finally:
            lite().unsubscribe(q)
            self.close_connection = True

    def _events(self):
        """GET /api/msg/events: the page's event stream (Server-Sent Events), until it goes."""
        m = messenger()
        q = m.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            import messenger as mm
            self.wfile.write(mm.sse("hello", {"at": time.time()}))
            self.wfile.flush()
            for frame in mm.frames(q):
                self.wfile.write(frame)
                self.wfile.flush()
            log("messenger stream: a page fell behind: told to reload")
        except (OSError, ValueError):
            pass                                      # the page went away
        finally:
            m.hub.unsubscribe(q)
            self.close_connection = True

    def do_GET(self):
        qos.interactive()                          # the orb and the window, answered on a busy Mac too
        if not self._gate():
            return
        with watchdog.Request("GET " + self.path.partition("?")[0]):
            return self._get()

    def do_POST(self):
        qos.interactive()
        if not self._gate():
            return
        with watchdog.Request("POST " + self.path.partition("?")[0]):
            self.path = self.path.partition("?")[0] if "token=" in self.path else self.path
            return self._post()

    def _get(self):
        path, _, query = self.path.partition("?")
        if path == "/api/lite/stream":
            return self._lite_stream({k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}.get("cc"))
        if path.startswith("/api/lite/"):
            return self._json(lite_get(path, {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}))
        if path == "/api/msg/events":
            return self._events()
        if path.startswith("/api/msg/"):
            return self._json(messenger_get(path, {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}))
        if path == "/":
            return self._send(main_page(), "text/html; charset=utf-8")
        if path == "/settings":                   # the Settings window's: the same page, Settings alone
            return self._send(main_page(settings=True), "text/html; charset=utf-8")
        if path in ("/agents.js", "/agents.css"):   # Standby's view, beside today's page
            try:
                body = (HERE / path[1:]).read_text()       # read each time: an edit shows on reload
            except OSError:
                body = ""
            return self._send(body, "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
        if path == "/companion":
            try:
                page = COMPANION_PAGE.read_text()       # read each time: a page edit shows on reload
            except OSError:
                page = COMPANION_MISSING
            return self._send(page.replace("__SB_TOKEN__", TOKEN), "text/html; charset=utf-8")
        if path == "/api/companion":
            return self._json(companion_state())
        if path == "/api/whoami":
            return self._json(whoami())
        if path == "/api/state":
            live = _live_now()
            if live:
                maybe_title(live)
            with _lock:
                starting = dict(STARTING) if STARTING["state"] else None
            return self._json({"sessions": list_sessions(live), "live": live,
                               "starting": starting, "agent": agent_label(),
                               "claude_error": claude_error(page_machine(live)), "mic_muted": mic_muted(live)})
        if path.startswith("/api/session/"):
            sid = path.rsplit("/", 1)[1]
            if not valid_id(sid):
                return self._json({"error": "no such session"}, 404)
            pending = pending_shown(sid)
            chat = read_jsonl(SESSIONS / sid / "chat.jsonl")
            info = session_info(sid)
            # The folder chip follows a /cd in Claude Code: only here, for the one
            # open session, never in the list (that is one lookup per session).
            info["workspace"] = session_folder(sid, info["workspace"], info["joined"])
            return self._json({"info": info, "chat": chat,
                               "transcript": fixed_rows(transcript_rows(SESSIONS / sid / "transcript.jsonl")),
                               "learned": learned_words(sid).get("words") or [],
                               "speakers": speakers(sid), "pending": pending,
                               "suggest": suggest_for(sid, chat), "claude_error": claude_error(page_machine(sid))})
        if path.startswith("/api/export/"):
            sid = path.rsplit("/", 1)[1]
            if not valid_id(sid):
                return self._json({"error": "no such session"}, 404)
            fmt = "txt" if "fmt=txt" in query else "md"
            name = "%s.%s" % (slug(session_info(sid)["title"]), fmt)
            return self._send(export_text(sid, fmt), "text/plain; charset=utf-8",
                              extra={"Content-Disposition": 'attachment; filename="%s"' % name})
        if path.startswith("/api/live/"):
            sid = path.rsplit("/", 1)[1]
            if not valid_id(sid):
                return self._json({"error": "no such session"}, 404)
            return self._json(dict(live_status(sid) or {}, **access(sid, live_capture(sid))) if sid == live_session() else {})
        if path.startswith("/api/voice/"):
            sid = path.rsplit("/", 1)[1]
            if not valid_id(sid):
                return self._json({"error": "no such session"}, 404)
            return self._json(voice_status(sid))
        if path == "/api/screen/targets/icon":
            bundle = dict(kv.partition("=")[::2] for kv in query.split("&") if kv).get("app", "")
            data = snap_app_icon(bundle) if BUNDLE_ID.match(bundle) else None
            if data is None:
                return self._json({"error": "no icon"}, 404)
            return self._send(data, "image/png", extra={"Cache-Control": "max-age=3600"})
        if path == "/api/settings":
            return self._json({"settings": dict(get_settings(), mic_default=mic_default()), "mics": list_mics()})
        if path == "/api/settings/named":
            return self._json(settings_named())
        if path == "/api/voices":
            return self._json(voices_info())
        if path == "/api/speech":                     # Settings polls a download's progress
            return self._json(speech_status())
        if path == "/api/models":
            return self._json(models_info())
        if path == "/api/usage":
            m = re.search(r"(?:^|&)range=([\w]+)", query)
            return self._json(usage_info(m.group(1) if m else "today"))
        if path == "/api/prompt":
            return self._json(get_prompt())
        if path == "/api/mic-test":
            return self._json(mic_test_state())
        if path == "/api/dictation-app":
            return self._json(dictation_app())
        return self._json({"error": "not found"}, 404)

    def _post(self):
        # Refuse cross-site requests: only this page may drive the recorder.
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin and origin not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
            return self._json({"error": "forbidden origin"}, 403)
        if self.path == "/api/stall":                  # the page's own timer fired late
            return self._json(watchdog.page_stall(self._body()))
        if self.path == "/api/window":
            return self._json(open_window(self._body()))
        if self.path.startswith("/api/lite/") and self.path in LITE_POST:
            return self._json(LITE_POST[self.path](self._body()))
        if self.path.startswith("/api/msg/") and self.path in MSG_POST:
            return self._json(MSG_POST[self.path](self._body()))
        b = self._body()
        sid = b.get("id")
        routes_with_id = ("/api/rename", "/api/speaker", "/api/copy", "/api/claude", "/api/voice", "/api/mic")
        if self.path in routes_with_id and not valid_id(sid):
            return self._json({"error": "no such session"}, 404)
        if self.path == "/api/screen/permission":
            ok, why = _snap_available()
            state, reason = screen_permission(bool(b.get("confirm"))) if ok else ("unavailable", why)
            out = dict({"state": state, "reason": reason}, **({"confirm": SNAP_WARNING} if state == "confirm" else {}))
            log("screen permission: %s" % state)
            return self._json(out)
        if self.path == "/api/mic/permission":         # the mic refused: System Settings, at Microphone
            return self._json(mic_permission())
        if self.path == "/api/screen/reset":           # its own entry only, on the user's click
            return self._json(screen_reset())
        if self.path == "/api/capture/answer":
            return self._json(capture_answer(b))
        if self.path == "/api/start" and TEST_ROUTES:      # tests only (test_ui): meetings start from Claude Code
            return self._json(start_session(workspace=b.get("workspace")))
        if self.path == "/api/msg/memory":
            return self._json(card_memory(b.get("session"), bool(b.get("keep"))))
        if self.path == "/api/msg/call/end":
            return self._json(end_call(b.get("session")))
        if self.path in ("/api/msg/mac/pause", "/api/msg/mac/resume", "/api/msg/mac/end"):
            fn = {"pause": mac_pause, "resume": mac_resume, "end": mac_end}[self.path.rsplit("/", 1)[1]]
            return self._json(fn(b.get("session")))
        if self.path == "/api/rename":
            return self._json(rename_session(sid, b.get("title")))
        if self.path == "/api/speaker":
            return self._json(rename_speaker(sid, b.get("spk"), b.get("name")))
        if self.path == "/api/claude":
            return self._json(set_claude(sid, bool(b.get("on"))))
        if self.path == "/api/voice":
            return self._json(voice_action(sid, b.get("action"), b.get("from")))
        if self.path == "/api/mic":                  # mute my mic for the meeting
            return self._json(set_mic(sid, bool(b.get("muted"))))
        if self.path == "/api/live/source":          # one source heard on or off, for this meeting
            return self._json(set_source(b.get("session"), b.get("id"), bool(b.get("on"))))
        if self.path == "/api/prompt":
            return self._json(save_prompt(b))
        if self.path == "/api/forget-voices":
            return self._json(forget_voices(me_only=bool(b.get("me"))))
        if self.path == "/api/copy-text":
            return self._json(copy_to_clipboard(str(b.get("text") or "")))
        if self.path == "/api/mic-test":
            return self._json(start_mic_test())
        if self.path == "/api/dictation-app":
            return self._json(dictation_action(b))
        if self.path == "/api/copy":
            return self._json(copy_to_clipboard(export_text(sid, "txt")))
        if self.path == "/api/settings":
            return self._json(set_named(b["name"], b.get("value")) if "name" in b else set_settings(b))
        if self.path == "/api/voices":
            return self._json(voices_action(b))
        return self._json({"error": "not found"}, 404)


class Api(object):
    """Bridge for the native window (pywebview js_api)."""

    def open_settings(self, pane=None):
        """The page's Settings (its gear, ⋯ menu, Cmd-comma): in the Settings window."""
        return open_settings_window(pane)

    def __init__(self):
        self.window = None
        self.titlebar = None                     # titlebar.Titlebar, once the window has one
        self.companion = None                    # companion.Presence, once it is up

    def window_drag(self):
        """A press in one of the page's drag regions: macOS drags the window."""
        if self.titlebar is not None:
            window.on_main(self.titlebar.drag)       # pywebview calls this on a thread of its own

    def window_zoom(self):
        """A double-click there: zoom, minimise or nothing, as System Settings says."""
        if self.titlebar is not None:
            window.on_main(self.titlebar.zoom)

    def to_orb(self):
        """The focused call's minimize to orb: a call from Claude Code shows no orb until asked for; asked,
        the window goes and the orb carries the call (a click on it brings the window back)."""
        sid = _live_now()
        if sid and is_talk(sid):                 # the orb is for calls only: never a meeting
            LITE_ORB.add(sid)
        return self.to_corner()

    def to_corner(self):
        """The header's ⇲: into the corner, as Cmd+M, while a session is live."""
        if self.companion is None:
            return {"error": "no companion"}
        window.on_main(self.companion.to_corner)
        return {"ok": True}

    def quit(self):
        """The page's Quit, after it asked whether to stop recording and quit."""
        if self.companion is not None:
            window.on_main(self.companion.quit_now)
        else:
            window.quit_app()
        return {"ok": True}

    def open_url(self, url):
        """Links in answers and documents open in the browser (a mail link in Mail), not inside the app window."""
        if isinstance(url, str) and re.match(r"^(https?://|mailto:)", url):
            subprocess.run(["open", url], stdin=subprocess.DEVNULL, timeout=10)
            return {"ok": True}
        return {"error": "not a web link"}

    def save(self, sid, fmt):
        import webview
        if not valid_id(sid):
            return {"error": "no such session"}
        name = "%s.%s" % (slug(session_info(sid)["title"]), fmt)
        kind = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
        path = self.window.create_file_dialog(kind, save_filename=name)
        if not path:
            return {"cancelled": True}
        path = path[0] if isinstance(path, (list, tuple)) else path
        Path(path).write_text(export_text(sid, fmt))
        return {"ok": True, "path": path}


# Whose server is on the port: Standby answers /api/whoami with this, and its folder's mark,
# so another Standby (another checkout, its own folder) or another app (AnkiConnect also
# uses 8765) is never taken for this one.
WHOAMI = "standby"
PORT_FILE = ROOT / "ui.port"          # the port this Standby serves on, for `standby app`




class SettingsApi(Api):
    """The Settings window's bridge: the main window's, and the window hiding itself."""

    def settings_close(self):
        """The dialog closed in the Settings window: the window goes (hidden, kept for the next open)."""
        win = self.window
        if win is not None:
            _save_frame(win, "settings")
            win.hide()
        return {"ok": True}

def root_mark():
    """This Standby's folder, as a mark that doesn't show the path."""
    import hashlib
    return hashlib.sha256(str(ROOT.resolve()).encode("utf-8")).hexdigest()[:16]


def whoami():
    """GET /api/whoami: this is Standby, and which one."""
    return {"app": WHOAMI, "root": root_mark()}


TOKEN_FILE = ROOT / "ui.token"       # this launch's token, for Standby's own clients (owner only)


def write_token():
    """~/.standby/ui.token: this launch's token, readable by its owner only, swapped in whole."""
    tmp = TOKEN_FILE.with_name(".ui.token.%d" % os.getpid())
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(TOKEN + "\n")
        os.replace(str(tmp), str(TOKEN_FILE))
        # and as a header line, for curl's -H @file (the mod's stream): never on a command line, where ps shows it
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("%s: %s\n" % (TOKEN_HEADER, TOKEN))
        os.replace(str(tmp), str(TOKEN_FILE.with_name("ui.header")))
    except OSError as exc:
        log("the token file: %r" % exc)


def port_owner(port, timeout=1.5):
    """Who answers on 127.0.0.1:port: "this" (this Standby), "standby" (another one), or
    "other" (another app, or nothing that answers like Standby)."""
    import urllib.error
    import urllib.request
    try:
        mine = TOKEN_FILE.read_text().strip()     # the one serving now, if it's this Standby's
    except OSError:
        mine = ""
    req = urllib.request.Request("http://127.0.0.1:%d/api/whoami" % port, headers={TOKEN_HEADER: mine})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return "standby" if exc.code == 401 and exc.headers.get("X-Standby") == "1" else "other"
    except Exception:
        return "other"
    if not isinstance(got, dict) or got.get("app") != WHOAMI:
        return "other"
    return "this" if got.get("root") == root_mark() else "standby"


class LocalServer(ThreadingHTTPServer):
    """The app's server on 127.0.0.1, bound without a name lookup: http.server asks the network for
    127.0.0.1's full name (socket.getfqdn), which on a Mac offline, on a captive Wi-Fi or a VPN waited
    about 35 s before the app could start (measured on GitHub's hosted Macs)."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def _bind():
    """The server on PORT, or None when this same Standby already serves there. Another app,
    or another Standby, on PORT: the next free port instead, said in the log (PORT moves)."""
    global PORT
    try:
        return LocalServer(("127.0.0.1", PORT), Handler)
    except OSError:
        owner = port_owner(PORT)
        if owner == "this":
            return None
    first = PORT
    for port in list(range(first + 1, first + 50)) + [0]:
        try:
            server = LocalServer(("127.0.0.1", port), Handler)
        except OSError:
            continue
        PORT = server.server_address[1]
        log("port %d is taken by %s: serving on %d instead" % (
            first, "another Standby" if owner == "standby" else "another app", PORT))
        return server
    raise OSError("no free port")


def serve():
    """The server, or None when this same Standby already serves (reuse it)."""
    SESSIONS.mkdir(parents=True, exist_ok=True)
    server = _bind()
    if server is None:
        return None
    try:
        PORT_FILE.write_text("%d\n" % PORT)
    except OSError:
        pass
    write_token()
    log("serving http://127.0.0.1:%d (local only, with its token)" % PORT)
    messenger().start()                # the agents' transcripts, read as they grow
    threading.Thread(target=_mac_sweep_forever, name="mac-sweep", daemon=True).start()
    return server


def adopt_login_path():
    """Opened from Finder, the app gets launchd's bare PATH, so `claude` (and
    the node it may need) is not found. Borrow the login shell's PATH."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        r = subprocess.run([shell, "-ilc", 'printf "__SBPATH__%s" "$PATH"'], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log("login PATH: not read (%s)" % exc.__class__.__name__)
        return
    found = r.stdout.rpartition("__SBPATH__")[2].strip().split(":") if "__SBPATH__" in r.stdout else []
    have = os.environ.get("PATH", "").split(":")
    extra = [p for p in found if p and p not in have]
    if extra:
        os.environ["PATH"] = ":".join(have + extra)
    log("login PATH: +%d entries" % len(extra))


def main():
    window_mode = "--window" in sys.argv
    cfg = transport.load_config()
    pinned, was = cfg.get("mic_device"), cfg.get("mode")
    if cfg and (transport.migrate_mic(cfg) | transport.migrate_capture(cfg)   # once: Automatic from the start
                | transport.migrate_voice_model(cfg)):                            # once: voice mode on Haiku
        transport.save_config(cfg)
        if pinned:
            log("mic: %r was pinned; Automatic now, following the Mac's mic (once)" % pinned)
        if cfg.get("mode") != was:
            log("capture: %s -> %s (once: the Mac's sound joins when an app plays)" % (was, cfg["mode"]))
    speech_migrate()               # a stored speech model that is no longer offered (once)
    if window_mode:
        adopt_login_path()
    try:
        server = serve()
    except OSError as exc:
        log("no port to serve on: %s" % exc)
        return 1
    if server is None:           # this Standby already serves there (an earlier launch): its window
        log("port %d: this Standby is already running there, reusing it" % PORT)
    if server is not None:                    # the one app that serves: one watcher, one pass per session
        if not window_mode:                   # with a window, it watches the main thread too (after_start)
            watchdog.Watchdog(log).start()
        threading.Thread(target=_learn_words_forever, name="learn-words", daemon=True).start()
    if not window_mode:
        if server is None:
            return 1
        if sys.stdout.isatty():               # in a browser: the page needs this launch's token (never in the log)
            print("Standby: open http://127.0.0.1:%d/?token=%s" % (PORT, TOKEN), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if server is not None:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    import webview
    api = Api()
    # Opened by Claude Code for a call or a meeting (`open -g ... --args --background`): no window until the
    # user asks for it (the band's Full window, the Dock icon); the band shows the call (the user, 2026-10-27)
    background = "--background" in sys.argv
    if background:
        log("started in the background, by Claude Code: the window opens when asked for")
    win = webview.create_window("Standby", "http://127.0.0.1:%d/?token=%s" % (PORT, TOKEN), js_api=api,
                                width=WINDOW[0], height=WINDOW[1], min_size=WINDOW_MIN, hidden=background)
    api.window = win
    APP_WINDOW["win"], APP_WINDOW["api"] = win, api

    def lights(on):
        """The page keeps their corner clear while the traffic lights are there.
        evaluate_js waits for the main thread, so never from it."""
        js = "document.documentElement.classList.%s('lights')" % ("add" if on else "remove")
        threading.Thread(target=win.evaluate_js, args=(js,), daemon=True).start()

    def hide_title():
        # Before the window first shows (pywebview runs this on the main thread),
        # so the old title bar never flashes. Without it, the standard one stays.
        try:
            api.titlebar = window.titlebar(win.native, on_fullscreen=lambda full: lights(not full))
        except Exception as exc:
            log("title bar: kept the standard one (%r)" % exc)
    win.events.before_show += hide_title

    def place_first():
        # where it was left (the first time, at the right of the screen), before it first shows
        try:
            window.place(win.native, _frame("window", _window_default()))
        except Exception as exc:
            log("Standby's window: couldn't place it (%r)" % exc)
    win.events.before_show += place_first
    win.events.loaded += lambda: api.titlebar is not None and lights(not api.titlebar.fullscreen())

    present = {}

    def on_closing():
        # With the companion, close while recording shrinks into it, and Quit
        # stops everything (companion.Presence). Without it, as always: closing
        # stops the recording, so nothing records out of sight.
        p = present.get("companion")
        _save_frame(win)
        if p is not None and p.closing() is False:
            return False
        close_settings_window()                  # the window really goes: the hidden Settings one with it
        stop_voice()
        if live_session():
            log("window closed while recording: stopping the recording")
            stop_session(detach=True)
    win.events.closing += on_closing

    def start_companion():
        try:
            present["companion"] = api.companion = window.companion(win, "http://127.0.0.1:%d" % PORT, CompanionHooks())
            log("companion: up")
        except Exception as exc:
            log("companion: not available (%r); closing the window stops the recording, as before" % exc)

    def after_start():
        window.on_main(start_companion)          # the orb's work, on the main thread
        # A freeze leaves its stack in ui.log: the main thread, a slow request, or the page
        watchdog.Watchdog(log, on_main=window.on_main, main_ident=threading.main_thread().ident).start()
    webview.start(after_start)
    return 0


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Standby</title>
<style>
:root{color-scheme:light dark;--dock:#f7f7fc;--bg:#ffffff;--side:#f4f3fc;--line:#ebebf3;--edge:#dcdcea;--text:#121226;--muted:#696a80;
--soft:#f3f3f9;--hover:#ebeafb;--raised:#ffffff;--me:#e7e5ff;--me-text:#221b80;--sel:#e4e2ff;--sel-text:#2a2296;--accent-soft:#eeedff;--rec:#e5484d;--rec-soft:rgba(229,72,77,.1);--ok:#30a46c;
--accent:#5046e5;--on-accent:#ffffff;--link:#5046e5;--wave:linear-gradient(#8a7dff,#5046e5);--logo:linear-gradient(135deg,#5b4ff0,#8b3fe8);--orb:#5046e5;--orb-soft:rgba(80,70,229,.1);--chart:#5046e5;--focus:rgba(80,70,229,.5);--warn:#b54708;
--shadow:0 1px 2px rgba(0,0,0,.04),0 4px 16px rgba(0,0,0,.05);
--s0:#2f6fed;--s1:#c2410c;--s2:#15803d;--s3:#9333ea;--s4:#0e7490;--s5:#be185d;--s6:#a16207;--s7:#4d7c0f}
@media (prefers-color-scheme:dark){:root{--dock:#1c1c33;--bg:#17172a;--side:#121223;--line:#262640;--edge:#33335a;--text:#ebebf7;
--muted:#9a9ab8;--soft:#22223c;--hover:#24244a;--raised:#1d1d34;--me:#302c70;--me-text:#ecebff;--sel:#2d2a66;--sel-text:#e4e2ff;--accent-soft:rgba(139,135,255,.14);--rec:#f2555a;--rec-soft:rgba(242,85,90,.14);
--accent:#8b87ff;--on-accent:#16153a;--link:#a5a2ff;--wave:linear-gradient(#b3afff,#7d78f5);--logo:linear-gradient(135deg,#6d63ff,#9a5cf0);--focus:rgba(139,135,255,.5);--orb:#a5a2ff;--orb-soft:rgba(139,135,255,.15);--chart:#7d78f5;--warn:#f0a35e;--shadow:0 1px 2px rgba(0,0,0,.3),0 4px 16px rgba(0,0,0,.25);
--s0:#7aa7ff;--s1:#f59e6b;--s2:#5cc98a;--s3:#c49bff;--s4:#5cc6d9;--s5:#f283b5;--s6:#e3b341;--s7:#9fce6a}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{font:14px/1.5 ui-rounded,"SF Pro Rounded",-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;color:var(--text);
background:var(--bg);display:flex;overflow:hidden;-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}
button:disabled{opacity:.4;cursor:default}
input,select,textarea{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:1px}:focus:not(:focus-visible){outline:none}
svg{flex:none}
/* the window has no title bar of its own: each view's 52 px header holds the traffic lights
   (their corner kept clear, html.lights) and drags the window */
.logo{width:22px;height:22px;border-radius:7px;background:var(--logo);display:flex;align-items:center;justify-content:center;gap:2px;flex:none;
box-shadow:0 1px 3px rgba(80,70,229,.35)}
.logo i{display:block;width:2.5px;border-radius:2px;background:#fff}.logo i:nth-child(1){height:6px}.logo i:nth-child(2){height:12px}.logo i:nth-child(3){height:8px}
#list{overflow:auto;flex:1;padding:0 10px 12px}
.item{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:8px;cursor:pointer;white-space:nowrap}
.item:hover{background:var(--hover)}
.item .t{flex:1;overflow:hidden;text-overflow:ellipsis}
.item .meta{flex:none;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}.item:hover .meta{display:none}
.dot{width:8px;height:8px;border-radius:50%;background:var(--rec);flex:none;animation:pulse 1.6s infinite}
/* Standby's own loader: the logo's three bars in a ripple, wherever something is on its way */
.spin{display:inline-flex;align-items:center;justify-content:center;gap:2px;width:14px;height:14px;flex:none}
.spin i{display:block;width:2.5px;height:5px;border-radius:2px;background:var(--accent);animation:spinbar 1s ease-in-out infinite}
.spin i:nth-child(2){animation-delay:-.67s}.spin i:nth-child(3){animation-delay:-.33s}
@keyframes spinbar{0%,100%{height:4px;opacity:.55}50%{height:13px;opacity:1}}
/* while a meeting gets going: the home screen's wave, rippling across */
@keyframes loadbar{0%,100%{height:14px;opacity:.45}45%{height:var(--h,56px);opacity:1}}
@keyframes pulse{50%{opacity:.35}}@keyframes rot{to{transform:rotate(360deg)}}
.status{display:none;width:7px;height:7px;border-radius:50%;background:var(--ok);flex:none}.status.ok{display:block}
.status.bad{display:block;background:var(--rec)}

/* header */
#main{flex:1;display:flex;flex-direction:column;min-width:0;position:relative}
header{display:flex;align-items:center;gap:6px;padding:0 14px;height:52px;flex:none}
.iconbtn{color:var(--muted);width:34px;height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;flex:none}
.iconbtn:hover{background:var(--soft);color:var(--text)}.iconbtn.on{background:var(--soft);color:var(--text)}
.vhead .iconbtn{width:32px;height:32px;border-radius:50%}   /* a call's header: quiet round icons, their words their tips (the user, 2026-10-27) */
#title{font-weight:700;font-size:16px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
padding:4px 8px;border-radius:8px;cursor:text;letter-spacing:-.005em}
#title:hover{background:var(--soft)}
#title[contenteditable="true"]{background:var(--soft);outline:2px solid var(--focus);white-space:normal}
.pill{display:flex;align-items:center;gap:7px;font-size:13px;padding:0 4px;color:var(--muted);height:32px;
font-variant-numeric:tabular-nums;white-space:nowrap}
.pill.rec{color:var(--rec);background:var(--rec-soft);border-radius:999px;padding:0 12px;font-weight:500}
.cl{display:flex;align-items:center;gap:8px;font-size:13px;height:32px;padding:0 8px;border-radius:999px;white-space:nowrap;color:var(--muted);flex:none}
.cl:hover{background:var(--soft);color:var(--text)}.cl.on{color:var(--text)}
/* C · Wave: a little sound before the title while a session records */
@keyframes hw{0%,100%{transform:scaleY(.6)}50%{transform:scaleY(1)}}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:32px;padding:0 14px;border-radius:999px;
border:1px solid var(--edge);font-size:13px;font-weight:500;white-space:nowrap}
.btn:hover:not(:disabled){background:var(--soft)}
.btn.primary{background:var(--accent);color:var(--on-accent);border-color:var(--accent)}
.btn.primary:hover:not(:disabled){background:var(--accent);opacity:.85}
.btn.danger svg{color:var(--rec)}.btn.danger:hover:not(:disabled){border-color:var(--rec);color:var(--rec);background:none}
.btn.sm{height:28px;padding:0 12px}
/* the recorder's one question: above the box, until it is answered or has gone */
.item .kind{display:flex;color:var(--muted);flex:none;margin-left:-1px}
.dot.talk{background:var(--orb)}
.pill.talk{color:var(--orb);background:var(--orb-soft);border-radius:999px;padding:0 12px;font-weight:500}
/* the time and Stop are one control: one capsule, the time on the left, Stop (or End) on the right */
.pill.rec,.pill.talk{border-radius:999px 0 0 999px;padding-right:8px}
.pill.rec+#stop,.pill.talk+#stop{margin-left:-6px;height:32px;border:0;border-radius:0 999px 999px 0;padding:0 13px 0 9px;
background:var(--rec-soft);color:var(--rec);box-shadow:inset 1px 0 0 rgba(229,72,77,.22)}
.pill.talk+#stop{background:var(--orb-soft);color:var(--orb);box-shadow:inset 1px 0 0 color-mix(in srgb,var(--orb) 22%,transparent)}
.pill.rec+#stop:hover:not(:disabled){background:rgba(229,72,77,.2);color:var(--rec)}
.pill.talk+#stop:hover:not(:disabled){background:color-mix(in srgb,var(--orb) 20%,transparent);color:var(--orb)}          
/* files waiting above the box */
.tmenu{position:absolute;bottom:calc(100% + 8px);right:8px;z-index:30;width:330px;max-height:60vh;overflow:auto;padding:5px;border-radius:12px;
background:var(--raised);box-shadow:0 0 0 .5px var(--edge),0 10px 30px rgba(0,0,0,.14)}
.tmenu button{display:flex;align-items:center;gap:10px;width:100%;height:40px;padding:0 8px;border-radius:8px;font-size:13px;text-align:left;color:var(--text)}
.tmenu button:hover,.tmenu button:focus-visible{background:var(--soft);outline:none}
.tmenu .ai{width:18px;height:18px;border-radius:5px;flex:none;display:flex;align-items:center;justify-content:center;font:700 10px/1 -apple-system,system-ui,sans-serif;color:#fff;overflow:hidden}
.tmenu .ai img{width:18px;height:18px;display:block}
.tmenu .lb{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tmenu .lb small{color:var(--muted);font-size:12px;margin-left:5px}
.tmenu .ck{flex:none;width:14px;color:var(--muted)}
.tmenu hr{border:0;border-top:1px solid var(--line);margin:4px 8px}
/* the project folder: a field that opens your recent projects; its list in Settings and beside the sidebar */
.pf{position:relative}.pf .row{display:flex;gap:8px}
.pfield{flex:1;min-width:0;display:flex;align-items:center;gap:8px;height:36px;padding:0 12px;border:1px solid var(--edge);border-radius:10px;
background:var(--raised);font-size:13px;text-align:left;color:var(--text)}.pfield:hover{border-color:var(--muted)}
.pfield svg{flex:none;color:var(--muted)}.pfield b{font-weight:600;flex:none}.pfield small{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-size:12px}
.pfield:after{content:"";margin-left:auto;flex:none;width:6px;height:6px;border-right:1.6px solid var(--muted);border-bottom:1.6px solid var(--muted);transform:rotate(45deg) translateY(-2px)}
.tmenu.pmenu{top:66px;bottom:auto;left:0;right:auto;width:100%}
.tmenu .when{flex:none;font-size:12px;color:var(--muted)}
.tmenu input{width:100%;height:32px;margin:2px 0;padding:0 10px;border:1px solid var(--edge);border-radius:8px;font:inherit;font-size:13px;background:var(--bg);color:var(--text)}
#pstate .ok{color:var(--ok)}#pstate .bad{color:var(--rec)}
/* the dictation shortcut: presets, or one recorded; while recording the field says so */
.pfield.rec{border-color:var(--accent);color:var(--muted)}.pfield.rec:after,.pfield.busy:after{display:none}.pfield.busy{color:var(--muted)}.pfield.rec:before{content:"";flex:none;width:7px;height:7px;border-radius:50%;background:var(--rec);animation:pulse 1.2s ease-in-out infinite}
#khint.bad{color:var(--rec)}#kshortf[hidden],#kholdf[hidden]{display:none}
/* files waiting for the next question; and on a question that took them */
.msg.me{flex-direction:column;align-items:flex-end;gap:6px}
@keyframes lbin{from{opacity:0}to{opacity:1}}
@keyframes pop{from{transform:scale(.8);opacity:0}to{transform:scale(1);opacity:1}}
.btn.ghost{border-color:transparent;color:var(--muted)}.btn.ghost:hover:not(:disabled){color:var(--text)}
/* tabs */
.tab{padding:4px 14px;border-radius:7px;color:var(--muted);font-size:13px;font-weight:500}
.tab:hover{color:var(--text)}
.tab.on{background:var(--bg);color:var(--accent);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.08)}
/* conversation */
#body{flex:1;overflow:auto;padding:12px 0 24px}
.col{max-width:760px;margin:0 auto;padding:0 28px}
.msg{display:flex;margin:22px 0 10px}.msg.me{justify-content:flex-end}.msg:first-child{margin-top:8px}
.msg.ai{display:block;margin-top:10px}
.md{font-size:15px;line-height:1.65;word-wrap:break-word}.md>*:first-child{margin-top:0}.md>*:last-child{margin-bottom:0}
.md p{margin:0 0 12px}.md h1,.md h2,.md h3{margin:20px 0 8px;line-height:1.3;letter-spacing:-.005em;font-weight:700}
.md h1{font-size:17px}.md h2{font-size:15px}.md h3{font-size:15px}
.md ul,.md ol{margin:4px 0 12px;padding-left:22px}.md li{margin:4px 0}.md li::marker{color:var(--muted)}
.md strong{font-weight:600}
.md code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);padding:2px 5px;border-radius:5px}
.md pre{position:relative;background:var(--soft);border-radius:12px;padding:14px 16px;overflow:auto;margin:10px 0 14px}
.md pre code{background:none;padding:0;line-height:1.55;white-space:pre}
.md .cp{position:absolute;top:8px;right:8px;font-size:12px;padding:2px 9px;border-radius:6px;border:1px solid var(--edge);
background:var(--bg);color:var(--muted);opacity:0}.md pre:hover .cp{opacity:1}
.md blockquote{margin:8px 0 12px;padding:2px 14px;border-left:3px solid var(--edge);color:var(--muted)}
.md table{border-collapse:collapse;margin:10px 0 14px;font-size:14px;display:block;overflow:auto}
.md th,.md td{border-bottom:1px solid var(--line);padding:7px 14px 7px 0;text-align:left;vertical-align:top}
.md th{font-weight:600;border-bottom-color:var(--edge)}
.md a{color:var(--link);text-decoration:none}.md a:hover{text-decoration:underline}
.md hr{border:0;border-top:1px solid var(--line);margin:18px 0}
.steps{font-size:13px;color:var(--muted);margin:0 0 8px}
.step{display:flex;gap:9px;align-items:baseline;padding:2px 0}
.step:before{content:"";width:5px;height:5px;border-radius:50%;background:var(--muted);opacity:.6;flex:none;transform:translateY(-2px)}
.step.run:before{background:var(--accent);opacity:1;animation:pulse 1.1s infinite}
details.steps summary{cursor:pointer;list-style:none;display:inline-flex;align-items:center;gap:7px;padding:2px 0;user-select:none}
details.steps summary::-webkit-details-marker{display:none}details.steps summary:hover{color:var(--text)}
details.steps summary:before{content:"";width:5px;height:5px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;
transform:rotate(-45deg);transition:transform .15s}
details.steps[open] summary:before{transform:rotate(45deg) translate(-1px,-1px)}
details.steps[open]{margin-bottom:10px}details.steps[open] .step{padding-left:2px}
.msg .thinking{color:var(--muted);font-size:13px;display:flex;align-items:center;gap:8px;margin-top:4px}
/* transcript */
.lvl{display:flex;gap:2px;align-items:flex-end;height:14px;flex:none}.lvl i{display:block;width:3px;background:var(--ok);border-radius:1px;transition:height .2s}
/* what it records: one chip per source, its own meter; paused grey, a refusal with its way out */
.src{display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:13px;background:var(--soft);color:var(--text);font-size:12px;white-space:nowrap}
.src svg{flex:none;color:var(--muted)}
.src.paused{color:var(--muted)}.src.paused .lvl i{background:var(--muted)}
.src.bad{color:var(--rec);background:color-mix(in srgb,var(--rec) 10%,transparent)}.src.bad svg{color:var(--rec)}
.src button{font:inherit;font-weight:600;color:inherit;text-decoration:underline;text-underline-offset:2px;padding:0}
.partial{font-style:italic}
.who.known{border-bottom:1px dotted currentColor;border-radius:0}
.sep{display:flex;align-items:center;gap:12px;color:var(--muted);font-size:12px;margin:18px 0}
.learned{text-align:center;margin:22px 0 6px;font-size:12px;color:var(--muted)}
.learned b{color:var(--text);font-weight:600}.learned .lk{font:inherit;font-weight:600;color:var(--link);background:none;padding:0;border:0;cursor:pointer}.learned .lk:hover{text-decoration:underline}
.said .fx{text-decoration:underline dotted var(--muted);text-underline-offset:3px;cursor:help}
.sep:before,.sep:after{content:"";flex:1;border-top:1px solid var(--line)}
.line{display:flex;gap:12px;padding:5px 0;align-items:baseline;font-size:15px;line-height:1.6}
.tx{display:grid;grid-template-columns:58px auto max-content minmax(0,1fr);column-gap:14px;align-items:baseline}
/* where a line came from: a quiet mark per turn, its source's name on hover */
.tx .sm{display:flex;align-items:center;margin-right:-6px;color:var(--muted);align-self:center}.tx .line.turn>.sm{padding-top:14px}
.sm svg{display:block}.sm img{display:block;width:14px;height:14px;border-radius:3px;opacity:.85}
.sm .ai{width:14px;height:14px;border-radius:4px;display:flex;align-items:center;justify-content:center;font:700 8.5px/1 -apple-system,system-ui,sans-serif;color:#fff;opacity:.85}
.tx .line{display:contents}.tx .line>*{padding:2px 0}.tx .line.turn>*{padding-top:12px}
.tx .sep,.tx .empty{grid-column:1/-1}.tx .said{font-size:15px;line-height:1.6}
.who.editing{background:var(--soft);outline:2px solid var(--focus);cursor:text;color:var(--text)!important}
.ts{color:var(--muted);font-variant-numeric:tabular-nums;flex:none;width:58px;font-size:12px;white-space:nowrap}
.ts.same,.ts.in{color:transparent}.line:hover .ts.same,.line:hover .ts.in{color:var(--muted)}
.who{flex:none;font-weight:600;font-size:14px;cursor:pointer;border-radius:5px;padding:0 4px;margin:0 -4px}
.who:hover{background:var(--soft)}
/* empty, starting and welcome */
.empty{color:var(--muted);text-align:center;margin-top:16vh;line-height:1.65;font-size:16px}
.empty .wave{display:flex;justify-content:center;align-items:center;gap:6px;height:64px;margin:0 0 22px}
.empty .wave i{display:block;width:6px;border-radius:3px;background:var(--wave);animation:ew 2.6s ease-in-out infinite}
@keyframes ew{0%,100%{transform:scaleY(1)}50%{transform:scaleY(.6)}}
.empty h2{color:var(--text);font-size:34px;font-weight:700;letter-spacing:-.02em;line-height:1.2;margin:0 0 8px}
.empty .btn{margin-top:22px;height:40px;padding:0 20px;font-size:15px;font-weight:600}
.card{max-width:540px;margin:11vh auto 0;text-align:center;padding:0 24px}
.card h2{font-size:26px;font-weight:700;letter-spacing:-.02em;margin:0 0 8px}
.card .btn{margin-top:20px}
.acts{display:flex;justify-content:center;gap:8px;flex-wrap:wrap}.acts .btn{margin-top:20px}
.err{color:var(--rec);white-space:pre-wrap;text-align:left;font-size:13px;background:var(--rec-soft);
padding:10px 14px;border-radius:10px;margin-top:14px}
/* composer */
footer{padding:4px 24px 14px;position:relative}
/* suggested questions: they float just above the chat box, over the chat's end, so
   nothing moves when they come and go */
#vbtn{color:var(--accent)}#vbtn:hover:not(:disabled){background:var(--accent-soft);color:var(--accent)}
#q{flex:1;border:0;outline:0;background:none;resize:none;padding:7px 0;max-height:160px;font-size:15px;line-height:1.45}
#q::placeholder{color:var(--muted)}
.mic{width:36px;height:36px;border-radius:50%;color:var(--muted);display:flex;align-items:center;justify-content:center;flex:none}
.mic:hover:not(:disabled){background:var(--soft);color:var(--text)}
/* the mic has two states, off and recording (red); hover only deepens the fill, never swaps it,
   and while the last words are transcribed it just fades back */
.mic.rec,.mic.rec:hover:not(:disabled){background:var(--rec);color:#fff}.mic.rec:hover:not(:disabled){filter:brightness(.92)}
.mic.busy{background:var(--rec);color:#fff;opacity:.55;transition:opacity .3s}
.vmic{display:inline-block;vertical-align:-1px;margin-right:6px;opacity:.6}
/* a call (the user's design, 2026-10-21): its header (name, live and its time, Pause, End, ⋯), a strip
   with a small orb and what's happening, then its conversation */
.voice{position:absolute;inset:0;z-index:20;background:var(--bg);display:flex;flex-direction:column}
.vwho{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.25}
.vwho b{font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vwho small{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
/* live, one line: the mark, the name, the time (the name gives way first in a narrow window) */
.vwho.one{flex-direction:row;align-items:center;gap:8px}.vwho.one b{flex:0 1 auto;min-width:0}
.vwho .tm{flex:none;font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
.lmark{flex:none;width:9px;height:9px;border-radius:50%;background:var(--rec);box-shadow:0 0 0 3px var(--rec-soft);animation:pulse 1.6s infinite}
.lmark.call{background:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.lmark.paused{animation:none;box-shadow:none;background:none;border-radius:0;width:9px;height:11px;display:inline-flex;justify-content:space-between}
.lmark.paused:before,.lmark.paused:after{content:"";width:3px;height:100%;border-radius:1px;background:var(--muted)}
/* a header's Pause and End (the user, 2026-10-22: they should look like buttons): soft pills, End in red;
   while one is doing what it says ("Pausing…"), dimmed and not pressable */
.vtxt{height:28px;padding:0 12px;border-radius:999px;border:1px solid var(--edge);background:var(--bg);font-size:13px;font-weight:500;flex:none;white-space:nowrap;transition:background .12s,opacity .12s}
.vtxt:hover:not(:disabled){background:var(--soft)}
.vtxt.end{color:var(--rec);border-color:color-mix(in srgb,var(--rec) 35%,transparent);background:var(--rec-soft)}
.vtxt.end:hover:not(:disabled){background:color-mix(in srgb,var(--rec) 18%,transparent)}
.vtxt:disabled{opacity:.55;cursor:default}
/* a live header's buttons as icons (the user, 2026-10-23: the header was cramped), their words their tips:
   round, quiet; End red; mid-way dimmed and gently pulsing */
.vic{width:30px;height:30px;border-radius:50%;border:1px solid var(--edge);background:var(--bg);color:var(--text);display:flex;align-items:center;
justify-content:center;flex:none;transition:background .12s,opacity .12s}
.vic:hover:not(:disabled){background:var(--soft)}.vic:disabled{opacity:.55;cursor:default}.vic.busy{animation:pulse 1.2s infinite}
.vic.end{color:var(--rec);border-color:color-mix(in srgb,var(--rec) 35%,transparent);background:var(--rec-soft)}
.vic.end:hover:not(:disabled){background:color-mix(in srgb,var(--rec) 18%,transparent)}
.vhead .vic+.vic,#mview header .vic+.vic{margin-left:4px}
.vtxt.waiting,.vic.waiting{position:relative}.vtxt.waiting:after,.vic.waiting:after{content:"";position:absolute;top:-2px;right:-2px;width:9px;height:9px;border-radius:50%;
background:var(--rec);box-shadow:0 0 0 2px var(--bg)}
.vhead .vtxt+.vtxt,#mview header .vtxt+.vtxt{margin-left:2px}
/* what's live, one row at the bottom (the user, 2026-10-25: option B): the orb, its state and the newest two
   lines of words, Pause and End; one height whatever is said, so the conversation above never moves */
.vstage{flex:none;width:100%;display:flex;align-items:center;gap:10px;height:78px;padding:10px 12px 12px;background:var(--dock);border-top:1px solid var(--line)}
.vrow{display:flex;align-items:center;gap:7px;height:18px;min-width:0;overflow:hidden}
/* the words sit level with the orb, not at the row's bottom (the user, 2026-10-27): the text is only as
   tall as it is, up to its newest two lines, and the block is centred beside the orb; the status row
   above it shows only when it has something to say */
.vwords{max-height:38px;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end}
.vrow:not(:has(.vstate:not(:empty))):not(:has(.vbusy:not([hidden]))):not(:has(.vsteps:not(:empty))){display:none}
.vtext{flex:1;min-width:0}
.vlog{flex:1;min-height:0;overflow:auto;width:100%;max-width:680px;margin:0 auto;padding:8px 16px 40px;
-webkit-mask-image:linear-gradient(to bottom,#000 calc(100% - 28px),transparent);mask-image:linear-gradient(to bottom,#000 calc(100% - 28px),transparent)}
/* the orb: drawn at its full size, shown small (44 px) */
.orb{width:168px;height:168px;zoom:.27;border-radius:50%;flex:none;transform:scale(var(--s,1));transition:transform .1s linear;
display:flex;align-items:center;justify-content:center;gap:10px;
background:radial-gradient(circle at 34% 28%,#d9d6ff 0%,#8f86ff 38%,#5046e5 75%,#3b2fc0 100%);
box-shadow:0 22px 70px rgba(80,70,229,.3),inset -14px -18px 42px rgba(30,20,120,.35)}
/* C · Wave: the sound inside, and the mini window's motions, so the orb says the state with no words
   (the user, 2026-10-27): listening, the bars barely move; hearing you, they move with you and the glow
   turns cyan; thinking, three dots rising in a turning rainbow; Claude talking, quick bars, a violet
   glow and rings going out */
.orbw{position:relative;flex:none;width:45.36px;height:45.36px}.orbw .orb{position:relative;z-index:1}
.vhalo{position:absolute;inset:-3px;border-radius:50%;filter:blur(3px);opacity:0;pointer-events:none;transition:opacity .35s}
.vrp{position:absolute;inset:0;border-radius:50%;opacity:0;pointer-events:none}
.orb i{display:block;flex:none;width:13px;height:26px;border-radius:7px;background:rgba(255,255,255,.92);transition:width .32s,height .32s}.orb i:nth-child(2){height:38px}
.voice.listening .orb i{animation:vcalm 2.8s ease-in-out infinite}.voice.listening .orb i:nth-child(2){animation-delay:-.9s}.voice.listening .orb i:nth-child(3){animation-delay:-1.8s}
.voice.listening .vhalo{background:#5046e5;animation:vhb 3.6s ease-in-out infinite}
.voice.listening.hearing .orb i{animation:vbar 1s ease-in-out infinite}.voice.listening.hearing .orb i:nth-child(2){animation-delay:-.35s}.voice.listening.hearing .orb i:nth-child(3){animation-delay:-.7s}
.voice.listening.hearing .vhalo{background:#22d3ee;animation:none;opacity:.75}
.voice.thinking .orb i,.voice.starting .orb i{width:24px;height:24px;border-radius:50%;animation:vdot 1.25s ease-in-out infinite}
.voice.thinking .orb i:nth-child(2),.voice.starting .orb i:nth-child(2){animation-delay:.16s}.voice.thinking .orb i:nth-child(3),.voice.starting .orb i:nth-child(3){animation-delay:.32s}
.voice.thinking .vhalo,.voice.starting .vhalo{background:conic-gradient(#5046e5,#8b5cf6,#ec4899,#f59e0b,#22d3ee,#5046e5);opacity:.9;animation:vspin 2.4s linear infinite}
.voice.speaking .orb{animation:vspeak 1.05s ease-in-out infinite}
.voice.speaking .orb i{animation:vbar .5s ease-in-out infinite}.voice.speaking .orb i:nth-child(2){animation-delay:-.15s}.voice.speaking .orb i:nth-child(3){animation-delay:-.3s}
.voice.speaking .vhalo{background:#a855f7;animation:vhb2 1.05s ease-in-out infinite}
.voice.speaking .vrp{box-shadow:0 0 0 2.4px rgba(168,85,247,.7);animation:vripple 1.8s ease-out infinite}.voice.speaking .vrp+.vrp{animation-delay:.9s}
.voice.muted .orb,.voice.paused .orb{filter:grayscale(1);opacity:.4}
.voice.muted .orb i:nth-child(n),.voice.paused .orb i:nth-child(n){height:20px;animation:none}
.voice.holding .orb i{animation:none;height:26px}.voice.holding .orb{opacity:.75}
.voice.muted .vhalo,.voice.paused .vhalo,.voice.holding .vhalo{display:none}
@keyframes vbar{0%,100%{height:23px}50%{height:64px}}
@keyframes vcalm{0%,100%{transform:scaleY(1)}50%{transform:scaleY(1.2)}}
@keyframes vdot{0%,70%,100%{opacity:.45;transform:none}35%{opacity:1;transform:translateY(-16px)}}
@keyframes vspin{to{transform:rotate(1turn)}}
@keyframes vhb{0%,100%{opacity:.18}50%{opacity:.4}}
/* Claude talking, a fifth stronger than the mini window's first cut (the user, 2026-10-27: too subtle) */
@keyframes vhb2{0%,100%{opacity:.55}50%{opacity:.95}}
@keyframes vripple{from{transform:scale(1);opacity:.8}to{transform:scale(1.32);opacity:0}}
@keyframes vspeak{0%,100%{transform:scale(1)}25%{transform:scale(1.085)}55%{transform:scale(.975)}80%{transform:scale(1.05)}}
.vstate{font-size:12px;font-weight:600;color:var(--muted);white-space:nowrap}
/* while Claude works: its actions counted, a quiet status line */
.vsteps{min-width:0;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;
text-overflow:ellipsis;font-variant-numeric:tabular-nums}.vsteps:empty{display:none}
.vsteps:before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--accent);margin-right:8px;vertical-align:1px;animation:pulse 1.6s infinite}
.hint.fine{font-size:11.5px;opacity:.85}
/* Standby's voice, fetched the first time: a thin line and its percent; the Mac's voice speaks meanwhile */
.vprep{display:flex;flex-direction:column;align-items:center;gap:8px;margin:0 0 22px;font-size:12px;color:var(--muted)}.vprep[hidden]{display:none}
.vtrack{display:block;width:140px;height:3px;border-radius:2px;background:var(--line);overflow:hidden}
.vtrack i{display:block;height:100%;width:var(--p,30%);border-radius:2px;background:var(--chart);transition:width .4s ease}
.vprep.unknown .vtrack i{animation:vslide 1.4s ease-in-out infinite}
/* Try's first download, under the Voice row; the button keeps one width through its words */
#s_voicetry{width:100px;flex:none}
.tryprep{align-items:flex-start;gap:6px;margin:8px 0 0}.tryprep .vtrack{width:calc(100% - 108px)}
.tryprep.failed .vtrack{display:none}
.tryprep .lk{font:inherit;font-weight:600;color:var(--link);background:none;padding:0;border:0;cursor:pointer}.tryprep .lk:hover{text-decoration:underline}
@keyframes vslide{0%{transform:translateX(-100%)}100%{transform:translateX(470%)}}
.vcap{font-size:14px;line-height:19px;overflow-wrap:anywhere;flex:none}
.vcap:empty{display:none}.vcap.you{color:var(--muted)}
.vtext .vprep{align-items:flex-start;margin:6px 0 0}
.toast{position:fixed;bottom:24px;left:50%;transform:translate(-50%,6px);background:var(--accent);color:var(--on-accent);
padding:8px 16px;border-radius:999px;font-size:13px;font-weight:500;opacity:0;transition:opacity .2s,transform .2s;pointer-events:none;
box-shadow:0 6px 24px rgba(0,0,0,.18)}
.toast.on{opacity:1;transform:translate(-50%,0)}
.toast.act.on{pointer-events:auto}.toast button{margin-left:10px;font:inherit;font-weight:600;color:inherit;text-decoration:underline;text-underline-offset:2px}
/* dialogs */
dialog{border:0;border-radius:16px;padding:0;width:580px;max-width:92vw;background:var(--bg);color:var(--text);
box-shadow:0 0 0 1px var(--line),0 24px 64px rgba(0,0,0,.22)}
dialog::backdrop{background:rgba(0,0,0,.32)}
dialog.big{width:940px;max-width:94vw}
/* Settings (the user, 2026-10-21: one narrow window): the whole window, a header like the list's (back,
   the title, Saved), the sections as a list; a section opens in place, its settings one per row */
dialog.setwin{inset:0;margin:0;width:100%;height:100%;max-width:none;max-height:none;border-radius:0;box-shadow:none}
dialog.setwin::backdrop{background:none}
.setwin form{display:flex;flex-direction:column;height:100%}
.sethead{display:flex;align-items:center;gap:6px;padding:0 14px;height:52px;flex:none;transition:box-shadow .15s}
html.lights .sethead{padding-left:84px}.sethead.scrolled{box-shadow:0 1px 0 var(--line)}
.sethead h3{flex:1;min-width:0;margin:0;font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sethead h3:focus{outline:none}
.sethead .saved{font-size:12px;color:var(--muted);opacity:0;transition:opacity .2s}.sethead .saved.on{opacity:1}
.setnav{flex:1;overflow:auto;padding:4px 10px 16px;width:100%;max-width:680px;margin:0 auto}.setnav[hidden]{display:none}
.setnav button{display:flex;align-items:center;gap:11px;width:100%;min-height:54px;padding:8px 10px;border-radius:9px;text-align:left}
.setnav button:hover{background:var(--hover)}.setnav button svg{color:var(--muted)}
.setnav .t{flex:1;min-width:0}.setnav .t b{display:block;font-weight:500}
.setnav .t small{display:block;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.setmain{flex:1;min-height:0;overflow:auto;width:100%;max-width:680px;margin:0 auto;padding:0 20px 24px}.setmain[hidden]{display:none}
.setmain .field{margin:0;padding:14px 0;border-bottom:1px solid var(--line)}.setmain .sec>.field:last-child{border-bottom:0}
.setmain .seg button{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.setmain #dictperms{margin:0;padding:8px 0 12px;border-bottom:1px solid var(--line)}.setmain #dictperms:empty{display:none}
#s_voicetry{height:36px}
.pane{display:none}.pane.on{display:block}
/* Usage: three numbers, calls over time, then the tables */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin:18px 0 20px}
.kpi{padding:12px 14px;border-radius:12px;background:var(--soft);min-width:0}
.kpi .kl{display:block;font-size:12px;color:var(--muted);margin-bottom:2px}.kpi b{font-size:22px;font-weight:600;letter-spacing:-.01em}
.kpi b small{font-size:13px;font-weight:400;color:var(--muted)}
.uchart{margin:0 0 22px}.uct,.uax{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}
.ucols{display:flex;align-items:flex-end;gap:2px;height:88px;margin:6px 0 4px;border-bottom:1px solid var(--line)}
.ucols i{flex:1;height:100%;display:flex;align-items:flex-end;justify-content:center}
.ucols i b{display:block;width:100%;max-width:24px;background:var(--chart);border-radius:4px 4px 0 0}
.ucols i:hover b{filter:brightness(1.12)}
.ucols i.now b{opacity:.55}
.pane h5{margin:0 0 6px;font-size:13px;font-weight:600}
.utab{width:100%;border-collapse:collapse;font-size:13px;margin-bottom:20px;font-variant-numeric:tabular-nums}
.utab th{text-align:left;font-weight:500;color:var(--muted);padding:6px 0;border-bottom:1px solid var(--line)}
.utab td{padding:7px 0;border-bottom:1px solid var(--line)}
.utab th:not(:first-child),.utab td:not(:first-child){text-align:right}
.utab.kinds th:nth-child(2),.utab.kinds td:nth-child(2){text-align:left;color:var(--muted);padding-left:12px}
.uempty{margin:48px 0;text-align:center}.uempty b{display:block;font-size:15px;margin-bottom:4px}.uempty span{color:var(--muted);font-size:13px}.pane .sec{border:0;padding:0}.pane .sec>h4{margin-bottom:14px}
dialog.ask{width:400px}.ask .dhead{padding-top:20px}.ask .dbody p{margin:2px 0 6px;font-size:13px;line-height:1.5;color:var(--muted)}
.ask .dfoot{border-top:0;padding-top:10px}.btn.destroy{background:var(--rec);border-color:var(--rec);color:#fff}
.btn.destroy:hover:not(:disabled){background:var(--rec);opacity:.88}
.dhead{display:flex;align-items:center;gap:8px;padding:16px 16px 4px 24px}
.dhead h3{margin:0;font-size:17px;font-weight:600;flex:1;letter-spacing:-.01em}.dhead h3:focus{outline:none}
.dbody{padding:0 24px 8px;max-height:70vh;overflow:auto}
.sec{padding:16px 0;border-bottom:1px solid var(--line)}.sec:last-child{border:0}
.sec h4{margin:0 0 10px;font-size:13px;font-weight:600;color:var(--muted)}
.field{margin:12px 0}
.field>label{display:block;font-size:13px;font-weight:500;margin-bottom:6px}
.field input:not([type=checkbox]),.field select{width:100%;height:36px;padding:0 12px;border:1px solid var(--edge);border-radius:10px;background:var(--bg)}
.field input:focus,.field select:focus{outline:2px solid var(--focus);outline-offset:-1px;border-color:transparent}
.field select{-webkit-appearance:none;appearance:none;padding-right:32px;background-repeat:no-repeat;background-position:right 12px center;
background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none' stroke='%23888' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E")}
.field>label.tgl{display:flex;align-items:center;gap:16px;font-weight:400;font-size:14px;margin:0;cursor:pointer}
.tgl input{-webkit-appearance:none;appearance:none;order:2;margin:0 0 0 auto;width:32px;height:20px;border-radius:999px;background:var(--edge);
position:relative;flex:none;cursor:pointer;transition:background .15s}
.tgl input:before{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;
box-shadow:0 1px 2px rgba(0,0,0,.25);transition:left .15s}
.tgl input:checked{background:var(--accent)}
.sugg-n{color:var(--link);font-weight:600}.tgl input:checked:before{left:14px}
.mx-help[hidden]{display:none}.dotlink:before{content:"\00a0\00b7\00a0";color:var(--muted)}.setrow{display:flex;align-items:center;justify-content:space-between;gap:16px}.setrow label{margin:0}.setrow input.num{width:72px;text-align:center}.tgl .mx-help{margin-left:-8px}
.hint{font-size:12px;color:var(--muted);margin-top:5px;line-height:1.45}
.hint a{color:var(--link);text-decoration:none}.hint a:hover{text-decoration:underline}
.seg{display:flex;padding:3px;border-radius:10px;background:var(--soft)}
.seg button{flex:1;padding:6px;border-radius:7px;color:var(--muted);font-weight:500;font-size:13px}
.seg button.on{background:var(--bg);color:var(--text);box-shadow:0 1px 2px rgba(0,0,0,.08)}
.row{display:flex;gap:8px;align-items:center}
.checks{margin-top:10px;font-size:13px}
.check{display:flex;gap:10px;padding:4px 0}.check .k{flex:none;width:150px}.check .v{color:var(--muted);word-break:break-all}
.ok{color:var(--ok)}.bad{color:var(--rec)}
/* Settings > Dictation: one status line, then a row per missing permission */
#dictstate{display:flex;align-items:center;gap:8px}
#dictperms .check{align-items:center;gap:8px;padding:3px 0}#dictperms .check .k{width:auto;font-weight:500}
#dictperms .check .btn{margin-left:auto}#dictperms .hint{margin-top:8px}
.setup{text-align:left;margin:24px auto 0;font-size:13px;border:1px solid var(--line);border-radius:14px;padding:6px 18px 14px}
.dfoot{display:flex;justify-content:flex-end;gap:8px;padding:14px 24px 18px;border-top:1px solid var(--line)}
.meter{flex:1;height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin-left:10px}
.meter span{display:block;height:100%;background:var(--ok)}
.mt{padding:6px 0}
#ptext{width:100%;height:60vh;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;border:1px solid var(--edge);
border-radius:12px;padding:12px 14px;background:var(--bg);color:var(--text);resize:vertical;margin-top:12px}
pre.log{white-space:pre-wrap;font-size:12px;background:var(--soft);padding:10px;border-radius:10px;max-height:160px;overflow:auto}
@media (prefers-reduced-motion:reduce){*,*:before{animation:none!important;transition:none!important}}
</style><link rel="stylesheet" href="/agents.css"></head><body>
<main id="main">
  <div class="voice" id="voice" style="display:none" role="dialog" aria-label="Call">
    <header class="vhead" data-drag>
      <button class="iconbtn" onclick="mxCallBack()" title="Back to the list" aria-label="Back to the list"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg></button>
      <span class="vwho one"><span class="lmark call" id="vmark" role="img" aria-label="On a call" title="On a call"></span><b id="vname"></b><span class="tm" id="vtime"></span></span>
      <button class="iconbtn" onclick="mxToOrb()" title="Mini window" aria-label="Mini window"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="14" rx="2.5"/><rect x="12" y="12" width="6" height="4.5" rx="1.2" fill="currentColor" stroke="none"/></svg></button>
      <button class="iconbtn" onclick="mxCallMenu(event)" title="More" aria-label="More"><svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><circle cx="5.5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="18.5" cy="12" r="1.7"/></svg></button>
    </header>
    <div class="vlog" id="mxcalllog"></div>
    <div class="vstage">
      <span class="orbw"><span class="vhalo"></span><span class="vrp"></span><span class="vrp"></span><button class="orb" id="orb" onclick="voiceTap()" aria-label="Interrupt"><i></i><i></i><i></i></button></span>
      <div class="vtext">
        <div class="vrow"><div class="vstate" id="vstate"></div><span class="vbusy" id="vbusy" hidden></span><div class="vsteps" id="vsteps" aria-live="polite"></div></div>
        <div class="vwords"><div class="vcap you" id="vyou"></div><div class="vcap" id="vclaude"></div>
          <div class="vprep" id="vprep" hidden title="A one-time download. Your Mac's own voice speaks until it's ready."><span class="vtrack"><i id="vprepfill"></i></span><span id="vpreptxt"></span></div></div>
      </div>
      <button class="vic dk" id="vmute" onclick="voiceMute()" title="Pause" aria-label="Pause"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4.5" width="4.2" height="15" rx="1.3"/><rect x="13.8" y="4.5" width="4.2" height="15" rx="1.3"/></svg></button>
      <button class="vic dk end" onclick="closeVoice()" title="End" aria-label="End"><svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 9c-3.2 0-6.2.9-8.6 2.6a1.6 1.6 0 0 0-.6 1.8l.7 2a1.3 1.3 0 0 0 1.6.8l3.2-1a1.3 1.3 0 0 0 .9-1.1l.2-1.6a12 12 0 0 1 5.2 0l.2 1.6a1.3 1.3 0 0 0 .9 1.1l3.2 1a1.3 1.3 0 0 0 1.6-.8l.7-2a1.6 1.6 0 0 0-.6-1.8C18.2 9.9 15.2 9 12 9z"/></svg></button>
    </div>
  </div>
</main>
<div class="toast" id="toast"></div>
<dialog id="settings" class="setwin"><form method="dialog" onsubmit="event.preventDefault();autosave()">
  <header class="sethead" id="sethead" data-drag>
    <button type="button" class="iconbtn" id="setback" onclick="setBack()" title="Back" aria-label="Back"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg></button>
    <h3 id="settitle" tabindex="-1" autofocus>Settings</h3><span class="saved" id="ssaved">Saved</span></header>
  <nav class="setnav" id="setnav">
    <button type="button" data-p="call" onclick="setPane('call')"><span class="t"><b>Call</b><small>The voice and its model, talking over Claude, asking first</small></span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>
    <button type="button" data-p="recording" onclick="setPane('recording')"><span class="t"><b>Recording</b><small>What's recorded, the speech model, what Claude gets</small></span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>
    <button type="button" data-p="dictation" onclick="setPane('dictation')"><span class="t"><b>Dictation</b><small>Dictation in any app, its shortcut, word fixes</small></span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>
    <button type="button" data-p="usage" onclick="setPane('usage')"><span class="t"><b>Usage</b><small>Calls to Claude over time, by model and kind</small></span><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>
  </nav>
  <div class="setmain" id="setmain">
    <div class="pane on" data-p="call"><div class="sec">
      <div class="field"><label>Voice mode model</label><select id="s_vmodel" data-save><option value="haiku">Haiku · Recommended</option>
        <option value="sonnet">Sonnet</option><option value="opus">Opus</option></select>
        <div class="hint" id="s_vmodelnote" hidden></div></div>
      <div class="field"><label>Voice<span class="mx-help" id="s_voicecredit" tabindex="0" title="" aria-label="About the voice" onclick="event.preventDefault()" hidden></span></label>
        <div class="row"><select id="s_voice" style="flex:1" onchange="pickVoice(this.value)"></select>
          <button type="button" class="btn sm" id="s_voicetry" onclick="tryVoice()">Try</button></div>
        <div class="vprep tryprep" id="s_tryprep" hidden><span id="s_tryprept"></span><span class="vtrack"><i id="s_trypfill"></i></span></div>
        <div class="hint" id="s_voicehint" hidden>A Premium voice sounds much better, and it's free. <a href="#" onclick="event.preventDefault();getVoice()">Get one</a></div>
        <div class="hint" id="s_voiceprep" hidden></div>
        </div>
      <div class="field"><label class="tgl"><input type="checkbox" id="s_autoaccept" data-save> Let Claude start calls and listening without asking<span class="mx-help" tabindex="0" title="The same as Always accept on Claude Code's band. Off: the band asks each time." aria-label="About starting without asking" onclick="event.preventDefault()"></span></label></div>
      <div class="field"><label class="tgl"><input type="checkbox" id="s_talkover" data-save> Let me talk over Claude<span class="mx-help" tabindex="0" title="Echo cancelling, for laptop speakers; not needed with headphones." aria-label="About talking over Claude" onclick="event.preventDefault()"></span></label>
        <div class="hint fine" id="s_talkovernote" hidden></div></div>
    </div></div>
    <div class="pane" data-p="recording"><div class="sec">
      <div class="field"><label>Record<span class="mx-help" id="s_caphint" tabindex="0" title="" aria-label="About Record" onclick="event.preventDefault()"></span></label><input type="hidden" id="s_capture">
        <div class="seg" id="capseg"><button type="button" data-c="auto" onclick="setCapture('auto')">Automatic</button><button type="button" data-c="mic" onclick="setCapture('mic')">Mic only</button><button type="button" data-c="meeting" onclick="setCapture('meeting')">Meeting audio only</button></div>
        </div>
      <div class="field" id="micf"><label>Microphone</label><select id="s_mic" data-save></select></div>
      <div class="field"><label>Speech model<span class="mx-help" id="s_speechhint" tabindex="0" title="" aria-label="About the speech model" onclick="event.preventDefault()"></span></label><input type="hidden" id="s_speech">
        <div class="seg" id="speechseg"><button type="button" data-z="small" onclick="setSpeech('small')">Small</button><button type="button" data-z="standard" onclick="setSpeech('standard')">Standard</button></div>
        <label class="tgl" style="margin-top:10px"><input type="checkbox" id="s_english" onchange="setSpeech()"> I speak English only</label>
        <div class="hint" id="s_speechdl" hidden></div></div>
      <div class="field"><label class="tgl"><input type="checkbox" id="s_speakers" data-save> Label speakers</label></div>
      <div class="field"><label class="tgl"><input type="checkbox" id="s_remember" data-save> Recognize voices I have named<span class="mx-help" tabindex="0" title="Voice prints stay on this Mac. Your own voice is learnt from calls, so in a room only you talk to Claude." aria-label="About voice recognition" onclick="event.preventDefault()"></span></label>
        <div class="hint" id="s_voices"></div>
        <div class="hint" id="myvoicef" hidden><span id="s_myvoice"></span><a href="#" class="dotlink" id="s_myforget" onclick="event.preventDefault();forgetMyVoice()">Forget</a></div></div>
      <div class="field"><label>Instructions</label><div class="row"><span id="s_prompt" class="hint" style="margin:0;flex:1">What Claude is told: how to listen, when to answer, the meeting skill.</span>
        <button type="button" class="btn" onclick="openPrompt()">View and edit…</button></div></div>
      <div class="field"><label>Claude Code gets the meeting<span class="mx-help" tabindex="0" title="A meeting started from Claude Code. When you ask about it: Claude reads the meeting so far when you ask. As it goes: Claude follows it all along." aria-label="About Claude Code and your meeting"></span></label><select id="s_litepass" data-save><option value="ask">When you ask about it</option><option value="live">As it goes</option></select></div>
      <div class="field"><div class="setrow"><label>Update Claude every N sentences<span class="mx-help" tabindex="0" title="Lower keeps Claude more current; higher means fewer calls." aria-label="About updates to Claude"></span></label><input id="s_push" class="num" data-save type="number" min="1" max="200"></div></div>
      <div class="field"><button type="button" class="btn" id="micbtn" onclick="micTest()">Test recording</button>
        <div id="mictest" class="checks"></div></div>
    </div></div>
    <div class="pane" data-p="dictation"><div class="sec" id="dictsec">
      <div class="field"><label class="tgl"><input type="checkbox" id="s_dict" onchange="setDictation(this.checked)"> Dictation in any app<span class="mx-help" id="s_dictwhy" hidden tabindex="0" aria-label="" onclick="event.preventDefault()"></span></label>
        <div class="hint" id="dictstate"></div><div class="err" id="dicterr" style="display:none"></div></div>
      <div class="checks" id="dictperms"></div>
      <div class="field pf" id="kshortf" hidden><label>Shortcut</label>
        <div class="row"><button type="button" class="pfield" id="kfield" onclick="openShortcuts()"></button></div>
        <div class="tmenu pmenu" id="kmenu" role="menu" hidden></div>
        <div class="hint" id="khint"></div></div>
      <div class="field" id="kholdf" hidden><label class="tgl"><input type="checkbox" id="s_hold" onchange="setHold(this.checked)"> Hold to talk<span class="mx-help" tabindex="0" title="Hold the shortcut while you speak; let go to paste." aria-label="About hold to talk" onclick="event.preventDefault()"></span></label></div>
      <div class="field" id="kwordsf" hidden><label>Word fixes</label>
        <div class="row"><span class="hint" id="kwords" style="margin:0;flex:1"></span>
          <button type="button" class="btn sm" onclick="editWords()">Edit…</button></div></div>
    </div></div>
    <div class="pane" data-p="usage"><div class="sec">
      <div class="seg" id="urange"><button type="button" data-r="3h" onclick="loadUsage('3h')">3 hours</button><button type="button" data-r="today" onclick="loadUsage('today')">Today</button>
        <button type="button" data-r="7d" onclick="loadUsage('7d')">7 days</button><button type="button" data-r="30d" onclick="loadUsage('30d')">30 days</button></div>
      <div id="ubody"></div></div></div>
    <div class="err" id="serr" style="display:none"></div></div>
</form></dialog>
<dialog id="askdlg" class="ask"><form method="dialog">
  <div class="dhead"><h3 id="asktitle" tabindex="-1"></h3></div>
  <div class="dbody"><p id="askbody"></p></div>
  <div class="dfoot"><button class="btn" value="cancel">Cancel</button><button class="btn primary" id="askok" value="ok">OK</button></div>
</form></dialog>
<dialog id="promptdlg" class="big">
  <div class="dhead"><h3 tabindex="-1" autofocus>Claude's instructions</h3><span id="pstate" class="hint" style="margin:0 6px 0 0"></span>
    <button type="button" class="iconbtn" title="Close" onclick="promptdlg.close()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
  <div class="dbody"><div class="hint">What Claude is told at the start of every session: how to listen, when to answer, and the meeting skill.
    {session} and {workspace} are filled in for you. Changes apply from the next session.</div>
    <textarea id="ptext" spellcheck="false"></textarea><div class="err" id="perr" style="display:none"></div></div>
  <div class="dfoot"><button type="button" class="btn" onclick="resetPrompt()" style="margin-right:auto">Reset to default</button>
    <button type="button" class="btn" onclick="promptdlg.close()">Cancel</button>
    <button type="button" class="btn primary" onclick="savePrompt()">Save</button></div>
</dialog>
<script>
let S={sessions:[],live:null,starting:null,agent:{}},voice=null;     // the app's state (/api/state); the call's voice loop
const $=id=>document.getElementById(id);
// A freeze in the page itself: a 250 ms timer that fires more than 500 ms late means its JavaScript was
// busy. Said to ui.log (/api/stall) with the view it was on, and in the first minute also every request
// that took over 1 s. Not while hidden: macOS slows a hidden page's timers. Nor WebKit's once a second for
// a window it thinks idle, which it does with the page still visible: each tick then comes 0.75 s late,
// steadily, and that was logged as busy every second (the past list's paint takes 3 ms). See stallTick.
const T0=performance.now();
const SBT="__SB_TOKEN__";   // this launch's token: every request carries it (the app checks)
const SETWIN="__SB_SETWIN__"==="1";   // this page is the Settings window's (/settings): Settings alone, nothing else of the app
function stallSay(ms,where){fetch("/api/stall",{method:"POST",headers:{"Content-Type":"application/json","X-Standby-Token":SBT},body:JSON.stringify({ms:Math.round(ms),where})}).catch(()=>{})}
// How late the tick was -> the busy time to say, or 0. One tick late between ticks on time: busy (said one
// tick on). Later than WebKit's once a second can make it: busy. Late tick after late tick: the once a second.
const STALL={prev:0,prev2:0};
function stallTick(late,st){const say=late>1250?late:late<500&&st.prev>500&&st.prev<=1250&&st.prev2<500?st.prev:0;
  st.prev2=st.prev;st.prev=late;return say}
(()=>{let last=performance.now(),shown=!document.hidden;setInterval(()=>{const n=performance.now(),late=n-last-250;last=n;
  const say=shown&&!document.hidden?stallTick(late,STALL):(STALL.prev=STALL.prev2=0);
  if(say&&say<600000)stallSay(say,(typeof MX!=="undefined"&&MX.view?JSON.stringify(MX.view):"page")+(n-T0<60000?", at start":""));
  shown=!document.hidden},250)})();
const setHTML=(el,h)=>{if(el&&el._h!==h){el.innerHTML=h;el._h=h}};
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function inl(s){const codes=[];s=esc(s).replace(/`([^`]+)`/g,(m,c)=>{codes.push(c);return "\u0000"+(codes.length-1)+"\u0000"});
  s=s.replace(/\*\*(.+?)\*\*/g,"<strong>$1</strong>").replace(/(^|[^\w*])\*(?!\s)(.+?)\*(?!\w)/g,"$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2">$1</a>').replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,'$1<a href="$2">$2</a>');
  return s.replace(/\u0000(\d+)\u0000/g,(m,k)=>"<code>"+codes[+k]+"</code>")}
function md(src){const L=String(src||"").replace(/\r/g,"").split("\n"),out=[],para=[];let i=0,m;
  const flush=()=>{if(para.length){out.push("<p>"+para.map(inl).join("<br>")+"</p>");para.length=0}};
  const LI=/^(\s*)([-*+]|\d+[.)])\s+(.*)/;
  while(i<L.length){const l=L[i];
    if(/^\s*```/.test(l)){flush();const code=[];i++;while(i<L.length&&!/^\s*```/.test(L[i]))code.push(L[i++]);i++;
      out.push('<pre><button class="cp" onclick="copyCode(this)">Copy</button><code>'+esc(code.join("\n"))+"</code></pre>");continue}
    if(m=l.match(/^(#{1,6})\s+(.*)/)){flush();const n=Math.min(3,m[1].length);out.push("<h"+n+">"+inl(m[2])+"</h"+n+">");i++;continue}
    if(/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(l)){flush();out.push("<hr>");i++;continue}
    if(/^\s*>/.test(l)){flush();const q=[];while(i<L.length&&/^\s*>/.test(L[i]))q.push(L[i++].replace(/^\s*>\s?/,""));
      out.push("<blockquote>"+md(q.join("\n"))+"</blockquote>");continue}
    if(/^\s*\|.*\|\s*$/.test(l)&&i+1<L.length&&/^\s*\|?\s*:?-{2,}/.test(L[i+1])){flush();
      const row=r=>r.trim().replace(/^\|/,"").replace(/\|$/,"").split("|").map(c=>inl(c.trim()));
      const head=row(l),body=[];i+=2;while(i<L.length&&/^\s*\|/.test(L[i]))body.push(row(L[i++]));
      out.push("<table><thead><tr>"+head.map(c=>"<th>"+c+"</th>").join("")+"</tr></thead><tbody>"+
        body.map(r=>"<tr>"+r.map(c=>"<td>"+c+"</td>").join("")+"</tr>").join("")+"</tbody></table>");continue}
    if(m=l.match(LI)){flush();const ol=/\d/.test(m[2]),items=[];
      while(i<L.length&&(m=L[i].match(LI))){items.push(inl(m[3]));i++;
        while(i<L.length&&/^\s{2,}\S/.test(L[i])&&!LI.test(L[i]))items[items.length-1]+="<br>"+inl(L[i++].trim())}
      out.push((ol?"<ol>":"<ul>")+items.map(x=>"<li>"+x+"</li>").join("")+(ol?"</ol>":"</ul>"));continue}
    if(!l.trim()){flush();i++;continue}
    para.push(l);i++}
  flush();return out.join("")}
const clock=ts=>ts?new Date(ts*1000).toLocaleTimeString([],{hour:"numeric",minute:"2-digit"}):"";
function elapsed(info){const r=(info.runs||[])[(info.runs||[]).length-1];return r?r.t+(Date.now()/1000-r.start):Date.now()/1000-info.started}
let CERR=null;
// Meeting audio needs Screen Recording: asked for from the meeting window's line (agents.js), and
// reset when macOS keeps it on for an older copy of Standby.
const STALE_WHY="Standby is switched on, but for an older copy of the app. Reset it, then allow it again.";
async function resetScreen(){
  if(!await ask("Reset Standby\u2019s permission?","macOS keeps this permission for each copy of Standby, and the switch you see belongs to an older one. "+
    "Reset clears Standby\u2019s own entry (tccutil reset ScreenCapture dev.standby.app), then macOS asks again. Once you allow it, Standby quits and opens again.","Reset"))return;
  let r={};try{r=await api("/api/screen/reset",{})}catch(e){r={error:"Standby is not answering"}}
  toast(r.error||r.reason||"Reset: allow Standby when macOS asks")}
async function allowScreen(confirmed){let r={};
  try{r=await api("/api/screen/permission",confirmed?{confirm:true}:{})}catch(e){r={error:"Standby is not answering"}}
  if(r.state==="confirm"&&!confirmed)return await ask("Allow Screen Recording?",r.confirm||"","Continue")?allowScreen(true):undefined;
  // Still refused after the prompt: if the switch is on already, it's an older copy's.
  if(r.state==="settings")return toast(r.reason||"Allow Standby in System Settings",{label:"It\u2019s on already",fn:resetScreen});
  toast(r.error||r.reason||(r.state==="granted"?"Screen Recording is allowed":"Allow Standby in System Settings"))}
async function forgetVoices(){if(!await ask("Forget all voices?","Names already given in past sessions stay.","Forget",true))return;
  await api("/api/forget-voices",{});$("s_voices").textContent="No remembered voices yet";MYVOICE=null;paintMyVoice();toast("Forgot all voices")}
// The user's own voice: learnt from talks, so in a room only they
// talk to Claude. Shown while Remember voices is on; forgetting it leaves the named ones.
let MYVOICE=null;
function paintMyVoice(){const on=$("s_remember").checked,sec=MYVOICE;$("myvoicef").hidden=!on;$("s_myforget").hidden=sec==null;
  $("s_myvoice").textContent=sec==null?"Your voice \u00b7 not learnt yet":"Your voice"+(sec>=60?" \u00b7 "+Math.round(sec/60)+" min":"")}
async function forgetMyVoice(){if(!await ask("Forget your voice?","Standby learns it again from your next talks.","Forget"))return;
  const r=await api("/api/forget-voices",{me:true});if(r&&r.error)return toast(r.error);MYVOICE=null;paintMyVoice();toast("Forgot your voice")}
$("s_remember").addEventListener("change",paintMyVoice);
async function copyText(t){const r=await api("/api/copy-text",{text:t});toast(r.error?r.error:"Copied")}
function copyCode(btn){copyText(btn.parentNode.querySelector("code").textContent)}
document.addEventListener("click",e=>{const a=e.target&&e.target.closest&&e.target.closest("a[href]");if(!a||!/^https?:/.test(a.getAttribute("href")))return;
  e.preventDefault();if(window.pywebview&&window.pywebview.api)window.pywebview.api.open_url(a.getAttribute("href"));else window.open(a.getAttribute("href"),"_blank")});
const mmss=t=>{t=Math.max(0,Math.floor(t));const h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=t%60;
  return (h?h+":"+String(m).padStart(2,"0"):String(m).padStart(2,"0"))+":"+String(s).padStart(2,"0")};
let HIDS=0;document.addEventListener("visibilitychange",()=>{if(document.hidden)HIDS++});
async function api(path,body){const t0=performance.now(),h0=HIDS+(document.hidden?1:0);try{return await api0(path,body)}finally{const ms=performance.now()-t0;
  if(ms>1000&&t0-T0<60000)stallSay(ms,"fetch "+path.split("?")[0]+", at start"+(HIDS+(document.hidden?1:0)!==h0||document.hidden?", the window hidden meanwhile":""))}}
async function api0(path,body){const r=await fetch(path,body!==undefined?{method:"POST",headers:{"Content-Type":"application/json","X-Standby-Token":SBT},
  body:JSON.stringify(body)}:{headers:{"X-Standby-Token":SBT}});return r.json()}
function toast(t,act){const e=$("toast");e.textContent=t;e.classList.toggle("act",!!act);
  if(act){const b=document.createElement("button");b.textContent=act.label;b.onclick=()=>{e.classList.remove("on");act.fn()};e.appendChild(b)}
  e.classList.add("on");clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove("on"),act?8000:1800)}
let refreshSent=0,refreshShown=0;
async function refresh(){const n=++refreshSent,st=await api("/api/state");
  if(n<refreshShown)return;refreshShown=n;const was=JSON.stringify(CERR);S=st;CERR=(S&&S.claude_error)||null;
  if(JSON.stringify(CERR)!==was)paint()}      // Standby's view polls its own: it repaints only for what this says (Claude reachable)
function speakerLabel(r,speakers){const sp=speakers||{alias:{},names:{}};
  if(r.spk!==undefined&&r.spk!==null){let id=String(r.spk),seen=0;while(sp.alias[id]!==undefined&&seen++<20)id=String(sp.alias[id]);
    const known=(sp.known||{})[id];return {id,name:sp.names[id]||known||("Speaker "+id),known:!sp.names[id]&&!!known}}
  if(r.speaker==="Me"){const known=(sp.known||{}).me;return {id:"me",name:sp.names.me||known||"You",known:!sp.names.me&&!!known}}return null}
function color(id){if(id==="me")return"var(--text)";return"var(--s"+((parseInt(id)-1)%8+8)%8+")"}
function paint(){if(typeof MX==="object"&&MX.view)mxPaintSoon()}      // Standby's view paints itself (agents.js)
let PR=null;
// Recording: Automatic is the mic, and the sound the Mac plays, from any app playing.
const CAPHINT={auto:"Your mic, and the sound your Mac plays, a call or a video, whenever something is playing.",
  mic:"Only your mic. With headphones on, the other side isn't recorded.",
  meeting:"Only the sound your Mac plays, without your mic."};
// Speech model: measured, and on an 8 GB Mac
// Small is about 1 GB there, Standard about 3. Small is English only.
const SPEECHHINT={small:"Least memory, about 1 GB. More mistakes in a busy room.",
  standard:"The balance of memory and accuracy."};
let SPEECH=null,speechT=null,SPEECHSET=false,SPMAC={};    // SPEECHSET: the user picked one; SPMAC: this Mac's
function speechHint(z,en){const small=SPMAC.default==="small";      // an 8 GB Mac: Small is its default
  if(!en)return "Standard, in 25 languages. Small is English only.";
  if(z==="small"&&small&&!SPMAC.picked&&!SPEECHSET)return "Chosen for this Mac's "+SPMAC.memory+" GB. About 1 GB of memory; more mistakes in a busy room.";
  if(z==="standard"&&small)return "Fewer mistakes. About 3 GB of memory: heavy for this Mac.";
  return SPEECHHINT[z]}
function setSpeech(z,quiet){const en=$("s_english").checked;z=z||$("s_speech").value||"standard";
  if(!quiet)SPEECHSET=true;                               // saved only then: it used to store the default
  $("s_speech").value=z;const shown=en?z:"standard";      // the size is kept for English again
  document.querySelectorAll("#speechseg button").forEach(b=>{b.classList.toggle("on",b.dataset.z===shown);b.disabled=!en&&b.dataset.z!=="standard"});
  setHelp("s_speechhint",speechHint(z,en));
  if(!quiet)autosave().then(()=>pollSpeech())}
function paintSpeech(s){SPEECH=s||SPEECH;const d=$("s_speechdl"),sp=SPEECH;if(!sp){d.hidden=true;return}
  const dl=sp.download;let t=sp.moved?sp.moved+" ":"";
  if(dl&&dl.state==="downloading")t="Downloading "+sp.model+"… "+Math.floor(100*dl.done/Math.max(1,dl.total))+"%. Used from your next session.";
  else if(dl&&dl.state==="error")t="Couldn't download "+sp.model+". It tries again when a session starts.";
  else if(!sp.ready)t=sp.model+" downloads when a session starts ("+(sp.bytes/1e9).toFixed(1)+" GB, once).";
  d.textContent=t;d.hidden=!t}
async function pollSpeech(){clearTimeout(speechT);try{paintSpeech(await api("/api/speech"))}catch(e){}
  if(SPEECH&&SPEECH.download&&SPEECH.download.state==="downloading"&&settings.open)speechT=setTimeout(pollSpeech,1000)}
function setCapture(c,quiet){$("s_capture").value=c;document.querySelectorAll("#capseg button").forEach(b=>b.classList.toggle("on",b.dataset.c===c));
  setHelp("s_caphint",CAPHINT[c]||"");$("micf").style.display=c==="meeting"?"none":"";if(!quiet)autosave()}
async function openPrompt(){const r=await api("/api/prompt");if(r.error){toast(r.error);return}PR=r;$("ptext").value=r.prompt;
  $("pstate").textContent=r.custom?"Customized":"Default";$("perr").style.display="none";promptdlg.showModal()}
async function savePrompt(){const text=$("ptext").value;
  const r=PR&&text===PR.default?await api("/api/prompt",{reset:true}):await api("/api/prompt",{text});
  if(r.error){$("perr").textContent=r.error;$("perr").style.display="";return}
  promptdlg.close();$("s_prompt").textContent=PR&&text===PR.default?"Default instructions.":"Customized instructions.";toast("Saved. Applies from the next session.")}
async function resetPrompt(){if(!PR||!await ask("Reset the instructions?","Your edits are replaced by the default instructions.","Reset",true))return;
  const r=await api("/api/prompt",{reset:true});if(r.error){$("perr").textContent=r.error;$("perr").style.display="";return}
  PR.custom=false;$("ptext").value=PR.default;$("pstate").textContent="Default";$("s_prompt").textContent="Default instructions.";toast("Reset to default")}
async function micTest(){const s=await api("/api/settings",formSettings());if(s.error){$("serr").textContent=s.error;$("serr").style.display="";return}
  const r=await api("/api/mic-test",{});if(r.error){$("mictest").innerHTML=`<div class="bad">${esc(r.error)}</div>`;return}
  $("micbtn").disabled=true;pollMicTest()}
async function pollMicTest(){const r=await api("/api/mic-test");$("mictest").innerHTML=micTestHtml(r);
  if(r.state==="running")setTimeout(pollMicTest,400);else $("micbtn").disabled=false}
function micTestHtml(r){const wait=t=>`<div class="check"><span class="spin"><i></i><i></i><i></i></span><span class="v">${t}</span></div>`;
  if(!r.channels.length)return r.state==="running"?wait("Loading the speech model…"):`<div class="err">${esc(r.error||"The test did not run.")}</div>`;
  // Each channel records its 5 seconds in turn (Automatic: the mic, then the call); the call's
  // channel names its app once there is one.
  return r.channels.map((c,i)=>{const o=c.channel==="Others",name=o?"Meeting audio"+(c.device?" · "+esc(c.device):""):"Microphone",last=i===r.channels.length-1;
    if(c.peak===null)return wait(`${name}: ${o?"keep it playing":"say something"} for 5 seconds…`);
    const lvl=Math.min(100,Math.round(Math.sqrt(c.peak)*200));
    let v;if(c.heard.length)v=`<div class="ok">✓ Heard: “${esc(c.heard.join(" "))}”</div>`;
    else if(last&&r.state==="running"&&!c.problem.length)v=wait("Transcribing…");
    else v=`<div class="bad">✗ ${esc(c.problem.join(" ")||"Nothing transcribed.")}</div>`;
    return `<div class="mt"><div class="row"><b>${name}</b><span class="meter"><span style="width:${lvl}%"></span></span></div>${v}</div>`}).join("")}
const VMIC='<svg class="vmic" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
// A folder's path with the home folder as ~ (the agents view's).
const tilde=p=>(p||"").replace(/^\/Users\/[^/]+(?=\/|$)/,"~");
// The empty state's waveform (C · Wave): soft bars, quieter at the ends.
const EMPTYWAVE='<span class="wave" aria-hidden="true">'+[10,22,38,53,34,19,43,58,29,14,24,10].map((h,n)=>`<i style="height:${h}px;opacity:${n&&n<11?1:.6};animation-delay:-${(n*.22).toFixed(2)}s"></i>`).join("")+"</span>";
// A yes-or-no question in Standby's own dialog: true for OK. danger: OK in red.
function ask(title,body,ok,danger){return new Promise(res=>{const d=$("askdlg"),b=$("askok");
  $("asktitle").textContent=title;$("askbody").textContent=body||"";$("askbody").hidden=!body;b.textContent=ok;b.classList.toggle("destroy",!!danger);
  d.returnValue="";d.onclose=()=>res(d.returnValue==="ok");d.showModal();b.focus()})}
const SPIN='<span class="spin"><i></i><i></i><i></i></span>';
function attachVoice(sid){if(voice)return;voice={sid,lvl:0};$("voice").style.display="";voiceLoop(voice)}
// The window moves by its title areas ([data-drag]); a double-click there zooms it.
document.addEventListener("mousedown",e=>{const w=window.pywebview&&window.pywebview.api;
  if(!w||!w.window_drag||e.button!==0||!e.target.closest("[data-drag]"))return;
  if(e.target.closest("button,input,textarea,select,a,[contenteditable=true],[data-nodrag]"))return;
  if(e.detail===2)w.window_zoom();else w.window_drag()});
for(const id of["setmain","setnav"])$(id).addEventListener("scroll",e=>$("sethead").classList.toggle("scrolled",e.target.scrollTop>0),{passive:true});
document.addEventListener("pointerdown",e=>{const c=e.target.closest;if(!c)return;
  if(!$("kmenu").hidden&&!e.target.closest("#kmenu")&&!e.target.closest("#kfield"))$("kmenu").hidden=true;});

document.addEventListener("keydown",e=>{if(e.metaKey&&e.key===","){e.preventDefault();openSettings()}});
// ⌘Q while a meeting records: the Mac asks the page first (the native side).
async function askQuit(){const a=window.pywebview&&window.pywebview.api;
  if(await ask("Stop recording and quit?","What's recorded so far is saved.","Quit",true)&&a&&a.quit)a.quit()}
// A call's voice: the server runs the conversation (voice.py); the page shows it and passes on taps.
async function voiceLoop(v){while(voice===v){let st=null;try{st=await api("/api/voice/"+v.sid)}catch(e){}
    if(voice!==v)return;
    // an agent's call the app still has live (its voice can read off for a moment as it starts): keep waiting
    if(st&&st.state==="off"&&typeof MX==="object"&&MX.call&&MX.call.session===v.sid&&(((mxAg(MX.call.agent)||{}).live||{}).call===v.sid)&&Date.now()-(v.since||(v.since=Date.now()))<15000){
      paintVoice({state:"starting"});await new Promise(r=>setTimeout(r,250));continue}
    if(st&&st.state==="off"){voice=null;$("voice").style.display="none";const k=S.sessions.find(s=>s.id===v.sid);
      if(st.error)toast((k&&k.kind==="talk"?"Call ended: ":"Voice mode ended: ")+st.error);
      // a call that ended (the server says its voice is off): let go the one way a call ends (agents.js)
      if(typeof MX==="object"&&MX.call&&MX.call.session===v.sid)mxCallGone(MX.call);paint();return}
    if(st)paintVoice(st);await new Promise(r=>setTimeout(r,110))}}
function paintVoice(st){const s=st.state||"starting";
  // hearing you: the voice's level over a whisper, held 0.7 s so the orb doesn't flicker between words
  if(s==="listening"&&(st.level||0)>.02)voice.heard=Date.now();const hearing=s==="listening"&&Date.now()-(voice.heard||0)<700;
  $("voice").className="voice "+s+(st.holding?" holding":"")+(hearing?" hearing":"");
  const lvl=s==="listening"?Math.min(1,Math.sqrt((st.level||0)*5)):0;voice.lvl=voice.lvl*.55+lvl*.45;
  $("orb").style.setProperty("--s",(1+voice.lvl*.24).toFixed(3));
  $("orb").title=s==="thinking"||s==="speaking"?"Tap to interrupt":"";
  const work=s==="thinking"&&st.actions?stepLine(st):"";$("vsteps").textContent=work;
  // the orb says listening, thinking, speaking and paused by its motion (the user, 2026-10-27); the line
  // says only what the orb can't: a notice, on hold, what Claude is doing, starting, paused for dictation
  $("vstate").textContent=st.notice?st.notice:st.holding?HOLDING:s==="thinking"?(!work&&stepLine(st))||"":
    ({starting:"Starting…",paused:"Paused while you dictate"}[s]||"");
  paintPrep(st);
  $("vyou").textContent=(st.you||"").replace(/^\[CALL START\]\s*/,"");$("vclaude").textContent=s==="speaking"?(st.claude||""):"";
  // Pause and Resume answer at once (VPEND, below): until the voice has done it, the button says so
  const m=$("vmute");if(VPEND&&(!!st.muted===VPEND.to||Date.now()-VPEND.at>8000)){
    if(!!st.muted!==VPEND.to)toast(VPEND.to?"Couldn\u2019t pause the call":"Couldn\u2019t resume the call");VPEND=null;m.disabled=false;m.classList.remove("busy")}
  if(!VPEND)vmuteIs(m,!!st.muted);
  // paused with finished work held for Resume: a small red dot on Resume, gone once it's said
  const wt=!!(st.muted&&st.waiting);m.classList.toggle("waiting",wt);if(wt)m.title="Something\u2019s waiting: resume to hear it"}
// While Claude works: its steps counted, named when the step has a plain name, so
// work reads as work, not as a slow or broken line: "Taking actions · 2", "Searching your files · 3".
// "Hold on": Claude's answer waits, up to two minutes, until you say "go on".
const HOLDING="On hold · say “go on”";
const stepLine=st=>!st?"":st.actions?(st.action||"Taking actions")+" · "+st.actions:st.step?st.step+"…":"";
// How far along Standby's voice is: the server's number, else read from its sentence.
const prepPct=x=>x&&x.preparing_pct!=null?x.preparing_pct:x&&x.preparing&&/(\d+)%/.test(x.preparing)?+/(\d+)%/.exec(x.preparing)[1]:null;
function paintPrep(st){const el_=$("vprep"),on=!!(st&&st.preparing),p=prepPct(st);el_.hidden=!on;if(!on)return;
  el_.classList.toggle("unknown",p===null);$("vprepfill").style.setProperty("--p",p===null?"30%":Math.max(4,p)+"%");
  $("vpreptxt").textContent="Getting Standby's voice ready"+(p===null?"…":" · "+p+"%")}
async function voiceTap(){if(voice)await api("/api/voice",{id:voice.sid,action:"interrupt"})}
// The call's Pause (the user, 2026-10-22: it took a second, and nothing showed it was heard): at once
// "Pausing…" (or "Resuming…"), not pressable twice, until the voice says it has; refused, it's put back and why said
let VPEND=null;
// The call's Pause is an icon (the user, 2026-10-23: the header was cramped): a pause mark, a play mark while
// paused; its word is its tip. Mid-way it says so in its tip ("Pausing…"), dimmed and gently pulsing.
const VPAUSE='<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4.5" width="4.2" height="15" rx="1.3"/><rect x="13.8" y="4.5" width="4.2" height="15" rx="1.3"/></svg>',VPLAY='<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.2v13.6a1 1 0 0 0 1.5.86l10.6-6.8a1 1 0 0 0 0-1.72L9.5 4.34A1 1 0 0 0 8 5.2z"/></svg>';
function vmuteIs(m,paused,busy){m.classList.toggle("on",paused);m.classList.toggle("busy",!!busy);const w=busy||(paused?"Resume":"Pause");
  if(m._p!==paused){m.innerHTML=paused?VPLAY:VPAUSE;m._p=paused}m.title=w;m.setAttribute("aria-label",w)}
async function voiceMute(){if(!voice||VPEND)return;const m=$("vmute"),to=!m.classList.contains("on");
  VPEND={to,at:Date.now()};vmuteIs(m,!to,to?"Pausing\u2026":"Resuming\u2026");m.disabled=true;
  let r=null;try{r=await api("/api/voice",{id:voice.sid,action:to?"mute":"unmute",from:"full window"})}catch(e){r={error:"Standby isn\u2019t answering"}}
  if(r&&r.error){VPEND=null;m.disabled=false;vmuteIs(m,!to);toast(r.error)}}
async function closeVoice(){if(!voice)return;const v=voice;voice=null;$("voice").style.display="none";
  await api("/api/voice",{id:v.sid,action:"stop"})}
let spkEditing=false;
// Settings apply as they change: no Save. Text fields save when you leave them.
// Usage: how much each model is used, from Claude Code's own replies (/api/usage).
// Standby's own models only (sbusage.STANDBY_KINDS): what it hands your Claude Code session is that session's.
const KIND={voice:"Voice turns",briefing:"Voice briefings",title:"Titles",decisions:"Call and meeting summaries",
  "cc-gist":"Claude Code summaries for calls","call-summary":"Calls added to Claude Code",words:"Word fixes"};
const fam=m=>/opus/i.test(m)?"Opus":/sonnet/i.test(m)?"Sonnet":/haiku/i.test(m)?"Haiku":m;
const compact=n=>n>=1e6?(n/1e6).toFixed(n>=1e7?0:1).replace(/\.0$/,"")+"M":n>=1e4?Math.round(n/1e3)+"K":n>=1e3?(n/1e3).toFixed(1).replace(/\.0$/,"")+"K":String(n||0);
const toks=x=>(x.input||0)+(x.cache_write||0)+(x.output||0);
const tokTip=x=>compact((x.input||0)+(x.cache_write||0))+" in, "+compact(x.output||0)+" out"+(x.cache_read?", plus "+compact(x.cache_read)+" read from cache":"");
// Opens on the last 3 hours while a session is live ("how much is this call using"), else on
// today, until a range is picked.
let URANGE=null;
const SPAN={"3h":"in the last 3 hours",today:"today",["7d"]:"in the last 7 days",["30d"]:"in the last 30 days"};
async function loadUsage(r){URANGE=r||URANGE;const show=URANGE||(S&&S.live?"3h":"today");
  document.querySelectorAll("#urange button").forEach(b=>b.classList.toggle("on",b.dataset.r===show));
  let u=null;try{u=await api("/api/usage?range="+show)}catch(e){}paintUsage(u)}
function paintUsage(u){const t=u&&u.totals;
  if(!u||u.error){setHTML($("ubody"),'<div class="uempty"><b>Usage is not available right now</b><span>'+esc(u&&u.error||"Standby isn't answering.")+'</span></div>');return}
  if(!t||!t.calls){setHTML($("ubody"),u.since?'<div class="uempty"><b>No calls '+esc(SPAN[u.range]||"")+'</b><span>Try a longer range.</span></div>'
    :'<div class="uempty"><b>No calls yet</b><span>Usage shows here after your first meeting or call.</span></div>');return}
  let h='<div class="kpis"><div class="kpi"><span class="kl">Calls</span><b>'+t.calls.toLocaleString()+'</b></div>'+
    `<div class="kpi" title="${esc(tokTip(t))}"><span class="kl">Tokens</span><b>${compact(toks(t))}</b></div></div>`;
  // calls over time: one series, columns from one baseline, the value on hover
  const ser=u.series||[],max=Math.max(1,...ser.map(b=>b.calls||0)),day=u.bucket==="day",n=ser.length;
  const per={day:"per day",hour:"per hour","10 minutes":"per 10 minutes"}[u.bucket]||"";
  const lab=b=>day?new Date(b.t*1000).toLocaleDateString(undefined,{month:"short",day:"numeric"}):clock(b.t);
  const now=i=>u.partial&&i===n-1;
  h+=`<div class="uchart"><div class="uct"><span>Calls ${per}</span><span>${max} most</span></div><div class="ucols">`+
    ser.map((b,i)=>`<i${now(i)?' class="now"':""} title="${esc(lab(b))}${now(i)?(day?", so far":" to now"):""}: ${b.calls||0} call${b.calls===1?"":"s"}"><b style="height:${b.calls?Math.max(4,Math.round(b.calls/max*100)):0}%"></b></i>`).join("")+
    `</div><div class="uax"><span>${n?esc(lab(ser[0])):""}</span><span>${n?(u.partial?"Now":esc(lab(ser[n-1]))):""}</span></div></div>`;
  const row=(name,x,tip,extra)=>`<tr><td title="${esc(tip||"")}">${esc(name)}</td>${extra||""}<td>${(x.calls||0).toLocaleString()}</td><td title="${esc(tokTip(x))}">${compact(toks(x))}</td></tr>`;
  const hd=(first,extra)=>`<thead><tr><th>${first}</th>${extra||""}<th>Calls</th><th>Tokens</th></tr></thead>`;
  h+='<h5>By model</h5><table class="utab">'+hd("Model")+"<tbody>"+(u.models||[]).map(m=>row(fam(m.model),m,m.model)).join("")+"</tbody></table>";
  h+='<h5>By kind</h5><table class="utab kinds"><thead><tr><th>Kind</th><th>Calls</th><th>Tokens</th></tr></thead><tbody>'+
    (u.kinds||[]).map(k=>`<tr><td>${esc(KIND[k.kind]||"Other")}</td><td>${(k.calls||0).toLocaleString()}</td><td title="${esc(tokTip(k))}">${compact(toks(k))}</td></tr>`).join("")+"</tbody></table>";
  const since=u.since?" since "+new Date(u.since*1000).toLocaleDateString(undefined,{month:"short",day:"numeric"}):"";
  h+='<p class="hint">What Standby\'s own models did'+since+': the call\'s voice, summaries, titles and word fixes. Work it hands your Claude Code session counts there, not here. Tokens are what was sent and written; reads from cache are in each tooltip.</p>';
  setHTML($("ubody"),h)}
function setHelp(id,t){const e=$(id);e.title=t||"";delete e.dataset.mxtip;e.setAttribute("aria-label",t?"About this setting":"");e.hidden=!t}
// Settings' list of sections (setHome), or one section open in its place (setPane); back, and Esc, go from
// a section to the list and from the list out of Settings
let SETP=null;
function setPane(p){if(p==="usage")loadUsage();SETP=p;$("setback").style.display="";const m=$("setmain");m.scrollTop=0;m.hidden=false;$("setnav").hidden=true;$("sethead").classList.remove("scrolled");
  const b=document.querySelector('.setnav button[data-p="'+p+'"] b');$("settitle").textContent=b?b.textContent:"Settings";
  document.querySelectorAll(".pane").forEach(x=>x.classList.toggle("on",x.dataset.p===p))}
// In the Settings window the list is the top: no Back there, and Esc stays (closing is the window's own).
function setHome(){SETP=null;$("setback").style.display=SETWIN?"none":"";$("setmain").hidden=true;$("setnav").hidden=false;$("setnav").scrollTop=0;$("sethead").classList.remove("scrolled");$("settitle").textContent="Settings"}
function setBack(){if(SETP)setHome();else if(!SETWIN)settings.close()}
let savedT=null;
async function autosave(){const r=await api("/api/settings",formSettings());
  if(r.error){$("serr").textContent=r.error;$("serr").style.display="";return false}
  $("serr").style.display="none";const k=$("ssaved");k.classList.add("on");clearTimeout(savedT);savedT=setTimeout(()=>k.classList.remove("on"),1600);return true}
settings.addEventListener("change",e=>{if(e.target.closest&&e.target.closest("[data-save]"))autosave().then(ok=>{if(ok&&e.target.id==="s_vmodel")watchModel()})});
settings.addEventListener("close",()=>{if(SETWIN&&window.pywebview&&pywebview.api.settings_close)pywebview.api.settings_close();else refresh()});
settings.addEventListener("cancel",e=>{if(SETP||SETWIN){e.preventDefault();if(SETP)setHome()}});
// Settings opens in a window of its own (one, brought to the front if it's open), so a call or a meeting
// stays where it is; in a browser, or the Settings window itself, here in the page.
async function openSettings(pane){if(!SETWIN&&window.pywebview&&pywebview.api&&pywebview.api.open_settings){
    try{const r=await pywebview.api.open_settings(typeof pane==="string"?pane:null);if(r&&r.ok)return}catch(e){}}
  return showSettings(pane)}
async function showSettings(pane){const r=await api("/api/settings"),s=r.settings;
  $("s_vmodel").value=s.voice_model||"haiku";$("s_push").value=s.push_lines;$("s_litepass").value=s.lite_transcript==="live"?"live":"ask";$("s_speakers").checked=!!s.speakers;
  $("s_remember").checked=!!s.remember_voices;
  // Talking over Claude (the duplex engine): from the next voice mode; a quiet line when the mic chosen can't
  $("s_autoaccept").checked=!!s.auto_accept;$("s_talkover").checked=!!s.talk_over;{const n=$("s_talkovernote");n.textContent=s.talk_over_note||"";n.hidden=!s.talk_over_note}
  MYVOICE=s.my_voice;paintMyVoice();
  $("s_voices").innerHTML=s.voices.length?s.voices.map(esc).join(", ")+' \u00b7 <a href="#" onclick="event.preventDefault();forgetVoices()">Forget all</a>':"No remembered voices yet";$("mictest").innerHTML="";
  api("/api/prompt").then(p=>{if(!p.error)$("s_prompt").textContent=p.custom?"Customized instructions.":"Default instructions."}).catch(()=>{});
  loadModels();
  setCapture(CAPHINT[s.capture]?s.capture:"auto",true);
  if(s.speech){$("s_english").checked=!!s.speech.english_only;$("s_speech").value=s.speech.size;SPEECHSET=false;SPMAC=s.speech;
    setSpeech(s.speech.size,true);paintSpeech(s.speech);pollSpeech()}
  const mics=r.mics||[];$("s_mic").innerHTML=`<option value="">Automatic (follows your Mac${s.mic_default?": "+esc(s.mic_default)+" now":""})</option>`+
    mics.filter(m=>!/zoomaudio|teams audio|blackhole|aggregate/i.test(m)).map(m=>`<option ${m===s.mic_device?"selected":""}>${esc(m)}</option>`).join("");
  $("serr").style.display="none";$("ssaved").classList.remove("on");setHome();if(typeof pane==="string")setPane(pane);await Promise.all([loadDict(),loadVoices()]);if(!settings.open)settings.showModal()}
// The voice's model in a call going on: it switches at the call's next reply, and this says how that went.
let MODELT=null;
function loadModels(){return api("/api/models").then(m=>{if(m.error)return;markModels(m.unavailable||[]);
  const n=$("s_vmodelnote"),w=m.switch&&m.switch.wanted===$("s_vmodel").value;n.textContent=w?m.switch.text:"";n.hidden=!w}).catch(()=>{})}
function watchModel(){loadModels();
  clearInterval(MODELT);let k=0;MODELT=setInterval(()=>{if(++k>40)clearInterval(MODELT);loadModels()},3000)}
function markModels(off){["s_vmodel"].forEach(id=>{for(const o of $(id).options){o.dataset.label=o.dataset.label||o.textContent;
  o.disabled=off.includes(o.value);o.textContent=o.disabled?o.dataset.label.split(" (")[0]+" (not available)":o.dataset.label}})}
function formSettings(){return {voice_model:$("s_vmodel").value,
  push_lines:$("s_push").value,lite_transcript:$("s_litepass").value,speakers:$("s_speakers").checked,remember_voices:$("s_remember").checked,talk_over:$("s_talkover").checked,auto_accept:$("s_autoaccept").checked,capture:$("s_capture").value,mic_device:$("s_mic").value,
  speech_model:SPEECHSET?($("s_speech").value||undefined):undefined,english_only:$("s_english").checked}}
let VOICES=null;
const Q={premium:"Premium",enhanced:"Enhanced",standard:"Standard"};
async function loadVoices(){try{VOICES=await api("/api/voices")}catch(e){VOICES=null}paintVoices()}
function autoVoice(v){if((v.standby||[]).length)return v.standby[0].name;const b=(v.voices||[]).find(x=>x.quality!=="standard");return b?b.name+", "+Q[b.quality]:"System voice"}
function voiceName(v,id){const x=(v.voices||[]).concat(v.standby||[]).find(x=>x.id===id);return x?x.name:autoVoice(v).split(",")[0]}
function paintVoices(){const v=VOICES||{},sel=$("s_voice"),vs=v.voices||[],ours=v.standby||[];
  let h=`<option value="">Automatic (${esc(autoVoice(v))})</option>`;
  if(ours.length)h+=`<optgroup label="Standby">`+ours.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")+"</optgroup>";
  for(const q of ["premium","enhanced","standard"]){const g=vs.filter(x=>x.quality===q);if(!g.length)continue;
    h+=`<optgroup label="${Q[q]}">`+g.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}${x.region?" ("+esc(x.region)+")":""}</option>`).join("")+"</optgroup>"}
  const mine=!!(v.current&&v.current.quality==="standby");
  sel.innerHTML=h;sel.value=v.chosen||"";$("s_voicehint").hidden=!VOICES||!!v.better||mine;
  setHelp("s_voicecredit",mine?v.credit||"":"");
  // Fetched the first time: say so here, rather than only when Try can't speak yet.
  const p=prepPct(v);$("s_voiceprep").hidden=!v.preparing;
  $("s_voiceprep").textContent=v.preparing?(ours[0]?ours[0].name:"Standby's voice")+" is getting ready"+(p===null?"…":", "+p+"%")+". A one-time download; your Mac's voice speaks meanwhile.":"";
  paintTry(v.trying)}
async function pickVoice(id){TRYFAIL=null;const r=await api("/api/voices",{id:id||null});if(r.error){toast(r.error);return}
  if(r.voices)VOICES=r.voices;paintVoices();toast("Voice mode will use "+voiceName(VOICES||{},id))}
// Try: a macOS voice is said before the answer comes. Standby's own goes on in the
// background, its first time a download of about a minute: followed here. The button says
// what is really happening ("Playing…" only while it plays); how far goes on the line under it.
let TRYING=null,TRYFAIL=null;
const TRYWORD={starting:"Starting…",downloading:"Downloading…",loading:"Starting…",playing:"Playing…"};
function fmtLeft(s){return s===null||s===undefined?"":s<10?" · under 10 s left":s<60?" · about "+Math.ceil(s/10)*10+" s left":" · about "+Math.ceil(s/60)+" min left"}
function paintTry(t){const b=$("s_voicetry"),on=!!(t&&TRYWORD[t.state]),line=$("s_tryprep"),f=TRYFAIL;
  b.disabled=on;b.textContent=on?TRYWORD[t.state]:"Try";
  const dl=on&&t.state==="downloading";line.hidden=!dl&&!(f&&!on);
  line.classList.toggle("failed",!!(f&&!on));line.classList.toggle("unknown",dl&&t.pct===null);
  if(dl){$("s_tryprept").textContent="Downloading "+t.voice+", once only"+(t.pct===null?"…":": "+t.pct+"%"+fmtLeft(t.left));
    $("s_trypfill").style.setProperty("--p",t.pct===null?"30%":Math.max(4,t.pct)+"%")}
  else if(f&&!on)$("s_tryprept").innerHTML=esc("Couldn't "+(f.downloaded?"download ":"start ")+f.voice+": "+(f.error||"no reason given")
    +". A macOS voice speaks until it's here.")+' <button type="button" class="lk" onclick="tryVoice()">Try again</button>';
  if(dl||(f&&!on))$("s_voiceprep").hidden=true;          // one line about it, not two
  if(on&&TRYING!==t.n)followTry(t.n)}
async function followTry(n){TRYING=n;
  while(TRYING===n&&settings.open){await new Promise(r=>setTimeout(r,500));
    let v=null;try{v=await api("/api/voices")}catch(e){}
    if(!v)continue;const t=v.trying;
    if(t&&t.n===n&&t.state==="failed")TRYFAIL=t;
    VOICES=v;paintVoices();
    if(!t||t.n!==n||!TRYWORD[t.state])break}
  if(TRYING===n)TRYING=null}
async function tryVoice(){const b=$("s_voicetry");TRYFAIL=null;b.disabled=true;b.textContent=TRYWORD.starting;let t=null;
  try{const r=await api("/api/voices",{action:"try",id:$("s_voice").value||null});if(r&&r.error)toast(r.error);t=r&&r.trying}
  finally{paintTry(t)}}
async function getVoice(){await api("/api/voices",{action:"get"})}
// Standby Dictation is an app of its own: Settings shows it live, and its switch acts at once, not on Save.
let DICT=null,dictBusy=null;
async function loadDict(){try{DICT=await api("/api/dictation-app")}catch(e){DICT=null}paintDict()}
function paintDict(){const d=DICT||{},miss=d.missing||[],sw=$("s_dict"),n=miss.filter(m=>m.key!=="remap").length;
  sw.checked=dictBusy?dictBusy==="on":!!d.on;sw.disabled=!!dictBusy;
  const act=(a,t)=>`<button type="button" class="btn sm" onclick="dictDo('${a}')">${t}</button>`;
  $("dictstate").innerHTML=dictBusy?'<span class="spin"><i></i><i></i><i></i></span>'+(dictBusy==="on"?"Starting…":"Stopping…"):
    !d.on?"Uses the on-device model. Nothing leaves this Mac":!d.running?"Not running "+act("setup","Start"):
    d.old?"An older version is running "+act("on","Restart"):d.updating?'<span class="spin"><i></i><i></i><i></i></span>Updating to the new version…':
    n?"":miss.length?"Needs a fix":"Ready. "+(typeof d.shortcut==="string"?esc(d.shortcut):esc(kLabel(d.shortcut).replace(/^(?!Press )/,"Press ")))+" in any app";
  $("kshortf").hidden=$("kholdf").hidden=!d.on||!!dictBusy;
  // The words dictation corrects: its menu is easy to miss, so they're here too
  {const w=d.words,f=$("kwordsf");f.hidden=!d.on||!!dictBusy||!w;if(w){const c=w.count||0,g=w.suggested||0;
    setHTML($("kwords"),(c?c+" word"+(c>1?"s":"")+" Standby corrects":"None yet. Fix a dictation and Standby learns the word")+
      (g?` · <span class="sugg-n">${g} suggested</span>`:""))}}if(!KREC)paintShortcut();$("s_hold").checked=!!d.hold;
  $("dicterr").textContent=d.problem||"";$("dicterr").style.display=d.problem&&!dictBusy?"":"none";
  const rows=dictBusy||!d.running||d.old?[]:miss;
  $("dictperms").innerHTML=rows.map(m=>`<div class="check"><span class="bad">✗</span><span class="k" title="${esc(m.why)}">${esc(m.name)}</span>`+
    `<button type="button" class="btn sm" onclick="dictFix('${m.key}','${m.action}')">${esc(m.button.replace("…",""))}</button></div>`).join("");
  setHelp("s_dictwhy",rows.some(m=>m.key!=="remap")?"Switched on in System Settings but still missing? Click It\u2019s on already in the dictation setup window.":"")}
// The shortcut: a preset, or one recorded here. The page sends what was pressed
// (KeyboardEvent code, key and modifiers, or a modifier tapped twice); the server judges it and
// names it, and a refusal never replaces the shortcut in use.
const KB='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/></svg>';
const kLabel=k=>!k?"":typeof k==="string"?k:k.label||"";
let KREC=null;
function paintShortcut(){const d=DICT||{},f=$("kfield"),h=$("khint");f.classList.remove("rec","busy");setHTML(f,KB+`<b>${esc(kLabel(d.shortcut)||"None")}</b>`);
  // A recorded one can be taken later (Raycast installed after): the server says so; say it here.
  const p=d.shortcut&&d.shortcut.problem;if(p){h.textContent=p;h.className="hint bad"}else if(h.dataset.problem){h.textContent="";h.className="hint"}h.dataset.problem=p?"1":""}
function openShortcuts(){const d=DICT||{},m=$("kmenu"),cur=d.shortcut&&d.shortcut.id;if(KREC)return;
  m.innerHTML=(d.shortcuts||[]).map(x=>`<button type="button" role="menuitem" onclick="setShortcut(${esc(JSON.stringify(x.id))})"><span class="lb">${esc(x.label)}</span><span class="ck">${x.id===cur?"✓":""}</span></button>`).join("")+
    (cur==="custom"&&!(d.shortcuts||[]).some(x=>x.id==="custom")?`<button type="button" role="menuitem" onclick="$('kmenu').hidden=true"><span class="lb">${esc(kLabel(d.shortcut))}</span><span class="ck">✓</span></button>`:"")+
    '<hr><button type="button" role="menuitem" onclick="recordShortcut()"><span class="lb">Record a shortcut…</span></button>';m.hidden=false}
// The server can take a moment (up to ~2 s): the dictation app tries the keys, and another app
// (Alfred, Raycast) may already have them.
async function setShortcut(sc){$("kmenu").hidden=true;$("khint").textContent="";$("khint").className="hint";let r={};
  if(KREC)clearTimeout(KREC.t);KREC=null;const f=$("kfield");f.classList.remove("rec");f.classList.add("busy");setHTML(f,'<span class="spin"><i></i><i></i><i></i></span><b>Checking…</b>');
  try{r=await api("/api/dictation-app",{shortcut:sc})}catch(e){r={error:"Standby isn't answering"}}
  if(r.error){$("khint").textContent=r.error;$("khint").className="hint bad"}else if(r.shortcut&&DICT)DICT.shortcut=r.shortcut;
  if(KREC)clearTimeout(KREC.t);KREC=null;paintDict()}
// Recording: the next modifier and key, or one modifier tapped twice; Esc cancels. Every key is
// kept from the app while it records, so ⌘Q or ⌘W can't quit or close Standby.
const MODK=["ControlLeft","ControlRight","AltLeft","AltRight","MetaLeft","MetaRight","ShiftLeft","ShiftRight"];
function recordShortcut(){$("kmenu").hidden=true;const f=$("kfield");f.classList.add("rec");setHTML(f,"<b>Press the keys…</b>");
  $("khint").className="hint";$("khint").textContent="A modifier and a key, or one modifier twice. Esc cancels.";
  KREC={last:null,at:0,t:setTimeout(()=>{if(KREC)$("khint").textContent="Some keys belong to macOS and can't be used."},5000)}}
function stopRecording(){if(KREC)clearTimeout(KREC.t);KREC=null;paintShortcut()}
function recKey(e){if(!KREC)return;e.preventDefault();e.stopPropagation();
  if(e.key==="Escape"){stopRecording();$("khint").textContent="";return}
  if(MODK.includes(e.code)){const now=Date.now();
    if(KREC.last===e.code&&now-KREC.at<450){clearTimeout(KREC.t);return setShortcut({double:e.code})}
    KREC.last=e.code;KREC.at=now;return}
  const mods=[e.ctrlKey&&"ctrl",e.altKey&&"alt",e.shiftKey&&"shift",e.metaKey&&"meta"].filter(Boolean);
  clearTimeout(KREC.t);setShortcut({code:e.code,key:e.key,mods})}
document.addEventListener("keydown",recKey,true);
async function setHold(on){let r={};try{r=await api("/api/dictation-app",{hold:!!on})}catch(e){r={error:"Standby isn't answering"}}
  if(r.error){toast(r.error);$("s_hold").checked=!on}else if(DICT)DICT.hold=!!on}
async function dictDo(action){dictBusy=action==="off"?"off":"on";paintDict();let r;
  try{r=await api("/api/dictation-app",{action})}catch(e){r={error:String(e)}}
  dictBusy=null;if(r.status)DICT=r.status;if(r.error)toast(r.error);paintDict()}
function setDictation(on){dictDo(on?"on":"off")}
// a transcript line a learned word fixed (what was heard is on hover): the fixed words marked.
function fixedWords(r,L){let t=esc(r.text||"");for(const [heard,meant] of L||[]){if(!meant||!new RegExp(reEsc(heard),"i").test(r.heard||""))continue;
    t=t.replace(new RegExp("(^|[^\\w])("+reEsc(esc(meant))+")(?![\\w])","gi"),(m,a,b)=>a+'<span class="fx">'+b+"</span>")}
  return t}
const reEsc=x=>String(x).replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
async function editWords(){let r={};try{r=await api("/api/dictation-app",{action:"edit_words"})}catch(e){r={error:"Standby isn't answering"}}
  if(r.error)toast(r.error);setTimeout(loadDict,800)}
async function dictFix(key,action){const r=await api("/api/dictation-app",action==="open"?{action:"open",key}:{action:"setup"});
  if(r.error)toast(r.error);setTimeout(loadDict,800)}
setInterval(()=>{if(settings.open&&!dictBusy)loadDict()},1500);
if(SETWIN){refresh();showSettings(new URLSearchParams(location.search).get("pane")||undefined)}     // Settings, at once
else{refresh();setInterval(refresh,1500)}      // what's live at launch: Standby's view opens it (agents.js)
</script><script src="/agents.js"></script></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
