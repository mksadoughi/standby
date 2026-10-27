"""A quiet voice in a meeting: a far laptop mic, a low-gain one (a VM run: 76 s of talk at
0.002 to 0.01 RMS over a room at 0.0001, and 2 lines). The recorder's own VAD
(UtteranceTranscriber) cuts it into utterances and, with the speech model here, its words
come out; a fan that starts mid-meeting is still not speech. Speech made with `say`."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, shutil, subprocess, sys, tempfile, wave
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import standby as sb  # noqa: E402

sb.log = lambda *a, **k: None
fails = []
SR = 16000


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     %s" % (d,))


if not (shutil.which("say") and shutil.which("afconvert")):
    print("  (skipped: needs say and afconvert)")
    print("\nALL PASS")
    raise SystemExit(0)

root = Path(tempfile.mkdtemp(prefix="sbquiet-"))
SENTENCES = ["Let's move the launch to Friday, after the tests pass.",
             "Robin will write the release notes this afternoon.",
             "Sam asked whether the website needs a new picture."]
clips = []
for i, text in enumerate(SENTENCES):
    aiff, wav = str(root / ("%d.aiff" % i)), str(root / ("%d.wav" % i))
    subprocess.run(["say", "-o", aiff, text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav], check=True)
    w = wave.open(wav)
    clips.append(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0)

rng = np.random.default_rng(7)
room = lambda secs, level=0.0001: rng.normal(0, level, int(secs * SR)).astype("float32")


def quiet(clip, peak=0.012):
    """As the VM's mic heard the user: most of the speech about 0.003 RMS (its median was 0.0025)."""
    return (clip * (peak / float(np.abs(clip).max()))).astype("float32")


parts, spans, t = [room(2.0)], [], 2.0
for clip in clips:
    q = quiet(clip)
    parts += [q + room(len(q) / SR), room(2.0)]
    spans.append((t, t + len(q) / SR))
    t += len(q) / SR + 2.0
audio = np.concatenate(parts)
frames = np.sqrt((audio[:len(audio) // 480 * 480].reshape(-1, 480) ** 2).mean(axis=1))
speechy = frames[frames > 0.0005]
check("the made-up meeting is as quiet as the VM's: most speech frames under 0.004 RMS",
      np.median(speechy) < 0.004, np.median(speechy))


def cut(audio, decode=lambda a: "some words"):
    """The utterances the recorder cuts from `audio`, fed in 0.5 s blocks: [(start, end)]."""
    st = sb.UtteranceTranscriber(None, decode=decode)
    out = []
    real_close = st._close_utt

    def close(end_pos):
        if st.voiced >= st.MIN_SPEECH:
            out.append((st.utt_start / float(SR), end_pos / float(SR)))
        real_close(end_pos)
    st._close_utt = close
    for i in range(0, len(audio), SR // 2):
        st.feed(audio[i:i + SR // 2])
    return out, st


utts, st = cut(audio)
for (a, b), text in zip(spans, SENTENCES):
    covered = sum(max(0.0, min(b, u1) - max(a, u0)) for u0, u1 in utts)
    check("a quiet sentence is cut into an utterance (%.0f%% of it): %s" % (100 * covered / (b - a), text[:30]),
          covered >= 0.8 * (b - a), (round(a, 1), round(b, 1), utts))
check("  and the room between them is not", all(any(a - 0.5 <= u0 and u1 <= b + 1.0 for a, b in spans) for u0, u1 in utts), utts)

# A fan that starts mid-meeting (0.004 RMS, louder than the quiet voice's floor): at most one
# utterance while the floor catches up, then none.
fan = np.concatenate([room(3.0), rng.normal(0, 0.004, 20 * SR).astype("float32")])
utts, st = cut(fan)
check("a fan that starts: at most one short utterance, then the floor has it",
      len(utts) <= 1 and all(u1 - u0 < 5.0 for u0, u1 in utts) and st.noise > 0.003, (utts, st.noise))

# A call's mic through echo cancellation keeps the floor it had: its gain is controlled, and
# Claude's voice is faintly left in it.
check("a call's echo-cancelled mic keeps its floor",
      sb.UtteranceTranscriber.ENGINE_MIN_RMS == 0.006 and sb.spoken_part.__defaults__[1] == 0.006)

# The words, with the speech model here (never downloaded by a test).
hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
if not (hub / "models--mlx-community--parakeet-tdt-0.6b-v2").is_dir():
    print("  (words part skipped: needs the cached speech model)")
else:
    os.environ["HF_HUB_OFFLINE"] = "1"
    from parakeet_mlx import from_pretrained
    model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v2")
    st = sb.UtteranceTranscriber(model)
    for i in range(0, len(audio), SR // 2):
        st.feed(audio[i:i + SR // 2])
    st.feed(room(1.0))
    heard = " ".join(x[2] if isinstance(x, tuple) else str(x) for x in st.ready).lower()
    print("     heard: %r" % heard)
    for word in ("friday", "release notes", "website"):
        check("  quiet speech decoded: %r" % word, word in heard, heard)

shutil.rmtree(root, ignore_errors=True)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
raise SystemExit(1 if fails else 0)
