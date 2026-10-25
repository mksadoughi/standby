"""Add your Mac: today's recorder, an agent's meeting.

The sheet's switches as today's recording modes; the agent's own conversation, or a copy
when it is live elsewhere; the session marked as the agent's (agent.json); the
meeting card in the call card's shape; and the card sent to an agent that heard it on a copy,
kept in the outbox until the agents layer delivers it."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbaddmac-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import addmac as m  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-60s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:400])


check("both switches: Automatic, as today's default", m.capture_mode(True, True) == "auto")
check("meeting audio only, and mic only: today's two other modes",
      m.capture_mode(True, False) == "meeting" and m.capture_mode(False, True) == "mic")
check("both off: nothing to record", m.capture_mode(False, False) is None)

agent = {"id": "a-planner", "workspace": "w-orion", "session": "5f720bb1-0000-4000-8000-000000000001"}
ws = {"id": "w-orion", "machine": "devbox", "folder": "/Users/you/code/orion"}
fork = m.start_plan(agent, ws)
check("always a copy of the agent's conversation, on its machine and folder (forgetting is real)",
      fork == {"kind": "meeting", "where": "devbox", "workspace": "/Users/you/code/orion",
               "attach": agent["session"], "copy": True}, fork)
check("no machine given: this Mac", m.start_plan(agent, dict(ws, machine=None))["where"] == "local")
check("an agent with no conversation yet: a new one, nothing to copy",
      m.start_plan(dict(agent, session=None), ws)["copy"] is False)

sdir = root / "sessions" / "orion-20261015-1030"
sdir.mkdir(parents=True)
check("a session nobody's: not bound", m.bound(sdir) is None)
data = m.bind(sdir, agent, fork, True, False, "  Weekly routing sync ", now=1000.0)
check("bound: agent.json, with what the timeline reads (agent, copy, sources)",
      json.loads((sdir / m.AGENT_FILE).read_text()) == {
          "agent": "a-planner", "conversation": agent["session"], "copy": True,
          "meeting_audio": True, "mic": False, "name": "Weekly routing sync", "started": 1000.0,
          "stretches": [[1000.0, None]], "paused_at": None, "ended_at": None},
      (sdir / m.AGENT_FILE).read_text())
check("  and never meta.json, which the recorder writes", not (sdir / "meta.json").exists())
check("  read back", m.bound(sdir) == data)
check("the live session is this agent's meeting", m.live_for("a-planner", sdir.name, sdir) == sdir.name)
check("  not another agent's", m.live_for("a-d1", sdir.name, sdir) is None)
check("  nothing live: none", m.live_for("a-planner", None, sdir) is None)
(sdir / m.AGENT_FILE).write_text("{not json")
check("a broken agent.json: not bound, never an error", m.bound(sdir) is None)
m.bind(sdir, agent, fork, True, False, "Weekly routing sync", now=1000.0)

c = m.card(sdir.name, data, "", 1755.4, ["p99 target: 300 ms", "Fares cache by Thursday", "Jules owns it", "a fourth"])
check("the card: the call card's shape, with copy",
      sorted(c) == ["agent", "copy", "duration_s", "kind", "points", "session", "started", "title"]
      and c["kind"] == "meeting" and c["agent"] == "a-planner" and c["session"] == sdir.name, c)
check("  the sheet's name when nothing better, the duration in seconds",
      c["title"] == "Weekly routing sync" and c["duration_s"] == 755 and c["started"] == 1000.0, c)
check("  at most 3 points", len(c["points"]) == 3, c["points"])
check("  a better title wins", m.card(sdir.name, data, "Routing sync: Q4 targets", 1755, [])["title"]
      == "Routing sync: Q4 targets")
check("  nothing decided: no points, still a card", m.card(sdir.name, data, "", 1755, [])["points"] == [])
check("  no name at all: Meeting", m.card("s", {"agent": "a"}, "", 5, [])["title"] == "Meeting")

msg = m.card_message(c)
check("the card as a message to the real agent: title, minutes, where the transcript is, the points",
      msg.splitlines()[0] == "[MEETING SUMMARY] Meeting: Weekly routing sync (13 min), recorded by Standby. "
      "Transcript: standby-ctx --session %s" % sdir.name
      and msg.splitlines()[1:] == ["- p99 target: 300 ms", "- Fares cache by Thursday", "- Jules owns it"], msg)
check("  no emoji in it", all(ord(ch) < 0x2190 for ch in msg))

# -- Pause, Resume, End: one meeting in stretches, recorded time on the card
d = {"agent": "a-planner", "started": 1000.0, "stretches": [[1000.0, None]], "paused_at": None, "ended_at": None}
check("live: state live, recorded time up to now", m.state(d, True) == "live" and m.recorded_s(d, 1300.0) == 300.0)
m.paused(d, 1600.0)
check("paused: the stretch closes, the meeting stays open",
      d["stretches"] == [[1000.0, 1600.0]] and d["paused_at"] == 1600.0 and m.state(d, False) == "paused", d)
m.paused(d, 1700.0)
check("  paused twice (the window, then Quit): nothing moves", d["stretches"] == [[1000.0, 1600.0]]
      and d["paused_at"] == 1600.0, d)
check("  not over yet at 1:59 paused", not m.expired(d, 1600.0 + 2 * 3600 - 60))
check("  over at 2 hours paused", m.expired(d, 1600.0 + 2 * 3600))
m.resumed(d, 5000.0)
check("resumed: a new stretch, no longer paused", d["stretches"][-1] == [5000.0, None] and d["paused_at"] is None
      and not m.expired(d, 99999.0), d)
m.resumed(d, 5001.0)
check("  resumed twice: still one open stretch", len(d["stretches"]) == 2, d)
m.ended(d, 5300.0)
check("ended: the last stretch closed, state ended", d["stretches"][-1] == [5000.0, 5300.0] and d["ended_at"] == 5300.0
      and m.state(d, False) == "ended" and d["paused_at"] is None, d)
check("  recorded time is the stretches added up (pauses left out)", m.recorded_s(d) == 900.0)
check("  and the span is first start to last end", m.span(d) == (1000.0, 5300.0))
check("  an ended meeting never expires", not m.expired(d, 10 ** 9))
check("the card counts recorded time", m.card("s", d, "x", 5300.0, [])["duration_s"] == 900)
m.ended(d, 9999.0)
check("  ended twice: the first end stands", d["ended_at"] == 5300.0)
old_style = {"agent": "a", "started": 100.0}
check("a meeting from before stretches: start to end, as before", m.card("s", old_style, "", 400.0, [])["duration_s"] == 300)

box = root / "agents" / "outbox.jsonl"
r = m.deliver(c["agent"], msg)
r2 = m.deliver("a-d1", "second")
rows = [json.loads(x) for x in box.read_text().splitlines()]
check("until delivery lands: kept in the outbox, in order, nothing lost",
      r == r2 == {"queued": True} and [x["to"] for x in rows] == ["a-planner", "a-d1"]
      and rows[0]["text"] == msg and rows[0]["kind"] == "meeting_card", rows)
check("  the outbox is under STANDBY_ROOT, never the real one", str(m.OUTBOX).startswith(str(root)))

print("\nALL PASS" if not fails else "\nFAILURES: %s" % fails)
sys.exit(1 if fails else 0)
