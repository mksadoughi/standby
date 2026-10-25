"""A call from a Claude Code session knows what that session has been working on: about a page
(standby-agent cc-gist, one cheap tool-less call on its condensed transcript), made in the
background when the call starts and cached per session, never waited for. A cached one is in the
voice's brief from the start; a newer one comes with the voice's next turn, once.

The model is a stand-in here: what is checked is what it's given and what the voice is told."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label.strip())
        if detail != "":
            print("     %s" % (detail,))


def wait(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


root = tempfile.mkdtemp(prefix="sbgist-")
os.environ["STANDBY_ROOT"], os.environ["STANDBY_NO_DEVICES"] = root, "1"
stub, seen = os.path.join(root, "fake-claude"), os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys, time
open(%r, "a").write(json.dumps(sys.argv) + "\\n")
time.sleep(float(os.environ.get("FAKE_SLEEP") or 0))
print(json.dumps({"type": "result", "result": os.environ.get("FAKE_REPLY", ""), "duration_ms": 1200,
                  "modelUsage": {"claude-haiku-x": {"inputTokens": 5000, "outputTokens": 300}}}))
''' % seen)
os.chmod(stub, 0o755)
os.environ["STANDBY_CLAUDE"] = stub
from agentpath import AGENT as agent  # noqa: E402
PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


def rows_of(*rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


def user(text, **kw):
    return dict({"type": "user", "message": {"role": "user", "content": text}}, **kw)


def claude(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


TRANSCRIPT = os.path.join(root, "cc-session.jsonl")
open(TRANSCRIPT, "w").write(rows_of(
    {"type": "ai-title", "aiTitle": "Routing cache TTL"},
    user("The routing cache misses too often: make its TTL an hour and check the deploy hook invalidates it."),
    claude({"type": "text", "text": "I'll look at the cache config first."},
           {"type": "tool_use", "name": "Read", "input": {"file_path": "/work/orion/cache.py"}}),
    user([{"type": "tool_result", "tool_use_id": "t1", "content": "TTL = 300"}]),
    claude({"type": "tool_use", "name": "Edit", "input": {"file_path": "/work/orion/cache.py"}},
           {"type": "text", "text": "TTL is now 3600. The deploy hook doesn't invalidate yet."}),
    user("[Standby · meeting · 10:00-10:01]\nNadia: ship Friday", isMeta=True),
    user("a subagent's prompt", isSidechain=True),
    {"type": "system", "subtype": "away_summary", "content": "Raising the cache TTL; next, deploy invalidation."},
    user("Run the tests, then add invalidation to deploy.sh."),
))

# ---------------------------------------------------------------- what the model is given
loader = importlib.machinery.SourceFileLoader("sa_gist", agent)
sa = importlib.util.module_from_spec(importlib.util.spec_from_loader("sa_gist", loader))
loader.exec_module(sa)
rows = [json.loads(l) for l in open(TRANSCRIPT)]
text = sa.cc_condensed(rows)
check("condensed: its title, the user's requests, Claude's words and tools, and a recap",
      text.splitlines()[0] == "Title: Routing cache TTL" and "User: The routing cache misses too often" in text
      and "Claude used Read: /work/orion/cache.py" in text and "Claude: TTL is now 3600." in text
      and "Recap: Raising the cache TTL" in text and "User: Run the tests, then add invalidation" in text, text)
check("  not a meeting's notes, tool results or a subagent's rows",
      "Nadia" not in text and "TTL = 300" not in text and "subagent" not in text, text)
compacted = [user("Earlier: set up the Orion repo and its CI.", isCompactSummary=True, isMeta=True)] + rows
check("  a compacted session: the summary of its earlier part first",
      "Summary of the earlier part: Earlier: set up the Orion repo" in sa.cc_condensed(compacted))
long_rows = [user("First goal: make Orion fast.")] + [user("step %d %s" % (i, "x" * 300)) for i in range(400)] + [user("Latest: fix deploy.")]
short = sa.cc_condensed(long_rows, limit=6000)
check("  a long session: the first requests (the goal) and the newest part, within the limit",
      len(short) <= 6000 and "First goal: make Orion fast." in short and short.endswith("User: Latest: fix deploy."), len(short))

env = dict(os.environ, FAKE_REPLY="The user is raising the Orion routing cache's TTL to an hour.")
r = subprocess.run([PY, agent, "cc-gist", "--transcript", TRANSCRIPT], env=env, capture_output=True, text=True, timeout=60)
argv = json.loads(open(seen).read().splitlines()[-1])
check("cc-gist: the gist, printed", r.returncode == 0 and r.stdout.strip() == "The user is raising the Orion routing cache's TTL to an hour.",
      (r.returncode, r.stdout, r.stderr[-300:]))
check("  one tool-less, throwaway call on Haiku, for this same session on a call, up to 1000 words",
      argv[argv.index("--tools") + 1] == "" and "--no-session-persistence" in argv and "haiku" in argv[argv.index("--model") + 1]
      and "This same session, on a voice call, is about to talk with the user." in argv[argv.index("-p") + 1]
      and "at most 1000 words" in argv[argv.index("-p") + 1] and "User: Run the tests" in argv[argv.index("-p") + 1], argv)
usage = os.path.join(root, "agent", "usage.jsonl")
check("  counted in Usage as its own kind", os.path.exists(usage) and any(json.loads(l).get("kind") == "cc-gist" for l in open(usage)))
call_words = ("User: Let's keep the TTL at an hour, the PM agreed.\nVoice: Got it.\n"
              "Handed to Claude Code: Set the routing cache TTL to an hour.\nClaude Code came back: Done, it's 3600 now.")
r = subprocess.run([PY, agent, "call-summary", "--session", "talk-20261021-090000"], input=call_words,
                   env=dict(os.environ, FAKE_REPLY="The PM agreed the TTL stays at an hour."), capture_output=True, text=True, timeout=60)
argv = json.loads(open(seen).read().splitlines()[-1])
prompt = argv[argv.index("-p") + 1]
check("call-summary: the note for the session, printed", r.returncode == 0 and r.stdout.strip() == "The PM agreed the TTL stays at an hour.",
      (r.returncode, r.stdout, r.stderr[-300:]))
check("  one tool-less, throwaway call, told what the session already had, on the call's words",
      argv[argv.index("--tools") + 1] == "" and "--no-session-persistence" in argv
      and "only got the lines starting \"Handed to Claude Code:\"" in prompt and prompt.endswith(call_words), prompt[:400])
r = subprocess.run([PY, agent, "call-summary"], input=call_words, env=dict(os.environ, FAKE_REPLY="NONE"),
                   capture_output=True, text=True, timeout=60)
check("  nothing new for it: prints nothing", r.returncode == 0 and r.stdout.strip() == "", r.stdout)
r = subprocess.run([PY, agent, "decisions"], input="Voice ran: sleep 5 && echo done\nVoice: The five seconds are done.",
                   env=dict(os.environ, FAKE_REPLY="GIST: A five-second wait, run as a test."), capture_output=True, text=True, timeout=60)
argv = json.loads(open(seen).read().splitlines()[-1])
check("both summaries know the voice's own steps: \"Voice ran:\" told in the card's prompt and the call-summary's",
      '"Voice ran:" lines are steps the voice took itself' in argv[argv.index("-p") + 1]
      and '"Voice ran:" lines are steps the voice took itself' in prompt, argv[argv.index("-p") + 1][:600])
empty = os.path.join(root, "empty.jsonl")
open(empty, "w").write(rows_of(user("note", isMeta=True)))
n = len(open(seen).read().splitlines())
r = subprocess.run([PY, agent, "cc-gist", "--transcript", empty], env=env, capture_output=True, text=True, timeout=60)
check("  nothing to summarize: no model asked, nothing printed", r.stdout == "" and len(open(seen).read().splitlines()) == n)

# ---------------------------------------------------------------- what the voice is told
live = os.path.join(root, "live")
os.makedirs(live, exist_ok=True)
TALK = "talk-20261020-150000"
at = time.mktime((2026, 10, 20, 14, 5, 0, 0, 0, -1))
json.dump({"handoff": True, "folder": "/work/orion", "gist": "The user is raising the cache TTL.", "gist_at": at},
          open(os.path.join(live, TALK + ".voice.json"), "w"))
opening = sa.call_opening(TALK)
check("the call's first turn: its session, and the summary, as information, with when it was made",
      opening.startswith("Session: %s\n" % TALK)
      and "[SUMMARY] What this session has been working on (as of 14:05):\nThe user is raising the cache TTL.\n[END SUMMARY]" in opening
      and "[SUMMARY]" not in sa.voice_prelude(TALK), opening)          # its header says what it is
check("  and the system prompt carries neither: it's the same call after call",
      "[END SUMMARY]" not in sa.voice_prelude(TALK) and TALK not in sa.voice_prelude(TALK))
# Two calls from one folder, a different session and summary each: the system prompt is byte for byte the same,
# so Claude Code's prompt cache serves the second call's first turn warm.
TALK2 = "talk-20261020-163000"
json.dump({"handoff": True, "folder": "/work/orion", "gist": "Now the deploy hook invalidates the cache.", "gist_at": at + 5400},
          open(os.path.join(live, TALK2 + ".voice.json"), "w"))
sa.claude_caps(); sa._CAPS.update(settings=True)
a1 = sa.build_argv(TALK, "haiku", True, "00000000-0000-4000-8000-000000000001", stream=True, kind="voice")
a2 = sa.build_argv(TALK2, "haiku", True, "00000000-0000-4000-8000-000000000002", stream=True, kind="voice")
sys_of = lambda argv: argv[argv.index("--append-system-prompt") + 1]
check("two calls from one folder: the voice's system prompt is byte-identical", sys_of(a1).encode() == sys_of(a2).encode()
      and sa.call_opening(TALK) != sa.call_opening(TALK2), (len(sys_of(a1)), len(sys_of(a2))))
os.remove(os.path.join(live, TALK2 + ".voice.json"))
json.dump({"handoff": True, "gist": "zebra " * 1000}, open(os.path.join(live, TALK + ".voice.json"), "w"))
check("  a gist of 1000 words reaches the voice whole", sa.call_opening(TALK).count("zebra") == 1000)
json.dump({"handoff": True, "gist": "x" * 20000}, open(os.path.join(live, TALK + ".voice.json"), "w"))
check("  never more than that", sa.call_opening(TALK).count("x") <= sa.VOICE_GIST_CHARS)
json.dump({"handoff": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
check("  none made yet: no summary block", "[END SUMMARY]" not in sa.call_opening(TALK))

# ---------------------------------------------------------------- the app: cached, made in the background, given once
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
CC = "0f3c2a77-1111-4222-8333-944455556666"
ui._cc_transcript = lambda cc: __import__("pathlib").Path(TRANSCRIPT) if cc == CC else None
check("no gist yet: none, not current", ui.lite_gist(CC) == ("", None, False))
made = []
os.environ["FAKE_REPLY"] = "Gist one."
check("refresh: made in the background", ui.lite_gist_refresh(CC, then=made.append) is True)
check("  and not twice at once", ui.lite_gist_refresh(CC) is False)
check("  cached, current while the session hasn't moved on", wait(lambda: made == ["Gist one."], 10)
      and ui.lite_gist(CC)[0] == "Gist one." and ui.lite_gist(CC)[2] is True, (made, ui.lite_gist(CC)))
check("  current: nothing asked again", wait(lambda: not ui._GISTS_MAKING) and ui.lite_gist_refresh(CC) is False)
with open(TRANSCRIPT, "a") as fh:
    fh.write(rows_of(user("Now update the changelog.")))
check("the session moved on: the cached one is kept, but not current", ui.lite_gist(CC)[0] == "Gist one." and ui.lite_gist(CC)[2] is False)

talks, live_now, asked, answers = [], [None], [], []


def fake_talk(sid=None, wait=90, workspace=None, attach=None, copy=False, where=None, voice=None, model=None, **kw):
    talks.append(voice)
    s = "talk-20261020-15%02d" % len(talks)
    (ui.SESSIONS / s).mkdir(parents=True)
    (ui.SESSIONS / s / "meta.json").write_text(json.dumps({"kind": "talk", "started": time.time(), "mic": True}))
    live_now[0] = s
    return {"id": s}


ui.start_talk, ui.live_session = fake_talk, (lambda *a, **k: live_now[0])
ui.lite_publish = lambda cc, ev: None
ui._run_ask = lambda sid, q, voice=False, on_event=None, on_queued=None, heard=None: (ui.PENDING.pop(sid, None), asked.append(q), answers.pop(0))[2]
os.environ["FAKE_REPLY"] = "Gist two: the changelog is next."
t0 = time.time()
r = ui.LITE_POST["/api/lite/call"]({"cc": CC})
sid = r.get("session")
check("a call starts at once, never waiting for a gist", sid and time.time() - t0 < 0.5, time.time() - t0)
check("  its voice knows the cached gist from the start, with when it was made, and that it's from before the latest work",
      talks[-1].get("gist") == "Gist one." and isinstance(talks[-1].get("gist_at"), float) and talks[-1].get("gist_stale") is True, talks[-1])
check("  a newer one is made meanwhile", wait(lambda: (ui.LITE_CALLS[sid].get("fresh") or {}).get("text") == "Gist two: the changelog is next.", 10),
      ui.LITE_CALLS[sid])
answers += ["Sure.", "Okay."]
ui.ask_spoken(sid, "what were we doing?", None, None)
check("  and given with the voice's next turn, as information, once",
      asked[-1] == "[SUMMARY] What this session has been working on, now:\nGist two: the changelog is next.\n[END SUMMARY]\nwhat were we doing?",
      asked[-1])
ui.ask_spoken(sid, "thanks", None, None)
check("  not again", asked[-1] == "thanks", asked[-1])
mine = lambda: [r["text"] for r in ui.read_jsonl(ui.SESSIONS / sid / "chat.jsonl") if r.get("role") == "me"]
check("the user's line, as kept (the window, past transcripts, export, Copy): only their words, never the summary",
      mine() == ["what were we doing?", "thanks"], mine())
ui.LITE_CALLS[sid].update(gist="Gist three.")
t = ui._lite_task(ui.LITE_CALLS[sid], "handoff", "run the tests", "running")
t["doing"] = "Running the tests"
answers.append("Still going.")
ui.ask_spoken(sid, "how's it going?", None, None)
check("  with a summary and work in progress both: the voice gets both, before their words, the work as a task",
      asked[-1].startswith("[SUMMARY] What this session has been working on, now:\nGist three.\n[END SUMMARY]\n[TASKS]\n#1 running ")
      and "(Running the tests): run the tests\n[END TASKS]\nhow's it going?" in asked[-1], asked[-1])
check("  and their line is still only their words", mine()[-1] == "how's it going?", mine())
ui.LITE_CALLS.pop(sid, None)

# The hello: never a wait for a summary (the opening took 8-9 s for one). What's prepared is used at once,
# marked as from before the latest work if it is; the new one is made meanwhile and comes with a next turn, once.
voice_files = []
ui.voice_file = lambda s, where, voice: voice_files.append((s, dict(voice)))
with open(TRANSCRIPT, "a") as fh:
    fh.write(rows_of(user("Release notes done; now tag it.")))
os.environ["FAKE_REPLY"], os.environ["FAKE_SLEEP"] = "Gist five: tagging next.", "1"
wait(lambda: not ui._GISTS_MAKING)
live_now[0], n = None, len(voice_files)
sid = ui.LITE_POST["/api/lite/call"]({"cc": CC}).get("session")
check("a call while the cached gist is from before the latest work: it's marked so, and a new one is started",
      talks[-1].get("gist_stale") is True and CC in ui._GISTS_MAKING, (talks[-1], ui._GISTS_MAKING))
answers.append("Hi.")
t0 = time.time()
ui.ask_spoken(sid, ui.LITE_OPENING, None, None)
check("  the hello doesn't wait for it: at once, with the older one, marked old",
      time.time() - t0 < 0.5 and len(voice_files) == n and talks[-1].get("gist_stale") is True, (time.time() - t0, voice_files[n:]))
answers.append("Okay.")
wait(lambda: ui.lite_gist(CC)[0] == "Gist five: tagging next.", 10)
ui.ask_spoken(sid, "go on", None, None)
check("  the new one then comes with the next turn, once",
      asked[-1] == "[SUMMARY] What this session has been working on, now:\nGist five: tagging next.\n[END SUMMARY]\ngo on", asked[-1])
ui.LITE_CALLS.pop(sid, None)
json.dump({"handoff": True, "gist": "Gist one.", "gist_at": at, "gist_stale": True}, open(os.path.join(live, TALK + ".voice.json"), "w"))
check("the hello's summary from before the latest work says so, with when it was made",
      "[SUMMARY] What this session has been working on (as of 14:05, before its latest work, so some of it may be done):" in sa.call_opening(TALK),
      sa.call_opening(TALK))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
