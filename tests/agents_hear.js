// What's live in a meeting, in one row at the bottom (the user, 2026-10-25: option B): its sound as red bars,
// "Listening" and its sources small ("Your mic · Google Chrome ⌄"), the newest words, Pause and Stop. A click on
// the sources opens them above the row, each with its tick. Off, a source carries a calm "not listened to"
// mark (a muted speaker) and says if it's still playing. A tick sends /api/live/source at once and follows
// before the recorder answers; refused, it's put back and why said. One that joins later is "new" for a few
// seconds. Paused: grey bars, Resume, no words. The transcript marks a source turned off.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
var esc = s => String(s == null ? "" : s), MXI = {check: "✓", chev: "<svg></svg>", pause: "‖", play: "▶", stop: "■"}, MX = {}, posts = [], toasts = [], painted = 0, reply = {ok: true};
var mxGet = async (p, b) => { posts.push([p, b]); return reply }, mxPoll = async () => {}, mxPaint = () => { painted++ }, toast = t => toasts.push(t);
for (const n of ["const MXHEAR = ", "const MXBARS = ", "const MXNOEAR = "]) eval(src.match(new RegExp(n + "[^\\n]*\\n"))[0].replace("const", "var"));
eval(pick("function mxMeetDock(m, lv)")); eval(pick("async function mxSource(sid, id, on)").replace("async function mxSource", "var mxSource = async function"));
const mic = {id: "mic", kind: "mic", name: "Your mic", detail: "AirPods Pro", on: true, heard: true}, zoom = {id: "us.zoom.xos", kind: "app", name: "Zoom", detail: "", on: true, heard: false};
const M = {session: "s1", kind: "meeting", live: true}, dock = (lv, m) => mxMeetDock(m || M, lv).map(c => c.h).join("\n");
(async () => {
  let h = dock({hearing: [mic, zoom], partial: ""});
  check("the row: red bars, Listening and its sources small, Pause and Stop", /mx-recb"/.test(h) && /Listening · <button class="mx-dsrc"[^>]*><span class="">Your mic<\/span> · <span class="">Zoom<\/span>/.test(h)
    && /title="Pause"/.test(h) && /title="Stop"/.test(h) && !/mx-dpop/.test(h), h);
  h = dock({hearing: [mic, zoom], partial: "so for guests we keep two minutes"});
  check("someone talking: the newest words, with a caret", /class="mx-dwords"><span>so for guests we keep two minutes<span class="caret">/.test(h), h);
  check("  no list from the recorder yet: no sources, still the row", /Listening<\/div>/.test(dock({})) && !/mx-dsrc/.test(dock({})));
  check("  those there from the start: not \"new\"", !/class="new"/.test(h));
  MXHEAR.open = true; h = dock({hearing: [mic, zoom], partial: ""});
  check("open: every source above the row, a tick each, the mic's device under its name", /mx-dpop/.test(h) && (h.match(/class="mx-src"/g) || []).length === 2 && /Your mic<small>AirPods Pro<\/small>/.test(h), h);
  const p = mxSource("s1", "us.zoom.xos", false);
  h = dock({hearing: [mic, Object.assign({}, zoom, {heard: true})], partial: ""});
  check("untick Zoom while it plays: off at once, \"Playing, not listened to\", the muted-speaker mark",
    /class="mx-src off"[^>]*aria-checked="false"[\s\S]*Zoom<small>Playing, not listened to<\/small><\/span><svg class="mx-noear"/.test(h), h);
  check("  in the row: struck with the same calm mark", /<s title="Not listened to"><svg class="mx-noear"[^]*?<\/svg>Zoom<\/s>/.test(h), h);
  await p;
  check("  sent to the recorder: {session, id, on: false}", posts[0][0] === "/api/live/source" && posts[0][1].id === "us.zoom.xos" && posts[0][1].on === false, posts);
  check("  off and quiet: \"Not listened to\"", /Zoom<small>Not listened to<\/small>/.test(dock({hearing: [mic, Object.assign({}, zoom, {on: false, heard: false})]})));
  reply = {error: "Standby isn't hearing that any more"}; await mxSource("s1", "us.zoom.xos", false);
  check("  refused: put back (the recorder's own word), and why said", /class="mx-src"[^>]*onclick="event.stopPropagation\(\);mxSource\('s1','us.zoom.xos',false\)"/.test(dock({hearing: [mic, zoom]})) && toasts.includes("Standby isn't hearing that any more"));
  h = dock({hearing: [mic, zoom, {id: "com.google.Chrome", kind: "app", name: "Google Chrome", on: true, heard: true}]});
  check("a source that joins later: on, \"new\" for a few seconds", /<span class="new">Google Chrome<\/span>/.test(h), h);
  MXHEAR.open = false; h = dock({hearing: [mic], partial: "words"}, {session: "s1", kind: "meeting", paused: true});
  check("paused: grey bars, Paused, Resume, no words", /mx-recb off/.test(h) && /Paused · /.test(h) && /title="Resume"/.test(h) && !/caret/.test(h), h);
  check("the transcript marks a source turned off, while and after", /e\.kind === "source_off" && e\.name\) seps\.push\(sep\(tOf\(e\.from\), len === null \? e\.name \+ " is off" : e\.name \+ " was off · " \+ mxMin\(len\)\)\)/.test(src));
  check("  the row for a live or paused meeting only, never a call", /dock = exp && exp\.kind !== "call" && \(exp\.live \|\| exp\.paused\) \? mxMeetDock\(exp/.test(src));
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
