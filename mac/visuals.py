"""A call's visuals: what the voice shows while it talks (a list, a diagram, numbers), each an HTML
fragment on Standby's small kit, drawn by a side copy of the voice beside it (standby-agent).

The agent writes each one into its spool as it goes, <qid>.v<k>.json (written whole, then renamed in):
{"state": "drawing" | "ready" | "failed", "t", "what" (its one-line brief), "html" (ready), "why" (failed)}; qid is the voice question it
goes with. take() moves them into the call's folder, one file a visual (visuals/<n>.json, n counting
from 1 in the call), so the windows list them, in order, and fetch one's fragment when it's ready.
A visual comes when it comes: one whose turn has ended is kept the same.
"""

import json
import os
import re

MAX_HTML = 64 * 1024                   # a fragment bigger than this is not shown
SPOOLED = re.compile(r"^(\d+-\d{4})\.v(\d+)\.json$")
STATES = ("drawing", "ready", "failed")


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def folder(sdir):
    return os.path.join(str(sdir), "visuals")


def _ours(sdir):
    """{src: (n, data)} for the call's visuals so far."""
    out, d = {}, folder(sdir)
    for name in os.listdir(d) if os.path.isdir(d) else []:
        if name.endswith(".json") and name[:-5].isdigit():
            data = _read(os.path.join(d, name))
            if data is not None:
                out[data.get("src") or name] = (int(name[:-5]), data)
    return out


def take(spool, call_for, now):
    """Moves the spool's visuals into their calls: call_for(qid) is the call's folder, or None when the
    visual has no call to go to (it's dropped). Returns the folders that changed."""
    changed = set()
    try:
        names = sorted(os.listdir(str(spool)))
    except OSError:
        return changed
    for name in names:
        m = SPOOLED.match(name)
        if not m:
            continue
        path = os.path.join(str(spool), name)
        data = _read(path)
        try:
            os.remove(path)
        except OSError:
            pass
        sdir = call_for(m.group(1)) if data is not None else None
        if sdir is None or data.get("state") not in STATES:
            continue
        src = name[:-5]
        have = _ours(sdir)
        n, old = have.get(src) or (max([k for k, _ in have.values()] or [0]) + 1, {})
        if old.get("state") in ("ready", "failed"):
            continue                                       # settled: a late "drawing" never undoes it
        row = {"id": n, "src": src, "qid": m.group(1), "state": data["state"],
               "at": old.get("at") or data.get("t") or now, "t": data.get("t") or now,
               "what": str(data.get("what") or old.get("what") or "")[:200]}         # the voice's one-line brief
        if isinstance(data.get("ms"), (int, float)):
            row["ms"] = int(data["ms"])
        html = data.get("html") if row["state"] == "ready" else None
        if row["state"] == "ready" and (not isinstance(html, str) or not html.strip()):
            row.update(state="failed", why="nothing to show")
        elif html is not None and len(html.encode("utf-8")) > MAX_HTML:
            row.update(state="failed", why="too big")
        elif html is not None:
            row["html"] = html
        if row["state"] == "failed":
            row["why"] = str(row.get("why") or data.get("why") or "")[:200]
        os.makedirs(folder(sdir), mode=0o700, exist_ok=True)
        _write(os.path.join(folder(sdir), "%d.json" % n), row)
        changed.add(str(sdir))
    return changed


_LISTED = {}                           # folder -> (its mtime, the list): the windows ask several times a second


def listed(sdir):
    """The call's visuals in order, without their fragments: [{id, state, qid, at}] (the windows' poll)."""
    d = folder(sdir)
    try:
        mtime = os.stat(d).st_mtime_ns
    except OSError:
        return []
    if _LISTED.get(d, (None,))[0] != mtime:
        rows = sorted(_ours(sdir).values())
        _LISTED[d] = (mtime, [{k: v for k, v in data.items() if k in ("id", "state", "qid", "at", "what", "chosen")} for _n, data in rows])
    return list(_LISTED[d][1])


def one(sdir, n):
    """Visual n with its fragment, or None."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    data = _read(os.path.join(folder(sdir), "%d.json" % n)) if n > 0 else None
    return {k: v for k, v in data.items() if k != "src"} if data else None


def choose(sdir, n, label):
    """What was picked on visual n (one of its data-choice labels), kept with it: every window draws it from here."""
    path = os.path.join(folder(sdir), "%d.json" % int(n))
    data = _read(path)
    if data is not None:
        _write(path, dict(data, chosen=str(label)[:80]))


# The visual's document: the kit, then the fragment, under a policy that lets nothing in or out. Everything is
# inline (the kit, and KaTeX or Mermaid only when the fragment uses them, its fonts as data), so the visual
# loads nothing from anywhere: the same document in the mini window's card and in the main window.
KIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visualkit")
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
       "connect-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
       "manifest-src 'none'; form-action 'none'; base-uri 'none'")
_PARTS = {}


def _text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _part(name, libs):
    """kit, math or diagram: the inline <style>/<script> for it ("" when its library isn't installed)."""
    key = (name, libs)
    if key in _PARTS:
        return _PARTS[key]
    out = ""
    try:
        if name == "kit":
            out = "<style>%s</style><script>%s</script>" % (_text(os.path.join(KIT, "kit.css")), _text(os.path.join(KIT, "kit.js")))
        elif name == "math":
            d = os.path.join(libs, "katex")
            css = re.sub(r'src:url\(fonts/([\w-]+)\.woff2\) format\("woff2"\)[^;}]*',
                         lambda m: 'src:url(data:font/woff2;base64,%s) format("woff2")' % _b64(os.path.join(d, "fonts", m.group(1) + ".woff2")),
                         _text(os.path.join(d, "katex.min.css")))
            out = "<style>%s</style><script>%s</script>" % (css, _text(os.path.join(d, "katex.min.js")))
        elif name == "diagram":
            out = "<script>%s</script>" % _text(os.path.join(libs, "mermaid.min.js"))
    except OSError:
        out = ""
    _PARTS[key] = out
    return out


def _b64(path):
    import base64
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def doc(fragment, libs, inline=False, chosen=None):
    """The whole document for one visual's sandbox. libs: where install.sh put KaTeX and Mermaid; inline: in the
    main window's conversation, on the card's own tint (a clear background); chosen: what was picked on it, which
    the kit marks (and the other choices dim)."""
    import html
    libs = str(libs)
    head = ['<meta charset="utf-8">', '<meta http-equiv="Content-Security-Policy" content="%s">' % CSP,
            '<meta name="viewport" content="width=device-width">', _part("kit", libs)]
    if "<sb-math" in fragment:
        head.insert(3, _part("math", libs))
    if "<sb-diagram" in fragment:
        head.insert(3, _part("diagram", libs))
    attrs = (' class="sb-inline"' if inline else "") + (' data-chosen="%s"' % html.escape(chosen, quote=True) if chosen else "")
    return "<!doctype html><html%s><head>%s</head><body><div id=\"sb-root\">%s</div></body></html>" % (
        attrs, "".join(head), fragment)
