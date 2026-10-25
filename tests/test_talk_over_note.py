"""Settings' hint under "Let me talk over Claude" (talk_over_note): said only when
the microphone chosen is not the Mac's default one, which is all echo cancelling
can take. Core Audio is faked; its answer is kept a few seconds, not asked on every
Settings open. When the default cannot be told, the hint is said, as before."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, and no child may crash)
import json, os, sys, tempfile, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbtalkover-"))
os.environ.update(STANDBY_ROOT=str(root))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:400])


class Device(object):
    def __init__(self, name): self.name = name


class FakeCoreAudio(object):
    asked = 0
    default = "MacBook Pro Microphone"
    def default_input(self):
        FakeCoreAudio.asked += 1
        return Device(self.default) if self.default else None


ui._core_audio = lambda: FakeCoreAudio()
now = [1000.0]
ui.time.time = lambda: now[0]
note = lambda mic: ui.talk_over_note({"mic_device": mic} if mic is not None else {})

check("no mic chosen (the Mac's own, following it): no hint", note(None) == "" and note("") == "")
check("the chosen mic is the Mac's default anyway: no hint", note("MacBook Pro Microphone") == "")
check("another mic chosen: the hint", note("USB Headset Mic") == ui.TALK_OVER_MIC)
check("Core Audio asked once for all that: its answer is kept a while", FakeCoreAudio.asked == 1, FakeCoreAudio.asked)
FakeCoreAudio.default = "USB Headset Mic"            # the user made the headset the Mac's default
check("  within the while, the kept answer", note("USB Headset Mic") == ui.TALK_OVER_MIC)
now[0] += ui.DEFAULT_MIC_FOR + 1
check("  after it, asked again: the headset is the default now, no hint", note("USB Headset Mic") == ""
      and FakeCoreAudio.asked == 2, FakeCoreAudio.asked)
ui._core_audio = lambda: None
now[0] += ui.DEFAULT_MIC_FOR + 1
check("the default cannot be told (no Core Audio): the hint, as before", note("USB Headset Mic") == ui.TALK_OVER_MIC)
check("  and still none with no mic chosen", note(None) == "")
(root / "config.json").write_text(json.dumps({"mic_device": "USB Headset Mic"}))
check("in the settings Settings reads", ui.get_settings()["talk_over_note"] == ui.TALK_OVER_MIC, ui.get_settings())

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
