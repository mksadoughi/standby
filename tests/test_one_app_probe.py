"""The grant probe (tools/one_app_probe.py), off the screen: the test app is built
the way install.sh builds Standby, under its own bundle id, into a throwaway folder
(never ~/Applications); its LaunchAgent is written, never loaded (launchctl, pkill and
tccutil are fakes here); the report reads who started each part; and the app's own
code runs headless, with no delegate, no Dock icon and no window."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, no real devices)
import json, os, plistlib, sys, tempfile, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbgrant-"))
os.environ.update(GRANTTEST_REPORTS=str(root / "reports"), STANDBY_LAUNCH_AGENTS=str(root / "agents"))
(root / "reports").mkdir()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
import one_app_probe as probe  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-72s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


ran = []


class Done(object):
    returncode = 0


def fake_run(cmd, **kw):
    ran.append(list(cmd))
    return Done()


check("its own bundle id, never Standby's or Standby Dictation's",
      probe.BUNDLE_ID == "dev.standby.granttest" and probe.LABEL == probe.BUNDLE_ID)
check("its app lands in ~/Applications by default, under its own name",
      probe.app_path() == Path.home() / "Applications" / "Standby Grant Test.app")

# ---------------------------------------------------------------- built as install.sh builds Standby
apps = root / "apps"
(apps / "Standby.app").mkdir(parents=True)                       # a neighbour it must not touch
(apps / "Standby.app" / "keep").write_text("mine")
try:
    import py2app  # noqa: F401
    have_py2app = True
except ImportError:
    have_py2app = False
if have_py2app:
    path, before, first = probe.build(apps)
    info = plistlib.loads((path / "Contents" / "Info.plist").read_bytes())
    check("built: Standby Grant Test.app, py2app alias mode, its own id",
          path == apps / "Standby Grant Test.app" and info["CFBundleIdentifier"] == probe.BUNDLE_ID
          and info["CFBundleName"] == probe.NAME and probe.executable(apps).exists(), info)
    check("  no LSUIElement: one bundle with a Dock part, as Standby.app", "LSUIElement" not in info, info)
    check("  its microphone text", info["NSMicrophoneUsageDescription"] == probe.MIC_TEXT)
    check("  its neighbour untouched", (apps / "Standby.app" / "keep").read_text() == "mine")
    boot = "\n".join(p.read_text(errors="replace") for p in path.rglob("__boot__.py"))
    entry = probe.build_dir(apps) / "GrantTest.py"
    check("  it runs this checkout's probe, in place, from a folder that stays",
          str(entry) in boot and str(HERE.parent / "tools") in entry.read_text(), boot[-300:])
    _, again, second = probe.build(apps)
    check("rebuilt with nothing changed: the same mark (its grants kept)", again == first and second == first,
          (first, second))
    _, _, third = probe.build(apps, change_text=True)
    info = plistlib.loads((path / "Contents" / "Info.plist").read_bytes())
    check("  with one word of its microphone text changed: the text changed",
          info["NSMicrophoneUsageDescription"] == probe.MIC_TEXT_CHANGED, (first, third))
    print("     (marks: %s, then %s: whether macOS asks again is step 7's to show)" % (first, third))
else:
    print("  (py2app not in this Python: the build is not tried)")

# ---------------------------------------------------------------- its LaunchAgent, its grants: fakes only
ok = probe.agent(True, apps, run=fake_run)
plist = plistlib.loads((root / "agents" / "dev.standby.granttest.plist").read_bytes())
check("the LaunchAgent: the always-on part, started now, never brought back by itself",
      ok and plist["ProgramArguments"] == [str(probe.executable(apps)), "--always"] and plist["RunAtLoad"]
      and "KeepAlive" not in plist and ["launchctl", "bootstrap", "gui/%d" % os.getuid(),
                                        str(root / "agents" / "dev.standby.granttest.plist")] in ran, (plist, ran))
ran.clear()
probe.agent(False, apps, run=fake_run)
check("  off: unloaded, and its file gone", ran == [["launchctl", "bootout", "gui/%d/dev.standby.granttest" % os.getuid()]]
      and not (root / "agents" / "dev.standby.granttest.plist").exists(), ran)
ran.clear()
probe.reset(run=fake_run)
check("reset: the test app's grants, and no other app's", ran == [["tccutil", "reset", "All", "dev.standby.granttest"]], ran)
ran.clear()
(root / "reports" / "standby-granttest-always.json").write_text("{}")
probe.clean(apps, run=fake_run)
check("clean: the agent, the app, its grants and its reports; nothing else",
      not probe.app_path(apps).exists() and not probe.build_dir(apps).exists()
      and (apps / "Standby.app" / "keep").exists()
      and ["tccutil", "reset", "All", "dev.standby.granttest"] in ran
      and not (root / "reports" / "standby-granttest-always.json").exists()
      and all("dev.standby.app" not in " ".join(c) and "dev.standby.dictation" not in " ".join(c) for c in ran), ran)

# ---------------------------------------------------------------- the report
now = time.time()
for part, extra in (("always", {"xpc_service": "dev.standby.granttest"}),
                    ("window", {"xpc_service": "dev.standby.granttest", "parent_part": "always"})):
    (root / "reports" / ("standby-granttest-%s.json" % part)).write_text(json.dumps(dict(
        {"part": part, "pid": 100 + len(part), "ppid": 1, "bundle": "dev.standby.granttest", "at": now,
         "states": {"microphone": "allowed", "input_monitoring": True, "accessibility": False, "screen": None},
         "mic_level": -48.2, "events": [{"at": now, "what": "reopen"}]}, **extra)))
text = probe.report(now)
check("report: who started each part, in the hands-on's words",
      "always  pid 106 (parent 1), started by launchd (its LaunchAgent)" in text
      and "window  pid 106 (parent 1), started by the always-on part (its child)" in text, text)
check("  what macOS tells it, and what happened", "microphone allowed, input monitoring allowed, accessibility no, "
      "screen ?" in text and "reopen" in text, text)
check("  one from Finder is said so", probe.started_by({"xpc_service": "application.dev.standby.granttest.1.2"})
      == "Finder or the Dock")
check("  a part that stopped is said so", "NOT RUNNING" in probe.report(now + 60))
(root / "reports" / "standby-granttest-window.json").unlink()
check("  and one never started", "window  not seen" in probe.report(now))

# ---------------------------------------------------------------- the app's own code, headless
for part in ("always", "window"):
    r = probe.app_main(["--%s" % part] if part == "always" else [], headless=True)
    got = json.loads((root / "reports" / ("standby-granttest-%s.json" % part)).read_text())
    check("headless %s: a report, each permission as macOS says it, nothing measured" % part,
          got["part"] == part and got["pid"] == os.getpid() and set(got["states"]) ==
          {"microphone", "input_monitoring", "accessibility", "screen"} and got["mic_level"] in (None, "not measured (tests)")
          and r["part"] == part, got)
check("the steps: eight, in one sitting", probe.STEPS.count("\n 8.") == 1 and "clean" in probe.STEPS)

# ---------------------------------------------------------------- the sitting, guided (fakes: nothing built or started)
did = []
probe.build = lambda into=None: did.append("build")
probe.agent = lambda on, into=None: did.append("agent on" if on else "agent off")
probe.clean = lambda into=None: did.append("clean")
probe.report = lambda: "always  pid 1 ... microphone allowed\nwindow  pid 2 ... microphone allowed"
probe._wait_for = lambda pred, most=20.0, step=0.5: True
said, replies = [], iter(["", "n", "1", "y"])
block = probe.sitting(ask=lambda q: (said.append(q), next(replies))[1], say=said.append)
check("the sitting: built, started, four steps asked, removed at the end, one block to paste",
      did == ["build", "agent on", "clean"] and block[0] == "---- GRANT CHECK ----" and block[-1] == "---- end ----"
      and "second microphone prompt: n" in block and "Dock icons: 1" in block
      and "menu bar item stayed after Quit: y" in block, (did, block))
check("  its words name the app macOS will name, and what to click",
      any('"Standby Grant Test" would like to access the microphone' in x for x in said)
      and any("1. Ask for the microphone" in x for x in said), said[:6])
did.clear()
replies = iter([""])
def stop_early(q):
    if not replies.__length_hint__():
        raise KeyboardInterrupt
    return next(replies)
try:
    probe.sitting(ask=stop_early, say=lambda x: None)
except KeyboardInterrupt:
    pass
check("  stopped half way (Ctrl-C): everything is still removed", did[-1:] == ["clean"], did)

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
