"""Continue in Claude Code holds the meeting's conversation: one owner at a time.

`standby-agent hold -- <claude>` runs Claude Code on the meeting's own
conversation. Against a real serve loop and a stub Claude Code: the running
listen turn stops at once and the meeting engine exits before the child
starts; meanwhile a typed question waits (with a step saying why) and new
transcript stays unsent; a second hold is refused; after the child quits the
engine comes back with --resume, the question is answered and gets the lines
said meanwhile. A closing window (SIGHUP) or a kill ends the child too, Ctrl-C
is the child's, and a hold whose process died is cleaned up."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, signal, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbhold-")
stub, stub_log = os.path.join(root, "fake-claude"), os.path.join(root, "stub.log")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys, time
log = open(%r, "a", buffering=1)
kind = "help" if "--help" in sys.argv else "voice" if "--tools" in sys.argv else "meeting"
log.write("start %%d %%s %%s\\n" %% (os.getpid(), kind, " ".join(a for a in sys.argv if a in ("--resume", "--session-id"))))
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
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
            if not c:
                log.write("exit %%d\\n" %% os.getpid()); sys.exit(0)
            buf += c
while True:
    msg = next(lines(3600), None)
    if msg is None: break
    if msg.get("type") != "user": continue
    text = msg["message"]["content"][0]["text"]
    if text.startswith("[TRANSCRIPT]") and "slow" in text:
        for m in lines(20):
            if m.get("type") == "control_request":
                emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
                break
        else:
            emit({"type": "result", "is_error": False, "result": "."})
    elif text.startswith("[TRANSCRIPT]"):
        emit({"type": "result", "is_error": False, "result": "."})
    else:
        emit({"type": "result", "is_error": False, "result": "ANSWER"})
''' % stub_log)
os.chmod(stub, 0o755)
child = os.path.join(root, "child.py")
with open(child, "w") as fh:
    fh.write('''import os, sys, time
open(%r, "w").write(open(%r).read())          # what the stub had done when we started
end = time.time() + 30
while not os.path.exists(%r) and time.time() < end:
    time.sleep(0.05)
''' % (os.path.join(root, "child.saw"), stub_log, os.path.join(root, "release")))

live = os.path.join(root, "live"); os.makedirs(live)
S = "hold-test"
json.dump({"session": S, "mode": "mic"}, open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
transcript = os.path.join(live, S + ".jsonl")


def say(*texts):
    with open(transcript, "a") as fh:
        for i, t in enumerate(texts):
            fh.write(json.dumps({"t": i * 3, "speaker": "Room", "text": t}) + "\n")


say("slow topic 0", "slow topic 1", "slow topic 2", "slow topic 3")
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub,
           STANDBY_PUSH_LINES="3", STANDBY_PUSH_SECONDS="999", STANDBY_PUSH_MIN_GAP="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent
PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
run = lambda *a, **k: subprocess.run([PY, agent] + list(a), env=k.pop("env", env), capture_output=True, text=True,
                                     timeout=30, **k)
starts = lambda: [l for l in open(stub_log).read().splitlines() if " meeting " in l] if os.path.exists(stub_log) else []
fails = []


def check(label, ok, d=""):
    print("  %-62s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:600])


def wait(cond, secs=30):          # generous: five people run these suites at once, on one Mac
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


log = os.path.join(root, "serve.log")
srv = subprocess.Popen([PY, agent, "serve"], env=env, stderr=open(log, "w"))
q = hold = None
try:
    wait(lambda: len(starts()) == 1)
    # Real time on purpose: the engine has started (waited above), and this gives its
    # listen turn time to be under way, which is what `hold` has to interrupt.
    time.sleep(1.5)
    t0 = time.time()
    hold = subprocess.Popen([PY, agent, "hold", "--session", S, "--", PY, child], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    started = wait(lambda: os.path.exists(os.path.join(root, "child.saw")))
    took = time.time() - t0
    check("hold: Claude Code starts at once, the listen turn is stopped", started and took < 4, "%.1fs" % took)
    saw = open(os.path.join(root, "child.saw")).read() if started else ""
    check("  the meeting engine had exited before it started", "exit %s" % starts()[0].split()[1] in saw, saw)
    check("  held says so", run("held", "--session", S).returncode == 0)
    q = subprocess.Popen([PY, agent, "q", "--json", "what was topic 2"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    say("new line a", "new line b", "new line c")
    time.sleep(1.5)                   # real time: proving the question does NOT get answered needs time
    check("while held: a typed question waits", q.poll() is None)
    check("  and no engine starts, no transcript goes out", len(starts()) == 1, starts())
    hp = os.path.join(root, "agent", S + ".hold")
    taken = wait(lambda: os.path.exists(hp))          # the first hold has it before a second can be refused
    second = run("hold", "--session", S, "--", "true")
    check("a second hold is refused", second.returncode != 0 and "already open" in second.stderr,
          # Seen failing once under heavy load with no cause found: this says what the state was.
          "hold file %s before=%s after=%s first hold pid %s alive=%s | %s"
          % (hp, taken, os.path.exists(hp) and open(hp).read().strip(), hold.pid, hold.poll() is None, second))
    open(os.path.join(root, "release"), "w").close()
    rc = hold.wait(timeout=10)
    check("child quits: hold exits with its status, and lets go",
          rc == 0 and not os.path.exists(os.path.join(root, "agent", S + ".hold"))
          and run("held", "--session", S).returncode == 1, (rc, hold.stdout.read()))
    out, _ = q.communicate(timeout=20)
    events = [json.loads(l) for l in out.splitlines() if l.strip()]
    check("  the waiting question is answered", any(e.get("type") == "answer" and e["text"] == "ANSWER" for e in events), out)
    check("  with a step saying why it waited",
          any(e.get("kind") == "tool" and "Claude Code in Terminal" in e.get("text", "") for e in events), out)
    check("  the engine comes back on the same conversation (--resume)",
          len(starts()) == 2 and "--resume" in starts()[1], starts())
    check("  and the question carries what was said meanwhile", wait(lambda: "(+3 unsent lines)" in open(log).read()),
          open(log).read())
    srvlog = open(log).read()
    check("serve log: held, then back", "is open in Claude Code" in srvlog and "is back with the meeting agent" in srvlog, srvlog)

    # Signals: Ctrl-C is Claude Code's; a closing window (SIGHUP) or SIGTERM ends both.
    for sig in (signal.SIGHUP, signal.SIGTERM):
        up = os.path.join(root, "up-%d" % sig)
        h = subprocess.Popen([PY, agent, "hold", "--session", S, "--", PY, "-c",
                              "import time; open(%r, 'w'); time.sleep(30)" % up], env=env)
        wait(lambda: os.path.exists(up))             # Claude Code is running
        if sig == signal.SIGHUP:
            os.kill(h.pid, signal.SIGINT)
            time.sleep(0.5)
            check("Ctrl-C to the hold alone does not end it", h.poll() is None)
        os.kill(h.pid, sig)
        try:
            h.wait(timeout=5)
        except subprocess.TimeoutExpired:
            h.kill()
        check("%s: Claude Code ends too, and the hold lets go" % signal.Signals(sig).name,
              h.returncode is not None and run("held", "--session", S).returncode == 1, h.returncode)

    argv = os.path.join(root, "argv.json")
    show = "import json, sys; json.dump(sys.argv[1:], open(%r, 'w'))" % argv
    run("hold", "--session", S, "--", PY, "-c", show, "--resume", "never-said-1")
    got = json.load(open(argv)) if os.path.exists(argv) else None
    check("nothing said in the conversation yet: Claude Code starts it under its id",
          got == ["--session-id", "never-said-1"], got)
    proj = os.path.join(root, "home", ".claude", "projects", "-p")
    os.makedirs(proj)
    open(os.path.join(proj, "said-2.jsonl"), "w").write("{}\n")
    run("hold", "--session", S, "--", PY, "-c", show, "--resume", "said-2", env=dict(env, HOME=os.path.join(root, "home")))
    got = json.load(open(argv))
    check("  one that has started is resumed", got == ["--resume", "said-2"], got)

    dead = subprocess.Popen(["true"]); dead.wait()
    with open(os.path.join(root, "agent", S + ".hold"), "w") as fh:
        fh.write("%d\n" % dead.pid)
    check("a hold whose process died is cleaned up", run("held", "--session", S).returncode == 1
          and not os.path.exists(os.path.join(root, "agent", S + ".hold")))
finally:
    for p in (q, hold):
        if p is not None and p.poll() is None:
            p.kill()
    srv.terminate(); srv.wait(timeout=10)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
