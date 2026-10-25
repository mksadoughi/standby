// Standby as it is today, drawn for the films (with screens.js for the colours, the pointer and the captions):
// Claude Code in a terminal with Standby's band above the prompt, Standby's focused meeting and call windows,
// and the orb. Simplified, never the real app's pixels, but the same words, marks and places as the app.

// ---- Claude Code, in Terminal ------------------------------------------------------------------------------------------
const TC = { bg: DARK ? "#101018" : "#1e1e26", bar: DARK ? "#1a1a28" : "#2a2a34", ink: "#e6e6ee", dim: "#8d8d97", acc: "#8b87ff",
  warm: "#d97757", red: "#ff6b6b", green: "#5fb88a", line: "#55556a" };
const TROW = 34, TFONT = 21;
// rows: what the chat shows, top down (each a string, tspans allowed, or blur(s): there but out of focus, so the eye goes
// to what matters); band: the rows above the prompt; prompt: what's typed.
// Rows that don't fit scroll off the top, as a terminal does.
function terminal(r, t, o) {
  const { x, y, w, h } = r, size = o.size || TFONT, row = o.row || TROW;
  let g = `<rect x="${x}" y="${y + 10}" width="${w}" height="${h}" rx="14" fill="#000" opacity="${DARK ? .5 : .18}" filter="url(#soft)"/>` +
    box(x, y, w, h, 14, TC.bg) + box(x, y, w, 40, 14, TC.bar) + box(x, y + 26, w, 14, 0, TC.bar) +
    `<circle cx="${x + 24}" cy="${y + 20}" r="7" fill="#ff5f57"/><circle cx="${x + 46}" cy="${y + 20}" r="7" fill="#febc2e"/><circle cx="${x + 68}" cy="${y + 20}" r="7" fill="#28c840"/>` +
    text(x + w / 2, y + 27, o.title || "trips — claude", 18, { font: SYS, fill: "#a9a9b8" }) +
    text(x + 40, y + 82, "Claude Code", size + 1, { font: MONO, weight: 700, fill: "#e8e6e3", anchor: "start" }) +
    text(x + 40 + (size + 1) * 7.6, y + 82, "~/code/trips", size - 1, { font: MONO, fill: TC.dim, anchor: "start" });
  const band = o.band || [], promptY = y + h - 66, bandTop = promptY - 14 - band.length * row;
  const top = y + 126, fit = Math.max(0, Math.floor((bandTop - top - 6) / row) + 1), rows = o.rows || [], from = Math.max(0, rows.length - fit);
  rows.slice(from).forEach((s, i) => { const b = typeof s === "object";
    g += `<text x="${x + 26}" y="${top + i * row}" font-family="${MONO}" font-size="${size}" fill="${TC.ink}" xml:space="preserve"${b ? ` filter="url(#tblur)" opacity=".5"` : ""}>${b ? s.s : s}</text>` });
  band.forEach((s, i) => { g += `<text x="${x + 26}" y="${bandTop + (i + 1) * row - 8}" font-family="${MONO}" font-size="${size}" fill="${TC.ink}" xml:space="preserve">${s}</text>` });
  g += `<rect x="${x + 18}" y="${promptY}" width="${w - 36}" height="46" rx="10" fill="none" stroke="${TC.line}" stroke-width="2"/>` +
    `<text x="${x + 34}" y="${promptY + 30}" font-family="${MONO}" font-size="${size}" fill="${TC.ink}" xml:space="preserve"><tspan fill="${TC.dim}">&gt;</tspan> ${o.prompt || ""}${o.cursor !== false && blink(t) ? "▍" : ""}</text>`;
  return g;
}
const blur = s => ({ s });                                              // a row out of focus
DEFS_EXTRA += `<filter id="tblur" x="-5%" y="-60%" width="110%" height="220%"><feGaussianBlur stdDeviation="2.6"/></filter>`;
const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const dim = s => `<tspan fill="${TC.dim}">${s}</tspan>`;
const me = s => `<tspan fill="${TC.dim}">&gt;</tspan> ${s}`;                         // your prompt, sent
const said = s => `<tspan fill="${TC.warm}">⏺</tspan> ${s}`;                          // Claude's answer
const tool = (name, arg) => `<tspan fill="${TC.green}">⏺</tspan> <tspan font-weight="700">${name}</tspan>(${arg})`;
const under = (s, first = true) => (first ? dim("  ⎿  ") : `<tspan opacity="0">  ⎿  </tspan>`) + s;              // what a command or tool said, under it
const thinking = t => `<tspan fill="${TC.warm}">${"⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[Math.floor(t * 10) % 10]} Thinking…</tspan>`;
// Standby's band, one live line as the plugin draws it: its mark, what's on, the minutes, the keys (dim, digits in the
// accent; a key just pressed lights up), and under it, dim, the latest of it.
function bandLine(mark, markCol, label, mins, keys, t, press = {}) {
  const k = keys.map(([d, w]) => { const lit = press[d] !== undefined && Math.abs(t - press[d]) < .25;
    return `<tspan fill="${lit ? "#fff" : TC.acc}" font-weight="${lit ? 700 : 400}">${d}</tspan> ${w}` }).join(dim(" · "));
  return `<tspan fill="${markCol}">${mark}</tspan> ${label}  ${dim(mins)}  ${dim("(")}<tspan fill="${TC.dim}">${k}</tspan>${dim(")")}`;
}
const mins = (t, t0, perMin = 1.2, from = 0) => { const m = from + Math.floor(Math.max(0, t - t0) / perMin); return m < 1 ? "&lt;1m" : m + "m" };

// ---- Standby's windows: one meeting or one call, nothing else -----------------------------------------------------------
const W = DARK ? { win: "#1c1c26", line: "#33334a", soft: "#26263a", bubble: "#2e2a5c", ink: "#ececf8", mut: "#a3a3c0", acc: "#8b87ff", red: "#ff6b6b", redsoft: "#3a1f24" }
              : { win: "#ffffff", line: "#e6e5ef", soft: "#f4f3f8", bubble: "#e8e6ff", ink: "#16162a", mut: "#8a8aa0", acc: "#5046e5", red: "#e5484d", redsoft: "#fde3e4" };
function frame(r, k = 1) {
  return `<rect x="${f(r.x)}" y="${f(r.y + 14)}" width="${f(r.w)}" height="${f(r.h)}" rx="18" fill="#1e1a50" opacity="${fo((DARK ? .5 : .16) * k)}" filter="url(#soft)"/>` +
    box(r.x, r.y, r.w, r.h, 18, W.win, { stroke: W.line });
}
const kebab = (x, y) => [0, 1, 2].map(i => `<circle cx="${x + i * 8}" cy="${y}" r="2.6" fill="${W.mut}"/>`).join("");
const laptop = (x, y, c) => `<g fill="none" stroke="${c}" stroke-width="2.4" stroke-linejoin="round"><rect x="${x - 11}" y="${y - 9}" width="22" height="14" rx="2"/><path d="M${x - 15} ${y + 9} h30"/></g>`;
const micIcon = (x, y, c, s = 1) => `<g transform="translate(${f(x)} ${f(y)}) scale(${fs(s)})" fill="none" stroke="${c}" stroke-width="2.2" stroke-linecap="round"><rect x="-4.5" y="-10" width="9" height="13" rx="4.5"/><path d="M-8 0 q8 10 16 0 M0 6 v4"/></g>`;
// Wrap words to lines of about n characters
function wrap(s, n) { const out = []; let cur = ""; for (const wd of s.split(" ")) { if ((cur + " " + wd).trim().length > n && cur) { out.push(cur); cur = wd } else cur = (cur + " " + wd).trim() } if (cur) out.push(cur); return out }

// A window's header, as the app has it now: the three lights, ‹ (back to the list), then the name. Live (o.controls):
// a dot, the name (cut short with … to fit), the time after it, then Pause as an icon (Resume while paused), End as a
// red icon (a hang-up for a call, a stop square for a meeting), on a call "Mini window", and ⋯. Not live: the name and
// under it what it was ("Meeting · 12 min · ended …"). o.lean: only what the story needs (no Mini window, no ⋯).
function header(r, title, sub, o = {}) {
  const { x, y, w } = r;
  let g = [0, 1, 2].map(i => `<circle cx="${x + 26 + i * 22}" cy="${y + 34}" r="7" fill="${["#ff5f57", "#febc2e", "#28c840"][i]}"/>`).join("") +
    `<path d="M${x + 106} ${y + 27} l-7 7 7 7" fill="none" stroke="${W.mut}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`;
  if (!o.controls) return g + ui(x + 124, y + 32, title, 21, { weight: 700, fill: W.ink }) + ui(x + 124, y + 58, sub, 16, { fill: W.mut }) + kebab(x + w - 44, y + 35);
  const cy = y + 36, more = x + w - (o.lean ? 10 : 44), pill = o.call && !o.lean ? 132 : 0, endX = more - 30 - pill - (pill ? 12 : 0) - 20, pauseX = endX - 50;
  g += `<circle cx="${x + 130}" cy="${cy}" r="6" fill="${o.dot || W.red}" opacity=".75"/>`;
  const clock = o.clock || "", room = pauseX - 30 - (x + 146) - clock.length * 10.5 - 12, n = Math.max(4, Math.floor(room / 11.5));
  const name = title.length > n ? title.slice(0, n - 1).trimEnd() + "…" : title;
  g += ui(x + 146, cy + 7, name, 21, { weight: 700, fill: W.ink }) + ui(x + 146 + name.length * 11.5 + 12, cy + 7, clock, 18, { fill: W.mut });
  g += `<circle cx="${pauseX}" cy="${cy}" r="20" fill="${W.win}" stroke="${W.line}" stroke-width="2"/>` +
    (o.paused ? `<path d="M${pauseX - 5} ${cy - 8} l13 8 -13 8 Z" fill="${W.ink}"/>` : box(pauseX - 7, cy - 8, 4.5, 16, 1.5, W.ink) + box(pauseX + 2.5, cy - 8, 4.5, 16, 1.5, W.ink));
  g += `<circle cx="${endX}" cy="${cy}" r="20" fill="${W.redsoft}" stroke="#f2a8b0" stroke-width="2"/>` + (o.call
    ? `<path d="M${endX - 11} ${cy + 3} q11 -9 22 0 l-1.5 4.5 -5.5 -1.8 v-3.2 q-4 -1.6 -8 0 v3.2 l-5.5 1.8 Z" fill="${W.red}"/>`
    : box(endX - 6.5, cy - 6.5, 13, 13, 2.5, W.red));
  if (pill) g += box(endX + 32, cy - 20, pill, 40, 20, W.win, { stroke: W.line, sw: 2 }) + ui(endX + 32 + pill / 2, cy + 6, "Mini window", 16, { anchor: "middle", fill: W.ink });
  return g + (o.lean ? "" : kebab(more, y + 35));
}
// The meeting window: its header; "Started …"; each line's time, who (dotted when the voice is known from before),
// what they said; while someone talks, their words so far, dim, after the red bars.
// o.lines: [{ at, ts, who, col, text, known }] (shown from `at`), o.sep: [{ at, text }], o.live: { who, text, from, to },
// o.clock: the time recorded; o.ended: the whole line under the name instead ("Meeting · 12 min · ended 10:42 AM")
function meetingWin(r, t, o) {
  const { x, y, w, h } = r, pad = 30;
  let g = frame(r) + (o.ended ? header(r, o.title, o.ended) : o.paused ? header(r, o.title, "", { controls: true, paused: true, clock: o.clock, lean: o.lean })
    : header(r, o.title, "", { controls: true, dot: W.red, clock: o.clock, lean: o.lean }));
  const items = [];
  if (o.started) items.push({ at: -1, sep: o.started });
  (o.sep || []).forEach(s => items.push({ at: s.at, sep: s.text }));
  (o.lines || []).forEach(l => items.push(l));
  items.sort((a, b) => a.at - b.at);
  const shown = items.filter(i => t > i.at), ROWH = o.rowh || 84, SEPH = 44, live = o.live && t > o.live.from && t < o.live.to;
  const heights = shown.map(i => i.sep ? SEPH : ROWH), area = h - 98 - (o.top || 0) - (live ? 54 : 0);
  let total = heights.reduce((m, v) => m + v, 0), skip = 0;
  while (total > area && skip < shown.length) { total -= heights[skip]; skip++ }
  let cy = y + 96 + (o.top || 0);
  shown.slice(skip).forEach(i => {
    const k = out(span(t, i.at, i.at + .35));
    if (i.sep) { g += `<g opacity="${fo(k)}">` + ui(x + w / 2, cy + 22, i.sep, 16, { anchor: "middle", fill: W.mut }) + "</g>"; cy += SEPH; return }
    const nx = x + pad + 96;
    g += `<g opacity="${fo(k)}">` + (i.ts ? ui(x + pad, cy + 22, i.ts, 15, { fill: W.mut }) : "") +
      ui(nx, cy + 22, i.who, 18, { fill: i.col, weight: 700 }) +
      (i.known ? `<line x1="${nx}" y1="${cy + 28}" x2="${nx + i.who.length * 10}" y2="${cy + 28}" stroke="${i.col}" stroke-width="1.8" stroke-dasharray="2 3"/>` : "") +
      ui(x + pad + 96, cy + 54, i.text, 21, { fill: W.ink }) + "</g>";
    cy += ROWH;
  });
  if (live) {
    const n = Math.floor(clamp((t - o.live.from) * 18, 0, o.live.text.length)), bx = x + pad + 96;
    g += [0, 1, 2].map(i => box(bx + i * 8, cy + 16 - (6 + 8 * Math.abs(Math.sin(t * 7 + i))), 4.5, 2 * (6 + 8 * Math.abs(Math.sin(t * 7 + i))) - 12, 2, W.red)).join("") +
      ui(bx + 34, cy + 20, o.live.who + ": " + o.live.text.slice(0, n), 17, { fill: W.mut });
  }
  return g;
}

// The orb, as the app draws it: an indigo sphere and its three bars. state: listening, hearing (a cyan glow while you
// talk), thinking (dots, a soft rainbow ring), speaking (a violet glow that breathes), muted (grey).
function orbApp(x, y, R, state, t, op = 1) {
  if (op <= 0) return "";
  const muted = state === "muted", glow = state === "hearing" ? "#3ad7ff" : state === "speaking" ? "#b58cff" : null;
  let g = "";
  if (glow) g += `<circle cx="${f(x)}" cy="${f(y)}" r="${f(R * (1.14 + .05 * Math.sin(t * 6)))}" fill="${glow}" opacity=".35" filter="url(#soft)"/>`;
  if (state === "thinking") g += `<circle cx="${f(x)}" cy="${f(y)}" r="${f(R * 1.07)}" fill="none" stroke="url(#rainbow)" stroke-width="${f(R * .1)}" opacity=".8" transform="rotate(${f(t * 120)} ${f(x)} ${f(y)})" filter="url(#soft)"/>`;
  g += `<circle cx="${f(x)}" cy="${f(y + R * .08)}" r="${f(R)}" fill="#2a1f80" opacity="${muted ? 0 : .18}" filter="url(#soft)"/>` +
    `<circle cx="${f(x)}" cy="${f(y)}" r="${f(R)}" fill="${muted ? "url(#orbgrey)" : "url(#orbapp)"}"/>`;
  if (state === "thinking" || muted) g += [-1, 0, 1].map(i => `<circle cx="${f(x + i * R * .28)}" cy="${f(y)}" r="${f(R * .085)}" fill="#fff" opacity="${fo(muted ? .9 : .55 + .45 * Math.max(0, Math.sin(t * 6 - i)))}"/>`).join("");
  else {
    const amp = state === "listening" ? .06 : .3, base = [.26, .46, .32];
    g += base.map((b, i) => { const hh = R * (b + amp * Math.abs(Math.sin(t * (state === "listening" ? 2 : 9) + i * 1.4)));
      return `<rect x="${f(x + (i - 1) * R * .3 - R * .07)}" y="${f(y - hh)}" width="${f(R * .14)}" height="${f(hh * 2)}" rx="${f(R * .07)}" fill="#fff"/>` }).join("");
  }
  return `<g opacity="${fo(op)}">${g}</g>`;
}
DEFS_EXTRA += `<radialGradient id="orbapp" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#9a8cff"/><stop offset=".55" stop-color="#5a46ee"/><stop offset="1" stop-color="#3a22c8"/></radialGradient>` +
  `<radialGradient id="orbgrey" cx=".35" cy=".3" r=".8"><stop offset="0" stop-color="#e2e2e8"/><stop offset="1" stop-color="#a9a9b4"/></radialGradient>` +
  `<linearGradient id="rainbow" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff7a59"/><stop offset=".35" stop-color="#ffd166"/><stop offset=".65" stop-color="#5ad1ff"/><stop offset="1" stop-color="#b58cff"/></linearGradient>`;

// The call view: its header ("On a call · 00:08", Pause, End, ⋯); the small orb with the exchange going on beside it
// (what you asked, dim; what the voice says); then what was said: yours in a bubble with a mic, the voice's after its
// small mark.
// o.items: [{ at, kind: "you" | "voice" | "asked", text }], o.state(t), o.now(t): { q, a }, o.clock
const voiceMark = (x, y) => box(x - 12, y - 12, 24, 24, 7, "#6d4ff0") + [-1, 0, 1].map((j, m) => box(x + j * 5.5 - 1.5, y - [4, 7, 5][m], 3, [8, 14, 10][m], 1.5, "#fff")).join("");
function callWin(r, t, o) {
  const { x, y, w, h } = r;
  let g = frame(r) + header(r, o.title, "", { controls: true, call: true, dot: "#b9b4f2", clock: o.clock || "00:08", lean: o.lean });
  const st = o.state(t), oy = y + 130;
  g += orbApp(x + 64, oy, 40, st, t);
  if (o.now) {
    const q = o.now(t), n = Math.floor((w - 150) / 10.4);
    if (q && q.q) g += ui(x + 122, oy - 22, wrap(q.q, n + 6)[0], 16, { fill: W.mut });
    if (q && q.a) wrap(q.a, n).slice(0, 2).forEach((s, i) => { g += ui(x + 122, oy + 8 + i * 28, s, 20, { fill: W.ink }) });
  }
  g += `<line x1="${x}" y1="${oy + 72}" x2="${x + w}" y2="${oy + 72}" stroke="${W.line}" stroke-width="1.5"/>`;
  // each line wrapped to the window, as the app does: yours in a bubble on the right, the voice's after its mark
  const items = (o.items || []).filter(i => t > i.at), area = y + h - 24 - (oy + 92);
  const nYou = Math.floor((w - 150) / 9.2), nVoice = Math.floor((w - 100) / 9.2);
  const rows = i => wrap(i.text, i.kind === "you" ? nYou : nVoice), H = i => i.kind === "you" ? 66 + (rows(i).length - 1) * 24 : i.kind === "voice" ? 50 + (rows(i).length - 1) * 24 : 40;
  let total = items.reduce((m, i) => m + H(i), 0), skip = 0;
  while (total > area && skip < items.length) { total -= H(items[skip]); skip++ }
  let iy = oy + 92;
  items.slice(skip).forEach(i => {
    const k = out(span(t, i.at, i.at + .35)), ls = rows(i);
    if (i.kind === "you") { const bw = Math.min(w - 80, Math.max(...ls.map(l => l.length)) * 9.4 + 64), bh = 52 + (ls.length - 1) * 24;
      g += `<g opacity="${fo(k)}">` + box(x + w - 24 - bw, iy, bw, bh, 18, W.bubble) + micIcon(x + w - 24 - bw + 26, iy + 27, W.acc, .9) +
        ls.map((l, j) => ui(x + w - 24 - bw + 46, iy + 33 + j * 24, l, 17, { fill: W.acc })).join("") + "</g>" }
    else if (i.kind === "voice") g += `<g opacity="${fo(k)}">` + voiceMark(x + 40, iy + 19) + ls.map((l, j) => ui(x + 66, iy + 25 + j * 24, l, 17, { fill: W.ink })).join("") + "</g>";
    iy += H(i);
  });
  return g;
}
