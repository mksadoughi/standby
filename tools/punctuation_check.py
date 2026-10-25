#!/usr/bin/env python3
"""Does a speech model write capitals and full stops? (2026-10-11)

    ~/.standby/venv/bin/python tools/punctuation_check.py [key ...] [--scratch DIR]

One `say` sentence, decoded by each model named (every key in speechmodel.MODELS by
default), and what came back. Standby leans on punctuation: voice mode waits for a
full stop to know a turn is over, and dictation pastes what it is given. So the
`punctuates` flag in speechmodel.MODELS is measured, not taken from a model card, and
this is what measures it. Run it before offering a new model, and put the answer there.

Nothing is played: `say -o` writes a file. --scratch keeps the download out of the
user's own Hugging Face cache (any model not already there is downloaded once).
"""
import os
import subprocess
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SENTENCE = ("Please ask Claude Code to open the git repository. Then run the tests. "
            "Did the build pass, or did it fail?")


def speech(text=SENTENCE):
    """`text` as 16 kHz mono float32, spoken by macOS into a file (never the speakers)."""
    import numpy as np
    d = tempfile.mkdtemp(prefix="punc-")
    aiff, wav = os.path.join(d, "s.aiff"), os.path.join(d, "s.wav")
    subprocess.run(["say", "-o", aiff, text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True)
    with wave.open(wav) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0


def decode(path, audio):
    """One pass of the model in `path` over `audio`, as the recorder's first decode."""
    import mlx.core as mx
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel
    model = from_pretrained(str(path))
    cfg = model.preprocessor_config
    return model.generate(get_logmel(mx.array(audio.astype("float32")), cfg))[0].text.strip()


def punctuates(text):
    """It wrote at least one stop and one capital: what Standby needs of it."""
    words = text.split()
    return (any(c in text for c in ".?!") and any(w[:1].isupper() for w in words[1:] if w[:1].isalpha()))


def main(argv):
    if "--scratch" in argv:
        i = argv.index("--scratch")
        os.environ["HF_HUB_CACHE"] = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    sys.path.insert(0, os.path.join(HERE, "..", "mac"))
    import speechmodel
    keys = argv or list(speechmodel.MODELS)
    audio = speech()
    print("%.1fs of speech, %d model(s)\n" % (len(audio) / 16000.0, len(keys)))
    bad = []
    for key in keys:
        m = speechmodel.MODELS[key]
        path = speechmodel.local(key) or speechmodel.fetch(key)
        text = decode(path, audio)
        got = punctuates(text)
        print("%-12s %-32s punctuates: %s (the flag says %s)" % (key, m.repo.split("/")[-1], got, m.punctuates))
        print("   %r\n" % text)
        if got != m.punctuates:
            bad.append(key)
    if bad:
        print("the flag is wrong for: %s" % ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
