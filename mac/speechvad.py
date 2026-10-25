"""Is it speech? A speech model's word on talking over Claude's voice.

The recorder cuts utterances with an energy VAD (UtteranceTranscriber.feed):
loud enough over the noise floor is speech. That is right for cutting, but a
cough, a laugh, a keyboard or a click is loud too, and 0.3 s of it stopped
Claude mid-sentence. Silero VAD v5 (snakers4, MIT) is a small recurrent model,
about 2 MB, a fraction of a millisecond a call on the CPU through onnxruntime,
trained to tell speech from everything else. Here it judges only the onset of a
barge-in; the energy VAD still opens and closes every utterance.

It wants exactly what Silero's own OnnxWrapper feeds it (utils_vad.py at tag
v5.1.2, followed here): 16 kHz audio in chunks of 512 samples, each with the 64
samples before it in front (zeros at the start), and the recurrent state
(2, 1, 128) carried from one chunk to the next. The output is the probability
that the chunk is speech.
"""

import os
from pathlib import Path

SAMPLE_RATE = 16000
CHUNK = 512               # samples a call: 32 ms
CONTEXT = 64              # samples of the chunk before, in front of each one
THRESHOLD = 0.5           # Silero's own default: speech from here up
MODEL_NAME = "silero_vad_v5.1.2.onnx"
MODEL_URL = ("https://raw.githubusercontent.com/snakers4/silero-vad/v5.1.2/"
             "src/silero_vad/data/silero_vad.onnx")
MODEL_SHA256 = "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
MODEL = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby")) / "models" / MODEL_NAME


class SileroVAD(object):
    """The model; stream() gives one utterance its own state."""

    def __init__(self, path=MODEL):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=opts,
                                            providers=["CPUExecutionProvider"])

    def stream(self):
        return Stream(self)

    def run(self, x, state):
        """One chunk: x (1, CONTEXT + CHUNK), state (2, 1, 128) -> (probability, new state)."""
        import numpy as np
        out, state = self.session.run(None, {"input": x, "state": state,
                                             "sr": np.array(SAMPLE_RATE, dtype="int64")})
        return float(np.asarray(out).reshape(-1)[0]), state


class Stream(object):
    """Audio of one utterance, fed as it comes: speechy is how many seconds of it
    the model calls speech (THRESHOLD or more). A part chunk waits for the rest."""

    def __init__(self, vad):
        import numpy as np
        self.vad = vad
        self.state = np.zeros((2, 1, 128), dtype="float32")
        self.context = np.zeros((1, CONTEXT), dtype="float32")
        self.carry = np.zeros(0, dtype="float32")
        self.probs = []
        self.speechy = 0.0

    def feed(self, audio):
        import numpy as np
        buf = np.concatenate([self.carry, np.asarray(audio, dtype="float32").reshape(-1)])
        n = len(buf) // CHUNK
        for i in range(n):
            x = np.concatenate([self.context, buf[i * CHUNK:(i + 1) * CHUNK][None, :]], axis=1)
            p, self.state = self.vad.run(x, self.state)
            self.context = x[:, -CONTEXT:]
            self.probs.append(p)
            if p >= THRESHOLD:
                self.speechy += CHUNK / float(SAMPLE_RATE)
        self.carry = buf[n * CHUNK:]
        return self.speechy


def load(path=MODEL, log=None):
    """The model, or None (no onnxruntime, no file): talking over Claude is then
    judged by loudness alone, as before."""
    if not Path(path).exists():
        if log:
            log("speechvad: no Silero VAD model at %s; talking over the voice is judged by loudness" % path)
        return None
    try:
        return SileroVAD(path)
    except Exception as exc:                      # onnxruntime missing or the file is bad
        if log:
            log("speechvad: Silero VAD unavailable (%r); talking over the voice is judged by loudness" % exc)
        return None
