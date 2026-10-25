"""What's live sits in one row at the bottom (the user, 2026-10-25: option B), one height whatever is being
said: none, a few words, two lines, many more; the newest show. So the transcript or conversation above it
never moves. A live meeting's row and a call's, in a real browser on the preview's page at the window's size.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ui_preview as P  # noqa: E402

fails = 0


def check(label, ok, detail=None):
    global fails
    print("  %-66s%s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails += 1
        if detail is not None:
            print("    " + json.dumps(detail, default=str))


if not os.path.exists(P.CHROME):
    print("  no Google Chrome on this Mac: skipped")
    print("ALL PASS")
    sys.exit(0)

out = Path(tempfile.mkdtemp(prefix="standby-livebox-"))
html = out / "page.html"
html.write_text(P.page())
chrome = P.Chrome()
WORDS = ["", "so for guests", "so for guests we keep two minutes, and regional fares wait until Thursday, when Ines has the numbers",
         " ".join(["and then the fares endpoint gets the same cache as the routing call, so the p99 stays under three hundred"] * 4)]


def measure(set_words, row, above, words):
    return chrome.call("Runtime.evaluate", session=True, returnByValue=True, awaitPromise=True, expression="""(async () => { const out = [];
      for (const w of %s) { (%s)(w); await new Promise(r => setTimeout(r, 30));
        const b = document.querySelector(%s), a = document.querySelector(%s).getBoundingClientRect(), wd = b.querySelector(%s);
        const last = wd && wd.lastElementChild ? wd.lastElementChild.getBoundingClientRect() : null, wr = wd ? wd.getBoundingClientRect() : null;
        out.push({h: b.getBoundingClientRect().height, top: a.top, bottom: a.bottom, foot: w ? !!last && Math.abs(last.bottom - wr.bottom) < 22 : true}) }
      return out })()""" % (json.dumps(WORDS), set_words, json.dumps(row), json.dumps(above), json.dumps(words)))["result"]["value"]


try:
    chrome.shoot("%s?s=mx-hear#mx-hear" % html.as_uri(), out / "x.png", False, size=(500, 720), settle=4)
    v = measure("w => { MXW['sample-meet'].lv.partial = w; mxPaint() }", "#mxdock", "#mbody", ".mx-dwords")
    check("a meeting's row: one height, none to many words", max(x["h"] for x in v) - min(x["h"] for x in v) < 0.5, v)
    check("  the transcript above never moves", max(x["bottom"] for x in v) - min(x["bottom"] for x in v) < 0.5, v)
    check("  the newest words at the row's foot", all(x["foot"] for x in v), v)
    chrome.shoot("%s?s=cc-call#cc-call" % html.as_uri(), out / "x.png", False, size=(500, 720), settle=9)
    v = measure("w => { document.getElementById('vclaude').textContent = w }", ".vstage", "#mxcalllog", ".vwords")
    check("a call's row: one height, none to many words", max(x["h"] for x in v) - min(x["h"] for x in v) < 0.5, v)
    check("  the conversation above never moves", max(x["bottom"] for x in v) - min(x["bottom"] for x in v) < 0.5, v)
finally:
    chrome.close()

print("FAILURES: %d" % fails if fails else "ALL PASS")
sys.exit(1 if fails else 0)
