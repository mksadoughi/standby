"""Agents behind the scenes: a call's or a meeting's summary delivered to its Claude Code
session, and the agent's voice carried from call to call.

The work happens where the agent's Claude Code runs, in plugin/bin/sbagents.py through
standby-agent, so a summary never makes a second process resume a conversation another
one holds. This file hooks that into mac/ui.py's messenger (mac/messenger.py):

  MSG["deliver"]       (agent id, text) -> True once it reached the agent (call and
                       meeting summaries); else kept in an outbox and tried again
  MSG["voice_end"], MSG["voice_forget"]: the agent's voice, call to call
"""
import base64
import json
import os
import threading
import time
from pathlib import Path

DRAIN_EVERY = 300.0   # s: summaries that couldn't go are tried again this often
FILES_KEEP = 90 * 86400   # s: a message's files are kept this long on the Mac (and where the agent runs)
KEPT_NOTE = "The voice still remembers this call: later calls build on it."


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class Agents(object):
    def __init__(self, ui, m, clock=time.time, outbox=None):
        self.ui, self.m, self.clock = ui, m, clock
        self.lock = threading.Lock()
        self.outbox = Path(outbox) if outbox else _addmac_outbox()
        self.draining = threading.Lock()
        self.held = self._held_now()      # agents with a summary waiting in the outbox

    def install(self, drain=True):
        self.ui.MSG["deliver"] = self.deliver
        self.ui.MSG["voice_end"] = self.voice_end
        self.ui.MSG["voice_forget"] = self.voice_forget
        if drain and self.outbox is not None:
            threading.Thread(target=self._drain_forever, name="agents-outbox", daemon=True).start()

    # ---------------------------------------------------------------- summaries waiting to go

    def _drain_forever(self):
        while True:
            try:
                self.drain()
                self.prune()
            except Exception:
                pass
            time.sleep(DRAIN_EVERY)

    def _held_now(self):
        out = set()
        for path in (self.outbox, self.outbox.with_name(self.outbox.name + ".draining")) if self.outbox else ():
            try:
                for line in path.read_text().splitlines():
                    try:
                        out.add(json.loads(line).get("to"))
                    except (ValueError, AttributeError):
                        continue
            except OSError:
                continue
        out.discard(None)
        return out

    def _told(self, hook, *args):
        """mac/ui.py's card hook (MSG["on_delivered"] or MSG["on_dropped"]), if it has one."""
        f = (getattr(self.ui, "MSG", None) or {}).get(hook)
        if f is not None:
            try:
                f(*args)
            except Exception:
                pass

    def drain(self):
        """Summaries kept for later (the outbox.jsonl Add your Mac wrote before delivery
        existed, and any that couldn't go since): each delivered once, the rest kept.
        The file is moved aside first, so one written meanwhile is never lost. The card
        is told either way: MSG["on_delivered"](agent, its session or the text) once it
        went, MSG["on_dropped"](agent, the same, why) if its agent has left Standby."""
        if self.outbox is None or not self.outbox.exists():
            return 0
        with self.draining:
            work = self.outbox.with_name(self.outbox.name + ".draining")
            if not work.exists():
                try:
                    os.replace(str(self.outbox), str(work))
                except OSError:
                    return 0
            keep, sent = [], 0
            for line in work.read_text().splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or not entry.get("to") or not entry.get("text"):
                    continue
                which = entry.get("session") or entry["text"]
                if not self.store_has(entry["to"]):
                    # Its agent has left Standby: nowhere to go. The card says it never arrived.
                    self._told("on_dropped", entry["to"], which, "Its agent left Standby before it could be sent")
                elif self._send_text(self.m.store.agent(entry["to"]),
                                     entry["text"]).get("delivery") in ("sent", "queued"):
                    sent += 1
                    self._told("on_delivered", entry["to"], which)
                else:
                    keep.append(dict(entry, tries=int(entry.get("tries") or 0) + 1))
            if keep:
                with open(str(self.outbox), "a") as fh:
                    for entry in keep:
                        fh.write(json.dumps(entry) + "\n")
            work.unlink()
            self.held = self._held_now()
            return sent

    def store_has(self, aid):
        a = self.m.store.agent(aid)
        return bool(a and self.m.store.workspace(a["workspace"]))

    def _keep(self, aid, text, session=None):
        """Couldn't go now: kept in the outbox, tried again later; `session`, the card's."""
        if self.outbox is None:
            return
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        entry = {"to": aid, "kind": "summary", "text": text, "at": self.clock()}
        if session:
            entry["session"] = session
        with open(str(self.outbox), "a") as fh:
            fh.write(json.dumps(entry) + "\n")
        self.held.add(aid)

    # ---------------------------------------------------------------- talking to the agent's machine

    def _where(self, machine):
        f = getattr(self.ui, "machine_name", None)
        return f(machine) if f else ("This Mac" if machine == "local" else machine)

    def _run(self, machine, args, timeout):
        """standby-agent's JSON answer on that machine, or None (unreachable, an old helper)."""
        return self.ui._agent_json(args, timeout=timeout, machine=machine)

    def _send_text(self, agent, text):
        w = self.m.store.workspace(agent["workspace"])
        args = ["deliver", "--id", agent["session"], "--text", _b64(text), "--folder", _b64(w["folder"])]
        model = self.ui.agent_model(agent["id"]) if hasattr(self.ui, "agent_model") else None
        if model:
            args += ["--model", model]            # for a turn Standby runs; a window answers on its own
        got = self._run(w["machine"], args, timeout=150)
        if got is None:
            where = self._where(w["machine"])
            return {"delivery": "failed", "error": "Standby's agent didn't answer" if w["machine"] == "local"
                    else "Couldn't reach %s" % where}
        return got

    # ---------------------------------------------------------------- an agent's voice, call to call

    def voice_end(self, aid, call, voice):
        """MSG["voice_end"]: a call with the agent ended, its voice ran as conversation `voice`:
        the agent's voice is now that one (an undecided call is remembered). The one before
        stays, for Forget to go back to; the one before that is tidied away."""
        if not voice:
            return
        with self.m.store.lock:
            a = self.m.store.agent(aid)
            if not a:
                return
            v = a.get("voice") or {}
            if v.get("session") == voice:
                return
            gone = v.get("parent")
            a["voice"] = {"session": voice, "parent": v.get("session"), "call": call, "at": self.clock(),
                          "calls": (list(v.get("calls") or []) + [call])[-50:]}
            self.m.store.save()
        if gone and gone not in (voice, v.get("session")):
            w = self.m.store.workspace(a["workspace"])
            threading.Thread(target=self._run, args=(w["machine"], ["delete-agent", "--id", gone], 60),
                             name="voice-tidy", daemon=True).start()

    def voice_forget(self, aid, call):
        """MSG["voice_forget"]: the user forgot call `call`. The latest call: the voice goes back
        to before it. An older one, with calls built on it: the voice keeps it; the line why."""
        with self.m.store.lock:
            a = self.m.store.agent(aid)
            v = (a or {}).get("voice") or {}
            if not a or call not in (v.get("calls") or []):
                return None
            if v.get("call") == call and v.get("parent"):
                a["voice"] = dict(v, session=v["parent"], parent=None, call=None,
                                  calls=[c for c in v.get("calls") or [] if c != call])
                self.m.store.save()
                return None
            if v.get("call") == call:             # its first call: the voice starts afresh next time
                a.pop("voice", None)
                self.m.store.save()
                return None
        return KEPT_NOTE

    def deliver(self, aid, text, session=None):
        """MSG["deliver"]: True once it's with the agent; else kept and tried again later,
        and the card (`session`) told when it goes (MSG["on_delivered"])."""
        if not self.store_has(aid):
            return False
        if self._send_text(self.m.store.agent(aid), text).get("delivery") in ("sent", "queued"):
            return True
        self._keep(aid, text, session)
        return False

    # ---------------------------------------------------------------- files from before: tidied away

    def messages_dir(self):
        return Path(self.m.root) / "messages"

    def prune(self, now=None):
        """Files sent with a message, from when Standby had messages: gone once older than FILES_KEEP."""
        import shutil
        now = now or self.clock()
        for folder in self.messages_dir().glob("*") if self.messages_dir().is_dir() else []:
            try:
                if not folder.is_dir():
                    continue
                if folder.name.startswith("a-"):
                    for f in folder.iterdir():
                        if now - f.stat().st_mtime > FILES_KEEP:
                            f.unlink()
                if now - folder.stat().st_mtime > FILES_KEEP:
                    shutil.rmtree(str(folder))
            except OSError:
                pass


def _addmac_outbox():
    try:
        import addmac
        return Path(addmac.OUTBOX)
    except Exception:
        return None


def install(ui, m):
    got = Agents(ui, m)
    got.install()
    return got
