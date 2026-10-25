// A menu's button opens it and closes it again (the user, 2026-10-21: the title's menu didn't close on a
// second click), the button known by what it does since its header is drawn afresh; Esc and a click outside
// close it too. And it stays inside the window: the title's menu, 280 px from the title, went off the right.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };
let fails = 0; const check = (label, ok, d) => { console.log("  " + label.padEnd(70) + (ok ? "OK" : "FAIL")); if (!ok) { fails++; console.log("    " + JSON.stringify(d)) } };
var innerWidth = 420, innerHeight = 720, open = null;
const menu = () => { const m = {style: {}, setAttribute() {}, remove() { if (open === m) open = null },
  getBoundingClientRect() { const w = parseFloat(m.style.width) || 230, l = m.style.left !== undefined && m.style.left !== "auto" && m.style.left !== "" ? parseFloat(m.style.left) : innerWidth - parseFloat(m.style.right) - w;
    return {left: l, right: l + w, width: w, top: parseFloat(m.style.top), bottom: parseFloat(m.style.top) + 200, height: 200} }}; return m };
var document = {createElement: () => menu(), body: {appendChild(m) { open = m }}};
var $ = id => id === "mxmenu" ? open : null;
var mxNav = () => "<nav></nav>";
for (const n of ["function mxMenu(", "function mxKeepIn(", "function mxMenuClose(", "function mxScopeMenu("]) eval(pick(n));
// the title's button, as each paint draws it: a new element, the same onclick
const title = () => ({getAttribute: a => a === "onclick" ? "mxScopeMenu(event)" : null, getBoundingClientRect: () => ({left: 120, right: 300, bottom: 46})});
const ev = t => ({stopPropagation() {}, currentTarget: t});
mxScopeMenu(ev(title()));
check("a click on the title opens its menu", !!open);
const r = open.getBoundingClientRect();
check("  inside the window: its right edge 8 px in at most", r.right <= innerWidth - 8 && r.left >= 8, r);
mxScopeMenu(ev(title()));
check("  a second click (the header drawn afresh in between) closes it", open === null);
mxScopeMenu(ev(title()));
check("  and a third opens it again", !!open);
const gear = {getAttribute: () => "mxWinMenu(event,'c1')", getBoundingClientRect: () => ({left: 370, right: 404, bottom: 46})};
mxMenu(ev(gear), "<b></b>", "mx-menu");
check("another button's menu takes its place", !!open && open._by === "mxWinMenu(event,'c1')");
check("Esc and a click outside close it (the page's own handlers)", /addEventListener\("keydown", e => \{ if \(e.key === "Escape"\) mxMenuClose\(\)/.test(src)
  && /addEventListener\("click", e => \{ if \(\$\("mxmenu"\) && !e.target.closest\("#mxmenu"\)\) mxMenuClose\(\)/.test(src));
console.log(fails ? "FAILURES: " + fails : "ALL PASS"); process.exit(fails ? 1 : 0);
