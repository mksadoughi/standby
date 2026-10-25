"""Calls with an agent: today's voice mode, one to one with an agent.

The call card's points come from `standby-agent decisions`: one throwaway,
tool-less call on the call's chat, never a turn in the agent's conversation. The
stand-in Claude here answers with list marks, a blank line and a fourth point,
as a model might; the card keeps three clean lines."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile

fails = []


def check(label, ok, detail=""):
    print("  %-62s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label.strip())
        if detail != "":
            print("     %s" % (detail,))


root = tempfile.mkdtemp(prefix="sbcalls-")
stub, seen = os.path.join(root, "fake-claude"), os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys
open(%r, "a").write(json.dumps(sys.argv) + "\\n")
reply = os.environ.get("FAKE_REPLY", "")
if os.environ.get("FAKE_FAIL"):                 # the model failing for a moment, as the CLI says it
    sys.stderr.write("API Error: 529 Overloaded\\n")
    sys.exit(1)
print(json.dumps({"type": "result", "result": reply, "duration_ms": 900,
                  "modelUsage": {"claude-sonnet-x": {"inputTokens": 400, "outputTokens": 30}}}))
''' % seen)
os.chmod(stub, 0o755)
from agentpath import AGENT as agent  # noqa: E402
PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
CHAT = ("You: so we keep the cache at five minutes?\nClaude: Yes, five minutes, and we add a warm-up on deploy.\n"
        "You: and Theo writes the load test by Friday\nClaude: Noted.")


def decisions(text, reply):
    env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, FAKE_REPLY=reply)
    return subprocess.run([PY, agent, "decisions", "--session", "call-1"], input=text, env=env,
                          capture_output=True, text=True, timeout=60)


r = decisions(CHAT, "- Keep the Orion cache TTL at 5 minutes.\n\n2. Add a cache warm-up on deploy\n* Theo writes the load test by Friday\n- A fourth")
argv = json.loads(open(seen).read().splitlines()[-1])
check("decisions: three points, one a line, list marks gone", r.returncode == 0 and r.stdout.splitlines() == [
    "Keep the Orion cache TTL at 5 minutes.", "Add a cache warm-up on deploy", "Theo writes the load test by Friday"],
      (r.returncode, r.stdout, r.stderr[-300:]))
check("  one tool-less, throwaway call, never the agent's conversation",
      "--tools" in argv and argv[argv.index("--tools") + 1] == "" and "--no-session-persistence" in argv
      and "--resume" not in argv and "five minutes" in argv[argv.index("-p") + 1], argv)
check("  on the call's model for it (sonnet by default)", argv[argv.index("--model") + 1].endswith("sonnet") or
      "sonnet" in argv[argv.index("--model") + 1], argv[argv.index("--model") + 1])
usage = os.path.join(root, "agent", "usage.jsonl")
check("  counted in Usage as its own kind", os.path.exists(usage) and any(
    json.loads(l).get("kind") == "decisions" for l in open(usage)), usage)
GIST = "A quick sound check: the agent could hear you."
r = decisions("You: can you hear me\nClaude: Yes, loud and clear.\nYou: ok it was just a test", "GIST: " + GIST)
check("nothing decided: one gist line on what it was about, no points", r.returncode == 0
      and r.stdout.splitlines() == ["GIST: " + GIST], (r.stdout, r.stderr[-200:]))
argv = json.loads(open(seen).read().splitlines()[-1])
check("  asked for the gist when there are no decisions, and told to keep it short",
      "starts with GIST:" in argv[argv.index("-p") + 1] and "under 90 characters" in argv[argv.index("-p") + 1])
r = decisions("You: hi", "gist: A greeting")
check("  a very short call still gets a gist (no more skipping it), whatever the case of the marker",
      r.stdout.splitlines() == ["GIST: A greeting"], r.stdout)
r = decisions(CHAT, "- Keep the TTL at 5 minutes\nGIST: should not be shown with decisions")
check("  decisions win: no gist beside them", r.stdout.splitlines() == ["Keep the TTL at 5 minutes"], r.stdout)
r = decisions(CHAT, "None.")
check("  a model that says none and gives no gist: nothing, the Mac's side says so", r.returncode == 0 and r.stdout == "", r.stdout)
r = subprocess.run([PY, agent, "decisions", "--session", "call-1"], input=CHAT, capture_output=True, text=True,
                   timeout=60, env=dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, FAKE_FAIL="1"))
check("  the model failing: rc 1, and why on stderr, for the card's log",
      r.returncode == 1 and r.stdout == "" and "529 Overloaded" in r.stderr.strip().splitlines()[-1], (r.returncode, r.stderr))
n = len(open(seen).read().splitlines())
r = decisions("", "anything")
check("  no words at all: no call", r.returncode == 0 and r.stdout == "" and len(open(seen).read().splitlines()) == n)


# ---------------------------------------------------------------- the call, on the Mac's side
os.environ["STANDBY_ROOT"], os.environ["STANDBY_CLAUDE"] = root, stub
os.environ["STANDBY_NO_DEVICES"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import time  # noqa: E402
import ui  # noqa: E402
ui.DECISIONS_RETRY_S = 0
logged = []
ui.log = lambda line: logged.append(line)

AGENTS = {"a-planner": {"id": "a-planner", "name": "Planner", "session": "11111111-1111-4111-8111-111111111111",
                    "machine": "local", "folder": "/Users/you/code/orion"}}
events, delivered, talks, live = [], [], [], [None]
ui.MSG.update(agent=AGENTS.get, event=lambda e, d: events.append((e, d)),
              deliver=lambda a, text, session=None: delivered.append((a, text)) or True)
ui.start_sessions = lambda machine, folder: {"sessions": [
    {"id": "11111111-1111-4111-8111-111111111111", "title": "Planner", "open": {"where": "VS Code"}}]}
ui.live_session = lambda: live[0]
ui._live_now = lambda ttl=0.5: live[0]
n = [0]


def new_session(kind="talk"):
    n[0] += 1
    sid = "%s-20261015-10%02d" % (kind, n[0])
    d = ui.SESSIONS / sid
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"kind": kind, "started": time.time() - 240, "mic": True}))
    return sid


voices = []


def fake_talk(sid=None, wait=90, workspace=None, attach=None, copy=False, where=None, voice=None, model=None,
              voice_id=None):
    talks.append({"workspace": workspace, "attach": attach, "copy": copy, "where": where, "model": model,
                  "voice_id": voice_id})
    voices.append(voice)
    s = new_session()
    live[0] = s
    return {"id": s}


stopped = []


def fake_stop(detach=False):
    ending = live[0]
    stopped.append(ending)
    live[0] = None
    if ending:
        ui.call_ended(ending)
    return {"ok": True}


ui.start_talk, ui.stop_session = fake_talk, fake_stop


def start_call(agent_id):
    """An agent's call, as Standby once started one from its own window: a talk on a copy of the
    agent's conversation, its agent.json, the stream told. Calls start from Claude Code now; this
    fixture makes such a session so the end, the card, Keep or Forget and the voice are tested."""
    find = ui.MSG.get("agent")
    agent = find(agent_id) if find is not None and agent_id else None
    if not agent:
        return {"error": "No such agent"}
    live = ui.live_session()
    if live:
        return {"error": "You're on a call with %s" % (ui._call_info(ui.call_first(live)).get("name") or "an agent")
                if ui._call_info(live).get("agent") and ui.is_talk(live) else "A meeting is recording: stop it first",
                "busy": {"session": live, "agent": ui._call_info(live).get("agent")}}
    machine, folder, conv = agent.get("machine") or "local", agent.get("folder") or "", agent.get("session")
    found = ui.start_sessions(machine, folder)
    if found.get("error"):
        return found
    row = next((r for r in found["sessions"] if r["id"] == conv), None)
    if row is None:
        return {"error": "%s's conversation isn't in %s any more" % (agent.get("name") or "That agent", folder or "its folder")}
    copy = True                            # always a fork, made now: it knows all up to here, and is the user's to keep
    ui._msg_event("call", {"session": None, "agent": agent_id, "phase": "starting", "copy": copy, "started": time.time()})
    r = ui.start_talk(workspace=folder, attach=conv, copy=copy, where=machine, voice=None, model=ui.agent_model(agent_id),
                   voice_id=ui.agent_voice(agent_id))
    if r.get("error"):
        ui._msg_event("call", {"session": None, "agent": agent_id, "phase": "ended", "copy": copy, "started": None})
        return r
    sid = r["id"]
    started = ui.session_info(sid).get("started") or time.time()
    ui.write_json(ui.SESSIONS / sid / "agent.json", {"agent": agent_id, "name": agent.get("name") or "Claude", "copy": copy,
                                              "meeting_audio": False, "mic": True, "started": started})
    ui.log("call: %s with %s, on a copy of its conversation" % (sid, agent.get("name") or agent_id))
    ui._msg_event("call", {"session": sid, "agent": agent_id, "phase": "live", "copy": copy, "started": started})
    return {"session": sid, "copy": copy}


ui.ended_at = lambda sdir: time.time()

check("call: no such agent", start_call("a-nobody").get("error") == "No such agent")
real_model = ui.agent_model
ui.agent_model = lambda ident: "haiku"           # the agent's own choice goes with its talk
ui.agent_voice = lambda ident: "pocket:marius"
r = start_call("a-planner")
ui.agent_voice = lambda ident: None
ui.agent_model = lambda ident: None            # no choice from here on (the real one starts the timeline, which replaces the stand-in lookup)
sid = r.get("session")
check("call: a talk on a COPY of the agent's own conversation, in its folder, on its machine",
      sid and r.get("copy") is True and talks[-1] == {"workspace": "/Users/you/code/orion",
      "attach": "11111111-1111-4111-8111-111111111111", "copy": True, "where": "local", "model": "haiku", "voice_id": "pocket:marius"}, (r, talks))
info = json.loads((ui.SESSIONS / sid / "agent.json").read_text())
check("  agent.json: the agent, its name, a copy, the mic only", info["agent"] == "a-planner" and info["name"] == "Planner"
      and info["copy"] is True and info["mic"] is True and info["meeting_audio"] is False, info)
check("  the stream told: starting, then live with its session",
      [e[1]["phase"] for e in events if e[0] == "call"] == ["starting", "live"] and events[-1][1]["session"] == sid
      and events[-1][1]["copy"] is True, events)
st = ui.companion_state()
check("  the orb names the agent", (st["live"] or {}).get("call", {}).get("name") == "Planner"
      and st["live"]["call"]["copy"] is True, st.get("live"))
r2 = start_call("a-planner")
check("one call at a time: busy, with which", "on a call with Planner" in r2.get("error", "")
      and r2.get("busy") == {"session": sid, "agent": "a-planner"}, r2)

chat = [{"role": "me", "text": "so we keep the cache at five minutes?", "voice": True},
        {"role": "ai", "text": "Yes, five minutes, and a warm-up on deploy."}]
(ui.SESSIONS / sid / "chat.jsonl").write_text("".join(json.dumps(m) + "\n" for m in chat))
os.environ["FAKE_REPLY"] = "- Keep the cache TTL at 5 minutes\n- Add a warm-up on deploy"
events.clear()
r = ui.end_call(sid)
card_path = ui.SESSIONS / sid / "card.json"
t0 = time.time()
while not card_path.exists() and time.time() - t0 < 20:
    time.sleep(0.05)
card = json.loads(card_path.read_text()) if card_path.exists() else {}
check("end: the talk stops, the stream says ended", r == {"ok": True} and stopped[-1] == sid
      and any(e[0] == "call" and e[1]["phase"] == "ended" for e in events), (r, events))
check("  card.json: its points, when, about 4 min, a copy, memory pending, nothing sent to the agent",
      card.get("points") == ["Keep the cache TTL at 5 minutes", "Add a warm-up on deploy"]
      and 200 < card.get("duration_s", 0) < 300 and card.get("copy") is True and card.get("memory") == "pending"
      and card.get("delivered_at") is None and delivered == [], (card, delivered))
check("  the points were made from the call's words", "five minutes" in open(seen).read().splitlines()[-1])
check("end of a call that has ended: said so", "ended" in ui.end_call(sid).get("error", ""))

# Keep or forget: nothing reaches the agent until the user chooses.
r = ui.card_memory(sid, True)
check("keep: sent to the real agent once, as [CALL SUMMARY] with the card and the points",
      r.get("memory") == "kept" and r.get("delivered_at") and len(delivered) == 1 and delivered[0][0] == "a-planner"
      and delivered[0][1].startswith("[CALL SUMMARY] Call with the user (4 min)") and sid in delivered[0][1]
      and "- Keep the cache TTL at 5 minutes" in delivered[0][1] and "- Add a warm-up on deploy" in delivered[0][1], (r, delivered))
check("  the card says so, and the page is told", json.loads(card_path.read_text())["memory"] == "kept"
      and ("card", {"session": sid}) in events)
r = ui.card_memory(sid, True)
check("  a second Keep (a double click, a retry) sends nothing more", r.get("memory") == "kept" and len(delivered) == 1, (r, delivered))
r = ui.card_memory(sid, False)
check("  kept is final: Forget is refused", "It's in memory now" == r.get("error") and json.loads(card_path.read_text())["memory"] == "kept", r)

# A second call: forgotten, then kept after all.
r = start_call("a-planner")
sid2 = r["session"]
(ui.SESSIONS / sid2 / "chat.jsonl").write_text("".join(json.dumps(m) + "\n" for m in chat))
os.environ["FAKE_REPLY"] = "Ship it on Friday"
ui.end_call(sid2)
card_path2 = ui.SESSIONS / sid2 / "card.json"
t0 = time.time()
while not card_path2.exists() and time.time() - t0 < 20:
    time.sleep(0.05)
check("a call nobody chose about: pending, with no timeout, nothing sent",
      json.loads(card_path2.read_text())["memory"] == "pending" and len(delivered) == 1)
r = ui.card_memory(sid2, False)
check("forget: nothing is sent", r == {"memory": "forgotten"} and len(delivered) == 1, (r, delivered))
r = ui.card_memory(sid2, True)
check("  forgotten can still be kept: then it goes, once", r.get("memory") == "kept" and len(delivered) == 2
      and "Ship it on Friday" in delivered[1][1], (r, delivered))
check("a card that isn't there, or isn't a call's: said so", ui.card_memory("talk-20200101-0000", True).get("error")
      and ui.card_memory("../x", True).get("error") == "No such card")
queued_text = []
ui.MSG["deliver"] = lambda a, text, session=None: queued_text.append(text) and False       # the agents layer: kept for later
r = start_call("a-planner")
sid3 = r["session"]
(ui.SESSIONS / sid3 / "chat.jsonl").write_text("".join(json.dumps(m) + "\n" for m in chat))
os.environ["FAKE_REPLY"] = "Ship it"
ui.end_call(sid3)
card_path3 = ui.SESSIONS / sid3 / "card.json"
t0 = time.time()
while not card_path3.exists() and time.time() - t0 < 20:
    time.sleep(0.05)
r = ui.card_memory(sid3, True)
# The agents layer retries a held summary at app start and every 5 minutes until it is delivered.
check("keep while the agent can't be reached now: kept and queued with the agents layer, never twice",
      r.get("memory") == "kept" and r.get("queued") is True and not r.get("delivered_at")
      and ui.card_memory(sid3, True).get("queued") is True and len(queued_text) == 1, (r, queued_text))
ui.MSG["agent"] = lambda aid: None
sid4 = new_session()
(ui.SESSIONS / sid4 / "agent.json").write_text(json.dumps({"agent": "a-gone", "name": "Gone", "copy": True}))
(ui.SESSIONS / sid4 / "card.json").write_text(json.dumps({"points": ["x"], "duration_s": 60, "memory": "pending"}))
check("keep for an agent that was removed: said so, still pending", "isn't there any more" in ui.card_memory(sid4, True).get("error", "")
      and json.loads((ui.SESSIONS / sid4 / "card.json").read_text())["memory"] == "pending")
ui.MSG["agent"] = AGENTS.get
ui.MSG["deliver"] = lambda a, text, session=None: delivered.append((a, text)) or True

# A call with nothing decided still has something to keep: one line on what it was about.
def short_call(reply, chat_rows):
    r = start_call("a-planner")
    gid = r["session"]
    (ui.SESSIONS / gid / "chat.jsonl").write_text("".join(json.dumps(m) + "\n" for m in chat_rows))
    os.environ["FAKE_REPLY"] = reply
    ui.end_call(gid)
    path = ui.SESSIONS / gid / "card.json"
    t0 = time.time()
    while not path.exists() and time.time() - t0 < 20:
        time.sleep(0.05)
    return gid, json.loads(path.read_text()) if path.exists() else {}


test_chat = [{"role": "me", "text": "can you hear me", "voice": True}, {"role": "ai", "text": "Yes, loud and clear."},
             {"role": "me", "text": "ok it was just a test", "voice": True}]
gid, card = short_call("GIST: " + GIST, test_chat)
check("a short test call: no points, the gist instead, pending",
      card.get("points") == [] and card.get("gist") == GIST and card.get("memory") == "pending", card)
before = len(delivered)
r = ui.card_memory(gid, True)
check("  Keep sends the gist, so the agent hears what the call was",
      r.get("memory") == "kept" and len(delivered) == before + 1 and delivered[-1][1].startswith("[CALL SUMMARY] Call with the user")
      and delivered[-1][1].endswith("Gist: " + GIST) and "- " not in delivered[-1][1].split("\n", 1)[1], delivered[-1:])
gid, card = short_call("", [])
check("a call nobody spoke on: it says so, not nothing", card.get("points") == [] and card.get("gist") == "No one spoke on this call.", card)
gid, card = short_call("", test_chat)
check("a call the model gave nothing for: it says Standby couldn't, not nothing",
      card.get("points") == [] and card.get("gist") == "Standby couldn't summarize this call." and card.get("failed") is True, card)
asked = len(open(seen).read().splitlines())
os.environ["FAKE_FAIL"] = "1"
gid, card = short_call("GIST: " + GIST, test_chat)
os.environ.pop("FAKE_FAIL")
said = [x for x in logged if x.startswith("call: points for %s" % gid)]
check("  the model failing: asked twice, and why is logged (rc and its last line), each time",
      card.get("failed") is True and len(open(seen).read().splitlines()) == asked + 2 and len(said) == 2
      and all("rc 1" in x and "529 Overloaded" in x for x in said), (card, said))
r = ui.summarize_again({"session": gid})
now = json.loads((ui.SESSIONS / gid / "card.json").read_text())
check("  Summarize again: at once, the card is on its way (again), not failed",
      r == {"ok": True} and now.get("again") is True and not now.get("failed"), (r, now))
t0 = time.time()
while json.loads((ui.SESSIONS / gid / "card.json").read_text()).get("again") and time.time() - t0 < 20:
    time.sleep(0.05)
now = json.loads((ui.SESSIONS / gid / "card.json").read_text())
check("  then made anew from the same words: the gist, nothing failed, memory still the user's",
      now.get("gist") == GIST and not now.get("failed") and not now.get("again") and now.get("memory") == "pending", now)
check("  a card that didn't fail, or isn't there: nothing to make again",
      ui.summarize_again({"session": gid}).get("error") == "That summary is there already"
      and ui.summarize_again({"session": "../x"}).get("error") == "No such card")
gid, card = short_call("Ship it on Friday", test_chat)
check("a call with a decision: its points, and no gist", card.get("points") == ["Ship it on Friday"] and card.get("gist") is None, card)
ui.MSG["deliver"] = lambda a, text, session=None: delivered.append((a, text)) or True

check("  a meeting that never was a call: no card from the calls", ui.call_ended(new_session("meeting")) is None)
# A meeting's card is kept the same way: its text is the meeting's own.
msid = new_session("meeting")
(ui.SESSIONS / msid / "agent.json").write_text(json.dumps({"agent": "a-planner", "name": "Planner", "copy": True}))
(ui.SESSIONS / msid / "card.json").write_text(json.dumps({"title": "Weekly routing sync", "points": ["Cache stays at 5"],
                                                          "duration_s": 600, "memory": "pending"}))
before = len(delivered)
r = ui.card_memory(msid, True)
check("keep a meeting's card: its own [MEETING SUMMARY], once", r.get("memory") == "kept" and len(delivered) == before + 1
      and delivered[-1][1].startswith("[MEETING SUMMARY] Meeting: Weekly routing sync (10 min)")
      and "- Cache stays at 5" in delivered[-1][1], delivered[-1:])

gm = new_session("meeting")
(ui.SESSIONS / gm / "agent.json").write_text(json.dumps({"agent": "a-planner", "name": "Planner", "copy": True}))
(ui.SESSIONS / gm / "card.json").write_text(json.dumps({"title": "Standup", "points": [], "gist": "A standup: nothing new, everyone is on track",
                                                         "duration_s": 300, "memory": "pending"}))
before = len(delivered)
ui.card_memory(gm, True)
check("keep a meeting with nothing decided: its gist", delivered[-1][1].startswith("[MEETING SUMMARY] Meeting: Standup (5 min)")
      and delivered[-1][1].endswith("Gist: A standup: nothing new, everyone is on track"), delivered[-1:])

# The agent's voice, call after call: moved on at the end, and a forgotten call that later ones
# build on says the voice keeps it.
from pathlib import Path  # noqa: E402
ended_v, forgot = [], []
NOTE = "The voice still remembers this call: later calls build on it."
ui.MSG["voice_end"] = lambda a, call, v: ended_v.append((a, call, v))
ui.MSG["voice_forget"] = lambda a, call: forgot.append((a, call)) or NOTE
r = start_call("a-planner")
vsid = r["session"]
ran = "33333333-3333-4333-8333-333333333333"
(Path(root) / "agent").mkdir(parents=True, exist_ok=True)
(Path(root) / "agent" / ("%s.voice.uuid" % vsid)).write_text(ran + "\n")
(ui.SESSIONS / vsid / "chat.jsonl").write_text("".join(json.dumps(m) + "\n" for m in chat))
ui.end_call(vsid)
t0 = time.time()
while not ended_v and time.time() - t0 < 20:
    time.sleep(0.05)
check("  at its end, the agent's voice moves on to the one the call ran as", ended_v[-1:] == [("a-planner", vsid, ran)], ended_v)
vcard = ui.SESSIONS / vsid / "card.json"
t0 = time.time()
while not vcard.exists() and time.time() - t0 < 20:
    time.sleep(0.05)
r = ui.card_memory(vsid, False)
check("  forgotten: the voice is told; one later calls build on says the voice keeps it",
      forgot[-1:] == [("a-planner", vsid)] and json.loads(vcard.read_text()).get("memory_note") == NOTE, (forgot, r))
ui.voice_file("talk-20261016-1200", "local", {"from": None, "name": "Planner · voice", "brief": ""})
check("  the voice file lands where standby-agent reads it", json.loads(
      (Path(root) / "live" / "talk-20261016-1200.voice.json").read_text())["name"] == "Planner · voice")
check("  and what a voice ran as is read back; anything else is nothing",
      ui.voice_ran(vsid) == ran and ui.voice_ran("talk-20261016-1299") is None)
for k in ("voice_end", "voice_forget"):
    ui.MSG.pop(k, None)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
