"""Standby's setup window before a first call or meeting ("Set up Standby"): what a start needs from
macOS, and what the window shows.

A call needs the microphone; an online meeting also needs the meeting's sound (Screen & System
Audio Recording). While what the start needs is still undecided, the start waits and the window
asks for both, once: each its own row, either first, macOS's own prompt only on the row's button,
a tick as each is allowed (or the sound skipped), then the call or meeting starts by itself.
Allowed already: no window, no wait. Plain functions; mac/ui.py holds the start and runs the window.
"""

MIC, SOUND = "mic", "sound"
SKIP_DAYS = 30                 # the meeting's sound skipped: not asked again for this long
HELD_FOR = 15 * 60             # the most a start waits for its setup (and is taken up after a Quit & Reopen)

ROWS = [MIC, SOUND]               # the window always asks for both: a first call also offers the meeting's sound

NAMES = {MIC: ("Microphone", "Standby can hear you on calls and in meetings."),
         SOUND: ("Meeting sound", "Hears others in online meetings. macOS mentions the screen, "
                 "but Standby takes only the sound.")}
TITLE, SUB = "Set up Standby", "Two things for macOS to allow, once."
DONE = "You're all set"
REFUSED = "Turn on Standby in System Settings, then come back."   # refused before: macOS won't ask again
RETRY = "Try again from Claude Code."


def steps_for(kind, capture=None):
    """What a start needs, in order: a call the mic; a meeting as it records (capture)."""
    if kind == "call":
        return [MIC]
    return {"mic": [MIC], "meeting": [SOUND]}.get(capture, [MIC, SOUND])


def mic_state(status, heard=False):
    """The mic as the window takes it: "allowed", "ask" (macOS hasn't asked) or "denied".
    status: macOS's word (authorized, denied, restricted, undetermined, or None: unknown);
    heard: this build has heard the mic, which is the answer whatever this process is told.
    Unknown is allowed: the window never holds a start it can't be sure of."""
    if status in ("denied", "restricted"):
        return "denied"
    return "ask" if status == "undetermined" and not heard else "allowed"


def sound_state(permitted, heard=False, asked=False):
    """The meeting's sound: "allowed" (the screen is, or this build heard the Mac's sound), "ask"
    (macOS has never asked: it asks once, ever) or "denied"."""
    if permitted or heard:
        return "allowed"
    return "denied" if asked else "ask"


def skipped(skipped_at, now):
    """The meeting's sound was skipped (its row's Skip: meetings record the mic only) within SKIP_DAYS."""
    return bool(skipped_at) and 0 <= now - skipped_at < SKIP_DAYS * 86400


def can_skip(steps):
    """Skip is offered only where the mic still records: the window's two rows."""
    return MIC in steps and SOUND in steps


def settled(key, states, skip):
    return states.get(key) == "allowed" or (key == SOUND and skip)


def needed(kind, capture, states, skipped_at=None, now=0):
    """What a start is held for: its steps still undecided, or [] (no window, no wait). The window then
    asks for all ROWS."""
    steps = steps_for(kind, capture)
    skip = can_skip(steps) and skipped(skipped_at, now)
    return steps if any(not settled(k, states, skip) for k in steps) else []


def complete(steps, states, skip=False):
    return all(settled(k, states, skip and can_skip(steps)) for k in steps)


def cause(error):
    """Why a start failed, as the window says it under "Couldn't start your ...": a sentence of the
    start's own error ("Standby is busy with another call or meeting. End it first."); the recorder
    not starting, said plainly."""
    text = str(error or "").strip().rstrip(".")
    if not text or text == "Your Mac didn't start":
        return "Standby couldn't start listening."
    text = ". ".join(p[:1].upper() + p[1:] for p in text.split(": "))
    return text + "."


def view(kind, steps, states, skip=False, phase="open", error=None):
    """What the window shows: {title, sub, steps: [{key, name, line, state, button, do, waiting, skip}],
    go, error, retry, button}. A row's state: done, skipped or open; an open row has its button (do: what it
    asks Standby to do) and, once macOS has been refused, the line on turning it on and the waiting spinner.
    phase: open, starting (all done: it's starting) or failed."""
    what = "call" if kind == "call" else "meeting"
    skip = skip and can_skip(steps)
    out = []
    for key in steps:
        name, line = NAMES[key]
        st = {"key": key, "name": name, "line": line, "state": "open", "button": None, "do": None,
              "waiting": False, "skip": None}
        if states.get(key) == "allowed":
            st["state"] = "done"
        elif key == SOUND and skip:
            st["state"] = "skipped"
        else:
            ask = states.get(key) == "ask"
            st.update(button="Allow" if ask else "Open Settings",
                      do=("allow_" if ask else "settings_") + key, line=line if ask else REFUSED, waiting=not ask,
                      skip="Skip" if key == SOUND and can_skip(steps) else None)
        out.append(st)
    if phase == "failed":
        return {"title": "Couldn't start your %s" % what, "sub": "", "steps": out, "go": None,
                "error": cause(error), "retry": RETRY, "button": "Close"}
    if phase == "starting" or all(st["state"] != "open" for st in out):
        for st in out:
            if st["state"] == "open":
                st.update(state="done", button=None, do=None, waiting=False, skip=None)
        return {"title": DONE, "sub": "", "steps": out, "go": "Starting your %s…" % what, "error": None, "button": None}
    return {"title": TITLE, "sub": SUB, "steps": out, "go": None, "error": None, "button": None}
