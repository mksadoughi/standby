"""Standby inside Claude Code: the meeting side of the Lite mod.

The mod (lite/, a Claude Code plugin) is the UI and the only thing that talks to the person's
Claude Code session. This module is what it asks the app for: a meeting on the app's own
recorder, with the app's own Claude off (the person's session is the listener), its lines with
the names the engine gives them, the next batch of them under the same push policy a meeting's
agent gets, pause, resume and end, the card, and one live stream of events
for the mod (meeting lines now; calls publish into it too).

How the transcript reaches Claude is the meeting's "pass": "ask" (the default) sends it with each
prompt the user types there, all of it the first time and then what's new, and the mod's meeting
tool reads it afterwards; "live" pushes it in batches as it's said. The summary stays in Standby:
nothing about a meeting or a call goes to Claude at the end.

A Lite meeting is an ordinary meeting, bound like Add your Mac's (agent.json) to an owner of
its own, "lite:<the Claude Code session id>", so pause, resume, end, the card and the sweep are
the same code. Two things differ: Claude stays off when it resumes, and its card stays in
Standby, with nothing to choose.
"""

import json
import os
import queue
import re
import threading
import time
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "bin"))

CC_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
OWNER = "lite:"
LITE_FILE = "lite.json"               # in the session's folder: {cc, through, pass}: what was sent to the session
PREAMBLE = ("Standby, the user's meeting companion, is recording a meeting on their Mac and sends you its "
            "transcript as it goes, in these [Standby · meeting] notes. The speech-to-text can mangle names. "
            "Listen and build context; answer only when the user asks you something.")
ASK_PREAMBLE = ("Standby, the user's meeting companion, is recording a meeting on their Mac. Its transcript so "
                "far comes with the user's prompts (new lines each time) in these [Standby · meeting] blocks; "
                "after the meeting, the standby meeting tool reads it. The speech-to-text can mangle names.")
PASSES = ("ask", "live")
SAVED = "Summaries from Claude Code are kept in Standby"


def owner(cc):
    return OWNER + cc


def is_lite(data):
    """An agent.json that belongs to a Lite meeting."""
    return isinstance(data, dict) and str(data.get("agent") or "").startswith(OWNER)


def cc_of(data):
    return str(data.get("agent"))[len(OWNER):] if is_lite(data) else None


class Lite(object):
    """The meeting endpoints, over the app (`ui`: mac/ui.py, or a test's stand-in)."""

    def __init__(self, ui):
        self.ui = ui
        self.lock = threading.RLock()
        self.subs = []                        # [(cc, queue)]: the mod's live streams
        self._seen = {}                       # session -> (rows, names, state): what the stream said last
        self._cards = {}                      # session -> the card as last announced
        self._open = {}                       # cc -> its meeting not yet ended (live or paused), or None
        self._thread = None

    # ---------------------------------------------------------------- whose meeting

    def meeting(self, sid):
        """(folder, agent.json) of a Lite meeting, or (None, None)."""
        sdir, data = self.ui._mac_meeting(sid)
        return (sdir, data) if is_lite(data) and data.get("kind") != "call" else (None, None)

    def carded(self, sid):
        """(folder, agent.json) of a Lite meeting or call, the two that have a card, or (None, None)."""
        if not isinstance(sid, str) or not self.ui.valid_id(sid):
            return None, None
        sdir = self.ui.SESSIONS / sid
        data = self.ui.addmac.bound(sdir)
        return (sdir, data) if is_lite(data) else (None, None)

    def _live_of(self, cc):
        """(session, agent.json) of this Claude Code session's meeting not yet ended, live or paused,
        or (None, None). Asked twice a second: the session folders are read once per Claude Code
        session (a meeting paused before Standby restarted), then only that meeting's own."""
        live = self.ui.live_session()
        sdir, data = self.meeting(live) if live else (None, None)
        if data and cc_of(data) == cc:
            self._open[cc] = live
            return live, data
        if cc not in self._open:
            self._open[cc] = self._open_on_disk(cc)
        sid = self._open[cc]
        sdir, data = self.meeting(sid) if sid else (None, None)
        if data and not data.get("ended_at"):
            return sid, data                      # paused: still this session's meeting
        self._open[cc] = None
        return None, None

    def _open_on_disk(self, cc):
        for d in sorted(self.ui.SESSIONS.glob("*/" + self.ui.addmac.AGENT_FILE), reverse=True):
            data = self.ui.addmac.bound(d.parent)
            if is_lite(data) and data.get("kind") != "call" and cc_of(data) == cc and not data.get("ended_at"):
                return d.parent.name
        return None

    def last_of(self, cc):
        """This Claude Code session's latest meeting or call, ended or not, as {session, kind}, or None."""
        mine = []
        for d in self.ui.SESSIONS.glob("*/" + self.ui.addmac.AGENT_FILE):
            data = self.ui.addmac.bound(d.parent)
            if is_lite(data) and cc_of(data) == cc:
                mine.append((float(data.get("started") or 0), d.parent.name, "call" if data.get("kind") == "call" else "meeting"))
        return {"session": max(mine)[1], "kind": max(mine)[2]} if mine else None

    # ---------------------------------------------------------------- the endpoints

    def state(self, cc):
        """GET /api/lite/state?cc=: the app is up, what it can do, and this session's live meeting if any."""
        # What this build can do: the mod offers only these
        can = {"listen": True, "window": callable(getattr(self.ui, "open_window", None)),     # Standby's compact window, once the app has it
               "orb": callable(getattr(self.ui, "lite_orb", None))}                          # and a call into the orb from the band
        out = {"app": True, "can": can, "live": None}
        if not CC_ID.match(str(cc or "")):
            return dict(out, error="No such Claude Code session")
        sid, data = self._live_of(cc)
        if sid:
            ev = self.ui.mac_event(sid) or {}
            out["live"] = {"kind": "meeting", "session": sid, "started": data.get("started"),
                           "title": data.get("name") or self.ui.session_info(sid).get("title") or "",
                           "paused": bool(ev.get("paused")), "recorded_s": ev.get("recorded_s"),
                           "capture": self.ui.addmac.capture_mode(data.get("meeting_audio"), data.get("mic")),
                           "mic_denied": bool(self.ui.session_info(sid).get("mic_denied")),
                           "pass": self.pass_of(self.ui.SESSIONS / sid)}
        return out

    def _state_event(self, cc, sid):
        """The meeting's state to its session's stream, now, and noted so the next look doesn't say
        it again."""
        ev = self.ui.mac_event(sid) or {}
        state = (bool(ev.get("live")), bool(ev.get("paused")), bool(ev.get("ended")))
        with self.lock:
            rows, names, _ = self._seen.get(sid, (len(self._rows(sid)), self._names(sid)[1], None))
            self._seen[sid] = (rows, names, state)
        note = self.ui.auto_stopped(sid) if state[2] else None
        self.publish(cc, dict({"meet": "state", "session": sid, "live": state[0], "paused": state[1], "ended": state[2]},
                              **({"note": note} if note else {})))

    def card_event(self, sid):
        """The engine wrote (or rewrote) a Lite meeting's or call's card: one event per change, not per writer."""
        sdir, data = self.carded(sid)
        if data is None:
            return
        card = self.ui.read_json(sdir / "card.json") or {}
        mark = (tuple(card.get("points") or []), card.get("gist"))
        with self.lock:
            if self._cards.get(sid) == mark:
                return
            self._cards[sid] = mark
        kind = "call" if data.get("kind") == "call" else "meet"
        self.publish(cc_of(data), {kind: "card", "session": sid})

    def listen(self, b):
        """POST /api/lite/listen {cc, capture?, name?, pass?}: a meeting on the app's recorder, its own
        Claude off, its transcript passed as `pass` says (else Settings'). {session, pass} once the
        recorder is live, or {error}."""
        cc = str(b.get("cc") or "")
        if not CC_ID.match(cc):
            return {"error": "No such Claude Code session"}
        how = b.get("pass") or self.ui.get_settings().get("lite_transcript") or "ask"
        if how not in PASSES:
            return {"error": "Pass the transcript on ask or live"}
        mode = b.get("capture") or self.ui.get_settings().get("capture") or "auto"
        if mode not in ("auto", "mic", "meeting"):
            return {"error": "Choose meeting audio, the mic, or both"}
        have, _ = self._live_of(cc)
        if have:
            return {"session": have, "pass": self.pass_of(self.ui.SESSIONS / have)}
        # one at a time: a meeting never ends a call (the recorder would end the talk to start it)
        live = self.ui.live_session()
        if live and self.ui.is_talk(live):
            return {"error": "You're on a call: end it first"}
        who = self.ui.mac_with()
        if who:
            self.ui.log("lite: %s's listen refused: the Mac is with %s (live: %s)" % (cc[:8], who, self.ui.live_session()))
            return {"error": "Your Mac is with %s: stop it there first" % who}
        r = self.ui.start_session(kind="meeting", capture=mode, no_claude=True)
        if not r.get("started"):
            return {"error": r.get("error") or "Your Mac didn't start"}
        sid = self.ui._wait_live(self.ui.MAC_WAIT)
        if isinstance(sid, dict):
            return sid
        audio, mic = {"auto": (True, True), "meeting": (True, False), "mic": (False, True)}[mode]
        sdir = self.ui.SESSIONS / sid
        self.ui.addmac.bind(sdir, {"id": owner(cc), "session": None}, {"copy": False}, audio, mic, b.get("name") or "")
        self._save(sdir, {"cc": cc, "through": 0, "pass": how})
        self._open[cc] = sid
        self.ui.messenger().lite_session(cc, b.get("cwd"))     # listed in Standby's past meetings and calls
        with self.lock:
            self._seen[sid] = (0, self._names(sid)[1], (True, False, False))     # from its first line on
        if (b.get("name") or "").strip():
            self.ui.rename_session(sid, b["name"])
        self.ui.log("lite: %s listens to %s (%s, transcript %s)" % (cc[:8], sid, mode, how))
        self.publish(cc, {"meet": "state", "session": sid, "live": True, "paused": False})
        self.start()
        return {"session": sid, "pass": how}

    def pass_of(self, sdir):
        """How a meeting's transcript reaches Claude: "ask" or "live"."""
        how = self._load(sdir).get("pass")
        return how if how in PASSES else "ask"

    def set_pass(self, b):
        """POST /api/lite/pass {session, pass}: change it for this meeting (its window's ⋯). To live,
        the lines not yet sent go with the next batch; to ask, they wait for the next prompt."""
        sdir, data = self.meeting(b.get("session"))
        if data is None:
            return {"error": "No such meeting"}
        if b.get("pass") not in PASSES:
            return {"error": "Pass the transcript on ask or live"}
        with self.lock:
            lite = self._load(sdir)
            lite["pass"] = b["pass"]
            self._save(sdir, lite)
        self._state_event(cc_of(data), b["session"])
        return {"ok": True, "pass": b["pass"]}

    def _rows(self, sid):
        read = getattr(self.ui, "transcript_rows", self.ui.read_jsonl)     # read on from where the last look stopped
        return self.ui.fixed_rows(read(self.ui.SESSIONS / sid / "transcript.jsonl"))

    def _names(self, sid):
        sp = self.ui.speakers(sid)
        return sp, dict(sp.get("known") or {}, **(sp.get("names") or {}))

    def _line(self, i, r, sp, names):
        import sbspeakers
        return {"i": i, "t": r.get("t", 0), "speaker": r.get("spk") if r.get("spk") is not None else r.get("ch"),
                "name": sbspeakers.label(r, sp.get("alias") or {}, names) or "", "text": r.get("text", ""),
                "src": r.get("src"), "end": r.get("end")}

    def lines(self, sid, after=0):
        """GET /api/lite/lines?session=&after=: the lines after cursor `after`, named as the engine
        names them (remembered voices, the user's names), and the names as they stand."""
        sdir, data = self.meeting(sid)
        if data is None:
            return {"error": "No such meeting"}
        try:
            after = max(0, int(after or 0))
        except (TypeError, ValueError):
            after = 0
        rows = self._rows(sid)
        sp, names = self._names(sid)
        return {"lines": [self._line(i, r, sp, names) for i, r in enumerate(rows) if i >= after],
                "next": len(rows), "renamed": {str(k): v for k, v in names.items()}}

    def _said(self, sid, rows):
        """Transcript rows as Claude reads them: "14:02 Nadia: it ships Friday", one a line."""
        import sbspeakers
        sp, names = self._names(sid)
        out = []
        for r in rows:
            who = sbspeakers.label(r, sp.get("alias") or {}, names)
            out.append("%s %s%s" % (self._stamp(sid, r.get("t")), (who + ": ") if who else "", r.get("text", "")))
        return out

    def _stamp(self, sid, t):
        started = float(self.ui.session_info(sid).get("started") or 0)
        return time.strftime("%H:%M", time.localtime(started + float(t or 0)))

    def batch(self, sid, everything=False, now=None):
        """GET /api/lite/batch?session=&all=: the next batch for the session, by the meeting's own push
        policy (every push_lines lines, or push_seconds after the first unsent one), or with all=1 every
        unsent line (the user is asking: they go with the question). {text, through} or {}."""
        sdir, data = self.meeting(sid)
        if data is None:
            return {"error": "No such meeting"}
        meta = self.ui.read_json(sdir / "meta.json") or {}
        every = int(meta.get("push_lines") or self.ui.get_settings().get("push_lines") or 10)
        seconds = float(meta.get("push_seconds") or 60)
        lite = self._load(sdir)
        through = int(lite.get("through") or 0)
        rows = self._rows(sid)
        pending = rows[through:]
        if not pending:
            return {}
        started = float(self.ui.session_info(sid).get("started") or 0)
        oldest = started + float(pending[0].get("end", pending[0].get("t", 0)) or 0)
        if not everything and (self.pass_of(sdir) == "ask" or (len(pending) < every and (now or time.time()) - oldest < seconds)):
            return {}                     # on ask: only with a prompt (all=1), never on its own
        out = ["[Standby · meeting · %s-%s]" % (self._stamp(sid, pending[0].get("t")), self._stamp(sid, pending[-1].get("t")))]
        if through == 0:
            out.append(ASK_PREAMBLE if self.pass_of(sdir) == "ask" else PREAMBLE)
        out += self._said(sid, pending)
        return {"text": "\n".join(out), "through": through + len(pending)}

    def batch_sent(self, b):
        """POST /api/lite/batch/sent {session, through}: the session has the lines up to `through`."""
        sdir, data = self.meeting(b.get("session"))
        if data is None:
            return {"error": "No such meeting"}
        try:
            through = int(b.get("through"))
        except (TypeError, ValueError):
            return {"error": "Bad cursor"}
        with self.lock:
            lite = self._load(sdir)
            lite["through"] = max(int(lite.get("through") or 0), min(through, len(self._rows(b["session"]))))
            self._save(sdir, lite)
        return {"ok": True, "through": lite["through"]}

    def pause(self, b):
        sdir, data = self.meeting(b.get("session"))
        if data is None:
            return {"error": "No such meeting"}
        r = self.ui.mac_pause(b["session"])
        if not r.get("error"):
            self._state_event(cc_of(data), b["session"])
        return r

    def resume(self, b):
        """Recording again into the same meeting, Claude still off (the session listens)."""
        sdir, data = self.meeting(b.get("session"))
        if data is None:
            return {"error": "No such meeting"}
        r = self.ui.mac_resume(b["session"])
        if not r.get("error"):
            self._state_event(cc_of(data), b["session"])
        return r

    def stop(self, b):
        """The meeting ends, and the engine's own summarizer makes its card."""
        sdir, data = self.meeting(b.get("session"))
        if data is None:
            return {"error": "No such meeting"}
        r = self.ui.mac_end(b["session"])
        if not r.get("error"):
            self._state_event(cc_of(data), b["session"])     # ended: the band knows at once, not at the next poll
        return r

    def card(self, sid):
        """GET /api/lite/card?session=: {kind, title, points, gist, duration_s, note}, or {} until it's made.
        A meeting's or a call's."""
        sdir, data = self.carded(sid)
        if data is None:
            return {"error": "No such meeting"}
        card = self.ui.read_json(sdir / "card.json") or {}
        if not card:
            return {}
        return {"kind": "call" if data.get("kind") == "call" else "meeting",
                "title": card.get("title") or data.get("name") or self.ui.session_info(sid).get("title"),
                "points": card.get("points") or [], "gist": card.get("gist"), "duration_s": card.get("duration_s"),
                "note": self.ui.auto_stopped(sid)}

    def transcript(self, cc, sid=None):
        """GET /api/lite/transcript?cc=&session=: a meeting's whole transcript as Claude reads it, for
        the mod's meeting tool: `session`, else this Claude Code session's latest meeting, live or
        ended. {session, title, live, text} or {error}."""
        if not CC_ID.match(str(cc or "")):
            return {"error": "No such Claude Code session"}
        if not sid:
            mine = []
            for d in self.ui.SESSIONS.glob("*/" + self.ui.addmac.AGENT_FILE):
                data = self.ui.addmac.bound(d.parent)
                if is_lite(data) and data.get("kind") != "call" and cc_of(data) == cc:
                    mine.append((float(data.get("started") or 0), d.parent.name))
            if not mine:
                return {"error": "No meeting from this Claude Code session"}
            sid = max(mine)[1]
        sdir, data = self.meeting(sid)
        if data is None or cc_of(data) != cc:
            return {"error": "No such meeting"}
        rows = self._rows(sid)
        title = data.get("name") or self.ui.session_info(sid).get("title") or "Meeting"
        out = ["[Standby · meeting · %s · %s]" % (title, "%s-%s" % (self._stamp(sid, rows[0].get("t")), self._stamp(sid, rows[-1].get("t")))
                                                  if rows else "nothing said yet")] + self._said(sid, rows)
        return {"session": sid, "title": title, "live": not data.get("ended_at"), "text": "\n".join(out)}

    # ---------------------------------------------------------------- what's kept per meeting

    def _load(self, sdir):
        return self.ui.read_json(Path(sdir) / LITE_FILE) or {}

    def _save(self, sdir, data):
        self.ui.write_json(Path(sdir) / LITE_FILE, data)

    # ---------------------------------------------------------------- the live stream

    def subscribe(self, cc):
        q = queue.Queue(maxsize=4000)
        with self.lock:
            self.subs.append((cc, q))
        self.start()
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs = [s for s in self.subs if s[1] is not q]

    def publish(self, cc, event):
        """One event to every stream of this Claude Code session (calls publish theirs here too).
        A stream that fell this far behind is ended: the mod reconnects and catches up by cursor.
        How many streams it went to."""
        if not cc:
            return 0
        with self.lock:
            subs = [q for c, q in self.subs if c == cc]
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                self.unsubscribe(q)
                q.dropped = True
        return len(subs)

    @staticmethod
    def frames(q, heartbeat=15.0):
        """The stream's bytes: one JSON line per event, a heartbeat line while it's quiet; it ends
        when the stream fell behind (the mod reconnects and catches up with /lines)."""
        while True:
            if getattr(q, "dropped", False):
                yield (json.dumps({"resync": True}) + "\n").encode("utf-8")
                return
            try:
                event = q.get(timeout=heartbeat)
            except queue.Empty:
                yield b'{"heartbeat": true}\n'
                continue
            yield (json.dumps(event) + "\n").encode("utf-8")

    def tick(self):
        """What changed in each streamed session's live meeting: new lines, names, its state."""
        with self.lock:
            ccs = {c for c, _ in self.subs}
        for cc in ccs:
            sid, data = self._live_of(cc)
            if not sid:
                continue
            rows = self._rows(sid)
            sp, names = self._names(sid)
            ev = self.ui.mac_event(sid) or {}
            state = (bool(ev.get("live")), bool(ev.get("paused")), bool(ev.get("ended")))
            had_rows, had_names, had_state = self._seen.get(sid, (len(rows), names, state))
            for i in range(had_rows, len(rows)):
                self.publish(cc, dict(self._line(i, rows[i], sp, names), meet="line", session=sid))
            if names != had_names:
                self.publish(cc, {"meet": "rename", "session": sid, "renamed": {str(k): v for k, v in names.items()}})
            if state != had_state:
                self.publish(cc, {"meet": "state", "session": sid, "live": state[0], "paused": state[1], "ended": state[2]})
            self._seen[sid] = (len(rows), names, state)
            self.ui.maybe_title(sid, listener=True)     # named from what's been said, once there's enough

    def start(self, every=0.5):
        if self._thread is not None:
            return

        def loop():
            while True:
                try:
                    self.tick()
                except Exception as exc:
                    self.ui.log("lite: stream: %r" % exc)
                time.sleep(every)
        self._thread = threading.Thread(target=loop, name="lite-stream", daemon=True)
        self._thread.start()
