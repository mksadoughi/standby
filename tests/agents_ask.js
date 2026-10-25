// The recorder's question while a meeting records ("AirPods Pro connected: record from their mic?"), in the
// meeting's window now that the orb is calls only: the question, its why in small, its answers (the likely
// one first, filled); answered here, it goes at once and the answer goes to /api/capture/answer.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
var esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
var posts = [], painted = 0, polled = 0, toasts = [], mxGet = async (p, b) => { posts.push([p, b]); return {ok: true} }, mxPaint = () => { painted++ }, mxPoll = () => { polled++ }, toast = t => toasts.push(t);
eval(src.match(/const MXASKED = [^\n]*\n/)[0].replace("const", "var"));
eval(pick("function mxAskHtml(q)")); eval(pick("async function mxAnswerAsk(id, value)").replace("async function mxAnswerAsk", "var mxAnswerAsk = async function"));
const q = {id: "mic:airpods", text: "AirPods Pro connected: record from their mic? Their sound drops to call quality while it records.",
  actions: [{label: "Switch", value: "use"}, {label: "Keep this mic", value: "keep"}]};
(async () => {
  const h = mxAskHtml(q);
  check("the question, then its why in small", /<b>AirPods Pro connected: record from their mic\?<\/b><small>Their sound drops/.test(h), h);
  check("  its answers, the likely one first and filled", /class="vtxt first"[^>]*>Switch<\/button><button class="vtxt"[^>]*>Keep this mic</.test(h), h);
  check("  in the meeting's window: drawn under its header when live", /k: "ask", h: mxAskHtml\(live && !paused && d && d\.info && d\.info\.ask\)/.test(src));
  check("no question: nothing", mxAskHtml(null) === "" && mxAskHtml({}) === "");
  await mxAnswerAsk("mic:airpods", "use");
  check("answered: gone at once, the answer sent, the meeting fetched again", mxAskHtml(q) === "" && posts[0][0] === "/api/capture/answer" && posts[0][1].id === "mic:airpods" && posts[0][1].value === "use" && painted === 1 && polled === 1, posts);
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
