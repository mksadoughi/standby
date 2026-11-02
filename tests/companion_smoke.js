// The companion's page (mac/companion.html) against a fake DOM and a fake /api, as
// both of its windows: the orb's tells the panel its size, where the orb sits
// and where the glass goes; the words' says only a mic macOS refuses. The orb is for
// calls only (a meeting never has one). The panel is never key and holds no dialog.
// Run by tests/test_ui_page.sh. The page's code and these checks share one scope (eval),
// as in ui_page_smoke.js, so the checks can call the page's own functions.
const fs = require("fs");
const html = fs.readFileSync(process.env.COMPANION_HTML || __dirname + "/../mac/companion.html", "utf8");
const script = html.slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>"));

const els = {};
const mk = id => (els[id] = { id, textContent: "", innerHTML: "", hidden: false, dataset: {},
  style: { setProperty() {}, removeProperty() {} }, className: "", title: "",
  classList: { t: new Set(), add(c) { this.t.add(c) }, remove(c) { this.t.delete(c) },
    toggle(c, on) { on === undefined ? (this.t.has(c) ? this.t.delete(c) : this.t.add(c)) : (on ? this.t.add(c) : this.t.delete(c)) },
    contains(c) { return this.t.has(c) } },
  addEventListener(t) { (this.on = this.on || []).push(t) }, setAttribute() {}, offsetWidth: 104, getBoundingClientRect: () => ({ width: 180, height: 44, left: 10, right: 190, top: 10, bottom: 54 }),
  get firstChild() { return this.innerHTML ? {} : null },
  // the orb's window keeps its orb and the pill's rail: each its own element here
  querySelector(sel) { return { ".talk": els.talk, ".rail": els.rail, ".ctls": els.ctls }[sel] || els.orb } });
["cmp", "says", "sp", "orb", "talk", "rail", "ctls"].forEach(mk);
// the document's own listeners, kept so a check can fire them (a press, its release)
global.docOn = {};
global.document = { getElementById: id => els[id] || mk(id), addEventListener(kind, fn) { (docOn[kind] = docOn[kind] || []).push(fn) },
  documentElement: { classList: { add() {}, remove() {}, toggle() {} }, style: { setProperty() {} } },
  body: { classList: { add() {}, remove() {}, toggle() {}, contains: c => c === "r" }, dataset: {} } };
global.location = { hash: "", search: "" };
global.window = { webkit: { messageHandlers: { sb: { postMessage: m => posts.push(m) } } } };

global.posts = [];
global.calls = [];
// A live call (the only thing the orb is for), with an agent unless said otherwise.
global.LIVE = (extra, call) => Object.assign({ live: { id: "talk-1", kind: "talk",
  call: call === null ? undefined : { agent: "a-planner", name: "Planner", copy: false, started: Date.now() / 1000 - 134 } },
  starting: null, voice: { state: "off" } }, extra || {});
global.STATE = LIVE();
global.VISLIST = [];
global.screen = { availWidth: 1440, availHeight: 900 };
global.fetch = async (p, o) => { calls.push([p, o && o.body]);
  return { json: async () => p === "/api/companion" ? STATE : p.startsWith("/api/visuals?") ? { visuals: VISLIST, on: true } : { ok: true } } };

// Any error no check saw (an async page call, a timer, a handler) fails the run: an undeclared
// name in the project menu once threw while every check still said OK (found when the folder picker was ported).
const loose = [];
const loosen = kind => e => { loose.push(kind); process.exitCode = 1;
  console.log("  " + kind + ": " + (e && e.stack ? e.stack.split("\n").slice(0, 2).join(" ") : e)) };
process.on("uncaughtException", loosen("uncaught error"));
process.on("unhandledRejection", loosen("unhandled rejection"));
process.on("exit", () => { if (loose.length) console.log("FAILURES: " + loose.length + " error(s) outside the checks") });
let fails = 0;
let LAST = "(none yet)";
global.ok = (label, cond, d) => { LAST = label.trim(); console.log("  " + label.padEnd(58) + (cond ? "OK" : "FAIL"));
  if (!cond) { fails++; console.log("     " + JSON.stringify(d)) } };
// Never hang: a check that never comes back fails the run instead (the suite takes seconds)
// (said with a synchronous write: console.log's buffered lines can be lost to process.exit, hiding where it stuck)
const WATCHDOG = setTimeout(() => { fs.writeSync(1, "FAILURES: timed out (a check never came back); the last one reached: " + LAST + "\n"); process.exit(1) }, 60000);
global.done = async () => { clearTimeout(WATCHDOG); await new Promise(r => setTimeout(r, 0));     // one more turn: a late rejection still counts
  const n = fails + loose.length; console.log(n ? "FAILURES: " + n : "ALL PASS"); process.exit(n ? 1 : 0) };

// Nothing the page paints may reach the orb window's edge, which would cut it straight into a
// faint rectangle (on the user's screen): the pill draws no shadow (its glass is the
// panel's), and the orb's glow at its largest (72, grown 1.07 speaking) stays inside the 12.
{
  const css = html.slice(html.indexOf("<style>"), html.indexOf("</style>"));
  const rule = sel => (new RegExp("(^|[}\\n])" + sel.replace(/[.]/g, "\\.") + "\\{([^}]*)\\}").exec(css) || [])[2] || "";
  ok("the pill draws no shadow: its glass is the panel's", rule(".ctls") && !/box-shadow/.test(rule(".ctls")), rule(".ctls"));
  const outer = ((rule(".orb").match(/box-shadow:([^;}]*)/) || [])[1] || "").split(/,(?![^(]*\))/).filter(x => x && !/inset/.test(x))
    .map(x => { const n = x.replace(/rgba?\([^)]*\)/, "").trim().split(/\s+/).map(parseFloat);
      return Math.max(Math.abs(n[0]), Math.abs(n[1])) + (n[2] || 0) + (n[3] || 0) });
  const reach = Math.max(...outer) * 1.07 + 36 * .07;
  ok("  the orb's glow stays in its window, at 72 and grown", outer.length > 0 && reach <= 12, { outer, reach });
}

const ORB_CHECKS = `
(async () => {
  const tick = ms => new Promise(r => setTimeout(r, ms || 40));
  const fresh = async () => { await poll(true); await tick() };
  // what shows: the orb, and the pill's content while it is out (it rolls back, its content kept a moment)
  const shown = () => els.cmp.innerHTML + (els.rail.classList.contains("out") ? els.ctls.innerHTML : ""),
    last = t => posts.filter(m => m.type === t).slice(-1)[0];
  await fresh();
  ok("a call, its voice not on yet: the orb alone, no red dot and no time", shown().includes("orb") && !/class="[^"]*\brec\b/.test(shown()) && !shown().includes("2:14"), shown());
  ok("  the orb in C · Wave: three bars inside it", shown().includes('<span class="orb" id="orb"><i></i><i></i><i></i></span>'), shown());
  ok("  at rest the orb alone, 52: no glass, in a window of one size, 12 in from its corner", (m => m && m.glyph === 52 && m.glass === null && m.w === 360 && m.h === 112 && m.inset === 12)(last("orb")), last("orb"));
  ok("  and only the orb takes clicks: its circle", (m => m && m.areas.length === 1 && m.areas[0].r === 26 && m.areas[0].x === 360 - 12 - 26 && m.areas[0].y === 112 - 12 - 26)(last("hit")), last("hit"));
  ok("  no old size or caption messages", !posts.some(m => m.type === "size" || m.type === "caption"), posts);
  ok("  no meeting's pieces: no mic button or mark", !shown().includes("mmark") && !shown().includes("Mute your mic"), shown());

  STATE = LIVE({ voice: { state: "listening", level: 0.2 } });
  await fresh();
  ok("its voice on: the orb grows to 88 in the same window, still no glass", els.cmp.className.includes("voice") && (m => m.glyph === 88 && m.glass === null && m.w === 360)(last("orb")), [els.cmp.className, last("orb")]);
  ok("  a click on it pauses the call, naming the agent", els.talk.title === "Click to pause the call with Planner", els.talk.title);
  STATE = LIVE({ voice: { state: "listening", preparing: "Getting the voice ready… 42%", preparing_pct: 42 } });
  await fresh();
  ok("voice getting ready: a ring around the orb, its percent on hover, no words", els.cmp.className.includes("prep") && els.talk.title.includes("42%") && !/Getting/.test(els.sp ? els.sp.textContent : ""), [els.cmp.className, els.talk.title]);
  STATE = LIVE({ voice: { state: "thinking", step: "Looking it up online" } });
  await fresh();
  ok("  thinking: no state words in the orb's window", !/Looking|Thinking|Listening/.test(shown()), shown());
  open = true; render();
  ok("  pointed at: one panel on its own glass, rolled out from under the orb (two rows, inside the orb's height), and it takes clicks too",
     (m => m.glass && m.glass.h === 72 && m.glass.h < m.glyph && m.glass.radius === 16 && m.under && m.under.w === 72 && m.ms === 340)(last("orb")) && els.rail.classList.contains("out")
     && last("hit").areas.length === 2, [last("orb"), last("hit")]);
  ok("  its top row: the call's session, then the icons under it", (h => h.includes('<span class="nmrow"><span class="dot"></span><span class="nm">') && h.includes('id="ctm"') && h.indexOf("nmrow") < h.indexOf('class="btns"'))(els.ctls.innerHTML)
     && els.ctls.classList.contains("card"), els.ctls.innerHTML.slice(0, 300));
  const words = () => [...els.ctls.innerHTML.matchAll(/<button[^>]*title="([^"]*)"/g)].map(x => x[1]);
  ok("  five icons, each named in its tip: Hide, Full window, Visuals, Settings, End (no Pause, no Mini window: a click on it pauses)", JSON.stringify(words()) === '["Hide the window. The call goes on","Full window","Visuals on","Settings","End"]'
     && !/<button[^>]*>[A-Za-z]/.test(els.ctls.innerHTML) && !shown().includes("More"), words());
  ok("  End is the red hang-up phone, its word in the tip", els.ctls.innerHTML.includes('<button class="hang" title="End" aria-label="End" onclick="end()"><svg'), els.ctls.innerHTML);
  ok("  the pill's glass runs on under the orb to its centre (its inner end hidden): it slides out of the mini window",
     (m => m.glass && m.glass.x + m.glass.w === m.w - 12 - m.glyph / 2)(last("orb")), last("orb"));
  posts = []; callSettings();
  ok("  Settings opens Standby's Settings on its Call pane", JSON.stringify(posts) === '[{"type":"settings","pane":"call"}]', posts);
  posts = []; end(); full();
  ok("  End ends the call through the panel (as End Call); Full window opens Standby's window", posts.map(m => m.type).join() === "end,expand", posts);
  posts = []; hideMini();
  ok("  Hide: the panel hides the mini window (the call goes on)", JSON.stringify(posts) === '[{"type":"hide"}]', posts);
  posts = []; (docOn.keydown || []).forEach(f => f({ key: "Escape" })); (docOn.keydown || []).forEach(f => f({ key: "a" }));
  ok("  Escape hides it too, when it has the keyboard; other keys do nothing", JSON.stringify(posts) === '[{"type":"hide"}]', posts);
  open = false; render();
  ok("  left: the pill rolls back under the orb, its glass with it", !els.rail.classList.contains("out") && (m => m.glass === null && m.under)(last("orb")), last("orb"));
  STATE = LIVE({ voice: { state: "listening", holding: true } });
  await fresh();
  ok("  hold on: the orb holds still", els.cmp.className.includes("holding"), els.cmp.className);
  // Paused, with work that finished meanwhile held for Resume: a red dot on the paused orb, until it is said.
  STATE = LIVE({ voice: { state: "muted", muted: true, waiting: true } });
  await fresh();
  ok("paused with finished work waiting: the red dot on the mini window, and the tip says a click hears it", els.cmp.className.includes("waiting")
     && els.talk.title === "Paused. Something's waiting: click to resume and hear it", [els.cmp.className, els.talk.title]);
  // A press that moved: the panel dragged the window and said so; its release is no click.
  const fire = kind => { const e = { stopped: false, stopPropagation() { this.stopped = true }, preventDefault() {} };
    (docOn[kind] || []).forEach(fn => fn(e)); return e.stopped };
  fire("mousedown"); dragged();
  ok("a drag of the mini window: its release is no click, so it never pauses or ends the call", fire("click") && fire("dblclick"));
  fire("mousedown");
  ok("  the next press that doesn't move: a click again", !fire("click"));
  calls = []; await orb();
  ok("  the click goes on with the call", calls.some(c => c[0] === "/api/voice" && c[1].includes('"unmute"')), calls);
  STATE = LIVE({ voice: { state: "muted", muted: true } });
  await fresh();
  ok("  paused with nothing waiting: no dot", !els.cmp.className.includes("waiting") && els.talk.title === "Paused. Click to go on with the call", [els.cmp.className, els.talk.title]);
  STATE = LIVE({ voice: { state: "listening", waiting: true } });
  await fresh();
  ok("  not paused: no dot, whatever the flag", !els.cmp.className.includes("waiting"), els.cmp.className);

  ok("in the panel, hover is the panel's own: no page mouseover (WebKit replays one at a stale spot, and the pill never went)",
     !(els.cmp.on || []).includes("mouseover") && !(els.cmp.on || []).includes("mouseleave"), els.cmp.on);
  // The peek: the panel says when the pointer is over the orb; 120 ms in, 150 ms out.
  STATE = LIVE(); await fresh(); open = false; render();
  hover(true); await tick(160);
  ok("pointed at: the orb stays 52, and the buttons roll out", open && last("orb").glyph === 52 && els.rail.classList.contains("out")
     && shown().includes("Full window") && !shown().includes("Open Standby"), [open, last("orb"), shown()]);
  hover(false); await tick(60);
  ok("  a moment after leaving, still there", open);
  await tick(160);
  ok("  then back to the orb alone, the same 52", !open && last("orb").glyph === 52 && last("orb").glass === null, last("orb"));
  posts = []; await menu(5, 5);
  ok("  the menu: just where, nothing of the voice or a meeting", (m => m && !("voice" in m) && !("meeting" in m))(posts.find(m => m.type === "menu")), posts);
  // Halo (the user's pick): a glow under the orb; listening, the outer bars blink now and then, never while you talk
  ok("halo: a glow under the orb", shown().includes('<span class="orbs"><span class="halo"></span><span class="orb" id="orb">'), shown());
  STATE = LIVE({ voice: { state: "listening", level: 0 } }); await fresh();
  blinkNow(true);
  ok("  listening to quiet: a blink", els.cmp.className.includes("blink") && els.cmp.className.includes("listening"), els.cmp.className);
  await tick(260);
  ok("  and it's over in a moment", !els.cmp.className.includes("blink"), els.cmp.className);
  STATE = LIVE({ voice: { state: "listening", level: .3 } }); await fresh(); blinkNow(true);
  ok("  hearing you: no blink", !els.cmp.className.includes("blink") && els.cmp.className.includes("hearing"), els.cmp.className);
  clearTimeout(blinkT);

  calls = [];
  STATE = LIVE({ voice: { state: "speaking", claude: "Theo has a number by Thursday." } });
  await fresh();
  ok("the orb's window never shows the words", !shown().includes("Theo"), shown());
  // Claude can't be reached: no words, a grey orb with a red dot, the line on hover; a click opens Standby.
  STATE = LIVE({ claude_error: { kind: "signin", text: "Claude can't sign in.", fix: "Run claude, then /login, and try again.", detail: "", at: 1 } });
  await fresh(); posts = []; calls = [];
  ok("claude down: the orb grey with its red dot, the line on hover", els.cmp.className.includes("down") && els.talk.title.includes("Claude can't sign in. Run claude, then /login"), [els.cmp.className, els.talk.title]);
  await orb();
  ok("  a click opens Standby, where the line and its Details are", posts.some(m => m.type === "expand") && !calls.some(c => c[0] === "/api/voice"), [posts, calls]);
  // the call's work going on deeper while the voice listens: the orb shows the thinking motion (it's at work, still there)
  STATE = LIVE({ voice: { state: "listening", level: 0, tasks: [{ id: "t1", state: "running", label: "Run the tests" }] } });
  await new Promise(r => setTimeout(r, 750)); await fresh();               // past any voice heard just before
  ok("work going on deeper, the voice listening: the orb thinks, its tip says it's working", els.cmp.className.includes("thinking")
     && els.cmp.className.includes("working") && els.talk.title.startsWith("Working on it"), [els.cmp.className, els.talk.title]);
  STATE = LIVE({ voice: { state: "listening", level: 0.3, tasks: [{ id: "t1", state: "running" }] } }); await fresh();
  ok("  the user talking over it: the orb hears them", els.cmp.className.includes("hearing") && !els.cmp.className.includes("working"), els.cmp.className);
  STATE = LIVE({ voice: { state: "listening", level: 0, tasks: [{ id: "t1", state: "done" }, { id: "t2", state: "queued" }] } });
  await new Promise(r => setTimeout(r, 750)); await fresh();
  ok("  nothing running (done, or only waiting its turn): listening as before", els.cmp.className.includes("listening") && !els.cmp.className.includes("working"), els.cmp.className);
  // Visuals: the panel's button is Settings > Call's switch, from here
  STATE = LIVE({ visuals_on: true }); await fresh(); calls = [];
  await visualsOnOff();
  ok("Visuals: the panel's button turns visuals off (Settings > Call's switch), and says so",
     calls.some(c => c[0] === "/api/settings" && JSON.parse(c[1]).call_visuals === false) && C.visuals_on === false, calls);
  // the card closed, the call has visuals: a picture mark on the orb's edge, its own hit area; a click opens the card
  STATE = LIVE({ visual_mark: true, voice: { state: "listening" } }); posts = []; calls = []; await fresh();
  const hits = posts.filter(m => m.type === "hit").slice(-1)[0];
  ok("visuals closed: the picture mark on the orb, with its own 28 px hit area", els.cmp.className.includes(" vis")
     && hits && hits.areas.some(a => a.r === 14), [els.cmp.className, hits]);
  await showVisuals();
  ok("  a click on it: the app opens the card again (not the orb's pause)", calls.some(c => c[0] === "/api/visual/show")
     && !calls.some(c => c[0] === "/api/voice"), calls);
  STATE = LIVE({ voice: { state: "listening" } }); await fresh();
  ok("  no visuals closed: no mark", !els.cmp.className.includes(" vis"), els.cmp.className);
  STATE = LIVE();
  words_page();
})();
`;
const WORDS_CHECKS = `
(async () => {
  const tick = ms => new Promise(r => setTimeout(r, ms || 40));
  const fresh = async () => { await poll(true); await tick() };
  const last = () => posts.filter(m => m.type === "words").slice(-1)[0];
  posts = [];
  // On a call, just the orb (the user): no time, no captions, no working line; a refused mic still said
  STATE = LIVE({ voice: { state: "listening", you: "" } });
  await fresh();
  ok("words: on a call, just the orb: no time, the window hidden", els.sp.textContent === "" && last() && last().w === 0, [els.sp.textContent, last()]);
  STATE = LIVE({ voice: { state: "speaking", claude: "The cache stays at five minutes." } });
  await fresh();
  ok("  no captions while either side speaks", els.sp.textContent === "" && last().w === 0, els.sp.textContent);
  STATE = LIVE({ voice: { state: "thinking", you: "Run the tests", action: "Searching your files", actions: 3 } });
  await fresh();
  ok("  nor a working line while it thinks", els.sp.textContent === "" && last().w === 0, els.sp.textContent);
  STATE = LIVE({ voice: { state: "listening" } }); STATE.live.mic_denied = true; await fresh();
  ok("  a mic macOS refuses: said, in amber, in a window hugging it", els.sp.textContent === "Can’t hear your mic" && els.sp.className.includes("warn")
     && (m => m.w === 184 && m.h === 44 && m.radius === 14 && m.material === "soft" && m.ms === 180)(last()), [els.sp, last()]);
  STATE = LIVE({ voice: { state: "listening" } }); await fresh();
  ok("    and gone once the mic is allowed", els.sp.textContent === "" && last().w === 0, els.sp.textContent);
  card_page();
})();
`;
// The call's visual card (#card): the frame around the visual, its sandbox the panel's; what it tells the panel.
const CARD_CHECKS = `
(async () => {
  const tick = ms => new Promise(r => setTimeout(r, ms || 40));
  const fresh = async () => { await poll(true); await tick(); await poll(true); await tick() };
  const last = () => posts.filter(m => m.type === "card").slice(-1)[0];
  const V = (id, state, what) => ({ id, state: state || "ready", qid: "q" + id, at: id, what: what || "" });
  const AT = list => { VISLIST = list; const t = list[list.length - 1]; STATE = LIVE({ visuals_on: true, voice: { state: "listening", visual: Object.assign({ n: list.length }, t) } }) };
  posts = [];
  AT([V(1, "drawing", "The <three> points")]); await fresh();
  ok("card: a visual being drawn: Drawing… with the voice's brief (escaped), no sandbox yet, its close a hit area",
     (m => m.w === 300 && m.id === 0 && m.session === "talk-1" && m.hit.length === 1)(last()) && els.vcard.innerHTML.includes("Drawing")
     && els.vcard.innerHTML.includes("The &lt;three&gt; points"), [last(), els.vcard.innerHTML]);
  AT([V(1)]); await fresh();
  ok("  ready: the panel lays visual 1 over the card's area", last().id === 1 && last().area.w === last().w && last().area.h === last().h, last());
  window.visualSize({ want: "small", w: 260, h: 90 });
  ok("  its size: the visual's choice (small), its content's height, no pager for one", (m => m.w === 260 && m.h === 90 && m.area.h === 90)(last()) && !els.vcard.innerHTML.includes(" / "), last());
  AT([V(1), V(2)]); await fresh();
  ok("a second: the card goes to it (2 / 2), the pager below, its New mark", last().id === 2 && els.vcard.innerHTML.includes("2 / 2")
     && els.vcard.innerHTML.includes("vnew") && last().h === last().area.h + 34 && last().hit.length === 2, [last(), els.vcard.innerHTML]);
  // the browser reads markup back changed (disabled="", entities): the buttons must not be redrawn each poll
  { let sets = 0, v = els.vcard.innerHTML; Object.defineProperty(els.vcard, "innerHTML", { configurable: true,
      get: () => v.replace(/ disabled(?=[ >])/g, ' disabled=""'), set: x => { sets++; v = x } });
    await fresh(); await fresh();
    ok("  the same card polled again: its buttons are not drawn again (a click across a redraw was lost)", sets === 0, sets) }
  cardGo(-1);
  ok("  ‹: back to 1 / 2, no New on it (it was seen as the newest)", last().id === 1 && els.vcard.innerHTML.includes("1 / 2") && !els.vcard.innerHTML.includes("vnew"), last());
  AT([V(1), V(2), V(3)]); await fresh();
  ok("  a third while looking back: stays on 1, and only › says New", last().id === 1 && els.vcard.innerHTML.includes("1 / 3") && els.vcard.innerHTML.includes('class="nx"')
     && !els.vcard.innerHTML.includes("vnew"), els.vcard.innerHTML);
  calls = [];
  cardClose(); await tick();
  ok("closed: the card goes, and the app is told (the orb shows its picture mark meanwhile)", last().w === 0
     && calls.some(c => c[0] === "/api/visual/closed" && JSON.parse(c[1]).closed === true), [last(), calls]);
  await fresh();
  ok("  and stays gone while nothing new comes", last().w === 0, last());
  calls = []; STATE.visual_open = 1; await fresh();
  ok("  the orb's mark or the voice asked to show it: open again, at the newest, and the app told", last().id === 3 && last().h > 28
     && calls.some(c => c[0] === "/api/visual/closed" && JSON.parse(c[1]).closed === false), [last(), calls]);
  cardClose(); await fresh();
  AT([V(1), V(2), V(3), V(4)]); await fresh();
  ok("  the next one opens it again by itself, at 4 / 4", last().id === 4 && els.vcard.innerHTML.includes("4 / 4"), last());
  // two new ones at once (the user, 2026-10-31): in the order they were made, the first unseen first
  AT([V(1), V(2), V(3), V(4), V(5), V(6)]); await fresh();
  ok("two new at once: the card shows the first of them (5 / 6), New on it, and › says New for the next",
     last().id === 5 && els.vcard.innerHTML.includes("5 / 6") && els.vcard.innerHTML.includes('class="nx"') && els.vcard.innerHTML.includes("vnew"), els.vcard.innerHTML);
  cardGo(1);
  ok("  › : the next (6 / 6), and nothing more waits", last().id === 6 && els.vcard.innerHTML.includes("6 / 6") && !els.vcard.innerHTML.includes('class="nx"'), els.vcard.innerHTML);
  AT([V(1), V(2), V(3), V(4), V(5), V(6), V(7)]); await fresh();
  ok("  one more while at the end: it comes up (7 / 7)", last().id === 7 && els.vcard.innerHTML.includes("7 / 7"), last());
  window.visualSize({ want: "line", w: 150, h: 30 });
  ok("sizes: a line at least 240 wide; its own height, the pager under it", last().w === 240 && last().h === 30 + 34, last());
  window.visualSize({ want: "large", w: 900, h: 2000 });
  ok("  large: half the screen wide; taller than half the screen: held there (the visual scrolls)", last().w === 720 && last().h === 450, last());
  STATE = LIVE({ visuals_on: false, voice: STATE.voice }); await fresh();
  ok("visuals turned off: the card goes", last().w === 0, last());
  STATE = LIVE({ voice: { state: "off" } }); STATE.live = null; await fresh();
  ok("the call over: nothing", last().w === 0);
  done();
})();
`;
const card_page = () => { location.hash = "#card"; posts = []; eval(script + CARD_CHECKS) };
// The page's code and these checks share one scope (eval); each window is its own run.
const words_page = () => { location.hash = "#words"; eval(script + WORDS_CHECKS) };
eval(script + ORB_CHECKS);
