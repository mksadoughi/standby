"""A call's visuals, the Mac's side: the agent drops each one in its spool by the voice question it goes with
(<qid>.v<k>.json: drawing, then ready with its fragment, or failed); the windows' polls move it into the call's
folder, numbered in order, and the windows list them and fetch one's fragment. "Show visuals during calls" is
on by default, saved like the other switches, and settable by name. No agent, no window: files and the server."""
import json, os, socket, sys, tempfile, threading, time, urllib.request
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbvis-"))
(root / "sessions").mkdir()
(root / "config.json").write_text(json.dumps({"agent": {"mode": "local"}}))
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
os.environ.update(STANDBY_ROOT=str(root), STANDBY_UI_PORT=str(port))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import visuals  # noqa: E402
import sbtoken  # noqa: E402
sbtoken.use(lambda: ui.TOKEN)
ui.log = lambda m: None
fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


spool = root / "agent" / "spool"
spool.mkdir(parents=True)


def drop(name, data):
    tmp = spool / (name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(str(tmp), str(spool / name))


print("the setting")
check("Show visuals during calls: on by default", ui.get_settings()["call_visuals"] is True)
check("  saved off and on as Settings saves it", ui.set_settings({"call_visuals": False})["settings"]["call_visuals"] is False
      and json.loads((root / "config.json").read_text())["call_visuals"] is False
      and ui.set_settings({"call_visuals": True})["settings"]["call_visuals"] is True)
named = {x["name"]: x for x in ui.settings_named()["settings"]}
check("  by name from Claude Code: visuals, on or off", named["visuals"]["value"] == "on" and named["visuals"]["choices"] == ["on", "off"]
      and ui.set_named("visuals", "off")["value"] == "off" and ui.get_settings()["call_visuals"] is False
      and ui.set_named("visuals", "on")["value"] == "on", named.get("visuals"))
page = ui.PAGE
check("  the switch in Settings > Call, saved with the rest", 'id="s_visuals" data-save' in page and "call_visuals:$(\"s_visuals\").checked" in page
      and "Show visuals during calls" in page)

print("the spool into the call")
sid, other = "talk-20261031-100000", "talk-20261031-090000"
for s in (sid, other):
    (root / "sessions" / s).mkdir()
calls = {"1000-0001": root / "sessions" / sid, "1000-0002": root / "sessions" / sid, "900-0001": root / "sessions" / other}
call_for = calls.get
drop("1000-0001.v1.json", {"state": "drawing", "t": 100})
changed = visuals.take(spool, call_for, 101)
rows = visuals.listed(root / "sessions" / sid)
check("drawing: in the call at once, number 1, its turn kept, the spool emptied",
      rows == [{"id": 1, "state": "drawing", "qid": "1000-0001", "at": 100}] and not list(spool.iterdir())
      and changed == {str(root / "sessions" / sid)}, (rows, list(spool.iterdir())))
drop("1000-0001.v1.json", {"state": "ready", "t": 108, "html": '<div data-size="small"><h1>Three points</h1></div>'})
visuals.take(spool, call_for, 109)
got = visuals.one(root / "sessions" / sid, 1)
check("  ready: the same visual, now with its fragment, still asked at 100",
      got["state"] == "ready" and got["html"].startswith('<div data-size="small">') and got["at"] == 100 and "src" not in got, got)
drop("1000-0001.v1.json", {"state": "drawing", "t": 120})
visuals.take(spool, call_for, 121)
check("  a late drawing never undoes a ready one", visuals.one(root / "sessions" / sid, 1)["state"] == "ready")
drop("1000-0002.v1.json", {"state": "drawing", "t": 130})
drop("1000-0002.v2.json", {"state": "ready", "t": 131, "html": "x" * (visuals.MAX_HTML + 1)})
drop("900-0001.v1.json", {"state": "failed", "t": 132, "why": "the model gave up"})
visuals.take(spool, call_for, 133)
rows = visuals.listed(root / "sessions" / sid)
check("two in one turn: numbered on, in order", [r["id"] for r in rows] == [1, 2, 3] and [r["qid"] for r in rows] == ["1000-0001", "1000-0002", "1000-0002"], rows)
check("  too big: failed, saying so, its fragment never kept", visuals.one(root / "sessions" / sid, 3)["state"] == "failed"
      and visuals.one(root / "sessions" / sid, 3)["why"] == "too big" and "html" not in visuals.one(root / "sessions" / sid, 3))
check("  each call its own: the other call's visual is its number 1, failed with why",
      visuals.listed(root / "sessions" / other) == [{"id": 1, "state": "failed", "qid": "900-0001", "at": 132}]
      and visuals.one(root / "sessions" / other, 1)["why"] == "the model gave up")
drop("777-0001.v1.json", {"state": "ready", "t": 140, "html": "<p>lost</p>"})
drop("1000-0001.v9.json", {"state": "sideways", "t": 141})
(spool / "1000-0001.v1.json.tmp").write_text("{half")
(spool / "1000-0001.vq").write_text("a question")
visuals.take(spool, call_for, 142)
check("  no call to go to, or a state it doesn't know: dropped; a half-written file and the agent's own files left alone",
      sorted(p.name for p in spool.iterdir()) == ["1000-0001.v1.json.tmp", "1000-0001.vq"]
      and len(visuals.listed(root / "sessions" / sid)) == 3, sorted(p.name for p in spool.iterdir()))
check("  no visual, or a number it doesn't have: None", visuals.one(root / "sessions" / sid, 9) is None and visuals.one(root / "sessions" / sid, "x") is None)

print("the app: the voice's turn, the windows' poll, the endpoints")
ui.visual_turn("1100-0001", sid)
drop("1100-0001.v1.json", {"state": "drawing", "t": time.time()})
ui._VISUALS["at"] = 0
latest = ui.visual_latest(sid)
check("the windows' poll takes it in: the newest, and how many", latest == dict(id=4, state="drawing", qid="1100-0001", at=latest["at"], n=4), latest)
ui._live_now = lambda ttl=0.5: other
drop("1200-0001.v1.json", {"state": "drawing", "t": time.time()})
ui._VISUALS["at"] = 0
ui.take_visuals()
check("  a turn the Mac didn't queue (a result told aloud): the live call's", len(visuals.listed(root / "sessions" / other)) == 2)

srv = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
P = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (P, path), timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


st, body = get("/api/visuals?session=" + sid)
check("GET /api/visuals: the call's list, no fragments, and whether visuals are on",
      st == 200 and [v["id"] for v in body["visuals"]] == [1, 2, 3, 4] and not any("html" in v for v in body["visuals"]) and body["on"] is True, body)
st, body = get("/api/visual?session=%s&id=1" % sid)
check("GET /api/visual: one, with its fragment", st == 200 and body["html"].startswith("<div"), body)
check("  one it doesn't have, or a session that isn't one: 404", get("/api/visual?session=%s&id=99" % sid)[0] == 404
      and get("/api/visuals?session=../etc")[0] == 404 and get("/api/visuals?session=talk-20990101-000000")[0] == 404)
req = urllib.request.Request("http://127.0.0.1:%d/api/visuals?session=%s" % (P, sid), headers={"X-Standby-Token": "wrong"})
try:
    urllib.request.urlopen(req, timeout=5)
    refused = False
except urllib.error.HTTPError as e:
    refused = e.code in (401, 403)
check("  without this launch's token: refused", refused)
srv.shutdown()

if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
