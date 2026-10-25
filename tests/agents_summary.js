// An ended meeting's or call's summary: once its card exists (memory set) it is final, points or not;
// before that, on its way for 3 minutes, then given up on. Keep and Forget are gone: nothing is asked.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
eval(src.slice(src.indexOf("const MX_SUMMARY_WAIT"), src.indexOf("\n", src.indexOf("const MX_SUMMARY_WAIT"))).replace(/^const /, "var ") + "\n" + pick("function mxSummary(m, now)"));
let fails = 0; const check = (label, ok) => { console.log("  " + label.padEnd(64) + (ok ? "OK" : "FAIL")); if (!ok) fails++ };
const now = 1_800_000_000;
check("a 26 s test call, card written, no points: Nothing decided", mxSummary({kind: "call", points: [], memory: "pending", ended_at: now - 30}, now) === "none");
check("with a gist and no points: the gist", mxSummary({points: [], gist: "A sound check.", memory: "pending"}, now) === "gist");
check("points: the points", mxSummary({points: ["Ship it"], memory: "kept"}, now) === "points");
check("no card yet, just ended: on its way", mxSummary({points: [], ended_at: now - 20}, now) === "wait");
check("no card after 3 minutes: given up (No summary)", mxSummary({points: [], ended_at: now - 200}, now) === "gone");
check("an older card with points and no memory field: its points", mxSummary({points: ["Ship it"], ended_at: now - 9000}, now) === "points");
check("forgotten, no points: still final", mxSummary({points: [], memory: "forgotten"}, now) === "none");
check("made again: on its way, not the old gist", mxSummary({points: [], gist: "Standby couldn't summarize this meeting.", again: true, memory: "pending", ended_at: now - 9000}, now) === "wait");
// the summary box asks nothing: no Keep or Forget, pending or not
const html = pick("function mxSummaryHtml(m, cls)");
check("no Keep or Forget anywhere in the view", !/Keep summary|Forget it|mxMemory/.test(src) && !/mxMemory/.test(html));
const esc = x => String(x), page = eval("(" + html.replace(/^function mxSummaryHtml/, "function") + ")");
check("failed: Summarize again beside it; else not",
      page({points: [], gist: "Standby couldn't summarize this meeting.", failed: true, memory: "pending", session: "s1"}, "card").includes("mxAgain('s1')")
      && !page({points: [], gist: "A sound check.", memory: "pending", session: "s1"}, "card").includes("Summarize again"));
console.log(fails ? "\nFAILURES: " + fails : "\nALL PASS"); process.exit(fails ? 1 : 0);
