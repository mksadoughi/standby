"""The voice's model chosen in Settings during a call: the call's next turn runs on it, the same
Claude Code process and conversation (its set_model control request), never a restart. A call
started after the change starts on it. One Claude Code refuses: the call keeps the model it has,
and switch.json says why, for Settings. The stand-in claude answers set_model as the real one does
(a control_response) and says in each turn's init which model it runs."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, sys, tempfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

root = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbmodelswitch-")))
std = root / "standby"
(std / "agent" / "spool").mkdir(parents=True)
(std / "live").mkdir()
stub, starts = root / "fake-claude", root / "starts.jsonl"
stub.write_text('''#!/usr/bin/env python3
import json, os, sys
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")')
    sys.exit(0)
a = sys.argv[1:]
model = a[a.index("--model") + 1]
log = open(os.environ["STUB_LOG"], "a", buffering=1)
log.write(json.dumps({"start": model}) + "\\n")
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line)
    if m.get("type") == "control_request" and m["request"].get("subtype") == "set_model":
        want = m["request"]["model"]
        log.write(json.dumps({"set_model": want}) + "\\n")
        if want == "opus":
            emit({"type": "control_response", "response": {"subtype": "error", "request_id": m["request_id"],
                                                           "error": "Opus isn't on this plan"}})
        else:
            model = want
            emit({"type": "control_response", "response": {"subtype": "success", "request_id": m["request_id"]}})
    elif m.get("type") == "user":
        emit({"type": "system", "subtype": "init", "model": "claude-%s-5-5" % model})
        emit({"type": "result", "is_error": False, "result": "on " + model})
''')
os.chmod(str(stub), 0o755)
os.environ.update(STANDBY_ROOT=str(std), STANDBY_CLAUDE=str(stub), STUB_LOG=str(starts))
S = "talk-20261023-120000"
(std / "live" / (S + ".meta.json")).write_text(json.dumps({"session": S, "mode": "mic", "kind": "talk"}))
agent = SourceFileLoader("standby_agent_switch", agentpath.AGENT).load_module()
fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def settings(model):
    (std / "config.json").write_text(json.dumps({"agent": {"mode": "local", "voice_model": model}}))


def log():
    return [json.loads(x) for x in starts.read_text().splitlines()] if starts.exists() else []


def switched():
    return json.loads((std / "agent" / "switch.json").read_text())


try:
    settings("haiku")                                   # changed before the call: it starts on it
    text, rc = agent.engine_claude("Hi.", S, "sonnet", 20, kind="voice")
    check("a call after Settings changed the voice's model: it starts on that one", rc == 0 and text == "on haiku"
          and log() == [{"start": "haiku"}], (text, rc, log()))
    settings("sonnet")                                  # changed during the call
    text, rc = agent.engine_claude("And now?", S, "sonnet", 20, kind="voice")
    check("changed during the call: its next turn is on it", rc == 0 and text == "on sonnet", (text, rc))
    check("  the same process and conversation: set_model, never a restart",
          log() == [{"start": "haiku"}, {"set_model": "sonnet"}], log())
    check("  switch.json says it switched, for Settings",
          switched()["ok"] and switched()["wanted"] == "sonnet" and switched()["was"] == "haiku", switched())
    text, rc = agent.engine_claude("Again.", S, "sonnet", 20, kind="voice")
    check("  the turns after it: no switch again", rc == 0 and log()[-1] == {"set_model": "sonnet"}
          and len(log()) == 2, log())
    settings("opus")                                    # this Claude Code refuses it
    text, rc = agent.engine_claude("Bigger?", S, "sonnet", 20, kind="voice")
    check("refused: the call goes on, on the model it has", rc == 0 and text == "on sonnet", (text, rc))
    check("  and switch.json says why, for Settings", switched()["ok"] is False and switched()["wanted"] == "opus"
          and switched()["was"] == "sonnet" and "Opus isn't on this plan" in (switched()["error"] or ""), switched())
    agent.engine_claude("Still there?", S, "sonnet", 20, kind="voice")
    check("  not asked again at every turn while Settings stays on it", [r for r in log() if r.get("set_model") == "opus"]
          == [{"set_model": "opus"}], log())
    settings("haiku")
    text, rc = agent.engine_claude("Smaller then.", S, "sonnet", 20, kind="voice")
    check("  another choice after it switches as usual", text == "on haiku" and switched()["ok"], (text, switched()))
finally:
    for e in list(agent._LIVE_SESSION.values()):
        e.stop()

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
