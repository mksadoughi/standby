"""Deleting a past meeting or call (POST /api/msg/delete {session}): gone for good, its folder and what
it left in live/ and agent/, and gone from the past list; every window is told ("deleted"), and
Claude Code's band for one of its own. A call that went on in two sessions goes whole. Never one
that's live, paused or starting; never a bad id or anything outside the sessions folder; nothing
removed when it's refused. A scratch STANDBY_ROOT only."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, socket, sys, tempfile, threading, time, urllib.error, urllib.request
from pathlib import Path

root = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbdelete-")))
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
os.environ.update(STANDBY_ROOT=str(root), STANDBY_UI_PORT=str(PORT))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-700:])


CC = "12345678-1234-4234-8234-123456789abc"
(root / "workspaces.json").write_text(json.dumps({
    "workspaces": [{"id": "w-1", "name": "orion", "machine": "local", "folder": "/Users/you/code/orion", "agents": ["a-planner"]}],
    "agents": {"a-planner": {"id": "a-planner", "workspace": "w-1", "session": CC, "sessions": []}}}))
for d in ("live", "agent"):
    (root / d).mkdir(parents=True, exist_ok=True)
ui.SESSIONS.mkdir(parents=True, exist_ok=True)


def session(sid, kind="meeting", **bound):
    sd = ui.SESSIONS / sid
    sd.mkdir()
    started = time.time() - 3600
    (sd / "meta.json").write_text(json.dumps({"started": started, "kind": kind}))
    (sd / "transcript.jsonl").write_text(json.dumps({"t": 1, "end": 2, "speaker": "Room", "text": "we ship on Friday"}) + "\n")
    (sd / "agent.json").write_text(json.dumps(dict({"agent": "a-planner", "name": "Planner", "started": started}, **bound)))
    (sd / "card.json").write_text(json.dumps({"points": ["Ship on Friday"], "memory": "pending"}))
    (root / "live" / (sid + ".jsonl")).write_text("x")
    (root / "live" / (sid + ".meta.json")).write_text("{}")
    (root / "agent" / (sid + ".uuid")).write_text(CC + "\n")
    (root / "marks").mkdir(exist_ok=True)
    (root / "marks" / (sid + ".jsonl")).write_text("{}\n")
    return sid


PAST = session("meeting-20261020-100000", ended_at=time.time() - 60)
LIVE = session("meeting-20261022-090000")
PAUSED = session("meeting-20261021-100000", paused_at=time.time() - 120)
CALL = session("talk-20261019-100000", kind="talk", moved=["meeting-20261019-101500"])
MOVED = session("meeting-20261019-101500", call="talk-20261019-100000", ended_at=time.time() - 30)
LITE = session("meeting-20261018-100000", agent="lite:" + CC, ended_at=time.time() - 30)
NEIGHBOUR = root / "live" / "meeting-20261020-1000001.jsonl"     # another session's name that starts the same
NEIGHBOUR.write_text("keep")
OUTSIDE = root / "keep.txt"
OUTSIDE.write_text("not a session")

ui.live_session = lambda fresh=False: LIVE
events, band = [], []
m = ui.messenger()
m.hub.publish = lambda ev, data: events.append((ev, data))


class Band(object):
    def publish(self, cc, event):
        band.append((cc, event))


ui.lite = lambda: Band()


def past():
    return {c["session"] for c in ui.past_sessions(m)}


check("the past list shows them all at first", {PAST, CALL, LITE} <= past(), past())

srv = ui.serve()
threading.Thread(target=srv.serve_forever, daemon=True).start()


def post(body, origin=None):
    req = urllib.request.Request("http://127.0.0.1:%d/api/msg/delete" % PORT, json.dumps(body).encode(),
                                 dict({"Content-Type": "application/json"}, **({"Origin": origin} if origin else {})))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, r = post({"session": PAST})
check("a past meeting: deleted", code == 200 and r == {"ok": True, "session": PAST}, (code, r))
check("  its folder is gone, and its live/, agent/ and marks/ files (the routine purge uses too)",
      not (ui.SESSIONS / PAST).exists() and not list((root / "live").glob(PAST + ".*"))
      and not list((root / "agent").glob(PAST + ".*")) and not list((root / "marks").glob(PAST + ".*")),
      sorted(os.listdir(root / "live")))
check("  another session whose name starts the same keeps its file", NEIGHBOUR.read_text() == "keep")
check("  gone from the past list", PAST not in past(), past())
check("  every window is told", ("deleted", {"session": PAST}) in events, events)
check("  Claude Code's band isn't told about a meeting that isn't its own", band == [], band)

r = ui.delete_past({"session": PAST})
check("asked again: already gone, nothing else touched", r == {"error": ui.DELETE_GONE} and OUTSIDE.exists(), r)
for name, sid in (("live", LIVE), ("paused", PAUSED)):
    r = ui.delete_past({"session": sid})
    check("a %s one: refused, nothing removed" % name, r == {"error": ui.DELETE_LIVE} and (ui.SESSIONS / sid / "card.json").exists()
          and (root / "live" / (sid + ".jsonl")).exists(), r)
with ui._lock:
    ui.STARTING.update(state="starting", resume=PAUSED)
check("one being resumed right now: refused", ui.delete_past({"session": PAUSED}) == {"error": ui.DELETE_LIVE})
with ui._lock:
    ui.STARTING.update(state=None, resume=None)
BAD = ["..", ".", "../keep.txt", "/etc", str(ui.SESSIONS / LIVE), LIVE + "/..", "", None, 5, "meeting", "x" * 300]
check("a bad id: refused every time, nothing outside the sessions folder touched",
      all(ui.delete_past({"session": b}) == {"error": ui.DELETE_GONE} for b in BAD) and OUTSIDE.exists()
      and ui.SESSIONS.is_dir() and (ui.SESSIONS / LIVE).is_dir(), [b for b in BAD if ui.delete_past({"session": b}) != {"error": ui.DELETE_GONE}])
check("  and a request that isn't JSON's dict shape", ui.delete_past(["x"]) == {"error": ui.DELETE_GONE})
os.symlink(str(root / "live"), str(ui.SESSIONS / "meeting-20261023-100000"))
check("  a link in the sessions folder: refused, and what it points at stays",
      ui.delete_past({"session": "meeting-20261023-100000"}) == {"error": ui.DELETE_GONE} and (root / "live").is_dir())

code, r = post({"session": MOVED})
check("a call that went on in a meeting: asked by either session, both go, as one card",
      code == 200 and r == {"ok": True, "session": CALL} and not (ui.SESSIONS / CALL).exists()
      and not (ui.SESSIONS / MOVED).exists() and CALL not in past(), (code, r))
code, r = post({"session": LITE})
check("a meeting from Claude Code: deleted, and its band told too", code == 200 and r.get("ok")
      and band == [(CC, {"deleted": True, "session": LITE})], (code, r, band))
code, r = post({"session": PAUSED}, origin="https://evil.example")
check("never from another site", code == 403 and (ui.SESSIONS / PAUSED).is_dir(), (code, r))
srv.shutdown()

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
