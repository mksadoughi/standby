"""The AI caller's test audio (mac/testaudio.py): the mic from mic.pcm at real-time pace, never exact
zeros; Standby's voice to speaker.pcm as a speaker plays it, `playing` until the last sound, events with
wall-clock times; halt cuts it at once; duck holds it where it was; the echo comes back into the mic only
when asked for. Standby's voice here is tts.py's stand-in engine (80 ms of sound a word), never the model."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, time
import numpy as np

d = tempfile.mkdtemp(prefix="sbta-")
os.environ["STANDBY_TEST_AUDIO"] = d
os.environ["STANDBY_TTS_ENGINE"] = "fake"
os.environ.pop("STANDBY_TEST_ECHO", None)
sys.path.insert(0, "mac")
import testaudio  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print("  %-72s %s" % (name, "OK" if ok else "FAIL %s" % (detail,)))
    if not ok:
        fails.append(name)


def events():
    try:
        return [json.loads(l) for l in open(os.path.join(d, "events.jsonl"))]
    except OSError:
        return []


def wait(cond, secs):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.02)
    return cond()


print("the mic")
open(os.path.join(d, "mic.pcm"), "ab").write(np.full(16000, 0.5, "<f4").tobytes())    # said before the call
mic = testaudio.Mic.get()
got = []
mic.listen(got.append)
time.sleep(0.5)
heard = np.concatenate(got) if got else np.zeros(0)
check("what was said before the call isn't heard", heard.size > 0 and np.abs(heard).max() < 0.01, heard[:4])
check("its quiet is a mic's floor, never exact zeros", heard.size > 0 and np.count_nonzero(heard) == heard.size)
got.clear()
t0 = time.time()
open(os.path.join(d, "mic.pcm"), "ab").write(np.full(8000, 0.5, "<f4").tobytes())     # half a second, said now
wait(lambda: sum(np.count_nonzero(np.abs(b) > 0.4) for b in got) >= 8000, 3)
took = time.time() - t0
loud = sum(np.count_nonzero(np.abs(b) > 0.4) for b in got)
check("half a second said is heard whole", loud == 8000, loud)
check("at the pace it was said (0.5 s, not at once)", 0.4 <= took <= 0.9, round(took, 2))
mic.leave(got.append)

print("the sounddevice stand-in")
sd = testaudio.sounddevice_module()
check("one input, the test's", [x["name"] for x in sd.query_devices()] == [testaudio.MIC_NAME])
blocks = []
s = sd.InputStream(device=0, channels=1, samplerate=16000, dtype="float32", blocksize=1600,
                   callback=lambda x, n, _t, _s: blocks.append((x.shape, n)))
s.start()
wait(lambda: len(blocks) >= 2, 2)
s.close()
check("its blocks are the size asked for, (frames, 1)", blocks[:1] == [((1600, 1), 1600)], blocks[:1])

print("Standby's voice")
b = testaudio.engine(neural="alba", log=lambda m: None)
b.start_mic(testaudio.MIC_NAME, lambda x: None)
check("echo cancelled (no echo asked for)", b.aec is True)
wait(lambda: b.speech is not None and b.speech.usable, 10)
t0 = time.time()
b.say("one two three four five")          # 5 words: 0.4 s of sound
check("playing from say()", b.playing)
wait(lambda: not b.playing, 5)
took = time.time() - t0
check("playing until its last sound has played (about 0.4 s)", 0.35 <= took <= 1.5, round(took, 2))
pcm = np.fromfile(os.path.join(d, "speaker.pcm"), dtype="<f4")
check("all of it in speaker.pcm, at 24 kHz", abs(len(pcm) - 0.4 * 24000) <= 480, len(pcm))
ev = [e["kind"] for e in events()]
check("events: say, start, end", ev[:3] == ["say", "start", "end"], ev)
start = [e for e in events() if e["kind"] == "start"][0]
check("its start carries the sentence", start.get("text") == "one two three four five", start)
check("its own voice kept for its print", b.own_secs > 0.3 and b.own_audio, b.own_secs)

print("halt and duck")
b.say(" ".join(["word"] * 40))            # 3.2 s
wait(lambda: any(e["kind"] == "start" for e in events()[3:]), 3)
time.sleep(0.3)
b.halt()
time.sleep(0.15)
before = os.path.getsize(os.path.join(d, "speaker.pcm"))
time.sleep(0.4)
check("halt: nothing more plays", os.path.getsize(os.path.join(d, "speaker.pcm")) == before)
check("halt: not playing", not b.playing)
check("halt: in the events, with what had played", any(e["kind"] == "halt" and e.get("played_s", 0) > 0.1 for e in events()))
b.say(" ".join(["word"] * 20))            # 1.6 s
wait(lambda: b.played_s > 0.2, 3)
b.duck()
time.sleep(0.1)
at = os.path.getsize(os.path.join(d, "speaker.pcm"))
time.sleep(0.5)
check("duck: held where it was", os.path.getsize(os.path.join(d, "speaker.pcm")) == at)
b.unduck()
wait(lambda: not b.playing, 4)
check("unduck: the rest plays", os.path.getsize(os.path.join(d, "speaker.pcm")) > at)
b.stop_mic()

print("the echo")
testaudio.Mic._one = None
os.environ["STANDBY_TEST_ECHO"] = "0.5,40"
e = testaudio.engine(neural="alba", log=lambda m: None)
check("echo asked for: aec is False", e.aec is False)
loud = []
e.start_mic(testaudio.MIC_NAME, lambda x: loud.append(float(np.abs(x).max())))
wait(lambda: e.speech is not None and e.speech.usable, 10)
e.say("one two three four five six")
wait(lambda: not e.playing, 5)
time.sleep(0.2)
check("what it played came back into the mic", max(loud or [0]) > 0.01, max(loud or [0]))
e.stop_mic()

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
