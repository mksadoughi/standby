// Standby opened from Claude Code (/standby window): focused on that session's call or meeting, by its
// session id, as the app sends it ({session, kind, live}). During a call, the call view of THIS call,
// never a call remembered from before (one that ended out of sight leaves MX.call and a voice loop on
// its old session, which hid the call view); a live meeting, its window; nothing live, the session's
// last call or meeting (its summary); none ever: the past meetings and calls. When what it opened ends
// (by that session's own row), its summary; back to the list, it's no longer the focus. The window is an
// ordinary one throughout: the page never asks for it above other apps.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
var MX = {items: []}, voice = null, shown = 0, opened = [], summary = 0, asked = [], closed = 0;
var went = [];
var mxSlide = (dir, change) => change(), mxWake = async () => {}, mxGo = async v => { went.push(v) }, mxCallShow = () => { shown++ }, mxOpenWin = async s => opened.push(s);
var mxToSummary = () => { summary++ }, mxCloseWin = () => { closed++ };
var window = {pywebview: {api: new Proxy({}, {get: (t, k) => (...a) => asked.push([k, ...a])})}};
for (const n of ["async function mxFocusSession(f)", "function mxFocusCheck()", "function mxBack()"]) eval(pick(n));
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
(async () => {
  MX.items = [{from: "a1", kind: "call", session: "talk-2", live: true, at: 20}];
  MX.call = {agent: "a1", session: "talk-1", t0: 1}; voice = {sid: "talk-1"};
  await mxFocusSession({session: "talk-2", kind: "call", live: true});
  check("a call from before is replaced by this one, and its voice loop let go",
    MX.call.session === "talk-2" && MX.call.agent === "a1" && voice === null && shown === 1, {call: MX.call, voice});
  check("  focused on the call: its view", MX.fc && MX.fc.kind === "call" && MX.fc.session === "talk-2");
  voice = {sid: "talk-2"}; const was = MX.call;
  await mxFocusSession({session: "talk-2", kind: "call", live: true});
  check("  opened again during the same call: the same call, its voice kept", MX.call === was && voice.sid === "talk-2" && shown === 2);
  mxFocusCheck();
  check("  while the call's own row is live: on the call view, no summary", MX.fcLive === true && summary === 0 && opened.length === 0);
  MX.items[0].live = false; mxFocusCheck();
  await new Promise(r => setTimeout(r, 0));
  check("  the call ended: it stays on that call, at its summary", MX.fcLive === false && opened[0] === "talk-2" && summary === 1, {opened});
  MX.call = null; MX.items = [{from: "a1", kind: "meeting", session: "meeting-1", live: true, at: 30}];
  await mxFocusSession({session: "meeting-1", kind: "meeting", live: true});
  check("a live meeting instead: its window, no call view", opened[1] === "meeting-1" && shown === 2 && MX.call === null && MX.fc.kind === "meeting");
  MX.items[0].live = false; MX.items[0].paused = true; mxFocusCheck();
  check("  paused: still this session's meeting, not its summary", MX.fcLive === true && summary === 1);
  await mxFocusSession({session: "talk-2", kind: "call", live: false});
  check("nothing live: the session's last call or meeting, at its summary, still focused",
    opened[2] === "talk-2" && summary === 2 && MX.fc && MX.fc.session === "talk-2" && MX.fcLive === false, {opened, summary});
  await mxFocusSession(null);
  check("  none ever: the past meetings and calls, not focused", MX.fc === null && opened.length === 3 && went.length === 5, went);
  MX.items[0].paused = false; MX.items[0].live = true;
  await mxFocusSession({session: "meeting-1", kind: "meeting", live: true});
  mxBack();
  check("back to the list from a live meeting: not the focus", MX.fc === null && MX.fcLive === false && closed === 1);
  check("the page never asks the app to keep the window above others", asked.length === 0, asked);
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
