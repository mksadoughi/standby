// The crisp screens for Standby's feature demos, the approved look (2026-10-12): simplified windows drawn
// clearly, never the real app's pixels. Claude Code's dark Terminal, Standby with its logo, lavender sidebar and
// indigo; a pointer aimed at what it clicks; a caption at the top. Used with kit.js by yours.html, orb.html and the rest.
const MONO = "Menlo, ui-monospace, monospace", SYS = "-apple-system, 'SF Pro Text', system-ui, sans-serif";
const typed = (s, t, t0, cps = 24) => s.slice(0, Math.floor(clamp((t - t0) * cps, 0, s.length)));
const blink = t => Math.floor(t * 2.2) % 2 === 0;
const ui = (x, y, s, size, o = {}) => text(x, y, s, size, { font: SYS, anchor: "start", ...o });
const box = (x, y, w, h, r, fill, o = {}) => `<rect x="${f(x)}" y="${f(y)}" width="${f(w)}" height="${f(h)}" rx="${r}" fill="${fill}"` +
  (o.stroke ? ` stroke="${o.stroke}" stroke-width="${o.sw || 1.5}"` : "") + (o.op !== undefined ? ` opacity="${fo(o.op)}"` : "") + "/>";
const C = DARK ? { win: "#1c1c26", side: "#232340", line: "#33334a", sel: "#3a3366", soft: "#26263a", ink: "#ececf8", mut: "#a3a3c0", acc: "#8b87ff" }
               : { win: "#ffffff", side: "#f4f3fc", line: "#e6e5ef", sel: "#e4e2ff", soft: "#f6f5fb", ink: "#16162a", mut: "#76768c", acc: "#5046e5" };
const logo = (x, y, s = 30) => `<rect x="${x}" y="${y}" width="${s}" height="${s}" rx="${s * .27}" fill="url(#logo)"/>` +
  [-1, 0, 1].map((j, m) => `<rect x="${f(x + s / 2 + j * s * .2 - s * .05)}" y="${f(y + s / 2 - [.2, .38, .27][m] * s)}" width="${f(s * .1)}" height="${f([.4, .76, .54][m] * s)}" rx="${f(s * .05)}" fill="#fff"/>`).join("");
const mid = r => [r.x + r.w / 2, r.y + r.h / 2];
// The pointer: K is [[t, x, y], …] (where it is when), clicks are the times it clicks; aimed at its tip.
function pointerAt(t, K, clicks, show = 1) {
  let i = 0; while (i < K.length - 2 && t > K[i + 1][0]) i++;
  const [t0, x0, y0] = K[i], [t1, x1, y1] = K[i + 1], q = inout(span(t, t0, t1)), x = lerp(x0, x1, q), y = lerp(y0, y1, q);
  const c = clicks.reduce((m, ct) => Math.max(m, 1 - Math.abs(t - ct) / .16), 0);
  return (c > 0 ? `<circle cx="${f(x)}" cy="${f(y)}" r="${f(12 + 16 * (1 - c))}" fill="none" stroke="${C.acc}" stroke-width="3" opacity="${fo(c * .7)}"/>` : "") +
    at(x, y, 1 - c * .12, `<path d="M0 0 L0 28 L7.5 21.5 L13 33.5 L18.5 31 L13 19.5 L23 19.5 Z" fill="#fff" stroke="#16162a" stroke-width="2.2" stroke-linejoin="round"/>`, show);
}
// The words at the top: [[from, to, sentence], …], one at a time.
const captions = (t, lines) => lines.map(([a, b, s]) => { const i = out(span(t, a, a + .6)), o = span(t, b - .4, b), k = i * (1 - o);
  return k > 0 ? `<g opacity="${fo(k)}" transform="translate(0 ${f((1 - i) * 12 - o * 8)})">${text(800, 96, s, 46, { font: ROUND, weight: 700, fill: C.ink })}</g>` : "" }).join("");
// Draw film(t) into the page's svg, as render(t) for tools/demo/render.py; opened as a file, it plays live.
// A scene can add its own gradients in DEFS_EXTRA.
var DEFS_EXTRA = "";
function play(film) {
  const S0 = document.getElementById("s");
  window.render = t => {
    t = ((t % DUR) + DUR) % DUR;
    const fade = out(span(t, 0, .4)) * (1 - span(t, DUR - .4, DUR));
    S0.innerHTML = DEFS.replace("</defs>", `<linearGradient id="logo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5b4ff0"/><stop offset="1" stop-color="#8b3fe8"/></linearGradient>${DEFS_EXTRA}</defs>`) +
      `<g opacity="${fo(fade)}">${film(t)}</g>`;
    S0.style.background = PAPER;
  };
  if (!/[?&]render/.test(location.search)) {
    const t0 = performance.now();
    (function tick(now) { window.render((now - t0) / 1000); requestAnimationFrame(tick) })(t0);
  } else window.render(0);
}
// People, drawn flat to match the screens (the meeting and orb demos): a person at the table, their name above them,
// and their voice going out as three arcs.
function human(x, y, o, t) {
  const { skin, hair, shirt, look, long } = o, open = o.talk * (.5 + .5 * Math.abs(Math.sin(t * 13)));
  let g = long ? `<path d="M${x - 56} ${y + 46} V${y - 6} Q${x - 56} ${y - 62} ${x} ${y - 62} Q${x + 56} ${y - 62} ${x + 56} ${y - 6} V${y + 46} Z" fill="${hair}"/>` : "";
  g += `<path d="M${x - 80} ${y + 270} V${y + 124} Q${x - 80} ${y + 70} ${x - 32} ${y + 64} H${x + 32} Q${x + 80} ${y + 70} ${x + 80} ${y + 124} V${y + 270} Z" fill="${shirt}"/>` +
    `<rect x="${x - 15}" y="${y + 34}" width="30" height="34" fill="${skin}"/><circle cx="${x}" cy="${y}" r="50" fill="${skin}"/>`;
  g += long ? `<path d="M${x - 50} ${y - 6} Q${x - 50} ${y - 56} ${x} ${y - 56} Q${x + 50} ${y - 56} ${x + 50} ${y - 6} Q${x + 10} ${y - 40} ${x - 50} ${y - 6} Z" fill="${hair}"/>`
    : `<path d="M${x - 52} ${y - 2} Q${x - 56} ${y - 60} ${x} ${y - 60} Q${x + 56} ${y - 60} ${x + 52} ${y - 2} Q${x + 32} ${y - 32} ${x} ${y - 30} Q${x - 32} ${y - 32} ${x - 52} ${y - 2} Z" fill="${hair}"/>`;
  g += [-1, 1].map(s => `<circle cx="${x + s * 17 + look * 7}" cy="${y - 2}" r="5.5" fill="#22223a"/>`).join("");
  g += open > .08 ? `<ellipse cx="${x + look * 7}" cy="${y + 23}" rx="10" ry="${f(2.5 + open * 7)}" fill="#7a3b3b"/>`
    : `<path d="M${x - 10 + look * 7} ${y + 21} q10 8 20 0" fill="none" stroke="#22223a" stroke-width="3" stroke-linecap="round"/>`;
  return g;
}
function tag(x, y, s, c, k) {
  if (k <= 0) return ""; const w = s.length * 10.5 + 34;
  return `<g opacity="${fo(k)}" transform="translate(0 ${f((1 - k) * 10)})">` + box(x - w / 2, y - 20, w, 40, 20, C.win, { stroke: c, sw: 2.5 }) + ui(x, y + 7, s, 19, { anchor: "middle", fill: c, weight: 700 }) + "</g>";
}
function voice(x, y, dir, c, k, t) {
  if (k <= 0) return "";
  return [0, 1, 2].map(i => { const a = .35 + .65 * Math.max(0, Math.sin(t * 6 - i * 1.1));
    return `<path d="M${f(x + dir * i * 16)} ${f(y - 14 - i * 9)} q ${f(dir * (11 + i * 4))} ${f(14 + i * 9)} 0 ${f(28 + i * 18)}" fill="none" stroke="${c}" stroke-width="4" stroke-linecap="round" opacity="${fo(k * a)}"/>` }).join("");
}
