"""An agent's meeting on your Mac, and its card.

The agent's own conversation, or a copy when it is open elsewhere; the sheet's switches as
the recording mode; Claude on for an agent's meeting whatever Settings says; one recorder at
a time; at Stop, card.json and, for a copy, the card to the real agent. The agent's
answers and the recorder are stand-ins."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, sys, tempfile, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbaddmacui-"))
os.environ["STANDBY_ROOT"] = str(root)
argv_file = root / "cli-argv.txt"
cli = root / "fake-standby"
cli.write_text('#!/bin/bash\nprintf "%%s\\n" "$@" > %s\nexit 0\n' % argv_file)
cli.chmod(0o755)
os.environ["STANDBY_CLI"] = str(cli)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import addmac  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-700:])


CONV = "11111111-2222-4333-8444-555555555555"
FORK = "66666666-7777-4888-9999-aaaaaaaaaaaa"
AGENTS = {"a-planner": {"id": "a-planner", "name": "Planner", "session": CONV, "machine": "local", "folder": "/Users/you/code/orion"}}
ui.MSG["agent"] = lambda ident: AGENTS.get(ident)       # the timeline's lookup, as calls use it

OPEN = {"open": False}
ui._agent_json = lambda args, timeout=20, machine=None: {"uuid": FORK} if args[0] == "resume-cmd" else {}
real_make_card = ui.make_card
ui.make_card = None                                      # the calls' generator: stood in for below

started, LIVE = [], {"sid": None}
ui.live_session = lambda: LIVE["sid"]


def fake_start(**kw):
    started.append(kw)
    return {"started": True}


def go_live(sid, kind="meeting"):
    d = ui.SESSIONS / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"kind": kind}))
    LIVE["sid"] = sid
    return sid


def mac_add(b):
    """An agent's meeting, as Standby once started one from its own window ({agent, meeting_audio,
    mic, name?}): answers {session} once the recorder is live. Meetings start from Claude Code
    now; this fixture makes such a session so the card, Stop and pause are tested."""
    mode = addmac.capture_mode(b.get("meeting_audio"), b.get("mic"))
    if mode is None:
        return {"error": "Turn on meeting audio or your mic"}
    agent, ws = ui.msg_agent(b.get("agent"))
    if agent is None or ws is None:
        return {"error": "No such agent"}
    who = ui.mac_with()
    if who:
        return {"error": "Your Mac is with %s: stop it there first" % who}
    plan = addmac.start_plan(agent, ws)
    r = ui.start_session(kind="meeting", workspace=plan["workspace"], attach=plan["attach"], copy=plan["copy"],
                      where=plan["where"], allow_open=True, capture=mode, agent=agent["id"],
                      model=ui.agent_model(agent["id"]), voice_id=ui.agent_voice(agent["id"]))
    if not r.get("started"):
        return {"error": r.get("error") or "Your Mac didn't start"}
    sid = ui._wait_live(ui.MAC_WAIT)
    if isinstance(sid, dict):
        return sid
    addmac.bind(ui.SESSIONS / sid, agent, plan, b.get("meeting_audio"), b.get("mic"), b.get("name"))
    if (b.get("name") or "").strip():
        ui.rename_session(sid, b["name"])
    ui.log("mac: %s's meeting %s (%s, on a copy)" % (agent["id"], sid, mode))
    return {"session": sid}


real_start, real_wait = ui.start_session, ui._wait_live
ui.start_session = fake_start
ui._wait_live = lambda wait: go_live("orion-20261015-1030")

check("both switches off: refused", mac_add({"agent": "a-planner", "meeting_audio": False, "mic": False})
      == {"error": "Turn on meeting audio or your mic"} and started == [])
check("an agent that isn't there: refused", mac_add({"agent": "a-nope", "mic": True}) == {"error": "No such agent"})

real_model = ui.agent_model
ui.agent_model = lambda ident: "haiku"           # the agent's own choice goes with its meeting
ui.agent_voice = lambda ident: "pocket:marius"
r = mac_add({"agent": "a-planner", "meeting_audio": True, "mic": True, "name": "Weekly routing sync"})
ui.agent_voice = lambda ident: None
ui.agent_model = lambda ident: None            # no choice from here on (the real one starts the timeline, which replaces the stand-in lookup)
sid = "orion-20261015-1030"
check("added: answers the new session once it is live", r == {"session": sid}, r)
check("  on a copy of the agent's conversation, in its workspace, Automatic, Claude on",
      started[-1] == dict(kind="meeting", workspace="/Users/you/code/orion", attach=CONV, copy=True, where="local",
                          allow_open=True, capture="auto", agent="a-planner", model="haiku", voice_id="pocket:marius"), started[-1])
b = addmac.bound(ui.SESSIONS / sid)
check("  the session is the agent's (agent.json), with its sources",
      b and b["agent"] == "a-planner" and b["copy"] is True and b["meeting_audio"] and b["mic"], b)
check("  named as the sheet said", (ui.SESSIONS / sid / "title.txt").read_text().strip() == "Weekly routing sync")
check("  the meeting event says so", ui.mac_event(sid) == {"session": sid, "agent": "a-planner", "live": True,
                                                         "paused": False, "ended": False, "started": b["started"],
                                                         "recorded_s": ui.mac_event(sid)["recorded_s"]}, ui.mac_event(sid))
check("  with the time recorded so far", 0 <= ui.mac_event(sid)["recorded_s"] < 60, ui.mac_event(sid))
check("another session's: no meeting event", ui.mac_event("orion-20260101-0000") is None)

n = len(started)
r = mac_add({"agent": "a-planner", "meeting_audio": True, "mic": False})
check("one recorder on the Mac: a second is refused, naming who has it",
      r == {"error": "Your Mac is with Planner: stop it there first"} and len(started) == n, r)
# A meeting just ended (or paused) whose recorder is still exiting (a long recording finishing):
# it's waited for, not a reason to refuse; one that won't go is still named, after the wait.
import threading  # noqa: E402
kept = ui.addmac.bound(ui.SESSIONS / sid)
ui.addmac.save(ui.SESSIONS / sid, dict(kept, ended_at=time.time()))
threading.Timer(0.3, lambda: LIVE.update(sid=None)).start()
t0 = time.time()
who = ui.mac_with()
check("a meeting ended a moment ago, its recorder still exiting: waited for, then nobody has the Mac",
      who is None and 0.2 < time.time() - t0 < 5, (who, time.time() - t0))
LIVE["sid"] = sid
check("  one that doesn't exit in time: still named", ui.mac_with(wait=0.3) == "Planner")
ui.addmac.save(ui.SESSIONS / sid, kept)
check("  a meeting that's recording: refused at once, no wait", (lambda t: ui.mac_with() == "Planner" and time.time() - t < 1)(time.time()))

check("Settings has no meeting model any more (an agent's model is its own), and voice mode defaults to Haiku",
      'id="s_model"' not in ui.PAGE and '<option value="haiku">Haiku · Recommended</option>' in ui.PAGE
      and ui.get_settings()["voice_model"] in ("haiku", "sonnet", "opus"))

# -- the recorder's mode, per start (the CLI's argv), and Claude on for an agent's meeting
LIVE["sid"] = None
s = ui.get_settings()
ui.save_settings = getattr(ui, "save_settings", None)
real_settings = ui.get_settings
ui.get_settings = lambda: dict(s, capture="mic")
ui.wait_for_shutdown = lambda: True
real_start_live = ui.live_session
ui.live_session = lambda: None
ui._do_start(None, "meeting", "/Users/you/code/orion", "local", False, "meeting", "a-planner")
argv = argv_file.read_text().split()
check("_do_start: the sheet's mode, not Settings'", argv[argv.index("--mode") + 1] == "meeting", argv)
check("  an agent's meeting has Claude on", "--no-claude" not in argv, argv)
ui._do_start(None, "meeting", "/Users/you/code/orion", "local", False, "meeting", "a-planner", "haiku",
             "pocket:marius")
argv = argv_file.read_text().split()
check("  and its voice (--claude-voice), for the call's speaker",
      argv[argv.index("--claude-voice") + 1] == "pocket:marius", argv)
check("  the agent's model goes to the recorder (--claude-model), for the agent's meeting engine",
      argv[argv.index("--claude-model") + 1] == "haiku", argv)
ui._do_start(None, "meeting", "/Users/you/code/orion", "local", False, "meeting", "a-planner", "haiku; rm -rf ~",
             "bad\nvoice")
check("  never anything but a model, or a one-line voice", "--claude-model" not in argv_file.read_text().split()
      and "--claude-voice" not in argv_file.read_text().split()
      and "rm" not in argv_file.read_text().split(), argv_file.read_text())
check("  models: an alias or a full id", ui.valid_model("opus") and ui.valid_model("claude-sonnet-5-5")
      and not ui.valid_model("gpt-4") and not ui.valid_model("") and not ui.valid_model(None) and not ui.valid_model("sonnet; ls"))
ui._do_start(None, "meeting", "/Users/you/code/orion", "local", False)
argv = argv_file.read_text().split()
check("  a plain meeting: Settings' mode, and Claude on",
      argv[argv.index("--mode") + 1] == "mic" and "--no-claude" not in argv, argv)
ui.get_settings, ui.live_session = real_settings, real_start_live
with ui._lock:
    ui.STARTING.update(state=None, error=None)

# -- open in VS Code or a Terminal: a copy, never waiting
OPEN["open"] = True
LIVE["sid"] = None
ui._wait_live = lambda wait: go_live("orion-20261015-1100")
r = mac_add({"agent": "a-planner", "meeting_audio": False, "mic": True})
check("open elsewhere: a copy as always, mic only",
      r == {"session": "orion-20261015-1100"} and started[-1]["copy"] is True and started[-1]["capture"] == "mic", started[-1])
check("  marked as a copy", addmac.bound(ui.SESSIONS / "orion-20261015-1100")["copy"] is True)
OPEN["open"] = False

# -- at the end: the card, waiting for the user to keep or forget it
box = root / "agents" / "outbox.jsonl"
sent = []
ui.MSG["deliver"] = lambda agent, text, session=None: sent.append((agent, text)) or True
c = ui.mac_card("orion-20261015-1100", now=addmac.bound(ui.SESSIONS / "orion-20261015-1100")["started"] + 600)
card = json.loads((ui.SESSIONS / "orion-20261015-1100" / "card.json").read_text())
check("no generator yet: card.json still written, nothing decided, the card's fields",
      card["points"] == [] and card["duration_s"] == 600 and card["copy"] is True and "at" in card
      and card["started_at"] and card["ended_at"] and card["ended_at"] - card["started_at"] == 600, card)
check("  memory pending: nothing goes to the agent until the user keeps it",
      card["memory"] == "pending" and not card["delivered_at"] and sent == [] and not box.exists(), (card, sent))
c2 = json.loads((ui.SESSIONS / "orion-20261015-1100" / "card.json").read_text())
c2["memory"] = "kept"
(ui.SESSIONS / "orion-20261015-1100" / "card.json").write_text(json.dumps(c2))
ui.mac_card("orion-20261015-1100")
check("  made again (Summarize): the user's choice stands",
      json.loads((ui.SESSIONS / "orion-20261015-1100" / "card.json").read_text())["memory"] == "kept")
rows = []

(ui.SESSIONS / sid / "card.json").write_text(json.dumps({"points": ["p99: 300 ms", "Fares cache by Thursday"],
                                                       "title": "Routing sync", "at": 1.0, "duration_s": 60,
                                                       "copy": False, "delivered_at": None}))
made = []
ui.make_card = lambda s: made.append(s)
c = ui.mac_card(sid)
check("with the calls' make_card: it writes the points, the card uses them",
      made == [sid] and c["points"] == ["p99: 300 ms", "Fares cache by Thursday"] and c["title"] == "Routing sync", c)
check("  nothing sent", sent == [] and not box.exists())
ui.make_card = None

ran = []
real_card = ui.mac_card
ui.mac_card = lambda s, now=None: ran.append(s)
LIVE["sid"] = None
ui.mac_stopped(sid)
go_live("orion-20261015-1200", kind="talk")
addmac.bind(ui.SESSIONS / "orion-20261015-1200", {"id": "a-planner", "session": CONV}, {}, False, True, "")
ui.mac_stopped("orion-20261015-1200")
ui.mac_stopped("orion-20260101-0000")
time.sleep(0.3)
check("stopping (the companion's Stop, Quit): a pause, no card yet", ran == [] and
      addmac.bound(ui.SESSIONS / sid)["paused_at"] and ui.mac_event(sid)["paused"], ran)
check("  a call's session (the calls' own) isn't paused by it",
      not addmac.bound(ui.SESSIONS / "orion-20261015-1200").get("paused_at"))
ui.mac_card = real_card

# -- Pause, Resume, End meeting, and two hours paused
events = []
ui.MSG["event"] = lambda ev, data: events.append((ev, data))
real_stop = ui.stop_session
ui.stop_session = lambda detach=False: (LIVE.update(sid=None), ui.mac_stopped(m_sid), {"ok": True})[2]
started_at = time.time() - 100
m_sid = go_live("orion-20261015-1400")
addmac.bind(ui.SESSIONS / m_sid, AGENTS["a-planner"], {"copy": True}, True, False, "Planning", now=started_at)
r = ui.mac_pause(m_sid)
d = addmac.bound(ui.SESSIONS / m_sid)
check("Pause: the recorder stops, the meeting stays open, the stream says paused",
      r == {"ok": True} and LIVE["sid"] is None and d["paused_at"] and not d["ended_at"]
      and events[-1] == ("meeting", ui.mac_event(m_sid)) and events[-1][1]["paused"] and not events[-1][1]["live"], events[-1:])
check("  paused again: still fine, nothing moves", ui.mac_pause(m_sid) == {"ok": True}
      and addmac.bound(ui.SESSIONS / m_sid)["paused_at"] == d["paused_at"])
n = len(started)
ui._wait_live = lambda wait: go_live(m_sid)
r = ui.mac_resume(m_sid)
d = addmac.bound(ui.SESSIONS / m_sid)
check("Resume: today's Resume into the same session, its own switches, the agent on",
      r == {"session": m_sid} and len(started) == n + 1 and started[-1].get("resume") == m_sid
      and started[-1]["capture"] == "meeting" and started[-1]["agent"] == "a-planner", started[-1:])
check("  a second stretch, no longer paused, the stream says live",
      len(d["stretches"]) == 2 and d["paused_at"] is None and events[-1][1]["live"], d)
check("  resumed while live: just the session", ui.mac_resume(m_sid) == {"session": m_sid} and len(started) == n + 1)
other = go_live("orion-20261015-1500")
ui.mac_pause(m_sid)
LIVE["sid"] = other
check("  the Mac with another session: refused, saying so",
      "stop it there first" in ui.mac_resume(m_sid).get("error", ""))
LIVE["sid"] = None
before = ui.mac_event(m_sid)["recorded_s"]
time.sleep(0.3)
check("  recorded_s: the stretches added up, standing still while paused",
      ui.mac_event(m_sid)["paused"] and ui.mac_event(m_sid)["recorded_s"] == before
      and before == round(addmac.recorded_s(addmac.bound(ui.SESSIONS / m_sid)), 1), (before, ui.mac_event(m_sid)))
LIVE["sid"] = m_sid
cards = []
ui.mac_card = lambda s, now=None: cards.append(s)
r = ui.mac_end(m_sid, background=False)
d = addmac.bound(ui.SESSIONS / m_sid)
check("End meeting while live: stopped, over for good, one card",
      r == {"ok": True} and LIVE["sid"] is None and d["ended_at"] and d["stretches"][-1][1] is not None
      and cards == [m_sid] and events[-1][1]["ended"] and not events[-1][1]["paused"], (d, cards))
check("  ended again: nothing more", ui.mac_end(m_sid, background=False) == {"ok": True} and cards == [m_sid])
check("  no resuming an ended meeting", ui.mac_resume(m_sid) == {"error": "That meeting has ended"}
      and ui.mac_pause(m_sid) == {"error": "That meeting has ended"})
check("not an agent's meeting: refused", ui.mac_pause("orion-20260101-0000") == {"error": "No such meeting"}
      and ui.mac_end("../etc") == {"error": "No such meeting"})

# the sweep: a recorder gone without a pause (a crash, Quit) is paused; two hours paused, it ends
gone = go_live("orion-20261015-1600")
addmac.bind(ui.SESSIONS / gone, AGENTS["a-planner"], {"copy": True}, True, True, "", now=time.time() - 50)
old = go_live("orion-20261015-0600")
addmac.bind(ui.SESSIONS / old, AGENTS["a-planner"], {"copy": True}, True, True, "", now=time.time() - 3 * 3600)
addmac.save(ui.SESSIONS / old, addmac.paused(addmac.bound(ui.SESSIONS / old), time.time() - 2 * 3600 - 5))
LIVE["sid"], cards[:] = None, []
ui.mac_end_bg = ui.mac_end
ui.mac_end = lambda s, now=None, background=True: ui.mac_end_bg(s, now=now, background=False)
ui.mac_sweep()
check("sweep: a recorder gone without a pause is paused, not ended",
      addmac.bound(ui.SESSIONS / gone)["paused_at"] and not addmac.bound(ui.SESSIONS / gone)["ended_at"])
check("  paused two hours: ended by itself, with its card",
      addmac.bound(ui.SESSIONS / old)["ended_at"] and cards == [old], cards)
LIVE["sid"] = gone
addmac.save(ui.SESSIONS / gone, addmac.resumed(addmac.bound(ui.SESSIONS / gone)))
ui.mac_sweep()
check("  the live one is left alone", not addmac.bound(ui.SESSIONS / gone)["paused_at"])
ui.mac_end = ui.mac_end_bg
ui.mac_card, ui.stop_session, ui.MSG["event"] = real_card, real_stop, None

# -- no orb for an agent's meeting, recording or paused: the orb is for calls only
hooks = ui.CompanionHooks()
ui._live_now = lambda ttl=0.5: LIVE["sid"]
orb = go_live("orion-20261015-1700")
addmac.bind(ui.SESSIONS / orb, AGENTS["a-planner"], {"copy": True}, True, True, "Design review", now=time.time() - 30)
check("an agent's meeting recording: nothing for the orb, though Quit still knows it records",
      ui.companion_state()["live"] is None and hooks.live_kind() == "meeting" and hooks.recording() is True
      and hooks.orb_asked() is False)
hooks.ask_orb()
check("  minimised (Cmd+M): never sent to the orb", hooks.orb_asked() is False and orb not in ui.LITE_ORB)
LIVE["sid"] = None
ui.mac_stopped(orb)
check("  paused: no orb either, nothing live, nothing recording",
      ui.companion_state()["live"] is None and hooks.live_kind() is None and hooks.recording() is False)
ui.mac_card = lambda s, now=None: None
ui.mac_end(orb, background=False)
ui.mac_card = real_card

print("\nALL PASS" if not fails else "\nFAILURES: %s" % fails)
sys.exit(1 if fails else 0)
