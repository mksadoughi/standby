#!/usr/bin/env python3
"""Talk to the Standby agent on this Mac.

    agentctl.py ensure            start the agent if needed
    agentctl.py check             diagnostics
"""

import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transport  # noqa: E402


def ask(question, out=sys.stdout, err=sys.stderr, on_progress=None, on_queued=None, session=None):
    """Ask the agent; returns the answer text. Progress lines go to `err`.
    on_queued(id) gets the question's id as soon as it is queued, for cancel().
    session: the session it is for, when that may not be the live one (an
    ended talk, continued by typing)."""
    t = transport.for_config()
    ok, msg = t.ensure_agent()
    if not ok:
        return "Agent not available: %s" % msg
    answer, problem = _ask(t, question.strip(), session, err, on_progress, on_queued)
    return (answer or problem or "(no answer)").strip()


VOICE = "[VOICE] "      # ui.VOICE_TAG: a voice mode question, whose wait is timed
POLL = 0.03             # as standby-agent's: the spool is looked at this often, which adds to every answer
GONE_FOR = 10.0         # the agent's server gone this long: the question has no answer coming
ASKED_FOR = ".s"        # beside a queued question: the session it is for (standby-agent's ASKED_FOR)


# The agent's server and this app share its spool (agent/spool): a question is a file, its
# progress and answer files beside it. Written and read here, in this process, as
# `standby-agent q --json` would: no process started on a turn's path (on a busy Mac a cold
# start of one took most of a second, every turn).
def _spool(t):
    spool = t.root / "agent" / "spool"
    if not spool.is_dir():
        spool.mkdir(parents=True, exist_ok=True)
        os.chmod(str(spool), 0o700)             # holds meeting content: owner only
    return spool


def _drop(path, text):
    """`text` at `path`, all at once: written beside it, then renamed in."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _ask(t, question, session, err, on_progress, on_queued):
    """One question through the spool: (the answer, or None; why there is none)."""
    if not question:
        return None, "nothing to ask"
    # where a voice question's wait goes. A voice question's only: other askers'
    # progress has text in every event.
    timed = bool(on_progress) and question.startswith(VOICE)
    if timed:
        on_progress({"kind": "timing", "app_spawned": time.time()})
    spool = _spool(t)
    ident = "%d-%04d" % (int(time.time() * 1000), random.randint(0, 9999))
    if session:                                 # before the question appears: the server may take it at once
        _drop(spool / (ident + ASKED_FOR), session)
    # Voice questions go to the voice's own thread, and never stop the meeting agent's listening.
    _drop(spool / (ident + (".vq" if question.startswith(VOICE) else ".q")), question)
    if on_queued:
        on_queued(ident)
    if timed:
        now = time.time()
        on_progress({"kind": "timing", "app_queued": now, "agent_queued": now})
    apath, ppath = spool / (ident + ".a"), spool / (ident + ".p")
    seen, gone = [0], None

    def pump():
        """The progress lines written since the last look: whole lines only (one being
        written is taken next time, not skipped)."""
        try:
            text = ppath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        lines = text.split("\n")[:-1]                 # the last piece: empty, or a line not yet whole
        for line in lines[seen[0]:]:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("kind") == "timing":
                ev = dict(ev, app_at=time.time())
            if on_progress:
                on_progress(dict(ev, type="progress"))
            elif ev.get("kind") == "tool" and err is not None:
                err.write("  . %s\n" % ev.get("text", ""))
                err.flush()
        seen[0] = max(seen[0], len(lines))

    while True:
        pump()
        if apath.exists():
            pump()                                    # what it wrote just before its answer: never skipped
            answer = apath.read_text(encoding="utf-8", errors="replace").rstrip()
            for f in (apath, ppath):
                try:
                    f.unlink()
                except OSError:
                    pass
            return answer, ""
        if not t.agent_alive():
            gone = gone or time.time()
            if time.time() - gone > GONE_FOR:
                return None, "the agent server is not running (standby-agent serve)"
        else:
            gone = None
        time.sleep(POLL)


def warm_voice():
    """Voice mode opened: the agent briefs its voice now, before the first words."""
    _drop(_spool(transport.for_config()) / ("%d-%04d.w" % (int(time.time() * 1000), random.randint(0, 9999))), "voice")


def cancel(qid, keep=False):
    """Stop a question asked with ask(): skipped if waiting, interrupted if running. keep: the user cut
    the voice off (not "they went on"): work its whole reply hands on still goes."""
    if not re.match(r"^[0-9]+-[0-9]+$", str(qid or "")):
        return
    spool = _spool(transport.for_config())
    if not (spool / (qid + ".a")).exists():        # answered already: nothing to stop
        (spool / (qid + ".c")).write_text("keep\n" if keep else "")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "ensure":
        ok, msg = transport.for_config().ensure_agent()
        print("standby: %s" % msg)
        return 0 if ok else 1
    if cmd == "check":
        t = transport.for_config()
        print("Claude Code runs on: %s" % t.label)
        bad = 0
        for ok, label, detail in t.check():
            bad += not ok
            print("  %s %-26s %s" % ("OK  " if ok else "FAIL", label, detail))
        return 1 if bad else 0
    print("unknown command %r" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
