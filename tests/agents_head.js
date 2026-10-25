// A live meeting's or call's header is one line (the user, 2026-10-23: the glowing dot already says it's
// recording): its mark, its name, its time; no "Listening" or "On a call" words. Paused, the mark is a still
// pause mark and the time stays. Ended, what it was sits under its name as before.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const ui = fs.readFileSync(path.join(__dirname, "..", "mac", "ui.py"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
var MX = {}, MXW = {}, MXI = {back: "", more: "", pause: "", play: "", stop: ""}, esc = s => String(s == null ? "" : s), mxAg = () => ({name: "Guest TTL fix"}), mxTitle = () => "Weekly sync", mxSrc = () => "",
  elapsed = () => 46, mmss = t => "00:" + String(t).padStart(2, "0"), mxDay = () => "Today", clock = () => "10:00", mxMin = () => "4 min";
eval(src.match(/const MXMARKS = [^\n]*\n/)[0].replace("const", "var")); eval(src.match(/const mxLiveMark = [^\n]*\n/)[0].replace("const", "var"));
eval(pick("function mxOpenHead(m)"));
const live = mxOpenHead({id: "m1", session: "s1", kind: "meeting", live: true, duration_s: 46});
check("a live meeting: one line, the red mark, its name, its time", /<span class="vwho one"[^>]*><span class="lmark rec"[^>]*aria-label="Recording"[^>]*><\/span><b[^>]*>Weekly sync<\/b><span class="tm">00:46<\/span><\/span>/.test(live), live);
check("  no \"Listening\" and no second line", !/Listening|<small>/.test(live));
const paused = mxOpenHead({id: "m1", session: "s1", kind: "meeting", paused: true, duration_s: 46});
check("paused: the pause mark, the time kept", /lmark paused[^>]*aria-label="Paused"/.test(paused) && /class="tm">00:46</.test(paused) && !/Paused ·/.test(paused), paused);
const ended = mxOpenHead({id: "m2", session: "s2", kind: "meeting", duration_s: 240});
check("ended: what it was, under its name", /<small>Meeting · 4 min · ended 10:00<\/small>/.test(ended) && !/lmark/.test(ended), ended);
const head = ui.slice(ui.indexOf('<header class="vhead"'), ui.indexOf("</header>", ui.indexOf('<header class="vhead"')));
check("a live call: one line, the call's mark, its name, its time", /<span class="vwho one"><span class="lmark call" id="vmark"[^>]*><\/span><b id="vname"><\/b><span class="tm" id="vtime"><\/span><\/span>/.test(head), head);
check("  its time alone, and the pause mark while paused", /\$\("vtime"\)\.textContent = i && i\.started \? mmss\(elapsed\(i\)\) : ""/.test(src) && /st\.muted \? "paused" : "call"/.test(src));
console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
