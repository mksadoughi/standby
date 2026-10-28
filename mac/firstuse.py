"""Standby's setup window before a first call or meeting: what a start needs from macOS, in order,
and what the window shows.

A call needs the microphone; an online meeting also needs the meeting's sound (Screen & System
Audio Recording). While one of them is still undecided, the start waits and the window asks, one
step at a time, as dictation's setup does: macOS's own prompt only on the step's button, a tick as
each is allowed, then the call or meeting starts by itself. Allowed already: no window, no wait.
Plain functions; mac/ui.py holds the start and runs the window.
"""

MIC, SOUND = "mic", "sound"
SKIP_DAYS = 30                 # the meeting's sound skipped: not asked again for this long
HELD_FOR = 15 * 60             # the most a start waits for its setup (and is taken up after a Quit & Reopen)

NAMES = {MIC: ("Microphone", "So Standby can hear you on calls and in meetings."),
         SOUND: ("Meeting sound", "So Standby can hear the other people in an online meeting. "
                 "It takes only the sound, never your screen.")}
TITLES = {"call": "Before your first call", "meeting": "Set up meetings"}
DONE = "You're all set"
WAITING = "Waiting for you to turn it on"
# the how-to under a step, **bold** as the page shows it
MIC_HOW = "In the list, turn on **Standby**, then come back here."
SOUND_HOW = ("macOS will ask you to quit and reopen Standby: choose **Quit & Reopen**. "
             "Your %s starts right after.")
SOUND_SETTINGS_HOW = ("In the list, turn on **Standby**. macOS then asks to quit and reopen it: choose "
                      "**Quit & Reopen**. Your %s starts right after.")
SKIP = "Skip: record only my mic"
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
    """The meeting's sound was skipped (Skip: record only my mic) within SKIP_DAYS."""
    return bool(skipped_at) and 0 <= now - skipped_at < SKIP_DAYS * 86400


def can_skip(steps):
    """Skip is offered only where the mic still records: a meeting on both."""
    return MIC in steps and SOUND in steps


def settled(key, states, skip):
    return states.get(key) == "allowed" or (key == SOUND and skip)


def needed(kind, capture, states, skipped_at=None, now=0):
    """The steps a start's window shows, or [] when nothing is undecided (no window, no wait)."""
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


def view(kind, steps, states, pressed=(), skip=False, phase="open", error=None):
    """What the window shows: {title, sub, steps: [{key, n, name, why, state, button, do, note,
    waiting, how, skip}], go, error, retry}. state: done, skipped, current or later; only the current step has
    a button (do: what it asks Standby to do). phase: open, starting (all done: it's starting) or failed."""
    what = "call" if kind == "call" else "meeting"
    skip = skip and can_skip(steps)
    out, current = [], None
    for n, key in enumerate(steps, 1):
        name, why = NAMES[key]
        st = {"key": key, "n": n, "name": name, "why": why, "state": "later", "button": None, "do": None,
              "note": None, "waiting": False, "how": None, "skip": None}
        if states.get(key) == "allowed":
            st["state"] = "done"
        elif key == SOUND and skip:
            st.update(state="skipped", note="Skipped: only your mic")
        elif current is None:
            current = st
            st["state"] = "current"
            sent = key in pressed
            if key == MIC and states.get(key) == "ask":
                st.update(button="Allow Microphone", do="allow_mic", note="macOS will ask: choose Allow.")
            elif key == MIC:
                st.update(button="Open System Settings", do="settings_mic", how=MIC_HOW,
                          waiting=sent, note=WAITING if sent else None)
            else:
                ask = states.get(key) == "ask"
                st.update(button="Allow" if ask else "Open System Settings", do="allow_sound" if ask else "settings_sound",
                          how=(SOUND_HOW if ask else SOUND_SETTINGS_HOW) % what, waiting=sent,
                          note=WAITING if sent else None, skip=SKIP if can_skip(steps) else None)
        out.append(st)
    if phase == "failed":
        return {"title": "Couldn't start your %s" % what, "sub": "", "steps": out, "go": None,
                "error": cause(error), "retry": RETRY, "button": "Close"}
    if current is None or phase == "starting":
        for st in out:
            if st["state"] == "current":
                st.update(state="done", button=None, do=None, note=None, waiting=False, how=None, skip=None)
        return {"title": DONE, "sub": "", "steps": out, "go": "Starting your %s…" % what,
                "error": None, "button": None}
    return {"title": TITLES[what], "sub": "Your %s starts as soon as this is done." % what, "steps": out, "go": None,
            "error": None, "button": None}
