#!/usr/bin/env python3
"""Standby capture daemon. Runs on the Mac.

Captures one or two audio channels depending on the mode (see MODES below),
transcribes them on-device with parakeet-mlx, appends JSONL to a local session
file, and delivers new lines to the Claude Code agent, as plain files on this Mac.

Binds no ports and stores no credentials.

Audio never leaves this Mac. Text goes only to your own Claude Code agent.
Nothing is sent to any third party.
"""

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # transport.py

HOME = Path.home()
ROOT = Path(os.environ.get("STANDBY_ROOT", HOME / ".standby"))   # as everywhere else; tests set it
CONFIG = ROOT / "config.json"
SESSIONS = ROOT / "sessions"

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.5
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
HEARTBEAT = 10.0   # seconds between status lines in the log

# Capture modes. A call's own sound comes straight from macOS (native.appaudio) when
# one plays, and joins as "Others"; nothing here is meeting-platform specific.
#
#   auto     the mic, and whatever the Mac plays (a call, a video) while an app plays. The default.
#   mic      the mic only, labelled Room. Right for a meeting in the room.
#   meeting  the call's sound only, without the mic.
MODES = {
    "auto":    [("Room", "mic_device")],
    "mic":     [("Room", "mic_device")],
    "meeting": [],
}
MEETING_MODES = ("auto", "meeting")

_stop = threading.Event()


def log(msg):
    # the time on each line, to read with the app's own log (ui.log) on one clock
    sys.stderr.write("[standby] %s %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


def load_config():
    if not CONFIG.exists():
        raise SystemExit("no config at %s - run: standby setup" % CONFIG)
    with open(CONFIG) as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# ASR backend
# --------------------------------------------------------------------------

class UtteranceTranscriber:
    """Segment audio into utterances by silence, then decode each one ONCE.

    Why not commit from parakeet's streaming sentence list: measured on a real
    device, that list is a rolling re-decode of the current window. Sentence
    starts drift between drains (the same sentence arrived at 9.92s and 11.36s,
    so start-based dedup emitted it twice), and silence decodes to 0.08s
    punctuation-only scraps ('?'). Two rounds of patching that logic failed.

    Instead: a small energy VAD over 30 ms frames with an adaptive noise floor.
    An utterance opens on speech (with 300 ms of pre-roll so the first syllable
    is kept), closes after END_SILENCE of quiet or at MAX_UTT, and is decoded in
    a fresh context with the exact call the probe verified. So every line is
    decoded once, timestamps are monotonic by construction, and text with fewer
    than two letters/digits is dropped as noise.

    Must be driven from the main thread (MLX streams are thread-local).
    """

    FRAME = 0.03         # seconds per VAD frame
    PREROLL = 0.3        # seconds of audio kept before speech onset
    END_SILENCE = 0.6    # trailing quiet that closes an utterance
    MAX_UTT = 15.0       # force-close long monologues so answers stay fresh
    MIN_SPEECH = 0.25    # utterances with less voiced audio are discarded
    MIN_RMS = 0.006      # absolute floor for "speech"
    NOISE_MULT = 3.0     # speech must be this far above the noise floor

    def __init__(self, model, rotate_seconds=None, decode=None):
        import numpy as np
        self.np = np
        self.model = model
        self.decode = decode or self._decode_parakeet
        self.frame = int(SAMPLE_RATE * self.FRAME)
        self.pos = 0                 # samples consumed so far
        self.carry = np.zeros(0, dtype="float32")
        self.pre = []                # recent quiet frames (pre-roll)
        self.utt = None              # frames of the open utterance, or None
        self.utt_start = 0
        self.silence = 0.0
        self.voiced = 0.0
        self.noise = 0.003
        self.ready = []
        self.decoded = 0
        self.dropped = 0
        self._partial, self._partial_at = "", 0.0
        self.partial_every, self.partial_min = 1.5, 0.6    # voice mode: faster
        self.end_silence = self.END_SILENCE                 # voice mode: shorter
        # Voice mode: the open utterance decoded once at this much quiet, before
        # it closes, so Claude can start on likely-final words (take_early); its text
        # is the utterance's own when it closes with no speech since. None: off.
        self.early_after = None
        self._early = None           # this pause's early text ("" for noise), None before it
        self.early = None            # (start, end, text, audio), until taken or the utterance closes
        self.early_lost = 0          # early words the utterance closed over before they were taken
        # each closed utterance's words with their times, so a line can be split where
        # the speaker changes: start (s) -> [(t0, t1, piece)], seconds into the utterance.
        self.words = {}
        self._words = None           # the last decode's: (samples decoded, its pieces)

    # -- decoding --------------------------------------------------------
    def _decode_parakeet(self, audio):
        from native import speech
        text, words = speech.stream_decode(self.model, audio)
        self._words = (len(audio), words) if words is not None else None
        return text

    # -- VAD -------------------------------------------------------------
    def feed(self, samples):
        np = self.np
        buf = np.concatenate([self.carry, samples.astype("float32")])
        n = len(buf) // self.frame
        for i in range(n):
            fr = buf[i * self.frame:(i + 1) * self.frame]
            rms = float(np.sqrt(np.mean(fr * fr)))
            speech = rms > max(self.MIN_RMS, self.noise * self.NOISE_MULT)
            if self.utt is None:
                if speech:
                    self.utt = list(self.pre) + [fr]
                    self.utt_start = self.pos - sum(len(x) for x in self.pre)
                    self.pre = []
                    self.silence, self.voiced = 0.0, self.FRAME
                else:
                    self.noise = 0.95 * self.noise + 0.05 * rms
                    self.pre.append(fr)
                    keep = int(self.PREROLL / self.FRAME)
                    if len(self.pre) > keep:
                        self.pre = self.pre[-keep:]
            else:
                self.utt.append(fr)
                if speech:
                    self.silence = 0.0
                    self.voiced += self.FRAME
                    self._early = self.early = None     # they go on: what came early is not their words
                else:
                    self.silence += self.FRAME
                length = len(self.utt) * self.FRAME
                if self.silence >= self.end_silence or length >= self.MAX_UTT:
                    self._close_utt(self.pos + len(fr))
                elif (self.early_after is not None and self._early is None and not speech
                      and self.silence >= self.early_after and self.voiced >= self.MIN_SPEECH):
                    self._early_decode(self.pos + len(fr))
                    if self.early is not None:
                        # Stop here, the rest of the block kept: the app takes the early words
                        # before the pause that follows closes the utterance. Audio comes in
                        # 0.5 s blocks, and the close is only 0.25 s after this point, so it
                        # was nearly always in the same block, and the words were gone.
                        self.pos += len(fr)
                        self.carry = buf[(i + 1) * self.frame:]
                        return
            self.pos += len(fr)
        self.carry = buf[n * self.frame:]

    @property
    def held(self):
        """Audio kept back after an early decode, for the next feed: one is due at once."""
        return len(self.carry) >= self.frame

    def partial(self, every=None, min_voiced=None):
        """Text of the utterance still being spoken, re-decoded at most every
        `every` seconds, so the app can show words before the pause."""
        every = self.partial_every if every is None else every
        min_voiced = self.partial_min if min_voiced is None else min_voiced
        if self.utt is None or self.voiced < min_voiced:
            return ""
        if time.time() - self._partial_at >= every:
            self._partial_at = time.time()
            try:
                self._partial = self.decode(self.np.concatenate(self.utt))
            except Exception as exc:
                log("partial decode failed: %r" % exc)
        return self._partial

    def _early_decode(self, end_pos):
        """The open utterance's words so far, decoded once in this pause."""
        np = self.np
        audio = np.concatenate(self.utt)
        t0 = time.time()
        try:
            text = self.decode(audio)
        except Exception as exc:
            log("early decode failed: %r" % exc)
            text = ""
        self._early = text if sum(ch.isalnum() for ch in text) >= 2 else ""
        if self._early:
            self.early = (self.utt_start / float(SAMPLE_RATE), end_pos / float(SAMPLE_RATE), text, audio)
            log("utterance %.1f-%.1fs decoded early, at %.2fs of quiet, in %.2fs"
                % (self.utt_start / float(SAMPLE_RATE), end_pos / float(SAMPLE_RATE), self.silence, time.time() - t0))
            trace(TRACE["sid"], "decode", a=round(self.utt_start / float(SAMPLE_RATE), 2),
                  b=round(end_pos / float(SAMPLE_RATE), 2), ms=int((time.time() - t0) * 1000), early=True,
                  quiet_ms=int(self.silence * 1000), text=text)

    def take_early(self):
        """The open utterance's early words, once: (start, end, text, audio), or None."""
        early, self.early = self.early, None
        return early

    def _close_utt(self, end_pos):
        np = self.np
        frames, start, voiced = self.utt, self.utt_start, self.voiced
        early = self._early
        if self.early is not None:
            self.early_lost += 1             # closed before anyone took its early words
        self.utt, self.silence, self.voiced = None, 0.0, 0.0
        self._partial, self._partial_at = "", 0.0
        self._early = self.early = None
        if voiced < self.MIN_SPEECH:
            self.dropped += 1
            return
        audio = np.concatenate(frames)
        t0 = time.time()
        # Nothing said since the early decode: its words are the utterance's (one decode, not two).
        text = early if early else self.decode(audio)
        took = time.time() - t0
        trace(TRACE["sid"], "decode", a=round(start / float(SAMPLE_RATE), 2), b=round(end_pos / float(SAMPLE_RATE), 2),
              ms=0 if early else int(took * 1000), early=False, reused_early=bool(early),
              voiced_s=round(voiced, 2), noise=sum(ch.isalnum() for ch in text) < 2, text=text)
        if sum(ch.isalnum() for ch in text) < 2:
            self.dropped += 1
            log("utterance %.1f-%.1fs dropped as noise (decode %.2fs)"
                % (start / float(SAMPLE_RATE), end_pos / float(SAMPLE_RATE), took))
            return
        self.decoded += 1
        log("utterance %.1f-%.1fs (%.1fs voiced) decoded in %.2fs"
            % (start / float(SAMPLE_RATE), end_pos / float(SAMPLE_RATE), voiced, took))
        if self._words is not None and self._words[0] == len(audio):    # this audio's own decode
            self.words[start / float(SAMPLE_RATE)] = self._words[1]
            while len(self.words) > 16:
                self.words.pop(next(iter(self.words)))
        self.ready.append((start / float(SAMPLE_RATE), end_pos / float(SAMPLE_RATE), text, audio))

    def drain(self, force=False):
        if force and self.utt is not None:
            self._close_utt(self.pos)
        out, self.ready = self.ready, []
        return out

    def rotate(self):
        return []          # each utterance already uses a fresh context

    def _close(self):
        pass


MAX_UTT = UtteranceTranscriber.MAX_UTT    # the longest an utterance runs with no quiet to close it

import devices  # noqa: E402  (the Mac's mics, and which input to open: dictation's too)
from calltrace import trace  # noqa: E402  (a call's trace.jsonl: sbtrace)
TRACE = {"sid": None}     # the call this recorder traces (a talk), else None: nothing written

MIC_ROLES = devices.MIC_ROLES
VIRTUAL_INPUTS = devices.VIRTUAL_INPUTS
list_inputs = devices.list_inputs
_is_virtual = devices.is_virtual


def resolve_device(wanted, role):
    """devices.resolve_device, logged here."""
    return devices.resolve_device(wanted, role, log=log)


import speechmodel  # noqa: E402  (the speech model: which, and loading it; dictation's too)

SMALL_MODEL = speechmodel.SMALL_MODEL
SPEECH_CACHE = speechmodel.SPEECH_CACHE
speech_name = speechmodel.speech_name


def load_model(name):
    """The speech model, through speechmodel.load_model, logged here."""
    return speechmodel.load_model(name, log=log, limit=SPEECH_CACHE)


def serve_decodes(folder, model, decode=None):
    """Standby Dictation's recordings, decoded with this recorder's model while a
    session runs (until one app holds the model for both): so a meeting
    with dictation holds one copy, not two. See dictation.RecorderDecode. Each
    `<n>.f32` (16 kHz float32) is taken by deleting it, then answered with
    `<n>.json`: {"text"} or {"error"}. Dictation deleting it first means it gave up
    waiting and decodes it itself. On the main thread, as all MLX work here. Returns
    how many it answered."""
    import numpy as np
    if decode is None:
        import dictation
        decode = dictation.transcribe_audio
    try:
        names = sorted(n for n in os.listdir(str(folder)) if n.endswith(".f32"))
    except OSError:
        return 0
    answered = 0
    for name in names:
        path = os.path.join(str(folder), name)
        try:
            audio = np.fromfile(path, dtype="<f4")
            os.unlink(path)                    # taken: from here dictation waits for the answer
        except OSError:
            continue                           # dictation took it back
        t0 = time.time()
        try:
            reply = {"text": decode(model, audio)}
            log("dictation: decoded its %.1fs of audio in %.2fs" % (len(audio) / float(SAMPLE_RATE), time.time() - t0))
        except Exception as exc:
            reply = {"error": str(exc)[:200]}
            log("dictation: decode failed: %r" % exc)
        part = path[:-4] + ".json.part"
        try:
            with open(part, "w") as fh:
                json.dump(reply, fh)
            os.replace(part, path[:-4] + ".json")
            answered += 1
        except OSError as exc:
            log("dictation: could not answer: %r" % exc)
    return answered


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

SPEAKER_MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                     "speaker-recongition-models/nemo_en_titanet_small.onnx")
SPEAKER_MODEL_SHA256 = "ad4a1802485d8b34c722d2a9d04249662f2ece5d28a7a039063ca22f515a789e"   # checked by install.sh
SPEAKER_MODEL = ROOT / "models" / "nemo_en_titanet_small.onnx"


class OnlineDiarizer(object):
    """Assign each utterance a speaker id by voice fingerprint (online clustering).

    Tuned on labeled multi-speaker speech with TitaNet-small embeddings
    (tools/eval_diarization.py): 100% of 2-8 s utterances and 86-91% of 1.5 s
    ones grouped correctly, erring toward splitting one person into two
    labels rather than merging two people, which a rename can fix.

      * join the most similar speaker if cosine >= JOIN;
      * a short utterance (< MIN_NEW s) only starts a new speaker when it is
        clearly unlike everyone (best cosine < SHORT_NEW); short fingerprints
        are noisy;
      * two speakers whose centroids reach MERGE are merged into the lower id;
      * except named ones: two different names never merge, and a named
        speaker survives a merge, so a name stays on its voice. Measured on AMI
        meetings: a name on other people's speech
        12.9% -> 8.1%, names lost for good after a mic change 21% -> 8%.
    """
    JOIN, MERGE, MIN_NEW, SHORT_NEW = 0.40, 0.55, 2.0, 0.25

    def __init__(self):
        self.cent, self.count, self.alias = {}, {}, {}
        self.next_id = 1
        # sid -> (rank, name): 2 the user's own, 1 recognized from memory. Not saved:
        # SpeakerID sets it from the session's names files.
        self.named = {}

    def resolve(self, sid):
        while sid in self.alias:
            sid = self.alias[sid]
        return sid

    def assign(self, emb, seconds):
        import numpy as np
        e = np.asarray(emb, dtype="float64")
        e = e / (np.linalg.norm(e) + 1e-9)
        best, best_sim = None, -1.0
        for sid, c in self.cent.items():
            sim = float(np.dot(e, c / (np.linalg.norm(c) + 1e-9)))
            if sim > best_sim:
                best, best_sim = sid, sim
        if best is not None and best_sim >= self.JOIN:
            sid = best
        elif best is not None and seconds < self.MIN_NEW and best_sim >= self.SHORT_NEW:
            sid = best
        else:
            sid = self.next_id
            self.next_id += 1
            self.cent[sid] = np.zeros_like(e)
            self.count[sid] = 0
        self.cent[sid] = self.cent[sid] + e
        self.count[sid] += 1
        merged = self._merge()
        return self.resolve(sid), merged

    def _merge(self):
        import numpy as np
        merged = False
        ids = sorted(self.cent)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if a in self.cent and b in self.cent:
                    na, nb = self.named.get(a), self.named.get(b)
                    if na and nb and na[1] != nb[1]:
                        continue                     # two people the user can tell apart
                    ca = self.cent[a] / np.linalg.norm(self.cent[a])
                    cb = self.cent[b] / np.linalg.norm(self.cent[b])
                    if float(ca @ cb) >= self.MERGE:
                        # the page shows the name of the id a line resolves to: keep the named one
                        keep, drop = (b, a) if (nb or (0,))[0] > (na or (0,))[0] else (a, b)
                        self.cent[keep] = self.cent[keep] + self.cent.pop(drop)
                        self.count[keep] += self.count.pop(drop)
                        self.alias[drop] = keep
                        if drop in self.named and keep not in self.named:
                            self.named[keep] = self.named[drop]
                        merged = True
        return merged

    def join(self, a, b):
        """The user said a and b are one person: one speaker from now on, the lower id."""
        keep, drop = sorted((self.resolve(a), self.resolve(b)))
        if keep != drop and keep in self.cent and drop in self.cent:
            self.cent[keep] = self.cent[keep] + self.cent.pop(drop)
            self.count[keep] += self.count.pop(drop)
            self.alias[drop] = keep
        return keep

    def to_json(self):
        return {"next_id": self.next_id,
                "alias": {str(k): v for k, v in self.alias.items()},
                "cent": {str(k): [float(x) for x in v] for k, v in self.cent.items()},
                "count": {str(k): v for k, v in self.count.items()}}

    @classmethod
    def from_json(cls, d):
        import numpy as np
        o = cls()
        o.next_id = int(d.get("next_id", 1))
        o.alias = {int(k): int(v) for k, v in d.get("alias", {}).items()}
        o.cent = {int(k): np.asarray(v, dtype="float64") for k, v in d.get("cent", {}).items()}
        o.count = {int(k): int(v) for k, v in d.get("count", {}).items()}
        return o


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def load_voices(lib=None):
    """Voices the user named in earlier sessions: {name: unit vector}. Written
    by the app (ui.py) when a speaker is named; stays on this Mac. lib: voices.json, read."""
    import numpy as np
    out = {}
    for name, v in (_read_json(ROOT / "voices.json") if lib is None else lib).get("voices", {}).items():
        e = np.asarray(v.get("emb") or [], dtype="float64")
        if e.size:
            out[name] = e / (np.linalg.norm(e) + 1e-9)
    return out


def my_print(lib=None):
    """The user's own voice print (myvoice's "me" in voices.json), as a unit vector, once
    it has heard enough of them to be relied on (myvoice.TRUST); else None."""
    import myvoice
    me = (_read_json(ROOT / "voices.json") if lib is None else lib).get("me") or {}
    if not me.get("emb") or float(me.get("seconds") or 0.0) < myvoice.TRUST:
        return None
    return myvoice._unit(me["emb"])


def my_name(lib=None):
    """The name the user's own voice is known by, so their lines carry one label all
    meeting: the voice they named (voices.json) that their own print matches, clearly
    (myvoice.YOU, and SpeakerID.MARGIN ahead of the next); else None, and they are "You".
    Measured: the user's print against the voice they named themselves, 0.53."""
    import myvoice
    lib = _read_json(ROOT / "voices.json") if lib is None else lib
    me = my_print(lib)
    if me is None:
        return None
    sims = sorted(((float(me @ v), name) for name, v in load_voices(lib).items()), reverse=True)
    if not sims or sims[0][0] < myvoice.YOU or (len(sims) > 1 and sims[0][0] - sims[1][0] < SpeakerID.MARGIN):
        return None
    return sims[0][1]


_EXTRACTOR = []          # TitaNet through sherpa-onnx, made once: SpeakerID's and the user's own voice's


def speaker_extractor():
    """The voice-print model, or None where it is not installed."""
    if not _EXTRACTOR:
        ex = None
        if SPEAKER_MODEL.exists():
            try:
                import sherpa_onnx
                ex = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(SPEAKER_MODEL), num_threads=2))
            except Exception as exc:
                log("speakers: could not load sherpa-onnx (%r)" % exc)
        _EXTRACTOR.append(ex)
    return _EXTRACTOR[0]


def embed(audio, extractor=None):
    """An utterance's voice print, or None (no model, or under half a second)."""
    ex = extractor if extractor is not None else speaker_extractor()
    if ex is None or audio is None or len(audio) < SAMPLE_RATE // 2:
        return None
    try:
        stream = ex.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
        stream.input_finished()
        return ex.compute(stream)
    except Exception as exc:
        log("speakers: fingerprint failed (%r)" % exc)
        return None


# What the voice said lately, (when, sentence), as played here: in a talk, an utterance that
# repeats it and doesn't sound like the user is the voice heard back, never a turn.
VOICE_SAID = deque(maxlen=12)
VOICE_ECHO_SECS = 20.0


def _words(text):
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


# Sounds an echo's onset is often heard as, in place of the voice's first word.
_ECHO_FILLERS = {"um", "uh", "er", "erm", "ah", "hm", "hmm", "mm"}


def echoes_voice(text, now=None, said=None):
    """Are `text`'s words what the voice said in the last VOICE_ECHO_SECS (in order,
    within one sentence)? Heard back, an echo's words come out a little off: three or
    more of them still are, with fillers among them and, from five on, one word misheard
    ("Hi, good to hear you." heard as "Uh good to hear you.", talk-20261026-195509)."""
    import difflib
    now = time.time() if now is None else now
    heard = _words(text)
    if not heard:
        return False
    for when, sentence in list(VOICE_SAID if said is None else said):
        if now - when > VOICE_ECHO_SECS:
            continue
        w = _words(sentence)
        if any(w[i:i + len(heard)] == heard for i in range(len(w) - len(heard) + 1)):
            return True
        if len(heard) < 3:
            continue
        blocks = [b for b in difflib.SequenceMatcher(None, heard, w, autojunk=False).get_matching_blocks() if b.size]
        if not blocks:
            continue
        found = {b.a + k for b in blocks for k in range(b.size)}
        off = [x for i, x in enumerate(heard) if i not in found and x not in _ECHO_FILLERS]
        stretch = blocks[-1].b + blocks[-1].size - blocks[0].b      # one stretch of the sentence, not words from all over it
        if len(off) <= (1 if len(heard) >= 5 else 0) and len(found) >= 3 and stretch <= len(heard):
            return True
    return False


# In a call where other voices are ignored, an utterance still being spoken is told apart
# once it has this much speech (its print then takes about 6 ms on an M-series Mac): talking
# over the voice stops it only then, against 0.3 s of sound before, and its words show.
OPEN_ID_SECS = 0.5


class SpeakerID(object):
    """Voice fingerprints (sherpa-onnx + TitaNet-small) feeding OnlineDiarizer.

    Optional: if sherpa-onnx or the model is missing, identify() returns None
    and lines simply carry no speaker label.
    """

    # Naming a voice from an earlier session. Measured on labeled speech (see
    # tools/eval_voice_memory.py): the same person scored 0.73-0.85 median
    # against their saved voice, different people never above 0.40.
    KNOWN, MARGIN, KNOWN_AFTER = 0.55, 0.10, 2

    def __init__(self, state_path, on_change=None, on_known=None, remember=True):
        self.state_path = state_path
        self.on_change = on_change
        self.on_known = on_known
        self.extractor = None
        self.prints = {}             # id(audio) -> (audio, its print): the last few, for the user's own voice
        self.diar = OnlineDiarizer()
        self.known_path = state_path.with_name("speakers.known.json")
        self.names_path = state_path.with_name("speakers.names.json")
        known = _read_json(self.known_path).get("known", {})
        self.known = {int(k): v for k, v in known.items() if str(k).isdigit()}
        # the user's own: "me" (the name their voice is known by, run_session's); their print,
        # so a voice heard that is theirs is "You" when they have no name
        self.me_name = known.get("me")
        self.me_print = my_print() if remember and not self.me_name else None
        self.remember = remember
        self.names, self.names_seen = {}, None       # the user's names, as the app last wrote them
        self.voices = load_voices() if remember else {}
        if self.voices:
            log("speakers: %d remembered voice(s)" % len(self.voices))
        try:
            if state_path.exists():
                self.diar = OnlineDiarizer.from_json(json.loads(state_path.read_text()))
                log("speakers: resumed %d known voices" % len(self.diar.cent))
        except (OSError, ValueError) as exc:
            log("speakers: could not load saved voices (%r), starting fresh" % exc)
        if not SPEAKER_MODEL.exists():
            log("speakers: model not installed (%s), lines will carry no speaker "
                "label. Run the installer to add it." % SPEAKER_MODEL)
            return
        self.extractor = speaker_extractor()
        if self.extractor is not None:
            log("speakers: voice matching ON (%s)" % SPEAKER_MODEL.name)
        else:
            log("speakers: disabled, could not load sherpa-onnx")

    def embedding(self, audio):
        """An utterance's print, made once whoever asks (identify, or the user's own voice)."""
        if audio is None:
            return None
        kept = self.prints.get(id(audio))
        if kept is not None and kept[0] is audio:
            return kept[1]
        emb = embed(audio, self.extractor) if self.extractor is not None else None
        self.prints[id(audio)] = (audio, emb)
        while len(self.prints) > 8:
            self.prints.pop(next(iter(self.prints)))
        return emb

    def identify(self, audio):
        if self.extractor is None or audio is None or len(audio) < SAMPLE_RATE // 2:
            return None
        emb = self.embedding(audio)
        if emb is None:
            return None
        joined = self.read_names()
        before = len(self.diar.cent)
        sid, merged = self.diar.assign(emb, len(audio) / float(SAMPLE_RATE))
        merged = merged or joined
        if merged or len(self.diar.cent) != before:
            self.save()
            if merged and self.on_change:
                self.on_change(self.diar.alias)
        if merged:   # a recognized name follows its voice into the merge
            for k in list(self.known):
                r = self.diar.resolve(k)
                if r != k and r not in self.known:
                    self.known[r] = self.known[k]
        self._recognize(sid)
        self._mark_named()
        return sid

    def read_names(self):
        """The user's renames reach the matcher as they happen, not next session:
        their speakers are protected in merges, and the voices the app just
        remembered (voices.json) can name a later number in this session. Two
        numbers the user gave one name are one person: they are joined. True if so."""
        try:
            seen = self.names_path.stat().st_mtime_ns
        except OSError:
            seen = None
        if seen == self.names_seen:
            return False
        self.names_seen = seen
        names, joined = {}, False
        for k, v in sorted(_read_json(self.names_path).get("names", {}).items()):
            if not (str(k).isdigit() and v):
                continue
            k = self.diar.resolve(int(k))
            same = [s for s, n in names.items() if n == v and s != k and s in self.diar.cent]
            if same and k in self.diar.cent:
                k = self.diar.join(same[0], k)
                joined = True
                log("speakers: two speakers named alike, joined as Speaker %d" % k)
                names = {self.diar.resolve(s): n for s, n in names.items()}
            names[k] = v
        if names != self.names:
            if self.remember and set(names.values()) - set(self.names.values()):
                self.voices = load_voices()
            log("speakers: %d named in this session" % len(names))
            self.names = names
        self._mark_named()
        return joined

    def _mark_named(self):
        named = {k: (1, v) for k, v in self.known.items()}
        named.update({k: (2, v) for k, v in self.names.items()})
        self.diar.named = {self.diar.resolve(k): v for k, v in sorted(named.items(), key=lambda kv: kv[1][0])}

    def _recognize(self, sid):
        """Name a voice heard in an earlier session, once it has said enough."""
        import numpy as np
        voices = dict(self.voices)
        if self.me_print is not None and "You" not in voices:
            voices["You"] = self.me_print          # the user, by no name: as the Me channel is
        if not voices or sid in self.known or self.diar.count.get(sid, 0) < self.KNOWN_AFTER:
            return
        if sid in self.names or str(sid) in _read_json(self.names_path).get("names", {}):
            return                       # the user already named it
        c = self.diar.cent[sid] / (np.linalg.norm(self.diar.cent[sid]) + 1e-9)
        sims = sorted(((float(c @ v), name) for name, v in voices.items()), reverse=True)
        best, name = sims[0]
        second = sims[1][0] if len(sims) > 1 else -1.0
        if best >= self.KNOWN and best - second >= self.MARGIN:
            self.known[sid] = name
            log("speakers: Speaker %d matched a remembered voice (%.2f, next %.2f)" % (sid, best, second))
            known = {str(k): v for k, v in self.known.items()}
            if self.me_name:
                known["me"] = self.me_name
            data = json.dumps({"known": known})
            self.known_path.write_text(data)
            if self.on_known:
                self.on_known(data)

    def save(self):
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.diar.to_json()))
            tmp.replace(self.state_path)
        except OSError:
            pass

    # -- numbers for the diarization model's voices (Voices) ----------------
    def new_number(self):
        """A speaker number no one has, from the same count as the matcher's."""
        self.read_names()
        n = self.diar.next_id
        self.diar.next_id += 1
        return n

    def learn(self, number, audio):
        """A line of this number's voice: its print joins the number's, which names it
        from the voices remembered (as identify does) and, after a resume or before the
        model was ready, is what an earlier number of the same voice is found by."""
        import numpy as np
        emb = self.embedding(audio) if audio is not None and len(audio) >= SAMPLE_RATE else None
        if emb is None:
            return
        self.read_names()
        e = np.asarray(emb, dtype="float64")
        e = e / (np.linalg.norm(e) + 1e-9)
        number = self.diar.resolve(number)
        new = number not in self.diar.cent
        self.diar.cent[number] = e if new else self.diar.cent[number] + e
        self.diar.count[number] = self.diar.count.get(number, 0) + 1
        if new or self.diar.count[number] % 5 == 0:
            self.save()
        self._recognize(number)
        self._mark_named()

    def same_as(self, number, live):
        """The earlier number this voice had in the session, or None: a match by voice
        print (as two of the matcher's speakers merge: MERGE, with MARGIN over the next)
        among the numbers no live voice holds, a name the user gave never crossed."""
        import numpy as np
        number = self.diar.resolve(number)
        if self.diar.count.get(number, 0) < self.KNOWN_AFTER:
            return None
        c = self.diar.cent[number] / (np.linalg.norm(self.diar.cent[number]) + 1e-9)
        mine = self.diar.named.get(number)
        sims = sorted(((float(c @ (v / (np.linalg.norm(v) + 1e-9))), k) for k, v in self.diar.cent.items()
                       if k != number and k not in live), reverse=True)
        if not sims:
            return None
        best, k = sims[0]
        second = sims[1][0] if len(sims) > 1 else -1.0
        theirs = self.diar.named.get(k)
        if best < OnlineDiarizer.MERGE or best - second < self.MARGIN or (mine and theirs and mine[1] != theirs[1]):
            return None
        return k

    def alias_to(self, number, target):
        """This number is that earlier one: one speaker, the earlier number, as a merge."""
        number, target = self.diar.resolve(number), self.diar.resolve(target)
        if number == target or number not in self.diar.cent or target not in self.diar.cent:
            return target
        self.diar.cent[target] = self.diar.cent[target] + self.diar.cent.pop(number)
        self.diar.count[target] += self.diar.count.pop(number)
        self.diar.alias[number] = target
        if number in self.known and target not in self.known:
            self.known[target] = self.known[number]
        self.save()
        if self.on_change:
            self.on_change(self.diar.alias)
        log("speakers: Speaker %d is Speaker %d's voice again" % (number, target))
        return target


class Voices(object):
    """A channel's speakers from the diarization model (native.diarizer). Its voices
    (slots 0-7) are the session's speaker numbers: a new voice gets a new number, and
    once it has said a few lines, the number it had earlier in the session if it had
    one (heard before the model was ready, or before a resume), by voice print."""

    HOLD = 0.45          # seconds a line may wait for its label (measured: never more than 0.45)
    CHECK = 6            # lines of a voice compared with earlier numbers

    def __init__(self, stream, speakers, s0):
        self.stream, self.sp = stream, speakers
        self.s0 = s0 / float(SAMPLE_RATE)       # where the stream starts on the channel's clock
        self.num, self.seen = {}, {}

    def number(self, slot, audio):
        n = self.num.get(slot)
        if n is None:
            n = self.num[slot] = self.sp.new_number()
        n = self.sp.diar.resolve(n)
        self.sp.learn(n, audio)
        self.seen[slot] = self.seen.get(slot, 0) + 1
        if self.seen[slot] <= self.CHECK:
            live = {self.sp.diar.resolve(v) for s, v in self.num.items() if s != slot}
            earlier = self.sp.same_as(n, live)
            if earlier is not None:
                n = self.sp.alias_to(n, earlier)
        self.num[slot] = n
        return n


def split_words(runs, words, at, text):
    """A line's words shared out by who spoke when: [(t0, t1, text, slot)] on the
    runs' clock. A word goes to the voice that holds its middle (words: parakeet's
    pieces, seconds into the line, which starts at `at`); a part left with no words
    joins its neighbour. One part back: the line is not split."""
    parts = [[r0, r1, [], slot] for r0, r1, slot in runs]
    for w0, w1, piece in words:
        mid = at + (w0 + w1) / 2.0
        k = next((i for i, p in enumerate(parts) if mid < p[1]), len(parts) - 1)
        parts[k][2].append(piece)
    said = lambda t: sum(ch.isalnum() for ch in t) >= 2
    out = []
    for p0, p1, pieces, slot in parts:
        t = "".join(pieces).strip()
        if out and not said(t):
            out[-1][1], out[-1][2] = p1, (out[-1][2] + " " + t).strip()
        else:
            out.append([p0, p1, t, slot])
    if len(out) > 1 and not said(out[0][2]):
        out[1][0], out[1][2] = out[0][0], (out[0][2] + " " + out[1][2]).strip()
        out.pop(0)
    if len(out) < 2:
        return [(runs[0][0], runs[-1][1], text, None)]
    return [tuple(p) for p in out]


# The mic follows the Mac: Core Audio says what is connected and which mic
# is the system's default (devices.py); the recorder decides and reopens. Tests
# put a fake here; else the real one, and never in a test (STANDBY_NO_DEVICES).
DEVICES = None
STALL = 2.0               # an open stream with no audio for this long is opened again
BACKOFF = (0.5, 30.0)     # a mic that will not open: tried again, waiting twice as long each time
PERMISSION_POLL = 10.0    # the meeting's audio not allowed: looked at again this often, asking macOS nothing
MAX_FILL = 120.0          # a longer gap (the Mac asleep) is skipped over, not filled with silence
CHOICES = ROOT / "capture-choices.json"     # answers kept per device and app (what the app asks)
# a mic macOS refuses still opens, and sends exact zeros (a real mic in a silent
# room never does). This long of nothing but zeros from the device: not allowed.
MIC_DENIED_AFTER = 3.0
MIC_AUTH_EVERY = 5.0      # s between asks of macOS what it says about the mic


_MIC_AUTH = {"at": 0.0, "status": None}


def mic_authorization():
    """What macOS says about this process and the mic: "authorized", "denied",
    "restricted", "undetermined", or None when it can't be asked (no AVFoundation, not a
    Mac). Asked at most every MIC_AUTH_EVERY s. In a test (no devices), "authorized": the
    fakes' streams send exact zeros as a working mic; tests of a refusal put a fake here."""
    if os.environ.get("STANDBY_NO_DEVICES"):
        return "authorized"                    # a test: its fake streams stand in for a working mic
    now = time.time()
    if now - _MIC_AUTH["at"] < MIC_AUTH_EVERY:
        return _MIC_AUTH["status"]
    _MIC_AUTH["at"], status = now, None
    try:
        from native import permissions
        status = permissions.mic_status()
    except ImportError:                        # an OS Standby can't ask yet
        status = None
    _MIC_AUTH["status"] = status
    return status


def mic_devices():
    if DEVICES is not None:
        return DEVICES
    if os.environ.get("STANDBY_NO_DEVICES") or sys.platform != "darwin":
        return None
    import devices
    return devices.real_devices()


def pa_refresh():
    """PortAudio's device list again. It is learned once, when PortAudio
    starts, so a mic connected later is not in it. Every PortAudio stream must
    be closed first: they all go with it."""
    import sounddevice as sd
    sd._terminate()
    sd._initialize()


def pa_index(name):
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i
    return None


# The meeting's audio: native.appaudio, in this process
# (it inherits the app's permission). Tests put a fake maker here.
MEETING_AUDIO = None

# who's speaking, every 10 ms (native.diarizer). Tests put a fake maker here; else the
# real model where it can run, and never in a test (STANDBY_NO_DEVICES).
DIARIZER = None


def load_diarizer():
    if DIARIZER is not None:
        return DIARIZER()
    if os.environ.get("STANDBY_NO_DEVICES") or sys.platform != "darwin":
        return None
    from native import diarizer
    return diarizer.load(log=log)


def describe_capture(cause, app=""):
    """The words for a cause the recorder found itself (a stall), from
    native.appaudio's table, so there is one source of wording. {} without it."""
    try:
        from native import appaudio
        return appaudio.describe(cause, app) or {}
    except Exception:
        return {}


def meeting_audio():
    """The meeting audio capture: apps(), start(target, feed), stop(), ok,
    error, name; feed(block, t), mono float32 at 16 kHz, t the monotonic time
    of its first sample. None where there is none (yet), or in a test."""
    if MEETING_AUDIO is not None:
        return MEETING_AUDIO()
    if os.environ.get("STANDBY_NO_DEVICES") or sys.platform != "darwin":
        return None
    try:
        from native import appaudio
        return appaudio.capture(log=log)          # its lines in the session's log, with everything else
    except Exception as exc:
        log("meeting audio: not available (%r): the mic only" % exc)
        return None


class MeetingFollow(object):
    """Which apps' audio is the meeting's, as calls and videos start and end
    (choose_meetings: each app on its own; choose_meeting, one, for the checks).
    ask: an app to ask the user about, or None (the app shows it). seen: what
    apps() said at the last look."""

    SUSTAIN = 2.0         # playing this long before it joins: a notification ping is not a call

    def __init__(self, capture, mode, choices=None):
        self.capture, self.mode = capture, mode
        self.choices_path = choices or CHOICES
        self.ask, self._told, self.seen = None, set(), []
        self._since = {}      # when each app was first seen playing, without a break
        self._played = {}     # when each app was last seen playing

    def sustained(self, apps, current, now=None, recorded=()):
        """The apps, with those playing for under SUSTAIN s marked not playing yet
        (a Slack or Teams ping would otherwise start a capture, and make the mic
        the user's own for the rest of the meeting). The ones recorded from stay
        (current: an app, or None; recorded: app_keys)."""
        import devices as dv
        now = now or time.time()
        keep = set(recorded) | ({dv.app_key(current)} if current is not None else set())
        keys = set()
        out = []
        for a in apps:
            key = dv.app_key(a)
            keys.add(key)
            if not a.get("playing"):
                self._since.pop(key, None)
                out.append(a)
                continue
            since = self._since.setdefault(key, now)
            out.append(a if key in keep or now - since >= self.SUSTAIN else dict(a, playing=False))
        for key in [k for k in self._since if k not in keys]:
            self._since.pop(key)
        return out

    def _look(self):
        """apps() now, and the answers kept per app."""
        try:
            apps = self.capture.apps()
        except Exception as exc:
            log("meeting audio: apps: %r" % exc)
            apps = []
        try:
            choices = json.loads(Path(self.choices_path).read_text()).get("apps") or {}
        except (OSError, ValueError, AttributeError):
            choices = {}
        self.seen = apps
        return apps, choices

    def decide(self, current):
        import devices as dv
        apps, choices = self._look()
        app, why, self.ask = dv.choose_meeting(self.sustained(apps, current), self.mode, choices, current)
        if self.ask is not None and dv.answer_key(self.ask) not in self._told:
            self._told.add(dv.answer_key(self.ask))
            log("meeting: %s (not asked yet)" % why)
        return app, why

    def decide_all(self, recorded, off):
        """Every app to record now: recorded, the app_keys recorded now; off, those the
        user switched off for this meeting."""
        import devices as dv
        apps, choices = self._look()
        now = time.time()
        for a in apps:
            if a.get("playing"):
                self._played[dv.app_key(a)] = now
        return dv.choose_meetings(self.sustained(apps, None, now, recorded), self.mode, choices, off, recorded,
                                  lambda key: now - self._played.get(key, now))


# where each transcript line came from. Five words for the page's glyphs.
TRANSPORT_WORDS = {"built-in": "builtin", "bluetooth": "bluetooth", "usb": "usb", "virtual": "virtual"}


class Sources(object):
    """The sources heard in a session (sources.json), one id each: a device, or an
    app, under one name. A mid-meeting switch is a new id, so each transcript row
    ("src") says where it came from as it was then. Kept with the session, and on
    resume the same ids go on; on_change(text) ships it to the agent."""

    def __init__(self, path, on_change=None):
        self.path, self.on_change = Path(path), on_change
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            rows = []
        self.rows = rows if isinstance(rows, list) else []

    def id_for(self, kind, name, transport=None, bundle=None):
        want = {"kind": kind, "name": name}
        if transport:
            want["transport"] = transport
        if bundle is not None:
            want["bundle"] = bundle
        for row in self.rows:
            if dict((k, v) for k, v in row.items() if k != "id") == want:
                return row["id"]
        row = dict(want, id="s%d" % (len(self.rows) + 1))
        self.rows.append(row)
        text = json.dumps(self.rows, ensure_ascii=False)
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text)
            tmp.replace(self.path)
            if self.on_change:
                self.on_change(text)
        except Exception as exc:               # never at the cost of the line being written
            log("sources: could not keep or ship the table (%r)" % exc)
        return row["id"]


class MicFollow(object):
    """Which mic a mic channel records from as the Mac changes: the system's
    default, or the one chosen in Settings while it is connected (choose_mic).
    ask: a mic to ask the user about, or None (the app shows it)."""

    def __init__(self, devices, pinned, choices=CHOICES):
        self.devices, self.pinned, self.choices_path = devices, pinned or "", choices
        self.ask, self._told = None, set()

    def choices(self):
        try:
            return json.loads(Path(self.choices_path).read_text()).get("mic") or {}
        except (OSError, ValueError, AttributeError):
            return {}

    def decide(self, current):
        import devices as dv
        mic, why, self.ask = dv.choose_mic(self.devices.inputs(), self.devices.default_input(), self.pinned,
                                           current, self.choices(), self.devices.in_use)
        if self.ask is not None and self.ask.uid not in self._told:
            self._told.add(self.ask.uid)
            log("mic: %s (not asked yet)" % why)
        return mic, why


class Channel(object):
    """One audio device -> one streaming transcriber -> emit callback.

    NOT a thread. MLX streams are thread-local: a model loaded on the main
    thread raises "There is no Stream(gpu, ...) in current thread" when used
    from a worker thread. The first version ran ASR on one thread per channel,
    so every session died on its first audio block, which in mic mode (a single
    channel) ended the whole session. Now sounddevice's callback thread only
    copies numpy buffers into a queue, and pump() does all MLX work on the
    main thread.
    """

    def __init__(self, speaker, device, model, emit, wav_path, rotate_seconds,
                 speakers=None):
        self.speaker = speaker
        self.speakers = speakers     # SpeakerID, or None for no voice matching
        self.device = device
        self.model = model
        self.emit = emit
        self.wav_path = wav_path
        self.rotate_seconds = rotate_seconds
        self.audio = queue.Queue()
        self.error = None
        self.traceback = None
        self._input = None
        self._wav = None
        self._stream = None
        self._last_rotate = time.time()
        self.blocks = 0          # audio blocks transcribed
        self.lines = 0           # sentences emitted
        self.peak = 0.0          # loudest sample since last heartbeat
        self.level = 0.0         # loudest sample since the last live status
        self.partial = ""        # words of the utterance still being spoken
        self.device_name = "?"
        self.borrowed = None     # voice mode's echo-cancelling engine has the mic (VoiceIO)
        self.borrow_error = None # why it could not, for the app (live.json voice.why)
        # a mic channel follows the Mac (follow), switches and reconnects in the same
        # recording; its clock stays the session's (silence fills a gap).
        self.follow, self.mic, self.refresh = None, None, None
        # the meeting's audio instead of a device (capture: native.appaudio, app: whose);
        # offset: where this channel starts on the session clock, for one that joins late.
        self.capture, self.app, self.offset = None, None, 0.0
        # zero: the wall time of the session clock's 0 for this run, base: t_offset there; once the
        # first block comes, its own time (ScreenCaptureKit's, monotonic) places the channel exactly.
        self.zero, self.base, self.placed, self._placed_saved = None, 0.0, False, False
        self.state = "live"      # live | switching | reconnecting
        self.clock0, self.queued, self.last_audio = None, 0, None
        self.retry_at, self.backoff = None, BACKOFF[0]
        self._suspect = None     # when a stall was first seen: it must still be one a moment later
        self.cause = None        # why it is not recording, when the capture does not say (a stall)
        self.pumped = 0          # samples transcribed so far (heard_until: how far on its clock that is)
        self.table = None        # the session's Sources, for each line's "src"
        # the Mac's own sound taken out of this mic while dictating or in voice mode
        # (roomonly.RoomOnly), or None. Its lock keeps what it hands back in order.
        self.room, self._room_lock = None, threading.Lock()
        # While the engine has the mic (borrowed), its last second, and where Standby Dictation
        # takes it from during a call (tap_to): a second reader of the mic gets near silence then.
        self._ring, self._ring_n, self._tap, self._tap_lock = deque(), 0, None, threading.Lock()
        # diarizer() is the diarization model once loaded (None meanwhile, or never:
        # macOS 13, not installed); voices, this channel's speakers from it; held, lines
        # waiting for theirs; label_wait(), whether a line now is a transcript line (not
        # dictation or a voice turn, which need no label and never wait).
        self.diarizer, self.voices, self.held = None, None, []
        self.label_wait = lambda: True
        # seconds of nothing but exact zeros from the device, in a row (a mic macOS
        # refuses), and when it was last opened again for it.
        self.zeros, self._denied_reopen = 0.0, None
        # Q3: the speech model's view of the utterance still being spoken: (its frames, how
        # many of them it has heard, its speechvad.Stream), for onset.
        self._heard = None

    def _feed(self, block):
        """Audio from the device's thread (or voice mode's engine): queued, counted.
        While a room is on, what it hands back instead: the same samples in all, cleaned,
        some a moment later (they wait for the Mac's sound of their time)."""
        if self.cause is not None:             # audio again: a stall is over only now, not at reopening
            self.cause, self.backoff = None, BACKOFF[0]
        self.last_audio = time.time()
        if self.borrowed is not None:
            self._tap_feed(block)
        if self.capture is None:               # a call's capture sends zeros whenever the app is quiet
            self.zeros = 0.0 if block.any() else self.zeros + len(block) / float(SAMPLE_RATE)
        if self.room is None:
            return self._queue(block)
        with self._room_lock:
            self.queued += len(block)           # counted as it came: the clock is the mic's
            if self.room is not None:
                block = self.room.clean(block)
            if len(block):
                self.audio.put(block)

    def _tap_feed(self, block):
        """The engine's audio thread: its last second kept, and handed on while tapped."""
        with self._tap_lock:
            self._ring.append(block)
            self._ring_n += len(block)
            while self._ring and self._ring_n - len(self._ring[0]) >= SAMPLE_RATE:
                self._ring_n -= len(self._ring.popleft())
            if self._tap is not None:
                try:
                    self._tap.write(block.astype("float32", copy=False).tobytes())
                except Exception:
                    self._tap = None             # never at the cost of the call's own audio

    def tap_to(self, fh):
        """This mic, as the call's engine hears it (echo cancelled), from its last second on,
        written to fh (an open binary file), or no more (None)."""
        with self._tap_lock:
            if fh is not None:
                for block in self._ring:
                    fh.write(block.astype("float32", copy=False).tobytes())
            self._tap = fh

    def room_on(self, room):
        """From the next block, this mic with the Mac's own sound taken out."""
        with self._room_lock:
            self.room = room

    def room_off(self):
        """The mic as it is again; what the room still held is queued first."""
        with self._room_lock:
            room, self.room = self.room, None
            if room is not None:
                rest = room.flush()
                if len(rest):
                    self.audio.put(rest)
        return room

    def _room_drain(self):
        """What the room held for the Mac's sound of its time, once that has come."""
        if self.room is None:
            return
        with self._room_lock:
            if self.room is not None:
                out = self.room.drain()
                if len(out):
                    self.audio.put(out)

    def _queue(self, block):
        self.queued += len(block)
        self.audio.put(block)

    def _callback(self, indata, frames, time_info, status):
        if status:
            log("%s: %s" % (self.speaker, status))
        # Downmix to mono, copy out of the driver buffer. numpy only: no MLX here.
        self._feed(indata.mean(axis=1).astype("float32").copy())

    def _fail(self, exc):
        import traceback
        self.error = exc
        self.traceback = traceback.format_exc()
        log("%s channel died: %r" % (self.speaker, exc))
        log(self.traceback)
        self.close()

    def open(self):
        try:
            import sounddevice as sd
            self._stream = UtteranceTranscriber(self.model, self.rotate_seconds)
            if self.wav_path:
                self._wav = wave.open(str(self.wav_path), "wb")
                self._wav.setnchannels(1)
                self._wav.setsampwidth(2)
                self._wav.setframerate(SAMPLE_RATE)
            self.clock0 = self.last_audio = time.time()
            if self.capture is not None:
                return self._open_capture()
            if self.follow is not None:
                return self._open_following()
            self.device = resolve_device(self.device, self.speaker)
            self.device_name = sd.query_devices(self.device)["name"]
            self._open_input()
            log("%s: capturing from device %r" % (self.speaker, self.device))
        except Exception as exc:
            self._fail(exc)

    def _open_following(self):
        self.mic, why = self.follow.decide(None)
        if self.mic is None:
            return self._lost(RuntimeError(why))
        self.device_name = self.mic.name
        try:
            self._start(self.mic.name, refresh=False)       # PortAudio's list is fresh at start
        except Exception as exc:
            return self._lost(exc)
        log("%s: recording from %s (%s)" % (self.speaker, self.mic.name, why))

    def _open_capture(self):
        self.device_name = self.app.get("name") or self.app.get("app") or "the meeting"
        try:
            self._start(self.device_name)
        except Exception as exc:
            return self._lost(exc)
        log("%s: recording %s's audio" % (self.speaker, self.device_name))

    def _feed_t(self, block, t=None):
        """The meeting's audio: continuous (zeros while the app is silent), and t
        the buffer's own time: the first one places the channel on the session
        clock (the first audio comes ~0.2 s after asking)."""
        if not self.placed and t is not None and self.zero is not None:
            wall = time.time() - (time.monotonic() - t)
            self.offset, self.clock0, self.placed = wall - self.zero, wall, True
        self._feed(block)

    # -- another mic, or the same one again, in the same recording ----------------
    def _close_input(self):
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                pass
        if self._input is not None:
            try:
                self._input.stop()
                self._input.close()
            except Exception:
                pass
            self._input = None

    def _start(self, name, refresh=True):
        """This mic's stream: through voice mode's engine while it has the mic,
        else PortAudio's, after a fresh look at what is connected. The meeting's
        audio: its capture, on the app's sound."""
        if self.capture is not None:
            self.capture.start(self.app.get("bundle") or self.app.get("pid"), self._feed_t)
            err = getattr(self.capture, "error", None)
            if err:
                raise RuntimeError(err)
            return
        if refresh and self.refresh is not None:
            self.refresh(self)
        if self.borrowed is not None:
            try:
                self.borrowed.stop_mic()
            except Exception:
                pass
            try:
                self.borrowed.start_mic(name, self._feed)
                return
            except Exception as exc:
                log("%s: echo cancellation unavailable on %s (%r): the plain mic" % (self.speaker, name, exc))
                self.borrowed = None
        idx = pa_index(name)
        if idx is None:
            raise RuntimeError("%s is not connected" % name)
        self.device = idx
        self._open_input()

    def _fill(self):
        """Silence for the time no audio came, so this channel's clock stays the
        session's (and the other channel's): a switch, a device gone a while."""
        import numpy as np
        if self.clock0 is None:
            return
        missing = (time.time() - self.clock0) - self.queued / float(SAMPLE_RATE)
        if missing > MAX_FILL:
            return self._skip(missing)
        n, block = int(missing * SAMPLE_RATE), int(SAMPLE_RATE * BLOCK_SECONDS)
        if missing > 0.1:
            while n > 0:                           # silence, not audio: a pause is not over for it
                self._queue(np.zeros(min(n, block), dtype="float32"))
                n -= block

    def _skip(self, missing):
        """A gap too long to fill (the Mac slept through part of the meeting; an
        hour of silence would be 230 MB): later lines are moved on by it, and the
        recording goes on in a new wav file, placed where it resumes."""
        self.flush()                           # what came before the gap stays before it
        if self.voices is not None:            # its clock jumps: a fresh stream after the gap
            self.voices.stream.finish()
            self.voices = None
        self.offset += missing
        self.clock0 += missing
        log("%s: %.0fs with no audio (the Mac asleep?): the transcript goes on from now" % (self.speaker, missing))
        if self._wav is None or not self.wav_path:
            return
        try:
            self._wav.close()
            stem, n = self.wav_path.name.split(".")[0], 2
            path = self.wav_path.with_name("%s.%d.wav" % (stem, n))
            while path.exists():
                n += 1
                path = self.wav_path.with_name("%s.%d.wav" % (stem, n))
            path.with_name(path.name + ".offset").write_text("%.2f\n" % (self.base + self.offset + self.queued / float(SAMPLE_RATE)))
            self._wav = wave.open(str(path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(SAMPLE_RATE)
            self.wav_path = path
        except Exception as exc:
            log("%s: a new wav after the gap: %r" % (self.speaker, exc))
            self._wav = None

    def _lost(self, exc):
        """The mic went away or will not open: tried again, never given up on.
        The transcript waits; the app shows it reconnecting. The meeting's audio not
        allowed: looked at again every PERMISSION_POLL, which asks macOS nothing (the
        capture asks ScreenCaptureKit only once allowed), and said once in the log."""
        self._close_input()
        self.state = "reconnecting"
        if getattr(self.capture, "cause", None) == "permission":
            if self.retry_at is None or self.backoff != PERMISSION_POLL:
                log("%s: %s's sound is not allowed: waiting for it in System Settings, without asking again"
                    % (self.speaker, self.device_name))
            self.backoff = PERMISSION_POLL
            self.retry_at = time.time() + PERMISSION_POLL
            return
        self.retry_at = time.time() + self.backoff
        log("%s: %s is not recording (%s): trying again in %.1fs" % (self.speaker, self.device_name, exc, self.backoff))
        self.backoff = min(BACKOFF[1], self.backoff * 2)

    def _live(self):
        """Open again. After a stall it is not recording until audio comes (cause stays
        until _feed clears it): a capture can restart fine and still send nothing."""
        self.state, self.retry_at, self.last_audio = "live", None, time.time()
        if self.cause is None:
            self.backoff = BACKOFF[0]

    def mute(self, on):
        """The user's mic muted for the meeting. Closed (macOS's orange dot goes
        out), so nothing of it is heard or kept, while the session's clock runs on.
        Unmuted, it opens again (the Mac's mic now, if it follows), and the gap is
        silence like any other."""
        if on:
            if self.state == "muted" or self.error is not None:
                return
            self.flush()                          # what was said before it is kept
            if self.borrowed is not None:
                backend, self.borrowed = self.borrowed, None
                try:
                    backend.stop_mic()
                except Exception:
                    pass
            self._close_input()
            self.state, self.retry_at = "muted", None
            log("%s: muted, %s closed" % (self.speaker, self.device_name))
        elif self.state == "muted":
            self.state = "reconnecting"           # a mic chosen afresh, as after a device gone
            self.reconnect()
            log("%s: unmuted" % self.speaker)

    def switch(self, mic, why):
        """Another mic: what was being said is closed first, the gap is silence."""
        t0, was = time.time(), self.device_name
        self.flush()
        self._close_input()
        self.state, self.mic, self.device_name = "switching", mic, mic.name
        self._fill()
        try:
            self._start(mic.name)
        except Exception as exc:
            return self._lost(exc)
        self._live()
        log("%s: now %s (%s), was %s: gap %.2fs" % (self.speaker, mic.name, why, was, time.time() - t0))

    def due(self, now):
        """Its stream needs opening again: a retry is due, or an open stream has
        sent nothing for STALL s (a device gone often just goes quiet)."""
        if self.error is not None or self._stream is None:
            return False
        if self.state == "reconnecting":
            return self.retry_at is not None and now >= self.retry_at
        stale = ((self._input is not None or self.borrowed is not None or self.capture is not None)
                 and self.last_audio is not None and now - self.last_audio > STALL)
        if not stale and self.mic_denied():
            # Refused, and still sending its silence (one that stops is a stall, below). A
            # stream opened while refused may stay silent once allowed: opened again every
            # PERMISSION_POLL, so allowing it in System Settings is all the user does.
            if self._denied_reopen is None:
                self._denied_reopen = now
                log("%s: %s sends only silence: macOS isn't letting Standby use the microphone"
                    % (self.speaker, self.device_name))
            if now - self._denied_reopen >= PERMISSION_POLL:
                self._denied_reopen = now
                return True
            return False
        self._denied_reopen = None
        if not stale:
            self._suspect = None
            return False
        # A long decode holds this (main) thread, and the audio thread waits for it: its
        # blocks are late, not gone. Still stale after this loop has slept a little: gone.
        if self._suspect is None or now - self._suspect < 0.5:
            self._suspect = self._suspect or now
            return False
        self._suspect = None
        again = self.cause == "stalled"        # no audio since it was last opened for this: back off
        self.cause = "stalled"
        err = getattr(self.capture, "error", None) if self.capture is not None else None
        log("%s: no audio from %s for %.1fs%s: opening it again" % (
            self.speaker, self.device_name, now - self.last_audio, " (%s)" % err if err else ""))
        if again:                              # waits longer each time, until audio comes
            self._lost(RuntimeError("still no audio"))
            return False
        return True

    def reconnect(self):
        name = self.device_name
        if self.follow is not None and self.capture is None:
            mic, why = self.follow.decide(self.mic if self.state == "live" else None)
            if mic is None:
                return self._lost(RuntimeError(why))
            self.mic, name = mic, mic.name
        self._close_input()
        self.device_name = name
        self._fill()
        try:
            self._start(name)
        except Exception as exc:
            return self._lost(exc)
        self._live()
        log("%s: recording again from %s" % (self.speaker, name))

    def reopen_input(self):
        """PortAudio started again for another channel: this one's stream again."""
        if self.borrowed is not None or self.state != "live" or self.capture is not None:
            return
        idx = pa_index(self.device_name)
        try:
            if idx is None:
                raise RuntimeError("%s is not connected" % self.device_name)
            self.device = idx
            self._fill()
            self._open_input()
        except Exception as exc:
            self._lost(exc)

    def src_id(self):
        """This channel's source now, in the session's table. A switch closes the
        utterance in progress before the source changes, so a line's is its own."""
        if self.table is None:
            return None
        kind = "mic" if self.speaker in MIC_ROLES else "meeting"
        if self.capture is not None:
            return self.table.id_for(kind, self.device_name, bundle=(self.app or {}).get("bundle") or "")
        transport = getattr(self.mic, "transport", None) if self.mic is not None else None
        return self.table.id_for(kind, self.device_name, TRANSPORT_WORDS.get(transport, "other"))

    def heard_until(self):
        """How far this channel has listened, on the session clock (before t_offset)."""
        return self.offset + self.pumped / float(SAMPLE_RATE)

    def source(self):
        """For the app: what this channel hears, and whether it does."""
        out = {"kind": "mic" if self.speaker in MIC_ROLES else "meeting", "name": self.device_name,
               "level": round(self.level, 4), "ok": self.state == "live" and self.error is None and self.cause is None,
               "state": "dead" if self.error is not None else self.state}
        if self.state == "muted":                # not a fault, the user's choice
            out["muted"] = True
        if self.table is not None and self.device_name not in ("?", ""):   # the same id as the lines'
            out["id"] = self.src_id()
            row = next((r for r in self.table.rows if r["id"] == out["id"]), {})
            for key in ("transport", "bundle"):
                if key in row:
                    out[key] = row[key]
        if self.capture is not None:
            out.update(self.pause())
        elif self.mic_denied():                  # said like the meeting's refusal, with the way to allow it
            out.update({"ok": False, "cause": "permission", "reason": "not allowed",
                        "detail": "Allow Standby in System Settings > Privacy & Security > Microphone. "
                                  "It records by itself once allowed."})
        return out

    def mic_denied(self):
        """A mic that sends nothing but exact zeros, and macOS doesn't say it's
        allowed (a refused mic still opens; it is just silence). Allowed and still
        zeros (a muted interface) is not a permission, and isn't said to be one. Nor is voice
        mode's echo-cancelled stream (borrowed), whose silence may be exact zeros."""
        return (self.capture is None and self.borrowed is None and self.state == "live" and self.error is None
                and self.zeros >= MIC_DENIED_AFTER and mic_authorization() != "authorized")

    def pause(self):
        """The meeting's audio: recording, paused (it comes back by itself: retried)
        or error (the user has to do something), and why, in the capture's own words
        (native.appaudio's one table, never written here): reason for the chip, detail
        for the tooltip, past for the session's record. cause is a code, for logic
        and tests, never shown. A capture that says only .error still gets its words
        in front of the user rather than nothing."""
        if self.state == "live" and self.error is None and self.cause is None:
            return {"state": "recording"}
        cap, error = self.capture, getattr(self.capture, "error", None)
        words = {}
        if getattr(cap, "cause", None) is None and self.cause is not None:
            words = describe_capture(self.cause, self.device_name)     # a stall is the recorder's finding
        out = {"state": "paused", "cause": self.cause or "failed", "reason": error or "no audio is arriving",
               "detail": error or "", "past": "the meeting audio stopped"}
        for key in ("state", "cause", "reason", "detail", "past"):
            value = getattr(cap, key, None) or words.get(key)
            if value:
                out[key] = value
        if out["state"] not in ("paused", "error"):
            out["state"] = "paused"
        if out["state"] == "error":
            for key in ("confirm", "needs_restart"):          # what to do about it, when it offers one
                if getattr(cap, key, None) is not None:
                    out[key] = getattr(cap, key)
        return out

    def _open_input(self):
        import sounddevice as sd
        self._input = sd.InputStream(
            device=self.device, channels=1, samplerate=SAMPLE_RATE,
            dtype="float32", blocksize=int(SAMPLE_RATE * BLOCK_SECONDS),
            callback=self._callback)
        self._input.start()

    def borrow(self, backend):
        """Voice mode on open speakers: the mic comes through the backend's
        echo cancellation instead (VoiceIO), into the same queue, so the rest
        is unchanged. True if it does; else the plain mic stays."""
        self.borrow_error = None
        if self.error is not None or self._input is None or self.borrowed is not None:
            self.borrow_error = "the mic isn't open" if self.borrowed is None else None
            return False
        try:                                  # one stream at a time on the mic: no doubled audio
            self._input.stop()
            self._input.close()
        except Exception:
            pass
        self._input = None
        try:
            backend.start_mic(self.device_name, self._feed)
            self.borrowed = backend
            return True
        except Exception as exc:
            self.borrow_error = str(exc)      # why voice mode is half duplex: to the app (live.json)
            log("%s: echo cancellation unavailable (%r): the plain mic stays" % (self.speaker, exc))
            try:
                self._open_input()
            except Exception as exc2:
                self._fail(exc2)
            return False

    def give_back(self):
        """Voice mode is over: the plain mic again."""
        backend, self.borrowed = self.borrowed, None
        if backend is None:
            return
        try:
            backend.stop_mic()
        except Exception as exc:
            log("%s: stopping the echo-cancelled mic: %r" % (self.speaker, exc))
        if self._stream is not None and self.error is None and self.state == "live":   # not while closing
            try:
                if self.follow is not None:
                    self._start(self.device_name)       # the mic may have changed meanwhile
                else:
                    self._open_input()
            except Exception as exc:
                self._lost(exc) if self.follow is not None else self._fail(exc)

    def pump(self):
        """Drain queued audio into the transcriber. Main thread only."""
        if self.error is not None:
            return
        if self.placed and not self._placed_saved:
            self._placed_saved = True
            if self.wav_path:                  # the re-pass places its wav where the live lines are
                self.wav_path.with_name(self.wav_path.name + ".offset").write_text("%.2f\n" % (self.base + self.offset))
        import numpy as np
        self._room_drain()
        try:
            if getattr(self._stream, "held", False):
                # What an early decode held back, before any new audio
                self._stream.feed(np.zeros(0, "float32"))
                self._emit_all(self._stream.drain())
            # Early words that appear now: hand back once, so the app takes them before the
            # pause closes the utterance. Once only: words nobody takes never stall the audio.
            waiting = getattr(self._stream, "early", None) is not None
            while waiting or getattr(self._stream, "early", None) is None:
                try:
                    block = self.audio.get_nowait()
                except queue.Empty:
                    break
                if self._wav is not None:
                    self._wav.writeframes((np.clip(block, -1.0, 1.0) * 32767.0
                                           ).astype("<i2").tobytes())
                self.blocks += 1
                self.pumped += len(block)
                loud = float(np.abs(block).max())
                self.peak = max(self.peak, loud)
                self.level = max(self.level, loud)
                if self.voices is None and self.diarizer is not None and self.speakers is not None:
                    self._attach_voices(self.pumped - len(block))
                if self.voices is not None:
                    self.voices.stream.feed(block)
                self._stream.feed(block)
                self._emit_all(self._stream.drain())
            partial = getattr(self._stream, "partial", None)
            self.partial = partial() if partial else ""
            if time.time() - self._last_rotate > self.rotate_seconds:
                self._emit_all(self._stream.rotate())
                self._last_rotate = time.time()
            self._release()
        except Exception as exc:
            self._fail(exc)

    def _attach_voices(self, at):
        """Once the diarization model is ready, this channel's speakers come from
        it, from this sample on (at); until then, and if it fails, from the matcher."""
        model = self.diarizer()
        if model is None:
            return
        from native import diarizer
        self.voices = Voices(diarizer.stream(model, log=log), self.speakers, at)
        log("%s: speakers from the diarization model from %.1fs" % (self.speaker, at / float(SAMPLE_RATE)))

    def drop_voices(self, why):
        """Back to the matcher: the lines waiting go out first, labelled as far as known."""
        self._release(force=True)
        if self.voices is not None:
            s = self.voices.stream
            log("%s: diarization off (%s) after %d calls, %.0f ms each, %.1fs CPU" % (
                self.speaker, why, s.calls, 1000 * s.model_s / max(1, s.calls), s.busy))
            s.finish()
        self.voices, self.diarizer = None, None

    @property
    def speaking(self):
        return self._stream is not None and getattr(self._stream, "utt", None) is not None

    @property
    def voiced(self):
        """Seconds of speech in the utterance still being spoken, 0 if none."""
        return float(getattr(self._stream, "voiced", 0.0) or 0.0) if self.speaking else 0.0

    speech_vad = None     # Q3: speechvad's model, once voice mode loads it (None: loudness alone)

    @property
    def speechy(self):
        """Seconds of the utterance still being spoken that the speech model calls speech,
        0 if none. Heard lazily, only what came since the last ask: a few 32 ms calls."""
        utt = getattr(self._stream, "utt", None) if self.speaking else None
        if self.speech_vad is None or not isinstance(utt, list):
            self._heard = None
            return 0.0
        if self._heard is None or self._heard[0] is not utt:       # a new utterance: its own state
            self._heard = [utt, 0, self.speech_vad.stream()]
        heard = self._heard
        if len(utt) > heard[1]:
            import numpy as np
            try:
                heard[2].feed(np.concatenate(utt[heard[1]:]))
            except Exception as exc:                # a broken model: loudness again, as before
                log("speechvad: failed (%r); talking over the voice is judged by loudness" % exc)
                Channel.speech_vad, self._heard = None, None
                return 0.0
            heard[1] = len(utt)
        return heard[2].speechy

    @property
    def onset(self):
        """How much of the open utterance counts toward talking over Claude: speech by the
        speech model when it is loaded (a cough, a click or a laugh is loud, not speech),
        else the energy VAD's voiced seconds, as before."""
        return self.speechy if self.speech_vad is not None else self.voiced

    def open_audio(self):
        """(id, audio so far) of the utterance still being spoken, or (None, None)."""
        import numpy as np
        utt = getattr(self._stream, "utt", None) if self._stream is not None else None
        if not utt or not isinstance(utt, list):
            return None, None
        return getattr(self._stream, "utt_start", 0), np.concatenate(utt)

    @property
    def open_id(self):
        """Which utterance is being spoken (its start), or None."""
        return getattr(self._stream, "utt_start", None) if self.speaking else None

    def paused_words(self):
        """The open utterance's words at its first short pause (decoded early, VOICE_EARLY),
        or None while the speech goes on. Looked at, not taken (take_early)."""
        return getattr(self._stream, "_early", None) if self.speaking else None

    OVERLAP_PARTIAL = (0.4, 0.2)    # talked over: words so far this often (s), from this much speech (s)

    def overlap_mode(self, on):
        """Talked over (barge_in): its words sooner, the voice is waiting on them. A decode is
        about 0.3 s on the main thread (2026-10-20), so not more often than this."""
        if self._stream is not None and hasattr(self._stream, "partial_every"):
            self._stream.partial_every, self._stream.partial_min = (
                self.OVERLAP_PARTIAL if on else (0.5, 0.3) if self._stream.end_silence == self.VOICE_END_SILENCE
                else (1.5, 0.6))

    VOICE_END_SILENCE = 0.45

    def voice_mode(self, on):
        """Voice mode: words-so-far every half second (the captions), and a
        shorter pause closes an utterance, since Smart Turn and the punctuation,
        not the pause alone, decide when a turn is over."""
        if self._stream is not None:
            self._stream.partial_every, self._stream.partial_min = (0.5, 0.3) if on else (1.5, 0.6)
            self._stream.end_silence = (self.VOICE_END_SILENCE if on
                                        else getattr(type(self._stream), "END_SILENCE", 0.6))
            if hasattr(self._stream, "early_after"):
                self._stream.early_after = self.VOICE_EARLY if on else None

    VOICE_EARLY = 0.2     # voice mode: the open utterance decoded at this much quiet

    def take_early(self):
        """Voice mode: the open utterance's words at its first short pause, once,
        (start, end, text, audio), or None. Main thread only."""
        take = getattr(self._stream, "take_early", None)
        return take() if take is not None and self.error is None else None

    def flush(self):
        """Close the utterance in progress now, e.g. when dictation starts or
        stops, so its words land on the right side. Main thread only."""
        if self._stream is None or self.error is not None:
            return
        self.pump()
        try:
            self._emit_all(self._stream.drain(force=True))
            self._release(force=True)
        except Exception as exc:
            self._fail(exc)

    def _emit_all(self, items):
        v = self.voices
        if v is not None and v.stream.failed:
            self.drop_voices("it stopped")
            v = None
        for item in items:
            if v is not None and self.label_wait():
                self.held.append((time.time(), item))      # out once its speakers are known
            elif v is not None:     # dictation or a voice turn: no label, and never the matcher's
                self._emit_part(item[0], item[1], item[2], item[3] if len(item) > 3 else None, None)
            else:
                start, end, text = item[:3]
                audio = item[3] if len(item) > 3 else None
                spk = self.speakers.identify(audio) if self.speakers is not None else None
                self._emit_part(start, end, text, audio, spk)
        self._release()

    def _emit_part(self, start, end, text, audio, spk):
        self.lines += 1
        src = self.src_id()                   # an emit that doesn't know sources gets none
        self.emit(self.speaker, start + self.offset, end + self.offset, text, spk, audio,
                  **({"src": src} if src is not None else {}))

    def _release(self, force=False):
        """Lines whose speakers the model now knows, in order: at most Voices.HOLD after
        they would have gone out (a line cut at MAX_UTT as well), split where the voice
        changes. Measured: the label is known 0.10 s after a line closes (median),
        0.45 s at most, so a line waits for nothing parakeet wasn't still decoding."""
        while self.held:
            at, item = self.held[0]
            v = self.voices
            start, end = item[0], item[1]
            if v is not None and not force and time.time() - at < Voices.HOLD and \
                    v.stream.upto < end - v.s0 - self._end_silence():
                return
            self.held.pop(0)
            if v is None:
                audio = item[3] if len(item) > 3 else None
                self._emit_part(start, end, item[2], audio,
                                self.speakers.identify(audio) if self.speakers is not None else None)
            else:
                self._emit_labelled(item)

    def _end_silence(self):
        return float(getattr(self._stream, "end_silence", 0.6) or 0.0)

    def _emit_labelled(self, item):
        start, end, text = item[:3]
        audio = item[3] if len(item) > 3 else None
        v = self.voices
        a, b = start - v.s0, end - v.s0
        words = getattr(self._stream, "words", {}).pop(start, None)
        runs = v.stream.runs(a, b)
        parts = split_words(runs, words, a, text) if len(runs) > 1 and words and audio is not None else []
        if len(parts) < 2:
            parts = [(a, b, text, v.stream.who(a, b))]
        for p0, p1, ptext, slot in parts:
            seg = audio[int((p0 - a) * SAMPLE_RATE):int((p1 - a) * SAMPLE_RATE)] if audio is not None else None
            spk = None
            if slot is not None and p1 - p0 >= 0.5 and self.speakers is not None:     # as the matcher: none under 0.5 s
                spk = v.number(slot, seg)
            self._emit_part(p0 + v.s0, p1 + v.s0, ptext, seg, spk)

    def close(self):
        if self.borrowed is not None:
            backend, self.borrowed = self.borrowed, None
            try:
                backend.stop_mic()
            except Exception:
                pass
        self._close_input()
        self.room_off()
        if self._stream is not None and self.error is None:
            try:
                self.pump()
                self._emit_all(self._stream.drain(force=True))
                self._release(force=True)
            except Exception:
                pass
        if self.voices is not None:
            self.drop_voices("the channel closed")
        if self._stream is not None:
            self._stream._close()
            self._stream = None
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None


# --------------------------------------------------------------------------
# Voice mode on open speakers
# --------------------------------------------------------------------------

# The user's own voice (myvoice.MyVoice), and how an utterance's print is
# made. Tests put fakes here; else the real ones, only while remember_voices is on.
MY_VOICE = None
EMBED = None


# makes what hears the Mac's own sound, to take it out of the mic while dictating
# or in voice mode: make(output) -> roomonly.Reference, or None. Tests put a fake here;
# else the real one (ScreenCaptureKit, all apps but Standby), never in a test.
ROOM_ONLY = None


def room_only():
    """What makes the Mac's sound for this session, or None. Off until checked with
    real audio on the user's Mac (room_only in config.json), like voice mode's
    duplex was; and only once allowed, asking nothing (roomonly.start)."""
    if ROOM_ONLY is not None:
        return ROOM_ONLY
    if os.environ.get("STANDBY_NO_DEVICES") or sys.platform != "darwin":
        return None
    if not load_config().get("room_only", False):
        return None

    def make(output):
        import roomonly
        return roomonly.start(output, log=log)
    return make


# Makes the echo-cancelling audio engine. Tests put a fake here; else
# the real one (native.duplex) where it can run, and never in a test.
# Without one, voice mode is half duplex, as before.
VOICE_BACKEND = None


def voice_backend(voice=None):
    """What makes voice mode's engine for this session, or None. A test never
    gets the real one: it would open the mic and play on the speakers
    (STANDBY_NO_DEVICES, set by the tests' guard and run_all). voice: the voice the
    session's agent was given, else Settings' voice."""
    if VOICE_BACKEND is not None:
        return VOICE_BACKEND
    if os.environ.get("STANDBY_NO_DEVICES"):
        return None
    from native import duplex as os_duplex
    if not os_duplex.available():
        return None

    wanted = voice

    def make():
        system, neural, why = backend_voices(wanted, load_config())
        b = os_duplex.engine(voice=system, neural=neural, log=log)
        if why:                                 # never the Mac's voice instead: the orb says why
            b.failed = b._why = why
            log("voice: Standby's voice can't speak here: %s" % why)
        return b
    return make


def backend_voices(wanted, cfg, installed=None):
    """(macOS voice, Pocket voice, why it's the Mac's) for voice mode's engine: Standby's own voice
    (Jean, the chosen Pocket voice, or the agent's), unless a macOS voice is the one chosen.
    `why` is "" unless the Mac's voice speaks although the user didn't choose it."""
    import tts
    chosen = wanted or (cfg.get("voice_mode") or {}).get("voice")
    have = tts.available() if installed is None else installed
    neural = tts.voice_for(chosen, installed=have)
    pocket = not chosen or str(chosen).startswith("pocket:")
    system = None if neural or pocket else chosen
    why = "Standby's voice isn't installed" if pocket and not neural else ""
    return system, neural, why


def _output_leaks():
    """devices.output_leaks for VoiceIO; True (it may) when it can't be told, and in a test."""
    if os.environ.get("STANDBY_NO_DEVICES"):
        return True
    try:
        from native import mics
        return bool(mics.system().output_leaks())
    except Exception:
        return True


class VoiceIO(object):
    """Voice mode's audio, so the user can talk over Claude on open speakers.

    Apple's echo cancellation takes out of the mic only what the same engine
    plays. So while voice mode is on, the mic channels come through it here
    (Channel.borrow), and so does Claude's voice: the app writes the sentences
    to say to the session's speak.jsonl ({"seq", "id", "say"},
    {"stop"}, {"begin"}: a new answer), and this plays them, on a thread of
    its own (never the MLX one). With Claude's own voice gone from the mic,
    talking over it is heard: see barge_in in run_session.

    backend: start_mic(device_name, feed) and stop_mic(): mono float32 blocks
    at SAMPLE_RATE to feed; say(text), halt(); playing, true from say()
    until the sentence has played or halt(); aec, true when the echo is
    cancelled. Tests give a fake one."""

    POLL = 0.02           # a sentence written by the app starts within this
    BARGE_MIN = 0.3       # seconds of the user's speech over Claude's that stop it
    # The echo cancelling learns the call's sound from Claude's first seconds of voice, and a
    # faint echo gets through meanwhile: it ducked on its own greeting, a second's gap mid-
    # sentence (talk-20261026-212951, 203402, 195509, all 1.6 to 2.3 s in). Then it ducks once
    # the voice is told to be the user's (open_whose), or on BARGE_LEARNING of speech.
    LEARNING = 4.0        # seconds of Claude's voice played before the echo cancelling has it
    BARGE_LEARNING = 0.8
    # A sentence still "playing" after this long is an engine that stopped (a route
    # change, a voice that hung): about twice as slow as any voice speaks, plus a start.
    STUCK_BASE, STUCK_PER_CHAR = 5.0, 0.15
    # The first sentence of a voice mode can wait on Standby's voice starting cold (its
    # model loads in about 2 s, the first sound 1.2 s after, more with the GPU busy:
    # measured): it gets this much more, so a cold start never trips it.
    STUCK_COLD = 8.0
    DUCK_MAX = 3.0        # paused under the user's voice this long, nothing decided (overlap.MAX_DUCK is 1.6 s): on
    LEAK_EVERY = 1.0      # on headphones: how often it looks whether the output still is them
    SETTINGS_EVERY = 0.5  # how often it looks whether Settings changed the voice (one stat)

    def __init__(self, sdir, backend, log=log, leaks=None, talk_over=True, fixed=None):
        self.path = sdir / "speak.jsonl"
        self.backend, self.log = backend, log
        self.fixed = fixed     # a voice the session's agent was given: Settings' doesn't change it
        try:                   # config.json's mtime when the voice was last taken from it
            self._settings = CONFIG.stat().st_mtime
        except OSError:
            self._settings = None
        self._settings_at = 0.0
        # Could what it plays reach the mic (devices.output_leaks)? Unsure: it could.
        self.leaks = leaks or _output_leaks
        self.talk_over = talk_over  # Settings' "Let me talk over Claude": off, it plays and the mic is held
        self.own = False       # it plays on an engine of its own, the mic left as it is (start_output)
        self.no_echo = False   # ...on headphones, where nothing played reaches the mic: talking over works
        self._leak_at = 0.0
        self.seq, self._offset = None, 0
        self._queue, self._lock = deque(), threading.Lock()
        self.current, self.done, self.since = None, 0, None
        self._on = threading.Event()
        self.borrowed = []
        self.failed = None    # why the engine stopped working: the app's own voice speaks then
        self._warm = False    # a sentence has played in full: Standby's voice is warm
        self.ducked = None    # since when it is paused under the user's voice (duck), or None
        self.first_sound = None   # when its first sentence began to play (learning)
        self._turn_of = {}    # a sentence's turn, as the app wrote it (the trace's)

    def start(self, seq, mics):
        from native import qos
        self.seq = seq
        qos.interactive()                        # the recorder's loop (its decodes, the voice's run loop), from now on
        self._held = qos.hold("Standby: a call's voice")    # no App Nap while it plays
        try:
            self._offset = self.path.stat().st_size
        except OSError:
            self._offset = 0
        self.borrowed = [c for c in mics if c.borrow(self.backend)] if self.talk_over else []
        if not self.borrowed and hasattr(self.backend, "start_output"):
            # Echo cancellation didn't take the mic (not the default one: the Mac's, kept while
            # AirPods play), or talking over is off: the voice plays here all the same, the one
            # voice of the call. On headphones the user can still talk over it (no echo).
            try:
                self.backend.start_output()
                self.own, self.no_echo = True, self.talk_over and not self.leaks()
            except Exception as exc:
                self.log("voice %s: the voice could not play here (%r)" % (seq, exc))
        self._on.set()
        threading.Thread(target=self._loop, name="voice-out", daemon=True).start()
        self.log("voice %s: %s" % (seq, "echo cancelled: talking over Claude stops it" if self.aec
                                   else "on headphones, no echo to cancel: talking over Claude stops it" if self.no_echo
                                   else "played here, no echo cancellation: half duplex" if self.own
                                   else "its voice could not play"))

    def stop(self):
        from native import qos
        self._on.clear()
        self.halt()
        qos.release(getattr(self, "_held", None))
        for c in self.borrowed:
            c.give_back()
        self.borrowed = []
        if self.own:
            self.own = self.no_echo = False
            try:
                self.backend.stop_mic()                  # its engine, and Standby's voice process
            except Exception as exc:
                self.log("voice: stopping the audio engine: %r" % exc)

    @property
    def aec(self):
        return bool(self.borrowed) and bool(getattr(self.backend, "aec", False))

    @property
    def plays(self):
        """It plays Claude's voice: through echo cancellation, or on an engine of its own."""
        return bool(self.borrowed) or self.own

    @property
    def duplex(self):
        """The user can talk over Claude: its echo cancelled, or none to cancel."""
        return self.aec or self.no_echo

    @property
    def talking(self):
        """Claude is saying something."""
        with self._lock:
            return self.current is not None or bool(self._queue)

    @property
    def learning(self):
        """Its first seconds of voice: the echo cancelling still learns it (LEARNING). On
        headphones there's no echo to learn."""
        return self.aec and (self.first_sound is None or time.time() - self.first_sound < self.LEARNING)

    @property
    def can_duck(self):
        """The engine can pause under the user's voice and go on (duck), not only stop."""
        return hasattr(self.backend, "duck") and not self.failed

    def duck(self):
        """Talked over: quiet, then paused, with what is being said kept where it
        was, until the words say whether to go on (unduck) or stop (halt)."""
        with self._lock:
            if self.ducked is not None:
                return
            self.ducked = time.time()
        trace(TRACE["sid"], "duck", self._turn_of.get(self.current[0]) if self.current else None)
        try:
            self.backend.duck()
        except Exception as exc:
            self.log("voice: duck: %r" % exc)

    def unduck(self):
        """On from where it paused. The time paused is not the sentence's time: a
        sentence is stuck only after its own playing time (_step)."""
        with self._lock:
            at, self.ducked = self.ducked, None
            if at is not None and self.since is not None:
                self.since += time.time() - at
        if at is None:
            return
        trace(TRACE["sid"], "unduck", ducked_ms=int((time.time() - at) * 1000))
        try:
            self.backend.unduck()
        except Exception as exc:
            self.log("voice: unduck: %r" % exc)

    def halt(self):
        """Stop speaking now, and drop what was still to be said."""
        with self._lock:
            if self.current is not None or self._queue:
                cur = self.current
                trace(TRACE["sid"], "say_cut", self._turn_of.get(cur[0]) if cur else None,
                      id=cur[0] if cur else None, at_ms=int((time.time() - self.since) * 1000) if cur and self.since else None,
                      dropped=len(self._queue))
            self.ducked = None
            for item in list(self._queue) + ([self.current] if self.current else []):
                self.done = max(self.done, item[0])
            self._queue.clear()
            self.current = None
        try:
            self.backend.halt()
        except Exception as exc:
            self.log("voice: halt: %r" % exc)

    def _rows(self):
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1                # whole lines only
        self._offset += end
        out = []
        for line in data[:end].splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("seq") == self.seq:
                out.append(row)
        return out

    def _loop(self):
        from native import qos
        qos.interactive()                        # the voice's sentences, to the player on time
        while self._on.is_set():
            try:
                self._step()
            except Exception as exc:
                self.log("voice: speaking: %r" % exc)
            time.sleep(self.POLL)

    def _settings_voice(self):
        """Settings' voice, changed during the call: the engine takes it from the next sentence."""
        if self.fixed or not hasattr(self.backend, "use_voice") or time.time() - self._settings_at < self.SETTINGS_EVERY:
            return
        self._settings_at = time.time()
        try:
            mtime = CONFIG.stat().st_mtime
        except OSError:
            return
        if mtime == self._settings:
            return
        self._settings = mtime
        try:
            system, neural, why = backend_voices(None, load_config())
        except (ValueError, OSError, SystemExit) as exc:
            return self.log("voice %s: Settings unreadable: %r" % (self.seq, exc))
        self.backend.use_voice(system, neural, why)

    def _step(self):
        self._settings_voice()
        if self.own and self.talk_over and not self.failed and time.time() - self._leak_at >= self.LEAK_EVERY:
            self._leak_at = time.time()
            no_echo = not self.leaks()
            if no_echo != self.no_echo:              # headphones off (the speakers would echo), or on
                self.no_echo = no_echo
                self.log("voice %s: %s" % (self.seq, "on headphones: talking over Claude stops it" if no_echo
                                           else "headphones off: half duplex, the mic held while Claude speaks"))
        for row in self._rows():
            if row.get("stop"):
                self.halt()
            elif row.get("begin"):
                if hasattr(self.backend, "begin"):      # which voice says this answer
                    self.backend.begin()
            elif row.get("say"):
                with self._lock:
                    self._queue.append((int(row.get("id") or 0), str(row["say"])))
                    self._turn_of[int(row.get("id") or 0)] = row.get("turn")
                VOICE_SAID.append((time.time(), str(row["say"])))
        if self.failed:
            return                                   # the app speaks now: what comes is not ours
        if self.ducked is not None and time.time() - self.ducked > self.DUCK_MAX:
            self.log("voice %s: ducked %.0fs with nothing decided: it goes on" % (self.seq, time.time() - self.ducked))
            self.unduck()                            # whatever happened to the decision (barge_in)
        try:
            playing = self.backend.playing
        except Exception as exc:
            return self.fail("the engine: %r" % exc)
        loading = bool(getattr(self.backend, "loading", False))     # Standby's voice still getting ready
        with self._lock:
            stuck = None
            if self.current is not None and loading:
                self.since = time.time()             # it waits for the voice: its time starts once it can play
            elif self.current is not None and self.first_sound is None:
                self.first_sound = time.time()
            if self.current is not None and not playing:
                b = self.backend
                trace(TRACE["sid"], "said", self._turn_of.pop(self.current[0], None), id=self.current[0],
                      play_ms=int((time.time() - self.since) * 1000),
                      synth_ms=int((b.first_sound - b.said_at) * 1000)
                      if getattr(b, "first_sound", None) and getattr(b, "said_at", None) else None)
                self.done, self.current, self._warm = max(self.done, self.current[0]), None, True
            elif self.current is not None and self.ducked is None and time.time() - self.since > (
                    self.STUCK_BASE + self.STUCK_PER_CHAR * len(self.current[1])
                    + (0.0 if self._warm else self.STUCK_COLD)):
                stuck = time.time() - self.since
            nxt = self._queue.popleft() if self.current is None and self._queue else None
            if nxt is not None:
                self.current, self.since = nxt, time.time()
        if stuck is not None:
            return self.fail("a sentence was still playing after %.0fs" % stuck)
        if nxt is not None:
            trace(TRACE["sid"], "say", self._turn_of.get(nxt[0]), id=nxt[0], text=nxt[1])
            try:
                self.backend.say(nxt[1])
            except Exception as exc:
                self.fail("speaking: %r" % exc)

    def fail(self, why):
        """The engine stopped working: nothing more is played by it, what it held counts as
        done (never waited on), and the recorder gives the plain mic back and makes the
        engine again (retire_voice_io)."""
        with self._lock:
            if self.failed:
                return
            self.failed = why
            for item in list(self._queue) + ([self.current] if self.current else []):
                self.done = max(self.done, item[0])
            self._queue.clear()
            self.current = None
        self.log("voice %s: Claude's voice stopped working here (%s)" % (self.seq, why))
        try:
            self.backend.halt()
        except Exception:
            pass

    def status(self):
        if self.failed:
            return {"speaks": False, "failed": self.failed, "done": self.done}
        with self._lock:
            st = {"speaks": True, "aec": self.aec, "duplex": self.duplex, "saying": self.current[1] if self.current else "",
                  "since": self.since if self.current else None, "done": self.done,
                  "ducked": self.ducked is not None}
        tts = getattr(self.backend, "tts_status", None)
        if tts is not None and tts() is not None:
            st["tts"] = tts()                         # Standby's voice: getting ready, ready, failed
        return st


# --------------------------------------------------------------------------
# Delivering the transcript to the agent
# --------------------------------------------------------------------------

class Shipper(threading.Thread):
    """Tail the local JSONL and deliver new bytes to the agent's live dir
    (~/.standby/live, through transport.py's files on this Mac).

    The local file is the source of truth and the shipper tracks a byte
    offset, so a write that fails only delays delivery: nothing is lost and
    nothing is duplicated.
    """

    def __init__(self, cfg, session, local_path, sdir=None, meta=None):
        threading.Thread.__init__(self, daemon=True)
        import transport
        self.cfg = cfg
        self.session = session
        self.local = local_path
        self.sdir = sdir
        self.meta = meta
        self.offset = 0
        self.stream = None
        self.connected = False
        # Claude on for this session? None until first checked. While off,
        # nothing is delivered; turning it on catches up from self.offset.
        self.enabled = None
        self.announced = False
        self._last_announce = 0.0
        self.deferred = {}
        self.lock = threading.Lock()
        self.t = transport.for_config(cfg)

    def claude_on(self):
        return self.sdir is None or not (self.sdir / "claude.off").exists()

    def announce(self, meta=None):
        if meta is not None:
            self.meta = meta
        ok = True
        if self.meta is not None:
            copy_now(self.sdir, self.meta)
            ok = self.t.write("live/%s.meta.json" % self.session, json.dumps(self.meta))
        ok = ok and self.t.write("live/CURRENT", self.session + "\n")
        if not ok:
            log("announce to %s failed: trying again in 5 s" % self.t.label)
        return ok

    def ship_file(self, rel, data):
        with self.lock:
            if not self.enabled:
                self.deferred[rel] = data
                return True
        return self.t.write("live/%s.%s" % (self.session, rel), data)

    def _sync(self):
        """Follow the session's Claude switch (claude.off, set by the app)."""
        on = self.claude_on()
        if on == self.enabled:
            return
        with self.lock:
            self.enabled = on
            deferred, self.deferred = (self.deferred, {}) if on else ({}, self.deferred)
        if on:
            size = self.local.stat().st_size if self.local.exists() else 0
            log("Standby's meeting agent is on for this session: delivering to %s (%d bytes to catch up)"
                % (self.t.label, max(size - self.offset, 0)))
            self.announced = False
            for name in ("speakers.auto.json", "speakers.names.json", "speakers.known.json"):
                if self.sdir is not None and (self.sdir / name).exists():
                    deferred[name] = (self.sdir / name).read_text()
            for rel, data in deferred.items():
                self.t.write("live/%s.%s" % (self.session, rel), data)
        else:
            # Off is Standby's own meeting agent only: the transcript is still written to the
            # session's folder, where a meeting started from Claude Code is read by its plugin.
            log("Standby's meeting agent is off for this session: nothing goes to ~/.standby/live; "
                "the transcript is in the session's folder (a meeting from Claude Code reads it there)")
            self._disconnect()
            self.t.remove("live/CURRENT")

    def ship_log(self):
        """Copy the daemon log next to the transcript as live/<session>.log, so
        a failure is readable from Claude Code without anyone relaying it."""
        if not self.enabled:
            return
        logfile = ROOT / "daemon.log"
        if logfile.exists():
            with open(logfile, "rb") as fh:
                self.ship_file("log", fh.read()[-200000:])

    def finish(self, error_text=None):
        """Clear the live pointer, and ship the failure reason if there was one."""
        self.t.remove("live/CURRENT")
        if error_text and self.enabled:
            self.ship_file("error", error_text)

    def _disconnect(self):
        self.connected = False
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _connect(self):
        self.stream = self.t.appender("live/%s.jsonl" % self.session)
        self.connected = True
        log("delivering transcript to %s (live/%s.jsonl)" % (self.t.label, self.session))

    def run(self):
        while not _stop.is_set() or (self.enabled and self._pending()):
            try:
                self._sync()
                if not self.enabled:
                    time.sleep(0.4)
                    continue
                if not self.announced and time.time() - self._last_announce > 5.0:
                    self._last_announce = time.time()
                    self.announced = self.announce()
                if not self.connected:
                    self._connect()
                self._flush()
            except Exception as exc:
                log("delivery to %s failed, retrying in 5s: %r" % (self.t.label, exc))
                self._disconnect()
                time.sleep(5.0)
                continue
            time.sleep(0.4)
        if self.stream:
            self.stream.close()

    def _pending(self):
        try:
            return self.local.stat().st_size > self.offset
        except OSError:
            return False

    def _flush(self):
        if not self._pending():
            return
        with open(self.local, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
        if not chunk:
            return
        self.stream.write(chunk)
        self.stream.flush()
        self.offset += len(chunk)
        if self.sdir is not None:
            (self.sdir / "delivered").write_text("%d\n" % self.offset)


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------

def new_session_id(label, now=None, sessions=None):
    """A new session's id, and its folder, claimed: the label and its start to
    the second, then -2, -3... if taken. Ids used to stop at the minute, so two
    sessions started within one minute shared a folder, and the second wrote
    into the first's transcript, chat and agent conversation. Older ids (no
    seconds) stay valid: nothing reads the time back out of an id."""
    sessions = SESSIONS if sessions is None else sessions
    # The label is the user's word (`standby start <label>`): letters, digits, - and _ only, so
    # an id is always a plain folder name the app accepts (ui.SESSION_ID), never a path.
    label = re.sub(r"[^A-Za-z0-9_-]+", "", str(label or "")).lstrip("-_")[:40] or "meeting"
    base = "%s-%s" % (label, time.strftime("%Y%m%d-%H%M%S", time.localtime(now)))
    sid, n = base, 2
    sessions.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            (sessions / sid).mkdir()          # atomic: whoever makes it, owns it
            return sid
        except FileExistsError:
            sid, n = "%s-%d" % (base, n), n + 1


ATTACH_COPY = "attach.copy"     # in a session dir: the app switched it to a copy of its conversation


def copy_now(sdir, meta):
    """A session on one of the user's conversations, switched by the app to a copy
    while that conversation was open elsewhere ("Use a copy"): its meta says so
    from then on, here and where the agent is."""
    a = meta.get("attach")
    if sdir is not None and isinstance(a, dict) and not a.get("copy") and (sdir / ATTACH_COPY).exists():
        meta["attach"] = dict(a, copy=True)


class CaptureAsks(object):
    """When it is not clear what to record, one calm question at a time
    (capture.ask.json, which the app shows), answered through capture.answer, and
    kept in capture-choices.json per device and per app, so it is asked once.

    mics() and follow() are the session's mic channels and its meeting follower, as
    they are now; watch is the Mac's mic watch (or None); on_answer() is called once
    an answer is kept, so the recorder looks again at once."""

    def __init__(self, sdir, mics, follow, watch, on_answer):
        self.ask_path, self.answer_path = sdir / "capture.ask.json", sdir / "capture.answer"
        self.mics, self.follow, self.watch, self.on_answer = mics, follow, watch, on_answer
        self.current = None

    @staticmethod
    def answered():
        try:
            choices = json.loads(CHOICES.read_text())
        except (OSError, ValueError):
            return set()
        return set("mic:" + k for k in choices.get("mic") or {}) | set("app:" + k for k in choices.get("apps") or {})

    def candidates(self):
        """What to ask about, from the last looks; never what was answered already
        (a look taken before the answer still has it as a question)."""
        import devices as _dv
        out = []
        for ch in self.mics():
            if ch.follow is not None and ch.follow.ask is not None:
                out.append(_dv.ask_for("mic", ch.follow.ask))
        follow = self.follow()
        if follow is not None and follow.ask is not None:
            out.append(_dv.ask_for("meeting", follow.ask))
        done = self.answered() if out else set()
        return [a for a in out if a["id"] not in done]

    @staticmethod
    def remember(ask_id, value):
        try:
            choices = json.loads(CHOICES.read_text())
        except (OSError, ValueError):
            choices = {}
        kind, key = ask_id.split(":", 1)
        choices.setdefault("mic" if kind == "mic" else "apps", {})[key] = value
        tmp = CHOICES.with_suffix(".tmp")
        tmp.write_text(json.dumps(choices, indent=1, sort_keys=True))
        tmp.replace(CHOICES)

    def update(self):
        current = self.current
        try:
            answer = json.loads(self.answer_path.read_text())
        except (OSError, ValueError):
            answer = None
        if answer is not None:
            try:
                self.answer_path.unlink()
            except OSError:
                pass
            if current is not None and answer.get("id") == current["id"] \
                    and answer.get("value") in [a["value"] for a in current["actions"]]:
                self.remember(current["id"], answer["value"])
                log("asked: capture choice kept for next time")
                try:
                    self.ask_path.unlink()
                except OSError:
                    pass
                self.current = current = None
                if self.watch is not None:
                    self.watch.poke()              # act on it now, not at the next look
                self.on_answer()
                return                             # the next question, if any, after those looks
            else:
                log("asked: an answer to a question no longer asked, ignored")
        wanted = self.candidates()
        if current is not None and current["id"] not in [a["id"] for a in wanted]:
            try:
                self.ask_path.unlink()
            except OSError:
                pass
            log("asked: withdrawn, no longer the case")
            self.current = current = None
        if current is None and wanted:
            self.current = wanted[0]               # one at a time: the next waits for this one
            tmp = self.ask_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(dict(wanted[0], at=time.time()), ensure_ascii=False))
            tmp.replace(self.ask_path)
            log("asking: capture choice")


class Events(object):
    """events.jsonl: what happened to the recording, not what was said (the agent reads
    transcript.jsonl as speech), as spans in session time: the meeting's audio paused,
    voice mode in a meeting, the user's mic muted, an app switched off for the meeting. Written whole, so a row
    can be closed; a span is opened when it starts, so a crash mid-span leaves it on record.

    now() is the session clock; shipper (or None) gets each version for the agent."""

    def __init__(self, sdir, kind, now, shipper):
        self.path, self.kind, self.now, self.shipper = sdir / "events.jsonl", kind, now, shipper
        self.pause_rows = {}        # a meeting app's audio not recorded, since: per app (app_key)
        self.talked_row = None      # voice mode in a meeting: the transcript's "You talked to Claude"
        # the app writes "muted" to sdir/mic, and takes it away to unmute.
        self.mic_ctl, self.muted, self.muted_row = sdir / "mic", False, None
        # the app writes the apps switched off for this meeting to sdir/sources.off, {app_key: name}:
        # each is not recorded while it is there. A new meeting is a new folder: all on again.
        self.off_ctl, self.off, self.off_rows = sdir / "sources.off", {}, {}

    def session_time(self):
        return round(self.now(), 2)

    def write(self, rows):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        tmp.replace(self.path)
        if self.shipper:
            self.shipper.ship_file("events.jsonl", self.path.read_text())

    def read(self):
        try:
            return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]
        except (OSError, ValueError):
            return []

    def open_row(self, row):
        self.write(self.read() + [row])
        return row

    def close_row(self, row):
        """The span `row` ends now."""
        rows, closed = self.read(), dict(row, to=self.session_time())
        self.write([closed if r == row else r for r in rows] if row in rows else rows + [closed])
        return closed

    def watch_pause(self, chs):
        """A span for each stretch a meeting app's audio (chs: its channels now) is not
        recorded. One no longer recorded from ends its own."""
        import devices as dv
        recording = set()
        for ch in chs:
            key, state = dv.app_key(ch.app), ch.pause()
            if state["state"] == "recording":
                recording.add(key)
            elif key not in self.pause_rows:
                self.pause_rows[key] = self.open_row({"kind": "meeting", "name": ch.device_name, "from": self.session_time(),
                                                      "to": None, "cause": state["cause"], "reason": state["past"]})
                log("meeting: %s's audio paused: %s" % (ch.device_name, state["reason"]))
        for key in [k for k in self.pause_rows if k in recording or k not in set(dv.app_key(c.app) for c in chs)]:
            self.close_pause(key)

    def close_pause(self, key=None):
        """The pause span of one app (app_key), or of every one (None), ends."""
        for k in [k for k in self.pause_rows if key in (None, k)]:
            closed = self.close_row(self.pause_rows.pop(k))
            log("meeting: %s's audio back after %.0fs" % (closed["name"], closed["to"] - closed["from"]))

    def poll_sources(self):
        """The apps switched off for this meeting, as the app says: a span for each, from
        when it went off until it is on again (or the session ends), for the transcript's
        "Zoom was off". True when it changed."""
        try:
            want = json.loads(self.off_ctl.read_text())
        except (OSError, ValueError):
            want = {}
        want = dict((str(k), str(v)) for k, v in want.items()) if isinstance(want, dict) else {}
        if want == self.off:
            return False
        for key, name in want.items():
            if key not in self.off_rows:
                self.off_rows[key] = self.open_row({"kind": "source_off", "id": key, "name": name,
                                                    "from": self.session_time(), "to": None})
                log("meeting: %s off for this meeting" % name)
        for key in [k for k in self.off_rows if k not in want]:
            closed = self.close_row(self.off_rows.pop(key))
            log("meeting: %s on again after %.0fs" % (closed["name"], closed["to"] - closed["from"]))
        self.off = want
        return True

    def voice_span(self, on):
        """Voice mode in a meeting, opened when it starts and closed when it ends (or
        the session does): where the user talked to Claude. A talk is all voice mode:
        none there."""
        if on and self.talked_row is None and self.kind != "talk":
            self.talked_row = self.open_row({"kind": "voice", "from": self.session_time(), "to": None})
        elif not on and self.talked_row is not None:
            self.close_row(self.talked_row)
            self.talked_row = None

    def poll_mic(self, mics):
        """The mic muted for the meeting, or not, as the app says: the mic channels (mics)
        close and open again; the call and the rest go on."""
        try:
            want = self.mic_ctl.read_text().split()[:1] == ["muted"]
        except OSError:
            want = False
        if want == self.muted:
            return
        self.muted = want
        for c in mics:
            c.mute(want)
        if want:
            self.muted_row = self.open_row({"kind": "mic_muted", "from": self.session_time(), "to": None})
            log("mic: muted for the meeting")
        elif self.muted_row is not None:
            closed, self.muted_row = self.close_row(self.muted_row), None
            log("mic: unmuted after %.0fs" % (closed["to"] - closed["from"]))

    def close_all(self):
        """The session ends: every open span ends with it."""
        self.close_pause()
        self.voice_span(False)
        if self.muted_row is not None:
            self.close_row(self.muted_row)
        for row in self.off_rows.values():
            self.close_row(row)
        self.off_rows = {}


class EchoGate(object):
    """On open speakers the mic hears the call. Its lines are held a moment, until
    the meeting apps' channels have heard as far, then kept, or dropped as the laptop's
    sound. Only when that can happen at all: an app being recorded, and the sound able
    to reach the mic (not on headphones: devices.output_leaks).

    channels() are the meeting apps' channels now; emit(...) keeps a line; dictating()
    says a dictation or voice turn is on, which waits less (someone is waiting)."""

    def __init__(self, devs, channels, emit, dictating):
        self.devs, self.channels, self.emit, self.dictating = devs, channels, emit, dictating
        self.pending = deque()        # mic lines being checked
        self.others = deque()         # the meeting apps' lines, the last minute
        self.leak = {"now": True, "at": 0.0}

    def output_leaks(self):
        devs, leak = self.devs, self.leak
        if devs is None or not hasattr(devs, "output_leaks"):
            return True                           # unsure: may (the check drops nothing it is not sure of)
        if time.time() - leak["at"] > 2.0:
            try:
                now_leaks = bool(devs.output_leaks())
            except Exception:
                now_leaks = True
            if now_leaks != leak["now"]:
                log("echo: the Mac's sound %s reach the mic now (%s)" % (
                    "can" if now_leaks else "cannot", "speakers" if now_leaks else "headphones"))
            leak.update(now=now_leaks, at=time.time())
        return leak["now"]

    def active(self):
        return any(ch.state == "live" and ch.cause is None for ch in self.channels()) and self.output_leaks()

    def check(self, force=False):
        """The mic lines held: each, in order, once every app recorded has been heard
        past it (or it has waited long enough: less for dictation and voice turns,
        someone is waiting for those)."""
        import echo as _echo
        chs, emit = self.channels(), self.emit
        live = [ch for ch in chs if ch.state == "live" and ch.cause is None]
        if chs and not live:
            # The meeting's audio paused (the screen asleep, a stall): nothing to compare a
            # held line with for that stretch, so it is kept, as it was said. (An app that
            # stopped is different: its last words were flushed when it closed.)
            while self.pending:
                _q, speaker, start, end, text, spk, audio, ctx, src = self.pending.popleft()
                log("echo: kept, the call's audio is paused, nothing to compare with")
                emit(speaker, start, end, text, spk, audio, ctx=ctx, src=src)
            return
        heard = min([ch.heard_until() for ch in live] or [float("inf")])
        wait_for = 1.2 if self.dictating() else 3.0
        while self.pending:
            queued, speaker, start, end, text, spk, audio, ctx, src = self.pending[0]
            if not force and heard < end + _echo.SLACK and time.time() - queued < wait_for:
                return
            self.pending.popleft()
            near = list(self.others)
            for ch in live:                               # an app's utterance still being spoken
                if ch.partial:
                    near.append((max(0.0, ch.heard_until() - 10.0), ch.heard_until(), ch.partial))
            drop, match, overlap = _echo.judge((start, end, text), near)
            if drop or match >= _echo.NEAR:
                log("echo: %s (match %.2f, overlap %.2f)" % (
                    "DROPPED from the mic, the laptop's sound" if drop else "kept, near", match, overlap))
            if not drop:
                emit(speaker, start, end, text, spk, audio, ctx=ctx, src=src)


def spoken_part(audio, frame=480, floor=UtteranceTranscriber.MIN_RMS):
    """The speech in an utterance, without the quiet around and between its words: its 30 ms
    frames within 20 dB of its loudest, as the recorder's own VAD hears speech above the room
    (UtteranceTranscriber). For a voice print, which the quiet would make the room's."""
    import numpy as np
    a = np.asarray(audio, dtype="float32")
    n = len(a) // frame
    if n == 0:
        return a[:0]
    frames = a[:n * frame].reshape(n, frame)
    rms = np.sqrt((frames.astype("float64") ** 2).mean(axis=1))
    keep = rms >= max(floor, 0.1 * float(rms.max()))
    return frames[keep].reshape(-1)


class VoiceIdentity(object):
    """The user's own voice in a call: learnt from their turns, nothing at all until it
    has heard enough of them (myvoice.TRUST), or with remember_voices off. Once it is
    known on an input, voices not theirs are ignored there.

    mics() and speakers() are the session's mic channels and SpeakerID list now; seq()
    is the dictation's number; talking() says the recorder's own voice is speaking."""

    def __init__(self, sdir, cfg, kind, mics, speakers, seq, talking):
        self.cfg, self.kind, self.mics, self.speakers, self.seq, self.talking = cfg, kind, mics, speakers, seq, talking
        self.made, self.v = False, None
        self.open_ids = {}            # (channel, utterance) -> whose: the utterance still being spoken
        # the user's voice as heard in this call, per input: [sum of prints, seconds]. The same
        # mic, the same room, today: a closer likeness than their lasting print, learnt elsewhere.
        self.here = {}

    def mine(self):
        """The user's own voice print (myvoice.MyVoice), made at first use, or None."""
        if not self.made:
            self.made = True
            if MY_VOICE is not None:
                self.v = MY_VOICE
            elif self.cfg.get("remember_voices", True):
                import myvoice
                self.v = myvoice.MyVoice(ROOT / "voices.json", log=log)
        return self.v

    def input_of(self, speaker):
        """The name of the input a mic channel records from (where the user's voice is known)."""
        return next((c.device_name for c in self.mics() if c.speaker == speaker), "?")

    def ignoring_others(self, speaker):
        """In a call, once the user's voice is known on this input: voices not theirs are ignored."""
        me = self.mine()
        return self.kind == "talk" and me is not None and me.trusted_on(self.input_of(speaker))

    def someone_else(self, audio, speaker):
        """This utterance's print says it's not the user, on an input where that's reliable.
        One cut at MAX_UTT never is: no quiet closed it, so it mixes whatever was said with
        the room's sound (a TV, people nearby), and the user's words may be in it."""
        import myvoice
        if not self.ignoring_others(speaker) or audio is None:
            return False
        if len(audio) / float(SAMPLE_RATE) >= MAX_UTT - BLOCK_SECONDS:
            return False
        speech = spoken_part(audio)            # its words, not the quiet around them
        if len(speech) / float(SAMPLE_RATE) < myvoice.TELL:
            return False
        emb = self.print_of(speech)
        match = self.mine().match(emb)
        if match is None or match >= myvoice.OTHER:
            return False
        # Unlike their lasting print: someone else's only if unlike them as heard in this call too
        here = self.like_here(emb, speaker)
        return here is None or here < myvoice.OTHER

    def like_here(self, emb, speaker):
        """How like the user's voice heard in this call, on this input, a print is (0..1), or
        None before HERE seconds of it."""
        import myvoice
        kept = self.here.get(self.input_of(speaker))
        if emb is None or kept is None or kept[1] < self.HERE:
            return None
        return float(myvoice._unit(emb) @ myvoice._unit(kept[0]))

    HERE = 4.0          # seconds of the user's voice in this call before it is a likeness to go by

    def open_whose(self, c):
        """Whose voice the utterance still being spoken is, once there's enough of it to tell,
        in a call where others are ignored: "you", "other", "unsure", or None (not yet; or not
        asked here). Worked out once per utterance."""
        import myvoice
        if not self.ignoring_others(c.speaker):
            return None
        uid, audio = c.open_audio()
        if audio is None or c.voiced < OPEN_ID_SECS:
            return None
        key = (c.speaker, uid)
        if key not in self.open_ids:
            self.open_ids.clear()
            self.open_ids[key] = myvoice.who(self.mine().match(self.print_of(audio)), len(audio) / float(SAMPLE_RATE)) or "unsure"
        return self.open_ids[key]

    def not_the_user(self, audio):
        """The user's print, once trusted, says this utterance is someone else's."""
        import myvoice
        me = self.mine()
        if me is None or not me.trusted or audio is None or len(audio) / float(SAMPLE_RATE) < myvoice.SHORT:
            return False
        match = me.match(self.print_of(audio))
        return match is not None and match < myvoice.OTHER

    def print_of(self, audio):
        if EMBED is not None:
            return EMBED(audio)
        speakers = self.speakers()
        return speakers[0].embedding(audio) if speakers else embed(audio)

    def learn(self, audio, seconds, speaker):
        """A call's utterance: the user, so their print learns it, and it counts toward
        knowing their voice on this input. Not while Claude speaks (its echo is not them)."""
        me = self.mine()
        if me is None or self.kind != "talk" or audio is None or self.talking():
            return
        emb, length = self.print_of(audio), len(audio) / float(SAMPLE_RATE)
        me.note_input(self.input_of(speaker), me.match(emb), length)     # the user's voice known on this mic
        me.learn(emb, length)
        speech = spoken_part(audio)
        if emb is not None and len(speech) >= SAMPLE_RATE:                # this call's likeness: speech only
            import myvoice
            here = self.print_of(speech)
            if here is not None:
                kept = self.here.setdefault(self.input_of(speaker), [0.0, 0.0])
                kept[0] = kept[0] + myvoice._unit(here) * (len(speech) / float(SAMPLE_RATE))
                kept[1] += len(speech) / float(SAMPLE_RATE)


def run_session(label, no_ship=False, keep_audio=True, mode=None, small=False,
                workspace=None, resume=None, push_lines=None, push_seconds=None,
                no_claude=False, kind=None, attach=None, claude_model=None,
                claude_voice=None):
    cfg = load_config()
    import transport
    pinned, was = cfg.get("mic_device"), cfg.get("mode")
    mic_moved, capture_moved = transport.migrate_mic(cfg), transport.migrate_capture(cfg)
    if mic_moved and pinned:
        log("mic: %r was pinned; Automatic now, following the Mac's mic (once; "
            "a mic picked in Settings from now on stays)" % pinned)
    if capture_moved and cfg.get("mode") != was:
        log("capture: %s -> %s (once: the Mac's sound joins when an app plays)" % (was, cfg["mode"]))
    if mic_moved or capture_moved:
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(CONFIG)
    prior = {}
    if resume:
        # Continue an ended session: same id, same transcript, same agent
        # conversation. Timestamps continue from where it stopped.
        if not (SESSIONS / resume / "meta.json").exists():
            raise SystemExit("no such session to resume: %s" % resume)
        prior = json.loads((SESSIONS / resume / "meta.json").read_text())
        workspace = workspace or prior.get("workspace")
        # BlackHole's "both" and "system" are gone: a session that ran on them goes on Automatic
        mode = mode or {"both": "auto", "system": "auto"}.get(prior.get("mode"), prior.get("mode"))
        attach = attach or prior.get("attach")      # still on the user's conversation
        if isinstance(attach, dict) and (SESSIONS / resume / ATTACH_COPY).exists():
            attach = dict(attach, copy=True)        # or on the copy the app switched it to
    kind = prior.get("kind") or kind or "meeting"
    if kind == "talk":
        # Claude without a meeting: the user's turns, in voice mode, and
        # nothing else. Text only: no audio kept, no speaker labels, and Claude
        # is always on (it is the point).
        label, mode, keep_audio, no_claude = label if resume else "talk", "mic", False, False
    if small:
        cfg["model"] = SMALL_MODEL
    mode = mode or cfg.get("mode", "auto")
    if mode not in MODES:
        raise SystemExit("unknown mode %r, expected one of %s"
                         % (mode, ", ".join(sorted(MODES))))
    session = resume or new_session_id(label)
    sdir = SESSIONS / session
    sdir.mkdir(parents=True, exist_ok=True)
    if kind == "talk":
        import calltrace
        calltrace.source("rec")
        TRACE["sid"] = session
    os.chmod(str(SESSIONS), 0o700)
    os.chmod(str(sdir), 0o700)
    local = sdir / "transcript.jsonl"
    local.touch()

    # On resume, new timestamps continue after the last recorded line.
    t_offset = 0.0
    if resume:
        for line in local.read_text().splitlines():
            try:
                t_offset = max(t_offset, float(json.loads(line).get("end", 0)) + 1.0)
            except ValueError:
                pass
        log("resume: continuing %s from %s" % (session, fmt_offset(t_offset)))
    # Delivery resumes where it stopped. Sessions from before the delivered
    # file existed were delivered in full.
    shipped_already = 0
    if resume:
        delivered = sdir / "delivered"
        shipped_already = (int(delivered.read_text().strip() or 0) if delivered.exists()
                           else local.stat().st_size)
    if no_claude:
        (sdir / "claude.off").write_text("")
    # Dictation: while on, mic speech goes to the app, not the transcript. The
    # app writes `dictate`: "on N" (the chat box's mic), "voice N" (voice mode,
    # listening: speech is gathered into turns), "hold N" (voice mode while
    # Claude thinks and speaks: the mic hears Claude, so speech is dropped),
    # "off N". Rows go to dictation.jsonl tagged with N.
    dict_ctl = sdir / "dictate"
    dict_out = sdir / "dictation.jsonl"
    # Standby Dictation's recordings, to decode with this model (serve_decodes):
    # this user's only, and gone with the session.
    decode_dir = sdir / "decode"
    try:
        decode_dir.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        pass
    try:
        dict_ctl.unlink()
    except OSError:
        pass
    # floor: the hold is Claude speaking (the app's "floor"), not a mute or a dictation elsewhere
    dictation = {"ctl": None, "seq": None, "stop_at": None, "mode": None, "turn": 0, "floor": False}
    voice = {"turns": None, "io": None, "half": False, "why": None, "rebuilt": False,    # half: this voice mode, no engine (why)
             "early_no": [], "early_lost": 0}    # this turn's early decodes that asked nothing, and why
    make_room = room_only()
    room = {"ref": None}                # the Mac's sound, while dictating or in voice mode

    import speechmodel
    speech = speechmodel.for_load(cfg, log)        # the Settings choice, pinned
    model = load_model(speech)
    started = prior.get("started") or time.time()
    lock = threading.Lock()
    fh = open(local, "a", buffering=1)

    def dictation_row(row, seq=None):
        with lock:
            with open(dict_out, "a") as dh:
                dh.write(json.dumps(dict(row, seq=dictation["seq"] if seq is None else seq),
                                    ensure_ascii=False) + "\n")

    ident = VoiceIdentity(sdir, cfg, kind, mics=lambda: mics(), speakers=lambda: _speakers,
                          seq=lambda: dictation["seq"], talking=lambda: getattr(voice["io"], "talking", False))

    def likely_turn():
        """Voice mode: words at a short pause, before their utterance closes,
        that would end the turn if they are the last: Claude may start on them. The
        end_turn that follows says whether they were."""
        for c in mics():
            early = c.take_early()
            if early is None or not c.speaking:
                continue
            if kind == "talk" and ident.someone_else(early[3], c.speaker):
                continue                     # the same ruling its words will get: never asked early for
            t = voice["turns"]
            if t.likely(early[2], early[3]):
                row = {"turn": dictation["turn"], "likely_turn": True, "text": t.heard_with(early[2]),
                       "why": getattr(t, "last_why", None)}
                dictation_row(row)
                log("voice: turn %d likely over (%s)" % (dictation["turn"], getattr(t, "last_why", None)))
                trace(TRACE["sid"], "turn_likely", dictation["turn"], likely=True, why=getattr(t, "last_why", None),
                      p=getattr(t, "last_p", None), text=row["text"])
            else:
                why = getattr(t, "last_why", None) or "no"
                voice["early_no"].append(why)
                log("voice: turn %d not likely over yet (%s)" % (dictation["turn"], why))
                trace(TRACE["sid"], "turn_likely", dictation["turn"], likely=False, why=why, p=getattr(t, "last_p", None))

    def end_turn(text):
        t = voice["turns"]
        quiet = Channel.VOICE_END_SILENCE
        lost = sum(getattr(getattr(c, "_stream", None), "early_lost", 0) for c in mics())
        row = {"turn": dictation["turn"], "end_turn": True, "text": text,
               # counters, numbers only: early decodes that asked nothing (why), and
               # early words an utterance closed over before they were taken
               "early_no": voice["early_no"], "early_lost": lost - voice["early_lost"],
               "p": None if t.last_p is None else round(t.last_p, 3),
               # about when the user stopped speaking, for voice mode's timing
               "spoke_until": round(t.last_closed - quiet, 3) if t.last_closed else None}
        voice["early_no"], voice["early_lost"] = [], lost
        dictation_row(row)
        log("voice: turn %d over%s" % (dictation["turn"], "" if t.last_p is None
                                       else " (finished %.2f)" % t.last_p))
        trace(TRACE["sid"], "turn_end", dictation["turn"], p=row["p"], spoke_until=row["spoke_until"],
              waited_ms=int((time.time() - row["spoke_until"]) * 1000) if row["spoke_until"] else None,
              why=getattr(t, "last_why", None), text=text)
        dictation["turn"] += 1

    def floor_heard():
        """While Claude speaks, are the user's words told from its voice: the engine's echo
        cancellation on the mic, or headphones (its voice can't reach the mic)? Then what they
        say over it is kept, as words: a turn, or a backchannel, never lost.
        Else (half duplex on speakers) the mic hears Claude's own voice, and is dropped as before."""
        vio = voice["io"]
        return bool(vio is not None and getattr(vio, "duplex", False)) or not gate.output_leaks()

    def speech_lost(start, end, why):
        """Words of the user's that reach no one: a span in events.jsonl, for the window's quiet
        "Something you said wasn't heard". Never the words themselves."""
        try:
            events.write(events.read() + [{"kind": "speech_lost", "from": round(start, 2), "to": round(end, 2), "why": why}])
        except Exception as exc:
            log("voice: speech lost, not recorded (%r)" % exc)

    def emit(speaker, start, end, text, spk=None, audio=None, ctx=None, src=None):
        """ctx: where a mic line held for the echo check belonged when it was said
        (dictation's seq, mode and turn): dictation may have ended meanwhile."""
        if ctx is None:
            ctx = (dictation["seq"], dictation["mode"] if dictation["seq"] is not None else None, dictation["turn"])
            if speaker == "Others":
                gate.others.append((start, end, text))
                while gate.others and gate.others[0][1] < start - 60:
                    gate.others.popleft()
            elif speaker in MIC_ROLES and ctx[1] != "hold" and gate.active():
                gate.pending.append((time.time(), speaker, start, end, text, spk, audio, ctx, src))
                return
        seq, mode, turn = ctx
        start, end = start + t_offset, end + t_offset
        if kind == "talk" and not mode:
            log("talk: speech outside a turn, not kept")     # before voice mode is on, or after
            return
        if mode and speaker in MIC_ROLES:
            over = mode == "hold" and dictation["floor"] and floor_heard()
            if mode == "hold" and not over:
                log("voice: dropped while Claude has the floor")
                trace(TRACE["sid"], "heard", turn, text=text, a=round(start, 2), b=round(end, 2), fate="floor")
                if dictation["floor"]:                # the user's words, lost: the window can say so
                    speech_lost(start, end, "floor")
                return
            if over:                                  # said over Claude, its voice taken out: words like any
                mode = "voice"
            if mode == "voice" and kind == "talk" and ident.someone_else(audio, speaker):
                log("voice: someone else heard, ignored")
                trace(TRACE["sid"], "heard", turn, text=text, a=round(start, 2), b=round(end, 2), fate="someone_else")
                return
            if mode == "voice" and kind == "talk" and echoes_voice(text) and ident.not_the_user(audio):
                log("voice: its own words heard back, not a turn")
                trace(TRACE["sid"], "heard", turn, text=text, a=round(start, 2), b=round(end, 2), fate="echo")
                return
            row = {"t": round(start, 2), "end": round(end, 2), "text": text}
            if mode == "voice":
                row["turn"] = turn
                trace(TRACE["sid"], "heard", turn, text=text, a=round(start, 2), b=round(end, 2),
                      fate="over" if over else "kept")
                if over:
                    row["over"] = True                # over Claude: the app judges it (a turn, or "mm-hm")
                else:
                    ident.learn(audio, end - start, speaker)
            dictation_row(row, seq)
            log("%s: speech transcribed" % ("voice" if mode == "voice" else "dictation"))
            if mode == "voice" and voice.get("overlap") is not None:
                voice["overlap"]["said"].append(text)     # said over Claude: what the duck becomes
            if mode == "voice" and seq == dictation["seq"] and voice["turns"] is not None:
                done = voice["turns"].feed(text, audio, time.time())
                if done:
                    end_turn(done)
            return
        row = {"t": round(start, 2), "end": round(end, 2), "speaker": speaker, "text": text}
        if spk is not None:
            row["spk"] = spk
        if src is not None:
            row["src"] = src
        with lock:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        who = ("Speaker %d" % spk) if spk is not None else ("You" if speaker == "Me" else "")
        # The CLI can show live words at a terminal; background stdout is daemon.log.
        if sys.stdout.isatty():
            print("%s %s%s" % (fmt_offset(start), (who + ": ") if who else "", text), flush=True)
        else:
            log("transcript: %.1f-%.1fs transcribed" % (start, end))

    meta = {"session": session, "label": prior.get("label") or label or "meeting",
            "kind": kind, "started": started, "started_iso": iso(started),
            # The Mac's time zone, which is where the user is: the agent may run
            # on a host in another (often UTC) and still says the user's time.
            "utc_offset": time.localtime(started).tm_gmtoff, "tz": time.strftime("%Z", time.localtime(started)),
            "model": speech_name(speech), "mode": mode,
            "speakers": [name for name, _key in MODES[mode]],
            # A path where Claude Code runs; the agent works there if it exists.
            "workspace": workspace,
            # one of the user's own Claude Code conversations to run on, or a copy of
            # it: {"from": its id, "copy": bool, "title"}. The agent resumes it, or forks it.
            "attach": attach,
            # The Claude model its agent was given: its Claude Code runs on that (an agent's own
            # choice, or the one it last ran). Not "model", which is the speech model above.
            "claude_model": claude_model or prior.get("claude_model"),
            # The voice its agent speaks in during a call (an agent's own choice), else Settings'.
            "claude_voice": claude_voice or prior.get("claude_voice"),
            # How often the agent is sent new transcript.
            "push_lines": int(push_lines or prior.get("push_lines")
                              or cfg.get("push_lines") or 10),
            "push_seconds": float(push_seconds or prior.get("push_seconds")
                                  or cfg.get("push_seconds") or 60),
            "host_note": "captured on Mac, transcribed on-device"}
    with open(sdir / "meta.json", "w") as mh:
        json.dump(meta, mh, indent=2)

    shipper = None
    if not no_ship:
        shipper = Shipper(cfg, session, local, sdir=sdir, meta=meta)
        shipper.offset = shipped_already
        shipper.start()

    rotate = float(cfg.get("rotate_minutes", 5)) * 60.0
    channels = []
    _speakers = []
    # where each line came from, kept with the session and shipped like the speaker names.
    sources = Sources(sdir / "sources.json",
                      on_change=(lambda text: shipper.ship_file("sources.json", text)) if shipper else None)
    devs = mic_devices()
    if devs is not None:
        import devices as _dv
        watch = _dv.Watch(devs, time.time())
        log("mic: following the Mac%s" % ("" if watch.listening else " (polled: no Core Audio listener)"))
    else:
        watch = None

    def refresh_portaudio(caller):
        """A mic to open that PortAudio may not know yet: its device list again,
        with the other PortAudio streams closed and opened again around it."""
        others = [c for c in channels if c is not caller and c._input is not None]
        for c in others:
            c._close_input()
        pa_refresh()
        for c in others:
            c.reopen_input()

    def publish_aliases(alias):
        """Two voices turned out to be one person: tell the UI and the agent."""
        data = json.dumps({"alias": {str(k): v for k, v in alias.items()}})
        (sdir / "speakers.auto.json").write_text(data)
        if shipper:
            shipper.ship_file("speakers.auto.json", data)

    def publish_known(data):
        """A voice from an earlier session was recognized: tell the agent."""
        if shipper:
            shipper.ship_file("speakers.known.json", data)

    def publish_me():
        """The name the user's own voice is known by (my_name), as "me" among the voices
        known: their mic's lines (Me) carry it, as a voice heard in the room that is theirs
        does, in the window, the export and what Claude reads. None: "You" for both."""
        path = sdir / "speakers.known.json"
        was = _read_json(path).get("known", {})
        known = dict(was)
        name = my_name() if cfg.get("remember_voices", True) else None
        if name:
            known["me"] = name
        else:
            known.pop("me", None)
        if known != was:
            data = json.dumps({"known": known})
            path.write_text(data)
            publish_known(data)
            log("speakers: the user's own voice is known" if name else "speakers: the user is You")

    try:
        publish_me()
    except Exception as exc:                  # never at the cost of the meeting
        log("speakers: the user's name not known (%r): You" % exc)

    def speaker_ids():
        if _speakers:                   # one for the session: a channel that joins later shares it
            return _speakers[0]
        sid = SpeakerID(sdir / "speakers.state.json", on_change=publish_aliases,
                        on_known=publish_known, remember=cfg.get("remember_voices", True))
        _speakers.append(sid)
        return sid

    # who's speaking from the diarization model, loaded in the background (its first
    # load compiles it for the Neural Engine: 20-55 s); the matcher labels lines meanwhile.
    diar = {"model": None}
    if cfg.get("speakers", True) and cfg.get("diarizer", True) and kind != "talk":
        threading.Thread(target=lambda: diar.update(model=load_diarizer()), name="diarize-load",
                         daemon=True).start()

    def with_voices(ch):
        if ch.speakers is not None:
            ch.diarizer = lambda: diar["model"]
            if ch.speaker in MIC_ROLES:          # a dictation or a voice turn needs no label
                ch.label_wait = lambda: dictation["seq"] is None
        return ch

    def new_wav(speaker, at):
        if not keep_audio:
            return None
        wav = sdir / ("%s.wav" % speaker.lower())
        n = 2
        while wav.exists():
            wav = sdir / ("%s.%d.wav" % (speaker.lower(), n))
            n += 1
        # Where this file starts on the session clock, for the re-pass.
        wav.with_name(wav.name + ".offset").write_text("%.2f\n" % at)
        return wav

    # capture: what says which apps play (each app recorded has its own); follow: which to record
    meeting = {"capture": None, "follow": None}

    def meeting_chs():
        """The meeting apps' channels now: one per app recorded (Others)."""
        return [c for c in channels if c.capture is not None]

    gate = EchoGate(devs, meeting_chs, lambda *a, **k: emit(*a, **k), lambda: dictation["seq"] is not None)
    if mode in MEETING_MODES and kind != "talk":
        cap = meeting_audio()
        if cap is not None:
            meeting.update(capture=cap, follow=MeetingFollow(cap, mode))
            try:
                cap.apps()                  # the first look costs ~60 ms, the next ones ~6: warm it now
            except Exception:
                pass
            log("meeting: recording what the Mac plays, each app playing for 2 s on its own (%s)" % mode)
    from devices import app_key as _dv_key
    for speaker, key in MODES[mode]:
        device = cfg.get(key)               # None: the Mac's own mic, followed
        wav = new_wav(speaker, t_offset)
        # Voice matching for every channel except the user's own mic ("Me"),
        # which is already known to be the user.
        speakers = None
        if speaker != "Me" and cfg.get("speakers", True) and kind != "talk":
            speakers = speaker_ids()
        ch = with_voices(Channel(speaker, device, model, emit, wav, rotate, speakers=speakers))
        ch.base = t_offset                       # where its wav starts on the session clock
        ch.table = sources
        if devs is not None and speaker in MIC_ROLES:
            ch.follow = MicFollow(devs, cfg.get("mic_device") if isinstance(cfg.get("mic_device"), str) else "")
        ch.refresh = refresh_portaudio
        ch.open()
        channels.append(ch)

    clock_zero = time.time()            # the session clock's 0 for this run (t_offset on it)
    if not channels and meeting["capture"] is None:
        raise SystemExit("no capture device for mode %r%s" % (
            mode, ": meeting audio is not available on this Mac" if mode == "meeting" else " - run: standby setup"))

    broken = [c for c in channels if c.error is not None]
    for c in broken:
        log("CAPTURE FAILED for %s: %s" % (c.speaker, c.error))
    if channels and len(broken) == len(channels):
        if shipper:
            shipper.finish("\n".join("%s channel:\n%s" % (c.speaker, c.traceback)
                                     for c in broken))
        raise SystemExit("standby: no channel could open. See the reason above. "
                         "Try: standby devices   then   standby setup --mode mic")

    print("")
    print("  Standby is live. Session: %s" % session)
    print("  Mode: %s  (channels: %s)"
          % (mode, ", ".join(c.speaker for c in channels)))
    print("  Workspace: %s" % (workspace or "(none: skills and notes only)"))
    print("  AI update: every %d sentences or %.0fs" % (meta["push_lines"], meta["push_seconds"]))
    print("  Stop with:         standby stop   (or Ctrl-C here)")
    print("", flush=True)
    (ROOT / "daemon.ready").write_text(session + "\n")
    # Each recording run (first start, then every Resume), for the app's dividers.
    runs = sdir / "runs.jsonl"
    with open(runs, "a") as rh:
        rh.write(json.dumps({"start": clock_zero, "t": round(t_offset, 2)}) + "\n")    # the session clock's 0

    def mics():
        return [c for c in channels if c.speaker in MIC_ROLES]

    asks = CaptureAsks(sdir, mics, lambda: meeting["follow"], watch,
                       on_answer=lambda: meeting_next.__setitem__(0, 0.0))     # act on it now, not at the next look

    def update_meta():
        meta["speakers"] = [c.speaker for c in channels]
        copy_now(sdir, meta)
        with open(sdir / "meta.json", "w") as mh:
            json.dump(meta, mh, indent=2)
        if shipper:
            shipper.meta = meta
            shipper.ship_file("meta.json", json.dumps(meta))

    def join_meeting(app, why):
        """An app plays: its audio joins as Others, on its own capture, from now on the
        session clock; the others recorded go on untouched. The mic is then the user's
        own voice (Me)."""
        cap = meeting_audio()
        if cap is None:
            return
        at = time.time() - clock_zero
        ch = with_voices(Channel("Others", None, model, emit, new_wav("Others", t_offset + at), rotate,
                                 speakers=speaker_ids() if cfg.get("speakers", True) else None))
        ch.capture, ch.app, ch.offset, ch.refresh = cap, app, at, refresh_portaudio
        ch.zero, ch.base, ch.table = clock_zero, t_offset, sources
        ch.open()
        channels.append(ch)
        for c in mics():
            if c.speaker == "Room":
                c.flush()
                c.drop_voices("the mic is the user's own now")
                c.speaker, c.speakers = "Me", None
        update_meta()
        log("meeting: %s joins as Others at %s (%s)" % (ch.device_name, fmt_offset(t_offset + at), why))

    events = Events(sdir, kind, lambda: t_offset + time.time() - clock_zero, shipper)

    def leave_meeting(ch, why):
        events.close_pause(_dv_key(ch.app))
        ch.close()
        channels.remove(ch)
        update_meta()
        log("meeting: %s's audio stops (%s)" % (ch.device_name, why))

    def follow_meeting():
        """Every app playing is recorded, each on its own channel, but those switched off
        for this meeting (events.off). One that stops playing, closes or is switched off
        leaves, and the others go on."""
        import devices as dv
        chs = meeting_chs()
        want = meeting["follow"].decide_all([_dv_key(c.app) for c in chs], events.off)
        keys, running = set(_dv_key(a) for a in want), set(_dv_key(a) for a in meeting["follow"].seen)
        for ch in chs:
            key = _dv_key(ch.app)
            if key not in keys:
                leave_meeting(ch, "off for this meeting" if key in events.off else
                              "it stopped playing" if key in running else "it closed")
        have = set(_dv_key(c.app) for c in meeting_chs())
        for a in want:
            if _dv_key(a) not in have:
                join_meeting(a, "%s is playing%s" % (dv._name(a), " (a call)" if dv.confidence(a) >= dv.CAPTURE_AT else ""))

    def hearing():
        """The live meeting's sources, each with its switch (the window's list): the user's
        mic, whose switch is its mute; each app recorded now; and each app switched off for
        this meeting, so it can come back on. heard: recorded, and speaking or playing now."""
        out, ms = [], mics()
        if ms:
            out.append({"id": "mic", "kind": "mic", "name": "Your mic", "detail": ms[0].device_name,
                        "on": not events.muted, "heard": not events.muted and any(c.speaking for c in ms)})
        playing = set(_dv_key(a) for a in (meeting["follow"].seen if meeting["follow"] is not None else [])
                      if a.get("playing"))
        for c in meeting_chs():
            key = _dv_key(c.app)
            out.append({"id": key, "kind": "app", "name": c.device_name, "detail": "", "on": True,
                        "heard": c.state == "live" and c.cause is None and key in playing})
        for key, name in events.off.items():
            out.append({"id": key, "kind": "app", "name": name, "detail": "", "on": False, "heard": False})
        return out

    def output_uid():
        """The Mac's output now (its canceller learns the way from these speakers to the mic)."""
        try:
            dev = devs.default_output() if devs is not None and hasattr(devs, "default_output") else None
            return dev.uid if dev is not None else "default"
        except Exception:
            return "default"

    # The apps switched off for the meeting, on open speakers: each still heard on a capture
    # of its own, never transcribed or kept, only so its sound is taken back out of the mic
    # (roomonly), and off holds in the room too. On headphones there is none to take out.
    off_echo = {"mixed": None, "refs": {}, "mics": set(), "at": {}, "names": {}}    # at: when each was started

    def sync_off_echo():
        import roomonly
        refs = off_echo["refs"]
        want = set(events.off) if events.off and gate.output_leaks() else set()
        playing = set(_dv_key(a) for a in (meeting["follow"].seen if meeting["follow"] is not None else [])
                      if a.get("playing"))
        for key in [k for k in refs if refs[k] is not None and refs[k].started
                    and not getattr(refs[k].capture, "ok", False) and k in playing
                    and time.time() - off_echo["at"].get(k, 0.0) > 5.0]:
            refs.pop(key).stop()                # it closed, or the screen slept, and plays again: heard again
        for key in [k for k in refs if k not in want]:
            ref = refs.pop(key)
            if ref is not None:
                ref.stop()
                log("meeting: %s no longer taken out of the mic" % off_echo["names"].get(key, key))
        for key in sorted(want - set(refs)):
            cap = meeting_audio()               # None: it can't be heard, the mic as it is (said once)
            refs[key] = roomonly.Reference(cap, log=log, target=key).start() if cap is not None else None
            off_echo["at"][key], off_echo["names"][key] = time.time(), events.off[key]
            if cap is not None:
                log("meeting: %s off, its sound taken out of the mic (heard, never kept)" % events.off[key])
        if off_echo["mixed"] is None:
            off_echo["mixed"] = roomonly.Mixed()
        off_echo["mixed"].refs = dict((k, r) for k, r in refs.items() if r is not None)
        # Dictation's own room, while it is on, takes out all the Mac plays: these with it. So
        # does voice mode's engine (the mic is its, echo-cancelled): nothing added to its latency.
        on = bool(off_echo["mixed"].refs) and room["ref"] is None and voice["io"] is None
        for c in mics():
            if on and id(c) not in off_echo["mics"]:
                c.room_off()
                c.room_on(roomonly.RoomOnly(off_echo["mixed"], roomonly.canceller_for((output_uid(), c.speaker)), log=log))
                off_echo["mics"].add(id(c))
            elif not on and id(c) in off_echo["mics"]:
                if room["ref"] is None:
                    c.room_off()
                off_echo["mics"].discard(id(c))

    def room_start():
        """Dictation or voice mode begins: the Mac's own sound is taken out of each
        mic, on speakers only (on headphones there is none to take out). Decided now, and
        kept until this dictation ends. Returns at once: its first moment passes as it is."""
        if make_room is None or room["ref"] is not None or not gate.output_leaks():
            return
        import roomonly
        output = output_uid()
        try:
            ref = make_room(output)
        except Exception as exc:
            log("room only: %r: the mic as it is" % exc)
            ref = None
        if ref is None:
            return
        room["ref"] = ref
        off_echo["mics"].clear()                 # the apps switched off are in all the Mac plays
        for c in mics():
            c.room_off()
            c.room_on(roomonly.RoomOnly(ref, roomonly.canceller_for((output, c.speaker)), log=log))
        log("room only: on, the Mac's own sound taken out of the mic (%s)" % output)

    def room_stop():
        """The mic as it is again, and the Mac's sound no longer heard."""
        ref, room["ref"] = room["ref"], None
        for c in mics():
            c.room_off()
        if ref is not None:
            ref.stop()

    def end_dictation():
        if dictation["mode"] in ("voice", "hold"):
            dictation["mode"] = "hold"      # words cut off by Stop are not a question
        if voice["io"] is not None:
            voice["io"].stop()
            voice["io"] = None
        room_stop()
        for c in mics():
            c.flush()
            c.voice_mode(False)
        events.voice_span(False)              # the record is whole before the app hears "done"
        dictation_row({"done": True})
        log("dictation %s: off" % dictation["seq"])
        dictation.update(seq=None, stop_at=None, mode=None)
        voice["half"], voice["why"], voice["rebuilt"] = False, None, False    # the next voice mode tries again
        if voice["turns"] is not None:
            voice["turns"].reset()

    def switch(mode):
        """Voice mode between listening and holding. An utterance open across the
        switch is Claude's own voice from the speakers, or words from before a
        mute: it is dropped. A turn the user started before the hold keeps its
        words (Turns.resume), and closes as any turn does from here: words the user
        said are never dropped, and voice mode asks them as it said it would."""
        if dictation["mode"] == "voice":
            for c in mics():
                c.flush()           # Claude takes the floor: what they were saying began before it, theirs
        dictation["mode"] = "hold"
        events.voice_span(True)
        for c in mics():
            c.flush()
            c.voice_mode(mode == "voice")
        if mode == "voice":
            if voice["turns"] is None:
                import turn
                voice["turns"] = turn.Turns(turn.load(log=log))
                import speechvad
                Channel.speech_vad = speechvad.load(log=log)
            voice["turns"].resume(time.time())
        # Settings > Voice, "Let me talk over Claude", read at each voice mode: a change
        # applies from the next one, not mid-conversation.
        if voice["io"] is None and not voice["half"]:
            open_voice_io()
        dictation["mode"] = mode
        log("voice %s: %s" % (dictation["seq"], "listening" if mode == "voice" else "holding"))

    def open_voice_io(done=0, retrying=None):
        """The call's voice, played here: the one voice of a call (never the app's, nor the
        Mac's instead). The mic through echo cancellation when it can; else the voice on an
        engine of its own. False if nothing can play it: the app shows why, and the answers.
        done: the sentences an engine before this one finished, or dropped."""
        make = voice_backend(meta.get("claude_voice"))
        if make is None:
            voice["half"], voice["why"] = True, "no audio engine here"
            log("voice %s: no audio engine: Claude's voice can't play" % dictation["seq"])
            return False
        talk_over = bool((load_config().get("voice_mode") or {}).get("duplex", False))
        try:
            voice["io"] = VoiceIO(sdir, make(), log=log, talk_over=talk_over, fixed=meta.get("claude_voice"))
            voice["io"].done = done
            if retrying is not None:                 # made again after the last one stopped: coming back
                voice["io"].backend.retrying_since = retrying
            voice["io"].start(dictation["seq"], mics())
        except Exception as exc:
            log("voice: its audio engine did not start (%r)" % exc)
            voice["io"], voice["half"], voice["why"] = None, True, "its audio engine did not start: %s" % exc
            return False
        if not voice["io"].plays:
            voice["io"].stop()
            voice["io"], voice["half"] = None, True
            voice["why"] = next((c.borrow_error for c in mics() if getattr(c, "borrow_error", None)),
                                "its audio engine could not play")
            log("voice %s: Claude's voice can't play (%s)" % (dictation["seq"], voice["why"]))
            return False
        return True

    def retire_voice_io():
        """An engine that stopped working mid voice mode (VoiceIO.fail): its mic is given back
        here, on the main thread, and it is made again, once; failing again, the voice is off
        for this voice mode, and the app says so."""
        vio = voice["io"]
        if vio is not None and vio.failed:
            vio.stop()
            voice["io"] = None
            if not voice["rebuilt"]:
                voice["rebuilt"] = True
                log("voice %s: its voice stopped working (%s): its engine made again" % (dictation["seq"], vio.failed))
                if open_voice_io(vio.done, retrying=time.time()):
                    return
            voice["half"], voice["why"] = True, "its voice stopped working: %s" % vio.failed

    def barge_in():
        """Talking over Claude, heard through the echo cancellation. Only while
        Claude speaks: thinking, muted or paused, nothing is played to talk over.

        The voice ducks (quiet, then paused, kept where it was) and listens: the
        words decide (overlap.decide). A turn, and it stops: the app is told, and
        what is being said is the next turn (the open utterance is kept). "Mm-hm",
        "um", a cough, and it goes on from where it paused, as if nothing was said.
        An engine that can't pause stops at once, as before."""
        vio = voice["io"]
        if voice.get("overlap") is not None:
            return overlap_step(vio)
        if (dictation["mode"] != "hold" or vio is None or not vio.duplex or not vio.talking
                or not any(c.onset >= VoiceIO.BARGE_MIN for c in mics())):
            return
        over = [c for c in mics() if c.onset >= VoiceIO.BARGE_MIN]
        if vio.learning and not any(ident.open_whose(c) == "you" or (ident.open_whose(c) != "other"
                                                                      and c.onset >= VoiceIO.BARGE_LEARNING) for c in over):
            return                           # its own echo, maybe: not yet
        if any(ident.ignoring_others(c.speaker) for c in over):
            whose_now = [ident.open_whose(c) for c in over]
            # Not yet told apart (None): it ducks now, and the words decide (overlap_step); only
            # a voice known to be someone else's leaves Claude going. A short "Yes." closed before
            # its voice could be told, so it never ducked, and was lost (talk-20261024-232831).
            if whose_now and all(w == "other" for w in whose_now):
                if "other" in whose_now and not voice.get("other_over"):
                    voice["other_over"] = True
                    log("voice %s: someone else talked over Claude: it carries on" % dictation["seq"])
                return                       # someone else's voice: Claude goes on
            voice["other_over"] = False
        if not vio.can_duck:
            return give_way(vio, "talked over")
        vio.duck()
        t = voice["turns"]
        voice["overlap"] = {"at": time.time(), "turn": dictation["turn"], "said": [], "voiced": 0.0,
                            "open": {c.speaker: (c.open_id, c.voiced) for c in mics()},
                            "kept": (len(t.texts), len(t.audio)) if t is not None else None}
        dictation_row({"overlap": "duck", "turn": dictation["turn"]})
        for c in mics():
            c.voice_mode(True)               # its words are heard now, and sooner (OVERLAP_PARTIAL)
            c.overlap_mode(True)
        dictation["mode"] = "voice"
        log("voice %s: talked over Claude: it ducks, and listens" % dictation["seq"])

    def overlap_step(vio):
        """A duck in progress (barge_in): more of what is said, and what it means."""
        import interrupt
        import overlap
        ov, now = voice["overlap"], time.time()
        if vio is None or vio.ducked is None:            # the app stopped it meanwhile (its words came first)
            voice["overlap"] = None
            for c in mics():
                c.overlap_mode(False)
            if voice["turns"] is not None:
                voice["turns"].resume(now)
            log("voice %s: talked over Claude: the app stopped it" % dictation["seq"])
            return
        speaking, words = False, []
        for c in mics():
            uid, was = ov["open"].get(c.speaker, (None, 0.0))
            if c.speaking:
                ov["voiced"] += max(0.0, c.voiced - (was if uid == c.open_id else 0.0))
                ov["open"][c.speaker] = (c.open_id, c.voiced)
                pause = c.paused_words()                  # its words at a short pause, before it closes
                speaking = speaking or pause is None
                words.append(pause if pause is not None else c.partial)
        text = " ".join([w for w in ov["said"] + words if w]).strip()
        since = now - ov["at"]
        echo = len(_words(text)) >= 2 and echoes_voice(text) and interrupt.classify(text) not in (interrupt.CANCEL, interrupt.WAIT)
        if echo:                                         # the voice's own words, heard back past the echo cancelling
            text, ov["echo"] = "", True
        whose = [ident.open_whose(c) for c in mics() if c.speaking and ident.ignoring_others(c.speaker)]
        try:
            if whose and all(w == "other" for w in whose):
                act, why = overlap.RESUME, "someone else"    # ducked before the voice was told apart
            else:
                act, why = overlap.decide(text, since, ov["voiced"], speaking)
                if ov.get("echo") and act == overlap.RESUME:
                    why = "its own words"
        except Exception as exc:                         # never left ducked (VoiceIO.DUCK_MAX is the last resort)
            act, why = overlap.RESUME, "could not decide: %r" % exc
        if act is None:
            return
        voice["overlap"] = None
        for c in mics():
            c.overlap_mode(False)
        log("voice %s: talked over for %.2fs, %.2fs of speech: %s (%s)" % (
            dictation["seq"], since, ov["voiced"], "it stops" if act == overlap.COMMIT else "it goes on", why))
        if act == overlap.COMMIT:
            return give_way(vio, why, ov["turn"], since)
        vio.unduck()
        dictation_row({"overlap": "resume", "turn": ov["turn"], "why": why, "after": round(since, 2)})
        dictation["mode"] = "hold"
        for c in mics():
            c.flush()                                     # in a hold: an "mm-hm" still open is dropped
            c.voice_mode(False)
        t = voice["turns"]
        if t is not None and ov["kept"] is not None:      # a closed "mm-hm" is not the start of their next turn
            del t.texts[ov["kept"][0]:]
            del t.audio[ov["kept"][1]:]
            if not t.texts:
                t.due = None

    def give_way(vio, why, turn=None, after=None):
        """Claude stops for the user: the app is told, and what is being said is the
        next turn (the open utterance is kept, unlike a switch)."""
        vio.halt()
        row = {"barge_in": True, "turn": dictation["turn"] if turn is None else turn, "why": why}
        if after is not None:
            row["after"] = round(after, 2)
        dictation_row(row)
        for c in mics():
            c.voice_mode(True)
        if voice["turns"] is not None:
            voice["turns"].resume(time.time())      # what they said before Claude spoke goes with it
        dictation["mode"] = "voice"
        log("voice %s: talked over Claude: it stops, and this is the next turn" % dictation["seq"])

    lent = {"ch": None, "fh": None}           # the call's mic, lent to Standby Dictation (follow_dictating)
    lent_path = sdir / "dictating.f32"

    def follow_dictating():
        """Standby Dictation during a call: while voice mode's engine has the mic, a second
        reader of it gets near silence (6 of 6 dictations in a call heard nothing, the mic
        "3ch"; 316 of 324 heard fine at "1ch"). So while it says it dictates (the session's
        `dictating` file, MeetingGuard's), the call's own mic, echo cancelled, from its
        last second on, goes to dictating.f32, which it reads instead. This user's only;
        gone when it is done."""
        want = dictation["mode"] in ("voice", "hold") and (sdir / "dictating").exists()
        src = next((c for c in mics() if c.borrowed is not None), None) if want else None
        if src is not None and lent["ch"] is None:
            try:
                fh = os.fdopen(os.open(str(lent_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb", buffering=0)
            except OSError as exc:
                log("dictation: the call's mic not lent (%r): it opens the mic itself" % exc)
                return
            src.tap_to(fh)
            lent.update(ch=src, fh=fh)
            log("dictation: during voice mode, from the call's own mic (echo cancelled)")
        elif src is None and lent["ch"] is not None:
            end_lending()

    def end_lending():
        ch, fh = lent["ch"], lent["fh"]
        lent.update(ch=None, fh=None)
        if ch is not None:
            ch.tap_to(None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        try:
            lent_path.unlink()
        except OSError:
            pass

    end_lending()                             # one a crash left behind

    def poll_dictation():
        try:
            ctl = dict_ctl.read_text().split()
        except OSError:
            ctl = []
        if ctl != dictation["ctl"]:
            dictation["ctl"] = ctl
            mode = {"on": "dictate", "voice": "voice", "hold": "hold", "floor": "hold"}.get(ctl[0] if ctl else None)
            dictation["floor"] = bool(ctl) and ctl[0] == "floor"
            if len(ctl) == 2 and mode:
                if dictation["seq"] is None:
                    for c in mics():        # speech so far belongs to the meeting
                        c.flush()
                    dictation.update(seq=int(ctl[1]), stop_at=None, mode="dictate", turn=0)
                    log("dictation %s: on" % ctl[1])
                    room_start()
                if int(ctl[1]) == dictation["seq"] and mode != dictation["mode"] and mode != "dictate":
                    switch(mode)
            elif len(ctl) == 2 and ctl[0] == "off":
                if dictation["seq"] is None:
                    dictation["seq"] = int(ctl[1])
                    end_dictation()
                elif dictation["mode"] == "dictate":
                    # Let the last words come through the audio buffer first.
                    dictation["stop_at"] = time.time() + BLOCK_SECONDS + 0.2
                else:
                    end_dictation()
        if dictation["stop_at"] and time.time() >= dictation["stop_at"]:
            end_dictation()
        barge_in()
        if dictation["mode"] == "voice" and dictation["seq"] is not None:
            likely_turn()
            done = voice["turns"].tick(time.time(), any(c.speaking for c in mics()))
            if done:
                end_turn(done)

    status_path = sdir / "live.json"
    last_status = [0.0]

    def write_status():
        """What the recorder hears right now, for the app's listening bar."""
        talking = [c for c in channels if c.speaking]
        # In a call where other voices are ignored, an utterance's words show once it's told to be
        # the user's (or can't be told): never someone else's.
        talking = [c for c in talking if not ident.ignoring_others(c.speaker) or ident.open_whose(c) in ("you", "unsure", "short")]
        partial = next((c for c in talking if c.partial), None)
        st = {"at": time.time(), "level": round(max([c.level for c in channels] or [0.0]), 4),
              "speaking": bool(talking), "partial": partial.partial if partial else "",
              "dictating": dictation["seq"] is not None, "sources": [c.source() for c in channels],
              "mic_muted": events.muted, "decodes": True}         # Standby Dictation's too
        if kind != "talk":
            st["hearing"] = hearing()
        if meeting["capture"] is not None and not meeting_chs():     # no app playing yet
            st["sources"].append({"kind": "meeting", "name": "", "level": 0.0, "ok": False, "state": "waiting"})
        if dictation["mode"] in ("voice", "hold"):
            st["voice"] = {"mode": dictation["mode"], "turn": dictation["turn"],
                           "heard": voice["turns"].heard if voice["turns"] else "",
                           # the user's mics only: a meeting's voices are not the user going on
                           "you_speaking": any(c.speaking and (not ident.ignoring_others(c.speaker) or ident.open_whose(c) != "other")
                                               for c in mics())}
            if voice["io"] is not None:               # this recorder plays Claude's voice (VoiceIO)
                st["voice"].update(voice["io"].status())
            elif voice["half"]:                       # it could not, or stopped: the app's voice speaks
                st["voice"]["half"] = True
                st["voice"]["why"] = voice["why"]         # for the app's log
        for c in channels:
            c.level = 0.0
        tmp = status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False))
        tmp.replace(status_path)

    error_text = None
    meeting_next = [0.0]
    last_beat = time.time()
    try:
        while not _stop.is_set():
            for ch in channels:
                ch.pump()
            now = time.time()
            if watch is not None and watch.due(now):         # the Mac's mics changed, or a look anyway
                for ch in mics():
                    if ch.follow is None or ch.error is not None or ch.state == "muted":
                        continue
                    if ch.state == "reconnecting":
                        ch.reconnect()
                        continue
                    mic, why = ch.follow.decide(ch.mic)
                    if mic is not None and mic != ch.mic:
                        ch.switch(mic, why)
            if events.poll_sources():
                meeting_next[0] = 0.0             # an app switched off or on: now, not at the next look
            if meeting["follow"] is not None and now >= meeting_next[0]:
                meeting_next[0] = now + 1.0
                follow_meeting()
            for ch in list(channels):
                if ch.due(now):
                    ch.reconnect()
            if meeting_chs() or events.pause_rows:
                events.watch_pause(meeting_chs())
            if gate.pending:
                gate.check(force=not meeting_chs())     # the call gone: decide with what there is
            asks.update()
            events.poll_mic(mics())
            sync_off_echo()
            retire_voice_io()
            poll_dictation()
            follow_dictating()
            serve_decodes(decode_dir, model)
            # Voice mode's orb and captions follow the mic closely.
            if time.time() - last_status[0] >= (0.1 if dictation["mode"] in ("voice", "hold") else 0.5):
                last_status[0] = time.time()
                write_status()
            if time.time() - last_beat >= HEARTBEAT:
                last_beat = time.time()
                parts = []
                for c in channels:
                    if c.error is not None:
                        parts.append("%s=DEAD(%s)" % (c.speaker, c.error))
                    elif c.state != "live":
                        parts.append("%s: %s, %s" % (c.speaker, c.state.upper(), c.device_name))
                    else:
                        level = ("SILENT on %r" % c.device_name) if c.peak < 1e-3 \
                            else "peak %.3f" % c.peak
                        if c.peak == 0.0 and c.blocks >= 10:
                            # Exact zeros, not just quiet: macOS returns digital
                            # silence when this process lacks mic permission.
                            level = ("SILENT (exact zeros: macOS is probably DENYING "
                                     "microphone access to this process; check System "
                                     "Settings > Privacy & Security > Microphone)")
                        parts.append("%s: %.0fs audio, %s, %d lines"
                                     % (c.speaker, c.pumped / float(SAMPLE_RATE),
                                        level, c.lines))
                        c.peak = 0.0
                ship = ""
                if shipper and not shipper.enabled:
                    ship = " | Standby's agent off, transcript in the session folder"
                elif shipper:
                    ship = " | shipped %d bytes%s" % (
                        shipper.offset, "" if shipper.connected else " (delivery failing, retrying)")
                log("heartbeat %s%s" % ("; ".join(parts), ship))
                if shipper:
                    shipper.ship_log()
            if channels and all(c.error is not None for c in channels):
                log("STOPPING: all channels failed")
                break
            # In voice mode the engine's speech arrives on this thread's run loop: wait there.
            idle = getattr(voice["io"].backend, "idle", None) if voice["io"] is not None else None
            (idle or time.sleep)(0.1)
    except KeyboardInterrupt:
        log("STOPPING: KeyboardInterrupt")
    except Exception:
        import traceback
        error_text = traceback.format_exc()
        log("STOPPING: main loop crashed\n" + error_text)
    finally:
        _stop.set()
        try:
            (ROOT / "daemon.ready").unlink()
        except OSError:
            pass
        try:
            end_lending()
            events.close_all()                    # a pause, voice mode or a mute: its span ends with the session
            for ref in off_echo["refs"].values():
                if ref is not None:
                    ref.stop()
            for ch in channels:                       # the last words in, then the held mic lines decided
                ch.pump()
            gate.check(force=True)
        except Exception:
            pass
        for ch in channels:
            ch.close()
        if dictation["seq"] is not None:
            end_dictation()
        try:
            # by samples: a meeting app's blocks are 20 ms, a mic's BLOCK_SECONDS
            recorded = max([c.pumped for c in channels] or [0]) / float(SAMPLE_RATE)
            with open(runs, "a") as rh:
                rh.write(json.dumps({"end": time.time(), "t": round(t_offset + recorded, 2)}) + "\n")
        except Exception:
            pass
        for path in (dict_ctl, status_path, asks.ask_path, asks.answer_path):   # no question outlives its session
            try:
                path.unlink()
            except OSError:
                pass
        import shutil
        shutil.rmtree(str(decode_dir), ignore_errors=True)        # nor a dictation's audio
        fh.close()
        for sp in _speakers:
            sp.save()
        failures = ["%s channel:\n%s" % (c.speaker, c.traceback)
                    for c in channels if c.traceback]
        if error_text:
            failures.insert(0, "main loop:\n%s" % error_text)
        log("session %s ended" % session)
        if shipper:
            shipper.join(timeout=30)
            shipper.ship_log()
            shipper.finish("\n".join(failures) or None)
    log("session %s ended (%s)" % (session, sdir))
    return session


def fmt_offset(seconds):
    seconds = int(seconds)
    return "%02d:%02d" % (seconds // 60, seconds % 60)


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


# --------------------------------------------------------------------------
# setup / devices / selftest
# --------------------------------------------------------------------------

def cmd_devices(_args):
    import sounddevice as sd
    print("%-4s %-42s %s" % ("idx", "name", "in/out channels"))
    for idx, dev in enumerate(sd.query_devices()):
        print("%-4d %-42s %d/%d" % (idx, dev["name"][:42],
                                    dev["max_input_channels"],
                                    dev["max_output_channels"]))


def cmd_setup(args):
    import sounddevice as sd
    ROOT.mkdir(parents=True, exist_ok=True)
    devices = sd.query_devices()
    existing = {}
    if CONFIG.exists():
        with open(CONFIG) as fh:
            existing = json.load(fh)

    mode = args.mode
    # As Settings > Recording: Automatic is the default.
    if not mode:
        print("Record:")
        print("  1) Automatic           your mic, and the sound your Mac plays (a call, a")
        print("                         video) whenever something plays (recommended). No setup.")
        print("  2) Mic only            only your mic. With headphones on, the other side")
        print("                         is not recorded.")
        print("  3) Meeting audio only  only the sound your Mac plays, without your mic.")
        choice = input("Choose [1]: ").strip() or "1"
        mode = {"1": "auto", "2": "mic", "3": "meeting"}.get(choice, "auto")
    print("")
    print("record: %s" % mode)

    cfg = dict(existing)
    cfg["mode"] = mode
    cfg["capture_automatic"] = cfg["mic_automatic"] = True     # chosen here: never migrated
    needed = set(key for _name, key in MODES[mode])

    if "mic_device" in needed:
        # Automatic follows the Mac (AirPods in, AirPods out): only a mic picked here is pinned.
        print("")
        print("Microphone: Enter for Automatic (follows your Mac), or pick one:")
        inputs = [(i, d["name"]) for i, d in enumerate(devices)        # not Zoom's, Teams' or BlackHole: never a mic
                  if d["max_input_channels"] > 0 and not _is_virtual(d["name"])]
        for idx, name in inputs:
            print("  %-4d %s" % (idx, name))
        raw = input("Microphone [Automatic]: ").strip()
        if raw:
            cfg["mic_device"] = devices[int(raw)]["name"]
        else:
            cfg.pop("mic_device", None)
    cfg.pop("loopback_device", None)

    import transport
    cfg["agent"] = transport.agent_settings(existing)       # Claude Code runs on this Mac
    for legacy in ("host", "remote_dir"):
        cfg.pop(legacy, None)
    cfg.setdefault("rotate_minutes", 5)
    with open(CONFIG, "w") as fh:
        json.dump(cfg, fh, indent=2)
    print("")
    print("wrote %s" % CONFIG)
    print("Now run: standby selftest")


NO_CALL = "Nothing is playing to test. Start a call or a video, and test again."


def selftest_meeting(seconds, model, out=print):
    """Settings' Test recording, the meeting's half: the call that would be
    recorded, for the same few seconds, transcribed as the mic is. Printed in
    the mic channel's format, so the page shows it ("Others" is "Meeting
    audio" there): no call playing, or no permission, is one problem line."""
    import numpy as np
    import devices as dv
    cap = meeting_audio()
    head = lambda name: out("--- Others (device %s): recording %ds, the call's sound ---" % (name, seconds))
    out("")
    if cap is None:
        head("")
        out("  peak level: 0.0000")
        out("  Meeting audio isn't available on this Mac.")
        return
    follow = MeetingFollow(cap, "auto")
    try:
        apps = cap.apps()
    except Exception:
        apps = []
    try:
        choices = json.loads(Path(follow.choices_path).read_text()).get("apps") or {}
    except (OSError, ValueError, AttributeError):
        choices = {}
    # As recording would choose, but with no 2 s wait: the user started the call to test it.
    app, _why, _ask = dv.choose_meeting(apps, "auto", choices, None)
    if app is None:
        head("")
        out("  peak level: 0.0000")
        out("  " + NO_CALL)
        return
    name = app.get("name") or app.get("app") or "the meeting app"
    head(name)
    blocks = []
    cap.start(app.get("bundle") or app.get("pid"), lambda block, _t=None: blocks.append(np.asarray(block, "float32")))
    if getattr(cap, "error", None):
        out("  peak level: 0.0000")
        out("  " + (getattr(cap, "reason", None) or cap.error))
        return
    end = time.time() + seconds
    while time.time() < end and not getattr(cap, "error", None):
        time.sleep(0.05)
    cap.stop()
    audio = np.concatenate(blocks) if blocks else np.zeros(0, "float32")
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    out("  peak level: %.4f" % peak)
    if getattr(cap, "error", None) and not len(audio):
        out("  " + (getattr(cap, "reason", None) or cap.error))
        return
    if peak < 1e-4:
        out("  SILENCE from %s: nobody spoke in the call during the test." % name)
        return
    stream = UtteranceTranscriber(model)
    stream.feed(audio)
    got = stream.drain(force=True)
    for start, end_, text, *_ in got or []:
        out("  %s  %s" % (fmt_offset(start), text))
    if not got:
        out("  audio from %s but nothing transcribed (too quiet or no speech)" % name)


def cmd_selftest(args):
    import numpy as np
    import sounddevice as sd
    cfg = load_config()
    asked = mode = args.mode or cfg.get("mode", "auto")
    seconds = args.seconds
    import speechmodel
    model = load_model(speechmodel.for_load(cfg, print))
    print("mode: %s" % mode)
    for speaker, key in MODES[mode]:
        device = cfg.get(key)
        print("")
        print("--- %s (device %s): recording %ds, speak or play audio now ---"
              % (speaker, device, seconds))
        try:
            device = resolve_device(device, speaker)
            rec = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                         channels=1, dtype="float32", device=device)
            sd.wait()
        except Exception as exc:
            print("  capture FAILED: %r" % exc)
            continue
        audio = rec[:, 0]
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        print("  peak level: %.4f" % peak)
        if peak < 1e-4:
            print("  SILENCE on the mic. Check System Settings > Privacy &")
            print("  Security > Microphone, and that the right input is picked.")
            continue
        stream = UtteranceTranscriber(model)
        stream.feed(audio)
        got = stream.drain(force=True)
        if got:
            for start, end, text, *_ in got:
                print("  %s  %s" % (fmt_offset(start), text))
        else:
            print("  audio captured but nothing transcribed (too quiet or no speech)")
    if asked in MEETING_MODES:             # the meeting's half too, or alone for Meeting audio only
        selftest_meeting(seconds, model)
    print("")
    print("If both channels transcribed, you are ready: standby start <label>")


def main():
    ap = argparse.ArgumentParser(prog="standby.py")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="run a capture session in the foreground")
    p.add_argument("label", nargs="?", default="meeting")
    p.add_argument("--mode", choices=sorted(MODES),
                   help="override the configured capture mode")
    p.add_argument("--workspace", metavar="PATH",
                   help="directory the agent works in, e.g. ~/code/my-project")
    p.add_argument("--small", action="store_true",
                   help="use the small 110M model (faster load, lower accuracy)")
    p.add_argument("--resume", metavar="SESSION",
                   help="continue recording into an ended session")
    p.add_argument("--push-lines", type=int, metavar="N",
                   help="send new transcript to the AI every N sentences (default 10)")
    p.add_argument("--push-seconds", type=float, metavar="S",
                   help="...or after S seconds with anything unsent (default 60)")
    p.add_argument("--no-ship", action="store_true",
                   help="do not send the transcript to the agent")
    p.add_argument("--no-claude", action="store_true",
                   help="start with Claude off: record only, nothing leaves this Mac")
    p.add_argument("--no-audio", action="store_true",
                   help="do not keep the wav (disables the archival re-pass)")
    p.add_argument("--kind", choices=("meeting", "talk"),
                   help="talk: Claude without a meeting (mic only, voice mode turns, text only)")
    p.add_argument("--attach", metavar="ID",
                   help="run on this Claude Code conversation of yours, in its own folder")
    p.add_argument("--attach-copy", action="store_true", help="...on a copy of it, made at its first turn")
    p.add_argument("--attach-title", metavar="TITLE", help="its title, for the session's own")
    p.add_argument("--claude-model", metavar="MODEL", help="the Claude model its Claude Code runs on")
    p.add_argument("--claude-voice", metavar="VOICE", help="the voice its agent speaks in: a voice id from Settings")
    p.set_defaults(func=lambda a: run_session(a.label, a.no_ship,
                                              not a.no_audio, a.mode, a.small,
                                              a.workspace, a.resume,
                                              a.push_lines, a.push_seconds,
                                              a.no_claude, a.kind,
                                              {"from": a.attach, "copy": a.attach_copy,
                                               "title": a.attach_title or ""} if a.attach else None,
                                              a.claude_model, a.claude_voice))

    p = sub.add_parser("devices", help="list audio devices")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("setup", help="write ~/.standby/config.json")
    p.add_argument("--mode", choices=sorted(MODES),
                   help="skip the prompt and set this capture mode")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("selftest", help="verify the configured channels and model")
    p.add_argument("--seconds", type=int, default=8)
    p.add_argument("--mode", choices=sorted(MODES))
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 1

    def on_term(signum, _frame):
        name = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT",
                signal.SIGHUP: "SIGHUP"}.get(signum, str(signum))
        try:
            parent = subprocess.run(["ps", "-o", "command=", "-p", str(os.getppid())],
                                    capture_output=True, text=True, timeout=2).stdout.strip()
        except Exception:
            parent = "?"
        log("STOPPING: received %s (parent pid %d: %s)" % (name, os.getppid(), parent[:120]))
        _stop.set()
        # Watchdog: if orderly shutdown hangs (audio driver, ssh), exit anyway.
        # A recorder that ignores stop keeps capturing the mic, which is the
        # one failure this tool must never have.
        def _force():
            time.sleep(12)
            log("shutdown hung for 12s, forcing exit")
            os._exit(1)
        threading.Thread(target=_force, daemon=True).start()
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    # Log (not die on) hangups, so a closing terminal is visible in the log.
    signal.signal(signal.SIGHUP, on_term)

    args.func(args)
    return 0


def exit_now(code):
    """Exit as Python would, its threads waited for and its exit hooks run (the captures
    stopped, Core Audio's listeners removed), but without tearing the interpreter down:
    Core Audio and ScreenCaptureKit call in on threads of their own, and one that came
    in while Python was being torn down killed the recorder inside libdispatch, with
    macOS's "Python quit unexpectedly" on the user's screen as a call ended (2026-10-26)."""
    import atexit
    import threading
    if isinstance(code, str):
        print(code, file=sys.stderr)
        code = 1
    try:
        threading._shutdown()
    except Exception:
        pass
    try:
        atexit._run_exitfuncs()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code if isinstance(code, int) else 0 if code is None else 1)


if __name__ == "__main__":
    try:
        result = main()
    except SystemExit as exc:
        result = exc.code
    except Exception:
        import traceback
        traceback.print_exc()
        result = 1
    exit_now(result)
