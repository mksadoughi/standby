// A meeting's Pause, Resume and End, and a call's Pause and Resume, answer at once (the user, 2026-10-22: a
// second passed with no sign the click was heard): the button says what it's doing ("Pausing…"), both are
// dimmed and not pressable twice, until it's done; refused, it's put back and the reason said.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const ui = fs.readFileSync(path.join(__dirname, "..", "mac", "ui.py"), "utf8");
const pick = (s, name) => { const i = s.indexOf(name); if (i < 0) throw new Error(name + " not found"); return s.slice(i, s.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
var MX = {exp: null}, MXW = {}, MXI = {back: "", more: "", pause: "", play: "", stop: "", chev: "", check: ""}, MXHEAR = {open: false, want: {}, seen: {}}, MXBARS = () => "", MXNOEAR = "", esc = s => String(s == null ? "" : s), mxAg = () => null, mxTitle = () => "Weekly sync", mxSrc = () => "", elapsed = () => 0,
  mmss = () => "1:00", mxDay = () => "Today", clock = () => "10:00", mxMin = () => "4 min";
var heads = [], toasts = [], reply = {ok: true}, release = null, mxMenuClose = () => {}, mxToSummary = () => {}, toast = t => toasts.push(t);
var mxPaint = () => heads.push(mxMeetDock(meeting, {}).map(c => c.h).join("")), mxLoad = async () => {}, mxGet = (p, b) => new Promise(r => { release = () => r(reply) });
const meeting = {id: "m1", session: "s1", kind: "meeting", live: true};
eval(src.match(/const MXMARKS = [^\n]*\n/)[0].replace("const", "var")); eval(src.match(/const mxLiveMark = [^\n]*\n/)[0].replace("const", "var"));
for (const n of ["function mxMeetDock(m, lv)", "function mxOpenHead(m)", "async function mxBusy(", "async function mxPause(", "async function mxEndMeeting("]) eval(pick(src, n).replace(/^(async )?function (\w+)/, "var $2 = $1function"));
(async () => {
  const p = mxPause("s1", true); await null;
  const h = heads[heads.length - 1];
  check("Pause: at once \"Pausing…\", both buttons dimmed and not pressable", /class="vic dk busy" disabled[^>]*title="Pausing…"/.test(h) && /class="vic dk end" disabled[^>]*title="Stop"/.test(h), h);
  mxPause("s1", true); mxEndMeeting("s1");
  check("  pressed again meanwhile: nothing more sent", heads.length === 1);
  release(); await p;
  check("  done: back to the buttons, pressable", !/disabled/.test(heads[heads.length - 1]) && MX.busy === null);
  const e = mxEndMeeting("s1"); await null;
  check("End: at once \"Ending…\"", /class="vic dk end busy" disabled[^>]*title="Ending…"/.test(heads[heads.length - 1]));
  reply = {error: "That meeting has ended"}; release(); await e;
  check("  refused: put back, and why said", !/disabled/.test(heads[heads.length - 1]) && toasts.includes("That meeting has ended"));
  // the call's: the page's own Pause (ui.py): Pausing… until the voice says it is, then Resume; refused, back
  const vm = ui.slice(ui.indexOf("async function voiceMute(){"), ui.indexOf("\nasync function closeVoice")), pv = ui.slice(ui.indexOf("const m=$(\"vmute\");if(VPEND"), ui.indexOf("}}", ui.indexOf("const m=$(\"vmute\");if(VPEND")) + 2);
  check("a call's Pause: \"Pausing…\"/\"Resuming…\" at once, not pressable twice", /if\(!voice\|\|VPEND\)return/.test(vm) && /Pausing\\u2026/.test(vm) && /Resuming\\u2026/.test(vm) && /m\.disabled=true/.test(vm), vm);
  check("  refused: put back and why said; the label follows the voice once it's done", /r\.error\)\{VPEND=null;m\.disabled=false;[^}]*toast\(r\.error\)/.test(vm) && /if\(!VPEND\)vmuteIs\(m,!!st\.muted\)/.test(pv) && /paused\?"Resume":"Pause"/.test(ui), pv);
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
