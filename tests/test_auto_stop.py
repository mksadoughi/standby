"""A meeting left recording with nothing said stops itself after auto_stop_min minutes (15 unless
set; 0 never), as Stop does, and says so in its own record. "Said" is a transcribed line (the
recorder heard speech), so a quiet room or a fan never keeps it going. Calls aren't touched."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json
import os
import sys
import tempfile
import time

root = tempfile.mkdtemp(prefix="sbautostop-")
os.environ["STANDBY_ROOT"], os.environ["STANDBY_NO_DEVICES"] = root, "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label.strip())
        if detail != "":
            print("     %s" % (detail,))


live, stopped = [None], []
ui.live_session = lambda *a, **k: live[0]
ui.stop_session = lambda *a, **k: (stopped.append(("stop", live[0])), live.__setitem__(0, None), {"ok": True})[2]
ui.mac_end = lambda sid, now=None, background=True: stopped.append(("end", sid)) or {"ok": True}


class FakeLite(object):
    def stop(self, b):
        stopped.append(("lite", b["session"]))
        return {"ok": True}


ui.lite = lambda: FakeLite()


def meeting(sid, last_line_ago, kind="meeting", bound=None):
    d = ui.SESSIONS / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"kind": kind, "started": time.time() - 4000}))
    (d / "transcript.jsonl").write_text(json.dumps({"t": 1, "text": "Hello."}) + "\n")
    t = time.time() - last_line_ago
    os.utime(d / "transcript.jsonl", (t, t))
    if bound:
        (d / "agent.json").write_text(json.dumps(bound))
    ui.READY.write_text(sid + "\n")
    os.utime(ui.READY, (time.time() - 4000, time.time() - 4000))      # the recorder started long ago
    live[0] = sid
    return d


check("auto_stop: 15 minutes unless set", ui.get_settings()["auto_stop_min"] == 15)
d = meeting("meeting-20261020-210000", 14 * 60)
check("a line 14 minutes ago: it keeps recording", ui.silence_stop() is None and stopped == [])
os.utime(d / "transcript.jsonl", (time.time() - 15 * 60 - 5,) * 2)
check("nothing said for 15 minutes: it stops itself, as Stop does", ui.silence_stop() == "meeting-20261020-210000"
      and stopped == [("stop", "meeting-20261020-210000")], stopped)
check("  and says so in its own record", ui.auto_stopped("meeting-20261020-210000") == "Stopped after 15 minutes of silence"
      and json.loads((d / ui.AUTO_STOPPED).read_text())["minutes"] == 15)
check("  a meeting that didn't stop itself says nothing", ui.auto_stopped("meeting-20261020-200000") is None)

stopped.clear()
d = meeting("meeting-20261020-220000", 20 * 60)
os.utime(ui.READY, None)                                     # the recorder started again (resumed) just now
check("resumed a moment ago, its last line older: the run counts from the resume", ui.silence_stop() is None and stopped == [])

stopped.clear()
meeting("meeting-20261020-230000", 16 * 60, bound={"agent": "lite:0f3c2a77-1111-4222-8333-944455556666", "started": time.time() - 4000})
check("a meeting from Claude Code: ended the Lite way (its band told, its summary made)", ui.silence_stop() == "meeting-20261020-230000"
      and stopped == [("lite", "meeting-20261020-230000")], stopped)
stopped.clear()
meeting("meeting-20261020-231000", 16 * 60, bound={"agent": "a-planner", "started": time.time() - 4000})
check("an agent's meeting (Add your Mac): ended with its card", ui.silence_stop() == "meeting-20261020-231000"
      and stopped == [("end", "meeting-20261020-231000")], stopped)

stopped.clear()
meeting("talk-20261020-232000", 60 * 60, kind="talk")
check("a call is left alone (it ends on its own, sooner)", ui.silence_stop() is None and stopped == [])

stopped.clear()
meeting("meeting-20261020-233000", 60 * 60)
check("set to 0: never", ui.set_named("auto_stop", "0").get("ok") and ui.get_settings()["auto_stop_min"] == 0
      and ui.silence_stop() is None and stopped == [])
check("set by name from Claude Code, in minutes", ui.set_named("auto_stop", "30") == {"ok": True, "name": "auto_stop", "value": "30"}
      and ui.get_settings()["auto_stop_min"] == 30)
check("  listed with the others", any(s["name"] == "auto_stop" and s["value"] == "30" for s in ui.settings_named()["settings"]))
check("  a value that isn't one: refused in words", "number of minutes" in ui.set_named("auto_stop", "soon").get("error", "")
      and "number of minutes" in ui.set_named("auto_stop", "9999").get("error", ""))
check("  nor through Settings", "error" in ui.set_settings({"auto_stop_min": "x"}) and ui.get_settings()["auto_stop_min"] == 30)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
