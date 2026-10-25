"""A call's voice turns keep Claude Code's prompt cache: checked on the real Claude Code, against a stand-in
for the API that records each request (no model, no login: a home folder and token of its own). Each turn is
a fork of the call's conversation, so its request must begin with the request that made the conversation's
last committed turn, exactly: that request's cache entry is then read, not written again (the first ledger
call wrote ~6.5k tokens a turn and read none of the record: 10/25 00:36). And no "name this session" request
for each turn (-n). Skipped where there's no Claude Code to run."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, shutil, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "bin"))
import sbclaude  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:900])


CLAUDE = sbclaude.find() if hasattr(sbclaude, "find") else shutil.which("claude")
if not CLAUDE or not os.path.exists(CLAUDE):
    print("  no Claude Code here: skipped\n\nALL PASS")
    sys.exit(0)

root = os.path.realpath(tempfile.mkdtemp(prefix="sbcache-"))
home = os.path.join(root, "home")                 # the agent's and Claude Code's own: never the user's ~/.claude
os.makedirs(home)
reqs, lock = [], threading.Lock()


class API(BaseHTTPRequestHandler):
    """The stand-in API: keeps each request, answers "ok" as a stream."""
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        with lock:
            reqs.append(body)
        text = json.dumps((body.get("messages") or [])[-2:])
        reply = "Hi, I'm here." if "[CALL START]" in text else "Sure."
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for name, d in (("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
                         "model": body.get("model"), "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}),
                        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}}),
                        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}),
                        ("message_stop", {"type": "message_stop"})):
            self.wfile.write(("event: %s\ndata: %s\n\n" % (name, json.dumps(d))).encode())

    def do_GET(self):
        self.send_response(404)
        self.end_headers()


s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv_api = HTTPServer(("127.0.0.1", port), API)
threading.Thread(target=srv_api.serve_forever, daemon=True).start()

live = os.path.join(root, "live")
os.makedirs(live)
TALK = "talk-20261025-120000"
os.makedirs(os.path.join(root, "sessions", TALK))
folder = os.path.join(root, "work")
os.makedirs(folder)
json.dump({"session": TALK, "mode": "mic", "kind": "talk", "workspace": folder}, open(os.path.join(live, TALK + ".meta.json"), "w"))
json.dump({"handoff": True, "name": "Claude Code · voice", "folder": folder}, open(os.path.join(live, TALK + ".voice.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(TALK + "\n")
json.dump({"voice": {"ledger": True}}, open(os.path.join(root, "config.json"), "w"))
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=CLAUDE, HOME=home, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"),
           ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % port, ANTHROPIC_AUTH_TOKEN="a-stand-in-token")
env.pop("STANDBY_VOICE_LEDGER", None)
env.pop("ANTHROPIC_API_KEY", None)
os.environ["STANDBY_ROOT"] = root
import sbledger  # noqa: E402
sbledger.ROOT = root
from agentpath import AGENT as agent  # noqa: E402


def ask(q):
    p = subprocess.run([sys.executable, agent, "q", "--json", "[VOICE] " + q], env=env, capture_output=True, text=True, timeout=90)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return lines[-1].get("text") if lines else p.stderr[-300:]


def said(q, heard, cut=False):
    sbledger.append(TALK, "asked", q)
    sbledger.append(TALK, "you", heard, cut=cut, unsaid=not heard)


def turn_reqs():
    with lock:
        return [r for r in reqs if not any("naming a coding session" in (b.get("text") or "") for b in r.get("system") or [])
                and r.get("tools")]


def bare(x):
    """A request's messages without the cache marks: what decides whether an entry is reused. A message's
    content as a string is the API's shorthand for one text block (Claude Code sends the first turn's
    environment as a block with its mark, and the same text as a string once it's history)."""
    if isinstance(x, dict):
        if isinstance(x.get("content"), str) and "role" in x:
            x = dict(x, content=[{"type": "text", "text": x["content"]}])
        return {k: bare(v) for k, v in x.items() if k != "cache_control"}
    if isinstance(x, list):
        return [bare(v) for v in x]
    return x


def extends(a, b):
    """b's request begins with a's whole request (its system, tools, and every message of a, as sent)."""
    return (bare(a.get("system")) == bare(b.get("system")) and bare(a.get("tools")) == bare(b.get("tools"))
            and bare(b["messages"][:len(a["messages"])]) == bare(a["messages"]))


srv = subprocess.Popen([sys.executable, agent, "--model", "opus", "--voice-model", "haiku", "serve"], env=env,
                       stderr=open(os.path.join(root, "serve.log"), "w"))
try:
    hello = ask("[CALL START]")
    said("[CALL START]", hello)
    r1 = turn_reqs()[-1]
    a2 = ask("What's the score?")
    said("What's the score?", a2)
    r2 = turn_reqs()[-1]
    early = ask("Tell me a joke")                   # an early ask they went on from: never committed
    r3 = turn_reqs()[-1]
    a4 = ask("Tell me a joke and then stop")
    said("Tell me a joke and then stop", a4)
    r4 = turn_reqs()[-1]
    a5 = ask("Thanks")
    r5 = turn_reqs()[-1]
    check("a call's turns answered by the real Claude Code against the stand-in", hello == "Hi, I'm here." and a5 == "Sure.",
          (hello, a2, early, a4, a5, open(os.path.join(root, "serve.log")).read()[-800:]))
    check("each committed turn's request begins with the last committed turn's, exactly: its cache entry is read",
          extends(r1, r2) and extends(r2, r4) and extends(r4, r5),
          [len(r["messages"]) for r in (r1, r2, r4, r5)])
    check("  an early ask's fork too, from the conversation as it stood", extends(r2, r3))
    check("  and the committed turn after it never carries the early one's question or answer",
          not extends(r3, r4) and "Tell me a joke\"" not in json.dumps(r4["messages"]), json.dumps(r4["messages"])[-600:])
    check("  the cache mark is on each request's last block", all(
          "cache_control" in json.dumps(r["messages"][-1]) for r in (r1, r2, r3, r4, r5)))
    with lock:
        titles = [r for r in reqs if any("naming a coding session" in (b.get("text") or "") for b in r.get("system") or [])]
    check("no \"name this session\" request for any turn (each is named)", not titles, len(titles))
finally:
    srv.terminate()
    srv.wait(10)
    srv_api.shutdown()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
