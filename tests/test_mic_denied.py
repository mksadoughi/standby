"""A microphone macOS refuses: it still opens, and sends exact zeros (a real mic in a quiet
room never does). The recorder says so on the mic's source (cause "permission"), as it does
for the meeting's sound, unless macOS says the mic is allowed; it opens the mic again every
so often, so allowing it in System Settings is all the user does; and it clears the moment
sound comes. The app's Allow opens System Settings at Microphone. No mic, no Core Audio:
the channel is fed by hand."""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import json, os, socket, stat, sys, tempfile
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbmicdeny-"))
os.environ["STANDBY_ROOT"] = str(root)
opened = root / "opened.txt"
fake_open = root / "fake-open"
fake_open.write_text("#!/bin/sh\necho \"$@\" >> %s\n" % opened)
fake_open.chmod(fake_open.stat().st_mode | stat.S_IEXEC)
os.environ["STANDBY_OPEN"] = str(fake_open)
s0 = socket.socket(); s0.bind(("127.0.0.1", 0)); os.environ["STANDBY_UI_PORT"] = str(s0.getsockname()[1]); s0.close()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import standby as sb  # noqa: E402
sb.log = lambda m: logged.append(m)
logged = []

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-500:])


SR = sb.SAMPLE_RATE
quiet = lambda secs: np.zeros(int(SR * secs), dtype="float32")
room = lambda secs: (np.random.RandomState(1).randn(int(SR * secs)) * 1e-4).astype("float32")   # a quiet room: never exact zeros
auth = {"now": "denied"}
sb.mic_authorization = lambda: auth["now"]


def mic():
    c = sb.Channel("Room", None, None, lambda *a: None, None, 30)
    c.device_name = "MacBook Pro Microphone"
    return c


check("in a test, macOS isn't asked: the fakes' streams stand in for a working mic",
      __import__("importlib").reload(sb).mic_authorization() == "authorized")
sb.mic_authorization = lambda: auth["now"]
sb.log = lambda m: logged.append(m)

c = mic()
c._feed(quiet(2.9))
check("2.9 s of exact zeros: not yet", not c.mic_denied() and "cause" not in c.source())
c._feed(quiet(0.2))
src = c.source()
check("over 3 s: the mic's source says it isn't allowed, with how to allow it",
      c.mic_denied() and src["cause"] == "permission" and src["reason"] == "not allowed" and src["ok"] is False
      and "Privacy & Security > Microphone" in src["detail"] and src["kind"] == "mic", src)
check("  macOS says nothing (no AVFoundation): still said", (auth.update(now=None), c.mic_denied())[1])
auth["now"] = "authorized"
check("  macOS says it's allowed (a muted interface sends zeros too): not a permission",
      not c.mic_denied() and "cause" not in c.source())
auth["now"] = "denied"
c._feed(room(0.1))
check("sound comes: cleared at once", not c.mic_denied() and c.zeros == 0.0 and "cause" not in c.source())
q = mic()
q._feed(room(10))
check("a quiet room (low, never exact zeros): never taken for a refusal", not q.mic_denied())

print("only a mic the recorder opened itself")
m = mic()
m.capture = object()
m._feed(quiet(5))
check("the meeting's sound (zeros whenever the app is quiet): never", not m.mic_denied() and m.zeros == 0.0)
v = mic()
v._feed(quiet(5))
v.borrowed = object()
check("voice mode's echo-cancelled stream: never", not v.mic_denied())
u = mic()
u._feed(quiet(5))
u.state = "muted"
check("muted by the user: never (that's their choice)", not u.mic_denied())
f = mic()
f._fill = lambda: None
f.clock0, f.queued = 0, 0
f._queue(quiet(5))                 # the silence a gap is filled with: not the device's
check("the silence filling a gap isn't the device's: not counted", f.zeros == 0.0 and not f.mic_denied())

print("allowing it in System Settings is all the user does")
c = mic()
c._stream = object()
c.last_audio = 1000.0
c._feed(quiet(4))
c.last_audio = 1000.0
check("refused: said once in the log, and not opened again at once",
      c.due(1000.1) is False and any("isn't letting Standby use the microphone" in x for x in logged), logged[-1:])
n0 = len([x for x in logged if "isn't letting" in x])
check("  opened again every %d s, so a switch turned on is heard" % sb.PERMISSION_POLL,
      c.due(1000.1 + sb.PERMISSION_POLL - 1) is False and c.due(1000.2 + sb.PERMISSION_POLL) is True
      and len([x for x in logged if "isn't letting" in x]) == n0)
c._feed(room(0.1))
check("  once sound comes, back to ordinary checks", c.due(1000.3 + sb.PERMISSION_POLL) is False
      and c._denied_reopen is None)
s_ = mic()
s_._stream, s_._input = object(), object()
s_._feed(quiet(4))
s_.last_audio = 2000.0
s_.due(2000.1)
check("refused, and then the stream stops altogether: a stall, opened again as any stall",
      s_.due(2000.0 + sb.STALL + 0.2) is False and s_.due(2000.0 + sb.STALL + 1.0) is True
      and any("no audio from MacBook Pro Microphone" in x for x in logged), logged[-2:])

print("the app")
import ui  # noqa: E402
ui.log = lambda m: None
check("Allow: System Settings at Privacy & Security > Microphone",
      ui.mic_permission() == {"ok": True} and opened.read_text().strip() ==
      "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone", opened.read_text())
srcs = [{"kind": "mic", "name": "MacBook Pro Microphone", "ok": False, "cause": "permission", "reason": "not allowed",
         "detail": "Allow Standby in System Settings > Privacy & Security > Microphone. It records by itself once allowed."},
        {"kind": "meeting", "name": "Zoom", "state": "recording"}]
check("/api/live passes the mic's state on as it is (the meeting's own check doesn't touch it)",
      ui.with_screen_why(srcs) == srcs, ui.with_screen_why(srcs))

print()
print("%d failed" % len(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
