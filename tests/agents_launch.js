// A session opened anywhere (the orb) is Standby's window for it: what's live opens as Claude Code
// opens it, at launch too; anything else at its summary. The old session view is gone from the page.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const page = fs.readFileSync(path.join(__dirname, "..", "mac", "ui.py"), "utf8");
const wrap = src.match(/async function select\(id\) \{[\s\S]*?\n  if \(it\) return mxPastOpen\(id\) \}\n/);
const launch = src.match(/\nmxGo\(\)\.then\([^\n]*\n/);
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
check("select is Standby's view's own, and the launch opens what's live", !!wrap && !!launch);
var MX = {view: null, items: [], agents: []}, did = [];
var mxGo = async () => { did.push("go"); MX.view = {kind: "past"} }, mxShowLive = async () => { did.push("live") }, mxPastOpen = async id => { did.push("open " + id) };
eval(wrap[0].replace("async function select", "var select = async function"));
(async () => {
  MX.items = [{session: "m1", kind: "meeting", live: true}, {session: "m0", kind: "meeting"}];
  await select("m1");
  check("a live meeting: Standby's view, then its window as Claude Code opens it", did.join() === "go,live" && MX.view.kind === "past", did);
  did = []; await select("m0");
  check("an ended one: its window at the summary, the view kept", did.join() === "open m0", did);
  did = []; await select("unknown");
  check("one not in the list yet: the list loaded again, nothing else", did.join() === "go", did);
  did = []; MX.agents = [{id: "a1", live: {meeting: "m1"}}]; MX.view = null;
  eval(launch[0]); await new Promise(r => setTimeout(r, 10));
  check("at launch: Standby's view, and the live meeting opened", did.join() === "go,live", did);
  check("the page has no old session view: no select, renderList, send or tabs of its own", !/function (select|renderList|send|setTab)\(/.test(page)
    && !page.includes('id="foot"') && !page.includes('id="list"') && !page.includes('id="tabs"'));
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
