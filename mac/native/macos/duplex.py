"""native/duplex.py on a Mac: voice mode's audio engine on open speakers, for VoiceIO in standby.py.

AVAudioEngine with Apple's voice processing: the mic comes out of it with the
engine's own playback (Claude's voice) taken out, so the user can talk over
Claude without headphones. It removes only what this engine plays, which is
why Claude's voice is played here and not by the app.

The AVFoundation calls are thin; the parts with logic (the mic's rate to the
speech model's, which voice is playing, stopping) are plain Python, tested
with fakes. Nothing here runs on the recorder's main (MLX) thread: the mic
arrives on the engine's audio thread, the speech engine's buffers on its own.
"""

import os
import time

import numpy as np

RATE = 16000              # what the speech-to-text model wants
SLOW_FIRST = 0.3          # a sentence whose first sound takes longer is logged


class Resampler(object):
    """Mono float32 at `rate` to 16 kHz, in pieces as they come.

    A windowed-sinc low-pass first (nothing above 7.2 kHz: the model listens
    to speech up to 8 kHz, and anything higher would fold back into it), then
    the 16 kHz samples are read off the filtered signal, interpolating where
    the rates do not divide (44.1 kHz). 48 kHz, the usual rate, is an exact
    one in three. The filter's history and the read position carry across
    pieces, so a stream cut anywhere comes out the same as in one piece."""

    TAPS = 63

    def __init__(self, rate, out=RATE):
        self.rate, self.out = float(rate), float(out)
        self.step = self.rate / self.out
        n = np.arange(self.TAPS) - (self.TAPS - 1) / 2.0
        cutoff = 2.0 * 0.45 * self.out / self.rate          # 0.45 of the output rate, as a share of Nyquist
        h = np.sinc(cutoff * n) * np.hanning(self.TAPS)
        self.h = (h / h.sum()).astype("float64")
        self.hist = np.zeros(self.TAPS - 1)
        self.last = np.zeros(0)
        self.pos = 0.0                                      # where the next output sample is read

    def feed(self, x):
        x = np.asarray(x, dtype="float64").reshape(-1)
        if self.rate == self.out:
            return x.astype("float32")
        buf = np.concatenate([self.hist, x])
        y = np.convolve(buf, self.h, mode="valid")          # one per input sample, a fixed delay
        self.hist = buf[-(self.TAPS - 1):]
        y = np.concatenate([self.last, y])
        if len(y) < 2:
            self.last = y
            return np.zeros(0, dtype="float32")
        stop = len(y) - 1                                   # the last sample is kept for the next piece
        at = self.pos + self.step * np.arange(max(0, int(np.ceil((stop - self.pos) / self.step))))
        at = at[at < stop]
        i = at.astype(int)
        f = at - i
        out = y[i] * (1.0 - f) + y[i + 1] * f
        nxt = (at[-1] + self.step) if len(at) else self.pos
        self.pos = nxt - stop
        self.last = y[stop:]
        return out.astype("float32")


def pick_voice(voices, wanted=None):
    """The speech engine's voice: the one asked for (identifier or name, as in
    config.json's voice_mode.voice, which the app's speaker uses too), else
    the most natural English one installed: Premium, then Enhanced, US first.
    None: the system's own."""
    def rank(v):
        lang = str(v.language())
        return (int(v.quality()), lang == "en-US") if lang.startswith("en") else (-1, False)
    if wanted:
        want = str(wanted).lower().replace("(", "").replace(")", "").strip()
        for v in voices:
            grade = {3: "premium", 2: "enhanced"}.get(int(v.quality()), "")
            if want in (str(v.identifier()).lower(), str(v.name()).lower(), ("%s %s" % (v.name(), grade)).lower().strip()):
                return v
    best = max(voices, key=rank, default=None)
    return best if best is not None and rank(best)[0] >= 2 else None


class AVFBackend(object):
    """VoiceIO's engine: the system's default mic and speakers through
    AVAudioEngine with voice processing.

    start_mic(device_name, feed): voice processing on (aec says whether it
    took), the mic tapped, resampled to 16 kHz and given to feed, on the
    engine's audio thread. Only the system's default input can go through it,
    so a session recording another mic is refused.

    start_output(): the same voice, played without voice processing and without
    touching the mic, for headphones (devices.output_leaks is false): nothing it
    plays reaches the mic there, so there is no echo to cancel, and the session's
    own mic (one that isn't the default, say the Mac's while AirPods play) stays
    as it is. Opening the default input would drop AirPods to call quality.

    say(text): the speech engine's buffers are played as
    they come; playing is true from say() until the last one has played, or
    halt(). Those buffers arrive on the main thread's run loop only (measured),
    so the recorder's main loop waits in idle() while voice mode is on.

    neural: a Pocket voice ("alba"): Standby's own voice (tts.py), its
    process started with the mic and ended with it, its sound played by the
    same player, so it is cancelled from the mic too. Every sentence is in it:
    while it loads, its process holds them and says them once ready. Never the
    Mac's voice instead (the user's rule): a process that dies is opened again
    (REOPENS), and past that `failed` says why, for the orb, and nothing is said.
    voice: a Mac voice the user chose instead (no neural), said by macOS.

    avf, foundation: the AVFoundation and Foundation modules, or fakes.
    process: a tts.VoiceProcess, in tests."""

    def __init__(self, avf=None, foundation=None, default_input=None, voice=None, log=print,
                 neural=None, process=None, default_output=None):
        if avf is None:
            import AVFoundation as avf
        if foundation is None:
            import Foundation as foundation
        self.avf, self.fd, self.log = avf, foundation, log
        self.default_input = default_input or _default_input
        self.default_output = default_output or _default_output
        self.wanted, self.voice = voice, None
        self.engine = self.inp = self.player = self.synth = None
        self.aec, self._av, self.res = False, False, None
        self._fmt, self._token, self._pending, self._ended = None, None, 0, False
        self.neural, self._process, self.speech = neural, process, None
        self.failed = ""         # why Standby's voice can't speak: on the orb, and nothing is said
        self._why = ""           # ...when it's because of the voice chosen (use_voice)
        self._reopens = 0        # its process opened again this voice mode (REOPENS at most)
        self.retrying_since = None   # when it started coming back (reopened, or the engine made again): the band says so
        self._logged = False     # this answer's voice, logged at its first sentence
        self._halts = 0          # halt()s so far
        self._ducked = False     # turned down and paused while the user talks over it (duck)
        self._ramped = False     # its first sound came in from quiet (ramp_in): once per engine
        self.own_audio, self.own_secs = [], 0.0     # Standby's voice as played, its first seconds (VoiceIO.learn_own)
        self._fades = 0          # duck()s, unduck()s and halt()s so far: a newer one ends a fade
        import threading
        self._lock = threading.Lock()

    # -- the mic ---------------------------------------------------------------
    def start_mic(self, device_name, feed):
        default = self.default_input()
        if device_name != default:
            raise RuntimeError("echo cancellation takes the system's default mic (%s), not %s"
                               % (default, device_name))
        avf = self.avf
        engine = avf.AVAudioEngine.alloc().init()
        inp = engine.inputNode()
        ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
        self.aec = bool(ok) and err is None
        if not self.aec:
            self.log("voice: voice processing did not start (%s): no echo cancellation" % err)
        else:
            self._duck_little(inp)
        fmt = inp.outputFormatForBus_(0)
        self.res = Resampler(fmt.sampleRate())
        res = self.res

        def tap(buf, _when):
            n = int(buf.frameLength())
            if n:
                x = np.frombuffer(buf.floatChannelData()[0].as_buffer(n), dtype=np.float32)
                feed(res.feed(x))
        inp.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, tap)
        # The path to the speakers, at the mic's rate: left to the engine, voice processing's output
        # won't initialize on a MacBook's own mic and speakers (-10875, measured with tools/duplex_probe.py)
        err = self._play_through(engine, fmt.sampleRate())
        if err is not None:
            inp.removeTapOnBus_(0)
            inp.setVoiceProcessingEnabled_error_(False, None)
            raise RuntimeError("the audio engine did not start (%s): %s" % (self._where(engine, fmt, default), err))
        self.engine, self.inp, self._ramped = engine, inp, False
        self._voices()

    def start_output(self):
        """The voice on headphones, no voice processing, the mic left alone (see the class)."""
        engine = self.avf.AVAudioEngine.alloc().init()
        rate = engine.outputNode().outputFormatForBus_(0).sampleRate() or 48000.0
        err = self._play_through(engine, rate)
        if err is not None:
            raise RuntimeError("the audio engine did not start (output %s): %s" % (self.default_output() or "?", err))
        self.engine, self.inp, self.aec = engine, None, False
        self._voices()

    def _play_through(self, engine, rate):
        """Claude's player on the engine, the mixer to the output at `rate`, started: None, or why not."""
        avf = self.avf
        self.player, self._fmt = avf.AVAudioPlayerNode.alloc().init(), None    # a new player: connected at its first buffer
        engine.attachNode_(self.player)
        out = avf.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(rate, self._out_channels(engine))
        engine.connect_to_format_(engine.mainMixerNode(), engine.outputNode(), out)
        engine.prepare()
        ok, err = engine.startAndReturnError_(None)
        return None if ok else err

    def _voices(self):
        """The Mac's voice, and Standby's own process (opened with the engine, ended with it)."""
        avf = self.avf
        self.synth = avf.AVSpeechSynthesizer.alloc().init()
        voices = list(avf.AVSpeechSynthesisVoice.speechVoices() or [])
        self.voice = pick_voice(voices, self.wanted)
        if self.neural:
            import tts
            process = self._process or tts.VoiceProcess(self.neural, log=self.log)
            process.open()
            self.speech = tts.Speech(process, _PlayerOut(self), log=self.log)

    def use_voice(self, system, neural, why=""):
        """Settings' voice changed during the call: from the next sentence. Another of Standby's
        own voices is a word to its process; the Mac's voice, or back, takes the next sentence's path.
        why: as backend_voices says (Standby's voice isn't installed), or ""."""
        if (system, neural) == (self.wanted, self.neural):
            return
        self.wanted = system
        if self.synth is not None:
            self.voice = pick_voice(list(self.avf.AVSpeechSynthesisVoice.speechVoices() or []), system)
        if neural and self.speech is not None:
            self.speech.process.use(neural)
        elif neural and (self.engine is not None or self.player is not None):     # started: open it now
            import tts
            process = tts.VoiceProcess(neural, log=self.log)
            process.open()
            self.speech = tts.Speech(process, _PlayerOut(self), log=self.log)
        self.neural, self._logged = neural, False
        self.own_audio, self.own_secs = [], 0.0     # another voice: its print made again (VoiceIO.learn_own)
        if why or self.failed == self._why:          # a reason of the old choice's goes with it
            self.failed = why
        self._why = why
        self.log("voice: Settings changed the voice to %s, from the next sentence" % (neural or system or "the system's"))

    @staticmethod
    def _out_channels(engine):
        """The speakers' channels (2 on a MacBook), 1 to 2 whatever they report."""
        try:
            return max(1, min(2, int(engine.outputNode().outputFormatForBus_(0).channelCount())))
        except Exception:
            return 2

    def _where(self, engine, fmt, mic):
        """The mic and the output, as voice processing met them: what to look at when
        it won't start (-10875 on the user's Mac: Apple's forums have it when the mic
        and the speakers are different devices). Never raises."""
        def rate(f):
            try:
                return "%d Hz x %d" % (f.sampleRate(), f.channelCount())
            except Exception:
                return "?"
        try:
            out_fmt = engine.outputNode().outputFormatForBus_(0)
        except Exception:
            out_fmt = None
        try:
            out = self.default_output() or "?"
        except Exception:
            out = "?"
        return "mic %s at %s, output %s at %s" % (mic, rate(fmt), out, rate(out_fmt))

    def _duck_little(self, inp):
        """Other apps (a call) are turned down while voice processing runs: as
        little as macOS allows (macOS 14+). The user's choice."""
        try:
            cfg = getattr(self.avf, "AVAudioVoiceProcessingOtherAudioDuckingConfiguration", None)
            inp.setVoiceProcessingOtherAudioDuckingConfiguration_(
                cfg(False, 10) if cfg is not None else (False, 10))       # no advanced ducking, the minimum level
        except Exception as exc:
            self.log("voice: other apps are ducked as macOS does by default (%r)" % exc)

    def stop_mic(self):
        self.halt()
        speech, self.speech = self.speech, None
        if speech is not None:
            speech.close()                          # the voice process ends: its memory goes back
        engine, inp, self.engine, self.inp = self.engine, self.inp, None, None
        if engine is None:
            return
        try:
            engine.stop()
            if inp is not None:
                inp.removeTapOnBus_(0)
                inp.setVoiceProcessingEnabled_error_(False, None)
        except Exception as exc:
            self.log("voice: stopping the audio engine: %r" % exc)
        self.aec = False

    # -- Claude's voice ----------------------------------------------------------
    REOPENS = 2          # Standby's voice process opened again this often in a voice mode, then: failed

    @property
    def playing(self):
        speech = self.speech
        if speech is not None and speech.lost:       # its process stopped mid-answer: open it, say them again
            lost = speech.take_lost()
            if self._alive(reopen=True) is not None:
                for text in lost:
                    speech.say(text)
        return self._av or bool(speech is not None and speech.busy)

    @property
    def loading(self):
        """Standby's voice is still getting ready (a sentence waits for it: not stuck)."""
        speech = self.speech
        return bool(speech is not None and not self.failed
                    and getattr(speech.process, "state", "") in ("starting", "downloading", "loading"))

    def begin(self):
        """A new answer: its voice is logged at its first sentence."""
        self._logged = False

    def tts_status(self):
        st = self.speech.status() if self.speech is not None else None
        if self.failed:                          # Standby's voice can't speak, and why: the orb says so
            return dict(st or {}, failed=self.failed)
        if self.retrying_since is not None:
            if self.speech is None or self.speech.usable:
                self.retrying_since = None       # back (or a Mac voice chosen: nothing to wait for)
            else:
                st = dict(st or {}, retrying_since=self.retrying_since)
        return st

    def say(self, text):
        self.said_at, self.first_sound = time.time(), None      # its first sound: _PlayerOut (the trace's synth time)
        if not self.neural:                      # a Mac voice the user chose, or Standby's not there
            if self.failed:
                return
            if not self._logged:
                self._logged = True
                self.log("voice: this answer in the voice chosen (%s)" % (self.voice.name() if self.voice is not None else "the system's"))
            return self._say_av(text)
        speech = self._alive()
        if speech is None:
            return
        if not speech.say(text):                 # its pipe broke: the process again, then this sentence
            speech = self._alive(reopen=True)
            if speech is None or not speech.say(text):
                return self._fail("Standby's voice stopped taking sentences")
        if not self._logged:
            self._logged = True
            self.log("voice: this answer in Standby's voice (%s)%s"
                     % (self.neural, "" if speech.usable else ", once it is ready (%s)" % speech.process.state))

    def _alive(self, reopen=False):
        """Standby's voice, its process opened again if it stopped (REOPENS at most), or None: failed."""
        speech = self.speech
        if self.failed:
            return None
        if speech is None:
            return self._fail("Standby's voice isn't open")
        if reopen or getattr(speech.process, "state", "") in ("failed", "off"):
            import tts
            if self._reopens >= self.REOPENS:
                return self._fail(tts.not_ready(speech))
            self._reopens += 1
            self.retrying_since = self.retrying_since or time.time()
            self.log("voice: Standby's voice opened again (%s)" % tts.not_ready(speech))
            speech.process.open()
        return speech

    def _fail(self, why):
        """Standby's voice can't speak: said once, on the orb; nothing is said instead."""
        if not self.failed:
            self.failed, self.retrying_since = why, None
            self.log("voice: Standby's voice isn't working (%s): nothing is said, the orb says so" % why)
            if self.speech is not None:
                self.speech.stop()
        return None

    def _say_av(self, text):
        avf = self.avf
        with self._lock:
            token = self._token = object()
            self._av, self._pending, self._ended = True, 0, False
        import tts
        utt = avf.AVSpeechUtterance.speechUtteranceWithString_(tts.say_as(text))     # as heard; shown as written
        if self.voice is not None:
            utt.setVoice_(self.voice)
        asked = time.time()
        first = []

        def got(buf):                               # on the main thread, as the speech engine writes
            if self._token is not token:
                return                              # halted, or a newer sentence
            if not first:
                first.append(time.time() - asked)
                if first[0] > SLOW_FIRST:          # e.g. behind a long decode on that thread
                    self.log("voice: a sentence's first sound took %.2fs (%d characters)" % (first[0], len(text)))
            if int(buf.frameLength()) <= 1:         # the end of this sentence: empty, or one sample on macOS 14
                with self._lock:
                    self._ended = True
                    if self._pending == 0:
                        self._av = False
                return
            with self._lock:
                self._pending += 1
            self._schedule(buf, buf.format(), lambda: self._played(token))
            if not self.player.isPlaying() and not self._ducked:
                self.player.play()
        self.synth.writeUtterance_toBufferCallback_(utt, got)

    def _played(self, token):
        with self._lock:
            if self._token is not token:
                return
            self._pending -= 1
            if self._ended and self._pending <= 0:
                self._av = False

    def _schedule(self, buf, fmt, done):
        """`buf` to the player, connected in its format. One the player refuses (its
        connection is not the buffer's: a mic switch made a new engine, a route changed
        the output) connects it again and goes at once; refused again, it is dropped
        (done), never left waiting with the voice silent."""
        for again in (False, True):
            try:
                self._connect(fmt, force=again)
                self.player.scheduleBuffer_completionHandler_(buf, done)
                return True
            except Exception as exc:
                if again:
                    self.log("voice: a buffer could not play, dropped (%r)" % exc)
                    done()
                    return False
                self.log("voice: the player refused a buffer (%r): connected again" % exc)

    def _connect(self, fmt, force=False):
        """The player takes the speech engine's own format: connected at its
        first buffer (it is not known before), again only if it changes."""
        if not force and self._fmt is not None and self._fmt.isEqual_(fmt):
            return
        self.engine.connect_to_format_(self.player, self.engine.mainMixerNode(), fmt)
        self._fmt = fmt

    def halt(self):
        with self._lock:
            self._token, self._av = None, False
            self._halts += 1
            self._ducked, self._fades = False, self._fades + 1
        if self.speech is not None:
            self.speech.stop()
        try:
            if self.synth is not None:
                self.synth.stopSpeakingAtBoundary_(0)          # AVSpeechBoundaryImmediate
            if self.player is not None:
                self.player.stop()                             # drops what was scheduled
                with self._lock:                               # after a duck: the next answer at full volume,
                    self.player.setVolume_(1.0)                # and no fade acts after this (_fade)
        except Exception as exc:
            self.log("voice: halt: %r" % exc)

    # Its very first sound through voice processing comes in from quiet over RAMP: the echo
    # cancelling converges on it meanwhile instead of on its loudest (Apple's advice: "ramp up
    # your playback audio volume at the start ... over the first ~200 ms").
    RAMP = 0.25
    OWN_SECS = 4.0       # seconds of Standby's voice kept as played, for its print (VoiceIO.learn_own)

    def ramp_in(self):
        """The first sound of this engine: faded in (RAMP). Once; never after a duck or halt began."""
        with self._lock:
            if self._ramped or not self.aec or self.player is None:
                return
            self._ramped, self._fades = True, self._fades + 1
            fade = self._fades
        self._fade(fade, self.LOW, 1.0, self.RAMP)

    FADE = 0.25          # seconds the voice takes to go quiet when talked over, and FADE / 2 to come back
    LOW = 0.1            # the volume it fades to before it pauses (-20 dB)

    def duck(self):
        """The user talks over it: fade out, then pause, with every sentence kept
        where it was. The player itself pauses, not Standby's voice, so nothing is
        dropped or made again; a duck is not a halt (_halts), so a first sentence
        waiting for Standby's voice keeps waiting."""
        with self._lock:
            if self._ducked or self.player is None:
                return
            self._ducked, self._fades = True, self._fades + 1
            fade = self._fades
        self._fade(fade, 1.0, self.LOW, self.FADE, then=lambda: self.player.pause())

    def unduck(self):
        """It was nothing ("mm-hm", "um"): on from exactly where it paused."""
        with self._lock:
            if not self._ducked or self.player is None:
                return
            self._ducked, self._fades = False, self._fades + 1
            fade = self._fades
        self.player.play()
        self._fade(fade, self.LOW, 1.0, self.FADE / 2)

    def _fade(self, fade, start, end, seconds, then=None, steps=10):
        """The player's volume from start to end over seconds, on a thread of its
        own; a newer duck, unduck or halt ends it where it is."""
        def run():
            for i in range(1, steps + 1):
                with self._lock:                     # checked and set together: never after a newer one
                    if self._fades != fade:
                        return
                    self.player.setVolume_(start + (end - start) * i / float(steps))
                time.sleep(seconds / steps)
            with self._lock:
                if then is not None and self._fades == fade:
                    then()
        import threading
        player = self.player
        if player is None:
            return
        with self._lock:
            if self._fades == fade:
                player.setVolume_(start)
        threading.Thread(target=run, name="voice-fade", daemon=True).start()

    SLICE = 0.01

    def idle(self, seconds):
        """The recorder's main loop waits here while voice mode is on: the
        speech engine delivers its buffers on this thread's run loop. In short
        slices: with nothing attached the run loop returns at once, and a bare
        loop would spin the CPU; and never past `seconds`, so a decode waiting
        for the loop is never held up by more than one tick."""
        end = time.time() + seconds
        loop = self.fd.NSRunLoop.currentRunLoop()
        while True:
            left = end - time.time()
            if left <= 0:
                return
            loop.runUntilDate_(self.fd.NSDate.dateWithTimeIntervalSinceNow_(min(left, self.SLICE)))
            left = end - time.time()
            if left > 0:
                time.sleep(min(left, self.SLICE / 2))


class _PlayerOut(object):
    """Standby's voice through the voice-processing engine's own player (tts.Speech's out)."""

    def __init__(self, backend):
        import tts
        self.b, self.tts = backend, tts
        self.fmt = None

    def play(self, samples, done):
        b = self.b
        if b.player is None:
            done()
            return
        if getattr(b, "first_sound", 0) is None:
            b.first_sound = time.time()
        if self.fmt is None:
            self.fmt = self.tts.pcm_format(b.avf)
        if b.own_secs < b.OWN_SECS:                 # its own voice, kept a moment: its print (VoiceIO.learn_own)
            b.own_audio.append(np.asarray(samples, dtype=np.float32))
            b.own_secs += len(samples) / float(self.tts.RATE)
        b._schedule(self.tts.pcm_buffer(b.avf, self.fmt, samples), self.fmt, done)
        if not b.player.isPlaying() and not b._ducked:
            b.ramp_in()
            b.player.play()

    def halt(self):
        if self.b.player is not None:
            self.b.player.stop()


def _default_output():
    """Where the Mac plays now, by name (and transport): for the log only. Not in a test."""
    if os.environ.get("STANDBY_NO_DEVICES"):
        return None
    from native import mics
    d = mics.system().default_output()
    return "%s (%s)" % (d.name, d.transport) if d is not None else None


def _default_input():
    """The Mac's default mic now: Core Audio's (PortAudio's is the one from
    its start, and the mic follows the Mac)."""
    try:
        from native import mics
        d = mics.system().default_input()
        if d is not None:
            return d.name
    except Exception:
        pass
    import sounddevice as sd
    return sd.query_devices(sd.default.device[0])["name"]


def available():
    """AVFoundation can be loaded here (the engine can run)."""
    try:
        import AVFoundation  # noqa: F401
        return True
    except ImportError:
        return False


def engine(voice=None, neural=None, log=print):
    return AVFBackend(voice=voice, neural=neural, log=log)
