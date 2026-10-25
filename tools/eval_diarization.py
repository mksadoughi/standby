"""Evaluate speaker grouping on labeled speech (sherpa-onnx sr-data).

Setup: pip install sherpa-onnx soundfile numpy; git clone https://github.com/csukuangfj/sr-data;
download nemo_en_titanet_small.onnx (see mac/standby.py SPEAKER_MODEL_URL). Run from that dir.
The OnlineDiarizer here mirrors the one in mac/standby.py; results are in its docstring.
"""
import glob, os, re, random, itertools, numpy as np, soundfile as sf, sherpa_onnx
from collections import Counter, defaultdict
import diarizer
ex = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model="nemo_en_titanet_small.onnx", num_threads=2))
def label(p):
    b = os.path.basename(p); m = re.match(r"(speaker\d)", b) or re.match(r"([a-z]+)-", b); return m.group(1)
def emb(x):
    s = ex.create_stream(); s.accept_waveform(sample_rate=16000, waveform=x); s.input_finished(); return np.array(ex.compute(s))
clips = [(label(p), sf.read(p, dtype="float32", always_2d=True)[0][:, 0]) for p in sorted(glob.glob("sr-data/**/*.wav", recursive=True))]
def segs(L):
    out = []
    for l, x in clips:
        if L is None: out.append((l, x, len(x) / 16000)); continue
        n = int(L * 16000)
        for i in range(0, len(x) - n + 1, n): out.append((l, x[i:i + n], L))
    return out
sets = {"full": segs(None), "3.0s": segs(3.0), "1.5s": segs(1.5), "mixed": segs(3.0) + segs(1.5)}
emb_sets = {k: [(l, emb(x), sec) for l, x, sec in v] for k, v in sets.items()}
def acc(labels, a):
    by = defaultdict(list)
    for l, s in zip(labels, a): by[s].append(l)
    return sum(Counter(v).most_common(1)[0][1] for v in by.values()) / len(labels)
rows = []
for J, M, SN in itertools.product((0.35, 0.40, 0.45), (0.50, 0.55, 0.60, 0.70), (0.10, 0.15, 0.20, 0.25)):
    diarizer.OnlineDiarizer.JOIN, diarizer.OnlineDiarizer.MERGE, diarizer.OnlineDiarizer.SHORT_NEW = J, M, SN
    res = {}
    for k, E in emb_sets.items():
        A, K = [], []
        for t in range(25):
            random.seed(t); it = E[:]; random.shuffle(it)
            d = diarizer.OnlineDiarizer(); raw = [d.assign(e, sec) for _, e, sec in it]
            fin = [d.resolve(s) for s in raw]; A.append(acc([l for l, _, _ in it], fin)); K.append(len(set(fin)))
        res[k] = (np.mean(A), min(A), np.mean(K))
    worst_acc = min(v[0] for v in res.values())
    over = np.mean([v[2] for v in res.values()])
    rows.append((worst_acc, -abs(over - 5), J, M, SN, res))
rows.sort(key=lambda r: (round(r[0], 2), r[1]), reverse=True)
for worst, _, J, M, SN, res in rows[:6]:
    print("JOIN=%.2f MERGE=%.2f SHORT_NEW=%.2f  worst-set acc %.0f%%  |  %s" % (J, M, SN, 100 * worst,
          "  ".join("%s %.0f%%/%.1fspk" % (k, 100 * v[0], v[2]) for k, v in res.items())))
