"""Usage: a row per Claude Code turn, summed for Settings > Usage.

Claude Code's modelUsage is a running total for the conversation, across turns
and across processes (a resumed one carries on from what its transcript
saved). The stand-in does the same, so a row must keep only its turn's share:
after an agent restart too, and for a turn stopped for a question. Titles are
counted too, on the model really running. Then the summary: buckets, models,
kinds, since when, Standby's own models only and never a cost; 90 days kept; and
the app's /api/usage, read here, in the Mac's local days and hours."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import io, json, os, sys, tempfile, time
from importlib.machinery import SourceFileLoader

root = tempfile.mkdtemp(prefix="sbusage-")
stub = os.path.join(root, "fake-claude")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys
ROOT = %r
RUNS = {"haiku": "claude-opus-5-5[1m]", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5[1m]"}
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")'); sys.exit(0)
model = RUNS.get(sys.argv[sys.argv.index("--model") + 1])
def turn(totals, n):                       # one turn's tokens, added to the running totals
    u = totals.setdefault(model, {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
                                  "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": 0.0,
                                  "contextWindow": 200000, "maxOutputTokens": 32000})
    u["inputTokens"] += 100 * n; u["outputTokens"] += 10 * n; u["cacheReadInputTokens"] += 1000 * n
    u["cacheCreationInputTokens"] += 50 * n; u["costUSD"] += 0.01 * n
    return totals
if "--no-session-persistence" in sys.argv:  # the one-shot title call, as JSON
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 800,
                      "result": "Caching The Map Page\\n", "modelUsage": turn({}, 1)}))
    sys.exit(0)
conv = sys.argv[sys.argv.index("--session-id") + 1] if "--session-id" in sys.argv else \\
    sys.argv[sys.argv.index("--resume") + 1]
saved = os.path.join(ROOT, "conv-%%s.json" %% conv)   # what the transcript keeps: a resume carries on
totals = json.load(open(saved)) if "--resume" in sys.argv and os.path.exists(saved) else {}
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init", "model": model})
for line in sys.stdin:
    if not line.strip(): continue
    msg = json.loads(line)
    if msg.get("type") != "user": continue
    text = msg["message"]["content"][0]["text"]
    n = 3 if "big" in text else 1
    turn(totals, n)
    json.dump(totals, open(saved, "w"))
    if "stop me" in text:                    # stopped for a question: it still used tokens
        sys.stdin.readline()                 # the interrupt
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "duration_ms": 300,
              "modelUsage": totals, "total_cost_usd": 1})
        continue
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "duration_ms": 1200,
          "modelUsage": totals, "total_cost_usd": sum(u["costUSD"] for u in totals.values())})
''' % root)
os.chmod(stub, 0o755)
os.environ["STANDBY_ROOT"] = root
os.environ["STANDBY_CLAUDE"] = stub
agentpath.guard()
ag = SourceFileLoader("ag", agentpath.AGENT).load_module()
import sbusage  # noqa: E402                        (the agent put plugin/bin first on the path)
sys.stderr = io.StringIO()
fails = []


def check(label, ok, d=""):
    sys.__stdout__.write("  %-66s %s\n" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        sys.__stdout__.write("     %s\n" % str(d)[:700])


def rows():
    try:
        return [json.loads(l) for l in open(ag.USAGE)]
    except IOError:
        return []


ONE = {"input": 100, "output": 10, "cache_read": 1000, "cache_write": 50, "cost_usd": 0.01}
M = "meeting-20261005-180000"
ag.engine_claude("[TRANSCRIPT] lines", M, "opus", 30, label="listen")
ag.engine_claude("[QUESTION] big one", M, "opus", 30, label="question")
r = rows()
check("a row per turn: what it was for, the engine, the model really running",
      [(x["kind"], x["engine"], list(x["models"])) for x in r]
      == [("listen", "meeting", ["claude-opus-5-5[1m]"]), ("question", "meeting", ["claude-opus-5-5[1m]"])], r)
check("  each its own turn's share of the running totals, not the totals",
      r[0]["models"]["claude-opus-5-5[1m]"] == ONE
      and r[1]["models"]["claude-opus-5-5[1m]"] == dict((k, round(v * 3, 6)) for k, v in ONE.items()), r)
check("  when, and how long it took", r[1]["session"] == M and abs(r[1]["t"] - time.time()) < 30
      and r[1]["seconds"] == 1.2, r[1])

# The agent restarts mid-meeting (new code): the conversation is resumed, and its
# first result already carries the earlier turns.
for e in list(ag._LIVE_SESSION.values()):
    e.stop(hard=True)
ag._LIVE_SESSION.clear()
ag.engine_claude("[QUESTION] after the restart", M, "opus", 30, label="question")
check("after an agent restart, a resumed conversation: only the new turn counted",
      rows()[-1]["models"]["claude-opus-5-5[1m]"] == ONE, rows()[-1])

ag.engine_claude("[QUESTION] stop me", M, "opus", 30, label="question", should_interrupt=lambda: True)
check("a turn stopped for a question is counted (it used tokens)",
      rows()[-1]["models"]["claude-opus-5-5[1m]"] == ONE and rows()[-1]["kind"] == "question", rows()[-1])

ag.forget_session(M)
for e in list(ag._LIVE_SESSION.values()):
    e.stop(hard=True)
ag._LIVE_SESSION.clear()
ag.engine_claude("[QUESTION] a new conversation", M, "opus", 30, label="question")
check("a new conversation starts its totals again: all of its first turn counted",
      rows()[-1]["models"]["claude-opus-5-5[1m]"] == ONE, rows()[-1])

ag.engine_claude("[BRIEFING] so far", M, "sonnet", 30, kind="voice", label="briefing")
ag.engine_claude("[VOICE] They said: hi", M, "sonnet", 30, kind="voice", label="voice")
check("the voice engine's turns: briefing and voice, on its own model",
      [(x["kind"], x["engine"], list(x["models"])) for x in rows()[-2:]]
      == [("briefing", "voice", ["claude-sonnet-5-5"]), ("voice", "voice", ["claude-sonnet-5-5"])], rows()[-2:])
check("  and its conversation's totals are its own (one turn each)",
      all(x["models"]["claude-sonnet-5-5"] == ONE for x in rows()[-2:]), rows()[-2:])


class Args(object):
    title_model, stdin, session = "haiku", False, M


os.makedirs(os.path.join(root, "live"), exist_ok=True)
open(os.path.join(root, "live", M + ".jsonl"), "w").write(
    "".join(json.dumps({"t": i, "text": "we talked about the routing cache %d" % i}) + "\n" for i in range(20)))
out = io.StringIO()
real_out, sys.stdout = sys.stdout, out
rc = ag.cmd_title(Args())
sys.stdout = real_out
check("a title, from Claude Code's JSON reply", rc == 0 and out.getvalue().strip() == "Caching The Map Page",
      (rc, out.getvalue()))
check("  counted as a title, on the model really running (haiku ran Opus here)",
      rows()[-1]["kind"] == "title" and list(rows()[-1]["models"]) == ["claude-opus-5-5[1m]"], rows()[-1])
check("  and haiku not asked for again for titles", ag.model_for("haiku") == "sonnet")
for e in list(ag._LIVE_SESSION.values()):
    e.stop(hard=True)

# ---------------------------------------------------------------- the summary
day = 1791000000
fake = os.path.join(root, "fake-usage.jsonl")
row = lambda t, kind, engine, models: json.dumps({"t": t, "session": M, "kind": kind, "engine": engine,
                                                  "seconds": 1.0, "models": models}) + "\n"
O, S = "claude-opus-5-5[1m]", "claude-sonnet-5-5"
u = lambda n, cost=0.01: {"input": 100 * n, "output": 10 * n, "cache_read": 1000 * n, "cache_write": 50 * n,
                          "cost_usd": cost * n}
with open(fake, "w") as fh:
    fh.write(row(day - 86400 * 3, "voice", "voice", {S: u(1)}))             # before the range
    for i, t in enumerate([day + 10, day + 30, day + 50, day + 65, day + 300]):   # the user's Claude Code work: not counted
        fh.write(row(t, "listen" if i % 2 else "question", "meeting", {O: u(1)}))
    for t in (day + 20, day + 25, day + 28, day + 29, day + 31):             # the call's voice
        fh.write(row(t, "voice", "voice", {S: u(1, 0.002)}))
    fh.write(row(day + 3700, "deep", "meeting", {O: u(2), S: u(1, 0.002)}))   # deep work, handed on: not counted
    fh.write(row(day + 3800, "words", "meeting", {O: u(2)}))                 # Standby's word-fixes pass
    fh.write(row(day + 3 * 3600 + 5, "title", "title", {S: u(1, 0.002)}))
    fh.write(row(day + 3 * 3600 + 9, "call-summary", "call-summary", {S: u(1, 0.002)}))
    fh.write("not json\n")
edges = [day + h * 3600 for h in range(25)]
sm = sbusage.summary(edges, fake)
check("summary: Standby's own calls in the range (one before it, and the user's Claude Code work, left out)",
      sm["from"] == day and sm["to"] == day + 86400 and sm["totals"]["calls"] == 8, sm["totals"])
check("  every bucket, zeros included", len(sm["series"]) == 24 and [b["calls"] for b in sm["series"][:4]] == [5, 1, 0, 2]
      and sm["series"][5] == {"t": day + 5 * 3600, "calls": 0}, sm["series"][:6])
check("  tokens summed, all four kinds, and no cost anywhere",
      sm["totals"]["input"] == 100 * 9 and sm["totals"]["cache_read"] == 1000 * 9 and "cost_usd" not in sm["totals"]
      and not any("cost_usd" in x for x in sm["models"] + sm["kinds"]), sm["totals"])
check("  models, most calls first: exact ids", [(m["model"], m["calls"]) for m in sm["models"]] == [(S, 7), (O, 1)],
      sm["models"])
ks = dict((k["kind"], k) for k in sm["kinds"])
check("  kinds: Standby's own only (no listen, question or deep), with the models each used",
      set(ks) == {"voice", "words", "title", "call-summary"} and ks["voice"]["calls"] == 5 and ks["voice"]["models"] == [S]
      and ks["words"]["models"] == [O] and ks["words"]["input"] == 200, sm["kinds"])
check("  no busiest minute: the meeting agent's budget is the user's Claude Code's", "peak" not in sm, sm.get("peak"))
check("  since: the first of Standby's calls ever counted", sm["since"] == day - 86400 * 3, sm["since"])
check("every kind standby-agent records is either Standby's own or the user's work",
      set(sbusage.STANDBY_KINDS) == {"voice", "briefing", "decisions", "call-summary", "cc-gist", "title", "words"})
empty = sbusage.summary(edges, os.path.join(root, "none.jsonl"))
check("nothing counted yet: zeros, every bucket, since null",
      empty["totals"]["calls"] == 0 and len(empty["series"]) == 24
      and empty["since"] is None and empty["models"] == [], empty)

# 90 days kept.
real = ag.USAGE
ag.USAGE = fake
before = [json.loads(l)["t"] for l in open(fake) if l.startswith("{")]
ag.prune_usage(now=day + 86400 * 89)
kept = [json.loads(l)["t"] for l in open(fake) if l.startswith("{")]
check("rows older than 90 days go; the rest stay, in order", kept == before[1:] and len(kept) == 14, kept[:3])
size = os.path.getsize(fake)
ag.prune_usage(now=day + 86400 * 89)
check("  and nothing is rewritten when nothing is that old", os.path.getsize(fake) == size)
ag.USAGE = real

# ---------------------------------------------------------------- the app: /api/usage
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import transport  # noqa: E402                      (STANDBY_ROOT is the temp root, above)
json.dump({"agent": {"mode": "local", "model": "opus", "voice_model": "sonnet"}}, open(transport.CONFIG, "w"))
ui = SourceFileLoader("ui", os.path.join(os.path.dirname(transport.__file__), "ui.py")).load_module()
# Every range ends at now (the user asked): nothing counted into the future, and the
# bucket in progress is partial. NOON is a fixed "now" so the shapes are exact.
NOON = int(time.mktime((2026, 10, 6, 12, 34, 56, 0, 0, -1)))
hm = lambda t: time.strftime("%d %H:%M", time.localtime(t))
b, e = ui.usage_edges("today", NOON)
check("today: this day's hours so far, the last ending now, not at midnight",
      b == "hour" and len(e) == 14 and hm(e[0]) == "06 00:00" and hm(e[-2]) == "06 12:00"
      and e[-1] == NOON + 1, [hm(x) for x in e])
b, e = ui.usage_edges("3h", NOON)
check("3h: ten-minute buckets, the last ending now", b == "10 minutes" and e[-1] == NOON + 1
      and hm(e[-2]) == "06 12:30" and all(y - x == 600 for x, y in zip(e[:-2], e[1:-1]))
      and 18 <= len(e) - 1 <= 19 and NOON - e[0] <= 3 * 3600 + 600, [hm(x) for x in e[:3]] + [hm(e[-2])])
b, e = ui.usage_edges("7d", NOON)
check("7 days: local midnights, today's bucket ending now", b == "day" and len(e) == 8
      and all(time.localtime(x)[3:5] == (0, 0) for x in e[:-1]) and e[-1] == NOON + 1
      and hm(e[0]) == "30 00:00", [hm(x) for x in e])
check("30 days too, across the month", len(ui.usage_edges("30d", NOON)[1]) == 31
      and ui.usage_edges("30d", NOON)[1][-1] == NOON + 1)
# Just after midnight: today has one partial bucket, and nothing from yesterday.
JUST = int(time.mktime((2026, 10, 6, 0, 3, 0, 0, 0, -1)))
b, e = ui.usage_edges("today", JUST)
check("  just after midnight: one partial hour, starting at midnight", len(e) == 2
      and hm(e[0]) == "06 00:00" and e[-1] == JUST + 1, [hm(x) for x in e])
own = lambda: len([x for x in rows() if x["kind"] in sbusage.STANDBY_KINDS])     # this session's: briefing, voice, title
got = ui.usage_info("today")
check("/api/usage: the agent's rows summed here, this session's own calls in it (not its listen or questions)",
      got.get("range") == "today" and got.get("bucket") == "hour" and got["totals"]["calls"] == own() < len(rows())
      and got["since"] is not None and got.get("partial") is True
      and got["series"] and abs(got["to"] - time.time()) < 3,
      {k: got.get(k) for k in ("range", "bucket", "partial", "totals", "error")})
three = ui.usage_info("3h")
check("  3h as well, with its own buckets, and this session's calls in the last one",
      three.get("bucket") == "10 minutes" and len(three["series"]) >= 18
      and three["series"][-1]["calls"] == own(), (three.get("bucket"), three["series"][-2:], own()))
check("  a range it doesn't know: an error", "error" in ui.usage_info("1y"))
real_cmd = transport.LocalTransport.agent_cmd
transport.LocalTransport.agent_cmd = lambda self, a: ["/usr/bin/false"]
check("  read without asking the agent: the same totals with it away",
      ui.usage_info("today")["totals"]["calls"] == own(), ui.usage_info("today").get("error"))
transport.LocalTransport.agent_cmd = real_cmd

sys.stderr = sys.__stderr__
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
