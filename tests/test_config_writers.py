"""config.json: every writer keeps what it doesn't know.

The app, the recorder, dictation, `standby setup` and the installer all write the
file; each is run here on a config holding keys it never sets (a debug switch, a
voice setting, a key no Standby has) and must leave them as they were. Then many
writers at once, as separate processes: no write is lost. And a file that isn't
JSON is never written over. A throwaway STANDBY_ROOT; fakes for devices."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import builtins, io, json, os, subprocess, sys, tempfile, threading, types
from contextlib import redirect_stdout
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbconfig-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
MAC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac")
sys.path.insert(0, MAC)
DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1}, {"name": "AirPods Pro", "max_input_channels": 1}]
sys.modules["sounddevice"] = types.SimpleNamespace(query_devices=lambda i=None: DEV if i is None else DEV[i])
import config  # noqa: E402
import transport  # noqa: E402
import dictation  # noqa: E402
import ui  # noqa: E402
from importlib.machinery import SourceFileLoader  # noqa: E402
sb = SourceFileLoader("sb", os.path.join(MAC, "standby.py")).load_module()
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


# What a Mac's config holds that a given writer doesn't set: Settings' and the voice's own keys, a
# debug switch set by hand, and keys no Standby knows (a newer one's, say), top-level and nested.
KEPT = {"debug": {"call_trace": True}, "voice": {"ledger": True}, "lite_call": "live",
        "voice_mode": {"early_asks": "chunk", "duplex": True, "voice": "pocket:robin"},
        "agent": {"mode": "local", "model": "opus", "voice_model": "sonnet", "voice_effort": "medium",
                  "from_a_newer_standby": 1},
        "dictation": {"shortcut": "fn"}, "voice_model_haiku": False,
        "from_a_newer_standby": {"nested": [1, 2, {"deep": "yes"}]}}


def kept_in(cfg, want=KEPT, path=""):
    """The keys of want that cfg lacks or holds otherwise, as dotted paths."""
    lost = []
    for k, v in want.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            lost += kept_in(cfg[k], v, path + k + ".")
        elif cfg.get(k) != v:
            lost.append(path + k)
    return lost


def start(extra=None):
    cfg = json.loads(json.dumps(KEPT))
    cfg.update(extra or {})
    config.CONFIG.write_text(json.dumps(cfg))
    return cfg


def read():
    return json.loads(config.CONFIG.read_text())


def no_temp():
    return [p.name for p in root.iterdir() if p.name.endswith(".tmp")]


# ---------------------------------------------------------------- each writer
start({"mic_device": "Yeti", "mode": "both"})          # an old Mac's: both migrations move it
transport.migrate_config(lambda m: None)
c = read()
check("the app's and the recorder's start migration: every other key kept",
      not kept_in(c) and c.get("mic_automatic") and c.get("capture_automatic") and "mic_device" not in c,
      (kept_in(c), c))

start({"speech_model": "large", "english_only": True})
moved = ui.speech_migrate()
c = read()
check("the app's speech model migration: every other key kept",
      moved and c["speech_model"] != "large" and not kept_in(c), (kept_in(c), c))

start()
r = ui.set_settings({"push_lines": 7, "talk_over": False, "voice_effort": "low"})
c = read()
want = json.loads(json.dumps(KEPT))
want["voice_mode"]["duplex"], want["agent"]["voice_effort"] = False, "low"
check("Settings (and the settings tool): what it changes, and every other key kept",
      r.get("ok") and c["push_lines"] == 7 and not kept_in(c, want), (r.get("error"), kept_in(c, want)))

start()
r = ui.set_settings({"push_lines": "many", "speakers": False})
check("  a value it refuses: nothing saved", "error" in r and read() == KEPT, r)

start()
ui.INCOMING["rq1"] = {"id": "rq1", "kind": "call", "cc": "x", "at": 0, "expires_at": 1e12, "answer": None,
                      "done": threading.Event()}
r = ui.lite_incoming_answer({"id": "rq1", "answer": "always"})
c = read()
check("Always accept on the band: saved, every other key kept",
      r.get("ok") and c.get("auto_accept") is True and not kept_in(c), (r, kept_in(c)))

start()
ui.voices_info = lambda: {"voices": [{"id": "com.apple.voice.Ava"}], "standby": [], "current": {}}
r = ui.voices_action({"id": "com.apple.voice.Ava"})
c = read()
want = json.loads(json.dumps(KEPT))
want["voice_mode"]["voice"] = "com.apple.voice.Ava"
check("Settings > Voice: the voice, every other key kept (voice_mode's own too)",
      r.get("ok") and not kept_in(c, want), (r, kept_in(c, want)))

start()
dictation.save_setting("hold", True)
c = read()
check("dictation's settings: every other key kept (dictation's own too)",
      c["dictation"].get("hold") is True and not kept_in(c), kept_in(c))

start({"host": "devbox", "mic_device": 3})
config.install()
c = read()
check("the installer: Claude Code on this Mac, every other key kept",
      c["agent"]["mode"] == "local" and "host" not in c and "mic_device" not in c and c["mode"] == "auto"
      and not kept_in(c), (kept_in(c), c))
start()
p = subprocess.run([sys.executable, os.path.join(MAC, "config.py"), "install"], capture_output=True, text=True,
                   env=dict(os.environ, STANDBY_ROOT=str(root)))
check("  as install.sh runs it", p.returncode == 0 and str(config.CONFIG) in p.stdout and not kept_in(read()),
      (p.returncode, p.stdout, p.stderr))

start({"mic_device": "Yeti"})
feed = iter(["2", "1"])                  # Mic only, the AirPods
real, builtins.input = builtins.input, lambda prompt="": next(feed)
try:
    with redirect_stdout(io.StringIO()):
        sb.cmd_setup(types.SimpleNamespace(mode=None))
finally:
    builtins.input = real
c = read()
check("standby setup: what it asks, every other key kept",
      c["mode"] == "mic" and c["mic_device"] == "AirPods Pro" and not kept_in(c), (kept_in(c), c))
check("no temp file left by any of them", not no_temp(), no_temp())

# ---------------------------------------------------------------- many at once
# Each process adds its own key, one at a time, while the others do: with a read, a change and a
# write that weren't one locked step, writes made in between would be lost.
start()
WRITER = ("import sys; sys.path.insert(0, %r); import config\n"
          "for i in range(25):\n"
          "    with config.update() as cfg:\n"
          "        cfg['w%%s_%%d' %% (sys.argv[1], i)] = i\n") % MAC
procs = [subprocess.Popen([sys.executable, "-c", WRITER, str(n)], env=dict(os.environ, STANDBY_ROOT=str(root)))
         for n in range(6)]
for t in range(3):                       # and the app's own writers, in this process, meanwhile
    ui.set_settings({"push_lines": 10 + t})
    dictation.save_setting("hold", bool(t % 2))
codes = [p.wait(60) for p in procs]
c = read()
missing = ["w%d_%d" % (n, i) for n in range(6) for i in range(25) if c.get("w%d_%d" % (n, i)) != i]
check("six writers at once, 150 writes, the app's meanwhile: none lost, every other key kept",
      codes == [0] * 6 and not missing and c["push_lines"] == 12 and not kept_in(c),
      (codes, missing[:10], kept_in(c)))
check("  and no temp file left", not no_temp(), no_temp())

# ---------------------------------------------------------------- a file that isn't JSON
bad = '{"debug": {"call_trace": true}, "mode": "auto",'       # cut short, by hand or a crash
config.CONFIG.write_text(bad)
try:
    with config.update() as cfg:
        cfg["x"] = 1
    raised = False
except config.Unreadable:
    raised = True
check("a config.json that isn't JSON: never written over", raised and config.CONFIG.read_text() == bad)
r = ui.set_settings({"push_lines": 5})
check("  Settings says so, and leaves it", "isn't valid JSON" in (r.get("error") or "")
      and config.CONFIG.read_text() == bad, r)
check("  the app starts anyway, and leaves it", ui.speech_migrate() is None and config.CONFIG.read_text() == bad)
p = subprocess.run([sys.executable, os.path.join(MAC, "config.py"), "install"], capture_output=True, text=True,
                   env=dict(os.environ, STANDBY_ROOT=str(root)))
check("  the installer says so, and leaves it", p.returncode == 1 and "isn't valid JSON" in p.stdout
      and config.CONFIG.read_text() == bad, (p.returncode, p.stdout))
config.CONFIG.unlink()
with config.update() as cfg:
    pass
check("no config.json and nothing to change: none written", not config.CONFIG.exists())

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
