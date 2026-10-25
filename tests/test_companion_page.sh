#!/usr/bin/env bash
# The companion's page (mac/companion.html) against a fake DOM and a fake
# server (needs node): the orb belongs to the live session and never starts
# one. The orb is for calls only (a meeting never has one: companion_state gives it none).
# Nothing live, a click does nothing; a call, it starts the call's voice, then pauses it or goes on.
source "$(dirname "$0")/guard.sh"     # never the real ~/.standby
set -euo pipefail
cd "$(dirname "$0")/.."
NODE="${NODE:-$(command -v node || echo ~/.local/share/mise/installs/node/22.21.1/bin/node)}"
D="$(mktemp -d -t sbcmp)"; trap 'rm -rf "$D"' EXIT
python3 -c "
import sys; s=open('mac/companion.html').read(); open(sys.argv[1],'w').write(s[s.rindex('<script>')+8:s.rindex('</script>')])" "$D/page.js"
cat > "$D/smoke.js" <<'JS'
// An error the checks don't see (an undeclared name in the page, a promise that
// rejects) fails the run, named, whatever the checks said. Node already exits non-zero
// on these; this puts them in the report, and the drain before the exit catches one
// that lands just as the checks end.
const uncaught = [];
process.on("uncaughtException", e => { uncaught.push(e); console.log("  uncaught: " + (e && e.stack || e)); process.exitCode = 1 });
process.on("unhandledRejection", e => { uncaught.push(e); console.log("  unhandled rejection: " + (e && e.stack || e)); process.exitCode = 1 });
const els = {};
const el = id => els[id] || (els[id] = { id, innerHTML: "", textContent: "", style: { setProperty() {} }, dataset: {},
  classList: { t: new Set(), add(c) { this.t.add(c) }, remove(c) { this.t.delete(c) }, toggle(c, on) { on ? this.t.add(c) : this.t.delete(c) }, contains(c) { return this.t.has(c) } },
  addEventListener() {}, getBoundingClientRect: () => ({ left: 0, bottom: 44, width: 44, height: 44 }), querySelector: () => null,
  querySelectorAll: () => [], closest: () => null, offsetWidth: 44, offsetHeight: 44, scrollWidth: 44 });
global.document = { getElementById: el, documentElement: el("html"), body: el("body"), addEventListener() {},
  querySelector: () => null, querySelectorAll: () => [] };
global.location = { hash: "" };
const posts = [], calls = [];
global.window = { webkit: { messageHandlers: { sb: { postMessage: m => posts.push(m) } } } };
global.setTimeout = () => 0; global.clearTimeout = () => {};
let state = { live: null, starting: null, voice: { state: "off" }, snap: null, screen: null };
global.fetch = async (p, o) => { calls.push([p, o && o.body ? JSON.parse(o.body) : null]);
  return { json: async () => p === "/api/companion" ? state : { ok: true } } };
eval(require("fs").readFileSync(process.argv[2], "utf8") + `
(async () => {
  const out = [], ok = (k, v, d) => out.push([k, !!v, d]);
  const after = async () => { await poll(true); calls.length = 0; await orb(); await new Promise(r => setImmediate(r)); return calls.slice() };
  let c = await after();
  ok("nothing live: a click on the orb does nothing (no talk started)", c.filter(x => x[0] !== "/api/companion").length === 0, c);
  state = { ...state, live: { id: "talk-1", kind: "talk", claude: true, elapsed: 12 } };
  c = await after();
  ok("a live talk: voice mode on that talk, never a new one", c.some(x => x[0] === "/api/voice" && x[1].id === "talk-1" && x[1].action === "start")
     && !c.some(x => x[0] === "/api/talk"), c);
  state = { ...state, voice: { state: "speaking" } };
  c = await after();
  ok("a call, Claude speaking: a click on the mini window pauses it (mute, saying where from), never ends it",
     c.some(x => x[0] === "/api/voice" && x[1].action === "mute" && x[1].from === "mini window")
     && !c.some(x => x[0] === "/api/voice" && x[1].action === "stop"), c);
  state = { ...state, voice: { state: "muted", muted: true } };
  c = await after();
  ok("  paused: a click goes on with the call", c.some(x => x[0] === "/api/voice" && x[1].action === "unmute" && x[1].from === "mini window"), c);
  const bad = out.filter(x => !x[1]);
  for (const [k, v, d] of out) console.log("  " + k.padEnd(62) + (v ? "OK" : "FAIL  " + JSON.stringify(d)));
  await new Promise(r => setImmediate(r));       // a rejection about to land, before the exit
  const failed = bad.length || uncaught.length;
  console.log(failed ? "FAILURES" : "ALL PASS"); process.exit(failed ? 1 : 0);
})();`);
JS
"$NODE" "$D/smoke.js" "$D/page.js" || exit 1
# The guard itself: a page that throws where no check looks must fail the run.
sed 's/async function orb() {/async function orb() { plantedUndeclaredName;/' "$D/page.js" > "$D/planted.js"
if "$NODE" "$D/smoke.js" "$D/planted.js" > "$D/planted.out" 2>&1; then
  echo "  a page that throws fails the run, the error named               FAIL (it passed)"; exit 1
elif grep -q plantedUndeclaredName "$D/planted.out"; then
  echo "  a page that throws fails the run, the error named               OK"
else
  echo "  a page that throws fails the run, the error named               FAIL"; cat "$D/planted.out"; exit 1
fi
# Each of the orb's bars on a layer of its own: in the panel, a bar that shrank into a thinking
# dot left its old pixels under the dots when they shared one.
if python3 -c "
import re,sys; s=open('mac/companion.html').read(); m=re.search(r'\n\.orb i\{([^}]*)\}', s)
sys.exit(0 if m and 'will-change:transform' in m.group(1) else 1)"; then
  echo "  the orb's bars each on their own layer (no stale bar under the dots)   OK"
else
  echo "  the orb's bars each on their own layer (no stale bar under the dots)   FAIL"; exit 1
fi
# Something waiting on a paused call: a bell on the orb, not the red dot (the user), tilted as it rings,
# outward on either side; Claude out of reach keeps its red dot.
if python3 -c "
import re,sys; s=open('mac/companion.html').read()
w=re.search(r'\n\.cmp\.waiting \.orbs:after\{([^}]*)\}', s); d=re.search(r'\n\.cmp\.down \.orbs:after\{([^}]*)\}', s)
sys.exit(0 if w and 'mask:' in w.group(1) and 'border-radius' not in w.group(1) and d and 'border-radius:50%' in d.group(1)
         and '.cmp.waiting .orbs:after{transform:rotate(15deg)}' in s and 'left:-1%;transform:rotate(-15deg)}' in s else 1)"; then
  echo "  something waiting: a tilted bell on the orb; Claude out of reach: its red dot OK"
else
  echo "  something waiting: a tilted bell on the orb; Claude out of reach: its red dot FAIL"; exit 1
fi
