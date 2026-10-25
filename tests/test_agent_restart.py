"""A long-lived agent server whose code changes on disk restarts into the new code
in place (same pid, so the pidfile stays right), with an easy
line in its log, and only once nothing would notice: never mid-answer, never
while voice mode is in use, never while a conversation is open in Claude Code.
A copy of the agent runs in a temp folder against a stand-in Claude."""
import json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agentpath import AGENT as agent  # noqa: E402

root = tempfile.mkdtemp(prefix="sbrestart-")
bindir = os.path.join(root, "bin")
os.makedirs(bindir)
copy = os.path.join(bindir, "standby-agent")
shutil.copy(agent, copy)
import glob  # noqa: E402
for _m in glob.glob(os.path.join(os.path.dirname(agent), "sb*.py")):     # its helper modules, as the agent watches them
    shutil.copy(_m, bindir)
stub = os.path.join(root, "fake-claude")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys, time
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
buf = b""
while True:
    r, _, _ = select.select([sys.stdin], [], [], 3600)
    c = os.read(sys.stdin.fileno(), 65536)
    if not c: break
    buf += c
    while b"\\n" in buf:
        l, buf = buf.split(b"\\n", 1)
        if not l.strip(): continue
        msg = json.loads(l)
        if msg.get("type") != "user": continue
        text = "".join(b.get("text", "") for b in msg["message"]["content"] if b["type"] == "text")
        if "slow" in text:
            open(os.environ["STUB_SLOW_MARK"], "w").close()      # the answer is under way
            time.sleep(3)
        emit({"type": "result", "is_error": False, "result": "SLOW ANSWER" if "slow" in text else "ANSWER"})
''')
os.chmod(stub, 0o755)
live = os.path.join(root, "live")
os.makedirs(live)
MEET = "meeting-20261005-200000"
json.dump({"session": MEET, "mode": "mic"}, open(os.path.join(live, MEET + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(MEET + "\n")
slow_mark = os.path.join(root, "slow-started")
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, STANDBY_CODE_CHECK="0.3", STANDBY_VOICE_QUIET="6", STUB_SLOW_MARK=slow_mark,
           STANDBY_PUSH_LINES="999", STANDBY_PUSH_SECONDS="999")
PY = "/usr/bin/python3"
log = os.path.join(root, "serve.log")
pidfile = os.path.join(root, "agent", "server.pid")
fails = []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-900:])


def wait(pred, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def logged():
    return open(log).read()


def edit(find, replace):
    with open(copy) as fh:
        text = fh.read()
    assert find in text, find
    with open(copy, "w") as fh:
        fh.write(text.replace(find, replace, 1))
    os.utime(copy, (time.time() + 1, time.time() + 1))        # a newer stamp even within the same second


def ask(question, timeout=60):
    p = subprocess.run([PY, copy, "q", "--json", question], env=env, capture_output=True, text=True, timeout=timeout)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return lines[-1].get("text") if lines else p.stderr


def restarts():
    return logged().count("standby-agent: RESTART")


srv = subprocess.Popen([PY, copy, "--model", "opus", "--voice-model", "sonnet", "serve"], env=env,
                       stderr=open(log, "w"))
try:
    wait(lambda: "serving" in logged())
    pid = int(open(pidfile).read())
    check("the log says which code a server runs", "code of 20" in logged(), logged()[-300:])
    check("an answer before any change", ask("hello") == "ANSWER")

    edit('"standby-agent: serving %s', '"standby-agent: NEW serving %s')
    check("idle, its code changed: it restarts into the new code", wait(lambda: "NEW serving" in logged()), logged())
    check("  with an easy line in the log", restarts() == 1 and "my code changed on disk" in logged())
    check("  in place: same pid, the pidfile still right, still answering",
          srv.poll() is None and int(open(pidfile).read()) == pid == srv.pid and ask("again") == "ANSWER",
          (srv.poll(), open(pidfile).read(), pid))

    out = {}
    import threading
    t = threading.Thread(target=lambda: out.update(a=ask("a slow one")))
    t.start()
    # Edit while the answer is under way, however slowly the question got there: when the stand-in
    # says it began, not after a pause that is only enough on an idle machine.
    wait(lambda: os.path.exists(slow_mark), 20)
    edit("NEW serving", "NEWER serving")
    t.join(30)
    check("mid-answer: the answer is delivered whole", out.get("a") == "SLOW ANSWER", out)
    wait(lambda: "NEWER serving" in logged())
    text = logged()
    check("  and it restarted only after it", restarts() == 2 and "> a slow one" in text
          and text.index("> a slow one") < text.rindex("standby-agent: RESTART") < text.index("NEWER serving"), text[-700:])

    subprocess.run([PY, copy, "warm"], env=env, check=True)
    time.sleep(0.6)
    edit("NEWER serving", "NEWEST serving")
    check("voice mode in use: it waits", wait(lambda: "waiting to restart: voice mode is on" in logged(), 5)
          and "NEWEST serving" not in logged(), logged()[-500:])
    check("  and restarts once voice has been quiet", wait(lambda: "NEWEST serving" in logged(), 15) and restarts() == 3)

    hold = os.path.join(root, "agent", "other-session.hold")
    open(hold, "w").write("%d\n" % os.getpid())
    time.sleep(0.6)
    edit("NEWEST serving", "LAST serving")
    check("a conversation open in Claude Code: it waits",
          wait(lambda: "waiting to restart: a conversation is open in Claude Code" in logged(), 5)
          and "LAST serving" not in logged(), logged()[-500:])
    os.remove(hold)
    check("  and restarts once it's closed", wait(lambda: "LAST serving" in logged()) and restarts() == 4)
    check("after four restarts: one server, the same pid", srv.poll() is None and int(open(pidfile).read()) == pid)
finally:
    srv.terminate()
    srv.wait(10)

shutil.rmtree(root, ignore_errors=True)
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
