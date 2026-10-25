"""Only the room: what the Mac itself plays (a video, a call on its
speakers, music) taken back out of the microphone, so dictation and voice mode
hear the user and the people near them, never the laptop.

    mic = the room + the Mac's own sound, as it comes back through the air
    reference = the Mac's own sound, as it was played (ScreenCaptureKit, all apps but ours)
    -> an echo canceller learns the way from the speakers to the mic, per frequency,
       and takes out what the reference predicts; what is left is the room

Plain numpy, no Cocoa: a short-time Fourier transform (32 ms frames, every
16 ms), and in each frequency band an adaptive filter over the last few frames
of the reference (NLMS, 160 ms of the room's echo tail), after a bulk delay
found by cross-correlation. Then a gentle suppressor for what the filter
misses. It adapts only while the reference plays and the user is quiet, so the
user's own words are never learnt as echo. Only while dictating or in voice
mode, and never on headphones (nothing to take out).
"""

import atexit
import collections
import os
import threading
import time

import numpy as np

RATE = 16000
FRAME = 512             # 32 ms
HOP = 256               # 16 ms
TAPS = 10               # 160 ms of the echo's tail per band
MAX_DELAY = 0.4         # seconds: speakers' and mic's buffering, and the air
MU = 0.8               # how fast the filter learns
FLOOR = 0.08            # what the suppressor keeps at most cut: no hard gaps (-22 dB)
GATE = 0.7              # a frame this like the reference throughout is the Mac alone: taken down to FLOOR
ECHO_COH = 0.6          # a band this like the reference is echo: what the filter misses there is learnt
SMOOTH = 0.9            # how the likeness is averaged, per 16 ms frame
LEFT = 0.03             # a frame the canceller took this far down (-15 dB) was the Mac's: what is left of
                        # it is taken down too, so it never reads as words
OVERSUB = 1.5           # take out a little more than the filter predicts
DIVERGED = 2.0          # a band whose echo, as predicted, is this much louder than the mic itself: the filter
                        # learnt something wrong there (the user, during double talk), and lets half of it go
QUIET = 1e-6            # reference power below this: nothing is playing
START_DELAY = 0.03      # before the first measure: a laptop's usual output and input latency, less a frame


def _window():
    return np.sqrt(np.hanning(FRAME + 1)[:-1]).astype("float64")   # sqrt-Hann both ways: exact at 50% overlap


class EchoCanceller(object):
    """process(mic, ref) -> the mic with the reference's echo taken out, the
    same length, a block at a time (any size; the two kept in step by the
    caller), LATENCY samples late (32 ms): the same always, so nothing played
    leaves the mic exactly as it was, only later."""

    LATENCY = FRAME

    def __init__(self, rate=RATE, heard=0.0):
        """heard: how likely the mic hears the speakers, before it has measured it
        (on speakers, most likely; the measure soon corrects it)."""
        if rate != RATE:
            raise ValueError("16 kHz only")
        self.win = _window()
        bins = FRAME // 2 + 1
        self.W = np.zeros((TAPS, bins), dtype="complex128")       # the echo path, per band
        self.X = np.zeros((TAPS, bins), dtype="complex128")       # the reference's last TAPS frames
        self.mic_in = np.zeros(0)
        self.ref_in = np.zeros(0)
        self.ref_hist = np.zeros(0)                               # for the bulk delay
        self.mic_hist = np.zeros(0)
        self.out_tail = np.zeros(FRAME - HOP)
        self.out = np.zeros(HOP)                                  # with the overlap's FRAME - HOP: LATENCY
        self.delay = int(START_DELAY * RATE)                      # samples the reference is ahead by
        self.mic_buf = np.zeros(FRAME - HOP)
        self.ref_buf = np.zeros(FRAME - HOP + int(MAX_DELAY * RATE))
        self.frames = 0
        self.playing = 0                                          # hops with something playing, so far
        self.erle = []                                            # echo return loss, dB, per frame with echo
        self.sxe = np.zeros((TAPS, bins), dtype="complex128")     # the error's likeness to each frame of
        self.pxx = np.full((TAPS, bins), 1e-9)                    # the reference, smoothed per band
        self.pee = np.full(bins, 1e-9)
        self.env = []                                             # the last second's (mic, reference) loudness
        self.heard = heard                                        # does the mic hear the reference at all: 0..1
        self.pest = np.full(bins, 1e-9)                           # the echo predicted, and the mic: their
        self.pyy = np.full(bins, 1e-9)                            # power per band, smoothed
        self.leak = np.zeros(bins)                                # what the filter misses, per band, as a
                                                                  # share of the reference's power

    def restart(self):
        """A new dictation, in the same room through the same speakers: the streams
        start again, what it learnt (the echo's way, its delay, whether the mic hears
        the speakers) is kept, so a video already playing is taken out at once."""
        keep = self.W, self.leak, self.delay, self.heard, self.playing
        self.__init__()
        self.W, self.leak, self.delay, self.heard, self.playing = keep

    # -- the bulk delay -----------------------------------------------------
    def find_delay(self, mic, ref):
        """Where the reference best explains the mic, in samples (0..MAX_DELAY):
        mic[t] ~ ref[t - d], by GCC-PHAT (cross-correlation with only the phase
        kept, robust to the room's colour and the speaker's). ref ends where mic
        ends and starts MAX_DELAY before it. A frame's worth earlier than the
        peak, so the filter's first taps hold the direct sound."""
        n = int(MAX_DELAY * RATE)
        if len(mic) < RATE or len(ref) < len(mic) + n:
            return None
        r = ref[-(len(mic) + n):]
        size = 1 << int(np.ceil(np.log2(len(r) + len(mic))))
        cross = np.fft.rfft(mic, size) * np.conj(np.fft.rfft(r, size))
        cc = np.fft.irfft(cross / (np.abs(cross) + 1e-12), size)
        # mic[t] = ref_full[t - d] = r[t + n - d]: the peak sits at lag d - n, i.e. index size - (n - d)
        lags = np.arange(0, n)
        at = int(np.argmax(cc[(size - (n - lags)) % size]))
        return max(0, at - HOP)

    # -- the work -------------------------------------------------------------
    def process(self, mic, ref):
        mic = np.asarray(mic, dtype="float64")
        ref = np.asarray(ref, dtype="float64")
        self.mic_in = np.concatenate([self.mic_in, mic])
        self.ref_in = np.concatenate([self.ref_in, ref])
        while len(self.mic_in) >= HOP and len(self.ref_in) >= HOP:
            self._hop(self.mic_in[:HOP], self.ref_in[:HOP])
            self.mic_in, self.ref_in = self.mic_in[HOP:], self.ref_in[HOP:]
        out, self.out = self.out[:len(mic)], self.out[len(mic):]
        return out.astype("float32")

    def _hop(self, mic, ref):
        self.mic_hist = np.concatenate([self.mic_hist, mic])[-3 * RATE:]
        self.ref_hist = np.concatenate([self.ref_hist, ref])[-(3 * RATE + int(MAX_DELAY * RATE)):]
        self.frames += 1
        # The delay: first after half a second of something playing, then about once a second.
        if np.mean(ref ** 2) > QUIET:
            self.playing += 1
        if self.playing and (self.playing == 31 or self.frames % 62 == 0) and np.mean(ref ** 2) > QUIET:
            d = self.find_delay(self.mic_hist[-2 * RATE:], self.ref_hist)
            if d is not None:
                self.delay = d
        self.mic_buf = np.concatenate([self.mic_buf, mic])[-FRAME:]
        self.ref_buf = np.concatenate([self.ref_buf, ref])[-(FRAME + int(MAX_DELAY * RATE)):]
        end = len(self.ref_buf) - self.delay
        rframe = self.ref_buf[end - FRAME:end]
        Y = np.fft.rfft(self.mic_buf * self.win)
        Xf = np.fft.rfft(rframe * self.win)
        self.X = np.roll(self.X, 1, axis=0)
        self.X[0] = Xf
        echo = np.sum(self.W * self.X, axis=0)
        E = Y - echo
        px = np.sum(np.abs(self.X) ** 2, axis=0) + 1e-9
        ref_on = np.mean(px) > QUIET * FRAME           # playing now, or its echo's tail still in the room
        # Does the mic hear what plays at all? Their loudness over the last second, rising and
        # falling together: a video on speakers, yes; the user over one in headphones, no.
        self.env = (self.env + [(np.sqrt(np.sum(np.abs(Y) ** 2)), np.sqrt(np.mean(px)))])[-62:]
        if len(self.env) >= 12 and ref_on:
            e = np.array(self.env)
            if e[:, 0].std() > 0 and e[:, 1].std() > 0:
                rho = float(np.corrcoef(e[:, 0], e[:, 1])[0, 1])
                self.heard = 0.6 * self.heard + 0.4 * min(1.0, max(0.0, (rho - 0.2) / 0.4))
        heard = self.heard
        if ref_on:
            # Learn where what is left still sounds like the reference (coherent with it): that is
            # the room's echo. The user talking over it is not like the reference, and is not learnt.
            py = np.abs(Y) ** 2
            lam = SMOOTH
            self.sxe = lam * self.sxe + (1 - lam) * np.conj(self.X) * E[None, :]
            self.pxx = lam * self.pxx + (1 - lam) * np.abs(self.X) ** 2
            self.pee = lam * self.pee + (1 - lam) * np.abs(E) ** 2
            # Coherent with any of the frames it remembers: the echo may come a frame or two late.
            coh = np.max(np.abs(self.sxe) ** 2 / (self.pxx * self.pee[None, :] + 1e-18), axis=0)
            mu = MU * np.clip(coh, 0.0, 1.0) * heard
            self.W += (mu / px)[None, :] * np.conj(self.X) * E[None, :]
            echo = np.sum(self.W * self.X, axis=0)
            # The echo can never be louder than the mic that hears it: where the filter says so,
            # it learnt the user's voice as the room's (two voices' harmonics meeting, in double
            # talk), and it would add what it learnt to the mic. Half of it goes, until it is right.
            self.pest = lam * self.pest + (1 - lam) * np.abs(echo) ** 2
            self.pyy = lam * self.pyy + (1 - lam) * py
            wrong = self.pest > DIVERGED * self.pyy
            if wrong.any():
                self.W[:, wrong] *= 0.5
                self.pest[wrong] *= 0.25
                echo = np.sum(self.W * self.X, axis=0)
            E = Y - echo
            # What the filter misses (the speaker's distortion, the room's long tail), learnt where
            # what is left is still the echo, and suppressed: gently, never to silence.
            pe = np.abs(E) ** 2 + 1e-12
            echo_only = (coh > ECHO_COH) & (heard > 0.5)
            self.leak[echo_only] = 0.9 * self.leak[echo_only] + 0.1 * np.minimum(1.0, pe[echo_only] / px[echo_only])
            self.leak[~echo_only] *= 0.995                    # what is not heard as echo fades: no echo, no cut
            residual = self.leak * px
            gain = np.maximum(FLOOR, 1.0 - OVERSUB * residual / pe)
            E = E * gain
            # A frame that is the reference all through (no one in the room speaking): all of it
            # down, so what the filter has not learnt yet never reads as words.
            if heard > 0.5 and np.sum(coh * py) / (np.sum(py) + 1e-12) > GATE:
                E = E * FLOOR
            elif heard > 0.5 and np.sum(np.abs(E) ** 2) < LEFT * np.sum(py):
                E = E * FLOOR
            if np.sum(py) > 1e-6:
                self.erle.append(10 * np.log10(np.sum(py) / (np.sum(np.abs(E) ** 2) + 1e-12)))
        frame = np.fft.irfft(E, FRAME) * self.win
        self.out = np.concatenate([self.out, self.out_tail + frame[:FRAME - HOP]])
        self.out_tail = frame[FRAME - HOP:]


# ---------------------------------------------------------------- what the Mac plays, as it plays

HOLD = 0.25             # seconds the mic waits at most for the Mac's sound of the same moment (it comes in
                        # bursts, up to ~60 ms late: native.appaudio); later than that, the mic goes on without
GONE = 0.5              # no sound from the Mac this long (seconds): it stopped, and the mic waits for none
REANCHOR = 0.15         # the mic's block this much later than its count says: samples were lost (a switch)


def allowed():
    """May the Mac's sound be heard (Screen & System Audio Recording)? True, False,
    or None when macOS cannot say. Asks nothing: it never shows a prompt."""
    from native import appaudio
    return appaudio.preflight()


_LIVE = set()             # the references capturing now: stopped at exit, if no one did


@atexit.register
def _stop_all():
    """A process that exits with ScreenCaptureKit still handing it audio crashes in
    pyobjc's callback ("Python quit unexpectedly" on the user's screen): stopped first.
    Runs after Python has waited for its threads, so no start is still under way."""
    for ref in list(_LIVE):
        ref.stop()


class Reference(object):
    """What the Mac plays (all apps but Standby; or one app, target its id), kept KEEP s
    on the monotonic clock, for each mic to be lined up with. capture:
    native.appaudio.capture(), or a fake with its interface (start(target, feed),
    stop(), ok, error). Nothing of it is kept longer, or written. start() returns at
    once: ScreenCaptureKit takes 0.1-0.3 s to start, and until its first audio the mic
    passes as it is (only 32 ms late), so the user's first word is never held up."""

    KEEP = 3.0

    def __init__(self, capture, log=lambda m: None, clock=time.monotonic, target=None):
        self.capture, self.log, self.clock, self.target = capture, log, clock, target
        self.lock = threading.Lock()
        self.ring = collections.deque()      # (monotonic time of its first sample, samples)
        self.end = None                      # the time its audio reaches; None before any
        self.heard_at = None                 # when its last block came (clock)
        self.started = self.stopped = False
        self.thread = None

    def start(self):
        _LIVE.add(self)
        # Not a daemon: one ending inside ScreenCaptureKit as Python exits would crash it.
        self.thread = threading.Thread(target=self._start, name="room-only reference")
        self.thread.start()
        return self

    def _start(self):
        from native import appaudio
        try:
            self.capture.start(self.target or appaudio.EVERYTHING, self._feed)
            if not getattr(self.capture, "ok", False):
                self.log("room only: the Mac's sound is not heard (%s): the mic as it is"
                         % (getattr(self.capture, "error", None) or getattr(self.capture, "cause", None)))
        except Exception as exc:
            self.log("room only: the Mac's sound did not start (%r): the mic as it is" % exc)
        with self.lock:
            self.started, late = True, self.stopped
        if late:                             # stopped while it was starting
            self._stop()

    def stop(self):
        """Stops the capture (now, or as soon as its start is done)."""
        _LIVE.discard(self)
        with self.lock:
            self.stopped, started = True, self.started
        if started:
            self._stop()

    def _stop(self):
        try:
            self.capture.stop()
        except Exception:
            pass

    def _feed(self, block, t):
        """A block from ScreenCaptureKit's thread, with its own time. Never raises there."""
        try:
            block = np.asarray(block, dtype="float64")
            with self.lock:
                self.ring.append((t, block))
                self.end = t + len(block) / float(RATE)
                self.heard_at = self.clock()
                while self.ring and self.ring[0][0] < self.end - self.KEEP:
                    self.ring.popleft()
        except Exception:
            pass

    def ready(self, end, now):
        """Can the mic up to `end` go on now: the Mac's sound of that moment has come,
        or none will (none yet, it stopped, or it is later than HOLD)."""
        with self.lock:
            have, at = self.end, self.heard_at
        return have is None or have >= end or now - at > GONE or now - end > HOLD

    def window(self, t, n):
        """The Mac's sound over [t, t + n samples), zeros where there was none."""
        out = np.zeros(n)
        with self.lock:
            ring = list(self.ring)
        for start, block in ring:
            i = int(round((start - t) * RATE))
            a, b = max(0, i), min(n, i + len(block))
            if a < b:
                out[a:b] = block[a - i:b - i]
        return out


class Mixed(object):
    """Several references as one, their sound added: the apps switched off in a meeting,
    each on its own capture. refs ({key: Reference}) is replaced whole as apps go off
    and on, never changed in place (the mic's thread reads it)."""

    def __init__(self):
        self.refs = {}

    def ready(self, end, now):
        return all(r.ready(end, now) for r in self.refs.values())

    def window(self, t, n):
        out = np.zeros(n)
        for r in self.refs.values():
            out += r.window(t, n)
        return out


# ---------------------------------------------------------------- the mic, with it taken out

CANCELLERS = {}           # output device -> its canceller: what it learnt is kept for the next dictation
_CANCELLERS_LOCK = threading.Lock()


def canceller_for(output):
    """The canceller for this output device (the room through these speakers),
    restarted: what it learnt last time is kept."""
    with _CANCELLERS_LOCK:
        ec = CANCELLERS.get(output)
        if ec is None:
            ec = CANCELLERS[output] = EchoCanceller()
        else:
            ec.restart()
        return ec


class RoomOnly(object):
    """One mic with what the Mac plays taken out, while dictating or in voice
    mode, from the first block to flush().

        out = room.clean(block)    each block of the mic, in order, as it arrives (any thread)
        out = room.drain()         now and then: what waited for the Mac's sound of its time
        out = room.flush()         at the end: the rest. Then clean() passes the mic as it is.

    Each returns the mic so far, cleaned, in order: a block that may be shorter or
    longer than what came in, or empty. All of them together are exactly the samples
    that came in, so the mic's clock is kept. Each block is placed on the monotonic
    clock by its count of samples since the first (it never jitters), and waits for the
    Mac's sound of that moment, HOLD s at most. None of them raises: anything wrong and
    the mic passes as it is from then on (said once, in .broken)."""

    def __init__(self, reference, canceller, log=lambda m: None, clock=time.monotonic, owns=False):
        self.ref, self.ec, self.log, self.clock, self.owns = reference, canceller, log, clock, owns
        self.lock = threading.Lock()
        self.pending = collections.deque()   # (its time, samples): the mic, waiting for the Mac's sound
        self.t0, self.n = None, 0            # the mic's first sample's time; samples since
        self.skip = canceller.LATENCY        # the canceller's first samples are from before the mic began
        self.done, self.broken = False, None
        self.cpu, self.cleaned = 0.0, 0      # seconds of CPU, samples cleaned

    def clean(self, block):
        block = np.asarray(block, dtype="float32")
        with self.lock:
            if self.done or self.broken:
                return block
            now = self.clock()
            first = now - (self.n + len(block)) / float(RATE)    # where its count says the mic began
            if self.t0 is None or first < self.t0 or first - self.t0 > REANCHOR:
                self.t0 = first
            self.pending.append((self.t0 + self.n / float(RATE), block))
            self.n += len(block)
            return self._run(now, everything=False)

    def drain(self):
        with self.lock:
            if self.done or self.broken:
                return np.zeros(0, "float32")
            return self._run(self.clock(), everything=False)

    def flush(self):
        with self.lock:
            if self.done or self.broken:
                return np.zeros(0, "float32")
            self.done = True
            out = self._run(self.clock(), everything=True)
            if self.broken:
                return out
            try:                             # the canceller's last LATENCY samples: pushed out by silence
                t = self.t0 + self.n / float(RATE) if self.t0 is not None else 0.0
                rest = self.ec.process(np.zeros(self.ec.LATENCY), self.ref.window(t, self.ec.LATENCY))
                out = np.concatenate([out, self._trim(rest)])
            except Exception as exc:
                self._broke(exc)
            self.log("room only: %.1fs of the mic, %.1f%% of a core%s" % (
                self.cleaned / float(RATE), 100.0 * self.cpu / max(1e-9, self.cleaned / float(RATE)),
                ", heard the Mac's sound" if self.ec.heard > 0.5 else ""))
            return out

    def stop(self):
        """The reference too, when it is this mic's own (Standby Dictation's): on a
        thread of its own, so the text is never held up by ScreenCaptureKit's stop
        (not a daemon: Python waits for it at exit, and _stop_all after)."""
        if self.owns:
            threading.Thread(target=self.ref.stop, name="room-only stop").start()

    def _run(self, now, everything):
        out = []
        try:
            while self.pending:
                t, block = self.pending[0]
                if not everything and not self.ref.ready(t + len(block) / float(RATE), now):
                    break
                c0 = time.thread_time()
                cleaned = self.ec.process(block, self.ref.window(t, len(block)))
                self.cpu += time.thread_time() - c0
                self.pending.popleft()
                self.cleaned += len(block)
                out.append(self._trim(cleaned))
        except Exception as exc:
            out.extend(self._broke(exc))
        return np.concatenate(out).astype("float32") if out else np.zeros(0, "float32")

    def _trim(self, cleaned):
        k = min(self.skip, len(cleaned))
        self.skip -= k
        return cleaned[k:]

    def _broke(self, exc):
        """The mic as it is from now on: what waited goes as it came."""
        self.broken = repr(exc)
        self.log("room only: stopped (%s): the mic as it is" % self.broken)
        raw = [b for _t, b in self.pending]
        self.pending.clear()
        return raw


def start(output, log=lambda m: None, capture=None, clock=time.monotonic, permitted=None):
    """The Mac's sound for one dictation or voice turn, starting (it returns at once),
    or None when macOS has not allowed it: nothing is asked, the mic is as it is, and
    the log says so. capture: a fake in tests; else ScreenCaptureKit's. permitted:
    allowed() as last looked at, when the caller keeps it (the preflight takes ~8 ms)."""
    if capture is None:
        if (allowed() if permitted is None else permitted) is not True:
            log("room only: Screen & System Audio Recording is not allowed: the mic as it is")
            return None
        if os.environ.get("STANDBY_NO_DEVICES"):        # a test: never the real capture (2026-10-08)
            return None
        from native import appaudio
        capture = appaudio.capture(sample_rate=RATE, log=log)
    return Reference(capture, log=log, clock=clock).start()


_CORE_AUDIO = []          # Core Audio, made once: its first use imports pyobjc's (~40 ms)


def _core_audio():
    if not _CORE_AUDIO:
        from native import mics
        _CORE_AUDIO.append(mics.system())
    return _CORE_AUDIO[0]


def warm():
    """What for_dictation needs, loaded ahead of the key press (Core Audio's first
    answers take tens of ms; warm, under 2 ms, measured)."""
    try:
        ca = _core_audio()
        ca.output_leaks()
        ca.default_output()
    except Exception:
        pass


def for_dictation(log=lambda m: None, capture=None, devices=None, permitted=None):
    """Standby Dictation's: a RoomOnly with its own reference, starting (it returns
    at once: ~0.5 ms warm, measured), or None: on headphones (nothing to take out),
    or not allowed (nothing is asked; the setup window offers it). devices: Core
    Audio's, or a fake. permitted: the permission as last looked at (start), stale
    by a poll at most (a couple of seconds): revoked meanwhile, the start is refused
    once, the mic is as it is, and native.appaudio's rule asks macOS no more."""
    output = "default"
    try:
        if devices is None:
            devices = _core_audio()
        if not devices.output_leaks():
            return None
        dev = devices.default_output()
        output = dev.uid if dev is not None else output
    except Exception:
        pass                              # unsure: the Mac's sound may reach the mic
    ref = start(output, log=log, capture=capture, permitted=permitted)
    if ref is None:
        return None
    log("room only: on, the Mac's own sound taken out of the mic (%s)" % output)
    return RoomOnly(ref, canceller_for((output, "dictation")), log=log, owns=True)


class FakeCapture(object):
    """The Mac's sound for tests, with no macOS in it: play(block, t) hands a block on
    at once, as ScreenCaptureKit would on its thread; fail_with makes start() refuse."""

    def __init__(self):
        self.feed, self.target, self.ok, self.error = None, None, False, None
        self.fail_with, self.starts, self.stops = None, 0, 0

    def start(self, target=None, feed=None):
        self.starts += 1
        self.target = target
        if self.fail_with:
            self.ok, self.error = False, self.fail_with
            return
        self.feed, self.ok, self.error = feed, True, None

    def play(self, block, t):
        if self.ok and self.feed is not None:
            self.feed(np.asarray(block, dtype="float32"), t)

    def stop(self):
        self.stops += 1
        self.ok, self.feed = False, None
