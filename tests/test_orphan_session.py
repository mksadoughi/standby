"""An orphaned conversation id must recover, not fail the question.

Reproduces the real failure: `standby-agent status` minted a uuid, no Claude
conversation ever backed it, and the next question ran `--resume <phantom>` and
returned "engine error: unknown".
"""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os
import subprocess
import sys
import tempfile
from importlib.machinery import SourceFileLoader

STUB = os.path.join(tempfile.mkdtemp(prefix="sbstub-"), "fake-claude-orphan")   # not a shared /tmp path
with open(STUB, "w") as fh:
    fh.write('''#!/usr/bin/env python3
"""Rejects --resume (like a real missing conversation), accepts --session-id."""
import json, sys
argv = sys.argv[1:]
resuming = "--resume" in argv
sys.stderr.write("stub argv: %s\\n" % (" ".join(
    a for a in argv if len(a) < 60)))
if resuming:
    sys.stderr.write("No conversation found with session ID: phantom\\n")
def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if not line.strip():
        continue
    q = json.loads(line)["message"]["content"][0]["text"]
    if resuming:
        # real CLI behaviour: error result, empty result text, detail on stderr
        emit({"type": "result", "subtype": "error", "is_error": True,
              "result": ""})
    else:
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "ANSWER(%s)" % q})
''')
os.chmod(STUB, 0o755)

root = tempfile.mkdtemp(prefix="sbtest-")
os.environ["STANDBY_ROOT"] = root
os.environ["STANDBY_CLAUDE"] = STUB
ag = SourceFileLoader(
    "ag", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "bin", "standby-agent")
).load_module()

fails = []


def check(label, cond, detail=""):
    print("  %-54s %s" % (label, "OK" if cond else "FAIL"))
    if not cond:
        fails.append(label)
        if detail:
            print("     %s" % detail)


SESSION = "orphan-test"

print("=== 1. read-only calls must not mint a conversation id ===")
sid, _ = ag.session_uuid(SESSION, create=False)
check("session_uuid(create=False) returns None", sid is None)
check("and wrote no file", not os.path.exists(ag.uuid_path(SESSION)))

print("\n=== 2. plant an orphaned id, exactly as `status` used to ===")
os.makedirs(os.path.join(root, "agent"), exist_ok=True)
with open(ag.uuid_path(SESSION), "w") as fh:
    fh.write("f7e8429a-a4ca-46f8-90c8-cccda5ad1a6a\n")
sid, fresh = ag.session_uuid(SESSION)
check("it would be used with --resume (fresh=False)", fresh is False)

print("\n=== 3. the question still gets answered (auto-recovery) ===")
text, rc = ag.engine_claude("summarize what I said", SESSION, "sonnet", 20)
check("rc == 0", rc == 0, "rc=%d text=%r" % (rc, text))
check("real answer, not 'engine error: unknown'",
      text.startswith("ANSWER("), "text=%r" % text)
check("orphaned uuid was discarded and replaced",
      os.path.exists(ag.uuid_path(SESSION)))
if os.path.exists(ag.uuid_path(SESSION)):
    with open(ag.uuid_path(SESSION)) as fh:
        newid = fh.read().strip()
    check("new id differs from the phantom",
          newid != "f7e8429a-a4ca-46f8-90c8-cccda5ad1a6a", "id=%s" % newid)

print("\n=== 4. the retry is bounded (no infinite loop) ===")
log = os.path.join(root, "agent", "engine.log")
with open(log) as fh:
    starts = fh.read().count("start session")
check("exactly two child starts: resume then fresh", starts == 2,
      "starts=%d" % starts)

print("\n=== 5. a non-recoverable error reports real detail, not 'unknown' ===")
with open(STUB + "2", "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, sys
sys.stderr.write("Upstream throttling: please retry\\n")
def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if line.strip():
        emit({"type": "result", "is_error": True, "result": ""})
''')
os.chmod(STUB + "2", 0o755)
ag.CLAUDE = STUB + "2"
ag._LIVE_SESSION.clear()
text, rc = ag.engine_claude("another question", "other-session", "sonnet", 20)
check("surfaces the child's stderr", "throttling" in text, "text=%r" % text)
check("does not say 'unknown'", "unknown" not in text, "text=%r" % text)

for engine in ag._LIVE_SESSION.values():
    engine.stop(hard=True)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
