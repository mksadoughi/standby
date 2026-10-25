"""The computer's own voices, for Settings: the ones installed, and one of them saying a line
(Try). A call never speaks in one unless the user chose it (the recorder's engine then says it).
"""
from native import impl

_os = impl("voices")


def installed():
    """[(id, {"VoiceName": name, "VoiceLocaleIdentifier": "en_US"...})]: every voice installed,
    as the OS lists it (voice.english_voices picks those fit for a call)."""
    return _os.installed()


def say(voice_id, line, timeout=15.0):
    """`line` said in voice `voice_id` (None: the system's), returning once it has been, or
    after `timeout`."""
    return _os.say(voice_id, line, timeout)
