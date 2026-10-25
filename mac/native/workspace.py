"""The other apps on this computer, as Standby sees them: so far, an app's icon, for a
meeting's source.
"""
from native import impl

_os = impl("workspace")
ICON_SIZE = 64            # an app's icon, in pixels (2x a 32-point icon)


def app_icon(app_id):
    """An app's icon as a small PNG (ICON_SIZE square, transparent), or None if there is
    no such app. app_id: its bundle id on a Mac. Slow the first time; the server keeps them."""
    return _os.app_icon(app_id, ICON_SIZE)
