"""standby-ctx reads and writes only a session's own files: --session is a session id, never a
path, and no file is reached through a link that leads out of Standby's folders. The voice and
the meeting agent may run it unasked, so a name it is given must not open anything else."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CTX = os.path.join(HERE, "..", "plugin", "bin", "standby-ctx")
sys.path.insert(0, os.path.join(HERE, "..", "mac"))
import forget  # noqa: E402

root = os.path.realpath(tempfile.mkdtemp(prefix="sbctxpaths-"))
outside = os.path.realpath(tempfile.mkdtemp(prefix="sbctxoutside-"))
ENV = dict(os.environ, STANDBY_ROOT=root, STANDBY_RECORDER_MATCH="sbctxpathsfake%d[.]py run" % os.getpid())
fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     %s" % (detail,))


def ctx(*args):
    return subprocess.run([sys.executable, CTX] + list(args), capture_output=True, text=True, env=ENV, timeout=60)


SECRET = "the secret line"
live, marks = os.path.join(root, "live"), os.path.join(root, "marks")
os.makedirs(live)
os.makedirs(marks)
SID = "meeting-20261025-090000"
with open(os.path.join(live, SID + ".jsonl"), "w") as fh:
    fh.write(json.dumps({"t": 1, "end": 2, "speaker": "Room", "text": "An ordinary line."}) + "\n")
# A transcript-shaped file outside, which a path would reach: <outside>/x.jsonl, and as a
# session's folder <outside>/x/transcript.jsonl.
with open(os.path.join(outside, "x.jsonl"), "w") as fh:
    fh.write(json.dumps({"t": 1, "end": 2, "text": SECRET}) + "\n")
os.makedirs(os.path.join(outside, "x"))
with open(os.path.join(outside, "x", "transcript.jsonl"), "w") as fh:
    fh.write(json.dumps({"t": 1, "end": 2, "text": SECRET}) + "\n")

m = re.search(r'SESSION_ID = re\.compile\(r"([^"]+)"\)', open(CTX).read())
check("the same session-id pattern as the Mac's (forget.py), which purge and the window use",
      m is not None and m.group(1) == forget.SESSION_ID.pattern)
r = ctx("all", "--session", SID)
check("a session id: its transcript", r.returncode == 0 and "An ordinary line." in r.stdout, r.stderr[-200:])

for name, bad in (("an absolute path", os.path.join(outside, "x")), ("../", "../../" + os.path.basename(outside) + "/x"),
                  ("a dot", ".."), ("an empty-looking id", "-")):
    for cmd in ("all", "recent", "search", "marks"):
        args = [cmd] + (["secret"] if cmd == "search" else []) + ["--session", bad]
        r = ctx(*args)
        check("%s for --session (%s): refused, nothing read" % (name, cmd),
              r.returncode != 0 and SECRET not in r.stdout and "no such session" in r.stderr, (r.returncode, r.stdout[-200:]))
r = ctx("mark", "--session", os.path.join(outside, "x"), "a", "note")
check("  and nothing written: mark with a path", r.returncode != 0 and not os.path.exists(os.path.join(outside, "x.jsonl.new"))
      and sorted(os.listdir(outside)) == ["x", "x.jsonl"], (r.returncode, os.listdir(outside)))

# A link inside Standby's folders that leads out: never followed.
LINKED = "meeting-20261025-091000"
os.symlink(os.path.join(outside, "x.jsonl"), os.path.join(live, LINKED + ".jsonl"))
r = ctx("all", "--session", LINKED)
check("a session's transcript that is a link to elsewhere: not read", SECRET not in r.stdout, r.stdout[-200:])
os.makedirs(os.path.join(root, "sessions"))
os.symlink(os.path.join(outside, "x"), os.path.join(root, "sessions", "meeting-20261025-092000"))
r = ctx("all", "--session", "meeting-20261025-092000")
check("  a session folder that is a link to elsewhere: not read", SECRET not in r.stdout, r.stdout[-200:])
note = os.path.join(outside, "notes.jsonl")
open(note, "w").close()
os.symlink(note, os.path.join(marks, SID + ".jsonl"))
r = ctx("mark", "--session", SID, "a", "note")
check("  marks that are a link to elsewhere: nothing written through it", os.path.getsize(note) == 0, r.stderr[-200:])
with open(os.path.join(live, "CURRENT"), "w") as fh:
    fh.write("../../" + os.path.basename(outside) + "/x")
r = ctx("all")
check("  the live pointer naming a path: not followed either", SECRET not in r.stdout, r.stdout[-200:])

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
raise SystemExit(1 if fails else 0)
