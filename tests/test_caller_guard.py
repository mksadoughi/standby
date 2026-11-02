"""The AI caller gives way to the user's own calls: it never starts while one of theirs is live, and a run stops
itself the moment one starts (a run on top of a live call stalled the user's recorder 48 s, 2026-11-01).
A stand-in for the user's app answers /api/state; nothing real is started or read."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import http.server, importlib.util, json, os, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("caller", os.path.join(HERE, "caller", "caller.py"))
caller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(caller)
fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


real = tempfile.mkdtemp(prefix="sbcallerguard-")      # the user's Standby folder, as the caller reads it
caller.REAL_ROOT = real
state = {"live": None, "starting": None, "sessions": []}
seen = []


class App(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        seen.append((self.path, self.headers.get("X-Standby-Token")))
        body = json.dumps(state).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), App)
threading.Thread(target=srv.serve_forever, daemon=True).start()
open(os.path.join(real, "ui.port"), "w").write(str(srv.server_address[1]))
open(os.path.join(real, "ui.token"), "w").write("tok-1")

check("nothing of theirs live: the caller may run", caller.user_live() is None)
check("  asked of their app, with its own token", seen[-1:] == [("/api/state", "tok-1")], seen)
state["live"] = "talk-20990101-120000"
check("a call of theirs live: it says so", "live (talk-20990101-120000)" in (caller.user_live() or ""))
state["live"], state["starting"] = None, {"state": "starting"}
check("one starting counts too", caller.user_live() == "a call or meeting is starting")
state["starting"] = None


class FakeStack(object):
    stopped = 0

    def stop(self):
        self.stopped += 1


caller.UserGuard.EVERY = 0.1
stack, said = FakeStack(), []
g = caller.UserGuard(stack, said.append)
time.sleep(0.4)
check("during a run, nothing of theirs: the run goes on", stack.stopped == 0 and g.why is None)
state["live"] = "talk-20990101-120500"
end = time.time() + 3
while not stack.stopped and time.time() < end:
    time.sleep(0.05)
check("  a call of theirs starts: the run's Standby is stopped at once, and the log says why",
      stack.stopped == 1 and "live (talk-20990101-120500)" in (g.why or "")
      and any("This run stops now" in m for m in said), (stack.stopped, g.why, said))
g.end()

caller.signed_in = lambda: (True, "test")
caller.setup = lambda log=print: True
ran = []
caller.run_one = lambda *a, **k: ran.append(a) or {"passed": True}
check("a batch asked for while one of theirs is live: nothing runs, and it ends with 3",
      caller.main(["run", "handoff"]) == 3 and ran == [], ran)
state["live"] = None
check("  and once it's over, it runs", caller.main(["run", "handoff"]) == 0 and len(ran) == 1, ran)

os.remove(os.path.join(real, "ui.port"))
srv.shutdown()
check("their app not up, and no recorder of theirs: nothing live", caller.user_live() is None)

print()
if fails:
    print("FAILURES: %s" % fails)
    sys.exit(1)
print("ALL PASS")
