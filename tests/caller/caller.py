#!/usr/bin/env python3
"""The AI caller: Standby's calls tested end to end with no one on them. A scripted caller plays the user
(tests/caller/scenarios/*.json): its lines are spoken with macOS's `say` into a file, fed to Standby as its
mic, and Standby's voice is played to a file; a real Claude Code with the plugin is on the call, so the voice,
its hand-offs and its cards are the real ones. Each run is scored and kept with the call's trace.
On demand only, never in a normal test run (tests/suites.txt group "ondemand").

    python3 tests/caller/caller.py setup            its own Standby folder and Claude Code, the plugin
    python3 tests/caller/caller.py list             the scenarios
    python3 tests/caller/caller.py run [NAME ...] [--echo GAIN,MS] [--no-transcribe]

Never the user's mic or speakers (mac/testaudio.py, with STANDBY_NO_DEVICES), never their Standby: it all
runs under its own HOME, ~/.standby/caller/home, so every ~/.standby and ~/.claude path Standby has is the
test's, its app on its own port, and only its own recorder is ever stopped (STANDBY_RECORDER_MATCH). Claude
Code signs in there with a token of its own, made once by the user (`claude setup-token`) and kept in
~/.standby/replay/oauth-token, shared with tools/voice_replay.py; nothing of the user's own login is read.
Runs land in ~/.standby/caller/runs/<time>-<name>/: report.json, report.md, the audio, and the session.

The audio is behind one small interface (AudioIO: start, speak, heard, stop), so the same scenarios run through
virtual devices in a VM (level 2) with only the backend changed (--audio).
"""
import argparse
import glob
import json
import os
import pty
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
REAL_HOME = os.path.expanduser("~")
BASE = os.path.expanduser(os.environ.get("STANDBY_CALLER_ROOT", "~/.standby/caller"))
HOME = os.path.join(BASE, "home")
RUNS = os.path.join(BASE, "runs")
PROJECT = os.path.join(HOME, "project")              # the folder the test Claude Code works in
TOKEN_FILE = os.path.join(REAL_HOME, ".standby", "replay", "oauth-token")
REAL_ROOT = os.path.join(REAL_HOME, ".standby")
ROOT = os.path.join(HOME, ".standby")
PY = os.path.join(REAL_ROOT, "venv", "bin", "python")
PORT = int(os.environ.get("STANDBY_CALLER_PORT", "8799"))
SCENARIOS = os.path.join(HERE, "scenarios")
RATE = 16000
SPEAKER_RATE = 24000
SHARED = ("venv", "python", "models", "kit")         # the install's, used as they are: never written to


def claude_path():
    return shutil.which("claude") or os.path.join(REAL_HOME, ".local", "bin", "claude")


def token():
    try:
        with open(TOKEN_FILE) as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def env(audio_dir=None, echo=None):
    """The test's environment: its own HOME, the test audio, never a device, its own port and recorder."""
    out = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_CODE_", "CLAUDECODE", "STANDBY_"))}
    out.update(HOME=HOME, STANDBY_NO_DEVICES="1", STANDBY_UI_PORT=str(PORT), STANDBY_CLAUDE=claude_path(),
               HF_HOME=os.environ.get("HF_HOME") or os.path.join(REAL_HOME, ".cache", "huggingface"),
               DISABLE_AUTOUPDATER="1", STANDBY_CALL_TRACE="1", PYTHONUTF8="1",
               STANDBY_RECORDER_MATCH=re.escape(os.path.join(REPO, "mac")) + "/standby[.]py run",
               PATH=os.path.join(REAL_HOME, ".local", "bin") + os.pathsep + os.environ.get("PATH", ""))
    tok = token()
    if tok:
        out["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    if audio_dir:
        out["STANDBY_TEST_AUDIO"] = audio_dir
    if echo:
        out["STANDBY_TEST_ECHO"] = echo
    return out


# ---------------------------------------------------------------- setup

def setup(log=print):
    """Its own Standby folder (the install's Python and models linked in), a project, Claude Code's first-run
    answers, and the plugin from this checkout. Safe to run again."""
    os.makedirs(ROOT, exist_ok=True)
    os.makedirs(PROJECT, exist_ok=True)
    for name in SHARED:
        src, dst = os.path.join(REAL_ROOT, name), os.path.join(ROOT, name)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    cfg_path = os.path.join(ROOT, "config.json")
    cfg = {}
    try:
        with open(os.path.join(REAL_ROOT, "config.json")) as fh:
            real = json.load(fh)
        # the same speech model, and the same voice settings (its voice, talking over it)
        cfg.update({k: real[k] for k in ("speech_model", "speech_size", "model", "voice_mode") if k in real})
    except (OSError, ValueError):
        pass
    cfg.setdefault("debug", {})["call_trace"] = True
    cfg["call_visuals"] = True
    with open(cfg_path, "w") as fh:
        json.dump(cfg, fh, indent=2)
    readme = os.path.join(PROJECT, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w") as fh:
            fh.write("# Lighthouse\n\nA small test project: a lighthouse keeper's log. The keeper is Robin.\n")
    # Claude Code's first run, answered: no theme question, the project trusted
    cj = os.path.join(HOME, ".claude.json")
    try:
        with open(cj) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.update(hasCompletedOnboarding=True, theme=data.get("theme", "dark"), autoUpdates=False)
    data.setdefault("projects", {}).setdefault(PROJECT, {}).update(hasTrustDialogAccepted=True,
                                                                  hasCompletedProjectOnboarding=True)
    with open(cj, "w") as fh:
        json.dump(data, fh, indent=2)
    e = env()
    listed = subprocess.run([claude_path(), "plugin", "list"], env=e, cwd=PROJECT, capture_output=True, text=True)
    if "standby@standby" not in (listed.stdout or ""):
        subprocess.run([claude_path(), "plugin", "marketplace", "add", REPO], env=e, cwd=PROJECT, capture_output=True)
        r = subprocess.run([claude_path(), "plugin", "install", "standby@standby"], env=e, cwd=PROJECT,
                           capture_output=True, text=True)
        log("plugin: %s" % ((r.stdout or r.stderr or "").strip().splitlines() or ["installed"])[-1])
    ok, how = signed_in()
    log("Claude Code: %s" % how)
    return ok


def signed_in():
    if not token():
        return False, "no token: run  claude setup-token  once and keep it in %s" % TOKEN_FILE
    try:
        r = subprocess.run([claude_path(), "auth", "status", "--json"], env=env(), cwd=PROJECT,
                           capture_output=True, text=True, timeout=30)
        if json.loads(r.stdout or "{}").get("loggedIn"):
            return True, "signed in with its own token"
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return False, "its token doesn't sign in: make a new one with  claude setup-token"


# ---------------------------------------------------------------- the audio (AudioIO)

class LoopbackAudio(object):
    """AudioIO through mac/testaudio.py's files. speak(pcm) is heard from when it's queued (at real-time pace);
    heard() is what Standby's voice did since the last call, with wall-clock times."""

    def __init__(self, folder):
        self.dir = folder
        self.mic = os.path.join(folder, "mic.pcm")
        self.events = os.path.join(folder, "events.jsonl")
        self._read = 0

    def start(self):
        os.makedirs(self.dir, exist_ok=True)
        open(self.mic, "ab").close()

    def speak(self, pcm):
        t = time.time()
        with open(self.mic, "ab") as fh:
            fh.write(np.asarray(pcm, dtype="<f4").tobytes())
        return t

    def heard(self):
        out = []
        try:
            with open(self.events) as fh:
                fh.seek(self._read)
                for line in fh:
                    if not line.endswith("\n"):
                        break
                    self._read += len(line.encode("utf-8"))
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
        return out

    def stop(self):
        pass


def synth(text, voice="Samantha"):
    """`text` spoken by macOS into a file (never the speakers): 16 kHz mono float32."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "line.wav")
        subprocess.run(["say", "-v", voice, "-o", path, "--file-format=WAVE", "--data-format=LEI16@16000", text],
                       check=True, capture_output=True)
        with wave.open(path) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return x * 0.6


# ---------------------------------------------------------------- the stack

class Stack(object):
    """The test's Standby app (headless, its own port) and Claude Code in a terminal of its own."""

    def __init__(self, run_dir, audio_dir, echo=None, log=print):
        self.run_dir, self.audio_dir, self.log = run_dir, audio_dir, log
        self.env = env(audio_dir, echo)
        self.app = self.cc = None
        self.tui = open(os.path.join(run_dir, "claude-code.log"), "wb")

    def start(self):
        for f in ("ui.port", "ui.token"):
            try:
                os.unlink(os.path.join(ROOT, f))
            except OSError:
                pass
        self.app = subprocess.Popen([PY, os.path.join(REPO, "mac", "ui.py")], env=self.env, cwd=REPO,
                                    stdin=subprocess.DEVNULL, stdout=open(os.path.join(self.run_dir, "app.out"), "wb"),
                                    stderr=subprocess.STDOUT, start_new_session=True)
        if not wait(lambda: os.path.exists(os.path.join(ROOT, "ui.token")) and self.api("/api/lite/state") is not None, 60):
            raise RuntimeError("the test's Standby didn't start (see app.out)")
        master, slave = pty.openpty()
        self.master = master
        self.cc = subprocess.Popen([claude_path(), "--permission-mode", "acceptEdits"], env=dict(self.env, TERM="xterm-256color",
                                   COLUMNS="120", LINES="40"), cwd=PROJECT, stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True, close_fds=True)
        os.close(slave)
        threading.Thread(target=self._drain, daemon=True).start()
        time.sleep(8)                                        # Claude Code and the plugin up

    def _drain(self):
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.tui.write(chunk)
            self.tui.flush()

    def type(self, text):
        for ch in text:
            os.write(self.master, ch.encode("utf-8"))
            time.sleep(0.02)
        time.sleep(0.3)
        os.write(self.master, b"\r")

    def api(self, path, body=None):
        try:
            with open(os.path.join(ROOT, "ui.token")) as fh:
                tok = fh.read().strip()
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), headers={"x-standby-token": tok})
            if body is not None:
                req.data = json.dumps(body).encode("utf-8")
                req.add_header("content-type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read() or b"{}")
        except Exception:
            return None

    def live(self):
        st = self.api("/api/lite/state") or {}
        return (st.get("live") or {}).get("id")

    def stop(self):
        sid = self.live()
        if sid:
            self.api("/api/lite/call/end", {"session": sid})
            time.sleep(3)
        for p in (self.cc, self.app):
            if p is not None and p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except OSError:
                    pass
        try:                                                 # its agent server, started by its Claude Code
            with open(os.path.join(ROOT, "agent", "server.pid")) as fh:
                os.kill(int(fh.read().strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        for p in (self.cc, self.app):
            if p is not None:
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)


def wait(cond, secs, every=0.2):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(every)
    return bool(cond())


# ---------------------------------------------------------------- a scenario

class Call(object):
    """One scenario's call: what the caller said and when, what Standby's voice did, each expect's result."""

    def __init__(self, stack, audio, log=print):
        self.stack, self.audio, self.log = stack, audio, log
        self.said = []          # {t0, t1, text, interrupt}
        self.voice = []         # events from the audio
        self.expects = []
        self.mark = time.time()  # the start of the current exchange (the last thing said)
        self.sid = None
        self.started = None

    def pump(self):
        self.voice += self.audio.heard()

    def voice_since(self, t, kinds=("start",)):
        self.pump()
        return [e for e in self.voice if e["t"] >= t and e["kind"] in kinds]

    def speaking(self):
        """Standby's voice is sounding now: a start not yet followed by an end or a halt."""
        self.pump()
        state = False
        for e in self.voice:
            if e["kind"] == "start":
                state = True
            elif e["kind"] in ("end", "halt"):
                state = False
        return state

    def say(self, text, gap_ms=600, interrupt=False):
        parts = [p.strip() for p in text.split("|") if p.strip()]
        pcm = []
        for i, p in enumerate(parts):
            if i:
                pcm.append(np.zeros(int(RATE * gap_ms / 1000.0), np.float32))
            pcm.append(synth(p))
        x = np.concatenate(pcm)
        t0 = self.audio.speak(x)
        t1 = t0 + len(x) / float(RATE)
        self.said.append({"t0": t0, "t1": t1, "text": " ".join(parts), "interrupt": interrupt})
        self.log("  caller: %s%s" % ("(over it) " if interrupt else "", " ".join(parts)))
        self.mark = t0
        wait(lambda: time.time() >= t1, t1 - time.time() + 1)

    def step(self, s):
        if "say" in s:
            self.say(s["say"], s.get("gap_ms", 600))
        elif "wait" in s:
            what, timeout = s["wait"], s.get("timeout_s", 30)
            last = self.said[-1]["t1"] if self.said else self.started
            if what == "voice_start":
                ok = wait(lambda: self.voice_since(last), timeout)
            elif what == "voice_end":
                ok = wait(lambda: self.voice_since(last) and not self.speaking(), timeout)
            else:                                            # quiet: no voice for quiet_s
                q = s.get("quiet_s", 4)
                ok = wait(lambda: not self.speaking() and time.time() - max([last] + [e["t"] for e in self.voice]) >= q, timeout)
            if not ok:
                self.log("  (waited %ds for %s: it didn't come)" % (timeout, what))
        elif "interrupt" in s:
            last = self.said[-1]["t1"] if self.said else self.started
            if wait(lambda: self.voice_since(last), s.get("timeout_s", 30)):
                start = self.voice_since(last)[0]["t"]
                wait(lambda: time.time() >= start + s.get("after_ms", 1500) / 1000.0, 10)
                self.say(s["interrupt"], s.get("gap_ms", 600), interrupt=True)
            else:
                self.log("  (nothing to talk over: the voice never started)")
        elif "action" in s:
            self.action(s["action"])
        elif "sleep_s" in s:
            time.sleep(s["sleep_s"])
        elif "expect" in s:
            self.expect(s["expect"], s.get("within_s", 0))

    def action(self, what):
        sid = self.sid
        if what in ("pause", "resume"):
            r = self.stack.api("/api/lite/mute", {"session": sid, "muted": what == "pause"})
        elif what == "end":
            r = self.stack.api("/api/lite/call/end", {"session": sid})
        elif what in ("visuals_off", "visuals_on"):
            r = self.stack.api("/api/settings", {"call_visuals": what == "visuals_on"})
        else:
            r = {"error": "no such action"}
        self.log("  action: %s -> %s" % (what, "ok" if r and not r.get("error") else r))

    def session_dir(self):
        return os.path.join(ROOT, "sessions", self.sid) if self.sid else None

    def cards_since(self, t):
        d = self.session_dir()
        out = []
        for f in glob.glob(os.path.join(d, "visuals", "*.json")) if d else []:
            try:
                with open(f) as fh:
                    v = json.load(fh)
                if float(v.get("at") or v.get("t") or 0) >= t:
                    out.append(v)
            except (OSError, ValueError):
                pass
        return out

    def handoffs_since(self, t):
        return [r for r in read_jsonl(os.path.join(self.session_dir() or "", "trace.jsonl"))
                if r.get("kind") == "handoff" and r.get("step") == "handed" and float(r.get("t") or 0) >= t]

    def expect(self, want, within):
        if within:
            time.sleep(within)
        t = self.mark
        for key, value in want.items():
            if key == "spoken_contains":
                said = " ".join(e.get("text") or "" for e in self.voice_since(t, ("say",)))
                ok, got = value.lower() in said.lower(), said[:200]
            elif key == "card":
                n = len(self.cards_since(t))
                ok, got = (n > 0) == bool(value), "%d card(s)" % n
            elif key == "handoff":
                n = len(self.handoffs_since(t))
                ok, got = (n > 0) == bool(value), "%d hand-off(s)" % n
            elif key == "halted":                            # it stopped soon after the caller started talking
                n = len([e for e in self.voice_since(t, ("halt",)) if e["t"] <= t + 2.5])
                ok, got = (n > 0) == bool(value), "%d halt(s) within 2.5 s" % n
            elif key == "file_contains":                     # a hand-off's work, done in the project
                try:
                    with open(os.path.join(PROJECT, value["path"])) as fh:
                        body = fh.read()
                except OSError:
                    body = ""
                ok, got = value["text"].lower() in body.lower(), "%s: %d chars" % (value["path"], len(body))
            elif key == "voice":
                n = len(self.voice_since(t))
                ok, got = (n > 0) == bool(value), "%d answer(s) started" % n
            else:
                ok, got = False, "unknown expect"
            self.expects.append({"want": {key: value}, "ok": ok, "got": got, "at": round(t - self.started, 1)})
            self.log("  expect %s=%r: %s (%s)" % (key, value, "pass" if ok else "FAIL", got))


def read_jsonl(path):
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- scoring

LOG_SIGNS = {"self_cuts": re.compile(r"own voice|echo", re.I), "talked_over": re.compile(r"talked over|barge", re.I),
             "dropped_early": re.compile(r"early question is dropped", re.I),
             "dropped_floor": re.compile(r"dropped while Claude has the floor", re.I)}


def score(call, ui_lines, silence_s=4.0):
    starts = [e for e in call.voice if e["kind"] == "start"]
    ends = [e for e in call.voice if e["kind"] in ("end", "halt")]
    out = {"session": call.sid, "first_sound_s": round(starts[0]["t"] - call.started, 2) if starts else None}
    turns = []
    for s in call.said:
        if s["interrupt"]:
            continue
        nxt = next((e for e in starts if e["t"] > s["t1"]), None)
        turns.append({"said": s["text"][:80], "first_word_s": round(nxt["t"] - s["t1"], 2) if nxt else None})
    out["turns"] = turns
    lat = [t["first_word_s"] for t in turns if t["first_word_s"] is not None]
    out["first_word_median_s"] = round(float(np.median(lat)), 2) if lat else None
    # it started while the caller was still talking (not an interrupt it was answering)
    out["cut_ins"] = [{"at": round(e["t"] - call.started, 1), "text": (e.get("text") or "")[:60]} for e in starts
                      if any(s["t0"] + 0.2 < e["t"] < s["t1"] - 0.2 for s in call.said)]
    # the caller talked over it: did it stop (a halt or a duck within 1.5 s)?
    over = []
    for s in call.said:
        if s["interrupt"]:
            stopped = any(e["kind"] in ("halt", "duck") and s["t0"] <= e["t"] <= s["t0"] + 1.5 for e in call.voice)
            over.append({"said": s["text"][:60], "stopped": stopped})
    out["talked_over"] = over
    # silences: neither side making a sound for longer than silence_s, while the call was on
    spans = sorted([(s["t0"], s["t1"]) for s in call.said] +
                   [(a["t"], next((b["t"] for b in ends if b["t"] >= a["t"]), a["t"])) for a in starts])
    gaps, edge = [], call.started
    for a, b in spans:
        if a - edge > silence_s:
            gaps.append({"at": round(edge - call.started, 1), "s": round(a - edge, 1)})
        edge = max(edge, b)
    out["silences"] = gaps
    out["cards"] = len(call.cards_since(call.started))
    out["handoffs"] = len(call.handoffs_since(call.started))
    out["log"] = {k: sum(1 for l in ui_lines if rx.search(l)) for k, rx in LOG_SIGNS.items()}
    out["expects"] = call.expects
    out["passed"] = all(x["ok"] for x in call.expects)
    return out


def transcribe(audio_dir, events, log=print):
    """What was actually played, transcribed back: each start to its end or halt, from speaker.pcm."""
    try:
        sys.path.insert(0, os.path.join(REPO, "mac"))
        import speechmodel
        from native import speech
        with open(os.path.join(ROOT, "config.json")) as fh:
            cfg = json.load(fh)
        model = speechmodel.load_model(speechmodel.for_load(cfg))
        pcm = np.fromfile(os.path.join(audio_dir, "speaker.pcm"), dtype="<f4")
    except Exception as exc:
        log("  (no transcription: %r)" % exc)
        return []
    out, start = [], None
    for e in events:
        if e["kind"] == "start":
            start = e
        elif e["kind"] in ("end", "halt") and start is not None:
            seg = pcm[int(start.get("pos", 0)):int(e.get("pos", 0))]
            if len(seg) > SPEAKER_RATE // 4:
                x = np.interp(np.arange(0, len(seg), SPEAKER_RATE / float(RATE)), np.arange(len(seg)), seg).astype(np.float32)
                try:
                    text, _words = speech.stream_decode(model, x)
                except Exception as exc:
                    text = "(couldn't transcribe: %r)" % exc
                out.append({"t": start["t"], "heard": text, "said": start.get("text"), "halted": e["kind"] == "halt"})
            start = None
    return out


def report_md(name, sc, heard):
    lines = ["# %s: %s" % (name, "PASS" if sc["passed"] else "FAIL"), "",
             "- session: %s" % sc["session"], "- first sound: %s s" % sc["first_sound_s"],
             "- first word after the caller stops (median): %s s" % sc["first_word_median_s"],
             "- cut into the caller: %d" % len(sc["cut_ins"]),
             "- talked over: %s" % (", ".join("stopped" if x["stopped"] else "KEPT GOING" for x in sc["talked_over"]) or "-"),
             "- silences over 4 s: %s" % (", ".join("%.0f s at %.0f s" % (g["s"], g["at"]) for g in sc["silences"]) or "none"),
             "- cards: %d, hand-offs: %d" % (sc["cards"], sc["handoffs"]),
             "- from the log: %s" % ", ".join("%s %d" % kv for kv in sc["log"].items()), "", "## Expects", ""]
    lines += ["- %s %s: %s" % ("pass" if x["ok"] else "**FAIL**", json.dumps(x["want"]), x["got"]) for x in sc["expects"]]
    lines += ["", "## Turns", ""] + ["- \"%s\" -> %s s" % (t["said"], t["first_word_s"]) for t in sc["turns"]]
    if heard:
        lines += ["", "## What was played (transcribed back)", ""]
        lines += ["- %s%s" % (h["heard"], " (cut)" if h["halted"] else "") for h in heard]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- running

def run_one(path, echo=None, do_transcribe=True, log=print):
    with open(path) as fh:
        sc = json.load(fh)
    name = sc.get("name") or os.path.splitext(os.path.basename(path))[0]
    run_dir = os.path.join(RUNS, time.strftime("%Y%m%d-%H%M%S-") + name)
    audio_dir = os.path.join(run_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    log("== %s: %s" % (name, sc.get("goal", "")))
    with open(os.path.join(ROOT, "config.json")) as fh:
        cfg = json.load(fh)
    cfg["call_visuals"] = True                              # each run starts with cards on
    with open(os.path.join(ROOT, "config.json"), "w") as fh:
        json.dump(cfg, fh, indent=2)
    ui_log = os.path.join(ROOT, "ui.log")
    ui_from = os.path.getsize(ui_log) if os.path.exists(ui_log) else 0
    audio = LoopbackAudio(audio_dir)
    audio.start()
    stack = Stack(run_dir, audio_dir, echo or sc.get("echo"), log=log)
    call = Call(stack, audio, log=log)
    result = None
    try:
        stack.start()
        call.started = time.time()
        stack.type("/standby call")
        if not wait(lambda: stack.live(), 60):
            raise RuntimeError("the call didn't start (see claude-code.log and app.out)")
        call.sid = stack.live()
        log("  call %s" % call.sid)
        if not wait(lambda: call.voice_since(call.started), 60):
            log("  (no greeting in 60 s)")
        wait(lambda: not call.speaking(), 30)
        for s in sc.get("steps", []):
            call.step(s)
        time.sleep(2)
        call.pump()
    except Exception as exc:
        log("  run stopped: %s" % exc)
        result = {"error": str(exc)}
    finally:
        stack.stop()
    try:
        with open(ui_log, errors="replace") as fh:
            fh.seek(ui_from)
            ui_lines = fh.read().splitlines()
    except OSError:
        ui_lines = []
    with open(os.path.join(run_dir, "ui.log"), "w") as fh:
        fh.write("\n".join(ui_lines) + "\n")
    if call.sid and os.path.isdir(call.session_dir()):
        shutil.copytree(call.session_dir(), os.path.join(run_dir, "session"), dirs_exist_ok=True)
    if result is None and call.started:
        result = score(call, ui_lines)
        heard = transcribe(audio_dir, call.voice, log) if do_transcribe else []
        result["played"] = heard
        with open(os.path.join(run_dir, "report.md"), "w") as fh:
            fh.write(report_md(name, result, heard))
    result = dict(result or {}, name=name, said=call.said, voice=call.voice)
    with open(os.path.join(run_dir, "report.json"), "w") as fh:
        json.dump(result, fh, indent=2)
    log("  %s  (%s)" % ("PASS" if result.get("passed") else "FAIL", run_dir))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("setup")
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("names", nargs="*")
    r.add_argument("--echo", help="what it plays comes back into the mic: GAIN[,DELAY_MS]")
    r.add_argument("--audio", default="loopback", choices=["loopback"])
    r.add_argument("--no-transcribe", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "setup":
        return 0 if setup() else 1
    files = sorted(glob.glob(os.path.join(SCENARIOS, "*.json")))
    if a.cmd == "list":
        for f in files:
            with open(f) as fh:
                print("%-24s %s" % (os.path.splitext(os.path.basename(f))[0], json.load(fh).get("goal", "")))
        return 0
    if a.cmd != "run":
        ap.print_help()
        return 2
    ok, how = signed_in()
    if not ok:
        print("Claude Code: %s" % how)
        return 1
    setup(log=lambda m: None)
    pick = [f for f in files if not a.names or os.path.splitext(os.path.basename(f))[0] in a.names]
    results = [run_one(f, a.echo, not a.no_transcribe) for f in pick]
    print("\n%d of %d passed" % (sum(1 for r in results if r.get("passed")), len(results)))
    return 0 if all(r.get("passed") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
