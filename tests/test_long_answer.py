"""A question that keeps working must never be cut off; a silent one must be.
Progress streams to the client while the agent works."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sblong-")
stub = os.path.join(root, "fake-claude")
open(stub, "w").write('''#!/usr/bin/env python3
import json, sys, time
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if not line.strip(): continue
    m = json.loads(line)
    if m.get("type") != "user": continue
    q = m["message"]["content"][0]["text"]
    if "HANG" in q:
        time.sleep(60); continue                     # genuinely stuck: silent
    for i in range(8):                               # 8 s of real work, 1 event/s
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/home/x/notes/v9-%d.md" % i}}]}})
        time.sleep(1)
    emit({"type": "result", "is_error": False, "result": "Here is how to rerun V9: ..."})
''')
os.chmod(stub, 0o755)
live = os.path.join(root, "live"); os.makedirs(live)
json.dump({"session": "s"}, open(os.path.join(live, "s.meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write("s\n")
open(os.path.join(live, "s.jsonl"), "w").close()
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent
# idle timeout 3 s: the 8 s working turn exceeds it in total but never goes idle
srv = subprocess.Popen(["/usr/bin/python3", agent, "--timeout", "3", "serve"], env=env,
                       stderr=open(os.path.join(root, "serve.log"), "w"))
fails = []
def check(label, ok, d=""):
    print("  %-58s %s" % (label, "OK" if ok else "FAIL")); ok or (fails.append(label), print("     " + str(d)))
try:
    time.sleep(1)
    t0 = time.time()
    p = subprocess.run(["/usr/bin/python3", agent, "q", "--json", "how do we rerun V9"],
                       env=env, capture_output=True, text=True, timeout=60)
    took = time.time() - t0
    evs = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    prog = [e for e in evs if e["type"] == "progress"]
    ans = [e for e in evs if e["type"] == "answer"]
    check("8 s of work with a 3 s idle limit is NOT cut off", ans and "rerun V9" in ans[0]["text"], p.stdout + p.stderr)
    check("took longer than the idle limit (really ran)", took > 7, took)
    check("progress streamed: 8 tool steps", len(prog) == 8, prog)
    check("progress is human readable", prog and prog[0]["text"] == "Reading /home/x/notes/v9-0.md", prog[:1])
    t0 = time.time()
    p = subprocess.run(["/usr/bin/python3", agent, "q", "HANG please"],
                       env=env, capture_output=True, text=True, timeout=60)
    took = time.time() - t0
    check("a silent (stuck) turn is stopped after the idle limit", "looks stuck" in p.stdout, p.stdout + p.stderr)
    check("...promptly, not after a long total cap", took < 10, took)
    p = subprocess.run(["/usr/bin/python3", agent, "q", "again after the stuck one"],
                       env=env, capture_output=True, text=True, timeout=60)
    check("the next question still works", "rerun V9" in p.stdout, p.stdout + p.stderr)
finally:
    srv.terminate(); srv.wait(timeout=10)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails)); sys.exit(1 if fails else 0)
