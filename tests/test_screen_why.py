"""Why the screen can't be recorded, in exact words.
macOS can't be read without asking, so the app goes by what this build did: its
capture heard the Mac (allowed the sound only), or an earlier build had the screen
(macOS kept that old one in the list), or neither (never allowed). Fakes only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, socket, sys, tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbwhy-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sock = socket.socket(); sock.bind(("127.0.0.1", 0)); os.environ["STANDBY_UI_PORT"] = str(sock.getsockname()[1]); sock.close()
sys.path.insert(0, "mac")
from native.macos import appaudio as meeting_audio  # noqa: E402  (the Mac's capture itself)
from native import app  # noqa: E402  (this build, and what it was let do)
from native.macos import app as mac_app  # noqa: E402
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
check("nothing known: never allowed, the words as before", ui.screen_why() == "never"
      and ui.SCREEN_WHY["never"] == "Allow Screen Recording, then quit and reopen Standby")

app.note_grant("heard")
g = json.loads(grants.read_text())
check("its capture heard the Mac: noted, with this build", g["heard"]["build"] == "NEWBUILD" and g["heard"]["at"] > 0, g)
check("  so it has the sound only: the top list", ui.screen_why() == "audio_only"
      and ui.SCREEN_WHY["audio_only"].startswith("Standby can hear the Mac but not see the screen")
      and "the top list" in ui.SCREEN_WHY["audio_only"], ui.screen_why())

grants.write_text(json.dumps({"heard": {"at": 1, "build": "OLDBUILD"}, "screen": {"at": 1, "build": "OLDBUILD"}}))
check("an earlier build had the screen, this one doesn't: macOS kept the old one", ui.screen_why() == "stale"
      and ui.SCREEN_WHY["stale"] == ("Standby is switched on, but for an older copy of the app. Reset it: run "
                                     "tccutil reset ScreenCapture dev.standby.app, then allow it again"),
      ui.screen_why())
refused = [{"kind": "mic", "name": "MacBook Pro Microphone"},
           {"kind": "meeting", "name": "Zoom", "cause": "permission", "reason": "allow Standby to hear it"}]
check("  the meeting's sound refused: marked as an older copy's, for the chip to say so",
      [s.get("stale") for s in ui.with_screen_why(refused)] == [None, True], ui.with_screen_why(refused))
grants.write_text(json.dumps({"screen": {"at": 1, "build": "NEWBUILD"}}))
check("this build had it and doesn't now (turned off): allowed again, as ever", ui.screen_why() == "never")
check("  not marked as an older copy's then (no history to tell): the chip asks the user instead",
      ui.with_screen_why(refused)[1]["stale"] is False)

# The user says it's switched on (the case with no history, 2026-10-12): Standby resets its
# own entry, only that, then asks macOS again. A stand-in for tccutil: never the real one.
tcc_args = root / "tccutil.args"
fake_tcc = root / "fake-tccutil"
fake_tcc.write_text('#!/bin/sh\necho "$@" >> "%s"\n[ -f "%s.fail" ] && { echo "tccutil: no" >&2; exit 1; }\n'
                    'echo "Successfully reset ScreenCapture approval status for $3"\n' % (tcc_args, fake_tcc))
fake_tcc.chmod(0o755)
real = ui.TCCUTIL
check("the real tccutil is never run in a test", real == ["/usr/bin/tccutil"]
      and ui.screen_reset()["error"].startswith("not in a test") and not tcc_args.exists())
ui.TCCUTIL = [str(fake_tcc)]
asked = []
permissions.ask_screen = lambda: asked.append(1)
ui.SNAP_ASKED.unlink() if ui.SNAP_ASKED.exists() else None
r = ui.screen_reset()
check("reset: Standby's own entry only, by its bundle id", tcc_args.read_text().split() == ["reset", "ScreenCapture", "dev.standby.app"],
      tcc_args.read_text())
check("  then macOS asks again, and the user is told what comes", r == {"state": "asked",
      "reason": "Allow Standby when macOS asks, then quit and reopen it"} and asked == [1], (r, asked))
check("  noted as asked: a refusal after it opens System Settings, not the prompt again", ui.SNAP_ASKED.exists())
check("  the bundle id is the one install.sh gives Standby.app",
      '"CFBundleIdentifier": "%s"' % ui.APP_ID in open("install.sh").read())
(root / "fake-tccutil.fail").write_text("1")
r = ui.screen_reset()
check("reset refused by macOS: said, with the command to run by hand, and nothing asked",
      r["state"] == "failed" and r["error"].endswith("tccutil reset ScreenCapture dev.standby.app") and asked == [1], r)
(root / "fake-tccutil.fail").unlink()

grants.unlink()
ui._SNAP_CHECKS.clear()
permissions.screen_allowed = lambda: True
check("allowed now: noted for this build", ui._snap_permitted(fresh=True)
      and json.loads(grants.read_text())["screen"]["build"] == "NEWBUILD")

check("every line is ASCII (they reach the log, and launchd gives no locale)",
      all(t == t.encode("ascii", "replace").decode() for t in ui.SCREEN_WHY.values()), ui.SCREEN_WHY)

# What Allow for the meeting audio says (POST /api/screen/permission).
permissions.screen_allowed = lambda: False
ui._SNAP_CHECKS.clear()
ui.SNAP_ASKED.parent.mkdir(parents=True, exist_ok=True)
ui.SNAP_ASKED.write_text("1\n")
app.note_grant("heard")
opened = []
permissions.open_screen_settings = lambda: opened.append(1)
ui.SETTINGS_OPENED[:] = []
state, reason = ui.screen_permission(confirm=True)
state2, reason2 = ui.screen_permission(confirm=True)
check("the exact line, and System Settings by itself once a launch, then just the line", state == state2 == "settings"
      and reason == reason2 == ui.SCREEN_WHY["audio_only"] and opened == [1], (state, reason, opened))

# The flow from the start: a warning first while a session is live, macOS's prompt once ever,
# then System Settings once a launch; nothing asked once allowed.
ui.SNAP_ASKED.unlink()
asked = []
permissions.ask_screen = lambda: asked.append(1)
ui.live_session = lambda: "meeting-20261006-130000"
ui.SETTINGS_OPENED[:] = []
opened[:] = []
check("a session live, macOS never asked: confirm first, nothing asked", ui.screen_permission() == ("confirm", "") and not asked)
state, reason = ui.screen_permission(confirm=True)
check("  confirmed: macOS's own prompt, once", state == "asked" and asked == [1] and "System Settings" in reason, (state, reason))
state, reason = ui.screen_permission(confirm=True)
check("  again: never the prompt twice; System Settings, once this launch", state == "settings" and asked == [1] and opened == [1], (state, asked, opened))
permissions.screen_allowed = lambda: True
ui._SNAP_CHECKS.clear()
check("once allowed: granted, nothing asked", ui.screen_permission() == ("granted", "") and asked == [1])

print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
