"""native/sound.py on a Mac: NSSound, on the system output as macOS's own alerts are, started on the main
thread as the app's other AppKit work is. Each sound is held here until it has finished: an NSSound let go
of mid-play stops."""
import threading

from AppKit import NSSound
from PyObjCTools import AppHelper

_playing = []
_lock = threading.Lock()


def play(path):
    s = NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
    if s is None:
        return False
    with _lock:
        _playing[:] = [p for p in _playing if p.isPlaying()]
        _playing.append(s)
    AppHelper.callAfter(s.play)
    return True
