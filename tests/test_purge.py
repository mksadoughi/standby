"""`standby purge`: a session goes, with the files copied for the agent beside it on
this Mac (live/<session>.snap-*, .att-*), and nothing of another session's. Run with
a scratch HOME whose ~/.standby is this test's root: never the user's own."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, subprocess, tempfile, time
from pathlib import Path

root = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbpurge-")))
home = Path(tempfile.mkdtemp(prefix="sbpurgehome-"))
(home / ".standby").symlink_to(root)
HERE = Path(__file__).resolve().parent
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def purge(*args):
    return subprocess.run(["bash", str(HERE.parent / "mac" / "standby"), "purge"] + list(args),
                          env=dict(os.environ, HOME=str(home)), capture_output=True, text=True, timeout=60)


SID, OTHER = "meeting-20261012-110000", "meeting-20261012-120000"
live = root / "live"
live.mkdir(parents=True)
for sid in (SID, OTHER):
    (root / "sessions" / sid).mkdir(parents=True)
    (root / "sessions" / sid / "meta.json").write_text(json.dumps({"started": time.time()}))
ours = [live / ("%s.att-0123456789ab-brief.pdf" % SID), live / ("%s.snap-20261012-110001-000.jpg" % SID)]
theirs = live / ("%s.att-keep.pdf" % OTHER)
for f in ours + [theirs]:
    f.write_bytes(b"x")

r = purge(SID)
check("a session's files copied for the agent go with it", r.returncode == 0 and not any(f.exists() for f in ours)
      and not (root / "sessions" / SID).exists(), (r.returncode, r.stderr[-300:]))
check("  another session's stay", theirs.exists() and (root / "sessions" / OTHER).is_dir())
r = purge("../" + OTHER)
check("  never a path: refused, and nothing goes", r.returncode != 0 and theirs.exists()
      and (root / "sessions" / OTHER).is_dir(), r.stderr[-300:])

# Every file a session leaves beside its folder goes with it (live/, agent/, marks/): the one
# routine the window's delete uses too (mac/forget.py).
SID2, NEXT = "talk-20261025-003631", "talk-20261025-003631-2"
(root / "sessions" / SID2).mkdir(parents=True)
for d in ("agent", "marks"):
    (root / d).mkdir(exist_ok=True)
outside = root.parent / ("outside-%d.txt" % os.getpid())
outside.write_text("not Standby's")
beside = [live / (SID2 + x) for x in (".jsonl", ".repass.jsonl", ".meta.json", ".log", ".sources.json", ".voice.json",
                                      ".speakers.known.json", ".snap-20261025-003700-000.jpg", ".att-0a-brief.pdf")] \
    + [root / "agent" / (SID2 + x) for x in (".uuid", ".voice.uuid", ".fed", ".voice.fed", ".usage.json", ".rules")] \
    + [root / "marks" / (SID2 + ".jsonl")]
for f in beside:
    f.write_text("x")
link = live / (SID2 + ".link")
link.symlink_to(outside)
others = [live / (NEXT + ".jsonl"), root / "agent" / (NEXT + ".uuid"), root / "agent" / "spool"]
for f in others:
    f.write_text("x")
r = purge(SID2)
check("every file it left in live/, agent/ and marks/ goes, and its folder",
      r.returncode == 0 and not [f.name for f in beside if f.exists()] and not (root / "sessions" / SID2).exists(),
      (r.returncode, r.stderr[-300:], [f.name for f in beside if f.exists()]))
check("  a link of its: the link goes, never what it points to", not os.path.lexists(link) and outside.exists())
check("  its continuation (another session) and the agent's own files stay", all(f.exists() for f in others),
      [f.name for f in others if not f.exists()])

# Never a live or paused one, by name or with --all.
LIVE, PAUSED, OLD = "meeting-20261025-090000", "meeting-20261025-091000", "meeting-20261025-092000"
for sid in (LIVE, PAUSED, OLD):
    (root / "sessions" / sid).mkdir(parents=True)
    (live / (sid + ".jsonl")).write_text("x")
(root / "sessions" / PAUSED / "agent.json").write_text(json.dumps({"agent": "a-1", "paused_at": time.time()}))
(root / "daemon.ready").write_text(LIVE + "\n")
(root / "daemon.pid").write_text("%d\n" % os.getpid())        # a live process: this test's own
r = purge(LIVE)
check("the live session: refused, nothing of it goes", r.returncode != 0 and "live" in r.stderr
      and (root / "sessions" / LIVE).is_dir() and (live / (LIVE + ".jsonl")).exists(), r.stderr[-300:])
r = purge(PAUSED)
check("  a paused one: refused too (Resume brings it back)", r.returncode != 0 and "paused" in r.stderr
      and (root / "sessions" / PAUSED).is_dir(), r.stderr[-300:])
(live / ("meeting-20261001-080000.jsonl")).write_text("x")        # files that outlived their session
r = subprocess.run(["bash", str(HERE.parent / "mac" / "standby"), "purge", "--all"], input="y\n",
                   env=dict(os.environ, HOME=str(home)), capture_output=True, text=True, timeout=60)
left = sorted(p.name for p in (root / "sessions").iterdir())
check("--all: every session but the live and the paused one, and files that outlived theirs",
      r.returncode == 0 and left == [LIVE, PAUSED] and not (live / "meeting-20261001-080000.jsonl").exists()
      and not (live / (OLD + ".jsonl")).exists() and (live / (LIVE + ".jsonl")).exists(), (left, r.stdout, r.stderr[-300:]))
check("  and says which stayed, and why", "kept" in r.stdout and LIVE + " (live)" in r.stdout and PAUSED + " (paused)" in r.stdout,
      r.stdout)
check("  the agent's own files, of no session, stay", (root / "agent" / "spool").exists())
(root / "daemon.ready").unlink()
outside.unlink()

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
raise SystemExit(1 if fails else 0)
