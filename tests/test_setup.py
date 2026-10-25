"""`standby setup` as Settings > Recording does it.

It offered mic / both (BlackHole) / system, and pinned a mic by name, defaulting
to the MacBook's: that pin is how the user's config came to record the MacBook
mic with AirPods on. Now Automatic is the default and the mic is Automatic unless
one is picked; BlackHole is gone, and a Mac set up with it is set up afresh. Answers typed into a fake input(); fakes only."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import builtins, io, json, os, sys, tempfile, types
from contextlib import redirect_stdout
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbsetup-"))
os.environ["STANDBY_ROOT"] = str(root)
agentpath.guard(str(root))
sys.path.insert(0, "mac")
DEV = [{"name": "MacBook Pro Microphone", "max_input_channels": 1},
       {"name": "AirPods Pro", "max_input_channels": 1}, {"name": "BlackHole 2ch", "max_input_channels": 2}]
sys.modules["sounddevice"] = types.SimpleNamespace(query_devices=lambda i=None: DEV if i is None else DEV[i])
from importlib.machinery import SourceFileLoader  # noqa: E402
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
sb.ROOT, sb.CONFIG = root, root / "config.json"
fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


def setup(answers, existing=None, mode=None):
    if existing is None:
        sb.CONFIG.unlink() if sb.CONFIG.exists() else None
    else:
        sb.CONFIG.write_text(json.dumps(existing))
    feed = iter(answers)
    real, builtins.input = builtins.input, lambda prompt="": next(feed)
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            sb.cmd_setup(types.SimpleNamespace(mode=mode))
    finally:
        builtins.input = real
    return json.loads(sb.CONFIG.read_text()), out.getvalue()


cfg, said = setup(["", "", ""])            # Enter, Enter, Enter
check("a fresh setup, Enter all the way: Automatic, and the mic Automatic (not pinned)",
      cfg["mode"] == "auto" and "mic_device" not in cfg and cfg["agent"]["mode"] == "local", cfg)
check("  the choices are Settings' own: Automatic, Mic only, Meeting audio only",
      "Automatic" in said and "Mic only" in said and "Meeting audio only" in said, said[:600])
check("  and no BlackHole for a Mac that never had it", "BlackHole" not in said, said[:600])
check("  what is chosen here is the user's: never migrated later",
      cfg.get("capture_automatic") is True and cfg.get("mic_automatic") is True, cfg)
cfg, _ = setup(["2", "1", ""])
check("Mic only, picking the AirPods: pinned, by name", cfg["mode"] == "mic" and cfg["mic_device"] == "AirPods Pro", cfg)
cfg, _ = setup(["3", ""])
check("Meeting audio only: no mic asked for", cfg["mode"] == "meeting" and "mic_device" not in cfg, cfg)
cfg, said = setup(["4", "", ""], existing={"mode": "both", "loopback_device": "BlackHole 2ch"})
check("a Mac set up with BlackHole: not offered it, typing 4 is Automatic, the device forgotten",
      "BlackHole" not in said and cfg["mode"] == "auto" and "loopback_device" not in cfg, cfg)
cfg, _ = setup(["", ""], mode="auto")
check("setup --mode auto (standby try): no question about what to record", cfg["mode"] == "auto", cfg)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
