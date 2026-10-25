// A meeting's transcript in the window: a sentence split at pauses reads as one line (the same rule
// as Standby inside Claude Code's pane); the words being said now are the bottom row's, never the transcript's.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
const line = name => { const i = src.indexOf(name); return src.slice(i, src.indexOf("\n", i)) };
eval(line("const MX_JOIN_GAP").replace(/^const /, "var ") + "\n" + pick("function mxJoin(rows)"));
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
const R = (t, end, text, spk = 1, x = {}) => Object.assign({t, end, text, spk}, x);
const out = mxJoin([R(126, 128, "It didn't actually open"), R(128.1, 129.8, "uh, in a"), R(129.9, 132.7, "It did open a new window, but it didn't"),
  R(132.8, 136, "change the focus of the screen."), R(137.1, 140, "Which I clicked on to there."), R(140.5, 141, "Yes", 2), R(150, 151, "later", 2)]);
check("a sentence split at pauses: one line, from its first start", out.length === 4 && out[0].t === 126 && out[0].end === 136
  && out[0].text === "It didn't actually open uh, in a It did open a new window, but it didn't change the focus of the screen.", out);
check("  a finished sentence, another speaker, a long gap: lines of their own", out.slice(1).map(r => r.text).join("|") === "Which I clicked on to there.|Yes|later", out);
const fixed = mxJoin([R(0, 1, "the staging", 1, {heard: "the stage in"}), R(1.2, 2, "box")]);
check("  a line with learned word fixes is never joined (its fixes are per line)", fixed.length === 2, fixed);
check("the words said now: in the row at the bottom, under the transcript, never in it (the user, 2026-10-25)",
  !/add\("partial"/.test(src) && /words = paused \? "" : String\(\(lv && lv\.partial\) \|\| ""\)\.trim\(\)/.test(src));
console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
