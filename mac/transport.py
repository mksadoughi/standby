"""Where the agent lives, and how the Mac reaches it.

Claude Code runs on this Mac. The agent is started from this repo's plugin/bin,
and the "shipped" transcript is plain files under ~/.standby/live. (Standby used
to reach other machines over SSH too; that is gone, and a session or setting an
older Standby saved for a host now runs here.)

Everything above this module (recorder, UI, CLI) gets this Mac's transport from
for_config() and only calls:
  write(rel, data)      replace a file under the agent root
  appender(rel)         a writable stream that appends to a file there
  remove(rel)
  agent_cmd(args)       argv that runs `standby-agent <args>` where the agent is
  ensure_agent()        start the agent server if needed -> (ok, message)
  check()               diagnostics for Settings -> list of (ok, label, detail)
  probe()               can Claude Code be reached now -> (ok, kind, fix)

Python standard library only.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CONFIG, ROOT, load_config, save_config  # noqa: E402,F401  (one place for config.json)
REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "plugin" / "bin"))
import sbclaude  # noqa: E402  (where Claude Code is: the one finder, the agent's too)
LOCAL_AGENT = REPO / "plugin" / "bin" / "standby-agent"


def migrate_mic(cfg):
    """Once: a mic pinned before the mic followed the Mac becomes
    Automatic, as the user decided: it was pinned by the old setup, and it
    kept recording the MacBook mic with AirPods on. One picked in Settings
    from now on stays. True when cfg changed (the caller saves it)."""
    if cfg.get("mic_automatic"):
        return False
    cfg["mic_automatic"] = True
    cfg.pop("mic_device", None)
    return True


def migrate_capture(cfg):
    """Once: Automatic recording, the mic and the meeting's audio when a
    call plays. Mic only was the choice when the other side needed BlackHole.
    And always: BlackHole's own modes ("both", "system") are gone, so a Mac set
    up that way records Automatically, the call's sound straight from macOS.
    True when cfg changed."""
    moved = False
    if cfg.get("mode") in ("both", "system"):
        cfg["mode"], moved = "auto", True
    if cfg.pop("loopback_device", None) is not None:
        moved = True
    if cfg.get("capture_automatic"):
        return moved
    cfg["capture_automatic"] = True
    if cfg.get("mode") in (None, "mic"):
        cfg["mode"] = "auto"
    return True


def migrate_voice_model(cfg):
    """Once: voice mode's model becomes Haiku (it answers fast; an agent's own model does the
    work). Sonnet was the default before, and the installer wrote it into every config, so a saved
    Sonnet is the old default, not a choice. A Haiku or Opus that was saved stays. True when cfg
    changed."""
    if cfg.get("voice_model_haiku"):
        return False
    cfg["voice_model_haiku"] = True
    a = cfg.get("agent")
    if isinstance(a, dict) and a.get("voice_model") == "sonnet":
        a["voice_model"] = "haiku"
    if cfg.get("voice_model") == "sonnet":            # an old flat config (agent_settings reads it too)
        cfg["voice_model"] = "haiku"
    return True


def agent_settings(cfg=None):
    """Normalised agent settings. Claude Code runs on this Mac only: an SSH host saved
    by an older Standby is ignored."""
    cfg = load_config() if cfg is None else cfg
    a = dict(cfg.get("agent") or {})
    a["mode"] = "local"
    a.setdefault("model", "opus")             # what an agent with no model of its own, or a meeting with no agent, runs on
    a.setdefault("voice_model", "haiku")       # voice mode answers fast; the agent's own model does the work
    return a


def for_config(cfg=None):
    return LocalTransport(agent_settings(cfg))


# ---------------------------------------------------------------- local

class _FileAppender(object):
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "ab")

    def write(self, data):
        self._fh.write(data)

    def flush(self):
        self._fh.flush()

    def close(self):
        self._fh.close()


class LocalTransport(object):
    mode = "local"

    def __init__(self, settings):
        self.settings = settings
        self.root = ROOT
        self.label = "this Mac"

    def _path(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, rel, data):
        p = self._path(rel)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        tmp.replace(p)
        return True

    def appender(self, rel):
        return _FileAppender(self._path(rel))

    def read(self, rel):
        """A file the agent wrote (live/...), as bytes, or None."""
        try:
            return (self.root / rel).read_bytes()
        except OSError:
            return None

    def remove(self, *rels):
        for rel in rels:
            try:
                (self.root / rel).unlink()
            except OSError:
                pass

    def agent_alive(self):
        """The agent's server is running (standby-agent's own check: its pidfile names a live
        process), looked at here: no process started for it on a turn's path."""
        try:
            pid = int((self.root / "agent" / "server.pid").read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def agent_cmd(self, args):
        return [sys.executable, str(LOCAL_AGENT)] + list(args)

    def ensure_agent(self, model=None):
        if not LOCAL_AGENT.exists():
            return False, "agent not found at %s" % LOCAL_AGENT
        if self.agent_alive():
            return True, "agent running on this Mac"
        log = self.root / "agent" / "serve.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        find_claude(full=True)             # noted in agent/, where the agent's own quick look finds it
        with open(log, "ab") as out:
            subprocess.Popen(self.agent_cmd(["serve", "--model", model or self.settings["model"],
                                             "--voice-model", self.settings["voice_model"]]),
                             stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                             start_new_session=True)
        for _ in range(40):
            time.sleep(0.25)
            if self.agent_alive():
                return True, "agent started on this Mac"
        return False, "agent did not start; see %s" % log

    def probe(self):
        """Can Claude Code be reached on this Mac, now: (ok, kind, fix)."""
        if not LOCAL_AGENT.exists():
            return False, "agent", "Standby's agent is missing from %s: install Standby again." % REPO
        if not find_claude(full=True):
            return False, "missing", "Install Claude Code on this Mac and run claude once in Terminal."
        return agent_probe(self.agent_cmd(["probe"]), "this Mac")

    def check(self):
        out = []
        out.append((LOCAL_AGENT.exists(), "Standby agent", str(LOCAL_AGENT)))
        claude = find_claude(full=True)
        out.append((bool(claude), "Claude Code CLI",
                    claude or "not found: install Claude Code and make sure `claude` is on PATH"))
        if claude:
            try:
                v = subprocess.run([claude, "--version"], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
                text = (v.stdout or v.stderr).strip()
                out.append((v.returncode == 0, "Claude Code runs",
                            text.splitlines()[0] if text else "no output"))
            except (subprocess.TimeoutExpired, OSError) as exc:
                out.append((False, "Claude Code runs", "did not answer --version (%s)" % exc.__class__.__name__))
        import sbagents                    # which conversations are open, for messages to agents
        ok, detail = sbagents.health()
        out.append((ok, "Claude Code's open sessions", detail))
        return out


def find_claude(full=False):
    """The claude CLI, if it's there, else None: sbclaude's lookup, the one finder,
    the agent's too (plugin/bin/sbclaude.py). full: with the shell's own PATH, and
    noted for the agent, for Test connection and the agent's start."""
    found = sbclaude.find_claude(full)
    return found if found and os.access(found, os.X_OK) else None


PROBE_WAIT = 75          # s: the agent's own probe gives up after 45 (STANDBY_PROBE_TIMEOUT)
HELPER_OLD = "Standby's agent on %s is older than this Standby: update it there."


def agent_probe(argv, where):
    """The agent's `probe` (one tiny Claude turn) run by argv: (ok, kind, fix).
    Its own error, said for `where` ("this Mac" or the host), else why it
    gave no answer."""
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=PROBE_WAIT)
        got = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except subprocess.TimeoutExpired:
        return False, "engine", "Claude Code on %s didn't answer within %d s. Try again." % (where, PROBE_WAIT)
    except (OSError, ValueError):
        return False, "agent", "The agent on %s gave no answer: install it again, then try again." % where
    if got.get("ok"):
        return True, "ok", ""
    if not r.stdout.strip() and "invalid choice" in (r.stderr or ""):     # an agent from before `probe`
        return False, "agent", HELPER_OLD % where
    err = got.get("error") if isinstance(got.get("error"), dict) else {}
    text, fix = (err.get("text") or "Claude Code stopped with an error.").strip(), (err.get("fix") or "").strip()
    return False, err.get("kind") or "engine", ("%s (on %s) %s" % (text.rstrip("."), where, fix)).strip()


