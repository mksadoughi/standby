"""Has the speaker finished? End-of-turn detection for voice mode.

Silence alone is a poor judge: people pause mid-sentence ("what I want is...
the deadline"), and waiting long enough to be safe makes every answer slow.
Smart Turn v3 (pipecat-ai, BSD-2) listens to the audio of the turn itself,
grammar, tone and pace, and says whether it sounds finished. About 8M
parameters (Whisper Tiny's encoder plus a classifier), 8.7 MB int8 on disk, a
few milliseconds a call on the CPU through onnxruntime.

The CPU it stays on: onnxruntime 1.30's CoreML provider ran this int8 model at
47 ms a call against the CPU's 21 (8 s of audio, 100 calls after warm-up), took
0.8 s to build its session and gave probabilities up to 0.09 apart from the CPU's;
as an ML Program it failed to run at all (2026-10-20, an M3).

It wants exactly what pipecat's reference inference feeds it, so this follows
that code: the last 8 s of 16 kHz audio, zero-padded at the START when shorter,
normalised to zero mean and unit variance over the whole 8 s, then Whisper's
log-mel features (WhisperFeatureExtractor(chunk_length=8)), shape (1, 80, 800).
Done in numpy here rather than pulling in transformers for one function.
"""

import re
import os
from pathlib import Path

SAMPLE_RATE = 16000
SECONDS = 8
N_FFT = 400
HOP = 160
N_MELS = 80
MODEL_NAME = "smart-turn-v3.2-cpu.onnx"
MODEL_URL = ("https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/"
             "f766f81d3cfdf7737ac64aad813d91bbfd56bf93/" + MODEL_NAME)   # a fixed revision (install.sh)
MODEL_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
MODEL = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby")) / "models" / MODEL_NAME


def _hz_to_mel(f):
    """Slaney's mel scale: linear below 1 kHz, logarithmic above."""
    import numpy as np
    f = np.asarray(f, dtype="float64")
    mels = f / (200.0 / 3)
    log_t = f >= 1000.0
    mels[log_t] = 15.0 + np.log(f[log_t] / 1000.0) / (np.log(6.4) / 27.0)
    return mels


def _mel_to_hz(m):
    import numpy as np
    m = np.asarray(m, dtype="float64")
    freqs = m * (200.0 / 3)
    log_t = m >= 15.0
    freqs[log_t] = 1000.0 * np.exp((np.log(6.4) / 27.0) * (m[log_t] - 15.0))
    return freqs


_FILTERS = []


def mel_filters():
    """(80, 201) Slaney-normalised triangular filters, as librosa and Whisper make them."""
    import numpy as np
    if _FILTERS:
        return _FILTERS[0]
    fft_freqs = np.linspace(0, SAMPLE_RATE / 2.0, 1 + N_FFT // 2)
    mel_pts = _mel_to_hz(np.linspace(_hz_to_mel([0.0])[0], _hz_to_mel([SAMPLE_RATE / 2.0])[0], N_MELS + 2))
    fdiff = np.diff(mel_pts)
    ramps = mel_pts[:, None] - fft_freqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    weights *= (2.0 / (mel_pts[2:N_MELS + 2] - mel_pts[:N_MELS]))[:, None]
    _FILTERS.append(weights.astype("float32"))
    return _FILTERS[0]


def prepare(audio):
    """The last 8 s, zero-padded at the start, normalised: Smart Turn's input audio."""
    import numpy as np
    n = SECONDS * SAMPLE_RATE
    audio = np.asarray(audio, dtype="float32")
    audio = audio[-n:] if len(audio) >= n else np.pad(audio, (n - len(audio), 0))
    return (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)


def log_mel(audio):
    """Whisper's log-mel features of 8 s of audio: (80, 800)."""
    import numpy as np
    pad = N_FFT // 2
    x = np.pad(audio.astype("float64"), (pad, pad), mode="reflect")
    window = np.hanning(N_FFT + 1)[:-1]                 # periodic Hann, as torch and transformers
    frames = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    power = np.abs(np.fft.rfft(x[idx] * window, n=N_FFT)) ** 2       # (frames, 201)
    mel = mel_filters().astype("float64") @ power.T                  # (80, frames)
    spec = np.log10(np.maximum(mel, 1e-10))[:, :-1]
    spec = np.maximum(spec, spec.max() - 8.0)
    return ((spec + 4.0) / 4.0).astype("float32")


def trim_tail(audio, keep=0.2, frame=0.03):
    """Keep `keep` seconds after the last loud frame. The recorder closes an
    utterance after 0.6 s of quiet; Smart Turn was trained on audio that stops
    about 0.2 s after the speech, as pipecat feeds it after its VAD stops."""
    import numpy as np
    step = int(frame * SAMPLE_RATE)
    n = len(audio) // step
    if n < 2:
        return audio
    rms = np.sqrt(np.mean(audio[:n * step].reshape(n, step) ** 2, axis=1))
    loud = np.nonzero(rms > max(0.006, rms.max() * 0.1))[0]
    if not len(loud):
        return audio
    end = min(len(audio), (loud[-1] + 1) * step + int(keep * SAMPLE_RATE))
    return audio[:end]


class SmartTurn(object):
    """complete(audio) -> probability that the turn is finished, 0..1."""

    def __init__(self, path=MODEL):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(path), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def complete(self, audio):
        import numpy as np
        feats = log_mel(prepare(trim_tail(audio)))[None, :, :]
        out = float(np.asarray(self.session.run(None, {self.input: feats})[0]).reshape(-1)[0])
        if not 0.0 <= out <= 1.0:                  # a logit, should an export ever leave out the sigmoid
            out = 1.0 / (1.0 + np.exp(-out))
        return out


def load(path=MODEL, log=None):
    """The model, or None (no onnxruntime, no file): callers then judge by
    silence and punctuation alone."""
    if not Path(path).exists():
        if log:
            log("turn: no Smart Turn model at %s; judging the end of a turn by pauses" % path)
        return None
    try:
        return SmartTurn(path)
    except Exception as exc:                      # onnxruntime missing or the file is bad
        if log:
            log("turn: Smart Turn unavailable (%r); judging the end of a turn by pauses" % exc)
        return None


# A turn whose last words are one of these is mid-thought, whatever its punctuation:
# Standard puts a full stop at every pause, and a hesitant "…you know, uh." is not an
# end (the user, cut off mid-speech, 2026-10-12). Not "so" ("I think so.") nor "yeah".
HESITATIONS = ("uh", "um", "uhm", "er", "erm", "hmm", "and", "but", "or", "because", "like",
               "the", "a", "an", "to", "of", "with", "i mean", "you know")


def hesitating(text):
    """Do the words stop on a hesitation (HESITATIONS)? "you know?" is a real question."""
    t = text.rstrip().rstrip("\"')]}»”’").strip().lower()
    if t.endswith("?") and t.rstrip("?").endswith("you know"):
        return False
    words = re.sub(r"[^\w' ]+", " ", t).split()
    return bool(words) and (words[-1] in HESITATIONS or " ".join(words[-2:]) in HESITATIONS)


def punctuated(text):
    """Did the speech-to-text close the thought? Parakeet only ends a phrase
    with . ? ! when it sounds finished: "...about the budget?" against
    "...what we need to figure out is" (measured)."""
    return text.rstrip().rstrip("\"')]}»”’").endswith((".", "?", "!", "…"))


class Turns(object):
    """What is said in voice mode, gathered into turns, and when each is over.

    feed() gets each utterance as the recorder closes it, which it does after
    0.6 s of quiet; tick() runs every loop. Either returns the finished turn's
    text once the turn is over. How long to wait after an utterance:

      punctuated, and Smart Turn says finished   FLOOR more: then answer
      punctuated, Smart Turn unsure (or absent)  UNSURE more seconds
      no closing punctuation, or a hesitation     OPEN more seconds, up to OPEN_MAX the
                                                  surer Smart Turn is that it goes on

The wait is variable, as LiveKit's min/max endpointing delay and AssemblyAI's
confidence and silence do: short when the words and the model both say done,
longer when either says it goes on. Measured on the user's calls (10/23-24): of
13 turns ended on unpunctuated words, 3 went on within a second ("One issue is
that", p 0.09, cut after 2.5 s); none of 49 punctuated ones did.

    Never less than FLOOR: on the user's own talk, the turn ended at once on a pause
    Smart Turn scored 0.54 and 0.77, and they went on 0.0 and 0.2 s later (2026-10-12).
    Not even when Smart Turn is sure: 0.15 s at 0.85, 0.95 or 0.97 or more would have
    cut in on 10, 9 and 8 more pauses in 3 h of the user's room audio (51 today), and
    0.99 never fires (2026-10-20, tests/measure_cutoffs.py SureTurns).
    Speech starting again holds the turn open; its next utterance re-decides.
    """

    UNSURE = 1.0
    OPEN_MAX = 3.0          # unpunctuated, Smart Turn sure it goes on (p 0): mid-thought. Never less
                            # than OPEN however sure it is of an end: "...you know, uh." at 0.99 went on
    EAGER = 0.3             # likely(): a punctuated statement from here may be asked early (speculation);
                            # the turn still ends only by the wait above, and the ask is dropped if they go on
    QUESTION = 0.25         # ends with "?": parakeet heard a whole question (measured:
                            # Smart Turn gave real finished questions 0.19 and 0.41)
    NO_MODEL = 0.5          # punctuated, no Smart Turn: trust the punctuation, briefly
    OPEN = 2.0
    THRESHOLD = 0.5
    FLOOR = 0.4             # after the utterance closes (0.6 s of quiet), however sure

    def __init__(self, model=None):
        self.model = model
        self.last_p = None
        self.last_closed = None
        self.reset()

    def reset(self):
        self.texts, self.audio, self.due = [], [], None
        self.closed_at = None   # when the last utterance closed (speech ended just before)

    def resume(self, now):
        """Listening again after Claude had the floor, or after a barge-in: a turn the
        user started before keeps its words (said while the meeting agent worked, then
        Claude spoke before it closed: they were dropped, never asked, the user
        2026-10-12), and its wait starts from now, so a long answer doesn't close it
        before they can go on."""
        self.due = now + self._wait() if self.texts else None

    @property
    def heard(self):
        return " ".join(self.texts)

    def _wait(self):
        return max(self.FLOOR, self._wait_for())

    def _wait_for(self):
        text = self.heard
        p = self._p()
        if not punctuated(text) or hesitating(text):
            if p is None:
                return self.OPEN
            return self.OPEN + (self.OPEN_MAX - self.OPEN) * max(0.0, self.THRESHOLD - p) / self.THRESHOLD
        asked = text.rstrip().rstrip("\"')]}»”’").endswith("?")
        if self.model is None:
            return self.QUESTION if asked else self.NO_MODEL
        if p is None:
            return self.NO_MODEL
        if p >= self.THRESHOLD:
            return 0.0
        return self.QUESTION if asked else self.UNSURE

    def _p(self):
        """Smart Turn on the turn's audio: last_p, or None (no model, no audio, or it failed)."""
        self.last_p = None
        if self.model is None or not self.audio:
            return None
        import numpy as np
        try:
            self.last_p = self.model.complete(np.concatenate(self.audio))
        except Exception:
            self.last_p = None
        return self.last_p

    def heard_with(self, text):
        """The turn so far, with words not yet closed into an utterance."""
        return " ".join(self.texts + [t for t in [(text or "").strip()] if t])

    def likely(self, text, audio):
        """May the turn be over if `text` (words still in an open utterance,
        decoded at a short pause) were its last: worth asking early (eager)?
        Punctuated, and Smart Turn at EAGER or more (below the end's THRESHOLD:
        speculation is dropped if they go on, so it may be wrong), or a question,
        or punctuated with no model. last_why says why, for
        the log: "no punctuation", "hesitating", "question", "no model", "no
        audio", "model failed", or "p=0.42" (numbers only, never the words)."""
        whole = self.heard_with(text)
        if not punctuated(whole) or hesitating(whole):
            self.last_why = "hesitating" if punctuated(whole) else "no punctuation"
            return False
        if whole.rstrip().rstrip("\"')]}»”’").endswith("?") or self.model is None:
            self.last_why = "question" if self.model is not None else "no model"
            return True
        if audio is None:
            self.last_why = "no audio"
            return False
        import numpy as np
        try:
            p = self.model.complete(np.concatenate(self.audio + [audio]))
        except Exception:
            self.last_why = "model failed"
            return False
        self.last_why = "p=%.2f" % p
        return p >= self.EAGER

    def feed(self, text, audio, now):
        text = (text or "").strip()
        self.closed_at = now
        if text:
            self.texts.append(text)
            if audio is not None:
                self.audio.append(audio)
        if self.texts:
            self.due = now + self._wait()
        return self._finish(now)

    def tick(self, now, speaking):
        if speaking:
            self.due = None                 # still talking: the next utterance decides
            return None
        if self.texts and self.due is None:
            self.due = now + self._wait()   # speech that came to nothing (noise)
        return self._finish(now)

    def _finish(self, now):
        if self.texts and self.due is not None and now >= self.due:
            text = self.heard
            self.last_closed = self.closed_at
            self.reset()
            return text
        return None
