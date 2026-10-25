#!/usr/bin/env python3
"""A session gone for good, one way, for `standby purge` and the window's delete alike:
its folder, and every file it left beside it on this Mac. Never one that's live or
paused, never anything but a session's own files.

    forget.py [--root DIR] <session> | --all

A session's files outside its folder (each named "<session>.<anything>", right inside):

    live/     what was sent to its agent: the transcript, its meta, sources, speaker
              names, the log, the voice's state, snapshots and attachments
    agent/    its agent's own: the conversation ids, what was fed, usage, rules, forks
    marks/    its marks

Standard library only: it runs whatever state the venv is in.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# A session id as Standby makes it: never ".", "..", a path or a link's name.
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}-\d{8}-\d{4}(?:\d{2})?(?:-\d{1,4})?$")
BESIDE = ("live", "agent", "marks")
AGENT_FILE = "agent.json"


def folder(root, sid):
    """The session's folder: only for an id of that shape, and only a real folder right
    inside root/sessions (never a link, never anywhere else); else None."""
    if not isinstance(sid, str) or not SESSION_ID.match(sid):
        return None
    sessions = Path(root) / "sessions"
    path = sessions / sid
    try:
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != sessions.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    return path


def files(root, sid):
    """Its files beside its folder (BESIDE): files or links right inside each, never
    followed. "<sid>.*" alone: a continuation (<sid>-2) is a session of its own."""
    if not isinstance(sid, str) or not SESSION_ID.match(sid):
        return []
    out = []
    for d in BESIDE:
        where = Path(root) / d
        try:
            names = os.listdir(where)
        except OSError:
            continue
        for name in sorted(names):
            if not name.startswith(sid + "."):
                continue
            f = where / name
            if f.is_symlink() or f.is_file():
                out.append(f)
    return out


def paused(root, sid):
    """An agent's meeting paused and not ended: Resume brings it back."""
    try:
        data = json.loads((Path(root) / "sessions" / sid / AGENT_FILE).read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("paused_at")) and not data.get("ended_at")


def live_session(root):
    """The session a recorder is running now, or None: the one daemon.ready names, while
    the recorder in daemon.pid (or any standby.py) is alive."""
    try:
        sid = (Path(root) / "daemon.ready").read_text().strip()
    except OSError:
        return None
    if not sid:
        return None
    try:
        pid = int((Path(root) / "daemon.pid").read_text().strip())
        os.kill(pid, 0)
        return sid
    except (OSError, ValueError):
        pass
    try:
        r = subprocess.run(["pgrep", "-f", "mac/standby.py"], capture_output=True, text=True, timeout=5)
        return sid if r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return sid                           # can't tell: as if live, so nothing goes


def why_not(root, sid, live=None):
    """Why session sid can't go, or None: "live" (live: the live session, as the caller
    knows it; else looked up) or "paused"."""
    if sid == (live if live is not None else live_session(root)):
        return "live"
    if paused(root, sid):
        return "paused"
    return None


def forget(root, sid, log=lambda m: None):
    """Session sid's files beside it, then its folder. The caller has checked why_not.
    Returns the names that stayed (a file it couldn't remove), [] when all went."""
    stayed = []
    for f in files(root, sid):
        try:
            f.unlink()
        except OSError as exc:
            stayed.append(f.name)
            log("forget %s: %s stays (%r)" % (sid, f.name, exc))
    path = folder(root, sid)                 # checked again, right before it goes
    if path is not None:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            stayed.append(sid)
            log("forget %s: its folder stays (%r)" % (sid, exc))
    return stayed


def everything(root):
    """Every session there is: each folder, and any session whose files outlived it."""
    sids = set()
    try:
        sids.update(n for n in os.listdir(Path(root) / "sessions") if folder(root, n) is not None)
    except OSError:
        pass
    for d in BESIDE:
        try:
            names = os.listdir(Path(root) / d)
        except OSError:
            continue
        for name in names:
            sid = name.split(".", 1)[0]
            if "." in name and SESSION_ID.match(sid):
                sids.add(sid)
    return sorted(sids)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(os.environ.get("STANDBY_ROOT") or Path.home() / ".standby")
    if args[:1] == ["--root"] and len(args) >= 2:
        root, args = Path(args[1]), args[2:]
    if len(args) != 1:
        print("usage: standby purge <session>|--all", file=sys.stderr)
        return 2
    live = live_session(root)
    if args[0] == "--all":
        gone, kept = [], []
        for sid in everything(root):
            why = why_not(root, sid, live=live or "")
            if why:
                kept.append("%s (%s)" % (sid, why))
            elif not forget(root, sid, log=lambda m: print("standby: " + m, file=sys.stderr)):
                gone.append(sid)
        print("standby: purged %d session(s)%s" % (len(gone), "; kept " + ", ".join(kept) if kept else ""))
        return 0
    sid = args[0]
    if folder(root, sid) is None and not files(root, sid):
        print("standby: no such session: %s" % sid, file=sys.stderr)
        return 1
    why = why_not(root, sid, live=live or "")
    if why:
        print("standby: %s is %s: end it first" % (sid, why), file=sys.stderr)
        return 1
    stayed = forget(root, sid, log=lambda m: print("standby: " + m, file=sys.stderr))
    if stayed:
        print("standby: %s: these stayed: %s" % (sid, ", ".join(stayed)), file=sys.stderr)
        return 1
    print("standby: purged %s" % sid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
