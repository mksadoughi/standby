"""A call, turn by turn, as one HTML page, from its trace (sessions/<sid>/trace.jsonl: plugin/bin/sbtrace.py).

    python tools/call_trace.py <talk-session> [out.html]

Each turn opens with where its time went, from the end of their words to the voice's first sound:
hearing (the turn decided over), waiting (the voice's model busy: on what), preparing the turn, the model
to its first word, and the voice to its first sound. Under it, the turn's events in order: what was
heard, the question and the notes folded in, the exact message the voice's model got and its system prompt
(folded), the model's whole reply, each sentence said or cut, who had
the floor, talk-over decisions, hand-offs and pauses. A call from before the trace: call_trace_logs.py.

The page holds the user's words and the replies, as the transcript does: it's written where you say,
for you to read, and is never sent anywhere."""
import json
import os
import runpy
import sys
import time

ROOT = os.path.expanduser(os.environ.get("STANDBY_ROOT", "~/.standby"))
PARTS = (("hear", "Hearing", "#8a8a99"), ("wait", "Waiting", "#d9443b"), ("prep", "Preparing", "#c77700"),
         ("model", "Model", "#6b4fe0"), ("other", "Between", "#b5b5c0"), ("voice", "Voice", "#0f8b8d"))


def load(sid):
    path = os.path.join(ROOT, "sessions", sid, "trace.jsonl")
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue                         # a line cut short by a crash: the rest still reads
    rows.sort(key=lambda r: r.get("t", 0))
    return rows


def turns_of(rows):
    """The agent's events know their question, not the turn: each takes the turn its question was asked in."""
    by_q = {r["q"]: r.get("turn") for r in rows if r.get("ev") == "ask_q" and r.get("q")}
    for r in rows:
        if r.get("turn") is None and r.get("q") in by_q:
            r["turn"] = by_q[r["q"]]
    return rows


def breakdown(evs):
    """Where a turn's time went, in ms: {part: ms}, the total, and what it waited on."""
    timing = next((e for e in evs if e["ev"] == "timing"), None)
    wait = next((e for e in reversed(evs) if e["ev"] == "wait"), None)
    if timing is None:
        return None
    p = timing.get("parts") or {}
    first = timing.get("first_word_ms")
    out = {"hear": timing.get("turn_over_ms"), "wait": p.get("queue"), "prep": p.get("prep"),
           "model": p.get("engine"), "voice": timing.get("voice_ms")}
    known = sum(v for k, v in out.items() if k in ("wait", "prep", "model") and v)
    out["other"] = max(0, first - known) if first is not None else None
    on = ", ".join("%s %.1fs" % (w["what"], w["ms"] / 1000.0) for w in (wait or {}).get("on") or [])
    return {"parts": out, "total": timing.get("total_ms"), "first_word": first, "early_ms": timing.get("early_ms"),
            "on": on}


def text_of(e):
    """One event in plain words."""
    ev = e["ev"]
    f = lambda k: e.get(k)
    if ev == "heard":
        return {"kept": "", "floor": "(while the voice had the floor) ", "someone_else": "(someone else) ",
                "echo": "(its own words heard back) "}.get(f("fate"), "") + (f("text") or "")
    if ev == "decode":
        return "decoded %s-%ss in %s ms%s%s" % (f("a"), f("b"), f("ms"), " (early)" if f("early") else "",
                                                 " (noise)" if f("noise") else "")
    if ev == "turn_likely":
        return "%s (%s, p=%s)" % ("likely over" if f("likely") else "not over yet", f("why"), f("p"))
    if ev == "turn_end":
        return "turn over (p=%s, %s ms after their words)" % (f("p"), f("waited_ms"))
    if ev == "ask":
        return "%s%s" % ("asked early: " if f("early") else "asked: ", f("words"))
    if ev == "early":
        return "early question %s%s" % (f("step"), " (%s)" % f("why") if f("why") else "")
    if ev == "wait":
        on = ", ".join("%s %.1fs" % (w["what"], w["ms"] / 1000.0) for w in f("on") or [])
        notes = ", ".join("%s %d chars" % (n["kind"], n["chars"]) for n in f("notes") or [])
        return "the voice's model took it after %.1fs%s%s" % ((f("ms") or 0) / 1000.0, "; busy with " + on if on else "",
                                                              "; notes: " + notes if notes else "")
    if ev == "model":
        tok = f("tokens") or {}
        t = ", ".join("%s: %s in, %s out, %s cache read, %s cache written" % (
            m.split("-")[1] if "-" in m else m, u.get("input"), u.get("output"), u.get("cache_read"), u.get("cache_write"))
            for m, u in tok.items())
        return "%s: %s, first token %s ms, %s ms in all%s\n%s" % (f("model"), f("outcome"), f("first_token_ms"),
                                                                f("model_ms"), " (%s)" % t if t else "", f("reply") or "")
    if ev == "voice_start":
        return "the voice's Claude Code started on %s (%s)" % (f("model"), "a new conversation" if f("fresh") else "its conversation")
    if ev == "say":
        return "says: %s" % f("text")
    if ev == "said":
        return "said sentence %s (sound after %s ms, played %s ms)" % (f("id"), f("synth_ms"), f("play_ms"))
    if ev == "say_cut":
        return "cut %s ms into sentence %s, %s more dropped" % (f("at_ms"), f("id"), f("dropped"))
    if ev == "floor":
        return "floor: %s" % f("who")
    if ev == "barge":
        return "talked over (%s): %s%s" % (f("phase"), f("action"), " · %s" % f("words") if f("words") else "")
    if ev == "turn_done":
        return "turn %s%s" % (f("outcome"), " · heard: %s" % f("heard") if f("heard") else "")
    if ev == "handoff":
        return "hand-off %s: %s" % (f("step"), f("work") or f("text") or f("why") or "")
    if ev in ("told", "told_done"):
        return "what came back (%s): %s" % (f("how"), "told alone" + (" after the pause" if f("after_pause") else "")
                                             if ev == "told" else "said %r in %s ms" % (f("said"), f("ms")))
    if ev in ("pause", "resume"):
        return "%s (%s)" % ("paused" if ev == "pause" else "resumed", f("by"))
    if ev == "end":
        return "the call ended (%s)" % (f("why") or f("by") or "")
    rest = {k: v for k, v in e.items() if k not in ("t", "src", "turn", "ev")}
    return "%s %s" % (ev, json.dumps(rest, ensure_ascii=False))


def folds(e):
    """The long texts behind an event, folded on the page: [(label, text)]."""
    if e["ev"] == "model" and e.get("sent"):
        return [["What the voice's model was sent", e["sent"]]]
    if e["ev"] == "voice_start" and e.get("system_prompt"):
        return [["Its system prompt (tools: %s)" % (e.get("tools") or "-"), e["system_prompt"]]]
    return []


QUIET = {"floor", "decode", "turn_likely", "ask_q", "duck", "unduck", "timing"}   # off unless asked for


def page(sid, rows):
    t0 = rows[0]["t"] if rows else time.time()
    order, turns = [], {}
    for r in rows:
        key = "call" if r.get("turn") is None else str(r["turn"])
        if key not in turns:
            turns[key] = []
            order.append(key)
        turns[key].append(r)
    cards = []
    for key in order:
        evs = turns[key]
        b = breakdown(evs) if key != "call" else None
        cards.append({"turn": key, "at": round(evs[0]["t"] - t0, 1), "b": b,
                      "rows": [{"off": round(e["t"] - t0, 1), "src": e.get("src"), "ev": e["ev"], "quiet": e["ev"] in QUIET,
                                "text": text_of(e), "fold": folds(e)} for e in evs]})
    firsts = sorted(c["b"]["first_word"] for c in cards if c["b"] and c["b"]["first_word"] is not None)
    summary = {"sid": sid, "start": time.strftime("%b %d, %H:%M", time.localtime(t0)),
               "minutes": round((rows[-1]["t"] - t0) / 60.0, 1) if rows else 0,
               "turns": sum(1 for c in cards if c["b"]), "median": firsts[len(firsts) // 2] if firsts else None,
               "slow": sum(1 for x in firsts if x > 3000), "parts": PARTS}
    return PAGE.replace("__S__", json.dumps(summary)).replace("__C__", json.dumps(cards, ensure_ascii=False))


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Call trace</title>
<style>
:root{--bg:#f7f7f9;--card:#fff;--ink:#16161d;--dim:#6b6b78;--line:#e4e4ea}
@media (prefers-color-scheme:dark){:root{--bg:#121218;--card:#1b1b23;--ink:#ececf2;--dim:#9a9aa8;--line:#2b2b36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 20px 10px}
h1{margin:0 0 2px;font-size:18px}.sub{color:var(--dim);font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 4px}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:6px 10px}
.stat b{font-size:16px;display:block}.stat span{color:var(--dim);font-size:12px}
.key{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--dim);margin-top:6px}.key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
main{max-width:980px;margin:0 auto;padding:12px 20px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:12px 0}
.head{display:flex;justify-content:space-between;gap:10px;align-items:baseline}.head b{font-size:15px}
.total{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}.slow{color:#d9443b}
.bar{display:flex;height:18px;border-radius:5px;overflow:hidden;margin:8px 0 4px;background:var(--line)}
.bar i{display:block;height:100%}.parts{font-size:12.5px;color:var(--dim);font-variant-numeric:tabular-nums}
.parts b{color:var(--ink)}.on{font-size:12.5px;color:#d9443b;margin-top:2px}
.ev{display:grid;grid-template-columns:64px 54px 1fr;gap:8px;padding:3px 0;border-top:1px solid var(--line);font-size:13px}
.ev .o{color:var(--dim);font-variant-numeric:tabular-nums;text-align:right}.ev .s{color:var(--dim);font-size:11px;text-transform:uppercase;padding-top:2px}
.ev .x{white-space:pre-wrap;word-break:break-word}
details{margin:4px 0 2px}summary{cursor:pointer;color:var(--dim);font-size:12.5px}
details pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:12px/1.45 ui-monospace,Menlo,monospace;max-height:480px;overflow:auto}.q{display:none}body.all .q{display:grid}
label{font-size:12px;color:var(--dim)}
</style></head><body>
<header><h1>Call trace</h1><div class="sub" id="sub"></div><div class="stats" id="stats"></div>
<div class="key" id="key"></div><label><input type="checkbox" id="all"> every event (the floor, decodes, timing)</label></header>
<main id="list"></main>
<script>
const S=__S__,C=__C__,esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const sec=ms=>ms==null?'–':(ms/1000).toFixed(2)+' s';
document.getElementById('sub').textContent=S.sid+' · '+S.start+' · '+S.minutes+' min';
document.getElementById('stats').innerHTML=[[S.turns,'turns'],[S.median!=null?sec(S.median):'–','median to first word'],[S.slow,'turns slower than 3 s']]
 .map(s=>`<div class=stat><b>${s[0]}</b><span>${s[1]}</span></div>`).join('');
document.getElementById('key').innerHTML=S.parts.map(p=>`<span><i style="background:${p[2]}"></i>${p[1]}</span>`).join('');
document.getElementById('all').onchange=e=>document.body.classList.toggle('all',e.target.checked);
function bar(b){const P=b.parts,sum=S.parts.reduce((a,p)=>a+(P[p[0]]||0),0)||1;
 return `<div class=bar>${S.parts.map(p=>P[p[0]]?`<i title="${p[1]} ${sec(P[p[0]])}" style="width:${P[p[0]]/sum*100}%;background:${p[2]}"></i>`:'').join('')}</div>
 <div class=parts>${S.parts.map(p=>`${p[1]} <b>${sec(P[p[0]])}</b>`).join(' · ')}${b.early_ms!=null?' · asked '+sec(b.early_ms)+' early':''}</div>
 ${b.on?`<div class=on>Waited on: ${esc(b.on)}</div>`:''}`}
document.getElementById('list').innerHTML=C.map(c=>{const b=c.b,t=b&&b.total;
 return `<div class=card><div class=head><b>${c.turn==='call'?'The call':'Turn '+esc(c.turn)} <span class=sub>+${c.at}s</span></b>
 ${b?`<span class="total ${t>3000?'slow':''}">${sec(t)}</span>`:''}</div>${b?bar(b):''}
 ${c.rows.map(r=>`<div class="ev ${r.quiet?'q':''}"><span class=o>+${r.off.toFixed(1)}s</span><span class=s>${esc(r.src)}</span><span class=x>${esc(r.text)}${(r.fold||[]).map(f=>`<details><summary>${esc(f[0])}</summary><pre>${esc(f[1])}</pre></details>`).join('')}</span></div>`).join('')}</div>`}).join('');
</script></body></html>"""


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    sid = argv[0]
    out = argv[1] if len(argv) > 1 else "%s.html" % sid
    try:
        rows = turns_of(load(sid))
    except IOError:
        sys.argv = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_trace_logs.py"), sid, out]
        runpy.run_path(sys.argv[0], run_name="__main__")      # a call from before the trace: its logs
        return 0
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # the user's words: theirs to read only
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(page(sid, rows))
    print(out, len(rows), "events")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
