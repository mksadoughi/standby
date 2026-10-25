#!/usr/bin/env python3
"""Standby's app icons (the user's "C: Wave"): Standby's is three bars of sound in a
soft circle on an indigo-to-violet macOS rounded square; Standby Dictation's is an indigo mic
on a pale one. Drawn with numpy, no image libraries, by install.sh.

    python3 mac/icons.py OUT_DIR           Standby.icns, Dictation.icns (needs iconutil)
    python3 mac/icons.py OUT_DIR --png     also icon-standby.png, icon-dictation.png (1024)

The bars are the sound Standby listens to, the same mark as the companion's orb and the
menu bar's (SF Symbols' waveform.circle); red is kept for what records. Drawn once at
1024 px, then halved by averaging for every smaller size, so small icons stay crisp.
"""
import os
import struct
import subprocess
import sys
import tempfile
import zlib

import numpy as np

N = 1024                        # the macOS icon grid: a 824 px square with a 100 px margin
SQ, HALF = 824, 412
INDIGO, VIOLET = (80, 70, 229), (139, 63, 232)      # Standby's square, top left to bottom right
PALE_TOP, PALE_BOTTOM = (248, 247, 255), (233, 231, 255)   # Dictation's
MIC_INK = (80, 70, 229)


def _grid():
    y, x = np.mgrid[0:N, 0:N].astype(np.float64) + 0.5
    return x - N / 2, y - N / 2


def _cover(d):
    """Antialiased coverage from a signed distance in pixels (negative inside)."""
    return np.clip(0.5 - d, 0.0, 1.0)


def _squircle(x, y, half=HALF, n=5.0):
    """Signed distance (about, in px) to Apple's continuous-corner square: a superellipse."""
    r = (np.abs(x / half) ** n + np.abs(y / half) ** n) ** (1.0 / n)
    return (r - 1.0) * half


def _base(x, y, top=INDIGO, bottom=VIOLET, diagonal=True):
    """The rounded square with its soft shadow, as straight RGBA floats (0-1): a gradient
    from the top left (or the top) down, and a faint light edge along the top."""
    rgba = np.zeros((N, N, 4))
    shadow = np.exp(-np.maximum(_squircle(x, y - 10), 0) ** 2 / (2 * 9.0 ** 2)) * 0.24
    rgba[..., 3] = shadow                                   # black, in the margin below it
    lean = 0.94 if diagonal else 0.0
    t = np.clip(((x + HALF) * lean + (y + HALF) * 0.94) / (SQ * (1.88 if diagonal else 0.94)), 0, 1)[..., None]
    fill = (np.array(top) * (1 - t) + np.array(bottom) * t) / 255.0
    rim = np.clip(1 - np.abs(_squircle(x, y) + 1.5) / 1.5, 0, 1)[..., None] * (y < 0)[..., None] * 0.10
    fill = np.clip(fill + rim, 0, 1)
    a = _cover(_squircle(x, y))
    return _over(rgba, fill, a)


def _over(rgba, rgb, alpha):
    """Paint rgb (N×N×3) with coverage alpha (N×N) over straight RGBA."""
    a0, a = rgba[..., 3:4], alpha[..., None]
    out_a = a + a0 * (1 - a)
    rgb_out = (rgb * a + rgba[..., :3] * a0 * (1 - a)) / np.maximum(out_a, 1e-6)
    return np.concatenate([rgb_out, out_a], axis=-1)


def _stops(t, stops):
    """A CSS-like gradient: t (N×N) through [(at, (r, g, b)), ...]."""
    out = np.zeros(t.shape + (3,))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        k = np.clip((t - t0) / (t1 - t0), 0, 1)[..., None]
        seg = (t >= t0)[..., None] & (t <= t1)[..., None]
        out = np.where(seg, np.array(c0) * (1 - k) + np.array(c1) * k, out)
    return out / 255.0


def _bar(x, y, cx, h, w):
    """Distance to an upright capsule, h tall and w wide, centred at (cx, 0)."""
    r = w / 2
    return _seg(x, y, cx, -(h / 2 - r), cx, h / 2 - r) - r


def wave_icon(small=False):
    """small: for 16 and 32 pt, simpler and bolder (no circle, thicker bars), as Apple's are."""
    x, y = _grid()
    img = _base(x, y)
    unit = SQ / 94.0                                     # the report's 100-unit drawing, onto the 824 px square
    if not small:
        disc = np.hypot(x, y) - 27 * unit                # a soft white circle behind the bars
        img = _over(img, np.ones(img[..., :3].shape), _cover(disc) * 0.14)
    w, k, gap = (11 * unit, 1.25, 14 * unit) if small else (7 * unit, 1.0, 12 * unit)
    d = np.minimum(np.minimum(_bar(x, y, -gap, 16 * k * unit, w), _bar(x, y, 0, 30 * k * unit, w)),
                   _bar(x, y, gap, 20 * k * unit, w))
    return _over(img, np.ones(img[..., :3].shape), _cover(d))


def _seg(x, y, ax, ay, bx, by):
    """Distance to the segment from (ax, ay) to (bx, by)."""
    px, py, vx, vy = x - ax, y - ay, bx - ax, by - ay
    h = np.clip((px * vx + py * vy) / (vx * vx + vy * vy), 0, 1)
    return np.hypot(px - vx * h, py - vy * h)


def mic_icon(small=False):
    x, y = _grid()
    img = _base(x, y, PALE_TOP, PALE_BOTTOM, diagonal=False)
    u = 22.0 if small else 20.0                       # one unit of the 24-unit mic glyph
    cx, oy = 0.0, -12 * u + 6                         # the glyph's top in units (-12), nudged to look centred
    sw = (2.8 if small else 1.9) * u                  # the stroke, bolder where it's tiny
    gy = lambda v: oy + v * u
    body = _seg(x, y, cx, gy(6), cx, gy(11)) - 3 * u                 # the capsule, filled
    ring = np.abs(np.hypot(x - cx, y - gy(11)) - 7 * u) - sw / 2     # the cradle: the lower half
    ring = np.where(y >= gy(11), ring, 1e9)
    caps = np.minimum(np.hypot(x - (cx - 7 * u), y - gy(11)), np.hypot(x - (cx + 7 * u), y - gy(11))) - sw / 2
    ring = np.minimum(ring, caps)                     # round ends, as the SVG's
    stem = _seg(x, y, cx, gy(18), cx, gy(21)) - sw / 2
    d = np.minimum(np.minimum(body, ring), stem)
    return _over(img, np.broadcast_to(np.array(MIC_INK) / 255.0, img[..., :3].shape), _cover(d))


def _png(rgba, path):
    a = np.clip(np.round(rgba * 255), 0, 255).astype(np.uint8)
    h, w = a.shape[:2]
    raw = b"".join(b"\x00" + a[r].tobytes() for r in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _halve(rgba):
    """Half size by averaging 2×2 blocks, in premultiplied alpha so edges don't darken."""
    p = rgba.copy()
    p[..., :3] *= p[..., 3:4]
    h, w = p.shape[0] // 2, p.shape[1] // 2
    p = p.reshape(h, 2, w, 2, 4).mean(axis=(1, 3))
    p[..., :3] /= np.maximum(p[..., 3:4], 1e-6)
    return p


def icns(rgba, out, name, small=None):
    """Every size of the iconset from the 1024 drawing (16 and 32 pt from the simpler small
    one, when there is one), then iconutil."""
    sizes, img = {}, rgba
    s = N
    while s >= 16:
        sizes[s] = img
        img, s = _halve(img), s // 2
    if small is not None:
        img, s = small, N
        while s >= 16:
            if s <= 64:
                sizes[s] = img
            img, s = _halve(img), s // 2
    iconset = tempfile.mkdtemp(suffix=".iconset")
    for pt in (16, 32, 128, 256, 512):
        _png(sizes[pt], os.path.join(iconset, "icon_%dx%d.png" % (pt, pt)))
        _png(sizes[pt * 2], os.path.join(iconset, "icon_%dx%d@2x.png" % (pt, pt)))
    try:
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(out, name + ".icns")], check=False)
    except OSError:
        pass                                          # no icon is fine


def main(argv):
    out = argv[0] if argv else "."
    os.makedirs(out, exist_ok=True)
    for name, draw in (("Standby", wave_icon), ("Dictation", mic_icon)):
        rgba = draw()
        icns(rgba, out, name, draw(small=True))
        if "--png" in argv:
            _png(rgba, os.path.join(out, "icon-%s.png" % name.lower()))


if __name__ == "__main__":
    main(sys.argv[1:])
