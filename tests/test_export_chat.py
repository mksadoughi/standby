"""A call's Copy transcript and Download as Markdown carry what was said, as the window shows it: no blank
"Claude:" lines for the rows that hold what work brought back (their text is empty), no old "[HANDOFF]"
markers or the app's old stock "I'm on it." on a hand-off; a hand-off's own words, when the voice said some,
stay. (talk-20261023-200116 had blank "Claude:" rows.) A scratch STANDBY_ROOT only."""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import json, os, sys, tempfile, time
from pathlib import Path

root = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbexport-")))
os.environ.update(STANDBY_ROOT=str(root))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-900:])


SID = "talk-20261023-200116"
sd = ui.SESSIONS / SID
sd.mkdir(parents=True)
t = time.time() - 600
(sd / "meta.json").write_text(json.dumps({"started": t, "kind": "talk", "title": "Planner"}))
rows = [{"role": "ai", "text": "Hey, I'm here.", "at": t + 1, "voice": True},
        {"role": "me", "text": "Run the routing cache tests.", "at": t + 2, "voice": True},
        {"role": "ai", "text": "Sure, I'll run them now.", "handoff": "Run the routing cache tests", "at": t + 3, "voice": True},
        {"role": "ai", "text": "", "result": "All 112 pass. `test_reroute` is slow.", "reason": "done", "at": t + 4, "voice": True},
        {"role": "ai", "text": "The tests are done: all 112 pass.", "at": t + 5, "voice": True},
        {"role": "ai", "text": "I'm on it.", "handoff": "Check the fares cache", "at": t + 6, "voice": True},
        {"role": "ai", "text": "[HANDOFF] An older call's task", "at": t + 7},
        {"role": "ai", "text": "  ", "result": "Noted", "reason": "aborted", "at": t + 8, "voice": True}]
(sd / "chat.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
for fmt in ("md", "txt"):
    out = ui.export_text(SID, fmt)
    lines = [l for l in out.splitlines() if l.strip()]
    claude = [l for l in lines if "Claude:" in l]
    check(fmt + ": no blank Claude lines", all(l.split("Claude:", 1)[1].strip() for l in claude), claude)
    check(fmt + ":   what was said, in order, once", [l.split(":", 1)[1].strip().strip("*").strip() for l in claude + [l for l in lines if "You:" in l]][:3]
          == ["Hey, I'm here.", "Sure, I'll run them now.", "The tests are done: all 112 pass."], lines)
    check(fmt + ":   no stock \"I'm on it.\", no [HANDOFF], nothing work brought back", "I'm on it." not in out and "HANDOFF" not in out and "test_reroute" not in out, out)
check("a row is said: words, not a result, not a marker", ui.said_row({"text": "Hi"}) and not ui.said_row({"text": "", "result": "x"})
      and not ui.said_row({"text": "I'm on it.", "handoff": "x"}) and ui.said_row({"text": "I'm on it."}))
print("FAILURES: %d" % len(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
