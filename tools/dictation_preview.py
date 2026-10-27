#!/usr/bin/env python3
"""Pictures of Standby Dictation's setup window and panel, without showing them.

    ~/.standby/venv/bin/python tools/dictation_preview.py OUT

Draws, off screen, in light and dark, at 2x:
  OUT/setup-<step>-<light|dark>.png    the setup window, at each of its steps
  OUT/hud-<state>-<light|dark>.png     the panel: listening, transcribing, a note
For design review: the window and the panel only appear for real. The panel's
glass is drawn by the window server, so here a plain capsule stands in for it
(and the working bars are caught at one moment); the sizes, places, type and copy are the real ones,
read from the panel itself."""
import os
import sys
import tempfile

os.environ.setdefault("STANDBY_ROOT", tempfile.mkdtemp(prefix="sbprev-"))   # never the real one
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import dictation as d  # noqa: E402
from native.macos import dictation_app as da  # noqa: E402  (its Mac parts: the app, keys, pasting)

# (permissions, the words tried, the panes sent to, what a try said instead): each step of the setup window
STATES = {
    "1-microphone": ({"listen": False, "post": False, "mic": "ask"}, None, (), None),
    "2-input-monitoring": ({"listen": False, "post": False, "mic": "allowed"}, None, (), None),
    "2-waiting": ({"listen": False, "post": False, "mic": "allowed"}, None, ("listen",), None),
    "3-accessibility": ({"listen": True, "post": False, "mic": "allowed"}, None, ("post",), None),
    "4-try": ({"listen": True, "post": True, "mic": "allowed"}, None, (), None),
    "4-try-again": ({"listen": True, "post": True, "mic": "allowed"}, None, (), "Didn't catch that. Try again."),
    "4-done": ({"listen": True, "post": True, "mic": "allowed"}, "Let's ship the release notes on Friday.", (), None),
    "mic-denied": ({"listen": False, "post": False, "mic": "denied"}, None, ("mic",), None),
}


def main(out):
    import warnings
    import objc
    from AppKit import NSAppearance, NSApplication, NSColor
    warnings.simplefilter("ignore", objc.ObjCPointerWarning)        # the CGColor handed to the layer
    os.makedirs(out, exist_ok=True)
    NSApplication.sharedApplication()
    c = da.Controller.alloc().initHeadless_(True)
    for mode, name in (("light", "NSAppearanceNameAqua"), ("dark", "NSAppearanceNameDarkAqua")):
        look = NSAppearance.appearanceNamed_(name)
        NSAppearance.setCurrentAppearance_(look)       # colors resolve against this while drawing
        for state, (perm, tried, sent, failed) in STATES.items():
            sw = da.SetupWindow(c)
            sw.window.setAppearance_(look)
            sw.update(d.setup_steps(perm, d.DEFAULTS, tried, sent, failed))
            view = sw.window.contentView()
            view.setWantsLayer_(True)
            view.layer().setBackgroundColor_(NSColor.windowBackgroundColor().CGColor())
            rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
            view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
            path = os.path.join(out, "setup-%s-%s.png" % (state, mode))
            rep.representationUsingType_properties_(4, {}).writeToFile_atomically_(path, True)   # 4: PNG
            print(path)


def huds(out):
    """The panel's three states, drawn from its own frames, font and colours."""
    import math
    from AppKit import (NSAppearance, NSBezierPath, NSColor, NSFontAttributeName, NSForegroundColorAttributeName,
                        NSImage, NSMakeRect, NSString, NSBitmapImageRep)
    for mode, name, bg, cap in (("light", "NSAppearanceNameAqua", (0.93, 0.93, 0.95), (1, 1, 1, 0.85)),
                                ("dark", "NSAppearanceNameDarkAqua", (0.12, 0.12, 0.13), (0.24, 0.24, 0.25, 0.9))):
        look = NSAppearance.appearanceNamed_(name)
        NSAppearance.setCurrentAppearance_(look)
        indigo = (0.545, 0.529, 1.0) if mode == "dark" else (0.314, 0.275, 0.898)   # the voice bars too (the user)
        for state, (text, level, spin) in (("listening", ("Listening", True, False)),
                                           ("transcribing", ("Transcribing…", False, True)),
                                           ("note", (d.ready_text(d.DEFAULTS["shortcut"]), False, False))):   # the app's own line
            h = da.HUD()
            h.panel.setAppearance_(look)
            h.show(text, level=level, spin=spin, front=False)
            w, m = h.panel.contentView().frame().size.width, 20
            img = NSImage.alloc().initWithSize_((w + 2 * m, h.H + 2 * m))
            img.lockFocus()
            NSColor.colorWithSRGBRed_green_blue_alpha_(*bg, 1.0).setFill()
            NSBezierPath.fillRect_(NSMakeRect(0, 0, w + 2 * m, h.H + 2 * m))
            NSColor.colorWithSRGBRed_green_blue_alpha_(*cap).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(m, m, w, h.H), h.R, h.R).fill()
            if level:
                NSColor.colorWithSRGBRed_green_blue_alpha_(*indigo, 1.0).setFill()
                for bar, hh in zip(h.bars, h.heights(0.7)):
                    x = bar.frame().origin.x
                    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                        NSMakeRect(m + x, m + (h.H - hh) / 2.0, h.BAR_W, hh), 1.5, 1.5).fill()
            if spin:                               # the three dots, caught mid-rise
                for bar, (up, alpha) in zip(h.work, h.dots(0.25)):
                    NSColor.colorWithSRGBRed_green_blue_alpha_(*indigo, alpha).setFill()
                    x = bar.frame().origin.x
                    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(m + x, m + (h.H - h.DOT) / 2.0 + up, h.DOT, h.DOT)).fill()
            lf = h.label.frame()
            NSString.stringWithString_(text).drawAtPoint_withAttributes_(
                (m + lf.origin.x, m + lf.origin.y), {NSFontAttributeName: h.label.font(),
                                                     NSForegroundColorAttributeName: NSColor.labelColor()})
            img.unlockFocus()
            rep = NSBitmapImageRep.imageRepWithData_(img.TIFFRepresentation())
            path = os.path.join(out, "hud-%s-%s.png" % (state, mode))
            rep.representationUsingType_properties_(4, {}).writeToFile_atomically_(path, True)
            print(path, int(w), "pt wide")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip())
    main(sys.argv[1])
    huds(sys.argv[1])
