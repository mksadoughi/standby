"""All MLX work must run on the main thread, and failures must carry a traceback.

Fakes sounddevice (callback on its own thread, like PortAudio) and the model, so
this runs without a Mac. The fake transcriber raises if touched off the main
thread, reproducing MLX's thread-local stream error.
"""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import sys, threading, time, types
import numpy as np
from importlib.machinery import SourceFileLoader

sb = SourceFileLoader("sb", "mac/standby.py").load_module()
MAIN = threading.main_thread()


class FakeInput(object):
    def __init__(self, callback=None, **kw):
        self.cb, self._t, self._run = callback, None, False
    def start(self):
        self._run = True
        def loop():
            while self._run:
                self.cb(np.full((8000, 1), 0.1, "float32"), 8000, None, None)
                time.sleep(0.05)
        self._t = threading.Thread(target=loop, daemon=True); self._t.start()
    def stop(self): self._run = False
    def close(self): pass

sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput,
    query_devices=lambda i=None: [{"name": "Fake Mic", "max_input_channels": 1}] if i is None
        else {"name": "Fake Mic", "max_input_channels": 1},
    default=types.SimpleNamespace(device=[0, 0]))


class FakeStream(object):
    n = 0
    def __init__(self, model, rotate):
        self.fed = 0
    def _guard(self):
        if threading.current_thread() is not MAIN:
            raise RuntimeError("There is no Stream(gpu, 0) in current thread.")
    def feed(self, block): self._guard(); self.fed += 1
    def drain(self, force=False):
        self._guard()
        if self.fed and self.fed % 4 == 0:
            FakeStream.n += 1
            return [(float(FakeStream.n), float(FakeStream.n) + .5, "line %d" % FakeStream.n)]
        return []
    def rotate(self): return self.drain(force=True)
    def _close(self): pass

sb.UtteranceTranscriber = FakeStream
fails = []
def check(label, ok, detail=""):
    print("  %-50s %s" % (label, "OK" if ok else "FAIL"))
    if not ok: fails.append(label); detail and print("     " + detail)

print("=== 1. ASR runs on the main thread and emits ===")
got = []
ch = sb.Channel("Room", 0, None, lambda *a: got.append(a), None, 300)
ch.open()
deadline = time.time() + 2
while time.time() < deadline:
    ch.pump(); time.sleep(0.05)
ch.close()
check("no error (MLX never touched off main thread)", ch.error is None, repr(ch.error))
check("emitted transcript lines", len(got) >= 2, "got=%d" % len(got))

print("\n=== 2. the OLD design reproduces the real crash ===")
err = []
def old_worker():
    try:
        FakeStream(None, 300).feed(None)
    except RuntimeError as e:
        err.append(str(e))
t = threading.Thread(target=old_worker); t.start(); t.join()
check("MLX use from a worker thread raises", bool(err), "no error raised")

print("\n=== 3. a channel failure carries a traceback ===")
class Boom(FakeStream):
    def feed(self, block): raise ValueError("simulated decode failure")
sb.UtteranceTranscriber = Boom
ch = sb.Channel("Room", 0, None, lambda *a: None, None, 300)
ch.open(); time.sleep(0.2); ch.pump()
check("error recorded", isinstance(ch.error, ValueError), repr(ch.error))
check("traceback captured for shipping",
      bool(ch.traceback) and "simulated decode failure" in ch.traceback)
print("\n=== 4. Early words: the app sees them before the close ===")
class EarlyStream(FakeStream):
    """Early words after the first block; the rest held, as UtteranceTranscriber does."""
    def __init__(self, model, rotate):
        FakeStream.__init__(self, model, rotate)
        self.early, self.carry_n, self.blocks = None, 0, []
    @property
    def held(self): return self.carry_n > 0
    def feed(self, block):
        self._guard()
        self.blocks.append(len(block))
        if self.carry_n:
            self.carry_n = 0                    # the held audio goes first
        elif len(self.blocks) == 1:
            self.early, self.carry_n = ("words",), 1
    def drain(self, force=False): return []
sb.UtteranceTranscriber = EarlyStream
ch = sb.Channel("Room", 0, None, lambda *a: None, None, 300)
ch._stream = EarlyStream(None, 300)
for _ in range(3):
    ch.audio.put(np.zeros(8000, "float32"))
ch.pump()
check("early words waiting: pump stops, the rest stays queued", ch._stream.blocks == [8000]
      and ch.audio.qsize() == 2 and ch._stream.early is not None, repr((ch._stream.blocks, ch.audio.qsize())))
ch._stream.early = None                          # likely_turn took them
ch.pump()
check("  next pump: the held audio first, then the queue", ch._stream.blocks == [8000, 0, 8000, 8000]
      and ch.audio.qsize() == 0, repr(ch._stream.blocks))
ch._stream = EarlyStream(None, 300)
for _ in range(3):
    ch.audio.put(np.zeros(8000, "float32"))
ch.pump()
ch.pump()                                        # nobody took them (no turn going)
check("  words nobody takes: handed back once only, the audio never stalls",
      ch.audio.qsize() == 0 and ch._stream.blocks == [8000, 0, 8000, 8000], repr(ch._stream.blocks))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
