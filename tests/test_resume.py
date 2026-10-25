"""Resuming an ended session: same id, timestamps continue after the last line,
earlier audio is not overwritten, and nothing already shipped is resent."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, re, sys, tempfile, threading, time, types
from pathlib import Path
import numpy as np
from importlib.machinery import SourceFileLoader

class FakeInput(object):
    def __init__(self, callback=None, **kw): self.cb = callback; self.run = False
    def start(self):
        self.run = True
        def loop():
            while self.run:
                self.cb(np.full((8000, 1), 0.1, "float32"), 8000, None, None); time.sleep(0.05)
        threading.Thread(target=loop, daemon=True).start()
    def stop(self): self.run = False
    def close(self): pass
DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(
    InputStream=FakeInput, query_devices=lambda i=None: DEV if i is None else DEV[i],
    default=types.SimpleNamespace(device=[0, 0]))
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.log = lambda m: None
home = Path(tempfile.mkdtemp())
sb.ROOT, sb.SESSIONS, sb.CONFIG = home, home / "sessions", home / "config.json"
sb.CONFIG.write_text(json.dumps({"mic_device": "MacBook Pro Microphone", "host": "x",
                                 "remote_dir": "~/.standby", "mode": "mic"}))
sb.load_model = lambda name: None

class OneLine(object):
    """Emits one line per run, at t=2.0 relative to the run."""
    def __init__(self, *a, **k): self.done = False
    def feed(self, b): pass
    def drain(self, force=False):
        if not self.done:
            self.done = True
            return [(2.0, 3.0, "line %d" % OneLine.n)]
        return []
    def rotate(self): return []
    def _close(self): pass
sb.UtteranceTranscriber = OneLine

offsets = []
class FakeShipper(object):
    def __init__(self, cfg, session, local, **kw): self.session = session; self.offset = 0; self.connected = True
    def announce(self, meta): return True
    def start(self): offsets.append(self.offset)
    def join(self, timeout=None): pass
    def ship_log(self): pass
    def finish(self, err=None): pass
sb.Shipper = FakeShipper

def run(**kw):
    sb._stop.clear()
    threading.Timer(0.8, sb._stop.set).start()
    return sb.run_session("meet", keep_audio=True, **kw)

fails = []
def check(label, ok, d=""):
    print("  %-56s %s" % (label, "OK" if ok else "FAIL")); ok or (fails.append(label), print("     " + str(d)))

OneLine.n = 1; sid = run()
rows = [json.loads(l) for l in (sb.SESSIONS / sid / "transcript.jsonl").read_text().splitlines()]
size1 = (sb.SESSIONS / sid / "transcript.jsonl").stat().st_size
OneLine.n = 2; sid2 = run(resume=sid)
rows = [json.loads(l) for l in (sb.SESSIONS / sid / "transcript.jsonl").read_text().splitlines()]
check("resume keeps the same session id", sid2 == sid, (sid, sid2))
check("both runs' lines in one transcript", [r["text"] for r in rows] == ["line 1", "line 2"], rows)
check("timestamps continue after the first run", rows[1]["t"] > rows[0]["end"], rows)
wavs = sorted(p.name for p in (sb.SESSIONS / sid).glob("*.wav"))
check("earlier audio kept, new audio in a new file", wavs == ["room.2.wav", "room.wav"], wavs)
offs = {n: float((sb.SESSIONS / sid / (n + ".offset")).read_text()) for n in wavs}
check("each audio file records its start on the session clock",
      offs["room.wav"] == 0 and abs(offs["room.2.wav"] - (rows[0]["end"] + 1.0)) < 0.01, offs)
check("shipper starts after what was already shipped", offsets == [0, size1], (offsets, size1))
meta = json.loads((sb.SESSIONS / sid / "meta.json").read_text())
check("meta keeps the original label", meta["label"] == "meet", meta)
check("meta carries push_lines (default 10)", meta.get("push_lines") == 10, meta)
check("ids go to the second", re.match(r"^meet-\d{8}-\d{6}$", sid), sid)
now = time.time()
a, b, c = (sb.new_session_id("talk", now=now) for _ in range(3))
check("two sessions in the same second get their own folders",
      a != b != c and b == a + "-2" and c == a + "-3" and all((sb.SESSIONS / x).is_dir() for x in (a, b, c)), (a, b, c))
OneLine.n = 3; s3 = run()
OneLine.n = 4; s4 = run()
r3 = [json.loads(l)["text"] for l in (sb.SESSIONS / s3 / "transcript.jsonl").read_text().splitlines()]
r4 = [json.loads(l)["text"] for l in (sb.SESSIONS / s4 / "transcript.jsonl").read_text().splitlines()]
check("back-to-back sessions never share a transcript", s3 != s4 and r3 == ["line 3"] and r4 == ["line 4"], (s3, s4, r3, r4))
old_id = "meeting-20260928-1000"                    # an id from before seconds
(sb.SESSIONS / old_id).mkdir()
(sb.SESSIONS / old_id / "meta.json").write_text(json.dumps({"label": "meeting", "started": now - 86400, "mode": "mic"}))
(sb.SESSIONS / old_id / "transcript.jsonl").write_text(json.dumps({"t": 0, "end": 4, "text": "old line"}) + "\n")
OneLine.n = 5; resumed = run(resume=old_id)
rows = [json.loads(l)["text"] for l in (sb.SESSIONS / old_id / "transcript.jsonl").read_text().splitlines()]
check("an old-style id still resumes", resumed == old_id and rows == ["old line", "line 5"], (resumed, rows))
sys.path.insert(0, "mac")
os.environ["STANDBY_ROOT"] = str(home)
ui = SourceFileLoader("ui_for_ids", "mac/ui.py").load_module()
ids = [x["id"] for x in ui.list_sessions()]
check("the app lists old and new ids alike, newest first",
      ids[-1] == old_id and ids.index(s4) < ids.index(s3) and all(ui.valid_id(x) for x in (old_id, b, s4)), ids)
# the folder a resume runs in. Claude Code finds a conversation only from the
# folder it is kept under, so a session started with no folder keeps none. A folder
# an older Settings saved as the default is never used: sessions come with their own.
import subprocess  # noqa: E402
OneLine.n = 6; plain = run()
cfg = json.loads(sb.CONFIG.read_text())
default = os.path.realpath(tempfile.mkdtemp(prefix="sbdefault-"))
sb.CONFIG.write_text(json.dumps(dict(cfg, workspace=default)))
OneLine.n = 7; again = run(resume=plain)
meta = json.loads((sb.SESSIONS / plain / "meta.json").read_text())
check("resumed with an old default folder saved: keeps its own (none)", again == plain and not meta.get("workspace"), meta)
OneLine.n = 8; fresh = run()
check("  a new session doesn't take it either", not json.loads((sb.SESSIONS / fresh / "meta.json").read_text()).get("workspace"))

started, real_run, real_settings = [], ui.subprocess.run, ui.get_settings
class Stop(Exception):
    pass
def fake_run(cmd, **kw):
    if not (isinstance(cmd, list) and len(cmd) > 1 and cmd[1] == "start"):
        return real_run(cmd, **kw)          # the app's own lookups (session_info asks too)
    started.append(cmd)
    raise Stop()
ui.subprocess.run, ui.wait_for_shutdown = fake_run, lambda: True
ui.get_settings = lambda: dict(real_settings(), workspace=default)
try:
    ui._do_start(plain)
    check("  the app resumes it with no folder, not today's default",
          started and "--resume" in started[-1] and "--workspace" not in started[-1], started[-1:])
    ui._do_start(None)
    check("  and starts a new one with no folder of its own", "--workspace" not in started[-1], started[-1:])
finally:
    ui.subprocess.run, ui.get_settings = real_run, real_settings

# The agent: whatever the session's record says now, an existing conversation is
# resumed in the folder its transcript was kept under.
aroot = Path(tempfile.mkdtemp(prefix="sbagent-"))
fake_home = Path(tempfile.mkdtemp(prefix="sbhome-"))
for d in ("live", "agent"):
    (aroot / d).mkdir()
agent_dir = os.path.realpath(str(aroot / "agent"))
slot = lambda path: re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(path))
def conversation(session, said_in, cwds, workspace=default):
    cid = "c0ffee00-0000-4000-8000-%012d" % (abs(hash(session)) % 10 ** 12)
    (aroot / "live" / (session + ".meta.json")).write_text(json.dumps({"session": session, "workspace": workspace}))
    (aroot / "agent" / (session + ".uuid")).write_text(cid + "\n")
    if said_in:
        proj = fake_home / ".claude" / "projects" / slot(said_in)
        proj.mkdir(parents=True, exist_ok=True)
        (proj / (cid + ".jsonl")).write_text("".join(json.dumps({"type": "user", "sessionId": cid, "cwd": c}) + "\n" for c in cwds))
def resumes_in(session):
    r = subprocess.run([sys.executable, agentpath.AGENT, "resume-cmd", "--session", session], capture_output=True, text=True,
                       timeout=30, env=dict(os.environ, STANDBY_ROOT=str(aroot), HOME=str(fake_home)))
    try:
        return os.path.realpath(json.loads(r.stdout)["cwd"])     # /var and /private/var are one folder
    except (ValueError, KeyError):
        return r.stdout + r.stderr
conversation("ran-in-agent", agent_dir, [agent_dir])
check("  the agent: one that ran in Standby's own folder resumes there, not in the default",
      resumes_in("ran-in-agent") == agent_dir, resumes_in("ran-in-agent"))
elsewhere = os.path.realpath(tempfile.mkdtemp(prefix="sbproj-"))
conversation("ran-elsewhere", elsewhere, [elsewhere])
check("  one that ran in a project folder resumes there", resumes_in("ran-elsewhere") == elsewhere, resumes_in("ran-elsewhere"))
moved = os.path.realpath(tempfile.mkdtemp(prefix="sbmoved-"))
conversation("moved-with-cd", moved, [elsewhere, elsewhere, moved])
check("  one moved with /cd (its old folder named first): the folder it was moved to",
      resumes_in("moved-with-cd") == moved, resumes_in("moved-with-cd"))
conversation("nothing-said", None, [])
check("  nothing said yet: the session's own folder", resumes_in("nothing-said") == default, resumes_in("nothing-said"))
gone = tempfile.mkdtemp(prefix="sbgone-")
conversation("folder-gone", gone, [gone], workspace=None)
os.rmdir(gone)
check("  its folder gone: Standby's own folder", resumes_in("folder-gone") == agent_dir, resumes_in("folder-gone"))

try:
    run(resume="does-not-exist"); check("unknown session refused", False)
except SystemExit as e:
    check("unknown session refused", "no such session" in str(e), e)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
