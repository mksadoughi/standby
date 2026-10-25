"""Workspaces and agents: the Claude Code sessions Standby has met, by folder.

A workspace is a project folder on this Mac; its agents are Claude Code sessions in it.
Both are Standby's own records, in ~/.standby/workspaces.json: a call or a meeting from
Claude Code belongs to its session's agent, and its card comes from Standby's own session.
The page reads them (read only) for its navigator and its list of past calls and meetings,
and listens on one event stream (Hub) for what is live.
"""

import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ID = re.compile(r"^[aw]-[0-9a-f]{6,12}$")
YOU = "you"
COLORS = 8


def _now():
    return time.time()


class Subscription(queue.Queue):
    """One page's stream. `dropped` says it fell too far behind to be kept up with: it must be
    told, so it reloads, rather than left open and silent."""
    dropped = False


class Hub(object):
    """The page's event stream: every subscriber gets every event, in order."""

    def __init__(self):
        self._subs, self._lock = [], threading.Lock()

    def subscribe(self):
        q = Subscription(maxsize=4000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event, data):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait((event, data))
            except queue.Full:                    # a page that stopped reading: its stream is closed (frames)
                q.dropped = True
                self.unsubscribe(q)


def frames(sub, keepalive=15.0):
    """What the page's connection writes, as bytes, until the stream should end: each event, a
    comment line while it's quiet (so the connection stays open), and, if the subscription fell
    behind and was dropped, one `resync` and the end, so the page reconnects and loads what it
    missed (the stream never replays)."""
    while True:
        if sub.dropped:
            yield b"retry: 500\n" + sse("resync", {})        # reconnect at once, not after the browser's few seconds
            return
        try:
            event, data = sub.get(timeout=keepalive)
        except queue.Empty:
            yield b": keep-alive\n\n"
            continue
        yield sse(event, data)


def sse(event, data):
    """One Server-Sent Event, as bytes."""
    return ("event: %s\ndata: %s\n\n" % (event, json.dumps(data))).encode("utf-8")


class Store(object):
    """~/.standby/workspaces.json: the user's workspaces and agents, and what they've read."""

    def __init__(self, path):
        self.path, self.lock = Path(path), threading.RLock()
        self.data = self._load()

    def _load(self):
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        d.setdefault("workspaces", [])
        d.setdefault("agents", {})
        d.setdefault("read", {})
        d.setdefault("last_to", None)
        return d

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            os.replace(str(tmp), str(self.path))

    def workspace(self, wid):
        return next((w for w in self.data["workspaces"] if w["id"] == wid), None)

    def agent(self, aid):
        return self.data["agents"].get(aid)

    def new_id(self, prefix):
        while True:
            i = "%s-%s" % (prefix, uuid.uuid4().hex[:8])
            if not self.workspace(i) and i not in self.data["agents"]:
                return i


class Messenger(object):
    """The model and its timeline. `ui` is mac/ui.py (or a test's stand-in): its
    SESSIONS, live_session, machine_name, call_sessions, and its MSG hook table."""

    def __init__(self, root, ui=None, reader=None):
        self.root, self.ui = Path(root), ui
        self.store = Store(self.root / "workspaces.json")
        self.hub = Hub()
        self.agents_shown, self.ws_shown = {}, {}
        self.lock = threading.RLock()
        self._live_seen = None                # the live Standby session, as last seen
        self._meeting_sent = {}               # session -> the meeting event last sent for it
        self._thread = None

    # ---------------------------------------------------------------- hooks for the other parts

    def attach(self):
        """Fill mac/ui.py's MSG table: the agent lookup and the stream, for calls and meetings."""
        if self.ui is None:
            return
        table = getattr(self.ui, "MSG", None)
        if table is None:
            table = {}
            setattr(self.ui, "MSG", table)
        table["agent"] = self.agent_for_hook
        table["event"] = self.on_event

    def agent_for_hook(self, aid):
        """{id, name, session, machine, folder} or None, for a call or a meeting."""
        a = self.store.agent(aid)
        w = self.store.workspace(a["workspace"]) if a else None
        if not a or not w:
            return None
        return {"id": aid, "name": self._name(a), "session": a["session"], "machine": w["machine"],
                "folder": w["folder"]}

    def on_event(self, event, data):
        """An event from a call or a meeting, to the page as it is. ("card", {session}) means that
        call's or meeting's card changed (its summary came, Keep or Forget): the page's list of past
        calls and meetings reads it again."""
        if event == "card":
            self.hub.publish("card", {"session": (data or {}).get("session")})
            self.publish_changes()
            return
        if event == "meeting":
            self.meeting_event(data, event)
            return
        self.hub.publish(event, data)

    def meeting_event(self, data, event="meeting"):
        """A meeting started, paused, resumed, changed or ended: the event once (the
        same one from two sources is sent once)."""
        if not isinstance(data, dict):
            return
        key = json.dumps(data, sort_keys=True)
        with self.lock:
            if self._meeting_sent.get((event, data.get("session"))) == key:
                return
            self._meeting_sent[(event, data.get("session"))] = key
        self.hub.publish(event, data)
        self.publish_changes()

    # ---------------------------------------------------------------- workspaces and agents

    def _title(self, a):
        """The Claude Code session's own name (ui.cc_title), or "" while it has none."""
        f = getattr(self.ui, "cc_title", None)
        try:
            return (f(a.get("session")) if f else "") or ""
        except Exception:
            return ""

    def _name(self, a):
        return (a.get("name") or self._title(a) or "Claude Code session").strip()[:60]

    def _machine_name(self, machine):
        f = getattr(self.ui, "machine_name", None)
        return f(machine) if f else ("This Mac" if machine == "local" else machine)

    def _live_session(self):
        """The live Standby session. Asked of the app's recent answer when it has one: the real
        question runs a process listing."""
        f = getattr(self.ui, "live_session_recent", None) or getattr(self.ui, "live_session", None)
        return f() if f else None

    def _live(self, aid):
        """{call, meeting}: this agent's live Standby sessions."""
        out = {"call": None, "meeting": None}
        live = self._live_session()
        if live:
            # A call that moved to a new session (your Mac was added) is still the first one's.
            first = _json(Path(self.ui.SESSIONS) / live / "agent.json").get("call") or live \
                if getattr(self.ui, "SESSIONS", None) is not None else live
            info = self._session_agent(first)
            if info and info[0] == aid:
                out["call" if info[1] == "call" else "meeting"] = live
        return out

    def agent_json(self, aid, messages=None):
        """An agent as the page's navigator shows it: who, where, and what of it is live."""
        a = self.store.agent(aid)
        live = self._live(aid)
        return {"id": aid, "workspace": a["workspace"], "name": self._name(a), "color": a.get("color", 0),
                "session": a["session"], "sessions": list(a.get("sessions") or []),
                "status": "call" if live["call"] else "idle", "live": live,
                "title": (a.get("name") or self._title(a)).strip()[:60],   # its real name, or "": the page leaves it off
                "cc": bool(a.get("from_cc"))}                 # added from Claude Code (/standby): its >_ mark

    # What an agent with no choice falls back to when it has not run a turn yet.
    DEFAULT_MODEL = "opus"

    def defaults(self):
        """{model, voice}: the model and the voice an agent with no choice uses: its own last-run
        model comes first (its `running.model`), and the Settings voice."""
        voice = getattr(self.ui, "default_voice", None)
        try:
            voice = voice() if voice else None
        except Exception:
            voice = None
        return {"model": self.DEFAULT_MODEL, "voice": voice or ""}

    def agent_choice(self, aid):
        """{model, voice, record} for the agent, each None for "no choice": the model is the one chosen,
        else the one it last ran; the others are as chosen. Used when a call, a meeting or
        a message of Standby's own starts."""
        a = self.store.agent(aid)
        if not a:
            return {"model": None, "voice": None, "record": None}
        s = a.get("settings") or {}
        return {"model": s.get("model") or None, "voice": s.get("voice") or None, "record": s.get("record") or None}

    def workspace_json(self, w, messages=None):
        return {"id": w["id"], "name": w["name"], "machine": w["machine"],
                "machine_name": self._machine_name(w["machine"]), "folder": w["folder"],
                "agents": list(w["agents"])}

    def workspaces(self):
        """GET /api/msg/workspaces: the folders and their Claude Code sessions, read only."""
        with self.lock:
            return {"workspaces": [self.workspace_json(w) for w in self.store.data["workspaces"]],
                    "agents": [self.agent_json(aid) for w in self.store.data["workspaces"]
                               for aid in w["agents"] if self.store.agent(aid)],
                    "defaults": self.defaults()}

    def add_workspace(self, b):
        """POST /api/msg/workspaces {machine, folder, name?, agents: [{session} | {new: name}]}."""
        machine, folder = b.get("machine") or "local", (b.get("folder") or "").strip()
        keys = getattr(self.ui, "machine_keys", None)
        if keys is not None and machine not in keys():
            return {"error": "No such machine"}
        if not folder or "\n" in folder:
            return {"error": "Choose a folder"}
        name = (b.get("name") or "").strip()[:60] or os.path.basename(folder.rstrip("/")) or folder
        with self.store.lock:
            w = {"id": self.store.new_id("w"), "name": name, "machine": machine, "folder": folder,
                 "agents": []}
            self.store.data["workspaces"].append(w)
            added = []
            for spec in b.get("agents") or []:
                got = self.add_agent(dict(spec or {}, workspace=w["id"]), save=False)
                if got.get("error"):
                    self.store.data["workspaces"].remove(w)
                    for a in added:
                        self.store.data["agents"].pop(a["id"], None)
                    return got
                added.append(got["agent"])
            self.store.save()
        out = self.workspace_json(w)
        self.hub.publish("workspace", out)
        return {"workspace": out, "agents": [self.agent_json(a["id"]) for a in added]}

    def add_agent(self, b, save=True):
        """{workspace, session}: a Claude Code conversation already there, recorded as an agent."""
        w = self.store.workspace(b.get("workspace"))
        if not w:
            return {"error": "No such workspace"}
        session = b.get("session")
        if not session or not UUID.match(str(session)):
            return {"error": "No such Claude Code session"}
        with self.store.lock:
            for a in self.store.data["agents"].values():
                if a["session"] == session or session in (a.get("sessions") or []):
                    if a["workspace"] == w["id"]:
                        return {"agent": a}           # the same one again: still one agent
                    # One conversation, one agent: never a second contact for it in another workspace.
                    return {"error": "That conversation is already %s" % self._name(a)}
            used = {self.store.agent(x).get("color") for x in w["agents"] if self.store.agent(x)}
            color = next((c for c in range(COLORS) if c not in used), len(w["agents"]) % COLORS)
            a = {"id": self.store.new_id("a"), "workspace": w["id"],
                 "name": (str(b.get("name") or "").strip()[:60] or None),
                 "role": "", "color": color, "session": session, "sessions": [session], "added": _now()}
            self.store.data["agents"][a["id"]] = a
            w["agents"].append(a["id"])
            if save:
                self.store.save()
        if save:
            self.hub.publish("agent", self.agent_json(a["id"]))
            self.hub.publish("workspace", self.workspace_json(w))
        return {"agent": a}

    def agent_for_cc(self, cc, folder):
        """The agent of a Claude Code session Standby runs in (/standby): the one it already is, or a new
        one in its folder's workspace on this Mac (made if there's none), named as Claude Code names the
        session. Its id, or None when it can't be made."""
        a = self._agent_of(cc)
        if a:
            return a["id"]
        folder = os.path.expanduser(str(folder or "").strip()).rstrip("/")
        if not folder or not UUID.match(str(cc)):
            return None
        same = lambda f: os.path.realpath(os.path.expanduser(f or "")).rstrip("/") == os.path.realpath(folder)
        w = next((x for x in self.store.data["workspaces"] if x.get("machine", "local") == "local" and same(x.get("folder"))), None)
        if w is None:
            got = self.add_workspace({"machine": "local", "folder": folder, "agents": [{"session": cc}]})
        else:
            got = self.add_agent({"workspace": w["id"], "session": cc})
        if got.get("error"):
            return None
        a = self._agent_of(cc)
        if not a:
            return None
        with self.store.lock:
            a["from_cc"] = True
            self.store.save()
        self.hub.publish("agent", self.agent_json(a["id"]))
        return a["id"]

    def lite_session(self, cc, folder=None):
        """A meeting or call of a Claude Code session was just bound to it ("lite:<cc>"): that session's
        record, made the first time, by which the past list and its navigator list, group and name its
        meetings and calls (in its folder: the one Claude Code gave, else its transcript's). Its id, or None."""
        try:
            return self.agent_for_cc(cc, folder or getattr(self.ui, "_cc_folder", lambda _cc: None)(cc))
        except Exception as exc:
            getattr(self.ui, "log", print)("lite: no record for Claude Code session %s (%r)" % (str(cc)[:8], exc))
            return None

    # ---------------------------------------------------------------- what is live

    def watch_live(self):
        """Your Mac added or stopped: the meeting event (ui.mac_event), for the session
        that ended and the one that began, whenever the live session changes."""
        live = self._live_session()
        if live == self._live_seen:
            return
        was, self._live_seen = self._live_seen, live
        for sid in (was, live):
            if not sid:
                continue
            data = self._state(sid, "meeting")
            if data:
                self.meeting_event(data, "meeting")

    def _agent_of(self, session):
        for a in self.store.data["agents"].values():
            if session == a["session"] or session in (a.get("sessions") or []):
                return a
        return None

    # ---------------------------------------------------------------- cards

    def _session_agent(self, sid):
        """(agent id, "call" | "meeting", first session) for a Standby session run for an
        agent, or None. agent.json first (calls and meetings write it), else the
        conversation it ran on (meta.attach.from)."""
        sessions = getattr(self.ui, "SESSIONS", None)
        if sessions is None:
            return None
        sdir = Path(sessions) / sid
        info = _json(sdir / "agent.json")
        if info.get("call") and info["call"] != sid:
            return None                               # a later part of a call: its first session has the card
        meta = _json(sdir / "meta.json")
        if meta.get("kind") == "browser":
            return None                               # a browser session from before the browser went: no card
        kind = "call" if meta.get("kind") == "talk" else "meeting"
        aid = info.get("agent")
        if isinstance(aid, str) and aid.startswith("lite:"):
            # Standby inside Claude Code: the meeting or call is that Claude Code session's, as an agent
            a = self._agent_of(aid[len("lite:"):])
            aid = a["id"] if a else None
        if not (aid and self.store.agent(aid)):
            attach = (meta.get("attach") or {}).get("from")
            a = self._agent_of(attach) if attach else None
            aid = a["id"] if a else None
        return (aid, kind, sid) if aid else None

    def _kind(self, sid):
        """The kind of a Standby session (meta.json), "" if it can't be told."""
        sessions = getattr(self.ui, "SESSIONS", None)
        return (_json(Path(sessions) / sid / "meta.json").get("kind") or "") if sessions is not None else ""

    def _state(self, sid, kind):
        """A meeting's state now ({live, paused, ended, ...}), from Add your Mac's own account of it."""
        f = getattr(self.ui, "mac_event_recent", None) or getattr(self.ui, "mac_event", None)
        try:
            return f(sid) if f else None
        except Exception:
            return None

    def cards(self):
        sessions = getattr(self.ui, "SESSIONS", None)
        if sessions is None or not Path(sessions).is_dir():
            return []
        live = self._live_session()
        many = getattr(self.ui, "call_sessions", None)
        out = []
        for sdir in Path(sessions).iterdir():
            if not (sdir / "agent.json").exists() and not (sdir / "meta.json").exists():
                continue
            found = self._session_agent(sdir.name)
            if not found:
                continue
            aid, kind, first = found
            a = self.store.agent(aid)
            info, meta, card = _json(sdir / "agent.json"), _json(sdir / "meta.json"), _json(sdir / "card.json")
            parts = many(first) if many else [first]
            started = info.get("started") or meta.get("started") or card.get("at") or sdir.stat().st_mtime
            now = (self._state(first, kind) or {}) if kind == "meeting" else {}
            # A meeting's own account of itself decides (paused and ended are not live,
            # whatever the recorder is doing); a call, which has none, is live while its session is.
            is_live = bool(now.get("live")) if now else (bool(live) and live in parts)
            m = {"id": "c-" + first, "kind": kind, "workspace": a["workspace"], "from": aid, "to": YOU,
                 "at": started, "session": first, "sessions": parts, "live": is_live,
                 "duration_s": round(_now() - started, 1) if is_live else card.get("duration_s"),
                 "copy": bool(card.get("copy", info.get("copy"))),
                 "points": card.get("points") if card else None, "text": "", "via": "voice", "files": []}
            if card.get("gist"):                          # nothing was decided: one line on what it was about
                m["gist"] = card["gist"]
            if card.get("failed") or card.get("again"):   # the model couldn't be asked; or it's being asked again
                m["failed" if card.get("failed") else "again"] = True
            if card.get("memory") or kind != "call":      # pending | kept | forgotten, once it has ended (a call's too)
                m["memory"] = card.get("memory")
            if kind == "call" and is_live:                # paused (muted) from the orb, the band or its window
                m["paused"] = bool((getattr(self.ui, "call_muted", None) or (lambda s: None))(first))
            if kind == "meeting":
                # Paused keeps one card: not live, no points yet, and its time is what was recorded.
                m["paused"] = bool(now.get("paused"))
                m["started_at"], m["ended_at"] = card.get("started_at"), card.get("ended_at")
                if now.get("recorded_s") is not None:
                    m["duration_s"] = now["recorded_s"]
                elif m["paused"]:
                    m["duration_s"] = card.get("duration_s")
            if kind == "meeting" and str(info.get("agent") or "").startswith("lite:"):
                # a meeting from Claude Code: how its session gets it (ask, or live as it goes; before the
                # choice existed, live)
                m["pass"] = "ask" if _json(sdir / "lite.json").get("pass") == "ask" else "live"
            if kind == "meeting":
                m["title"] = card.get("title") or _read(sdir / "title.txt") or _read(sdir / "title.auto.txt") or ""
                m["sources"] = {"meeting_audio": bool(info.get("meeting_audio")), "mic": bool(info.get("mic", True))}
            if card.get("memory_note"):                   # why a kept card is pending again (it never arrived)
                m["memory_note"] = card["memory_note"]
            out.append(m)
        return out

    # ---------------------------------------------------------------- what changed, to the page

    def publish_changes(self):
        """What changed for the page's navigator: an agent's or a workspace's event, once per change."""
        with self.lock:
            for w in self.store.data["workspaces"]:
                for aid in w["agents"]:
                    if not self.store.agent(aid):
                        continue
                    a = self.agent_json(aid)
                    s = json.dumps(a, sort_keys=True)
                    if self.agents_shown.get(aid) != s:
                        self.agents_shown[aid] = s
                        self.hub.publish("agent", a)
                wj = self.workspace_json(w)
                s = json.dumps(wj, sort_keys=True)
                if self.ws_shown.get(w["id"]) != s:
                    self.ws_shown[w["id"]] = s
                    self.hub.publish("workspace", wj)

    def start(self):
        """In the background, for as long as the app runs: what is live, and the navigator's changes."""
        if getattr(self, "_thread", None) is not None:
            return

        def loop():
            while True:
                try:
                    self.watch_live()
                    self.publish_changes()
                except Exception as exc:
                    log = getattr(self.ui, "log", None)
                    if log:
                        log("messenger: %r" % exc)
                time.sleep(1.0)
        self._thread = threading.Thread(target=loop, name="messenger", daemon=True)
        self._thread.start()


def _json(path):
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""
