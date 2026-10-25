"""The model an agent's own turns run on: a session whose meta says which (claude_model, written
when the app starts it for an agent) runs its meeting engine on that model; one without a choice,
or with something that isn't a model, runs on the one the server was started with; voice mode
keeps the voice model either way. The stand-in claude records the arguments it was started with."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, shutil, subprocess, sys, tempfile, time

fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-700:])


def wait(pred, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return bool(pred())


root = tempfile.mkdtemp(prefix="sbmodels-")
stub, seen = os.path.join(root, "fake-claude"), os.path.join(root, "starts.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
a = sys.argv[1:]
open(os.environ["STUB_LOG"], "a").write(json.dumps({"model": a[a.index("--model") + 1] if "--model" in a else None,
                                                   "voice": "--bare" in a or any("voice" in x.lower() for x in a[-3:]),
                                                   "key": a[a.index("--session-id") + 1] if "--session-id" in a else None}) + "\\n")
sys.stdout.write(json.dumps({"type": "system", "subtype": "init"}) + "\\n"); sys.stdout.flush()
while True:
    r, _, _ = select.select([sys.stdin], [], [], 3600)
    if not os.read(sys.stdin.fileno(), 65536):
        break
''')
os.chmod(stub, 0o755)
from agentpath import AGENT as agent  # noqa: E402
PY = sys.executable
live = os.path.join(root, "live")
os.makedirs(live)
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, STUB_LOG=seen)


def meta(session, **extra):
    with open(os.path.join(live, session + ".meta.json"), "w") as fh:
        json.dump(dict({"session": session, "mode": "mic"}, **extra), fh)


def starts():
    try:
        return [json.loads(l) for l in open(seen) if l.strip()]
    except IOError:
        return []


def serve_with(session, **extra):
    """A server up with `session` current: its engines warmed up. -> (process, [start rows])."""
    meta(session, **extra)
    open(os.path.join(live, "CURRENT"), "w").write(session + "\n")
    n = len(starts())
    srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"], env=env,
                           stderr=open(os.path.join(root, "serve.log"), "w"))
    wait(lambda: len(starts()) >= n + 2)             # the meeting's engine and the voice's warm up together
    return srv, starts()[n:]


def stop(srv):
    srv.terminate()
    srv.wait(10)
    shutil.rmtree(os.path.join(root, "agent"), ignore_errors=True)    # the next server is a new one


srv, rows = serve_with("meeting-20261016-100000", claude_model="haiku")
stop(srv)
check("a session whose agent chose Haiku: its meeting engine runs on it, voice keeps the voice model (Sonnet)",
      sorted(str(r["model"]) for r in rows) == ["haiku", "sonnet"], rows)

srv, rows = serve_with("meeting-20261016-110000")
stop(srv)
check("a session with no choice: the meeting engine runs on the server's model (Opus), the voice on its own",
      sorted(str(r["model"]) for r in rows) == ["opus", "sonnet"], rows)

srv, rows = serve_with("meeting-20261016-120000", claude_model="opus; rm -rf ~")
stop(srv)
check("something that isn't a model is ignored, never put on a command line",
      sorted(str(r["model"]) for r in rows) == ["opus", "sonnet"] and not any("rm" in str(r) for r in rows), rows)

srv, rows = serve_with("meeting-20261016-130000", claude_model="claude-sonnet-5-5")
stop(srv)
check("a full model id, as an agent's last-run model is read from its transcript",
      sorted(str(r["model"]) for r in rows) == ["claude-sonnet-5-5", "sonnet"], rows)

shutil.rmtree(root, ignore_errors=True)
if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
