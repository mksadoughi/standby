"""Voice mode's engine runs --bare where Claude Code signs in without the keychain:
otherwise the context Claude Code attaches to the newest message
made every turn miss the prompt cache. Vertex
or an API key, in the agent's environment or in ~/.claude/settings.json; never
the meeting agent; never with STANDBY_VOICE_BARE=0; and once a bare engine could not
sign in, the voice goes on without it. A stand-in Claude only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile
from importlib.machinery import SourceFileLoader

root = tempfile.mkdtemp(prefix="sbbaretest-")
home = os.path.join(root, "home")
os.makedirs(os.path.join(home, ".claude"))
stub = os.path.join(root, "fake-claude")
seen = os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys
seen = %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--bare  Minimal mode')
    sys.exit(0)
bare = "--bare" in sys.argv
open(seen, "a").write(json.dumps({"bare": bare}) + "\\n")
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if not line.strip(): continue
    if bare and os.environ.get("FAKE_BARE_REFUSED"):
        emit({"type": "result", "is_error": True, "result": "Invalid API key \\u00b7 Please run /login"})
        continue
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "VOICE ANSWER."}}})
    emit({"type": "result", "is_error": False, "result": "VOICE ANSWER."})
''' % seen)
os.chmod(stub, 0o755)
os.environ.update(STANDBY_ROOT=root, STANDBY_CLAUDE=stub, HOME=home)
for k in ("CLAUDE_CODE_USE_VERTEX", "ANTHROPIC_API_KEY", "STANDBY_VOICE_BARE"):
    os.environ.pop(k, None)
agent = SourceFileLoader("standby_agent", "plugin/bin/standby-agent").load_module()
agent.ensure_dirs()
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


def argv(kind="voice"):
    return agent.build_argv("probe-20261008-000000", "sonnet", True, "sid", stream=True, kind=kind)


check("signed in some other way (a /login): the voice engine as before, not bare", "--bare" not in argv())
os.environ["CLAUDE_CODE_USE_VERTEX"] = "1"
check("Vertex: the voice engine runs --bare", "--bare" in argv(), argv())
check("  its prelude and tools still given (they are what --bare keeps)",
      "--append-system-prompt" in argv() and "--tools" in argv() and "Bash,Read,Grep" in argv())
check("  never the meeting agent: it keeps the user's own Claude Code", "--bare" not in argv("meeting"))
os.environ["CLAUDE_CODE_USE_VERTEX"] = "0"
check("  Vertex turned off (\"0\"): not bare", "--bare" not in argv())
os.environ.pop("CLAUDE_CODE_USE_VERTEX")
os.environ["ANTHROPIC_API_KEY"] = "x"
check("an API key: bare", "--bare" in argv())
os.environ.pop("ANTHROPIC_API_KEY")
with open(os.path.join(home, ".claude", "settings.json"), "w") as fh:
    json.dump({"env": {"CLAUDE_CODE_USE_VERTEX": "1"}}, fh)
check("Vertex in ~/.claude/settings.json (how a host may set it up): bare", "--bare" in argv())
os.environ["STANDBY_VOICE_BARE"] = "0"
check("  STANDBY_VOICE_BARE=0: not bare", "--bare" not in argv())
os.environ.pop("STANDBY_VOICE_BARE")
agent._CAPS.clear()
real_claude = agent.CLAUDE
agent.CLAUDE = os.path.join(root, "old-claude")
with open(agent.CLAUDE, "w") as fh:
    fh.write('#!/bin/sh\necho "--permission-mode <mode>"\n')
os.chmod(agent.CLAUDE, 0o755)
check("  a Claude Code without --bare: not asked for it", "--bare" not in argv())
agent._CAPS.clear()
agent.CLAUDE = real_claude

# A bare engine that cannot sign in that way: the voice goes on without it.
os.environ["FAKE_BARE_REFUSED"] = "1"
eng = agent.ClaudeSession("probe-20261008-000000", "sonnet", kind="voice")
import io  # noqa: E402
said, sys.stderr = sys.stderr, io.StringIO()
try:
    text, rc = eng.ask("hello", 20, label="voice")
finally:
    said, sys.stderr = sys.stderr.getvalue(), said
starts = [json.loads(l)["bare"] for l in open(seen)]
check("bare refused (\"Invalid API key · Please run /login\"): asked again, not bare, and answered",
      text == "VOICE ANSWER." and rc == 0 and starts == [True, False], (text, rc, starts))
check("  and not bare again in this run", "--bare" not in argv() and agent.NO_BARE, agent.NO_BARE)
check("  said in the agent's log", "--bare could not sign in" in said and "without it from now on" in said, said)
eng.stop()

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
