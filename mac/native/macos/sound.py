"""native/sound.py on a Mac: an output AudioQueue on the default output, one for each sound, let go once
it has played out. Any thread may start one (no main thread, no AppKit): the queue plays on a thread of
its own. Starting one on a device that is mic and speakers in one (AirPods, a VM's) has macOS check the
mic, as any player does there, and the start waits on its prompt: ui.call_sound plays nothing while the
mic is undecided, and plays on a thread of its own."""
import ctypes
import threading
import wave

LPCM = 0x6C70636D                          # 'lpcm'
SIGNED_PACKED = 0x4 | 0x8                  # kAudioFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked
LET_GO_AFTER = 1.0                         # seconds past its end: the queue has stopped by then

_LIB = None


class _Format(ctypes.Structure):           # AudioStreamBasicDescription
    _fields_ = [("rate", ctypes.c_double), ("format_id", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("bytes_per_packet", ctypes.c_uint32), ("frames_per_packet", ctypes.c_uint32),
                ("bytes_per_frame", ctypes.c_uint32), ("channels", ctypes.c_uint32),
                ("bits", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


class _Buffer(ctypes.Structure):           # AudioQueueBuffer
    _fields_ = [("capacity", ctypes.c_uint32), ("data", ctypes.c_void_p), ("size", ctypes.c_uint32),
                ("user", ctypes.c_void_p), ("desc_capacity", ctypes.c_uint32), ("descs", ctypes.c_void_p),
                ("desc_count", ctypes.c_uint32)]


_Done = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Buffer))
_done = _Done(lambda user, queue, buf: None)        # its one buffer played: the stop already asked for follows


def _lib():
    global _LIB
    if _LIB is None:
        lib = ctypes.CDLL("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")
        q, b = ctypes.c_void_p, ctypes.POINTER(_Buffer)
        for name, args in (("AudioQueueNewOutput", [ctypes.POINTER(_Format), _Done, ctypes.c_void_p, ctypes.c_void_p,
                                                    ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(q)]),
                           ("AudioQueueAllocateBuffer", [q, ctypes.c_uint32, ctypes.POINTER(b)]),
                           ("AudioQueueEnqueueBuffer", [q, b, ctypes.c_uint32, ctypes.c_void_p]),
                           ("AudioQueueStart", [q, ctypes.c_void_p]),
                           ("AudioQueueStop", [q, ctypes.c_ubyte]),
                           ("AudioQueueDispose", [q, ctypes.c_ubyte])):
            f = getattr(lib, name)
            f.argtypes, f.restype = args, ctypes.c_int32
        _LIB = lib
    return _LIB


def play(path):
    try:
        with wave.open(path, "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            pcm = w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error):
        return False
    if width not in (2, 3, 4) or not pcm:  # 8-bit WAV is unsigned: none of ours
        return False
    lib, frame = _lib(), channels * width
    fmt = _Format(rate, LPCM, SIGNED_PACKED, frame, 1, frame, channels, width * 8, 0)
    queue, buf = ctypes.c_void_p(), ctypes.POINTER(_Buffer)()
    if lib.AudioQueueNewOutput(ctypes.byref(fmt), _done, None, None, None, 0, ctypes.byref(queue)) != 0:
        return False
    started = lib.AudioQueueAllocateBuffer(queue, len(pcm), ctypes.byref(buf)) == 0
    if started:
        ctypes.memmove(buf.contents.data, pcm, len(pcm))
        buf.contents.size = len(pcm)
        started = lib.AudioQueueEnqueueBuffer(queue, buf, 0, None) == 0 and lib.AudioQueueStart(queue, None) == 0
    if not started:
        lib.AudioQueueDispose(queue, 1)
        return False
    lib.AudioQueueStop(queue, 0)           # not at once: it plays what's queued out, then stops
    let_go = threading.Timer(len(pcm) / float(rate * frame) + LET_GO_AFTER, lib.AudioQueueDispose, (queue, 1))
    let_go.daemon = True
    let_go.start()
    return True
