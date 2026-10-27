"""App backend end to end in LOCAL mode: the page, one-click start with a
'starting' state, voice turns answered by the real local agent (standby-agent ->
stub claude), AI titles, speaker renames, export, settings, and the safety checks
(origin, path traversal).

Uses a fake recorder CLI; everything else is the real code."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, re, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbui-"))
bindir = Path(tempfile.mkdtemp(prefix="sbuibin-"))
(root / "config.json").write_text(json.dumps({"agent": {"mode": "local"}, "mode": "mic"}))

claude = bindir / "fake-claude"
claude.write_text('''#!/usr/bin/env python3
import json, sys, time
if "--no-session-persistence" in sys.argv:          # the one-shot title call
    print("Pricing Review Sync"); sys.exit(0)
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
for line in sys.stdin:
    if not line.strip(): continue
    q = json.loads(line)["message"]["content"][0]["text"]
    if q.startswith("[TRANSCRIPT]"):
        emit({"type": "result", "is_error": False, "result": "."}); continue
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/notes.md"}}]}})
    for piece in ("Orion is ", "the routing cache."):
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
              "delta": {"type": "text_delta", "text": piece}}})
        time.sleep(1.0)
    emit({"type": "result", "is_error": False, "result": "Orion is the routing cache. Café …"})
''')
claude.chmod(0o755)

# Fake recorder CLI: writes the same files the real one does, locally.
cli = bindir / "standby"
cli.write_text('''#!/bin/bash
R="%s"
case "$1" in
  start)
    sid="meeting-20260928-1000"; prev=""; kind=meeting
    for a in "$@"; do [ "$prev" = "--resume" ] && sid="$a"; [ "$prev" = "--kind" ] && kind="$a"; prev="$a"; done
    [ "$kind" = talk ] && [ "$sid" = "meeting-20260928-1000" ] && sid="talk-20261004-090000"
    echo "ARGS $*" > "$R/args"
    mkdir -p "$R/sessions/$sid" "$R/live"
    [ -f "$R/sessions/$sid/meta.json" ] || echo '{"started": '$(date +%%s)', "workspace": null, "kind": "'$kind'"}' > "$R/sessions/$sid/meta.json"
    cp "$R/sessions/$sid/meta.json" "$R/live/$sid.meta.json"
    if [ "$kind" = talk ]; then : ; else for i in $(seq 1 13); do
      echo '{"t":'$((i*3))',"end":'$((i*3+2))',"speaker":"Room","spk":'$(( (i%%2)+1 ))',"text":"we talked about the pricing review number '$i'"}'
    done; fi > "$R/sessions/$sid/transcript.jsonl"
    cp "$R/sessions/$sid/transcript.jsonl" "$R/live/$sid.jsonl"
    wc -c < "$R/sessions/$sid/transcript.jsonl" | tr -d ' ' > "$R/sessions/$sid/delivered"
    echo "$sid" > "$R/live/CURRENT"
    # A python running standby.py, like the real recorder: that is what the app counts.
    # It lives until stopped, or until this test is gone however it ended (even killed).
    mkdir -p "$R/fake"; echo 'import os, time
while True:
    time.sleep(1)
    try:
        os.kill(%d, 0)
    except OSError:
        break' > "$R/fake/standby"".py"
    nohup python3 "$R/fake/standby"".py" run fake >/dev/null 2>&1 & echo $! > "$R/daemon.pid"
    echo "$sid" > "$R/daemon.ready" ;;
  stop) kill $(cat "$R/daemon.pid") 2>/dev/null; rm -f "$R/daemon.ready" "$R/live/CURRENT" ;;
  selftest)
    echo "[standby] model loaded in 1.2s"; echo "mode: mic"; echo ""
    echo "--- Room (device None): recording 5s, speak or play audio now ---"
    sleep 1; echo "  peak level: 0.0712"; echo "  00:00  testing one two"
    echo ""; echo "If both channels transcribed, you are ready: standby start <label>" ;;
esac
''' % (root, os.getpid()))
cli.chmod(0o755)

# Voice mode speaks with this instead of `say`: it writes down what it was given.
said = root / "said.txt"
fake_say = bindir / "fake-say"
fake_say.write_text('#!/bin/bash\n{ cat; echo; } >> "%s"\n' % said)
fake_say.chmod(0o755)

# `open`, as the app runs it for System Settings panes: it writes down what it was asked.
fake_open = bindir / "fake-open"
fake_open.write_text('#!/bin/bash\necho "$@" >> "%s/opened"\n' % root)
fake_open.chmod(0o755)

# Standby Dictation's command line, as the app runs it: it writes down what it was asked.
dict_cli = bindir / "fake-dictation"
dict_cli.write_text('#!/bin/bash\necho "$@" >> "%s/dictation-cli"\n[ "$1" = on ] && [ -f "%s/dictation-fails" ] && '
                    '{ echo "Standby Dictation did not start. Open it from ~/Applications"; exit 1; }\nexit 0\n' % (root, root))
dict_cli.chmod(0o755)

# tccutil, as Settings' Reset runs it: it writes down what it was asked.
fake_tcc = bindir / "fake-tccutil"
fake_tcc.write_text('#!/bin/bash\necho "$@" >> "%s/tccutil"\nexit 0\n' % root)
fake_tcc.chmod(0o755)

# A free port, not a fixed one: a teammate's server (or a second run) on a fixed
# port answered this test's requests instead of its own.
import socket  # noqa: E402
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
# Only this test's fake recorder counts: the user may be recording for real meanwhile.
env = dict(os.environ, STANDBY_ROOT=str(root), STANDBY_CLI=str(cli), STANDBY_UI_PORT=str(PORT),
           STANDBY_CLAUDE=str(claude), STANDBY_SAY=str(fake_say), STANDBY_DICTATION_CLI=str(dict_cli),
           STANDBY_LAUNCH_AGENTS=str(root / "LaunchAgents"), STANDBY_OPEN=str(fake_open),
           STANDBY_RECORDER_MATCH=re.escape(str(root)) + "/fake/standby[.]py run",
           HF_HUB_CACHE=str(root / "hf"),          # a made-up Hugging Face cache (speech models)
           STANDBY_TCCUTIL=str(fake_tcc),          # never the real reset of the user's permission
           STANDBY_TEST_ROUTES="1")                # /api/start: this test's own way to start a meeting
srv = subprocess.Popen([sys.executable, "mac/ui.py"], env=env,
                       stderr=open(root / "ui.err", "w"))
import sbtoken  # noqa: E402  (the requests here carry the server's token, as Standby's clients do)
sbtoken.use(lambda: (root / "ui.token").read_text().strip() if (root / "ui.token").exists() else "")


def cleanup(*_):
    """The server, its agent and our fake recorder stop: on the way out, and when this
    test is stopped from outside (a timeout, Ctrl-C), where a finally never runs."""
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()
    subprocess.run([sys.executable, "plugin/bin/standby-agent", "stop"], env=env, capture_output=True)
    subprocess.run(["pkill", "-f", re.escape(str(root)) + "/fake/standby[.]py run fake"],   # ours only
                   capture_output=True)
    if _:
        sys.exit(1)


import signal  # noqa: E402
for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(_sig, cleanup)
B = "http://127.0.0.1:%d" % PORT


def ours():
    """Our server is up, not exited: with the port taken it would quit and this
    test would be talking to someone else's."""
    if srv.poll() is not None:
        print("FAIL: the test's own server exited (port %d taken?):\n%s" % (PORT, (root / "ui.err").read_text()))
        sys.exit(1)


def call(path, body=None, origin=None):
    req = urllib.request.Request(B + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json"
                              else raw.decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), {}


def wait(pred, secs=20):
    end = time.time() + secs
    while time.time() < end:
        try:
            v = pred()
        except Exception:
            v = None
        if v:
            return v
        time.sleep(0.25)
    return None


fails = []


def stop():
    """The live session ends: the fake recorder stopped as `standby stop` would."""
    subprocess.run([str(cli), "stop"], env=env, capture_output=True)
    wait(lambda: not call("/api/state")[1]["live"])


def check(label, ok, d=""):
    print("  %-56s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


try:
    # Cold PyObjC imports can take over 30 seconds on a busy hosted Mac. A
    # request constructed before ui.token exists carries an empty token even if
    # the server starts before it is accepted; never continue after that wait fails.
    ready = wait(lambda: (root / "ui.token").exists() and call("/api/state")[0] == 200, secs=90)
    ours()
    if not ready:
        raise RuntimeError("the test server was not ready after 90s:\n%s" % (root / "ui.err").read_text())
    code, page, hdr = call("/")
    check("page served", code == 200 and "Settings" in page and "agents.js" in page and "New meeting" not in page,
          {"status": code, "content_type": hdr.get("Content-Type"),
           "settings": "Settings" in page, "agents_script": "agents.js" in page, "old_new_meeting": "New meeting" in page})
    _, st, _ = call("/api/state")
    check("agent shown as this Mac", st["agent"]["mode"] == "local", st["agent"])
    t0 = time.time()
    _, r, _ = call("/api/start", {})
    check("New session returns at once (no dialog, no wait)", r.get("started") and time.time() - t0 < 2, r)
    sid = wait(lambda: call("/api/state")[1]["live"])
    check("session goes live", sid == "meeting-20260928-1000", sid)
    args = (root / "args").read_text()
    check("start uses the Settings defaults (Automatic)", "--mode auto" in args and "--push-lines 10" in args, args)
    _, s, _ = call("/api/session/" + sid)
    check("default title is dated", s["info"]["title"].startswith("Meeting · "), s["info"]["title"])
    title = wait(lambda: (lambda x: x["title"] if x["title_source"] == "ai" else None)(
        call("/api/session/" + sid)[1]["info"]), 40)
    check("AI suggests a title after enough transcript", title == "Pricing Review Sync", title)
    call("/api/rename", {"id": sid, "title": "Q3 pricing review"})
    _, s, _ = call("/api/session/" + sid)
    check("user rename wins over the AI title", s["info"]["title"] == "Q3 pricing review"
          and s["info"]["title_source"] == "user", s["info"])
    call("/api/speaker", {"id": sid, "spk": "1", "name": "Alex"})
    _, s, _ = call("/api/session/" + sid)
    check("speaker rename saved", s["speakers"]["names"].get("1") == "Alex", s["speakers"])
    delivered = json.loads((root / "live" / (sid + ".speakers.names.json")).read_text())
    check("speaker names delivered to the agent", delivered["names"].get("1") == "Alex", delivered)

    _, r, _ = call("/api/claude", {"id": sid, "on": False})
    _, s, _ = call("/api/session/" + sid)
    check("Claude off: switch saved for the recorder", r.get("ok") and not s["info"]["claude"]
          and (root / "sessions" / sid / "claude.off").exists(), (r, s["info"]))
    call("/api/speaker", {"id": sid, "spk": "2", "name": "Sam"})
    delivered = json.loads((root / "live" / (sid + ".speakers.names.json")).read_text())
    check("Claude off: a rename stays on this Mac", "2" not in delivered["names"], delivered)
    call("/api/claude", {"id": sid, "on": True})

    _, r, _ = call("/api/voice", {"id": sid, "action": "start"})
    check("voice mode on a meeting: refused, it is only for calls", "only for calls" in r.get("error", "")
          and not (root / "sessions" / sid / "dictate").exists(), r)
    code, md, hdr = call("/api/export/" + sid)
    check("export is markdown with names", code == 200 and "# Q3 pricing review" in md
          and "Alex:" in md and "Sam:" in md, md[:300])
    check("export downloads with a sensible filename",
          'filename="q3-pricing-review.md"' in hdr.get("Content-Disposition", ""), hdr)
    _, r, _ = call("/api/copy", {"id": sid})
    check("copy answers cleanly (pbcopy exists only on macOS)",
          r.get("ok") or "clipboard" in r.get("error", ""), r)
    _, r, _ = call("/api/settings")
    check("settings readable, with no SSH host or default folder in them", r["settings"]["push_lines"] == 10
          and not {"agent_mode", "host", "workspace", "claude_on_start"} & set(r["settings"]), r)
    _, r, _ = call("/api/settings", {"agent_mode": "remote", "host": "devbox", "push_lines": 25, "mic_device": ""})
    saved = json.loads((root / "config.json").read_text())
    check("settings saved; an SSH host asked for is not kept", r.get("ok") and r["settings"]["push_lines"] == 25
          and (saved.get("agent") or {}).get("mode") == "local" and "host" not in (saved.get("agent") or {}), (r, saved))
    _, r, _ = call("/api/settings", {"voice_model": "opus"})
    check("voice mode's model is a setting of its own, Sonnet unless changed",
          r.get("ok") and r["settings"]["voice_model"] == "opus" and r["settings"]["model"] != "", r)
    call("/api/settings", {"voice_model": "sonnet"})
    _, pr, _ = call("/api/prompt")
    check("instructions: the full default prompt, with placeholders", not pr.get("custom")
          and "silent listener" in pr["prompt"] and "{session}" in pr["prompt"] and "QUESTION messages" in pr["prompt"]
          and "SKILL.md" not in pr["prompt"], pr.get("prompt", pr)[:200])
    _, r, _ = call("/api/prompt", {"text": "Be brief. Session {session}."})
    _, pr2, _ = call("/api/prompt")
    check("instructions: an edit is saved where the agent runs", r.get("ok") and pr2["custom"]
          and pr2["prompt"] == "Be brief. Session {session}." and (root / "prompt.md").exists(), (r, pr2.get("prompt")))
    call("/api/prompt", {"reset": True})
    _, pr3, _ = call("/api/prompt")
    check("instructions: reset goes back to the default", not pr3["custom"] and pr3["prompt"] == pr["prompt"])
    _, r, _ = call("/api/mic-test", {})
    check("mic test refused while recording", "Stop the recording" in r.get("error", ""), r)
    code, _, _ = call("/api/start", {}, origin="https://evil.example")
    check("cross-site POST refused", code == 403, code)
    stop()
    _, r, _ = call("/api/voice", {"id": sid, "action": "start"})
    check("  voice mode is still the live session's only", "live session" in r.get("error", ""), r)
    _, r, _ = call("/api/mic-test", {})
    mt = wait(lambda: (lambda x: x if x["state"] == "done" else None)(call("/api/mic-test")[1]), 20)
    check("mic test: level and what was heard", r.get("started") and mt and mt["channels"] == [
        {"channel": "Room", "device": "None", "peak": 0.0712, "heard": ["testing one two"], "problem": []}], mt)
    _, r, _ = call("/api/settings", {"push_lines": 10})
    check("talk over Claude: off until the user turns it on", r["settings"]["talk_over"] is False
          and r["settings"]["talk_over_note"] == "", r["settings"])
    _, r, _ = call("/api/settings", {"talk_over": True})
    duplex = json.loads((root / "config.json").read_text()).get("voice_mode", {}).get("duplex")
    check("  on: the one switch the recorder reads (voice_mode.duplex)",
          r.get("ok") and r["settings"]["talk_over"] is True and duplex is True, (r, duplex))
    _, r, _ = call("/api/settings", {"mic_device": "USB Headset Mic"})
    check("  with a mic of its own chosen: a plain note that it needs the Mac's default one",
          "default microphone" in r["settings"]["talk_over_note"], r["settings"])
    _, r, _ = call("/api/settings", {"mic_device": "", "talk_over": False})
    check("  off again, and the default mic: no note", r["settings"]["talk_over"] is False
          and r["settings"]["talk_over_note"] == ""
          and json.loads((root / "config.json").read_text())["voice_mode"]["duplex"] is False, r["settings"])
    _, r, _ = call("/api/screen/reset", {})
    check("Reset (Screen Recording on for an older copy): Standby's own entry, then macOS asks again",
          r.get("state") == "asked" and (root / "tccutil").read_text().split() == ["reset", "ScreenCapture", "dev.standby.app"],
          (r, (root / "tccutil").read_text() if (root / "tccutil").exists() else None))
    # Settings > Recording > Speech model: Small or Standard, and English only.
    sp = call("/api/settings")[1]["settings"]["speech"]
    check("speech model: this Mac's default, not downloaded yet, no download in a test",
          sp["size"] in ("small", "standard") and sp["size"] == sp["default"] and sp["ready"] is False
          and sp["download"] is None and sp["bytes"] > 1e8, sp)
    check("  the sizes offered punctuate, and the one in use says it does (2026-10-11)",
          sp["sizes"] == ["small", "standard"] and sp["punctuates"] is True and sp["moved"] is None, sp)
    _, r, _ = call("/api/settings", {"speech_model": "large"})
    check("  Large is refused: it writes no punctuation, so Settings does not offer it",
          r["settings"]["speech"]["size"] == sp["default"]
          and "speech_model" not in json.loads((root / "config.json").read_text()), r["settings"]["speech"])
    _, r, _ = call("/api/settings", {"speech_model": "small", "english_only": True})
    sp, saved = r["settings"]["speech"], json.loads((root / "config.json").read_text())
    check("  Small, English only: saved, and what it is", r.get("ok") and sp["size"] == "small" and sp["english_only"] is True
          and sp["model"] == "Small, English" and sp["bytes"] == 458690629
          and saved["speech_model"] == "small" and saved["english_only"] is True, (sp, saved))
    _, r, _ = call("/api/settings", {"english_only": False})
    sp = r["settings"]["speech"]
    check("  other languages too: Standard's multilingual model, whatever the size",
          sp["size"] == "small" and sp["english_only"] is False and sp["model"] == "Standard, 25 languages", sp)
    _, r, _ = call("/api/settings", {"speech_model": "huge", "english_only": True})
    check("  a size it doesn't know: unchanged", r["settings"]["speech"]["size"] == "small", r["settings"]["speech"])
    snap = root / "hf" / "models--mlx-community--parakeet-tdt_ctc-110m" / "snapshots" / "d62547387c356a1ab6bb3d85d98b2103f655282e"
    snap.mkdir(parents=True)
    for f in ("config.json", "model.safetensors"):
        (snap / f).write_text("x")
    _, sp, _ = call("/api/speech")
    check("  once its files are here: ready (and /api/speech, as Settings polls it)",
          sp["ready"] is True and sp["model"] == "Small, English", sp)
    check("  the change in the app's log", wait(lambda: "settings: speech model Small, English" in
                                                 (root / "ui.err").read_text()), (root / "ui.err").read_text()[-400:])
    call("/api/start", {})
    wait(lambda: call("/api/state")[1]["live"])
    args = (root / "args").read_text()
    check("a new session (tests only) starts with Claude on", "--no-claude" not in args, args)
    stop()
    code, _, _ = call("/api/session/..%2F..%2Fetc")
    check("path traversal rejected", code == 404, code)

    # Settings > Dictation: Standby Dictation's state, from its lock and the status it writes.
    _, dq, _ = call("/api/dictation-app")
    check("dictation: off, not running, nothing to fix", dq.get("on") is False and dq.get("running") is False
          and dq.get("missing") == [] and not dq.get("old"), dq)
    (root / "dictation.starting").write_text(json.dumps({"at": time.time(), "n": 4, "error": "RuntimeError: boom"}))
    _, dq, _ = call("/api/dictation-app")
    check("dictation: stopped after failed launches, and why", dq.get("problem") == "RuntimeError: boom", dq)
    (root / "dictation.starting").unlink()
    holder = subprocess.Popen([sys.executable, "-c", """
import fcntl, os, sys, time
fh = open(sys.argv[1], "a+"); fcntl.flock(fh, fcntl.LOCK_EX); fh.seek(0); fh.truncate()
fh.write("%d\\n" % os.getpid()); fh.flush(); print("held", flush=True); time.sleep(60)
""", str(root / "dictation.lock")], stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()
    try:
        status = {"pid": holder.pid, "started": time.time() - 60, "remapped": False, "shortcut": "mickey",
                  "permissions": {"listen": True, "post": False, "mic": "ask"},
                  "code": {"changed": "", "pending": False, "problem": None}}
        (root / "dictation.status.json").write_text(json.dumps(status))
        _, dq, _ = call("/api/dictation-app")
        check("dictation: running, and what is missing, in the setup window's order",
              dq.get("on") and dq.get("running") and not dq.get("old")
              and [m["key"] for m in dq.get("missing", [])] == ["mic", "post", "remap"], dq)
        check("  each with its fix, the F5 remap too, the key named in words",
              [m["action"] for m in dq["missing"]] == ["ask", "open", "setup"] and dq["missing"][2]["name"] == "F5 key"
              and dq["shortcut"] == {"id": "mickey", "label": "Press F5", "kind": "key"}, dq["missing"])
        status["code"] = {"changed": "dictation.py", "pending": True, "problem": None}
        (root / "dictation.status.json").write_text(json.dumps(status))
        check("dictation: moving to new code", call("/api/dictation-app")[1].get("updating") is True)
        status["code"] = {"changed": "dictation.py", "pending": False, "problem": "SyntaxError: invalid syntax"}
        (root / "dictation.status.json").write_text(json.dumps(status))
        check("  or why new code is not used", call("/api/dictation-app")[1].get("problem") == "SyntaxError: invalid syntax")
        del status["started"], status["code"]
        (root / "dictation.status.json").write_text(json.dumps(status))
        check("dictation: a copy from before self-updating is called old", call("/api/dictation-app")[1].get("old") is True)
        status["pid"] = 1
        (root / "dictation.status.json").write_text(json.dumps(status))
        dq = call("/api/dictation-app")[1]
        check("  so is one whose status is someone else's, and nothing is claimed for it",
              dq.get("old") is True and dq.get("missing") == [], dq)
        (root / "dictation-words.txt").write_text("cloud code => Claude Code\n\n# learned: from Fix Last Dictation\n"
                                                  "or ion => Orion\n")
        dq = call("/api/dictation-app")[1]
        check("dictation: its word fixes, as numbers only",
              dq.get("words") == {"count": 2, "learned": 1, "suggested": 0}, dq.get("words"))
        _, r, _ = call("/api/dictation-app", {"action": "edit_words"})
        check("  Edit asks the running app to open them", r == {"ok": True}
              and (root / "dictation.cmd").read_text().split()[-1] == "edit_words", r)
        _, r, _ = call("/api/dictation-app", {"action": "setup"})
        check("dictation: Set up asks the running app for its setup window",
              r.get("ok") and (root / "dictation.cmd").read_text().split()[-1] == "setup", r)

        # Settings > Dictation > Shortcut: presets, a recorded one, hold to talk.
        cfg_dict = lambda: json.loads((root / "config.json").read_text()).get("dictation", {})
        status["pid"], status["started"] = holder.pid, time.time()
        status["code"] = {"changed": "", "pending": False, "problem": None}
        (root / "dictation.status.json").write_text(json.dumps(status))
        dq = call("/api/dictation-app")[1]
        check("shortcut: the one in use, every preset with its kind, and hold to talk",
              dq["shortcut"]["id"] == "mickey" and dq["hold"] is False
              and [x["id"] for x in dq["shortcuts"]][:2] == ["mickey", "fn2"]
              and {x["kind"] for x in dq["shortcuts"]} == {"key", "double"}, dq.get("shortcuts"))
        _, r, _ = call("/api/dictation-app", {"shortcut": "fn2"})
        check("  a preset: saved, and the running app told to reload", r.get("ok")
              and r["shortcut"] == {"id": "fn2", "label": "Press Fn (Globe) twice", "kind": "double"}
              and cfg_dict().get("shortcut") == "fn2"
              and (root / "dictation.cmd").read_text().split()[-1] == "reload", r)
        _, r, _ = call("/api/dictation-app", {"shortcut": {"code": "KeyQ", "key": "q", "mods": ["meta"]}})
        check("  a recorded one that would clash: refused in one line, nothing saved",
              r == {"error": "⌘ Q is an app shortcut everywhere"} and cfg_dict().get("shortcut") == "fn2", r)

        def answer(ok, why=""):
            """The app, registering the hotkey a moment after it is told to reload."""
            def go():
                for _ in range(40):
                    if (root / "dictation.cmd").exists():
                        break
                    time.sleep(0.05)
                time.sleep(0.2)
                st2 = dict(status, hotkey_state={"label": "Press ⌥ Space", "ok": ok, "why": why, "at": time.time()})
                (root / "dictation.status.json").write_text(json.dumps(st2))
            (root / "dictation.cmd").unlink() if (root / "dictation.cmd").exists() else None
            t = threading.Thread(target=go)
            t.start()
            return t
        t = answer(True)
        _, r, _ = call("/api/dictation-app", {"shortcut": {"code": "Space", "key": " ", "mods": ["alt"]}})
        t.join()
        check("  a recorded combination, registered by the app: in use", r.get("ok")
              and r["shortcut"] == {"id": "custom", "label": "Press ⌥ Space", "kind": "combo"}
              and cfg_dict()["custom"]["keycode"] == 49 and cfg_dict()["custom"]["carbon"] == 0x0800, r)
        dq = call("/api/dictation-app")[1]
        check("  and offered among the presets from then on", dq["shortcuts"][-1] == {
            "id": "custom", "label": "Press ⌥ Space", "kind": "combo"} and "problem" not in dq["shortcut"], dq["shortcuts"][-1:])
        call("/api/dictation-app", {"shortcut": "rcommand2"})
        t = answer(False, "another app already uses it")
        _, r, _ = call("/api/dictation-app", {"shortcut": {"code": "Space", "key": " ", "mods": ["alt"]}})
        t.join()
        check("  keys another app holds (Alfred, Raycast): refused, and the previous shortcut is back",
              r == {"error": "⌥ Space: another app already uses it"} and cfg_dict().get("shortcut") == "rcommand2", r)
        _, r, _ = call("/api/dictation-app", {"shortcut": {"double": "AltRight"}})
        check("  a modifier pressed twice: no waiting, nothing to register", r.get("ok")
              and r["shortcut"] == {"id": "custom", "label": "Press Right Option twice", "kind": "double"}, r)
        _, r, _ = call("/api/dictation-app", {"hold": True})
        check("  hold to talk: saved, and the app told", r.get("ok") and cfg_dict().get("hold") is True
              and (root / "dictation.cmd").read_text().split()[-1] == "reload", r)
        _, r, _ = call("/api/dictation-app", {"shortcut": "nonsense"})
        check("  a preset that does not exist: refused", r == {"error": "No such shortcut"}, r)
    finally:
        holder.kill(); holder.wait()
    _, r, _ = call("/api/dictation-app", {"action": "on"})
    _, r2, _ = call("/api/dictation-app", {"action": "off"})
    cli_calls = (root / "dictation-cli").read_text().split()
    check("dictation: the switch turns it on and off at once", r.get("ok") and r2.get("ok")
          and cli_calls == ["on", "off"] and "status" in r, (r, r2, cli_calls))
    (root / "opened").write_text("")
    _, r, _ = call("/api/dictation-app", {"action": "edit_words"})
    check("  Edit with dictation not running: opened from here, the same file", r == {"ok": True}
          and wait(lambda: "dictation-words.txt" in (root / "opened").read_text())
          and (root / "opened").read_text().split()[0] == "-t", ((root / "opened").read_text(), r))
    _, r, _ = call("/api/dictation-app", {"action": "setup"})
    check("  Set up with nothing running starts it", r.get("ok") and (root / "dictation-cli").read_text().split()[-1] == "on")
    (root / "dictation-fails").write_text("")
    _, r, _ = call("/api/dictation-app", {"action": "on"})
    check("  and says why when it cannot", "did not start" in r.get("error", ""), r)
    # A call: a talk, started from Claude Code.
    beat = {"on": True}

    def heartbeat():                      # the recorder's live status, for voice mode
        while beat["on"]:
            try:
                live_sid = (root / "daemon.ready").read_text().strip()
                (root / "sessions" / live_sid / "live.json").write_text(
                    json.dumps({"at": time.time(), "level": 0.0, "partial": ""}))
            except OSError:
                pass
            time.sleep(0.1)
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        _, r, _ = call("/api/lite/call", {"cc": "0f3c2a77-1111-4222-8333-944455556666"})
        tid = r.get("session") or ""
        check("a call: a talk, its voice mode on", tid.startswith("talk-")
              and wait(lambda: (call("/api/voice/" + tid)[1] or {}).get("state") in ("listening", "speaking", "thinking"), 10),
              (r, call("/api/voice/" + tid)[1] if tid else None))
        args = (root / "args").read_text()
        check("  recorded as a talk, Claude on whatever the settings say", "--kind talk" in args
              and "--no-claude" not in args, args)
        ctl = (root / "sessions" / tid / "dictate").read_text().split() if tid else []
        check("  the recorder is listening for turns", ctl[:1] == ["voice"], ctl)
        _, s, _ = call("/api/session/" + tid)
        check("  its info says talk, titled Talk · …", s["info"]["kind"] == "talk"
              and s["info"]["title"].startswith("Talk · "), s["info"])
        check("  and so does the session list", any(x["id"] == tid and x["kind"] == "talk"
                                                     for x in call("/api/state")[1]["sessions"]))
        check("  asked again while it is live: the same call",
              call("/api/lite/call", {"cc": "0f3c2a77-1111-4222-8333-944455556666"})[1].get("session") == tid)
        _, r, _ = call("/api/voice", {"id": tid, "action": "stop"})
        check("ending voice mode ends the talk", wait(lambda: not call("/api/state")[1]["live"], 20),
              call("/api/state")[1]["live"])
        _, md, _ = call("/api/export/" + tid)
        check("  its export has no empty transcript", "## Transcript" not in md, md[:300])
        _, r, _ = call("/api/start", {})
        now_live = wait(lambda: (lambda l: l if l and l != tid else None)(call("/api/state")[1]["live"]), 30)
        args = (root / "args").read_text()
        check("starting a meeting ends the live talk, and the meeting goes live",
              r.get("started") and now_live and not now_live.startswith("talk-") and "--kind" not in args, (r, now_live, args))
        _, r, _ = call("/api/talk", {})
        check("  and /api/talk is gone", "error" in r or r == {}, r)
        stop()
    finally:
        beat["on"] = False
        stop()
    # Settings > Voice: the voices installed here, chosen at once, tried, and where to get better ones.
    _, vi, _ = call("/api/voices")
    check("voices: the English ones installed, and the one speaking now",
          isinstance(vi.get("voices"), list) and vi.get("current", {}).get("name") and vi.get("chosen") is None
          and isinstance(vi.get("better"), bool) and all(v.get("region") for v in vi["voices"]), vi)
    if vi["voices"]:
        pick = vi["voices"][-1]
        _, r, _ = call("/api/voices", {"id": pick["id"]})
        cfg = json.loads((root / "config.json").read_text())
        check("  choosing one saves it for voice mode", r.get("ok") and cfg["voice_mode"]["voice"] == pick["id"]
              and r["voices"]["chosen"] == pick["id"] and r["voices"]["current"]["id"] == pick["id"], (r, cfg))
        said.write_text("")
        _, r, _ = call("/api/voices", {"action": "try", "id": pick["id"]})
        check("  Try says a line in it (a stand-in for say: no sound in a test)",
              r.get("tried") == pick["name"] and ("I'm %s" % pick["name"]) in said.read_text(), (r, said.read_text()))
        _, r, _ = call("/api/voices", {"id": None})
        cfg = json.loads((root / "config.json").read_text())
        check("  automatic again", r.get("ok") and "voice" not in cfg.get("voice_mode", {}), cfg)
    _, r, _ = call("/api/voices", {"id": "com.example.not.installed"})
    check("  a voice that is not installed is refused", "not installed" in r.get("error", ""), r)
    _, r, _ = call("/api/voices", {"action": "get"})
    opened = (root / "opened").read_text()
    check("  Get one opens Read & Speak in System Settings (Spoken Content before macOS 26)",
          r.get("ok") and ("universalaccess?AX_FEATURE_SPOKENCONTENT" in opened or "universalaccess?SpeakableItems" in opened),
          (r, opened))
    _, r, _ = call("/api/dictation-app", {"action": "open", "key": "../etc"})
    _, r2, _ = call("/api/dictation-app", {"action": "explode"})
    check("dictation: unknown permissions and actions are refused",
          "unknown" in r.get("error", "") and "unknown" in r2.get("error", ""), (r, r2))
finally:
    cleanup()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
