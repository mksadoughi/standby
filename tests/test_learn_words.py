"""What the meeting's own Claude teaches dictation, the app's side,
with the agent faked and never a real Claude: at the end of each meeting and
talk (a minute after it ends, once no question of the user's is under way),
and every 30 minutes in a long meeting, the session's own conversation is
asked which words the speech recognition misheard; the fixes it lists, guarded
(a lone everyday word never alone), go into the word list dictation uses; the
session's transcript view and exports show them fixed while its saved transcript
keeps what was heard; its view says what was learned, for the chat's quiet
line. Nothing is asked with Claude off, with too little said, or twice for the
same; the log has counts, never the words."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import io, json, os, socket, sys, tempfile, threading, time, urllib.request
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sblearn-"))
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
os.environ.update(STANDBY_ROOT=str(root), STANDBY_UI_PORT=str(PORT))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
import agentctl  # noqa: E402
import vocabulary  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-68s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(detail)[:500])


WORDS = root / "dictation-words.txt"
WORDS.write_text("# mine\nCloud Code => Claude Code\n")
ui.words_file = lambda: WORDS
state = {"live": None}
ui.live_session = lambda: state["live"]
asked = []
ANSWER = {"text": "P X R => PXR\n- `cooper netties` => `Kubernetes`\ncloud => Claude\nSUGGEST: and the budget?"}


def fake_ask(question, err=None, session=None, **kw):
    asked.append((question, session))
    if isinstance(ANSWER["text"], Exception):
        raise ANSWER["text"]
    return ANSWER["text"]


agentctl.ask = fake_ask
logged = io.StringIO()
real_log = ui.log
ui.log = lambda m: (logged.write(m + "\n"), real_log(m))
sync = lambda fn, *a: fn(*a)
NOW = time.time()


def session(sid, kind="meeting", lines=0, me=0, started=NOW, claude=True):
    d = ui.SESSIONS / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"started": started, "kind": kind}))
    rows = [{"t": i, "end": i + 1, "speaker": "Room", "text": "we ship P X R on cooper netties, line %d" % i}
            for i in range(lines)]
    (d / "transcript.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    chat = [{"role": "me", "text": "what is P X R"}, {"role": "ai", "text": "a format"}] * me
    (d / "chat.jsonl").write_text("".join(json.dumps(m) + "\n" for m in chat))
    if not claude:
        (d / "claude.off").write_text("")
    return d


def more(sid, n):
    with open(ui.SESSIONS / sid / "transcript.jsonl", "a") as fh:
        for i in range(n):
            fh.write(json.dumps({"t": 1000 + i, "text": "and more about P X R"}) + "\n")


# A long meeting, live: a pass every 30 minutes, with something new.
d = session("meeting-20261009-150000", lines=60, started=NOW - 31 * 60)
state["live"] = "meeting-20261009-150000"
ui.learn_tick(now=NOW, start=sync)
check("a long meeting, 30 minutes in: its own conversation is asked, once",
      asked == [(ui.WORDS_TAG + vocabulary.WORDS_QUESTION, "meeting-20261009-150000")], asked)
text = WORDS.read_text()
check("  the fixes it listed go into dictation's word list, learned",
      "P X R => PXR" in text and "cooper netties => Kubernetes" in text and vocabulary.LEARNED in text, text)
check("  but not a lone everyday word (cloud => Claude), and the user's own lines stay first",
      "cloud => Claude\n" not in text.split(vocabulary.LEARNED)[-1] and text.startswith("# mine\nCloud Code => Claude Code\n"), text)
got = ui.learned_words("meeting-20261009-150000")
check("  what was learned kept with the session", got.get("words") == [["P X R", "PXR"], ["cooper netties", "Kubernetes"]]
      and got.get("size") == 60, got)
check("  never as a question in its chat", ui.read_jsonl(d / "chat.jsonl") == [])
check("  the log: counts, never the words", "2 new, 1 left out" in logged.getvalue()
      and "Kubernetes" not in logged.getvalue() and "cooper" not in logged.getvalue(), logged.getvalue())
ui.learn_tick(now=NOW + 60, start=sync)
check("  a minute later, nothing new: not asked again", len(asked) == 1, asked)
more("meeting-20261009-150000", 10)
ui.learn_tick(now=NOW + 120, start=sync)
check("  ten lines more, mid-meeting: still not (every 30 minutes, 50 lines new)", len(asked) == 1, asked)

# The meeting ends: a last pass, with what is new since.
state["live"] = None
ANSWER["text"] = "P X R => PXR\nsprint review => Sprint Review"
ui.learn_tick(now=NOW + 180, start=sync)
check("the meeting ends: not at once (a Summarize asked straight away goes first)", len(asked) == 1, asked)
with ui._lock:
    ui.PENDING["meeting-20261009-150000"] = {"progress": []}
ui.learn_tick(now=NOW + 250, start=sync)
check("  a minute on, the user's question still under way: it waits for it", len(asked) == 1, asked)
with ui._lock:
    del ui.PENDING["meeting-20261009-150000"]
ui.learn_tick(now=NOW + 260, start=sync)
check("  then its last pass", len(asked) == 2 and asked[-1][1] == "meeting-20261009-150000", asked)
check("  only the new ones written; the session's list grows",
      WORDS.read_text().count("P X R => PXR") == 1 and ["sprint review", "Sprint Review"] in ui.learned_words("meeting-20261009-150000")["words"])
ui.learn_tick(now=NOW + 400, start=sync)
check("  and not again once it has ended", len(asked) == 2, asked)

# Shown fixed: the view and the exports; the saved transcript keeps what was heard.
rows = ui.fixed_rows(ui.read_jsonl(d / "transcript.jsonl"))
check("the transcript as shown: the fixes applied, what was heard beside it",
      rows[0]["text"] == "we ship PXR on Kubernetes, line 0" and rows[0]["heard"] == "we ship P X R on cooper netties, line 0", rows[0])
check("  the saved transcript keeps what was heard", "P X R" in (d / "transcript.jsonl").read_text())
check("  Copy and Download show them fixed", "we ship PXR on Kubernetes" in ui.export_text("meeting-20261009-150000", "md")
      and "cooper netties" not in ui.export_text("meeting-20261009-150000", "txt"))
server = ui.serve()
threading.Thread(target=server.serve_forever, daemon=True).start()
with urllib.request.urlopen("http://127.0.0.1:%d/api/session/meeting-20261009-150000" % PORT, timeout=10) as r:
    view = json.loads(r.read())
check("  /api/session: the fixed transcript, and what was learned, for the chat's quiet line",
      view["transcript"][0]["text"].startswith("we ship PXR") and ["P X R", "PXR"] in view["learned"], view.get("learned"))
server.shutdown()

# Talks, too: from the user's own spoken questions.
session("t1", kind="talk", me=2)
state["live"] = "t1"
ui.learn_tick(now=NOW, start=sync)
state["live"] = None
ui.learn_tick(now=NOW, start=sync)
ui.learn_tick(now=NOW + 61, start=sync)
check("a talk ends, with questions in it: asked too", asked[-1] == (ui.WORDS_TAG + vocabulary.WORDS_QUESTION, "t1"), asked[-1:])

# Not asked: Claude off (nothing ever left the Mac), too little said, a pass that failed.
n = len(asked)
session("off", lines=80, started=NOW - 3600, claude=False)
state["live"] = "off"
ui.learn_tick(now=NOW, start=sync)
state["live"] = None
ui.learn_tick(now=NOW, start=sync)
ui.learn_tick(now=NOW + 61, start=sync)
check("Claude off: never asked, live or ended", len(asked) == n, asked[n:])
session("short", lines=5)
state["live"] = "short"
ui.learn_tick(now=NOW, start=sync)
state["live"] = None
ui.learn_tick(now=NOW, start=sync)
ui.learn_tick(now=NOW + 61, start=sync)
check("a short meeting: nothing to go by, not asked", len(asked) == n, asked[n:])
ANSWER["text"] = RuntimeError("the agent is gone")
session("meeting-20261009-160000", lines=30)
state["live"] = "meeting-20261009-160000"
ui.learn_tick(now=NOW, start=sync)
state["live"] = None
ui.learn_tick(now=NOW, start=sync)
ui.learn_tick(now=NOW + 61, start=sync)
check("a pass that fails: logged, nothing learned, and the session can be asked again later",
      "learning words from meeting-20261009-160000" in logged.getvalue() and not ui.learned_words("meeting-20261009-160000") and "meeting-20261009-160000" not in ui._LEARNING)
ANSWER["text"] = "NONE"
check("  NONE: nothing learned", vocabulary.parse_terms("NONE") == [])

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
