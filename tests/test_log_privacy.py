"""Production diagnostics omit words; session data and opt-in debug traces keep them.

Real app question, task, title and vocabulary paths, with the model and delivery
replaced. No audio, external service or real user data.
"""
import agentpath  # noqa: F401  (first: never the real ~/.standby or devices)
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import types
from collections import deque
from pathlib import Path
from unittest import mock

root = Path(tempfile.mkdtemp(prefix="sblogprivacy-"))
os.environ["STANDBY_ROOT"] = str(root)
os.environ.pop("STANDBY_CALL_TRACE", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mac"))
import ui  # noqa: E402
import agentctl  # noqa: E402
import sbtrace  # noqa: E402
import voice  # noqa: E402
import vocabulary  # noqa: E402

sid = "meeting-20261025-100000"
sdir = root / "sessions" / sid
sdir.mkdir(parents=True)
question = "Private question about Zephyr's confidential budget"
reply = "Private reply about Zephyr's confidential budget"
work = "Private task to change Zephyr's confidential budget"
title = "Private Zephyr budget title"
news = "Private Zephyr budget announcement"
diagnostics = io.StringIO()

with contextlib.redirect_stderr(diagnostics):
    # Typed and spoken questions still get their answer and their session chat.
    with mock.patch.object(agentctl, "ask", return_value=reply), \
            mock.patch.object(ui, "is_talk", return_value=False), \
            mock.patch.object(ui, "live_session", return_value=sid), \
            mock.patch.object(ui, "claude_on", return_value=True), \
            mock.patch.object(ui, "after_ask"):
        assert ui._run_ask(sid, question) == reply
        assert ui._run_ask(sid, question, voice=True) == reply
    assert reply in (sdir / "chat.jsonl").read_text()

    c = {"cc": "c0ffee00-1111-4222-8333-444444444444"}
    ui.LITE_CALLS[sid] = c
    with mock.patch.object(ui, "_lite_out") as deliver, \
            mock.patch.object(ui.threading, "Timer"), \
            mock.patch.object(ui, "_lite_announce"):
        t = ui._lite_hand(sid, c, work)
        assert work in deliver.call_args.args[1]["text"]
        ui.lite_progress({"session": sid, "id": t["id"], "kind": "queued"})
        ui._lite_lost(sid, t["id"], question)
    assert work in (sdir / "tasks.txt").read_text()

    # Titles remain in the session, without being repeated in ui.log.
    ui.append_chat(sid, "me", question)
    with mock.patch.object(ui, "session_info", return_value={"title_source": "default"}), \
            mock.patch.object(ui.transport, "for_config") as transport, \
            mock.patch.object(ui.subprocess, "run", return_value=types.SimpleNamespace(stdout=title)), \
            mock.patch.object(ui.threading, "Thread", side_effect=lambda target, **kw: types.SimpleNamespace(start=target)):
        transport.return_value.agent_cmd.return_value = ["fake-title"]
        ui.title_talk(sid)
    assert (sdir / "title.auto.txt").read_text().strip() == title

    vs = voice.VoiceSession.__new__(voice.VoiceSession)
    vs.seq, vs.log = 1, ui.log
    vs._told = deque([(news, time.time() - 100, None)])
    assert not vs._fresh_told()

    # What came back from a hand-off, as the voice words it: said and kept in the call, not in the log.
    told = "Private Zephyr budget result, told"
    task = {"id": "1", "state": "done", "seen": False}
    ui.LITE_CALLS[sid] = {"cc": c["cc"], "tasks": {"1": task}}
    with mock.patch.object(agentctl, "ask", return_value=told), \
            mock.patch.object(ui, "call_muted", return_value=False), \
            mock.patch.object(ui, "_lite_user_busy", return_value=False), \
            mock.patch.object(ui, "_lite_typed_words", return_value=""), \
            mock.patch.object(ui, "_lite_task_line", return_value="task 1: done"), \
            mock.patch.object(ui, "_lite_came_back"), \
            mock.patch.object(ui, "LITE_QUIET_GAP", 0.0):
        ui._lite_tell(sid)
    assert told in (sdir / "chat.jsonl").read_text(), "what the voice said is kept in the call's chat"
    assert "what came back" in diagnostics.getvalue()

    learner = vocabulary.Learner(root / "words.txt", folders=lambda: [],
                                 vocab=root / "vocab.json", store=root / "suggestions.json")
    assert learner.learn([("privatezephir", "PrivateZephyr")])

sbtrace.flush()
assert not (sdir / "trace.jsonl").exists(), "production tracing must default to off"
for private in (question, reply, work, title, news, told, "privatezephir", "PrivateZephyr"):
    assert private not in diagnostics.getvalue(), "private text reached a routine log"
assert "ask " in diagnostics.getvalue() and "handed to the session" in diagnostics.getvalue()

# The same real hand-off retains full details when explicitly opted into debugging.
(root / "config.json").write_text(json.dumps({"debug": {"call_trace": True}}))
debug_sid = "meeting-20261025-110000"
debug_dir = root / "sessions" / debug_sid
debug_dir.mkdir()
with contextlib.redirect_stderr(diagnostics), mock.patch.object(ui, "_lite_out"), \
        mock.patch.object(ui.threading, "Timer"):
    ui._lite_hand(debug_sid, {"cc": c["cc"]}, work)
sbtrace.flush()
rows = [json.loads(line) for line in (debug_dir / "trace.jsonl").read_text().splitlines()]
assert any(row.get("work") == work for row in rows), "debug trace lost its useful detail"
assert work not in diagnostics.getvalue(), "debug words must stay in the session trace"
print("ALL PASS")
