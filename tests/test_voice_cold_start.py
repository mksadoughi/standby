"""A call's greeting while Standby's voice is still starting (a cold start, about 2 s): the app's
voice session (voice.py) feeds its sentences through RecorderSpeaker to the recorder's VoiceIO
(standby.py), whose engine holds them until the voice is ready. All of the greeting is said, in
order, once it is: none of it dropped by the wait, nor the turn handed back meanwhile (REOPEN).
No audio: the engine is a fake whose voice "loads" for COLD s, then plays each sentence a while."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, sys, tempfile, threading, time, types
from pathlib import Path
from importlib.machinery import SourceFileLoader

root = Path(tempfile.mkdtemp(prefix="sbcold-"))
os.environ["STANDBY_ROOT"] = str(root)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
sys.modules.setdefault("sounddevice", types.SimpleNamespace(
    InputStream=None, query_devices=lambda i=None: [], default=types.SimpleNamespace(device=[0, 0])))
sb = SourceFileLoader("sb", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac", "standby.py")).load_module()
import voice  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:600])


def wait(pred, secs=10.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


class Engine(object):
    """The voice's engine (duplex.AVFBackend's interface). Its voice loads for COLD s from the
    start: a sentence given meanwhile waits for it (playing, and loading), then plays SAY s."""
    COLD, SAY = 2.5, 0.3

    def __init__(self):
        self.ready_at = time.time() + self.COLD
        self.heard, self._until, self.lock = [], 0.0, threading.Lock()

    def start_output(self):
        pass

    def stop_mic(self):
        pass

    def begin(self):
        pass

    @property
    def loading(self):
        return time.time() < self.ready_at

    def say(self, text):
        with self.lock:
            start = max(time.time(), self.ready_at)
            self.heard.append((start, text))
            self._until = start + self.SAY

    def halt(self):
        with self.lock:
            self._until = 0.0

    @property
    def playing(self):
        return time.time() < self._until


sdir = root / "sessions" / "talk-1"
sdir.mkdir(parents=True)
(sdir / "dictation.jsonl").write_text("")
eng = Engine()
vio = sb.VoiceIO(sdir, eng, log=lambda m: None, leaks=lambda: True, talk_over=False)
on = {"rec": True}


def recorder():
    """The recorder's loop, as far as the voice goes: its status in live.json."""
    while on["rec"]:
        st = {"at": time.time(), "level": 0.0, "partial": "", "voice": vio.status()}
        tmp = sdir / "live.json.tmp"
        tmp.write_text(json.dumps(st))
        tmp.replace(sdir / "live.json")
        time.sleep(0.03)


GREETING = ["Hi, we're connected.", "Two things are open from earlier.",
            "First, do you want mock three to replace the real page?", "Second, the film's caption."]


def ask(question, on_event, on_queued, heard=None):
    """A quick voice: the greeting's words as they are written, over about a second."""
    on_queued("q1")
    for s in GREETING:
        for word in s.split(" "):
            on_event({"kind": "delta", "text": word + " "})
            time.sleep(0.03)
    return " ".join(GREETING)


logged = []
vs = voice.VoiceSession("talk-1", sdir, ask=ask, cancel=lambda *a, **k: None, speaker=voice.RecorderSpeaker(sdir),
                        log=logged.append)
vs.GUARD = 0.05
vs.start()
vio.start(vs.seq, [])
threading.Thread(target=recorder, daemon=True).start()
vs.open_with("[CALL START]")

check("the greeting, while the voice still starts: all of it said, in order, once it is ready",
      wait(lambda: [t for _at, t in eng.heard] == GREETING, 15), [t for _at, t in eng.heard])
check("  its first word only once the voice is ready, and nothing before",
      eng.heard and eng.heard[0][0] >= eng.ready_at - 0.01, (eng.heard[:1], eng.ready_at))
check("  nothing left unsaid, nor the turn handed back mid-greeting",
      not any("sentence(s) left" in m or "interrupted" in m or "more to the question" in m for m in logged), logged[-5:])
check("  then listening", wait(lambda: vs.status()["state"] == "listening", 5), vs.status())

on["rec"] = False
vs.stop()
vio.stop()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
