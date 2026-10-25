#!/usr/bin/env python3
"""The story film's soundtrack, in Standby's own voices (Pocket TTS, mac/tts.py), from tools/demo/story-lines.js.

    ~/.standby/venv/bin/python tools/demo/voiceover.py --measure            each line's length, to set its `at`
    ~/.standby/venv/bin/python tools/demo/voiceover.py OUT.wav DURATION      every line at its `at`, one track

Each line is spoken once and kept in a cache beside the output (a line changes, it's spoken again). Nothing is
sent anywhere: the voices are the ones Standby downloads for calls.
"""
import hashlib
import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "mac"))
import tts  # noqa: E402
from native import tts as os_tts  # noqa: E402

CACHE = Path.home() / ".standby" / "demo-voice"
GAP = 0.25                                                  # the least pause between one line's end and the next


def lines():
    """The lines, each `at` where it really falls: after the opening's hold, that much later."""
    src = (HERE / "story-lines.js").read_text()
    hold = re.search(r"STORY_HOLD\s*=\s*\[([\d.]+),\s*([\d.]+)\]", src)
    out = json.loads(re.search(r"STORY_LINES\s*=\s*(\[.*\])\s*;", src, re.S).group(1))
    if hold:
        at0, by = float(hold.group(1)), float(hold.group(2))
        for line in out:
            if line["at"] > at0:
                line["at"] += by
    return out


def clip(engines, line):
    """The line, spoken in its voice, as float32 at tts.RATE; from the cache if it's been said before."""
    words = line.get("say") or line["text"]
    key = hashlib.sha1(("%s|%s" % (line["voice"], words)).encode()).hexdigest()[:16]
    path = CACHE / ("%s-%s.npy" % (line["id"], key))
    if path.exists():
        return np.load(path)
    if line["voice"] not in engines:
        engine = os_tts.engine(line["voice"])
        engine.load(lambda state: None)
        engines[line["voice"]] = engine
    audio = np.concatenate(list(engines[line["voice"]].stream(words))).astype("float32")
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(path, audio)
    return audio


def main(argv):
    engines, todo = {}, lines()
    clips = [(line, clip(engines, line)) for line in todo]
    if argv[:1] == ["--measure"]:
        prev_end = None
        for line, audio in clips:
            d = len(audio) / tts.RATE
            warn = "  <- starts before the last line ends" if prev_end is not None and line["at"] < prev_end + GAP else ""
            print("%-3s %-7s at %5.1f  %4.1f s  ends %5.1f%s" % (line["id"], line["who"], line["at"], d, line["at"] + d, warn))
            prev_end = line["at"] + d
        return 0
    out, dur = Path(argv[0]), float(argv[1])
    track = np.zeros(int(dur * tts.RATE) + 1, dtype="float32")
    for line, audio in clips:
        a = int(line["at"] * tts.RATE)
        track[a:a + len(audio)] += audio[:max(0, len(track) - a)]
    track = np.clip(track, -1, 1)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.RATE)
        w.writeframes((track * 32767).astype("<i2").tobytes())
    print("ok   %s: %.1f s, %d lines" % (out, dur, len(clips)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
