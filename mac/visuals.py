"""A call's visuals: what the voice shows while it talks (a list, a diagram, numbers), each an HTML
fragment on Standby's small kit, drawn by a side copy of the voice beside it (standby-agent).

The agent writes each one into its spool as it goes, <qid>.v<k>.json (written whole, then renamed in):
{"state": "drawing" | "ready" | "failed", "t", "html" (ready), "why" (failed)}; qid is the voice question it
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
               "at": old.get("at") or data.get("t") or now, "t": data.get("t") or now}
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
        _LISTED[d] = (mtime, [{k: v for k, v in data.items() if k in ("id", "state", "qid", "at")} for _n, data in rows])
    return list(_LISTED[d][1])


def one(sdir, n):
    """Visual n with its fragment, or None."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    data = _read(os.path.join(folder(sdir), "%d.json" % n)) if n > 0 else None
    return {k: v for k, v in data.items() if k != "src"} if data else None
