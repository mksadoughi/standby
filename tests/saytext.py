"""Speech for tests: `text` spoken by macOS `say` into a file (never the speakers), as 16 kHz mono float32.

Some machines' `say` writes only silence (a CI runner's macOS 26 image, 2026-11-11: test_quiet_speech divided by
its peak of 0). Then the natural voices in FALLBACK that are installed are tried, in order, never any other (the
singing and effect voices make sound, not speech), and the first that speaks is kept for the run; with none that
speaks, speech() is None, and the test skips its speech part saying SILENT, never crashing.

    import saytext
    audio = saytext.speech("Robin will write the release notes.")
    if audio is None:
        print("  (%s)" % saytext.SILENT)
"""
import os
import re
import shutil
import subprocess
import tempfile
import wave

import numpy as np

SILENT = "macOS say produced silence on this machine: skipped"
# Plain speaking voices, tried in this order after the default (the first six that tuned measure_interruptions)
FALLBACK = ["Samantha", "Daniel", "Karen", "Moira", "Flo (English (UK))", "Fred"]
TIMEOUT = 60               # seconds for one say or afconvert: a voice that hangs never holds up the suite
_voice = []                # [] not looked for yet; [None] the default speaks; [name] that voice; [False] none speaks
_LINE = re.compile(r"^(.+?)\s+[a-z]{2,3}_[A-Z]{2}\s+#")    # `say -v ?`: "Name (maybe (with) spaces)  en_US  # ..."


def usable():
    """`say` and `afconvert` are here (a Mac)."""
    return bool(shutil.which("say") and shutil.which("afconvert"))


def _make(text, voice, folder):
    aiff, wav = os.path.join(folder, "say.aiff"), os.path.join(folder, "say.wav")
    subprocess.run(["say"] + (["-v", voice] if voice else []) + ["-o", aiff, text], check=True, capture_output=True,
                   timeout=TIMEOUT)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True, capture_output=True,
                   timeout=TIMEOUT)
    with wave.open(wav) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0


def _speaks(a):
    return a.size > 0 and float(np.abs(a).max()) > 0.01


def installed():
    """The names of the voices installed on this Mac, as `say -v` takes them."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=TIMEOUT).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [m.group(1).strip() for m in (_LINE.match(line) for line in out.splitlines()) if m]


def speech(text, folder=None):
    """`text` as speech, or None: no `say` here, or every voice tried gave silence."""
    if not usable() or _voice == [False]:
        return None
    folder = folder or tempfile.mkdtemp(prefix="saytext-")
    have = set(installed()) if not _voice else set()
    for voice in (_voice[:1] if _voice else [None] + [v for v in FALLBACK if v in have]):
        try:
            a = _make(text, voice, folder)
        except (OSError, subprocess.SubprocessError):
            continue
        if _speaks(a):
            _voice[:] = [voice]
            return a
    if not _voice:
        _voice[:] = [False]
    return None
