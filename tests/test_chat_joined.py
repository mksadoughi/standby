"""A voice question the user went on from leaves no trace in the chat: no
"(stopped)" row before the question that has all of it. More to the question while
Claude thinks, or an early question (asked on likely-final words) that was not the
user's turn after all, is cancelled quietly, and its rows go: even when it had
already been answered. Stopped any other
way (never mind, a tap, cut off), it stays, as what happened. The agent is faked."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby, and no child may crash)
import json, os, sys, tempfile, threading, time
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbjoined-"))
os.environ.update(STANDBY_ROOT=str(root))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import agentctl  # noqa: E402
import transport  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-70s %s" % (label, "OK" if ok else "FAIL"))
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


class FakeTransport(object):
    mode = "local"
    def read(self, rel):
        return None
    def ensure_agent(self, model=None):
        return True, "ok"


transport.for_config = lambda cfg=None: FakeTransport()
SID = "talk-20261008-170000"
(ui.SESSIONS / SID).mkdir(parents=True)
(ui.SESSIONS / SID / "meta.json").write_text(json.dumps({"started": time.time(), "kind": "talk"}))
ui.live_session = lambda: SID
ui.title_talk = lambda sid: None
ui.claude_on = lambda sid: True
ui.wait_delivered = lambda sid, seconds=30: True

cancelled, slow, n = [], {"on": False}, [0]


def fake_ask(question, err=None, on_progress=None, on_queued=None, session=None,
             where=None):
    n[0] += 1
    qid = "q%d" % n[0]
    on_queued(qid)
    on_progress({"kind": "timing", "app_spawned": time.time()})     # timing marks: the voice's, not the chat's
    shown.append([e.get("kind") for e in (ui.PENDING.get(SID) or {}).get("progress", [])])
    if "quietly" not in question:
        on_progress({"kind": "delta", "text": "Let me see. "})
    if slow["on"]:
        end = time.time() + 10
        while time.time() < end and qid not in cancelled:
            time.sleep(0.02)
        return "(stopped)"
    if "take it deeper" in question:
        return "[HANDOFF] Look into the outage."
    return "The answer to " + question.replace("[VOICE] ", "")


shown = []                                          # what the page would show as the ask's steps
agentctl.ask = fake_ask
agentctl.cancel = lambda qid, where=None, keep=False: cancelled.append(qid)
chat = lambda: [(r["role"], r["text"]) for r in ui.read_jsonl(ui.SESSIONS / SID / "chat.jsonl")]


def ask(question, out):
    out.append(ui.ask_spoken(SID, question, lambda ev: None, lambda qid: out.append(qid)))


# More to the question while Claude thinks: the first is dropped quietly.
slow["on"], got = True, []
t = threading.Thread(target=ask, args=("What's the Q3 budget?", got))
t.start()
wait(lambda: got)                                   # its id: the agent has it
ui.cancel_voice(SID, got[0], quiet=True)
t.join(10)
check("more to the question: the first is cancelled in the agent", cancelled == [got[0]], cancelled)
check("  and leaves nothing in the chat: no question, no (stopped)", chat() == [], chat())
slow["on"] = False
ui.ask_spoken(SID, "What's the Q3 budget? And Friday.", lambda ev: None, lambda qid: None)
check("  the joined question is the chat's, with its answer",
      chat() == [("me", "What's the Q3 budget? And Friday."), ("ai", "The answer to What's the Q3 budget? And Friday.")],
      chat())

# Stopped any other way: it stays, as what happened.
slow["on"], got = True, []
t = threading.Thread(target=ask, args=("Dig into the outage.", got))
t.start()
wait(lambda: got)
ui.cancel_voice(SID, got[0])                        # never mind, a tap, cut off
t.join(10)
check("stopped any other way (never mind): it stays, with what Claude had said",
      chat()[-2:] == [("me", "Dig into the outage."), ("ai", "Let me see. …\n\n(stopped)")], chat()[-2:])
slow["on"] = False

# An early question, answered already, that was not the user's turn after all.
before = chat()
got = []
ask("And who owns it?", got)
check("an early question answered: in the chat for now", chat()[-2:] == [("me", "And who owns it?"),
                                                                     ("ai", "The answer to And who owns it?")])
ui.cancel_voice(SID, got[0], quiet=True)
check("  they went on after all: both its rows go, and nothing is cancelled in the agent",
      chat() == before and got[0] not in cancelled, (chat(), cancelled))
ui.cancel_voice(SID, got[0], quiet=True)
check("  and a second word of it changes nothing", chat() == before, chat())

# Answered and partly said before they went on: the answer's row stays, saying exactly what was heard.
before = chat()
got = []
ask("And the budget?", got)
ui.cancel_voice(SID, got[0], quiet=True, said="The answer to")
check("  answered and partly said, then they went on: the question goes, the answer stays as what was said",
      chat() == before + [("ai", "The answer to")], chat()[-2:])


# A call's hand-off: the app adds nothing of its own, said or shown. Its row is exactly what the voice
# streamed before taking it deeper, or nothing when it streamed none; and the app says nothing more.
ui.LITE_CALLS[SID] = {"cc": "0f3c2a77-1111-4222-8333-944455556666"}
for q, streamed in (("please take it deeper", "Let me see."), ("quietly take it deeper", "")):
    before = len(chat())
    ui._begin_ask(SID, q, voice=True)
    ui._run_ask(SID, q, voice=True)
    check("a hand-off on a call (the voice streamed %r): its row is exactly that, no line of the app's" % streamed,
          chat()[before:] == [("me", q), ("ai", streamed)], chat()[before:])
ui.LITE_CALLS.pop(SID, None)
check("  and no fixed hand-off line is left in the app to say", not hasattr(ui, "LITE_HANDED"))

# the ask's timing marks go to the voice, and never into the chat (saved or shown).
import agentctl as _ac  # noqa: E402
check("agentctl times what ui tags as a voice question", _ac.VOICE == ui.VOICE_TAG)
voice_ev, before = [], len(ui.read_jsonl(ui.SESSIONS / SID / "chat.jsonl"))
ui.ask_spoken(SID, "And the plan?", voice_ev.append, lambda qid: None)
check("the ask's timing marks reach the voice", any(e.get("kind") == "timing" for e in voice_ev), voice_ev)
check("  and never the chat's rows or steps", not any(
    "app_spawned" in json.dumps(r) for r in ui.read_jsonl(ui.SESSIONS / SID / "chat.jsonl")[before:])
      and shown and not any("timing" in k for k in shown), shown[-1:])

if fails:
    print("%d FAILED" % len(fails))
    sys.exit(1)
print("ALL PASS")
