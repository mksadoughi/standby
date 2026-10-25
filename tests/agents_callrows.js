// A call's conversation (live under the call view, and in a past call's transcript: the same rows) shows two
// kinds of entry (the user's design, 2026-10-22): what you said, the mic first; what the voice said to you,
// Standby's mark first. The voice and the session are one self: a hand-off is no row of its own and no
// "Claude Code" wording; only what the voice really said as it handed off (not the app's old stock "I'm on
// it."), and what comes back is never shown. While deeper work runs
// on a live call, one "✻ Working on it…" line under the rest, gone when it's done; a past call has none.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
var esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
var clock = t => "10:0" + (t % 10), md = t => "<p>" + esc(t) + "</p>", speakerLabel = () => null, fixedWords = r => esc(r.text), SPIN = "";
var MXLOGO = '<span class="logo"></span>', MXCP = "";
eval(src.match(/const MXMIC = [^\n]*\n/)[0].replace("const", "var"));
eval(src.match(/const mxMin = [^\n]*\n/)[0].replace("const", "var"));
eval(src.match(/const MX_JOIN_GAP = [^\n]*\n/)[0].replace("const", "var"));
eval(pick("function mxJoin(rows)")); eval(pick("function mxRows(d, lv, live, paused, calling, tasks)"));
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
const d = {info: {id: "talk-1", runs: []}, transcript: [], speakers: {}, chat: [
  {role: "me", text: "[CALL START] Hi", at: 1, voice: true},
  {role: "me", text: "Run the routing cache tests", at: 2, voice: true},
  {role: "ai", text: "I'm on it.", handoff: "Run the routing cache tests and report", at: 3, voice: true},
  {role: "ai", text: "The tests are done: all 112 pass.", result: "All 112 routing cache tests pass. `test_reroute` is slow.", reason: "done", at: 4, voice: true},
  {role: "ai", text: "", result: "A result the voice said nothing of", reason: "done", at: 5},
  {role: "ai", text: "[HANDOFF] Check the fares cache", at: 6}]};
const rows = mxRows(d, null, false, false), html = rows.map(r => r.h).join("\n");
const said = html.match(/mx-qa( run)?( dropped)?"[^>]*>[^\n]*/g) || [], voice = html.match(/mx-qa ans[^\n]*/g) || [];
check("what you said: your words (no mic glyph on a call's bubbles), the greeting's hidden marker gone", said.length === 2 && !said.some(r => r.includes('class="mx-mic"')) && /qt">Hi</.test(said[0]) && !html.includes("[CALL START]"), said);
check("what the voice said: Standby's mark first, its words", voice.length === 1 && voice[0].includes('class="mx-vmark"><span class="logo"') && voice[0].includes("all 112 pass"), voice);
check("no hand-off row: not its \"I'm on it.\", no \"Claude Code\" wording, no task", !html.includes("I'm on it.") && !/Claude Code/.test(html) && !html.includes("Run the routing cache tests and report") && !html.includes("Check the fares cache") && !html.includes("HANDOFF"), html);
check("what came back: never shown", !html.includes("test_reroute") && !html.includes("A result the voice said nothing of"));
check("a past call: no working line, even with a hand-off never answered (the [HANDOFF] one)", !html.includes("mx-working"));
check("nothing twice: each entry once", rows.length === new Set(rows.map(r => r.k)).size);
// a hand-off with the voice's own words (what it said as it took the work deeper): those show, as said
const own = mxRows(Object.assign({}, d, {chat: [{role: "ai", text: "Understood, I'll take the seconds off.", handoff: "Take the seconds off the band", at: 3, voice: true},
  {role: "ai", text: "", handoff: "Check the logs", at: 4, voice: true}]}), null, false, false).map(r => r.h).join("\n");
check("a hand-off with the voice's own words: shown as its line, the task not", own.includes("Understood, I&#39;ll take the seconds off.") || own.includes("Understood, I'll take the seconds off."), own);
check("  one that said nothing: nothing", (own.match(/mx-qa ans/g) || []).length === 1 && !own.includes("Check the logs"), own);
// live: two hand-offs, one answered so far: the line shows, once, after everything else; both answered: gone
const live = c => mxRows(Object.assign({}, d, {chat: c}), null, false, false, true).map(r => r.h).join("\n");
const two = d.chat.slice(0, 4).concat([{role: "me", text: "And the fares cache?", at: 6, voice: true}, {role: "ai", text: "I'm on it.", handoff: "Check the fares cache", at: 7, voice: true}]);
let h = live(two);
check("live, deeper work running: one \"✻ Working on it\" line, on the voice's side, last", (h.match(/mx-working/g) || []).length === 1 && /mx-star[\s\S]*<\/svg><span>Working on it/.test(h) && h.lastIndexOf("mx-working") > h.lastIndexOf("mx-qa"), h.slice(-300));
h = live(two.concat([{role: "ai", text: "", result: "Fifteen minutes", reason: "done", at: 8, voice: true}]));
check("  the work done: the line goes", !h.includes("mx-working"));
check("  nothing running: no line", !live(d.chat.slice(0, 4)).includes("mx-working"));
// a conversation (the user, 2026-10-25): runs grouped, a quiet time after a pause, only what was said
const C = (role, text, at, x) => Object.assign({role, text, at, voice: true}, x || {});
const conv = mxRows({info: {id: "talk-2", runs: []}, transcript: [], speakers: {}, chat: [
  C("ai", "Hey, I'm here.", 1000), C("ai", "Go ahead.", 1002), C("me", "Run the tests", 1010), C("me", "and the fares cache", 1012),
  C("ai", "The first test is finished. Plain waits were blocked", 1200, {cut: true, heard: "The first test is finished."}),
  C("ai", "A reply never played", 1205, {unsaid: true}), C("me", "one two three", 1210, {dropped: true})]}, null, false, false);
const ch = conv.map(r => r.h).join("\n");
check("a run of the voice's lines: one mark, the next under it (run)", /mx-qa ans"[^\n]*Hey, I'm here/.test(ch) && /mx-qa ans run"[^\n]*Go ahead/.test(ch), ch);
check("  a run of yours: one tail (run)", /class="mx-qa"[^\n]*Run the tests/.test(ch) && /class="mx-qa run"[^\n]*and the fares cache/.test(ch));
check("  a quiet time where the talk resumed after 2 minutes or more, once", (ch.match(/class="mx-time"/g) || []).length === 1 && ch.indexOf("mx-time") < ch.indexOf("The first test"), ch);
check("a reply cut off: what was heard, then \"cut off\"", /The first test is finished\.<span class="mx-cut">— cut off<\/span><\/p>/.test(ch) && !/Plain waits were blocked/.test(ch), ch);
check("  a reply never played: not shown", !/A reply never played/.test(ch));
check("  a turn of yours never answered: marked quietly", /mx-qa dropped"[^\n]*one two three[^\n]*Not answered/.test(ch), ch);
// work by name, from the task records; ended ones go; without records, the old count
const T = (id, state, label) => ({id, state, label});
const tk = mxRows(d, null, false, false, true, [T("t1", "running", "Write the 3000-word essay"), T("t2", "queued", "Note the cut-off words"),
  T("t3", "stalled", "Long input test"), T("t4", "done", "Old one")]).map(r => r.h).join("\n");
check("tasks: working, waiting, no progress lately, each by its label; done ones gone", /mx-task running[\s\S]*Working on: <b>Write the 3000-word essay/.test(tk)
  && /mx-task queued[\s\S]*Waiting: <b>Note the cut-off words/.test(tk) && /mx-task stalled[\s\S]*No progress lately: <b>Long input test/.test(tk) && !/Old one/.test(tk) && !/mx-working/.test(tk), tk);
check("  no records yet (an older app): the old \"Working on it…\" by count", /mx-working/.test(mxRows(Object.assign({}, d, {chat: d.chat.slice(0, 3)}), null, false, false, true).map(r => r.h).join("")));
// words lost over the voice (the recorder's "speech_lost" span): a faint line at that time, never the words
const lost = mxRows({info: {id: "talk-3", runs: [], started: 1000, events: [{kind: "speech_lost", from: 20, to: 23, why: "floor"}]}, transcript: [], speakers: {},
  chat: [C("me", "Before", 1010), C("me", "After", 1030)]}, null, false, false).map(r => r.h).join("\n");
check("words lost over the voice: \"Something you said wasn't heard\", in its place, no words", /mx-sep2">Something you said wasn’t heard</.test(lost)
  && lost.indexOf("Before") < lost.indexOf("wasn’t heard") && lost.indexOf("wasn’t heard") < lost.indexOf("After"), lost);
console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
