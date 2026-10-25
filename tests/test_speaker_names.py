"""Speaker ids -> display names, merges (aliases), user renames, and the
diarizer's merge + save/restore logic, without any audio."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, see agentpath.guard)
import json, os, subprocess, sys, tempfile, time, types
import numpy as np
from importlib.machinery import SourceFileLoader
sys.path.insert(0, "plugin/bin")
import sbspeakers
fails = []
def check(label, ok, d=""):
    print("  %-56s %s" % (label, "OK" if ok else "FAIL")); ok or (fails.append(label), print("     " + str(d)))
alias, names = {"3": 1}, {"1": "Alex"}
check("plain id -> Speaker N", sbspeakers.label({"spk": 2}, {}, {}) == "Speaker 2")
check("user name wins", sbspeakers.label({"spk": 1}, alias, names) == "Alex")
check("merged voice shows the survivor's name", sbspeakers.label({"spk": 3}, alias, names) == "Alex")
check("alias cycle cannot hang", sbspeakers.label({"spk": 4}, {"4": 5, "5": 4}, {}) in ("Speaker 4", "Speaker 5"))
check("Me channel is You", sbspeakers.label({"speaker": "Me"}, {}, {}) == "You")
check("unattributed row has no label", sbspeakers.label({"speaker": "Room"}, {}, {}) == "")

# ctx + agent render names from the files next to the transcript
S = "meeting-20261012-110000"        # a session id, as Standby makes them
root = tempfile.mkdtemp(); live = os.path.join(root, "live"); os.makedirs(live)
rows = [{"t": 5, "end": 7, "speaker": "Room", "spk": 1, "text": "first"},
        {"t": 9, "end": 11, "speaker": "Room", "spk": 3, "text": "merged voice"},
        {"t": 12, "end": 13, "speaker": "Room", "spk": 2, "text": "someone new"}]
with open(os.path.join(live, S + ".jsonl"), "w") as fh:
    fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
json.dump({"session": S}, open(os.path.join(live, S + ".meta.json"), "w"))
open(os.path.join(live, "CURRENT"), "w").write(S + "\n")
json.dump({"alias": {"3": 1}}, open(os.path.join(live, S + ".speakers.auto.json"), "w"))
json.dump({"names": {"1": "Alex", "2": "Sam"}}, open(os.path.join(live, S + ".speakers.names.json"), "w"))
env = dict(os.environ, STANDBY_ROOT=root)
out = subprocess.run(["/usr/bin/python3", "plugin/bin/standby-ctx", "recent", "--minutes", "5"],
                     env=env, capture_output=True, text=True).stdout
check("standby-ctx shows names", "00:05 Alex: first" in out and "00:09 Alex: merged voice" in out
      and "00:12 Sam: someone new" in out, out)
os.environ["STANDBY_ROOT"] = root
ag = SourceFileLoader("ag", "plugin/bin/standby-agent").load_module()
block = ag.transcript_block(rows, S)
check("agent pushes use names too", "00:09 Alex: merged voice" in block and "Sam" in block, block)
# The user's clock: from the Mac's zone in the meta.
meta_path = os.path.join(root, "live", "s.meta.json")
old_meta = open(meta_path).read() if os.path.exists(meta_path) else None
json.dump({"session": "s", "started": 1791087777.65, "utc_offset": 7200, "tz": "CEST"}, open(meta_path, "w"))
block = ag.transcript_block(rows, "s")
check("pushes say when 00:00 was, in the user's time", block.startswith("[TRANSCRIPT]\n(times are into the session; "
      "00:00 was Sun 4 Oct 06:22 CEST)\n"), block[:120])
# A session that didn't say: the zone of the machine the agent runs on (on the Mac, the Mac's
# own), never a fixed one. Two zones, to show it follows the machine.
clock_in = {}
for zone in ("Europe/Berlin", "Asia/Tokyo"):
    os.environ["TZ"] = zone
    time.tzset()
    clock_in[zone] = ag.session_clock({"started": 1791087777.65})
os.environ.pop("TZ", None)
time.tzset()
check("  a session that didn't say: this machine's own zone, named, never a fixed one",
      clock_in == {"Europe/Berlin": "Sun 4 Oct 06:22 CEST", "Asia/Tokyo": "Sun 4 Oct 13:22 JST"}, clock_in)
if old_meta is None:
    os.remove(meta_path)
else:
    open(meta_path, "w").write(old_meta)

# diarizer: merge + persistence (no model needed)
sys.modules.setdefault("sounddevice", types.SimpleNamespace())
sb = SourceFileLoader("sb", "mac/standby.py").load_module()
d = sb.OnlineDiarizer()
base = np.zeros(16); base[0] = 1
a = base.copy(); b = base.copy(); b[1] = 3.0        # cosine 0.32: below JOIN (0.40)
i1, _ = d.assign(a, 3.0); i2, _ = d.assign(b, 3.0)
check("dissimilar long utterances -> two speakers", (i1, i2) == (1, 2), (i1, i2))
c = base.copy(); c[1] = 0.9
i3, merged = d.assign(c, 3.0)
check("a bridging voice can merge the two", merged or i3 in (1, 2), (i3, merged, d.alias))
s2 = sb.OnlineDiarizer.from_json(json.loads(json.dumps(d.to_json())))
check("state survives a JSON round trip", s2.next_id == d.next_id and s2.alias == d.alias
      and set(s2.cent) == set(d.cent))
short = base.copy(); short[2] = 0.5
before = len(d.cent)
d.assign(short, 1.0)
check("a short, ambiguous utterance never starts a new speaker", len(d.cent) == before, len(d.cent))
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails)); sys.exit(1 if fails else 0)
