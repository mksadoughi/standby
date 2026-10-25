// The call view updates live and ends cleanly, one way whoever ends it; one poller keeps what's on screen
// fresh. A call ended here (End, the overlay's close) stops its voice and ends it on the server, once;
// one that ended elsewhere (the event stream, the voice reading off) is only let go, never ended again.
// A call on screen when it ends stays there, ended: the same call at its summary.
// The focused call's conversation is drawn from the poller's record: no fetch or timer of its own.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
const cls = new Set(), els = {voice: {style: {display: ""}, classList: {remove: c => cls.delete(c), add: c => cls.add(c), contains: c => cls.has(c)}}};
var $ = id => id === "mxdock" ? null : els[id] || (els[id] = {style: {}, classList: {remove() {}, add() {}}, scrollHeight: 0, scrollTop: 0, clientHeight: 0});
var posts = [], fetched = [], loads = 0, painted = 0, voice = null, spkEditing = false, focus = true;
var api = async (p, b) => { if (b !== undefined) posts.push([p, b]); else fetched.push(p); return p.startsWith("/api/session/") ? {info: {id: p.split("/").pop()}, transcript: [], chat: [{role: "me", text: "hi", at: 1}]} : {ok: true} };
var VTASKS = null, mxFadeIn = () => {}, openedWin = [], mxOpenWin = async s => { openedWin.push(s) }, mxToSummary = () => {};
var mxGet = api, mxLoad = () => { loads++ }, mxPaint = () => { painted++ }, mxFocusOn = () => focus;
var mxPatch = (el, chunks) => { el.drawn = chunks.length }, mxRows = d => (d.chat || []).map((c, n) => ({k: "c:" + n}));
var MX = {view: {kind: "past"}, call: null, agents: [{id: "a1", live: {call: "talk-1"}}], items: []}, mxAg = id => MX.agents.find(a => a.id === id);
for (const n of ["mxCallGone", "async function mxEndCall", "async function mxFetch", "async function mxCallLog", "async function mxPoll", "function mxTick"])
  eval(pick(n.startsWith("async") || n.startsWith("function") ? n : "function " + n).replace(/^(async )?function (\w+)/, "var $2 = $1function"));
eval(src.match(/const MXW = \{\};\n/)[0].replace("const", "var")); eval(src.match(/let MXN = 0;\n/)[0].replace("let", "var"));
var mxWatch = () => { MX.watch = MX.call ? [MX.call.session] : [] };
(async () => {
  // live: the poller fetches the call's record each tick, the list every other one, the log drawn from it
  MX.call = {agent: "a1", session: "talk-1", t0: 1}; MX.fc = {kind: "call", session: "talk-1"}; voice = {sid: "talk-1"}; mxWatch();
  mxTick(); await new Promise(r => setTimeout(r, 10));
  check("live: one tick fetches the call's record once, and draws its conversation from it", fetched.filter(p => p === "/api/session/talk-1").length === 1 && els.mxcalllog.drawn === 1, fetched);
  mxTick(); await new Promise(r => setTimeout(r, 10));
  check("  the list and what's live every other tick, the call's record every tick", loads === 1 && fetched.filter(p => p === "/api/session/talk-1").length === 2 && fetched.filter(p => p === "/api/live/talk-1").length === 2, {loads, fetched});
  // ended here: the voice stops, the server ends it, once; let go
  await mxEndCall();
  check("End here: its voice stops, then the call ends on the server", posts.map(p => p[0] + (p[1].action ? ":" + p[1].action : "")).join() === "/api/voice:stop,/api/msg/call/end", posts);
  check("  and the view lets it go: no call, its view hidden, no voice loop, its agent not live", MX.call === null && els.voice.style.display === "none" && voice === null && MX.agents[0].live.call === null);
  check("  on screen when it ended: the same call, open at its summary", openedWin.join() === "talk-1", openedWin);
  posts = []; await mxEndCall();
  check("  a second End does nothing", posts.length === 0);
  // ended elsewhere: only let go, never ended again
  const c = MX.call = {agent: "a1", session: "talk-2", t0: 2}; voice = {sid: "talk-2"}; MX.agents[0].live.call = "talk-2";
  check("ended elsewhere (the event stream): let go, nothing sent", mxCallGone(c) === true && MX.call === null && posts.length === 0 && voice === null);
  check("  not on screen (the list was): nothing opened", openedWin.length === 1, openedWin);
  check("  a stale end for a call no longer up does nothing", mxCallGone(c) === false);
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
