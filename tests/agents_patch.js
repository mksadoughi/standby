// The timeline's repaint (mxPatch): a chunk whose key and markup are unchanged keeps its very
// nodes; a changed or new one is drawn fresh; the order follows the chunks; what's gone goes.
const fs = require("fs"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "mac", "agents.js"), "utf8");
const pick = name => { const i = src.indexOf(name); if (i < 0) throw new Error(name + " not found"); return src.slice(i, src.indexOf("\n}\n", i) + 2) };

// Just enough DOM: a chunk's markup becomes one element that remembers it
class Node_ {
  constructor(h) { this.h = h; this.nodeType = 1; this.parentNode = null; this.childNodes = [] }
  get firstChild() { return this.childNodes[0] || null }
  get nextSibling() { const p = this.parentNode; if (!p) return null; return p.childNodes[p.childNodes.indexOf(this) + 1] || null }
  set textContent(v) { for (const n of this.childNodes) n.parentNode = null; this.childNodes = [] }
  insertBefore(n, ref) { if (n.parentNode) n.remove(); const i = ref ? this.childNodes.indexOf(ref) : -1; this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, n); n.parentNode = this; return n }
  remove() { const p = this.parentNode; if (p) { p.childNodes.splice(p.childNodes.indexOf(this), 1); this.parentNode = null } }
}
global.document = {createElement: () => ({set innerHTML(h) { this.content = {childNodes: [new Node_(h)]} }})};
eval("var MXT = null;\n" + pick("function mxPatch(el, chunks, fresh)"));

let fails = 0; const check = (label, ok) => { console.log("  " + label.padEnd(64) + (ok ? "OK" : "FAIL")); if (!ok) fails++ };
const box = new Node_("body"), html = () => box.childNodes.map(n => n.h).join("|");

let fresh = mxPatch(box, [{k: "a", h: "A"}, {k: "b", h: "B"}, {k: "c", h: "C"}]);
check("first paint: every chunk drawn, in order", html() === "A|B|C" && fresh.length === 3);
const [a, b, c] = box.childNodes;

fresh = mxPatch(box, [{k: "a", h: "A"}, {k: "b", h: "B"}, {k: "c", h: "C"}]);
check("nothing changed: nothing drawn, the same nodes", fresh.length === 0 && box.childNodes[0] === a && box.childNodes[2] === c);

fresh = mxPatch(box, [{k: "a", h: "A"}, {k: "b", h: "B"}, {k: "c", h: "C"}, {k: "d", h: "D"}]);
check("a new message: only it is drawn, at the end", fresh.length === 1 && html() === "A|B|C|D" && box.childNodes[1] === b);

fresh = mxPatch(box, [{k: "a", h: "A"}, {k: "b", h: "B2"}, {k: "c", h: "C"}, {k: "d", h: "D"}]);
check("one message changed (a draft growing): only it is redrawn", fresh.length === 1 && html() === "A|B2|C|D" && box.childNodes[0] === a && box.childNodes[2] === c);

const d = box.childNodes[3];
fresh = mxPatch(box, [{k: "a", h: "A"}, {k: "c", h: "C"}, {k: "e", h: "E"}, {k: "d", h: "D"}]);
check("one gone, one in its place (tmp- to its real id): remove plus insert", html() === "A|C|E|D" && fresh.length === 1 && box.childNodes[3] === d);

fresh = mxPatch(box, [{k: "x", h: "X"}, {k: "a", h: "A"}, {k: "c", h: "C"}, {k: "e", h: "E"}, {k: "d", h: "D"}]);
check("older ones above: drawn above, the rest kept", html() === "X|A|C|E|D" && fresh.length === 1 && box.childNodes[1] === a);

fresh = mxPatch(box, [{k: "a", h: "A"}, {k: "a", h: "A"}]);
check("the same key twice: both shown, no node used twice", html() === "A|A" && box.childNodes[0] !== box.childNodes[1]);

fresh = mxPatch(box, [{k: "col", h: "COL", kids: [{k: "m1", h: "M1"}, {k: "m2", h: "M2"}]}]);
const col = box.childNodes[0], m1 = col.childNodes[0];
check("a container: its kids go inside it", html() === "COL" && col.childNodes.map(n => n.h).join("|") === "M1|M2" && fresh.length === 3);
fresh = mxPatch(box, [{k: "col", h: "COL", kids: [{k: "m1", h: "M1"}, {k: "m2", h: "M2"}, {k: "m3", h: "M3"}]}]);
check("and a new kid is drawn alone, the container and the rest kept", fresh.length === 1 && box.childNodes[0] === col && col.childNodes[0] === m1 && col.childNodes.length === 3);

box._chunks = null;
fresh = mxPatch(box, [{k: "a", h: "A"}]);
check("asked for a whole new body (_h = null): all drawn again", html() === "A" && fresh.length === 1);

// The page: events paint once a frame
const paint = pick("function mxPaint()"), events = pick("function mxEvents()");
check("no event handler paints the whole view at once", !/mxPaint\(\)/.test(events) && /mxPaintSoon/.test(events));
check("typing in a rename or a speaker's name: the body is left alone", /spkEditing \|\| MX\.editing \? null : mxPatch/.test(paint));

console.log(fails ? "\nFAILURES: " + fails : "\nALL PASS"); process.exit(fails ? 1 : 0);
