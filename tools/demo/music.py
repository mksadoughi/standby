#!/usr/bin/env python3
"""A light music bed under the film's voices: soft chords and a slow pluck, written here (no samples), dipping while
anyone speaks.

    python3 tools/demo/music.py VOICES.wav OUT.wav

VOICES.wav is voiceover.py's track as the film uses it (mono, 16-bit); OUT.wav is the same with the music under it,
ready for render.py --audio.
"""
import sys
import wave

import numpy as np

CHORDS = [[50, 57, 61, 66], [47, 54, 57, 62], [43, 50, 54, 59], [45, 52, 57, 61]]   # Dmaj7, Bm7, Gmaj7, A: four seconds each
BAR, BED_DB, DIP = 4.0, -33.0, .6                                                    # the bed's loudness; how far it dips under words


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def bed(n, sr):
    t = np.arange(n) / sr
    pad, pluck = np.zeros(n), np.zeros(n)
    for i in range(int(n / sr / BAR) + 2):
        a, notes = i * BAR, CHORDS[i % len(CHORDS)]
        # each chord swells in and overlaps the next by a second and a half
        env = np.clip((t - a + 1.5) / 1.5, 0, 1) * np.clip((a + BAR + 1.5 - t) / 1.5, 0, 1)
        env = np.sin(env * np.pi / 2) ** 2
        for m in notes:
            f = hz(m)
            pad += env * (np.sin(2 * np.pi * f * t) + .5 * np.sin(2 * np.pi * f * 1.003 * t) + .25 * np.sin(4 * np.pi * f * t))
        # a soft pluck on the half beat, an octave up, walking the chord
        for k, j in enumerate([0, 2, 1, 3, 2, 3, 1, 2]):
            s = int((a + k * BAR / 8) * sr)
            if s >= n:
                break
            m = min(n - s, int(1.6 * sr))
            u = np.arange(m) / sr
            pluck[s:s + m] += np.sin(2 * np.pi * hz(notes[j] + 12) * u) * np.exp(-u / .45) * np.minimum(u / .01, 1)
    out = pad / np.abs(pad).max() + .35 * pluck / max(np.abs(pluck).max(), 1e-9)
    return out * 10 ** (BED_DB / 20) / np.sqrt((out ** 2).mean())


def main(src, dst):
    with wave.open(src) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        voice = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float).reshape(-1, ch).mean(1) / 32768
    n = len(voice)
    music = bed(n, sr)
    # where someone is speaking (30 ms windows above -42 dB), held and eased, so the music dips under the words
    hop = int(.03 * sr)
    rms = np.sqrt(np.convolve(voice ** 2, np.ones(hop) / hop, mode="same"))
    talk = (rms > 10 ** (-42 / 20)).astype(float)
    for a in (int(.08 * sr), int(.4 * sr)):                              # a quick dip, a slower return
        talk = np.convolve(talk, np.ones(a) / a, mode="same")
    music *= 1 - DIP * np.clip(talk * 1.5, 0, 1)
    music *= np.clip(np.arange(n) / (1.5 * sr), 0, 1) * np.clip((n - np.arange(n)) / (2.5 * sr), 0, 1)
    mix = np.clip(voice + music, -1, 1)
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())
    print(f"ok   {dst}: {n / sr:.1f} s, music at {BED_DB:.0f} dB, {int(DIP * 100)}% lower under the words")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
