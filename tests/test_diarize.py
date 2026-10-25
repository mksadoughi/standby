"""Who's speaking, every 10 ms (mac/native/macos/diarizer.py): the mel front end, Nemotron-3's
streaming state, and the stream, with a fake model (no Core ML, no audio).

The reference functions below are the measurement harness's own, whose output matched the published accuracy of the
Core ML build on AMI: the product must give the same."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import hashlib
import os
import sys

import numpy as np

sys.path.insert(0, "mac")
from native.macos import diarizer as D  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:400])


# -- references (the harness, unchanged in substance) ----------------------------------

def ref_mel(x):
    n_fft, hop, win, pre = 512, 160, 400, 0.97
    N = len(x)
    pad = n_fft // 2
    padded = np.zeros(N + 2 * pad, dtype=np.float32)
    padded[pad] = x[0]
    padded[pad + 1:pad + N] = x[1:] - pre * x[:-1]
    frames = 1 + (len(padded) - win) // hop
    w = (0.5 * (1 - np.cos(2 * np.pi * np.arange(win) / (win - 1)))).astype(np.float32)
    off = (n_fft - win) // 2
    idx = np.arange(frames)[:, None] * hop + off + np.arange(win)[None, :]
    fr = np.zeros((frames, n_fft), dtype=np.float32)
    fr[:, off:off + win] = np.where(idx < len(padded), padded[np.minimum(idx, len(padded) - 1)], 0) * w
    spec = np.abs(np.fft.rfft(fr, n=n_fft, axis=1)).astype(np.float32) ** 2
    return np.log(spec @ D._mel_filterbank().T + 2.0 ** -24).astype(np.float32)


def ref_compress(embs, preds, n, sil, cap=264, spk=8):
    per = cap // spk - 1
    strong, weak, minpos = int(per * 0.75), int(per * 1.5), int(per * 0.5)
    lp, l1 = np.log(np.maximum(preds[:n], 0.25)), np.log(np.maximum(1 - preds[:n], 0.25))
    sc = lp - l1 + l1.sum(1, keepdims=True) + np.log(2.0)
    pos = ((preds[:n] > 0.5) & (sc > 0)).sum(0)
    sc = np.where(preds[:n] <= 0.5, -np.inf, sc)
    sc = np.where((sc <= 0) & (pos[None, :] >= minpos), -np.inf, sc)
    if n > cap:
        late = sc[cap:]
        sc[cap:] = np.where(np.isfinite(late), late + 0.05, late)
    for k, f in ((strong, 2.0), (weak, 1.0)):
        for s in range(spk):
            col = sc[:, s]
            fin = np.where(np.isfinite(col))[0]
            if len(fin):
                top = fin[np.argsort(-col[fin], kind="stable")[:min(k, len(fin))]]
                sc[top, s] += f * np.log(2.0)
    tot = n + 1
    sc = np.vstack([sc, np.full((1, spk), np.inf)])
    flat = sc.T.reshape(-1)
    order = np.lexsort((np.arange(len(flat)), -flat))[:cap]
    top = np.sort(np.where(flat[order] == -np.inf, 99999, order))
    dis, fi = np.zeros(cap, bool), np.zeros(cap, int)
    for i, t in enumerate(top):
        if t == 99999 or t % tot >= n:
            dis[i] = True
        else:
            fi[i] = t % tot
    return (np.where(dis[:, None], sil[None, :], embs[fi]).astype(np.float32),
            np.where(dis[:, None], 0.0, preds[fi]).astype(np.float32))


def ref_update(st, embs, enc_len, preds, hi, rc, sil, chunk=9, rcap=4, fifo=40, period=40, cache=264):
    sc_len, ff_len = st["cache_len"], st["fifo_len"]
    clen = min(max(enc_len, 0), chunk + rcap - rc)
    a = sc_len + ff_len
    out = hi[a * 8:(a + clen) * 8]
    comb = ff_len + clen
    pop = min(comb, max(period, comb - fifo)) if comb > fifo else 0
    if clen == 0:
        pop = ff_len
    L_e = np.vstack([st["fifo"][:ff_len], embs[:clen]])
    L_p = np.vstack([preds[sc_len:a], preds[a:a + clen]])
    st["fifo"][:] = 0
    st["fifo_preds"][:] = 0
    st["fifo"][:comb - pop] = L_e[pop:comb]
    st["fifo_preds"][:comb - pop] = L_p[pop:comb]
    st["fifo_len"] = comb - pop
    new = sc_len + pop
    if new > cache:
        ce = np.vstack([st["cache"][:sc_len], L_e[:pop]])
        cp = np.vstack([st["cache_preds"][:sc_len] if st["compressed"] else preds[:sc_len], L_p[:pop]])
        st["cache"], st["cache_preds"] = ref_compress(ce, cp, new, sil)
        st["cache_len"], st["compressed"] = cache, True
    elif pop > 0:
        st["cache"][sc_len:new] = L_e[:pop]
        st["cache_preds"][sc_len:new] = L_p[:pop]
        st["cache_len"] = new
    return out


def digest(*arrays):
    h = hashlib.sha1()
    for a in arrays:
        h.update(np.ascontiguousarray(np.asarray(a, dtype=np.float32)).tobytes())
    return h.hexdigest()[:12]


rng = np.random.default_rng(61)

# 1. The mel filterbank: Slaney mel bands, as librosa's.
fb = D._mel_filterbank()
check("128 mel bands over 257 FFT bins, none empty", fb.shape == (128, 257) and (fb.sum(1) > 0).all(), fb.shape)
try:
    import librosa
    ref = librosa.filters.mel(sr=16000, n_fft=512, n_mels=128, fmin=0.0, fmax=8000.0, norm="slaney", htk=False)
    check("  the same as librosa's (norm=slaney)", np.abs(fb - ref).max() < 1e-6, np.abs(fb - ref).max())
except ImportError:
    print("  (librosa not here: its comparison skipped)")

# 2. The mel front end: frames as they become computable, the same whatever the blocks.
x = (rng.normal(size=16000 * 7) * 0.05).astype(np.float32)
x[16000:24000] += 0.3 * np.sin(np.arange(8000) * 0.2).astype(np.float32)
whole = ref_mel(x)
m = D.Mel()
streamed = np.vstack([m.feed(x), m.feed([], final=True)])
check("one block: the harness's frames, as many and the same", streamed.shape == whole.shape
      and np.abs(streamed - whole).max() < 1e-4, (streamed.shape, whole.shape))
m, parts, i = D.Mel(), [], 0
while i < len(x):
    n = int(rng.integers(1, 4000))
    parts.append(m.feed(x[i:i + n]))
    i += n
parts.append(m.feed([], final=True))
check("random blocks, as the recorder's: the same frames", np.abs(np.vstack(parts) - whole).max() < 1e-4)
m = D.Mel()
got = len(m.feed(x[:16000]))
check("frames come out as soon as their samples are in (no waiting for the end)", got == (16000 - 200) // 160 + 1, got)

# 3. The streaming state: the harness's, step for step, through the cache filling and compressing.
sil = rng.normal(size=512).astype(np.float32)
a, b = D.new_state(), D.new_state()
same, outs = True, []
for call in range(90):
    T = a["cache_len"] + a["fifo_len"] + 13
    preds = rng.uniform(size=(T, 8)).astype(np.float32) ** 3
    preds[:, call % 3] = np.maximum(preds[:, call % 3], 0.8)             # someone speaks
    hi = np.repeat(preds, 8, axis=0)
    embs = rng.normal(size=(13, 512)).astype(np.float32)
    o1 = D.update(a, embs, 13, preds, hi, 4, sil)
    o2 = ref_update(b, embs, 13, preds, hi, 4, sil)
    outs.append(o1)
    same = same and np.array_equal(o1, o2) and all(
        np.array_equal(a[k], b[k]) for k in ("cache", "cache_preds", "fifo", "fifo_preds")) and \
        (a["cache_len"], a["fifo_len"], a["compressed"]) == (b["cache_len"], b["fifo_len"], b["compressed"])
check("90 chunks: the same state and output as the harness, every step", same)
check("  the speaker cache filled and was compressed on the way", a["compressed"] and a["cache_len"] == 264)
check("  the FIFO stays within its 40 frames", a["fifo_len"] <= 40, a["fifo_len"])
check("  each chunk gives 72 frames of 10 ms (0.72 s)", all(len(o) == 72 for o in outs))
# Frozen as sums, not bytes: float32 math rounds a little differently from one Apple chip to
# the next, so a hash of the raw state only ever matched the Mac it was measured on.
FROZEN = {"cache": (904.1075, 106306.5503), "cache_preds": (415.1128, 415.1128), "fifo": (-31.4406, 3992.4999)}
got = {k: (float(np.asarray(a[k], dtype=np.float64).sum()), float(np.abs(np.asarray(a[k], dtype=np.float64)).sum()))
       for k in FROZEN}
check("  frozen: the state after 90 chunks is the one measured",
      (a["cache_len"], a["fifo_len"]) == (264, 10)
      and all(abs(got[k][1] - want[1]) <= 1e-4 * want[1] and abs(got[k][0] - want[0]) <= 1e-4 * want[1]
              for k, want in FROZEN.items()), got)


# 4. The stream, with a fake model: who speaks is scripted by time.
class Fake(object):
    """Speaker 0 until 3.0 s, speaker 1 until 3.3 s (a 0.3 s "yeah"), 0 until 6 s,
    then speaker 2. Its 10 ms output follows the chunk it was given."""

    def __init__(self):
        self.sil = np.zeros(512, np.float32)
        self.t = 0                                  # mel frames given so far (chunk cores)
        self.calls = []

    def __call__(self, chunk, length, st):
        self.calls.append((chunk.shape, length, st["cache_len"], st["fifo_len"]))
        T = st["cache_len"] + st["fifo_len"] + (length + 7) // 8
        hi = np.zeros((T * 8, 8), np.float32)
        a = (st["cache_len"] + st["fifo_len"]) * 8
        for k in range(min(72, length)):
            f = self.t + k
            who = 0 if f < 300 else 1 if f < 330 else 0 if f < 600 else 2
            hi[a + k, who] = 0.9
        self.t += 72
        return np.zeros((T, 8), np.float32), hi, np.zeros(((length + 7) // 8, 512), np.float32)


fake = Fake()
s = D.Stream(fake, thread=False)
audio = np.zeros(16000 * 9, np.float32)
s.feed(audio[:16000])
check("one second in: nothing known yet (a chunk needs 1.04 s of audio)", s.upto == 0 and not fake.calls, s.upto)
s.feed(audio[16000:17600])
check("1.1 s in: 0.72 s known, one chunk run (it waits for 0.32 s of look-ahead)",
      abs(s.upto - 0.72) < 1e-6 and len(fake.calls) == 1, (s.upto, len(fake.calls)))
check("  the model gets 1.04 s of mel (104 frames), as the preset's fixed shape",
      fake.calls[0][0] == (1, 104, 128) and fake.calls[0][1] == 104, fake.calls[0])
for i in range(17600, len(audio), 8000):
    s.feed(audio[i:i + 8000])
check("labels lag the audio by at most 1.04 s", 9.0 - s.upto <= 1.04 + 1e-6, s.upto)
s.finish()
check("at the end, every 10 ms has a label", abs(s.upto - 9.0) < 0.02, s.upto)
check("who: the most active voice over a span", s.who(0.5, 2.5) == 0 and s.who(6.5, 8.0) == 2)
r = s.runs(1.0, 5.0)
check("runs: a 0.3 s word from someone else doesn't split a line", [k for _a, _b, k in r] == [0], r)
r = s.runs(4.0, 8.0)
check("runs: a new voice that holds splits it, at the change",
      [k for _a, _b, k in r] == [0, 2] and abs(r[0][1] - 6.0) < 0.02 and r[-1][1] == 8.0, r)
check("runs: nothing heard, no label", s.runs(20.0, 21.0) == [(20.0, 21.0, None)], s.runs(20.0, 21.0))

D.KEEP, keep = 2.0, D.KEEP
s2 = D.Stream(Fake(), thread=False)
for i in range(0, len(audio), 8000):
    s2.feed(audio[i:i + 8000])
D.KEEP = keep
check("old labels are let go (a long meeting keeps only the last minutes)",
      len(s2.probs) <= 200 and s2.upto > 7.0 and s2.who(7.0, 7.5) == 2, (len(s2.probs), s2.upto))


class Broken(Fake):
    def __call__(self, chunk, length, st):
        raise RuntimeError("the Neural Engine went away")


logs = []
s3 = D.Stream(Broken(), log=logs.append)
s3.feed(audio[:32000])
import time  # noqa: E402
for _ in range(500):                    # the worker thread gets to it
    if s3.failed:
        break
    time.sleep(0.01)
check("a model that fails stops the stream, and says so (the recorder falls back)",
      s3.failed is not None and any("stopped" in m for m in logs), logs)

# 5. Loading: nothing installed here, so no model, and a plain reason.
logs = []
check("not installed: load() gives None and says why", D.load("/nonexistent", log=logs.append) is None
      and any("not installed" in m for m in logs), logs)

# 6. In the recorder: lines wait for their speakers (never long), split where the voice changes.
import json  # noqa: E402
import tempfile  # noqa: E402
import types  # noqa: E402
from importlib.machinery import SourceFileLoader  # noqa: E402
from pathlib import Path  # noqa: E402

sys.modules.setdefault("sounddevice", types.SimpleNamespace())
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
check("a line waits at most 0.45 s for its speakers (the measured worst case)", sb.Voices.HOLD == 0.45, sb.Voices.HOLD)
unit = lambda v: v / np.linalg.norm(v)
PRINT = {0: unit(np.eye(16)[0]), 1: unit(np.eye(16)[1]), 2: unit(np.eye(16)[2])}
SCRIPT = [(0.0, 2.0, 0), (2.0, 4.0, 1), (4.0, 99.0, 0)]     # who speaks when, on the channel's clock


def voice_at(t):
    return next(k for a, b, k in SCRIPT if a <= t < b)


class Scripted(Fake):
    """The model's answer follows SCRIPT, from where its stream started (offset)."""

    def __init__(self, offset=0.0):
        Fake.__init__(self)
        self.offset = offset

    def __call__(self, chunk, length, st):
        T = st["cache_len"] + st["fifo_len"] + (length + 7) // 8
        hi = np.zeros((T * 8, 8), np.float32)
        a = (st["cache_len"] + st["fifo_len"]) * 8
        for k in range(min(72, length)):
            hi[a + k, voice_at(self.offset + (self.t + k) / 100.0)] = 0.9
        self.t += 72
        return np.zeros((T, 8), np.float32), hi, np.zeros(((length + 7) // 8, 512), np.float32)


def recorder(model=None, hold=30.0, wait=lambda: True):
    """A Room channel with a fake transcriber (each line's 8 words spread evenly over
    it), the session's matcher with fake prints (a line's first sample says whose voice
    it is), and a fake model. hold: how long a line may wait (long, so a busy Mac
    can't flake the labels; the 0.45 s itself is checked above)."""
    sdir = Path(tempfile.mkdtemp(prefix="sbdz-"))
    sp = sb.SpeakerID(sdir / "speakers.state.json", remember=False)
    sp.extractor = object()
    out = []
    ch = sb.Channel("Room", None, None, lambda *a, **k: out.append((a[1], a[2], a[3], a[4])), None, 3600,
                    speakers=sp)

    def decode(audio):
        n = len(audio) / 16000.0
        words = [(i * n / 8, (i + 1) * n / 8, " w%d" % i) for i in range(8)]
        ch._stream._words = (len(audio), words)
        return "".join(w for _a, _b, w in words).strip()
    ch._stream = sb.UtteranceTranscriber(None, decode=decode)
    sb.embed = lambda audio, extractor=None: PRINT[int(round(float(audio[0]) * 10000)) % 3]
    ch.diarizer = (lambda: model) if model is not None else None
    ch.label_wait = wait
    sb.Voices.HOLD = hold
    return ch, sp, out


def speech(seconds, start):
    """Loud enough for the VAD; every 0.1 s a sample marks whose voice it is (for the fake prints)."""
    n = int(seconds * 16000)
    t = start + np.arange(n) / 16000.0
    x = (0.05 * np.sin(t * 900)).astype(np.float32)
    for i in range(0, n, 1600):
        x[i] = voice_at(t[i]) / 10000.0
    return x


def run(ch, blocks, settle=True):
    for b in blocks:
        ch.audio.put(b)
        ch.pump()
    for _ in range(800):                       # the model's worker thread catches up
        ch.pump()
        if not ch.held or not settle:
            break
        time.sleep(0.01)


def meeting(ch):
    audio = np.concatenate([np.zeros(4800, np.float32), speech(3.4, 0.3), np.zeros(16000, np.float32),
                            speech(1.5, 4.7), np.zeros(16000, np.float32)])
    run(ch, [audio[i:i + 8000] for i in range(0, len(audio), 8000)])


ch, sp, out = recorder(None)
meeting(ch)
check("no model: lines go out at once, labelled by the matcher, as before",
      len(out) == 2 and all(o[3] is not None for o in out) and not ch.held, out)

ch, sp, out = recorder(Scripted())
meeting(ch)
words = " ".join("w%d" % i for i in range(8))
check("with the model: a line where two people speak back to back is split in two",
      len(out) == 3 and (out[0][2] + " " + out[1][2]) == words, [o[2] for o in out])
check("  at the change of voice (2.0 s), each part with its own words",
      len(out) == 3 and abs(out[0][1] - 2.0) < 0.05 and abs(out[1][0] - 2.0) < 0.05, [(o[0], o[1]) for o in out])
check("  two speakers, and the first one back is the same number",
      len(out) == 3 and None not in [o[3] for o in out] and out[0][3] != out[1][3] and out[2][3] == out[0][3],
      [o[3] for o in out])
check("  nothing left waiting", not ch.held)

ch, sp, out = recorder(Scripted(), hold=0.0)
run(ch, [np.zeros(4800, np.float32), speech(1.5, 0.3), np.zeros(16000, np.float32)], settle=False)
check("past its wait, a line goes out labelled as far as known", len(out) == 1 and not ch.held, out)

ch, sp, out = recorder(Scripted(), wait=lambda: False)
before = json.dumps(sp.diar.to_json())
run(ch, [np.zeros(4800, np.float32), speech(1.5, 0.3), np.zeros(16000, np.float32)])
check("dictation or a voice turn: out at once, no label, and the matcher left alone",
      len(out) == 1 and out[0][3] is None and json.dumps(sp.diar.to_json()) == before, out)

ch, sp, out = recorder(Broken())
run(ch, [np.zeros(4800, np.float32), speech(1.5, 0.3), np.zeros(16000, np.float32)])
run(ch, [speech(1.5, 4.7), np.zeros(16000, np.float32)])
check("the model fails: back to the matcher, every line still out and labelled",
      ch.voices is None and len(out) == 2 and out[-1][3] is not None, (out, ch.voices))

SCRIPT[:] = [(0.0, 99.0, 0)]
late = {"m": None}
ch, sp, out = recorder()
ch.diarizer = lambda: late["m"]
run(ch, [np.zeros(4800, np.float32), speech(1.5, 0.3), np.zeros(16000, np.float32)])
first = out[-1][3]
late["m"] = Scripted(offset=ch.pumped / 16000.0)
for k in range(4):
    run(ch, [speech(1.5, 0), np.zeros(16000, np.float32)])
check("the model ready mid-meeting: the voice keeps the number the matcher gave it",
      first is not None and ch.voices is not None and sp.diar.resolve(out[-1][3]) == first,
      ([o[3] for o in out], sp.diar.alias))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
