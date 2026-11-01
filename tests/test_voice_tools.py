"""A call voice's actions as tools (config.json "voice": {"tools": true}): the engine serves them over
Claude Code's own pipe, as its SDK does, and a PostToolBatch hook ends the turn after them, so nothing is
said after a tool and no second model call is made, unless nothing was said before it. The turn's answer
is the marker text the engine already acts on, so the Mac sees no change. The prompt is the one prelude
with each action named by its tool. A stand-in Claude that speaks the protocol; no model."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, time
from importlib.machinery import SourceFileLoader

root = tempfile.mkdtemp(prefix="sbtoolstest-")
home = os.path.join(root, "home")
os.makedirs(os.path.join(home, ".claude"))
stub, log = os.path.join(root, "fake-claude"), os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys
log = %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "dontAsk")\\n--settings\\n--no-session-persistence')
    sys.exit(0)
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
def note(**k): open(log, "a").write(json.dumps(k) + "\\n")
pending, n = [], [0]
def read(fresh=False):
    if pending and not fresh:
        return pending.pop(0)
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    return json.loads(line)
def answer_host(m):
    note(host=m["request"])
    emit({"type": "control_response", "response": {"subtype": "success", "request_id": m["request_id"], "response": {}}})
def ask(sub, **req):
    n[0] += 1
    rid = "cli-%%d" %% n[0]
    emit({"type": "control_request", "request_id": rid, "request": dict(subtype=sub, **req)})
    while True:
        m = read(fresh=True)
        if m.get("type") == "control_response" and (m.get("response") or {}).get("request_id") == rid:
            return m["response"]
        if m.get("type") == "control_request":
            answer_host(m)
            continue
        pending.append(m)
def mcp(method, params=None, mid=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None: msg["params"] = params
    if mid is not None: msg["id"] = mid
    return (ask("mcp_message", server_name="voice", message=msg).get("response") or {}).get("mcp_response")
note(argv=sys.argv)
emit({"type": "system", "subtype": "init"})
if "--mcp-config" in sys.argv and not os.environ.get("FAKE_NO_MCP"):
    mcp("initialize", {"protocolVersion": "2025-06-18"}, 1)
    mcp("notifications/initialized")
    listed = mcp("tools/list", {}, 2)["result"]["tools"]
    note(handshake=[t["name"] for t in listed], upfront=all((t.get("_meta") or {}).get("anthropic/alwaysLoad") for t in listed))
def say(text):
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
while True:
    m = read()
    if m.get("type") == "control_request":
        answer_host(m)
        continue
    if m.get("type") != "user":
        continue
    last, error, plan = "", False, json.loads(os.environ.get("FAKE_PLAN", "[]"))
    k = 0
    while k < len(plan):
        if "say" in plan[k]:
            say(plan[k]["say"]); last = plan[k]["say"]
            k += 1
            continue
        batch = []                                  # tools one after another: one message, one batch
        while k < len(plan) and "tool" in plan[k]:
            batch.append(plan[k]); k += 1
        for step in batch:
            emit({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "mcp__voice__" + step["tool"]}}})
            raw = json.dumps(step.get("args") or {})       # its input as the API streams it: a few characters at a time
            for i in range(0, len(raw), 5):
                emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": raw[i:i + 5]}}})
            emit({"type": "stream_event", "event": {"type": "content_block_stop"}})
            emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__voice__" + step["tool"], "input": step.get("args") or {}}]}})
        for j, step in enumerate(batch):
            got = mcp("tools/call", {"name": step["tool"], "arguments": step.get("args") or {}}, 10 + k + j)
            note(result=got["result"]["content"][0]["text"])
        hook = ask("hook_callback", callback_id="voice-stop", input={"hook_event_name": "PostToolBatch"}).get("response") or {}
        note(hook=hook)
        if hook.get("continue") is False:
            error = bool(os.environ.get("FAKE_STOP_ERROR"))
            break
    emit({"type": "result", "is_error": error, "subtype": "error_during_execution" if error else "success", "result": "" if error else last})
''' % log)
os.chmod(stub, 0o755)
os.environ.update(STANDBY_ROOT=root, STANDBY_CLAUDE=stub, HOME=home, STANDBY_VOICE_TOOLS="1")
agent = SourceFileLoader("standby_agent", "plugin/bin/standby-agent").load_module()
agent.ensure_dirs()
fails = []


def check(label, ok, d=""):
    print("  %-78s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def seen():
    return [json.loads(l) for l in open(log)] if os.path.exists(log) else []


def turn(plan, **env):
    """One voice turn on the stand-in, as the engine asks it: (answer, rc, summary, deltas, the stub's notes)."""
    if os.path.exists(log):
        os.remove(log)
    os.environ["FAKE_PLAN"] = json.dumps(plan)
    for k in ("FAKE_NO_MCP", "FAKE_STOP_ERROR"):
        os.environ.pop(k, None)
    os.environ.update(env)
    t = agent.OneVoiceTurn("the voice's prompt", "haiku")
    deltas = []
    try:
        answer, rc = t.ask("They said: hello", 20, on_event=lambda k, x: deltas.append(x) if k == "delta" else None)
        summary = t.tools.summary() if t.tools is not None else None
    finally:
        t.stop()
    return answer, rc, summary, deltas, seen()


# ---------------------------------------------------------------- the prompt: one text, a tools variant of it
SID = "talk-20261031-100000"
os.makedirs(os.path.join(root, "live"), exist_ok=True)
json.dump({"handoff": True, "folder": "/Users/you/code/orion"}, open(os.path.join(root, "live", SID + ".voice.json"), "w"))
os.makedirs(os.path.join(root, "sessions", SID), exist_ok=True)
json.dump({"kind": "talk"}, open(os.path.join(root, "sessions", SID, "meta.json"), "w"))
source = agent.CALL_VOICE_PRELUDE + agent.CALL_VISUALS
missing = [m for m, _t in agent.TOOL_WORDING
           if not __import__("re").search(r"\s+".join(__import__("re").escape(w) for w in m.split()), source)]
check("every marker phrase in the table is in the prompt as written (a reworded one shows here)", not missing, missing)
for visuals in (False, True):
    agent.visuals_on = lambda v=visuals: v
    for ledger in (False, True):
        plain, tools = agent.voice_prelude(SID, ledger=ledger), agent.voice_prelude(SID, ledger=ledger, tools=True)
        left = [m for m in agent.ACTION_MARKERS if m in tools]
        check("tools variant (record %s, visuals %s): no action marker left" % (ledger, visuals), not left, (left, tools))
        check("  markers variant: the prompt exactly as it was, every action by its marker",
              plain == agent.voice_prelude(SID, ledger=ledger, tools=False) and "[DEEP]" in plain and "[QUIET]" in plain
              and "[END]" in plain and "[CANCEL #n]" in plain and (("[VISUAL:" in plain) == visuals))
tools = agent.voice_prelude(SID, tools=True)
check("  the layout rule keeps its meaning: words first, the deeper call never said",
      "say a few words to them, as deeper's say:" in " ".join(tools.split()) and "deeper's task is never said" in " ".join(tools.split()), tools)
check("  the task rule stays in the prompt, and in deeper's task description",
      "not how to do it" in tools and "not how to do it" in agent.VOICE_TOOLS[0]["inputSchema"]["properties"]["task"]["description"])
check("the tools: the same six, every time, loaded up front (never behind a tool search)",
      [t["name"] for t in agent.VOICE_TOOLS] == ["deeper", "stop_task", "end_call", "show", "bring_into_view", "stay_quiet"]
      and all(t["_meta"] == {"anthropic/alwaysLoad": True} for t in agent.VOICE_TOOLS)
      and json.loads(agent.VOICE_MCP)["mcpServers"]["voice"]["type"] == "sdk")

# ---------------------------------------------------------------- the command line
agent.TOOLS_ON = None
argv = agent.build_argv(SID, "haiku", True, "00000000-0000-4000-8000-000000000001", stream=True, kind="voice", turn=True)
sp = argv[argv.index("--append-system-prompt") + 1]
check("a call's voice with tools on: its server on the pipe, allowed, and the tools prompt",
      argv[argv.index("--mcp-config") + 1] == agent.VOICE_MCP and "mcp__voice" in argv and "--strict-mcp-config" in argv
      and "[DEEP]" not in sp and "call deeper with what they want" in sp, argv)
os.environ["STANDBY_VOICE_TOOLS"] = "0"
agent.TOOLS_ON = None
argv = agent.build_argv(SID, "haiku", True, "00000000-0000-4000-8000-000000000001", stream=True, kind="voice", turn=True)
check("  off (the default): no server, no tool allowed, the marker prompt as before",
      "--mcp-config" not in argv and "mcp__voice" not in argv and "[DEEP]" in argv[argv.index("--append-system-prompt") + 1])
os.environ["STANDBY_VOICE_TOOLS"] = "1"
agent.TOOLS_ON = None
argv = agent.build_argv("talk-20261031-100001", "haiku", True, "x", stream=True, kind="voice", turn=True)
check("  only a call from a Claude Code session has them (its voice.json says hand-off)", "--mcp-config" not in argv)

# ---------------------------------------------------------------- the turn
t = agent.OneVoiceTurn("p", "haiku")
t.start()
t0 = time.time()
while not any("handshake" in x for x in seen()) and time.time() - t0 < 5:
    time.sleep(0.02)
hs = next((x for x in seen() if "handshake" in x), {})
host = [x["host"] for x in seen() if "host" in x]
check("a spare sitting idle: the tools' handshake answered at once, before any turn",
      hs.get("handshake") == [x["name"] for x in agent.VOICE_TOOLS] and hs.get("upfront") is True, seen())
check("  the hook asked for at its start: PostToolBatch, as an SDK callback (it runs with hooks off)",
      any(h.get("subtype") == "initialize" and h["hooks"]["PostToolBatch"][0]["hookCallbackIds"] == ["voice-stop"]
          for h in host), host)
t.stop()

answer, rc, s, deltas, notes = turn([{"say": "Sure, I'll look into it."}, {"tool": "deeper", "args": {"task": "Find why the build fails"}}])
check("words, then deeper: the marker answer, words first and the task after [DEEP]",
      rc == 0 and answer == "Sure, I'll look into it. [DEEP] Find why the build fails", answer)
check("  the words streamed (they're said); the task never did", deltas == ["Sure, I'll look into it."], deltas)
check("  the hook ended the turn: no model call after the tool, nothing said after it",
      {"continue": False, "stopReason": "Done."} in [x["hook"] for x in notes if "hook" in x]
      and s["post_tool_words"] == "" and not s["continued"], (notes, s))
check("  measured: words before the tool, and when", s["tool"] == ["deeper"] and s["words_before_tool_ms"] is not None
      and s["tool_ms"] is not None and s["words_before_tool_ms"] <= s["tool_ms"], s)

answer, rc, s, deltas, notes = turn([{"tool": "deeper", "args": {"task": "Run the tests"}}, {"say": "On it, running the tests."}])
check("deeper with nothing said first: told so, and the turn goes on for their few words",
      notes[[i for i, x in enumerate(notes) if "result" in x][0]]["result"] == agent.TOOL_UNSAID
      and {} in [x["hook"] for x in notes if "hook" in x] and s["continued"] is True, notes)
check("  the words still come before the hand-off (it's acted on once the turn ends)",
      rc == 0 and answer == "On it, running the tests. [DEEP] Run the tests" and s["words_before_tool_ms"] is None
      and s["post_tool_words"] == "On it, running the tests.", (answer, s))

# The live replay: the model often calls deeper first and speaks after (7 of 16 hand-offs). Its words are an
# argument it writes first, spoken as they stream, so the order can't go wrong and no second call is made.
answer, rc, s, deltas, notes = turn([{"tool": "deeper", "args": {"say": "Sure, I\u2019ll check \"the build\" now.",
                                                                 "task": "Find why the build fails"}}])
check("deeper first, its say written first: spoken as it streams, the task never",
      "".join(deltas) == "Sure, I\u2019ll check \"the build\" now." and "Find" not in "".join(deltas), deltas)
check("  the turn ends with the tool: no second call, nothing after it, said in say",
      {"continue": False, "stopReason": "Done."} in [x["hook"] for x in notes if "hook" in x] and not s["continued"]
      and s["post_tool_words"] == "" and s["said_in"] == "say" and s["words_before_tool_ms"] is not None, (notes, s))
check("  the answer: its words, then the hand-off", rc == 0 and answer == "Sure, I\u2019ll check \"the build\" now. "
      "[DEEP] Find why the build fails", answer)
answer, rc, s, deltas, notes = turn([{"say": "On it."}, {"tool": "deeper", "args": {"say": "On it, checking.", "task": "Check it"}}])
check("  words in the reply and in say: the reply's are said, say isn't said twice",
      deltas == ["On it."] and answer == "On it. [DEEP] Check it" and s["said_in"] == "text", (deltas, answer, s))
answer, rc, s, deltas, notes = turn([{"tool": "end_call", "args": {"say": "Talk soon, bye!"}}])
check("end_call with its goodbye: said, then the end", "".join(deltas) == "Talk soon, bye!" and answer == "Talk soon, bye! [END]"
      and not s["continued"], (deltas, answer))
answer, rc, s, deltas, notes = turn([{"tool": "show", "args": {"say": "Here's the flow.", "what": "the build steps"}}])
check("show with its words (it was drawn in silence once): they're said, then it's drawn",
      "".join(deltas) == "Here's the flow." and answer == "Here's the flow. [VISUAL: the build steps]" and not s["continued"],
      (deltas, answer))
answer, rc, s, deltas, notes = turn([{"tool": "stop_task", "args": {"say": "Stopping that one.", "number": 2}},
                                     {"tool": "deeper", "args": {"say": "And starting the new one.", "task": "Use v2"}}])
check("  two tools, each with words: the first's are said, and the answer has only those",
      "".join(deltas) == "Stopping that one." and answer == "Stopping that one. [CANCEL #2] [DEEP] Use v2", (deltas, answer))
check("  every tool they'd notice has say, written first; stay_quiet has none",
      all(list(t["inputSchema"]["properties"])[0] == "say" and "say" in t["inputSchema"].get("required", [])
          for t in agent.VOICE_TOOLS if t["name"] != "stay_quiet")
      and not agent.VOICE_TOOLS[-1]["inputSchema"]["properties"])
ss = agent.SayStream()
got = [ss.feed(c) for c in ['{"task": "a \\"say\\": \\"x", "sa', 'y": "Hi \\', 'u00e9 the', 're\\', 'n!", "more": "no"}']]
check("say decoded as it streams: a key inside another string isn't it, escapes cut by the stream wait",
      "".join(got) == "Hi \u00e9 there !" and got[0] == "", got)
answer, rc, s, deltas, notes = turn([{"tool": "stay_quiet"}], FAKE_STOP_ERROR="1")
check("stay_quiet: [QUIET], ended at once; however Claude Code reports the hook's stop",
      rc == 0 and answer == "[QUIET]" and {"continue": False, "stopReason": "Done."} in [x["hook"] for x in notes if "hook" in x],
      (answer, rc, notes))
answer, rc, s, deltas, notes = turn([{"say": "Okay, stopping that one and showing the plan. Bye for now."},
                                     {"tool": "deeper", "args": {"task": "Use the new schema", "stop": [2]}},
                                     {"tool": "show", "args": {"what": "the plan [v2]"}}, {"tool": "end_call"}])
check("several: replaced task stopped, a visual, the end, and the hand-off last",
      answer == "Okay, stopping that one and showing the plan. Bye for now. [CANCEL #2] [VISUAL: the plan (v2)] [END] "
                "[DEEP] Use the new schema", answer)

answer, rc, s, deltas, notes = turn([{"say": "Sure. [DEEP] Look at the log"}], FAKE_NO_MCP="1")
check("a Claude Code that never asks for the tools: its markers still work, and markers from then on",
      rc == 0 and "[DEEP] Look at the log" in answer and agent.TOOLS_BROKEN, (answer, agent.TOOLS_BROKEN))
agent.TOOLS_BROKEN[:] = []

check("tools_answer: words alone stay words; quiet with words is just the words",
      agent.tools_answer("Hi there.", []) == "Hi there." and agent.tools_answer("Okay.", [("stay_quiet", {})]) == "Okay."
      and agent.tools_answer("", [("stop_task", {"number": 3}), ("bring_into_view", {})]) == "[CANCEL #3] [SHOW VISUAL]")

# ---------------------------------------------------------------- the replay harness's entry point
sysf, msgf = os.path.join(root, "sys.txt"), os.path.join(root, "msg.txt")
open(sysf, "w").write(agent.voice_prelude(SID, tools=True))
open(msgf, "w").write("They said: can you check the build")
os.remove(log)
env = dict(os.environ, FAKE_PLAN=json.dumps([{"say": "Checking it now."}, {"tool": "deeper", "args": {"task": "Check the build"}}]))
r = subprocess.run([sys.executable, "plugin/bin/standby-agent", "voice-turn", "--system", sysf, "--message", msgf,
                    "--model", "haiku", "--effort", "low"], capture_output=True, text=True, env=env, timeout=60)
try:
    out = json.loads(r.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    out = {}
argv = next((x["argv"] for x in seen() if "argv" in x), [])
check("voice-turn: one turn as the engine asks it, as JSON for the replay",
      r.returncode == 0 and out.get("answer") == "Checking it now. [DEEP] Check the build" and out.get("tool") == ["deeper"]
      and out.get("words_before_tool_ms") is not None and out.get("post_tool_words") == "" and out.get("continued") is False
      and "first_token_ms" in out, (r.returncode, r.stdout[-400:], r.stderr[-400:]))
os.remove(log)
env["FAKE_PLAN"] = json.dumps([{"say": "It passed this morning."}])
r2 = subprocess.run([sys.executable, "plugin/bin/standby-agent", "voice-turn", "--system", sysf, "--message", msgf,
                     "--model", "haiku"], capture_output=True, text=True, env=env, timeout=60)
try:
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    out2 = {}
check("  the first word's time in both: before a tool, and in a turn of words only (no tool)",
      isinstance(out.get("first_token_ms"), int) and isinstance(out2.get("first_token_ms"), int)
      and out2.get("answer") == "It passed this morning." and out2.get("tool") == []
      and out2.get("words_before_tool_ms") is None, (out, out2))
check("  with the tools, the hook's settings, its effort, and nothing saved",
      "--mcp-config" in argv and argv[argv.index("--effort") + 1] == "low" and "--no-session-persistence" in argv
      and json.loads(argv[argv.index("--settings") + 1])["disableAllHooks"] is True, argv)

print("\nFAILURES: %s" % fails if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
