"""The agent is fed transcript continuously, questions carry unsent lines, no
line is sent twice, and the agent runs in the session's workspace."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbpush-")
ws = tempfile.mkdtemp(prefix="sbws-")
rec = os.path.join(root, "received.jsonl")
stub = os.path.join(root, "fake-claude")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys
rec = %r
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if not line.strip(): continue
    text = json.loads(line)["message"]["content"][0]["text"]
    with open(rec, "a") as f:
        f.write(json.dumps({"cwd": os.getcwd(), "text": text}) + "\\n")
    reply = "ANSWER" if text.startswith("[QUESTION]") else "."
    emit({"type": "result", "is_error": False, "result": reply})
''' % rec)
os.chmod(stub, 0o755)

live = os.path.join(root, "live"); os.makedirs(live)
S = "push-test"
json.dump({"session": S, "mode": "mic", "workspace": ws},
          open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
tx = os.path.join(live, S + ".jsonl")
def say(t, text):
    with open(tx, "a") as fh:
        fh.write(json.dumps({"t": t, "speaker": "Room", "text": text}) + "\n")

env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub,
           STANDBY_PUSH_LINES="3", STANDBY_PUSH_SECONDS="2", STANDBY_PUSH_MIN_GAP="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent
srv = subprocess.Popen(["/usr/bin/python3", agent, "serve"], env=env,
                       stderr=open(os.path.join(root, "serve.log"), "w"))
def until(pred, secs=30):
    """Wait for what the test waits on, not for a fixed time: with other suites running
    beside this one, the server can take longer than a second to start or to push."""
    end = time.time() + secs
    while time.time() < end and not pred():
        time.sleep(0.05)


def received():
    return open(rec).read() if os.path.exists(rec) else ""


try:
    until(lambda: os.path.exists(os.path.join(root, "agent", "server.pid")))
    time.sleep(0.5)                                          # its first look at the transcript
    for i, t in enumerate([1, 4, 7]):
        say(t, "line %d about the pricing" % i)          # 3 lines -> push
    until(lambda: "line 2" in received())                  # the push has happened
    say(10, "line 3 about Orion")                            # 1 line, waits
    q = subprocess.run(["/usr/bin/python3", agent, "q", "what is Orion"],
                       env=env, capture_output=True, text=True, timeout=30)
    say(20, "line 4 after the question")                      # pushed by time
    time.sleep(3.5)
finally:
    srv.terminate(); srv.wait(timeout=10)

got = [json.loads(l) for l in open(rec)] if os.path.exists(rec) else []
fails = []
def check(label, ok, detail=""):
    print("  %-58s %s" % (label, "OK" if ok else "FAIL"))
    if not ok: fails.append(label); print("     %s" % detail)

listens = [g for g in got if g["text"].startswith("[TRANSCRIPT]")]
questions = [g for g in got if g["text"].startswith("[QUESTION]")]
check("3 lines triggered a silent listen turn", len(listens) >= 1 and "line 2" in listens[0]["text"], got)
check("question answered", q.stdout.strip() == "ANSWER", q.stdout + q.stderr)
check("question carried the unsent line", questions and "line 3 about Orion" in questions[0]["text"], questions)
check("question did NOT resend already-pushed lines", questions and "line 0" not in questions[0]["text"], questions)
check("later line pushed on the time trigger", any("line 4" in g["text"] for g in listens), listens)
allsent = "".join(g["text"] for g in got)
check("every line sent exactly once", all(allsent.count("line %d " % i) == 1 for i in range(5)), allsent)
check("agent ran in the session workspace", got and all(g["cwd"] == os.path.realpath(ws) for g in got),
      set(g["cwd"] for g in got))
print("\n" + open(os.path.join(root, "serve.log")).read().strip())
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
