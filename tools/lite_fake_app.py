#!/usr/bin/env python3
"""The Standby app as Standby inside Claude Code sees it, with a stand-in recorder: for running
the mod against the real /api/lite/* without a microphone or a model.

    ~/.standby/venv/bin/python tools/lite_fake_app.py [port]       (default 8799)

Serves the real mac/ui.py and mac/lite.py on a scratch STANDBY_ROOT (never ~/.standby). A
listen starts nothing: the meeting goes live at once and a line is "said" every second, from
two speakers, one of them a remembered voice. Stop ends it and makes a card without a model.
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
root = Path(tempfile.mkdtemp(prefix="sblite-app-"))
os.environ.update(STANDBY_ROOT=str(root), STANDBY_UI_PORT=str(port), STANDBY_NO_DEVICES="1")
cli = root / "fake-standby"
cli.write_text("#!/bin/bash\nexit 0\n")
cli.chmod(0o755)
os.environ["STANDBY_CLI"] = str(cli)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mac"))
import ui  # noqa: E402

LIVE = {"sid": None}
ui.live_session = lambda fresh=False: LIVE["sid"]
ui.mac_with = lambda: None if not LIVE["sid"] else "a meeting"
SAY = ["Let's start with the routing cache.", "The TTL is too short, five minutes.", "Can we make it an hour?",
       "Only if invalidation works on deploy.", "I'll check the deploy hook today.", "Then we ship Friday."]


def go_live(wait):
    sid = time.strftime("meeting-%Y%m%d-%H%M%S")
    d = ui.SESSIONS / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"kind": "meeting", "started": time.time(), "push_lines": 3, "push_seconds": 30}))
    (d / "speakers.known.json").write_text(json.dumps({"known": {"1": "Theo"}}))
    LIVE["sid"] = sid

    def talk():
        t0, i = time.time(), 0
        while LIVE["sid"] == sid:
            row = {"t": time.time() - t0, "end": time.time() - t0 + 1, "spk": i % 2, "ch": "Room", "text": SAY[i % len(SAY)]}
            with open(d / "transcript.jsonl", "a") as fh:
                fh.write(json.dumps(row) + "\n")
            i += 1
            time.sleep(1.0)
    threading.Thread(target=talk, daemon=True).start()
    return sid


ui.start_session = lambda **kw: {"started": True}
ui._wait_live = go_live
_stop = ui.stop_session


def stop_session(*a, **kw):
    sid, LIVE["sid"] = LIVE["sid"], None
    return {"ok": True, "session": sid}


ui.stop_session = stop_session
ui._decisions = lambda first, text: (["Make the cache TTL an hour once deploy invalidation works", "Ship Friday"], "")
print("fake Standby app on http://127.0.0.1:%d (root %s)" % (port, root), flush=True)
server = ui.serve()
server.serve_forever()
