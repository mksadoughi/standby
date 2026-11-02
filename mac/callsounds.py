"""The call sounds (ui.call_sound), made from scratch: a soft mallet on felt, round and close, quiet
(-28 dBFS RMS, never above -13 dBFS peak) and soft at the start. Four moments: incoming (Claude asks to
call), connecting, message (something arrived, off a call) and ending.

They are made, never committed (the repository holds no audio): codecopy makes them in the apps' copy of the
code, at install and at any `standby` command after an update, only when they aren't there as SHA256 has
them (a look at the files, no numpy). Not made (no numpy, a checkout never installed): no sound, no error.

    python mac/callsounds.py DIR         makes them in DIR
    python mac/callsounds.py --check     says whether what it makes is SHA256's (tests/test_call_sounds.py)
"""
import hashlib
import io
import os
import sys
import wave

SR = 44100
RMS = 10 ** (-28 / 20.0)           # each as loud as the others: quiet, like a phone turned down
PEAK = 10 ** (-13 / 20.0)          # and never peaks above -13 dBFS
SHORT = 0.58                       # connecting, message and ending stay under 0.6 s
# Each file as made here, the user's choice (Felt, 2026-11-01): what's made is checked against these
SHA256 = {"incoming": "373715c211d08688b57abf02f34386c0791e265929339ae41447d37df2ec2cb4",
          "connecting": "4b0b0fd963c74f70ebbd233f8698b145e67e49a2249f303167e4bf1fc6722652",
          "message": "69f044773c2982ba65a1022dbec8006894a6fb3d402c10cac9b14a4176b1e3e2",
          "ending": "220f0b5cd8309ac9b38ebbb2e3545e1116d3e3b8726c071d42db18f6a41990e5"}

G4, C5, E5 = 392.00, 523.25, 659.25


np = None                          # numpy, imported when they're made (a look at the files never needs it)


def t(dur):
    return np.arange(int(dur * SR)) / SR


def attack(n, secs):
    """A raised-cosine fade-in: no click, no edge."""
    a = min(n, max(1, int(secs * SR)))
    env = np.ones(n)
    env[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    return env


def fade_out(x, secs=0.04):
    n = min(len(x), int(secs * SR))
    x = x.copy()
    x[-n:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(n) / n)
    return x


def place(parts, total):
    out = np.zeros(int(total * SR))
    for at, x in parts:
        i = int(at * SR)
        n = min(len(x), len(out) - i)
        out[i:i + n] += x[:n]
    return out


def felt(f, dur=0.5, tau=0.16, gain=1.0):
    """One note: a soft mallet on felt."""
    tt = t(dur)
    x = np.sin(2 * np.pi * f * tt) + 0.22 * np.sin(2 * np.pi * 2 * f * tt) + 0.05 * np.sin(2 * np.pi * 3 * f * tt)
    x *= np.exp(-tt / tau) * attack(len(tt), 0.016)
    return gain * x


def finish(x, short=True):
    if short:
        x = x[:int(SHORT * SR)]
    x = fade_out(x)
    x = x * RMS / (np.sqrt(np.mean(x ** 2)) or 1)
    return x * min(1.0, PEAK / np.max(np.abs(x)))


def sounds():
    global np
    import numpy
    np = numpy
    return {
        # two notes rising, asked twice: a polite knock, not a ring
        "incoming": finish(place([(0.0, felt(G4, 0.5)), (0.16, felt(C5, 0.5)),
                                  (0.74, felt(G4, 0.5)), (0.90, felt(C5, 0.55))], 1.48), short=False),
        # two quick notes going up: on its way
        "connecting": finish(place([(0.0, felt(C5, 0.4, 0.10, 0.7)), (0.13, felt(E5, 0.42, 0.12, 0.55))], 0.58)),
        # a single soft tap: there's something here
        "message": finish(felt(E5, 0.58, 0.14)),
        # the incoming pair turned downward: settled, done
        "ending": finish(place([(0.0, felt(C5, 0.4, 0.12)), (0.15, felt(G4, 0.43, 0.15, 0.9))], 0.58)),
    }


def wav_bytes(x):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return b.getvalue()


def _sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def ready(folder):
    """The four files are in `folder`, each as SHA256 has it."""
    return all(_sha(os.path.join(folder, name + ".wav")) == want for name, want in SHA256.items())


def ensure(folder):
    """The four files in `folder`, made where one is missing or not as it should be: the names written (none
    when they're right already, so an unchanged copy is never touched). Never raises: not made, no sound."""
    if ready(folder):
        return []
    try:
        made = {name: wav_bytes(x) for name, x in sounds().items()}
        os.makedirs(folder, exist_ok=True)
        wrote = []
        for name, data in made.items():
            path = os.path.join(folder, name + ".wav")
            if _sha(path) == hashlib.sha256(data).hexdigest():
                continue
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
            wrote.append(name + ".wav")
        return wrote
    except Exception:
        return []


def main(argv):
    if "--check" in argv:
        wrong = [name for name, x in sounds().items() if hashlib.sha256(wav_bytes(x)).hexdigest() != SHA256[name]]
        print("not as SHA256 has them: " + ", ".join(wrong) if wrong else "all four as SHA256 has them")
        return 1 if wrong else 0
    if not argv:
        print(__doc__.strip().splitlines()[-2].strip())
        return 2
    for name in ensure(argv[0]):
        print(os.path.join(argv[0], name))
    return 0 if ready(argv[0]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
