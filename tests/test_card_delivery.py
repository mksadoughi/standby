"""A kept card whose summary was held in the agents layer's outbox: when it finally goes,
the card records it (delivered_at, no longer queued); when it has nowhere to go (its
agent left Standby), the card is pending again, saying why, never "in memory" for
something that never arrived. Keep hands the card's session to the delivery, so the
outbox knows whose card it is. Stand-ins for the agents layer."""
import agentpath  # noqa: F401  (first: never the real ~/.standby, and no child may crash)
import json, os, socket, sys, tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbcarddel-"))
os.environ["STANDBY_ROOT"] = str(root)
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); os.environ["STANDBY_UI_PORT"] = str(sock.getsockname()[1]); sock.close()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
ui.log = lambda m: None

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-700:])


events = []
ui.MSG["event"] = lambda e, d: events.append((e, d))
ui.MSG["agent"] = lambda ident: {"id": ident, "name": "Nadia"} if ident == "a-nadia" else None


def card(sid, **kw):
    folder = ui.SESSIONS / sid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"kind": "talk", "started": 1000.0}))
    (folder / "agent.json").write_text(json.dumps({"agent": "a-nadia", "name": "Nadia", "copy": True}))
    (folder / "card.json").write_text(json.dumps(dict({"points": ["Ship it Friday"], "at": 1000.0, "duration_s": 240,
                                                       "copy": True, "memory": "pending", "delivered_at": None}, **kw)))
    return folder / "card.json"


read = lambda p: json.loads(p.read_text())

check("the hooks are registered for the agents layer",
      ui.MSG.get("on_delivered") == ui.card_delivered and ui.MSG.get("on_dropped") == ui.card_dropped)

print("Keep, when the agent can't be reached now")
path = card("orion-20261016-0900")
asked = []
ui.MSG["deliver"] = lambda aid, text, session=None: asked.append((aid, text, session)) and False
r = ui.card_memory("orion-20261016-0900", True)
check("the delivery is given the card's session, so the outbox knows whose card it is",
      asked and asked[-1][0] == "a-nadia" and asked[-1][2] == "orion-20261016-0900"
      and asked[-1][1].startswith("[CALL SUMMARY]"), asked)
check("  kept, queued, not delivered yet", r.get("memory") == "kept" and r.get("queued") is True
      and not r.get("delivered_at"), r)

print("the outbox delivers it later")
events.clear()
ui.MSG["on_delivered"]("a-nadia", "orion-20261016-0900")
c = read(path)
check("delivered_at set, queued cleared, still kept", c["delivered_at"] and "queued" not in c and c["memory"] == "kept", c)
check("  and the page is told", ("card", {"session": "orion-20261016-0900"}) in events, events)
path2 = card("orion-20261016-0910", memory="kept", queued=True)
ui.card_delivered("a-nadia", "[MEETING SUMMARY] Meeting: Sync (12 min), recorded by Standby. "
                             "Transcript: standby-ctx --session orion-20261016-0910\n- Ship it")
check("an entry kept before the session went with it: found by the session in its text",
      read(path2)["delivered_at"] and "queued" not in read(path2), read(path2))

print("its agent left Standby before it could go")
path3 = card("orion-20261016-0920", memory="kept", queued=True)
events.clear()
ui.MSG["on_dropped"]("a-nadia", "orion-20261016-0920", "Its agent left Standby before it could be sent")
c = read(path3)
check("pending again, not queued, never delivered, saying why",
      c["memory"] == "pending" and "queued" not in c and not c.get("delivered_at")
      and c["memory_note"] == "Its agent left Standby before it could be sent", c)
check("  and the page is told", ("card", {"session": "orion-20261016-0920"}) in events, events)

print("nothing that isn't a card is touched")
for which in ("no-such-card-20261016-0930", "../../etc", "plain text without a session", None):
    ui.card_delivered("a-nadia", which)
    ui.card_dropped("a-nadia", which, "x")
check("an unknown session, a path or plain text: ignored, no folder made",
      not (ui.SESSIONS / "no-such-card-20261016-0930").exists())

print()
print("%d failed" % len(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
