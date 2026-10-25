"""The threads a call's voice depends on, ahead of whatever else keeps the computer busy.

The OS schedules by quality of service: work marked user-interactive runs before the default
work of everything else (a build, a test run, a browser), and on the fast cores. The voice's
threads are marked so: the recorder's loop and its playback, Standby's voice process, the
app's conversation and its question, the agent's voice. A user process may raise its own
threads' QoS (not its nice value, which takes root).

    from native import qos
    qos.interactive()      # this thread, from now on
    held = qos.hold("a call's voice")    # the whole process, while a call is live: no App Nap
    qos.release(held)

A process the OS sees as idle (its window hidden, no audio of its own) may be put to sleep
(App Nap on a Mac: its timers stretched, its priority dropped; the app was found at priority
4, against 55 for dictation). An activity held for the call says it is the user's,
latency-critical work. Never raises: where there is no implementation, it does nothing.
"""
from native import impl

try:
    _os = impl("qos")
except ImportError:
    _os = None                       # an OS without one: nothing is raised, nothing done


def interactive():
    """This thread at user-interactive QoS. True if it took."""
    try:
        return bool(_os and _os.interactive())
    except Exception:
        return False


def current():
    """This thread's QoS class (0x21 user-interactive, 0x19 user-initiated, 0x15 default...), or None."""
    try:
        return _os.current() if _os else None
    except Exception:
        return None


def hold(reason):
    """An activity for this process (no App Nap, no timer coalescing) until release(); its
    token, or None where there is none to hold."""
    try:
        return _os.hold(reason) if _os else None
    except Exception:
        return None


def release(token):
    if token is None or not _os:
        return
    try:
        _os.release(token)
    except Exception:
        pass
