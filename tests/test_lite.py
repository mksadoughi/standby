"""Standby inside Claude Code, the meeting side (mac/lite.py and /api/lite/*): a meeting on the
app's recorder with its own Claude off, its lines named as the engine names them, batches under
the meeting's push policy, pause and resume with Claude still off, the card,
keep or forget, and the mod's live stream. The recorder and the model are stand-ins."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, sys, tempfile, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sblite-"))
os.environ["STANDBY_ROOT"] = str(root)
cli = root / "fake-standby"
cli.write_text("#!/bin/bash\nexit 0\n")
cli.chmod(0o755)
os.environ["STANDBY_CLI"] = str(cli)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import lite  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-700:])


CC = "c0ffee00-1111-4222-8333-444444444444"
started, LIVE = [], {"sid": None}
ui.live_session = lambda: LIVE["sid"]
ui.mac_with = lambda: None if not LIVE["sid"] else "a meeting"
SID = "meeting-20261017-100000"


def fake_start(**kw):
    started.append(kw)
    return {"started": True}


def go_live(wait):
    d = ui.SESSIONS / SID
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "meta.json").exists():
        (d / "meta.json").write_text(json.dumps({"kind": "meeting", "started": time.time() - 600, "push_lines": 3,
                                                 "push_seconds": 60}))
    LIVE["sid"] = SID
    return SID


ui.start_session, ui._wait_live = fake_start, go_live
L = ui.lite()

# ---------------------------------------------------------------- listen
check("listen: a Claude Code session id is needed", L.listen({"cc": "nope"}).get("error"))
check("  and a recording mode that is one", L.listen({"cc": CC, "capture": "everything"}).get("error"))
check("the transcript setting: on ask unless chosen, in Standby's Settings", ui.get_settings()["lite_transcript"] == "ask")
check("  and a meeting asked to pass it some other way is refused", L.listen({"cc": CC, "pass": "sometimes"}).get("error"))
FOLDER = str(root / "orion")
os.makedirs(FOLDER, exist_ok=True)
r = L.listen({"cc": CC, "capture": "meeting", "name": "Weekly routing sync", "pass": "live", "cwd": FOLDER})
check("listen: the app's recorder starts, its own Claude off (the session listens), meeting audio only, live as asked",
      r == {"session": SID, "pass": "live"} and started[-1] == {"kind": "meeting", "capture": "meeting", "no_claude": True}, (r, started))
bound = ui.addmac.bound(ui.SESSIONS / SID)
check("  the meeting is the session's (agent.json, owner lite:<id>), with its sources",
      bound["agent"] == "lite:" + CC and bound["meeting_audio"] and not bound["mic"] and lite.is_lite(bound), bound)
# A brand-new Claude Code session: its first meeting is in Standby's past meetings and calls at once,
# under its folder in the navigator (each folder, its sessions, what each one did), by that session.
ws = ui.messenger().workspaces()
rec = next((x for x in ws.get("agents", []) if x.get("session") == CC), None)
w = next((x for x in ws.get("workspaces", []) if rec and rec["id"] in (x.get("agents") or [])), None)
past = [c for c in ui.past_sessions(ui.messenger()) if c["session"] == SID]
check("  a brand-new session's first meeting: in the past list, by that session, under its folder in the navigator",
      rec is not None and w is not None and w["folder"] == FOLDER and past and past[0]["from"] == rec["id"] and past[0]["kind"] == "meeting",
      (rec, w, past))
check("  and named", (ui.SESSIONS / SID / "title.txt").read_text().strip() == "Weekly routing sync")
hooks = ui.CompanionHooks()
check("  no orb for it: the companion sees it live (Quit still asks), but it was never sent to the orb",
      hooks.live_kind() == "meeting" and hooks.orb_asked() is False)
hooks.ask_orb()
check("    and never: a meeting is not put on the orb, even if asked (the orb is for calls only)",
      hooks.orb_asked() is False and SID not in ui.LITE_ORB)
ui.LITE_ORB.discard(SID)
n = len(started)
check("listen again: the same meeting, nothing started twice", L.listen({"cc": CC}) == {"session": SID, "pass": "live"} and len(started) == n)
check("  BlackHole's old \"both\" is no capture any more: refused, in words", "error" in L.listen({"cc": CC, "capture": "both"}))

# macOS, as the band asks it: stand-ins (never this Mac's own answers)
MAC = {"mic": "authorized", "screen": False}
ui.mic_access = lambda: MAC["mic"]
ui.permissions.screen_allowed = lambda: MAC["screen"]
st = L.state(CC)
check("state: the app is up, and this session's live meeting",
      st["app"] is True and "settings" not in st and st["live"]["session"] == SID and st["live"]["kind"] == "meeting"
      and st["live"]["capture"] == "meeting" and st["live"]["pass"] == "live" and "ask" not in st["live"], st)
check("  what this build can do: listen, its window, and a call into the orb (calls are always there)",
      st.get("can") == {"listen": True, "window": callable(getattr(ui, "open_window", None)),
                        "orb": callable(getattr(ui, "lite_orb", None))}, st.get("can"))
check("  another session has none", L.state("c0ffee00-1111-4222-8333-555555555555")["live"] is None)
check("  with the meeting's name", st["live"]["title"] == "Weekly routing sync", st)


# What macOS refuses the meeting, or will ask for: for the band's second line and the window's
def heard(mic=None, sound=None):
    srcs = [dict({"kind": k, "ok": c is None}, **({"cause": c} if c else {})) for k, c in (("mic", mic), ("meeting", sound)) if c != "none"]
    (ui.SESSIONS / SID / "live.json").write_text(json.dumps({"at": time.time(), "sources": srcs}))
    ui._SNAP_CHECKS.pop("permission", None)


def access_now():
    live = L.state(CC)["live"]
    return {k: live[k] for k in ("mic", "meeting_audio") if k in live}


heard()
check("access: meeting audio only, never asked: \"ask\" (no mic in it, so no word of the mic)", access_now() == {"meeting_audio": "ask"}, access_now())
check("  and the same in /api/live/<sid>, for the window", ui.access(SID, ui.live_capture(SID)) == {"meeting_audio": "ask"})
heard(sound="permission")
check("  the recorder refused it (cause permission): \"denied\"", access_now() == {"meeting_audio": "denied"}, access_now())
heard()
ui.SNAP_ASKED.write_text("1\n")
check("  asked before, and still not allowed: \"denied\"", access_now() == {"meeting_audio": "denied"}, access_now())
MAC["screen"] = True
heard()
check("  allowed: \"allowed\"", access_now() == {"meeting_audio": "allowed"}, access_now())
ui.SNAP_ASKED.unlink()
bound = ui.addmac.bound(ui.SESSIONS / SID)
(ui.SESSIONS / SID / ui.addmac.AGENT_FILE).write_text(json.dumps(dict(bound, mic=True)))
MAC["mic"] = "undetermined"
check("the mic in it too: never asked, \"ask\"", access_now() == {"mic": "ask", "meeting_audio": "allowed"}, access_now())
MAC["mic"] = "denied"
check("  macOS says denied: \"denied\"", access_now()["mic"] == "denied", access_now())
MAC["mic"] = "authorized"
heard(mic="permission")
check("  the recorder hears only zeros (cause permission): \"denied\", though macOS says nothing",
      access_now()["mic"] == "denied", access_now())
opened = root / "opened"
ui.OPEN = ["/bin/sh", "-c", 'echo "$0" >> "%s"' % opened]
check("  Open Settings: System Settings at Microphone (the mic before the sound)",
      ui.lite_permission({"session": SID}) == {"ok": True} and opened.read_text().split() == [ui.MIC_PANE], opened.read_text() if opened.exists() else None)
heard(mic="none", sound="permission")
ui.lite_permission({"session": SID})
check("  then the sound refused: at Screen & System Audio Recording", opened.read_text().split()[-1] == ui.SCREEN_PANE, opened.read_text())
heard()
check("  nothing refused: nothing opens", ui.lite_permission({"session": SID}) == {"ok": True, "allowed": True} and len(opened.read_text().split()) == 2)
check("  a session that isn't live: refused", "error" in ui.lite_permission({"session": "meeting-20200101-000000"}))
(ui.SESSIONS / SID / ui.addmac.AGENT_FILE).write_text(json.dumps(bound))
(ui.SESSIONS / SID / "live.json").unlink()

# a new meeting's stream starts from its first line, not from what was there when the stream first looked
first = L.subscribe(CC)
(ui.SESSIONS / SID / "transcript.jsonl").write_text(json.dumps({"t": 2, "spk": 0, "text": "Hello all."}) + "\n")
L.tick()
got0 = []
while not first.empty():
    got0.append(first.get_nowait())
check("the stream: a new meeting's very first line is published", [(e.get("meet"), e.get("i")) for e in got0][:1]
      == [("line", 0)] and got0[0]["text"] == "Hello all.", got0)
L.unsubscribe(first)

# ---------------------------------------------------------------- lines, with the engine's names
rows = [{"t": 5 + i * 5, "end": 8 + i * 5, "spk": i % 2, "ch": "Room", "text": "Line %d about the cache." % i} for i in range(4)]
(ui.SESSIONS / SID / "transcript.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
(ui.SESSIONS / SID / "speakers.names.json").write_text(json.dumps({"names": {"0": "Nadia"}}))
(ui.SESSIONS / SID / "speakers.known.json").write_text(json.dumps({"known": {"1": "Theo"}}))
got = L.lines(SID)
check("lines: each with its speaker, the name the engine gives it (the user's, a remembered voice), its text",
      [(x["i"], x["name"], x["text"]) for x in got["lines"]] ==
      [(0, "Nadia", "Line 0 about the cache."), (1, "Theo", "Line 1 about the cache."), (2, "Nadia", "Line 2 about the cache."),
       (3, "Theo", "Line 3 about the cache.")] and got["next"] == 4 and got["renamed"] == {"0": "Nadia", "1": "Theo"}, got)
check("  from a cursor", [x["i"] for x in L.lines(SID, after=3)["lines"]] == [3])
check("  only a Lite meeting's", L.lines("meeting-20200101-000000").get("error"))

# ---------------------------------------------------------------- batches, by the meeting's push policy
b = L.batch(SID)
check("batch: 4 unsent lines, push_lines 3: the batch, its time span, the opening words, each line named",
      b and b["through"] == 4 and b["text"].startswith("[Standby · meeting · ") and lite.PREAMBLE in b["text"]
      and "Nadia: Line 0 about the cache." in b["text"] and "Theo: Line 3" in b["text"], b)
check("  sent: the cursor moves, never back", L.batch_sent({"session": SID, "through": 4})["through"] == 4
      and L.batch_sent({"session": SID, "through": 2})["through"] == 4)
check("  nothing new: no batch", L.batch(SID) == {})
rows.append({"t": 30, "end": 33, "spk": 0, "text": "One more."})
(ui.SESSIONS / SID / "transcript.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
check("  one new line, under push_lines and fresh: not yet", L.batch(SID, now=time.time() - 600 + 40) == {})
b2 = L.batch(SID, everything=True)
check("  but with the user's question (all=1): it goes now, and only the opening batch had the opening words",
      b2["through"] == 5 and "Nadia: One more." in b2["text"] and lite.PREAMBLE not in b2["text"], b2)
check("  and once push_seconds have passed since that line, it goes anyway",
      L.batch(SID, now=time.time() + 120)["through"] == 5)
L.batch_sent({"session": SID, "through": 5})

# ---------------------------------------------------------------- on ask: only with a prompt
check("pass: changed for this meeting (its window's ⋯), and refused for a value that isn't one",
      L.set_pass({"session": SID, "pass": "ask"}) == {"ok": True, "pass": "ask"} and L.state(CC)["live"]["pass"] == "ask"
      and L.set_pass({"session": SID, "pass": "never"}).get("error") and L.set_pass({"session": "meeting-20200101-000000", "pass": "ask"}).get("error"))
rows.append({"t": 34, "end": 36, "spk": 1, "text": "Thursday then."})
(ui.SESSIONS / SID / "transcript.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
check("  on ask, nothing goes on its own, however long it waits", L.batch(SID, now=time.time() + 600) == {})
b3 = L.batch(SID, everything=True)
check("  but with a prompt (all=1), what's new since the last one", b3.get("through") == 6 and "Theo: Thursday then." in b3["text"]
      and "One more." not in b3["text"], b3)
L.set_pass({"session": SID, "pass": "live"})
rows = rows[:5]                              # as it was: the rest of the meeting goes on from here
(ui.SESSIONS / SID / "transcript.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))

# ---------------------------------------------------------------- the stream
q = L.subscribe(CC)
other = L.subscribe("c0ffee00-1111-4222-8333-555555555555")
L._seen.clear()
L.tick()                                     # what's there now is the start, not news
rows.append({"t": 45, "spk": 0, "text": "Ship it Thursday."})
(ui.SESSIONS / SID / "transcript.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
(ui.SESSIONS / SID / "speakers.names.json").write_text(json.dumps({"names": {"0": "Nadia", "1": "Theo M."}}))
L.tick()
evs = []
while not q.empty():
    evs.append(q.get_nowait())
check("the stream: a new line, named, and the new names, as they happen",
      [e.get("meet") for e in evs] == ["line", "rename"] and evs[0]["text"] == "Ship it Thursday." and evs[0]["name"] == "Nadia"
      and evs[1]["renamed"]["1"] == "Theo M.", evs)
check("  only to that Claude Code session's streams", other.empty())
L.publish(CC, {"call": "caption", "text": "hello"})
g = L.frames(q, heartbeat=0.05)
check("  one JSON line per event (calls publish theirs here too), a heartbeat while quiet",
      json.loads(next(g)) == {"call": "caption", "text": "hello"} and json.loads(next(g)) == {"heartbeat": True})
q.dropped = True
check("  a stream that fell behind says resync and ends", json.loads(next(L.frames(q))) == {"resync": True})
L.unsubscribe(q)
L.unsubscribe(other)

# ---------------------------------------------------------------- pause and resume, Claude still off
real_stop = ui.stop_session
def fake_stop(detach=False):                  # as the real one: its hook marks the meeting paused
    sid = LIVE["sid"]
    LIVE["sid"] = None
    if sid:
        ui.mac_stopped(sid)
    return {"ok": True}


ui.stop_session = fake_stop
check("pause: the recorder stops, the meeting stays open", L.pause({"session": SID}) == {"ok": True}
      and ui.addmac.bound(ui.SESSIONS / SID)["paused_at"])
check("  the session still has it (paused)", L.state(CC)["live"]["paused"] is True, L.state(CC))
fresh = type(L)(ui)                          # as after Standby restarts
looked = []
fresh._open_on_disk = (lambda real: lambda cc: looked.append(cc) or real(cc))(fresh._open_on_disk)
check("  after a restart too: found once on disk, then not looked for again, however often it's asked",
      [fresh.state(CC)["live"]["session"] for _ in range(5)] == [SID] * 5
      and all(fresh.state("c0ffee00-1111-4222-8333-555555555555")["live"] is None for _ in range(5))
      and looked == [CC, "c0ffee00-1111-4222-8333-555555555555"], looked)
started.clear()
r = L.resume({"session": SID})
check("resume: recording again into the same meeting, with the app's own Claude still off",
      r == {"session": SID} and started and started[-1].get("no_claude") is True and started[-1].get("agent") is None
      and started[-1].get("resume") == SID, (r, started))

# ---------------------------------------------------------------- the end: the card, keep or forget
end_q = L.subscribe(CC)
made = []
ui.make_card = lambda sid: made.append(sid) or ui.write_json(ui.SESSIONS / sid / "card.json", {
    "points": ["Ship the cache fix Thursday"], "at": time.time(), "copy": False, "delivered_at": None})
check("stop: the meeting ends, and the engine's own card is made", L.stop({"session": SID}) == {"ok": True}
      and ui.addmac.bound(ui.SESSIONS / SID)["ended_at"])
check("  then the session has none, without looking on disk again", fresh.state(CC)["live"] is None and looked.count(CC) == 1, looked)
time.sleep(0.5)
c = L.card(SID)
ui._msg_event("card", {"session": SID})          # the calls' card writer says so too, for the same card
end_evs = []
while not end_q.empty():
    end_evs.append(end_q.get_nowait())
check("  the stream says it ended at once, and the card once, though two parts of the engine wrote it",
      ("state", True) in [(e.get("meet"), e.get("ended")) for e in end_evs]
      and [e.get("meet") for e in end_evs].count("card") == 1, end_evs)
L.unsubscribe(end_q)
check("  the card: its title, points, recorded time, saved in Standby with nothing to choose",
      made == [SID] and c["title"] == "Weekly routing sync" and c["points"] == ["Ship the cache fix Thursday"]
      and "memory" not in c and "duration_s" in c, c)
check("  no keep or forget for it: it stays as it is",
      "/api/lite/memory" not in ui.LITE_POST and ui.card_memory(SID, False) == {"error": lite.SAVED} and (ui.SESSIONS / SID).exists())
t = L.transcript(CC, SID)
check("the meeting tool's transcript: all of it, named, after the meeting too",
      t.get("session") == SID and t["live"] is False and t["text"].startswith("[Standby · meeting · Weekly routing sync · ")
      and "Nadia: Line 0 about the cache." in t["text"] and "Nadia: Ship it Thursday." in t["text"], t)
check("  only this Claude Code session's", L.transcript("c0ffee00-1111-4222-8333-555555555555", SID).get("error")
      and L.transcript("c0ffee00-1111-4222-8333-555555555555").get("error"))
check("Standby's window: refused without a Claude Code session, in words for the mod's toast",
      "/standby window" in ui.open_window({}).get("error", ""), ui.open_window({}))
check("  and without the app's own window (the server alone can't show one)",
      ui.APP_WINDOW.get("win") is not None or "Standby app" in ui.open_window({"cc": CC}).get("error", ""), ui.open_window({"cc": CC}))
check("  its session's cc is found from the meeting when the mod sends none",
      "Standby app" in ui.open_window({"session": SID}).get("error", ""), ui.open_window({"session": SID}))
# Standby opened from Claude Code: the app itself, on the session's thread; its one window, never resized
# (one layout at any width), in front but an ordinary window: never kept above other apps, live or not.
class FakeMain(object):
    def __init__(self, visible, width):
        self.x, self.y, self.width, self.height = 100, 100, width, 700
        self.on_top, self.shown, self.js, self.placed = False, 0, [], []
    def resize(self, w, h): self.placed.append(("size", w, h)); self.width, self.height = w, h
    def move(self, x, y): self.placed.append(("at", x, y)); self.x, self.y = x, y
    def show(self): self.shown += 1; self.placed.append(("shown",))
    def evaluate_js(self, js): self.js.append(js)
real = (ui._to_front, ui.APP_WINDOW.get("win"))
fronted = []
ui._to_front = fronted.append
live_now = bool(L._live_of(CC)[0] or ui.lite_call_live(CC))
focus_js = 'window.mxFocusSession&&mxFocusSession(%s)' % json.dumps(
    {"session": SID, "kind": "meeting", "live": True} if live_now else dict(L.last_of(CC), live=False))
hidden = FakeMain(False, 500); ui.APP_WINDOW["win"] = hidden
r = ui.open_window({"cc": CC, "session": SID})
time.sleep(0.2)
check("opened from Claude Code: shown, on the session's meeting, by its id",
      r == {"ok": True} and hidden.shown == 1 and hidden.js == [focus_js], (r, hidden.placed, hidden.js))
check("  brought to the front, but never kept above other windows, live or not",
      fronted == [hidden] and hidden.on_top is False and hasattr(ui.Api, "window_float") is False,
      (hidden.on_top, live_now))
wide = FakeMain(True, 1100); ui.APP_WINDOW["win"] = wide
r = ui.open_window({"cc": CC})
time.sleep(0.2)
check("  a window stretched wide: focused all the same, not resized or moved",
      r == {"ok": True} and wide.placed == [("shown",)] and wide.js == [focus_js], (wide.placed, wide.js))
none = FakeMain(True, 1100); ui.APP_WINDOW["win"] = none
r = ui.open_window({"cc": "c0ffee00-1111-4222-8333-555555555555"})
time.sleep(0.2)
check("  a session with no meeting or call yet: the past meetings and calls",
      r == {"ok": True} and none.js == ['window.mxFocusSession&&mxFocusSession(null)'] and none.on_top is False, (r, none.js))
ui._to_front, ui.APP_WINDOW["win"] = real[0], real[1]

# one at a time: on a call, a meeting is refused, never started over it
real_talk = ui.is_talk
LIVE["sid"], ui.is_talk = "talk-20261017-120000", (lambda sid: str(sid).startswith("talk-"))
check("on a call: listening is refused in words, and the call goes on",
      L.listen({"cc": CC}) == {"error": "You're on a call: end it first"} and LIVE["sid"] == "talk-20261017-120000")
ui.is_talk = real_talk

# a second meeting, as Settings has it: on ask
LIVE["sid"] = None
FIRST, SID = SID, "meeting-20261017-110000"
check("a second meeting: on ask, as Settings has it", L.listen({"cc": CC}) == {"session": SID, "pass": "ask"})
(ui.SESSIONS / SID / "transcript.jsonl").write_text(json.dumps({"t": 1, "spk": 0, "text": "Quick sync."}) + "\n")
b4 = L.batch(SID, everything=True)
check("  its first prompt has all of it, with words for on ask", lite.ASK_PREAMBLE in b4["text"] and lite.PREAMBLE not in b4["text"]
      and "Quick sync." in b4["text"], b4)
check("  the meeting tool reads the latest one, the first still by its session",
      L.transcript(CC).get("session") == SID and L.transcript(CC, FIRST).get("session") == FIRST)
check("Settings: on ask or live, kept", ui.set_settings({"lite_transcript": "live"}).get("ok")
      and ui.get_settings()["lite_transcript"] == "live" and ui.set_settings({"lite_transcript": "x"})["settings"]["lite_transcript"] == "live")
ui.set_settings({"lite_transcript": "ask"})
named = {x["name"]: x for x in ui.settings_named()["settings"]}
check("Settings by name, for Claude Code: the ones that matter, each with its value and choices",
      {"transcript", "capture", "mic", "speech_model", "voice", "voice_model", "talk_over"} <= set(named)
      and named["transcript"]["value"] == "ask" and named["transcript"]["choices"] == ["ask", "live"]
      and named["mic"]["choices"][0] == "default" and named["voice"]["choices"][0] == "automatic", named.get("transcript"))
check("  one set by name, as Settings saves it", ui.set_named("transcript", "Live") == {"ok": True, "name": "transcript", "value": "live"}
      and ui.get_settings()["lite_transcript"] == "live" and ui.set_named("talk-over", "on")["value"] == "on"
      and ui.get_settings()["talk_over"] is True)
check("  a value not among its choices: refused, saying the choices, and nothing changes",
      ui.set_named("transcript", "loud") == {"error": "transcript can be ask, live."} and ui.get_settings()["lite_transcript"] == "live")
check("  a setting it doesn't have: refused, listing the settings",
      "There's no setting called volume. The settings: transcript, capture" in ui.set_named("volume", "11")["error"])
check("  capture: Automatic, the mic, or meeting audio (BlackHole's \"both\" is gone)",
      named["capture"]["choices"] == ["auto", "mic", "meeting"], named["capture"])
check("  Claude starting calls and listening without asking: off by default, on or off by name",
      named["auto_accept"]["value"] == "off" and named["auto_accept"]["choices"] == ["on", "off"]
      and ui.set_named("auto_accept", "on")["value"] == "on" and ui.get_settings()["auto_accept"] is True
      and ui.set_named("auto_accept", "off")["value"] == "off")
check("  a number in range only", ui.set_named("ai_update", "500").get("error") and ui.set_named("ai_update", "20")["value"] == "20")
ui.set_named("transcript", "ask"); ui.set_named("talk_over", "off"); ui.set_named("ai_update", "10")
ui.addmac.save(ui.SESSIONS / SID, dict(ui.addmac.bound(ui.SESSIONS / SID), ended_at=time.time()))
LIVE["sid"] = None

# ---------------------------------------------------------------- a call's card, the same way (calls mark agent.json kind "call")
CALL = "talk-20261017-120000"
(ui.SESSIONS / CALL).mkdir()
(ui.SESSIONS / CALL / "meta.json").write_text(json.dumps({"kind": "talk", "started": time.time() - 300}))
(ui.SESSIONS / CALL / "agent.json").write_text(json.dumps({"agent": "lite:" + CC, "kind": "call", "name": "Claude Code", "copy": True,
                                                         "mic": True, "meeting_audio": False, "started": time.time() - 300}))
check("a call is never this session's live meeting", L.state(CC)["live"] is None and L.listen({"cc": CC}).get("session") != CALL)
LIVE["sid"] = None
for d in list(ui.SESSIONS.iterdir()):           # the meeting listen just started: out of the way
    if d.name not in (CALL,) and (d / "agent.json").exists() and not (d / "card.json").exists():
        ui.addmac.save(d, dict(ui.addmac.bound(d), ended_at=time.time()))
ui.write_json(ui.SESSIONS / CALL / "card.json", {"points": ["Ship it Monday"], "at": time.time(), "duration_s": 300})
cq = L.subscribe(CC)
L.card_event(CALL)
cev = cq.get_nowait() if not cq.empty() else None
L.unsubscribe(cq)
check("  but its card is read and announced (as a call's), saved the same way",
      L.card(CALL)["points"] == ["Ship it Monday"] and cev == {"call": "card", "session": CALL}
      and ui.card_memory(CALL, True) == {"error": lite.SAVED}, (L.card(CALL), cev))

# ---------------------------------------------------------------- the routes
check("the routes: the mod's GETs and POSTs reach the meeting side",
      set(ui.LITE_GET) >= {"/api/lite/state", "/api/lite/lines", "/api/lite/batch", "/api/lite/card",
                           "/api/lite/transcript"}
      and set(ui.LITE_POST) >= {"/api/lite/listen", "/api/lite/batch/sent", "/api/lite/pause", "/api/lite/resume",
                                "/api/lite/stop", "/api/lite/pass"}
      and ui.lite_get("/api/lite/state", {"cc": CC})["live"] is None)
ui.stop_session = real_stop

# ---------------------------------------------------------------- a meeting names itself from what's been said
class FakeT(object):
    wrote = {}
    def write(self, path, data):
        FakeT.wrote[path] = data
        return True
    def agent_cmd(self, args):
        return ["/bin/echo", "Routing cache review"]
real_for = ui.transport.for_config
ui.transport.for_config = lambda cfg=None: FakeT()
T = "meeting-20261019-203430"
td = ui.SESSIONS / T
td.mkdir(parents=True, exist_ok=True)
(td / "meta.json").write_text(json.dumps({"kind": "meeting", "started": time.time() - 300}))
(td / "claude.off").write_text("")
(td / "transcript.jsonl").write_text("".join(json.dumps({"t": k, "spk": 1, "text": "line %d" % k}) + "\n" for k in range(ui.TITLE_AFTER_LINES)))
ui.maybe_title(T)
check("the full app's titler skips a meeting whose own Claude is off", not (td / "title.auto.txt").exists() and T not in ui.TITLED)
ui.maybe_title(T, listener=True)
for _ in range(50):
    if (td / "title.auto.txt").exists():
        break
    time.sleep(0.05)
check("a meeting Claude Code listens to: named from what's been said, by the same titler, its transcript sent to it",
      (td / "title.auto.txt").read_text().strip() == "Routing cache review" and ("live/%s.jsonl" % T) in FakeT.wrote,
      (FakeT.wrote.keys(), (td / "title.auto.txt").exists()))
U = "meeting-20261019-203431"
ud = ui.SESSIONS / U
ud.mkdir(parents=True, exist_ok=True)
for f in ("meta.json", "claude.off", "transcript.jsonl"):
    (ud / f).write_bytes((td / f).read_bytes())
(ud / "title.txt").write_text("Weekly sync\n")
ui.maybe_title(U, listener=True)
time.sleep(0.2)
check("  a name the person gave always wins", not (ud / "title.auto.txt").exists() and ui.session_info(U)["title"] == "Weekly sync")
ui.transport.for_config = real_for

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
