"""A call's trace: one JSON object per event in sessions/<sid>/trace.jsonl, from each process
that sees the call (the recorder, the app, the agent), to read what really happened in a turn
(tools/call_trace.py). {"t": epoch with ms, "src", "turn", "ev", ...fields}.

Cheap and never in the way: event() only appends to a list; a thread of its own writes what
gathered every FLUSH seconds, in one O_APPEND write per batch (whole lines, so the processes'
lines never mix), and at exit. Never from the audio thread, never a wait for the disk.

Private as the transcript is: the user's words and the replies, in the session's own folder only
(0600), never anywhere else, deleted with the session. Nothing is written for a session whose
folder isn't there.

A debugging tool, off unless asked for: config.json "debug": {"call_trace": true}, or STANDBY_CALL_TRACE=1
(=0 turns it off whatever the config says). Read once per call, at its first event in each process. Off,
event() returns at once: no thread is started and nothing is kept."""
import atexit
import json
import os
import threading
import time

ROOT = os.path.expanduser(os.environ.get("STANDBY_ROOT", "~/.standby"))
FLUSH = 0.5
SRC = "?"                 # who writes: "rec", "app" or "agent" (set once by each)

_lock = threading.Lock()
_rows = []                # (sid, line)
_wake = threading.Event()
_thread = None
_on = {}                  # session -> traced or not, decided at its first event


def enabled(sid):
    """Is this call traced? Decided once, at its first event: STANDBY_CALL_TRACE, else config.json."""
    on = _on.get(sid)
    if on is None:
        env = os.environ.get("STANDBY_CALL_TRACE")
        if env in ("0", "1"):
            on = env == "1"
        else:
            try:
                with open(os.path.join(ROOT, "config.json")) as fh:
                    on = bool(((json.load(fh) or {}).get("debug") or {}).get("call_trace"))
            except (IOError, OSError, ValueError, AttributeError):
                on = False
        _on[sid] = on
    return on


def source(name):
    global SRC
    SRC = name


def path(sid):
    return os.path.join(ROOT, "sessions", sid, "trace.jsonl")


def event(sid, ev, turn=None, **fields):
    """One event for the call `sid` (a session name), at once: kept until the next flush."""
    if not sid or "/" in str(sid) or str(sid).startswith(".") or not enabled(sid):
        return
    row = {"t": round(time.time(), 3), "src": SRC, "turn": turn, "ev": ev}
    row.update(fields)
    try:
        line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    except (TypeError, ValueError):
        return
    with _lock:
        _rows.append((str(sid), line))
    _start()


def _start():
    global _thread
    if _thread is None:
        with _lock:
            if _thread is None:
                _thread = threading.Thread(target=_run, name="trace", daemon=True)
                _thread.start()


def _run():
    while True:
        _wake.wait(FLUSH)
        _wake.clear()
        flush()


def flush():
    """What gathered, to each session's file: one write per session."""
    with _lock:
        rows, _rows[:] = list(_rows), []
    by = {}
    for sid, line in rows:
        by.setdefault(sid, []).append(line)
    for sid, lines in by.items():
        p = path(sid)
        if not os.path.isdir(os.path.dirname(p)):
            continue                                  # no such session here: nothing kept
        try:
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, "".join(lines).encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            pass


atexit.register(flush)
