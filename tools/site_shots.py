#!/usr/bin/env python3
"""The website's pictures (docs/img/), made-up data only, light and dark.

    python3 tools/site_shots.py [SRC]

SRC is the checkout whose app to picture (default: this one): its mac/ui.py page
and mac/companion.html through tools/ui_preview.py, in one Chrome; Standby
Dictation's panel through tools/dictation_preview.py; the icon through mac/icons.py.
Writes into this checkout's docs/img/.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE
sys.path.insert(0, str(SRC / "tools"))
import ui_preview as up  # noqa: E402

OUT = HERE / "docs" / "img"
WINDOW = ["suggest-listen", "suggest", "transcript", "ended", "voice"]
# The orb's window alone, cut to what it draws: now the window is one 340x96 canvas
# with the orb in its corner, so the picture is the orb and its pill, not the canvas.
PILLS = {"recording": "recording-hover", "attached": "attached"}
BOX = """<script>setInterval(() => {
  const rs = [...document.querySelectorAll(".cmp .orbs, .cmp .ctls")].map(e => e.getBoundingClientRect()).filter(r => r.width);
  if (!rs.length) return;
  let b = document.getElementById("shotbox");
  if (!b) { b = document.createElement("div"); b.id = "shotbox"; b.style.cssText = "position:fixed;pointer-events:none"; document.body.appendChild(b) }
  const x = Math.min(...rs.map(r => r.left)), y = Math.min(...rs.map(r => r.top));
  Object.assign(b.style, { left: x + "px", top: y + "px", width: Math.max(...rs.map(r => r.right)) - x + "px",
    height: Math.max(...rs.map(r => r.bottom)) - y + "px" }) }, 50)</script>"""
# Voice mode is two windows, the words above the orb: drawn together as the panel places
# them, the words' edge lined up with the orb's and 8 pt above it.
VOICE = {"voice-you": ("listening", "words-you"), "voice-claude": ("speaking", "words-speaking")}
# Each page draws its stand-in glass M px in from its corner, room enough for its shadow:
# the orb's window is 360x112 with the orb 12 in, 88 talking, so the orb's outer edge is
# M + 12 from its frame's; the words' frame sits 12 in from the orb's frame, its glass 8 above
# the orb: M + 12 + 88 + 8 from the bottom, less its own M.
M = 44
TWO = """<!doctype html><meta name="color-scheme" content="light dark"><body style="margin:0;background:transparent">
<div id="c" style="position:absolute;left:0;top:0;width:400px;height:238px">
<iframe src="{cmp}?s={orb}" style="position:absolute;right:0;bottom:0;width:448px;height:200px;border:0"></iframe>
<iframe src="{cmp}?s={words}#words" style="position:absolute;right:12px;bottom:108px;width:388px;height:130px;border:0"></iframe>
</div></body>"""


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="sbsite-"))
    page, cmp = tmp / "page.html", tmp / "companion.html"
    page.write_text(up.page())
    cmp.write_text(up.companion_page().replace(".preview .cmp,.preview .says{margin:16px}",
                                               ".preview .cmp,.preview .says{margin:%dpx}" % M).replace("</body>", BOX + "</body>"))
    chrome = up.Chrome()
    try:
        for dark in (False, True):
            sfx = "-dark" if dark else ""
            for scene in WINDOW:
                chrome.shoot("%s?s=%s#%s" % (page.as_uri(), scene, scene), OUT / ("%s%s.jpg" % (scene, sfx)), dark,
                             size=(1100, 720), scale=1.5, quality=82)
            # The pills on a clear background, so they sit on the page's own colour.
            chrome.call("Emulation.setDefaultBackgroundColorOverride", session=True, color={"r": 0, "g": 0, "b": 0, "a": 0})
            for name, scene in PILLS.items():
                chrome.shoot("%s?s=%s" % (cmp.as_uri(), scene), OUT / ("pill-%s%s.png" % (name, sfx)), dark,
                             size=(460, 200), scale=2, clip="#shotbox", pad=14, settle=0.8)
            for name, (orb, words) in VOICE.items():
                two = tmp / ("%s.html" % name)
                two.write_text(TWO.format(cmp=cmp.as_uri(), orb=orb, words=words))
                chrome.shoot(two.as_uri(), OUT / ("%s%s.png" % (name, sfx)), dark, size=(400, 238), scale=2, clip="#c", settle=1.2)
            chrome.call("Emulation.setDefaultBackgroundColorOverride", session=True)
    finally:
        chrome.close()
    py = os.path.expanduser("~/.standby/venv/bin/python")
    subprocess.run([py, str(SRC / "mac" / "icons.py"), str(tmp), "--png"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # The icon at 256 px (the favicon and the brand), from the iconset the installer builds.
    subprocess.run(["iconutil", "-c", "iconset", str(tmp / "Standby.icns"), "-o", str(tmp / "s.iconset")], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if (tmp / "s.iconset" / "icon_128x128@2x.png").exists():
        shutil.copy(tmp / "s.iconset" / "icon_128x128@2x.png", OUT / "icon.png")
    subprocess.run([py, str(SRC / "tools" / "dictation_preview.py"), str(tmp / "d")], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # The dictation panel, dark: it sits in a dark terminal on the page.
    f = tmp / "d" / "hud-listening-dark.png"
    if f.exists():
        shutil.copy(f, OUT / "hud-listening-dark.png")
    shutil.rmtree(tmp, ignore_errors=True)
    for f in sorted(OUT.iterdir()):
        print("ok   " + str(f))


if __name__ == "__main__":
    main()
