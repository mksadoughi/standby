"""The computer's microphones, as the OS lists them (native.mics), and which to record.

PortAudio (sounddevice) learns the device list once, when it starts: the
system default and any device that connects later are invisible to it until it
starts again. So what is connected, which mic is the system's default, and
whether another app has a mic open come from Core Audio here, with a word when
any of it changes. The recorder then decides (choose_mic, a pure function,
tested with fakes) and reopens its stream on the mic chosen.

Nothing here opens audio: these are property reads and listeners.
"""

import threading


# ---------------------------------------------------------------- the input to open (PortAudio)
# The recorder's and dictation's: macOS renumbers devices, and Zoom's or BlackHole's are never a mic.

def list_inputs():
    """[(index, name, max_input_channels)] for every current input device."""
    import sounddevice as sd
    return [(i, d["name"], d["max_input_channels"])
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0]


VIRTUAL_INPUTS = ("zoomaudiodevice", "microsoft teams audio", "blackhole",
                  "loopback", "soundflower", "aggregate", "multi-output")
MIC_ROLES = ("Room", "Me")


def is_virtual(name):
    n = name.lower()
    return any(v in n for v in VIRTUAL_INPUTS)


def resolve_device(wanted, role, log=lambda m: None):
    """Turn a configured device (name or index) into a CURRENT input index.

    macOS renumbers audio devices whenever one connects or disconnects, so a
    saved index is not trustworthy. Measured failure: a mic saved as index 2
    (then 'External Microphone') later resolved to 'ZoomAudioDevice', Zoom's
    virtual device, which is silent outside a Zoom call, so every UI session
    recorded 70 s of silence.

    Rules:
      * a saved NAME is matched exactly, then by substring;
      * for mic roles a saved INDEX is ignored (logged) in favour of the
        default input; for the loopback role it is still honoured;
      * virtual devices (Zoom, Teams, BlackHole, aggregates) are never chosen
        for a mic role unless named explicitly;
      * last resort: system default input, or the first real microphone.
    """
    import sounddevice as sd
    inputs = list_inputs()
    log("%s: available inputs: %s" % (
        role, "; ".join("%d=%s(%dch)%s" % (i, n, c, " [virtual]" if is_virtual(n) else "")
                        for i, n, c in inputs) or "NONE"))
    mic_role = role in MIC_ROLES

    if isinstance(wanted, str) and wanted:
        for idx, name, _ch in inputs:
            if name == wanted:
                log("%s: using %r (index %d, matched by name)" % (role, name, idx))
                return idx
        for idx, name, _ch in inputs:
            if wanted.lower() in name.lower():
                log("%s: using %r (index %d, partial name match for %r)"
                    % (role, name, idx, wanted))
                return idx
        log("%s: configured device %r not connected" % (role, wanted))
    elif isinstance(wanted, int):
        if mic_role:
            log("%s: ignoring saved device index %d (indices change when devices "
                "connect; run 'standby setup --mode mic' to pin a mic by name)"
                % (role, wanted))
        else:
            for idx, name, _ch in inputs:
                if idx == wanted:
                    log("%s: using %r (index %d)" % (role, name, idx))
                    return idx
            log("%s: saved index %r is not an input any more" % (role, wanted))

    default = sd.default.device[0]
    if default is not None and default >= 0:
        name = sd.query_devices(default)["name"]
        if not (mic_role and is_virtual(name)):
            log("%s: using system default input %r (index %d)" % (role, name, default))
            return default
        log("%s: system default %r is a virtual device, skipping it" % (role, name))
    for idx, name, _ch in inputs:
        if not (mic_role and is_virtual(name)):
            log("%s: using first real input %r (index %d)" % (role, name, idx))
            return idx
    raise RuntimeError("no usable input device for %s (inputs: %s)" % (role, inputs))


BUILT_IN, BLUETOOTH, USB, VIRTUAL = "built-in", "bluetooth", "usb", "virtual"
# Devices that are someone else's plumbing, never a mic, whatever they say they are.
VIRTUAL_NAMES = ("zoomaudiodevice", "microsoft teams audio", "blackhole", "loopback", "soundflower",
                 "aggregate", "multi-output")


class Device(object):
    def __init__(self, uid, name, transport, ident=None):
        self.uid, self.name, self.transport, self.id = uid, name, transport, ident

    @property
    def virtual(self):
        n = self.name.lower()
        return self.transport == VIRTUAL or any(v in n for v in VIRTUAL_NAMES)

    def __eq__(self, other):
        return isinstance(other, Device) and other.uid == self.uid

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        return hash(self.uid)

    def __repr__(self):
        return "<%s %s>" % (self.name, self.transport)


def choose_mic(inputs, default, pinned, current, choices, in_use):
    """Which mic to record from now: (device, why, ask).

    inputs: the connected inputs; default: the system's default input; pinned:
    a name chosen in Settings, "" for Automatic; current: the one recorded from
    now, or None; choices: answers kept per device ({uid: "use" | "keep"});
    in_use(device): another app has its mic open (a call on the AirPods).

    - A pinned mic while it is connected. Gone: the Mac's default, until it is back.
    - Automatic: the system's default input, never a virtual one (Zoom's,
      Teams', BlackHole): the one recorded from stays, or the first real mic.
    - A Bluetooth mic costs its owner their sound while it records (call
      quality). Taken without asking when a call already has it open, or when
      the user said so; told no, it is not taken; else it is asked about once,
      and the one recorded from stays meanwhile. ask: the device, or None.
    """
    real = [d for d in inputs if not d.virtual]
    if pinned:
        named = [d for d in inputs if d.name == pinned] or [d for d in inputs if pinned.lower() in d.name.lower()]
        if named:
            return named[0], "chosen in Settings", None
    connected = current if current is not None and current in inputs and not current.virtual else None
    if default is not None and default in inputs and not default.virtual:
        want, why = default, "the Mac's mic"
    elif connected is not None:
        want, why = connected, "the Mac's default is %s, not a mic" % (default.name if default else "none")
    elif real:
        want, why = sorted(real, key=lambda d: d.transport != BUILT_IN)[0], "the first real mic"
    else:
        return None, "no mic connected", None
    if pinned:
        why = "%s is not connected: %s" % (pinned, why)
    if want.transport == BLUETOOTH and want != current:
        answer = (choices or {}).get(want.uid)
        if in_use(want):
            return want, why + ", a call has it open", None
        if answer == "use":
            return want, why + ", as you said", None
        # The one recorded from stays, even a Bluetooth one (AirPods the user said yes to);
        # else the first real mic that is not Bluetooth.
        others = [d for d in ([connected] if connected else []) + sorted(real, key=lambda d: d.transport != BUILT_IN)
                  if d.transport != BLUETOOTH or d == connected]
        if others:
            keep = others[0]
            return keep, "keeping %s: %s %s" % (keep.name, want.name, "not used, as you said" if answer == "keep"
                                                 else "would drop to call quality"), (None if answer else want)
    return want, why, None


# The meeting's audio: which app's sound is the other side of the call.
# native.appaudio.apps() says who is playing, whether it is a known
# call app, and how sure it is (confidence 0..1).
CAPTURE_AT = 0.75        # from here an app is a call, and the log says so ("Zoom is playing (a call)")


def app_key(app):
    """Which app this is: its bundle id, or its pid for a process without one
    (some apps play through a helper)."""
    return app.get("bundle") or "pid:%s" % app.get("pid")


def answer_key(app):
    """What an answer is kept under: the bundle id; a process without one by
    its name (its pid changes every run)."""
    return app.get("bundle") or "name:%s" % (app.get("name") or app.get("app") or "")


def confidence(app):
    c = app.get("confidence")
    return float(c) if c is not None else (0.9 if app.get("call") else 0.3)


def choose_meeting(apps, capture, choices, current):
    """Whose audio to record as the meeting's: (app, why, ask).

    apps: native.appaudio.apps(); capture: "auto", "mic" or "meeting"; choices:
    answers kept per app ({answer_key: "yes" | "no"}); current: the app recorded
    from now, or None.

    - Mic only: none.
    - Otherwise whatever the Mac plays is the meeting's sound (the user's choice,
      2026-10-21: a video shown in a meeting is part of it): any app playing is
      recorded, a call app before another. MeetingFollow.sustained has already
      held back one playing for under 2 s, so a ping starts nothing.
    - Never Standby's own sound (dictation's cue, its voice), nor an app the user
      said no to.
    - The app recorded from stays while it plays; quiet, it gives way to another
      app playing, except a call: a call paused is still the call, and only
      another call takes its place (one app is recorded at a time, and music in
      the background must not take a call's place while nobody speaks).
    ask: None; nothing is asked about an app any more (kept for the callers' shape).
    """
    if capture == "mic":
        return None, "mic only", None
    choices = choices or {}
    # Standby's own (dictation's cue, Jean's voice) is never a meeting, whatever listed it.
    apps = [a for a in apps if not (a.get("own") or (a.get("bundle") or "").startswith("dev.standby."))]
    playing = sorted((a for a in apps if a.get("playing") and choices.get(answer_key(a)) != "no"),
                     key=lambda a: -confidence(a))
    if current is not None:
        same = [a for a in apps if app_key(a) == app_key(current)]
        others = [a for a in playing if app_key(a) != app_key(current)]
        if same and (same[0].get("playing") or not others or (
                confidence(same[0]) >= CAPTURE_AT and not [a for a in others if confidence(a) >= CAPTURE_AT])):
            return same[0], "%s, still" % (same[0].get("name") or same[0].get("app")), None
    if playing:
        a = playing[0]
        return a, "%s is playing%s" % (_name(a), " (a call)" if confidence(a) >= CAPTURE_AT else ""), None
    return None, "nothing playing", None


QUIET_LEAVE = 60.0       # s: an app recorded that has been quiet this long stops being recorded (a call: never)


def choose_meetings(apps, capture, choices, off, recorded, quiet_for):
    """Every app whose sound to record as the meeting's, each on its own: [app].

    choose_meeting's rules, for all the apps at once (the user's choice, 2026-10-23:
    the live meeting lists every source heard, each with its own switch): an app
    playing joins (MeetingFollow.sustained has held back a ping), never Standby's
    own, one the user said no to, or one switched off for this meeting (off:
    app_keys). One recorded (recorded: app_keys) stays while it runs: a call while
    quiet too, another app until it has been quiet QUIET_LEAVE s (quiet_for(key):
    s since it last played)."""
    if capture == "mic":
        return []
    choices, off, recorded = choices or {}, set(off or ()), set(recorded or ())
    out = []
    for a in apps:
        key = app_key(a)
        if a.get("own") or (a.get("bundle") or "").startswith("dev.standby.") or key in off \
                or choices.get(answer_key(a)) == "no":
            continue
        if a.get("playing") or (key in recorded and (confidence(a) >= CAPTURE_AT or quiet_for(key) < QUIET_LEAVE)):
            out.append(a)
    return sorted(out, key=lambda a: -confidence(a))


def _name(app):
    """An app in the user's words: its name, never its bundle id ("org.example.app" reads "App")."""
    name = app.get("name") or app.get("app")
    if name and not ("." in name and " " not in name and name == app.get("bundle")):
        return name
    last = (app.get("bundle") or "").rsplit(".", 1)[-1]
    return (last[:1].upper() + last[1:]) if last else "An app"


# The questions (the user's idea: "detected voice on xyz, should I switch?"):
# asked in one calm line when it is not clear what to record, never a popup. The clear
# cases just happen. Every word the user sees for them is here, in one place.
ASKS = {
    "mic": ("{name} connected: record from their mic? Their sound drops to call quality while it records.",
            [("Switch", "use"), ("Keep this mic", "keep")]),
    "meeting": ("{name} is playing with your mic open, maybe a call: record its sound too?",
                [("Record it", "yes"), ("No", "no")]),
}


def ask_for(kind, subject):
    """The question about a mic (a Device) or an app (one of apps()), as the page
    shows it: {id, text, actions [{label, value}], about {kind, name}}."""
    name = subject.name if kind == "mic" else _name(subject)
    text, actions = ASKS[kind]
    key = ("mic:" + subject.uid) if kind == "mic" else ("app:" + answer_key(subject))
    return {"id": key, "text": text.format(name=name), "about": {"kind": kind, "name": name},
            "actions": [{"label": label, "value": value} for label, value in actions]}


def headset_mic(output, inputs):
    """A Bluetooth input with the output's own address (the part of its uid before ":"),
    or None: the same headset's mic, listed by macOS as a device of its own."""
    addr = str(getattr(output, "uid", "") or "").split(":")[0]
    if not addr or getattr(output, "transport", None) != BLUETOOTH:
        return None
    return next((d for d in inputs if d.transport == BLUETOOTH and str(d.uid).split(":")[0] == addr), None)


class Watch(object):
    """A flag Core Audio sets from its thread; the recorder's loop takes it.
    Polled too (every POLL s), for what has no listener: a call starting on
    the AirPods, and a listener that could not be registered."""

    POLL = 2.0

    def __init__(self, devices, now):
        self.devices = devices
        self._flag = threading.Event()
        self.listening = False
        try:
            self.listening = bool(devices.watch(self._flag.set))
        except Exception:
            self.listening = False
        self.next = now

    def poke(self):
        """Look again now (an answer to a question changed what to record)."""
        self._flag.set()

    def due(self, now):
        if self._flag.is_set() or now >= self.next:
            self._flag.clear()
            self.next = now + (self.POLL if self.listening else 1.0)
            return True
        return False


def real_devices():
    """The OS's devices (native.mics), or None where they can't be reached (a test, an OS
    without them yet)."""
    try:
        from native import mics
        return mics.system()
    except Exception:
        return None
