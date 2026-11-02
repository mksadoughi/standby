"""The call sounds: the four files as mac/callsounds.py makes them (each as its SHA256 has it), quiet and short,
made in the apps' copy of the code (never committed) and only when they aren't right; and when each plays:
incoming as Claude asks to call, connecting before the recorder opens the mic, ending once a call has stopped,
message for what arrives off a call (a meeting's summary, Claude asking to listen). Never while a call or
meeting is live, never on Quit, none with Settings' Call sounds off, none (and no error) when they weren't
made. The player is a stand-in: nothing plays here."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, shutil, subprocess, sys, tempfile, time, types, wave
from pathlib import Path

import numpy as np

root = tempfile.mkdtemp(prefix="sbsounds-")
os.environ["STANDBY_ROOT"], os.environ["STANDBY_NO_DEVICES"] = root, "1"
HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.join(HERE, "..", "mac")
sys.path.insert(0, MAC)
fails = []


def check(label, ok, detail=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label.strip())
        if detail != "":
            print("     %s" % (detail,))


# ---------------------------------------------------------------- the files
MOMENTS = ("incoming", "connecting", "message", "ending")
made = subprocess.run([sys.executable, os.path.join(MAC, "callsounds.py"), "--check"], capture_output=True, text=True, timeout=120)
check("what mac/callsounds.py makes is each file's SHA256, byte for byte (the user's Felt)", made.returncode == 0,
      (made.stdout + made.stderr)[-300:])
check("  none in the repository: the checkout holds no audio", not os.path.exists(os.path.join(MAC, "sounds"))
      or not subprocess.run(["git", "ls-files", "mac/sounds"], cwd=os.path.join(MAC, ".."), capture_output=True, text=True).stdout.strip())

import codecopy  # noqa: E402
src, dest = Path(root) / "checkout", Path(root) / "code"
(src / "mac").mkdir(parents=True)
shutil.copy(os.path.join(MAC, "callsounds.py"), src / "mac" / "callsounds.py")
first = codecopy.sync(src, dest, install=True)
SOUNDS = dest / "mac" / "sounds"
check("installed: the copy of the code has the four, made there", sorted(first) == sorted(["mac/callsounds.py"] + [
    "mac/sounds/%s.wav" % m for m in MOMENTS]), first)
for m in MOMENTS:
    with wave.open(str(SOUNDS / (m + ".wav")), "rb") as w:
        shape = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32768.0
    secs, peak = len(x) / 44100.0, 20 * np.log10(np.max(np.abs(x)))
    rms = 20 * np.log10(np.sqrt(np.mean(x ** 2)))
    longest = 1.5 if m == "incoming" else 0.6
    check("  %s: mono 16-bit 44.1 kHz, %.2f s (at most %.1f), quiet (rms %.1f, peak %.1f dBFS)" % (m, secs, longest, rms, peak),
          shape == (1, 2, 44100) and 0.3 < secs <= longest and peak <= -13.0 and -29.0 <= rms <= -27.0)
times = {m: (SOUNDS / (m + ".wav")).stat().st_mtime_ns for m in MOMENTS}
check("  again (install.sh run again, a `standby` command): nothing made again, the files untouched",
      codecopy.sync(src, dest) == [] and codecopy.sync(src, dest, install=True) == []
      and times == {m: (SOUNDS / (m + ".wav")).stat().st_mtime_ns for m in MOMENTS})
(SOUNDS / "message.wav").write_bytes(b"not it")
(SOUNDS / "ending.wav").unlink()
check("  one wrong and one gone: just those two made again", sorted(codecopy.sync(src, dest)) == ["mac/sounds/ending.wav",
      "mac/sounds/message.wav"] and times["incoming"] == (SOUNDS / "incoming.wav").stat().st_mtime_ns)
plain = Path(root) / "plain"
(plain / "mac").mkdir(parents=True)
(plain / "mac" / "x.py").write_text("x = 1\n")
check("  a checkout without the maker: none made, and nothing else changes", codecopy.sync(plain, Path(root) / "code2", install=True)
      == ["mac/x.py"] and not (Path(root) / "code2" / "mac" / "sounds").exists())
cs = codecopy._sounds(src)
cs.sounds = lambda: (_ for _ in ()).throw(ImportError("No module named 'numpy'"))
check("  no numpy to make them: nothing made, nothing raised", cs.ensure(str(Path(root) / "nonumpy")) == []
      and not (Path(root) / "nonumpy" / "message.wav").exists())

import ui  # noqa: E402

ui.CALL_SOUNDS = SOUNDS                         # the copy's, made above
played = []
ui.sound = types.SimpleNamespace(play=lambda path: (played.append(os.path.basename(path)[:-4]), True)[1])
live = [None]
ui.live_session = lambda: live[0]
ui._live_now = lambda ttl=0.5: live[0]
ui._lite_out = lambda cc, ev: None
ui._msg_event = lambda kind, data: None


def heard():
    got = list(played)
    del played[:]
    return got


# ---------------------------------------------------------------- the setting
named = {s["name"]: s for s in ui.settings_named()["settings"]}
check("Call sounds: on unless set, and named for Claude Code", ui.get_settings()["call_sounds"] is True
      and named.get("call_sounds", {}).get("value") == "on" and named["call_sounds"]["choices"] == ["on", "off"])
check("  off and on again by name and by Settings", ui.set_named("call_sounds", "off").get("value") == "off"
      and ui.get_settings()["call_sounds"] is False and ui.set_settings({"call_sounds": True}).get("ok")
      and ui.get_settings()["call_sounds"] is True)

# ---------------------------------------------------------------- incoming, and a listen ask
CC = "0f3c2a77-1111-4222-8333-944455556666"
inc = ui.LITE_POST["/api/lite/incoming"]
r = inc({"cc": CC, "kind": "call"})
check("Claude asks to call: the band asks, and it rings once (incoming)", "id" in r and heard() == ["incoming"])
inc({"cc": CC, "kind": "listen"})
check("Claude asks to listen: the message sound (it waits for their OK too)", heard() == ["message"])
live[0] = "meeting-20261101-1000"
inc({"cc": CC, "kind": "call"})
check("  asked while a meeting records: nothing plays (the mic would hear it)", heard() == [])
live[0] = None
ui.set_settings({"auto_accept": True})
r = inc({"cc": CC, "kind": "call"})
check("  Always accept: nothing asks, so nothing rings", r == {"answer": "accept"} and heard() == [])
ui.set_settings({"auto_accept": False})

# ---------------------------------------------------------------- connecting
order, talks = [], [0]


def fake_talk(wait=90, workspace=None, where=None, voice=None, **kw):
    order.append("talk")
    talks[0] += 1
    s = "talk-20261101-10%02d" % talks[0]
    (ui.SESSIONS / s).mkdir(parents=True)
    (ui.SESSIONS / s / "meta.json").write_text(json.dumps({"kind": "talk", "started": time.time(), "mic": True}))
    live[0] = s
    return {"id": s}


ui.start_talk = fake_talk
ui.sound = types.SimpleNamespace(play=lambda path: (played.append(os.path.basename(path)[:-4]), order.append("sound"), True)[2])
ui.setup_hold = lambda what, b: None
ui.lite_gist = lambda cc: (None, None, True)
ui.lite_gist_refresh = lambda cc, then=None: None
ui._cc_work_dirs = lambda cc, folder: []
ui._agent_from_claude_code = lambda cc, wait=None: True
ui._lite_watch = lambda sid: None
ui.messenger = lambda: types.SimpleNamespace(lite_session=lambda cc, folder: None)
ui.call_opens = lambda cc, sid: None
r = ui.lite_call({"cc": CC})
check("a call starts: connecting plays once, before the recorder (and its mic) starts",
      r.get("session") and heard() == ["connecting"] and order == ["sound", "talk"], (r, order))
again = ui.lite_call({"cc": CC})
check("  already on this call: nothing plays", again.get("already") and heard() == [])

# ---------------------------------------------------------------- ending
sid = r["session"]
live[0] = None
ui.lite_ended(sid)
check("the call stops: ending plays once", heard() == ["ending"])
ui.lite_ended(sid)
check("  told again: nothing more", heard() == [])
del order[:]
r = ui.lite_call({"cc": CC})
heard()
ui.QUITTING.set()
live[0] = None
ui.lite_ended(r["session"])
check("  Quit stops it: no sound, the app is going", heard() == [])
ui.QUITTING.clear()

# ---------------------------------------------------------------- message: a meeting's summary
MID = "meeting-20261101-1100"
(ui.SESSIONS / MID).mkdir(parents=True)
ui._mac_meeting = lambda s: (ui.SESSIONS / s, {"name": "Standup"})
ui.addmac = types.SimpleNamespace(bound=lambda sdir: None, ended=lambda data, now=None: dict(data, ended_at=time.time()),
                                  save=lambda sdir, data: None, recorded_s=lambda data: 60.0)
ui.mac_event = lambda s: {}
cards = []
ui.mac_card = lambda s, now=None: cards.append(s)
ui.mac_end(MID, background=False)
check("a meeting ends: its summary made, then the message sound", cards == [MID] and heard() == ["message"])
live[0] = "talk-20261101-1200"
ui.mac_end(MID, background=False)
check("  a summary ready while a call is on: nothing plays", heard() == [])
live[0] = None

# ---------------------------------------------------------------- off
ui.set_settings({"call_sounds": False})
inc({"cc": CC, "kind": "call"})
inc({"cc": CC, "kind": "listen"})
r = ui.lite_call({"cc": CC})
live[0] = None
ui.lite_ended(r["session"])
ui.mac_end(MID, background=False)
check("Call sounds off: none of the four plays", heard() == [])

# ---------------------------------------------------------------- not made
ui.set_settings({"call_sounds": True})
ui.CALL_SOUNDS = Path(root) / "never-made"
ui.call_sound("message")
check("not made (a checkout never installed): no sound, no error", heard() == [])
ui.CALL_SOUNDS = SOUNDS

# ---------------------------------------------------------------- the player
from native import sound  # noqa: E402
from native.macos import sound as mac_sound  # noqa: E402
check("the player plays nothing with STANDBY_NO_DEVICES (every test)", sound.play(str(SOUNDS / "message.wav")) is False)
check("  on a Mac, a file that isn't there: not played, nothing raised", mac_sound.play(str(Path(root) / "nope.wav")) is False)

print("\n%s" % ("all OK" if not fails else "%d FAILED: %s" % (len(fails), "; ".join(fails))))
sys.exit(1 if fails else 0)
