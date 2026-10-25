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


def slide(expr):
    # Observe the real Web Animations at their midpoint, in one browser task.
    # Wall-clock sleeps can miss a 250 ms animation on a busy hosted runner.
    return js("""(async () => {
      const original = Element.prototype.animate, animations = [];
      Element.prototype.animate = function(...args) {
        const a = original.apply(this, args);
        a.pause(); a.currentTime = a.effect.getTiming().duration / 2;
        animations.push(a); return a;
      };
      try {
        await (%s); await Promise.resolve();
        const mid = {snap: !!document.querySelector('.mx-snap'), open: MX.exp || null,
          moving: animations.length,
          progress: animations.map(a => a.effect.getComputedTiming().progress)};
        const ended = animations.map(a => new Promise(r => a.addEventListener('finish', r, {once: true})));
        animations.forEach(a => a.finish());
        await Promise.all(ended);
        return {mid, done: %s};
      } finally { Element.prototype.animate = original; }
    })()""" % (expr, MID))


try:
    chrome.shoot("%s?s=mx-past#mx-past" % html.as_uri(), out / "x.png", False, size=(500, 720), settle=4)
    chrome.call("Emulation.setEmulatedMedia", session=True, features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
    sid = js("[...document.querySelectorAll('.mx-prow[data-sid]')].find(r => !r.classList.contains('live')).dataset.sid")
    state = slide("mxPastOpen(%s)" % json.dumps(sid))
    m = state["mid"]
    check("opening one from its row: it slides in, the list's still copy under it", m["snap"] and m["moving"] >= 2 and m["open"] == sid
          and all(0 < p < 1 for p in m["progress"]), m)
    m = state["done"]
    check("  done: nothing left behind, the one open", not m["snap"] and m["moving"] == 0 and m["open"] == sid, m)
    state = slide("mxBack()")
    m = state["mid"]
    check("back (its ‹): it slides out, the list comes back", m["snap"] and m["moving"] >= 2 and m["open"] is None
          and all(0 < p < 1 for p in m["progress"]), m)
    m = state["done"]
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
