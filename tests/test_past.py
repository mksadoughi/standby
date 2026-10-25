"""Standby opened on its own lists the past meetings and calls (GET /api/msg/past): only those, newest
first, a meeting with the names of who spoke (named, or recognized from before), each name once."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, sys, tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbpast-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-700:])


ui.SESSIONS.mkdir(parents=True, exist_ok=True)
for sid, names, known in (("meeting-20261017-100000", {"1": "Nadia", "2": "Theo"}, {"3": "Nadia", "4": "Ines"}),
                          ("meeting-20261019-100000", {}, {})):
    (ui.SESSIONS / sid).mkdir()
    (ui.SESSIONS / sid / "speakers.names.json").write_text(json.dumps({"names": names}))
    (ui.SESSIONS / sid / "speakers.known.json").write_text(json.dumps({"known": known}))


class Fake:
    def cards(self):
        return [{"kind": "meeting", "session": "meeting-20261017-100000", "at": 100},
                {"kind": "browser", "session": "browser-20261018-100000", "at": 200},
                {"kind": "call", "session": "talk-20261020-100000", "at": 400},
                {"kind": "meeting", "session": "meeting-20261019-100000", "at": 300}]


got = ui.past_sessions(Fake())
check("only meetings and calls, newest first", [c["session"][:4] for c in got] == ["talk", "meet", "meet"]
      and got[1]["at"] == 300, got)
check("a meeting names who spoke, each once", got[2]["names"] == ["Nadia", "Theo", "Ines"], got[2])
check("  none named yet: no names", got[1]["names"] == [], got[1])
check("a call has no names", "names" not in got[0], got[0])
# Each one's topic, for its row's title: the user's title, else Claude's short one, else "" (never its gist)
(ui.SESSIONS / "talk-20261020-100000").mkdir()
(ui.SESSIONS / "talk-20261020-100000" / "title.auto.txt").write_text("Snake game and pause test\n")
(ui.SESSIONS / "meeting-20261019-100000" / "title.auto.txt").write_text("Fare page numbers\n")
(ui.SESSIONS / "meeting-20261019-100000" / "title.txt").write_text("Fares, with Ines\n")
got = ui.past_sessions(Fake())
check("each one's topic: Claude's short title", got[0]["topic"] == "Snake game and pause test", got[0])
check("  the user's own title over Claude's", got[1]["topic"] == "Fares, with Ines", got[1])
check("  none yet: empty (the row then says plainly what it was)", got[2]["topic"] == "", got[2])
# A Claude Code session's name, as Claude Code shows it: /rename, else Claude's title, else the last prompt.
tx = root / "cc.jsonl"
ui._cc_transcript = lambda cc: tx if cc == "c1" else None
tx.write_text("\n".join(json.dumps(r) for r in [{"type": "last-prompt", "lastPrompt": "fix the   guest TTL"}]) + "\n")
check("a session's name: its last prompt while it has nothing better", ui.cc_title("c1") == "fix the guest TTL", ui.cc_title("c1"))
with open(tx, "a") as f:
    f.write(json.dumps({"type": "ai-title", "aiTitle": "Guest TTL fix"}) + "\n")
check("  Claude's own title once there is one (read again when the transcript grows)", ui.cc_title("c1") == "Guest TTL fix", ui.cc_title("c1"))
with open(tx, "a") as f:
    f.write(json.dumps({"type": "custom-title", "customTitle": "TTL work"}) + "\n" + json.dumps({"type": "ai-title", "aiTitle": "Later"}) + "\n")
check("  the user's own name (/rename) wins", ui.cc_title("c1") == "TTL work", ui.cc_title("c1"))
check("  no transcript: no name", ui.cc_title("c2") == "" and ui.cc_title(None) == "")
import messenger  # noqa: E402
m = messenger.Messenger.__new__(messenger.Messenger)
m.ui = ui
check("the navigator gets that name, never \"Agent\"", m._name({"session": "c1"}) == "TTL work"
      and m._name({"session": "c2"}) == "Claude Code session" and m._title({"session": "c2"}) == "")

print("FAILURES: %d" % len(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
