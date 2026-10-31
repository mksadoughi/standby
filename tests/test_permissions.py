"""Settings > Permissions: what macOS lets Standby (and Standby Dictation) use, each with its status now and at
most one button. The rules (permsview), then the app's endpoints with macOS and Standby Dictation faked:
nothing is asked of the real macOS, and System Settings is never opened."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, sys, tempfile, types
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbperm-"))
os.environ["STANDBY_ROOT"] = str(root)
os.environ.pop("STANDBY_NO_DEVICES", None)
sys.path.insert(0, "mac")
import permsview as pv  # noqa: E402
import ui  # noqa: E402
import dictation as dct  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


print("the rules")
check("Standby's mic: allowed (or heard by this build), not asked, or off", (pv.call_mic("authorized"), pv.call_mic("undetermined"),
      pv.call_mic("undetermined", heard=True), pv.call_mic("denied"), pv.call_mic("restricted")) == ("allowed", "ask", "allowed", "off", "off"))
check("the meeting's sound: asked with no sign of a no counts as allowed; refused is off, or needs a reset when an older copy had it",
      (pv.sound("allowed"), pv.sound("asked"), pv.sound("not_asked"), pv.sound("denied"), pv.sound("denied", stale=True))
      == ("allowed", "allowed", "ask", "off", "stale"))
check("Standby Dictation's, from what it last wrote; Input Monitoring not needed for F5",
      pv.dictation({"mic": "allowed", "post": True, "listen": False}, needs_listen=False) == {"mic": "allowed", "post": "allowed", "listen": "notneeded"}
      and pv.dictation({"mic": "denied", "post": False, "listen": False}, needs_listen=True) == {"mic": "off", "post": "off", "listen": "off"}
      and pv.dictation({"mic": "ask"}, True)["mic"] == "ask")
check("  not running: unknown, nothing to press", pv.dictation(None, True, running=False) == {"mic": "unknown", "post": "unknown", "listen": "unknown"})
v = pv.view({"mic": "allowed", "sound": "ask"}, {"mic": "off", "post": "stale", "listen": "notneeded"})
rows = {(r["group"], r["key"]): r for g in v["groups"] for r in g["rows"]}
check("the view: two groups, Calls and meetings then Dictation (Standby Dictation), in the drafts' order",
      [(g["title"], g.get("note")) for g in v["groups"]] == [("Calls and meetings", None), ("Dictation", "Standby Dictation")]
      and [r["key"] for g in v["groups"] for r in g["rows"]] == ["mic", "sound", "mic", "post", "listen"], v)
check("  each status its words and at most one button: Allowed none, Not asked Allow, Off and stale Open Settings, Not needed none",
      [(r["label"], r["button"]) for r in rows.values()] == [("Allowed", None), ("Not asked", "Allow"), ("Off", "Open Settings"),
                                                             ("Needs a reset", "Open Settings"), ("Not needed", None)], rows)
check("  stale: the line saying so", rows[("dictation", "post")]["warn"] == pv.STALE_LINE and "warn" not in rows[("calls", "mic")])
check("  the names macOS and Standby use: Meeting sound, System Audio Recording", rows[("calls", "sound")]["name"] == "Meeting sound"
      and "System Audio Recording" in rows[("calls", "sound")]["line"])
check("  what needs the user: off and stale only (not asked doesn't nag)", v["need"] == 2)

print("the app")
fake = {"mic": "authorized", "sound": "not_asked", "why": "never"}
ui.mic_access = lambda: fake["mic"]
ui._sound_state = lambda fresh=False: fake["sound"]
ui.sound_why = lambda: fake["why"]
pid = {"pid": None}
dct.running_pid = lambda: pid["pid"]
got = ui.permissions_info()
check("Standby Dictation off: its rows unknown; Standby's own read from macOS", [r["status"] for g in got["groups"] for r in g["rows"]]
      == ["allowed", "ask", "unknown", "unknown", "notneeded" if not dct.needs(dct.settings(), "listen") else "unknown"], got)
pid["pid"] = 4242
Path(dct.STATUS).parent.mkdir(parents=True, exist_ok=True)
Path(dct.STATUS).write_text(json.dumps({"pid": 4242, "permissions": {"mic": "allowed", "post": False, "listen": False}}))
fake.update(sound="denied", why="stale")
got = ui.permissions_info()
st = {(r["group"], r["key"]): r["status"] for g in got["groups"] for r in g["rows"]}
check("running: its own word; the meeting's sound on for an older copy: needs a reset; counted",
      st[("dictation", "mic")] == "allowed" and st[("dictation", "post")] == "off" and st[("calls", "sound")] == "stale"
      and got["need"] == 2, (st, got["need"]))
opened, asked, said = [], [], []
ui.OPEN = ["echo"]
ui.subprocess = types.SimpleNamespace(run=lambda cmd, **kw: opened.append(cmd[-1]), DEVNULL=None, SubprocessError=Exception)
ui.permissions = types.SimpleNamespace(ask_mic=lambda answered: asked.append("mic"))
ui.sound_permission = lambda: asked.append("sound")
ui.dictation_action = lambda b: said.append(b) or {"ok": True}
r = ui.permissions_act({"group": "calls", "key": "sound"})
check("Open Settings on the meeting's sound: System Settings at System Audio Recording, nothing asked", opened == [ui.SOUND_PANE] and asked == []
      and r["ok"], (opened, r))
fake.update(mic="undetermined")
ui.permissions_act({"group": "calls", "key": "mic"})
check("Allow on the mic: macOS asks (its own prompt), on the click", asked == ["mic"])
fake.update(sound="not_asked")
ui.permissions_act({"group": "calls", "key": "sound"})
check("  Allow on the meeting's sound: macOS asks", asked == ["mic", "sound"])
ui.permissions_act({"group": "dictation", "key": "post"})
check("Standby Dictation's: its pane, through Standby Dictation", said == [{"action": "open", "key": "post"}], said)
r = ui.permissions_act({"group": "calls", "key": "nonsense"})
check("  a row with nothing to press, or none at all: nothing done", r["ok"] and len(opened) == 1 and len(said) == 1)
page = ui.PAGE
check("the page: Permissions in Settings' list between Dictation and Usage, with its count; its pane", page.index('data-p="dictation" onclick')
      < page.index('data-p="permissions" onclick') < page.index('data-p="usage" onclick') and 'id="permneed"' in page
      and '<div class="pane" data-p="permissions">' in page and "permissions" in ui.SETTINGS_PANES)

if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
