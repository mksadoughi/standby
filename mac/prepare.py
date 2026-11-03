"""A new Mac, ready for Standby's first start: install.sh runs this once, at its end.

macOS checks each native library the first time anything on the Mac loads it (Gatekeeper,
online, about 0.2 s each), and remembers it after. On a fresh macOS 26 Mac (2026-10-30) the
first starts paid for about 130 of them: the app froze 6 s as it opened (AppKit, WebKit,
Quartz), the first meeting went live 8 s late (the speech model: scipy, numba), and the
first call's voice was ready after 32 s (its 240 MB download, then 87 more of scipy's). Here
each of those is loaded once, and the voice downloaded, while an install is expected to take
a while: the same starts then take what they take every time after.

    python prepare.py          a line for each, as the installer's
"""
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# What the app, the recorder and the windows import, most of them inside the function that needs it
LIBRARIES = ["objc", "Foundation", "CoreFoundation", "AppKit", "WebKit", "Quartz", "AVFoundation", "CoreAudio",
             "CoreML", "PyObjCTools.AppHelper", "webview", "sounddevice", "onnxruntime",
             "sherpa_onnx", "mlx.core", "scipy.signal"]
VOICE_WAIT = 600                 # the most the voice's download and first load are waited for


def ok(text):
    print("   \033[32m✓\033[0m %s" % text, flush=True)


def warn(text):
    print("   \033[33m!\033[0m %s" % text, flush=True)


def libraries():
    for name in LIBRARIES:
        try:
            importlib.import_module(name)
        except Exception:
            pass                       # not on this Mac (or this macOS): nothing loads it later either


def speech_model():
    """The recorder's own load: the model, then a decode of silence."""
    import speechmodel
    try:
        cfg = json.load(open(Path.home() / ".standby" / "config.json"))
    except Exception:
        cfg = {}
    speechmodel.migrate(cfg)
    speechmodel.load_model(speechmodel.for_load(cfg))


def voice(said):
    """Standby's voice, as a call opens it: downloaded the first time, then loaded, then quit."""
    import tts
    if not tts.available():
        return
    p = tts.VoiceProcess(log=lambda m: None)
    p.open()
    end, told = time.time() + VOICE_WAIT, False
    while p.state not in ("ready", "failed") and time.time() < end:
        if p.state == "downloading" and not told:
            print("   downloading Standby's voice (%s, about %d MB, first time only)..."
                  % (tts.ENGINE_NAME, tts.DOWNLOAD_BYTES // 1000000), flush=True)
            told = True
        time.sleep(0.2)
    said["voice"] = p.state if p.state in ("ready", "failed") else "slow"
    said["error"] = p.error
    if p.ready:
        tts.Clean().feed([0.0] * 2400)      # its sound's clean-up, as the first sentence runs it
    p.close()


def main():
    t0 = time.time()
    said = {}
    v = threading.Thread(target=voice, args=(said,), name="voice")
    v.start()                          # in its own process: its download goes on meanwhile
    libraries()
    try:
        speech_model()
    except Exception as exc:
        warn("the speech model didn't load (%s): the first meeting loads it" % str(exc)[:120])
    v.join()
    if said.get("voice") == "failed":
        warn("Standby's voice didn't load (%s): a call speaks with a macOS voice until it does"
             % str(said.get("error") or "no reason given")[:120])
    elif said.get("voice") == "slow":
        warn("Standby's voice wasn't ready in %d minutes: a call gets it ready the first time" % (VOICE_WAIT // 60))
    ok("ready for a first call or meeting (%.0f s, once)" % (time.time() - t0))


if __name__ == "__main__":
    main()
