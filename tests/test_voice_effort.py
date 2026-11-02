"""The voice's thinking (Settings > Voice, "Thinking"): --effort on the voice's Claude Code only, from config.json
agent.voice_effort, read once a call and kept for its turns and forks alike (a change mid-call would miss the prompt
cache); unset, high (Sonnet's best, passed explicitly, never Claude Code's own by default); "auto", no --effort, as
Claude Code is set; never on the meeting agent; never where this Claude Code has no
--effort. Settings saves it and Claude Code can set it by name. A stand-in Claude only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile
from importlib.machinery import SourceFileLoader

root = tempfile.mkdtemp(prefix="sbefforttest-")
home = os.path.join(root, "home")
os.makedirs(os.path.join(home, ".claude"))
stub = os.path.join(root, "fake-claude")
with open(stub, "w") as fh:
    fh.write('#!/usr/bin/env python3\nimport sys\nif "--help" in sys.argv:\n'
             '    print("--effort <level>  Effort level for the current session (low, medium, high, xhigh, max)")\n')
os.chmod(stub, 0o755)
os.environ.update(STANDBY_ROOT=root, STANDBY_CLAUDE=stub, HOME=home)
agent = SourceFileLoader("standby_agent", "plugin/bin/standby-agent").load_module()
agent.ensure_dirs()
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


def config(**agent_cfg):
    with open(os.path.join(root, "config.json"), "w") as fh:
        json.dump({"agent": agent_cfg}, fh)


def effort(argv):
    return argv[argv.index("--effort") + 1] if "--effort" in argv else None


def argv(session, kind="voice", turn=False):
    return agent.build_argv(session, "sonnet", True, "sid", stream=True, kind=kind, turn=turn)


config()
check("unset: --effort high, passed explicitly, never Claude Code's own", effort(argv("talk-20261101-090000")) == "high")
config(voice_effort="auto")
check("auto: no --effort, the voice as Claude Code is set", effort(argv("talk-20261101-093000")) is None)
config(voice_effort="low")
check("Low: the voice's Claude Code at --effort low", effort(argv("talk-20261101-100000")) == "low")
config(voice_effort="high")
check("  changed mid-call: that call keeps low, its turns and forks too (its prompt cache holds)",
      effort(argv("talk-20261101-100000")) == "low" and effort(argv("talk-20261101-100000", turn=True)) == "low")
check("  the next call: high", effort(argv("talk-20261101-110000")) == "high")
check("never the meeting agent", effort(argv("talk-20261101-120000", kind="meeting")) is None)
config(voice_effort="xhigh")
check("a value Settings doesn't offer: high", effort(argv("talk-20261101-130000")) == "high")
config(voice_effort="medium")
agent._CAPS.clear(); agent._CAPS.update(agent.claude_caps()); agent._CAPS["effort"] = False
check("a Claude Code without --effort: none passed", effort(argv("talk-20261101-140000")) is None)

print("Settings")
with open(os.path.join(home, ".claude", "settings.json"), "w") as fh:
    json.dump({"effortLevel": "low"}, fh)
sys.path.insert(0, "mac")
import ui  # noqa: E402
ui.log = lambda m: None
config()
check("Settings: High by default, whatever Claude Code has (here low)",
      ui.get_settings()["voice_effort"] == "high" and ui.get_settings()["voice_effort_inherit"] == "low")
check("  Medium saved as the agent's voice_effort; auto kept as auto",
      ui.set_settings({"voice_effort": "medium"})["settings"]["voice_effort"] == "medium"
      and json.load(open(os.path.join(root, "config.json")))["agent"]["voice_effort"] == "medium"
      and ui.set_settings({"voice_effort": "auto"})["settings"]["voice_effort"] == "auto"
      and json.load(open(os.path.join(root, "config.json")))["agent"]["voice_effort"] == "auto")
check("  anything else: ignored", ui.set_settings({"voice_effort": "max"})["settings"]["voice_effort"] == "auto")
check("  the page's fallback is High", 'setEffort(s.voice_effort||"high",true)' in ui.PAGE)
named = {x["name"]: x for x in ui.settings_named()["settings"]}
check("  by name from Claude Code: voice_thinking, auto, low, medium or high",
      named["voice_thinking"]["choices"] == ["auto", "low", "medium", "high"]
      and ui.set_named("voice_thinking", "Low")["value"] == "low" and ui.get_settings()["voice_effort"] == "low")
check("  the page: Thinking under the voice's model, three choices, saved with the rest", 'id="effseg"' in ui.PAGE and 'id="s_veffort"' in ui.PAGE
      and 'voice_effort:$("s_veffort").value' in ui.PAGE and ui.PAGE.index('id="s_vmodel"') < ui.PAGE.index('id="s_veffort"'))

if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
