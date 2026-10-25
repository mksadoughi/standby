"""A question must not wait behind a long listen turn: it interrupts it.

The stub mimics the protocol measured on the real CLI: a turn runs until done
or until a control_request interrupt arrives, then ends with an
error_during_execution result, and the same process takes the next message."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbint-")
stub = os.path.join(root, "fake-claude")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys, time
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
buf = b""
def lines(timeout):
    global buf
    end = time.time() + timeout
    while time.time() < end:
        while b"\\n" in buf:
            l, buf = buf.split(b"\\n", 1)
            if l.strip(): yield json.loads(l)
        r, _, _ = select.select([sys.stdin], [], [], 0.1)
        if r:
            c = os.read(sys.stdin.fileno(), 65536)
            if not c: return
            buf += c
while True:
    msg = next(lines(3600), None)
    if msg is None: break
    if msg.get("type") != "user": continue
    text = msg["message"]["content"][0]["text"]
    if text.startswith("[TRANSCRIPT]"):
        stopped = False
        for m in lines(20):            # "investigating" for up to 20 s
            if m.get("type") == "control_request":
                emit({"type": "control_response", "response": {"subtype": "success"}})
                emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
                stopped = True
                break
        if not stopped:
            emit({"type": "result", "is_error": False, "result": "."})
    else:
        emit({"type": "result", "is_error": False, "result": "ANSWER"})
''')
os.chmod(stub, 0o755)
live = os.path.join(root, "live"); os.makedirs(live)
S = "int-test"
json.dump({"session": S, "mode": "mic"}, open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
with open(os.path.join(live, S + ".jsonl"), "w") as fh:
    for i in range(4):
        fh.write(json.dumps({"t": i * 3, "speaker": "Room", "text": "topic %d" % i}) + "\n")
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub,
           STANDBY_PUSH_LINES="3", STANDBY_PUSH_SECONDS="999", STANDBY_PUSH_MIN_GAP="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent
log = os.path.join(root, "serve.log")
srv = subprocess.Popen(["/usr/bin/python3", agent, "serve"], env=env, stderr=open(log, "w"))
try:
    time.sleep(2)                       # the 20 s listen turn is now running
    t0 = time.time()
    q = subprocess.run(["/usr/bin/python3", agent, "q", "what was topic 2"],
                       env=env, capture_output=True, text=True, timeout=40)
    waited = time.time() - t0
finally:
    srv.terminate(); srv.wait(timeout=10)
out = open(log).read()
fails = []
def check(label, ok, d=""):
    print("  %-56s %s" % (label, "OK" if ok else "FAIL")); ok or (fails.append(label), print("     " + d))
check("question answered", q.stdout.strip() == "ANSWER", q.stdout + q.stderr)
check("waited < 3 s despite a 20 s listen turn", waited < 3.0, "waited %.1fs" % waited)
check("listen turn logged as INTERRUPTED", "INTERRUPTED for a question" in out, out)
check("interrupted lines not resent to the question", "+0 unsent lines" in out, out)
print("\n  question waited %.2fs\n\n%s" % (waited, out.strip()))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
