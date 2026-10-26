// Standby's view: past meetings and calls (by folder and Claude Code session), one of them open with
// its summary and transcript, and the call view. Nothing here starts a meeting or call: Claude Code
// does. Its server side is /api/msg/*. It never shows made-up data: the previews (tools/ui_preview.py)
// bring their own server answers.
"use strict";
const MX = {
  view: null,            // {kind: "past"} once up: the one view, the list (one meeting or call open in it: MX.exp)
  ws: [], agents: [], items: [], call: null, loaded: false,
  scope: "all",          // what's listed (the title's menu): "all", or a Claude Code session (its agent id)
};
const MXI = {
  phone: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h3.5l1.8 4.5-2.3 1.4a11 11 0 0 0 5.6 5.6l1.4-2.3L19.5 15v3.5a1.5 1.5 0 0 1-1.6 1.5A16 16 0 0 1 3.5 5.6 1.5 1.5 0 0 1 5 4z"/></svg>',
  chev: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
  back: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>',
  more: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><circle cx="5.5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="18.5" cy="12" r="1.7"/></svg>',
  pause: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4.5" width="4.2" height="15" rx="1.3"/><rect x="13.8" y="4.5" width="4.2" height="15" rx="1.3"/></svg>',
  play: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.2v13.6a1 1 0 0 0 1.5.86l10.6-6.8a1 1 0 0 0 0-1.72L9.5 4.34A1 1 0 0 0 8 5.2z"/></svg>',
  stop: '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="3"/></svg>',
  check: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  wave: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 10.5v3"/></svg>',
  gear: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
  list: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
  folder: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/></svg>',
};

/* ---------------------------------------------------------------- the data */
const mxAg = id => MX.agents.find(a => a.id === id);
const mxWs = id => MX.ws.find(w => w.id === id);
async function mxGet(path, body) {
  let r; try { r = await api(path, body) } catch (e) { r = null }
  // An app started before this page's server code knows nothing of the route: it needs a restart
  if (path.startsWith("/api/msg/") && r && r.error && /^(not found|unknown request)$/i.test(r.error.trim())) r = Object.assign({}, r, {error: "Standby was updated: restart it to use this"});
  return r;
}
// The Claude Code sessions (what's live on each), then the past meetings and calls. Not loaded until both
// came back: while the server can't answer (it's still starting, or busy) the list keeps its spinner and
// asks again in a second, rather than saying there's nothing, or showing anything made up.
let MXRETRY = 0;
async function mxLoad() {
  const r = await mxGet("/api/msg/workspaces"), ok = !!(r && Array.isArray(r.workspaces));
  if (ok) { MX.ws = r.workspaces; MX.agents = r.agents || [] }
  const t = ok && MX.view ? await mxGet("/api/msg/past") : null, got = !!(t && Array.isArray(t.items));
  if (got && MX.view) MX.items = t.items;
  clearTimeout(MXRETRY);
  if (ok && (got || !MX.view)) MX.loaded = true;
  else MXRETRY = setTimeout(mxLoad, 1000);
  mxPaint();
}
// Events come in bursts: one repaint per frame takes them all
let MXRAF = 0;
// A frame asked for while the window was hidden may never come (WebKit stops frames for a window out of
// sight): one waiting longer than a moment is dropped and the paint goes by a timer instead.
let MXRAFAT = 0;
function mxPaintSoon() {
  if (MXRAF && Date.now() - MXRAFAT < 500) return;
  if (MXRAF && typeof cancelAnimationFrame === "function") cancelAnimationFrame(MXRAF);
  const go = () => { MXRAF = 0; mxPaint() };
  MXRAFAT = Date.now();
  MXRAF = typeof requestAnimationFrame === "function" && !document.hidden ? requestAnimationFrame(go) : setTimeout(go, 16) }
// The window shown again (Claude Code's /standby window, the Dock, the orb): what it shows, fresh
async function mxWake() {
  MXRAF = 0;
  const b = $("mbody"); if (b) { b._chunks = null; b._h = "" }
  try { await mxLoad() } catch (e) {}      // it paints
}

/* ---------------------------------------------------------------- drawing */
// Puts chunks [{k, h, kids?}] into el, keeping the nodes of every chunk whose key and markup are
// unchanged. A chunk with kids is a container: its h is the empty element, its kids go inside it.
// Returns the elements it made.
let MXT = null;
function mxPatch(el, chunks, fresh) {
  fresh = fresh || []; MXT = MXT || document.createElement("template");
  if (!el._chunks) { el.textContent = ""; el._chunks = [] }
  const old = new Map(), next = [], kept = new Set();
  for (const c of el._chunks) if (!old.has(c.k)) old.set(c.k, c);
  for (const c of chunks) { const o = old.get(c.k);
    if (o && o.h === c.h && !kept.has(o)) { kept.add(o); next.push(Object.assign({}, c, {nodes: o.nodes})) }
    else { MXT.innerHTML = c.h; const nodes = [...MXT.content.childNodes]; for (const n of nodes) if (n.nodeType === 1) fresh.push(n); next.push(Object.assign({}, c, {nodes})) } }
  for (const o of el._chunks) if (!kept.has(o)) for (const n of o.nodes) n.remove();      // gone, or changed: its old nodes go
  let at = el.firstChild;
  for (const c of next) for (const n of c.nodes) { if (n === at) at = at.nextSibling; else el.insertBefore(n, at) }
  el._chunks = next;
  for (const c of next) if (c.kids) { const box = c.nodes.find(n => n.nodeType === 1); if (box) mxPatch(box, c.kids, fresh) }
  return fresh;
}

/* ---------------------------------------------------------------- the main view */
// One layout at any width (the user, 2026-10-21). The list: Standby's logo, what's listed (its menu picks a
// Claude Code session, or All), the gear. One meeting or call open: back, its name and time, Pause and End
// while it's live, ⋯.
const MXLOGO = '<span class="logo" aria-hidden="true"><i></i><i></i><i></i></span>';
function mxHeader() {
  const exp = MX.exp && MX.items.find(m => m.session === MX.exp);
  if (exp) return mxOpenHead(exp);
  const a = MX.scope !== "all" && mxAg(MX.scope), w = a && mxWs(a.workspace), n = mxPastIn().length;
  const sub = a ? "Claude Code session" + (w && w.folder ? " · " + tilde(w.folder) : "") : n + " · newest first";
  const bad = !!(typeof S === "object" && S && S.claude_error);
  return `<header data-drag><span class="mx-hlogo">${MXLOGO}</span>
    <button class="mx-scope" onclick="mxScopeMenu(event)" aria-haspopup="menu"><b>${esc(mxScopeName())}${MXI.chev}</b><small>${esc(sub)}</small></button>
    <button class="iconbtn mx-gear${bad ? " bad" : ""}" title="${bad ? esc(S.claude_error.text + " " + (S.claude_error.fix || "")) : "Settings"}" aria-label="Settings" onclick="openSettings()">${MXI.gear}<span class="dot"></span></button></header>`;
}
// a live header's mark: recording (red), on a call (Standby's indigo), both glowing; paused, a still pause mark
const MXMARKS = {rec: "Recording", call: "On a call", paused: "Paused"};
const mxLiveMark = k => `<span class="lmark ${k}" role="img" aria-label="${MXMARKS[k]}" title="${MXMARKS[k]}"></span>`;
function mxOpenHead(m) {
  const d = (MXW[m.session] || {}).d, i = d && d.info, live = m.live || m.paused, paused = !!m.paused, call = m.kind === "call", sid = esc(m.session);
  const t = i ? (i.live ? elapsed(i) : i.duration || 0) : m.duration_s || 0;
  const name = call ? `<b>${esc((mxAg(m.from) || {}).name || "Call")}</b>` : `<b class="mx-ed" onclick="mxRenameWin(this,'${sid}')">${esc(mxTitle(m, d))}</b>`;
  const end = m.ended_at || ((m.started_at || m.at || 0) + (m.duration_s || 0)), day = mxDay(end);
  // live, one line (the user, 2026-10-23: the glowing dot already says it's recording): its mark (a pause
  // mark while paused), its name, its time; ended, what it was under its name
  const who = live ? `<span class="vwho one" title="${esc(mxSrc(m))}">${mxLiveMark(paused ? "paused" : call ? "call" : "rec")}${name}<span class="tm">${mmss(t)}</span></span>`
    : `<span class="vwho" title="${esc(mxSrc(m))}">${name}<small>${esc([call ? "Call" : "Meeting", mxMin(m.duration_s), "ended " + (day === "Today" ? "" : day + ", ") + clock(end)].join(" · "))}</small></span>`;
  return `<header data-drag><button class="iconbtn" onclick="mxBack()" title="Back to the list" aria-label="Back to the list">${MXI.back}</button>
    ${who}
    <button class="iconbtn" onclick="mxWinMenu(event,'${esc(m.id)}')" title="More" aria-label="More">${MXI.more}</button></header>`;
}
function mxPaint() {
  mxFocusCheck();
  const v = MX.view;
  document.body.classList.toggle("mx", !!v);
  if (!v) return;
  const mv = $("mview");
  const b0 = $("mbody"), atEnd = !b0 || b0.scrollTop + b0.clientHeight >= b0.scrollHeight - 40, keep = b0 ? b0.scrollTop : 0;
  const exp = MX.exp ? MX.items.find(m => m.session === MX.exp) : null;
  if (MX.exp && !exp && MX.loaded) MX.exp = null;
  const one = h => [{k: "one", h}];
  const head = mxHeader(), body = !exp ? one(mxPast()) : mxExpanded(exp);
  document.body.classList.toggle("mx-exp", !!exp);
  if (mv._head !== head && !MX.editing) { mv.querySelector(".mx-headwrap").innerHTML = head; mv._head = head }
  const b = $("mbody"), h0 = b.scrollHeight;
  if (b._h == null) b._chunks = null;                  // _h = null asks for a whole new body, as it always has
  const fresh = spkEditing || MX.editing ? null : mxPatch(b, body); if (fresh) { b._h = "patched"; mxFadeIn(b._exp === (exp && exp.session) ? fresh : [], b) }
  const dk = $("mxdock"), dock = exp && exp.kind !== "call" && (exp.live || exp.paused) ? mxMeetDock(exp, (MXW[exp.session] || {}).lv) : [];
  if (dk) { dk.hidden = !dock.length; mxPatch(dk, dock) }
  if (fresh && (fresh.length || b.scrollHeight !== h0)) {
    // an open meeting stays at its newest line while you're there; the list where you left it
    b.scrollTop = exp && (atEnd || b._exp !== exp.session) ? b.scrollHeight : keep;
    b._exp = exp ? exp.session : null }
  mxWatch();
}

/* ---------------------------------------------------------------- a meeting or call, open */
// The whole transcript (a call's: what was said, handed to Claude Code and what came back), live as it
// goes; ended, its summary on top.
const mxMin = s => (s || 0) < 60 ? Math.max(1, Math.round(s || 0)) + " s" : Math.round(s / 60) + " min";
// Each session's record, the one copy the view reads: {d: /api/session/<sid>, lv: /api/live/<sid>}.
// Fetched in one place (mxFetch), kept fresh by the one poller (mxTick) for what's on screen.
const MXW = {};
async function mxFetch(sid) {
  try { const d = await api("/api/session/" + sid); if (d && !d.error) MXW[sid] = Object.assign(MXW[sid] || {}, {d}) } catch (e) {}
  return (MXW[sid] || {}).d;
}
function mxTitle(m, d) { if (m.kind === "call") return "Call"; const i = d && d.info; return i && i.title && i.title_source !== "default" ? i.title : m.title || "Meeting" }
function mxSrc(m) { const x = m.sources || {}; return x.meeting_audio && x.mic ? "Meeting audio and your mic" : x.meeting_audio ? "Meeting audio" : x.mic ? "Your mic" : "Your Mac" }
// Name a speaker by clicking their name: type in place, Enter. The whole meeting follows, the agent
// is told, and (with "Recognize voices I have named") the voice is remembered (/api/speaker, as ever).
function mxSpk(sp, sid, cls) {
  if (!sp) return "";
  const tip = sp.known ? "Recognized from an earlier meeting. Click to correct" : "Click to name";
  return `<span class="${cls}${sp.known ? " known" : ""}" style="color:${color(sp.id)}" title="${tip}" onclick="event.stopPropagation();mxNameSpk(this,'${esc(sid)}','${esc(sp.id)}')">${esc(sp.name)}</span>`;
}
function mxNameSpk(el, sid, spk) {
  spkEditing = true;
  mxEdit(el, el.textContent, async v => { const r = await mxGet("/api/speaker", {id: sid, spk, name: v}); if (r && r.error) toast(r.error);
    await mxFetch(sid); mxPaint() });
  const end = el.onblur; el.onblur = () => { spkEditing = false; end() };
  const key = el.onkeydown; el.onkeydown = e => { if (e.key === "Enter" || e.key === "Escape") spkEditing = false; key(e) };
}
// macOS hasn't allowed the mic: nothing of yours is recorded until it is. One amber line, Allow beside it.
const mxMicNo = lv => (((lv && lv.sources) || []).find(x => x.kind === "mic" && x.cause === "permission")) || null;
const mxMicNoHtml = x => `<div class="mx-micno" title="${esc(x.detail || "")}">Standby can’t hear your mic<button class="lk" onclick="mxMicAllow()">Allow</button></div>`;
// The meeting's own sound refused (Screen & System Audio Recording): the same line, Allow beside it;
// switched on already but for an older copy of Standby (stale): Reset instead. It records by itself once allowed.
const mxSoundNo = lv => (((lv && lv.sources) || []).find(x => x.kind === "meeting" && x.cause === "permission")) || null;
const mxSoundNoHtml = x => x.stale ? `<div class="mx-micno" title="${esc(STALE_WHY)}">Standby can’t hear the meeting’s sound · it’s on for an older copy of Standby<button class="lk" onclick="resetScreen()">Reset</button></div>`
  : `<div class="mx-micno" title="${esc(x.detail || "")}">Standby can’t hear the meeting’s sound<button class="lk" onclick="allowScreen()">Allow</button><button class="lk" onclick="resetScreen()">It’s on already</button></div>`;
// A meeting that will hear the Mac's sound, before macOS has ever asked: its prompt names Screen Recording,
// which alarms; one dim line says what to choose and what Standby takes. The same words as the band in Claude Code.
const mxSoundAsk = lv => !!(lv && lv.meeting_audio === "ask");
const mxSoundAskHtml = () => `<div class="mx-micno ask"><span>macOS will ask for Screen &amp; System Audio Recording · choose Allow;</span><span>Standby takes only the sound</span></div>`;
async function mxMicAllow() { const r = await mxGet("/api/mic/permission", {}); if (r && r.error) toast(r.error) }
// What an ended one's summary shows: once its card exists (memory is set) the summary is final, even with
// no points; before that it is on its way, for 3 minutes at most.
const MX_SUMMARY_WAIT = 180;
function mxSummary(m, now) {
  if ((m.points || []).length) return "points";                    // points always show, whatever else
  if (m.again) return "wait";                                       // Summarize again: on its way
  if (m.gist) return "gist";
  if (m.memory != null) return "none";
  const ended = m.ended_at || ((m.started_at || m.at || 0) + (m.duration_s || 0));
  return ended && now - ended > MX_SUMMARY_WAIT ? "gone" : "wait";
}
function mxSummaryHtml(m, cls) {
  const k = mxSummary(m, Date.now() / 1000);
  return k === "points" ? `<div class="${cls === "card" ? "" : "mx-lws"}"><ul>${m.points.map(p => `<li>${esc(p)}</li>`).join("")}</ul></div>`
    : `<div class="${cls === "card" ? "mx-note" : "mx-lwb"}" style="text-align:left"><div class="ln">${k === "gist" ? esc(m.gist) + (m.failed ? ` <button class="lk" onclick="mxAgain('${esc(m.session)}')">Summarize again</button>` : "") : k === "none" ? "Nothing decided." : k === "gone" ? "No summary." : "The summary comes in a moment."}</div></div>`;
}
// A summary the model couldn't be asked for, made again from the same words (the server answers at once;
// the card comes when it's made, and says so again if it still couldn't).
async function mxAgain(sid) {
  const m = MX.items.find(x => x.session === sid);
  const r = await mxGet("/api/msg/card/again", {session: sid});
  if (r && r.error) return toast(r.error);
  if (m) { m.failed = false; m.again = true; mxPaint() }
}
// A meeting's transcript as keyed chunks: its lines with who said them (click to name), the questions
// asked during it and their answers, its own events between them (started, paused and resumed, the mic
// muted, the call's sound paused), and the words being said now. The expanded window draws it.
// Speech to text ends a line at every pause, so one sentence can come as several lines. A line that
// goes on from the one before (same speaker, within a moment, the sentence not yet ended) is shown
// joined to it; the saved transcript keeps them apart. The same rule as Standby inside Claude Code's pane.
const MX_JOIN_GAP = 2.5;
function mxJoin(rows) {
  const out = [];
  for (const r of rows || []) {
    const p = out[out.length - 1], who = x => x.spk != null ? x.spk : x.ch;
    if (p && !p.heard && !r.heard && who(p) === who(r) && !/[.?!\u2026]["\u201d')\]]?\s*$/.test(String(p.text || ""))
        && r.t - (p.end != null ? p.end : p.t) <= MX_JOIN_GAP && String(p.text || "").length < 400)
      out[out.length - 1] = Object.assign({}, p, {text: String(p.text || "").trim() + " " + String(r.text || "").trim(), end: r.end != null ? r.end : r.t});
    else out.push(r);
  }
  return out;
}
// Copy a message's words (a call's or a meeting's: yours, the voice's, Claude Code's): on hover, quiet
const MXMIC = '<svg class="mx-mic" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-label="Said out loud"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';
const MXCP = '<button class="mx-cp" title="Copy" aria-label="Copy" onclick="mxCopyRow(event,this)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg></button>';
function mxCopyRow(e, b) {
  e.stopPropagation();
  const row = b.closest(".mx-qa"); if (!row) return;
  const c = row.cloneNode(true); c.querySelectorAll(".mx-cp,.mx-vmark,.mx-mic").forEach(x => x.remove());
  copyText((c.innerText || c.textContent || "").trim());
}
function mxRows(d, lv, live, paused, calling, tasks) {
  const i = d && d.info, isCall = !!(i && (i.kind === "talk" || /^talk-/.test(i.id || "")));
  const runs = (i && i.runs) || [], at = r => { let k = 0; while (k + 1 < runs.length && r.t >= runs[k + 1].t - 0.01) k++; return runs.length ? runs[k].start + (r.t - runs[k].t) : (i && i.started || 0) + r.t };
  // the meeting's own events between its lines: started, paused and resumed, the mic muted, the call's sound paused
  const tOf = t => runs.length ? at({t}) : (i && i.started || 0) + t, sep = (when, text) => ({at: when, sep: text});
  const seps = [];
  if (runs.length) seps.push(sep(runs[0].start - 0.001, "Started " + clock(runs[0].start)));
  for (let k = 1; k < runs.length; k++) { const a = runs[k - 1], z = runs[k];
    seps.push(sep(z.start - 0.001, (a.end ? "Paused " + clock(a.end) + " · " : "") + "Resumed " + clock(z.start))) }
  for (const e of (i && i.events) || []) { if (e.from == null) continue; const len = e.to == null ? null : e.to - e.from;
    // words the recorder heard over the voice but couldn't tell from it (open speakers): never the words, a faint line
    if (e.kind === "speech_lost") seps.push(sep(tOf(e.from), "Something you said wasn\u2019t heard"));
    else if (e.kind === "source_off" && e.name) seps.push(sep(tOf(e.from), len === null ? e.name + " is off" : e.name + " was off · " + mxMin(len)));
    else if (e.kind === "mic_muted") seps.push(sep(tOf(e.from), len === null ? "Your mic is muted" : "Your mic was muted · " + mxMin(len)));
    else if (e.kind === "meeting") seps.push(sep(tOf(e.from), "Meeting audio paused" + (len === null ? "" : " · " + mxMin(len)) + (e.reason ? ": " + e.reason : ""))) }
  const rows = mxJoin((d && d.transcript) || []).map((r, n) => ({at: at(r), r, n})).concat(((d && d.chat) || []).map((c, n) => ({at: c.at || 0, c, n})))
    .concat(d ? seps : []).sort((a, b) => a.at - b.at);
  // keyed chunks: a line said, or an answer come, adds itself without redrawing the rest
  const tx = [], add = (k, h) => tx.push({k, h});
  // a call's rows read as a conversation (the user, 2026-10-25): a run of the voice's lines shares one mark, a
  // run of yours one tail; a quiet time where the talk resumes after 2 minutes or more
  let lastWho, asked = 0, came = 0, prev = null, lastAt = null, lastClock = null;
  const QUIET = 120;
  for (const x of rows) {
    if (x.sep) { add("s:" + x.sep, `<div class="mx-sep2">${esc(x.sep)}</div>`); lastWho = undefined; prev = null; if (/^Started /.test(x.sep)) lastClock = x.sep.slice(8); continue }
    if (x.c && isCall && !(x.c.role === "ai" && (x.c.unsaid || x.c.result != null && !String(x.c.text || "").trim()))) {
      if (lastAt != null && (x.c.at || 0) - lastAt >= QUIET) { add("t:" + x.n, `<div class="mx-time">${esc(clock(x.c.at))}</div>`); prev = null }
      lastAt = x.c.at || lastAt }
    // A call's conversation (the user's design, 2026-10-22): what you said (the mic first) and what the voice
    // said to you (Standby's mark first). The voice and the session are one self, so a hand-off is no row of
    // its own and no "Claude Code" wording: only what the voice really said as it took the work deeper (its
    // text; the app's old stock "I'm on it." not). While deeper work runs on a live call, one "✻ Working on
    // it…" line under the rest, gone when the work is done; what comes back is never shown.
    if (x.c) { const c = x.c;
      if (c.role === "me") {
        const t = /^\[CALL START\]/.test(c.text || "") ? String(c.text).replace(/^\[CALL START\]\s*/, "") : c.text;   // the greeting's hidden marker
        // a turn of yours never answered ("dropped"): a quiet mark under it
        if (t) add("c:" + x.n, `<div class="mx-qa${prev === "me" ? " run" : ""}${c.dropped ? " dropped" : ""}" title="${esc(clock(c.at))}">${MXCP}<div class="qt">${c.voice && !isCall ? MXMIC : ""}${esc(t)}</div>${c.dropped ? '<div class="mx-drop">Not answered</div>' : ""}</div>`);
        if (t) prev = "me"; lastWho = undefined; continue }
      // a hand-off: counted; an older call marked it in its text ("[HANDOFF] the task"), or said the app's
      // stock "I'm on it.": neither is drawn
      const handoff = !!c.handoff || /^\[HANDOFF\]/.test(c.text || "");
      if (handoff) asked++;
      if (c.result != null) came++;
      // only what was said: never a reply none of which was played ("unsaid"); one cut off partway, what was heard
      const stock = handoff && (/^\[HANDOFF\]/.test(c.text || "") || String(c.text || "").trim() === "I'm on it.");
      const said = stock || c.unsaid ? "" : String(c.cut && c.heard != null ? c.heard : c.text || "").trim();
      if (said) { add("c:" + x.n, `<div class="mx-qa ans${prev === "ai" ? " run" : ""}">${MXCP}<div class="a mx-said"><span class="mx-vmark">${MXLOGO}</span><div class="t">${c.cut ? md(said).replace(/<\/p>\s*$/, '<span class="mx-cut">\u2014 cut off</span></p>') : md(said)}</div></div></div>`); prev = "ai" }
      lastWho = undefined; continue }
    // a speaker's turn: their name and its time on one small line, the words at full width under it
    // its time only when the minute moved on since the last one shown ("Started 12:42 AM", then Speaker 1 without it)
    const sp = speakerLabel(x.r, d.speakers), who = sp ? sp.id : null, turn = who !== lastWho; lastWho = who;
    const ts = clock(x.at), showTs = turn && ts !== lastClock; if (showTs) lastClock = ts;
    add("r:" + x.n, `<div class="mx-line${turn ? " turn" : ""}">${turn ? `<div class="mx-lh">${sp ? mxSpk(sp, d.info.id, "mx-who") : ""}${showTs ? `<span class="mx-ts">${ts}</span>` : ""}</div>` : ""}<div class="mx-lt"${x.r.heard ? ` title="Heard: ${esc(x.r.heard)}"` : ""}>${x.r.heard ? fixedWords(x.r, d.learned) : esc(x.r.text || "")}</div></div>`);
  }
  // work going on, by name, from the call's task records (the voice's "tasks"): working, waiting, or no
  // progress lately; ended ones go. Without records (an older app), one "Working on it…" while hand-offs
  // outnumber what came back.
  if (calling && Array.isArray(tasks)) {
    const W = {sent: "Working on", running: "Working on", queued: "Waiting", stalled: "No progress lately"};
    for (const t of tasks) if (W[t.state]) add("task:" + t.id + ":" + t.state, `<div class="mx-task ${esc(t.state)}"><span class="s">${t.state === "queued" || t.state === "stalled" ? '<span class="dot0"></span>' : '<span class="spin0"></span>'}</span><span>${W[t.state]}: <b>${esc(t.label || "a task")}</b></span></div>`);
  } else if (calling && asked > came) add("working", `<div class="mx-working"><svg class="mx-star" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><g stroke="currentColor" stroke-width="3.2" stroke-linecap="round"><path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/></g></svg><span>Working on it<span class="mx-dots"><i>.</i><i>.</i><i>.</i></span></span></div>`);
  // the question being answered, unless it's already drawn as the last thing the user said
  const lastMe = [...rows].reverse().find(x => x.c && x.c.role === "me");
  if (d && d.pending && !(lastMe && (lastMe.c.text || "").trim() === (d.pending.question || "").trim())) add("pending", `<div class="mx-qa"><div class="qt">${esc(d.pending.question || "")}</div><div class="a">${SPIN}</div></div>`);
  if (!rows.length) add("empty", `<div class="mx-empty">${live ? "Nothing said yet." : "Nothing was transcribed."}</div>`);
  return tx;
}
function mxExpanded(m) {
  const w = MXW[m.session] || {}, d = w.d, live = m.live || m.paused, paused = !!m.paused;
  const tx = mxRows(d, w.lv, live, paused, live && m.kind === "call");
  if (!live) tx.unshift({k: "psum:" + (m.points || []).length + (m.gist || "") + m.memory + !!m.failed + !!m.again, h: `<div class="mx-psum"><div class="k">Summary</div>${mxSummaryHtml(m, "card")}</div>`});
  return [{k: "full", h: `<div class="mx-lwfull${m.kind === "call" ? " call" : ""}"></div>`, kids: [
    {k: "micno", h: live && !paused && (mxMicNo(w.lv) || mxSoundNo(w.lv)) ? `<div class="col">${mxMicNo(w.lv) ? mxMicNoHtml(mxMicNo(w.lv)) : ""}${mxSoundNo(w.lv) ? mxSoundNoHtml(mxSoundNo(w.lv)) : ""}</div>`
      : live && !paused && m.kind === "meeting" && mxSoundAsk(w.lv) ? `<div class="col">${mxSoundAskHtml()}</div>` : ""},
    {k: "ask", h: mxAskHtml(live && !paused && d && d.info && d.info.ask)},
    {k: "tx", h: '<div class="col mx-tx"></div>', kids: d ? tx : [{k: "wait", h: '<div class="mx-empty">' + SPIN + "</div>"}]}]}];
}
// The recorder's question while a meeting records, when it's not clear what to record ("AirPods Pro
// connected: record from their mic?"): one calm box under the header, its answers beside it, the likely
// one first; gone once answered (the recorder acts on it). It lived on the orb, which is calls only now.
const MXASKED = new Set();
function mxAskHtml(q) {
  if (!q || !q.id || MXASKED.has(q.id)) return "";
  const [first, ...rest] = String(q.text || "").split(/(?<=\?) /);
  const btn = (a, n) => `<button class="vtxt${n ? "" : " first"}" onclick="mxAnswerAsk(${esc(JSON.stringify(q.id))},${esc(JSON.stringify(a.value))})">${esc(a.label)}</button>`;
  return `<div class="col"><div class="mx-ask"><div class="q"><b>${esc(first)}</b>${rest.length ? `<small>${esc(rest.join(" "))}</small>` : ""}</div><div class="a">${(q.actions || []).map(btn).join("")}</div></div></div>`;
}
async function mxAnswerAsk(id, value) {
  MXASKED.add(id); mxPaint();
  const r = await mxGet("/api/capture/answer", {id, value});
  if (r && r.error) toast(r.error);
  mxPoll();
}
// What's live in a meeting, in one row at the bottom (the user, 2026-10-25: option B), under the finished
// transcript: its sound as small red bars; "Listening" and its sources, small ("Your mic · Google Chrome ⌄");
// the newest two lines being said; Pause and Stop. A click on the sources opens them above the row, each with
// its tick: untick stops recording it for this meeting (nothing of it reaches Claude), tick resumes it; your mic's
// tick is its mute. Off, a source carries a calm "not listened to" mark, a muted speaker, and says if it's
// still playing. One that starts playing joins ticked, "new" for a few seconds. Paused, grey bars and Resume.
// The words keep one fixed height, three lines, empty or full (the user, 2026-10-23: the transcript jumped as
// they grew and shrank): the newest show, the oldest slide off the top. Only opening the list moves anything.
// The recorder's: /api/live/<sid>'s "hearing" and "partial"; a choice: POST /api/live/source.
const MXHEAR = {open: false, want: {}, seen: {}};
const MXBARS = (heard, cls) => `<span class="lv${heard ? "" : " still"}${cls ? " " + cls : ""}" aria-hidden="true"><i></i><i></i><i></i></span>`;
const MXNOEAR = '<svg class="mx-noear" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></svg>';
function mxMeetDock(m, lv) {
  const sid = m.session, paused = !!m.paused, list = (lv && lv.hearing) || [], words = paused ? "" : String((lv && lv.partial) || "").trim();
  const now = Date.now(), on = x => x.id in MXHEAR.want ? MXHEAR.want[x.id] : x.on !== false;
  // seen first now, after this meeting's list was first drawn: new (those there from the start aren't)
  const seen = MXHEAR.seen[sid] || (MXHEAR.seen[sid] = {}), first = !Object.keys(seen).length;
  for (const x of list) if (!(x.id in seen)) seen[x.id] = first ? 0 : now;
  const fresh = x => seen[x.id] && now - seen[x.id] < 6000, NOT = "Not listened to";
  // its sources, small, in the state line: those on by name, one off struck with the muted-speaker mark
  const names = list.map(x => on(x) ? `<span class="${fresh(x) ? "new" : ""}">${esc(x.name)}</span>` : `<s title="${NOT}">${MXNOEAR}${esc(x.name)}</s>`).join(" \u00b7 ");
  const srcs = list.length ? ` \u00b7 <button class="mx-dsrc" aria-expanded="${MXHEAR.open}" title="${MXHEAR.open ? "Close" : "Every source, each one you can turn off"}" onclick="MXHEAR.open=!MXHEAR.open;mxPaint()">${names}${MXI.chev}</button>` : "";
  const rows = list.map(x => { const o = on(x);
    return `<button class="mx-src${o ? "" : " off"}" role="checkbox" aria-checked="${o}" onclick="event.stopPropagation();mxSource('${esc(sid)}','${esc(x.id)}',${!o})">
      <span class="ck">${o ? MXI.check : ""}</span><span class="n">${esc(x.name)}<small>${o ? esc(x.detail || "") : x.heard ? "Playing, not listened to" : NOT}</small></span>${o ? MXBARS(x.heard) : MXNOEAR}</button>` }).join("");
  const b = MX.busy && MX.busy.sid === sid ? MX.busy : null, s = esc(sid);
  const pause = `<button class="vic dk${paused ? " on" : ""}${b && b.what !== "end" ? " busy" : ""}"${b ? " disabled" : ""} onclick="mxPause('${s}',${paused ? "false" : "true"})" title="${b && b.what !== "end" ? b.words : paused ? "Resume" : "Pause"}" aria-label="${b && b.what !== "end" ? b.words : paused ? "Resume" : "Pause"}">${paused ? MXI.play : MXI.pause}</button>`;
  const stop = `<button class="vic dk end${b && b.what === "end" ? " busy" : ""}"${b ? " disabled" : ""} onclick="mxEndMeeting('${s}')" title="${b && b.what === "end" ? b.words : "Stop"}" aria-label="${b && b.what === "end" ? b.words : "Stop"}">${MXI.stop}</button>`;
  return [{k: "bars:" + paused, h: `<span class="mx-recb${paused ? " off" : ""}" aria-hidden="true"><i></i><i></i><i></i><i></i></span>`},
    {k: "t", h: `<div class="mx-dt"><div class="mx-dstate">${paused ? "Paused" : "Listening"}${srcs}</div><div class="mx-dwords${words ? "" : " quiet"}">${words ? `<span>${esc(words)}<span class="caret"></span></span>` : ""}</div></div>`},
    {k: "btns", h: pause + stop},
    {k: "pop", h: MXHEAR.open && list.length ? `<div class="mx-dpop">${rows}</div>` : ""}];
}
async function mxSource(sid, id, on) {
  MXHEAR.want[id] = on; mxPaint();
  const r = await mxGet("/api/live/source", {session: sid, id, on});
  if (r && r.error) toast(r.error);
  await mxPoll(); delete MXHEAR.want[id]; mxPaint();
}
/* ---------------------------------------------------------------- past meetings and calls */
// Standby opened on its own: every past meeting and call, newest first by day; what's live pinned on top.
// A row opens that session's summary and transcript; nothing here starts one (Claude Code does).
const mxDay = at => { const d = new Date(at * 1000), t = new Date(), y = new Date(t - 864e5), same = (a, b) => a.toDateString() === b.toDateString();
  return same(d, t) ? "Today" : same(d, y) ? "Yesterday" : d.toLocaleDateString([], d.getFullYear() === t.getFullYear() ? {month: "short", day: "numeric"} : {month: "short", day: "numeric", year: "numeric"}) };
// A row's title is a short topic (the user, 2026-10-25): the one the user gave it, else Claude's (the item's
// topic, title.auto.txt); a meeting's own title if it isn't the default. Never its gist, a sentence or a failed
// summary ("Standby couldn't summarize this call."); with nothing better, plainly what it was and when.
const MXDEFAULT = /^(Meeting|Talk|Call)( · |$)/;
function mxPastTitle(m) {
  const i = ((MXW[m.session] || {}).d || {}).info;
  const t = (i && i.title_source && i.title_source !== "default" && i.title) || m.topic || (m.kind !== "call" && !MXDEFAULT.test(m.title || "") && m.title);
  if (t) return t;
  return (m.kind === "call" ? "Call" : "Meeting") + " · " + new Date((m.at || 0) * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}) }
function mxPastRow(m) {
  const live = m.live || m.paused, who = m.kind === "call" ? [] : (m.names || []);
  const what = live ? (m.kind === "call" ? "On a call" : m.paused ? "Paused" : "Listening") : m.kind === "call" ? "Call" : "Meeting";
  const where = MX.scope === "all" ? [(mxAg(m.from) || {}).title].filter(Boolean) : [];      // its session's real name, or none
  const sub = [what, mxMin(m.duration_s)].concat(where, who.length ? [who.join(", ")] : []).join(" · ");
  return `<button class="mx-prow${live ? " live" : ""}" data-sid="${esc(m.session)}" onclick="mxPastOpen('${esc(m.session)}')"><span class="mx-pico${m.kind === "call" ? " call" : ""}">${live ? '<span class="dot"></span>' : m.kind === "call" ? MXI.phone : MXI.wave}</span>
    <span class="t"><b>${esc(mxPastTitle(m))}</b><small>${esc(sub)}</small></span><span class="at">${live ? "Live" : esc(clock(m.at))}</span>${live ? "" :
    `<span class="mx-pmore" role="button" title="More" aria-label="More" onclick="event.stopPropagation();mxRowMenu(event,'${esc(m.session)}')">${MXI.more}</span>`}</button>`;
}
// every meeting and call, newest first; those of the session chosen in the navigator
// (sorted once per list the server sends, not once per paint: the header, the list and the navigator all ask)
let MXPA = {items: null, out: []};
const mxPastAll = () => { if (MXPA.items !== MX.items) MXPA = {items: MX.items, out: MX.items.filter(m => (m.kind === "meeting" || m.kind === "call") && m.session).sort((x, y) => (y.at || 0) - (x.at || 0))}; return MXPA.out };
const mxPastIn = () => mxPastAll().filter(m => MX.scope === "all" || m.from === MX.scope);
function mxPast() {
  if (!MX.loaded) return '<div class="mx-empty">' + SPIN + "</div>";
  if (!mxPastAll().length) return `<div class="mx-welcome">${EMPTYWAVE}<h2>No meetings or calls yet</h2><p>Start one from Claude Code: type <code>/standby listen</code> or <code>/standby call</code>, or just ask Claude. They'll be listed here afterwards, each with its summary and transcript.</p></div>`;
  const all = mxPastIn();
  let h = '<div class="mx-plist">', day = "";
  for (const m of all.filter(m => m.live || m.paused)) h += mxPastRow(m);
  for (const m of all.filter(m => !(m.live || m.paused))) { const d = mxDay(m.at || 0); if (d !== day) { day = d; h += `<div class="mx-pday">${esc(d)}</div>` } h += mxPastRow(m) }
  return h + "</div>";
}
// What can be listed, read-only, in the title's menu: each folder, its Claude Code sessions that have a meeting or call (a count,
// or a red dot while one is live), and All at the top. Nothing here adds, removes or renames.
function mxSessions() {
  const items = mxPastAll(), out = [];
  for (const w of MX.ws) {
    const as = (w.agents || []).map(mxAg).filter(Boolean).map(a => ({a, n: items.filter(m => m.from === a.id).length, live: !!(a.live && (a.live.call || a.live.meeting))})).filter(x => x.n || x.live);
    if (as.length) out.push({w, as});
  }
  return out;
}
function mxScopeName() { return MX.scope === "all" ? "All meetings and calls" : (mxAg(MX.scope) || {}).name || "Claude Code session" }
function mxNav() {
  const it = (id, icon, name, end, tip) => `<button class="mx-nit${MX.scope === id ? " on" : ""}" onclick="mxNavPick('${esc(id)}')"${tip ? ` title="${esc(tip)}"` : ""}><span class="mx-cc">${icon}</span><span class="t">${esc(name)}</span>${end}</button>`;
  return it("all", MXI.list, "All meetings and calls", `<span class="n">${mxPastAll().length}</span>`)
    + mxSessions().map(({w, as}) => `<div class="mx-nws" title="${esc(tilde(w.folder || ""))}">${MXI.folder}<span>${esc(w.name)}</span></div>`
      + as.map(x => it(x.a.id, "&gt;_", x.a.name, x.live ? '<span class="dot" title="Live now"></span>' : `<span class="n">${x.n}</span>`)).join("")).join("");
}
function mxNavPick(id) { mxMenuClose(); MX.scope = id; if (MX.exp) mxCloseWin(); else { $("mbody")._h = null; mxPaint() } $("mbody").scrollTop = 0 }
function mxScopeMenu(e) { if (!mxMenu(e, `<div class="mx-navm">${mxNav()}</div>`)) return; const m = $("mxmenu"), r = e.currentTarget.getBoundingClientRect();
  m.style.width = Math.min(280, innerWidth - 16) + "px"; m.style.left = r.left + "px"; m.style.right = "auto"; mxKeepIn(m) }
// a live call is the call view; anything else opens here, at its summary
async function mxPastOpen(sid) {
  const m = MX.items.find(x => x.session === sid); if (!m) return;
  await mxSlide("in", async () => {
    if (m.kind === "call" && m.live) { MX.call = MX.call && MX.call.session === sid ? MX.call : {agent: m.from, session: sid, t0: Date.now()}; mxCallShow(); return }
    await mxOpenWin(sid); if (!m.live && !m.paused) mxToSummary();
  });
}
// Moving between the list and one meeting or call slides, as a phone does (the user, 2026-10-25: like
// Telegram): opening, it comes in from the right over the list, which eases a little to the left; back, it
// goes out to the right and the list comes back from the left. 250 ms, eased out; with reduced motion, none.
// The page changes in place, so what was on screen is kept as a still copy for the slide, then dropped.
let MXSLIDING = null;
async function mxSlide(dir, change) {
  const main = $("main"), reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !main || !main.animate || MXSLIDING) return change();
  const shown = () => $("voice").style.display !== "none" ? $("voice") : $("mview");
  const from = shown(), snap = document.createElement("div"), copy = from.cloneNode(true);
  copy.removeAttribute("id"); copy.querySelectorAll("[id]").forEach(e => e.removeAttribute("id"));
  snap.className = "mx-snap" + (from.id === "voice" ? " voice" : ""); snap.appendChild(copy); snap.setAttribute("aria-hidden", "true");
  main.appendChild(snap);
  // the copy at the same scroll as what it copies
  const a = [from, ...from.querySelectorAll("*")], b = [copy, ...copy.querySelectorAll("*")];
  a.forEach((e, i) => { if (e.scrollTop && b[i]) b[i].scrollTop = e.scrollTop });
  MXSLIDING = snap;
  try { await change() } catch (e) {}
  const to = shown(), w = main.clientWidth, t = {duration: 250, easing: "cubic-bezier(.2,.8,.2,1)"};
  to.classList.add("mx-sliding"); snap.classList.toggle("over", dir === "back");
  const done = () => { snap.remove(); to.classList.remove("mx-sliding"); MXSLIDING = null };
  if (dir === "in") {
    to.animate([{transform: `translateX(${w}px)`}, {transform: "none"}], t);
    snap.animate([{transform: "none", opacity: 1}, {transform: `translateX(${-w * .25}px)`, opacity: .5}], t).onfinish = done;
  } else {
    to.animate([{transform: `translateX(${-w * .25}px)`, opacity: .5}, {transform: "none", opacity: 1}], t);
    snap.animate([{transform: "none"}, {transform: `translateX(${w}px)`}], t).onfinish = done;
  }
  setTimeout(() => { if (MXSLIDING === snap) done() }, 600);        // never left behind
}
// The one poller, every 1.5 s while the view is up: what's on screen (the open meeting or call, the call
// going on) each time, the list and what's live every other time. The event stream says sooner.
let MXN = 0;
function mxTick() { if (!MX.view) return; mxPoll(); if (++MXN % 2 === 0) mxLoad() }
// what's polled while it's on screen: the open meeting or call, and the call going on
function mxWatch() {
  MX.watch = MX.exp ? [MX.exp] : [];
  if (MX.call && MX.call.session && !MX.watch.includes(MX.call.session)) MX.watch.push(MX.call.session);
}
async function mxPoll() {
  for (const sid of MX.watch || []) {
    await mxFetch(sid);
    try { const lv = await api("/api/live/" + sid); if (lv && !lv.error) MXW[sid].lv = lv } catch (e) {}
  }
  if ((MX.watch || []).length && MX.view) mxPaint();
  mxCallLog();
}
// opening one: the whole view is it, its record fetched before it paints
async function mxOpenWin(sid) { if (!sid) return; MX.exp = sid; mxMenuClose();
  await mxFetch(sid);
  $("mbody")._h = null; mxPaint() }
function mxCloseWin() { MX.exp = null; $("mbody")._h = null; mxPaint() }
// back to the list: what Claude Code opened is no longer the focus, nor above other windows
function mxBack() { mxSlide("back", () => { MX.fc = null; MX.fcLive = false; mxCloseWin() }) }
// from the orb: that meeting, open
async function mxShowWin(sid) { await mxGo(); await mxOpenWin(sid) }
// The companion's "Open Standby": whatever is live; the past meetings and calls otherwise
async function mxShowLive() {
  await mxLoad();
  const ac = MX.call ? mxAg(MX.call.agent) : MX.agents.find(x => x.live && x.live.call);
  if (ac) { MX.call = MX.call || {agent: ac.id, session: ac.live.call, t0: Date.now()}; await mxGo(); mxCallShow(); return "call" }
  const a = MX.agents.find(x => x.live && x.live.meeting);
  if (a) { await mxShowWin(a.live.meeting); return "meeting" }
  if (!MX.view) await mxGo();
  return "view";
}
// A meeting's Pause, Resume and End say at once what they're doing ("Pausing…"), not pressable twice, until
// the list shows it done (the user, 2026-10-22: a second with no sign it was heard); refused: put back, why said
async function mxBusy(sid, what, words, path) {
  if (MX.busy) return; MX.busy = {sid, what, words}; mxPaint();
  const r = await mxGet(path, {session: sid}); if (r && r.error) toast(r.error);
  await mxLoad(); MX.busy = null; mxPaint(); return r;
}
async function mxPause(sid, pause) { await mxBusy(sid, pause ? "pause" : "resume", pause ? "Pausing\u2026" : "Resuming\u2026", pause ? "/api/msg/mac/pause" : "/api/msg/mac/resume") }
async function mxEndMeeting(sid) { mxMenuClose(); const r = await mxBusy(sid, "end", "Ending\u2026", "/api/msg/mac/end"); if (r && !r.error && MX.exp === sid) mxToSummary() }
async function mxCopyT(sid) { mxMenuClose(); const r = await mxGet("/api/copy", {id: sid}); toast(r && r.error ? r.error : "Transcript copied") }
async function mxSave(sid) { mxMenuClose(); if (window.pywebview && window.pywebview.api) { const r = await window.pywebview.api.save(sid, "md"); toast(r && r.ok ? "Saved" : r && r.error ? r.error : ""); return }
  const a = document.createElement("a"); a.href = "/api/export/" + encodeURIComponent(sid) + "?fmt=md&token=" + encodeURIComponent(SBT); a.download = ""; document.body.appendChild(a); a.click(); a.remove() }
function mxRenameWin(el, sid) { const was = el.textContent; mxEdit(el, was, async v => { const r = await mxGet("/api/rename", {id: sid, title: v}); if (r && r.error) toast(r.error); mxPoll() }) }
// A meeting from Claude Code: how its session gets the meeting, chosen here for this meeting
// (Settings > Recording has the default for new ones)
function mxPassItems(m) {
  const it = (v, words) => `<button class="mi" role="menuitemradio" aria-checked="${m.pass === v}" onclick="mxPass('${esc(m.session)}','${v}')"><span class="ck">${m.pass === v ? MXI.check : ""}</span>${words}</button>`;
  return it("ask", "Claude gets the meeting when you ask about it") + it("live", "Claude follows the meeting as it goes") + "<hr>";
}
async function mxPass(sid, pass) { mxMenuClose(); const r = await mxGet("/api/lite/pass", {session: sid, pass}); if (r && r.error) toast(r.error); mxLoad() }
// ⋯ on a meeting or call: copy it, download it, Settings; live, how Claude gets it (the user, 2026-10-21:
// a compact menu, nothing idle)
function mxWinMenu(e, id) {
  const m = MX.items.find(x => x.id === id); if (!m) return; const sid = esc(m.session), live = m.live || m.paused;
  const mi = (fn, words, cls) => `<button class="mi${cls ? " " + cls : ""}" role="menuitem" onclick="${fn}">${words}</button>`;
  mxMenu(e, mi(`mxCopyT('${sid}')`, "Copy transcript") + mi(`mxSave('${sid}')`, "Download as Markdown\u2026") + "<hr>"
    + (live && m.pass ? mxPassItems(m) : "") + mi("mxMenuClose();openSettings()", "Settings")
    + (live ? "" : "<hr>" + mi(`mxDelete('${sid}')`, "Delete\u2026", "danger")), "mx-menu");
}

// A row's ⋯ (or a right-click on it): copy, download, delete; nothing for what's live
function mxRowMenu(e, sid) {
  const m = MX.items.find(x => x.session === sid); if (!m || m.live || m.paused) return;
  const mi = (fn, words, cls) => `<button class="mi${cls ? " " + cls : ""}" role="menuitem" onclick="${fn}">${words}</button>`;
  if (!mxMenu(e, mi(`mxCopyT('${esc(sid)}')`, "Copy transcript") + mi(`mxSave('${esc(sid)}')`, "Download as Markdown\u2026") + "<hr>" + mi(`mxDelete('${esc(sid)}')`, "Delete\u2026", "danger"), "mx-menu")) return;
  if (e.type === "contextmenu") { const p = $("mxmenu"); p.style.left = e.clientX + "px"; p.style.top = e.clientY + "px"; p.style.right = "auto"; mxKeepIn(p) }
}
// Deleting a meeting or call (the user, 2026-10-22): asked first, in words; its recording, transcript and
// summary go for good (the server: /api/msg/delete, refused while it's live). Every window drops it.
async function mxDelete(sid) {
  mxMenuClose();
  const m = MX.items.find(x => x.session === sid); if (!m) return;
  if (m.live || m.paused) return toast("End it first");
  const what = m.kind === "call" ? "call" : "meeting";
  if (!await ask("Delete this " + what + "?", "Its recording, transcript and summary are removed.", "Delete", true)) return;
  const r = await mxGet("/api/msg/delete", {session: sid});
  if (r && r.error) return toast(r.error);
  mxGone(sid); mxLoad();
}
// gone, deleted here or anywhere: off the list, and closed if it's the one open
function mxGone(sid) { MX.items = MX.items.filter(x => x.session !== sid); delete MXW[sid]; if (MX.exp === sid) mxCloseWin(); else mxPaint() }

/* ---------------------------------------------------------------- moving around */
// a session opened anywhere (the orb): Standby's own window for it. What's live opens as Claude Code
// opens it; the rest at its summary.
async function select(id) { const m = () => MX.items.find(x => x.session === id);
  if (!MX.view || !m()) await mxGo();
  const it = m(); if (it && (it.live || it.paused)) return mxShowLive();
  if (it) return mxPastOpen(id) }
// the one view: past meetings and calls (one of them open when MX.exp names it)
async function mxGo() {
  mxMenuClose();
  MX.view = {kind: "past"}; MX.exp = null;
  const b = $("mbody"); if (b) { b._h = null; b.innerHTML = "" }
  if (voice && !MX.call) closeVoice();
  mxPaint(); await mxLoad();
}

/* menus, and renaming in place */
// A menu's button opens it, and closes it again (so do Esc and a click outside). The button is known by
// what it does: the header it's in is drawn afresh as things change. Returns whether it opened.
function mxMenu(e, html, cls) { e.stopPropagation(); const by = e.currentTarget.getAttribute("onclick"), was = $("mxmenu");
  mxMenuClose(); if (was && was._by === by) return false; const m = document.createElement("div"); m._by = by; m.className = "mx-pop" + (cls ? " " + cls : ""); m.id = "mxmenu"; if (!cls) m.style.width = "230px"; m.setAttribute("role", "menu");
  m.innerHTML = html; document.body.appendChild(m); const r = e.currentTarget.getBoundingClientRect(); m.style.top = (r.bottom + 6) + "px"; m.style.right = (innerWidth - r.right) + "px"; mxKeepIn(m); return true }
// a menu stays inside the window, whatever it grows to
function mxKeepIn(m) { const r = m.getBoundingClientRect(); if (r.left < 8) { m.style.right = "auto"; m.style.left = "8px" }
  else if (r.right > innerWidth - 8) { m.style.right = "auto"; m.style.left = Math.max(8, innerWidth - 8 - r.width) + "px" }
  if (r.bottom > innerHeight - 8) m.style.top = Math.max(8, innerHeight - 8 - r.height) + "px" }
function mxMenuClose() { const m = $("mxmenu"); if (m) m.remove() }
// Renaming is done on the name itself: Enter saves, Esc puts it back, leaving it saves (no dialogs)
function mxEdit(el, was, save, empty) {
  if (!el || el.isContentEditable) return;
  el.contentEditable = "true"; el.classList.add("mx-editing"); el.textContent = was || ""; el.dataset.ph = empty || "";
  el.focus(); const sel = window.getSelection(), rg = document.createRange(); rg.selectNodeContents(el); sel.removeAllRanges(); sel.addRange(rg);
  let done = false;
  const end = async ok => { if (done) return; done = true; el.contentEditable = "false"; el.classList.remove("mx-editing");
    const v = el.textContent.replace(/\s+/g, " ").trim().slice(0, 80);
    if (ok && v !== (was || "") && (v || empty)) { el.textContent = v; await save(v) } else el.textContent = was || "";
    MX.editing = false; mxLoad() };
  MX.editing = true;
  el.onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); end(true) } else if (e.key === "Escape") { e.preventDefault(); end(false) } e.stopPropagation() };
  el.onblur = () => end(true); el.onclick = e => e.stopPropagation();
}

/* ---------------------------------------------------------------- calls: the page's voice overlay, as a call */
// One call view (the user, 2026-10-21): its header, the orb small with what's happening, its conversation.
// Live shows once: here in its header, and in the list as its row.
function mxCallShow() {
  if (!MX.call) return;
  $("vname").textContent = (mxAg(MX.call.agent) || {}).name || "Call";
  if (!voice) attachVoice(MX.call.session); else $("voice").style.display = "";
  mxCallLog();
}
// Back to the list: the call goes on (its row says so; a click brings it back)
function mxCallBack() { mxSlide("back", () => { $("voice").style.display = "none"; MX.fc = null; MX.fcLive = false; mxPaint() }) }
// A call ends one way, whoever ends it: the view lets it go (mxCallGone), and only when the user ended it
// here (End) does it tell the server (mxEndCall). One that ended elsewhere (Claude Code, the orb) comes as
// the event stream's "call" ended, and is only let go. On screen, the same call stays, ended: its summary.
function mxCallGone(c) {
  if (!c || MX.call !== c) return false;
  MX.call = null;
  const shown = $("voice").style.display !== "none";
  if (voice && voice.sid === c.session) voice = null;      // its voice loop stops
  $("voice").style.display = "none";
  const a = mxAg(c.agent); if (a && a.live) a.live.call = null;
  if (shown && c.session) mxOpenWin(c.session).then(mxToSummary);
  return true;
}
async function mxEndCall() {
  const c = MX.call; if (!mxCallGone(c)) return;
  // as both ways did before: its voice stops at once, then the call ends on the server
  if (c.session) { try { await api("/api/voice", {id: c.session, action: "stop"}) } catch (e) {} await mxGet("/api/msg/call/end", {session: c.session}) }
  mxLoad();
}

/* ---------------------------------------------------------------- wiring into the page */
// One tooltip in the app's own style instead of the system's grey one: an element's title moves
// to data-mxtip on first hover and shows after a short pause, below it (above, near the bottom).
let MXTIP = null;
function mxTips() {
  const tip = document.createElement("div"); tip.className = "mx-tip"; tip.setAttribute("role", "tooltip"); document.body.appendChild(tip);
  const hide = () => { clearTimeout(MXTIP && MXTIP.t); MXTIP = null; tip.classList.remove("in") };
  document.addEventListener("pointerover", e => {
    const el = e.target.closest && e.target.closest("[title],[data-mxtip]"); if (!el || (MXTIP && MXTIP.el === el) || el.classList.contains("mx-help")) return;
    if (el.hasAttribute("title")) { const t = el.getAttribute("title"); el.removeAttribute("title"); if (!t) return; el.dataset.mxtip = t;
      if (!el.hasAttribute("aria-label") && !el.textContent.trim()) el.setAttribute("aria-label", t) }
    hide(); const text = el.dataset.mxtip; if (!text) return;
    MXTIP = {el, t: setTimeout(() => { if (!MXTIP || MXTIP.el !== el || !el.isConnected) return;
      tip.textContent = text; const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
      const below = r.bottom + 8 + h < innerHeight;
      tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
      tip.style.top = (below ? r.bottom + 6 : r.top - h - 6) + "px"; tip.classList.add("in") }, 450)};
  }, true);
  document.addEventListener("focusout", e => { if (MXTIP && e.target === MXTIP.el) { clearTimeout(MXTIP.t); MXTIP = null; document.querySelector(".mx-tip").classList.remove("in") } }, true);
  mxHelpWire();
  document.addEventListener("pointerout", e => { if (MXTIP && !(e.relatedTarget && MXTIP.el.contains(e.relatedTarget))) hide() }, true);
  for (const ev of ["pointerdown", "keydown", "wheel"]) document.addEventListener(ev, hide, true);
}
// A (?) beside a setting: its help in a small popover on click (Enter or Space too); a second click, Esc,
// a click outside or a scroll closes it. It goes inside whatever the (?) is in: Settings is a modal dialog,
// and the page's own layer (where the hover tip lives) is under it, so its help never showed (2026-10-21).
function mxHelpClose() { const p = document.querySelector(".mx-helpop"); if (p) p.remove(); return !!p }
function mxHelp(el) {
  const was = document.querySelector(".mx-helpop"); mxHelpClose(); if (was && was._for === el) return;
  const text = el.getAttribute("title") || el.dataset.mxtip; if (!text) return;
  const p = document.createElement("div"); p.className = "mx-helpop"; p.setAttribute("role", "tooltip"); p.textContent = text; p._for = el;
  (el.closest("dialog[open]") || document.body).appendChild(p);
  const r = el.getBoundingClientRect(), w = p.offsetWidth, h = p.offsetHeight;
  p.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
  p.style.top = (r.bottom + 6 + h <= innerHeight - 8 ? r.bottom + 6 : Math.max(8, r.top - h - 6)) + "px";
}
function mxHelpWire() {
  // capture: the (?) sits in a <label>, whose click would flip its switch
  document.addEventListener("click", e => { const h = e.target.closest && e.target.closest(".mx-help");
    if (h) { e.preventDefault(); e.stopPropagation(); mxHelp(h) } else if (!(e.target.closest && e.target.closest(".mx-helpop"))) mxHelpClose() }, true);
  document.addEventListener("keydown", e => { const h = e.target.closest && e.target.closest(".mx-help");
    if (h && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); e.stopPropagation(); mxHelp(h) }
    else if (e.key === "Escape" && mxHelpClose()) { e.preventDefault(); e.stopPropagation() } }, true);
  // Esc in Settings is its dialog's cancel (back, or out): with help open, it only closes the help
  document.addEventListener("cancel", e => { if (mxHelpClose()) e.preventDefault() }, true);
  document.addEventListener("scroll", () => mxHelpClose(), true);
}
function mxInstall() {
  mxTips();
  // shown again after being hidden (closed, in the Dock, behind the orb): load and paint afresh
  let away = false;
  document.addEventListener("visibilitychange", () => { if (document.hidden) away = true; else if (away) { away = false; mxWake() } });
  // the main view
  const mv = document.createElement("section"); mv.id = "mview"; mv.innerHTML = '<div class="mx-headwrap"></div><div id="mbody"></div><div class="mx-ddock" id="mxdock" hidden></div>';
  $("main").insertBefore(mv, $("voice"));
  document.addEventListener("click", e => { if ($("mxmenu") && !e.target.closest("#mxmenu")) mxMenuClose() });
  document.addEventListener("keydown", e => { if (e.key === "Escape") mxMenuClose() });
  // a right-click on a past meeting's or call's row: its menu
  document.addEventListener("contextmenu", e => { const row = e.target.closest && e.target.closest(".mx-prow[data-sid]");
    if (!row) return; e.preventDefault(); mxRowMenu({type: "contextmenu", clientX: e.clientX, clientY: e.clientY, stopPropagation() {}, currentTarget: row}, row.dataset.sid) });
  // a call that can't hear you says so where it would say "Listening"; its header, how long it's been on
  const _paintVoice = paintVoice; paintVoice = function (st) { _paintVoice(st);
    const no = MX.call && mxMicNo((MXW[MX.call.session] || {}).lv), el = $("vstate");
    if (no && el) { el.innerHTML = mxMicNoHtml(no); el.classList.add("mx-warn") } else if (el) el.classList.remove("mx-warn");
    const i = MX.call && ((MXW[MX.call.session] || {}).d || {}).info;
    $("vtime").textContent = i && i.started ? mmss(elapsed(i)) : "";
    // busy, quietly (the user, 2026-10-25; no sound): thinking for over a second, or work going on, or stalled
    const was = JSON.stringify(VTASKS); VTASKS = Array.isArray(st.tasks) ? st.tasks : null;
    if (JSON.stringify(VTASKS) !== was) mxCallLog();
    VTHINK = st.state === "thinking" ? VTHINK || Date.now() : 0;
    const ts = VTASKS || [], working = ts.filter(t => t.state === "running" || t.state === "sent").length, waiting = ts.filter(t => t.state === "queued").length;
    const words = working || waiting ? [working && working + " working", waiting && waiting + " waiting"].filter(Boolean).join(" \u00b7 ")
      : VTHINK && Date.now() - VTHINK > 1000 ? "Thinking" : ts.some(t => t.state === "stalled") ? "No progress lately" : "";
    const bz = $("vbusy"), stall = words === "No progress lately", key = words + stall;
    if (bz && bz._k !== key) { bz._k = key; bz.hidden = !words; bz.className = "vbusy" + (stall ? " stall" : "");
      bz.innerHTML = words ? (stall ? "" : '<span class="spin0"></span>') + esc(words) : "" }
    const k = st.muted ? "paused" : "call", mk = $("vmark"); if (mk && !mk.classList.contains(k)) mk.outerHTML = mxLiveMark(k).replace('<span ', '<span id="vmark" ') };
  // the call's End: the call's one way to end
  const _closeVoice = closeVoice; closeVoice = async function () { if (MX.call) return mxEndCall(); return _closeVoice() };
  setInterval(mxTick, 1500);
  mxEvents();
}
// the event stream (/api/msg/events): what's live, as it changes; polling covers a lost stream
function mxEvents() {
  if (typeof EventSource === "undefined") return;
  let es; try { es = new EventSource("/api/msg/events?token=" + encodeURIComponent(SBT)) } catch (e) { return }     // EventSource takes no header
  // The stream never replays: after a reconnect (the server closed it because this page fell behind, or the
  // connection dropped) what happened meanwhile is loaded again, as the first load does.
  let opened = false;
  es.addEventListener("hello", () => { if (opened) mxLoad(); opened = true });
  es.addEventListener("agent", e => { let a; try { a = JSON.parse(e.data) } catch (x) { return } const i = MX.agents.findIndex(x => x.id === a.id); if (i >= 0) MX.agents[i] = a; else MX.agents.push(a);
    mxPaintSoon() });
  es.addEventListener("workspace", e => { let w; try { w = JSON.parse(e.data) } catch (x) { return } const i = MX.ws.findIndex(x => x.id === w.id);
    if (w.removed) MX.ws = MX.ws.filter(x => x.id !== w.id); else if (i >= 0) MX.ws[i] = w; else MX.ws.push(w); mxPaintSoon() });
  // A call goes on in a new session when your Mac joins it: follow it; an end from the orb or
  // the companion closes the call here too.
  es.addEventListener("call", e => { let c; try { c = JSON.parse(e.data) } catch (x) { return } const a = mxAg(c.agent);
    if (a) a.live = Object.assign({}, a.live, {call: c.phase === "ended" ? null : c.session || (a.live || {}).call});
    if (MX.call && MX.call.agent === c.agent) {
      if (c.phase === "live" && c.session && c.session !== MX.call.session) { MX.call.session = c.session; if (voice) { voice = null; attachVoice(c.session) } }
      if (c.phase === "ended" && mxCallGone(MX.call)) mxLoad() }
    mxPaintSoon() });
  es.addEventListener("deleted", e => { let d; try { d = JSON.parse(e.data) } catch (x) { return } if (d && d.session) mxGone(d.session) });
  es.addEventListener("meeting", e => { let m; try { m = JSON.parse(e.data) } catch (x) { return } const a = mxAg(m.agent);
    if (a) a.live = Object.assign({}, a.live, {meeting: m.live ? m.session : null}); mxLoad() });
  es.onerror = () => {};
}

/* ---------------------------------------------------------------- opened from Claude Code */
// f, as the app sends it: that Claude Code session's call or meeting, {session, kind, live}: what's live
// there, else its last; null when it has none yet. A live call is the call view of THIS call, never one
// remembered from before (a call that ended out of sight leaves MX.call and a voice loop on its old
// session, which would hide the call view as soon as that session reads off); a live meeting, open; an
// ended one, at its summary; none, the past meetings and calls. MX.fc: what Claude Code opened, the focus
// until you go back to the list. The window is an ordinary one, live or not: other apps go in front of it.
async function mxFocusSession(f) {
  MX.fc = null;
  await mxWake();
  await mxGo();                                 // MX.items afresh: one that just started is in it
  const m = f ? MX.items.find(x => x.session === f.session) : null;
  if (f && f.kind === "call" && f.live) {
    if (!MX.call || MX.call.session !== f.session) MX.call = {agent: m && m.from, session: f.session, t0: Date.now()};
    if (typeof voice !== "undefined" && voice && voice.sid !== f.session) voice = null;
    MX.fc = {kind: "call", session: f.session};
    mxCallShow();
  } else if (f) {
    MX.fc = {kind: f.kind, session: f.session};
    await mxOpenWin(f.session);
    if (!f.live) mxToSummary();
  }
  MX.fcLive = !!(f && f.live);
}
// at the summary: the top of an ended meeting or call (painted on the next frame, and once more after)
function mxToSummary() { const top = () => { const b = $("mbody"); if (b) b.scrollTop = 0 }; setTimeout(top, 60); setTimeout(top, 400) }
// A call from Claude Code shows no floating orb unless asked for: this sends the call to it (the window
// goes; a click on the orb brings it back)
async function mxToOrb() { mxMenuClose(); const api = window.pywebview && window.pywebview.api; if (api && api.to_orb) { const r = await api.to_orb(); if (r && r.error) toast(r.error) } }
// ⋯ in a call: Settings (the voice and its model). The orb is its own button beside End (the user, 2026-10-22).
function mxCallMenu(e) {
  mxMenu(e, `<button class="mi" role="menuitem" onclick="mxMenuClose();openSettings()">Settings</button>`, "mx-menu");
}
// the call's conversation, under it: drawn from the record the poller keeps (MXW), no fetch or timer of
// its own; the first time, before the poller has it, fetched once
// New rows come in gently (the user, 2026-10-25): a short fade up for a row that wasn't there, never on the
// first draw, never for the live box or a header; nothing above them moves.
const MXROWS = ".mx-qa,.mx-line,.mx-sep2,.mx-time,.mx-task,.mx-working";
function mxFadeIn(fresh, el) {
  if (el._drawn) for (const n of fresh || []) if (n.matches && n.matches(MXROWS)) n.classList.add("mx-in");
  el._drawn = true;
}
// The voice's own state, kept for the call's log and its busy chip: its task records, and since when it thinks
let VTASKS = null, VTHINK = 0;
async function mxCallLog() {
  const el = $("mxcalllog"), c = MX.call;
  if (!el || !c || !c.session) return;
  const d = (MXW[c.session] || {}).d || await mxFetch(c.session);
  if (!d || spkEditing) return;
  const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  mxFadeIn(mxPatch(el, mxRows(d, null, false, false, true, VTASKS).filter(r => r.k !== "empty")), el);
  if (stick) el.scrollTop = el.scrollHeight;
}
// what Claude Code opened ended: what ended at its summary
function mxFocusCheck() {
  if (!MX.fcLive) return;
  const m = MX.fc && MX.items.find(x => x.session === MX.fc.session);
  if (!m || m.live || m.paused) return;          // still on (or not listed yet)
  MX.fcLive = false;
  if (MX.fc.kind === "call" && MX.exp !== MX.fc.session) mxOpenWin(MX.fc.session).then(mxToSummary); else mxToSummary();
}

mxInstall();
// At launch: Standby's view; a meeting or call live then opens in its own window, as Claude Code opens it.
mxGo().then(() => { if (MX.agents.some(a => a.live && (a.live.meeting || a.live.call))) mxShowLive() });
