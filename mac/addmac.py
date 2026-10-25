"""Add your Mac: today's recorder brought into a conversation with an agent.

A meeting stays a Standby session, as it always was (its transcript, audio, speaker
labels, snapshots, repass, export). What is new is whose it is: an agent's. Its Claude
runs on a copy of that agent's conversation, forked as the meeting starts: it knows
everything up to then and hears the meeting live, and the agent itself hears nothing.
At the end the user chooses: keep it (the card's summary goes to the agent, once) or
forget it (nothing ever reached the agent, so it really is forgotten).

Plain Python: the app (ui.py) does the starting and stopping, with these answers.
"""
import json
import os
import time
from pathlib import Path

ROOT = Path(os.environ.get("STANDBY_ROOT", os.path.expanduser("~/.standby")))
PAUSE_ENDS = 2 * 3600.0           # a meeting paused this long ends by itself, as End does
AGENT_FILE = "agent.json"          # in the session's folder: whose meeting it is, how it ran
OUTBOX = ROOT / "agents" / "outbox.jsonl"

# The sheet's two switches, as today's recording modes (Settings > Recording > Record).
MODES = {(True, True): "auto", (True, False): "meeting", (False, True): "mic"}


def capture_mode(audio, mic):
    """The recording mode for the sheet's switches, or None: both off records nothing."""
    return MODES.get((bool(audio), bool(mic)))


def start_plan(agent, workspace):
    """How the meeting starts for this agent: start_session's arguments. Always on a copy
    of its conversation, open elsewhere or not: forgetting a meeting is only real if the
    agent never heard it. agent and workspace: as the app keeps them (the agent's
    `session` is its Claude Code conversation; machine and folder are its workspace's)."""
    conv = agent.get("session") or None
    return {"kind": "meeting", "where": workspace.get("machine") or "local",
            "workspace": workspace.get("folder") or "", "attach": conv, "copy": bool(conv)}


def bind(sdir, agent, plan, audio, mic, name, now=None):
    """Mark a session as this agent's meeting (agent.json: a card's agent and
    sources are read from it). Its own file, never meta.json, which the recorder writes
    while it runs."""
    now = now or time.time()
    data = {"agent": agent.get("id"), "conversation": agent.get("session"),
            "copy": bool(plan.get("copy")), "meeting_audio": bool(audio), "mic": bool(mic),
            "name": (name or "").strip()[:120], "started": now,
            "stretches": [[now, None]], "paused_at": None, "ended_at": None}
    Path(sdir, AGENT_FILE).write_text(json.dumps(data))
    return data


def save(sdir, data):
    Path(sdir, AGENT_FILE).write_text(json.dumps(data))
    return data


# -- Pause, Resume, End: one meeting, recorded in stretches, one card at the end

def state(data, live):
    """"live", "paused" or "ended", for the window and the meeting event."""
    if data.get("ended_at"):
        return "ended"
    return "live" if live else "paused"


def paused(data, now=None):
    """The recorder stopped (Pause in the window, Stop on the companion, Quit): the live
    stretch closes and the meeting stays open. Idempotent."""
    now = now or time.time()
    st = data.setdefault("stretches", [[data.get("started") or now, None]])
    if st and st[-1][1] is None:
        st[-1][1] = max(now, st[-1][0])
    if not data.get("ended_at") and not data.get("paused_at"):
        data["paused_at"] = now
    return data


def resumed(data, now=None):
    """Recording again into the same meeting: a new stretch."""
    now = now or time.time()
    st = data.setdefault("stretches", [])
    if not st or st[-1][1] is not None:
        st.append([now, None])
    data["paused_at"] = None
    return data


def ended(data, now=None):
    """End meeting (or 2 hours paused): the last stretch closed, the meeting over."""
    now = now or time.time()
    paused(data, now)
    data["paused_at"] = None
    data["ended_at"] = data.get("ended_at") or now
    return data


def recorded_s(data, now=None):
    """The time actually recorded: the live stretches added up (a live one up to now)."""
    now = now or time.time()
    total = 0.0
    for a, b in data.get("stretches") or []:
        total += max(0.0, (b if b is not None else now) - a)
    return total


def span(data, now=None):
    """(first start, last end) of the meeting, for the card beside the recorded time."""
    st = data.get("stretches") or []
    if not st:
        return data.get("started"), data.get("ended_at")
    return st[0][0], st[-1][1] if st[-1][1] is not None else (now or time.time())


def expired(data, now=None, limit=PAUSE_ENDS):
    """Paused for `limit` seconds or more: it ends by itself."""
    now = now or time.time()
    return bool(not data.get("ended_at") and data.get("paused_at") and now - data["paused_at"] >= limit)


def bound(sdir):
    """The agent.json of a session, or None: not an agent's meeting."""
    try:
        data = json.loads(Path(sdir, AGENT_FILE).read_text())
        return data if isinstance(data, dict) and data.get("agent") else None
    except (OSError, ValueError):
        return None


def live_for(agent_id, sid, sdir):
    """The live session `sid`, if it is this agent's meeting, else None."""
    if not sid:
        return None
    data = bound(sdir)
    return sid if data and data["agent"] == agent_id else None


def card(sid, data, title, ended, pts):
    """The meeting card as a message to send, the call card's shape: {kind,
    agent, session, title, started, duration_s, points, copy}. The points are card.json's,
    which the calls' make_card writes (`standby-agent decisions`): up to 3, none if nothing
    was decided (card.json's gist then says what it was about)."""
    started = float(data.get("started") or ended)
    took = recorded_s(data, ended) if data.get("stretches") else ended - started
    return {"kind": "meeting", "agent": data.get("agent"), "session": sid,
            "title": (title or data.get("name") or "Meeting").strip(),
            "started": started, "duration_s": max(0, int(round(took))),
            "points": list(pts or [])[:3], "copy": bool(data.get("copy"))}


# A copy's meeting card reaches the real agent with this tag, which the timeline hides.
SUMMARY_TAG = "[MEETING SUMMARY]"


def card_message(c):
    """The card as one plain message, for the agent, when the user keeps the meeting."""
    mins = max(1, int(round(c["duration_s"] / 60.0)))
    lines = ["%s Meeting: %s (%d min), recorded by Standby. Transcript: standby-ctx --session %s"
             % (SUMMARY_TAG, c["title"], mins, c["session"])]
    lines += ["- " + p for p in c["points"]]
    if not c["points"] and c.get("gist"):
        lines.append("Gist: " + c["gist"])         # nothing was decided: what it was about
    return "\n".join(lines)


def deliver_stub(agent_id, text, kind="meeting_card", outbox=None):
    """Delivering a message to an agent, until the agents layer does: kept in an outbox so
    nothing is lost, and the agents layer delivers what is waiting there. {"queued": True}."""
    path = Path(outbox or OUTBOX)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps({"to": agent_id, "kind": kind, "text": text, "at": time.time()}) + "\n")
    return {"queued": True}


deliver = deliver_stub             # the agents layer replaces this with the real delivery
