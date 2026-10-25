"""Voice mode's real engine (duplex.AVFBackend), driven with fake
AVFoundation objects: nothing opens the mic or plays a sound.

The mic: only the default input, voice processing on (or not: no echo
cancellation), the least ducking, its audio tapped and resampled to 16 kHz.
Claude's voice: each sentence's buffers played as they come, the player
connected in their format once, playing until the last one has played,
halted at once (late buffers of a halted sentence never play). The voice
it picks. And a test never gets the real engine."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import os, sys, types
import numpy as np

sys.path.insert(0, "mac")
from native.macos import duplex  # noqa: E402  (the Mac's engine itself, with fakes)

fails = []


def check(label, ok, d=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:400])


# ---------------------------------------------------------------- the 16 kHz the model wants
t = np.arange(48000 * 2) / 48000.0
tone = np.sin(2 * np.pi * 1000 * t).astype("float32")
one = duplex.Resampler(48000).feed(tone)
r, rng, parts, i = duplex.Resampler(48000), np.random.RandomState(0), [], 0
while i < len(tone):
    n = rng.randint(1, 3000); parts.append(r.feed(tone[i:i + n])); i += n
pieces = np.concatenate(parts)
check("resample: 48 kHz to 16 kHz, one in three", len(one) == 32000 and len(pieces) == 32000, (len(one), len(pieces)))
check("  the same whether it comes whole or in pieces", np.allclose(one, pieces, atol=1e-5))
check("  speech frequencies kept", abs(np.sqrt(np.mean(one[200:-200] ** 2)) - 0.707) < 0.01)
high = duplex.Resampler(48000).feed(np.sin(2 * np.pi * 12000 * t).astype("float32"))
check("  above 8 kHz removed, not folded back in", np.sqrt(np.mean(high[200:-200] ** 2)) < 0.003)
odd = duplex.Resampler(44100).feed(np.sin(2 * np.pi * 1000 * np.arange(44100) / 44100.0).astype("float32"))
check("  44.1 kHz too", abs(len(odd) - 16000) <= 1 and abs(np.sqrt(np.mean(odd[200:-200] ** 2)) - 0.707) < 0.01, len(odd))


# ---------------------------------------------------------------- fake AVFoundation
class Obj(object):
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


class Fmt(object):
    def __init__(self, rate, ch=1): self.rate, self.ch = rate, ch
    def sampleRate(self): return self.rate
    def channelCount(self): return self.ch
    def isEqual_(self, o): return (self.rate, self.ch) == (o.rate, o.ch)


class Ptr(object):
    def __init__(self, a): self.a = a
    def as_buffer(self, n): return memoryview(self.a[:n]).cast("B")     # writable, as pyobjc's


class PCM(object):
    def __init__(self, frames, fmt, a=None):
        self.n, self.fmt = frames, fmt
        self.a = a if a is not None else np.zeros(frames, "float32")
    def frameLength(self): return self.n
    def setFrameLength_(self, n): self.n = n
    def format(self): return self.fmt
    def floatChannelData(self): return [Ptr(self.a)]


class Input(object):
    vp_ok = True
    def __init__(self): self.vp, self.tap, self.duck = False, None, None
    def setVoiceProcessingEnabled_error_(self, on, _err):
        if on and not Input.vp_ok:
            return False, "voice processing refused"
        self.vp = on
        return True, None
    def setVoiceProcessingOtherAudioDuckingConfiguration_(self, cfg): self.duck = cfg
    def outputFormatForBus_(self, _bus): return Fmt(48000)
    def installTapOnBus_bufferSize_format_block_(self, _bus, _n, _fmt, block): self.tap = block
    def removeTapOnBus_(self, _bus): self.tap = None


engines = []


class Engine(Obj):
    start_ok = True
    def init(self):
        self.inp, self.attached, self.connections, self.running = Input(), [], [], False
        self.inp_used = False
        engines.append(self)
        return self
    def inputNode(self):
        self.inp_used = True
        return self.inp
    def outputNode(self): return Input()             # its format: 48 kHz, as Input's
    def mainMixerNode(self): return "mixer"
    def attachNode_(self, n): self.attached.append(n)
    def connect_to_format_(self, a, b, f):
        self.connections.append((a, b, f))
        if isinstance(a, Player):
            a.fmt = f
    def prepare(self): pass
    def startAndReturnError_(self, _e):
        self.running = Engine.start_ok
        return (True, None) if Engine.start_ok else (False, "no audio device")
    def stop(self): self.running = False


class Player(Obj):
    """As AVAudioPlayerNode: a buffer in another channel count than its connection's (2,
    the mixer's, before it is connected) is refused, as AVFoundation raises."""
    def init(self):
        self.scheduled, self.playing, self.fmt = [], False, None
        return self
    def scheduleBuffer_completionHandler_(self, buf, h):
        ch = self.fmt.channelCount() if self.fmt is not None else 2
        if buf.fmt.channelCount() != ch:
            raise RuntimeError("required condition is false: _outputFormat.channelCount == buffer.format.channelCount")
        self.scheduled.append((buf, h))
    def play(self): self.playing = True
    def pause(self): self.playing = False             # as AVFoundation: what is scheduled stays
    def setVolume_(self, v): self.volume = v
    def isPlaying(self): return self.playing
    def stop(self):                                   # as AVFoundation: drops them, calling their handlers
        self.playing = False
        done, self.scheduled = self.scheduled, []
        for _b, h in done:
            if h:
                h()
    def play_one(self):
        buf, h = self.scheduled.pop(0)
        if h:
            h()


class Synth(Obj):
    def init(self):
        self.asked, self.stops = [], 0
        return self
    def writeUtterance_toBufferCallback_(self, utt, cb): self.asked.append((utt, cb))
    def stopSpeakingAtBoundary_(self, _b): self.stops += 1


class Utt(object):
    def __init__(self, text): self.text, self.voice = text, None
    @classmethod
    def speechUtteranceWithString_(cls, text): return cls(text)
    def setVoice_(self, v): self.voice = v


class V(object):
    def __init__(self, ident, name, lang, quality): self.i, self.n, self.l, self.q = ident, name, lang, quality
    def identifier(self): return self.i
    def name(self): return self.n
    def language(self): return self.l
    def quality(self): return self.q


VOICES = [V("com.apple.voice.compact.en-US.Samantha", "Samantha", "en-US", 1),
          V("com.apple.voice.enhanced.en-GB.Daniel", "Daniel", "en-GB", 2),
          V("com.apple.voice.premium.en-US.Ava", "Ava", "en-US", 3),
          V("com.apple.voice.premium.fr-FR.Amelie", "Amelie", "fr-FR", 3)]
class APCM(Obj):
    def initWithPCMFormat_frameCapacity_(self, fmt, n): return PCM(n, fmt)


class AFormat(Obj):
    def initWithCommonFormat_sampleRate_channels_interleaved_(self, kind, rate, ch, inter):
        assert kind == 1 and not inter                 # float32, one channel's samples together
        return Fmt(rate, ch)
    def initStandardFormatWithSampleRate_channels_(self, rate, ch):
        return Fmt(rate, ch)


avf = types.SimpleNamespace(AVAudioEngine=Engine, AVAudioPlayerNode=Player, AVSpeechSynthesizer=Synth,
                            AVSpeechUtterance=Utt, AVAudioPCMBuffer=APCM, AVAudioFormat=AFormat,
                            AVSpeechSynthesisVoice=types.SimpleNamespace(speechVoices=lambda: VOICES))
loops = []
fd = types.SimpleNamespace(
    NSURL=types.SimpleNamespace(fileURLWithPath_=lambda p: p),
    NSDate=types.SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda s: s),
    NSRunLoop=types.SimpleNamespace(currentRunLoop=lambda: types.SimpleNamespace(runUntilDate_=loops.append)))
logged = []


def backend(**kw):
    return duplex.AVFBackend(avf=avf, foundation=fd, default_input=lambda: "MacBook Pro Microphone",
                             log=logged.append, **kw)


# ---------------------------------------------------------------- the mic
b = backend()
try:
    b.start_mic("External Microphone", lambda x: None)
    check("only the system's default mic goes through echo cancellation", False)
except RuntimeError as exc:
    check("only the system's default mic goes through echo cancellation",
          "default mic" in str(exc) and engines == [], exc)
got = []
b.start_mic("MacBook Pro Microphone", got.append)
e = engines[-1]
check("voice processing on: echo cancelled", e.inp.vp and b.aec and e.running)
check("  only Claude's player attached", e.attached == [b.player], e.attached)
check("  other apps turned down as little as macOS allows", e.inp.duck == (False, 10), e.inp.duck)
to_out = [c for c in e.connections if c[0] == "mixer"]
check("  the speakers connected at the mic's rate, before it starts (else -10875 on a MacBook's own pair)",
      len(to_out) == 1 and to_out[0][2].sampleRate() == 48000 and to_out[0][2].channelCount() in (1, 2), to_out)
e.inp.tap(PCM(4800, Fmt(48000), np.full(4800, 0.1, "float32")), None)
check("  the mic, tapped, comes out at 16 kHz", got and sum(len(x) for x in got) == 1600
      and got[0].dtype == np.float32, [len(x) for x in got])
Input.vp_ok = False
b2 = backend()
b2.start_mic("MacBook Pro Microphone", lambda x: None)
check("voice processing refused: the engine still runs, without echo cancellation",
      not b2.aec and engines[-1].running and any("did not start" in m for m in logged), logged)
Input.vp_ok = True
Engine.start_ok = False
try:
    backend(default_output=lambda: "P27h-28 (hdmi)").start_mic("MacBook Pro Microphone", lambda x: None)
    check("an engine that does not start says so, and undoes voice processing", False)
except RuntimeError as exc:
    check("an engine that does not start says so, and undoes voice processing",
          not engines[-1].inp.vp and engines[-1].inp.tap is None)
    # -10875 on the user's Mac, 7 of 7 tries, with nothing in the log to say which devices.
    check("  and which mic and output it met, at what rate, before macOS's own words",
          str(exc).startswith("the audio engine did not start (mic MacBook Pro Microphone at 48000 Hz x 1, "
                              "output P27h-28 (hdmi) at 48000 Hz x 1): no audio device"), str(exc))
Engine.start_ok = True

# Headphones while the session keeps a mic that isn't the default (the Mac's, while AirPods
# play): the voice plays without voice processing, and the mic is never opened here.
bo = backend()
bo.start_output()
eo = engines[-1]
check("on headphones: the voice plays with no voice processing, the mic never touched",
      eo.running and not eo.inp_used and not eo.inp.vp and not bo.aec and bo.inp is None, (eo.running, eo.inp_used))
check("  Claude's player to the output at its own rate",
      eo.attached == [bo.player] and [c[2].sampleRate() for c in eo.connections if c[0] == "mixer"] == [48000], eo.connections)
bo.say("On headphones.")
check("  and it speaks", bo.playing and bo.synth.asked[-1][0].text == "On headphones.")
bo.halt()
bo.stop_mic()
check("  stopped: the engine stops, still without touching the mic", not eo.running and not eo.inp_used and bo.engine is None)

# ---------------------------------------------------------------- Claude's voice
syn, player = b.synth, b.player
b.say("Orion is the cache.")
utt, cb = syn.asked[-1]
check("say: playing from the start", b.playing and utt.text == "Orion is the cache.")
check("  in the most natural English voice installed", utt.voice is VOICES[2], utt.voice and utt.voice.name())
fmt = Fmt(22050)
cb(PCM(256, fmt))
cb(PCM(256, fmt))
check("  each buffer played as it comes, the player connected once, in their format",
      len(player.scheduled) == 2 and player.playing and
      [c for c in e.connections if c[0] is player] == [(player, "mixer", fmt)], e.connections)
cb(PCM(0, fmt))
check("  written, but still playing until its last buffer has played", b.playing)
player.play_one()
player.play_one()
check("  then done", not b.playing)
b.say("Again.")
cb3 = syn.asked[-1][1]
cb3(PCM(256, fmt))
cb3(PCM(1, fmt))                                     # macOS 14 ends a sentence with one sample, not none
player.play_one()
check("  a sentence that ends on a one-sample buffer ends too (macOS 14.7)", not b.playing and len(player.scheduled) == 0)
b.say("It replaced Redis.")
cb2 = syn.asked[-1][1]
cb2(PCM(256, fmt))
check("the next sentence: no new connection", len([c for c in e.connections if c[0] is player]) == 1)
b.halt()
check("halt: stops at once", not b.playing and syn.stops == 1 and player.scheduled == [] and not player.playing)
cb2(PCM(256, fmt))
cb2(PCM(0, fmt))
check("  what the speech engine still writes for it never plays", player.scheduled == [] and not b.playing)
b.say("Next.")
syn.asked[-1][1](PCM(256, fmt))
check("  and the next sentence plays as usual", len(player.scheduled) == 1 and b.playing)
b.halt()

# Talked over (R-conversation, Q1): quiet, then paused, everything kept; on from there, or stopped.
import time  # noqa: E402


def wait(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end and not pred():
        time.sleep(0.01)
    return pred()


b.say("A long answer about the budget.")
cbd = syn.asked[-1][1]
cbd(PCM(256, fmt))
halts = b._halts
b.duck()
check("duck: it fades out, then pauses, with what was scheduled kept",
      wait(lambda: not player.playing) and abs(player.volume - b.LOW) < 1e-6 and len(player.scheduled) == 1
      and b.playing, (player.playing, getattr(player, "volume", None), len(player.scheduled)))
check("  a duck is not a halt: a first sentence waiting for Standby's voice keeps waiting", b._halts == halts)
cbd(PCM(256, fmt))
check("  what comes while ducked is scheduled, not played", len(player.scheduled) == 2 and not player.playing)
b.unduck()
check("unduck: on from where it paused, back to full volume",
      player.playing and wait(lambda: abs(player.volume - 1.0) < 1e-6) and len(player.scheduled) == 2,
      (player.playing, player.volume))
b.duck()
b.unduck()
b.duck()
wait(lambda: not player.playing)
b.halt()
check("halt while ducked: stopped, dropped, and the next answer at full volume",
      not player.playing and player.scheduled == [] and player.volume == 1.0 and not b._ducked, player.volume)
time.sleep(b.FADE + 0.1)
check("  an old fade never comes back after it", player.volume == 1.0 and not player.playing, player.volume)
b.say("Another long answer.")
cbh = syn.asked[-1][1]
cbh(PCM(256, fmt))
b.duck()
time.sleep(b.FADE / 3)                               # halfway down
b.halt()
b.say("The next answer.")
syn.asked[-1][1](PCM(256, fmt))
time.sleep(b.FADE + 0.1)
check("halt mid-fade: the next answer at full volume, and not paused by the old fade",
      player.volume == 1.0 and player.playing, (player.volume, player.playing))
b.halt()

# ---------------------------------------------------------------- Standby's voice
class FakeProcess(object):
    def __init__(self):
        self.state, self.downloading, self.done, self.total = "loading", False, 0, 0
        self.said, self.stops, self.opened, self.closed, self.error = [], [], 0, 0, None
        self.on_audio = self.on_end = self.on_gone = None
    @property
    def ready(self): return self.state == "ready"
    def open(self): self.opened += 1
    def say(self, sid, text): self.said.append((sid, text)); return True
    def stop(self, upto): self.stops.append(upto)
    def close(self): self.closed += 1; self.state = "off"
    def use(self, voice): self.used = getattr(self, "used", []) + [voice]


fp = FakeProcess()
bn = backend(neural="jean", process=fp)
bn.start_mic("MacBook Pro Microphone", lambda x: None)
en, pn = engines[-1], bn.player
check("Standby's voice: its process starts with the mic", fp.opened == 1 and bn.speech is not None)
fp.state, fp.downloading, fp.done, fp.total = "downloading", True, 10, 40
check("  its getting ready goes to the recorder's status",
      bn.tts_status() == {"state": "downloading", "downloading": True, "done": 10, "total": 40}, bn.tts_status())
bn.begin()
bn.say("Before it was ready.")
check("  an answer begun while it loads: in Standby's voice all the same, held by its process until ready",
      fp.said == [(1, "Before it was ready.")] and bn.synth.asked == [] and bn.loading, (fp.said, bn.synth.asked))
fp.state = "ready"
bn.halt()
bn.begin()
bn.say("Orion is the cache.")
check("  the next answer: Standby's voice", fp.said[-1] == (2, "Orion is the cache.") and bn.playing
      and bn.synth.asked == [] and not bn.loading)
for _ in range(3):                            # 0.24 s: held until 0.2 s is there, then it goes (tts.Speech.PREROLL)
    fp.on_audio(2, np.full(1920, 0.25, "float32"))
buf, _h = pn.scheduled[-1]
check("  its sound through the same player (cancelled from the mic), as 24 kHz float buffers",
      pn.playing and buf.n == 1920 and buf.fmt.rate == 24000 and abs(float(buf.a[0]) - 0.25) < 1e-6
      and (pn, "mixer", buf.fmt) in [(a, b2, f) for a, b2, f in en.connections if f.rate == 24000])
fp.on_end(2)
check("  playing until its last piece has played", bn.playing)
for _ in range(3):
    pn.play_one()
check("  then done", not bn.playing)
bn.say("Short.")
fp.on_audio(3, np.full(1920, 0.25, "float32"))
check("  a piece short of what is held first: not yet played", len(pn.scheduled) == 0)
fp.on_end(3)
check("  its end plays it", len(pn.scheduled) == 1)
pn.play_one()
bn.say("Talked over.")
fp.on_audio(4, np.zeros(1920, "float32"))
bn.halt()
check("halt: Standby's voice stops at once too", not bn.playing and pn.scheduled == [] and fp.stops[-1] == 4)
del logged[:]
bn.say("Lost with the process.")
fp.on_gone()
fp.state = "failed"
check("its process dies mid-answer: opened again, and what it took is said again in Standby's voice, never the Mac's",
      bn.playing and fp.opened == 2 and fp.said[-1][1] == "Lost with the process." and bn.synth.asked == [],
      (fp.opened, fp.said[-1:], bn.synth.asked))
st = bn.tts_status() or {}
check("  while it comes back, the recorder's status says since when (the band: \"Trying again\")",
      isinstance(st.get("retrying_since"), float) and time.time() - st["retrying_since"] < 5 and "failed" not in st, st)
fp.state = "ready"
check("  back: no longer said", "retrying_since" not in (bn.tts_status() or {}), bn.tts_status())
fp.state = "failed"
fp.on_gone()
bn.playing
fp.on_gone()
bn.playing
check("  dying again past REOPENS: it says it isn't working, for the orb, and nothing is said instead",
      bn.failed and (bn.tts_status() or {}).get("failed") == bn.failed and fp.opened == 3 and bn.synth.asked == []
      and any("isn't working" in m for m in logged), (bn.failed, fp.opened, logged[-2:]))
check("  and no longer that it's coming back: given up", "retrying_since" not in (bn.tts_status() or {}), bn.tts_status())
bn.say("After it failed.")
check("  and after that, nothing at all: no Mac voice", bn.synth.asked == [] and fp.said[-1][1] != "After it failed.")
bn.halt()
bn.stop_mic()
check("stop: the process ends with the mic", fp.closed == 1 and bn.speech is None)

import threading, time  # noqa: E401,E402
fp2 = FakeProcess()
bn2 = backend(neural="jean", process=fp2)
bn2.start_mic("MacBook Pro Microphone", lambda x: None)
bn2.begin()                                   # a call's greeting, a second in: Jean still loading
t0 = time.time()
bn2.say("Hi, what can I do for you?")
check("a greeting while Standby's voice still loads: given to it at once, nothing waits here, never the Mac's",
      fp2.said == [(1, "Hi, what can I do for you?")] and time.time() - t0 < 0.05 and bn2.synth.asked == [],
      (fp2.said, time.time() - t0))
fp2.state = "ready"
bn2.halt()
bn2.begin()
t0 = time.time()
bn2.say("Fast.")
check("  ready: no wait at all (the first word stays as fast as before)", time.time() - t0 < 0.05 and fp2.said[-1][1] == "Fast.")
bn2.halt()
bn2.stop_mic()
fp3 = FakeProcess()
fp3.state = "ready"
bn3 = backend(neural="jean", process=fp3)
bn3.start_mic("MacBook Pro Microphone", lambda x: None)
bn3.say("No begin before me.")
check("the first answer with no begin() before it: Standby's voice",
      fp3.said == [(1, "No begin before me.")] and bn3.synth.asked == [], (fp3.said, bn3.synth.asked))
bn3.halt()
bn3.begin()
bn3.say("First in Jean.")
refuse = [1]


def flaky(sid, text):
    if refuse[0]:
        refuse[0] -= 1
        return False                          # the pipe to it broke on this sentence
    fp3.said.append((sid, text))
    return True


fp3.say = flaky
bn3.say("Second.")
check("Standby's voice can't take a sentence mid-answer: its process opened again, the sentence goes to it",
      fp3.said[-1][1] == "Second." and fp3.opened == 2 and bn3.synth.asked == [] and not bn3.failed,
      (fp3.said, fp3.opened, bn3.synth.asked))
bn3.halt()
bn3.stop_mic()
fp4 = FakeProcess()
fp4.state = "ready"
bn4 = backend(neural="jean", process=fp4)
bn4.start_output()
bn4.say("Jean on the AirPods.")
check("on headphones too: Standby's own voice, opened with the engine",
      fp4.opened == 1 and fp4.said == [(1, "Jean on the AirPods.")] and bn4.synth.asked == [], (fp4.said, bn4.synth.asked))
bn4.halt()
bn4.stop_mic()
check("  and its process ends with it", fp4.closed == 1)
bc = backend(voice="Daniel")
bc.start_mic("MacBook Pro Microphone", lambda x: None)
bc.say("In the voice chosen.")
check("a Mac voice the user chose in Settings: that voice speaks (a choice, not a fallback)",
      [u.text for u, _ in bc.synth.asked] == ["In the voice chosen."] and not bc.failed)
bc.say("Standby heard that.")
check("  it says \"Standby\" as heard (two words), the text itself as written", bc.synth.asked[-1][0].text == "Stand by heard that.")
bc.halt()
bc.stop_mic()
bx = backend()
bx.failed = "Standby's voice isn't installed"   # as standby.voice_backend sets it
bx.start_mic("MacBook Pro Microphone", lambda x: None)
bx.say("Nobody to say it.")
check("Standby's voice not there: nothing said in the Mac's voice, and the orb says why",
      bx.synth.asked == [] and (bx.tts_status() or {}).get("failed") == "Standby's voice isn't installed", bx.tts_status())
bx.stop_mic()

# A mic switched mid-call (the Mac's to an external one): the engine is made again, with a new
# player. Standby's voice must play on it: the old player's connection is not the new one's.
def jean(b, fp, text):
    """A sentence in Standby's voice, its sound made: the buffers scheduled for it."""
    n = len(b.player.scheduled)
    b.say(text)
    sid = fp.said[-1][0]
    for _ in range(3):
        fp.on_audio(sid, np.full(1920, 0.25, "float32"))
    fp.on_end(sid)
    return len(b.player.scheduled) - n


fp5 = FakeProcess()
fp5.state = "ready"
bn5 = backend(neural="jean", process=fp5)
bn5.start_mic("MacBook Pro Microphone", lambda x: None)
check("Standby's voice plays", jean(bn5, fp5, "Before the switch.") == 3)
bn5.halt()
del logged[:]
bn5.stop_mic()                                   # Channel._start on a switch: the engine stopped, made again
fp5.state = "ready"
bn5.start_mic("MacBook Pro Microphone", lambda x: None)
bn5.begin()
check("a mic switched mid-call: Standby's voice plays on the new engine, no buffer refused",
      jean(bn5, fp5, "After the switch.") == 3 and bn5.player.fmt.channelCount() == 1
      and not any("refused" in m or "could not play" in m for m in logged), logged[-3:])
bn5.halt()
bn5._say_av("The Mac's voice too.")
bn5.synth.asked[-1][1](PCM(256, Fmt(22050)))
check("  and so does the Mac's voice", any(b.fmt.rate == 22050 for b, _h in bn5.player.scheduled))
bn5.halt()
bn5.begin()
jean(bn5, fp5, "Connected in its own format.")
bn5.halt()
bn5.begin()
bn5.player.fmt = Fmt(48000, 2)                   # the route changed under it: stereo now, unbeknown to it
check("a player whose connection changed refuses a buffer: connected again at once, and it plays",
      jean(bn5, fp5, "After a route change.") == 3 and bn5.player.fmt.channelCount() == 1
      and any("connected again" in m for m in logged), logged[-3:])
real_connect = bn5.engine.connect_to_format_
bn5.engine.connect_to_format_ = lambda a, b, f: None    # a player that can't be connected at all
bn5.player.fmt, bn5._fmt = Fmt(48000, 2), None
bn5.halt()
bn5.begin()
check("  refused again: dropped, never waited on (the sentence ends, the next can go)",
      jean(bn5, fp5, "Nowhere to play.") == 0 and any("could not play, dropped" in m for m in logged)
      and not bn5.playing, (logged[-2:], bn5.playing))
bn5.engine.connect_to_format_ = real_connect
bn5.halt()
bn5.stop_mic()

# The probe for the user's -10875 (tools/duplex_probe.py) opens the real mic: never in a test.
import subprocess  # noqa: E402
r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "duplex_probe.py")],
                   capture_output=True, text=True, timeout=60, env=dict(os.environ, STANDBY_NO_DEVICES="1"))
check("the duplex probe refuses in a test, before touching anything", r.returncode == 2 and "not in a test" in r.stdout,
      (r.returncode, r.stdout, r.stderr[-300:]))

# Settings' voice changed during the call (use_voice): from the next sentence, no restart
fp6 = FakeProcess()
bn6 = backend(neural="jean", process=fp6)
bn6.start_mic("MacBook Pro Microphone", lambda x: None)
fp6.state = "ready"
bn6.use_voice(None, "alba")
check("Settings' voice changed mid-call: another of Standby's is a word to its process, never a restart",
      getattr(fp6, "used", None) == ["alba"] and fp6.opened == 1 and fp6.closed == 0 and bn6.neural == "alba",
      (getattr(fp6, "used", None), fp6.opened, fp6.closed))
bn6.use_voice(None, "alba")
check("  the same one again: nothing", fp6.used == ["alba"])
bn6.use_voice("Daniel", None)
check("  a Mac voice chosen: the next sentence takes the Mac's path, in that voice",
      bn6.neural is None and bn6.voice is VOICES[1] and not bn6.failed, (bn6.neural, bn6.voice))
bn6.use_voice(None, "jean")
check("  and back to Standby's: the process it has, in that voice", fp6.used == ["alba", "jean"] and bn6.neural == "jean")
bn6.use_voice(None, None, "Standby's voice isn't installed")
check("  a choice that can't speak says why, as at the start", bn6.failed == "Standby's voice isn't installed")
bn6.use_voice(None, "jean")
check("  and a good one after it clears that", bn6.failed == "" and bn6.neural == "jean", bn6.failed)
bn6.stop_mic()

check("asked for by name: that voice", backend(voice="Daniel").__class__ and
      duplex.pick_voice(VOICES, "Daniel") is VOICES[1] and duplex.pick_voice(VOICES, "Ava (Premium)") is VOICES[2]
      and duplex.pick_voice(VOICES, "com.apple.voice.enhanced.en-GB.Daniel") is VOICES[1])
check("  none better than the default installed: the system's own",
      duplex.pick_voice([VOICES[0], VOICES[3]]) is None)

b.idle(0.05)
check("idle: the main thread's run loop, where the speech engine delivers, in short slices",
      loops and all(x <= duplex.AVFBackend.SLICE + 1e-9 for x in loops), loops)
bs = backend()
bs.start_mic("MacBook Pro Microphone", lambda x: None)
real_sleep = duplex.time.sleep
bs.say("A sentence the engine is slow to start.")
cbs = bs.synth.asked[-1][1]
real_sleep(0.35)
cbs(PCM(256, fmt))
check("a sentence whose first sound waits over 300 ms is logged",
      any("first sound took" in m for m in logged), logged[-2:])
n = len([m for m in logged if "first sound took" in m])
bs.say("A quick one.")
bs.synth.asked[-1][1](PCM(256, fmt))
check("  a quick one is not", len([m for m in logged if "first sound took" in m]) == n)
bs.halt()

# The real run loop (no audio): idle waits one tick, no longer, without spinning the
# CPU, and runs what was handed to the main thread meanwhile (as the speech engine does).
try:
    import Foundation
    import threading
    import time as _t
    br = duplex.AVFBackend(avf=avf, foundation=Foundation, default_input=lambda: "x", log=logged.append)
    ran = []
    threading.Timer(0.03, lambda: Foundation.NSOperationQueue.mainQueue().addOperationWithBlock_(
        lambda: ran.append(_t.time()))).start()
    w0, c0 = _t.time(), _t.process_time()
    br.idle(0.1)
    wall, cpu = _t.time() - w0, _t.process_time() - c0
    check("idle on the real run loop: one tick, never more (a decode waits at most that)",
          0.09 <= wall <= 0.13, wall)
    check("  without spinning the CPU", cpu < 0.05, cpu)
    check("  and main-thread work handed over meanwhile runs in it", len(ran) == 1 and ran[0] - w0 < 0.1, ran)
except ImportError:
    print("  (real run loop part skipped: needs pyobjc Foundation)")
b.stop_mic()
check("stop: the engine stops, the tap goes, voice processing off",
      not e.running and e.inp.tap is None and not e.inp.vp and not b.aec)

# ---------------------------------------------------------------- never the real one in a test
from importlib.machinery import SourceFileLoader  # noqa: E402
sys.modules.setdefault("sounddevice", types.SimpleNamespace())
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
check("a test never gets the real engine (STANDBY_NO_DEVICES, from the guard)",
      os.environ.get("STANDBY_NO_DEVICES") == "1" and sb.voice_backend() is None)

# ---------------------------------------------------------------- which voice a call speaks in
check("a call's voice: automatic is Standby's own (Jean), never the Mac's, when it's installed",
      sb.backend_voices(None, {}, installed=True) == (None, "jean", ""))
check("  the chosen Pocket voice, or the agent's own", sb.backend_voices("pocket:alba", {}, installed=True)[1] == "alba"
      and sb.backend_voices(None, {"voice_mode": {"voice": "pocket:alba"}}, installed=True)[1] == "alba")
check("  a macOS voice only when that's the one chosen", sb.backend_voices("Samantha", {}, installed=True) == ("Samantha", None, ""))
check("  not installed: the Mac's voice, and it says why",
      sb.backend_voices(None, {}, installed=False) == (None, None, "Standby's voice isn't installed"))
# The engine as voice mode makes it (no devices: the engine is a fake): a session with no voice of its
# own must get Jean. It got the Mac's when `import voice` in make() shadowed the session's voice.
import tts  # noqa: E402
made_with, real = [], (duplex.AVFBackend, tts.available, sb.load_config, os.environ.pop("STANDBY_NO_DEVICES"))
duplex.AVFBackend = lambda **kw: made_with.append(kw) or types.SimpleNamespace(**kw)
tts.available, sb.load_config = (lambda: True), (lambda: {"voice_mode": {"duplex": True}})
try:
    make = sb.voice_backend(None)
    if make is not None:
        make()
finally:
    duplex.AVFBackend, tts.available, sb.load_config = real[0], real[1], real[2]
    os.environ["STANDBY_NO_DEVICES"] = real[3]
check("voice mode's engine for a call with an automatic voice: Standby's voice, not the Mac's",
      made_with and made_with[-1]["neural"] == "jean" and made_with[-1]["voice"] is None, made_with)
# The call's voice loop looks at Settings between sentences: a change reaches the engine; an agent's own
# voice is kept. A config.json of its own (sb.CONFIG): never the user's.
import json, tempfile, time  # noqa: E402
from pathlib import Path  # noqa: E402
tmp = Path(tempfile.mkdtemp(prefix="sbvoiceset-"))
real_cfg, real_av = sb.CONFIG, tts.available
sb.CONFIG, tts.available = tmp / "config.json", (lambda: True)
try:
    sb.CONFIG.write_text(json.dumps({"voice_mode": {"voice": "pocket:jean"}}))
    used = []
    eng = types.SimpleNamespace(use_voice=lambda system, neural, why="": used.append((system, neural, why)),
                                playing=False, loading=False)
    vio = sb.VoiceIO(tmp, eng, log=lambda m: None, leaks=lambda: True, talk_over=False)
    vio.SETTINGS_EVERY = 0.0
    vio._settings_voice()
    check("the call's voice loop: Settings unchanged, nothing", used == [], used)
    sb.CONFIG.write_text(json.dumps({"voice_mode": {"voice": "pocket:alba"}}))
    os.utime(str(sb.CONFIG), (time.time() + 5, time.time() + 5))
    vio._settings_voice()
    check("  Settings changed to Alba mid-call: the engine is told, between sentences", used == [(None, "alba", "")], used)
    fixed = sb.VoiceIO(tmp, eng, log=lambda m: None, leaks=lambda: True, talk_over=False, fixed="pocket:jean")
    fixed.SETTINGS_EVERY = 0.0
    os.utime(str(sb.CONFIG), (time.time() + 9, time.time() + 9))
    fixed._settings_voice()
    check("  a call whose agent has a voice of its own keeps it", used == [(None, "alba", "")], used)
finally:
    sb.CONFIG, tts.available = real_cfg, real_av
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
