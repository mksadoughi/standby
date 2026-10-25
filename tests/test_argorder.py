"""Both argument orders must work, and neither may clobber the other."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os
import subprocess
import sys
import tempfile

AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "bin", "standby-agent")
PY = "/usr/bin/python3"


ENV = dict(os.environ, STANDBY_ROOT=tempfile.mkdtemp(prefix="sbargs-"), STANDBY_CLAUDE="/usr/bin/true")


def run(args):
    p = subprocess.Popen([PY, AGENT] + args, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=ENV)
    out, _ = p.communicate()
    return p.returncode, out.decode("utf-8", "replace").strip()


CASES = [
    # (args, must_contain, label)
    (["status"], "session :", "bare subcommand"),
    (["--engine", "mock", "status"], "session :", "flag BEFORE subcommand"),
    (["status", "--engine", "mock"], "session :", "flag AFTER subcommand"),
    (["dry-run", "--model", "haiku"], "--model", "serve-style: flag after"),
]
fail = 0
for args, want, label in CASES:
    rc, out = run(args)
    ok = rc == 0 and want in out
    print("  %-32s rc=%d %s" % (label, rc, "OK" if ok else "FAIL"))
    if not ok:
        fail += 1
        print("     output: %s" % out[:300])

# the precedence question: a flag after the subcommand must win, and a flag
# before it must survive when the subcommand does not repeat it
rc, out = run(["dry-run", "--model", "haiku"])
print("\n  model resolution, after-subcommand:  %s"
      % ("haiku OK" if "haiku" in out else "FAIL: %s" % out[:200]))
rc, out = run(["--model", "haiku", "dry-run"])
print("  model resolution, before-subcommand: %s"
      % ("haiku OK" if "haiku" in out else "FAIL: %s" % out[:200]))
rc, out = run(["--model", "opus", "dry-run", "--model", "haiku"])
print("  both given, after-subcommand wins:   %s"
      % ("haiku OK" if "haiku" in out and "opus" not in out else "FAIL: %s" % out[:200]))

# the exact invocation that was killing the server
rc, out = run(["serve", "--model", "sonnet", "--engine", "mock", "--help"])
print("\n  the command that killed the server:  %s"
      % ("parses OK" if rc == 0 and "usage: standby-agent serve" in out else "FAIL: %s" % out[:200]))
sys.exit(1 if fail else 0)
