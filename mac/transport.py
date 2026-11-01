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
import signal
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
try:                                    # the apps' own copy of the code (codecopy), once installed: everyone's agent
    import codecopy
    if (codecopy.CODE / "plugin" / "bin" / "standby-agent").exists():
        LOCAL_AGENT = codecopy.CODE / "plugin" / "bin" / "standby-agent"
except ImportError:
    pass


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


def agent_settings(cfg=None):
    """Normalised agent settings. Claude Code runs on this Mac only: an SSH host saved
    by an older Standby is ignored."""
    cfg = load_config() if cfg is None else cfg
    a = dict(cfg.get("agent") or {})
    a["mode"] = "local"
    a.setdefault("model", "opus")             # what an agent with no model of its own, or a meeting with no agent, runs on
    a.setdefault("voice_model", "sonnet")      # the voice, at high thinking: Haiku answered too little (2026-11-01)
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

    def agent_program(self):
        """The program the running agent was started from (its command line), or None."""
        try:
            pid = int((self.root / "agent" / "server.pid").read_text().strip())
            r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() or None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def retire_agent(self, wait=5.0):
        """Stop the running agent (it restarts in place, so only this ends one started elsewhere)."""
        try:
            pid = int((self.root / "agent" / "server.pid").read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            return
        end = time.time() + wait
        while time.time() < end and self.agent_alive():
            time.sleep(0.1)

    def agent_owner(self):
        """Who started the running agent: "claude-code" (the mod, from the user's own Claude Code), "app"
        (Standby), "cli" (a terminal), or None (unknown, or not running)."""
        try:
            pid = int((self.root / "agent" / "server.pid").read_text().strip())
            got = json.loads((self.root / "agent" / "started_by.json").read_text())
        except (OSError, ValueError):
            return None
        return got.get("by") if isinstance(got, dict) and got.get("pid") == pid else None

    def agent_run(self, args, input=None, timeout=150, wait=30.0):
        """One of standby-agent's one-off asks (title, decisions, cc-gist, call-summary), run by the agent server,
        never started from here: each starts a Claude Code, and started by Standby, macOS asked Standby for the
        folders it read as it started (the plugin in ~/Documents). Run by the server your Claude Code started,
        it's that app's. A subprocess.CompletedProcess; TimeoutExpired past `timeout`."""
        import random
        spool = self.root / "agent" / "spool"
        end = time.time() + wait
        while not self.agent_alive() and time.time() < end:
            time.sleep(0.25)                      # its Claude Code is starting it
        if not self.agent_alive():
            return subprocess.CompletedProcess(args, 1, "", "the agent isn't running")
        spool.mkdir(parents=True, exist_ok=True)
        ident = "%d-%04d" % (int(time.time() * 1000), random.randint(0, 9999))
        tmp, out = spool / (ident + ".run.tmp"), spool / (ident + ".ran")
        tmp.write_text(json.dumps({"argv": list(args), "input": input or "", "timeout": timeout}))
        os.replace(tmp, spool / (ident + ".run"))
        end = time.time() + timeout + 10
        while time.time() < end:
            try:
                res = json.loads(out.read_text())
            except (OSError, ValueError):
                time.sleep(0.1)
                continue
            try:
                out.unlink()
            except OSError:
                pass
            return subprocess.CompletedProcess(args, int(res.get("rc", 1)), res.get("stdout") or "", res.get("stderr") or "")
        raise subprocess.TimeoutExpired(args, timeout)

    def ensure_agent(self, model=None, by=None, start=None):
        """The agent server running: (ok, message). by: who asks (STANDBY_AGENT_BY, else "cli"). The agent and
        every Claude Code it starts count, to macOS, as the app that started it: started by Standby, a project in
        ~/Documents needed Standby's own grant for that folder (refused, every turn of a call failed). So the
        user's Claude Code starts it (the mod, "claude-code"), and replaces one Standby started; Standby itself
        only looks (start=False), unless it has no Claude Code to ask (ui._agent_from_claude_code)."""
        by = by or os.environ.get("STANDBY_AGENT_BY") or "cli"
        start = (by != "app") if start is None else start
        if not LOCAL_AGENT.exists():
            return False, "agent not found at %s" % LOCAL_AGENT
        if self.agent_alive():
            if by == "app":
                return True, "agent running on this Mac"   # Standby never retires one: it would start it again as its own
            running = self.agent_program()
            mine = running is None or str(LOCAL_AGENT) in running
            if mine and not (by == "claude-code" and self.agent_owner() not in ("claude-code", "cli")):
                return True, "agent running on this Mac"
            # one started from somewhere else (the checkout, before the apps ran their own copy): it restarts
            # in place from there, and run by Standby it can lose that folder (a call: "can't open file");
            # or one Standby started, which your Claude Code now starts as its own
            self.retire_agent()
        if not start:
            return False, "the agent is started by your Claude Code"
        log = self.root / "agent" / "serve.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        find_claude(full=True)             # noted in agent/, where the agent's own quick look finds it
        with open(log, "ab") as out:
            proc = subprocess.Popen(self.agent_cmd(["serve", "--model", model or self.settings["model"],
                                                    "--voice-model", self.settings["voice_model"]]),
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                    start_new_session=True)
        try:
            (self.root / "agent" / "started_by.json").write_text(json.dumps({"by": by, "pid": proc.pid}))
        except OSError:
            pass
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


