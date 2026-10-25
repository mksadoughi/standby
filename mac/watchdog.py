"""What froze, written to ui.log while it is frozen.

Three things can make Standby look frozen while its server still answers:

- the main thread (AppKit, the window, the orb) is blocked: a beat sent to it every
  second doesn't arrive within STALL s. The log gets the main thread's Python stack,
  once per stall, and how long it lasted once it ends.
- a request is slow: one in flight for longer than STALL s is logged with its path and
  its thread's stack (the event stream, which stays open by design, is left out).
- the page itself (its JavaScript) is busy: the page reports a timer that fired late,
  and POST /api/stall writes it here.

One daemon thread does the watching; nothing here runs on the main thread except the
beat itself, which only notes the time.
"""

import sys
import threading
import time
import traceback

STALL = 2.0          # s: longer than this is a stall worth a line in the log
EVERY = 1.0          # s between beats sent to the main thread
LONG = ("/api/msg/events", "/api/lite/stream")   # requests that stay open by design

_requests = {}       # thread id -> (path, started)
_lock = threading.Lock()


def stack(ident, most=14):
    """The Python stack of one thread, innermost last, as one log-friendly block."""
    frame = sys._current_frames().get(ident)
    if frame is None:
        return "  (no Python frame)"
    lines = traceback.format_stack(frame)[-most:]
    return "".join(lines).rstrip()


class Request(object):
    """with watchdog.Request("GET /path"): ... marks a request in flight on this thread."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        if not self.path.split(" ", 1)[-1].startswith(LONG):     # "GET /api/msg/events"
            with _lock:
                _requests[threading.get_ident()] = [self.path, time.time(), False]
        return self

    def __exit__(self, *exc):
        with _lock:
            got = _requests.pop(threading.get_ident(), None)
        if got and got[2]:
            Watchdog.log("stall over: %s took %.1f s" % (got[0], time.time() - got[1]))
        return False


class Watchdog(object):
    log = staticmethod(lambda msg: None)

    def __init__(self, log, on_main=None, main_ident=None, stall=STALL, every=EVERY, clock=time.time):
        """log: where lines go. on_main(fn): runs fn on the main thread, soon (None: no
        main-thread watch, as in a server without a window). main_ident: that thread's id."""
        Watchdog.log = staticmethod(log)
        self.on_main, self.main_ident = on_main, main_ident
        self.stall, self.every, self.clock = stall, every, clock
        self.beat_sent = self.beat_seen = clock()
        self.stalled_since = None
        self.thread = None

    def _beat(self):
        self.beat_seen = self.clock()

    def check(self):
        """One look: the main thread, then the requests in flight. Called every `every` s."""
        now = self.clock()
        if self.on_main is not None:
            late = now - self.beat_sent if self.beat_seen < self.beat_sent else 0.0
            if late > self.stall and self.stalled_since is None:
                self.stalled_since = self.beat_sent
                Watchdog.log("stall: the main thread has not answered for %.1f s; it is at:\n%s"
                             % (late, stack(self.main_ident) if self.main_ident else "  (unknown)"))
            elif self.beat_seen >= self.beat_sent:
                if self.stalled_since is not None:
                    Watchdog.log("stall over: the main thread was blocked for %.1f s"
                                 % (self.beat_seen - self.stalled_since))
                    self.stalled_since = None
                self.beat_sent = now
                try:
                    self.on_main(self._beat)
                except Exception:
                    pass
        with _lock:
            slow = [(ident, r) for ident, r in _requests.items() if not r[2] and now - r[1] > self.stall]
            for _ident, r in slow:
                r[2] = True                      # said once; its end is said by Request
        for ident, r in slow:
            Watchdog.log("stall: %s has run for %.1f s; it is at:\n%s" % (r[0], now - r[1], stack(ident)))

    def start(self):
        def run():
            while True:
                time.sleep(self.every)
                try:
                    self.check()
                except Exception as exc:          # the watchdog never takes the app down
                    Watchdog.log("watchdog: %r" % exc)
        self.thread = threading.Thread(target=run, name="watchdog", daemon=True)
        self.thread.start()
        return self


def page_stall(b):
    """POST /api/stall {ms, where}: the page's own timer fired late (its JavaScript was busy), or
    (where "fetch <path>") a request it waited on took long, in its first minute."""
    try:
        ms = int(b.get("ms") or 0)
    except (TypeError, ValueError):
        ms = 0
    where = str(b.get("where") or "")[:120]
    if where.startswith("fetch "):              # "fetch /api/msg/workspaces, at start": a request the page waited on
        Watchdog.log("slow: the page waited %.1f s for %s" % (ms / 1000.0, where[6:]))
    else:
        Watchdog.log("stall: the page was busy for %.1f s (%s)" % (ms / 1000.0, where or "?"))
    return {"ok": True}
