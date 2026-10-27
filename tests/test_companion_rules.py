"""The companion's rules, without a window: snapping to a corner within 64 pt,
growing from the snapped corner, coming back to its spot on the same display
(and to the same corner of the main display when that one is gone), when it
shows at all (only while a session is live), what closing and minimising the
main window do, and its menu."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import companion_rules as cp  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-64s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


screen = (0, 0, 1512, 945)            # a visible frame: below the menu bar, above the Dock
pill = (180, 44)
br = cp.at_corner("bottom-right", pill, screen)
check("bottom right: 16 pt in from both edges", br == (1512 - 180 - 16, 16), br)
check("top left: 16 pt in", cp.at_corner("top-left", pill, screen) == (16, 945 - 44 - 16))

f, corner = cp.snap((1300, 40, 180, 44), screen)
check("dropped near a corner: snaps there", corner == "bottom-right" and f[:2] == br, (f, corner))
f, corner = cp.snap((700, 400, 180, 44), screen)
check("dropped in the middle: stays, no corner", corner is None and f == (700, 400, 180, 44), (f, corner))
f, corner = cp.snap((1500, -20, 180, 44), screen)
check("dropped off the edge: kept 16 pt inside", corner == "bottom-right" or (f[0] <= 1512 - 180 - 16 and f[1] >= 16), (f, corner))
f, corner = cp.snap((900, -30, 180, 44), screen)
check("  even away from a corner", corner is None and f[1] == 16, (f, corner))

check("edges for the page: the snapped corner's sides", cp.edges((0, 0, 1, 1), "bottom-right", screen) == ("right", "bottom"))
check("  unsnapped: the nearer sides", cp.edges((100, 800, 180, 44), None, screen) == ("left", "top"))

# Where it comes up, every time (the user, 2026-10-08): the display the main window
# is on, never where it was last time. Two displays: the built-in, and a Studio
# Display to its right (frames, then visible frames below the menu bar and above the Dock).
BUILTIN = ("Built-in 1512x982", (0, 0, 1512, 982), (0, 0, 1512, 945))
STUDIO = ("Studio 2560x1440", (1512, 0, 2560, 1440), (1512, 0, 2560, 1415))
two = [BUILTIN, STUDIO]
check("home: the main window on the Studio Display, the Studio's visible frame",
      cp.home((2000, 300, 1100, 760), two, BUILTIN[0]) == STUDIO[2])
check("  on the built-in one, the built-in's", cp.home((100, 100, 1100, 760), two, STUDIO[0]) == BUILTIN[2])
check("  across both: the one with most of it", cp.home((1000, 100, 1100, 760), two, BUILTIN[0]) == STUDIO[2]
      and cp.home((300, 100, 1100, 760), two, STUDIO[0]) == BUILTIN[2])
check("  no window frame, or one on no display: the main display",
      cp.home(None, two, STUDIO[0]) == STUDIO[2] and cp.home((-5000, -5000, 100, 100), two, BUILTIN[0]) == BUILTIN[2])
check("  never where it was: nothing remembered to go by (no place or remember any more)",
      not hasattr(cp, "place") and not hasattr(cp, "remember"))

# The orb is for calls only (the user): a meeting never has one.
check("close on a call: into the orb", cp.on_close("talk") == "companion")
check("  a meeting, or nothing live: the window hides, Standby keeps running (the meeting records on)",
      cp.on_close("meeting") == "hide" and cp.on_close(None) == "hide")
check("minimise on a call: into the orb", cp.on_minimise("talk") == "companion")
check("  a meeting, or nothing live: to the Dock, like any window", cp.on_minimise("meeting") == "dock" and cp.on_minimise(None) == "dock")

check("nothing live, or a meeting: no orb at all, window up or not",
      not any(cp.shown(k, v) for k in (None, "meeting") for v in (True, False)))
check("a call, the window minimised, closed or sent to the corner: shown", cp.shown("talk", False) is True)
check("a call, the window up (in front or behind another app): down, never both", cp.shown("talk", True) is False)

check("Quit while a meeting records asks first", cp.on_quit("meeting") == "ask")
check("  a talk, or nothing live: quits at once", cp.on_quit("talk") == "quit" and cp.on_quit(None) == "quit")
check("  a logout, restart or shutdown never asks", cp.on_quit("meeting", system=True) == "quit")
check("End from the mini window: it says so as it goes",
      cp.FAREWELL_WORDS == "Call ended" and 1 <= cp.FAREWELL <= 4 and not hasattr(cp, "farewell"))
items = cp.menu()
check("menu: the buttons' two, Full window then End, which ends it", items[0][:5] == ("Full window", "o", True, False, "open") and items[1][:5] == ("End", "", True, False, "stop"), items)
check("  no Pause, Resume, Mute or Mini window: a click on the mini window pauses",
      not [i for i in items if i and i[0] in ("Pause", "Resume", "Mute", "Unmute", "Mini window")], items)
check("  nothing of a meeting: no Stop Recording, Pause, Resume or End Meeting",
      not [i for i in items if i and i[4] in ("resume", "end_meeting", "end_voice")]
      and not [i for i in items if i and i[0] in ("Stop Recording", "Pause")], items)
check("  no Show Captions: the words are part of voice mode",
      not [i for i in items if i and i[4] == "captions"], items)
check("  nothing that starts a session, nothing that hides capture (no New Meeting, no Hide)",
      not [i for i in items if i and i[4] in ("new", "hide")], items)
check("  Full window ⌘O, Settings ⌘,, Quit ⌘Q, two separators",
      [(i[0], i[1]) for i in items if i and i[1]] == [("Full window", "o"), ("Settings…", ","), ("Quit Standby", "q")]
      and items.count(None) == 2, items)
check("  no Show Claude… (the user removed it)", not [i for i in items if i and i[0] == "Show Claude…"], items)
# two windows. The orb's is bigger than the orb (room for its halo), so
# both are placed by the glyph's edges, not the window's.
check("the glyph's inset in its window", cp.glyph_inset(96, 72) == 12 and cp.glyph_inset(44, 26) == 9)
for state, win, glyph in (("rest", 44, 26), ("voice", 96, 72)):
    for corner in cp.CORNERS:
        o = cp.orb_at_corner(corner, win, glyph, screen)
        inset = cp.glyph_inset(win, glyph)
        gx, gy = o[0] + inset, o[1] + inset
        near_x = gx if "left" in corner else 1512 - (gx + glyph)
        near_y = gy if "bottom" in corner else 945 - (gy + glyph)
        check("%s at %s: the glyph 16 pt from both screen edges" % (state, corner),
              round(near_x) == 16 and round(near_y) == 16, (o, near_x, near_y))
o = cp.orb_at_corner("bottom-right", 96, 72, screen)
orb = (o[0], o[1], 96, 96)
f = cp.words_frame(orb, 72, (300, 37), "bottom-right", screen)
check("the words line up with the glyph's outer edge, not the window's",
      round(f[0] + 300) == round(orb[0] + 96 - 12), (f, orb))
check("  and float 8 pt above the glyph's top (so they overlap the orb's clear top)",
      round(f[1]) == round(orb[1] + 96 - 12 + 8), (f, orb))
tl = cp.orb_at_corner("top-left", 96, 72, screen)
orb_tl = (tl[0], tl[1], 96, 96)
f = cp.words_frame(orb_tl, 72, (300, 37), "top-left", screen)
check("at a top-left corner: mirrored, below the glyph and aligned left",
      round(f[0]) == round(orb_tl[0] + 12) and round(f[1] + 37) == round(orb_tl[1] + 12 - 8), (f, orb_tl))
check("  every words window stays on the screen",
      all(0 <= cp.words_frame(cp.orb_at_corner(c, 96, 72, screen)[:2] + (96, 96), 72, (300, 58), c, screen)[0]
          and cp.words_frame(cp.orb_at_corner(c, 96, 72, screen)[:2] + (96, 96), 72, (300, 58), c, screen)[1] >= 0
          for c in cp.CORNERS))
# The hover controls widen the orb's window inwards: the glyph is (h - glyph)/2
# in from the outer side and the top whatever the width, so it stays put.
off = []
for corner in cp.CORNERS:
    rest = cp.orb_at_corner(corner, 44, 26, screen) + (44, 44)
    for size, glyph in (((146, 44), 26), ((96, 96), 72), ((208, 96), 72)):
        f = cp.orb_resized(rest, 26, size, glyph, corner, screen)
        inset = cp.glyph_inset(size[1], glyph)
        gx = f[0] + f[2] - inset - glyph if "right" in corner else f[0] + inset
        gy = f[1] + inset
        near = (gx if "left" in corner else 1512 - (gx + glyph), gy if "bottom" in corner else 945 - (gy + glyph))
        if near != (16, 16):
            off.append((corner, size, f, near))
check("at every corner, rest to hovered, voice and voice hovered: the glyph 16 pt in", not off, off)
free = (700.0, 300.0, 44.0, 44.0)                    # dropped in the left half, low
f = cp.orb_resized(free, 26, (208, 96), 72, None, screen)
check("unsnapped: the glyph's outer edges stay where they are (left and bottom here)",
      f[0] + 12 == 700 + 9 and f[1] + 12 == 300 + 9, f)
f = cp.orb_resized((1000.0, 700.0, 44.0, 44.0), 26, (146, 44), 26, None, screen)
check("  right half, high: the right and top edges", f[0] + f[2] == 1044 and f[1] + f[3] == 744, f)
check("the orb window's margin: the glyph's 16 less its inset", cp.orb_margin(44, 26) == 7 and cp.orb_margin(96, 72) == 4)
o = cp.orb_at_corner("bottom-right", (208, 96), 72, screen) + (208, 96)
f = cp.words_frame(o, 72, (300, 37), "bottom-right", screen)
check("voice hovered (208 wide): the words still line up with the glyph, 12 in from the right",
      f[0] + f[2] == o[0] + 208 - 12, (o, f))

# The page's glass rect is measured from the window's inner side, y from the
# top; AppKit wants x from the left, y from the bottom. The panel mirrors it.
br = (1316.0, 4.0, 208.0, 96.0)
g = cp.glass_rect({"x": 0, "y": 30, "w": 104, "h": 36, "radius": 18}, (208, 96), "bottom-right", br, screen)
check("voice hovered at a right corner: the buttons' glass on the inner (left) side",
      g == (0.0, 30.0, 104.0, 36.0, 18.0), g)
bl = (4.0, 4.0, 208.0, 96.0)
g = cp.glass_rect({"x": 0, "y": 30, "w": 104, "h": 36, "radius": 18}, (208, 96), "bottom-left", bl, screen)
check("  at a left corner: mirrored, so it stays on the inner side (the right)",
      g == (104.0, 30.0, 104.0, 36.0, 18.0), g)
g = cp.glass_rect({"x": 0, "y": 4, "w": 104, "h": 30, "radius": 15}, (208, 96), "bottom-right", br, screen)
check("  y from the page's top becomes AppKit's y from the bottom", g[1] == 96 - 4 - 30, g)
g = cp.glass_rect({"x": 0, "y": 0, "w": 44, "h": 44, "radius": 22}, (44, 44), "top-left", (16, 885, 44, 44), screen)
check("full-window glass (rest) is the whole window at any corner", g == (0.0, 0.0, 44.0, 44.0, 22.0), g)
check("no glass (voice): None", cp.glass_rect(None, (96, 96), "bottom-right", br, screen) is None)
check("the glass fills the window at rest and rest hovered; not in voice, nor behind the buttons only",
      cp.glass_fills({"x": 0, "y": 0, "w": 44, "h": 44, "radius": 22}, 44, 44)
      and cp.glass_fills({"x": 0, "y": 0, "w": 146, "h": 44, "radius": 22}, 146, 44)
      and not cp.glass_fills(None, 96, 96) and not cp.glass_fills({"x": 0, "y": 30, "w": 138, "h": 36}, 242, 96))
# the glyph pinned 12 pt in from the outer corner of a fixed 96 pt window,
# growing from that corner: 26 at rest, 40 peeking, 72 in voice mode.
for corner in cp.CORNERS:
    o = cp.orb_at_corner(corner, 96, 26, screen, inset=12) + (96, 96)
    near = []
    for g in (26, 40, 72):
        gx, gy, gw, gh = cp.glyph_rect(o, g, corner, screen, inset=12)
        near.append((gx if "left" in corner else 1512 - (gx + gw), gy if "bottom" in corner else 945 - (gy + gh)))
    check("at %s: the window 4 pt in, every glyph 16 pt in from both edges" % corner,
          (o[0] if "left" in corner else 1512 - o[0] - 96) == 4 and set(near) == {(16, 16)}, (o, near))
o = cp.orb_at_corner("bottom-right", 96, 26, screen, inset=12) + (96, 96)
f = cp.words_frame(o, 26, (184, 44), "bottom-right", screen, inset=12)
check("  the words: 8 pt above the resting glyph, lined up with its outer edge",
      f[0] + 184 == 1512 - 16 and f[1] == 16 + 26 + 8, f)
f = cp.words_frame(o, 72, (184, 44), "bottom-right", screen, inset=12)
check("  and above the voice glyph, which grew from the same corner", f[1] == 16 + 72 + 8, f)
c = cp.hit_on_screen(cp.hit_shapes({"x": 71, "y": 71, "r": 13}), (1412.0, 4.0, 96.0, 96.0))
check("the page's hit circle on screen (page y down, screen y up)", c == [("circle", 1483.0, 29.0, 13.0)], c)
check("  the pointer over the orb: inside", cp.pointer((1483.0, 29.0), c, None)[0] is True)
inside, d = cp.pointer((1420.0, 90.0), c, (1412.0, 4.0, 96.0, 96.0))
check("  in the window's empty corner: outside, a few points off", inside is False and 0 < d < 100, d)
# The orb keeps three buttons beside it (the user's choice): the hit region is shapes.
win = (1300.0, 4.0, 208.0, 96.0)
shapes = cp.hit_shapes({"type": "hit", "shapes": [{"x": 171, "y": 71, "r": 13},
                                                   {"x": 100, "y": 62, "w": 52, "h": 18, "radius": 9}]})
on = cp.hit_on_screen(shapes, win)
check("shapes: a circle and a rounded rect, both on screen", on == [("circle", 1471.0, 29.0, 13.0),
                                                                   ("rect", 1400.0, 20.0, 52.0, 18.0, 9.0)], on)
check("  a point on the button: inside", cp.pointer((1426.0, 29.0), on, win)[0] is True)
check("  the gap between the button and the orb: outside", cp.pointer((1455.0, 29.0), on, win)[0] is False)
check("  the button's rounded corner is not the button", cp.pointer((1401.0, 21.0), on, win)[0] is False
      and cp.pointer((1409.0, 21.0), on, win)[0] is True)
check("  how far: from the nearest shape", round(cp.pointer((1455.0, 29.0), on, win)[1], 1) == 3.0,
      cp.pointer((1455.0, 29.0), on, win))
check("  the page's name for the list, areas, means the same",
      cp.hit_shapes({"type": "hit", "areas": [{"x": 171, "y": 71, "r": 13}]}) == [("circle", 171.0, 71.0, 13.0)])
check("  a malformed shape: the whole window, as with no shapes",
      cp.hit_shapes({"shapes": [{"x": 1}]}) is None and cp.hit_shapes({"x": None}) is None)
check("  near: read 30 times a second; far: 5", cp.poll_every(50) == cp.FAST and cp.poll_every(500) == cp.SLOW)
check("  no circle (an older page): the window itself is the orb",
      cp.pointer((1420.0, 90.0), None, (1412.0, 4.0, 96.0, 96.0)) == (True, 0.0)
      and cp.pointer((1300.0, 90.0), None, (1412.0, 4.0, 96.0, 96.0))[0] is False)

check("  one End, and nothing that only ends the voice", [i[0] for i in items if i].count("End") == 1
      and not [i for i in items if i and i[4] == "end_voice"], items)

# ---------------------------------------------------------------- the same margin right and bottom
# The user, 2026-10-12: the orb 16 pt from the right and 101 from the bottom of a
# full-screen app, where no Dock showed: the visible frame keeps the Dock's 85 pt
# there. Made-up frames, as AppKit gives them on a 1512x982 Mac (menu bar 33 pt).
FULL = (0, 0, 1512, 982)


def glyph_margins(corner, frame, visible, dock_shown, window=(96, 96)):
    """(from the screen's right or left, from its bottom or top) of the resting glyph
    in that corner, the hover row out or not (window)."""
    u = cp.usable(frame, visible, dock_shown)
    o = cp.orb_at_corner(corner, window, 26, u, inset=12) + tuple(window)
    gx, gy, gw, gh = cp.glyph_rect(o, 26, corner, u, inset=12)
    fx, fy, fw, fh = frame
    side = gx - fx if "left" in corner else fx + fw - (gx + gw)
    end = gy - fy if "bottom" in corner else fy + fh - (gy + gh)
    return side, end


bottom_dock = (0, 85, 1512, 864)
check("Dock at the bottom, showing: 16 pt from the right, and 16 above the Dock (never under it)",
      glyph_margins("bottom-right", FULL, bottom_dock, True) == (16, 85 + 16), glyph_margins("bottom-right", FULL, bottom_dock, True))
check("  the user's screen: a full-screen app, the Dock hidden: 16 from the right, 16 from the bottom",
      glyph_margins("bottom-right", FULL, bottom_dock, False) == (16, 16), glyph_margins("bottom-right", FULL, bottom_dock, False))
check("    the hover row out: the glyph still where it was",
      glyph_margins("bottom-right", FULL, bottom_dock, False, (208, 96)) == (16, 16),
      glyph_margins("bottom-right", FULL, bottom_dock, False, (208, 96)))
check("    the menu bar's side kept as it is", cp.usable(FULL, bottom_dock, False) == (0, 0, 1512, 949),
      cp.usable(FULL, bottom_dock, False))
autohide = (0, 4, 1512, 945)
check("a Dock that hides itself (a 4 pt strip): 16 and 16, shown or not",
      glyph_margins("bottom-right", FULL, autohide, True) == (16, 16) == glyph_margins("bottom-right", FULL, autohide, False))
left_dock, right_dock = (80, 0, 1432, 949), (0, 0, 1432, 949)
check("Dock on the left, showing: bottom right 16 and 16; bottom left 16 beside the Dock",
      glyph_margins("bottom-right", FULL, left_dock, True) == (16, 16)
      and glyph_margins("bottom-left", FULL, left_dock, True) == (80 + 16, 16), glyph_margins("bottom-left", FULL, left_dock, True))
check("Dock on the right, showing: 16 beside the Dock, 16 from the bottom",
      glyph_margins("bottom-right", FULL, right_dock, True) == (80 + 16, 16), glyph_margins("bottom-right", FULL, right_dock, True))
check("  a full-screen app there: 16 and 16, at either side's Dock",
      glyph_margins("bottom-right", FULL, right_dock, False) == (16, 16) and glyph_margins("bottom-left", FULL, left_dock, False) == (16, 16))
second = (1512, 0, 1920, 1080)
check("a display with no Dock: as it was", cp.usable(second, (1512, 0, 1920, 1055), True) == (1512, 0, 1920, 1055)
      and cp.usable(second, (1512, 0, 1920, 1055), False) == (1512, 0, 1920, 1055))
check("  a top corner keeps below the menu bar in a full-screen Space too",
      glyph_margins("top-right", FULL, bottom_dock, False)[1] == 33 + 16, glyph_margins("top-right", FULL, bottom_dock, False))
dropped, corner = cp.snap((1380, 10, 96, 96), cp.usable(FULL, bottom_dock, False), margin=cp.orb_margin(96, 26, inset=12))
check("  dropped near the corner there: it snaps to the same 16 and 16", corner == "bottom-right" and dropped[:2] == (1412, 4), dropped)

# A full-screen Space, told from the window list (layers, owners and bounds; y down).
disp = (0, 0, 1512, 982)
win = lambda layer, pid, b: {"layer": layer, "pid": pid, "bounds": b}
check("full screen: another app's window filling the display", cp.full_screen([win(0, 41, (0, 0, 1512, 982))], disp, me=7))
check("  not: a window filling the visible frame (zoomed), the Dock's own, Standby's own, or one on another display",
      not cp.full_screen([win(0, 41, (0, 33, 1512, 864)), win(20, 9, (0, 0, 1512, 982)), win(0, 7, (0, 0, 1512, 982)),
                          win(0, 41, (1512, 0, 1920, 1080))], disp, me=7))
check("  the second display's own full screen", cp.full_screen([win(0, 41, (1512.0, -98.0, 1920.0, 1080.0))],
                                                               (1512, -98, 1920, 1080), me=7))

# Two displays: a laptop's, and a wide one above it, further left (the user's, 2026-10-26).
LAPTOP, WIDE = (0, 0, 1470, 956), (-1029, 956, 3440, 1440)
two = [("Built-in", LAPTOP, (0, 77, 1470, 846)), ("Wide", WIDE, WIDE)]
check("the orb's display: the one under its centre", cp.display_at((1300, 990), two)[0] == "Wide"
      and cp.display_at((1300, 900), two)[0] == "Built-in")
check("  off every display (past the laptop's right, below the wide one): the nearest", cp.display_at((1600, 500), two)[0] == "Built-in")
# Dragged up across the line, its glyph at the window's top (laid out for a top side): the orb is
# over the wide display, most of its 360 x 112 window (see-through) still over the laptop.
orb = (1058.0, 891.0, 360.0, 112.0)    # 65 of its 112 over the laptop, its centre 9 over the line
centre = cp.orb_point(orb, cp.hit_on_screen([("circle", 322.0, 38.0, 26.0)], orb))
check("  the orb's centre: its circle, not the window's", centre == (1380.0, 965.0), centre)
on = cp.display_at(centre, two)
dropped, corner = cp.snap(orb, on[2], margin=cp.orb_margin(112, 52, inset=12))
check("dropped over the line: on the display it's over, not clamped back to the first one's edge",
      on[0] == "Wide" and dropped[1] >= WIDE[1] and corner is None, (on[0], dropped, corner))
old_way, _c = cp.snap(orb, two[0][2], margin=cp.orb_margin(112, 52, inset=12))
check("  (the display with more of its window would have pulled it under the line)", old_way[1] + 112 <= LAPTOP[3], old_way)
# Mid-drag the page may resize the window (the pill's text changing): it stays under the pointer.
moved = (700.0, 1500.0, 360.0, 112.0)
f = cp.orb_resized(moved, 52, (360, 112), 52, "bottom-right", WIDE, inset_was=12, inset=12, held=True)
check("a drag holds it: a resize mid-drag never puts it back in its corner", f == moved, f)
f = cp.orb_resized(moved, 52, (360, 112), 52, "bottom-right", WIDE, inset_was=12, inset=12)
check("  (not dragged: in its corner, as before)", f[:2] == cp.orb_at_corner("bottom-right", (360, 112), 52, WIDE, inset=12), f)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
