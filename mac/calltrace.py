"""The call's trace (plugin/bin/sbtrace.py), for the recorder and the app: one writer for all three
processes that see a call. trace(sid, ev, turn, **fields) never waits."""
import os
import sys

_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin")
if _bin not in sys.path:
    sys.path.append(_bin)
import sbtrace  # noqa: E402

source, flush = sbtrace.source, sbtrace.flush


def trace(sid, ev, turn=None, **fields):
    try:
        sbtrace.event(sid, ev, turn, **fields)
    except Exception:
        pass                                     # a trace never breaks a call
