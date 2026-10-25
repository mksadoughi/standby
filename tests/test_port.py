"""The app's port: 8765, unless something else has it. A busy port is reused only when this
same Standby answers on it (/api/whoami, with its folder's mark). Another app there
(AnkiConnect uses 8765) or another Standby (its own folder): the next free port instead,
and the plugin finds it in ui.port. Fake servers stand in for the others."""
import agentpath  # noqa: F401  (first: never the real ~/.standby)
import json, os, socket, sys, tempfile, threading, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="sbport-"))
os.environ["STANDBY_ROOT"] = str(root)
s0 = socket.socket(); s0.bind(("127.0.0.1", 0)); os.environ["STANDBY_UI_PORT"] = str(s0.getsockname()[1]); s0.close()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mac"))
import ui  # noqa: E402
import sbtoken  # noqa: E402  (the requests here carry this launch's token, as Standby's clients do)
sbtoken.use(lambda: ui.TOKEN)
said = []
ui.log = said.append

fails = []


def check(label, ok, d=""):
    print("  %-74s %s" % (label, "OK" if ok else "FAIL"))
    if not ok:
        fails.append(label)
        print("     " + str(d)[-500:])


def fake(answer):
    """A server on a free port that answers /api/whoami with `answer` (None: a 404, as another app)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if answer is None:
                self.send_response(404); self.end_headers(); return
            body = json.dumps(answer).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


check("whoami: Standby, and a mark of its folder (not the path)",
      ui.whoami() == {"app": "standby", "root": ui.root_mark()} and str(root) not in json.dumps(ui.whoami()))

other = fake(None)
port = other.server_address[1]
check("another app on the port: not Standby", ui.port_owner(port) == "other")
ui.PORT = port
srv = ui._bind()
check("  so the next free port, never that app's page", srv is not None and ui.PORT != port
      and srv.server_address[1] == ui.PORT, (port, ui.PORT))
check("  and the log says so plainly", any("port %d is taken by another app: serving on %d instead" % (port, ui.PORT) == m
                                           for m in said), said)
srv.server_close()

anki = fake({"apiVersion": "AnkiConnect v.6"})
check("an app answering JSON of its own: still not Standby", ui.port_owner(anki.server_address[1]) == "other")

twin = fake({"app": "standby", "root": "0123456789abcdef"})
port = twin.server_address[1]
check("another Standby (its own folder): told apart from this one", ui.port_owner(port) == "standby")
ui.PORT = port
srv = ui._bind()
check("  served beside it, on another port", srv is not None and ui.PORT != port
      and any("taken by another Standby" in m for m in said), said[-1:])
srv.server_close()

me = fake(ui.whoami())
port = me.server_address[1]
ui.PORT = port
check("this same Standby already serving: reused, no second server", ui.port_owner(port) == "this"
      and ui._bind() is None and ui.PORT == port)

closed = socket.socket(); closed.bind(("127.0.0.1", 0)); cport = closed.getsockname()[1]; closed.close()
check("nothing answering at all: not Standby", ui.port_owner(cport, timeout=0.5) == "other")

ui.PORT = cport
srv = ui.serve()
threading.Thread(target=srv.serve_forever, daemon=True).start()
got = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/api/whoami" % ui.PORT, timeout=5).read())
check("the real server answers /api/whoami", got == ui.whoami(), got)
check("  and writes the port it serves on, for the plugin", (root / "ui.port").read_text().strip() == str(ui.PORT))
srv.shutdown()

print()
print("%d failed" % len(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
