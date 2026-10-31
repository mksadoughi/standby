#!/usr/bin/env python3
"""A light music track under the film's voices, written here (no samples): bright and easy, about 104 beats a
minute, as product films use; a soft kick, finger snaps, a quiet shaker, a bouncy bass and marimba chords over
D, A, Bm, G. It dips while anyone speaks.

    python3 tools/demo/music.py VOICES.wav OUT.wav

VOICES.wav is voiceover.py's track as the film uses it (mono, 16-bit); OUT.wav is the same with the music under it,
ready for render.py --audio.
"""
import sys
import wave

import numpy as np

CHORDS = [[62, 66, 69], [61, 64, 69], [59, 62, 66], [59, 62, 67]]        # D, A, Bm, G: one bar each
ROOTS = [38, 45, 47, 43]
BEAT = 60 / 104
BED_DB, DIP = -30.0, .55                                                  # the music's loudness; how far it dips under words


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def add(buf, at, sound, sr):
    s = int(at * sr)
    if 0 <= s < len(buf):
        m = min(len(buf) - s, len(sound))
        buf[s:s + m] += sound[:m]


def tone(f, dur, decay, sr, harm=()):
    u = np.arange(int(dur * sr)) / sr
    w = np.sin(2 * np.pi * f * u) + sum(a * np.sin(2 * np.pi * f * k * u) * np.exp(-u / (decay * .25)) for k, a in harm)
    return w * np.exp(-u / decay) * np.minimum(u / .004, 1)


def bed(n, sr):
    rng = np.random.default_rng(7)
    noise = lambda d: rng.standard_normal(int(d * sr))
    hi = lambda x: np.diff(x, prepend=0)                                  # a little brighter: the noise's highs
    u = np.arange(int(.3 * sr)) / sr
    kick = np.sin(2 * np.pi * np.cumsum(55 + 70 * np.exp(-u / .04)) / sr) * np.exp(-u / .12)
    snap = hi(noise(.15)) * np.exp(-np.arange(int(.15 * sr)) / sr / .035) * .5
    shake = hi(hi(noise(.06))) * np.exp(-np.arange(int(.06 * sr)) / sr / .015) * .12
    drums, bass, keys = np.zeros(n), np.zeros(n), np.zeros(n)
    for b in range(int(n / sr / (4 * BEAT)) + 1):
        a, ch, root = b * 4 * BEAT, CHORDS[b % 4], ROOTS[b % 4]
        for k in range(4):
            add(drums, a + k * BEAT, kick if k in (0, 2) else snap, sr)
        for k in range(8):
            add(drums, a + k * BEAT / 2, shake * (1 if k % 2 else .55), sr)
        for at, m in ((0, root), (1.5, root), (2, root + 12), (3, root + 7)):    # the bass: one, the and of two, three, four
            add(bass, a + at * BEAT, tone(hz(m), .5, .22, sr, ((2, .4),)), sr)
        for at, idx in ((0, (0, 1, 2)), (.75, (1, 2)), (1.5, (0, 2)), (2.5, (0, 1, 2)), (3.25, (1, 2))):   # marimba stabs, a little off the beat
            for i in idx:
                add(keys, a + at * BEAT, tone(hz(ch[i]), .7, .28, sr, ((4, .35), (10, .08))), sr)
        if b % 2 == 1:                                                    # a bell answering every other bar
            for k, i in enumerate((2, 1, 0)):
                add(keys, a + (3.5 + k * .25) * BEAT, .5 * tone(hz(ch[i] + 12), .8, .35, sr), sr)
    norm = lambda x: x / max(np.abs(x).max(), 1e-9)
    out = .8 * norm(drums) + .9 * norm(bass) + norm(keys)
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
