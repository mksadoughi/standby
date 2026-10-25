"""Tests talk to Standby's server as its own clients do: with this launch's token (mac/ui.py's TOKEN, which
the server writes to <root>/ui.token). use(get) names where a test's token comes from; every urlopen to
127.0.0.1 or localhost then carries it, unless the request names one itself (test_api_auth checks without)."""
import urllib.request

_real = urllib.request.urlopen
_get = [lambda: ""]


def use(get):
    """get(): the token, as a test has it: ui.TOKEN in the same process, or the server's ui.token file."""
    _get[0] = get


def _open(req, *args, **kw):
    if isinstance(req, str):
        req = urllib.request.Request(req)
    if req.host.split(":")[0] in ("127.0.0.1", "localhost") and not req.has_header("X-standby-token"):
        req.add_header("X-Standby-Token", _get[0]() or "")
    return _real(req, *args, **kw)


urllib.request.urlopen = _open
