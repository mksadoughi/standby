#!/usr/bin/env python3
"""The share banner, docs/img/og.png (1200x630), from source.

    python3 tools/og_banner.py [out.png]

The page's own look: its background, the logo, the headline and the line under it, and
the hero drawing (docs/img/hero.svg), still. Rendered in headless Chrome through
tools/ui_preview.py.
"""
import base64
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_preview as P  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def drawing():
    """The hero drawing as the page shows it, still: the cursor's blink left out."""
    svg = (ROOT / "docs" / "img" / "hero.svg").read_text()
    svg, n = re.subn(r'<rect x="338"[^>]*fill="#d97757">.*?</rect>', '', svg, flags=re.S)
    assert n == 1, "hero.svg's cursor has changed: update og_banner.py"
    return svg.replace("<svg ", '<svg class="draw" ', 1)


PAGE = """<!doctype html><meta charset="utf-8"><style>
*{box-sizing:border-box}
body{margin:0;width:1200px;height:630px;overflow:hidden;position:relative;color:#1b1b2f;-webkit-font-smoothing:auto;
  font:17px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;
  background:radial-gradient(1200px 700px at 85%% %(g1)s,#efeefb 0,transparent 60%%),radial-gradient(900px 600px at -10%% %(g2)s,#f3f1ea 0,transparent 60%%),#f7f6f2}
.brand{position:absolute;left:%(bx)dpx;top:%(by)dpx;display:flex;align-items:center;gap:%(bg)dpx;font:700 %(bs)dpx "Iowan Old Style","Charter","Palatino Linotype",Georgia,serif}
.brand img{width:%(ic)dpx;height:%(ic)dpx;border-radius:9px}
h1{position:absolute;left:70px;top:%(hy)spx;margin:0;font:700 %(hs)spx/%(hl)dpx "Iowan Old Style","Charter","Palatino Linotype",Georgia,serif;letter-spacing:-.01em}
p{position:absolute;left:70px;top:%(py)dpx;width:%(pw)dpx;margin:0;font-size:%(ps)dpx;line-height:%(pl)dpx;color:#66667a}
.draw{position:absolute;left:%(dx).1fpx;top:%(dy).1fpx;width:%(dw).1fpx}
</style>
<div class="brand"><img src="%(icon)s" alt="">Standby</div>
<h1>Your Claude Code,<br>right beside you.</h1>
<p>Call it, bring it to meetings, dictate anywhere. A free Claude Code plugin for your Mac.</p>
%(svg)s"""

# where things sit, measured against the banner as first published
LAYOUT = dict(bx=70, by=160, bg=12, bs=32, ic=44, hy=240, hs=63.2, hl=65, py=392, pw=640, ps=28, pl=39, dx=744.3, dy=186.3, dw=386.4, g1="-150px", g2="1500px")


def html(layout=LAYOUT):
    return PAGE % dict(layout, icon=(ROOT / "docs" / "img" / "icon.png").as_uri(), svg=drawing())


def render(out, layout=LAYOUT):
    page = Path(out).with_suffix(".html")
    page.write_text(html(layout))
    c = P.Chrome()
    try:
        c.call("Emulation.setDeviceMetricsOverride", session=True, width=1200, height=630, deviceScaleFactor=1, mobile=False)
        c.call("Page.navigate", session=True, url=page.as_uri())
        time.sleep(1.2)
        d = c.call("Page.captureScreenshot", session=True, format="png", clip={"x": 0, "y": 0, "width": 1200, "height": 630, "scale": 1})["data"]
        Path(out).write_bytes(base64.b64decode(d))
    finally:
        c.close()
        page.unlink()


if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "docs" / "img" / "og.png"))
