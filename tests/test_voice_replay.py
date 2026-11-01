"""On demand only (tests/suites.txt group "ondemand": run_all.sh runs it only when named): the voice's
replay, the gate for a change to its prompt, effort or model. Real turns from the user's own calls
(tools/voice_replay.py build: ~/.standby/replay/cases.json, never the repository), asked again of the
voice as this checkout has it, through a Claude Code with a config of its own, never the user's ~/.claude.

    bash tests/run_all.sh test_voice_replay
    STANDBY_REPLAY_MODEL=... STANDBY_REPLAY_EFFORT=... bash tests/run_all.sh test_voice_replay

First the checks themselves, on the turns as they were (the bare hand-offs fail, the rest pass), then the
live run: every case right."""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import voice_replay as vr  # noqa: E402

fails = []


def check(label, ok, d=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:500])


if not os.path.exists(vr.CASES):
    vr.build()
cases = json.load(open(vr.CASES))
check("cases from the calls, of every kind there is", len(cases) >= 20 and {"deep", "quiet", "answer"} <= {c["kind"] for c in cases},
      [c["kind"] for c in cases])
was = {c["id"]: vr.check(c, c["was"]) for c in cases}
check("  the checks, on the turns as they were: every bare hand-off fails",
      all(was[c["id"]] for c in cases if c["kind"] == "bare"), [c["id"] for c in cases if c["kind"] == "bare" and not was[c["id"]]])
check("  and every other one passes", not [c["id"] for c in cases if c["kind"] != "bare" and was[c["id"]]],
      [(c["id"], was[c["id"]]) for c in cases if c["kind"] != "bare" and was[c["id"]]])
ok, how = vr.signed_in()
if not ok:
    print("  the live run needs its own Claude Code login (%s)" % how)
    print("1 FAILED")
    sys.exit(1)
rows = vr.run(os.environ.get("STANDBY_REPLAY_MODEL"), os.environ.get("STANDBY_REPLAY_EFFORT"))
check("live: the voice as it is now does what each turn should", vr.report(rows, os.environ.get("STANDBY_REPLAY_MODEL"),
                                                                          os.environ.get("STANDBY_REPLAY_EFFORT")))
print("\n%s" % ("ALL PASS" if not fails else "%d FAILED: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
