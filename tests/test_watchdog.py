"""The stall watchdog: a blocked main thread, a slow request and a busy page each leave a line
(and for the first two, a stack) in ui.log, once per stall, and say when it is over."""
import os, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import watchdog  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-62s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label); print("     " + str(d)[:600])


lines, queued = [], []
now = [100.0]
w = watchdog.Watchdog(lines.append, on_main=queued.append, main_ident=threading.get_ident(), stall=2.0, clock=lambda: now[0])
w.check()
check("a beat goes to the main thread", len(queued) == 1)
queued.pop()()                                         # the main thread runs it
now[0] += 1; w.check()
check("  answered: nothing said, the next beat goes", lines == [] and len(queued) == 1, lines)
now[0] += 1.5; w.check()
check("  1.5 s late: not yet a stall", lines == [], lines)


def blocked_here():
    now[0] += 1.0
    w.check()


blocked_here()
check("2.5 s late: the stall, with where the main thread is", len(lines) == 1 and lines[0].startswith("stall: the main thread")
      and "blocked_here" in lines[0], lines)
now[0] += 3; w.check()
check("  said once, however long", len(lines) == 1, lines)
queued.pop()()
w.check()
check("  then how long it lasted", len(lines) == 2 and lines[1].startswith("stall over: the main thread was blocked for 5.5"), lines)

del lines[:]
w2 = watchdog.Watchdog(lines.append, stall=0.2)        # a server without a window: requests only
gate = threading.Event()


def slow_handler():
    with watchdog.Request("GET /api/msg/timeline"):
        gate.wait(5)


t = threading.Thread(target=slow_handler); t.start()
time.sleep(0.35); w2.check(); w2.check()
check("a request in flight past the limit: its path and stack, once", len(lines) == 1 and "GET /api/msg/timeline" in lines[0]
      and "slow_handler" in lines[0], lines)
gate.set(); t.join()
check("  and how long it took, when it ends", len(lines) == 2 and lines[1].startswith("stall over: GET /api/msg/timeline took"), lines)
with watchdog.Request("GET /api/msg/events"):
    time.sleep(0.3); w2.check()
check("the event stream stays open by design: never a stall", len(lines) == 2, lines)
with watchdog.Request("GET /api/lite/stream"):
    time.sleep(0.3); w2.check()
check("  nor Standby's stream into Claude Code", len(lines) == 2, lines)
with watchdog.Request("GET /api/state"):
    w2.check()
check("a quick request: nothing said", len(lines) == 2, lines)
watchdog.page_stall({"ms": 4200, "where": '{"kind":"agent"}'})
check("the page's late timer: said with the view", lines[-1] == 'stall: the page was busy for 4.2 s ({"kind":"agent"})', lines)

watchdog.page_stall({"ms": 1300, "where": "fetch /api/msg/workspaces, at start"})
check("a request the page waited on: said as slow, not as a stall", lines[-1] == "slow: the page waited 1.3 s for /api/msg/workspaces, at start", lines)

print("\nALL PASS" if not fails else "\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
