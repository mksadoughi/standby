"""native/voices.py on a Mac: NSSpeechSynthesizer's voices."""
import time


def installed():
    from AppKit import NSSpeechSynthesizer
    return [(str(v), NSSpeechSynthesizer.attributesForVoice_(v) or {}) for v in NSSpeechSynthesizer.availableVoices()]


def say(voice_id, line, timeout):
    """NSSpeechSynthesizer wants a run loop on its thread: this one's, while it speaks."""
    from AppKit import NSSpeechSynthesizer
    from Foundation import NSDate, NSRunLoop
    synth = NSSpeechSynthesizer.alloc().initWithVoice_(voice_id)
    synth.startSpeakingString_(line)
    end = time.time() + timeout
    while time.time() < end:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        if not synth.isSpeaking():
            break
