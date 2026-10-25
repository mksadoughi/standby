"""Claude Code that cannot sign in, against a stand-in Claude that says
what Claude Code and Google's auth library say: its Google Cloud credentials could
not be loaded as it started, then every call ended in "Could not load the default
credentials". And Claude Code's own: "Not logged in · Please run /login", "Invalid
API key · Please run /login", each with its own fix.

A failure that never gets better by itself: a voice question hears at once, in
under 3 s, that Claude can't sign in and what to do (below the --- line, the
detail: shown, never read aloud); a typed one too; listen pushes stop trying
instead of piling up; the agent keeps one record of it (live/claude-error.json)
for the app; `probe` (the app's Try again) finds out at once. Once Claude signs
in again everything works and the record is gone."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbsignin-")
stub = os.path.join(root, "fake-claude")
mode = os.path.join(root, "mode")       # expired | login | apikey | quick | ok, read as it goes
STUB = r'''#!/usr/bin/env python3
import json, os, select, sys, time
MODE = "@MODE@"
def now(): return open(MODE).read().strip()
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "auto", "dontAsk")\n--no-session-persistence')
    sys.exit(0)
def err(line): sys.stderr.write(line + "\n"); sys.stderr.flush()
def emit(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
DEFAULT = "Could not load the default credentials. Browse to https://cloud.google.com/docs/authentication"
def creds_fail():                     # what each call logs while it can't get credentials
    err("claude: error: " + DEFAULT)
FAILED = {"type": "result", "is_error": True, "result": "API Error: " + DEFAULT}
m = now()
if m == "expired":                    # Google's auth library, as it starts: never better by itself
    err("claude: error: " + DEFAULT)
LOGIN = {"login": "Not logged in · Please run /login", "apikey": "Invalid API key · Please run /login"}
if m == "quick":                      # says why on stderr and exits at once, before any answer
    err("claude: error: " + DEFAULT)
    sys.exit(1)
if "--input-format" not in sys.argv:  # `probe`: one prompt, one JSON result
    if m == "ok":
        emit({"type": "result", "is_error": False, "result": "."}); sys.exit(0)
    if m in LOGIN:                    # Claude Code's own: a result, at once
        emit({"type": "result", "is_error": True, "result": LOGIN[m]}); sys.exit(1)
    creds_fail()
    time.sleep(40); emit(FAILED); sys.exit(1)
emit({"type": "system", "subtype": "init"})
buf = b""
def lines(timeout):
    global buf
    end = time.time() + timeout
    while time.time() < end:
        while b"\n" in buf:
            l, buf = buf.split(b"\n", 1)
            if l.strip(): yield json.loads(l)
        r, _, _ = select.select([sys.stdin], [], [], 0.05)
        if r:
            c = os.read(sys.stdin.fileno(), 65536)
            if not c: return
            buf += c
def answer(text):
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})
    emit({"type": "result", "is_error": False, "result": text})
while True:
    msg = next(lines(3600), None)
    if msg is None: break
    if msg.get("type") != "user": continue
    text = msg["message"]["content"][-1]["text"]
    m = now()
    if m == "ok":
        answer("." if text.startswith(("[TRANSCRIPT]", "[BRIEFING]")) else "ANSWER"); continue
    if m in LOGIN:
        emit({"type": "result", "is_error": True, "result": LOGIN[m]}); continue
    creds_fail()                      # each call tries again
    for c in lines(40):
        if c.get("type") == "control_request":
            emit({"type": "control_response", "response": {"subtype": "success"}})
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
            break
    else:
        emit(FAILED)
'''
with open(stub, "w") as fh:
    fh.write(STUB.replace("@MODE@", mode))
os.chmod(stub, 0o755)


def set_mode(m):
    with open(mode, "w") as fh:
        fh.write(m + "\n")


set_mode("expired")
live = os.path.join(root, "live")
os.makedirs(live)
S = "meeting-signin"
json.dump({"session": S, "mode": "mic"}, open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
transcript = os.path.join(live, S + ".jsonl")


def said(text, t=[0]):
    t[0] += 3
    with open(transcript, "a") as fh:
        fh.write(json.dumps({"t": t[0], "speaker": "Room", "text": text}) + "\n")


# The real wait (1 s after a failure that never recovers); listen pushes back off longer than the test, so none runs into a question.
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, STANDBY_SIGNIN_BACKOFF="600",
           STANDBY_PUSH_LINES="1", STANDBY_PUSH_SECONDS="999", STANDBY_PUSH_MIN_GAP="0")
for k in ("STANDBY_SIGNIN_GRACE", "STANDBY_SIGNIN_SOFT_WAIT", "ANTHROPIC_API_KEY",
          "ANTHROPIC_AUTH_TOKEN"):
    env.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent  # noqa: E402
PY = "/usr/bin/python3"
log = os.path.join(root, "serve.log")
fails = []
PATIENCE = 30          # seconds: generous for a loaded Mac; each wait ends as soon as it is true


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


def wait(pred, secs=PATIENCE):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return bool(pred())


def ask(question, secs=60):
    t0 = time.time()
    r = subprocess.run([PY, agent, "q", question], env=env, capture_output=True, text=True, timeout=secs + 30)
    return r.stdout.strip(), time.time() - t0


def probe():
    t0 = time.time()
    r = subprocess.run([PY, agent, "probe"], env=env, capture_output=True, text=True, timeout=90)
    return json.loads(r.stdout.strip().splitlines()[-1]), time.time() - t0


def record():
    try:
        return json.load(open(os.path.join(live, "claude-error.json")))
    except (IOError, ValueError):
        return None


def serve_log():
    return open(log).read() if os.path.exists(log) else ""


SIGNIN = "I can't reach Claude: it can't sign in. Your Google Cloud credentials need refreshing, then ask again."
srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"],
                       env=env, stderr=open(log, "w"))
try:
    # -- expired: never recovers by itself
    answer, took = ask("[VOICE] Can you hear me?")
    check("Google Cloud credentials gone: a voice question hears it in under 3 s, not 40 s on",
          took < 3 and answer.split("---")[0].strip() == SIGNIN, (round(took, 1), answer))
    check("  the detail is below the --- line: in the chat, never read aloud",
          "---" in answer and "Could not load the default credentials" in answer.split("---", 1)[1], answer)
    rec = record()
    check("one record of it for the app: kind, what to do, the detail, when",
          rec and rec.get("kind") == "signin" and rec.get("fix") == "Your Google Cloud credentials need refreshing, then try again."
          and "Could not load the default credentials" in rec.get("detail", "") and rec.get("at"), rec)
    answer, took = ask("what did they decide")
    check("  a typed question too", took < 3 and answer.startswith(SIGNIN), (round(took, 1), answer))
    said("we ship on Friday")
    check("a listen push ends as fast, and says the next one waits",
          wait(lambda: "can't sign in; the next push waits" in serve_log()), serve_log()[-600:])
    said("and the budget is fine")
    said("the launch is in March")
    time.sleep(4)          # on purpose: lines are due all along; one more push would have come in this time
    check("  no more pushes meanwhile, however many lines wait (they piled up, ~40 s each)",
          serve_log().count("listen: pushed") == 1, serve_log())
    out, took = probe()
    check("probe (the app's Try again): still can't sign in, found out at once",
          took < 5 and out.get("ok") is False and out["error"]["kind"] == "signin", (round(took, 1), out))

    # -- signed in again
    set_mode("ok")
    out, took = probe()
    check("signed in again: probe says so, and the record is gone", out == {"ok": True, "error": None} and record() is None,
          (out, record()))
    answer, took = ask("what did they decide")
    check("  questions are answered again, no state kept from before", answer == "ANSWER", (took, answer))
    answer, took = ask("[VOICE] Can you hear me now?")
    check("  voice ones too", answer == "ANSWER", answer)

    # -- open in another Claude Code: nothing is sent, so nothing can fail to sign in
    set_mode("expired")
    hold = os.path.join(root, "agent", S + ".hold")
    with open(hold, "w") as fh:
        fh.write("%d\n" % os.getpid())            # held by a live process: this one
    check("held: the agent sees it", wait(lambda: os.path.exists(hold + ".ack")), os.listdir(os.path.join(root, "agent")))
    pushed = serve_log().count("listen: pushed")
    said("while it is open elsewhere")
    time.sleep(3)          # on purpose: a push would have been tried, and failed to sign in, in this time
    check("  a held session's lines wait: no push, no sign-in error recorded",
          serve_log().count("listen: pushed") == pushed and record() is None, (serve_log()[-400:], record()))
    os.remove(hold)
    set_mode("ok")
    check("  let go: back with the meeting agent", wait(lambda: "is back with the meeting agent" in serve_log().split(
        "is open in Claude Code")[-1]), serve_log()[-400:])
    answer, took = ask("and now")
    check("  its waiting line goes with the next question, which Claude answers; still no error",
          answer == "ANSWER" and "> and now  (+1 unsent lines)" in serve_log() and record() is None,
          (answer, serve_log()[-400:], record()))

    # -- Claude Code's own sign-in: not logged in, or a key it refuses
    set_mode("login")
    answer, took = ask("[VOICE] Are you there?")
    check("not logged in: said as sign-in, at once, with Claude Code's own fix",
          took < 5 and answer.split("---")[0].strip() == "I can't reach Claude: it can't sign in. "
          "Sign in to Claude Code again (run claude, then /login), then ask again.", (round(took, 1), answer))
    out, took = probe()
    check("  probe too, not \"Claude Code stopped with an error\"", out.get("ok") is False
          and out["error"]["kind"] == "signin" and out["error"]["fix"].startswith("Sign in to Claude Code again"), out)
    set_mode("apikey")
    answer, took = ask("what did they decide")
    check("a key it refuses, where no key is set: an account's sign-in, the same fix",
          "Sign in to Claude Code again" in answer.split("---")[0], answer)
    # -- one that says why and exits at once: no loop to see it in (standby-65's fake ssh found it)
    set_mode("quick")
    out, took = probe()
    check("a Claude Code that exits at once with a sign-in error: probe says sign-in, not \"said nothing\"",
          out.get("ok") is False and out["error"]["kind"] == "signin", out)
    answer, took = ask("[VOICE] Still there?")
    check("  and a question hears it as sign-in too", answer.split("---")[0].strip() == SIGNIN, answer)
    set_mode("ok")
    # The kinds, as the agent tells them apart (its own function, in this process).
    from importlib.machinery import SourceFileLoader
    os.environ["STANDBY_ROOT"] = root
    a = SourceFileLoader("standby_agent_kinds", agent).load_module()
    os.environ.pop("ANTHROPIC_API_KEY", None)
    kinds = [a.signin_kind(l) for l in ("Could not load the default credentials. Browse to https://cloud.google.com",
                                        "Not logged in · Please run /login", "Invalid API key · Please run /login")]
    os.environ["ANTHROPIC_API_KEY"] = "made-up"
    keyed = a.signin_kind("Invalid API key · Please run /login")
    os.environ.pop("ANTHROPIC_API_KEY")
    check("each kind its own fix: Google Cloud, an account, and a key where one is set",
          kinds == ["google", "account", "account"] and keyed == "api_key"
          and a.SIGNIN_FIXES["google"] == "Your Google Cloud credentials need refreshing"
          and a.SIGNIN_FIXES["api_key"] == "Check Claude Code's API key", (kinds, keyed))
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
check("the agent quit cleanly, never by a signal", srv.returncode is not None and srv.returncode >= 0, srv.returncode)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
