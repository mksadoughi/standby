"""Can Claude be reached, on the Mac: one answer for the session, the
sidebar and the companion. The agent's record (live/claude-error.json) and
what this Mac saw itself (no answer at all from where Claude runs), the newest
first; gone with the next call that works. The
detail for Details: ASCII, its last lines only. And voice mode: an error is
said in one plain sentence, and a long wait says Claude is still on its way.
No agent, no Claude: files and stand-ins."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, threading, time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import voice  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-66s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:500])


def wait(pred, secs=30):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


root = Path(os.environ["STANDBY_ROOT"])
(root / "live").mkdir(parents=True, exist_ok=True)
record = root / "live" / "claude-error.json"
check("nothing wrong: no error, anywhere", ui.claude_error() is None and ui.companion_state()["claude_error"] is None)

record.write_text(json.dumps({"kind": "signin", "text": "Claude can't sign in.", "fix": "Your Google Cloud credentials need refreshing, then try again.",
                              "detail": "API Error: Could not load the default credentials · from gcloud",
                              "at": time.time() - 5, "session": "m1", "turn": "voice"}, ensure_ascii=False))
e = ui.claude_error()
check("the agent's record: what, what to do, when", e and e["kind"] == "signin" and e["text"] == "Claude can't sign in."
      and e["fix"] == "Your Google Cloud credentials need refreshing, then try again." and e["at"], e)
check("  its detail in ASCII, for Details", e and e["detail"] == "API Error: Could not load the default credentials \\xb7 from gcloud",
      e and e["detail"])
check("  the companion has the same", ui.companion_state()["claude_error"] == e)
record.write_text(json.dumps({"kind": "engine", "text": "x", "fix": "y", "at": time.time(),
                              "detail": "\n".join("line %d" % i for i in range(40))}))
d = ui.claude_error()["detail"].splitlines()
check("  a long one: its last 15 lines only, never a wall", d == ["line %d" % i for i in range(25, 40)], d)

ui.after_ask("Agent not available: ssh: connect to host devbox port 22: Operation timed out")
e = ui.claude_error()
check("no answer at all from where Claude runs: can't reach it, the newest wins",
      e["kind"] == "unreachable" and e["text"].startswith("Can't reach Claude Code") and "try again" in e["fix"], e)
ui.after_ask("(stopped)")
check("  a stopped question says nothing either way", ui.claude_error()["kind"] == "unreachable")
ui.after_ask("The cache moved to Orion.")
check("  an answer that worked: this Mac's is gone (the agent removed its own with that turn)",
      ui.claude_error()["kind"] == "engine")
record.unlink()
check("  and with the agent's gone too, all is well", ui.claude_error() is None)
ui.after_ask("Could not reach the agent (OSError(61, 'Connection refused')). Check Settings > Claude Code.")
check("the app failing to reach the agent counts too", (ui.claude_error() or {}).get("kind") == "unreachable")
ui._errs().update(mac=None, agent=None)
check("  and with it gone, all is well again", ui.claude_error() is None)

# Voice mode: an error said plainly; a long wait shown as one.
said = voice.spoken_error("engine error: API Error: Could not load the default credentials")
check("voice: an error is one plain sentence, the error itself below the --- line",
      said.startswith("Sorry, I couldn't get an answer from Claude. Ask me again?\n\n---\nengine error"), said)
check("  an answer is said as it is", voice.spoken_error("The cache moved.") == "The cache moved.")
check("  so is the agent's own sign-in line, which already says what to do",
      voice.spoken_error("I can't reach Claude: it can't sign in. Your Google Cloud credentials need refreshing, then ask again.\n\n---\nx").startswith("I can't"))


class Quiet(object):
    voice, current, started_at, speaking = None, "", None, False
    def __init__(self): self.said = []
    def say(self, t): self.said.append(t)
    def stop(self): pass
    @property
    def busy(self): return False


sdir = root / "sessions" / "v1"
sdir.mkdir(parents=True)
(sdir / "live.json").write_text(json.dumps({"at": time.time()}))
release = threading.Event()


def slow_ask(q, on_event, on_queued):
    on_queued("q1")
    release.wait(30)
    return "Here it is."


keep = threading.Event()


def beat():                                   # the recorder, alive meanwhile
    while not keep.is_set():
        (sdir / "live.json").write_text(json.dumps({"at": time.time()}))
        time.sleep(0.2)


threading.Thread(target=beat, daemon=True).start()
vs = voice.VoiceSession("v1", sdir, ask=slow_ask, cancel=lambda q: None, speaker=Quiet())
vs.SLOW_ANSWER = 0.4
vs.start()
with open(sdir / "dictation.jsonl", "a") as fh:
    fh.write(json.dumps({"seq": vs.seq, "end_turn": True, "text": "anyone there?"}) + "\n")
check("voice: thinking, then, with no word for a while, it says Claude is still on its way",
      wait(lambda: vs.status()["state"] == "thinking") and wait(lambda: vs.status()["step"] == "Still waiting for Claude"),
      vs.status())
release.set()
check("  and the answer, when it comes, is said", wait(lambda: vs.speaker.said == ["Here it is."]), vs.speaker.said)
vs.stop()
keep.set()

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
