"""Voice mode: a call's spoken conversation with Claude, in the app.

    you speak --> recorder: same mic, same parakeet, Smart Turn (turn.py)
              --> the turn is over: the agent gets it as a [VOICE] question
              --> the answer streams in; each sentence is spoken as soon as it
                  is whole, by the recorder (RecorderSpeaker), in Standby's own
                  voice (tts.py) or a Mac voice the user chose, never another
              --> the mic is the user's again

The recorder plays the voice with the mic through echo cancellation, so the
user can talk over it; where it can't (talking over off, or no echo cancelling
for the mic on speakers), it holds the mic while Claude speaks. Tapping the
orb interrupts: speech stops, the question is cancelled in the agent, and it
listens again. What the user says in voice mode stays out of the meeting
transcript; the conversation lands in the chat like typed questions.

Everything here talks to the recorder through the session's `dictate` file and
dictation.jsonl, and to the agent through the ask/cancel functions it is
given, so it is tested without a mic, a speaker or Claude.
"""

import inspect
import json
import os
import random
import re
import threading
import time
from collections import deque

import interrupt
from calltrace import trace
import sbledger  # noqa: E402  (plugin/bin, on the path calltrace sets: the call's record)


# ---------------------------------------------------------------- text to speech

_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+")
# "(notes/orion.md)", "(see ~/work/plan.txt, line 4)": sources are for reading.
_SOURCE = re.compile(r"\s*\([^()]*(?:[/\\~]|\.(?:md|txt|py|js|ts|json|ya?ml|csv|pdf|docx?|key)\b)[^()]*\)")
_CODE = re.compile(r"`([^`]*)`")
_EMPH = re.compile(r"(\*\*|__|\*)(?=\S)(.+?)(?<=\S)\1")
_BULLET = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)])[ \t]+", re.M)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.M)


def speakable(text):
    """What to say aloud for a piece of an answer: markdown, links, URLs and
    file references removed. Empty if nothing worth saying is left."""
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _SOURCE.sub("", text)
    text = _CODE.sub(r"\1", text)
    text = _EMPH.sub(r"\2", text)
    text = _BULLET.sub("", text)
    text = _HEADING.sub("", text)
    text = re.sub(r"\s+", " ", text.replace("|", " ")).strip()
    return text if sum(ch.isalnum() for ch in text) >= 1 else ""


_BOUNDARY = re.compile(r"\n+|[.!?…]+[\"'”’)\]]*(?=\s)")
_ABBREV = {"e.g", "i.e", "etc", "vs", "mr", "mrs", "ms", "dr", "st", "jr", "sr", "approx",
           "fig", "no", "inc", "ltd", "co", "a.m", "p.m", "u.s", "cf"}
# Speech ends where the written part begins: a line of --- (the [VOICE] rule)
# or a code fence.
_WRITTEN = re.compile(r"(?m)^[ \t]*(?:-{3,}|`{3})")
# Where an answer's first clause may end, to be said before its sentence is whole.
_CLAUSE = re.compile(r"[,;:](?=\s)|\s[\u2014\u2013]\s")


class SentenceStream(object):
    """An answer arriving in pieces, out as whole sentences to speak, each as
    soon as it is complete. A sentence ends at . ! ? followed by a space (so
    "5.5" and "e.g. this" do not split), or at a line break.

    Its first clause goes first: at a , ; : or dash after FIRST_WORDS
    words, before the first sentence is whole. The voice starts that much
    sooner: a long first sentence is written over ~0.5 s. Only the first: the
    rest is said while the next is written."""

    FIRST_WORDS = 4

    def __init__(self):
        self.buf = ""
        self.closed = False          # past the written part: nothing more to say
        self.started = False         # something said already: no more clause cuts

    def feed(self, text):
        if self.closed:
            return []
        self.buf += text
        return self._take(final=False)

    def flush(self):
        if self.closed:
            return []
        out = self._take(final=True)
        self.closed = True
        return out

    def _take(self, final):
        m = _WRITTEN.search(self.buf)
        if m:
            self.buf, final = self.buf[:m.start()], True
        pieces, start = [], 0
        for b in _BOUNDARY.finditer(self.buf):
            if b.group().startswith("\n") or not self._abbreviation(self.buf[start:b.start()]):
                pieces.append(self.buf[start:b.end()])
                start = b.end()
        if not self.started and not pieces and not final:
            cut = self._first_clause(self.buf)
            if cut:
                pieces.append(self.buf[:cut])
                start = cut
        self.buf = self.buf[start:]
        if final:
            pieces.append(self.buf)
            self.buf = ""
            if m:
                self.closed = True
        out = [s for s in (speakable(p) for p in pieces) if s]
        self.started = self.started or bool(out)
        return out

    def _first_clause(self, text):
        """Where the first clause ends, if it has FIRST_WORDS words before it."""
        for c in _CLAUSE.finditer(text):
            if len(re.findall(r"\w+", text[:c.start()])) >= self.FIRST_WORDS:
                return c.end()
        return 0

    @staticmethod
    def _abbreviation(before):
        word = re.findall(r"[\w.]+$", before)
        if not word:
            return False
        w = word[0].lower().rstrip(".")
        return w in _ABBREV or (len(w) == 1 and w.isalpha())


# Old and novelty voices (Bells, Bubbles, Grandpa...): installed everywhere, never what to speak in.
_NOT_FOR_TALKING = ("com.apple.speech.synthesis.voice.", "com.apple.eloquence.")
_QUALITY_ORDER = {"premium": 0, "enhanced": 1, "standard": 2}


def english_voices(voices=None, attributes=None):
    """The installed English voices fit for voice mode, as Settings lists them:
    [{"id", "name", "quality": premium|enhanced|standard, "lang"}], Premium
    first, then Enhanced, then the rest, US English first within each."""
    if voices is None:
        try:
            from native import voices as os_voices
            listed = dict(os_voices.installed())
        except ImportError:
            return []
        voices, attributes = list(listed), listed.get
    out = []
    for vid in voices:
        vid = str(vid)
        a = (attributes(vid) if attributes else None) or {}
        lang = str(a.get("VoiceLocaleIdentifier", "")).replace("_", "-")
        if not lang.startswith("en") or vid.startswith(_NOT_FOR_TALKING):
            continue
        quality = "premium" if ".premium." in vid else "enhanced" if ".enhanced." in vid else "standard"
        out.append({"id": vid, "name": str(a.get("VoiceName") or vid.rsplit(".", 1)[-1]), "quality": quality,
                    "lang": lang})
    return sorted(out, key=lambda v: (_QUALITY_ORDER[v["quality"]], v["lang"] != "en-US", v["name"]))


def voice_in_use(voices, chosen=None):
    """The voice voice mode speaks with: the one chosen in Settings (an id or
    name, config.json's voice_mode.voice) if it is installed, else the best
    installed (Premium, then Enhanced), else the
    system's own voice."""
    if chosen:
        want = str(chosen).lower()
        for v in voices:
            if want in (v["id"].lower(), v["name"].lower(), ("%s (%s)" % (v["name"], v["quality"])).lower()):
                return v
    best = voices[0] if voices and voices[0]["quality"] != "standard" else None
    return best or {"id": None, "name": "System voice", "quality": "standard", "lang": ""}


class RecorderSpeaker(object):
    """Claude's voice in a call, played by the recorder: the one voice of a call, its
    echo cancelled with the mic (VoiceIO in standby.py). Sentences go to the session's
    speak.jsonl; what is being said comes back in live.json. Never another voice instead
    (the user's rule: not this process's, nor the Mac's): when the recorder can't play it,
    or Standby's voice failed, voice_failed says why, for the orb and the window, the
    answers are shown, and what it took and never said is dropped."""

    FRESH = 2.0           # a live.json older than this is a recorder that stopped
    GONE_AFTER = 4.0      # silent this long with sentences it took: it stopped, they are dropped
    FIRST_WAIT = 3.0      # an answer before the recorder plays (a greeting, a second in): waits this long

    def __init__(self, sdir, log=lambda m: None):
        self.path, self.live = sdir / "speak.jsonl", sdir / "live.json"
        self.log = log
        self.voice = None
        self.turn = None      # the turn its sentences answer (VoiceSession sets it): the trace's
        self.seq, self._sent = None, 0
        self._texts = {}         # id: a sentence the recorder took and has not said yet
        self._done = 0           # the recorder's last word on how far it has got
        self._gone = None        # since when the recorder has not been playing, with sentences it took
        self._half = False       # it says so itself: it can't play (why: voice_failed)
        self._why = ""
        self._waited = False     # this answer waited for the recorder to play, once
        self._stops = 0          # stop()s so far: one ends that wait

    def bind(self, seq):
        """The voice mode conversation this speaks for (VoiceSession.start)."""
        self.seq = seq

    def open(self):
        pass

    def close(self):
        pass

    def begin(self):
        """A new answer (the recorder logs which voice says it)."""
        self._texts.clear()
        self._waited = False
        self._send({"begin": True})

    @property
    def getting_ready(self):
        t = self._recorder_tts()
        if not t:
            return ""
        import tts
        return tts.getting_ready(t.get("state"), t.get("downloading"), t.get("done") or 0, t.get("total") or 0)

    @property
    def preparing_pct(self):
        t = self._recorder_tts()
        if not t:
            return None
        import tts
        return tts.getting_ready_pct(t.get("state"), t.get("downloading"), t.get("done") or 0, t.get("total") or 0)

    @property
    def voice_failed(self):
        """Why Claude's voice can't be heard (the recorder says), or ""."""
        r = self._recorder()
        if r is not None:
            return str((r.get("tts") or {}).get("failed") or "")
        return self._why if self._half else ""

    @property
    def voice_retrying_since(self):
        """When Claude's voice started coming back (the recorder reopens it), or None."""
        r = self._recorder()
        since = (r.get("tts") or {}).get("retrying_since") if r is not None else None
        return since if isinstance(since, (int, float)) else None

    def _recorder_tts(self):
        r = self._recorder()
        return None if r is None else (r.get("tts") or {})

    def _recorder(self):
        """The recorder's voice status if it plays Claude's voice, else None."""
        try:
            st = json.loads(self.live.read_text())
        except (OSError, ValueError):
            return None
        v = st.get("voice") or {}
        fresh = time.time() - st.get("at", 0) < self.FRESH
        if not (v.get("speaks") and fresh):
            self._half = fresh and bool(v.get("half") or v.get("failed"))
            if self._half:
                why = str(v.get("why") or v.get("failed") or "it didn't say why")
                if why != self._why:
                    self._why = why
                    self.log("voice: Claude's voice can't play (%s): the answers are shown"
                             % why[:420].encode("ascii", "backslashreplace").decode("ascii"))
            return None
        self._gone, self._half = None, False
        self._done = max(self._done, int(v.get("done") or 0))
        for i in [i for i in list(self._texts) if i <= self._done]:
            self._texts.pop(i, None)
        return v

    def _drop(self):
        """The recorder stopped playing mid-answer: what it took and never said is dropped,
        once it is sure (it says so, or it was silent a while: a long decode is not that)."""
        if not self._texts:
            return
        self._gone = self._gone or time.time()
        if self._half or time.time() - self._gone >= self.GONE_AFTER:
            self.log("voice: %d sentence(s) the recorder took and never said: not said" % len(self._texts))
            self._texts.clear()

    def _send(self, row):
        try:
            with open(self.path, "a") as fh:
                fh.write(json.dumps(dict(row, seq=self.seq), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def say(self, text):
        """One sentence, to the recorder. Before it plays (a call's greeting, a second in), the
        first one waits for it a moment, so it is read; stop ends that wait."""
        if not self._waited:
            self._waited = True
            stops, end = self._stops, time.time() + self.FIRST_WAIT
            while self._recorder() is None and not self._half and time.time() < end and self._stops == stops:
                time.sleep(0.05)
            if self._recorder() is not None:
                self._send({"begin": True})          # the begin before it played was not read
        if self._recorder() is None and self._half:
            return                                   # it can't play: the answer is shown, not said
        self._sent += 1
        self._texts[self._sent] = text
        self._send(dict({"id": self._sent, "say": text}, **({"turn": self.turn} if self.turn is not None else {})))

    def stop(self):
        self._texts.clear()                          # stopped: nothing of it is to be said
        self._stops += 1
        self._send({"stop": True})

    @property
    def speaking(self):
        r = self._recorder()
        if r is None:
            self._drop()
            return False
        return bool(r.get("saying"))

    @property
    def busy(self):
        r = self._recorder()
        if r is None:
            self._drop()                             # until it is sure, what the recorder took is still to say
            return bool(self._texts)
        return int(r.get("done") or 0) < self._sent       # sent, and not yet said

    @property
    def current(self):
        r = self._recorder()
        return (r.get("saying") or "") if r is not None else ""

    @property
    def started_at(self):
        r = self._recorder()
        return r.get("since") if r is not None else None


_ERRORS = ("engine error:", "Engine exited", "engine write failed", "No activity from the agent",
           "Could not reach the agent")


def spoken_error(answer):
    """An answer that is an error, as voice mode gives it: one plain
    sentence to say, the error itself below the --- line, shown in the chat and
    never read aloud. Any other answer as it is."""
    text = (answer or "").strip()
    if text.startswith(_ERRORS):
        return "Sorry, I couldn't get an answer from Claude. Ask me again?\n\n---\n" + text
    return answer


# ---------------------------------------------------------------- what Claude is doing

# Tool servers and tools, as a person would say what is going on. Checked in
# order; the first match wins.
# A tool's own name (mcp__<server>__<tool>), in plain words: its verb as "-ing", then
# what it acts on ("GetIssue": "Getting issue"); a name that ends in its verb reads as
# what it is ("CodeSearch": "Code search"). No list of particular tools: the words come
# from the name, whatever the user's servers are.
_VERBS = {"get": "Getting", "list": "Listing", "search": "Searching", "find": "Finding", "read": "Reading",
          "fetch": "Fetching", "query": "Querying", "create": "Creating", "update": "Updating",
          "add": "Adding", "send": "Sending", "post": "Posting", "check": "Checking", "run": "Running",
          "open": "Opening", "view": "Looking at", "lookup": "Looking up", "write": "Writing",
          "edit": "Editing", "delete": "Deleting", "remove": "Removing", "describe": "Looking at",
          "show": "Looking at", "load": "Loading", "browse": "Browsing", "count": "Counting",
          "download": "Downloading", "upload": "Uploading", "summarize": "Summarizing",
          "analyze": "Analyzing", "compare": "Comparing", "explain": "Explaining", "review": "Reviewing"}
_NAME_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tool_words(tool):
    """An MCP tool ("mcp__github__search_code") in a few plain words ("Searching code")."""
    parts = tool.split("__")
    server = parts[1] if len(parts) > 1 else ""
    name = "__".join(parts[2:])
    server_words = re.sub(r"[-_]+", " ", re.sub(r"(?i)[-_]?mcp$", "", server)).strip() or "a tool"
    words = [w.lower() for w in _NAME_WORDS.findall(name)]
    at = next((i for i, w in enumerate(words) if w in _VERBS), None)
    if at is None:
        return "Using %s" % server_words
    if at < len(words) - 1:
        said = "%s %s" % (_VERBS[words[at]], " ".join(words[at + 1:]))
    elif len(words) == 1:                         # "search" on its own: what it searches is the server
        said = "%s %s" % (_VERBS[words[at]], server_words)
    else:
        said = " ".join(words).capitalize()
    return said if len(said) <= 40 else said[:39] + "…"


# Said while the meeting agent takes over ([DEEP]): Standby's voice, no model call.
HANDOFF_LINES = ("Let me look into that.", "Give me a moment to check.", "Let me check.",
                 "One moment, I'll look.", "Let me find out.")
_HANDOFFS = ("asking the meeting agent", "thinking it through")    # standby-agent's handoff step


def is_handoff(step):
    """The quick voice handing the question to the meeting agent: real work."""
    return (step or "").strip().lower().startswith(_HANDOFFS)


# The agent's words for Claude using a tool (standby-agent's describe_tool): an action.
_ACTIONS = ("reading ", "searching for", "finding files", "running ", "loading skill", "using ")


def is_action(step):
    """Is this progress step Claude using a tool (reading, searching, a command,
    a skill, an MCP tool), not the agent's own word (a handoff, catching up)?"""
    return (step or "").strip().lower().startswith(_ACTIONS)


def friendly_step(step):
    """A tool step ("Running curl -s https://...", "Using mcp__github__
    search_code") in a few plain words for the voice screen ("Looking it up
    online", "Searching code"). The full step stays in the chat. Empty if not
    worth showing."""
    step = (step or "").strip()
    low = step.lower()
    if not step or low.startswith("asking the meeting agent"):
        return ""
    if low.startswith("turning claude on"):
        return "Catching up on the meeting"
    if low.startswith("running "):
        cmd = low[len("running "):]
        if cmd.startswith("standby-ctx"):
            return "Checking the transcript"
        if any(w in cmd for w in ("curl", "wget", "http://", "https://")):
            return "Looking it up online"
        if cmd.startswith("git "):
            return "Checking the code"
        return "Running a command"
    if low.startswith("reading "):
        name = os.path.basename(step[len("reading "):].rstrip("/")) or "a file"
        return "Reading %s" % (name if len(name) <= 36 else name[:35] + "…")
    if low.startswith("searching for") or low.startswith("finding files"):
        return "Searching your files"
    if low.startswith("loading skill"):
        return "Using a skill"
    if low.startswith("using "):
        tool = step[len("using "):].strip()
        if tool.lower().startswith("webfetch"):
            return "Reading a web page"
        if tool.lower().startswith("websearch"):
            return "Searching the web"
        if tool.lower().startswith("mcp__"):
            return tool_words(tool)
        return "Working on it"
    return ""


# ---------------------------------------------------------------- the conversation

def takes(fn, name):
    """Does fn take a keyword argument `name`?"""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


def with_cut(question, heard):
    """A turn after one the user cut off, as the agent gets it: what
    they heard of the answer, since Claude's history has all of it as written.
    heard: None, nothing to say; "", they heard none of it. The chat shows the
    question alone."""
    if heard is None:
        return question
    if not heard:
        return "(They went on before hearing your answer.) %s" % question
    return '(They cut in while you were saying: "%s") %s' % (heard.replace('"', "'"), question)


class Question(object):
    """One question to the agent, in flight: its answer as sentences to speak.
    held: sentences ready while it is not yet the conversation (live), or
    before Claude has the floor; spoken: those given to the speaker, in order,
    with playing, the one it is saying (or said last), since play_since."""

    def __init__(self, text, hold=False, cut=None):
        self.text, self.cut = text, cut
        self.started = time.time()
        self.stream = SentenceStream()
        self.got = {"delta": False, "answer": None, "qid": None}
        self.live = not hold
        self.held = []
        self.spoken = []
        self.playing, self.play_since = -1, None
        self.floor = False       # Claude is speaking: the mic is held
        self.deep = False        # handed to the meeting agent: real work, which words don't cancel
        self.ready_at = None     # when its first sentence was ready
        self.step = ""
        self.actions, self.action = 0, ""   # tools Claude used for it so far, and the latest, in plain words
        self.cancelled = False
        self.silenced = False    # paused while it thought: it runs to its end (a hand-off still goes), unsaid
        self.unsaid = []         # what it would have said, dropped (the next turn says they never heard it)
        self.quiet = False       # dropped because the user went on: the chat keeps no trace of it
        self.worker = None
        self.lock = threading.Lock()

    def heard(self, at):
        """What the user has heard of the answer by `at`: the sentences played,
        the last one cut where it was (interrupt.heard_of)."""
        if self.playing < 0 or self.playing >= len(self.spoken):
            return ""
        last = interrupt.heard_of(self.spoken[self.playing], at - (self.play_since or at))
        return " ".join(self.spoken[:self.playing] + ([last] if last else []))


def ask_parts(started, marks, first):
    """A voice question's wait, from its asking to its first word at the app, in its
    parts, for the timing line:

      app     the app before the agent's command: its checks, any files (this Mac)
      start   the command starting, locally or over ssh, until its "queued" is back (this Mac)
      queue   in the agent's spool until the voice thread took it (the agent's machine)
      prep    the turn put together: transcript, notes (the agent's machine)
      engine  Claude Code, from having the turn to its first word (the agent's machine)
      back    the first word's way back, past what "queued" took (both: see below)

    Each part is a difference of two times on one clock, so the Mac's and an SSH host's
    clocks never need to agree. back alone mixes them, as a difference of differences:
    the app's span from "queued" to the first word, less the agent's span between the
    same two moments. "-" when a mark is missing (an older agent)."""
    m = marks or {}
    queued = m.get("agent_queued") or m.get("queued")
    if not (started and first and queued and all(m.get(k) for k in ("app_spawned", "app_queued", "taken", "sent", "word"))):
        return "-"
    parts = (("app", m["app_spawned"] - started), ("start", m["app_queued"] - m["app_spawned"]),
             ("queue", m["taken"] - queued), ("prep", m["sent"] - m["taken"]), ("engine", m["word"] - m["sent"]),
             ("back", (first - m["app_queued"]) - (m["word"] - queued)))
    return ", ".join("%s %.2fs" % (k, v) for k, v in parts)


class VoiceSession(object):
    """Voice mode for one live session, run by a thread in the app.

    ask(question, on_event, on_queued) asks the agent and returns the answer;
    it blocks, and calls on_event({"kind": "delta"|"tool", "text"}) as the
    answer streams and on_queued(id) once the agent has it. cancel(id) stops a
    question. Both come from the app, so this is tested without one.

    For a talk, the app also gives: idle_end, the seconds of nothing said
    (muted included) after which the conversation ends; busy(), true while a
    typed answer is being written, which pauses that clock; and on_end(why),
    called when the user ends it or it ends idle, not when the recording
    stopped under it or the app stopped it. dictating(), true while Standby Dictation listens: the
    words are for another app, so voice mode holds ("paused") until it is done.

    the mic is the user's while Claude thinks, and held only from its
    first word. What the user says meanwhile is judged (interrupt.classify):
    more to the question asks again with all of it, "never mind" drops it,
    "hold on" keeps the answer until they are back, "mm-hm" changes nothing.
    Talking over Claude (the recorder's barge_in, echo cancelled) ducks it
    while the recorder's words decide: "mm-hm" and it goes on, a turn and the
    recorder stops it, which stands; the words then say only whether it was
    "never mind", "hold on" or the next turn. A recorder that can't duck stops
    it on the sound alone: then a backchannel or a cough and it carries on
    from the sentence it was in. The turn after one cut short tells Claude
    what was heard: ask is called with heard= (with_cut).
    """

    GUARD = 0.35        # after Claude stops, before the mic is the user's: room echo
    STALE = 4.0         # no word from the recorder this long: it has stopped
    SLOW_ANSWER = 8.0   # no word and no step this long: the screen says it is still waiting
    DECIDE = 1.2        # paused by a sound, and no words in this long, none coming: noise, carry on
    DECIDE_MAX = 4.0    # still talking this long over a paused Claude: an interruption, whatever it is
    DUCK_MAX = 4.0      # ducked this long with no word from the recorder (it decides in 1.6 s): back to speaking
    QUIET_BEFORE = 0.1  # Claude's first word waits until the user's utterance is closed and judged
    # but never longer than this: a mic that never goes quiet (a TV near it). Words said meanwhile
    # are kept, and are the next turn's (a 6 s wait spoke over a sentence and lost it, 06:23:48-55
    # in talk-20261024-061950); another voice told apart in a call doesn't hold it at all.
    FLOOR_WAIT = 20.0
    HOLD_ON = 120.0     # "hold on": the answer waits this long for them
    MUTED_IDLE = 1800.0  # a talk muted by the user ends after this long with nothing said (unmuted: idle_end)
    REOPEN = 1.0        # Claude quiet this long mid-answer (the meeting agent at work): the mic is the user's
    CUT_WAIT = 0.4      # the voice went quiet with sentences left: this long for the barge_in row behind it
    AFTER_THIS = "Got it, after this."   # words for the next question, said while the meeting agent works

    def __init__(self, sid, sdir, ask, cancel, speaker, log=lambda m: None,
                 idle_end=None, busy=lambda: False, on_end=None, dictating=lambda: False):
        self.sid, self.sdir = sid, sdir
        self.turn = None         # the turn being answered (the recorder's number; "open" for the greeting), for the trace
        self.ledger = False      # a call: each turn kept in its record (sbledger), which the voice's next turns read
        self.on_heard = None     # on_heard(qid, heard, cut, unsaid) once a call's turn is over: its chat row
        self._state = None
        self.ask, self.cancel, self.speaker, self.log = ask, cancel, speaker, log
        self.idle_end, self.busy, self.on_end, self.dictating = idle_end, busy, on_end, dictating
        self._quiet_since = time.time()
        self.ctl = sdir / "dictate"
        self.rows = sdir / "dictation.jsonl"
        self.live = sdir / "live.json"
        self.seq = int(time.time() * 1000)
        self.state = "starting"
        self.you = ""            # what the user said: words so far, then the question
        self.claude = ""         # the sentence being spoken
        self.step = ""           # what Claude is doing while it thinks, in plain words
        self.error = None
        self.level = 0.0
        self.muted = False
        self._stop = threading.Event()
        self._interrupt = threading.Event()
        self._offset = 0
        self._seen_recorder = time.time()
        self._asking = None
        self._epoch = 0          # bumped on mute: words heard before it are not a caption
        self._pending = deque()  # rows read and not yet acted on: the next turn's, while Claude answered
        self._q = None           # the question of the turn in progress
        self._carry = ""         # a question the user went on with: the next turn adds to it
        self._cut = None         # what the user heard of an answer cut short, for the next turn
        self._skip = set()       # recorder turns that were "mm-hm", "never mind", "hold on": no question
        self._later = set()      # recorder turns said while the meeting agent worked: the next questions
        self._st = {}            # the recorder's live status, as last read
        self._you_at = 0.0       # when the user's mic was last in an utterance
        self._ask_heard = takes(ask, "heard")    # an older app is not told what was heard
        self._cancel_quiet = takes(cancel, "quiet")
        self._cancel_said = takes(cancel, "said")      # an app that keeps what was said of an answer dropped quietly
        self.holding = False     # "hold on": an answer waits for them
        self._early = None       # a Question asked on likely-final words, held until its turn ends
        self._early_turn = {"asked": 0, "dropped": []}     # this turn's early asks, for its timing line
        self._line = random.randrange(len(HANDOFF_LINES))     # the handoff line said last
        self._told = deque(maxlen=3)  # lines to say when nobody is talking (announce): news, not answers
        self._opening = None     # a first turn of Claude's own, before the user says anything (open_with)

    # -- control, from the app's request handlers ---------------------------
    def start(self):
        try:
            self._offset = self.rows.stat().st_size
        except OSError:
            self._offset = 0
        if hasattr(self.speaker, "bind"):           # the recorder plays it: tell it which conversation
            self.speaker.bind(self.seq)
        if hasattr(self.speaker, "open"):           # Standby's voice loads now, while the user speaks
            self.speaker.open()
        self._write("voice")
        self.state = "listening"
        from native import qos
        self._held = qos.hold("Standby: a call's voice")      # no App Nap for the app while a call is live
        threading.Thread(target=self._run, name="voice-%s" % self.sid, daemon=True).start()
        self.log("voice %d: on" % self.seq)

    def stop(self, why=None, by_app=False):
        """by_app: the app is stopping it (the recording, or the app itself is
        ending), so on_end is not told: there is nothing more to end."""
        if self._stop.is_set():
            return
        self._stop.set()
        self._drop_early("voice mode off")
        self.speaker.stop()
        if hasattr(self.speaker, "close"):          # and goes, all of it
            try:
                self.speaker.close()
            except Exception as exc:
                self.log("voice: closing the voice: %r" % exc)
        self._write("off")
        from native import qos
        qos.release(getattr(self, "_held", None))
        self.state, self.you, self.claude, self.step = "off", "", "", ""     # the orb says nothing more
        if why:
            self.error = why
        self.log("voice %d: off%s" % (self.seq, " (%s)" % why if why else ""))
        self._trace("end", why=why or None)
        if self.on_end is not None and not by_app and why != "recording stopped":
            try:
                self.on_end(why)
            except Exception as exc:
                self.log("voice: ending: %r" % exc)

    ANNOUNCE_STALE = 2.0     # news not said within this long is a moment gone: dropped, never said late

    def announce(self, text, dropped=None):
        """Say `text` when the conversation is quiet: listening, nobody speaking, nothing
        asked. For news from outside the turn, such as work handed on that has finished:
        it never talks over the user, and a newer line pushes out the oldest of three. Not
        said within ANNOUNCE_STALE (they spoke, a turn ran), it's dropped and dropped() is told."""
        text = " ".join(str(text or "").split())
        if text:
            self._told.append((text, time.time(), dropped))

    def _fresh_told(self):
        """The news still worth saying now: what waited past ANNOUNCE_STALE is dropped (its teller told)."""
        while self._told and time.time() - self._told[0][1] > self.ANNOUNCE_STALE:
            text, _at, dropped = self._told.popleft()
            self.log("voice %d: news not said in time, dropped" % self.seq)
            if dropped is not None:
                try:
                    dropped()
                except Exception:
                    pass
        return bool(self._told)

    def open_with(self, question):
        """Claude speaks first: `question` (words for the agent, never shown) is asked as the
        conversation's first turn, once it is quiet; dropped if the user speaks first."""
        self._opening = question

    def interrupt(self):
        """Tap on the orb: stop talking or thinking, and listen."""
        if self.state in ("thinking", "speaking") or self._q is not None:
            self._interrupt.set()
            self.speaker.stop()

    def mute(self, on):
        self.muted = bool(on)
        self._epoch += 1
        if self.muted:
            self._drop_early("muted")
        if self._q is not None:                          # mid-turn: the turn's loop sets the mic
            # A pause while it answers aloud (a sentence playing, queued, or the gap between two:
            # it has the floor) stops the voice now, not at the answer's end. Deep work handed off
            # gives the floor back once quiet, so a pause then leaves that work running.
            q = self._q
            if self.muted and (self.state == "speaking" or self.speaker.speaking or self.speaker.busy
                               or getattr(q, "floor", False)):
                self.interrupt()
            elif self.muted and self.state == "thinking" and not q.deep:
                # Still working out its reply to words said before the pause: the turn runs to its end
                # (work it hands on still goes), but what it would say is never said, nor after Resume
                # (it would answer a moment that's gone); the next turn says they never heard it.
                with q.lock:
                    q.silenced = True
                    q.unsaid.extend(q.held)
                    q.held = []
            return
        if self.muted and (self.state == "speaking" or self.speaker.speaking or self.speaker.busy):
            # A line of its own outside a turn (news from work handed on, _aside): silenced now and
            # dropped; _aside then sees it done and leaves the session paused.
            self.speaker.stop()
            return
        if self.state in ("listening", "muted"):         # "paused" for dictation sets its own on resuming
            self._write("hold" if self.muted else "voice")
            self.state = "muted" if self.muted else "listening"
            self.you = ""

    def status(self):
        st = {"state": self.state, "level": round(self.level, 3), "you": self.you,
              "claude": self.claude, "step": self.step, "muted": self.muted}
        if self.error:
            st["error"] = self.error
        if self.holding:
            st["holding"] = True                     # "hold on": the answer waits for them
        why = getattr(self.speaker, "voice_failed", "") or ""
        if why:                                      # never another voice instead: said on the orb and the window
            st["notice"] = "Standby's voice isn't working (%s). Answers are shown here." % why
        else:
            since = getattr(self.speaker, "voice_retrying_since", None)
            if since:                                # coming back: the band says so, and for how long
                st["retrying_since"] = since
        q = self._q                                  # "Taking actions": work, not latency
        st["actions"], st["action"] = (q.actions, q.action) if q is not None else (0, "")
        ready = getattr(self.speaker, "getting_ready", "")
        if ready:                                    # Standby's voice is first downloading
            st["preparing"] = ready
            st["preparing_pct"] = getattr(self.speaker, "preparing_pct", None)     # 0-99, None: not known
        return st

    # -- the loop ------------------------------------------------------------
    def _write(self, word):
        try:
            self.ctl.write_text("%s %d\n" % (word, self.seq))
        except OSError:
            pass

    def _pull(self):
        """New rows from the recorder, after any not yet acted on."""
        self._pending.extend(self._read_rows())

    def _caption(self, heard, st):
        """What the orb shows while listening: the question the user went on from, the
        turn's words so far, and the words still being said. The turn's words are the
        recorder's own (its status), which start afresh when its turn does: after
        an answer, at a barge-in. Words said while Claude worked that it let go then
        stayed on the orb as if just said, up to a minute, and were never asked (the
        user, 2026-10-12). While it still holds, what it has is from before. A recorder
        that doesn't say: the rows heard here, as before."""
        vst = st.get("voice") or {}
        if "heard" not in vst:
            said = heard + ([st["partial"]] if st.get("partial") else [])
        elif vst.get("mode") == "voice":
            said = [w for w in (vst["heard"], st.get("partial")) if w]
        else:
            said = []
        return " ".join(([self._carry] if self._carry else []) + said).strip()

    def _joined(self, text):
        """A turn's words after the question the user went on from."""
        return " ".join(w for w in (self._carry, (text or "").strip()) if w)

    def _read_rows(self):
        try:
            with open(self.rows, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1          # whole lines only
        self._offset += end
        out = []
        for line in data[:end].splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("seq") == self.seq:
                out.append(row)
        return out

    def _recorder(self):
        """The recorder's live status: level, words still being spoken."""
        try:
            st = json.loads(self.live.read_text())
        except (OSError, ValueError):
            st = {}
        if st and time.time() - st.get("at", 0) < self.STALE:
            self._seen_recorder = time.time()
        self._st = st
        return st

    def user_speaking(self):
        """The user is in an utterance now (the recorder's word, before any of it is decoded)."""
        return self._you_speaking()

    def _you_speaking(self):
        """The user's mic is in an utterance, as last read. A recorder that does
        not say (an older one) never makes Claude wait: its "speaking" is any
        channel's, and in a meeting someone always is."""
        st = self._st or {}
        if time.time() - st.get("at", 0) > 1.0:
            return False
        return bool((st.get("voice") or {}).get("you_speaking"))

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value != self._state:                     # the floor: who has it now, in the trace
            self._state = value
            trace(self.sid, "floor", self.turn, who=value)

    def _record(self, q, end):
        """A call's turn, once it's over, in the call's record: what was asked, then what they heard of the
        reply ([cut here] where it stopped), so the voice's next turns know exactly that. A question they
        went on from before hearing anything isn't kept: it's asked again with the rest of their words."""
        if not self.ledger:
            return
        # Answered: every sentence it got to (a voice that stopped early: up to there); else where it was cut
        heard = (" ".join(q.spoken[:max(q.playing, 0) + 1]) if end == "answered" and q.playing >= 0
                 else q.heard(time.time())).strip()
        if end == "more" and not heard:
            return
        cut = end != "answered" and bool(heard)
        sbledger.append(self.sid, "asked", q.text, turn=self.turn)
        sbledger.append(self.sid, "you", heard, turn=self.turn, cut=cut, unsaid=not heard)
        if self.on_heard is not None:
            try:
                self.on_heard(q.got.get("qid"), heard, cut, not heard)
            except Exception as exc:
                self.log("voice: marking the reply's row: %r" % exc)

    def _trace(self, ev, **fields):
        trace(self.sid, ev, self.turn, **fields)

    def _set_turn(self, turn):
        self.turn = turn
        if hasattr(self.speaker, "turn"):
            self.speaker.turn = turn                 # its sentences carry it to the recorder

    def _run(self):
        from native import qos
        qos.interactive()                            # the conversation, ahead of a busy Mac's other work
        heard, epoch = [], self._epoch
        while not self._stop.is_set():
            if epoch != self._epoch:
                heard, epoch = [], self._epoch
            self._pull()
            while self._pending:
                row = self._pending.popleft()
                if row.get("done"):
                    self.stop("recording stopped")
                    return
                if row.get("likely_turn"):
                    if interrupt.is_words(row.get("text", "")):      # "mm-hm" asks nothing
                        self._ask_early(row)
                elif row.get("end_turn"):
                    heard = []
                    self._later.discard(row.get("turn"))
                    if row.get("turn") in self._skip:         # "mm-hm" over Claude: no question
                        self._skip.discard(row.get("turn"))
                        self._drop_early("mm-hm")
                        trace(self.sid, "turn_done", row.get("turn"), outcome="skipped", question=row.get("text", ""))
                        continue
                    question, self._carry = self._joined(row.get("text", "")), ""
                    self._set_turn(row.get("turn"))
                    if interrupt.classify(question) == interrupt.NOISE:    # "Um.": no question
                        self.log("voice %d: only a sound, nothing asked" % self.seq)
                        self._trace("turn_done", outcome="noise", question=question)
                        self._drop_early("only a sound")
                        continue
                    self._converse(question, row.get("spoke_until"), ask=self._take_early(question))
                    if self._stop.is_set():
                        return
                elif row.get("text") and not row.get("likely_turn"):
                    if row.get("turn") in self._skip and interrupt.is_words(row["text"]):
                        self._skip.discard(row.get("turn"))   # they went on to say something
                    heard.append(row["text"])
            st = self._recorder()
            if time.time() - self._seen_recorder > self.STALE:
                self.stop("recording stopped")
                return
            self._follow_dictation()
            if self.state == "listening":
                self.level = float(st.get("level", 0.0))
                self.you = self._caption(heard, st)
            else:
                self.level = 0.0
            if self._opening is not None and (heard or st.get("speaking") or self._you_speaking()):
                self._opening = None                      # they spoke first: no greeting
            if self._opening is not None and self.state == "listening" and not self._pending and self._q is None:
                opening, self._opening = self._opening, None
                self._set_turn("open")
                self._converse(opening, shown="")
                if self._stop.is_set():
                    return
            if self._fresh_told() and self.state == "listening" and not heard and not st.get("speaking") \
                    and not self._you_speaking() and not self._pending and self._q is None:
                self._set_turn("aside")
                self._aside(self._told.popleft()[0])      # news, said into a quiet moment
                if self._stop.is_set():
                    return
            # Speech counts only when it is for Claude: talking while muted is not.
            if self._idle_over(self.state == "listening" and (bool(heard) or bool(st.get("speaking")))):
                self.stop("nothing said for %d minutes" % round(self.idle_end / 60.0))
                return
            time.sleep(0.05)

    def _aside(self, text):
        """A line of Standby's own, said with the floor (no model call), then the
        mic is the user's again."""
        self._trace("aside", text=text)
        self._write("floor")
        self.state, self.claude, self.you = "speaking", text, ""
        if hasattr(self.speaker, "begin"):
            self.speaker.begin()
        self.speaker.say(text)
        end = time.time() + 5.0 + 0.15 * len(text)
        time.sleep(0.05)
        while self.speaker.busy and time.time() < end and not self._stop.is_set():
            time.sleep(0.04)
        # Not a conversation yet: only an answer makes one live (a "no" after it means
        # the room was talking, and the next doubtful turn is asked about again).
        if self._stop.is_set():
            return
        time.sleep(self.GUARD)
        self.claude = ""
        self._quiet_since = time.time()
        if self.muted:
            self._write("hold")
            self.state = "muted"
        else:
            self._write("voice")
            self.state = "listening"

    # -- asking on likely-final words ------------------------------------
    def _ask_early(self, row):
        """The recorder heard words that would end the turn if they are the last:
        ask now, ~0.25 s before the turn is surely over, and keep the answer until
        it is (held). Only while listening, once per words: the same words again
        change nothing, other words replace the early question."""
        text = self._joined(row.get("text", ""))
        if not text or self.state != "listening":
            return
        if self._early is not None:
            if self._early.text == text:
                return
            self._drop_early("other words")
        self._early = self._start_ask(text, hold=True, turn=row.get("turn"))
        trace(self.sid, "early", row.get("turn"), step="asked", words=text)
        self.early_asks += 1
        self._early_turn["asked"] += 1
        self.log("voice %d: asked early, on likely-final words" % self.seq)

    def _take_early(self, question):
        """The turn is over: the early question if it was for exactly this one (its
        words after any carried), else None (they went on: it is dropped, and the
        turn asked as ever)."""
        q, self._early = self._early, None
        if q is None:
            return None
        if q.text == question.strip():
            self._trace("early", step="taken", words=question.strip())
            return q
        self._trace("early", step="dropped", why="went on", words=q.text)
        self._cancel_ask(q, quiet=True)
        self.early_dropped += 1
        self._early_turn["dropped"].append("went on")
        self.log("voice %d: they went on: the early question is dropped" % self.seq)
        return None

    def _drop_early(self, why="dropped"):
        q, self._early = self._early, None
        if q is not None:
            self._trace("early", step="dropped", why=why, words=q.text)
            self._cancel_ask(q, quiet=True)
            self.early_dropped += 1
            self._early_turn["dropped"].append(why)
            self.log("voice %d: the early question is dropped (%s)" % (self.seq, why))

    # Counted, for the log and the budget. No cap (the user's call, 10/25): speculation is
    # eager, resumed and committed as Deepgram Flux's EagerEndOfTurn / TurnResumed /
    # EndOfTurn are. The cap left most of a call's turns unasked early, and their end
    # cost 1.5-3.4 s (talk-20261024-101030, -232831).
    early_asks = early_dropped = 0

    def _follow_dictation(self):
        """Standby Dictation listening: its words are for another app, so hold
        the mic ("paused"), and listen again once it is done."""
        if self.state == "listening" and self.dictating():
            self._write("hold")
            self.state, self.you = "paused", ""
        elif self.state == "paused" and not self.dictating():
            self._epoch += 1                              # what it heard meanwhile is no turn
            self._write("hold" if self.muted else "voice")
            self.state = "muted" if self.muted else "listening"

    def _idle_over(self, speech):
        """A talk ends after idle_end seconds with nothing said (MUTED_IDLE while muted).
        Speech, dictating elsewhere, or a typed answer being written start the
        clock again."""
        now = time.time()
        if speech or self.state == "paused" or self.busy():
            self._quiet_since = now
        # muted is a pause the user chose (typing to Claude Code meanwhile): a longer while
        limit = max(self.idle_end, self.MUTED_IDLE) if self.idle_end is not None and self.muted else self.idle_end
        return limit is not None and now - self._quiet_since >= limit

    def _timing(self, seen, spoke_until, got, early=None, started=None):
        """Where the wait went, one line per turn: the numbers to tune against.
        early: how long before the turn was over its question was asked;
        its first word then counts from the turn's end, at 0 if it came before.
        started: when its question was asked, for the ask's own parts (ask_parts).
        Numbers only, ASCII: never the words said."""
        def gap(a, b):
            return "%.2fs" % max(0.0, b - a) if a and b else "-"
        first, audio = got.get("first"), got.get("audio")
        self.log("voice timing: you stopped > turn over %s > Claude's first word %s > speaking %s"
                 " | %s in all%s | ask: %s | early: %s"
                 % (gap(spoke_until, seen), gap(seen, first), gap(max(first or 0, seen), audio),
                    gap(spoke_until or seen, audio),
                    " (asked %.2fs early)" % early if early is not None else "",
                    ask_parts(started, got.get("marks") or {}, first), self._early_said(early)))
        m = got.get("marks") or {}
        ms = lambda a, b: int(max(0.0, b - a) * 1000) if a and b else None
        queued = m.get("agent_queued") or m.get("queued")
        self._trace("timing", q=got.get("qid"),
                    turn_over_ms=ms(spoke_until, seen), first_word_ms=ms(seen, first),
                    voice_ms=ms(max(first or 0, seen), audio), total_ms=ms(spoke_until or seen, audio),
                    early_ms=int(early * 1000) if early is not None else None,
                    parts={"app": ms(started, m.get("app_spawned")), "start": ms(m.get("app_spawned"), m.get("app_queued")),
                           "queue": ms(queued, m.get("taken")), "prep": ms(m.get("taken"), m.get("sent")),
                           "engine": ms(m.get("sent"), m.get("word"))})
        self._early_turn = {"asked": 0, "dropped": []}

    def _early_said(self, early):
        """This turn's early asks: how many, which were dropped and why, and
        whether one was taken. "none" when none was asked."""
        t = self._early_turn
        if not t["asked"]:
            return "none"
        out = ["%d asked" % t["asked"]]
        if t["dropped"]:
            out.append("%d dropped (%s)" % (len(t["dropped"]), ", ".join(t["dropped"])))
        out.append("taken %.2fs early" % early if early is not None else "none taken")
        return ", ".join(out)

    def _start_ask(self, text, hold=False, turn=None):
        """Ask the agent `text`, in the background: a Question. Its sentences
        are spoken as they come, or, with hold, kept until _converse takes it
        (a question asked before the turn is surely over). A turn after one the
        user cut off carries what they heard (with_cut)."""
        q = Question(text, hold, cut=self._cut)
        q.turn = self.turn if turn is None else turn
        trace(self.sid, "ask", q.turn, words=text, early=bool(hold), cut=self._cut)

        def on_event(ev):
            if q.cancelled or (q.live and self._interrupt.is_set()):
                return                               # interrupted: never speak again
            if ev.get("kind") == "timing":           # where the wait went (agentctl, the agent)
                q.got.setdefault("marks", {}).update((k, v) for k, v in ev.items() if k != "kind")
                return
            if ev.get("kind") == "delta" and ev.get("text"):
                q.got.setdefault("first", time.time())
                q.got["delta"] = True
                q.got["fed"] = q.got.get("fed", "") + ev["text"]
                self._say(q, q.stream.feed(ev["text"]))
            elif ev.get("kind") == "tool" and ev.get("text"):
                if is_handoff(ev["text"]):
                    self._handoff(q)
                said = friendly_step(ev["text"])
                if is_action(ev["text"]):
                    q.actions += 1
                    q.action = said                  # "" when there is no plain name: not the last one
                if said:
                    q.step = said
                    if q.live:
                        self.step = said

        def on_queued(qid):
            q.got["qid"] = qid
            trace(self.sid, "ask_q", q.turn, q=qid)
            if q.cancelled:                          # dropped before the agent even had it
                self._cancel_ask(q)

        def work():
            try:
                extra = {"heard": q.cut} if q.cut is not None and self._ask_heard else {}
                q.got["answer"] = self.ask(text, on_event, on_queued, **extra)
            except Exception as exc:
                q.got["answer"] = "Sorry, I could not reach Claude (%s)." % exc.__class__.__name__

        if self._asking is not None:                 # the last, interrupted question
            self._asking.join(timeout=15)
        def interactive_work():
            from native import qos
            qos.interactive()                        # the question and its answer's words: a call's
            work()
        q.worker = threading.Thread(target=interactive_work, name="voice-ask", daemon=True)
        self._asking = q.worker
        q.worker.start()
        return q

    def _say(self, q, sentences):
        """Sentences of q's answer: to the speaker, or kept while q is held or
        Claude may not start yet (_give)."""
        with q.lock:
            if q.cancelled or not sentences:
                return
            if q.silenced:
                q.unsaid.extend(sentences)
                return
            q.held.extend(sentences)
            self._give(q)

    def _take_floor(self, q):
        """What q kept, said now if it may be (the turn's loop, once it has
        judged what the user said)."""
        with q.lock:
            self._give(q)

    def _give(self, q):
        """With q.lock held: what q kept, to the speaker, if it may be said. The
        first of it takes the floor, and the mic is held from Claude's first
        word, no sooner: not while the user is still talking, nor before the
        turn's loop has judged words of theirs it has not seen yet."""
        if q.cancelled or not q.live or not q.held:
            return
        if not q.floor:
            if self._pending or self._unread() or self._waits_for_user(q):
                return
            q.floor = True
            self._write("floor")
        held, q.held = q.held, []
        q.spoken.extend(held)
        for sentence in held:
            self.speaker.say(sentence)

    def _nothing_said(self, since, now):
        """Talked over at `since`, and DECIDE has passed since then and since the user last
        spoke, with no row unread: no words are coming (a closed utterance's row comes
        ~0.6 s after it, with its decode)."""
        return (now - max(since, self._you_at) >= self.DECIDE and not self._you_speaking()
                and not self._unread())

    def _unread(self):
        """Rows from the recorder not read yet."""
        try:
            return self.rows.stat().st_size > self._offset
        except OSError:
            return False

    def _waits_for_user(self, q):
        """Claude's first word waits while the user is still saying something
        (it may be more to the question), but not for ever."""
        if self.muted or self.dictating():
            return False
        now = time.time()
        if q.ready_at is None:
            q.ready_at = now
        if now - q.ready_at >= self.FLOOR_WAIT:
            return False
        return self._you_speaking() or now - self._you_at < self.QUIET_BEFORE

    def _release(self, q):
        """q is the conversation now: what it kept is said, and the rest as it comes."""
        with q.lock:
            q.live = True
        self.step = q.step
        self._take_floor(q)

    def _handoff(self, q):
        """Real work begins (the meeting agent answers): one short line, so the
        wait is not silence, unless something is being said already. Never the
        same line twice in a row."""
        q.deep = True
        with q.lock:
            if q.spoken or q.held:
                return
        self._line = (self._line + 1) % len(HANDOFF_LINES)
        self._say(q, [HANDOFF_LINES[self._line]])

    def _cancel_ask(self, q, quiet=False):
        """Stop q: nothing more of it is said, and the agent drops it if it is still on it.
        quiet: the user went on (more to the question, or an early question that was not
        their turn after all): the next question has all of it, so the chat keeps none of
        it but what the voice had said of the answer (said=), which the user heard."""
        said = q.heard(time.time())
        with q.lock:
            q.cancelled, q.held = True, []
            q.quiet = q.quiet or quiet
        if q.live:
            self.speaker.stop()
        alive = q.worker is not None and q.worker.is_alive()
        # A quiet drop goes to the app even once answered: its question and answer leave the chat.
        if q.got["qid"] and (alive or (q.quiet and self._cancel_quiet)):
            try:
                if q.quiet and self._cancel_quiet and said and self._cancel_said:
                    self.cancel(q.got["qid"], quiet=True, said=said)
                elif q.quiet and self._cancel_quiet:
                    self.cancel(q.got["qid"], quiet=True)
                else:
                    self.cancel(q.got["qid"])
            except Exception as exc:
                self.log("voice: cancel failed: %r" % exc)

    def _resume(self, q):
        """Carry on after a pause: from the start of the sentence it was cut in,
        then what came meanwhile."""
        with q.lock:
            q.live = True
            if q.floor:
                at = max(q.playing, 0)
                q.held = q.spoken[at:] + q.held
                q.spoken, q.playing = q.spoken[:at], at - 1
                q.floor, q.ready_at = False, None    # taken again, and the mic held again
        self._take_floor(q)

    def _follow_speaker(self, q):
        """Which of q's sentences the speaker is saying, for what was heard."""
        now = self.speaker.current
        for i in range(max(q.playing, 0), len(q.spoken)):
            if q.spoken[i] == now:
                if i != q.playing:
                    q.playing, q.play_since = i, self.speaker.started_at or time.time()
                return

    def _gap(self, q, gaps, gap, last, now, phase):
        """The silences in an answer, as it is spoken: each one the seconds spent
        waiting for Claude's next words and for the voice to say them. Gathered
        into gaps, one per silence, and logged at the end of the turn."""
        if phase == "speaking" and q.playing >= 0 and not self.speaker.speaking:
            gap = gap or [0.0, 0.0]
            gap[1 if self.speaker.busy else 0] += now - last      # words are in: the voice is behind
        elif gap is not None and self.speaker.speaking:
            gaps.append(gap)
            gap = None
        return gap, now

    @staticmethod
    def _asked(q):
        """The answer has put a question to the user, and they have heard it (a sentence
        played so far ends with "?"): a "yes" or an "mm-hm" now answers it, a turn."""
        return any(s.rstrip().rstrip("\"')]}»”’").endswith("?") for s in q.spoken[:q.playing + 1])

    def _judge(self, q, phase, row):
        """A row from the recorder during a turn, and what it means for it:
        None (nothing changes), "pause", "wait", "resume", "later" (the next
        question, asked once this answer is done: the row is kept for it), or
        the turn's end: "cancel", "more" (to the question), "cut" (an
        interruption). The rows of a next turn ("more", "cut") go back for the
        listening loop."""
        if row.get("turn") in self._later:
            return "later"
        if row.get("overlap") == "duck":             # talked over: the voice ducks while the words decide
            return "duck" if q.floor and phase == "speaking" else None
        if row.get("overlap") == "resume":           # it was nothing: the voice went on where it paused
            return "unduck" if phase == "ducked" else None
        if row.get("barge_in"):
            if not q.floor or phase not in ("speaking", "ducked"):
                return None
            if self.muted or self.dictating():
                return "resume"
            # Stopped on its words (overlap, "after"): that stands, the words only say how.
            # Stopped on the sound alone (an engine that can't duck): the words here decide.
            return "stop" if row.get("after") is not None else "pause"
        if row.get("end_turn"):
            self._skip.discard(row.get("turn"))      # a turn judged here, word by word: no question
            return None
        if not row.get("text"):
            return None
        if row.get("likely_turn"):
            # Likely-final words, before their utterance closes: more to the question,
            # or an interruption, a moment sooner. Anything else waits for the words' own row.
            if self.muted or self.dictating():
                return None
            kind = interrupt.classify(row["text"])
            if kind == interrupt.SHORT and phase not in ("paused", "stopped", "ducked"):
                kind = interrupt.REAL
            if kind != interrupt.REAL:
                return None
            self._skip.discard(row.get("turn"))
            self._pending.appendleft(row)
            # Some of the answer heard already (it went quiet mid-answer, the mic the user's):
            # an interruption, as for the words' own row, never "more": that drops what was said.
            return "cut" if q.floor or q.playing >= 0 else "more"
        if self.muted or self.dictating():           # not for Claude
            self._skip.add(row.get("turn"))
            return {"paused": "resume", "stopped": "resume"}.get(phase)
        kind = interrupt.classify(row["text"])
        over = row.get("over") or phase in ("paused", "stopped", "ducked")     # said over Claude's voice
        if kind == interrupt.SHORT:                  # one stray word: over Claude a misheard "mm-hm"
            kind = interrupt.BACKCHANNEL if over else interrupt.REAL
        if kind in (interrupt.BACKCHANNEL, interrupt.RESUME) and q.floor and self._asked(q) \
                and interrupt.classify(row["text"]) != interrupt.RESUME:
            # "Yes." said to a question the voice asked: the answer to it, a turn, never a
            # backchannel (23:29:39 in talk-20261024-232831 was dropped, and it talked on)
            kind = interrupt.REAL
        if kind == interrupt.REAL:
            self._skip.discard(row.get("turn"))
            if q.deep and not q.floor and phase == "thinking":     # the meeting agent at work: kept going
                self._later.add(row.get("turn"))
                return "later"
            self._pending.appendleft(row)
            return "cut" if q.floor or q.playing >= 0 else "more"
        self._skip.add(row.get("turn"))
        if kind == interrupt.CANCEL:
            return "cancel"
        if kind == interrupt.WAIT:
            return None if phase == "waiting" else "wait"
        # Not an interruption after all ("continue", "mm-hm", a cough): it goes on from the
        # sentence it was in, whoever stopped it. Never silence: "Continue, continue." over a
        # stopped punchline ended the turn with nothing asked (10:22:10, talk-20261024-101030).
        if phase in ("paused", "stopped") or (phase == "waiting" and kind in (interrupt.RESUME, interrupt.BACKCHANNEL)):
            return "resume"
        return None

    def _converse(self, question, spoke_until=None, ask=None, shown=None):
        """One turn: `question` answered aloud, or not (see the class). ask: a
        Question already asked for it (held), which this takes instead of
        asking again. shown: the caption instead of the question (a turn of Claude's own)."""
        question = question.strip()
        if not question:
            return
        seen = time.time()
        mic = not (self.muted or self.dictating())   # the mic stays the user's while Claude thinks
        if not mic:
            self._write("hold")
        self._interrupt.clear()
        self.state, self.you, self.claude, self.step = "thinking", question if shown is None else shown, "", ""
        if hasattr(self.speaker, "begin"):           # the voice for this answer, all of it
            self.speaker.begin()
        q = ask if ask is not None else self._start_ask(question)
        self._q, self._cut = q, None
        self._release(q)
        got = q.got
        flushed, end = False, None
        phase, since, later, told, quiet = "thinking", seen, [], set(), None
        gaps, gap, last = [], None, seen          # silences between Claude's sentences: [(words, voice)]
        dropped = None                            # when the voice went quiet with sentences still to say
        while True:
            if self._stop.is_set() or self._interrupt.is_set():
                end = "stopped"
                break
            st = self._recorder()                    # before the rows: words it has, it has written
            now = time.time()
            if self._you_speaking():
                self._you_at = now
            if phase == "thinking" and q.floor:
                phase = "speaking"
            self._pull()
            while self._pending and end is None:
                row = self._pending.popleft()
                if row.get("done"):
                    later.append(row)                # the recording stopped: after this answer
                    continue
                act = self._judge(q, phase, row)
                if act is not None:
                    self._trace("barge", action=act, phase=phase, words=row.get("text"), why=row.get("why"),
                                their_turn=row.get("turn"), barge_in=bool(row.get("barge_in")))
                if act == "later" and row.get("barge_in"):     # talking on over "Got it": they have the floor
                    with q.lock:
                        q.floor, q.ready_at = False, None
                    self._write("voice")
                    phase, mic = "thinking", True
                elif act == "later":
                    later.append(row)
                    if row.get("turn") not in told:
                        told.add(row.get("turn"))
                        self.log("voice %d: words while the meeting agent works: the next question" % self.seq)
                        self._say(q, [self.AFTER_THIS])
                elif act == "duck":
                    self.log("voice %d: talked over: Claude ducks while the words decide" % self.seq)
                    phase, since = "ducked", now
                elif act == "unduck":
                    self.log("voice %d: nothing said over it: Claude goes on where it was" % self.seq)
                    if q.play_since is not None:
                        q.play_since += now - since          # the time ducked was not heard
                    phase = "speaking"
                elif act in ("cancel", "more", "cut"):
                    end = act
                elif act in ("pause", "stop", "wait"):
                    with q.lock:
                        q.live = False               # what comes meanwhile waits
                    if act != "wait":
                        self._write("voice")         # as the recorder is now: it listens
                        mic = True
                    self.log("voice %d: %s" % (self.seq, {"pause": "talked over: Claude pauses",
                                                          "stop": "talked over: Claude stops (%s)" % row.get("why"),
                                                          "wait": "hold on: the answer waits"}[act]))
                    phase, since = {"pause": "paused", "stop": "stopped", "wait": "waiting"}[act], now
                elif act == "resume":
                    self.log("voice %d: %s" % (self.seq, "carries on" if q.floor else "back"))
                    self._resume(q)
                    phase = "thinking"
            if end is not None:
                break
            if phase == "ducked" and now - since >= self.DUCK_MAX:
                self.log("voice %d: ducked with no word from the recorder: Claude goes on" % self.seq)
                self._trace("barge", action="carry_on", phase=phase, why="no word from the recorder")
                phase = "speaking"                   # the recorder decides within 1.6 s (overlap.MAX_DUCK)
            if phase in ("paused", "stopped") and self._nothing_said(since, now):
                # No words over it came (a sound; or words dropped as someone else's): it carries on,
                # never left silent.
                self.log("voice %d: no words over Claude: it carries on" % self.seq)
                self._trace("barge", action="carry_on", phase=phase, why="no words over it")
                self._resume(q)
                phase = "thinking"
            elif phase in ("paused", "stopped") and now - since >= self.DECIDE_MAX:
                end = "cut"                          # still talking: their turn, its words to come
                break
            elif phase == "waiting" and now - since >= self.HOLD_ON:
                end = "cancel"
                break
            want = not (self.muted or self.dictating())
            if phase in ("thinking", "paused", "stopped", "waiting") and not q.floor and want != mic:
                self._write("voice" if want else "hold")
                mic = want
            if not q.worker.is_alive() and not flushed:
                flushed = True
                if not got["delta"]:                 # an engine that does not stream
                    q.stream.feed(got["answer"] or "")
                else:
                    # Words of the answer its deltas never brought (a greeting cut at "First, do"):
                    # the answer goes on from where they stopped, so the rest is said, all of it.
                    fed, answer = got.get("fed", ""), got["answer"] or ""
                    if fed and answer.startswith(fed) and len(answer) > len(fed):
                        self.log("voice %d: %d characters of the answer came only with it: said too"
                                 % (self.seq, len(answer) - len(fed)))
                        self._say(q, q.stream.feed(answer[len(fed):]))
                self._say(q, q.stream.flush())
            if phase == "thinking":
                self._take_floor(q)                  # kept until the user finished speaking
                if q.floor:
                    phase, mic = "speaking", False
            if phase == "speaking" and q.worker.is_alive() and not q.held and not self.speaker.busy:
                quiet = quiet or now
                if now - quiet >= self.REOPEN:       # Claude has nothing to say yet: listen meanwhile
                    with q.lock:
                        q.floor, q.ready_at = False, None
                    self._write("voice")
                    phase, mic, quiet = "thinking", True, None
            else:
                quiet = None
            gap, last = self._gap(q, gaps, gap, last, now, phase)
            self.holding = phase == "waiting"
            if phase in ("paused", "stopped", "waiting"):
                self.state, self.claude = ("muted" if self.muted else "listening"), ""
                self.level = float(st.get("level", 0.0))
            elif self.speaker.speaking:
                self._follow_speaker(q)
                self.state, self.claude = "speaking", self.speaker.current
                got.setdefault("audio", self.speaker.started_at or time.time())
            elif q.worker.is_alive() or q.held:
                self.state = "thinking"
                if not got["delta"] and not self.step and now - seen >= self.SLOW_ANSWER:
                    self.step = "Still waiting for Claude"      # not a dead mic: Claude has not answered yet
            if flushed and phase in ("thinking", "speaking") and not q.held and not self.speaker.busy:
                # The recorder halts the voice and writes its barge_in row just after,
                # so sentences can go quiet a moment before the row says why. Finish
                # only once the rows are in: else an "mm-hm" would end the answer.
                self._pull()
                if self._pending:
                    continue
                if q.playing < len(q.spoken) - 1:          # sentences dropped, and nothing asked us to
                    dropped = dropped or now
                    if now - dropped < self.CUT_WAIT:
                        time.sleep(0.02)
                        continue
                    self.log("voice %d: the voice stopped with %d sentence(s) left"
                             % (self.seq, len(q.spoken) - 1 - q.playing))
                end = "answered"
                break
            time.sleep(0.02 if phase == "thinking" else 0.04)
        self._pending.extendleft(reversed(later))
        self._timing(seen, spoke_until, got, early=(seen - q.started) if ask is not None else None,
                     started=q.started)
        if gaps:
            worst = max(gaps, key=sum)
            self.log("voice gaps: %d between sentences, longest %.2fs (%.2fs waiting for Claude's words,"
                     " %.2fs for the voice)" % (len(gaps), sum(worst), worst[0], worst[1]))
        self._q, self.holding = None, False
        self._trace("turn_done", outcome=end, q=got.get("qid"), question=question, sentences=len(q.spoken),
                    played=q.playing + 1, heard=q.heard(time.time()) if end != "answered" else None)
        self._record(q, end)
        held_mic = q.floor and phase in ("thinking", "speaking")
        if end == "answered":
            with q.lock:
                q.live, q.cancelled = False, True    # over: anything late of it is dropped
            if q.silenced and q.unsaid:
                self._cut = ""                       # paused before they heard any of it
                self.log("voice %d: paused while it thought: its reply is kept unsaid" % self.seq)
        else:
            heard = q.heard(time.time())
            self._cancel_ask(q, quiet=end == "more")
            self.speaker.stop()
            # Claude's history has the answer as written: the next turn says what was heard of it.
            self._cut = heard if got["delta"] else (q.cut if end == "more" else None)
            if end == "more" and shown is None:      # the next turn adds to this one
                self._carry = question               # (never to a turn of Claude's own: a greeting)
            self.log("voice %d: %s" % (self.seq, {
                "more": "more to the question: it is asked again with it",
                "cancel": "never mind: no answer",
                "cut": "interrupted: Claude is told what was heard"}.get(end, "interrupted")))
        if self._stop.is_set():
            return
        if held_mic and end in ("answered", "stopped"):
            time.sleep(self.GUARD)                   # the room's echo of Claude's voice
        self.claude = self.step = ""
        self.you = self._carry
        self._interrupt.clear()
        self._quiet_since = time.time()             # thinking and speaking are not silence
        if self.muted:
            self._write("hold")
            self.state = "muted"
        else:
            self._write("voice")
            self.state = "listening"
