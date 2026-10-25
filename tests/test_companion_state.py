"""The companion's side of the app server, without a window: /api/companion
(the live call, never a meeting: the orb is for calls only; its voice's state,
a mic macOS refuses, a start under way), nothing kept of where it sat (it
comes up at home every time), the /companion page served from its file, and the live-session lookup kept to
twice a second however fast the companion polls."""
import json, os, socket, sys, tempfile, threading, time, urllib.request
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbcmp-"))
(root / "sessions").mkdir()
(root / "config.json").write_text(json.dumps({"agent": {"mode": "local"}}))
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
os.environ.update(STANDBY_ROOT=str(root), STANDBY_UI_PORT=str(port))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
ui.log = lambda m: None
fails = []


def check(label, ok, d=""):
    print("  %-62s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


lookups = []
live = {"sid": None}
ui.live_session = lambda: (lookups.append(1), live["sid"])[1]


def fresh():
    ui._LIVE_SEEN.update(at=0.0, sid=None)


st = ui.companion_state()
check("idle: nothing live, voice off, no start, Claude reachable (and no captions setting or question)",
      st == {"live": None, "starting": None, "voice": {"state": "off"}, "claude_error": None}, st)

sid = "talk-20261004-090000"
sdir = root / "sessions" / sid
sdir.mkdir()
(sdir / "meta.json").write_text(json.dumps({"started": time.time() - 500, "kind": "talk"}))
now = time.time()
(sdir / "runs.jsonl").write_text(json.dumps({"start": now - 600, "t": 0}) + "\n" + json.dumps({"end": now - 300, "t": 300}) + "\n"
                                 + json.dumps({"start": now - 20, "t": 301}) + "\n")
live["sid"] = sid
fresh()
st = ui.companion_state()
check("a live call: its id and kind, and nothing of a meeting's", st["live"] and st["live"]["id"] == sid and st["live"]["kind"] == "talk"
      and not {"meeting", "paused", "mic_muted", "claude_wait"} & set(st["live"]), st)

check("  a mic nothing refuses: not denied", st["live"]["mic_denied"] is False, st["live"])
(sdir / "live.json").write_text(json.dumps({"sources": [{"kind": "mic", "name": "Mic", "ok": False, "cause": "permission", "reason": "not allowed"}]}))
check("  macOS refusing the mic (its source says cause permission): the orb is told", ui.companion_state()["live"]["mic_denied"] is True)
(sdir / "live.json").write_text(json.dumps({"sources": [{"kind": "meeting", "name": "", "ok": False, "cause": "permission"}]}))
check("  the meeting's sound refused is another matter: not the mic", ui.companion_state()["live"]["mic_denied"] is False)
(sdir / "live.json").unlink()


class FakeVoice(object):
    state = "listening"

    def status(self):
        return {"state": "listening", "level": 0.2, "you": "what is Orion", "claude": "", "step": "", "muted": False}


ui.VOICE[sid] = FakeVoice()
fresh()
st = ui.companion_state()
check("its voice on: the same status the call's window gets", st["voice"]["state"] == "listening"
      and st["voice"]["you"] == "what is Orion", st["voice"])
ui.VOICE.pop(sid)

meeting = "meeting-20261004-1000"
(root / "sessions" / meeting).mkdir()
(root / "sessions" / meeting / "meta.json").write_text(json.dumps({"started": time.time() - 65}))
live["sid"] = meeting
fresh()
st = ui.companion_state()
check("a meeting recording: nothing for the orb (it is for calls only)", st["live"] is None and st["voice"] == {"state": "off"}, st)

live["sid"] = None
fresh()
ui.STARTING.update(state="starting", kind="talk")
st = ui.companion_state()
check("a talk starting: 'starting' with its kind, nothing live yet", st["starting"] == {"kind": "talk"} and st["live"] is None, st)
ui.STARTING.update(state=None)

n = len(lookups)
for _ in range(40):
    ui.companion_state()
check("polled 40 times at once: the recorder looked up once", len(lookups) - n <= 1, len(lookups) - n)


# Over HTTP, as the page calls it.
srv = ui.ThreadingHTTPServer(("127.0.0.1", port), ui.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % port


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.read().decode("utf-8"), r.headers.get("Content-Type")


def post(path, body, origin=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers=dict({"Content-Type": "application/json"}, **({"Origin": origin} if origin else {})))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


body, ctype = get("/api/companion")
check("GET /api/companion: the state as JSON", json.loads(body)["live"] is None and "json" in ctype, body)
check("where it sat: not kept any more (it comes up at home each time)",
      not hasattr(ui, "save_companion") and not hasattr(ui, "companion_settings") and not hasattr(ui, "COMPANION"))
code, st = post("/api/companion", {"captions": True}, origin=base)
check("  nothing to post to /api/companion any more", code == 404 and "captions" not in ui.companion_state(), (code, st))

saved = ui.COMPANION_PAGE
ui.COMPANION_PAGE = root / "companion.html"
body, ctype = get("/companion")
check("/companion before the page exists: a blank transparent page", "transparent" in body and "text/html" in ctype, body[:120])
ui.COMPANION_PAGE.write_text("<!doctype html><title>companion v1</title>")
check("/companion: the page file, read as it is now", "companion v1" in get("/companion")[0])
ui.COMPANION_PAGE.write_text("<!doctype html><title>companion v2</title>")
check("  an edit shows on the next load, no restart", "companion v2" in get("/companion")[0])
ui.COMPANION_PAGE = saved
srv.shutdown()

# What the panel asks of the app (companion.Presence's hooks).
calls = []
ui.start_session = lambda *a, **k: (calls.append(("start", k)), {"started": True})[1]
ui.stop_session = lambda detach=False: calls.append(("stop", detach))
ui.stop_voice = lambda: calls.append(("stop_voice",))
hooks = ui.CompanionHooks()
live["sid"] = None
fresh()
check("hooks: nothing live, so no companion (and close quits)", hooks.live_kind() is None)
live["sid"] = sid
fresh()
check("  a live talk: its kind", hooks.live_kind() == "talk")
live["sid"] = meeting
fresh()
check("  a meeting recording (no kind in its meta, from before talks): a meeting", hooks.live_kind() == "meeting")
live["sid"] = sid
fresh()
check("  no captions hooks (the words are part of voice mode)",
      not hasattr(hooks, "captions") and not hasattr(hooks, "set_captions"))
check("  and no hooks for where it sits", not hasattr(hooks, "where") and not hasattr(hooks, "save_where"))
check("  nothing in them starts a session (no New Meeting from the companion)", not hasattr(hooks, "new_meeting"))
calls.clear()
hooks.stop_recording()
time.sleep(0.2)
check("  Stop Recording: voice off, then the recorder stopped (not on the main thread)",
      calls[:1] == [("stop_voice",)] and ("stop", False) in calls, calls)
calls.clear()
hooks.stop_all()
check("  Quit while recording: voice off and the recording stopped", calls == [("stop_voice",), ("stop", True)], calls)
calls.clear()
live["sid"] = None
hooks.stop_all()
check("  Quit when idle: nothing to stop but voice", calls == [("stop_voice",)], calls)
# The orb asks on the main thread: a stale look is answered at once and taken again elsewhere
del lookups[:]
where = []
was_lookup = ui.live_session
ui.live_session = lambda: (where.append(threading.current_thread() is threading.main_thread()), time.sleep(0.05), was_lookup())[2]
ui._LIVE_SEEN.update(at=time.time() - 5, sid="old-one"); live["sid"] = "new-one"
check("main thread, stale look: the last answer at once, no pgrep here", ui._live_now() == "old-one")
for _ in range(50):
    if ui._LIVE_SEEN["sid"] == "new-one":
        break
    time.sleep(0.02)
check("  and the new one comes from a thread of its own", ui._LIVE_SEEN["sid"] == "new-one" and where == [False], (ui._LIVE_SEEN, where))
ui.live_session = was_lookup
fresh()
check("  a cleared look (something just changed) is taken where asked", ui._live_now() == "new-one")
live["sid"] = None; fresh()

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
