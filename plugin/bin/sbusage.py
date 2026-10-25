"""Settings > Usage: the rows standby-agent writes to usage.jsonl (one per Claude Code
turn), summed by bucket, model and kind. The app reads them here, in-process.
It runs with python3 3.7 or later.

Only what Standby's own models do is shown: the work it hands to the user's Claude Code
(a meeting's lines, typed questions, deep work) is that session's, not Standby's. And no
dollars: the user's Claude subscription pays, so a price would mislead."""

import bisect
import json
import re

MEETING_BUDGET = 4               # the meeting agent's calls a minute (TEAM.md, Budgets)
USAGE_FIELDS = (("inputTokens", "input"), ("outputTokens", "output"), ("cacheReadInputTokens", "cache_read"),
                ("cacheCreationInputTokens", "cache_write"), ("costUSD", "cost_usd"))
# What Standby runs itself: the call's voice (its turns and briefings), the summaries
# (decisions, call-summary for Add to Claude Code, cc-gist for a call's voice), titles,
# and the word-fixes pass. Not listen, question or deep: the user's Claude Code work.
STANDBY_KINDS = ("voice", "briefing", "decisions", "call-summary", "cc-gist", "title", "words")
SHOWN = ("input", "output", "cache_read", "cache_write")                # tokens; never a cost


def row_time(line):
    """A row's time, read without parsing the rest ("t" comes first)."""
    m = re.match(r'\{"t": (\d+)', line)
    return int(m.group(1)) if m else None


def summary(edges, path):
    """Standby's own calls and tokens (STANDBY_KINDS) between edges[0] and edges[-1],
    by bucket (the edges: the app's local hours or days), model and kind; since when
    any of them was counted."""
    edges = sorted(set(int(e) for e in edges))
    if len(edges) < 2:
        raise ValueError("at least two edges")
    names = SHOWN
    zero = lambda: dict((n, 0) for n in names)
    totals, series, models, kinds, since = dict(zero(), calls=0), [0] * (len(edges) - 1), {}, {}, None
    try:
        fh = open(path)
    except IOError:
        fh = None
    for line in fh or []:
        t = row_time(line)
        if t is None:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") not in STANDBY_KINDS:
            continue
        since = t if since is None else min(since, t)
        if not edges[0] <= t < edges[-1]:
            continue
        series[bisect.bisect_right(edges, t) - 1] += 1
        totals["calls"] += 1
        kind = row["kind"]
        k = kinds.setdefault(kind, dict(zero(), kind=kind, calls=0, models=set()))
        k["calls"] += 1
        for model, u in (row.get("models") or {}).items():
            m = models.setdefault(model, dict(zero(), model=model, calls=0))
            m["calls"] += 1
            k["models"].add(model)
            for n in names:
                v = u.get(n) or 0
                totals[n] += v
                m[n] += v
                k[n] += v
    if fh:
        fh.close()
    for k in kinds.values():
        k["models"] = sorted(k["models"])
    most = lambda d: (-d["calls"], d.get("model") or d.get("kind"))
    return {"from": edges[0], "to": edges[-1], "totals": totals,
            "series": [{"t": edges[i], "calls": n} for i, n in enumerate(series)],
            "models": sorted(models.values(), key=most), "kinds": sorted(kinds.values(), key=most),
            "since": since}
