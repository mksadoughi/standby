"""Where Standby finds Claude Code: one finder, plugin/bin/sbclaude.py, for the
agent (here or on a host), the Mac's checks (mac/transport.py) and the ssh commands.

    full   $STANDBY_CLAUDE, PATH, the shell's own PATH, the installers' places, nvm's
           newest; noted in <root>/agent/claude-path. Test connection, the agent's start.
    quick  $STANDBY_CLAUDE, the noted one, PATH, the places, nvm's newest; never the
           shell. Every agent command.

A made-up home only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


home = os.path.realpath(tempfile.mkdtemp(prefix="sbhome-"))
root = os.path.join(home, ".standby")
os.makedirs(os.path.join(root, "agent"))
NOTE = os.path.join(root, "agent", "claude-path")
BIN = os.path.dirname(os.path.abspath(agentpath.AGENT))


def put(rel):
    path = os.path.join(home, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("#!/bin/sh\necho claude\n")
    os.chmod(path, 0o755)
    return path


def env_for(extra=None, shell="/bin/sh"):
    env = {"HOME": home, "PATH": "/usr/bin:/bin", "SHELL": shell, "STANDBY_ROOT": root, "STANDBY_NO_DEVICES": "1"}
    env.update(extra or {})
    return env


def finds(full=False, extra=None, shell="/bin/sh"):
    """sbclaude.find_claude, in a child with this made-up home."""
    code = "import sys; sys.path.insert(0, sys.argv[1]); import sbclaude; print(sbclaude.find_claude(sys.argv[2] == '1'))"
    r = subprocess.run([sys.executable, "-c", code, BIN, "1" if full else "0"], env=env_for(extra, shell),
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]


def agent_finds(extra=None, shell="/bin/sh"):
    code = ("import sys; from importlib.machinery import SourceFileLoader; "
            "print(SourceFileLoader('a', sys.argv[1]).load_module().CLAUDE)")
    r = subprocess.run([sys.executable, "-c", code, agentpath.AGENT], env=env_for(extra, shell),
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]


def forget():
    if os.path.exists(NOTE):
        os.remove(NOTE)


check("nothing anywhere: none (and the agent: \"claude\", left to its PATH)",
      finds(True) == "None" and agent_finds() == "claude", (finds(True), agent_finds()))
check("  and nothing noted", not os.path.exists(NOTE))
put(".nvm/versions/node/v9.11.2/bin/claude")
put(".nvm/versions/node/v18.20.0/bin/claude")
new_nvm = put(".nvm/versions/node/v22.11.0/bin/claude")
check("installed with nvm: the newest Node's, by its numbers (v22 over v18 and v9)",
      finds() == new_nvm and finds(True) == new_nvm, (finds(), finds(True)))
forget()
volta = put(".volta/bin/claude")
check("  a usual place comes before nvm's (volta)", finds() == volta, finds())
custom = put("custom/bin/claude")
rc = os.path.join(home, "my-shell")
with open(rc, "w") as fh:                       # a shell whose rc file (interactive only) puts it on PATH
    fh.write('#!/bin/sh\n[ "$1" = "-i" ] && [ "$2" = "-c" ] && PATH="%s:$PATH" exec /bin/sh -c "$3"\n'
             'exec /bin/sh "$@"\n' % os.path.dirname(custom))
os.chmod(rc, 0o755)
check("the quick look never asks the shell", finds(shell=rc) == volta, finds(shell=rc))
check("the full look: the shell's own PATH comes before the places", finds(True, shell=rc) == custom,
      finds(True, shell=rc))
check("  and notes what it found, for the agent", os.path.exists(NOTE) and open(NOTE).read().strip() == custom)
check("the quick look takes the noted one first (the shell's answer wins without the shell)",
      finds(shell="/bin/sh") == custom and agent_finds() == custom, (finds(), agent_finds()))
os.remove(custom)
check("  unless it's gone: then the places again", finds() == volta and agent_finds() == volta, finds())
on_path = put("on-path/claude")
path_env = {"PATH": os.path.dirname(on_path) + ":/usr/bin:/bin"}
check("PATH before the shell and the places (full)", finds(True, path_env, shell=rc) == on_path,
      finds(True, path_env, shell=rc))
check("  and noted", open(NOTE).read().strip() == on_path)
local = put(".local/bin/claude")
forget()
check("the agent: Claude Code's own installer's place first (it puts ~/.local/bin on its PATH)",
      agent_finds(path_env) == local, agent_finds(path_env))
check("$STANDBY_CLAUDE before any of them (the one override), as it is",
      agent_finds({"STANDBY_CLAUDE": "/opt/x/claude"}) == "/opt/x/claude"
      and finds(True, {"STANDBY_CLAUDE": "/opt/x/claude"}) == "/opt/x/claude")
check("  one that isn't there is not noted", not os.path.exists(NOTE))
check("  ~/ in it is the home", finds(False, {"STANDBY_CLAUDE": "~/.volta/bin/claude"}) == volta,
      finds(False, {"STANDBY_CLAUDE": "~/.volta/bin/claude"}))
forget()
with open(os.path.join(root, "config.json"), "w") as fh:
    json.dump({"claude_path": "~/chosen/claude"}, fh)
put("chosen/claude")
check("nothing in config.json chooses it", agent_finds() == local, agent_finds())
os.remove(os.path.join(root, "config.json"))
no_agent_dir = os.path.join(home, "elsewhere")
check("no agent folder: nothing noted, nothing made",
      finds(True, {"STANDBY_ROOT": no_agent_dir}) == local and not os.path.exists(no_agent_dir))

# The Mac's own checks: the same finder.
forget()
os.environ.update(HOME=home, STANDBY_ROOT=root, PATH="/usr/bin:/bin", SHELL=rc)
os.environ.pop("STANDBY_CLAUDE", None)
sys.path.insert(0, "mac")
import transport  # noqa: E402
custom = put("custom/bin/claude")
check("the Mac's checks: the quick look finds the agent's", transport.find_claude() == local, transport.find_claude())
check("  Test connection looks in full", transport.find_claude(full=True) == custom, transport.find_claude(full=True))
os.environ["STANDBY_CLAUDE"] = os.path.join(home, "missing/claude")
check("  $STANDBY_CLAUDE that isn't there: none (the checks say so), not another one",
      transport.find_claude() is None and transport.find_claude(full=True) is None)
os.environ.pop("STANDBY_CLAUDE")
src = open("mac/transport.py").read() + open(agentpath.AGENT).read()
check("one finder: no list of places in transport.py or the agent",
      "CLAUDE_PLACES" not in src and ".volta/bin/claude" not in src)

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
