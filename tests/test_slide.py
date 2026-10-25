"""Moving between the list and one meeting or call slides, as a phone does (the user, 2026-10-25): opening one
from its row, it comes in from the right; back (its ‹), it goes out to the right and the list comes back.
About 250 ms, and nothing left behind after; with reduced motion, no slide at all. In a real browser on the
preview's page, at the window's 500 px.
"""
import json
import os
import sys
import tempfile
import time
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

out = Path(tempfile.mkdtemp(prefix="standby-slide-"))
html = out / "page.html"
html.write_text(P.page())
chrome = P.Chrome()


def js(expr):
    r = chrome.call("Runtime.evaluate", session=True, expression=expr, awaitPromise=True, returnByValue=True)
    if "exceptionDetails" in r:
        raise RuntimeError(str(r["exceptionDetails"])[:400])
    return r["result"].get("value")


MID = """(() => ({snap: !!document.querySelector('.mx-snap'), moving: document.getAnimations().filter(a => a.playState === 'running').length,
  open: MX.exp || null}))()"""
try:
    chrome.shoot("%s?s=mx-past#mx-past" % html.as_uri(), out / "x.png", False, size=(500, 720), settle=4)
    sid = js("[...document.querySelectorAll('.mx-prow[data-sid]')].find(r => !r.classList.contains('live')).dataset.sid")
    js("mxPastOpen(%s)" % json.dumps(sid))
    time.sleep(0.1)
    m = js(MID)
    check("opening one from its row: it slides in, the list's still copy under it", m["snap"] and m["moving"] >= 2 and m["open"] == sid, m)
    time.sleep(0.7)
    m = js(MID)
    check("  done: nothing left behind, the one open", not m["snap"] and m["moving"] == 0 and m["open"] == sid, m)
    js("mxBack()")
    time.sleep(0.1)
    m = js(MID)
    check("back (its ‹): it slides out, the list comes back", m["snap"] and m["moving"] >= 2 and m["open"] is None, m)
    time.sleep(0.7)
    m = js(MID)
    check("  done: nothing left behind", not m["snap"] and m["moving"] == 0, m)
    chrome.call("Emulation.setEmulatedMedia", session=True, features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
    js("mxPastOpen(%s)" % json.dumps(sid))
    time.sleep(0.1)
    m = js(MID)
    check("reduced motion: it opens at once, no slide", not m["snap"] and m["moving"] == 0 and m["open"] == sid, m)
finally:
    chrome.close()

print("FAILURES: %d" % fails if fails else "ALL PASS")
sys.exit(1 if fails else 0)
