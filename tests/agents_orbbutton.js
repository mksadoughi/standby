// Mini window is a button you can see (the user, 2026-10-22: hidden in ⋯ it was lost), in a live call's
// header, in words (2026-10-23; Pause and End moved to the row at the bottom, 2026-10-25); it sends the call to the mini window, which is
// only for calls: a meeting has none.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const ui = fs.readFileSync(path.join(__dirname, "..", "mac", "ui.py"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
var MX = {}, esc = s => String(s == null ? "" : s), MXW = {}, MXI = {back: "", more: "", pause: "", play: "", stop: ""}, mxAg = () => null, mxTitle = () => "Weekly sync", mxSrc = () => "", elapsed = () => 0, mmss = () => "1:00",
  mxDay = () => "Today", clock = () => "10:00", mxMin = () => "4 min";
eval(src.match(/const MXMARKS = [^\n]*\n/)[0].replace("const", "var")); eval(src.match(/const mxLiveMark = [^\n]*\n/)[0].replace("const", "var"));
eval(pick("function mxOpenHead(m)"));
const orb = /<button class="vtxt" onclick="mxToOrb\(\)">Mini window<\/button>/;
const live = mxOpenHead({id: "m1", session: "s1", kind: "meeting", live: true});
check("a live meeting's header: no Mini window (it is only for calls), nor Pause or Stop (they're in its row at the bottom)", !/mxToOrb|Mini window|mxPause|mxEndMeeting/.test(live), live);
check("  an ended one: none", !/mxToOrb/.test(mxOpenHead({id: "m2", session: "s2", kind: "meeting", live: false, duration_s: 240})));
check("  nor its ⋯", !/Orb|orb\(/.test(pick("function mxWinMenu(e, id)")));
const head = ui.slice(ui.indexOf('<header class="vhead"'), ui.indexOf("</header>", ui.indexOf('<header class="vhead"')));
check("a call's header: Mini window, in words; Pause and End are in its row at the bottom (2026-10-25)", orb.test(head) && !/voiceMute|closeVoice/.test(head), head);
const stage = ui.slice(ui.indexOf('<div class="vstage">'), ui.indexOf("</main>"));
check("  the row at the bottom: the orb, the words, then Pause and End", /id="orb"[\s\S]*class="vwords"[\s\S]*id="vmute"[\s\S]*onclick="closeVoice\(\)"/.test(stage) && ui.indexOf('id="mxcalllog"') < ui.indexOf('<div class="vstage">'), stage.slice(0, 400));
check("  and ⋯ no longer hides it", !/Mini window|Minimize to orb/.test(pick("function mxCallMenu(e)")));
console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
