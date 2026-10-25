#!/usr/bin/env python3
"""The app icons (mac/icons.py, "C: Wave"): drawn at 1024, the bars and the mic where
they belong, and every size of the iconset (iconutil, on macOS)."""
import os
import shutil
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import icons  # noqa: E402

fails = []


def check(label, ok):
    print("  %-56s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)


def pixel(path, x, y):
    data = open(path, "rb").read()
    w, h = struct.unpack(">II", data[16:24])
    idat = b"".join(data[i + 8:i + 8 + struct.unpack(">I", data[i:i + 4])[0]]
                    for i in range(8, len(data)) if data[i + 4:i + 8] == b"IDAT")
    raw = zlib.decompress(idat)
    row = raw[y * (w * 4 + 1) + 1:(y + 1) * (w * 4 + 1)]
    return w, h, tuple(row[x * 4:x * 4 + 4])


out = tempfile.mkdtemp(prefix="sbicons-")
try:
    icons.main([out, "--png"])
    w, h, mid = pixel(os.path.join(out, "icon-standby.png"), 512, 512)
    check("Standby: 1024 px, a white bar of sound in the middle", (w, h) == (1024, 1024) and min(mid[:3]) > 230 and mid[3] == 255)
    _, _, corner = pixel(os.path.join(out, "icon-standby.png"), 4, 4)
    check("  outside the rounded square: clear", corner[3] < 10)
    _, _, sq = pixel(os.path.join(out, "icon-standby.png"), 160, 512)
    check("  the square is indigo to violet, not red", sq[2] > 200 and sq[1] < 110 and sq[2] > sq[0] and sq[3] == 255)
    _, _, mic = pixel(os.path.join(out, "icon-dictation.png"), 512, 380)
    check("Dictation: the mic is indigo", mic[2] > 200 and mic[0] < 120 and mic[1] < 110)
    _, _, pale = pixel(os.path.join(out, "icon-dictation.png"), 160, 512)
    check("  on a pale square", min(pale[:3]) > 220 and pale[3] == 255)
    if shutil.which("iconutil"):
        check("both .icns written", all(os.path.getsize(os.path.join(out, n + ".icns")) > 10000 for n in ("Standby", "Dictation")))
    else:
        print("  %-56s SKIP (no iconutil)" % "both .icns written")
finally:
    shutil.rmtree(out, ignore_errors=True)
print("FAILURES" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
