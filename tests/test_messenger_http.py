"""Standby's navigator over HTTP, against a real mac/ui.py server on a throwaway root and port:
the workspaces and agents it lists (read only), the past calls and meetings, the event stream,
and the chat routes that are gone."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

fails = []


def check(label, ok, d=""):
    print("  %-78s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:700])


root = Path(tempfile.mkdtemp(prefix="sbmsgh-"))
home = root / "home"
home.mkdir()
SID = "bbbbbbbb-0000-4000-8000-000000000001"
(root / ".standby").mkdir()
(root / ".standby" / "workspaces.json").write_text(json.dumps({
    "workspaces": [{"id": "w-0a1b2c3d", "name": "orion", "machine": "local", "folder": "/Users/you/code/orion",
                    "agents": ["a-0a1b2c3d"]}],
    "agents": {"a-0a1b2c3d": {"id": "a-0a1b2c3d", "workspace": "w-0a1b2c3d", "name": "Nadia", "color": 0,
                              "session": SID, "sessions": [SID]}}}))

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
env = dict(os.environ, HOME=str(home), STANDBY_ROOT=str(root / ".standby"), STANDBY_UI_PORT=str(PORT),
           STANDBY_LAUNCH_AGENTS=str(root / "LaunchAgents"))
srv = subprocess.Popen([sys.executable, "mac/ui.py"], env=env, stderr=open(root / "ui.err", "w"))
B = "http://127.0.0.1:%d" % PORT
import sbtoken  # noqa: E402  (the requests here carry the server's token, as Standby's clients do)
sbtoken.use(lambda: (root / ".standby" / "ui.token").read_text().strip() if (root / ".standby" / "ui.token").exists() else "")


def call(path, body=None):
    req = urllib.request.Request(B + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


try:
    # Cold PyObjC imports can take over 30 seconds on a busy hosted Mac. Wait for
    # the launch token before constructing a request: an empty token queued while
    # serve() is starting still fails authentication once the server accepts it.
    end = time.monotonic() + 90
    ready = False
    last = "the launch token is not written yet"
    while time.monotonic() < end:
        if srv.poll() is not None:
            print("FAIL: the test's own server exited:\n%s" % (root / "ui.err").read_text())
            sys.exit(1)
        if not (root / ".standby" / "ui.token").exists():
            time.sleep(0.2)
            continue
        try:
            call("/api/msg/workspaces")
            ready = True
            break
        except (urllib.error.URLError, OSError) as exc:
            last = "HTTP %s" % exc.code if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            time.sleep(0.2)
    if not ready:
        raise RuntimeError("the test server was not ready after 90s (%s):\n%s" % (last, (root / "ui.err").read_text()))

    got = call("/api/msg/workspaces")
    check("the navigator's list: the workspace and its agent, with what of it is live",
          [w["name"] for w in got["workspaces"]] == ["orion"] and [a["name"] for a in got["agents"]] == ["Nadia"]
          and got["agents"][0]["live"] == {"call": None, "meeting": None} and "unread" not in got["workspaces"][0], got)
    check("  and the past calls and meetings: none yet", call("/api/msg/past") == {"items": []})
    stream = urllib.request.urlopen(B + "/api/msg/events", timeout=30)
    first = stream.readline() + stream.readline()
    check("the event stream: SSE, and says hello", stream.headers.get_content_type() == "text/event-stream"
          and first.startswith(b"event: hello\ndata: "), first)
    gone = [("/api/msg/timeline?scope=all", None), ("/api/msg/workspaces", {"machine": "local", "folder": "/x"}),
            ("/api/msg/send", {"to": "a-0a1b2c3d", "text": "hi"}), ("/api/msg/read", {"scope": "all"}),
            ("/api/msg/agent", {"id": "a-0a1b2c3d", "name": "X"}), ("/api/msg/agents", {"workspace": "w-0a1b2c3d"}),
            ("/api/msg/workspace", {"id": "w-0a1b2c3d", "remove": True}), ("/api/msg/open", {}),
            ("/api/msg/file?message=m&file=f", None), ("/api/msg/call", {"agent": "a-0a1b2c3d"}),
            ("/api/msg/mac", {"agent": "a-0a1b2c3d", "mic": True})]
    answers = {}
    for path, body in gone:
        try:
            answers[path] = call(path, body)
        except urllib.error.HTTPError as e:
            answers[path] = {"error": "HTTP %d" % e.code}
    check("the chat's routes are gone: each says so, and changes nothing",
          all(v.get("error") for v in answers.values())
          and [w["name"] for w in call("/api/msg/workspaces")["workspaces"]] == ["orion"]
          and [a["name"] for a in call("/api/msg/workspaces")["agents"]] == ["Nadia"], answers)
    stream.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except subprocess.TimeoutExpired:
        srv.kill()

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
