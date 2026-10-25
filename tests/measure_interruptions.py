"""False interruptions, before and after, on speech made by `say` into files
(nothing is played) and on made-up noises, through the recorder's own
UtteranceTranscriber (its VAD, voice mode's 0.45 s close, the real parakeet model).

For each utterance said over Claude:
  before (main): the recorder's barge_in stops Claude once 0.3 s of it is voiced;
  after:   barge_in pauses; its words (interrupt.classify) decide: a
                 backchannel or noise and Claude carries on, a wait waits, a
                 cancel or real words stop it.
A false cut-off is Claude stopped (or waiting) for a backchannel or a noise; a
missed one is Claude carrying on over a real interruption or a command.

Not part of run_all: it loads the model (a few seconds) and takes about a minute.
    ~/.standby/venv/bin/python tests/measure_interruptions.py [--voices N] [--skip N] [--no-model] [--speech-vad]
--no-model: the recorder's VAD only (what makes barge_in fire: "before" is
exact), and the words as written in place of parakeet's ("after" is then the
classifier's best case).
--speech-vad: also what starts a barge-in with the speech model (Q3, speechvad):
how many utterances and noises trip the onset by loudness, and how many by Silero.
Its model from SB_SILERO (a path), else the installed one in ~/.standby/models.
"""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import os, re, shutil, subprocess, sys, tempfile, time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import numpy as np  # noqa: E402
import interrupt  # noqa: E402
import speechvad  # noqa: E402
import standby as sb  # noqa: E402

I = interrupt
CORPUS = [  # (what is said, what it is)
    ("Mm-hmm.", I.BACKCHANNEL), ("Uh-huh.", I.BACKCHANNEL), ("Yeah.", I.BACKCHANNEL),
    ("Yeah, yeah.", I.BACKCHANNEL), ("Right.", I.BACKCHANNEL), ("Okay.", I.BACKCHANNEL),
    ("Got it.", I.BACKCHANNEL), ("I see.", I.BACKCHANNEL), ("Sure.", I.BACKCHANNEL),
    ("Hmm.", I.BACKCHANNEL), ("Oh, interesting.", I.BACKCHANNEL), ("Makes sense.", I.BACKCHANNEL),
    ("Thanks.", I.BACKCHANNEL), ("Ha ha.", I.BACKCHANNEL), ("Wow.", I.BACKCHANNEL), ("Exactly.", I.BACKCHANNEL),
    ("Never mind.", I.CANCEL), ("Stop.", I.CANCEL), ("Forget it.", I.CANCEL), ("Okay, never mind.", I.CANCEL),
    ("Hold on.", I.WAIT), ("Wait.", I.WAIT), ("One sec.", I.WAIT), ("Hang on a second.", I.WAIT),
    ("Wait, what about the hiring budget?", I.REAL), ("No, I meant the other project.", I.REAL),
    ("Actually, can you check my calendar instead?", I.REAL), ("What?", I.REAL), ("No.", I.REAL),
    ("Sorry, say that again?", I.REAL), ("Hold on, is that for Q3 or Q4?", I.REAL),
    ("Stop the deployment first.", I.REAL), ("And what did Nadia say?", I.REAL),
    ("Yeah, but how much does it cost?", I.REAL), ("Okay, and the EU region?", I.REAL),
]
# The first six tuned the word lists (2026-10-08); the rest are held out, for an honest number.
VOICES = ["Samantha", "Daniel", "Karen", "Moira", "Flo (English (UK))", "Fred",
          "Eddy (English (US))", "Reed (English (UK))", "Kathy", "Ralph", "Shelley (English (US))",
          "Sandy (English (UK))", "Rocko (English (US))", "Tessa", "Rishi", "Grandma (English (US))"]
RATE = sb.SAMPLE_RATE


def installed():
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    names = {m.group(1).strip() for m in (re.match(r"^(.+?)\s+[a-z]{2,3}_[A-Z]{2}\s+#", l) for l in out.splitlines()) if m}
    return [v for v in VOICES if v in names]


def speech(text, voice, d):
    aiff, wav = os.path.join(d, "u.aiff"), os.path.join(d, "u.wav")
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True)       # a file: nothing is played
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@%d" % RATE, "-c", "1", aiff, wav], check=True)
    import wave
    w = wave.open(wav)
    return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32") / 32768.0


def noises(seed=7):
    """Things a mic hears over Claude that are not words: a cough, a breath, a knock, a chair."""
    r = np.random.RandomState(seed)
    t = lambda s: np.arange(int(s * RATE)) / float(RATE)
    out = []
    for i in range(4):                                                   # coughs: a burst, quick decay
        n = t(0.35 + 0.05 * i)
        burst = r.randn(len(n)) * np.exp(-n * (9 + 2 * i)) * (0.25 + 0.05 * i)
        out.append(("(cough %d)" % (i + 1), np.convolve(burst, np.ones(6) / 6, "same").astype("float32")))
    for i in range(2):                                                   # breaths: soft and long
        n = t(0.6)
        out.append(("(breath %d)" % (i + 1), (r.randn(len(n)) * 0.02 * np.sin(np.pi * n / 0.6)).astype("float32")))
    k = np.zeros(int(0.2 * RATE), "float32")
    k[:200] = r.randn(200) * 0.6
    out.append(("(knock)", k))
    n = t(0.5)
    out.append(("(chair)", (np.sin(2 * np.pi * 180 * n) * 0.1 * np.exp(-n * 4) + r.randn(len(n)) * 0.03).astype("float32")))
    return out


def recorder_running():
    """A recorder is running (the user's meeting): the model is theirs, wait."""
    return subprocess.run(["pgrep", "-f", "standby.py run"], capture_output=True).returncode == 0


def wait_for_quiet(most=600):
    t0 = time.time()
    while recorder_running():
        if time.time() - t0 > most:
            sys.exit("a recorder has been running for %d s: stopped, try later" % most)
        print("  (a recorder is running: waiting)", flush=True)
        time.sleep(10)


def silero():
    """The speech model for --speech-vad: SB_SILERO, else the installed one (read only)."""
    path = os.environ.get("SB_SILERO") or os.path.join(os.path.expanduser("~/.standby"), "models", speechvad.MODEL_NAME)
    return speechvad.load(path, log=print) if os.path.exists(path) else sys.exit("no Silero model at %s" % path)


def over_claude(model, audio, written=None, vad=None):
    """The recorder's stream on this, in voice mode, fed as the mic feeds it.
    -> (most voiced while open: barge_in fires at 0.3 s, the texts it closed with,
    most seconds of it the speech model calls speech, as Channel.speechy does; 0 without vad).
    No model: the VAD alone, and `written` as what was heard."""
    st = sb.UtteranceTranscriber(model, decode=None if model is not None else (lambda a: written or ""))
    st.end_silence = sb.Channel.VOICE_END_SILENCE
    room = np.random.RandomState(1).randn(int(0.8 * RATE)).astype("float32") * 0.002       # a quiet room
    tail = np.random.RandomState(2).randn(int(1.0 * RATE)).astype("float32") * 0.002
    mic = np.concatenate([room, audio + np.random.RandomState(3).randn(len(audio)).astype("float32") * 0.002, tail])
    most, block, speechy, heard = 0.0, int(0.05 * RATE), 0.0, None
    for i in range(0, len(mic), block):
        st.feed(mic[i:i + block])
        if st.utt is not None:
            most = max(most, st.voiced)
            if vad is not None:
                if heard is None or heard[0] is not st.utt:
                    heard = [st.utt, 0, vad.stream()]
                heard[2].feed(np.concatenate(st.utt[heard[1]:]))
                heard[1] = len(st.utt)
                speechy = max(speechy, heard[2].speechy)
    return most, [text for _s, _e, text, _a in st.drain(force=True)], speechy


def main():
    n_voices = int(sys.argv[sys.argv.index("--voices") + 1]) if "--voices" in sys.argv else 6
    skip = int(sys.argv[sys.argv.index("--skip") + 1]) if "--skip" in sys.argv else 0     # held-out voices
    voices = installed()[skip:skip + n_voices]
    wait_for_quiet()
    model = None if "--no-model" in sys.argv else sb.load_model(sb.DEFAULT_MODEL)
    vad = silero() if "--speech-vad" in sys.argv else None
    d = tempfile.mkdtemp(prefix="sbmeasure-")
    rows, onsets = [], []
    try:
        for voice in voices:
            for text, kind in CORPUS:
                wait_for_quiet()
                most, said, speechy = over_claude(model, speech(text, voice, d), text, vad)
                rows.append((voice, text, kind, most, " ".join(said)))
                onsets.append((kind, text, most, speechy))
        for name, audio in noises():
            most, said, speechy = over_claude(model, audio, vad=vad)
            rows.append(("-", name, I.NOISE, most, " ".join(said)))
            onsets.append((I.NOISE, name, most, speechy))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    barge = sb.VoiceIO.BARGE_MIN
    tally = defaultdict(Counter)
    wrong = []
    for voice, text, kind, most, said in rows:
        fired = most >= barge
        heard = interrupt.classify(said)
        # Before: any barge_in stops Claude. After: the words decide.
        before = "stops" if fired else "carries on"
        after = "carries on" if not fired else {I.BACKCHANNEL: "carries on", I.NOISE: "carries on",
                                                I.RESUME: "carries on", I.SHORT: "carries on", I.WAIT: "waits",
                                                I.CANCEL: "stops", I.REAL: "stops"}[heard]
        group = "not words" if kind in (I.BACKCHANNEL, I.NOISE) else "for Claude"
        want = "carries on" if group == "not words" else ("waits" if kind == I.WAIT else "stops")
        tally[group]["n"] += 1
        tally[group]["before ok"] += before == want or (want == "waits" and before == "stops")
        tally[group]["after ok"] += after == want
        if after != want:
            wrong.append((voice, text, said, heard, round(most, 2), after, want))
        if group == "not words":
            tally[group]["false before"] += before != "carries on"
            tally[group]["false after"] += after != "carries on"
        else:
            tally[group]["missed before"] += before == "carries on"
            tally[group]["missed after"] += after == "carries on"
            tally[group]["command right"] += kind in (I.CANCEL, I.WAIT) and heard == kind
            tally[group]["commands"] += kind in (I.CANCEL, I.WAIT)

    print("%d utterances over Claude (%d voices x %d, %d noises), %s"
          % (len(rows), len(voices), len(CORPUS), len(noises()),
             "parakeet " + sb.DEFAULT_MODEL if model is not None else "VAD only, the words as written"))
    nw, fc = tally["not words"], tally["for Claude"]
    print("  backchannels and noises: %d" % nw["n"])
    print("    false cut-offs before: %d (%.0f%%)   after: %d (%.0f%%)"
          % (nw["false before"], 100.0 * nw["false before"] / nw["n"], nw["false after"], 100.0 * nw["false after"] / nw["n"]))
    print("  interruptions and commands: %d" % fc["n"])
    print("    missed before: %d   after: %d   commands understood (cancel, wait): %d of %d"
          % (fc["missed before"], fc["missed after"], fc["command right"], fc["commands"]))
    print("  how parakeet wrote them, where the classifier gets it wrong:")
    for w in wrong:
        print("    %-22s %-40r heard %-28r -> %-11s (%.2fs voiced): %s, wanted %s" % w)
    spellings = defaultdict(set)
    for voice, text, kind, most, said in rows:
        if kind == I.BACKCHANNEL:
            spellings[text].add(said)
    print("  parakeet's spellings of the backchannels:")
    for text in sorted(spellings):
        print("    %-18s %s" % (text, sorted(spellings[text])))
    if vad is not None:
        print("  --speech-vad: what trips the onset (0.3 s), by loudness and by Silero (speechvad)")
        print("    %-26s %5s %8s %8s" % ("", "n", "energy", "silero"))
        for kind in (I.NOISE, I.BACKCHANNEL, I.CANCEL, I.WAIT, I.REAL):
            got = [o for o in onsets if o[0] == kind]
            print("    %-26s %5d %8d %8d" % (kind, len(got), sum(o[2] >= barge for o in got), sum(o[3] >= barge for o in got)))
        for kind, text, most, speechy in onsets:
            if kind == I.NOISE:
                print("    %-26s energy %.2fs  silero %.2fs" % (text, most, speechy))
        missed = [(text, round(most, 2), round(speechy, 2)) for kind, text, most, speechy in onsets
                  if kind in (I.CANCEL, I.WAIT, I.REAL) and most >= barge and speechy < barge]
        print("    words for Claude the energy onset catches and Silero doesn't: %s" % (missed or "none"))


if __name__ == "__main__":
    main()
