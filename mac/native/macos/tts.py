"""native/tts.py on a Mac: Pocket TTS on the GPU through MLX (in the voice process), and an
AVAudioEngine of its own for the speakers."""
import numpy as np

import tts
from tts import (DEFAULT_VOICE, LEVEL_DB, RATE, TARGET_DB, VOICES, Clean, fetch, lead_in, limit, pcm_buffer,
                 pcm_format, say_as)

CACHE_BYTES = 128 * 1024 * 1024          # MLX keeps no more than this of freed GPU memory for reuse


class Engine(object):
    """Pocket TTS through mlx-audio, in the voice process."""

    rate = RATE

    def __init__(self, voice):
        self.use(voice)
        self.model = None

    def use(self, voice):
        """The voice for the next sentence: its state is made for each sentence anyway."""
        self.voice = voice if voice in VOICES else DEFAULT_VOICE
        self.gain = 10 ** ((TARGET_DB - LEVEL_DB.get(self.voice, -22.0)) / 20.0)

    def load(self, tell):
        path = fetch(tell)
        tell({"state": "loading"})
        import mlx.core as mx
        mx.set_cache_limit(CACHE_BYTES)
        from mlx_audio.tts.utils import load_model
        self.model = load_model(path)
        for _ in self.stream("Hello."):          # the first one compiles: not the user's sentence
            pass

    def stream(self, text):
        import mlx.core as mx
        # A fresh voice state for each sentence: one reused garbles the next (measured).
        state = self.model.get_state_for_audio_prompt(self.voice)
        clean = Clean()                          # its pauses silent, as macOS's are (the user's hiss)
        for piece in self.model.generate_audio_stream(model_state=state, text_to_generate=lead_in(say_as(text))):
            mx.eval(piece)
            out = clean.feed(np.asarray(piece, dtype="float32") * self.gain)
            if len(out):
                yield limit(out)
        tail = clean.end()
        if len(tail):
            yield limit(tail)


class Speakers(object):
    """Standby's voice to the speakers, where the recorder does not play it: an
    AVAudioPlayerNode on an AVAudioEngine of its own, output only (it never
    touches the mic), from open() to close()."""

    def __init__(self, rate=RATE, avf=None, log=print):
        self.rate, self.avf, self.log = rate, avf, log
        self.engine = self.player = self.fmt = None

    def open(self):
        if self.engine is not None:
            return
        if self.avf is None:
            import AVFoundation
            self.avf = AVFoundation
        avf = self.avf
        engine, player = avf.AVAudioEngine.alloc().init(), avf.AVAudioPlayerNode.alloc().init()
        engine.attachNode_(player)
        self.fmt = pcm_format(avf, self.rate)
        engine.connect_to_format_(player, engine.mainMixerNode(), self.fmt)
        engine.prepare()
        ok, err = engine.startAndReturnError_(None)
        if not ok:
            raise RuntimeError("the speakers did not start: %s" % err)
        self.engine, self.player = engine, player

    def play(self, samples, done):
        engine, player = self.engine, self.player
        if engine is None:
            done()
            return
        if not engine.isRunning():                     # the output changed (headphones): it stopped
            ok, err = engine.startAndReturnError_(None)
            if not ok:
                self.log("voice: the speakers did not start again: %s" % err)
                done()
                return
        player.scheduleBuffer_completionHandler_(pcm_buffer(self.avf, self.fmt, samples), done)
        if not player.isPlaying():
            player.play()

    def halt(self):
        if self.player is not None:
            self.player.stop()

    def close(self):
        engine, self.engine = self.engine, None
        self.halt()
        self.player = None
        if engine is not None:
            try:
                engine.stop()
            except Exception as exc:
                self.log("voice: stopping the speakers: %r" % exc)
