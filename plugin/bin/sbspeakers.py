"""Display names for transcript speakers, shared by standby-agent and standby-ctx.

A transcript row may carry "spk": an id assigned by on-device voice matching.
Two files next to the transcript refine it:
  <session>.speakers.auto.json   {"alias": {"3": "1"}}  voices later found to be
                                  the same person (written by the recorder)
  <session>.speakers.names.json  {"names": {"1": "Alex"}}  names the user gave
                                  (written by the app)
  <session>.speakers.known.json  {"known": {"2": "Sam"}}  voices recognized from
                                  earlier sessions (written by the recorder);
                                  a name the user gave wins
Rows without "spk" use their channel: "Me" is the user, by the name their voice is
known by (known "me", the recorder's) or given, else "You"; anything else is
unattributed.
"""

import json
import os


def _load(path, key):
    # only a file right where it should be: never through a link to elsewhere
    if os.path.realpath(os.path.dirname(path)) != os.path.dirname(os.path.realpath(path)):
        return {}
    try:
        with open(path) as fh:
            return json.load(fh).get(key, {}) or {}
    except (IOError, OSError, ValueError):
        return {}


def load(live_dir, session):
    if not session or os.sep in session or session.startswith("."):     # a session id, never a path
        return {}, {}
    base = os.path.join(live_dir, session)
    names = dict(_load(base + ".speakers.known.json", "known"))
    names.update(_load(base + ".speakers.names.json", "names"))
    return _load(base + ".speakers.auto.json", "alias"), names


def resolve(spk, alias):
    seen = set()
    spk = str(spk)
    while spk in alias and spk not in seen:
        seen.add(spk)
        spk = str(alias[spk])
    return spk


def label(row, alias, names):
    """'Alex', 'Speaker 2', 'You', or '' when unattributed."""
    spk = row.get("spk")
    if spk is not None:
        sid = resolve(spk, alias)
        return names.get(sid) or "Speaker %s" % sid
    if row.get("speaker") == "Me":
        return names.get("me") or "You"
    return ""
