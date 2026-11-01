#!/usr/bin/env python3
"""The voice's replay: real turns from the user's call traces, asked again of the voice as it is now (its
prompt, model and effort), and what it does checked against what a turn of that kind should do. The gate for a
change to the voice's prompt, effort or model; never part of a normal test run (tests/test_voice_replay.py is
on demand: tests/suites.txt group "ondemand").

    python3 tools/voice_replay.py build              the cases, from ~/.standby/sessions/talk-*/trace.jsonl
    python3 tools/voice_replay.py login              is its own Claude Code signed in? (asks nothing)
    python3 tools/voice_replay.py run [--model M] [--effort E] [--jobs 4] [--only KIND]

The cases are the user's own words, so they stay on this Mac: ~/.standby/replay/cases.json, never the
repository. Claude Code runs with a config of its own (CLAUDE_CONFIG_DIR=~/.standby/replay/claude), never the
user's ~/.claude, with no tools and no MCP servers, and keeps no session. It signs in with the test tooling's
own token, which the user saves once (the AI caller in tests/caller uses the same one):
    claude setup-token        then save what it prints in ~/.standby/replay/oauth-token, readable by you alone
(or a /login in that config, or ANTHROPIC_API_KEY in the environment). Nothing is copied from the user's own
login, and the token is never printed or logged.

What each kind of turn should do (the voice's prompt's principles, checked by their shape, never by words):
    deep      it took the turn deeper: [DEEP], with its few words to them before the tag (the two calls whose
              hand-offs came out bare are in, as they should have gone)
    quiet     the voice chose silence: [QUIET], or a few words at most
    cancel    it stopped a task: the same [CANCEL #n]
    told      something came back (a result, the deeper side's message): no bare [DEEP], brief
    opening   the call just connected: a hello under ten words, nothing taken deeper
    answer    any other turn: no bare [DEEP], brief
Every reply is brief (BRIEF words said, at most). Timing: the first word's time, against the same turns on
the call and against the last run here (kept in ~/.standby/replay/last.json).
"""
import argparse
import concurrent.futures
import glob
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
AGENT = os.path.join(REPO, "plugin", "bin", "standby-agent")
ROOT = os.path.expanduser(os.environ.get("STANDBY_REPLAY_ROOT", "~/.standby/replay"))
CASES = os.path.join(ROOT, "cases.json")
LAST = os.path.join(ROOT, "last.json")
CONFIG = os.path.join(ROOT, "claude")
TOKEN = os.path.join(ROOT, "oauth-token")    # the one sign-in for all test tooling (claude setup-token), the user's to save
SESSIONS = os.path.expanduser(os.environ.get("STANDBY_REPLAY_SESSIONS", "~/.standby/sessions"))
# The calls whose hand-offs came out bare ("[DEEP] <task>", then their words, never said: 2026-10-31)
BARE_CALLS = ("talk-20261031-013229", "talk-20261031-173859")
QUOTA = {"deep": 8, "bare": 8, "quiet": 5, "cancel": 4, "told": 5, "answer": 6, "opening": 3}
PER_CALL = 3              # of one kind from one call, at most (the bare ones excepted): many calls, not one
HISTORY = 4               # earlier turns of the same call given with each case, for its context
CLIP = 1500               # characters of each earlier turn
BRIEF = 80                # words said in a reply, at most
QUIET_MAX = 8             # words a "quiet" turn may say instead of [QUIET]
DEEP = "[DEEP]"
CANCEL = re.compile(r"\[CANCEL\s*#?(\d+)\]")
TAG = re.compile(r"\[(?:QUIET|END|VISUAL[^\]]*|SHOW VISUAL|CANCEL[^\]]*)\]")


def spoken(reply):
    """What a reply says out loud: before [DEEP], its tags out."""
    return " ".join(TAG.sub("", (reply or "").split(DEEP, 1)[0]).split())


def kind_of(sent, reply):
    if CANCEL.search(reply or ""):
        return "cancel"
    if DEEP in (reply or ""):
        return "deep"
    if (reply or "").strip() == "[QUIET]":
        return "quiet"
    body = (sent or "").split("\n", 1)[-1]
    if body.startswith("[CALL START]") or body.startswith("They said: [CALL START]"):
        return "opening"
    if body.startswith("[RESULT") or "Deeper side:" in body:
        return "told"
    return "answer"


def check(case, reply):
    """[] if the reply does what a turn of its kind should, else what it got wrong."""
    wrong, said = [], spoken(reply)
    kind = case["kind"]
    if kind in ("deep", "bare") and DEEP not in reply:
        wrong.append("took nothing deeper")
    if DEEP in reply and len(said.split()) < 2:
        wrong.append("a bare [DEEP]: no words for them before it")
    if kind == "quiet" and reply.strip() != "[QUIET]" and len(said.split()) > QUIET_MAX:
        wrong.append("spoke (%d words) where it chose silence" % len(said.split()))
    if kind == "opening" and (DEEP in reply or len(said.split()) > 12):
        wrong.append("not a short hello: %d words%s" % (len(said.split()), ", and [DEEP]" if DEEP in reply else ""))
    if kind == "cancel" and case.get("cancel") not in CANCEL.findall(reply):
        wrong.append("stopped no task, or another (wanted #%s)" % case.get("cancel"))
    if len(said.split()) > BRIEF:
        wrong.append("long: %d words said" % len(said.split()))
    return wrong


def build(sessions=SESSIONS, out=CASES):
    """The cases: the voice's turns from every call's trace, by kind, up to QUOTA of each, newest first."""
    picked = {k: [] for k in QUOTA}
    for trace in sorted(glob.glob(os.path.join(sessions, "talk-*", "trace.jsonl")), reverse=True):
        call = os.path.basename(os.path.dirname(trace))
        turns = []
        try:
            for line in open(trace, encoding="utf-8", errors="replace"):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("ev") == "model" and str(r.get("sent", "")).startswith("[VOICE]") and r.get("reply") is not None:
                    turns.append(r)
        except OSError:
            continue
        for i, r in enumerate(turns):
            kind = kind_of(r["sent"], r["reply"])
            if kind == "deep" and call in BARE_CALLS and len(spoken(r["reply"]).split()) < 2:
                kind = "bare"
            if len(picked[kind]) >= QUOTA[kind] or (kind != "bare" and
                                                    sum(c["call"] == call for c in picked[kind]) >= PER_CALL):
                continue
            case = {"id": "%s#%d" % (call, i), "call": call, "kind": kind, "model": r.get("model") or "",
                    "sent": r["sent"], "was": r["reply"], "was_first_ms": r.get("first_token_ms"),
                    "history": [{"sent": h["sent"][-CLIP:], "reply": h["reply"][:CLIP]} for h in turns[max(0, i - HISTORY):i]]}
            if kind == "cancel":
                case["cancel"] = CANCEL.findall(r["reply"])[0]
            picked[kind].append(case)
    cases = [c for k in QUOTA for c in picked[k]]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)      # the user's words: theirs alone
    with os.fdopen(fd, "w") as fh:
        json.dump(cases, fh, indent=1)
    return cases


def prelude():
    """The voice's system prompt as a call gets it now, from the code in this checkout."""
    os.environ.setdefault("STANDBY_ROOT", tempfile.mkdtemp(prefix="sbreplay-"))
    loader = importlib.machinery.SourceFileLoader("standby_agent_replay", AGENT)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("standby_agent_replay", loader))
    loader.exec_module(mod)
    return mod.voice_prelude("talk-replay")


def message(case):
    """One case as the voice reads it: the call's earlier turns for context, then the turn itself."""
    earlier = "\n\n".join("%s\nYou replied: %s" % (h["sent"], h["reply"]) for h in case["history"])
    return ("Earlier in this call:\n%s\n\nNow:\n%s" % (earlier, case["sent"])) if earlier else case["sent"]


def claude():
    return shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


def token():
    """The test tooling's own sign-in (TOKEN), or None: only a file of the user's alone is used."""
    try:
        if os.stat(TOKEN).st_mode & 0o077:
            return None                   # readable by others: not used (chmod 600 it)
        with open(TOKEN) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def env():
    """Its own Claude Code config, never the user's ~/.claude, signed in with the tooling's token when saved."""
    os.makedirs(CONFIG, exist_ok=True)
    out = dict(os.environ, CLAUDE_CONFIG_DIR=CONFIG)
    out.pop("CLAUDE_CODE_CHILD_SESSION", None)
    t = token()
    if t:
        out["CLAUDE_CODE_OAUTH_TOKEN"] = t
    return out


def signed_in():
    """(True, how) when its own Claude Code can answer: a login in its config, or an API key."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True, "an API key in the environment"
    if token():
        return True, "the test tooling's token in %s" % TOKEN
    if os.path.exists(TOKEN):
        return False, "%s is readable by others, so it isn't used: chmod 600 it" % TOKEN
    try:
        r = subprocess.run([claude(), "auth", "status", "--json"], env=env(), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=20)
        if json.loads(r.stdout or "{}").get("loggedIn"):
            return True, "its own login in %s" % CONFIG
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return False, ("not signed in: run  claude setup-token  and save what it prints in %s (chmod 600), "
                   "or  CLAUDE_CONFIG_DIR=%s claude  then /login" % (TOKEN, CONFIG))


def ask(case, system, model=None, effort=None, timeout=120, run=subprocess.Popen):
    """The voice's reply to one case, and when its first word came (ms after asking), or an error."""
    argv = [claude(), "-p", "--model", model or case["model"] or "sonnet", "--tools", "", "--strict-mcp-config",
            "--no-session-persistence", "--append-system-prompt", system,
            "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if effort:
        argv += ["--effort", effort]
    t0 = time.monotonic()
    first, text = None, None
    try:
        p = run(argv, env=env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p.stdin.write(message(case))
        p.stdin.close()
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            delta = ((ev.get("event") or {}).get("delta") or {}) if ev.get("type") == "stream_event" else {}
            if first is None and delta.get("type") == "text_delta" and delta.get("text"):
                first = int((time.monotonic() - t0) * 1000)
            if ev.get("type") == "result":
                text = ev.get("result")
        p.wait(timeout)
        if text is None:
            return {"error": (p.stderr.read() or "no reply")[-300:]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": repr(exc)}
    return {"reply": text, "first_ms": first}


def run(model=None, effort=None, jobs=4, only=None, cases=None, asker=ask):
    cases = cases if cases is not None else json.load(open(CASES))
    if only:
        cases = [c for c in cases if c["kind"] == only]
    system = prelude()
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        got = list(pool.map(lambda c: asker(c, system, model, effort), cases))
    rows = []
    for c, g in zip(cases, got):
        wrong = [g["error"]] if "error" in g else check(c, g["reply"])
        rows.append({"id": c["id"], "kind": c["kind"], "wrong": wrong, "reply": g.get("reply"),
                     "first_ms": g.get("first_ms"), "was_first_ms": c.get("was_first_ms")})
    return rows


def report(rows, model=None, effort=None):
    """The run in a few lines, and whether it passes: every case right."""
    bad = [r for r in rows if r["wrong"]]
    for r in bad:
        print("  %-28s %-7s %s\n      %s" % (r["id"], r["kind"], "; ".join(r["wrong"]), " ".join(str(r["reply"] or "").split())[:200]))
    by = {}
    for r in rows:
        by.setdefault(r["kind"], [0, 0])[0 if r["wrong"] else 1] += 1
    print("  " + ", ".join("%s %d/%d" % (k, v[1], sum(v)) for k, v in sorted(by.items())))
    now = [r["first_ms"] for r in rows if r["first_ms"]]
    was = [r["was_first_ms"] for r in rows if r["was_first_ms"]]
    last = {}
    try:
        last = json.load(open(LAST))
    except (OSError, ValueError):
        pass
    if now:
        line = "  first word: median %d ms here" % statistics.median(now)
        if was:
            line += " (on the calls: %d ms, a warm process there)" % statistics.median(was)
        if last.get("median_first_ms"):
            line += "; last run here: %d ms" % last["median_first_ms"]
        print(line)
        with open(LAST, "w") as fh:
            json.dump({"at": time.time(), "median_first_ms": statistics.median(now), "model": model, "effort": effort,
                       "passed": not bad}, fh)
    print("%d of %d right" % (len(rows) - len(bad), len(rows)))
    return not bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("login")
    r = sub.add_parser("run")
    r.add_argument("--model")
    r.add_argument("--effort")
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--only", choices=sorted(QUOTA))
    a = ap.parse_args(argv)
    if a.cmd == "build":
        cases = build()
        counts = {}
        for c in cases:
            counts[c["kind"]] = counts.get(c["kind"], 0) + 1
        print("%d cases in %s: %s" % (len(cases), CASES, ", ".join("%s %d" % kv for kv in sorted(counts.items()))))
        return 0
    if a.cmd == "login":
        ok, how = signed_in()
        print(how)
        return 0 if ok else 1
    ok, how = signed_in()
    if not ok:
        print(how)
        return 2
    if not os.path.exists(CASES):
        build()
    return 0 if report(run(a.model, a.effort, a.jobs, a.only), a.model, a.effort) else 1


if __name__ == "__main__":
    sys.exit(main())
