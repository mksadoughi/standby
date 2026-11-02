"""A call's voice is compacted ahead of need, at a quiet moment, never while a question waits.

Claude Code compacts a conversation on its own only when a turn needs the room, and that turn waits for it: a
call's voice once went silent 35 s mid-call (talk-20261101-192602). So past VOICE_RENEW_AT tokens, once nothing
has been asked for VOICE_RENEW_QUIET, the agent sends the voice /compact; a question that comes meanwhile stops
it at once and is answered as ever. A stand-in Claude Code speaks the stream protocol (as test_voice_agent's),
reports the conversation's size as told, and takes /compact quickly or slowly."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, time

root = tempfile.mkdtemp(prefix="sbrenew-")
stub = os.path.join(root, "fake-claude")
seen = os.path.join(root, "seen.jsonl")
ctx = os.path.join(root, "ctx")                 # the size the stand-in reports for its conversation
slow_compact = os.path.join(root, "slow-compact")
with open(stub, "w") as fh:
    fh.write('''#!/usr/bin/env python3
import json, os, select, sys, time
seen, ctx, slow_compact = %r, %r, %r
if "--help" in sys.argv:
    print('--permission-mode <mode>  (choices: "acceptEdits", "auto", "dontAsk", "plan")\\n--permission-prompts <x>')
    sys.exit(0)
kind = "voice" if "--strict-mcp-config" in sys.argv else "meeting"
def emit(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
emit({"type": "system", "subtype": "init"})
buf = b""
def lines(timeout):
    global buf
    end = time.time() + timeout
    while time.time() < end:
        while b"\\n" in buf:
            l, buf = buf.split(b"\\n", 1)
            if l.strip(): yield json.loads(l)
        r, _, _ = select.select([sys.stdin], [], [], 0.05)
        if r:
            c = os.read(sys.stdin.fileno(), 65536)
            if not c: return
            buf += c
def size():
    try: return int(open(ctx).read())
    except (OSError, ValueError): return 1000
while True:
    msg = next(lines(3600), None)
    if msg is None: break
    if msg.get("type") != "user": continue
    text = msg["message"]["content"][0]["text"]
    open(seen, "a").write(json.dumps({"kind": kind, "text": text, "t": time.time()}) + "\\n")
    if text == "/compact":
        stopped = False
        for m in lines(8 if os.path.exists(slow_compact) else 0.3):
            if m.get("type") == "control_request":
                emit({"type": "control_response", "response": {"subtype": "success"}})
                emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
                stopped = True
                break
        if not stopped:
            emit({"type": "system", "subtype": "compact_boundary"})
            emit({"type": "result", "is_error": False, "result": "", "usage": {}})
        continue
    emit({"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": "Sure. "}}})
    emit({"type": "result", "is_error": False, "result": "Sure.",
          "usage": {"input_tokens": 4, "cache_read_input_tokens": size() - 4, "cache_creation_input_tokens": 0}})
''' % (seen, ctx, slow_compact))
os.chmod(stub, 0o755)
live = os.path.join(root, "live")
os.makedirs(live)
S = "talk-20990101-120000"
json.dump({"session": S, "kind": "talk", "mode": "mic"}, open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
env = dict(os.environ, STANDBY_ROOT=root, STANDBY_CLAUDE=stub, STANDBY_VOICE_LEDGER="0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agentpath import AGENT as agent  # noqa: E402
PY = "/usr/bin/python3"
log = os.path.join(root, "serve.log")
srv = subprocess.Popen([PY, agent, "--model", "opus", "--voice-model", "sonnet", "serve"], env=env, stderr=open(log, "w"))
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def voice_texts():
    out = []
    try:
        for line in open(seen):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "voice":
                out.append(r["text"])
    except IOError:
        pass
    return out


def compacts():
    return voice_texts().count("/compact")


def wait(pred, secs=10):
    end = time.time() + secs * 4          # a loaded Mac takes several times as long: returns as soon as it holds
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def ask(question):
    t0 = time.time()
    p = subprocess.run([PY, agent, "q", "--json", "[VOICE] " + question], env=env, capture_output=True, text=True)
    evs = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return evs[-1:] if evs else [], time.time() - t0


def logged(text):
    try:
        return text in open(log).read()
    except IOError:
        return False


try:
    open(ctx, "w").write("1000")
    last, _ = ask("hello there")
    check("a small conversation: answered as ever", last == [{"type": "answer", "text": "Sure."}], last)
    time.sleep(3)                          # on purpose: a renew would have gone in this time
    check("  and never compacted", compacts() == 0, voice_texts())

    open(ctx, "w").write("60000")
    last, _ = ask("tell me about the lamp")
    check("past the mark, once nothing is asked for a moment: the voice is compacted",
          last == [{"type": "answer", "text": "Sure."}] and wait(lambda: compacts() == 1 and logged("compacted in"), 5),
          (voice_texts(), open(log).read()[-600:]))
    time.sleep(3)
    check("  once: its conversation is small again", compacts() == 1, voice_texts())

    open(slow_compact, "w").write("1")
    last, _ = ask("and the keeper")
    check("past the mark again, it starts compacting at the next quiet moment", wait(lambda: compacts() == 2, 5),
          voice_texts())
    open(ctx, "w").write("1000")
    last, took = ask("quick, what time is it")
    check("  a question meanwhile stops it at once, and is answered as ever",
          last == [{"type": "answer", "text": "Sure."}] and took < 3 and wait(lambda: logged("stopped for a question"), 3),
          (last, took, open(log).read()[-600:]))
    time.sleep(3)
    check("  the conversation is small now (as its last turn said): not tried again", compacts() == 2, voice_texts())
finally:
    srv.terminate()
    try:
        srv.wait(5)
    except subprocess.TimeoutExpired:
        srv.kill()

print()
if fails:
    print("FAILURES: %s" % fails)
    print(open(log).read()[-3000:])
    sys.exit(1)
print("ALL PASS")
