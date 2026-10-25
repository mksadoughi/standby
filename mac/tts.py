"""Voice mode's own voice: Kyutai's Pocket TTS, on the GPU with MLX.

    a sentence --> the voice process --> its sound, 80 ms at a time --> played as it comes

The user picked it, and the voice Jean, from the candidates measured on this
Mac: the first sound is ready about
20 ms after the text, whatever its length, and it speaks 5-8x faster than
real time.

The model runs in a process of its own, started when voice mode opens and
ended when it closes. That is how all of its memory (0.4 GB, 1 GB at a peak)
goes back, so nothing stays while voice mode is off, and how it stays off the
recorder's MLX thread. The first time, the model is downloaded from Hugging
Face (about 240 MB, pinned revisions); until it is ready, sentences wait in
its process; if it fails, it is opened again (native.duplex's engine), never macOS's
voice instead.

    python tts.py [--voice jean] [--engine pocket|fake]

reads JSON lines on stdin: {"id": n, "say": text}; {"stop": n}, drop all up
to n; {"quit": true}. Stdin closing is a quit too, so it never outlives the
process that started it. It writes frames on stdout, each a header (kind, id,
length) and a body: b"PCM " float32 mono samples at RATE; b"END " a sentence
done (also when stopped); b"STAT" a JSON state: downloading (done, total),
loading, ready (rate), failed (error).
"""

import json
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
RATE = 24000
# Pinned: what was measured and listened to is what is downloaded. Both CC BY 4.0, not gated.
REPO, REVISION = "mlx-community/pocket-tts", "cbf71d5f6657bbc3f4bc02f85ee408261225bec7"
VOICES_REPO, VOICES_REVISION = "kyutai/pocket-tts-without-voice-cloning", "d4fdd22ae8c8e1cb3634e150ebeff1dab2d16df3"
VOICES = ["alba", "marius", "javert", "jean", "fantine", "cosette", "eponine", "azelma"]
DEFAULT_VOICE = "jean"                   # the user's pick, 2026-10-07
MODEL_FILES = ["config.json", "model.safetensors"]
VOICE_FILES = ["tokenizer.model"] + ["embeddings/%s.safetensors" % v for v in VOICES]
MODEL_BYTES = 235739497                  # model.safetensors; the rest is about 0.6 MB a file at most
DOWNLOAD_BYTES = 240000000               # all of it, measured: 235.7 MB of model, 4.4 MB of voices
CREDIT = "Voice: Pocket TTS by Kyutai (CC BY 4.0)"
ENGINE_NAME = "Pocket TTS"

# How loud each voice comes out (dB RMS while speaking, measured on the same sentence),
# brought to about macOS's level (Samantha: -15 dB): Jean is 11 dB quieter as it comes.
LEVEL_DB = {"alba": -17.9, "marius": -21.0, "javert": -24.2, "jean": -26.5, "fantine": -22.5,
            "cosette": -27.7, "eponine": -23.6, "azelma": -22.1}
TARGET_DB = -16.0


def available():
    """Pocket TTS's packages are installed (install.sh): voice mode can use it."""
    import importlib.util
    try:
        return importlib.util.find_spec("mlx_audio") is not None and importlib.util.find_spec("sentencepiece") is not None
    except (ImportError, ValueError):
        return False


def voice_for(chosen, installed=None):
    """The Pocket voice voice mode speaks in, from config.json's voice_mode.voice:
    automatic (None) is Jean, "pocket:<name>" that one. None for a macOS voice,
    or when the packages are not installed (then macOS's voice, as before)."""
    if not (available() if installed is None else installed):
        return None
    if not chosen:
        return DEFAULT_VOICE
    name = str(chosen)
    if name.startswith("pocket:") and name[len("pocket:"):] in VOICES:
        return name[len("pocket:"):]
    return None


def voices():
    """Settings > Voice: Standby's own voices, Jean first."""
    order = [DEFAULT_VOICE] + [v for v in VOICES if v != DEFAULT_VOICE]
    return [{"id": "pocket:" + v, "name": v.capitalize(), "quality": "standby", "lang": "en"} for v in order]


# How a word is written for the voice to say it right: the engines read some words their own way.
# Only what is heard changes; the sentence shown, and kept, is the one Claude wrote. Measured
# by the recorder's own speech model on Pocket's sound: "Standby" came out "Stanby", "Stabby",
# "Stan B" (20 of 20 sentences, Jean and Alba); "Stand by" right in 20 of 20.
SAY_AS = [(re.compile(r"\bstandby\b", re.I), "Stand by")]


def say_as(text):
    """`text` written as the voice should say it (SAY_AS)."""
    for word, spoken in SAY_AS:
        text = word.sub(spoken, text)
    return text


def lead_in(text):
    """The text as the model reads it best. A comma first: without one it often
    swallows or garbles the first word ("Sure, ...", "No, ..."). Read back by
    the recorder's own speech model, the first word was right 61 times in 64
    with it, 53 without, and the rest as good."""
    return ", " + text.strip()


def limit(x):
    """Peaks above 0.9 rounded off, to 0.99 at most: never clipped."""
    a = np.abs(x)
    over = a > 0.9
    if over.any():
        x = x.copy()
        x[over] = np.sign(x[over]) * (0.9 + 0.09 * np.tanh((a[over] - 0.9) / 0.09))
    return x


# ---------------------------------------------------------------- frames on the pipe

HEADER = struct.Struct("<4sIi")


def write_frame(fh, kind, sid=0, body=b""):
    fh.write(HEADER.pack(kind, sid, len(body)) + body)
    fh.flush()


def _read_exact(fh, n):
    out = b""
    while len(out) < n:
        chunk = fh.read(n - len(out))
        if not chunk:
            return None
        out += chunk
    return out


def read_frame(fh):
    """(kind, id, body), or None once the other side has gone."""
    head = _read_exact(fh, HEADER.size)
    if head is None:
        return None
    kind, sid, n = HEADER.unpack(head)
    body = _read_exact(fh, n) if n else b""
    return None if body is None else (kind, sid, body)


# ---------------------------------------------------------------- the voice process

def _hub():
    root = os.environ.get("HF_HUB_CACHE")
    if root:
        return Path(root)
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def _snapshot(repo, rev):
    return _hub() / ("models--" + repo.replace("/", "--")) / "snapshots" / rev


def _missing():
    """The pinned files not in the Hugging Face cache yet: (repo, revision, file)."""
    want = [(REPO, REVISION, f) for f in MODEL_FILES] + [(VOICES_REPO, VOICES_REVISION, f) for f in VOICE_FILES]
    return [w for w in want if not (_snapshot(w[0], w[1]) / w[2]).is_file()]


def _blob_bytes():
    total = 0
    for repo in (REPO, VOICES_REPO):
        blobs = _hub() / ("models--" + repo.replace("/", "--")) / "blobs"
        try:
            total += sum(p.stat().st_size for p in blobs.iterdir())
        except OSError:
            pass
    return total


def fetch(tell):
    """The model's folder, downloaded first if it is not all there, with
    progress told every half second (bytes in the cache: the same whichever
    way Hugging Face sends them)."""
    missing = _missing()
    if not missing:
        os.environ["HF_HUB_OFFLINE"] = "1"           # all there: no network check on the way
        return _snapshot(REPO, REVISION)
    from huggingface_hub import hf_hub_download
    base, stop = _blob_bytes(), threading.Event()
    need = sum(MODEL_BYTES if name == "model.safetensors" else 600000 for _r, _v, name in missing)

    def watch():
        while not stop.wait(0.5):
            tell({"state": "downloading", "done": min(need - 1, _blob_bytes() - base), "total": need})
    tell({"state": "downloading", "done": 0, "total": need})
    threading.Thread(target=watch, name="download", daemon=True).start()
    try:
        # All at once: each small file is about 3 s of requests, so one after another
        # the voices added half a minute after the model was in (97% for a minute).
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(missing)) as pool:
            for f in [pool.submit(hf_hub_download, repo, name, revision=rev) for repo, rev, name in missing]:
                f.result()
    finally:
        stop.set()
    tell({"state": "downloading", "done": need, "total": need})
    os.environ["HF_HUB_OFFLINE"] = "1"
    return _snapshot(REPO, REVISION)


class Clean(object):
    """One sentence of Standby's voice, quiet where it says nothing (the user,
    2026-10-11: "whenever the AI talks... a kind of white noise").

    Pocket's own output carries a DC offset (up to 0.015) and a noise floor in its
    pauses, about -65 dBFS above 150 Hz, pink-ish: the gain to macOS's level (+10.5
    dB for Jean) lifts it to -55, heard as hiss in every pause and for 0.3 to 0.6 s
    around each sentence, where macOS's voice is silent. Measured offline; the
    player, its resampling and the mixer add nothing. So, after the gain:

    - a high-pass at HIGHPASS Hz: the offset and the rumble (each sentence also
      ended on a step of up to 0.05 from it: a tick);
    - a gate, on the sound above SIDECHAIN Hz (the rumble kept it open): it opens
      on a BLOCK over OPEN_DB, AHEAD blocks early so a word's start is never cut,
      and closes HOLD after the last one, by FLOOR_DB, ramped over a block. Short
      pauses inside speech stay as they are. The floor's own peaks reach -44 dBFS,
      speech is over -40 within 20 ms of its start: only speech opens it.

    feed(samples) gives back what is ready, AHEAD blocks later; end() the rest."""

    HIGHPASS = 70.0
    SIDECHAIN = 200.0
    BLOCK = 240                    # 10 ms at 24 kHz
    OPEN_DB = -40.0                # after the gain: speech peaks -40 to -15 dBFS, the floor -65 to -44
    FLOOR_DB = -40.0               # the floor's -55 to -95 dBFS: under the -70 asked for
    HOLD = 12                      # blocks (120 ms) after the last loud one before it closes
    AHEAD = 2                      # blocks (20 ms) it opens early: the latency it adds

    def __init__(self, rate=RATE):
        from scipy.signal import butter
        self.sos = butter(2, self.HIGHPASS, "highpass", fs=rate, output="sos")
        self.side = butter(2, self.SIDECHAIN, "highpass", fs=rate, output="sos")
        self.zi, self.szi = np.zeros((1, 2)), np.zeros((1, 2))
        self.rest = np.zeros(0, "float32")        # input short of a whole block
        self.pending = deque()                    # (block, loud): the ones waiting for a look ahead
        self.floor = 10 ** (self.FLOOR_DB / 20.0)
        self.gain = None                          # set by the first block: closed if its lead-in is floor
        self.since = self.HOLD                    # blocks since the last loud one

    def _loud(self, block):
        return 10 * np.log10(max(float(np.mean(block.astype("float64") ** 2)), 1e-20)) >= self.OPEN_DB

    def _out(self, block, loud, ahead):
        self.since = 0 if loud else self.since + 1
        target = 1.0 if (loud or ahead or self.since < self.HOLD) else self.floor
        start = (1.0 if loud else self.floor) if self.gain is None else self.gain
        ramp = np.linspace(start, target, len(block) + 1, dtype="float32")[1:]
        self.gain = target
        return block * ramp

    def feed(self, samples):
        from scipy.signal import sosfilt
        x = np.concatenate([self.rest, np.asarray(samples, dtype="float32")])
        whole = len(x) // self.BLOCK * self.BLOCK
        self.rest = x[whole:]
        if not whole:
            return np.zeros(0, "float32")
        y, self.zi = sosfilt(self.sos, x[:whole], zi=self.zi)
        s, self.szi = sosfilt(self.side, x[:whole], zi=self.szi)
        out = []
        for i in range(0, whole, self.BLOCK):
            self.pending.append((y[i:i + self.BLOCK].astype("float32"), self._loud(s[i:i + self.BLOCK])))
            if len(self.pending) > self.AHEAD:
                block, loud = self.pending.popleft()
                out.append(self._out(block, loud, any(l for _b, l in self.pending)))
        return np.concatenate(out) if out else np.zeros(0, "float32")

    def end(self):
        from scipy.signal import sosfilt
        out = []
        while self.pending:
            block, loud = self.pending.popleft()
            out.append(self._out(block, loud, any(l for _b, l in self.pending)))
        if len(self.rest):
            y, self.zi = sosfilt(self.sos, self.rest, zi=self.zi)
            out.append(y.astype("float32") * (self.floor if self.gain is None else self.gain))
            self.rest = np.zeros(0, "float32")
        if not out:
            return np.zeros(0, "float32")
        tail = np.concatenate(out)
        n = min(len(tail), 60)                    # its last 2.5 ms to zero: no step at its end
        tail[-n:] *= np.linspace(1.0, 0.0, n, dtype="float32")
        return tail


class FakeEngine(object):
    """For tests: no model, the same process and frames. STANDBY_TTS_FAKE, JSON:
    load (seconds), download (bytes to pretend), fail ("load", or "say": the
    process dies mid-sentence), hang (a sentence that never comes), piece
    (seconds per 80 ms piece). One piece per word."""

    rate = RATE

    def __init__(self, voice):
        self.voice = voice
        self.opts = json.loads(os.environ.get("STANDBY_TTS_FAKE") or "{}")

    def use(self, voice):
        self.voice = voice

    def load(self, tell):
        o = self.opts
        if o.get("download"):
            for done in (0, o["download"] // 2, o["download"]):
                tell({"state": "downloading", "done": done, "total": o["download"]})
                time.sleep(o.get("load", 0) / 3.0)
        tell({"state": "loading"})
        time.sleep(o.get("load", 0))
        if o.get("fail") == "load":
            raise RuntimeError("no model here")

    def stream(self, text):
        if self.opts.get("hang") and self.opts["hang"] in text:
            time.sleep(3600)
        for i, _word in enumerate(text.split() or [""]):
            if self.opts.get("fail") == "say" and i == 1:
                os._exit(3)
            time.sleep(self.opts.get("piece", 0.0))
            sign = -1 if self.voice == "alba" else 1          # a test hears which voice said it
            yield np.full(RATE * 80 // 1000, sign * 0.01 * (i + 1), "float32")


def serve(engine, inp, out, log=None):
    """The voice process's loop: load, say ready, then each sentence in turn."""
    log = log or (lambda m: sys.stderr.write(m + "\n"))
    lock = threading.Lock()

    def send(kind, sid=0, body=b""):
        with lock:
            write_frame(out, kind, sid, body)

    def tell(st):
        send(b"STAT", 0, json.dumps(st).encode("ascii"))

    todo, stop = queue.Queue(), [0]

    def read():
        for line in inp:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("quit"):
                break
            if "stop" in row:
                stop[0] = max(stop[0], int(row["stop"]))
            elif row.get("voice"):              # Settings changed it: from the next sentence
                todo.put((None, str(row["voice"])))
            elif row.get("say"):
                todo.put((int(row.get("id") or 0), str(row["say"])))
        todo.put(None)                          # stdin closed: whoever started this went away
    threading.Thread(target=read, name="commands", daemon=True).start()
    t0 = time.time()
    try:
        engine.load(tell)
    except Exception as exc:
        tell({"state": "failed", "error": ("%s: %s" % (exc.__class__.__name__, exc))[:300]})
        return 1
    import resource
    tell({"state": "ready", "rate": engine.rate, "load_s": round(time.time() - t0, 2),
          "rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1000000})     # bytes on macOS
    while True:
        item = todo.get()
        if item is None:
            return 0
        sid, text = item
        if sid is None:
            engine.use(text)
            log("tts: now in the voice %s" % ascii_text(engine.voice))
            continue
        if sid > stop[0]:
            try:
                for piece in engine.stream(text):
                    if sid <= stop[0]:
                        break
                    send(b"PCM ", sid, np.asarray(piece, dtype="<f4").tobytes())
            except Exception as exc:
                log("tts: a sentence failed: %r" % exc)
        send(b"END ", sid)


def _lines(fd):
    """Lines from a file descriptor, read with os.read: no Python file object,
    so no lock a thread could still hold when the process ends."""
    rest = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            if rest:
                yield rest
            return
        rest += chunk
        while b"\n" in rest:
            line, rest = rest.split(b"\n", 1)
            yield line


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Voice mode's voice process (see tts.py)")
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--engine", default="pocket", choices=["pocket", "fake"])
    a = p.parse_args(argv)
    from native import qos
    qos.interactive()                       # its sentences are a call's voice: ahead of a busy Mac's other work
    # The frames get stdout to themselves: whatever a library prints goes to stderr.
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    sys.modules.setdefault("tts", sys.modules[__name__])     # this file, once: the engine's code uses it as `tts`
    from native import tts as os_tts
    engine = FakeEngine(a.voice) if a.engine == "fake" else os_tts.engine(a.voice)
    code = 1
    try:
        code = serve(engine, _lines(0), out)
    finally:
        # Out at once, past Python's shutdown: a thread still reading (stdin) or
        # downloading would make it abort, and macOS shows "Python quit unexpectedly".
        for fh in (out, sys.stderr):
            try:
                fh.flush()
            except Exception:
                pass
        os._exit(code)


# ---------------------------------------------------------------- the side that plays it

def command(voice):
    """How the voice process is started: this interpreter (the app's venv, where
    install.sh puts the packages), STANDBY_TTS_PYTHON instead for development,
    STANDBY_TTS_ENGINE=fake in tests."""
    py = os.environ.get("STANDBY_TTS_PYTHON") or sys.executable
    return [py, str(HERE / "tts.py"), "--voice", voice, "--engine", os.environ.get("STANDBY_TTS_ENGINE") or "pocket"]


def _env():
    env = dict(os.environ)
    # Under launchd there is no locale: UTF-8 always. No progress bars, no telemetry.
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_PROGRESS_BARS="1",
               HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false", TRANSFORMERS_VERBOSITY="error",
               TQDM_DISABLE="1")
    # The recorder goes offline once its speech model is cached (standby.py load_model): not
    # for this. The voice process goes offline by itself once its own files are there (fetch).
    env.pop("HF_HUB_OFFLINE", None)
    return env


def ascii_text(text):
    """Log lines are ASCII: under launchd there is no locale, and anything else can crash the app."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def ascii_line(raw):
    return ascii_text(raw.decode("utf-8", "replace").rstrip())


class VoiceProcess(object):
    """The voice process, from the side that plays it. open() starts it and
    returns at once; say(sid, text) and stop(upto) go to it; its sound comes
    to on_audio(sid, samples), then on_end(sid), and on_gone() if it dies, on
    a thread of its own. state: off, starting, downloading, loading, ready,
    failed (error says why)."""

    def __init__(self, voice=DEFAULT_VOICE, log=print, argv=None):
        self.voice, self.log, self.argv = voice, log, argv
        self.on_audio = self.on_end = self.on_gone = None
        self.state, self.error, self.done, self.total = "off", None, 0, 0
        self.downloading = False          # this time it had to download first
        self.exit_code = None             # how the last one ended: < 0 is a signal (a crash)
        self.ready_at = None              # when it last became ready
        self._proc, self._lock, self._t0 = None, threading.Lock(), None

    @property
    def ready(self):
        return self.state == "ready"

    def open(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self.state, self.error, self.downloading, self._t0 = "starting", None, False, time.time()
            try:
                proc = self._proc = subprocess.Popen(
                    self.argv or command(self.voice), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=_env(), close_fds=True)
            except OSError as exc:
                self._proc, self.state, self.error = None, "failed", ascii_text(repr(exc))
                self.log("voice: Standby's voice did not start (%s): macOS's voice speaks" % self.error)
                return
        threading.Thread(target=self._frames, args=(proc,), name="tts-out", daemon=True).start()
        threading.Thread(target=self._stderr, args=(proc,), name="tts-log", daemon=True).start()

    def _send(self, row):
        proc = self._proc
        if proc is None:
            return False
        try:
            proc.stdin.write((json.dumps(row) + "\n").encode("ascii"))
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def say(self, sid, text):
        with self._lock:
            return self._send({"id": sid, "say": text})

    def stop(self, upto):
        with self._lock:
            self._send({"stop": upto})

    def use(self, voice):
        """Another of Standby's voices, from the next sentence: the process as it is. Opened
        again later, it starts in this one."""
        with self._lock:
            self.voice = voice
            self._send({"voice": voice})

    def close(self):
        """Ends the process: everything it held goes back."""
        with self._lock:
            proc, self._proc, self.state = self._proc, None, "off"
        if proc is None:
            return
        try:
            proc.stdin.write(b'{"quit": true}\n')
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.log("voice: Standby's voice did not quit: stopped")
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self.exit_code = proc.returncode

    def _frames(self, proc):
        from native import qos
        qos.interactive()                            # the voice's sound, on its way to the player
        while True:
            f = read_frame(proc.stdout)
            if f is None or proc is not self._proc:
                break
            kind, sid, body = f
            try:
                if kind == b"PCM ":
                    if self.on_audio is not None:
                        self.on_audio(sid, np.frombuffer(body, dtype="<f4"))
                elif kind == b"END ":
                    if self.on_end is not None:
                        self.on_end(sid)
                elif kind == b"STAT":
                    self._state(json.loads(body.decode("ascii")))
            except Exception as exc:
                self.log("voice: Standby's voice: %s" % ascii_text(repr(exc)))
        if proc is self._proc:                       # not closed: it died
            code = self.exit_code = proc.wait()
            with self._lock:
                if proc is self._proc:
                    self._proc = None
                    if self.state != "failed":
                        self.state, self.error = "failed", self.error or "it stopped (exit %s)" % code
            self.log("voice: Standby's voice stopped (%s): macOS's voice speaks" % self.error)
            if self.on_gone is not None:
                self.on_gone()

    def _state(self, st):
        state = st.get("state")
        if state == "downloading":
            if not self.downloading:
                self.log("voice: downloading Standby's voice (%s, about %d MB, once)"
                         % (ENGINE_NAME, int(st.get("total") or DOWNLOAD_BYTES) // 1000000))
            self.downloading, self.done, self.total = True, int(st.get("done") or 0), int(st.get("total") or 0)
        elif state == "ready":
            self.ready_at = time.time()
            self.log("voice: Standby's voice ready (%s, %s) in %.1fs, %s MB"
                     % (ENGINE_NAME, self.voice.capitalize(), time.time() - (self._t0 or time.time()), st.get("rss_mb", "?")))
        elif state == "failed":
            self.error = ascii_text(st.get("error") or "failed")
            self.log("voice: Standby's voice failed (%s): macOS's voice speaks" % self.error)
        if state:
            self.state = state

    def _stderr(self, proc):
        for raw in iter(proc.stderr.readline, b""):
            line = ascii_line(raw)
            if line:
                self.log("voice process: %s" % line[:300])


def getting_ready(state, downloading, done=0, total=0):
    """What the voice screen says while Standby's voice is first downloaded
    (macOS's speaks meanwhile); nothing on an ordinary start, which takes 2 s."""
    if not downloading or state not in ("downloading", "loading"):
        return ""
    pct = getting_ready_pct(state, downloading, done, total)
    return "Getting the voice ready…" + (" %d%%" % pct if pct is not None else "")


def getting_ready_pct(state, downloading, done=0, total=0):
    """How far that first download is, 0-99, for a progress line; None while
    it loads after, without a known total, or when it is not getting ready."""
    if not downloading or state != "downloading" or not total:
        return None
    return max(0, min(99, 100 * int(done) // int(total)))


def not_ready(speech):
    """Why an answer can't be in Standby's voice now, in a few words for the log:
    speech is a Speech, or None when it never opened (no speakers for it)."""
    if speech is None:
        return "Standby's voice isn't open"
    p = speech.process
    since = (" (%.1fs in)" % (time.time() - p._t0)) if getattr(p, "_t0", None) else ""
    if p.state in ("starting", "loading"):
        return "Standby's voice is still loading%s" % since
    if p.state == "downloading":
        pct = getting_ready_pct(p.state, True, p.done, p.total)
        return "Standby's voice is downloading%s" % (" (%d%%)" % pct if pct is not None else "")
    if p.state == "failed":
        return "Standby's voice failed (%s)" % ascii_text(p.error or "no reason given")[:160]
    return "Standby's voice is %s" % p.state


class Speech(object):
    """Sentences in Standby's voice, through `out`, one after another: each goes
    to the voice process as soon as it is given, so the next is being made while
    one plays, and its sound is played as it comes.

    As it comes, but never faster than the player needs it (the user: Try
    "played glitchy"): with the GPU busy, Pocket makes sound slower than real
    time, and played piece by piece it ran dry every 250 ms (41 gaps of about
    170 ms in a 3 s line, measured at the mixer). So a sentence starts, and
    starts again if it ever runs dry, only once enough is held for the rest of it
    at the rate it is being made: PREROLL when that is faster than real time
    (on an idle Mac, 4x: the first sound about 40 ms later), more when it is
    not. Its end, or the process dying, plays whatever is held.

    out: play(samples, done) plays a piece after the ones before it, and calls
    done() once it has played or been dropped; halt() drops them all. This is
    the speakers' interface in voice.py: say, stop, speaking, busy, current,
    started_at. Sentences the process took with it when it died are in lost,
    for macOS's voice to say."""

    STALL = 8.0           # a sentence with no sound this long after it was given: the process is stuck
    PREROLL = 0.2         # seconds of sound held before a sentence starts (or starts again)
    CHARS_PER_S = 15.0    # a little faster than any of the voices speaks: a sentence's length guessed long

    def __init__(self, process, out, log=print):
        self.process, self.out, self.log = process, out, log
        process.on_audio, process.on_end, process.on_gone = self._audio, self._end, self._gone
        self._items, self._order = {}, deque()
        self._lock, self._play = threading.Lock(), threading.Lock()
        self._sid = 0
        self._ahead, self._gen = 0.0, 0       # seconds given to out not played yet; stop() starts a new count
        self.lost = []

    @property
    def usable(self):
        return self.process.ready

    def getting_ready(self):
        p = self.process
        return getting_ready(p.state, p.downloading, p.done, p.total)

    def preparing_pct(self):
        p = self.process
        return getting_ready_pct(p.state, p.downloading, p.done, p.total)

    def status(self):
        p = self.process
        return {"state": p.state, "downloading": p.downloading, "done": p.done, "total": p.total}

    def say(self, text):
        with self._lock:
            self._sid += 1
            sid = self._sid
            self._items[sid] = {"text": text, "pending": 0, "ended": False, "started": None,
                                "sent": time.time(), "heard": False, "held": [], "made": 0.0, "first": None}
            self._order.append(sid)
        if not self.process.say(sid, text):
            with self._lock:
                self._drop(sid)
            return False
        return True

    def stop(self):
        with self._play:
            with self._lock:
                self._items.clear()
                self._order.clear()
                upto = self._sid
                self._ahead, self._gen = 0.0, self._gen + 1
            self.process.stop(upto)
            try:
                self.out.halt()
            except Exception as exc:
                self.log("voice: halt: %r" % exc)

    def close(self):
        self.stop()
        self.process.close()

    def take_lost(self):
        with self._lock:
            lost, self.lost = self.lost, []
        return lost

    @property
    def speaking(self):
        with self._lock:
            return bool(self._order) and self._items[self._order[0]]["pending"] > 0

    @property
    def busy(self):
        stuck = False
        with self._lock:
            if self._order:
                head = self._items[self._order[0]]
                # Counted from when it could start: a sentence given while the voice loads waits for it.
                since = max(head["sent"], getattr(self.process, "ready_at", None) or 0)
                stuck = self.process.ready and not head["heard"] and not head["ended"] and time.time() - since > self.STALL
        if stuck:
            self.log("voice: Standby's voice is stuck: macOS's voice speaks")
            self.process.close()
            self._gone()
        with self._lock:
            return bool(self._order)

    @property
    def current(self):
        with self._lock:
            head = self._items[self._order[0]] if self._order else None
            return head["text"] if head and head["started"] else ""

    @property
    def started_at(self):
        with self._lock:
            return self._items[self._order[0]]["started"] if self._order else None

    # -- from the voice process's thread, and the player's ------------------------
    def _audio(self, sid, samples):
        with self._play:
            with self._lock:
                it = self._items.get(sid)
                if it is None:
                    return                             # stopped: never played
                now = time.time()
                it["heard"] = True
                it["first"] = it["first"] or now
                it["made"] += len(samples) / float(RATE)
                it["held"].append(samples)
                go = self._release(sid, now) if self._enough(it, now) else []
            self._give(go)

    def _enough(self, it, now):
        """Whether what is held of a sentence can go to the player now: it is all
        there, the player is still busy (nothing to catch up), or enough is held
        that it won't run dry before the sentence is made, at its rate so far."""
        if it["ended"] or self._ahead > 0:
            return True
        held = sum(len(x) for x in it["held"]) / float(RATE)
        if held < self.PREROLL:
            return False
        first = len(it["held"][0]) / float(RATE) if it["started"] is None else 0.0
        spent = now - it["first"]
        if spent <= 0:
            return True
        rate = (it["made"] - first) / spent        # seconds of sound made per second
        if rate >= 1.0:
            return True
        rest = max(len(lead_in(it["text"])) / self.CHARS_PER_S - it["made"], self.PREROLL)
        return held >= rest * (1.0 / max(rate, 0.05) - 1.0)

    def _release(self, sid, now):
        """What is held of `sid`, counted as given to the player (under _lock)."""
        it = self._items[sid]
        go, it["held"] = it["held"], []
        for x in go:
            it["pending"] += 1
            self._ahead += len(x) / float(RATE)
        if go and it["started"] is None and self._order and self._order[0] == sid:
            it["started"] = now
        return [(sid, x, self._gen) for x in go]

    def _give(self, go):
        """To the player, in order (under _play, not _lock: done can come at once)."""
        for sid, x, gen in go:
            self.out.play(x, lambda sid=sid, d=len(x) / float(RATE), gen=gen: self._played(sid, d, gen))

    def _played(self, sid, secs=0.0, gen=None):
        with self._lock:
            if gen == self._gen:
                self._ahead = max(0.0, self._ahead - secs)
            it = self._items.get(sid)
            if it is not None:
                it["pending"] -= 1
                self._settle()

    def _end(self, sid):
        with self._play:
            with self._lock:
                it = self._items.get(sid)
                if it is None:
                    return
                it["ended"] = True
                go = self._release(sid, time.time())
                self._settle()
            self._give(go)

    def _gone(self):
        """The process died: what had started plays out, the rest is lost."""
        with self._play:
            go = []
            with self._lock:
                for sid in list(self._order):
                    it = self._items[sid]
                    if it["heard"]:
                        it["ended"] = True
                        go += self._release(sid, time.time())
                    else:
                        self.lost.append(it["text"])
                        self._drop(sid)
                self._settle()
            self._give(go)

    def _drop(self, sid):
        self._items.pop(sid, None)
        try:
            self._order.remove(sid)
        except ValueError:
            pass

    def _settle(self):
        while self._order:
            head = self._items[self._order[0]]
            if not (head["ended"] and head["pending"] <= 0):
                break
            self._drop(self._order[0])
        if self._order:
            head = self._items[self._order[0]]
            if head["started"] is None and head["pending"] > 0:
                head["started"] = time.time()


# ---------------------------------------------------------------- to the speakers

def pcm_format(avf, rate=RATE):
    return avf.AVAudioFormat.alloc().initWithCommonFormat_sampleRate_channels_interleaved_(
        1, float(rate), 1, False)                      # 1: AVAudioPCMFormatFloat32


def pcm_buffer(avf, fmt, samples):
    """An AVAudioPCMBuffer holding `samples` (float32, mono)."""
    x = np.asarray(samples, dtype="float32")
    buf = avf.AVAudioPCMBuffer.alloc().initWithPCMFormat_frameCapacity_(fmt, len(x))
    buf.setFrameLength_(len(x))
    np.frombuffer(buf.floatChannelData()[0].as_buffer(len(x)), dtype="float32")[:] = x
    return buf


def speakers(log=print):
    """Standby's voice to the speakers where the recorder does not play it (Settings' Try): the
    OS's own output (native.tts), output only, from open() to close()."""
    from native import tts as os_tts
    return os_tts.speakers(RATE, log)


if __name__ == "__main__":
    main()
