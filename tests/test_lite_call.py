"""A call from a Claude Code session (Standby inside Claude Code): today's voice mode on a talk,
whose real work goes to that session through the mod. The talk, the voice and the model are
stand-ins; what is checked is the app's side: what goes on the session's stream (handoff,
abort, captions, state), what the voice is told and says, and the voice kept for next time."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, re, sys, tempfile, threading, time

import subprocess  # noqa: E402

root = tempfile.mkdtemp(prefix="sblitecall-")
os.environ["STANDBY_ROOT"], os.environ["STANDBY_NO_DEVICES"] = root, "1"
MAC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac")
sys.path.insert(0, MAC)
fails = []


def check(label, ok, detail=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label.strip())
        if detail != "":
            print("     %s" % (detail,))


# ---------------------------------------------------------------- calls are always there
# A fresh Standby with nothing in config.json: the call routes are there, so the mod offers Call.
probe = ("import sys; sys.path.insert(0, %r); import ui; "
         "print(sorted(p for p in ui.LITE_POST if 'call' in p or p in ('/api/lite/progress', '/api/lite/mute')))" % MAC)
plain = subprocess.run([sys.executable, "-c", probe], env=dict(os.environ, STANDBY_ROOT=tempfile.mkdtemp(prefix="sbnoswitch-")),
                       capture_output=True, text=True, timeout=120)
check("calls are there in any Standby, with no switch to turn on",
      plain.stdout.strip().splitlines()[-1:] == [str(sorted(["/api/lite/call", "/api/lite/call/end", "/api/lite/mute", "/api/lite/progress"]))],
      (plain.stdout[-300:], plain.stderr[-300:]))

import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
ui.LITE_DELIVER_WAIT = 3600             # hand-offs here are confirmed by the test, or not at all


def wait(pred, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


CC = "0f3c2a77-1111-4222-8333-944455556666"
out, talks, live, asked, answers = [], [], [None], [], []
ui.lite_publish = lambda cc, event: out.append((cc, dict(event)))
ui.live_session = lambda: live[0]
ui._live_now = lambda ttl=0.5: live[0]


class Voice(object):
    """The voice mode of the call: its status, Mute, and what it was given to say."""
    def __init__(self):
        self.st, self.told, self.muted, self.speaking_now = {"state": "listening", "you": "", "claude": ""}, [], False, False
    def status(self):
        return dict(self.st, muted=self.muted)
    def announce(self, text, dropped=None):
        self.told.append(text)
    def user_speaking(self):              # the recorder: in an utterance, before any of it is decoded
        return self.speaking_now
    def open_with(self, question):
        self.opening = question
    def mute(self, on):
        self.muted = bool(on)


def fake_talk(sid=None, wait=90, workspace=None, attach=None, copy=False, where=None, voice=None, model=None, **kw):
    talks.append({"workspace": workspace, "where": where, "voice": voice})
    s = "talk-20261017-10%02d" % len(talks)
    d = ui.SESSIONS / s
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"kind": "talk", "started": time.time(), "mic": True}))
    live[0] = s
    ui.VOICE[s] = Voice()
    return {"id": s}


ui.start_talk = fake_talk
real_begin_ask, real_run_ask = ui._begin_ask, ui._run_ask
begun = []
ui._begin_ask = lambda sid, q, voice=False, own=False: begun.append((q, own))
ui._run_ask = lambda sid, q, voice=False, on_event=None, on_queued=None, heard=None: (asked.append(q), answers.pop(0))[1]
chat = []
ui.append_chat = lambda *a, **k: chat.append((a, k))
real_voice_action = ui.voice_action
paths = []                                     # every pause and unpause, by session
ui.voice_action = lambda sid, action, src=None: (paths.append((sid, action)), setattr(ui.VOICE[sid], "muted", action == "mute"), {"ok": True})[2]
ran = {}
ui.voice_ran = lambda sid: ran.get(sid)
ended = []


def fake_end_call(sid):
    ended.append(sid)
    live[0] = None
    ui.lite_ended(sid)
    return {"ok": True}


ui.end_call = fake_end_call
work = tempfile.mkdtemp(prefix="sbcode-")


def kinds():
    return [e.get("call") for _cc, e in out]


# ---------------------------------------------------------------- starting
# The session's own transcript: what the user last typed to it, then the /standby call command, a note of Standby's
# and a tool's result (none of them theirs to pick up from).
CCT = os.path.join(work, "cc-transcript.jsonl")
with open(CCT, "w") as fh:
    for r in [{"type": "user", "message": {"role": "user", "content": "Fix the flaky login test first."}},
              {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
              {"type": "user", "message": {"role": "user", "content": "Can we talk this through on a call?"}},
              {"type": "user", "message": {"role": "user", "content": "<command-name>/standby</command-name><command-args>call</command-args>"}},
              {"type": "user", "isMeta": True, "message": {"role": "user", "content": "[Standby · meeting · 10:00-10:01]\nNadia: hi"}},
              {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}]:
        fh.write(json.dumps(r) + "\n")
ui._cc_transcript = lambda cc: __import__("pathlib").Path(CCT) if cc == CC else None
check("call: a Claude Code session id is needed", "error" in ui.LITE_POST["/api/lite/call"]({"cc": "../x"}))
r = ui.LITE_POST["/api/lite/call"]({"cc": CC, "cwd": work})
sid = r.get("session")
v = (talks[-1]["voice"] or {}) if talks else {}
check("call: voice mode on a talk, in the session's folder, on this Mac", sid and talks[-1]["workspace"] == work
      and talks[-1]["where"] == "local", (r, talks))
check("  with the voice's hand-off on, and no earlier voice yet", v.get("handoff") is True and v.get("from") is None
      and "Claude Code" in v.get("name", "") and v.get("folder") == work and not v.get("brief"), v)
check("  the voice speaks first: the call opens with its greeting turn (who it is: the agent's call prelude)",
      getattr(ui.VOICE[sid], "opening", None) == ui.LITE_OPENING, v)
answers.append("Hey, what are we working on?")
check("  that first turn goes to the voice as it is (what to pick up is in the session's summary), nothing handed on",
      ui.ask_spoken(sid, ui.LITE_OPENING, None, None) == "Hey, what are we working on?"
      and asked[-1] == ui.LITE_OPENING
      and not ui.lite_busy(sid), asked[-1:])
check("  and it's never shown as theirs: no row of it in the call's chat, nor the marker as a question being answered",
      not any("talk this through" in str(a) or ui.LITE_OPENING in str(a) for a, _k in chat)
      and begun[-1:] == [(ui.LITE_OPENING, True)], (chat, begun))
# The real _begin_ask for a turn of Claude's own: no row, and the window's "being answered" line has no question
_live, ui.live_session = ui.live_session, lambda: sid
_pend, ui.PENDING = ui.PENDING, {}
try:
    real_begin_ask(sid, ui.LITE_OPENING, voice=True, own=True)
    own_shown, own_row = ui.pending_shown(sid), ui.PENDING[sid].get("row")
    ui.PENDING.clear()
    real_begin_ask(sid, "what's next?", voice=True)
    theirs = ui.pending_shown(sid)
finally:
    ui.live_session, ui.PENDING = _live, _pend
check("  the greeting's turn: nothing drawn as asked while it's made ([CALL START] as their line, the VM run)",
      own_shown is None and own_row is None and not any(ui.LITE_OPENING in str(a) for a, _k in chat), (own_shown, chat[-2:]))
check("  their own question still is", theirs and theirs.get("question") == "what's next?", theirs)
answers.append("Sure.")
ui.ask_spoken(sid, "okay so where were we", None, None)
check("  given once: the next turn is only their words", asked[-1] == "okay so where were we", asked[-1:])
info = json.loads((ui.SESSIONS / sid / "agent.json").read_text())
check("  bound to the session, so its card follows", info.get("agent") == "lite:" + CC
      and info.get("kind") == "call" and info.get("copy") is True
      and info.get("mic") is True, info)
# A brand-new Claude Code session: its first call is in Standby's past meetings and calls at once,
# under its folder in the navigator, by that session.
ws = ui.messenger().workspaces()
rec = next((x for x in ws.get("agents", []) if x.get("session") == CC), None)
w = next((x for x in ws.get("workspaces", []) if rec and rec["id"] in (x.get("agents") or [])), None)
past = [c for c in ui.past_sessions(ui.messenger()) if c["session"] == sid]
check("  a brand-new session's first call: in the past list, by that session, under its folder in the navigator",
      rec is not None and w is not None and os.path.realpath(w["folder"]) == os.path.realpath(work)
      and past and past[0]["from"] == rec["id"] and past[0]["kind"] == "call", (rec, w, past))
check("  the stream says it's on: its state", wait(lambda: ("state" in kinds())), out)
check("  /api/lite/state shows the call", (ui.LITE_GET["/api/lite/state"]({"cc": CC}).get("live") or {}).get("kind") == "call",
      ui.LITE_GET["/api/lite/state"]({"cc": CC}))
check("asked again by the same session: the same call, said to be the one already on",
      ui.LITE_POST["/api/lite/call"]({"cc": CC}) == {"session": sid, "already": True})
check("  another session while it's on: busy", "busy" in ui.LITE_POST["/api/lite/call"]({"cc": "other-session-1"}))
was = live[0]; live[0] = sid
check("  no floating orb for it (the band in Claude Code says it): the companion sees nothing live",
      ui.CompanionHooks().live_kind() is None)
ui.LITE_ORB.add(sid)
check("    until it's sent to the orb (minimize to orb): then the orb carries it", ui.CompanionHooks().live_kind() == "talk")
ui.LITE_ORB.discard(sid); live[0] = was
answers.append("Hi.")
ui.ask_spoken(sid, ui.LITE_OPENING + " This is the test, one, two, three.", None, None)
check("  the greeting's hidden marker, carried onto the first words: never theirs, never shown",
      not str(asked[-1]).startswith(ui.LITE_OPENING) and "This is the test" in str(asked[-1])
      and not any(ui.LITE_OPENING in str(m.get("text")) for m in ui.read_jsonl(ui.SESSIONS / sid / "chat.jsonl")), asked[-1])

# ---------------------------------------------------------------- captions
ui.VOICE[sid].st = {"state": "speaking", "you": "what's in the README", "claude": "It's a meeting companion."}
check("captions: what you said and what the voice says, on the stream", wait(lambda: any(
    e.get("call") == "caption" and e["you"] == "what's in the README" and e["said"] == "It's a meeting companion."
    for _c, e in out)), out[-3:])
check("  and its state as it changes", wait(lambda: any(e.get("call") == "state" and e.get("phase") == "speaking" for _c, e in out)))
check("  each state saying whether work is going on deeper, for the band's \"Working on it\"",
      all("working" in e for _c, e in out if e.get("call") == "state"), [e for _c, e in out if e.get("call") == "state"][-2:])
ui.VOICE[sid].st = {"state": "listening", "you": "", "claude": ""}

# ---------------------------------------------------------------- what the voice answers itself
answers.append("Sure, that's in the README.")
check("an answer of the voice's own is spoken as it is",
      ui.ask_spoken(sid, "is it in the README", None, None) == "Sure, that's in the README." and asked[-1] == "is it in the README")
check("  nothing handed to the session", "handoff" not in kinds() and not ui.lite_busy(sid))

# ---------------------------------------------------------------- handing work on: each a task, as facts
# Every hand-off is a record: sent, queued, running, stalled, done, failed or stopped, made from what happened.
# The voice reads them as [TASKS] before their words (and sessions/<sid>/tasks.txt), and claims nothing else.
def handoff_of(words, task, received=True):
    """The voice hands `task` on for `words`; the session confirms it (as the mod does) unless received=False."""
    out.clear()
    answers.append("[HANDOFF] " + task)
    said = ui.ask_spoken(sid, words, None, None)
    ev = [e for _c, e in out if e.get("call") == "handoff"]
    if received and ev:
        ui.LITE_POST["/api/lite/progress"]({"session": sid, "id": ev[-1]["id"], "kind": "received"})
    return (ev[-1] if ev else {}), said


hand = lambda task: handoff_of(task, task)
tasks_txt = lambda: (ui.SESSIONS / sid / "tasks.txt").read_text() if (ui.SESSIONS / sid / "tasks.txt").exists() else ""
ev, said = handoff_of("run the tests and fix what fails", "run the tests and fix what fails", received=False)
check("real work: handed to the session with the voice's words, marked as from the call, the app saying nothing",
      ev.get("text") == "From your call: run the tests and fix what fails" and said == "" and all(cc == CC for cc, _e in out), (out, said))
check("  a task, sent, until the session says it has it", re.search(r"#1 sent since [\d:]{8}: run the tests and fix what fails", tasks_txt()), tasks_txt())
answers.append("Sending it now.")
ui.ask_spoken(sid, "did it start?", None, None)
check("  the voice's turn carries the tasks, before their words, as facts",
      re.match(r"\[TASKS\]\n#1 sent since [\d:]{8}: run the tests and fix what fails\n\[END TASKS\]\n", asked[-1]) and asked[-1].endswith("did it start?"), asked[-1])
ui.LITE_POST["/api/lite/progress"]({"session": sid, "id": ev["id"], "kind": "received"})
check("  the session has it: running", "#1 running" in tasks_txt() and ui.lite_busy(sid), tasks_txt())
ui.LITE_POST["/api/lite/progress"]({"session": sid, "turnId": "t1", "kind": "tool", "text": "Running the tests"})
answers.append("It's running the tests now.")
ui.ask_spoken(sid, "how's it going", None, None)
check("  its latest step, with when", re.search(r"#1 running since [\d:]{8}, last step [\d:]{8} \(Running the tests\): run the tests", asked[-1])
      and "[SESSION]" not in asked[-1], asked[-1])
check("  for the windows' busy cue: the voice's status lists it, short",
      [(t["id"], t["state"], t["label"]) for t in ui.voice_status(sid)["tasks"]] == [("1", "running", "run the tests and fix what fails")],
      ui.voice_status(sid).get("tasks"))

# "Stop" while the session works: its turn stopped at once, no model asked, the task stopped.
out.clear()
n = len(asked)
said = ui.ask_spoken(sid, "stop", None, None)
check("\"stop\" while it works: the session's turn is stopped, no model asked; the task says stopped",
      kinds() == ["abort"] and len(asked) == n and said == "Stopped it." and not ui.lite_busy(sid) and "#1 stopped" in tasks_txt(),
      (out, said, tasks_txt()))
answers.append("Okay.")
check("  \"stop\" with nothing running is just a turn", ui.ask_spoken(sid, "stop", None, None) == "Okay." and "abort" not in kinds()[1:])

# ---------------------------------------------------------------- what came back: told when both are quiet, worded then
import agentctl  # noqa: E402
told_model, replies = [], []


def fake_voice_ask(question, out=None, err=None, **kw):
    told_model.append(question)
    r = replies.pop(0)
    if isinstance(r, Exception):
        raise r
    return r


agentctl.ask = fake_voice_ask
vs = ui.VOICE[sid]
PATH = "/Users/you/Documents/projects/standby-try/standby-test.txt"
hand("Create a sample text file in the project folder.")
said_before, n_model = len(vs.told), len(told_model)
replies.append("Done! I made that empty test file in your project folder.\nSUGGEST: Should I fill it in?")
t0 = time.time()
ui.LITE_POST["/api/lite/progress"]({"session": sid, "turnId": "t3", "kind": "done", "text": "I created an empty file at %s." % PATH})
check("done: once both sides are quiet a moment, the voice is asked, as news, from the record",
      wait(lambda: len(told_model) > n_model) and time.time() - t0 >= ui.LITE_QUIET_GAP - 0.1
      and re.match(re.escape(ui.VOICE_TAG + "[RESULT done] #") + r"\d+ done at [\d:]{8}: Create a sample text file in the project folder\. → I created an empty file at",
                   told_model[-1]), told_model[n_model:])
check("  and what it says is said, in its words: no path, no follow-up chips",
      wait(lambda: len(vs.told) > said_before) and vs.told[-1] == "Done! I made that empty test file in your project folder.", vs.told[-1:])
came = [k for _a, k in chat if k.get("more", {}).get("result", "").startswith("I created an empty file")]
check("  in the call's log: what came back, in full", came and came[-1]["more"]["reason"] == "done", came[-1:])
answers.append("Yes.")
ui.ask_spoken(sid, "anything else?", None, None)
check("  told: the next turn's tasks show it ended, not as news again", "done" in asked[-1] and len(told_model) == n_model + 1, asked[-1])

# One step, then the session stops (talk-20261024-061950: the stars never built): its turn's end ends the task,
# with what it said; nothing claims it's still running.
ev, _s = hand("Add the star food, the score and the win at 20")
replies.append("It got the board done, but the stars and the score aren't in yet.")
n_model = len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "I added the board; next I'll add the stars."})
check("one step, then the session stops: the task is done with what it said, never shown as running",
      wait(lambda: len(told_model) > n_model) and "done" in told_model[-1] and "I added the board; next I'll add the stars." in told_model[-1]
      and not ui.lite_busy(sid) and "running" not in tasks_txt(), (told_model[n_model:], tasks_txt()))
# Mid-turn and quiet too long: stalled.
ev, _s = hand("render the film")
t = ui._lite_of(ui.LITE_CALLS[sid], ev["id"])
t["last"] -= ui.LITE_STALL + 1
ui._lite_watchdog(sid, ui.LITE_CALLS[sid])
check("running with no step for a while: stalled", t["state"] == "stalled" and re.search(r"stalled, no step since [\d:]{8}: render the film", tasks_txt()), tasks_txt())
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "tool", "text": "Encoding"})
check("  a step again: running", t["state"] == "running", t)
replies.append("The film's rendered.")
n_model = len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Rendered."})
wait(lambda: len(told_model) > n_model)
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))

# Turned away, or no word back at all: failed, told as news; the call's summary says it never got there.
ev, _s = handoff_of("remove the caption under the film", "remove the caption under the film", received=False)
replies.append("That one didn't reach the session. Shall I try again?")
n_model = len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "id": ev["id"], "kind": "undelivered", "text": "a Stop stands"})
check("a hand-off the session turned away: failed, and the voice is told it never got there",
      wait(lambda: len(told_model) > n_model) and told_model[-1].startswith(ui.VOICE_TAG + "[RESULT error] #")
      and "failed" in told_model[-1] and "remove the caption under the film → never reached the session" in told_model[-1], told_model[n_model:])
check("  and the log has it by its work, for the summary",
      wait(lambda: any(k.get("more") == {"result": "remove the caption under the film", "reason": "undelivered"} for _a, k in chat)))
ui.LITE_DELIVER_WAIT, wait_was = 0.3, ui.LITE_DELIVER_WAIT
replies.append("That didn't go through.")
n_model = len(told_model)
handoff_of("send the notes to the team", "send the notes to the team", received=False)
ui.LITE_DELIVER_WAIT = wait_was
check("  no word back at all: the same, after a while",
      wait(lambda: len(told_model) > n_model) and "send the notes to the team → never reached the session" in told_model[-1], told_model[n_model:])
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
cs = "talk-20990101-000000"
os.makedirs(str(ui.SESSIONS / cs), exist_ok=True)
with open(str(ui.SESSIONS / cs / "chat.jsonl"), "w") as fh:
    for r in ({"role": "ai", "text": "I'll do both.", "handoff": "fix the header"}, {"role": "ai", "text": "", "handoff": "remove the caption"},
              {"role": "ai", "text": "", "result": "remove the caption", "reason": "undelivered"}):
        fh.write(json.dumps(r) + "\n")
card = ui._card_text([cs], "Claude")
check("  the call's summary reads what really reached the session",
      "Handed to Claude Code: fix the header" in card and "Never reached Claude Code, so not done: remove the caption" in card
      and "Handed to Claude Code: remove the caption" not in card, card)

# A busy session queues it: queued (never lost while it waits), running when it starts.
ui.LITE_DELIVER_WAIT, wait_was = 0.3, ui.LITE_DELIVER_WAIT
ev, _s = handoff_of("wait ten seconds, then say so", "wait ten seconds, then say so", received=False)
ui.LITE_DELIVER_WAIT = wait_was
n_model = len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "id": ev["id"], "kind": "queued"})
time.sleep(0.5)
q = ui._lite_of(ui.LITE_CALLS[sid], ev["id"])
check("queued in a busy session: queued, never called lost, nothing said for it (bookkeeping)",
      q["state"] == "queued" and len(told_model) == n_model and ui.lite_busy(sid), (q, told_model[n_model:]))
ui.LITE_POST["/api/lite/progress"]({"session": sid, "id": ev["id"], "kind": "received"})
check("  it starts: running", q["state"] == "running")
replies.append("Ten seconds are up.")
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Ten seconds are up."})
wait(lambda: len(told_model) > n_model)
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))

# Work back while they talk: not told over them; their turn carries it (in [TASKS]) and it's never told again.
hand("build the panel")
n, n_model = len(vs.told), len(told_model)
vs.st = {"state": "listening", "you": "End the", "claude": ""}
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "It's being built."})
time.sleep(ui.LITE_QUIET_GAP + 0.3)
check("work back while they talk: not told on its own, nothing said over them", len(told_model) == n_model and len(vs.told) == n,
      (told_model[n_model:], vs.told[n:]))
answers.append("Before we hang up: it's being built. Talk soon!")
ui.ask_spoken(sid, "End the call.", None, None)
check("  their turn carries it, as a task that's done, before their words",
      re.search(r"#\d+ done at [\d:]{8}: build the panel → It's being built\.", asked[-1]) and asked[-1].endswith("End the call."), asked[-1:])
vs.st = {"state": "listening", "you": "", "claude": ""}
time.sleep(ui.LITE_QUIET_GAP + 0.3)
check("  and it's never told again on its own", len(told_model) == n_model and len(vs.told) == n, told_model[n_model:])

# Worded, but they spoke while it was: not said (the moment's gone); the next turn has it.
hand("check the logs")
n, n_model = len(vs.told), len(told_model)
real_ask = agentctl.ask
agentctl.ask = lambda q, **kw: (vs.st.update(you="wait"), fake_voice_ask(q, **kw))[1]
replies.append("The logs are clean.")
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "The logs are clean."})
check("worded as they began to speak: not said", wait(lambda: len(told_model) > n_model) and (time.sleep(0.3) or True)
      and len(vs.told) == n, vs.told[n:])
agentctl.ask = real_ask
vs.st = {"state": "listening", "you": "", "claude": ""}
answers.append("Clean, yes.")
ui.ask_spoken(sid, "and the logs?", None, None)
check("  their next turn carries it", "check the logs → The logs are clean." in asked[-1], asked[-1])

# The voice may choose silence: nothing said, and what came back is still in the log.
replies.append("[QUIET]")
hand("tidy the imports")
n, rows = len(vs.told), len(chat)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Tidied the imports in " + PATH})
check("the voice chooses silence: nothing is said, and what came back is still in the log",
      wait(lambda: len(chat) > rows) and (time.sleep(0.2) or True) and len(vs.told) == n
      and chat[-1][1].get("more", {}).get("result", "").startswith("Tidied"), (vs.told[n:], chat[rows:]))
answers.append("[QUIET]")
check("  a turn it answers with silence: nothing to say", ui.ask_spoken(sid, "mm, let me think", None, None) == "")
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
replies.append("All set, I created **%s** for you." % PATH)
hand("make another")
n = len(vs.told)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Created " + PATH})
check("  a path the model repeats anyway is said as the file's name only, no markdown",
      wait(lambda: len(vs.told) > n) and vs.told[-1] == "All set, I created standby-test.txt for you.", vs.told[-1:])
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
replies.append("Okay, Claude Code stopped before finishing that.")
hand("commit it")
n = len(vs.told)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "", "reason": "aborted"})
check("stopped from Claude Code: stopped, told so, said its way",
      wait(lambda: len(vs.told) > n) and told_model[-1].startswith(ui.VOICE_TAG + "[RESULT aborted]") and "stopped" in told_model[-1]
      and vs.told[-1] == "Okay, Claude Code stopped before finishing that.", (told_model[-1:], vs.told[-1:]))
for broken, how, key in ((OSError("the agent is gone"), "done", "done"), ("Could not reach the agent (OSError()). Try again", "error", "error")):
    wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
    replies.append(broken)
    hand("once more")
    n = len(vs.told)
    ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Wrote " + PATH, "reason": how})
    check("  the voice's model can't answer (%s): a short line of ours, never the session's words" % how,
          wait(lambda: len(vs.told) > n) and vs.told[-1] == ui.LITE_FALLBACK[key] and PATH not in vs.told[-1], vs.told[-1:])
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
n, n_model = len(vs.told), len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Late."})
time.sleep(ui.LITE_QUIET_GAP + 0.3)
check("  a done with nothing running says nothing, and asks nothing", len(vs.told) == n and len(told_model) == n_model)

# Cancel by voice: [CANCEL #n] stops a task; alone in its turn, the turn stops; with others, the session is told to drop it.
ev, _s = hand("make the snake gold")
tid = ui._lite_of(ui.LITE_CALLS[sid], ev["id"])["id"]
out.clear()
answers.append("Okay, I've stopped that. [CANCEL #%s]" % tid)
said = ui.ask_spoken(sid, "no, stop the gold one", None, None)
check("[CANCEL #n] alone in its turn: the session's turn stopped, the task stopped, the tag never said",
      kinds() == ["abort"] and ui._lite_of(ui.LITE_CALLS[sid], ev["id"])["state"] == "stopped" and said == "Okay, I've stopped that."
      and "CANCEL" not in str([r[0] for r in chat[-2:]]), (out, said))
ev1, _s = hand("write the release notes")
ev2, _s = hand("also update the changelog")
t1 = ui._lite_of(ui.LITE_CALLS[sid], ev1["id"])["id"]
out.clear()
answers.append("[HANDOFF] Update the changelog for 2.0 instead [CANCEL #%s]" % t1)
ui.ask_spoken(sid, "skip the release notes, do the changelog for 2.0", None, None)
sent_now = [e for _c, e in out if e.get("call") == "handoff"]
check("  replaced while another runs in the same turn: the session is told to drop it, and the new work goes",
      "abort" not in kinds() and any("stop it and drop it: write the release notes" in e["text"] for e in sent_now)
      and any(e["text"] == "From your call: Update the changelog for 2.0 instead" for e in sent_now)
      and ui._lite_of(ui.LITE_CALLS[sid], ev1["id"])["state"] == "stopped", sent_now)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "id": sent_now[-1]["id"], "kind": "received"})
replies.append("The changelog's done.")
n_model = len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Changelog updated."})
check("  a stopped task is never told back to the voice that stopped it", wait(lambda: len(told_model) > n_model)
      and "write the release notes" not in told_model[-1], told_model[-1:])
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))

# Typed or pasted in the session's chat: their words on their next turn, images and all; its answer, a task.
import base64  # noqa: E402
png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
cc_rows = os.path.join(tempfile.mkdtemp(prefix="sbcc-"), "cc.jsonl")
stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
open(cc_rows, "w").write(json.dumps({"type": "user", "timestamp": stamp, "origin": {"kind": "human"}, "message": {"role": "user", "content": [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}}, {"type": "text", "text": "what is this?"}]}}) + "\n")
real_tx, ui._cc_transcript = ui._cc_transcript, lambda cc: __import__("pathlib").Path(cc_rows)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "typed", "text": "what is this?", "images": 1})
check("typed during a call: a task of its own, labelled typed", "(typed) what is this?" in tasks_txt(), tasks_txt())
answers.append("That's the snake board.")
ui.ask_spoken(sid, "see what I pasted?", None, None)
img = sorted((ui.SESSIONS / sid).glob("pasted-*.png"))
check("  their next turn: what they typed as their words, and the image itself, once",
      "Typed in the chat: what is this?\n[IMAGE %s]\nsee what I pasted?" % (img[-1] if img else "?") in asked[-1]
      and img and img[-1].read_bytes() == base64.b64decode(png) and "(an image)" not in asked[-1], asked[-1])
ui._cc_transcript = real_tx
answers.append("Sure.")
ui.ask_spoken(sid, "ok", None, None)
check("    once", "Typed in the chat" not in asked[-1], asked[-1])
replies.append("It's the snake board, twenty cells across.")
n_model = len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "That's the snake game's board."})
check("  the session's answer to it: told as its task ended, a plain one included",
      wait(lambda: len(told_model) > n_model) and "(typed) what is this? → That's the snake game's board." in told_model[-1], told_model[n_model:])
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))

# Paused: what comes back waits in silence, with the bell; several are told as one on Resume, [RESUMED] first.
hand("build the film")
hand("make the cards")
ui.LITE_POST["/api/lite/mute"]({"session": sid, "muted": True})
n, n_model = len(vs.told), len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "turnId": "t7", "kind": "tool", "text": "Reading the film script"})
time.sleep(0.3)
check("paused, with background progress: no bell, nothing said", not ui.voice_status(sid).get("waiting") and len(told_model) == n_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "The film and the cards are done."})
check("  paused when work comes back: nothing said, the voice not asked, and the bell shows",
      wait(lambda: ui.voice_status(sid).get("waiting")) and (time.sleep(0.4) or True) and len(told_model) == n_model and len(vs.told) == n
      and ui.LITE_GET["/api/lite/state"]({"cc": CC})["live"].get("waiting") is True, ui.voice_status(sid))
check("  and the band hears it on the stream", wait(lambda: any(e.get("call") == "state" and e.get("waiting") for _c, e in out)))
call_live = ui.LITE_GET["/api/lite/state"]({"cc": CC})["live"]
check("a call's state: what macOS says of the mic, for the band, and never the meeting's sound",
      call_live.get("mic") == "allowed" and "meeting_audio" not in call_live, call_live)
replies.append("While you were away: the film and the cards are done.")
ui.LITE_POST["/api/lite/mute"]({"session": sid, "muted": False})
check("  Resume: one [RESUMED] turn with both, said once; the bell clears",
      wait(lambda: len(vs.told) > n) and told_model[-1].startswith(ui.VOICE_TAG + ui.LITE_RESUMED + "\n[RESULT done] #")
      and "build the film" in told_model[-1] and "make the cards" in told_model[-1] and len(told_model) == n_model + 1
      and not ui.voice_status(sid).get("waiting"), told_model[n_model:])
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
hand("wait fifteen seconds")
ui.LITE_POST["/api/lite/mute"]({"session": sid, "muted": True})
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "The wait is over."})
wait(lambda: ui.voice_status(sid).get("waiting"))
time.sleep(0.3)
vs.st = {"state": "listening", "you": "so", "claude": ""}
ui.LITE_POST["/api/lite/mute"]({"session": sid, "muted": False})
time.sleep(0.3)
answers.append("The wait's over.")
ui.ask_spoken(sid, "so is it done?", None, None)
vs.st = {"state": "listening", "you": "", "claude": ""}
check("  they speak first on Resume: their turn carries the task and the [RESUMED] line, once; the bell is gone",
      "wait fifteen seconds → The wait is over." in asked[-1] and ui.LITE_RESUMED + "\nso is it done?" in asked[-1]
      and not ui.voice_status(sid).get("waiting"), asked[-1])
answers.append("Yes.")
ui.ask_spoken(sid, "great", None, None)
check("    once", ui.LITE_RESUMED not in asked[-1], asked[-1])

# The mini window's own click, end to end: its page (mac/companion.html, in node with a stand-in DOM) on
# Standby's real server; the click pauses the call (the user's bell test: a click that did nothing left the
# call unpaused, so a result was said at once and no bell showed), the log says where it came from, a
# result that comes back is held, and the mini window shows the bell.
import shutil, socketserver  # noqa: E402
NODE = shutil.which("node") or os.path.expanduser("~/.local/share/mise/installs/node/22.21.1/bin/node")
srv = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac", "companion.html")).read()
page = page.replace("__SB_TOKEN__", ui.TOKEN)       # as the server serves it: with this launch's token
mini_js = os.path.join(tempfile.mkdtemp(prefix="sbmini-"), "mini.js")
with open(mini_js, "w") as fh:
    fh.write(r"""
const els = {};
const el = id => els[id] || (els[id] = { id, innerHTML: "", textContent: "", className: "", style: { setProperty() {} }, dataset: {},
  classList: { t: new Set(), add(c) { this.t.add(c) }, remove(c) { this.t.delete(c) }, toggle(c, on) { on ? this.t.add(c) : this.t.delete(c) }, contains(c) { return this.t.has(c) } },
  addEventListener() {}, getBoundingClientRect: () => ({ left: 0, bottom: 44, width: 44, height: 44 }), querySelector: () => null,
  querySelectorAll: () => [], closest: () => null, offsetWidth: 44, offsetHeight: 44, scrollWidth: 44 });
global.document = { getElementById: el, documentElement: el("html"), body: el("body"), addEventListener() {},
  querySelector: () => null, querySelectorAll: () => [] };
global.location = { hash: "" };
global.window = { webkit: { messageHandlers: { sb: { postMessage() {} } } } };
global.setTimeout = () => 0; global.clearTimeout = () => {};
const base = process.argv[2], realFetch = fetch;
global.fetch = (p, o) => realFetch(base + p, o);
""" + "eval(" + json.dumps(page[page.rindex("<script>") + 8:page.rindex("</script>")]) + " + `" + r"""
(async () => {
  await poll(true);
  if (process.argv[3] === "click") await orb();
  await poll(true);
  console.log(JSON.stringify({ live: C && C.live && C.live.id, voice: C && C.voice, cls: els.cmp.className }));
})();`);
""")
mini = lambda *a: json.loads(subprocess.run([NODE, mini_js, "http://127.0.0.1:%d" % srv.server_address[1]] + list(a),
                                            capture_output=True, text=True, timeout=30).stdout.strip().splitlines()[-1])
logged = []
real_log, ui.log = ui.log, lambda m: (logged.append(m), real_log(m))
stub_voice_action, ui.voice_action = ui.voice_action, real_voice_action      # the real path, end to end
seen = mini()
check("the mini window, on Standby's server, sees this call, not paused", seen["live"] == sid and not ui.call_muted(sid)
      and not seen["voice"].get("muted"), seen)
seen = mini("click")
check("  a click on the mini window pauses the call", ui.call_muted(sid) is True and seen["voice"].get("muted") is True, (ui.call_muted(sid), seen))
check("  and the log says so, and where it came from", ("lite: %s: paused (mini window)" % sid) in logged, logged[-5:])
hand("make the slides")
n, asked_model = len(ui.VOICE[sid].told), len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "The slides are done."})
check("  a result that comes back while paused is held: nothing said, and the call says something waits",
      wait(lambda: ui.voice_status(sid).get("waiting")) and (time.sleep(0.3) or True) and len(told_model) == asked_model
      and len(ui.VOICE[sid].told) == n, ui.voice_status(sid))
seen = mini()
check("  the mini window shows the bell (waiting)", "waiting" in seen["cls"].split() and seen["voice"].get("waiting") is True, seen)
replies.append("While you were away: the slides are done.")
seen = mini("click")
check("  a click again resumes, logged, and the voice says what waited",
      ui.call_muted(sid) is False and ("lite: %s: resumed (mini window)" % sid) in logged
      and wait(lambda: len(ui.VOICE[sid].told) > n) and "The slides are done." in told_model[-1], (seen, logged[-5:], told_model[asked_model:]))
ui.log, ui.voice_action = real_log, stub_voice_action
srv.shutdown()


# ---------------------------------------------------------------- replays of the traced calls (10/24)
# talk-20261024-061950, 06:21:54-06:23:44: "a maximum score of 20" handed on; the session edited game.js, then
# ended its turn with "Movement is in … the stars, the score and the win aren't in yet" (one step, then idle);
# its news was worded at once and played at 06:22:24, after two more turns of theirs, over their reply.
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
ev, _s = hand("The user wants a maximum score of 20 in the snake game in ~/Documents/projects/qa-playground/snake")
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "tool", "text": "editing game.js"})
n, n_model = len(vs.told), len(told_model)
vs.speaking_now = True                          # 06:21:58: they began to speak; nothing decoded yet
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Movement is in: the snake now steps one cell per "
                                    "tick and resets if it hits a wall or itself. The stars, the score and the win at 20 aren't in yet."})
time.sleep(ui.LITE_QUIET_GAP + 0.4)
check("replay 061950 · 06:22:01: work back while they've begun to speak (nothing decoded yet): not worded, not said",
      len(told_model) == n_model and len(vs.told) == n, told_model[n_model:])
vs.speaking_now = False
answers.append("Movement is in; the stars and the score aren't yet.")
ui.ask_spoken(sid, "How has your day been? What's the progress so far?", None, None)
check("  their turn carries it, as a task the session ended (done, with its own words), nothing claimed as running",
      re.search(r"#\d+ done at [\d:]{8}: The user wants a maximum score of 20 .* \u2192 Movement is in: the snake now steps", asked[-1])
      and "running" not in asked[-1].split("[END TASKS]")[0].split("maximum score")[1], asked[-1])
time.sleep(ui.LITE_QUIET_GAP + 0.4)
check("  never told again on its own (06:22:24's aside, and 06:23:44's stale \"is on it\", don't happen)",
      len(told_model) == n_model and len(vs.told) == n, (told_model[n_model:], vs.told[n:]))
answers.append("Not finished: the stars aren't in.")
ui.ask_spoken(sid, "Can you check where we are right now?", None, None)
check("  asked again later: the record still says done, with what came back; nothing says it's on it",
      "aren't in yet." in asked[-1] and "is on it" not in asked[-1] and "[SESSION]" not in asked[-1], asked[-1])

# On the call's record (the ledger): the agent places tasks.txt itself, as a [TASKS] entry when it changed, so
# lite_ask adds none; an ended task that entry carried is the voice's, never told again on its own.
import sbledger  # noqa: E402
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
vs.ledger = True
hand("tag the release")
n_model = len(told_model)
vs.st = {"state": "speaking", "you": "", "claude": "..."}       # a turn on: nothing told on its own meanwhile
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Tagged v2.0."})
tline = [t for t in ui.LITE_CALLS[sid]["tasks"].values() if t["what"] == "tag the release"][0]["line"]
sbledger.append(sid, "tasks", tasks_txt())                      # as the agent does, building that turn
answers.append("It's tagged.")
ui.ask_spoken(sid, "is it tagged?", None, None)
vs.st = {"state": "listening", "you": "", "claude": ""}
check("on the record: lite_ask adds no [TASKS] of its own (the agent places them)", "[TASKS]" not in asked[-1] and asked[-1].endswith("is it tagged?"), asked[-1])
time.sleep(ui.LITE_QUIET_GAP + 0.4)
check("  a task its [TASKS] entry carried is the voice's: never told again on its own",
      "Tagged v2.0." in tline and sbledger.carried(sid, tline) and len(told_model) == n_model, told_model[n_model:])
vs.ledger = False

# Once the voice has said goodbye ([END]), work that comes back is kept, not said.
wait(lambda: not ui.LITE_CALLS[sid].get("telling"))
hand("one more thing")
ui.LITE_CALLS[sid]["hanging_up"] = True
n, n_model = len(vs.told), len(told_model)
ui.LITE_POST["/api/lite/progress"]({"session": sid, "kind": "done", "text": "Done after the goodbye."})
time.sleep(ui.LITE_QUIET_GAP + 0.4)
check("after its goodbye: what came back is kept, not said or worded", len(vs.told) == n and len(told_model) == n_model
      and any(k.get("more", {}).get("result") == "Done after the goodbye." for _a, k in chat), vs.told[n:])
ui.LITE_CALLS[sid].pop("hanging_up", None)
check("progress for no call: said so", "error" in ui.LITE_POST["/api/lite/progress"]({"session": "talk-20200101-0000", "kind": "done"}))

# ---------------------------------------------------------------- the call's summary is made from
d = ui.SESSIONS / "talk-20261017-0900"
d.mkdir()
(d / "chat.jsonl").write_text("\n".join(json.dumps(r) for r in [
    {"role": "me", "text": "run the tests"},
    {"role": "ai", "text": "Let me check.", "handoff": "run the tests and fix what fails"},
    {"role": "ai", "text": "[HANDOFF] write the notes"},                      # an older call's
    {"role": "ai", "text": "Claude Code is done. 42 pass.", "result": "42 pass.\n\nFixed the flaky one.", "reason": "done"},
    {"role": "ai", "text": "Claude Code stopped.", "result": "", "reason": "aborted"}]) + "\n")
check("summary: the talk, each hand-off and what came back",
      ui._card_text(["talk-20261017-0900"], "Claude").splitlines() == [
          "You: run the tests", "Handed to Claude Code: run the tests and fix what fails",
          "Handed to Claude Code: write the notes", "Claude Code came back: 42 pass. Fixed the flaky one.",
          "Claude Code came back: Claude Code stopped."], ui._card_text(["talk-20261017-0900"], "Claude"))

# ---------------------------------------------------------------- mute, and the end
# Pause from each place goes to the one voice mute, which stops the voice mid-sentence (test_voice).
del paths[:]
orbed = []
ui.APP_WINDOW["api"] = type("FakeApi", (), {"to_orb": lambda self: (orbed.append(1), {"ok": True})[1]})()
check("the band's orb: the call into the orb, as the window's orb button",
      ui.LITE_POST["/api/lite/orb"]({"cc": CC}) == {"ok": True} and orbed == [1], orbed)
check("  only this session's call: none for another", "error" in ui.LITE_POST["/api/lite/orb"]({"cc": "other-session-1"}) and orbed == [1])
check("  offered where the app has it", ui.LITE_GET["/api/lite/state"]({"cc": CC})["can"].get("orb") is True)
ui.APP_WINDOW.pop("api", None)
ui.LITE_POST["/api/lite/mute"]({"session": sid, "muted": True})
check("mute: the call's mic, and state says so", ui.VOICE[sid].muted
      and ui.LITE_GET["/api/lite/state"]({"cc": CC})["live"]["muted"] is True)
check("  the band's Pause: the voice's mute", paths == [(sid, "mute")], paths)
ui.LITE_POST["/api/lite/mute"]({"session": sid, "muted": False})
del paths[:]
r = ui.mac_pause(sid)
past = [c for c in ui.past_sessions(ui.messenger()) if c["session"] == sid]
check("  the window's Pause on a call: the same voice mute (it said \"No such meeting\"), and its window shows it paused",
      r.get("ok") and paths == [(sid, "mute")] and ui.VOICE[sid].muted and past and past[0].get("paused") is True and past[0]["live"],
      (r, paths, past[:1]))
r = ui.mac_resume(sid)
check("    and its Resume: unmuted", r.get("ok") and paths[-1] == (sid, "unmute") and not ui.VOICE[sid].muted, (r, paths))


class Muted(object):
    def __init__(self):
        self.got, self.muted = [], False
    def mute(self, on):
        self.got.append(on)
    def status(self):
        return {"state": "muted"}


was_voice, ui.VOICE[sid] = ui.VOICE[sid], Muted()
real_voice_action(sid, "mute")
check("  the orb's click (/api/voice, action mute): the voice session's own mute", ui.VOICE[sid].got == [True], ui.VOICE[sid].got)
ui.VOICE[sid] = was_voice
ui.LITE_POST["/api/lite/mute"]({"session": sid, "muted": True})
ran[sid] = "7d1e2f30-aaaa-4bbb-8ccc-0123456789ab"
out.clear()
r = ui.LITE_POST["/api/lite/call/end"]({"session": sid})
check("end: as End does, and the stream says it ended", r == {"ok": True} and ended == [sid]
      and any(e.get("call") == "state" and e.get("phase") == "ended" for _c, e in out), (r, out))
check("  its voice is kept for this session's next call", json.loads(ui.LITE_VOICES.read_text()).get(CC) == ran[sid])
check("  and state shows nothing live", ui.LITE_GET["/api/lite/state"]({"cc": CC}).get("live") is None)
(ui.SESSIONS / sid / "card.json").write_text(json.dumps({"points": [], "gist": "A quick sound check: the agent could hear you.",
                                                          "duration_s": 60, "memory": "pending"}))
card = ui.LITE_GET["/api/lite/card"]({"session": sid})
check("  its card is served as a meeting's is, nothing to choose", card.get("gist", "").startswith("A quick sound check")
      and "memory" not in card, card)
check("  and says it's a call's (the band offers Add to Claude Code on a call's only)", card.get("kind") == "call", card)

# ---------------------------------------------------------------- after the call: Add to Claude Code
# What the call discussed that the session doesn't know yet, written by standby-agent call-summary (a stand-in
# here), goes into that Claude Code session, once.
(ui.SESSIONS / sid / "chat.jsonl").write_text("".join(json.dumps(m) + "\n" for m in [
    {"role": "me", "text": "Let's keep the cache TTL at an hour, the PM agreed."},
    {"role": "me", "text": "Wait five seconds for me, as a test."},
    {"role": "ai", "text": "The five seconds are done.", "steps": ['Running sleep 5 && echo "done"']},
    {"role": "ai", "text": "Got it, an hour it is."},
    {"role": "ai", "text": "Let me check.", "handoff": "Set the routing cache TTL to an hour."}]))
fed, said = [], {"out": "The user and the PM agreed the cache TTL stays at an hour.", "rc": 0}
real_cfg = ui.transport.for_config
class FakeT(object):
    def agent_cmd(self, args):
        fed.append(args)
        return [sys.executable, "-c", "import sys; open(%r, 'w').write(sys.stdin.read()); print(%r); sys.exit(%d)"
                % (os.path.join(work, "fed.txt"), said["out"], said["rc"])]
ui.transport.for_config = lambda: FakeT()
add = ui.LITE_POST["/api/lite/card/add"]
check("Add to Claude Code: not while that session isn't open", add({"session": sid}) == {"error": "That Claude Code session isn't open"} and not fed)
q = ui.lite().subscribe(CC)
said["rc"] = 1
check("  the summary couldn't be made: said so, nothing sent", add({"session": sid}) == {"error": "Couldn't summarize that call: try again"}
      and q.empty())
said.update(rc=0, out="")
check("  nothing new for it: said so, nothing sent", add({"session": sid}) == {"error": "There was nothing new for Claude Code in that call"}
      and q.empty())
said["out"] = "The user and the PM agreed the cache TTL stays at an hour."
r = add({"session": sid})
ev = q.get_nowait() if not q.empty() else {}
fedtext = open(os.path.join(work, "fed.txt")).read()
check("  added: the note goes to that session's stream, labelled, from this call", r == {"ok": True}
      and ev.get("call") == "summary" and ev.get("session") == sid
      and ev.get("text", "").startswith("[Standby · call summary · ") and ev["text"].endswith("\nThe user and the PM agreed the cache TTL stays at an hour."),
      (r, ev))
check("  made from the call's words: the user's, the voice's, and what went to Claude Code",
      fed[-1][:3] == ["call-summary", "--session", sid] and "User: Let's keep the cache TTL at an hour" in fedtext
      and "Voice: Got it, an hour it is." in fedtext and "Handed to Claude Code: Set the routing cache TTL to an hour." in fedtext, fedtext)
check("  with the steps the voice ran itself, before what it said (they happened: \"no wait was run\" was wrong)",
      'Voice ran: sleep 5 && echo "done"\nVoice: The five seconds are done.' in fedtext, fedtext)
n = len(fed)
check("  again (a double click): added once", add({"session": sid}) == {"ok": True, "already": True} and q.empty() and len(fed) == n)
ui.lite().unsubscribe(q)
ui.transport.for_config = real_cfg
check("  a meeting, or no card yet: not offered", "error" in add({"session": "meeting-20200101-000000"}))
check("end of a call that has ended: said so", "error" in ui.LITE_POST["/api/lite/call/end"]({"session": sid}))
r = ui.LITE_POST["/api/lite/call"]({"cc": CC, "cwd": work})
check("the next call: the same voice, carried on", talks[-1]["voice"]["from"] == ran[sid], talks[-1]["voice"])
ui.lite_ended(r["session"])

# ---------------------------------------------------------------- the voice ends the call
# Through the real ask (only the voice's model stands in): its goodbye is said, then the call ends as End ends it.
ui._begin_ask, ui._run_ask = real_begin_ask, real_run_ask
ui.claude_on = lambda s: True
live[0] = None
r = ui.LITE_POST["/api/lite/call"]({"cc": CC, "cwd": work})
sid = r["session"]
vs = ui.VOICE[sid]
replies.append("[HANDOFF] Write the release notes")       # work that keeps running in Claude Code
ui.ask_spoken(sid, "write the release notes", None, None)
out.clear(); chat.clear(); ended.clear()
# as the real voice shows it while thinking: the question it's answering under "you" (voice.py: _converse)
vs.st = {"state": "thinking", "you": "that's all for now, bye", "claude": ""}
replies.append("Okay, talk soon! [END]")
said = ui.ask_spoken(sid, "that's all for now, bye", None, None)
check("the voice hangs up: its goodbye is said, without the marker", said == "Okay, talk soon!", said)
check("  the marker isn't in the call's log", not any("[END]" in str(a) for a, _k in chat), chat)
time.sleep(0.4)
check("  the call isn't ended while the goodbye hasn't been said", ended == [] and sid in ui.LITE_CALLS)
vs.st = {"state": "speaking", "you": "", "claude": "Okay, talk soon!"}
time.sleep(0.3)
check("  nor while it's being said", ended == [])
vs.st = {"state": "listening", "you": "", "claude": ""}
check("  then it ends, as End ends it", wait(lambda: ended == [sid]) and sid not in ui.LITE_CALLS
      and any(e.get("call") == "state" and e.get("phase") == "ended" for _c, e in out), (ended, out[-2:]))
check("  and the work running in Claude Code isn't stopped", "abort" not in kinds(), kinds())

live[0] = None
r = ui.LITE_POST["/api/lite/call"]({"cc": CC, "cwd": work})
sid = r["session"]
vs = ui.VOICE[sid]
ended.clear()
vs.st = {"state": "speaking", "you": "", "claude": "Bye then!"}
replies.append("Bye then! [END]")
ui.ask_spoken(sid, "okay I think that's it", None, None)
time.sleep(0.2)
vs.st = {"state": "listening", "you": "wait, one more thing", "claude": ""}
time.sleep(1.0)
check("they go on talking before it hangs up: the call stays", ended == [] and sid in ui.LITE_CALLS, ended)
ui.lite_ended(sid)

# ---------------------------------------------------------------- Claude starting one itself: it rings on the band
evs = []
was_ev = ui.MSG.get("event")
ui.MSG["event"] = lambda ev, d: evs.append((ev, dict(d)))
inc, get, ans = ui.LITE_POST["/api/lite/incoming"], ui.LITE_GET["/api/lite/incoming"], ui.LITE_POST["/api/lite/incoming/answer"]
check("Claude asks to start a call: refused for no session or another kind", "error" in inc({"cc": "x", "kind": "call"})
      and "error" in inc({"cc": CC, "kind": "record"}))
check("  off by default: Always accept isn't on", ui.get_settings()["auto_accept"] is False)
rid = inc({"cc": CC, "kind": "call"}).get("id")
check("  raised: an id to wait on; no window, no panel in Standby (the band asks)", rid and not any(e == "incoming" for e, _d in evs), evs)
check("  no answer yet", get({"id": rid}) == {"answer": None})
held = {}
t = threading.Thread(target=lambda: held.update(got=get({"id": rid, "wait": "30"}), at=time.time()), daemon=True)
t0 = time.time(); t.start(); time.sleep(0.3)
check("  the plugin's wait is held in the app while the band asks", t.is_alive() and "got" not in held)
check("  Accept from the band: the answer is accept, and the wait returns at once", ans({"id": rid, "answer": "accept"}) == {"ok": True}
      and wait(lambda: "got" in held, 2) and held["got"] == {"answer": "accept"} and held["at"] - t0 < 1.5, held)
check("  answered twice: the second is too late", ans({"id": rid, "answer": "decline"}) == {"error": "That request has expired"})
rid = inc({"cc": CC, "kind": "listen"})["id"]
check("  Decline: nothing starts", ans({"id": rid, "answer": "decline"}) == {"ok": True} and get({"id": rid}) == {"answer": "decline"})
was_wait = ui.INCOMING_WAIT
ui.INCOMING_WAIT = 0.5
rid = inc({"cc": CC, "kind": "call"})["id"]
t0 = time.time()
got = get({"id": rid, "wait": "30"})
check("  no answer in time: the held wait ends at the expiry, declined, and says it expired", got == {"answer": "decline", "expired": True} and time.time() - t0 < 2
      and ans({"id": rid, "answer": "accept"}) == {"error": "That request has expired"}, got)
ui.INCOMING_WAIT = was_wait
rid = inc({"cc": CC, "kind": "call"})["id"]
check("  Always accept: this one accepted, and the setting saved", ans({"id": rid, "answer": "always"}) == {"ok": True}
      and get({"id": rid}) == {"answer": "accept"} and ui.get_settings()["auto_accept"] is True)
check("  then Claude's next one starts with no asking", inc({"cc": CC, "kind": "listen"}) == {"answer": "accept"})
check("  and Settings turns it off again: it asks again", ui.set_settings({"auto_accept": False}).get("ok")
      and ui.get_settings()["auto_accept"] is False and "id" in inc({"cc": CC, "kind": "call"}))
ui.MSG["event"] = was_ev

# The window shows only what was said: a reply cut off says what of it was heard; one never played, nothing
sid = "talk-20261025-0900"
(ui.SESSIONS / sid).mkdir(parents=True, exist_ok=True)
(ui.SESSIONS / sid / "meta.json").write_text(json.dumps({"session": sid, "kind": "talk", "started": time.time()}))
me_row = {"role": "me", "text": "tell me a joke", "at": 1.0, "voice": True}
ai_row = {"role": "ai", "text": "Here's one. A snake walks into a bar and orders a drink.", "at": 2.0, "voice": True}
(ui.SESSIONS / sid / "chat.jsonl").write_text("".join(json.dumps(r) + "\n" for r in (me_row, ai_row)))
ui.RECENT_VOICE[sid] = {"qid": "q-cut", "rows": [me_row, ai_row]}
ui._voice_heard(sid, "q-cut", "Here's one. A snake walks", True, False)
rows = [json.loads(x) for x in (ui.SESSIONS / sid / "chat.jsonl").read_text().splitlines()]
mine = [r for r in rows if r.get("at") == ai_row["at"] and r.get("role") == "ai"]
check("a reply cut off: its chat row says so, and what was heard (the window shows that)",
      mine and mine[0].get("cut") is True and mine[0].get("heard") == "Here's one. A snake walks"
      and mine[0]["text"].endswith("orders a drink."), mine)
ui._voice_heard(sid, "q-other", "", False, True)
check("  another turn's word changes nothing", [r for r in (json.loads(x) for x in (ui.SESSIONS / sid / "chat.jsonl")
      .read_text().splitlines()) if r.get("unsaid")] == [])
ui._voice_heard(sid, "q-cut", "", False, True)
rows = [json.loads(x) for x in (ui.SESSIONS / sid / "chat.jsonl").read_text().splitlines()]
check("  one never played: unsaid", any(r.get("at") == ai_row["at"] and r.get("unsaid") for r in rows))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
