// Deleting a meeting or call (the user, 2026-10-22): from its row's ⋯ (or a right-click) and from the open
// one's ⋯; asked first, in words; never for what's live. Done, it's off the list, and closed if it was open;
// refused, nothing changes and the reason is said. Deleted anywhere (the "deleted" event), every window drops it.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
var esc = s => String(s == null ? "" : s), MXI = {more: ""}, MXW = {}, $ = () => null, toasts = [], posts = [], asked = [], menus = [], closed = 0, painted = 0, loads = 0;
var MX = {items: [], exp: null}, answer = true, reply = {ok: true};
var toast = t => toasts.push(t), ask = async (t, b, ok, danger) => { asked.push([t, b, ok, danger]); return answer };
var mxGet = async (p, b) => { posts.push([p, b]); return reply }, mxLoad = () => { loads++ }, mxMenuClose = () => {}, mxKeepIn = () => {};
var mxMenu = (e, html) => { menus.push(html); return true }, mxCloseWin = () => { closed++; MX.exp = null }, mxPaint = () => { painted++ }, mxFocusOn = () => false, mxPassItems = () => "";
for (const n of ["function mxRowMenu(e, sid)", "async function mxDelete(sid)", "function mxGone(sid)", "function mxWinMenu(e, id)"]) eval(pick(n).replace(/^(async )?function (\w+)/, "var $2 = $1function"));
const reset = () => { MX.items = [{id: "c1", session: "s1", kind: "call"}, {id: "c2", session: "s2", kind: "meeting", live: true}, {id: "c3", session: "s3", kind: "meeting"}];
  MX.exp = null; toasts = []; posts = []; asked = []; menus = []; closed = 0; answer = true; reply = {ok: true} };
(async () => {
  reset();
  mxRowMenu({type: "click"}, "s2");
  check("what's live: no row menu", menus.length === 0);
  mxRowMenu({type: "click"}, "s1");
  check("a past one's row: Copy, Download and Delete…, in the danger colour", menus.length === 1 && /Copy transcript/.test(menus[0]) && /Download as Markdown/.test(menus[0]) && /class="mi danger"[^>]*mxDelete\('s1'\)">Delete…/.test(menus[0]), menus);
  mxWinMenu({}, "c2"); mxWinMenu({}, "c3");
  check("the open one's ⋯: Delete… once it's over, not while it's live", !/mxDelete/.test(menus[1]) && /mxDelete\('s3'\)/.test(menus[2]), menus.slice(1));
  answer = false; await mxDelete("s1");
  check("asked first, in words; Cancel deletes nothing", asked.length === 1 && asked[0][0] === "Delete this call?" && asked[0][1] === "Its recording, transcript and summary are removed." && asked[0][2] === "Delete" && asked[0][3] === true && posts.length === 0, asked);
  answer = true; MX.exp = "s3"; await mxDelete("s3");
  check("Delete: the route asked, off the list, and the open one closes", asked[1][0] === "Delete this meeting?" && posts[0][0] === "/api/msg/delete" && posts[0][1].session === "s3" && !MX.items.some(x => x.session === "s3") && closed === 1, {posts, items: MX.items});
  posts = []; await mxDelete("s2");
  check("what's live: refused here, never asked or sent", toasts.includes("End it first") && posts.length === 0 && asked.length === 2);
  reply = {error: "That one is still live: end it first"}; await mxDelete("s1");
  check("refused by the server: its words said, the row kept", toasts.includes("That one is still live: end it first") && MX.items.some(x => x.session === "s1"), toasts);
  mxGone("s1");
  check("deleted elsewhere (the event): off the list", !MX.items.some(x => x.session === "s1"));
  check("  the page listens for it, and a right-click on a row opens its menu", /addEventListener\("deleted"/.test(src) && /addEventListener\("contextmenu"[\s\S]{0,200}mx-prow\[data-sid\]/.test(src));
  console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
})();
