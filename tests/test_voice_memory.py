"""Remembered voices: naming a speaker in the app teaches the library; a later
session names that voice by itself, never overrides a name the user gave, and
stays quiet when two remembered voices are too alike to tell apart.
With sherpa-onnx and the model (SB_DIAR_DIR, default /tmp/sbdiar) it also runs
on real speech: enroll from one recording, recognize in another."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import glob, json, os, sys, tempfile, types
from pathlib import Path
import numpy as np

root = Path(tempfile.mkdtemp(prefix="sbvm-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, "mac")
sys.path.insert(0, "plugin/bin")
from importlib.machinery import SourceFileLoader
sys.modules.setdefault("sounddevice", types.SimpleNamespace())
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
sb.ROOT = root
import ui  # noqa: E402
import sbspeakers  # noqa: E402
ui.log = lambda m: None
(root / "config.json").write_text(json.dumps({"agent": {"mode": "local"}}))
fails = []


def check(label, ok, d=""):
    print("  %-60s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


rng = np.random.default_rng(0)
unit = lambda v: v / np.linalg.norm(v)
ALEX, SAM, SAM2 = unit(rng.normal(size=192)), unit(rng.normal(size=192)), None
SAM2 = unit(SAM + 0.05 * unit(rng.normal(size=192)))      # a near-twin of Sam
voice = lambda v, noise=0.35: unit(v + noise * unit(rng.normal(size=192)))


def session(name, voices):
    """A finished session whose speaker 1..n had these voices."""
    sdir = root / "sessions" / name
    sdir.mkdir(parents=True, exist_ok=True)
    d = sb.OnlineDiarizer()
    for v in voices:
        for _ in range(4):
            d.assign(voice(v), 3.0)
    (sdir / "speakers.state.json").write_text(json.dumps(d.to_json()))
    return sdir


# 1. Name speakers in a first session: the library learns them.
session("m1", [ALEX, SAM])
ui.rename_speaker("m1", "1", "Alex")
ui.rename_speaker("m1", "2", "Sam")
lib = json.loads((root / "voices.json").read_text())["voices"]
check("naming a speaker remembers the voice", sorted(lib) == ["Alex", "Sam"], sorted(lib))
check("  as a unit vector with its utterance count",
      abs(np.linalg.norm(lib["Alex"]["emb"]) - 1) < 1e-6 and lib["Alex"]["n"] == 4, lib["Alex"]["n"])
check("  voice prints are private to the user", oct((root / "voices.json").stat().st_mode & 0o777) == "0o600")

# 2. A new session recognizes them once they have said enough.
sdir = root / "sessions" / "m2"
sdir.mkdir(parents=True)
published = []
sp = sb.SpeakerID(sdir / "speakers.state.json", on_known=published.append)
check("the next session loads remembered voices", sorted(sp.voices) == ["Alex", "Sam"], sorted(sp.voices))
a1, _ = sp.diar.assign(voice(SAM), 3.0)
sp._recognize(a1)
check("one utterance is not enough to name anyone", a1 not in sp.known, sp.known)
a2, _ = sp.diar.assign(voice(SAM), 3.0)
sp._recognize(a2)
check("after two, the voice is named from memory", sp.known.get(a2) == "Sam", sp.known)
check("  written for the app and sent to the agent",
      json.loads((sdir / "speakers.known.json").read_text())["known"] == {str(a2): "Sam"} and published)
stranger = unit(rng.normal(size=192))
b, _ = sp.diar.assign(voice(stranger), 3.0)
b, _ = sp.diar.assign(voice(stranger), 3.0)
sp._recognize(b)
check("a stranger is not given anyone's name", b not in sp.known, sp.known)

# 3. A name the user gave always wins, in the recorder and in every label.
(sdir / "speakers.names.json").write_text(json.dumps({"names": {str(b): "Jordan"}}))
c, _ = sp.diar.assign(voice(ALEX), 3.0)
c, _ = sp.diar.assign(voice(ALEX), 3.0)
sp._recognize(c)
live = root / "live"
live.mkdir(exist_ok=True)
for f in ("speakers.names.json", "speakers.known.json"):
    (live / ("m2." + f)).write_text((sdir / f).read_text())
(live / "m2.speakers.names.json").write_text(json.dumps({"names": {str(a2): "Samantha"}}))
alias, names = sbspeakers.load(str(live), "m2")
check("labels: the user's name beats a recognized one",
      sbspeakers.label({"spk": a2}, alias, names) == "Samantha", names)
check("labels: recognized names show where the user gave none",
      sbspeakers.label({"spk": c}, alias, names) == "Alex", names)
info = ui.speakers("m2")
check("the app gets recognized names separately, to mark them",
      info["known"].get(str(c)) == "Alex" and str(c) not in info["names"], info)

# 4. Two remembered voices too alike to tell apart: no name rather than a guess.
session("m3", [SAM2])
ui.rename_speaker("m3", "1", "Sam's twin")
sd4 = root / "sessions" / "m4"
sd4.mkdir(parents=True)
sp4 = sb.SpeakerID(sd4 / "speakers.state.json")
d, _ = sp4.diar.assign(voice(SAM, 0.2), 3.0)
d, _ = sp4.diar.assign(voice(SAM, 0.2), 3.0)
sp4._recognize(d)
check("ambiguous between two remembered voices: left unnamed", d not in sp4.known, sp4.known)

# 5. Turned off in Settings: nothing is learned.
ui.set_settings({"remember_voices": False})
session("m5", [stranger])
ui.rename_speaker("m5", "1", "Casey")
check("with remembering off, naming teaches nothing",
      "Casey" not in json.loads((root / "voices.json").read_text())["voices"])
ui.set_settings({"remember_voices": True})
ui.forget_voices()
check("forget removes every voice print", not (root / "voices.json").exists())

# 6. The user's own voice: one label all meeting. Their print ("me", learnt on calls) names
# them by the voice they named, so their mic's lines (Me) carry the same name as their voice
# in the room; "You" for both when no name is known.
ME = unit(rng.normal(size=192))
mine = lambda v, seconds=2814.1: {"emb": list(v), "seconds": seconds}
named = lambda **vs: {name: {"emb": list(v), "n": 1} for name, v in vs.items()}
check("the user's print matches the voice they named: that is their name",
      sb.my_name({"me": mine(ME), "voices": named(Robin=unit(ME + 0.8 * unit(rng.normal(size=192))), Sam=SAM)})
      == "Robin")
check("  a print not heard enough yet: no name", sb.my_name({"me": mine(ME, 10.0), "voices": named(Robin=ME)}) is None)
check("  no voice named like them: no name", sb.my_name({"me": mine(ME), "voices": named(Alex=ALEX, Sam=SAM)}) is None)
check("  two named voices alike to it: no name (never a guess)",
      sb.my_name({"me": mine(ME), "voices": named(Robin=ME, Rob=voice(ME, 0.05))}) is None)
(root / "voices.json").write_text(json.dumps({"me": mine(ME), "voices": named(Robin=ME, Sam=SAM)}))
sd6 = root / "sessions" / "m6"
sd6.mkdir(parents=True)
(sd6 / "speakers.known.json").write_text(json.dumps({"known": {"me": "Robin"}}))
sp6 = sb.SpeakerID(sd6 / "speakers.state.json")
check("a session knows the user's name, and no speaker is \"me\"", sp6.me_name == "Robin" and sp6.known == {}, sp6.known)
s1, _ = sp6.diar.assign(voice(SAM), 3.0)
s1, _ = sp6.diar.assign(voice(SAM), 3.0)
sp6._recognize(s1)
check("  a voice recognized keeps the user's name beside it",
      json.loads((sd6 / "speakers.known.json").read_text())["known"] == {str(s1): "Sam", "me": "Robin"})
check("  their mic's lines carry it, in the export and what Claude reads",
      sbspeakers.label({"speaker": "Me"}, {}, {"me": "Robin", str(s1): "Sam"}) == "Robin")
check("  a name the user typed for their mic still wins",
      sbspeakers.label({"speaker": "Me"}, {}, dict({"me": "Robin"}, **{"me": "Rob"})) == "Rob")
(root / "voices.json").write_text(json.dumps({"me": mine(ME), "voices": named(Sam=SAM)}))
sd6b = root / "sessions" / "m6b"
sd6b.mkdir(parents=True)
sp6b = sb.SpeakerID(sd6b / "speakers.state.json")
y, _ = sp6b.diar.assign(voice(ME), 3.0)
y, _ = sp6b.diar.assign(voice(ME), 3.0)
sp6b._recognize(y)
check("no name: a voice in the room that is the user's is \"You\", as their mic is",
      sp6b.known.get(y) == "You" and sbspeakers.label({"speaker": "Me"}, {}, {}) == "You", sp6b.known)
ui.forget_voices()

# 7. Renames reach the live matcher: as they happen, not next session.
E0, E1 = np.eye(16)[0], np.eye(16)[1]
VEC = {1: E0, 2: unit(E0 + 3.0 * E1), 3: unit(E0 + 0.9 * E1)}      # cos(1,2) 0.32; 3 bridges them
real_embed = sb.embed
sb.embed = lambda audio, extractor=None: VEC[int(audio[0])]
say = lambda k, n=1: [sp7.identify(np.full(48000, float(k), dtype="float32")) for _ in range(n)]    # 3 s lines


def live(name, known=None):
    sdir = root / "sessions" / name
    sdir.mkdir(parents=True)
    if known:
        (sdir / "speakers.known.json").write_text(json.dumps({"known": known}))
    changes = []
    s = sb.SpeakerID(sdir / "speakers.state.json", on_change=changes.append, remember=False)
    s.extractor = object()
    return s, sdir, changes


sp7, sd7, ch7 = live("t61a")
a, b = say(1)[0], say(2)[0]
check("two unlike voices: two speakers", (a, b) == (1, 2), (a, b))
(sd7 / "speakers.names.json").write_text(json.dumps({"names": {"2": "Alex"}}))
say(3, 3)
check("a merge keeps the speaker the user named, so the name stays on screen",
      sp7.diar.resolve(1) == 2 and 1 not in sp7.diar.cent, (sp7.diar.alias, sorted(sp7.diar.cent)))
check("  and the app and the agent are told", ch7 and ch7[-1].get(1) == 2, ch7)
check("  its later lines carry the named speaker", say(1)[0] == 2)

sp7, sd7, ch7 = live("t61b")
say(1), say(2)
(sd7 / "speakers.names.json").write_text(json.dumps({"names": {"1": "Alex", "2": "Sam"}}))
say(3, 6)
check("two different names never merge, however alike they come to sound",
      sorted(sp7.diar.cent) == [1, 2] and not sp7.diar.alias, (sp7.diar.alias, sorted(sp7.diar.cent)))

sp7, sd7, ch7 = live("t61c", known={"1": "Alex"})
say(1), say(2)
(sd7 / "speakers.names.json").write_text(json.dumps({"names": {"2": "Sam"}}))
say(3, 6)
check("nor does a recognized voice with one the user named otherwise",
      sorted(sp7.diar.cent) == [1, 2], sorted(sp7.diar.cent))

sp7, sd7, ch7 = live("t61d")
say(1), say(2)
(sd7 / "speakers.names.json").write_text(json.dumps({"names": {"1": "Alex", "2": "Alex"}}))
say(1)
check("two numbers the user gave one name are joined into one speaker",
      sp7.diar.resolve(2) == 1 and 2 not in sp7.diar.cent, sp7.diar.alias)
check("  saved, and the app and the agent are told",
      json.loads((sd7 / "speakers.state.json").read_text())["alias"] == {"2": 1} and ch7, ch7)
check("  the voice now goes to that one speaker", say(2)[0] == 1)

sp7, sd7, ch7 = live("t61e")
say(1), say(2)
sp7.save()
(sd7 / "speakers.names.json").write_text(json.dumps({"names": {"2": "Alex"}}))
sp7 = sb.SpeakerID(sd7 / "speakers.state.json", on_change=ch7.append, remember=False)
sp7.extractor = object()
say(3, 3)
check("after a resume, the names the user gave are protected again", sp7.diar.resolve(1) == 2, sp7.diar.alias)

sd8 = root / "sessions" / "t61f"
sd8.mkdir(parents=True)
sp8 = sb.SpeakerID(sd8 / "speakers.state.json", remember=True)
before = sorted(sp8.voices)
lib = {"voices": {"Riley": {"emb": [float(x) for x in VEC[2]], "n": 3, "updated": 0}}}
(root / "voices.json").write_text(json.dumps(lib))       # what the app writes on a rename
(sd8 / "speakers.names.json").write_text(json.dumps({"names": {"1": "Riley"}}))
sp8.read_names()
check("a name given now is a remembered voice now, for later numbers in this session",
      "Riley" in sp8.voices and "Riley" not in before, (before, sorted(sp8.voices)))
(root / "voices.json").unlink()
sb.embed = real_embed

# 6. Real speech, when the speaker model is available.
data = os.environ.get("SB_DIAR_DIR", "/tmp/sbdiar")
model = os.path.join(data, "nemo_en_titanet_small.onnx")
try:
    import sherpa_onnx, soundfile as sf
    have = os.path.exists(model) and glob.glob(os.path.join(data, "sr-data/enroll/*.wav"))
except ImportError:
    have = False
if not have:
    print("  (real-speech part skipped: needs sherpa-onnx, soundfile and %s)" % model)
else:
    sb.SPEAKER_MODEL = Path(model)
    ex = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model, num_threads=2))

    def emb(x):
        s = ex.create_stream(); s.accept_waveform(sample_rate=16000, waveform=x); s.input_finished()
        return np.array(ex.compute(s))

    def pieces(path):
        x = sf.read(path, dtype="float32", always_2d=True)[0][:, 0]
        return [x[i:i + 48000] for i in range(0, len(x) - 32000, 48000)]
    enroll = lambda who: [p for f in sorted(glob.glob(os.path.join(data, "sr-data/enroll/%s-*.wav" % who))) for p in pieces(f)]
    test = lambda who: [p for f in sorted(glob.glob(os.path.join(data, "sr-data/test/%s-*.wav" % who))) for p in pieces(f)]
    d = sb.OnlineDiarizer()
    for who in ("fangjun", "leijun"):
        sid = d.next_id
        for x in enroll(who):
            d.cent.setdefault(sid, np.zeros(192)); d.cent[sid] = d.cent[sid] + unit(emb(x)); d.count[sid] = d.count.get(sid, 0) + 1
        d.next_id += 1
    (root / "sessions" / "r1").mkdir(parents=True)
    (root / "sessions" / "r1" / "speakers.state.json").write_text(json.dumps(d.to_json()))
    ui.rename_speaker("r1", "1", "Fangjun")
    ui.rename_speaker("r1", "2", "Leijun")
    for who, expect in (("fangjun", "Fangjun"), ("leijun", "Leijun"), ("liudehua", None)):
        sd = root / "sessions" / ("r-" + who)
        sd.mkdir(parents=True)
        spr = sb.SpeakerID(sd / "speakers.state.json")
        got = None
        for x in test(who)[:4]:
            k, _ = spr.diar.assign(emb(x), 3.0)
            spr._recognize(k)
            got = spr.known.get(spr.diar.resolve(k)) or got
        check("real speech: %s in a new recording -> %s" % (who, expect or "unnamed"), got == expect, (got, spr.known))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
