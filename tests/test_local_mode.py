"""Local mode end to end: no ssh anywhere. The shipper delivers transcript as
local files, agentctl starts the agent from this repo, a question is answered."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, threading, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sblocal-"))
stub = root / "fake-claude"
stub.write_text('''#!/usr/bin/env python3
import json, sys
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if not line.strip(): continue
    q = json.loads(line)["message"]["content"][0]["text"]
    emit({"type": "result", "is_error": False,
          "result": "." if q.startswith("[TRANSCRIPT]") else "LOCAL-ANSWER sees: " + q[-60:]})
''')
stub.chmod(0o755)
os.environ.update(STANDBY_ROOT=str(root), STANDBY_CLAUDE=str(stub))
(root / "config.json").write_text(json.dumps({"agent": {"mode": "local"}}))
sys.path.insert(0, "mac")
import transport, agentctl  # noqa
from importlib.machinery import SourceFileLoader
fails = []
def check(label, ok, d=""):
    print("  %-56s %s" % (label, "OK" if ok else "FAIL")); ok or (fails.append(label), print("     " + str(d)))

t = transport.for_config()
check("config selects local mode", t.mode == "local", t)
check("a config an older Standby saved with an SSH host: this Mac all the same",
      transport.agent_settings({"host": "box", "agent": {"mode": "remote", "host": "box"}})["mode"] == "local"
      and transport.for_config({"agent": {"mode": "remote", "host": "box"}}).mode == "local")

# recorder shipper through the local transport
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
src = root / "sessions" / "s1" / "transcript.jsonl"; src.parent.mkdir(parents=True)
src.write_text(json.dumps({"t": 1, "end": 2, "speaker": "Room", "text": "hello local"}) + "\n")
sh = sb.Shipper({"agent": {"mode": "local"}}, "s1", src)
check("announce writes meta + CURRENT locally", sh.announce({"session": "s1"}) and
      (root / "live" / "CURRENT").read_text().strip() == "s1")
sb._stop.clear(); sh.start(); time.sleep(1.0)
with open(src, "a") as fh:
    fh.write(json.dumps({"t": 3, "end": 4, "speaker": "Room", "text": "second line"}) + "\n")
time.sleep(1.0); sb._stop.set(); sh.join(timeout=5)
live = (root / "live" / "s1.jsonl").read_text()
check("transcript delivered to live/ without ssh", "hello local" in live and "second line" in live, live)
check("nothing duplicated", live.count("hello local") == 1, live)
sh.ship_file("error", "boom")
check("error file shipped locally", (root / "live" / "s1.error").read_text() == "boom")

# agent started locally, question answered
ok, msg = t.ensure_agent()
check("agent starts on this Mac from the repo", ok, msg)
ans = agentctl.ask("what did I say?", err=None)
check("question answered by local Claude Code", ans.startswith("LOCAL-ANSWER"), ans)
check("answer came from the live session", "hello local" in ans or "what did I say" in ans, ans)
checks = t.check()
check("diagnostics find the agent and claude", all(ok for ok, label, _ in checks[:2]), checks)
subprocess.run(t.agent_cmd(["stop"]), capture_output=True)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails)); sys.exit(1 if fails else 0)
