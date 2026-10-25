"""A session id is only ever a session: valid_id
accepted "." and "..", so a delete with {"id": ".."} removed all of ~/.standby, and
"." every session. Now an id must have the shape Standby makes (a label, the day, the
time) and name a real folder right inside sessions/, never a link. Every route that
takes one refuses the rest, and nothing outside sessions/ is touched. A throwaway
STANDBY_ROOT only; nothing real is started."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, socket, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

root = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbids-")))
_sock = socket.socket(); _sock.bind(("127.0.0.1", 0)); PORT = _sock.getsockname()[1]; _sock.close()
os.environ.update(STANDBY_ROOT=str(root), STANDBY_UI_PORT=str(PORT))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
import standby as sb  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


# What must survive: Standby's own files beside the sessions, a real session, and a
# folder elsewhere that a link in sessions/ points at.
(root / "venv").mkdir()
(root / "venv" / "keep").write_text("the venv")
(root / "config.json").write_text("{}")
REAL = "meeting-20261013-090000"
(ui.SESSIONS / REAL).mkdir(parents=True)
(ui.SESSIONS / REAL / "meta.json").write_text(json.dumps({"started": time.time(), "kind": "meeting"}))
(ui.SESSIONS / REAL / "transcript.jsonl").write_text("")
outside = root / "elsewhere"
outside.mkdir()
(outside / "precious.txt").write_text("not a session")
LINKED = "meeting-20261013-100000"
os.symlink(str(outside), str(ui.SESSIONS / LINKED))
ui.live_session = lambda: None


def intact():
    return ((root / "venv" / "keep").exists() and (root / "config.json").exists()
            and (ui.SESSIONS / REAL / "meta.json").exists() and (outside / "precious.txt").exists()
            and (ui.SESSIONS / LINKED).is_symlink())


BAD = [".", "..", "../x", "../..", "a/b", "/tmp/x", str(ui.SESSIONS / REAL), REAL + "/..", REAL + "/", "./" + REAL,
       LINKED, "", None, 5, REAL + "\n", "meeting", "x" * 300 + "-20261013-090000", "%2e%2e"]

check("valid_id: a session Standby made, and a minute-only one from before",
      ui.valid_id(REAL) and (ui.SESSIONS / "talk-20260930-1420").mkdir() is None and ui.valid_id("talk-20260930-1420")
      and (ui.SESSIONS / "meeting-20261013-090000-2").mkdir() is None and ui.valid_id("meeting-20261013-090000-2"))
check("  never \".\", \"..\", a path, a link, nothing of another shape",
      not [b for b in BAD if ui.valid_id(b)], [b for b in BAD if ui.valid_id(b)])
check("session_dir refuses every one of them, and nothing is touched",
      intact() and all(ui.session_dir(b) is None for b in BAD), [b for b in BAD if ui.session_dir(b) is not None])

# Every route that takes an id, over HTTP, as any local process could call it.
srv = ui.serve()
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % PORT


def call(method, path, body=None):
    req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json"}, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


POSTS = ["/api/rename", "/api/copy", "/api/claude", "/api/speaker", "/api/voice", "/api/mic"]
let_through = []
for path in POSTS:
    for b in BAD:
        status, body = call("POST", path, {"id": b, "title": "x", "text": "x", "on": True, "spk": "1", "name": "x"})
        got = json.loads(body or b"{}") if body else {}
        if status == 200 and "error" not in got:
            let_through.append((path, b, got))
check("every POST route that takes an id refuses all of them", not let_through, let_through[:5])
GETS = []
for b in BAD:
    if not isinstance(b, str) or not b:
        continue
    q = urllib.parse.quote(b, safe="")
    GETS += ["/api/session/" + q, "/api/export/" + q + "?fmt=md"]
served = [(g, s) for g, s in ((g, call("GET", g)[0]) for g in GETS) if s == 200]
check("every GET route that takes one refuses them too", not served, served[:5])
check("and after all of it, nothing outside a session is touched, nor the session itself", intact())
srv.shutdown()

# Ids are made in that shape, whatever label `standby start` is given.
made = sb.new_session_id("../../x y", sessions=ui.SESSIONS)
check("a new session's id from an odd label: a plain name, never a path",
      ui.SESSION_ID.match(made) and (ui.SESSIONS / made).is_dir() and made.startswith("xy-"), made)
check("  an empty or all-odd label: \"meeting\"", sb.new_session_id("../..", sessions=ui.SESSIONS).startswith("meeting-"))
check("  a usual one, as before", sb.new_session_id("talk", sessions=ui.SESSIONS).startswith("talk-"))

if fails:
    for label in fails:
        print("  FAILED: %s" % label.strip())
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
