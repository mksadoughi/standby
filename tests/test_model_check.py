"""A model Claude Code may not use: it is never asked for again.

The real CLI does not refuse a blocked model: it runs another in its place
and says so only in its init message (measured: --model haiku ran
claude-opus-5-5[1m]). The stand-in does the same. The engine reads that
message, asks again on Sonnet before anything is answered, remembers it in
agent/models.json for the next engines, and logs the model really running.
The app's Settings reads models.json to mark the model not available."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import io, json, os, sys, tempfile, time
from importlib.machinery import SourceFileLoader

root = tempfile.mkdtemp(prefix="sbmodel-")
stub = os.path.join(root, "fake-claude")
seen = os.path.join(root, "seen.jsonl")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, sys, time
RUNS = {"haiku": "claude-opus-5-5[1m]", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5[1m]"}
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")'); sys.exit(0)
model = sys.argv[sys.argv.index("--model") + 1]
conv = sys.argv[sys.argv.index("--session-id") + 1] if "--session-id" in sys.argv else \\
    sys.argv[sys.argv.index("--resume") + 1]
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
first = True
for line in sys.stdin:
    if not line.strip(): continue
    msg = json.loads(line)
    text = msg["message"]["content"][0]["text"]
    open(%r, "a").write(json.dumps({"model": model, "conv": conv, "resume": "--resume" in sys.argv,
                                    "text": text, "pid": os.getpid()}) + "\\n")
    if first:                                   # as the real one: at its first turn, before answering
        emit({"type": "system", "subtype": "init", "model": RUNS.get(model, model)})
        first = False
    if "slow" in text:                          # the API busy: a retry, then the words
        emit({"type": "system", "subtype": "api_retry"}); emit({"type": "system", "subtype": "api_retry"})
        time.sleep(0.4)
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}})
    emit({"type": "result", "is_error": False, "result": "answered by " + RUNS.get(model, model)})
''' % seen)
os.chmod(stub, 0o755)
os.environ["STANDBY_ROOT"] = root
os.environ["STANDBY_CLAUDE"] = stub
agentpath.guard()
ag = SourceFileLoader("ag", agentpath.AGENT).load_module()
err = io.StringIO()
sys.stderr = err
fails = []


def check(label, ok, d=""):
    sys.__stdout__.write("  %-66s %s\n" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        sys.__stdout__.write("     %s\n" % str(d)[:600])


def turns():
    try:
        return [json.loads(l) for l in open(seen)]
    except IOError:
        return []


def models():
    try:
        return json.load(open(os.path.join(root, "agent", "models.json")))
    except (IOError, ValueError):
        return {}


M1 = "meeting-20261005-180000"
text, rc = ag.engine_claude("[BRIEFING] the meeting so far", M1, "haiku", 30, kind="voice")
t = turns()
check("asked for haiku, it ran Opus: asked again on Sonnet, before answering",
      rc == 0 and text == "answered by claude-sonnet-5-5" and [r["model"] for r in t] == ["haiku", "sonnet"], (text, t))
check("  the question asked once more, in a new conversation (the first is dropped)",
      t[1]["text"] == t[0]["text"] and not t[1]["resume"] and t[1]["conv"] != t[0]["conv"], t)
eng = ag._LIVE_SESSION[(M1, "voice")]
check("  the engine knows what it really runs", eng.model == "haiku" and eng.asks == "sonnet"
      and eng.runs == "claude-sonnet-5-5" and ag.runs(M1, "voice", "haiku") == "claude-sonnet-5-5",
      (eng.model, eng.asks, eng.runs))
log = err.getvalue()
check("  logged: haiku not available, Sonnet asked for, then what runs",
      "voice engine for %s runs claude-opus-5-5[1m]  (haiku is not available here: asking for sonnet instead)" % M1 in log
      and "voice engine for %s runs claude-sonnet-5-5\n" % M1 in log, log)
m = models()
check("  kept in models.json: haiku replaced by Opus", m.get("haiku", {}).get("blocked") is True
      and m["haiku"]["runs"] == "claude-opus-5-5[1m]" and m.get("sonnet", {}).get("blocked") is False, m)
n = len(turns())
text, rc = ag.engine_claude("[VOICE] They said: hello", M1, "haiku", 30, kind="voice")
check("the next turn: the same process, nothing asked twice",
      rc == 0 and len(turns()) == n + 1 and turns()[-1]["pid"] == t[1]["pid"], turns()[-2:])

ag.SLOW_WORD = 0.3
text, rc = ag.engine_claude("[VOICE] They said: slow", M1, "haiku", 30, kind="voice")
slow = [l for l in err.getvalue().splitlines() if "first word took" in l]
check("a slow first word is logged with what came before it (here two API retries)",
      rc == 0 and len(slow) == 1 and "voice engine's first word took" in slow[0] and "(claude-sonnet-5-5)" in slow[0]
      and "system/api_retry" in slow[0] and "x2" in slow[0], slow)
text, rc = ag.engine_claude("[VOICE] They said: quick", M1, "haiku", 30, kind="voice")
check("  a quick one, or none at all, is not", len([l for l in err.getvalue().splitlines() if "first word took" in l]) == 1)

# A new engine, a fresh process from another run of the agent: Sonnet from the start.
ag._REPLACED.clear()
M2 = "meeting-20261005-190000"
n = len(turns())
ag.prewarm(M2, "haiku", "voice")
text, rc = ag.engine_claude("[VOICE] They said: hi", M2, "haiku", 30, kind="voice")
check("from models.json, a new engine never asks for haiku: Sonnet from the start",
      rc == 0 and [r["model"] for r in turns()[n:]] == ["sonnet"], turns()[n:])
check("  its warm-up says so", "voice engine warming up for %s (sonnet, haiku is not available)" % M2 in err.getvalue(),
      err.getvalue()[-400:])
start = [l for l in open(os.path.join(root, "agent", "engine.log")) if M2 in l]
check("  and engine.log too", start and "model=sonnet (haiku is not available)" in start[-1], start)

n = len(turns())
text, rc = ag.engine_claude("deep question", M2, "opus", 30, kind="meeting")
check("a model that runs as asked (opus): no second try, kept as available",
      rc == 0 and [r["model"] for r in turns()[n:]] == ["opus"] and models()["opus"]["blocked"] is False, turns()[n:])

# A meeting continued after the agent restarts: its conversation is resumed, not dropped.
ag._REPLACED.clear()
os.remove(os.path.join(root, "agent", "models.json"))
M3 = "meeting-20261005-200000"
ag.session_uuid(M3)                                  # a conversation already under way
n = len(turns())
text, rc = ag.engine_claude("typed question", M3, "haiku", 30, kind="meeting")
t3 = turns()[n:]
check("a resumed conversation asked again on Sonnet keeps it (resumed, not dropped)",
      rc == 0 and [r["model"] for r in t3] == ["haiku", "sonnet"] and all(r["resume"] for r in t3)
      and t3[0]["conv"] == t3[1]["conv"], t3)

# A week on, it is checked again: haiku may be allowed by then.
m = models()
m["haiku"]["at"] -= ag.RECHECK + 60
json.dump(m, open(os.path.join(root, "agent", "models.json"), "w"))
ag._REPLACED.clear()
check("a week on, haiku is asked for again (checked again)", ag.model_for("haiku") == "haiku")
check("  never in place of Sonnet itself, or a model it runs as asked",
      ag.model_for("sonnet") == "sonnet" and ag.model_for("opus") == "opus")

# Should Sonnet itself be replaced, there is nothing better to ask for: no loop.
ag.note_model("sonnet", "claude-opus-5-5[1m]")
check("Sonnet replaced too: it stays (nothing better), no loop", ag.model_for("sonnet") == "sonnet")

# The app's side: Settings marks it not available, through the transport.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import transport  # noqa: E402                      (STANDBY_ROOT is the temp root, above)
# Once: voice mode's model becomes Haiku, the new default. A saved Sonnet was the old default.
c = {"agent": {"mode": "local", "model": "opus", "voice_model": "sonnet"}}
check("migrate: a saved Sonnet (the old default) becomes Haiku, once", transport.migrate_voice_model(c) is True
      and c["agent"]["voice_model"] == "haiku" and c["voice_model_haiku"] is True, c)
c["agent"]["voice_model"] = "sonnet"
check("  never again: a Sonnet chosen after that stays", transport.migrate_voice_model(c) is False
      and c["agent"]["voice_model"] == "sonnet", c)
c = {"agent": {"voice_model": "opus"}}
check("  a model that wasn't the old default stays", transport.migrate_voice_model(c) and c["agent"]["voice_model"] == "opus", c)
c = {"voice_model": "sonnet", "host": "devbox"}
check("  an old flat config too", transport.migrate_voice_model(c) and c["voice_model"] == "haiku", c)
check("  and a new config's default is Haiku", transport.agent_settings({})["voice_model"] == "haiku")
json.dump({"agent": {"mode": "local", "model": "opus", "voice_model": "haiku"}}, open(transport.CONFIG, "w"))
json.dump({"haiku": {"runs": "claude-opus-5-5[1m]", "blocked": True, "at": int(time.time())},
           "sonnet": {"runs": "claude-sonnet-5-5", "blocked": False, "at": int(time.time())}},
          open(os.path.join(root, "agent", "models.json"), "w"))
ui = SourceFileLoader("ui", os.path.join(os.path.dirname(transport.__file__), "ui.py")).load_module()
check("the app: haiku not available, from the agent's models.json", ui.models_info() == {"unavailable": ["haiku"]},
      ui.models_info())
check("  the saved choice is kept (Sonnet runs in its place, the user sees why)",
      ui.get_settings()["voice_model"] == "haiku")
os.remove(os.path.join(root, "agent", "models.json"))
check("  none found yet: all available", ui.models_info() == {"unavailable": []})
# A call going on, its voice's model changed in Settings: how the switch went, plainly, for that call only.
real_live = ui.live_session
ui.live_session = lambda: "talk-1"
sw = os.path.join(root, "agent", "switch.json")
try:
    json.dump({"session": "talk-1", "wanted": "opus", "was": "sonnet", "ok": False, "error": "Opus isn't on this plan"},
              open(sw, "w"))
    check("Settings during a call: a refused switch says so, which model stays, and why",
          ui.models_info().get("switch") == {"wanted": "opus", "text": "This call couldn't switch to Opus, so it stays "
                                             "on Sonnet. Claude Code said: Opus isn't on this plan"}, ui.models_info())
    json.dump({"session": "talk-1", "wanted": "haiku", "was": "sonnet", "ok": True, "error": None}, open(sw, "w"))
    check("  a switch that went through says so", ui.models_info()["switch"]["text"] == "This call switched to Haiku.")
    ui.live_session = lambda: "talk-2"
    check("  another call's switch: nothing", "switch" not in ui.models_info(), ui.models_info())
    ui.live_session = lambda: None
    check("  no call going on: nothing", "switch" not in ui.models_info(), ui.models_info())
finally:
    ui.live_session = real_live
    os.remove(sw)

for e in list(ag._LIVE_SESSION.values()):
    e.stop(hard=True)
sys.stderr = sys.__stderr__
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
