"""native/diarizer.py on a Mac: Core ML.

Who is speaking, every 10 ms: NVIDIA's Nemotron-3-Diarization, a streaming
diarizer that follows up to 8 voices with a cache of how each one sounds, run on the
Mac's Neural Engine through Core ML (PyObjC). It labels frames, not lines, so a
line where two people speak can be split, and a voice keeps its number when its sound
changes (another mic, voice processing). Measured:
the right person on 92% of lines in a room and 96% in a call (today's matcher 81% and
88%), and labels known at most 0.45 s after a line closes.

    model = load()                    a Model, or None (macOS 13, not installed, no Core ML)
    s = Stream(model)                 one per audio source
    s.feed(block)                     16 kHz mono float32, any size; runs the model as
                                      chunks fill (0.72 s each, 0.32 s of look-ahead)
    s.upto                            seconds of audio whose speakers are known
    s.runs(t0, t1)                    [(t0, t1, slot)]: who spoke when, slots 0-7

The model is the Core ML build by Fluid Inference of NVIDIA's checkpoint (OpenMDW-1.1),
from Hugging Face at a fixed revision (install.sh). Its streaming state (a FIFO and a
speaker cache, compressed by score) lives here, ported from FluidAudio's Swift
(Nemotron3StateUpdater, Apache-2.0), which ports NeMo's streaming_update_async.
"""
import os
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby"))
MODEL_DIR = ROOT / "models" / "nemotron-3-diarization"
MODEL_FILE = "Nemotron3Diarizer_fast.mlmodelc"
SILENCE_FILE = "learnable_sil_emb.bin"
HF_REPO = "FluidInference/nemotron-3-diarization-coreml"
HF_REVISION = "25a90f97f254428d4b30374b76af9c74fdee8327"

SR = 16000
HOP, WIN, NFFT, NMEL, PREEMPH = 160, 400, 512, 128, 0.97
SPK, D, SUB, UP = 8, 512, 8, 8                     # speakers, embedding size, mel frames per step
# The "fast" preset: 1.04 s behind live (chunk + look-ahead, in 80 ms steps).
CHUNK, RC, FIFO, PERIOD, CACHE = 9, 4, 40, 40, 264
# Cache compression (NeMo's model config)
PRED_TH, BOOST_LATEST, STRONG, WEAK, MINPOS, SILF = 0.25, 0.05, 0.75, 1.5, 0.5, 1
KEEP = 300.0          # seconds of labels kept: lines need only the last few


def _mel_filterbank():
    """Slaney-scaled mel filters [128, 257], as librosa's (norm="slaney", htk=False)."""
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, step = min_log_hz / f_sp, np.log(6.4) / 27.0

    def to_mel(hz):
        return hz / f_sp if hz < min_log_hz else min_log_mel + np.log(hz / min_log_hz) / step

    def to_hz(m):
        return np.where(m >= min_log_mel, min_log_hz * np.exp(step * (m - min_log_mel)), f_sp * m)
    pts = to_hz(np.linspace(to_mel(0.0), to_mel(SR / 2.0), NMEL + 2))
    freqs = np.arange(NFFT // 2 + 1) * SR / float(NFFT)
    fb = np.zeros((NMEL, len(freqs)))
    for m in range(NMEL):
        lo, c, hi = pts[m], pts[m + 1], pts[m + 2]
        rise = (freqs - lo) / (c - lo)
        fall = (hi - freqs) / (hi - c)
        fb[m] = np.maximum(0, np.minimum(rise, fall)) * 2.0 / (hi - lo)
    return fb.astype(np.float32)


class Mel(object):
    """Log-mel frames as they become computable, one per 10 ms: frame f is centred on
    sample f*160 and needs the 200 samples either side (NeMo's front end: 0.97
    pre-emphasis, a 400-sample Hann window in a 512-point FFT, log(x + 2^-24))."""

    def __init__(self):
        self.fb = _mel_filterbank()
        self.win = (0.5 * (1 - np.cos(2 * np.pi * np.arange(WIN) / (WIN - 1)))).astype(np.float32)
        self.buf = np.zeros(WIN // 2, dtype=np.float32)    # pre-emphasised samples; zeros before the start
        self.base = -(WIN // 2)                             # sample index of buf[0]
        self.last = 0.0                                     # the sample before buf's end, for pre-emphasis
        self.frames = 0                                     # frames made so far
        self.received = 0

    def feed(self, x, final=False):
        x = np.asarray(x, dtype=np.float32)
        if len(x):
            y = np.empty_like(x)
            y[0] = x[0] - PREEMPH * self.last if self.received else x[0]
            y[1:] = x[1:] - PREEMPH * x[:-1]
            self.last = float(x[-1])
            self.received += len(x)
            self.buf = np.concatenate([self.buf, y])
        if final:
            total = 1 + (self.received + NFFT - WIN) // HOP
            need = (total - 1) * HOP + WIN // 2 - (self.base + len(self.buf))
            if need > 0:
                self.buf = np.concatenate([self.buf, np.zeros(need, dtype=np.float32)])
        else:
            total = (self.received - WIN // 2) // HOP + 1 if self.received >= WIN // 2 else 0
        n = total - self.frames
        if n <= 0:
            return np.zeros((0, NMEL), dtype=np.float32)
        start = self.frames * HOP - WIN // 2 - self.base
        idx = start + np.arange(n)[:, None] * HOP + np.arange(WIN)[None, :]
        fr = np.zeros((n, NFFT), dtype=np.float32)
        off = (NFFT - WIN) // 2
        fr[:, off:off + WIN] = self.buf[idx] * self.win
        spec = np.abs(np.fft.rfft(fr, axis=1)).astype(np.float32) ** 2
        out = np.log(spec @ self.fb.T + 2.0 ** -24).astype(np.float32)
        self.frames = total
        drop = self.frames * HOP - WIN // 2 - self.base          # what no later frame needs
        if drop > 0:
            self.buf = self.buf[drop:]
            self.base += drop
        return out


# -- the streaming state (NeMo streaming_update_async, batch 1) --------------------

def new_state():
    return {"cache": np.zeros((CACHE, D), np.float32), "cache_preds": np.zeros((CACHE, SPK), np.float32),
            "cache_len": 0, "compressed": False,
            "fifo": np.zeros((FIFO, D), np.float32), "fifo_preds": np.zeros((FIFO, SPK), np.float32),
            "fifo_len": 0}


def _scores(p):
    lp = np.log(np.maximum(p, PRED_TH))
    l1 = np.log(np.maximum(1 - p, PRED_TH))
    return lp - l1 + l1.sum(1, keepdims=True) + np.log(2.0)


def _compress(embs, preds, n, sil):
    """Keep CACHE frames: each speaker's most telling ones, a silence frame each."""
    per = CACHE // SPK - SILF
    strong, weak, minpos = int(per * STRONG), int(per * WEAK), int(per * MINPOS)
    sc = _scores(preds[:n])
    pos = ((preds[:n] > 0.5) & (sc > 0)).sum(0)
    sc = np.where(preds[:n] <= 0.5, -np.inf, sc)
    sc = np.where((sc <= 0) & (pos[None, :] >= minpos), -np.inf, sc)
    if BOOST_LATEST > 0 and n > CACHE:
        late = sc[CACHE:]
        sc[CACHE:] = np.where(np.isfinite(late), late + BOOST_LATEST, late)
    for k, f in ((strong, 2.0), (weak, 1.0)):
        for s in range(SPK):
            fin = np.where(np.isfinite(sc[:, s]))[0]
            if k > 0 and len(fin):
                top = fin[np.argsort(-sc[fin, s], kind="stable")[:min(k, len(fin))]]
                sc[top, s] += f * np.log(2.0)
    tot = n + SILF
    flat = np.vstack([sc, np.full((SILF, SPK), np.inf)]).T.reshape(-1)   # speaker-major
    order = np.lexsort((np.arange(len(flat)), -flat))[:CACHE]
    top = np.sort(np.where(flat[order] == -np.inf, 10 ** 9, order))
    frame = top % tot
    off = (top == 10 ** 9) | (frame >= n)
    frame = np.where(off, 0, frame)
    cache = np.where(off[:, None], sil[None, :], embs[frame])
    cpreds = np.where(off[:, None], 0.0, preds[frame])
    return cache.astype(np.float32), cpreds.astype(np.float32)


def update(st, embs, enc_len, preds, hi, rc, sil):
    """One chunk's outputs into the state; returns the chunk's own 10 ms probabilities."""
    sc_len, ff_len = st["cache_len"], st["fifo_len"]
    clen = min(max(enc_len, 0), CHUNK + RC - rc)
    a = sc_len + ff_len
    out = hi[a * UP:(a + clen) * UP]
    comb = ff_len + clen
    pop = min(comb, max(PERIOD, comb - FIFO)) if comb > FIFO else 0
    if clen == 0:
        pop = ff_len
    L_e = np.vstack([st["fifo"][:ff_len], embs[:clen]])
    L_p = np.vstack([preds[sc_len:a], preds[a:a + clen]])
    st["fifo"][:] = 0
    st["fifo_preds"][:] = 0
    st["fifo"][:comb - pop] = L_e[pop:comb]
    st["fifo_preds"][:comb - pop] = L_p[pop:comb]
    st["fifo_len"] = comb - pop
    new_len = sc_len + pop
    if new_len > CACHE:
        ce = np.vstack([st["cache"][:sc_len], L_e[:pop]])
        cp = np.vstack([st["cache_preds"][:sc_len] if st["compressed"] else preds[:sc_len], L_p[:pop]])
        st["cache"], st["cache_preds"] = _compress(ce, cp, new_len, sil)
        st["cache_len"], st["compressed"] = CACHE, True
    elif pop > 0:
        st["cache"][sc_len:new_len] = L_e[:pop]
        st["cache_preds"][sc_len:new_len] = L_p[:pop]
        st["cache_len"] = new_len
    return out


# -- the model -----------------------------------------------------------------------

class Model(object):
    """The Core ML model through PyObjC: inputs written into its MLMultiArrays, outputs
    read by their strides (Core ML pads rows), one autorelease pool per call. Bit for
    bit what coremltools gives, without torch."""

    def __init__(self, folder=MODEL_DIR, units="CPU_AND_NE"):
        import CoreML
        import Foundation
        import objc
        self.objc, self.ML = objc, CoreML
        cfg = CoreML.MLModelConfiguration.alloc().init()
        cfg.setComputeUnits_({"CPU_ONLY": 0, "ALL": 2, "CPU_AND_NE": 3}[units])   # no GPU: parakeet has it
        url = Foundation.NSURL.fileURLWithPath_(str(Path(folder) / MODEL_FILE))
        self.m, err = CoreML.MLModel.modelWithContentsOfURL_configuration_error_(url, cfg, None)
        if self.m is None:
            raise RuntimeError("the speaker model did not load: %s" % err)
        self.sil = np.fromfile(str(Path(folder) / SILENCE_FILE), dtype=np.float32)
        f16, i32 = CoreML.MLMultiArrayDataTypeFloat16, CoreML.MLMultiArrayDataTypeInt32
        self.shapes = {"chunk": ((1, (CHUNK + RC) * SUB, NMEL), np.float16, f16),
                       "chunk_lengths": ((1,), np.int32, i32),
                       "fifo": ((1, FIFO, D), np.float16, f16), "fifo_lengths": ((1,), np.int32, i32),
                       "spkcache": ((1, CACHE, D), np.float16, f16), "spkcache_lengths": ((1,), np.int32, i32)}
        self.arrays = {}
        for name, (shape, _dt, t) in self.shapes.items():
            self.arrays[name], _err = CoreML.MLMultiArray.alloc().initWithShape_dataType_error_(list(shape), t, None)
        feats = {k: CoreML.MLFeatureValue.featureValueWithMultiArray_(a) for k, a in self.arrays.items()}
        self.inputs, _err = CoreML.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(feats, None)
        self.lock = threading.Lock()            # one set of input arrays: the room's and the call's streams take turns

    @staticmethod
    def _view(arr, shape, dt, strides):
        item = np.dtype(dt).itemsize
        n = max((d - 1) * s for d, s in zip(shape, strides)) + 1
        raw = np.frombuffer(arr.dataPointer().as_buffer(n * item), dtype=dt)
        return np.lib.stride_tricks.as_strided(raw, shape, [s * item for s in strides])

    def _put(self, name, value):
        shape, dt, _t = self.shapes[name]
        a = self.arrays[name]
        self._view(a, shape, dt, [int(s) for s in a.strides()])[...] = np.asarray(value, dtype=dt).reshape(shape)

    def _get(self, out, name):
        arr = out.featureValueForName_(name).multiArrayValue()
        dt = np.float16 if arr.dataType() == self.ML.MLMultiArrayDataTypeFloat16 else np.float32
        return self._view(arr, [int(s) for s in arr.shape()], dt, [int(s) for s in arr.strides()]).astype(np.float32)

    def __call__(self, chunk, length, st):
        """(packed 80 ms predictions [T, 8], 10 ms ones [8T, 8], the chunk's embeddings [n, 512])."""
        with self.lock, self.objc.autorelease_pool():
            self._put("chunk", chunk)
            self._put("chunk_lengths", [length])
            self._put("fifo", st["fifo"])
            self._put("fifo_lengths", [st["fifo_len"]])
            self._put("spkcache", st["cache"])
            self._put("spkcache_lengths", [st["cache_len"]])
            out, err = self.m.predictionFromFeatures_error_(self.inputs, None)
            if out is None:
                raise RuntimeError("speaker model failed: %s" % err)
            return (self._get(out, "speaker_preds").reshape(-1, SPK),
                    self._get(out, "speaker_preds_10ms").reshape(-1, SPK),
                    self._get(out, "chunk_pre_encode_embs").reshape(-1, D))


def installed(folder=MODEL_DIR):
    return (Path(folder) / MODEL_FILE / "weights" / "weight.bin").exists() and (Path(folder) / SILENCE_FILE).exists()


def load(folder=MODEL_DIR, log=lambda m: None):
    """The model, or None where it can't run: not installed, macOS 13 (the build needs
    14), no Core ML for Python. The first load after an install compiles it for the
    Neural Engine (20-55 s measured), later ones take about 2 s: call it off the main thread."""
    if not installed(folder):
        log("speakers: the diarization model is not installed (%s): speaker matching as before" % folder)
        return None
    t0 = time.time()
    try:
        m = Model(folder)
    except Exception as exc:
        log("speakers: the diarization model could not load (%r): speaker matching as before" % exc)
        return None
    m.sil = m.sil if m.sil.size == D else np.zeros(D, np.float32)
    log("speakers: diarization model ready in %.1f s" % (time.time() - t0))
    return m


class Stream(object):
    """One audio source's speakers. feed() hands audio to a worker thread, which runs
    the model as chunks fill; the recorder reads upto and runs() from its own thread."""

    def __init__(self, model, log=lambda m: None, thread=True):
        self.model, self.log = model, log
        self.mel = Mel()
        self.st = new_state()
        self.feats = np.zeros((0, NMEL), dtype=np.float32)
        self.feats_base = 0                     # mel frame index of feats[0]
        self.core = 0                           # next chunk's first mel frame
        self.probs, self.probs_base = np.zeros((0, SPK), np.float16), 0
        self.lock = threading.Lock()
        self.calls, self.busy, self.model_s = 0, 0.0, 0.0     # model calls, the worker's CPU time, time in the model
        self.failed = None
        self.q = None
        if thread:
            import queue
            self.q = queue.Queue()
            threading.Thread(target=self._work, name="diarize", daemon=True).start()

    @property
    def upto(self):
        """Seconds of audio, from this stream's start, whose speakers are known."""
        with self.lock:
            return (self.probs_base + len(self.probs)) / 100.0

    def feed(self, block):
        if self.q is not None:
            self.q.put(np.asarray(block, dtype=np.float32))
        else:
            self._feed(block, False)

    def finish(self):
        if self.q is not None:
            self.q.put(None)
        else:
            self._feed(np.zeros(0, np.float32), True)

    def _work(self):
        while True:
            block = self.q.get()
            if self.failed:
                continue
            try:
                self._feed(np.zeros(0, np.float32) if block is None else block, block is None)
            except Exception as exc:          # labels stop; the recorder falls back
                self.failed = exc
                self.log("speakers: the diarization model stopped (%r): speaker matching as before" % exc)

    def _feed(self, x, final):
        c0 = time.thread_time()
        f = self.mel.feed(x, final)
        if len(f):
            self.feats = np.vstack([self.feats, f])
        total = self.feats_base + len(self.feats)
        core, rcm = CHUNK * SUB, RC * SUB
        while self.core < total and (final or self.core + core + rcm <= total):
            end = min(self.core + core, total)
            roff = min(rcm, total - end)
            ch = self.feats[self.core - self.feats_base:end + roff - self.feats_base]
            n = len(ch)
            if n < (CHUNK + RC) * SUB:
                ch = np.vstack([ch, np.zeros(((CHUNK + RC) * SUB - n, NMEL), np.float32)])
            t0 = time.time()
            preds, hi, embs = self.model(ch[None], n, self.st)
            self.model_s += time.time() - t0
            out = update(self.st, embs, (n + SUB - 1) // SUB, preds, hi, (roff + SUB - 1) // SUB, self.model.sil)
            out = out[:max(0, min(len(out), (total if final else end) - self.core))]
            self.calls += 1
            with self.lock:
                self.probs = np.vstack([self.probs, out.astype(np.float16)])
                over = len(self.probs) - int(KEEP * 100)
                if over > 0:
                    self.probs, self.probs_base = self.probs[over:], self.probs_base + over
            self.core = end
            keep_from = self.core - self.feats_base
            self.feats, self.feats_base = self.feats[keep_from:], self.core
        self.busy += time.thread_time() - c0

    def window(self, t0, t1):
        """The 10 ms probabilities [n, 8] from t0 to t1 (seconds from the stream's start)."""
        with self.lock:
            a = max(0, int(round(t0 * 100)) - self.probs_base)
            b = max(a, int(round(t1 * 100)) - self.probs_base)
            return self.probs[a:b].astype(np.float32)

    def who(self, t0, t1, floor=0.15):
        """The slot most active from t0 to t1 (mean probability), or None under floor."""
        w = self.window(t0, t1)
        if not len(w):
            return None
        act = w.mean(0)
        k = int(np.argmax(act))
        return k if act[k] >= floor else None

    def runs(self, t0, t1, min_run=0.5):
        """Who spoke when from t0 to t1: [(t0, t1, slot)], a change of voice counted once
        the new one holds min_run seconds (measured: 0.5 s splits best). Quiet frames
        join their neighbours; slot None where nobody is heard at all."""
        w = self.window(t0, t1)
        if not len(w):
            return [(t0, t1, None)]
        dom = np.where(w.max(1) > 0.5, w.argmax(1), -1)
        runs = []
        for i, d in enumerate(dom):
            if runs and (runs[-1][0] == d or d == -1):
                runs[-1][2] = i + 1
            elif runs and runs[-1][0] == -1:
                runs[-1][0], runs[-1][2] = d, i + 1
            else:
                runs.append([d, i, i + 1])
        out = []
        for r in runs:
            if out and (r[2] - r[1] < min_run * 100 or out[-1][0] == r[0]):
                out[-1][2] = r[2]
            else:
                out.append(r)
        if len(out) > 1 and out[0][2] - out[0][1] < min_run * 100:
            out[1][1] = out[0][1]
            out.pop(0)
        step = 0.01
        res = [(t0 + i * step, t0 + j * step, None if d < 0 else int(d)) for d, i, j in out]
        res[-1] = (res[-1][0], t1, res[-1][2])
        return res
