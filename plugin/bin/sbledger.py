"""A call's conversation as Standby keeps it: what the user said and what they heard of the voice.
Each turn of a call's voice is built from it, in a Claude Code of its own, so the voice's history is
exactly this, never the model's own memory (an answer they never heard never enters it).

sessions/<sid>/ledger.jsonl, append-only, one entry per line ({"t", "kind", "text", ...}):
  asked   a turn's question as it was asked (their words, or [CALL START], a [RESULT], [RESUMED]),
          written by the app once the turn is committed (an early ask that was dropped never is)
  you     what they heard of the reply: "cut" when stopped partway, "unsaid" when none of it played
  tasks   the call's tasks as they stood (tasks.txt), when they changed: written by the agent
  fact    something the voice was told: the call's opening summary, a result that came with their
          words, the session's news; written by the agent as it builds the turn that carries it

A turn's input is these entries as blocks, oldest first, then the turn's own question as the last block:
the same text it gets once the turn is committed, so each turn's input begins with the previous one's
(the prompt cache). Private as the transcript: the session's folder only, 0600, deleted with it."""
import json
import os
import re
import time

ROOT = os.path.expanduser(os.environ.get("STANDBY_ROOT", "~/.standby"))
CALL_START = "[CALL START]"
RESUMED_TAG = "[RESUMED]"
CUT = " [cut here]"
_RESULT = re.compile(r"^\[RESULT(?: ([a-z]+))?\]\s*(.*)$", re.S)     # told alone (the Mac's _lite_tell): no [END RESULT]
# Notes the Mac folded into a turn's words: facts of their own here, never their words
_FOLDED = re.compile(r"\[RESULT(?: ([a-z]+))?\]\s*.*?\n\[END RESULT\]\n?|\[SUMMARY\].*?\[END SUMMARY\]\s*|^\[(?:SESSION|TASKS)\][^\n]*\n?",
                     re.S | re.M)
_HEARD = re.compile(r'^\s*\(They (?:went on before hearing your answer\.|cut in while you were saying: ".*?")\)\s*', re.S)


def told(text):
    """A result told on its own, not with their words."""
    return bool(_RESULT.match(text or "")) and "[END RESULT]" not in (text or "")


def opening(raw):
    """What came with [CALL START] (the session, its summary, their last message before the call): a fact."""
    return (raw or "")[len(CALL_START):].strip() if (raw or "").startswith(CALL_START) else ""


def path(sid):
    return os.path.join(ROOT, "sessions", sid, "ledger.jsonl")


def append(sid, kind, text, **fields):
    """One entry, at once (the next turn reads it). Nothing for a session whose folder isn't there."""
    p = path(sid)
    if not sid or "/" in str(sid) or not os.path.isdir(os.path.dirname(p)):
        return False
    row = dict({"t": round(time.time(), 3), "kind": kind, "text": text}, **fields)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def read(sid):
    try:
        with open(path(sid), encoding="utf-8") as fh:
            out = []
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue                     # a line cut short by a crash
            return out
    except (IOError, OSError):
        return []


def folded(raw):
    """The notes the Mac put with a question ([RESULT] … [END RESULT], [SUMMARY], [SESSION] lines): [text]."""
    return [m.group(0).strip() for m in _FOLDED.finditer(raw or "")]


def words(raw):
    """A question as the voice gets it: its notes out (facts of their own), what they heard of an
    answer they cut off out (the record says that), and nothing else changed."""
    text = raw or ""
    if text.startswith(CALL_START):
        return CALL_START
    if text.startswith(RESUMED_TAG) or told(text):
        return text.strip()
    text = _FOLDED.sub("", text)
    text = _HEARD.sub("", text)
    return " ".join(text.split())


def asked_block(raw):
    """A question as a block: the same before and after it's committed."""
    w = words(raw)
    if w == CALL_START or w.startswith(RESUMED_TAG):
        return w
    if told(w):
        m = _RESULT.match(w)
        return "RESULT (%s): %s" % (m.group(1) or "done", m.group(2).strip() or "(nothing)")
    return "THEM: " + w


def block(e):
    """One entry as the voice reads it, or None (a reply they heard none of: no line)."""
    kind, text = e.get("kind"), e.get("text") or ""
    if kind == "asked":
        return asked_block(text)
    if kind == "you":
        if e.get("unsaid") or not text.strip():
            return None
        return "YOU: " + " ".join(text.split()) + (CUT if e.get("cut") else "")
    if kind == "tasks":
        return "TASKS:\n" + text.strip() if text.strip() else "TASKS: none now"
    if kind == "fact":
        return text.strip() or None
    return None


def blocks(sid, raw, entries=None):
    """The turn's input: the call so far, then its question, as text blocks."""
    out = [b for b in (block(e) for e in (read(sid) if entries is None else entries)) if b]
    return out + [asked_block(raw)]


def last(entries, kind):
    return next((e for e in reversed(entries) if e.get("kind") == kind), None)


def has_fact(entries, text):
    return any(e.get("kind") == "fact" and e.get("text") == text for e in entries)


def carried(sid, line):
    """A task's line (tasks.txt) has reached the voice: it's in one of the record's [TASKS] entries."""
    line = (line or "").strip()
    return bool(line) and any(e.get("kind") == "tasks" and line in (e.get("text") or "") for e in read(sid))
