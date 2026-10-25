"""An agent's voice, carried from call to call (standby-agent): the Mac writes
live/<session>.voice.json; the first call's voice starts under the agent's voice name, every
later call's voice is a fork of the one before, in that one's folder; it keeps its skills, stays
small (Claude Code's auto-compact, set low). A voice without that file (a talk with no agent) is today's. A fake claude;
Claude Code's transcripts are files in a throwaway HOME."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import json, os, stat, sys, tempfile, time
from importlib.machinery import SourceFileLoader
from pathlib import Path

home = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbvoice-")))
os.environ["HOME"] = str(home)
os.environ["STANDBY_ROOT"] = str(home / ".standby")
(home / ".standby" / "agent" / "spool").mkdir(parents=True)
(home / ".standby" / "live").mkdir()
FOLDER = home / "code" / "orion"
FOLDER.mkdir(parents=True)
log = home / "fake-claude.log"
fake = home / "claude"
fake.write_text("#!/bin/sh\necho \"$PWD|$*\" >> %s\ncat > /dev/null\n" % log)
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
agent = SourceFileLoader("standby_agent_voice", agentpath.AGENT).load_module()
agent.CLAUDE = str(fake)
agent._CAPS.clear()
agent._CAPS.update(auto=True, dont_ask=True, prompts=True, no_persist=True, bare=True, autocompact=True, name=True)
agent.voice_bare = lambda: True                       # bare where it would be today
fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[:600])


def meta(session, **kw):
    (home / ".standby" / "live" / (session + ".meta.json")).write_text(json.dumps({"kind": "talk", "workspace": str(FOLDER)}))
    if kw:
        (home / ".standby" / "live" / (session + ".voice.json")).write_text(json.dumps(kw))


def argv(session, sid, fresh=True):
    return agent.build_argv(session, "sonnet", fresh, sid, stream=True, kind="voice")


def after(a, flag):
    return a[a.index(flag) + 1] if flag in a else None


S0, S1, S2 = "talk-20261016-0900", "talk-20261016-1000", "talk-20261016-1100"
V1, V2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

meta(S0)
a = argv(S0, V1)
check("a talk with no agent: today's voice (its tools, bare, no name, no compact setting)",
      after(a, "--tools") == "Bash,Read,Grep" and "--bare" in a and "-n" not in a and "--autocompact" not in a
      and after(a, "--session-id") == V1 and "--fork-session" not in a, a)

meta(S1, **{"from": None, "name": "Nadia · voice"})
a = argv(S1, V1)
check("an agent's first call: its voice starts under the agent's voice name",
      after(a, "-n") == "Nadia · voice" and after(a, "--session-id") == V1 and "--resume" not in a, a)
check("  it keeps its skills (no --bare, the Skill tool) and stays small (--autocompact 100k)",
      "--bare" not in a and after(a, "--tools") == "Bash,Read,Grep,Skill" and "Skill" in a
      and after(a, "--autocompact") == "100k", a)

proj = home / ".claude" / "projects" / "-x-orion"
proj.mkdir(parents=True)
(proj / (V1 + ".jsonl")).write_text(json.dumps({"type": "user", "cwd": str(FOLDER), "message": {"content": "hi"}}) + "\n")
meta(S2, **{"from": V1, "name": "Nadia · voice"})
a = argv(S2, V2)
check("the next call: a fork of the voice so far, under a new id (one writer; a failed call harms nothing)",
      after(a, "--resume") == V1 and "--fork-session" in a and after(a, "--session-id") == V2, a)
a = argv(S2, V2, fresh=False)
check("  the same call going on (its voice restarted): that call's voice, resumed",
      after(a, "--resume") == V2 and "--fork-session" not in a, a)
meta("talk-20261016-1200", **{"from": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "name": "Nadia · voice"})
a = argv("talk-20261016-1200", V2)
check("a voice to fork from that isn't on this machine: a fresh one, never a resume that fails",
      "--resume" not in a and after(a, "--session-id") == V2, a)
agent._CAPS.update(autocompact=False, name=False)
a = argv(S1, V1)
check("an older Claude Code without --autocompact or --name: neither is passed", "--autocompact" not in a and "-n" not in a, a)
agent._CAPS.update(autocompact=True, name=True)

print("the engine")
import sbagents  # noqa: E402
eng = agent.ClaudeSession(S2, "sonnet", kind="voice")
eng.start()
t0 = time.time()
while not log.exists() and time.time() - t0 < 10:
    time.sleep(0.05)
eng.stop(hard=True)
cwd, args = log.read_text().split("|", 1)            # the prelude's line breaks are in the command line
sid = open(agent.uuid_path(S2 + ".voice")).read().strip()
check("its voice is noted as Standby's own (to tidy, and to delete with the agent)", sid in sbagents.created())
check("  and runs in the folder the voice it forks lives in", os.path.realpath(cwd) == os.path.realpath(FOLDER)
      and ("--resume %s --fork-session --session-id %s" % (V1, sid)) in args, (cwd, args[-200:]))
check("  what it ran as is where the Mac reads it (agent/<session>.voice.uuid)",
      (home / ".standby" / "agent" / (S2 + ".voice.uuid")).read_text().strip() == sid)


print("helper sessions never offered as agents")
import re  # noqa: E402
proj2 = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(FOLDER)))
proj2.mkdir(parents=True, exist_ok=True)
def conv(sid, title):
    (proj2 / (sid + ".jsonl")).write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "cwd": str(FOLDER), "message": {"content": "hi"}},
        {"type": "custom-title", "customTitle": title},
        {"type": "assistant", "message": {"usage": {"input_tokens": 5}}}]) + "\n")
REAL, VNOTED, VNAMED, COPY = ("%s-2222-4222-8222-222222222222" % (c * 8) for c in "1234")
conv(REAL, "Nadia")
conv(VNOTED, "Nadia · voice")
conv(VNAMED, "Theo · voice")
conv(COPY, "Nadia")
sbagents.note_created(VNOTED, "voice")
sbagents.note_created(COPY, "copy")
agent.TEMP_DIRS = ()                                  # this test's folder is a temporary one: count it as real
OWN, MADE, MCOPY, GONE = ("%s-3333-4333-8333-333333333333" % (c * 8) for c in "5678")
for sid_, title_ in ((OWN, "Jules"), (MADE, "Nadia"), (MCOPY, "Nadia"), (GONE, "Toby")):
    conv(sid_, title_)
for key, sid_, meta_ in (("meeting-20261016-0900", OWN, {"attach": {"from": OWN, "copy": False}}),   # the user's own, attached
                         ("meeting-20261016-0901", MADE, {"workspace": str(FOLDER)}),               # a meeting's own new one
                         ("meeting-20261016-0902", MCOPY, {"attach": {"from": REAL, "copy": True}}),  # a copy, noted before kinds
                         ("meeting-20261016-0903", GONE, None)):                                    # its meta gone: can't tell
    (home / ".standby" / "agent" / (key + ".uuid")).write_text(sid_ + "\n")
    if meta_ is not None:
        (home / ".standby" / "live" / (key + ".meta.json")).write_text(json.dumps(meta_))
(home / ".standby" / "agent" / "talk-20261016-0904.voice.uuid").write_text(VNAMED + "\n")      # an old per-call voice
ids = [c["id"] for c in agent.conversation_list(limit=50)]
check("Add agent's list (the folder's conversations): the real one, never a voice or a copy Standby made",
      REAL in ids and VNOTED not in ids and VNAMED not in ids and COPY not in ids, ids)
check("  nor anything Standby's own records say it made: a meeting's new one, a copy, a voice from before",
      MADE not in ids and MCOPY not in ids and VNAMED in agent.standby_made(), ids)
check("  the user's own conversation a session was attached to stays offered, and one it can't tell about",
      OWN in ids and GONE in ids, ids)
S3 = "talk-20261016-1400"
meta(S3)
mp = home / ".standby" / "live" / (S3 + ".meta.json")
mp.write_text(json.dumps({"kind": "talk", "workspace": str(FOLDER), "attach": {"from": V1, "copy": True}}))
eng = agent.ClaudeSession(S3, "sonnet", kind="meeting")
eng.start()
eng.stop(hard=True)
copy_sid = open(agent.uuid_path(S3)).read().strip()
check("a call's copy is noted as a helper as it's made", copy_sid in sbagents.helpers())

print()
print("%d failed" % len(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
