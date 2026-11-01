"""A call's audio for tests, with no mic or speakers: the AI caller's (tests/caller). On only with
STANDBY_TEST_AUDIO=<folder>, which the recorder reads at its start (standby.py); never otherwise.

    <folder>/mic.pcm        what the caller says: 16 kHz mono float32, appended as it speaks. Read at
                            real-time pace, 20 ms at a time, from where it ended when the call began;
                            the mic's own quiet floor while nothing new is there.
    <folder>/speaker.pcm    Standby's voice as it "played": 24 kHz mono float32, at real-time pace.
    <folder>/events.jsonl   {"t", "kind", ...}, t wall-clock: "say" (a sentence given, its text), "start"
                            (its first sound played), "end" (the voice went quiet), "halt", "duck", "unduck";
                            start, end and halt carry "pos", where speaker.pcm was then (in samples).

Both sides keep wall-clock time. Standby's voice is the real one (tts.py's process, as on a call); what it
plays is timed as a speaker plays it: `playing` stays true until the last sound has played, or halt().

STANDBY_TEST_ECHO=<gain>[,<delay ms>]: what it plays comes back into the mic, as open speakers do, and `aec`
is False, so the recorder's rules for no echo cancellation are the ones run. Without it, `aec` is True: the
echo is taken out, as voice processing does.
"""
import json
import os
import queue
import sys
import threading
import time
import types

import numpy as np

RATE_IN = 16000                  # the mic, as the recorder takes it
BLOCK = RATE_IN // 50            # 20 ms
FLOOR = 3e-4                     # a real mic's own quiet (about -70 dBFS): never exact zeros, which mean refused
MIC_NAME = "Standby Test Mic"


def folder():
    return os.environ.get("STANDBY_TEST_AUDIO") or None


def echo_setting():
    """(gain, delay s) from STANDBY_TEST_ECHO, or None."""
    raw = os.environ.get("STANDBY_TEST_ECHO") or ""
    if not raw.strip():
        return None
    parts = raw.split(",")
    try:
        return float(parts[0]), (float(parts[1]) / 1000.0 if len(parts) > 1 else 0.05)
    except ValueError:
        return None


class Events(object):
    def __init__(self, path):
        self.path, self._lock = path, threading.Lock()

    def write(self, kind, **kw):
        row = dict({"t": time.time(), "kind": kind}, **kw)
        with self._lock, open(self.path, "a") as fh:
            fh.write(json.dumps(row) + "\n")


class Mic(object):
    """mic.pcm, read at real-time pace and given to whoever listens (the recorder's stream, then the
    voice's engine), with the echo of what plays mixed in when that's on."""

    _one = None

    @classmethod
    def get(cls):
        if cls._one is None:
            cls._one = cls(os.path.join(folder(), "mic.pcm"))
        return cls._one

    def __init__(self, path):
        self.path = path
        self.listeners, self._lock = [], threading.Lock()
        self.echo = echo_setting()
        self._played = np.zeros(0, np.float32)       # what the voice played, at 16 kHz, for the echo
        self._played_at = None                       # the wall-clock time of its first sample
        open(path, "ab").close()
        self.pos = os.path.getsize(path) // 4        # in samples: what the caller said before the call isn't heard
        self._rng = np.random.default_rng(7)
        threading.Thread(target=self._run, name="test-mic", daemon=True).start()

    def listen(self, feed):
        with self._lock:
            if feed not in self.listeners:
                self.listeners.append(feed)

    def leave(self, feed):
        with self._lock:
            if feed in self.listeners:
                self.listeners.remove(feed)

    def played(self, samples16k, at):
        """The voice's sound as it played (16 kHz), for the echo."""
        if self.echo is None:
            return
        with self._lock:
            if self._played_at is None or at - (self._played_at + len(self._played) / float(RATE_IN)) > 1.0:
                self._played, self._played_at = np.zeros(0, np.float32), at
            self._played = np.concatenate([self._played, samples16k])[-RATE_IN * 30:]
            self._played_at = at + len(samples16k) / float(RATE_IN) - len(self._played) / float(RATE_IN)

    def _echo(self, now):
        gain, delay = self.echo
        with self._lock:
            if self._played_at is None:
                return None
            start = int(round((now - delay - self._played_at) * RATE_IN))
            if start + BLOCK <= 0 or start >= len(self._played):
                return None
            out = np.zeros(BLOCK, np.float32)
            lo, hi = max(0, start), min(len(self._played), start + BLOCK)
            out[lo - start:hi - start] = self._played[lo:hi]
        return out * gain

    def _run(self):
        t = time.time()
        while True:
            t += BLOCK / float(RATE_IN)
            wait = t - time.time()
            if wait > 0:
                time.sleep(wait)
            elif wait < -1.0:
                t = time.time()                      # far behind (the Mac asleep): never a burst to catch up
            block = self._rng.normal(0.0, FLOOR, BLOCK).astype(np.float32)
            try:
                with open(self.path, "rb") as fh:
                    fh.seek(self.pos * 4)
                    got = np.frombuffer(fh.read(BLOCK * 4), dtype="<f4")
            except OSError:
                got = np.zeros(0, np.float32)
            self.pos += len(got)
            block[:len(got)] += got
            if self.echo is not None:
                e = self._echo(time.time())
                if e is not None:
                    block += e
            with self._lock:
                listeners = list(self.listeners)
            for feed in listeners:
                try:
                    feed(block.copy())
                except Exception as exc:
                    sys.stderr.write("test mic: a listener failed: %r\n" % exc)


class InputStream(object):
    """sounddevice.InputStream as the recorder opens it: callback(indata (frames, 1), frames, time, status)."""

    def __init__(self, device=None, channels=1, samplerate=RATE_IN, dtype="float32", blocksize=0, callback=None, **_kw):
        self.callback, self.blocksize = callback, int(blocksize or BLOCK)
        self._held = np.zeros(0, np.float32)
        self.active = False

    def _feed(self, block):
        self._held = np.concatenate([self._held, block])
        while len(self._held) >= self.blocksize:
            x, self._held = self._held[:self.blocksize], self._held[self.blocksize:]
            if self.callback is not None:
                self.callback(x.reshape(-1, 1), len(x), None, None)

    def start(self):
        self.active = True
        Mic.get().listen(self._feed)

    def stop(self):
        self.active = False
        Mic.get().leave(self._feed)

    def close(self):
        self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_a):
        self.close()


DEVICES = [{"name": MIC_NAME, "index": 0, "max_input_channels": 1, "max_output_channels": 0,
            "default_samplerate": float(RATE_IN), "hostapi": 0}]


def sounddevice_module():
    """What the recorder imports as sounddevice: one mic, the test's."""
    def query_devices(device=None, kind=None):
        if device is None and kind is None:
            return list(DEVICES)
        return dict(DEVICES[0])

    return types.SimpleNamespace(
        InputStream=InputStream, query_devices=query_devices, default=types.SimpleNamespace(device=[0, 0]),
        _terminate=lambda: None, _initialize=lambda: None, PortAudioError=RuntimeError,
        __name__="sounddevice", __loopback__=True)


def install():
    """The recorder's sounddevice is the test's mic from here on (standby.py, at its start)."""
    sys.modules["sounddevice"] = sounddevice_module()


# ---------------------------------------------------------------- the voice's engine

class _Out(object):
    """tts.Speech's out: each piece played at real-time pace, its done() once it has; halt() drops the rest."""

    def __init__(self, backend):
        self.b = backend

    def play(self, samples, done):
        self.b._queue.put((np.asarray(samples, dtype=np.float32), done))

    def halt(self):
        self.b._drop()


class LoopbackBackend(object):
    """VoiceIO's engine (see native.duplex.engine): the test's mic in, Standby's own voice out to a file,
    at the pace a speaker plays it. Standby's voice only: a Mac voice chosen fails, as nothing could say it."""

    OWN_SECS = 4.0
    REOPENS = 2
    CHUNK = 0.02                                 # played 20 ms at a time

    def __init__(self, voice=None, neural=None, log=print):
        import tts
        self.tts, self.log = tts, log
        self.wanted, self.neural = voice, neural
        self.dir = folder()
        self.events = Events(os.path.join(self.dir, "events.jsonl"))
        self.mic = Mic.get()
        self.aec = self.mic.echo is None
        self.speech, self._feed = None, None
        self.failed, self._why, self.retrying_since = "", "", None
        self.said_at = self.first_sound = None
        self.own_audio, self.own_secs = [], 0.0
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._gen = 0                            # halt() starts a new one: what's queued from before is dropped
        self._ducked = False
        self._sounding = False                   # its sound is going out now
        self.played_s = 0.0                      # seconds of the current sentence played so far
        self._text = None
        self._pos = os.path.getsize(os.path.join(self.dir, "speaker.pcm")) // 4 \
            if os.path.exists(os.path.join(self.dir, "speaker.pcm")) else 0
        threading.Thread(target=self._player, name="test-speaker", daemon=True).start()

    # -- the mic ---------------------------------------------------------------
    def start_mic(self, device_name, feed):
        self._feed = feed
        self.mic.listen(feed)
        self._voices()

    def start_output(self):
        self.aec = False
        self._voices()

    def _voices(self):
        if not self.neural:
            self.failed = self.failed or "the test audio plays Standby's own voice only"
            return
        process = self.tts.VoiceProcess(self.neural, log=self.log)
        process.open()
        self.speech = self.tts.Speech(process, _Out(self), log=self.log)

    def stop_mic(self):
        self.halt()
        if self._feed is not None:
            self.mic.leave(self._feed)
            self._feed = None
        speech, self.speech = self.speech, None
        if speech is not None:
            speech.close()

    def use_voice(self, system, neural, why=""):
        if neural and self.speech is not None:
            self.speech.process.use(neural)
        self.wanted, self.neural, self.failed, self._why = system, neural, why or self.failed, why

    # -- Claude's voice ----------------------------------------------------------
    @property
    def playing(self):
        speech = self.speech
        return self._sounding or not self._queue.empty() or bool(speech is not None and speech.busy)

    @property
    def loading(self):
        speech = self.speech
        return bool(speech is not None and not self.failed
                    and getattr(speech.process, "state", "") in ("starting", "downloading", "loading"))

    def begin(self):
        pass

    def tts_status(self):
        st = self.speech.status() if self.speech is not None else None
        return dict(st or {}, failed=self.failed) if self.failed else st

    def say(self, text):
        self.said_at, self.first_sound = time.time(), None
        self.events.write("say", text=text)
        speech = self.speech
        if speech is None or self.failed:
            return
        if not speech.say(text):
            self.failed = "Standby's voice stopped taking sentences"

    def halt(self):
        with self._lock:
            self._gen += 1
            self._ducked = False
        if self.speech is not None:
            self.speech.stop()
        self._drop()
        self.events.write("halt", played_s=round(self.played_s, 3), pos=self._pos)

    def _drop(self):
        while True:
            try:
                _x, done = self._queue.get_nowait()
            except queue.Empty:
                break
            done()
        with self._lock:
            self._gen += 1

    def ramp_in(self):
        pass

    def duck(self):
        with self._lock:
            if self._ducked:
                return
            self._ducked = True
        self.events.write("duck")

    def unduck(self):
        with self._lock:
            if not self._ducked:
                return
            self._ducked = False
        self.events.write("unduck")

    def idle(self, seconds):
        time.sleep(max(0.0, seconds))

    # -- the speaker -----------------------------------------------------------
    def _player(self):
        rate, step = self.tts.RATE, int(self.tts.RATE * self.CHUNK)
        out = open(os.path.join(self.dir, "speaker.pcm"), "ab")
        while True:
            try:
                x, done = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._sounding:
                    self._sounding = False
                    self.events.write("end", played_s=round(self.played_s, 3), pos=self._pos)
                continue
            with self._lock:
                gen = self._gen
            current = getattr(self.speech, "current", None) if self.speech is not None else None
            text = current if isinstance(current, str) else None
            if text != self._text:
                self._text, self.played_s = text, 0.0
            i, t = 0, time.time()
            while i < len(x):
                with self._lock:
                    if self._gen != gen:
                        break
                    ducked = self._ducked
                if ducked:                       # paused under the user's voice, kept where it was
                    time.sleep(self.CHUNK)
                    t = time.time()
                    continue
                piece = x[i:i + step]
                if not self._sounding:
                    self._sounding = True
                    if self.first_sound is None:
                        self.first_sound = time.time()
                    self.events.write("start", text=self._text, pos=self._pos)
                out.write(piece.astype("<f4").tobytes())
                out.flush()
                self._pos += len(piece)
                if self.own_secs < self.OWN_SECS:
                    self.own_audio.append(piece.copy())
                    self.own_secs += len(piece) / float(rate)
                self.mic.played(np.interp(np.arange(0, len(piece), rate / float(RATE_IN)),
                                          np.arange(len(piece)), piece).astype(np.float32), time.time())
                i += len(piece)
                self.played_s += len(piece) / float(rate)
                t += len(piece) / float(rate)
                wait = t - time.time()
                if wait > 0:
                    time.sleep(wait)
            done()


def engine(voice=None, neural=None, log=print):
    return LoopbackBackend(voice=voice, neural=neural, log=log)
