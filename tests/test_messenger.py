"""The model behind Standby's navigator and its past calls and meetings (mac/messenger.py):
workspaces and agents in workspaces.json, read only for the page; what of an agent is live;
the cards made from Standby's own sessions; meeting events; the page's event stream; and a
Claude Code session that becomes an agent."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, sys, tempfile, time, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mac"))
import messenger as ms  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-78s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


root = Path(tempfile.mkdtemp(prefix="sbmsg-"))
SESS = root / "sessions"
SESS.mkdir()
NADIA, THEO = ("aaaaaaaa-0000-4000-8000-00000000000%d" % i for i in (1, 2))
T0 = 1792054800.0

ui = types.SimpleNamespace(SESSIONS=SESS, live=None, mac=[], log=lambda m: None,
                           machine_name=lambda m: "This Mac" if m == "local" else m)
ui.live_session = lambda: ui.live
ui.mac_event = lambda sid: (ui.mac.append(sid), {"session": sid, "live": sid == ui.live})[1]

m = ms.Messenger(root, ui=ui)
q = m.hub.subscribe()


def drain():
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ---------------------------------------------------------------- workspaces and agents, read only
r = m.add_workspace({"machine": "local", "folder": "/Users/you/code/orion",
                     "agents": [{"session": NADIA, "name": "Nadia"}, {"session": THEO, "name": "Theo"}]})
w = r["workspace"]
nadia, theo = (a["id"] for a in r["agents"])
check("a workspace: named after its folder, on this Mac, with its agents in order",
      w["name"] == "orion" and w["machine_name"] == "This Mac" and w["agents"] == [nadia, theo]
      and ms.ID.match(w["id"]) and all(ms.ID.match(x) for x in (nadia, theo)), r)
saved = json.loads((root / "workspaces.json").read_text())
check("  kept in workspaces.json, sessions and all", saved["agents"][nadia]["session"] == NADIA
      and saved["workspaces"][0]["agents"] == [nadia, theo], saved)
got = m.workspaces()
check("the page's list: workspaces and agents with what of each is live, and nothing of messages",
      [a["id"] for a in got["agents"]] == [nadia, theo] and got["agents"][0]["live"] == {"call": None, "meeting": None}
      and not any(k in got for k in ("last_to",)) and "unread" not in got["workspaces"][0]
      and "unread" not in got["agents"][0], got)
check("  the same conversation twice is one agent", m.add_agent({"workspace": w["id"], "session": NADIA})["agent"]["id"] == nadia)
check("  a session id that isn't one: refused", m.add_agent({"workspace": w["id"], "session": "../.."}).get("error"))
drain()
m.publish_changes()
ev = drain()
check("the navigator's events: each agent and workspace once", sorted(e for e, _ in ev) == ["agent", "agent", "workspace"], ev)
m.publish_changes()
check("  nothing changed: nothing sent", not drain())

# ---------------------------------------------------------------- cards
call = SESS / "2026-10-15_1030"
call.mkdir()
(call / "meta.json").write_text(json.dumps({"kind": "talk", "started": T0 + 30}))
(call / "agent.json").write_text(json.dumps({"agent": nadia, "copy": False, "started": T0 + 30, "moved": ["2026-10-15_1033"]}))
later = SESS / "2026-10-15_1033"
later.mkdir()
(later / "meta.json").write_text(json.dumps({"kind": "meeting", "started": T0 + 200}))
(later / "agent.json").write_text(json.dumps({"agent": nadia, "call": call.name, "meeting_audio": True}))
ui.call_sessions = lambda sid: [sid] + (json.loads((SESS / sid / "agent.json").read_text()).get("moved", [])
                                        if (SESS / sid / "agent.json").exists() else [])
ui.live = later.name
cards = [x for x in m.cards() if x["from"] == nadia]
check("a call card while it's live: one card for the whole call, though it moved; no points yet",
      len(cards) == 1 and cards[0]["kind"] == "call" and cards[0]["live"] and cards[0]["points"] is None
      and cards[0]["sessions"] == [call.name, later.name] and cards[0]["id"] == "c-" + call.name, cards)
check("  the agent is on a call", m.workspaces()["agents"][0]["live"]["call"] == later.name
      and m.workspaces()["agents"][0]["status"] == "call")
m.watch_live()
check("  Your Mac added (the live session changed): the meeting event goes out",
      ui.mac == [later.name] and ("meeting", {"session": later.name, "live": True}) in drain())
ui.live = None
(call / "card.json").write_text(json.dumps({"points": ["Ship the cache fix", "No group calls"], "at": T0 + 30,
                                             "duration_s": 240, "copy": False, "delivered_at": None}))
m.on_event("card", {"session": call.name})
card = [x for x in m.cards() if x["id"] == "c-" + call.name] if ("card", {"session": call.name}) in drain() else []
check("ended, card.json written (C's \"card\" event): the page is told, and the card has its points",
      card and not card[-1]["live"] and card[-1]["points"] == ["Ship the cache fix", "No group calls"]
      and card[-1]["duration_s"] == 240, card)
check("  a call card with points has memory pending and no gist", card[-1].get("memory") is None and "gist" not in card[-1], card[-1])
(call / "card.json").write_text(json.dumps({"points": [], "gist": "A quick sound check: the agent could hear you.", "at": T0 + 30,
                                             "duration_s": 60, "copy": True, "memory": "pending", "delivered_at": None}))
m.on_event("card", {"session": call.name})
card = [x for x in m.cards() if x["id"] == "c-" + call.name] if ("card", {"session": call.name}) in drain() else []
check("  nothing decided: the gist goes out with the call card, and its memory",
      card and card[-1]["points"] == [] and card[-1]["gist"] == "A quick sound check: the agent could hear you."
      and card[-1]["memory"] == "pending", card)
meet = SESS / "2026-10-15_1235"
meet.mkdir()
(meet / "meta.json").write_text(json.dumps({"kind": "meeting", "started": T0 + 900, "attach": {"from": THEO}}))
(meet / "title.txt").write_text("Weekly routing sync")
c = [x for x in m.cards() if x["from"] == theo and x["kind"] == "meeting"]
check("a meeting on an agent's conversation (meta.attach.from): a meeting card with its title",
      len(c) == 1 and c[0]["title"] == "Weekly routing sync" and c[0]["from"] == theo, c)
# A meeting changes over its life but keeps one card: live, paused (no points yet), resumed, ended.
m.attach()
state = {"live": True, "paused": False, "ended": False}
ui.mac_event = lambda sid: dict(session=sid, agent=theo, started=T0 + 900, **state) if sid == meet.name else None
ui.live = meet.name
def meeting_card():
    return [x for x in m.cards() if x["from"] == theo and x["kind"] == "meeting"]
c0 = meeting_card()[0]
check("a live meeting: live, not paused, no memory yet", c0["live"] and not c0["paused"] and c0["memory"] is None, c0)
drain()
state.update(live=False, paused=True); ui.live = None
ui.MSG["event"]("meeting", {"session": meet.name, "agent": theo, "live": False, "paused": True, "ended": False})
got = drain()
c1 = meeting_card()
check("paused: the meeting event goes out, and the same card is not live, paused, no points",
      any(e == "meeting" for e, _ in got) and c1 and c1[-1]["paused"] and not c1[-1]["live"]
      and c1[-1]["points"] is None and c1[-1]["id"] == c0["id"], got)
ui.MSG["event"]("meeting", {"session": meet.name, "agent": theo, "live": False, "paused": True, "ended": False})
check("  the same event again is sent once", not drain())
state.update(live=True, paused=False); ui.live = meet.name
ui.MSG["event"]("meeting", {"session": meet.name, "agent": theo, "live": True, "paused": False, "ended": False})
drain()
c2 = meeting_card()
check("resumed: the same card id, live again", c2 and c2[-1]["live"] and not c2[-1]["paused"], c2)
state.update(live=False, ended=True); ui.live = None
(meet / "card.json").write_text(json.dumps({"points": ["Ship Thursday"], "at": T0 + 900, "duration_s": 1500.0,
                                            "started_at": "10:02", "ended_at": "10:47", "memory": "pending"}))
ui.MSG["event"]("meeting", {"session": meet.name, "agent": theo, "live": False, "paused": False, "ended": True})
drain()
c3 = meeting_card()
check("ended (even though the live session didn't change): recorded time, the span, keep-or-forget, points",
      c3 and c3[-1]["duration_s"] == 1500.0 and c3[-1]["memory"] == "pending" and c3[-1]["started_at"] == "10:02"
      and c3[-1]["ended_at"] == "10:47" and c3[-1]["points"] == ["Ship Thursday"] and not c3[-1]["live"], c3)
(meet / "card.json").write_text(json.dumps({"points": [], "gist": "Walked through the Q4 latency target.", "at": T0 + 900,
                                            "duration_s": 1500.0, "memory": "pending"}))
c4 = meeting_card()[0]
check("a meeting where nothing was decided carries its one-line gist; the points are empty, not missing",
      c4["points"] == [] and c4["gist"] == "Walked through the Q4 latency target.", c4)
ui.mac_event = lambda sid: (ui.mac.append(sid), {"session": sid, "live": sid == ui.live})[1]    # as before

# A browser session, from when Standby had a browser: it has an agent.json like a meeting's, and no card.
web = SESS / "2026-10-15_1400"
web.mkdir()
(web / "meta.json").write_text(json.dumps({"kind": "browser", "started": T0 + 2000}))
(web / "agent.json").write_text(json.dumps({"agent": theo, "copy": True, "meeting_audio": False, "mic": False, "started": T0 + 2000}))
check("a browser session from before: no card in the timeline, of any kind",
      not [x for x in m.cards() if x.get("session") == web.name])

other = SESS / "2026-10-15_0800"
other.mkdir()
(other / "meta.json").write_text(json.dumps({"kind": "meeting", "started": T0}))
check("  a meeting that's no agent's: no card", not [x for x in m.cards() if x.get("session") == other.name])


# ---------------------------------------------------------------- hooks
m.attach()
check("attach(): calls and meetings find an agent by id, with its machine and folder",
      ui.MSG["agent"](nadia) == {"id": nadia, "name": "Nadia", "session": NADIA, "machine": "local",
                               "folder": "/Users/you/code/orion"} and ui.MSG["agent"]("a-nope") is None)
ui.MSG["event"]("call", {"session": "s1", "phase": "live"})
check("  their events go to the page as they are", ("call", {"session": "s1", "phase": "live"}) in drain())

# ---------------------------------------------------------------- a page that is told what it missed
big = ms.Hub()
sub = big.subscribe()
for i in range(sub.maxsize + 5):
    big.publish("message", {"id": "m%d" % i})
check("a page that falls too far behind is dropped, and marked, not left open and silent",
      sub.dropped and sub not in big._subs, (sub.dropped, len(big._subs)))
gen = ms.frames(sub)
check("  its stream says `resync` and ends, so the page reconnects and loads what it missed",
      next(gen) == b"retry: 500\n" + ms.sse("resync", {}) and next(gen, None) is None)
live_sub = big.subscribe()
big.publish("agent", {"id": "a-1"})
g = ms.frames(live_sub, keepalive=0.05)
check("a page keeping up: each event as a frame, a comment line while quiet",
      next(g) == ms.sse("agent", {"id": "a-1"}) and next(g) == b": keep-alive\n\n")

check("an event is one SSE frame", ms.sse("agent", {"id": "a-1"}) == b'event: agent\ndata: {"id": "a-1"}\n\n')

# Standby inside Claude Code: the session it runs in becomes an agent, in its folder's workspace, marked
CC = "aaaaaaaa-0000-4000-8000-0000000000cc"
ccf = str(root / "code" / "orion")
aid = m.agent_for_cc(CC, ccf)
ws_cc = [x for x in m.store.data["workspaces"] if x["folder"] == ccf]
check("a Claude Code session Standby runs in: an agent in a new workspace for its folder, marked from Claude Code",
      aid and len(ws_cc) == 1 and ws_cc[0]["agents"] == [aid] and m.agent_json(aid)["cc"] is True, (aid, ws_cc))
check("  again: the same agent, nothing new", m.agent_for_cc(CC, ccf) == aid and len([x for x in m.store.data["workspaces"] if x["folder"] == ccf]) == 1)
check("  an agent already there stays as it was", all(m.agent_json(x)["cc"] is False for x in m.store.data["agents"] if x != aid))
lm = SESS / "lite-meeting"
lm.mkdir()
(lm / "agent.json").write_text(json.dumps({"agent": "lite:" + CC, "meeting_audio": True, "mic": True, "started": T0 + 50}))
(lm / "meta.json").write_text(json.dumps({"kind": "meeting"}))
check("  its meetings from Claude Code (owner lite:<session>) are its agent's",
      m._session_agent("lite-meeting") == (aid, "meeting", "lite-meeting") and any(c["session"] == "lite-meeting" and c["from"] == aid for c in m.cards()))
check("  no folder, no agent (said by the caller)", m.agent_for_cc("aaaaaaaa-0000-4000-8000-0000000000dd", "") is None)

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
