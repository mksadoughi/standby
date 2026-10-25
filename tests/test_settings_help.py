"""Every (?) in Settings shows its help (the user, 2026-10-21: none did, e.g. Hold to talk's).

In a real browser, as the user does it: Settings open (a modal dialog, in the page's top layer), each
section in turn, each (?) clicked with the mouse. Its help shows on top, inside the window, with its own
words; a second click, Esc and a click outside each close it, and Esc closes only the help, not Settings.
The page and its sample data are the preview's (tools/ui_preview.py); one headless Chrome for the run.
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

out = Path(tempfile.mkdtemp(prefix="standby-help-"))
html = out / "page.html"
html.write_text(P.page())
chrome = P.Chrome()


def js(expr):
    r = chrome.call("Runtime.evaluate", session=True, expression=expr, awaitPromise=True, returnByValue=True)
    if "exceptionDetails" in r:
        raise RuntimeError(r["exceptionDetails"].get("text", "") + " " + str(r["exceptionDetails"].get("exception", {}).get("description", "")))
    return r["result"].get("value")


def click(x, y):
    for kind in ("mousePressed", "mouseReleased"):
        chrome.call("Input.dispatchMouseEvent", session=True, type=kind, x=x, y=y, button="left", clickCount=1)
    time.sleep(0.05)


def escape():
    for kind in ("keyDown", "keyUp"):
        chrome.call("Input.dispatchKeyEvent", session=True, type=kind, key="Escape", code="Escape", windowsVirtualKeyCode=27)
    time.sleep(0.05)


POP = """(() => { const p = document.querySelector(".mx-helpop"); if (!p) return null; const r = p.getBoundingClientRect();
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return {text: p.textContent, left: r.left, right: r.right, top: r.top, bottom: r.bottom, onTop: !!top && p.contains(top)} })()"""

try:
    # the dictation scene: its setup still missing a permission, so its (?) shows too
    chrome.shoot("%s?s=dictation#dictation" % html.as_uri(), out / "x.png", False, size=(500, 720), settle=3)
    sections = js('[...document.querySelectorAll(".setnav button[data-p]")].map(b => b.dataset.p)')
    check("Settings is open, its sections listed", js('settings.open') and len(sections) == 4, sections)
    clicked = []
    for sec in sections:
        helps = js("""(() => { setPane(%s); return [...document.querySelectorAll('.pane[data-p=%s] .mx-help')]
          .filter(h => !h.hidden && h.offsetParent).map(h => { h.scrollIntoView({block: "center"}); const r = h.getBoundingClientRect();
            return {x: r.left + r.width / 2, y: r.top + r.height / 2, text: h.getAttribute("title") || h.dataset.mxtip || "",
              where: (h.closest("label") || h.parentNode).textContent.trim().slice(0, 40)} }) })()""" % (json.dumps(sec), json.dumps(sec)))
        for h in helps:
            # each one scrolled into view again just before its click (the last one's may have moved the pane)
            pos = js("""(() => { const h = [...document.querySelectorAll('.pane[data-p=%s] .mx-help')].filter(h => !h.hidden && h.offsetParent)[%d];
              h.scrollIntoView({block: "center"}); const r = h.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2] })()"""
                     % (json.dumps(sec), helps.index(h)))
            time.sleep(0.05)
            click(*pos)
            p = js(POP)
            ok = bool(p) and h["text"] and p["text"] == h["text"] and p["onTop"] and p["left"] >= 8 and p["right"] <= 500 - 8 and p["top"] >= 0 and p["bottom"] <= 720
            check("%s > %s (?): its help, on top, inside the window" % (sec, h["where"]), ok, {"want": h["text"], "got": p})
            click(*pos)
            check("  a second click closes it", js(POP) is None)
            clicked.append(h["where"])
    check("every section's (?) was tried, Hold to talk among them",
          len(clicked) >= 9 and any(w.startswith("Hold to talk") for w in clicked), clicked)
    # Esc and a click outside, on one of them
    js('setPane("dictation")')
    pos = js("""(() => { const h = document.querySelector('#kholdf .mx-help'); const r = h.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2] })()""")
    hold = js('$("s_hold").checked')
    click(*pos)
    check("clicking the (?) leaves its switch as it was", js('$("s_hold").checked') == hold and js(POP) is not None)
    escape()
    check("Esc closes the help, and only the help: Settings stays on the section",
          js(POP) is None and js("settings.open") and js("SETP") == "dictation")
    click(*pos)
    click(250, 700)
    check("a click outside closes it", js(POP) is None)
finally:
    chrome.close()

print("FAILURES: %d" % fails if fails else "ALL PASS")
sys.exit(1 if fails else 0)
