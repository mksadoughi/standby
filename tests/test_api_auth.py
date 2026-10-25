"""Standby's local server answers only Standby's own clients: every request names 127.0.0.1 or localhost on
its port as its Host (a web page that rebinds its own name to 127.0.0.1 reaches nothing), and carries this
launch's token (X-Standby-Token, or ?token= where a header can't go: the page itself, EventSource, a download).
Pages are never framed; a body too large is refused; /api/start is for tests only. A real server on a
throwaway STANDBY_ROOT; nothing real is started."""
import agentpath  # noqa: E402,F401  (first: never the real ~/.standby)
import http.client, json, os, socket, stat, sys, tempfile, threading  # noqa: E401
from pathlib import Path

root = Path(os.path.realpath(tempfile.mkdtemp(prefix="sbauth-")))
os.environ.update(STANDBY_ROOT=str(root), STANDBY_NO_DEVICES="1")
os.environ.pop("STANDBY_TEST_ROUTES", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mac"))
import ui  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print("  %-90s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label.strip())
        if detail != "":
            print("     %s" % (detail,))


srv = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
T = ui.TOKEN
HOST = "127.0.0.1:%d" % PORT


def ask(method, path, token=None, host=HOST, body=None, query_token=False, read=True, extra=None):
    """(status, headers, body text) for one request, its headers exactly as given."""
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    if query_token:
        path += ("&" if "?" in path else "?") + "token=" + T
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host)
    if token is not None:
        c.putheader("X-Standby-Token", token)
    for k, v in (extra or {}).items():
        c.putheader(k, v)
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data)
    r = c.getresponse()
    text = r.read().decode("utf-8", "replace") if read else ""
    c.close()
    return r.status, r, text


# ---------------------------------------------------------------- the token
st, r, _t = ask("GET", "/api/state")
check("GET without the token: 401, said to be Standby (so another Standby on a port is known as one)",
      st == 401 and r.getheader("X-Standby") == "1", st)
check("  a wrong token: 401", ask("GET", "/api/state", token="nope")[0] == 401)
check("  this launch's token, as a header: answered", ask("GET", "/api/state", token=T)[0] == 200)
check("  or as ?token= (where a header can't go)", ask("GET", "/api/state", query_token=True)[0] == 200)
check("  a transcript without it: 401, nothing of it", ask("GET", "/api/session/meeting-20261009-150000")[0] == 401)
st, _r, text = ask("POST", "/api/rename", body={"id": "meeting-20261009-150000", "title": "x"})
check("POST without the token: 401", st == 401, (st, text))
check("  with it: past the gate (an unknown session, said so)", ask("POST", "/api/rename", token=T,
      body={"id": "meeting-20261009-150000", "title": "x"})[0] == 404)
check("  /api/lite/* (the mod's) without it: 401", ask("POST", "/api/lite/call", body={"cc": "x"})[0] == 401
      and ask("GET", "/api/lite/state?cc=x")[0] == 401)

# ---------------------------------------------------------------- the Host
for host in ("evil.example:%d" % PORT, "evil.example", "127.0.0.1:%d" % (PORT + 1), "198.51.100.7:%d" % PORT, ""):
    check("a foreign Host (%s), even with the token: 403" % (host or "none"),
          ask("GET", "/api/state", token=T, host=host)[0] == 403 and ask("POST", "/api/rename", token=T, host=host,
          body={"id": "x"})[0] == 403)
check("  localhost on this port: answered", ask("GET", "/api/state", token=T, host="localhost:%d" % PORT)[0] == 200)
check("  POST from another page's Origin, with the token: still refused (the browser's own check)",
      ask("POST", "/api/rename", token=T, body={"id": "x"}, extra={"Origin": "http://evil.example"})[0] == 403)

# ---------------------------------------------------------------- the streams
check("the page's event stream without the token: 401", ask("GET", "/api/msg/events")[0] == 401)
st, r, _t = ask("GET", "/api/msg/events", query_token=True, read=False)
check("  with ?token= (EventSource takes no header): open", st == 200 and "event-stream" in (r.getheader("Content-Type") or ""), st)
check("the mod's stream without the token: 401", ask("GET", "/api/lite/stream?cc=x")[0] == 401)
check("  from a foreign Host: 403", ask("GET", "/api/msg/events", host="evil.example:%d" % PORT, query_token=True)[0] == 403)

# ---------------------------------------------------------------- the pages
st, r, text = ask("GET", "/")
check("the page without the token: 401", st == 401)
st, r, text = ask("GET", "/", query_token=True)
check("  with it (the window loads /?token=): served, with the token in it for its own requests",
      st == 200 and ('const SBT="%s"' % T) in text and "__SB_TOKEN__" not in text, st)
check("  never framed: X-Frame-Options DENY and frame-ancestors 'none'",
      r.getheader("X-Frame-Options") == "DENY" and "frame-ancestors 'none'" in (r.getheader("Content-Security-Policy") or ""))
st, r, text = ask("GET", "/companion", query_token=True)
check("the mini window's page: served with the token (and not framed)", st == 200 and ('const SBT = "%s"' % T) in text
      and r.getheader("X-Frame-Options") == "DENY", st)
check("  without it: 401", ask("GET", "/companion")[0] == 401)
check("the page's own code (agents.js, agents.css): no data, loaded by its tags, open",
      ask("GET", "/agents.js")[0] == 200 and ask("GET", "/agents.css")[0] == 200)
check("  but never from a foreign Host", ask("GET", "/agents.js", host="evil.example:%d" % PORT)[0] == 403)

# ---------------------------------------------------------------- bodies, test routes
was, ui.MAX_BODY = ui.MAX_BODY, 64
check("a body too large: 413", ask("POST", "/api/rename", token=T, body={"id": "x" * 200})[0] == 413)
ui.MAX_BODY = was
st, _r, text = ask("POST", "/api/start", token=T, body={})
check("/api/start (tests only) is not served without STANDBY_TEST_ROUTES", "started" not in text and st != 200 or
      json.loads(text or "{}").get("error"), (st, text))

# ---------------------------------------------------------------- the token file, for Standby's own clients
ui.write_token()
tok, hdr = root / "ui.token", root / "ui.header"
check("the token is written for Standby's clients, readable by its owner only",
      tok.read_text().strip() == T and stat.S_IMODE(os.stat(tok).st_mode) == 0o600, oct(stat.S_IMODE(os.stat(tok).st_mode)))
check("  and as a header line, for curl -H @file (the mod's stream): owner only, never on a command line",
      hdr.read_text() == "X-Standby-Token: %s\n" % T and stat.S_IMODE(os.stat(hdr).st_mode) == 0o600)
check("another Standby on a port (its token not ours): known as Standby, not another app",
      ui.port_owner(PORT) == "this")
(root / "ui.token").write_text("someone-elses\n")
check("  with a token we don't have: still known as a Standby", ui.port_owner(PORT) == "standby")

# ---------------------------------------------------------------- the real clients
page_src = ui.PAGE
check("the page's script sends the token on every request (api0, the stall report)",
      page_src.count('"X-Standby-Token":SBT') >= 3, page_src.count('"X-Standby-Token":SBT'))
agents = (Path(ui.HERE) / "agents.js").read_text()
check("  its event stream and its download link carry it as ?token=",
      'EventSource("/api/msg/events?token=" + encodeURIComponent(SBT))' in agents and "&token=\" + encodeURIComponent(SBT)" in agents)
comp = (Path(ui.HERE) / "companion.html").read_text()
check("the mini window's page sends it on every request", '"X-Standby-Token": SBT' in comp and 'const SBT = "__SB_TOKEN__"' in comp)
mod = (Path(ui.HERE).parent / "lite" / "hooks" / "register.js").read_text()
check("the mod reads it from ~/.standby/ui.token and sends it; its stream reads it from ui.header",
      "'ui.token'" in mod and "'x-standby-token'" in mod and "'-H', '@'" in mod)

srv.shutdown()
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
