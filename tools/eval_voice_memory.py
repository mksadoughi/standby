# Run from a dir with sr-data/, libri.parquet and nemo_en_titanet_small.onnx (see eval_diarization.py).
"""Cross-session voice matching: a voice saved from one meeting (centroid of its
utterances) vs the cluster of a new meeting after its first few utterances."""
import io, glob, os, re, random, numpy as np, soundfile as sf, sherpa_onnx, pyarrow.parquet as pq
from collections import defaultdict
ex = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model="nemo_en_titanet_small.onnx", num_threads=4))
def emb(x):
    s = ex.create_stream(); s.accept_waveform(sample_rate=16000, waveform=x); s.input_finished()
    e = np.array(ex.compute(s)); return e / np.linalg.norm(e)
utts = defaultdict(list)     # speaker -> [(seconds, emb)]
t = pq.read_table("libri.parquet").to_pylist()
for r in t:
    a = r["audio"]; x, sr = sf.read(io.BytesIO(a["bytes"] if isinstance(a, dict) else a), dtype="float32")
    if x.ndim > 1: x = x[:, 0]
    # meeting-like utterances: cut into 2-6 s pieces
    i = 0
    while i + 2 * sr <= len(x):
        n = random.Random(i).randint(2, 6) * sr
        utts["libri-%s" % r["speaker_id"]].append((min(n, len(x) - i) / sr, emb(x[i:i + n]))); i += n
for p in glob.glob("sr-data/**/*.wav", recursive=True):
    b = os.path.basename(p); m = re.match(r"(speaker\d)", b) or re.match(r"([a-z]+)-", b)
    x, sr = sf.read(p, dtype="float32", always_2d=True); x = x[:, 0]
    for i in range(0, len(x) - 2 * sr + 1, 3 * sr):
        utts["sr-" + m.group(1)].append((min(3 * sr, len(x) - i) / sr, emb(x[i:i + 3 * sr])))
spk = [s for s, u in utts.items() if len(u) >= 6]
print("speakers:", len(spk), "utterances:", sum(len(utts[s]) for s in spk))
def cent(es):
    c = np.sum(es, axis=0); return c / np.linalg.norm(c)
for k in (1, 2, 3, 5):             # utterances heard in the new meeting before deciding
    same, diff = [], []
    for trial in range(40):
        rng = random.Random(trial)
        saved, probe = {}, {}
        for s in spk:
            u = utts[s][:]; rng.shuffle(u); half = len(u) // 2
            saved[s] = cent([e for _, e in u[:half]]); probe[s] = cent([e for _, e in u[half:half + k]])
        for s in spk:
            for v in spk:
                (same if s == v else diff).append(float(saved[v] @ probe[s]))
    same, diff = np.array(same), np.array(diff)
    print("\nafter %d utterance(s): same person p5=%.2f median=%.2f | different people p99=%.2f max=%.2f"
          % (k, np.percentile(same, 5), np.median(same), np.percentile(diff, 99), diff.max()))
    for th in (0.45, 0.50, 0.55, 0.60, 0.65):
        print("  threshold %.2f: recognized %.0f%% of the time, wrong name %.2f%% of pairs"
              % (th, 100 * (same >= th).mean(), 100 * (diff >= th).mean()))
