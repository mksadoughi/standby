"""What Standby's own messages to a Claude Code session look like: the tags its turns begin
with, the one-time token a summary carries, and where a conversation's transcript is.

(It used to read the transcripts too, for Standby's timeline of messages; that is gone.)
It runs where Claude Code runs, with python3 3.7 or later.
"""

import glob
import hashlib
import os
import re

# How every turn Standby sends an agent begins (standby-agent's STANDBY_TAGS and the
# rest): those turns, and their answers, belong to a call or meeting card.
STANDBY_TAGS = ("[TRANSCRIPT]", "[QUESTION]", "[VOICE]", "[STANDBY RULES]", "[BRIEFING]",
                "[CHAT]", "[DEEP]", "[CALL SUMMARY]", "[MEETING SUMMARY]")
MESSAGE_TAG = "[MESSAGE]"           # a message sent from Standby's chat: shown as the user's words
SUMMARY_TAGS = ("[CALL SUMMARY]", "[MEETING SUMMARY]")
# A message handed to a Claude Code window comes as another session's message, from "Standby"
# (sbagents' postman), with a line after its tag saying how to answer. The timeline shows the
# user's words alone.
STANDBY_PEER = "Standby"
MESSAGE_NOTE = ("(From the user, through Standby. Answer in your reply here: Standby shows them "
                "your last message. Not with SendMessage: the sender has already gone.)")
SUMMARY_NOTE = "(From Standby, for your notes. No reply needed.)"
# Handed to a window, every tagged text carries a one-time token, "[MESSAGE <token>]",
# "[CALL SUMMARY <token>]": sbagents records it for that conversation and those exact words
# before it goes, and the timeline takes it as Standby's only then, once. Without one, a peer
# calling itself Standby is just another agent, its turn shown (hiding is harm too).
TOKENED = re.compile(r"^\[(%s) ([0-9a-f]{32})\]" % "|".join(
    re.escape(t[1:-1]) for t in (MESSAGE_TAG,) + STANDBY_TAGS))


def words_key(words):
    """The hash a token is bound to: the words it carries, as the timeline shows them."""
    return hashlib.sha256(str(words or "").strip().encode("utf-8")).hexdigest()


def carried(body):
    """(tag, token, words) of a tokened text, or None: the words without the note."""
    m = TOKENED.match(body or "")
    if not m:
        return None
    rest = body[m.end():].lstrip(" ")
    for note in (MESSAGE_NOTE, SUMMARY_NOTE):
        if rest.startswith(note):
            rest = rest[len(note):]
            break
    return "[%s]" % m.group(1), m.group(2), rest.strip()


def find(session, root=None):
    """The transcript of one conversation, or None."""
    root = root or os.path.expanduser("~/.claude/projects")
    got = glob.glob(os.path.join(root, "*", session + ".jsonl"))
    return got[0] if got else None
