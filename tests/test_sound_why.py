"""Why the meeting's sound isn't heard, in exact words, and asking for it.
macOS never says whether it allows System Audio Recording, so the app goes by what this
build did: its tap heard the Mac (allowed), a capture heard only silence while an app
played (refused), it brought macOS's prompt up (asked), or none of these (never asked);
and an earlier build that heard it means macOS's switch is that one's. Fakes only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, socket, sys, tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbwhy-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); os.environ["STANDBY_UI_PORT"] = str(sock.getsockname()[1]); sock.close()
sys.path.insert(0, "mac")
from native import app  # noqa: E402  (this build, and what it was let do)
from native.macos import app as mac_app  # noqa: E402
from native.macos import permissions as mac_permissions  # noqa: E402
from native import permissions  # noqa: E402
import ui  # noqa: E402
ui.log = lambda m: None
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


grants = root / app.GRANTS
check("in a test, no build mark (never codesign)", app.build_mark() is None)
mac_app._BUILD[:] = ["NEWBUILD"]                     # this install's Standby.app, made up

# What macOS lets this build do, as far as it can be told (native.permissions.sound_state).
check("nothing known: never asked", mac_permissions.sound_state() == "not_asked")
app.note_grant("sound_asked")
check("  its prompt brought up: asked (the answer can't be read)", mac_permissions.sound_state() == "asked")
app.note_grant("sound")
check("  its tap heard the Mac: allowed", mac_permissions.sound_state() == "allowed")
g = json.loads(grants.read_text())
g["sound_refused"] = {"at": g["sound"]["at"] + 5, "build": "NEWBUILD"}
grants.write_text(json.dumps(g))
check("  a capture since heard only silence while an app played: denied", mac_permissions.sound_state() == "denied")
g["sound"]["at"] = g["sound_refused"]["at"] + 5
grants.write_text(json.dumps(g))
check("  and heard again after that: allowed again", mac_permissions.sound_state() == "allowed")
grants.write_text(json.dumps({"sound": {"at": 1, "build": "OLDBUILD"}, "sound_asked@dictation": {"at": 1, "build": "NEWBUILD"}}))
check("  an older build's, or Standby Dictation's, are not this one's", mac_permissions.sound_state() == "not_asked")

# Why the page says what it says.
grants.unlink()
check("nothing known: never allowed, the words", ui.sound_why() == "never"
      and ui.SOUND_WHY["never"] == "Allow Standby under System Audio Recording Only, in Privacy & Security")
grants.write_text(json.dumps({"sound": {"at": 1, "build": "OLDBUILD"}}))
check("an earlier build heard it, this one doesn't: macOS's switch is the old one's", ui.sound_why() == "stale"
      and ui.SOUND_WHY["stale"] == ("Standby is switched on, but for an older copy of the app. Reset it: run "
                                    "tccutil reset AudioCapture dev.standby.app, then allow it again"),
      ui.sound_why())
refused = [{"kind": "mic", "name": "MacBook Pro Microphone"},
           {"kind": "meeting", "name": "Zoom", "cause": "permission", "reason": "allow Standby to hear it"}]
check("  the meeting's sound refused: marked as an older copy's, for the chip to say so",
      [s.get("stale") for s in ui.with_sound_why(refused)] == [None, True], ui.with_sound_why(refused))
grants.write_text(json.dumps({"sound": {"at": 1, "build": "NEWBUILD"}}))
check("this build heard it: not an older copy's (no history to tell): the chip asks the user instead",
      ui.sound_why() == "never" and ui.with_sound_why(refused)[1]["stale"] is False)

# The user says it's switched on: Standby resets its own entry, only that, then asks macOS
# again. A stand-in for tccutil: never the real one.
tcc_args = root / "tccutil.args"
fake_tcc = root / "fake-tccutil"
fake_tcc.write_text('#!/bin/sh\necho "$@" >> "%s"\n[ -f "%s.fail" ] && { echo "tccutil: no" >&2; exit 1; }\n'
                    'echo "Successfully reset AudioCapture approval status for $3"\n' % (tcc_args, fake_tcc))
fake_tcc.chmod(0o755)
real = ui.TCCUTIL
check("the real tccutil is never run in a test", real == ["/usr/bin/tccutil"]
      and ui.sound_reset()["error"].startswith("not in a test") and not tcc_args.exists())
ui.TCCUTIL = [str(fake_tcc)]
asked = []
permissions.ask_sound = lambda done=None: asked.append(1)
r = ui.sound_reset()
check("reset: Standby's own entry only, by its bundle id", tcc_args.read_text().split() == ["reset", "AudioCapture", "dev.standby.app"],
      tcc_args.read_text())
check("  then macOS asks again, and the user is told what comes",
      r == {"state": "asked", "reason": "Allow Standby when macOS asks"} and asked == [1], (r, asked))
check("  the bundle id is the one install.sh gives Standby.app",
      '"CFBundleIdentifier": "%s"' % ui.APP_ID in open("install.sh").read())
(root / "fake-tccutil.fail").write_text("1")
r = ui.sound_reset()
check("reset refused by macOS: said, with the command to run by hand, and nothing asked",
      r["state"] == "failed" and r["error"].endswith("tccutil reset AudioCapture dev.standby.app") and asked == [1], r)
(root / "fake-tccutil.fail").unlink()

check("every line is ASCII (they reach the log, and launchd gives no locale)",
      all(t == t.encode("ascii", "replace").decode() for t in ui.SOUND_WHY.values()), ui.SOUND_WHY)

# What Allow for the meeting audio says (POST /api/sound/permission): macOS's prompt once a
# build (a short capture brings it up), then System Settings once a launch; nothing once heard.
STATE = ["not_asked"]
permissions.sound_state = lambda: STATE[0]
opened, asked = [], []
permissions.open_sound_settings = lambda: opened.append(1)
permissions.ask_sound = lambda done=None: (asked.append(1), STATE.__setitem__(0, "asked"))
ui.SETTINGS_OPENED[:] = []
ui._SOUND_CHECKS.clear()
state, reason = ui.sound_permission()
check("never asked: macOS's own prompt, at once (nothing to quit and reopen)",
      state == "asked" and asked == [1] and reason == "Allow Standby when macOS asks", (state, reason))
state, reason = ui.sound_permission()
state2, reason2 = ui.sound_permission()
check("  again: never the prompt twice; System Settings by itself once a launch, then just the line",
      state == state2 == "settings" and asked == [1] and opened == [1] and reason == ui.SOUND_WHY["never"],
      (state, asked, opened, reason))
STATE[0] = "allowed"
check("once heard: granted, nothing asked", ui.sound_permission() == ("granted", "") and asked == [1])

# What the band in Claude Code is told (access): refused by the recorder's finding, or by
# what this build has seen; asked and never heard: no warning (macOS's answer can't be read).
ui.live_status = lambda sid: {"sources": [{"kind": "meeting", "name": "Zoom"}]}
ui.mic_access = lambda: "authorized"
for state, want in (("not_asked", "ask"), ("asked", "allowed"), ("allowed", "allowed"), ("denied", "denied")):
    STATE[0] = state
    ui._SOUND_CHECKS.clear()
    got = ui.access("meeting-20261030-100000", "meeting")["meeting_audio"]
    check("  the band, %s: %s" % (state, want), got == want, got)
ui.live_status = lambda sid: {"sources": [{"kind": "meeting", "name": "Zoom", "cause": "permission"}]}
STATE[0] = "allowed"
ui._SOUND_CHECKS.clear()
check("  the recorder's own finding comes first", ui.access("meeting-20261030-100000", "meeting")["meeting_audio"] == "denied")

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
