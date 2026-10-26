"""Standby's first use on a Mac it was just installed on, without a microphone, a speaker or a window:

    ~/.standby/venv/bin/python tools/first_use_check.py [--standard]

1. The app's server starts, answers with this launch's token (~/.standby/ui.token), and refuses without it.
2. A sentence spoken by macOS's `say` (to a file, never played) is heard by the speech model the installer
   chose, through the recorder's own decode path, twice: the first run's time is what the first meeting
   or call waits; the second is every run after it.

--standard: then the Standard model too (what a 16 GB Mac gets), fetched if it isn't there: its first run
apart from the next. Before the server starts, the app's own imports are timed, the slowest named: what a
first launch waits for before its window has anything to show.

Prints each step, then one JSON line with the timings. Exits 1 if a step fails. It never uses the
user's Claude Code: only the app, its models and its server."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.join(HERE, "..", "mac")
SENTENCE = "Let's ship the new release on Friday, after the tests pass."
ROOT = os.environ.get("STANDBY_ROOT") or os.path.expanduser("~/.standby")
fails, out = [], {}


def step(label, ok, detail=""):
    print("  %-70s %s%s" % (label, "OK" if ok else "FAIL", "" if ok or detail == "" else "\n     %s" % (detail,)), flush=True)
    if not ok:
        fails.append(label)


def imports():
    """The app's own imports, the first time on this Mac (python -X importtime): the slowest, by cumulative time."""
    t0 = time.time()
    r = subprocess.run([sys.executable, "-X", "importtime", "-c", "import sys; sys.path.insert(0, %r); import ui" % MAC],
                       capture_output=True, text=True, timeout=600, env=dict(os.environ, STANDBY_NO_DEVICES="1"))
    out["ui_import_s"] = round(time.time() - t0, 1)
    rows = []
    for line in r.stderr.splitlines():
        parts = [x.strip() for x in line.split("|")]
        if len(parts) == 3 and parts[1].isdigit():
            rows.append((int(parts[1]) / 1e6, parts[2]))
    top = sorted(rows, reverse=True)[:8]
    out["ui_import_top"] = [[round(s, 1), name] for s, name in top]
    step("the app's code imports (%.1fs; slowest: %s)" % (out["ui_import_s"], ", ".join("%s %.1fs" % (n.strip(), s) for s, n in top[:3])),
         r.returncode == 0, r.stderr[-400:])


def server():
    """The app's server, headless, on a free port: its token, and a refusal without it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, STANDBY_UI_PORT=str(port), STANDBY_NO_DEVICES="1")
    t0 = time.time()
    log = os.path.join(tempfile.gettempdir(), "standby-first-use-ui.log")
    proc = subprocess.Popen([sys.executable, os.path.join(MAC, "ui.py")], env=env, stdout=subprocess.DEVNULL,
                            stderr=open(log, "w"))
    tok, answer, refused = os.path.join(ROOT, "ui.token"), None, None
    try:
        while time.time() - t0 < 120 and answer is None:
            time.sleep(0.5)
            try:
                token = open(tok).read().strip()
                req = urllib.request.Request("http://127.0.0.1:%d/api/whoami" % port, headers={"X-Standby-Token": token})
                answer = json.loads(urllib.request.urlopen(req, timeout=3).read())
            except (OSError, ValueError):
                continue
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/state" % port, timeout=3)
            refused = False
        except urllib.error.HTTPError as exc:
            refused = exc.code == 401
        except OSError:
            refused = None
    finally:
        proc.terminate()
    out["server_s"] = round(time.time() - t0, 1)
    if out["server_s"] > 5:                       # slow to answer: where its start went, by its own log's clock
        print("  the server's log, from its start (%s):" % time.strftime("%H:%M:%S", time.localtime(t0)))
        try:
            for line in open(log).read().splitlines()[:40]:
                print("    " + line)
        except OSError:
            pass
    step("the app's server starts and answers with its token (%.1fs)" % out["server_s"],
         isinstance(answer, dict) and answer.get("app") == "standby", answer)
    step("  and refuses a request without it", refused is True, refused)


def speech():
    """`say` to a file, then the recorder's own decode path, twice."""
    wav = os.path.join(tempfile.mkdtemp(prefix="standby-first-use-"), "hello.wav")
    try:
        subprocess.run(["say", "-o", wav, "--data-format=LEI16@16000", SENTENCE], check=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        step("macOS's say makes a sentence to hear", False, exc)
        return
    sys.path.insert(0, MAC)
    import soundfile as sf
    import speechmodel
    from native import speech as native_speech
    audio, _sr = sf.read(wav, dtype="float32")
    try:
        cfg = json.load(open(os.path.join(ROOT, "config.json")))
    except (OSError, ValueError):
        cfg = {}
    name = speechmodel.for_load(cfg)              # the model the installer chose for this Mac, as the recorder loads it
    out["model"] = name
    hear(speechmodel, native_speech, name, audio, "")
    if "--standard" in sys.argv and speechmodel.chosen(cfg) != "standard_en":
        t0 = time.time()
        path = speechmodel.fetch("standard_en")
        out["standard_fetch_s"] = round(time.time() - t0, 1)
        step("the Standard model is there (a 16 GB Mac's; %.0fs to fetch)" % out["standard_fetch_s"], bool(path), path)
        if path:
            hear(speechmodel, native_speech, path, audio, "standard ")


def hear(speechmodel, native_speech, name, audio, label):
    """The sentence through the recorder's own decode path, twice: the first run, then every run after."""
    for run in ("first", "again"):
        t0 = time.time()
        model = speechmodel.load_model(name, lambda line: None, None)
        loaded = time.time()
        text, _words = native_speech.stream_decode(model, audio)
        key = (label.strip() + " " + run).strip()
        out[key] = {"load_s": round(loaded - t0, 1), "decode_s": round(time.time() - loaded, 1)}
        heard = " ".join(str(text).lower().replace(",", "").replace(".", "").replace("'", "").split())
        want = " ".join(SENTENCE.lower().replace(",", "").replace(".", "").replace("'", "").split())
        step("the %sspeech model hears it (%s run: load %.1fs, decode %.1fs)" % (label, run, out[key]["load_s"], out[key]["decode_s"]),
             heard == want, text)


print("Standby's first use, on this Mac", flush=True)
imports()
server()
speech()
print(json.dumps(dict(out, failed=fails)))
sys.exit(1 if fails else 0)
