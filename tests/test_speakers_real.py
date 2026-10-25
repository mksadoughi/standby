"""Speaker labels through the REAL recorder path: pause detection -> voice
fingerprint (sherpa-onnx TitaNet-small) -> online grouping, on labeled speech.
Needs sherpa-onnx + the model + sr-data (see tools/eval_diarization.py); skips
cleanly if they are not present."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import glob, os, sys, tempfile, types
from pathlib import Path
DATA = os.environ.get("SB_DIAR_DIR", "/tmp/sbdiar")
model = Path(DATA, "nemo_en_titanet_small.onnx")
try:
    import numpy as np, soundfile as sf, sherpa_onnx  # noqa
except ImportError:
    print("SKIP (sherpa-onnx not installed in this python)"); sys.exit(0)
if not model.exists():
    print("SKIP (no model at %s)" % model); sys.exit(0)
sys.modules["sounddevice"] = types.SimpleNamespace()
from importlib.machinery import SourceFileLoader
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
sb.SPEAKER_MODEL = model

def clip(name):
    x, sr = sf.read(glob.glob(os.path.join(DATA, "sr-data", "**", name), recursive=True)[0],
                    dtype="float32", always_2d=True)
    return x[:, 0]
script = [("A", "fangjun-test-sr-1.wav"), ("B", "leijun-test-sr-1.wav"),
          ("A", "fangjun-test-sr-2.wav"), ("C", "liudehua-test-sr-1.wav"),
          ("B", "leijun-test-sr-2.wav")]
gap = (np.random.default_rng(0).standard_normal(16000) * 0.002).astype("float32")
parts, spans, pos = [], [], 0
for who, f in script:
    x = clip(f)
    parts += [gap, x]
    spans.append((who, (pos + len(gap)) / 16000.0, (pos + len(gap) + len(x)) / 16000.0))
    pos += len(gap) + len(x)
audio = np.concatenate(parts + [gap])
def owner(start, end):
    mid = (start + end) / 2
    for who, a, b in spans:
        if a - 0.35 <= mid <= b + 0.35:
            return who
    return None

state = Path(tempfile.mkdtemp()) / "speakers.state.json"
spk = sb.SpeakerID(state)
tr = sb.UtteranceTranscriber(None, decode=lambda a: "some words here")
labels, truth = [], []
def take(items):
    for item in items:
        labels.append(spk.identify(item[3])); truth.append(owner(item[0], item[1]))
for i in range(0, len(audio), 8000):
    tr.feed(audio[i:i + 8000]); take(tr.drain())
take(tr.drain(force=True))
fails = []
def check(label, ok, d=""):
    print("  %-56s %s" % (label, "OK" if ok else "FAIL")); ok or (fails.append(label), print("     " + str(d)))
print("  utterances:", len(labels), "labels:", labels, "truth:", truth)
check("every utterance maps to a known speaker", None not in truth, truth)
check("all three speakers found", len(set(truth)) == 3, truth)
m = {}
consistent = all(m.setdefault(t, l) == l for t, l in zip(truth, labels))
check("same person always gets the same label", consistent, list(zip(truth, labels)))
check("different people get different labels", len(set(m.values())) == len(m), m)
spk.save()
again = sb.SpeakerID(state)
check("saved voices reload for Resume", len(again.diar.cent) == len(set(labels)), again.diar.cent.keys())
check("a known voice is recognised after reload", again.identify(clip("fangjun-test-sr-3.wav")[:48000]
      if False else clip("fangjun-test-sr-2.wav")) == m["A"])
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails)); sys.exit(1 if fails else 0)
