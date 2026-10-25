"""A meeting on one of the user's own Claude Code conversations. The agent runs
in a temporary HOME with a made-up ~/.claude: the conversations it lists, the
original it resumes in its own folder, and Claude Code's registry of running
sessions, which a stand-in Claude fills in for itself as the real one does. Never
two writers: when "Terminal" (a process of another group) has the conversation open,
nothing is sent to it; when it closes, the lines it missed go once, then deltas.
A copy is forked at its first turn under the id chosen for it. No model calls."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, re, shutil, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agentpath import AGENT as agent  # noqa: E402

root = os.path.realpath(tempfile.mkdtemp(prefix="sbattach-"))
home = os.path.join(root, "home")
claude_home = os.path.join(home, ".claude")
live = os.path.join(root, "live")
for d in (live, os.path.join(claude_home, "sessions")):
    os.makedirs(d)
fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-900:])


def wait(pred, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return bool(pred())


def slot(path):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(path))


def conversation(sid, folder, title=None, custom=None, prompt="fix the flaky trips test", tokens=251002):
    """A made-up Claude Code conversation in `folder`, as Claude Code writes them."""
    os.makedirs(folder, exist_ok=True)
    d = os.path.join(claude_home, "projects", slot(folder))
    os.makedirs(d, exist_ok=True)
    rows = [{"type": "user", "cwd": folder, "sessionId": sid, "uuid": "u1",
             "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "cwd": folder, "sessionId": sid, "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}],
                         "usage": {"input_tokens": 2, "cache_read_input_tokens": tokens - 1002,
                                   "cache_creation_input_tokens": 1000}}}]
    if title:
        rows.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
    if custom:
        rows.append({"type": "custom-title", "customTitle": custom, "sessionId": sid})
    rows.append({"type": "last-prompt", "lastPrompt": prompt, "sessionId": sid})
    with open(os.path.join(d, sid + ".jsonl"), "w") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    return os.path.join(d, sid + ".jsonl")


def register(pid, sid):
    with open(os.path.join(claude_home, "sessions", "%d.json" % pid), "w") as fh:
        json.dump({"pid": pid, "sessionId": sid, "kind": "interactive", "entrypoint": "cli"}, fh)


ORIG = "11111111-2222-4333-8444-555555555555"
OTHER = "66666666-7777-4888-9999-aaaaaaaaaaaa"
GONE = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
TEMP = "12121212-3434-4565-8787-909090909090"
OWN = "abababab-cdcd-4efe-8f0f-a1a1a1a1a1a1"
trips = os.path.join(root, "code", "trips")
conversation(ORIG, trips, title="Trips refactor", custom="Trips: the flaky test")
time.sleep(0.02)
conversation(OTHER, os.path.join(root, "code", "billing"), title="Billing export", tokens=48000)
gone = conversation(GONE, os.path.join(root, "code", "removed"), title="Gone")
shutil.rmtree(os.path.join(root, "code", "removed"))
conversation(TEMP, os.path.join(root, "scratch", "x"), title="Scratch")
conversation(OWN, os.path.join(root, "agent"), title="Standby's own meeting")

stub = os.path.join(root, "fake-claude")
stub_log = os.path.join(root, "stub.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
"""Claude Code, as far as the agent and the registry can tell."""
import json, os, re, select, sys
args = sys.argv[1:]
if "--help" in args:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
opt = lambda n: args[args.index(n) + 1] if n in args else None
resume, chosen, fork = opt("--resume"), opt("--session-id"), "--fork-session" in args
me = chosen if (fork or not resume) else resume
home = os.environ["HOME"]
reg = os.path.join(home, ".claude", "sessions", "%d.json" % os.getpid())
json.dump({"pid": os.getpid(), "sessionId": me, "kind": "interactive", "entrypoint": "sdk-cli"}, open(reg, "w"))
log = lambda o: open(os.environ["STUB_LOG"], "a").write(json.dumps(o) + "\\n")
log({"start": me, "argv": args, "cwd": os.path.realpath(os.getcwd())})
conv_dir = os.path.join(home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.getcwd())))
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init", "session_id": me})
buf = b""
try:
    while True:
        r, _, _ = select.select([sys.stdin], [], [], 3600)
        c = os.read(sys.stdin.fileno(), 1 << 20)
        if not c:
            break
        buf += c
        while b"\\n" in buf:
            line, buf = buf.split(b"\\n", 1)
            if not line.strip():
                continue
            msg = json.loads(line)
            if msg.get("type") != "user":
                continue
            text = "".join(b.get("text", "") for b in msg["message"]["content"] if b["type"] == "text")
            log({"turn": me, "text": text})
            os.makedirs(conv_dir, exist_ok=True)
            with open(os.path.join(conv_dir, me + ".jsonl"), "a") as out:
                out.write(json.dumps({"type": "user", "cwd": os.path.realpath(os.getcwd()), "sessionId": me}) + "\\n")
            emit({"type": "result", "is_error": False, "result": "noted"})
finally:
    try:
        os.remove(reg)
    except OSError:
        pass
''')
os.chmod(stub, 0o755)
env = dict(os.environ, STANDBY_ROOT=root, HOME=home, STANDBY_CLAUDE=stub, STUB_LOG=stub_log,
           STANDBY_PUSH_LINES="2", STANDBY_PUSH_SECONDS="0.5", STANDBY_PUSH_MIN_GAP="0",
           STANDBY_TEMP_DIRS=os.path.join(root, "scratch") + "/", STANDBY_VOICE_QUIET="3600")
PY = sys.executable
log_path = os.path.join(root, "serve.log")


def agent_json(*args):
    r = subprocess.run([PY, agent] + list(args), env=env, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"error": r.stdout + r.stderr}


def stub_rows():
    try:
        return [json.loads(l) for l in open(stub_log) if l.strip()]
    except IOError:
        return []


def pushed():
    """Every transcript word the stand-in Claude was sent, in order."""
    return re.findall(r"word-\d{3}", " ".join(r["text"] for r in stub_rows() if r.get("turn")))


def say(session, *words):
    with open(os.path.join(live, session + ".jsonl"), "a") as fh:
        for w in words:
            n = int(w.split("-")[1])
            fh.write(json.dumps({"t": n, "end": n + 1, "text": "and then %s was said" % w, "speaker": "Room"}) + "\n")


# ---------------------------------------------------------------- the list
foreign = subprocess.Popen(["sleep", "600"], start_new_session=True)       # "Terminal": another process group
register(foreign.pid, OTHER)
register(999999, ORIG)                                                      # left by a crash: no such process
got = agent_json("conversations", "--limit", "10")
ids = [c["id"] for c in got.get("conversations", [])]
check("the list: the user's conversations, newest first", ids == [OTHER, ORIG], got)
first = next((c for c in got.get("conversations", []) if c["id"] == ORIG), {})
check("  each with its own title (the user's /rename first), last prompt, folder, size",
      first.get("title") == "Trips: the flaky test" and first.get("prompt") == "fix the flaky trips test"
      and first.get("path") == trips and first.get("tokens") == 251002 and first.get("at"), first)
check("  open in Claude Code: told by the registry, a crashed one's leftover file ignored",
      [bool(c["open"]) for c in got["conversations"]] == [True, False], got["conversations"])
check("  and where, and whether it's busy: a Terminal window, idle",
      got["conversations"][0]["open"] == {"where": "Terminal", "busy": False, "name": None}, got["conversations"][0].get("open"))
check("  never Standby's own, a temporary folder's, or one whose folder is gone",
      not {OWN, TEMP, GONE} & set(ids))
os.kill(foreign.pid, signal.SIGTERM)
foreign.wait()
os.remove(os.path.join(claude_home, "sessions", "%d.json" % foreign.pid))
os.remove(os.path.join(claude_home, "sessions", "999999.json"))

# ---------------------------------------------------------------- a meeting on the original
MEET = "meeting-20261007-150000"
json.dump({"session": MEET, "mode": "mic", "workspace": trips, "attach": {"from": ORIG, "copy": False}},
          open(os.path.join(live, MEET + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(MEET + "\n")
srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"], env=env,
                       stderr=open(log_path, "w"))
served = lambda: open(log_path).read()
try:
    wait(lambda: "serving" in served())
    # Two engines warm up at once, in either order: the meeting's (the user's own conversation) and
    # the voice's (its own, never the user's). Look for the meeting's by what it resumes.
    wait(lambda: any(r.get("start") == ORIG for r in stub_rows()))
    start = next((r for r in stub_rows() if r.get("start") == ORIG), {"start": None, "argv": []})
    meeting_uuid = open(os.path.join(root, "agent", MEET + ".uuid")).read().strip()
    check("attached: the user's own conversation is resumed, never a new one",
          meeting_uuid == ORIG and start["start"] == ORIG and "--resume" in start["argv"]
          and start["argv"][start["argv"].index("--resume") + 1] == ORIG
          and "--session-id" not in start["argv"] and "--fork-session" not in start["argv"], (meeting_uuid, start["argv"][-6:]))
    wait(lambda: os.path.exists(os.path.join(root, "agent", MEET + ".voice.uuid")))
    voice_uuid = open(os.path.join(root, "agent", MEET + ".voice.uuid")).read().strip()
    check("  and voice mode has a conversation of its own, never the user's",
          voice_uuid and voice_uuid != ORIG and not any(r.get("start") == voice_uuid and "--resume" in r["argv"]
                                                         for r in stub_rows()), voice_uuid)
    check("  in its own folder (Claude Code finds it only from there)", start["cwd"] == trips, start["cwd"])
    say(MEET, "word-001", "word-002")
    check("the transcript goes into it as into any meeting", wait(lambda: pushed() == ["word-001", "word-002"]),
          pushed())
    turns = lambda who: [r["text"] for r in stub_rows() if r.get("turn") == who]
    check("  its first turn carries Standby's instructions, marked as Standby's (its own system prompt is kept)",
          turns(ORIG) and turns(ORIG)[0].startswith("[STANDBY RULES]") and "[/STANDBY RULES]" in turns(ORIG)[0]
          and MEET in turns(ORIG)[0].split("[/STANDBY RULES]")[0], turns(ORIG)[:1])
    check("  scoped to Standby's own turns, which it names; what the user types in Claude Code is normal work",
          all(tag in turns(ORIG)[0] for tag in ("[TRANSCRIPT]", "[QUESTION]", "[VOICE]"))
          and "typed by the user directly in Claude Code" in turns(ORIG)[0]
          and turns(ORIG)[0].split("[/STANDBY RULES]", 1)[1].lstrip().startswith("[TRANSCRIPT]"), turns(ORIG)[0][-300:])
    say(MEET, "word-003", "word-004")
    check("  and the next push carries only the lines since the last", wait(lambda: len(pushed()) == 4)
          and pushed() == ["word-001", "word-002", "word-003", "word-004"], pushed())
    time.sleep(1.0)
    check("its own Claude Code in the registry is not someone else's: nothing is held",
          "is open in Claude Code" not in served() and not os.path.exists(os.path.join(root, "agent", MEET + ".hold")),
          served()[-400:])

    terminal = subprocess.Popen(["sleep", "600"], start_new_session=True)
    register(terminal.pid, ORIG)
    check("opened in Claude Code elsewhere: the meeting waits, within a moment",
          wait(lambda: "is open in Claude Code (pid %d)" % terminal.pid in served(), 5), served()[-500:])
    held = subprocess.run([PY, agent, "held", "--session", MEET], env=env, capture_output=True, text=True)
    check("  `held` says so, that it is held for another Claude Code, and where",
          held.returncode == 0 and held.stdout.split() == [str(terminal.pid), "elsewhere", "Terminal"], held.stdout)
    n = len(stub_rows())
    say(MEET, "word-005", "word-006", "word-007")
    time.sleep(2.0)
    check("  nothing is sent to it meanwhile: never two writers", len(stub_rows()) == n
          and not [r for r in stub_rows()[n:] if r.get("turn")], stub_rows()[n:])
    os.kill(terminal.pid, signal.SIGTERM)
    terminal.wait()
    os.remove(os.path.join(claude_home, "sessions", "%d.json" % terminal.pid))
    check("closed there: the meeting is back", wait(lambda: "is back with the meeting agent" in served(), 5),
          served()[-400:])
    check("  the lines it missed go once, together", wait(lambda: len(pushed()) == 7)
          and pushed()[4:] == ["word-005", "word-006", "word-007"], pushed())
    restarted = [r for r in stub_rows() if r.get("start")]
    check("  from a fresh Claude Code, which reads the conversation again, theirs included",
          len(restarted) >= 2 and restarted[-1]["start"] == ORIG, [r.get("argv", [])[-2:] for r in restarted])
    say(MEET, "word-008", "word-009")
    check("  then deltas again", wait(lambda: len(pushed()) == 9) and pushed()[7:] == ["word-008", "word-009"],
          pushed())
    check("  the instructions went once: not again after the pause, nor from the fresh Claude Code",
          sum("[STANDBY RULES]" in t for t in turns(ORIG)) == 1, [t[:40] for t in turns(ORIG)])
    check("two pushes in a row never repeat a line, across the pause",
          len(pushed()) == len(set(pushed())) == 9, pushed())

    # ---------------------------------------------------------------- a copy, when the user asks for one
    COPY = "meeting-20261007-160000"
    json.dump({"session": COPY, "mode": "mic", "workspace": trips, "attach": {"from": ORIG, "copy": True}},
              open(os.path.join(live, COPY + ".meta.json"), "w"))
    open(os.path.join(live, "CURRENT"), "w").write(COPY + "\n")
    wait(lambda: os.path.exists(os.path.join(root, "agent", COPY + ".uuid")))
    copy_id = open(os.path.join(root, "agent", COPY + ".uuid")).read().strip()
    wait(lambda: any(r.get("start") == copy_id for r in stub_rows()))
    made = next((r for r in stub_rows() if r.get("start") == copy_id), {"argv": []})
    a = made["argv"]
    check("a copy: forked from the original at its first start, under the id chosen for it",
          copy_id != ORIG and "--fork-session" in a and a[a.index("--resume") + 1] == ORIG
          and a[a.index("--session-id") + 1] == copy_id, a[-8:])
    before = os.path.getsize(os.path.join(claude_home, "projects", slot(trips), ORIG + ".jsonl"))
    say(COPY, "word-101", "word-102")
    wait(lambda: "word-101" in pushed())
    check("  the copy is told Standby's instructions too, in its first turn",
          turns(copy_id) and turns(copy_id)[0].startswith("[STANDBY RULES]") and COPY in turns(copy_id)[0],
          [t[:60] for t in turns(copy_id)])
    check("  its turns go to the copy, never the original",
          [r["turn"] for r in stub_rows() if r.get("turn") and "word-101" in r["text"]] == [copy_id]
          and os.path.getsize(os.path.join(claude_home, "projects", slot(trips), ORIG + ".jsonl")) == before)
    resume = agent_json("resume-cmd", "--session", COPY)
    check("  Continue in Claude Code opens the copy once it exists",
          "--resume %s" % copy_id in resume.get("command", "") and "--fork-session" not in resume.get("command", ""),
          resume)
finally:
    srv.terminate()
    try:
        srv.wait(10)
    except subprocess.TimeoutExpired:
        srv.kill()

resume = agent_json("resume-cmd", "--session", MEET)
check("Continue in Claude Code on the original lands on the user's own conversation",
      "--resume %s" % ORIG in resume.get("command", "") and resume.get("cwd") == trips, resume)
shutil.rmtree(root, ignore_errors=True)
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
