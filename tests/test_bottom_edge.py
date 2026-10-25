"""A meeting's or call's last row always ends clear of the window's bottom edge (the user, 2026-10-25: "a
file and give me its path" was cut in half by it), and a row under that edge fades rather than being cut.
In a real browser at the window's size, on the preview's page: an ended call and a live one, scrolled to the end.
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

out = Path(tempfile.mkdtemp(prefix="standby-edge-"))
html = out / "page.html"
html.write_text(P.page())
chrome = P.Chrome()
GAP = """(sel => { const box = document.querySelector(sel); box.scrollTop = box.scrollHeight;
  const rows = [...box.querySelectorAll('.mx-line,.mx-qa,.mx-sep2,.mx-working,.mx-hosent')].filter(r => r.offsetParent);
  const last = rows[rows.length - 1].getBoundingClientRect(), b = box.getBoundingClientRect(), cs = getComputedStyle(box);
  return {gap: b.bottom - last.bottom, mask: cs.webkitMaskImage || cs.maskImage} })"""
try:
    for scene, sel, what in (("cc-callend", "#mbody", "an ended call"), ("cc-call", "#mxcalllog", "a live call's conversation")):
        chrome.shoot("%s?s=%s#%s" % (html.as_uri(), scene, scene), out / "x.png", False, size=(500, 420), settle=10)
        r = chrome.call("Runtime.evaluate", session=True, returnByValue=True, expression=GAP + "(%s)" % json.dumps(sel))["result"].get("value")
        check(what + ": scrolled to the end, the last row ends 28 px or more above the edge", r and r["gap"] >= 28, r)
        check("  and the edge fades what passes under it", r and "linear-gradient" in (r["mask"] or ""), r)
finally:
    chrome.close()

print("FAILURES: %d" % fails if fails else "ALL PASS")
sys.exit(1 if fails else 0)
