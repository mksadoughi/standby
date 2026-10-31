"""The visual card's sandbox (native/macos/visualview.py), for real but never on screen: a WebKit view in a window
that is never ordered in loads a visual whose page tries what it shouldn't. It gets its size out, and its one
message; nothing gets in or out: no request to Standby's server, no image from the web, no leaving its own
document. Closing it lets everything go."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
try:
    import AppKit, Foundation  # noqa: E401
    from native.macos import visualview
except ImportError as exc:
    print("SKIP (needs PyObjC on macOS: %s)" % exc)
    sys.exit(0)
import visuals  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


check("its rules: its own document once, then nothing", visualview.may_load(visualview.HOME, False)
      and not visualview.may_load(visualview.HOME, True) and not visualview.may_load("https://example.com/", False)
      and not visualview.may_load("file:///etc/hosts", False))
check("  its messages: a size, or an https link; nothing else",
      visualview.message({"sb": "size", "want": "small", "w": "300", "h": 9e9}) == {"sb": "size", "want": "small", "w": 300, "h": 4000, "need": 0}
      and visualview.message({"sb": "open", "href": "https://example.com/a"}) == {"sb": "open", "href": "https://example.com/a"}
      and visualview.message({"sb": "open", "href": "http://example.com/"}) is None
      and visualview.message({"sb": "open", "href": "javascript:alert(1)"}) is None
      and visualview.message({"sb": "open", "href": "https://a b"}) is None and visualview.message("x") is None
      and visualview.message({"sb": "chose", "label": " Option\n 3 " + "x" * 200})["label"] == ("Option 3 " + "x" * 200)[:80]
      and visualview.message({"sb": "chose", "label": "  "}) is None and visualview.message({"sb": "chose", "label": 3}) is None)

AppKit.NSApplication.sharedApplication()
got = []
FRAG = """<div data-size="small"><h1>Probe</h1><button data-choice="Option  2">Two</button>
<script>
const out = [];
fetch("http://127.0.0.1:1/api/state").then(() => out.push("fetch")).catch(() => {});
const i = new Image(); i.onload = () => out.push("image"); i.src = "https://example.com/a.png";
const f = document.createElement("iframe"); f.src = "https://example.com/"; document.body.appendChild(f);
try { window.open("https://example.com/"); } catch (e) {}
setTimeout(() => document.querySelector("[data-choice]").click(), 1200);
setTimeout(() => webkit.messageHandlers.card.postMessage({ sb: "open", href: "https://report.invalid/" + (out.join("+") || "nothing") }), 1500);
setTimeout(() => { location.href = "https://example.com/away" }, 1800);
</script></div>"""
view = visualview.VisualView(visuals.doc(FRAG, "/nonexistent", inline=True), visuals.CSP, (0, 0, 300, 200), got.append)
win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(Foundation.NSMakeRect(-6000, -6000, 300, 200), 0, 2, False)
win.contentView().addSubview_(view.web)          # never ordered in: nothing on the screen
end = time.time() + 8
while time.time() < end and not any(m["sb"] == "open" for m in got):
    Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1))
for _ in range(10):                              # the navigation it tries after
    Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1))
sizes = [m for m in got if m["sb"] == "size"]
said = [m["href"] for m in got if m["sb"] == "open"]
check("loaded from its own scheme: its size came out (small, its content's height)", sizes and sizes[-1]["want"] == "small" and 20 < sizes[-1]["h"] < 400, got)
check("  nothing in or out: no request to Standby's server, no web image", said == ["https://report.invalid/nothing"], said)
check("a choice clicked: one message, its label as the kit sends it, and marked chosen there",
      [m["label"] for m in got if m["sb"] == "chose"] == ["Option 2"], got)
check("  and it never left its own document", str(view.web.URL().absoluteString()) == visualview.HOME, view.web.URL())
# what was picked, kept by the app: the kit marks it on loading, the others dimmed
picked = []
again = visualview.VisualView(visuals.doc('<div><button data-choice="Yes">Yes</button><button data-choice="No">No</button>'
    '<script>setTimeout(() => webkit.messageHandlers.card.postMessage({ sb: "open", href: "https://report.invalid/" + '
    '[...document.querySelectorAll("[data-choice].chosen")].map(b => b.textContent).join("+") + "/" + encodeURIComponent(document.documentElement.className) }), 600)</script></div>',
    "/nonexistent", inline=True, chosen="No"), visuals.CSP, (0, 0, 300, 120), picked.append)
win.contentView().addSubview_(again.web)
end = time.time() + 6
while time.time() < end and not any(m["sb"] == "open" for m in picked):
    Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1))
check("a visual opened again: what was picked shows picked (the others dim)", [m["href"] for m in picked if m["sb"] == "open"]
      == ["https://report.invalid/No/sb-inline%20sb-chose"], picked)
again.close()
view.close()
check("closed: let go, its messages no longer heard", view.web is None and view.on_message({"sb": "size"}) is None)

if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
