"""On the Mac: a session saying it runs on one of the user's Claude Code
conversations (an agent's meeting), and when it waits because that conversation is
open elsewhere."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, shutil, sys, tempfile, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbattachui-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-700:])


ORIG = "11111111-2222-4333-8444-555555555555"

# ---------------------------------------------------------------- the session says it
sid = "meeting-20261007-150000"
sdir = ui.SESSIONS / sid
sdir.mkdir(parents=True)
(sdir / "meta.json").write_text(json.dumps({"started": time.time(), "kind": "meeting", "workspace": "/code/trips",
                                            "attach": {"from": ORIG, "copy": False, "title": "Trips"}}))
watched = []
ui.watch_attached = lambda s, every=3.0: watched.append(s)
info = ui.session_info(sid, live=sid)
time.sleep(0.2)
check("a session on one: which conversation, and the original, not a copy",
      info["attached"] == {"id": ORIG, "title": "Trips", "copy": False}, info.get("attached"))
check("  and while live it is watched, once, for being opened elsewhere", watched == [sid]
      and (ui.session_info(sid, live=sid), True)[1] and watched == [sid], watched)
(sdir / ui.ATTACH_PAUSED).write_text("{}")
check("open elsewhere mid-meeting: the session says it waits, and why",
      ui.session_info(sid, live=sid)["attached"].get("paused") == ui.PAUSED_ELSEWHERE)
check("  an ended one does not", "paused" not in (ui.session_info(sid, live=None)["attached"] or {}))
(sdir / "meta.json").write_text(json.dumps({"started": time.time(), "kind": "meeting"}))
check("a session on its own conversation: nothing attached", ui.session_info(sid, live=sid)["attached"] is None)
check("the words, ASCII-safe for logs and one line each", all(len(x) < 110 and all(ord(ch) < 128 for ch in x)
                                                          for x in (ui.PAUSED_ELSEWHERE,)))

shutil.rmtree(root, ignore_errors=True)
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
