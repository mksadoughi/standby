"""Where Standby keeps its files, and config.json, which the app, the recorder,
dictation and the installer all read and write.

Every write is update(): under a lock, the whole file read fresh, changed in
place, and written to a temp file that is synced and renamed over it. So a key
the writer doesn't know is never lost, two writers at once never undo each
other, a reader never sees half a file, and a Mac (or VM) stopped right after
keeps what was written. A file that is there but can't be read is never written
over: update() raises Unreadable.

Standard library only, and nothing else of Standby's: dictation imports this at
launch, and the installer before anything else is set up.
"""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby"))
CONFIG = ROOT / "config.json"
LOCK = ROOT / "config.lock"      # its own file: the rename replaces config.json's


class Unreadable(ValueError):
    """config.json is there but isn't a JSON object: left as it is."""


def load_config():
    """To read only: a change goes through update(), never a write of this copy."""
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _read():
    try:
        text = CONFIG.read_text()
    except FileNotFoundError:
        return {}
    try:
        cfg = json.loads(text)
    except ValueError as exc:
        raise Unreadable("%s isn't valid JSON (%s): fix it or remove it" % (CONFIG, exc))
    if not isinstance(cfg, dict):
        raise Unreadable("%s isn't a JSON object: fix it or remove it" % CONFIG)
    return cfg


def _write(cfg):
    fd, tmp = tempfile.mkstemp(dir=str(ROOT), prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)                       # owner-only, as the folder it is in
        os.replace(tmp, str(CONFIG))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(str(ROOT), os.O_RDONLY)          # and the rename itself
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@contextmanager
def update():
    """with update() as cfg: change cfg in place, saved as the block ends if it changed
    (not if the block raises). Read fresh, under a lock the other writers wait on: keep
    the block short."""
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(str(LOCK), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        cfg = _read()
        was = json.dumps(cfg)
        yield cfg
        if json.dumps(cfg) != was:
            _write(cfg)


def install():
    """The installer's step (install.sh, "Configuration"): Claude Code on this Mac,
    Automatic recording unless one is chosen. Every other key kept."""
    with update() as cfg:
        agent = cfg.get("agent") or {}
        agent["mode"] = "local"                  # Claude Code runs on this Mac: an SSH host saved before goes
        for k in ("host", "remote_dir"):
            agent.pop(k, None)
        cfg["agent"] = agent
        for k in ("host", "remote_dir"):
            cfg.pop(k, None)
        if isinstance(cfg.get("mic_device"), int):   # saved indices are unreliable
            cfg.pop("mic_device")
        cfg.setdefault("mode", "auto")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["install"]:
        sys.exit("usage: config.py install")
    try:
        install()
    except Unreadable as exc:
        print("   \033[33m!\033[0m %s" % exc)
        sys.exit(1)
    print("   \033[32m✓\033[0m %s" % CONFIG)
