// Standby opened on its own: the past meetings and calls, newest first by day, what's live pinned on top,
// nothing else (no chat). The navigator, read-only: each folder's Claude Code sessions that recorded
// something, All at the top; picking one lists only its meetings and calls. A row is titled by what Claude
// called (the user's title, else Claude's short topic; never a gist or a failed summary; with nothing better,
// "Call · 11:22"), and in All says which session it was in.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
const line = re => src.match(re)[0].replace(/^const /, "var ");
var MX = {items: [], loaded: true, scope: "all", ws: [], agents: []}, MXW = {}, SPIN = "", EMPTYWAVE = "", MXI = {phone: "P", wave: "W", list: "L", folder: "F", chev: "V"};
var esc = s => String(s), clock = ts => new Date(ts * 1000).toTimeString().slice(0, 5), tilde = s => s;
eval(line(/const mxAg = [^\n]*\n/)); eval(line(/const mxWs = [^\n]*\n/));
eval(line(/const mxMin = [^\n]*\n/));
eval(pick("function mxTitle(m, d)"));
eval(pick("const mxDay = at =>").replace("const mxDay", "var mxDay"));
eval(src.match(/const MXDEFAULT = [^\n]*\n/)[0].replace("const", "var")); eval(pick("function mxPastTitle(m)"));
eval(pick("function mxPastRow(m)"));
eval(line(/let MXPA = [^\n]*\n/).replace(/^let /, "var ")); eval(line(/const mxPastAll = [^\n]*\n/)); eval(line(/const mxPastIn = [^\n]*\n/));
eval(pick("function mxPast()"));
eval(pick("function mxSessions()"));
eval(pick("function mxScopeName()"));
eval(pick("function mxNav()"));
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
const now = Date.now() / 1000, day = 86400;
check("nothing yet: how to start one, in Claude Code", /No meetings or calls yet/.test(mxPast()) && /\/standby listen/.test(mxPast()));
MX.ws = [{id: "w1", name: "orion-api", folder: "/Users/you/code/orion-api", agents: ["a1", "a2", "a3"]}, {id: "w2", name: "empty", agents: ["a4"]}];
MX.agents = [{id: "a1", workspace: "w1", name: "Guest TTL fix", title: "Guest TTL fix", live: {}}, {id: "a2", workspace: "w1", name: "Fares", title: "Fares", live: {meeting: "meeting-3"}},
  {id: "a3", workspace: "w1", name: "Never recorded", live: {}}, {id: "a4", workspace: "w2", name: "Nothing here", live: {}}];
MX.items = [
  {kind: "meeting", session: "meeting-1", from: "a1", at: now - 3 * day, duration_s: 1920, title: "Orion weekly", names: ["Nadia", "Theo"]},
  {kind: "call", session: "talk-1", from: "a1", at: now - 60, duration_s: 240, topic: "Ran the cache tests", gist: "A call just connected; the agent asked what to dig into, n"},
  {kind: "call", session: "talk-2", from: "a1", at: now - 2 * day, duration_s: 60},
  {kind: "text", from: "a1", at: now - 30, text: "a chat message"},
  {kind: "meeting", session: "meeting-2", from: "a1", at: now - day, duration_s: 600, title: "Meeting · Oct 23, 10:18", topic: "The fare page numbers", gist: "Standby couldn't summarize this meeting."},
  {kind: "meeting", session: "meeting-4", from: "a1", at: now - day - 60, duration_s: 600, title: ""},
  {kind: "meeting", session: "meeting-3", from: "a2", at: now - 300, live: true, duration_s: 240, title: "Standup", names: []}];
let h = mxPast(); const at = s => h.indexOf(s);
check("what's live is first, pinned", at("Standup") >= 0 && at("Standup") < at(">Today<") && /mx-prow live/.test(h), h);
check("then newest first, under its day", at(">Today<") < at("Ran the cache tests") && at("Ran the cache tests") < at(">Yesterday<") && at(">Yesterday<") < at("Orion weekly"));
check("a call: its topic, then kind, length and its session (All)", /Ran the cache tests<\/b><small>Call · 4 min · Guest TTL fix</.test(h), h);
check("  no topic: plainly a call and when (\"Call · 11:22\"), never a gist", /<b>Call · \d{1,2}:\d\d( [AP]M)?<\/b>/.test(h) && !/A call just connected|Call with Claude/.test(h), h);
check("a meeting: its title, length, session and who spoke", /Orion weekly<\/b><small>Meeting · 32 min · Guest TTL fix · Nadia, Theo</.test(h));
check("  untitled: its topic, never its failed summary; else plainly a meeting and when", /<b>The fare page numbers<\/b>/.test(h) && /<b>Meeting · \d{1,2}:\d\d/.test(h) && !/couldn.t summarize|<b>Meeting · Oct/.test(h), h);
check("no chat messages", !/a chat message/.test(h));
check("each row opens its session", /mxPastOpen\('meeting-1'\)/.test(h) && /mxPastOpen\('talk-1'\)/.test(h));
const nav = mxNav();
check("the navigator: All with every meeting and call", /mxNavPick\('all'\)/.test(nav) && /All meetings and calls<\/span><span class="n">6</.test(nav), nav);
check("  each folder, its sessions that recorded something", /orion-api/.test(nav) && /Guest TTL fix<\/span><span class="n">5</.test(nav)
  && !/Never recorded/.test(nav) && !/>empty</.test(nav), nav);
check("  a red dot on the one with something live", /Fares<\/span><span class="dot"/.test(nav), nav);
check("  read-only: no add, remove or rename", !/mxAdd|mxRemove|mxEdit|Rename|Remove/.test(nav));
MX.scope = "a2"; h = mxPast();
check("a session picked: only its meetings and calls", /Standup/.test(h) && !/Orion weekly/.test(h) && mxScopeName() === "Fares");
MX.scope = "all"; MX.agents[0].title = ""; MX.agents[0].name = "Claude Code session"; h = mxPast();
check("a session with no name yet: its rows leave it off", /Ran the cache tests<\/b><small>Call · 4 min</.test(h) && !/· Claude Code session/.test(h), h);
MX.scope = "a2"; h = mxPast();
check("  rows don't repeat the session there", /Standup<\/b><small>Listening · 4 min</.test(h), h);
// The meeting's own sound refused (Screen & System Audio Recording): said in the meeting window, with Allow;
// on already but for an older copy of Standby: Reset (the old view, which said so, is gone)
var STALE_WHY = "older copy";
eval(line(/const mxSoundNo = [^\n]*\n/));
eval(src.match(/const mxSoundNoHtml = [\s\S]*?<\/div>`;\n/)[0].replace("const ", "var "));
const refused = mxSoundNo({sources: [{kind: "mic", cause: "permission"}, {kind: "meeting", cause: "permission", detail: "Allow it"}]});
check("meeting audio not allowed: its line, with Allow and It's on already", refused && /Meeting audio isn't allowed/.test(mxSoundNoHtml(refused))
  && /allowScreen\(\)/.test(mxSoundNoHtml(refused)) && /resetScreen\(\)/.test(mxSoundNoHtml(refused)));
check("  on for an older copy: Reset", /older copy of Standby[\s\S]*resetScreen\(\)/.test(mxSoundNoHtml({stale: true})) && !/allowScreen/.test(mxSoundNoHtml({stale: true})));
check("  allowed: no line", mxSoundNo({sources: [{kind: "meeting", state: "recording"}]}) === null);
// the body carries .mx-past: no rule may style that class on its own (one capped the whole window at 680 px)
const css = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.css"), "utf8");
check("the window's body is never styled as the list (full width, the navigator at the left edge)",
  !/(^|[\s,}])\.mx-past[\s{,:]/m.test(css.replace(/\/\*[\s\S]*?\*\//g, "").replace(/body\.mx-past/g, "")), css.match(/[^\n]*\.mx-past[^\n]*/g));
console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
