#!/usr/bin/env python3
"""Why voice mode's echo-cancelling engine doesn't start on this Mac.

    ~/.standby/venv/bin/python tools/duplex_probe.py

The user's recorder logged, at every voice mode with talk-over on:
    the audio engine did not start: ... Code=-10875 ... PerformCommand(*outputNode, kAUInitialize ...)
so Claude's voice came from the app, half duplex. This starts the same engine four
ways, about a second each, and prints which start and which don't:

    1. no voice processing          the engine itself, plain
    2. voice processing, bare       nothing attached to it
    3. as Standby does              duplex.AVFBackend.start_mic, its own code
    4. as the recorder does         the plain mic open first, closed, then 3
    5. 3, the output at the mic's rate   the fix to try
    6. the mic tapped as 1 channel       the mic reports 9 with voice processing on
    7. 6, and the output 1 channel too

Before them: which mic and which output are the Mac's defaults (Apple's forums:
voice processing fails with this very error, "client-side input and output formats
do not match (err=-10875)", with AirPods' mic and the Mac's speakers, and works with
the Mac's own mic and speakers). After them: CoreAudio's own lines about it,
from macOS's log for this run (AUVPAggregate, AggregateDevice), which say why.

What the user sees: the mic dot in the menu bar, about a second each time, seven
times. Nothing is played, nothing is recorded or saved, other apps' sound may dip
for a moment (voice processing ducks it). It refuses while Standby is recording, and
in a test. Run from Terminal, macOS may ask once whether Terminal may use the mic.
"""
import os
import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "mac"))
ROOT = Path(os.environ.get("STANDBY_ROOT", Path.home() / ".standby"))


def formats(avf):
    """The default devices' formats, as an engine sees them before voice processing."""
    e = avf.AVAudioEngine.alloc().init()
    i, o = e.inputNode().inputFormatForBus_(0), e.outputNode().outputFormatForBus_(0)
    return "in %d Hz x %d, out %d Hz x %d" % (i.sampleRate(), i.channelCount(), o.sampleRate(), o.channelCount())


def plain(avf, vp):
    e = avf.AVAudioEngine.alloc().init()
    inp = e.inputNode()
    if vp:
        ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
        if not ok:
            raise RuntimeError("voice processing did not turn on: %s" % err)
    fmt = inp.outputFormatForBus_(0)
    inp.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, lambda buf, when: None)
    e.mainMixerNode()
    e.prepare()
    ok, err = e.startAndReturnError_(None)
    try:
        if not ok:
            raise RuntimeError("the audio engine did not start: %s" % err)
        time.sleep(1.0)
    finally:
        e.stop()
        inp.removeTapOnBus_(0)
        if vp:
            inp.setVoiceProcessingEnabled_error_(False, None)


def as_standby(log):
    from native.macos import duplex
    b = duplex.AVFBackend(log=log)                       # no voice of its own, no tone: nothing to play
    b.start_mic(duplex._default_input(), lambda x: None)
    try:
        time.sleep(1.0)
        return "echo cancelled" if b.aec else "started, but no echo cancellation"
    finally:
        b.stop_mic()


def at_mic_rate(avf):
    """As Standby does, but the main mixer connected to the output at the mic's own
    rate, so voice processing's two sides ask for the same rate."""
    e = avf.AVAudioEngine.alloc().init()
    inp = e.inputNode()
    ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
    if not ok:
        raise RuntimeError("voice processing did not turn on: %s" % err)
    fmt = inp.outputFormatForBus_(0)
    inp.installTapOnBus_bufferSize_format_block_(0, 1024, fmt, lambda buf, when: None)
    player = avf.AVAudioPlayerNode.alloc().init()
    e.attachNode_(player)
    out = avf.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(fmt.sampleRate(), 2)
    e.connect_to_format_(e.mainMixerNode(), e.outputNode(), out)
    e.connect_to_format_(player, e.mainMixerNode(), out)
    e.prepare()
    ok, err = e.startAndReturnError_(None)
    try:
        if not ok:
            raise RuntimeError("the audio engine did not start: %s" % err)
        time.sleep(1.0)
        return "started at %d Hz" % fmt.sampleRate()
    finally:
        e.stop()
        inp.removeTapOnBus_(0)
        inp.setVoiceProcessingEnabled_error_(False, None)


def mono(avf, out_channels):
    """Voice processing on, the mic tapped as one channel (the engine mixes its 9 down),
    the output at out_channels: the user's mic reads "48000 Hz x 9" with voice processing
    on, the output 2, and CoreAudio's own words for -10875 are "client-side input and
    output formats do not match"."""
    e = avf.AVAudioEngine.alloc().init()
    inp = e.inputNode()
    ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
    if not ok:
        raise RuntimeError("voice processing did not turn on: %s" % err)
    rate = inp.outputFormatForBus_(0).sampleRate()
    one = avf.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(rate, 1)
    inp.installTapOnBus_bufferSize_format_block_(0, 1024, one, lambda buf, when: None)
    out = avf.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(rate, out_channels)
    player = avf.AVAudioPlayerNode.alloc().init()
    e.attachNode_(player)
    e.connect_to_format_(e.mainMixerNode(), e.outputNode(), out)
    e.connect_to_format_(player, e.mainMixerNode(), out)
    e.prepare()
    ok, err = e.startAndReturnError_(None)
    try:
        if not ok:
            raise RuntimeError("the audio engine did not start: %s" % err)
        time.sleep(1.0)
        return "started: the mic as 1 channel, the output %d" % out_channels
    finally:
        e.stop()
        inp.removeTapOnBus_(0)
        inp.setVoiceProcessingEnabled_error_(False, None)


def devices_now():
    """The default mic and output, by name and transport (the Mac's own pair works in
    Apple's forums; another mic with the Mac's speakers fails with this error)."""
    from native import mics
    ca = mics.system()
    return "mic: %r | output: %r" % (ca.default_input(), ca.default_output())


def coreaudio_lines(since):
    """What CoreAudio logged about voice processing in this process, from macOS's log."""
    import subprocess
    pred = ('processID == %d AND (eventMessage CONTAINS "AUVPAggregate" OR eventMessage CONTAINS "AggregateDevice" '
            'OR eventMessage CONTAINS "10875" OR eventMessage CONTAINS "AVAEInternal")' % os.getpid())
    r = subprocess.run(["/usr/bin/log", "show", "--style", "compact", "--start", since, "--predicate", pred],
                       capture_output=True, text=True, timeout=120)
    return [l for l in r.stdout.splitlines() if "AUVP" in l or "Aggregate" in l or "10875" in l or "AVAE" in l]


def as_recorder(log):
    import sounddevice as sd
    s = sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=8000)
    s.start()
    time.sleep(0.5)
    s.stop()
    s.close()                                            # Channel.borrow: one stream at a time on the mic
    return as_standby(log)


def main():
    if os.environ.get("STANDBY_NO_DEVICES"):
        print("not in a test: it opens the real mic")
        return 2
    if (ROOT / "daemon.ready").exists():
        print("Standby is recording now: stop the session first, then run this again")
        return 2
    import AVFoundation as avf
    notes = []
    print("macOS %s, Python %s" % (platform.mac_ver()[0], platform.python_version()))
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        print(devices_now())
        print("formats: %s" % formats(avf))
    except Exception as exc:
        print("devices: %r" % exc)
    steps = [("1 no voice processing", lambda: plain(avf, False) or "started"),
             ("2 voice processing, bare", lambda: plain(avf, True) or "started"),
             ("3 as Standby does", lambda: as_standby(notes.append)),
             ("4 as the recorder does", lambda: as_recorder(notes.append)),
             ("5 output at the mic's rate", lambda: at_mic_rate(avf)),
             ("6 the mic as 1 channel", lambda: mono(avf, 2)),
             ("7 mic and output 1 channel", lambda: mono(avf, 1))]
    for name, step in steps:
        t0 = time.time()
        try:
            got = "OK, %s" % step()
        except Exception as exc:
            got = "FAILED: %s" % str(exc)[:300]
        print("%-26s %s (%.1fs)" % (name, got, time.time() - t0))
        time.sleep(0.3)
    for n in notes:
        print("  " + n)
    try:
        lines = coreaudio_lines(since)
        print("CoreAudio said (%d lines):" % len(lines))
        for l in lines[-20:]:
            print("  " + l[:300])
    except Exception as exc:
        print("CoreAudio's log: not read (%r)" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
