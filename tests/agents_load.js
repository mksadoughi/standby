// Standby's view never shows made-up data. When the server can't answer the first load (still starting,
// busy, a timeout), the list keeps its spinner and asks again in a second; once it answers, the real
// sessions and meetings show. The made-up Orion team lives only in the previews (tools/ui_preview.py).
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
check("no sample data or sample mode in the app", !/MXFAKE|MX\.sample|Sample data/.test(src));
var MX = {view: {kind: "past"}, ws: [], agents: [], items: [], loaded: false}, painted = 0, timers = [];
var mxPaint = () => { painted++ }, setTimeout = (f, ms) => { timers.push([f, ms]); return timers.length }, clearTimeout = () => {};
let answer = () => { throw new Error("connection refused") };
var api = async (p, b) => answer(p, b);
eval(pick("async function mxGet(path, body)").replace("async function mxGet", "var mxGet = async function"));
eval(src.match(/let MXRETRY = 0;\n/)[0].replace("let", "var"));
eval(pick("async function mxLoad()").replace("async function mxLoad", "var mxLoad = async function"));
(async () => {
  await mxLoad();
  check("the server down at launch: not loaded (the spinner stays), nothing made up", !MX.loaded && MX.items.length === 0 && MX.agents.length === 0);
  check("  and it asks again in a second", timers.length === 1 && timers[0][1] === 1000, timers.map(t => t[1]));
  answer = p => p === "/api/msg/workspaces" ? {workspaces: [{id: "w1"}], agents: [{id: "a1"}]} : {error: "busy"};
  await timers[0][0]();
  check("sessions back but the meetings not yet: still not loaded, asked again", !MX.loaded && MX.agents.length === 1 && timers.length === 2);
  answer = p => p === "/api/msg/workspaces" ? {workspaces: [{id: "w1"}], agents: [{id: "a1"}]} : {items: [{session: "m1", kind: "meeting"}]};
  await timers[1][0]();
  check("both back: loaded, the real ones shown, no more asking", MX.loaded && MX.items.length === 1 && timers.length === 2);
  answer = () => null;
  await mxLoad();
  check("a poll that fails later keeps what's shown", MX.loaded && MX.items.length === 1 && MX.agents.length === 1);
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
