"""Agents on the messenger (plugin/bin/sbagents.py, mac/agents.py): who holds a
conversation, what it's doing, and a message reaching it with one writer only.

A fake claude stands in for the real one: as the postman it records what it was asked
to hand over and answers as SendMessage does; resumed, it records the turn; started, it
writes the new conversation's transcript. Claude Code's registry and transcripts are
files in a throwaway HOME, written the way Claude Code writes them."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import base64, json, os, stat, subprocess, sys, tempfile, time
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="sbagents-home-"))
os.environ["HOME"] = str(home)                       # Claude Code's registry and transcripts: fakes
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "plugin" / "bin"))
sys.path.insert(0, str(REPO / "mac"))
import sbtimeline as tl  # noqa: E402
import sbagents as sa  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-78s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


def wait(pred, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


REG = home / ".claude" / "sessions"
PROJ = home / ".claude" / "projects" / "-Users-you-code-orion"
REG.mkdir(parents=True)
PROJ.mkdir(parents=True)
LOG = home / "fake-claude.jsonl"
ROOT = Path(os.environ["STANDBY_ROOT"])
FOLDER = home / "code" / "orion"
FOLDER.mkdir(parents=True)

# ---------------------------------------------------------------- a fake claude
FAKE = home / "fake-claude"
FAKE.write_text('''#!%s
import json, os, re, sys, time, uuid
argv, log = sys.argv[1:], os.environ["FAKE_LOG"]
def note(**kw):
    with open(log, "a") as fh:
        fh.write(json.dumps(dict(kw, argv=argv, cwd=os.getcwd(),
                 env_socket=os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET"))) + "\\n")
data = sys.stdin.read()
if "--tools" in argv:
    to = json.loads(re.search(r"with to set to (\\"[^\\"]*\\")", data).group(1))
    fence = re.search(r"between the two (=====\\w+) lines", data).group(1)
    body = data.split("\\n" + fence + "\\n", 1)[1].rsplit("\\n" + fence, 1)[0]
    note(kind="postman", to=to, body=body)
    sent = body + " (reworded)" if os.environ.get("FAKE_GARBLE") else body
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "SendMessage", "input": {"to": to, "message": sent}}]}}))
    if os.environ.get("FAKE_REFUSE"):
        res = {"success": False, "message": "no such session"}
    else:
        res = {"success": True, "message": "in that session's inbox", "msg_id": "mid-" + uuid.uuid4().hex[:6]}
    print(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
                      "tool_use_result": res}))
    print(json.dumps({"type": "result", "is_error": False, "result": "done"}))
elif "--resume" in argv:
    note(kind="resume", session=argv[argv.index("--resume") + 1], text=data)
    if "stream-json" in argv and os.environ.get("FAKE_WORDS"):
        ev = lambda e: print(json.dumps({"type": "stream_event", "event": e}), flush=True)
        ev({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        for w in os.environ["FAKE_WORDS"].split():
            ev({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": w + " "}})
            time.sleep(float(os.environ.get("FAKE_WORD_GAP", "0.05")))
        ev({"type": "content_block_stop", "index": 0})
    time.sleep(float(os.environ.get("FAKE_TURN", "0")))
    print(json.dumps({"type": "result", "result": "ok"}))
elif "--session-id" in argv:
    sid = argv[argv.index("--session-id") + 1]
    note(kind="start", session=sid, text=data)
    if os.environ.get("FAKE_START_FAIL"):
        sys.stderr.write("Not logged in. Run claude, then /login\\n"); sys.exit(1)
    d = os.path.join(os.environ["HOME"], ".claude", "projects", "-x")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, sid + ".jsonl"), "w").write("{}\\n")
    print(json.dumps({"type": "result", "result": "ready"}))
''' % sys.executable)
FAKE.chmod(FAKE.stat().st_mode | stat.S_IEXEC)
os.environ["FAKE_LOG"] = str(LOG)
os.environ["STANDBY_CLAUDE"] = str(FAKE)             # standby-agent's claude (sbclaude's override)
AGENT = str(REPO / "plugin" / "bin" / "standby-agent")


def calls(kind=None):
    if not LOG.exists():
        return []
    rows = [json.loads(x) for x in LOG.read_text().splitlines() if x.strip()]
    return [r for r in rows if kind is None or r["kind"] == kind]


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def register(pid, sid, status="idle", entrypoint="claude-vscode", name="orion-0", sock="/tmp/cc-socks/x.sock"):
    path = REG / ("%d-%s.json" % (pid, sid[:4]))
    path.write_text(json.dumps({"pid": pid, "sessionId": sid, "kind": "interactive", "entrypoint": entrypoint,
                                "status": status, "name": name, "messagingSocketPath": sock,
                                "peerProtocol": 1}))
    return path


S1 = "11111111-1111-4111-8111-111111111111"     # open in VS Code
S2 = "22222222-2222-4222-8222-222222222222"     # open nowhere
S3 = "33333333-3333-4333-8333-333333333333"     # open in a Terminal, busy

# ---------------------------------------------------------------- transcript records, as Claude Code writes them
nid = [0]


def uid():
    nid[0] += 1
    return "00000000-0000-4000-8000-%012d" % nid[0]


def ts(sec):
    return "2026-10-15T09:%02d:%02d.000Z" % (sec // 60, sec % 60)


def peer_turn(sec, name, body, msg_id):
    return {"type": "user", "uuid": uid(), "timestamp": ts(sec), "isMeta": True, "turnOrigin": "peer",
            "origin": {"kind": "peer", "from": "uds:/tmp/cc-socks/9.sock", "name": name, "msg_id": msg_id,
                       "body": body},
            "message": {"role": "user", "content": "Another Claude session sent a message: ..."}}


def queued(sec, name, body, msg_id):
    return {"type": "attachment", "uuid": uid(), "timestamp": ts(sec),
            "attachment": {"type": "queued_command", "prompt": "...",
                           "origin": {"kind": "peer", "name": name, "msg_id": msg_id, "body": body}}}


def said(sec, text, stop="end_turn"):
    return {"type": "assistant", "uuid": uid(), "timestamp": ts(sec),
            "message": {"role": "assistant", "stop_reason": stop, "content": [{"type": "text", "text": text}]}}


def tool(sec, name, inp):
    return {"type": "assistant", "uuid": uid(), "timestamp": ts(sec),
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": "tu" + uid()[-4:], "name": name, "input": inp}]}}


Q = "Is the cache fix merged?"
SUMMARY = "A call (4 min). Decided: ship it."
W = lambda words: tl.words_key(words)
TOK = {"a" * 32: {"session": S1, "words": W(Q), "used": None}, "b" * 32: {"session": S1, "words": W(Q), "used": None},
       "c" * 32: {"session": S1, "words": W("for S1 only"), "used": None},
       "e" * 32: {"session": S1, "words": W(SUMMARY), "used": None},
       "f" * 32: {"session": S1, "words": W(Q), "used": None}}   # the tokens Standby recorded, for S1


# ---------------------------------------------------------------- the registry
print("who holds a conversation: Claude Code's own registry")
me = os.getpid()
register(me, S1, "idle", "claude-vscode", sock="/tmp/cc-socks/101.sock")
register(dead_pid(), S2, "busy", "claude-vscode")                 # a window that crashed, file left behind
register(me, S3, "busy", "cli", name="orion-1", sock="/tmp/cc-socks/103.sock")
register(me, S2, "busy", "sdk-cli", name="Standby")              # a postman: never a holder
h = sa.holders(str(REG))
check("live windows only; a crashed one's file and Standby's postman don't count",
      set(h) == {S1, S3}, sorted(h))
check("where: VS Code, Terminal, and nothing for a claude -p",
      sa.open_in(h[S1]) == "vscode" and sa.open_in(h[S3]) == "terminal"
      and sa.open_in({"entrypoint": "sdk-cli"}) is None and sa.open_in(None) is None)
register(me, S1, "busy", "cli", sock="/tmp/cc-socks/104.sock")   # the same conversation, busy elsewhere
check("two for one conversation: the busy one", sa.holders(str(REG))[S1]["status"] == "busy")
(REG / ("%d-%s.json" % (me, S1[:4]))).unlink()
register(me, S1, "idle", "claude-vscode", sock="/tmp/cc-socks/101.sock")

# ---------------------------------------------------------------- the postman
print("held by a window: the postman hands it over, and nothing else writes the conversation")
check("its prompt fences the text, and names the receiver by its own address",
      '"uds:/tmp/cc-socks/101.sock"' in sa.postman_prompt("uds:/tmp/cc-socks/101.sock", "x\ny")
      and sa.postman_prompt("u", "body").count("=====") == 3)
text = 'Line one has "quotes", a `span` and a trailing space: \n  - indented\nEnd.'
got = sa.deliver(S1, "[MESSAGE] " + text + "\n\n", str(FOLDER), str(FAKE), AGENT, str(REG))
pm = calls("postman")
check("delivered to the idle VS Code window: sent, with the receiver's msg_id",
      got["delivery"] == "sent" and got["msg_id"].startswith("mid-") and got["open_in"] == "vscode", got)
check("  to that window's socket", pm[-1]["to"] == "uds:/tmp/cc-socks/101.sock", pm[-1])
import re  # noqa: E402
token = re.match(r"\[MESSAGE ([0-9a-f]{32})\]", pm[-1]["body"]).group(1)
check("  its one-time token recorded for that conversation and those words before it went",
      sa.tokens()[token]["session"] == S1 and sa.tokens()[token]["used"] is None
      and sa.tokens()[token]["words"] == tl.words_key(text), sa.tokens().get(token))
tp = ROOT / "agent" / "tokens.json"
check("  the table readable by its owner only (0600), in a folder that is too (0700)",
      stat.S_IMODE(tp.stat().st_mode) == 0o600 and stat.S_IMODE(tp.parent.stat().st_mode) == 0o700,
      (oct(tp.stat().st_mode), oct(tp.parent.stat().st_mode)))
sa.deliver(S1, "[CALL SUMMARY] " + SUMMARY, None, str(FAKE), AGENT, str(REG))
check("a summary to a window carries its token too",
      re.match(r"\[CALL SUMMARY [0-9a-f]{32}\] \(From Standby", calls("postman")[-1]["body"]), calls("postman")[-1]["body"])
check("  the text verbatim after the note (quotes, backticks, indents, a trailing space; final newlines dropped)",
      pm[-1]["body"] == sa.peer_body("[MESSAGE] " + text, token), pm[-1]["body"])
check("  the postman: Haiku, named Standby, SendMessage its only tool",
      all(x in pm[-1]["argv"] for x in ("haiku", "Standby", "SendMessage"))
      and pm[-1]["argv"][pm[-1]["argv"].index("--tools") + 1] == "SendMessage", pm[-1]["argv"])
check("  run in Standby's own folder, not the user's project", pm[-1]["cwd"].endswith("agent/postman"), pm[-1]["cwd"])
check("  never resumed by Standby: one writer", not calls("resume"))
os.environ["CLAUDE_CODE_MESSAGING_SOCKET"] = "/tmp/cc-socks/parent.sock"   # run from inside a Claude Code
sa.deliver(S1, "[MESSAGE] again", None, str(FAKE), AGENT, str(REG))
check("  started from inside a Claude Code, it doesn't borrow that one's messaging identity",
      calls("postman")[-1]["env_socket"] is None, calls("postman")[-1])
os.environ.pop("CLAUDE_CODE_MESSAGING_SOCKET")
got = sa.deliver(S3, "[MESSAGE] when you're free", None, str(FAKE), AGENT, str(REG))
check("the Terminal one is busy: queued (it reads it when its turn ends)", got["delivery"] == "queued"
      and got["msg_id"] and calls("postman")[-1]["to"] == "uds:/tmp/cc-socks/103.sock", got)
os.environ["FAKE_REFUSE"] = "1"
got = sa.deliver(S1, "[MESSAGE] x", None, str(FAKE), AGENT, str(REG))
os.environ.pop("FAKE_REFUSE")
check("SendMessage refused: failed, in its words", got["delivery"] == "failed" and "no such session" in got["error"], got)
check("the postman's answer read: no SendMessage at all is a failure",
      sa.parse_postman('{"type":"result","is_error":false,"result":"done"}')[2] == "the message wasn't handed over")
os.environ["FAKE_GARBLE"] = "1"
got = sa.deliver(S1, "[MESSAGE] exact words", None, str(FAKE), AGENT, str(REG))
os.environ.pop("FAKE_GARBLE")
check("a postman that reworded it: noted in the log (the receiver's copy is what it is)",
      "differs from the one given" in (ROOT / "agent" / "agents.log").read_text(), got)
got = sa.deliver(S1, "   \n", None, str(FAKE), AGENT, str(REG))
check("nothing to send: failed, and no postman", got["delivery"] == "failed")

# ---------------------------------------------------------------- the outbox
print("held by nobody: Standby runs the turn, one at a time")
transcript = PROJ / (S2 + ".jsonl")
transcript.write_text(json.dumps({"type": "user", "permissionMode": "acceptEdits", "message": {"content": "hi"}}) + "\n")
os.environ["FAKE_TURN"] = "1.5"
n0 = len(calls("resume"))
first = sa.deliver(S2, "[MESSAGE] first", str(FOLDER), str(FAKE), AGENT, str(REG))
check("not open anywhere: sent, Standby runs it", first == {"delivery": "sent"}, first)
check("  its turn starts", wait(lambda: len(calls("resume")) == n0 + 1), calls("resume"))
second = sa.deliver(S2, "[MESSAGE] second", str(FOLDER), str(FAKE), AGENT, str(REG))
check("  a second while the first runs: queued", second == {"delivery": "queued"}, second)
check("  busy meanwhile", sa.outbox_busy(S2) is True)
check("  both run, in order, never two at once",
      wait(lambda: len(calls("resume")) == n0 + 2) and [c["text"] for c in calls("resume")[n0:]]
      == ["[MESSAGE] first", "[MESSAGE] second"], calls("resume")[n0:])
r1 = calls("resume")[n0]
check("  as the user's turn, tagged (no note: it's not another session's message)", r1["text"] == "[MESSAGE] first")
check("  resumed in the workspace's folder, in the mode the conversation was left in",
      os.path.realpath(r1["cwd"]) == os.path.realpath(FOLDER) and r1["argv"][r1["argv"].index("--permission-mode") + 1] == "acceptEdits"
      and r1["argv"][r1["argv"].index("--resume") + 1] == S2, r1)
check("  the outbox empties, and it's idle again", wait(lambda: not sa.outbox_busy(S2)))
# the agent's own model, for a turn Standby runs itself
os.environ["FAKE_TURN"] = "0"
check("a turn with no choice of model asks for none: its conversation's own", "--model" not in r1["argv"], r1["argv"])
n1 = len(calls("resume"))
sa.deliver(S2, "[MESSAGE] on haiku", str(FOLDER), str(FAKE), AGENT, str(REG), model="haiku")
check("a chosen model goes with the turn Standby runs (--model)", wait(lambda: len(calls("resume")) == n1 + 1)
      and calls("resume")[n1]["argv"][calls("resume")[n1]["argv"].index("--model") + 1] == "haiku", calls("resume")[n1:])
wait(lambda: not sa.outbox_busy(S2))
sa.deliver(S2, "[MESSAGE] on a full id", str(FOLDER), str(FAKE), AGENT, str(REG), model="claude-sonnet-5-5")
check("  a full model id too", wait(lambda: len(calls("resume")) == n1 + 2)
      and calls("resume")[n1 + 1]["argv"][calls("resume")[n1 + 1]["argv"].index("--model") + 1] == "claude-sonnet-5-5")
wait(lambda: not sa.outbox_busy(S2))
sa.deliver(S2, "[MESSAGE] not a model", str(FOLDER), str(FAKE), AGENT, str(REG), model="haiku; rm -rf ~")
check("  anything else is dropped, never put on a command line", wait(lambda: len(calls("resume")) == n1 + 3)
      and "--model" not in calls("resume")[n1 + 2]["argv"] and "rm -rf" not in json.dumps(calls("resume")[n1 + 2]["argv"]),
      calls("resume")[n1 + 2]["argv"])
wait(lambda: not sa.outbox_busy(S2))
p = subprocess.run([sys.executable, AGENT, "deliver", "--id", S2, "--text", base64.b64encode(b"[MESSAGE] over the command").decode(),
                    "--model", "opus"], capture_output=True, text=True, timeout=60, env=dict(os.environ))
check("  and standby-agent deliver takes --model", p.returncode == 0 and wait(lambda: len(calls("resume")) == n1 + 4)
      and calls("resume")[n1 + 3]["argv"][calls("resume")[n1 + 3]["argv"].index("--model") + 1] == "opus", (p.stdout, p.stderr))
wait(lambda: not sa.outbox_busy(S2))
# a turn Standby runs: its words as they are written, then nothing left behind
os.environ["FAKE_TURN"], os.environ["FAKE_WORDS"], os.environ["FAKE_WORD_GAP"] = "1.5", "Merged and the load test passes", "0.3"
n2 = len(calls("resume"))
sa.deliver(S2, "[MESSAGE] is it merged?", str(FOLDER), str(FAKE), AGENT, str(REG))
seen = []
wait(lambda: (seen.append(sa.live_draft(S2)) or False) or len(calls("resume")) > n2 and sa.live_draft(S2)
     and len(sa.live_draft(S2).split()) >= 3, 15)
partial = sa.live_draft(S2)
check("a turn Standby runs streams: the block being written is there before the turn ends, a few words in",
      partial and partial.startswith("Merged and the") and sa.outbox_busy(S2), (partial, seen[-3:]))
r_argv = calls("resume")[n2]["argv"]
check("  it runs with Claude Code's partial messages (stream-json)",
      r_argv[r_argv.index("--output-format") + 1] == "stream-json" and "--include-partial-messages" in r_argv, r_argv)
wait(lambda: not sa.outbox_busy(S2), 20)
check("  and when the turn is over, nothing is left behind", sa.live_draft(S2) is None
      and not os.path.exists(sa._draft_path(S2)))
sa.write_draft(S2, "old words")
check("a draft nobody writes to any more isn't live", sa.live_draft(S2, now=time.time() + sa.DRAFT_STALE + 1) is None)
sa.clear_draft(S2)
os.environ.pop("FAKE_WORDS"); os.environ["FAKE_TURN"] = "0"

transcript.write_text(json.dumps({"type": "user", "permissionMode": "bypassPermissions",
                                  "message": {"content": "hi"}}) + "\n")
check("bypassPermissions is never carried over (it needs the user's own flag)", sa.last_mode(S2) is None)
os.environ["FAKE_TURN"] = "0"

print("  a window opens it while a message waits: that window gets it, not a second writer")
lock = sa._lock(S2)                                    # as if a turn of Standby's were running
sa.enqueue(S2, "[MESSAGE] while you were away", str(FOLDER))
reg2 = register(me, S2, "idle", "cli", name="orion-2", sock="/tmp/cc-socks/105.sock")
lock.close()
n0, p0 = len(calls("resume")), len(calls("postman"))
subprocess.run([sys.executable, AGENT, "deliver-run", "--id", S2], timeout=60)
check("  handed to the window by the postman, and not resumed",
      len(calls("resume")) == n0 and len(calls("postman")) == p0 + 1
      and calls("postman")[-1]["to"] == "uds:/tmp/cc-socks/105.sock", calls("postman")[-1:])
reg2.unlink()

print("  a registry Standby can't read: it never guesses a conversation is free")
check("health: readable now", sa.health(str(REG))[0] is True, sa.health(str(REG)))
odd = REG / "odd.json"
odd.write_text(json.dumps({"pid": me, "session": S2, "status": "idle"}))      # a format Standby doesn't know
n0 = len(calls("resume"))
got = sa.deliver(S2, "[MESSAGE] x", str(FOLDER), str(FAKE), AGENT, str(REG))
check("  an open Claude Code that doesn't say what it holds: not run, said plainly",
      got["delivery"] == "failed" and "can't tell whether this agent is open elsewhere" in got["error"]
      and not sa.outbox_busy(S2), got)
time.sleep(0.5)
check("  and no turn ran", len(calls("resume")) == n0)
check("  health says so, for standby check", sa.health(str(REG))[0] is False and "update Standby" in sa.health(str(REG))[1])
odd.unlink()
check("health: no registry at all is fine (no Claude Code open)", sa.health(str(home / "none")) == (True, "no Claude Code open now"))

print("through standby-agent, as the Mac (or an SSH host) asks it")
b64 = lambda s: base64.b64encode(s.encode()).decode()
out = subprocess.run([sys.executable, AGENT, "deliver", "--id", S1, "--text", b64("[MESSAGE] über ✓")],
                     capture_output=True, text=True, timeout=60)
ans = json.loads(out.stdout.strip().splitlines()[-1])
check("deliver: JSON, and any text survives (base64)", ans["delivery"] == "sent"
      and calls("postman")[-1]["body"].endswith("über ✓"), (ans, out.stderr[-300:]))
out = subprocess.run([sys.executable, AGENT, "deliver", "--id", "not-a-uuid", "--text", b64("x")],
                     capture_output=True, text=True, timeout=60)
check("  a bad id: refused", json.loads(out.stdout.strip().splitlines()[-1])["delivery"] == "failed")

# ---------------------------------------------------------------- the Mac side
print("the Mac side: a summary to the agent")
import messenger as mm  # noqa: E402
import agents  # noqa: E402


class FakeUI(object):
    MSG = {}
    MSG_POST = {}

    def __init__(self):
        self.asked, self.answer = [], {"delivery": "sent", "msg_id": "mid-77", "open_in": "vscode"}

    def _agent_json(self, args, timeout=20, machine=None):
        self.asked.append((machine, args))
        return self.answer

    def machine_name(self, machine):
        return "This Mac" if machine == "local" else machine


ui = FakeUI()
m = mm.Messenger(Path(tempfile.mkdtemp(prefix="sbagents-ws-")), ui=ui)
m.attach()
clock = [1000.0]
OUTBOX = Path(tempfile.mkdtemp(prefix="sbagents-outbox-")) / "agents" / "outbox.jsonl"
ag = agents.Agents(ui, m, clock=lambda: clock[0], outbox=OUTBOX)
ag.install(drain=False)
check("hooks in place: deliver (a summary), and the voice's",
      ui.MSG["deliver"] == ag.deliver and ui.MSG["voice_end"] == ag.voice_end and "voice_start" not in ui.MSG and "start_agent" not in ui.MSG
      and "/api/msg/send" not in ui.MSG_POST)
w = m.add_workspace({"machine": "local", "folder": str(FOLDER), "agents": [{"session": S1}]})["workspace"]
aid = w["agents"][0]
q = m.hub.subscribe()
ui.answer = {"delivery": "sent"}
check("deliver (a call's summary): True once it's with the agent",
      ag.deliver(aid, "[CALL SUMMARY] x") is True and ag.deliver("a-none", "x") is False)
ui.answer = {"delivery": "failed", "error": "x"}
check("  False when it isn't", ag.deliver(aid, "[CALL SUMMARY] x") is False)
kept = [json.loads(x) for x in OUTBOX.read_text().splitlines()]
check("  and kept in the outbox, to go later", kept == [{"to": aid, "kind": "summary", "text": "[CALL SUMMARY] x",
                                                         "at": clock[0]}], kept)

print("  summaries Add your Mac kept before delivery existed: each goes once, none lost")
OUTBOX.write_text("\n".join(json.dumps(x) for x in [
    {"to": aid, "kind": "meeting_card", "text": "[MEETING SUMMARY] Weekly routing sync (12 min)", "at": 1},
    {"to": "a-gone", "kind": "meeting_card", "text": "[MEETING SUMMARY] for an agent removed since", "at": 2},
    {"to": aid, "kind": "meeting_card", "text": "[MEETING SUMMARY] will fail", "at": 3}]) + "\nnot json\n")
answers = {"[MEETING SUMMARY] Weekly routing sync (12 min)": {"delivery": "sent", "msg_id": "mid-s1"},
           "[MEETING SUMMARY] will fail": {"delivery": "failed", "error": "unreachable"},
           "[MEETING SUMMARY] written meanwhile": {"delivery": "sent"}}
real = ui._agent_json


def answering(args, timeout=20, machine=None):
    text = base64.b64decode(args[args.index("--text") + 1]).decode()
    ui.asked.append((machine, args))
    if text.startswith("[MEETING SUMMARY] Weekly"):        # a meeting ends while the outbox drains
        with open(str(OUTBOX), "a") as fh:
            fh.write(json.dumps({"to": aid, "kind": "meeting_card", "text": "[MEETING SUMMARY] written meanwhile",
                                 "at": 4}) + "\n")
    return answers[text]


ui._agent_json = answering
n0 = len(ui.asked)
sent = ag.drain()
texts = [base64.b64decode(a[a.index("--text") + 1]).decode() for _, a in ui.asked[n0:]]
left = [json.loads(x) for x in OUTBOX.read_text().splitlines()]
check("  delivered once each; one for an agent no longer here isn't tried", sent == 1 and texts ==
      ["[MEETING SUMMARY] Weekly routing sync (12 min)", "[MEETING SUMMARY] will fail"], texts)
check("  the one that failed stays, counted; the one written meanwhile is kept too",
      sorted(x["text"] for x in left) == ["[MEETING SUMMARY] will fail", "[MEETING SUMMARY] written meanwhile"]
      and next(x for x in left if "fail" in x["text"])["tries"] == 1, left)
check("  nothing left aside", not OUTBOX.with_name("outbox.jsonl.draining").exists())
answers["[MEETING SUMMARY] will fail"] = {"delivery": "queued"}
n0 = len(ui.asked)
check("  next time: the rest go, and the outbox is empty", ag.drain() == 2 and not OUTBOX.exists()
      and len(ui.asked) - n0 == 2)
check("  an outbox that isn't there: nothing to do", ag.drain() == 0)
ui._agent_json = real

print("  a summary held for later tells its card when it goes, or why it can't")
told = []
ui.MSG["on_delivered"] = lambda a, which: told.append(("delivered", a, which))
ui.MSG["on_dropped"] = lambda a, which, why: told.append(("dropped", a, which, why))
for f in (OUTBOX, OUTBOX.with_name("outbox.jsonl.draining")):
    if f.exists():
        f.unlink()
ag.held = set()
ui.answer = {"delivery": "failed", "error": "Couldn't reach This Mac"}
check("deliver with the card's session: False, and kept with it",
      ag.deliver(aid, "[CALL SUMMARY] Call (4 min)", "orion-20261016-0900") is False
      and json.loads(OUTBOX.read_text().splitlines()[-1])["session"] == "orion-20261016-0900" and aid in ag.held)
with open(str(OUTBOX), "a") as fh:            # one Add your Mac kept before delivery existed: no session
    fh.write(json.dumps({"to": aid, "kind": "meeting_card", "text": "[MEETING SUMMARY] Sync", "at": 1}) + "\n")
ui.answer = {"delivery": "sent"}
ag.drain()
check("  once it goes: the card is told, by its session, or by its text when it had none",
      told == [("delivered", aid, "orion-20261016-0900"), ("delivered", aid, "[MEETING SUMMARY] Sync")]
      and aid not in ag.held, told)
told.clear()
with open(str(OUTBOX), "a") as fh:
    fh.write(json.dumps({"to": "a-left", "kind": "summary", "text": "[CALL SUMMARY] x", "session": "orion-20261016-0910",
                         "at": 1}) + "\n")
ag.drain()
check("  its agent left Standby: dropped, and the card told why",
      told == [("dropped", "a-left", "orion-20261016-0910", "Its agent left Standby before it could be sent")]
      and not OUTBOX.exists(), told)

# ---------------------------------------------------------------- an agent's voice, call to call
print("an agent's voice: one conversation, carried from call to call")
VSESS = "99999999-9999-4999-8999-999999999999"
wsv = m.add_workspace({"machine": "local", "folder": str(FOLDER), "agents": [{"session": VSESS, "name": "Theo"}]})
vid = wsv["agents"][0]["id"]
check("its hooks are in place", ui.MSG["voice_end"] == ag.voice_end and ui.MSG["voice_forget"] == ag.voice_forget)
V1, V2, V3 = ("%s-1111-4111-8111-111111111111" % (c * 8) for c in "abc")
clock[0] = 50050.0                                    # the call ends after all of the above
ag.voice_end(vid, "talk-20261016-0901", V1)
v = m.store.agent(vid)["voice"]
check("the call ends: its voice becomes the agent's voice (an undecided call is remembered)",
      v["session"] == V1 and v["parent"] is None and v["calls"] == ["talk-20261016-0901"], v)
clock[0] = 50100.0
ag.voice_end(vid, "talk-20261016-0902", V2)
n0 = len(ui.asked)
ui.answer = {"ok": True}
ag.voice_end(vid, "talk-20261016-0903", V3)
v = m.store.agent(vid)["voice"]
check("  each call moves it on, keeping the one before (for Forget)", v["session"] == V3 and v["parent"] == V2, v)
check("  and the one before that is tidied away, as Standby's own",
      wait(lambda: ("local", ["delete-agent", "--id", V1]) in ui.asked[n0:]), ui.asked[n0:])
ag.voice_end(vid, "talk-20261016-0903", V3)
check("  told twice for the same call: nothing changes", m.store.agent(vid)["voice"]["parent"] == V2)
print("  Keep or Forget")
check("forgetting an older call that later calls build on: the voice keeps it, and says so",
      ag.voice_forget(vid, "talk-20261016-0902") == "The voice still remembers this call: later calls build on it."
      and m.store.agent(vid)["voice"]["session"] == V3)
check("forgetting the latest call: the voice goes back to before it",
      ag.voice_forget(vid, "talk-20261016-0903") is None and m.store.agent(vid)["voice"]["session"] == V2
      and m.store.agent(vid)["voice"]["parent"] is None)
check("  a call that isn't the voice's: nothing", ag.voice_forget(vid, "talk-20261016-0999") is None
      and m.store.agent(vid)["voice"]["session"] == V2)
check("  forgetting the one it went back to, with no parent left: the voice keeps it",
      ag.voice_forget(vid, "talk-20261016-0902") == "The voice still remembers this call: later calls build on it.")
wsn = m.add_workspace({"machine": "local", "folder": str(FOLDER), "agents": [{"session": V3[:-1] + "0", "name": "Ines"}]})
nid = wsn["agents"][0]["id"]
ag.voice_end(nid, "talk-20261016-1001", V1)
check("forgetting an agent's only call: its voice starts afresh next time",
      ag.voice_forget(nid, "talk-20261016-1001") is None and "voice" not in m.store.agent(nid))
# ---------------------------------------------------------------- Standby's own sessions stay out of sight
print("the postman leaves nothing in the user's list of sessions")
sa._HELP[str(FAKE)] = "  --no-session-persistence   Disable session persistence"
slot = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(ROOT / "agent" / "postman")))
slot.mkdir(parents=True, exist_ok=True)
(slot / "11111111-2222-4333-8444-555555555555.jsonl").write_text("{}\n")       # one saved before
sa.deliver(S1, "[MESSAGE] nothing kept", None, str(FAKE), AGENT, str(REG))
check("a Claude Code that can: the postman runs with --no-session-persistence",
      "--no-session-persistence" in calls("postman")[-1]["argv"], calls("postman")[-1]["argv"])
check("  and what postmen saved before is tidied away", not slot.exists())
sa._HELP[str(FAKE)] = "  --print"
sa.deliver(S1, "[MESSAGE] older one", None, str(FAKE), AGENT, str(REG))
check("  a Claude Code that can't: the flag isn't passed (it would refuse to run)",
      "--no-session-persistence" not in calls("postman")[-1]["argv"])
sa._HELP.pop(str(FAKE), None)
print("  the conversations Standby made for itself are told apart from agents")
VX, CX = "a1a1a1a1-1111-4111-8111-111111111111", "c1c1c1c1-1111-4111-8111-111111111111"
sa.note_created(VX, "voice")
sa.note_created(CX, "copy")
check("a voice and a copy are helpers; an agent Standby started is not",
      {VX, CX} <= sa.helpers() and VX in sa.created() and not (sa.helpers() & {nadia for nadia in sa.created()
                                                                                if nadia not in (VX, CX)}), sa.helpers())
with open(sa._created_path(), "a") as fh:
    fh.write("d1d1d1d1-1111-4111-8111-111111111111\n")          # a line from before kinds: an agent
check("  a line from before kinds counts as an agent (deletable, not hidden)",
      "d1d1d1d1-1111-4111-8111-111111111111" in sa.created()
      and "d1d1d1d1-1111-4111-8111-111111111111" not in sa.helpers())

print("  one conversation, one agent")
taken = m.store.agent(aid)["session"]
other_ws = m.add_workspace({"machine": "local", "folder": str(FOLDER), "agents": []})["workspace"]["id"]
r = m.add_agent({"workspace": other_ws, "session": taken})
check("adding a conversation that's already an agent in another workspace: refused, by name",
      r.get("error", "").startswith("That conversation is already "), r)


print()
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
