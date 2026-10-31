"""Standby's setup window before a first call or meeting ("Set up Standby"): what a start needs from
macOS, and what the window shows.

A call needs the microphone; an online meeting also needs the meeting's sound (System Audio
Recording). While what the start needs is still undecided, the start waits and the window
asks for just that (a call never for the meeting's sound: the user's first call, 2026-10-31), each
its own row, either first, macOS's own prompt only on the row's button, a tick as each is allowed
(or the sound put off with Not now, for this meeting only: the next one asks again), then the call or
meeting starts by itself. The meeting's sound holds a meeting only until it's answered: refused, the
meeting goes on, and says so quietly while it can't hear the other side.
Allowed already: no window, no wait. Plain functions; mac/ui.py holds the start and runs the window.
"""

MIC, SOUND = "mic", "sound"
HELD_FOR = 15 * 60             # the most a start waits for its setup (and is taken up after a Quit & Reopen)

NAMES = {MIC: ("Microphone", "Standby can hear you on calls and in meetings."),
         SOUND: ("Meeting sound", "Hears others in online meetings. Standby takes only the sound.")}
TITLE = "Set up Standby"
SUB = {1: "One thing for macOS to allow, once.", 2: "Two things for macOS to allow, once."}
DONE = "You're all set"
REFUSED = "Turn on Standby in System Settings, then come back."   # refused before: macOS won't ask again
# the meeting's sound refused, though System Settings may show Standby on: macOS keeps the switch of the copy it
# allowed, and a new build (install.sh signs each) is another copy; turning it off and on gives it to this one
SOUND_REFUSED = "Turn on Standby in System Settings. Already on? Turn it off and on again."
SOUND_STALE = "macOS still has an older Standby allowed. Turn it off and on again in System Settings."
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


def sound_state(state, stale=False):
    """The meeting's sound, from native.permissions.sound_state: "allowed" (heard, or asked
    with no sign of a refusal: macOS never says), "denied" (a capture heard only silence
    while an app played), "stale" (denied, an earlier build known to have heard it: the switch
    on in System Settings is that one's) or "ask" (this build has never asked)."""
    got = {"allowed": "allowed", "asked": "allowed", "denied": "denied"}.get(state, "ask")
    return "stale" if got == "denied" and stale else got


def can_skip(steps):
    """Not now is offered only where the mic still records: the window's two rows."""
    return MIC in steps and SOUND in steps


def settled(key, states, skip):
    """Nothing more to wait for: allowed; or the meeting's sound put off (Not now), or answered with a no."""
    return states.get(key) == "allowed" or (key == SOUND and (skip or states.get(key) in ("denied", "stale")))


def needed(kind, capture, states):
    """What a start is held for and its window asks: its steps, or [] when none is undecided (no window, no wait)."""
    steps = steps_for(kind, capture)
    return steps if any(not settled(k, states, False) for k in steps) else []


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
            told = line if ask else SOUND_STALE if states.get(key) == "stale" else SOUND_REFUSED if key == SOUND else REFUSED
            st.update(button="Allow" if ask else "Open Settings",
                      do=("allow_" if ask else "settings_") + key, line=told, waiting=not ask,
                      skip="Not now" if key == SOUND and can_skip(steps) else None)
        out.append(st)
    if phase == "failed":
        return {"title": "Couldn't start your %s" % what, "sub": "", "steps": out, "go": None,
                "error": cause(error), "retry": RETRY, "button": "Close"}
    if phase == "starting" or all(st["state"] != "open" for st in out):
        for st in out:
            if st["state"] == "open":
                st.update(state="done", button=None, do=None, waiting=False, skip=None)
        return {"title": DONE, "sub": "", "steps": out, "go": "Starting your %s…" % what, "error": None, "button": None}
    return {"title": TITLE, "sub": SUB[len(out)] if len(out) in SUB else "", "steps": out, "go": None, "error": None, "button": None}
