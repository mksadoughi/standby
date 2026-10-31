"""Settings > Permissions: what macOS lets Standby use, each with its state now and at most one way to fix it.
Plain functions: mac/ui.py reads macOS (and Standby Dictation's last word) and acts on the buttons.

A row's status: "allowed", "ask" (macOS hasn't asked yet: Allow makes it ask), "off" (refused: Open Settings,
at its pane), "stale" (on in System Settings, but for an older copy of Standby, which a rebuild leaves behind:
Open Settings, to turn it off and on), "notneeded", or "unknown" (Standby Dictation isn't running, so it
can't be read). Only "off" and "stale" need the user: Settings' list counts those.
"""

LABEL = {"allowed": "Allowed", "ask": "Not asked", "off": "Off", "stale": "Needs a reset", "notneeded": "Not needed",
         "unknown": "Unknown"}
BUTTON = {"ask": "Allow", "off": "Open Settings", "stale": "Open Settings"}
STALE_LINE = "On in System Settings, but for an older copy of Standby. Turn it off and on there."
# The meeting's sound is "Meeting sound" here and "System Audio Recording" in macOS (the names to keep)
CALLS = (("mic", "Microphone", "Hears you on calls and in meetings."),
         ("sound", "Meeting sound", "Hears the other side of an online meeting. In macOS: System Audio Recording."))
DICTATION = (("mic", "Microphone", "Hears you dictate."),
             ("post", "Accessibility", "Types the words where your cursor is."),
             ("listen", "Input Monitoring", "Notices a double-tap shortcut. F5 doesn't need it."))


def call_mic(status, heard=False):
    """Standby's own mic, from macOS's word for it (and this build having heard it)."""
    if heard or status in ("authorized", None):
        return "allowed"
    return "ask" if status == "undetermined" else "off"


def sound(state, stale=False):
    """The meeting's sound, from native.permissions.sound_state: asked with no sign of a refusal counts as
    allowed (macOS never says); refused, an earlier build known to have heard it, is stale."""
    if state in ("allowed", "asked"):
        return "allowed"
    if state == "denied":
        return "stale" if stale else "off"
    return "ask"


def dictation(perm, needs_listen, running=True):
    """Standby Dictation's three, from what it last wrote of macOS (dictation.STATUS's "permissions")."""
    if not running or not isinstance(perm, dict):
        return {"mic": "unknown", "post": "unknown", "listen": "unknown" if needs_listen else "notneeded"}
    mic = perm.get("mic")
    return {"mic": "allowed" if mic == "allowed" else "ask" if mic in ("ask", None) else "off",
            "post": "allowed" if perm.get("post") else "off",
            "listen": "notneeded" if not needs_listen else "allowed" if perm.get("listen") else "off"}


def _row(group, key, name, line, status):
    out = {"group": group, "key": key, "name": name, "line": line, "status": status, "label": LABEL[status],
           "button": BUTTON.get(status)}
    if status == "stale":
        out["warn"] = STALE_LINE
    return out


def view(calls, dict_states):
    """{groups: [{id, title, note?, rows: [...]}], need}: calls {"mic", "sound"}, dict_states as dictation()."""
    groups = [{"id": "calls", "title": "Calls and meetings",
               "rows": [_row("calls", k, n, l, calls[k]) for k, n, l in CALLS]},
              {"id": "dictation", "title": "Dictation", "note": "Standby Dictation",
               "rows": [_row("dictation", k, n, l, dict_states[k]) for k, n, l in DICTATION]}]
    need = sum(1 for g in groups for r in g["rows"] if r["status"] in ("off", "stale"))
    return {"groups": groups, "need": need}
