"""The companion's rules, in plain Python (no AppKit, so they are tested
anywhere; mac/native/macos/companion.py hands them the frames and acts on the answers):
where it sits (the bottom right of the main window's display each time it
comes up, snapping when dragged, growing from its corner), when it shows,
what closing the main window and Quit do, and its menu.

Frames are (x, y, w, h) in Cocoa points: origin at the bottom left, y up.
A screen is (x, y, w, h) of its usable frame: the visible frame (below the menu
bar, beside the Dock), but to the screen's own edge where the Dock isn't showing
(usable).
"""

MARGIN = 16      # from the visible frame's edge
REACH = 64       # dropped this close to a corner, it snaps there
CORNERS = ("bottom-right", "bottom-left", "top-right", "top-left")
DEFAULT = "bottom-right"


def _sides(corner):
    v, h = corner.split("-")
    return h, v


def at_corner(corner, size, screen, margin=MARGIN):
    """The origin that puts a window of this size in that corner."""
    w, h = size
    sx, sy, sw, sh = screen
    hs, vs = _sides(corner)
    x = sx + margin if hs == "left" else sx + sw - w - margin
    y = sy + margin if vs == "bottom" else sy + sh - h - margin
    return x, y


def clamp(frame, screen, margin=MARGIN):
    """Kept `margin` inside the screen (and on it at all)."""
    x, y, w, h = frame
    sx, sy, sw, sh = screen
    x = min(max(x, sx + margin), sx + sw - w - margin)
    y = min(max(y, sy + margin), sy + sh - h - margin)
    return x, y, w, h


def snap(frame, screen, reach=REACH, margin=MARGIN):
    """Where a dropped window lands: (frame, corner). Within `reach` of a
    corner it snaps there; elsewhere it stays, kept inside the screen, and the
    corner is None."""
    x, y, w, h = frame
    for corner in CORNERS:
        cx, cy = at_corner(corner, (w, h), screen, margin)
        if abs(x - cx) <= reach and abs(y - cy) <= reach:
            return (cx, cy, w, h), corner
    return clamp(frame, screen, margin), None


def nearest_sides(frame, screen):
    """For a window not in a corner: the sides of the screen it is nearer to,
    (horizontal, vertical), so it grows away from them."""
    x, y, w, h = frame
    sx, sy, sw, sh = screen
    hs = "right" if x + w / 2 > sx + sw / 2 else "left"
    vs = "top" if y + h / 2 > sy + sh / 2 else "bottom"
    return hs, vs


def edges(frame, corner, screen):
    """(horizontal, vertical): the sides the window keeps to, for the page,
    so its controls and words open away from them."""
    return _sides(corner) if corner else nearest_sides(frame, screen)


DOCK_STRIP = 8     # pt: a Dock that hides itself leaves this little of a strip (4 pt): not a Dock showing


def usable(frame, visible, dock_shown=True):
    """Where on a screen the companion goes: its visible frame, but on the Dock's
    side to the screen's own edge when the Dock isn't showing there. A full-screen
    Space hides the Dock while the visible frame still keeps its place: the orb sat
    16 pt from the right and 101 from the bottom, over nothing (the user, 2026-10-12,
    an 85 pt Dock). A Dock that hides itself keeps a strip of a few points. The menu
    bar's side stays as it is. frame, visible: the screen's (x, y, w, h)."""
    fx, fy, fw, fh = frame
    vx, vy, vw, vh = visible
    gone = lambda gap: gap > 0 and (not dock_shown or gap <= DOCK_STRIP)
    x0 = fx if gone(vx - fx) else vx
    y0 = fy if gone(vy - fy) else vy
    x1 = fx + fw if gone((fx + fw) - (vx + vw)) else vx + vw
    return (x0, y0, x1 - x0, vy + vh - y0)


def full_screen(windows, display, me=None, slack=1.0):
    """A full-screen Space on this display: a normal window (layer 0) of another
    app filling it whole, menu bar's place and all, which the green button's full
    screen does and no window in a desktop Space can. windows: the window list's
    [{"layer", "pid", "bounds": (x, y, w, h)}]; display: its frame in the same
    coordinates (the window list's: y down from the top of the first display)."""
    for w in windows:
        b = w.get("bounds")
        if w.get("layer") == 0 and w.get("pid") != me and b and all(abs(p - q) <= slack for p, q in zip(b, display)):
            return True
    return False


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return w * h if w > 0 and h > 0 else 0.0


def display_at(point, displays):
    """The display a point is on, (name, frame, visible frame): the orb's
    centre, so a drop over the line onto another display is on that one, not on
    the one holding more of its window (mostly see-through; it was pulled back
    to the first display's edge). Off every display: the nearest one.
    displays: [(name, frame, visible frame)]."""
    if not displays:
        return None
    px, py = point
    away = lambda f: max(f[0] - px, 0, px - f[0] - f[2]) + max(f[1] - py, 0, py - f[1] - f[3])
    return min(displays, key=lambda d: away(d[1]))


def orb_point(frame, shapes=None):
    """The orb's centre on screen: its circle among the hit shapes on screen
    (hit_on_screen), else the window's centre."""
    circle = next((sh for sh in shapes or () if sh[0] == "circle"), None)
    if circle:
        return circle[1], circle[2]
    x, y, w, h = frame
    return x + w / 2.0, y + h / 2.0


def home(window, displays, main):
    """The visible frame the companion comes up in, every time (the user,
    2026-10-08): that of the display the main window is on (most of it),
    whether the window is up, minimised or closed. Never where the companion
    was last time, nor the pointer's display. With no window frame, or one on
    no display, the main display.
    window: the main window's frame, or None. displays: [(name, frame, visible
    frame)]. main: the main display's name."""
    best, most = None, 0.0
    for _name, frame, visible in displays:
        area = _overlap(window, frame) if window else 0.0
        if area > most:
            best, most = visible, area
    if best is None:
        best = next((v for n, _f, v in displays if n == main), displays[0][2] if displays else None)
    return best


# The orb is for calls only (the user): a meeting never has one. A meeting's window
# minimises to the Dock and closes like any window's, and the meeting records on.


def on_close(live):
    """The main window's red button, or Cmd+W: on a call it shrinks into the orb, as
    minimise does, and only End ends it; otherwise the window hides and
    Standby keeps running (Claude Code opens it, and reaches it, any time), as a Mac
    app's window does. Quit (Cmd+Q) quits; the Dock icon brings the window back."""
    return "companion" if live == "talk" else "hide"


def on_minimise(live):
    """Minimise (Cmd+M, the yellow button, a title-bar double-click, the
    header's ⇲): into the orb on a call; otherwise to the Dock, like any window."""
    return "companion" if live == "talk" else "dock"


def shown(live, window_up):
    """The orb belongs to a live call: with none there is none. While one is,
    it is up whenever Standby's window is not (minimised, closed, or sent to the
    corner), and never both: switching to another app changes nothing,
    so the window stays where the user left it."""
    return live == "talk" and not window_up


def on_quit(live, system=False):
    """Quit (Cmd+Q, the Dock, the companion's menu): "ask" while a meeting
    records, so a slip of the keys never ends one; otherwise "quit",
    which stops whatever is live. A logout, restart or shutdown (system) never
    asks: macOS would wait on Standby, and the meeting is saved either way."""
    return "ask" if live == "meeting" and not system else "quit"


FAREWELL = 2.5       # s the mini window stays after End, saying so (the page's say())
FAREWELL_WORDS = "Call ended"     # what it says as it goes: nothing takes the user's screen


def menu():
    """The mini window's right-click menu for the call, top to bottom: (title, key
    equivalent, enabled, checked, action), with None for a separator. Nothing here
    starts a call: the mini window only exists while one is live. Its buttons' two
    (the user, 2026-10-23): Full window, End; a click on it pauses. End ends the call:
    its voice going ends it too."""
    items = [("Full window", "o", True, False, "open"), ("End", "", True, False, "stop")]
    return items + [
        None,
        ("Settings…", ",", True, False, "settings"),
        None,
        ("Quit Standby", "q", True, False, "quit"),
    ]


# ---------------------------------------------------------------- two windows
#
# The companion is two windows: the orb's, which is the anchor and never moves
# unless dragged, and the words' above it, its child window, so AppKit moves and
# hides them as one. The orb's window is larger than the orb itself, so its halo
# and shadow are not clipped, and what the user lines up on is the glyph, not the
# window: both windows are placed by the glyph's edges. The glyph sits
# (h - glyph) / 2 in from the window's outer side and its top, so the hover
# controls widen the window inwards and the glyph stays put.

GLYPH_MARGIN = 16     # the glyph's outer edges, from the screen's visible frame
WORDS_GAP = 8         # between the glyph's top and the words' window


def glyph_inset(window, glyph, inset=None):
    """How far the glyph's outer edges sit inside its window: the page's own
    number (a fixed 12, the glyph pinned to the outer corner and growing
    from it), else centred, as the older page laid it out."""
    return float(inset) if inset is not None else max(0.0, (window - glyph) / 2.0)


def orb_margin(h, glyph, margin=GLYPH_MARGIN, inset=None):
    """The orb window's own margin from the screen: the glyph's, less its inset."""
    return margin - glyph_inset(h, glyph, inset)


def orb_at_corner(corner, window, glyph, screen, margin=GLYPH_MARGIN, inset=None):
    """The orb window's origin, placed so the *glyph's* outer edges are `margin`
    from the screen. window: its side, or (w, h) with the hover controls out."""
    w, h = window if isinstance(window, (tuple, list)) else (window, window)
    return at_corner(corner, (w, h), screen, orb_margin(h, glyph, margin, inset))


def orb_resized(frame, glyph_was, size, glyph, corner, screen, inset_was=None, inset=None, held=False):
    """The orb window at a new size and glyph (resting to voice, the hover
    controls out), with the glyph's outer edges held still: in a corner, where
    orb_at_corner puts them; unsnapped, or held by a drag, where they are now
    (a drag that put it back in its corner stopped following the pointer)."""
    nw, nh = size
    if corner and not held:
        return orb_at_corner(corner, (nw, nh), glyph, screen, inset=inset) + (nw, nh)
    x, y, w, h = frame
    was, now = glyph_inset(h, glyph_was, inset_was), glyph_inset(nh, glyph, inset)
    hs, vs = edges(frame, corner, screen)
    nx = x + w - was + now - nw if hs == "right" else x + was - now
    ny = y + h - was + now - nh if vs == "top" else y + was - now
    return nx, ny, nw, nh


def glyph_rect(orb, glyph, corner, screen, inset=None):
    """The glyph on screen: `inset` in from the window's outer side and its outer
    edge (both the corner's), whether centred (the older page) or pinned
    to the outer corner."""
    ox, oy, ow, oh = orb
    i = glyph_inset(oh, glyph, inset)
    hs, vs = edges(orb, corner, screen)
    gx = ox + ow - i - glyph if hs == "right" else ox + i
    gy = oy + i if vs == "bottom" else oy + oh - i - glyph
    return gx, gy, glyph, glyph


def words_frame(orb, glyph, size, corner, screen, gap=WORDS_GAP, inset=None):
    """The words window's frame: its outer edge lined up with the glyph's outer
    edge (not the window's), and `gap` beyond the glyph's near edge, so at a
    bottom corner it floats above the orb and at a top corner below it."""
    gx, gy, g, _g = glyph_rect(orb, glyph, corner, screen, inset)
    w, h = size
    hs, vs = edges(orb, corner, screen)
    x = gx + g - w if hs == "right" else gx
    y = gy + g + gap if vs == "bottom" else gy - gap - h
    return x, y, w, h


def glass_fills(glass, w, h):
    """Whether the page's glass is the whole window (rest, rest hovered), not
    part of it (voice hovered) or none (voice)."""
    return bool(glass) and float(glass.get("w", 0)) == w and float(glass.get("h", 0)) == h


def glass_rect(glass, window, corner, frame, screen):
    """The page's glass rect, measured from the window's *inner* side (the side
    towards the screen's centre), as AppKit's frame in the window: x from the
    left edge, y from the bottom. None stays None (no glass at all).

    glass: {"x", "y", "w", "h", "radius"} in page px, y from the top.
    window: (w, h). The page only knows "inner"; which edge that is depends on
    the corner, so the mirroring is done here."""
    if not glass:
        return None
    ww, wh = window
    gx, gy, gw, gh = (float(glass.get(k, 0)) for k in ("x", "y", "w", "h"))
    hs, _vs = edges(frame, corner, screen)
    x = ww - gx - gw if hs == "left" else gx     # inner side: the left at a right corner
    y = wh - gy - gh                             # the page measures from the top
    return x, y, gw, gh, float(glass.get("radius", 0))


# ---------------------------------------------------------------- the pointer
#
# Over another app WebKit sees no hover, so the panel reads the pointer itself:
# inside the orb's circle the orb window takes the mouse (and hover is on);
# anywhere else it ignores the mouse, so a click goes to the app beneath.

NEAR = 200.0              # pt from the circle: closer, the pointer is read often
FAST, SLOW = 1 / 30.0, 1 / 5.0


def hit_shapes(msg):
    """The page's {type:"hit"} as shapes in page px (y down, from the window's top
    left), or None for the whole window (the older page, or a null):
        areas: [{x, y, r}, ...]            circles, by their centre
               [{x, y, w, h, radius}, ...]  rounded rects, by their top left
    ("shapes" is the same list), or one circle as {x, y, r} on the message itself."""
    if not isinstance(msg, dict):
        return None
    raw = next((msg[k] for k in ("areas", "shapes") if isinstance(msg.get(k), list)), [msg])
    out = []
    try:
        for sh in raw:
            if sh.get("w") is not None:
                out.append(("rect", float(sh["x"]), float(sh["y"]), float(sh["w"]), float(sh["h"]),
                            float(sh.get("radius") or 0)))
            else:
                out.append(("circle", float(sh["x"]), float(sh["y"]), float(sh["r"])))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return out or None


def hit_on_screen(shapes, frame):
    """Page shapes as shapes on screen (y up): a circle by its centre, a rect by
    its bottom left."""
    wx, wy, ww, wh = frame
    out = []
    for sh in shapes:
        if sh[0] == "circle":
            out.append(("circle", wx + sh[1], wy + wh - sh[2], sh[3]))
        else:
            _k, x, y, w, h, r = sh
            out.append(("rect", wx + x, wy + wh - y - h, w, h, r))
    return out


def shape_distance(p, sh):
    """How far a point is from a shape's edge, 0 inside it. The same in page and
    screen coordinates, as long as the shape and the point share them."""
    px, py = p
    if sh[0] == "circle":
        _k, cx, cy, r = sh
        return max(0.0, ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 - r)
    _k, x, y, w, h, r = sh
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    qx = abs(px - (x + w / 2.0)) - (w / 2.0 - r)       # the rounded rect as a distance field
    qy = abs(py - (y + h / 2.0)) - (h / 2.0 - r)
    return max(0.0, (max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2) ** 0.5 - r)


def pointer(p, shapes, frame):
    """(inside, distance): is the pointer over the orb or one of its buttons
    (any shape), and how far it is from the nearest. No shapes (an older
    page): the whole window is the orb."""
    if not shapes:
        x, y, w, h = frame
        d = shape_distance(p, ("rect", x, y, w, h, 0.0))
        return d == 0.0, d
    d = min(shape_distance(p, sh) for sh in shapes)
    return d == 0.0, d


def poll_every(distance):
    """How soon to read the pointer again: often when it is near, rarely when far."""
    return FAST if distance <= NEAR else SLOW
