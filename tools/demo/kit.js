// The drawing kit for Standby's films: hand-drawn shapes, the cast and the cards, shared by
// sketch.html (the hero) and features.html (the feature films). Every frame is a function of time, render(t),
// so tools/demo/render.py can step a page into a video. Light and dark follow the system.
const DARK = matchMedia("(prefers-color-scheme: dark)").matches;
const TAU = Math.PI * 2;
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const lerp = (a, b, p) => a + (b - a) * p;
const span = (t, a, b) => clamp((t - a) / (b - a));
const out = p => 1 - Math.pow(1 - clamp(p), 4);
const inout = p => (p = clamp(p), p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const pop = p => { p = clamp(p); const c = 1.9; return 1 + (c + 1) * Math.pow(p - 1, 3) + c * Math.pow(p - 1, 2) };   // a bouncy arrival
function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let r = Math.imul(seed ^ seed >>> 15, 1 | seed); r = r + Math.imul(r ^ r >>> 7, 61 | r) ^ r; return ((r ^ r >>> 14) >>> 0) / 4294967296 } }
const f = n => n.toFixed(1);
const fs = n => n.toFixed(4), fo = n => n.toFixed(3);            // a scale, an opacity: f's tenths would move them in steps
const HAND = "Noteworthy, 'Chalkboard SE', 'Comic Sans MS', cursive";
const ROUND = "ui-rounded, 'SF Pro Rounded', -apple-system, system-ui, sans-serif";

// ---- drawing by hand: points with a little wobble, joined smoothly, stroked twice -------------------------------
function smooth(pts, closed) {
  const n = pts.length, at = i => closed ? pts[(i + n) % n] : pts[clamp(i, 0, n - 1)];
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2);
    d += `C${f(p1[0] + (p2[0] - p0[0]) / 6)} ${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)} ${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d + (closed ? "Z" : "");
}
function wobble(pts, seed, amp) {
  const r = rng(seed), a = r() * TAU, b = r() * TAU;
  return pts.map((p, i) => { const k = i / pts.length * TAU; return [p[0] + (Math.sin(k * 2 + a) * .7 + (r() - .5) * .6) * amp, p[1] + (Math.cos(k * 3 + b) * .7 + (r() - .5) * .6) * amp] });
}
function rectPts(x, y, w, h, r) {
  const pts = [], side = (ax, ay, bx, by) => { const n = Math.max(1, Math.round(Math.hypot(bx - ax, by - ay) / 45)); for (let i = 0; i < n; i++) pts.push([lerp(ax, bx, i / n), lerp(ay, by, i / n)]) };
  const arc = (cx, cy, a0) => { for (let i = 0; i < 4; i++) { const a = a0 + i / 4 * Math.PI / 2; pts.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]) } };
  side(x + r, y, x + w - r, y); arc(x + w - r, y + r, -Math.PI / 2); side(x + w, y + r, x + w, y + h - r); arc(x + w - r, y + h - r, 0);
  side(x + w - r, y + h, x + r, y + h); arc(x + r, y + h - r, Math.PI / 2); side(x, y + h - r, x, y + r); arc(x + r, y + r, Math.PI);
  return pts;
}
const circlePts = (cx, cy, r, n = 26, ry = r) => Array.from({ length: n }, (_, i) => [cx + Math.cos(i / n * TAU) * r, cy + Math.sin(i / n * TAU) * ry]);
// One shape, as a person draws it: a fill, then the outline twice, a little apart. `draw` 0..1 draws it on.
function sketch(pts, o = {}) {
  const { seed = 1, amp = 1.6, stroke = INK, sw = 3, fill = "none", closed = true, draw = 1, opacity = 1, twice = true } = o;
  if (draw <= 0) return "";
  const d1 = smooth(wobble(pts, seed, amp), closed), d2 = smooth(wobble(pts, seed + 99, amp), closed);
  const dash = draw < 1 ? ` pathLength="1" stroke-dasharray="1 1" stroke-dashoffset="${f(1 - draw)}"` : "";
  return (fill !== "none" ? `<path d="${d1}" fill="${fill}" opacity="${fo(opacity * clamp(draw * 1.6))}"/>` : "") +
    `<path d="${d1}" fill="none" stroke="${stroke}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" opacity="${opacity}"${dash}/>` +
    (twice ? `<path d="${d2}" fill="none" stroke="${stroke}" stroke-width="${sw * .55}" stroke-linecap="round" opacity="${opacity * .7}"${dash}/>` : "");
}
const line = (x1, y1, x2, y2, o = {}) => sketch(Array.from({ length: 6 }, (_, i) => [lerp(x1, x2, i / 5), lerp(y1, y2, i / 5)]), { closed: false, ...o });
let squiggle = (x, y, w, seed, col, sw = 3) => sketch(Array.from({ length: 9 }, (_, i) => [x + i / 8 * w, y + Math.sin(i * 1.7 + seed) * 4]), { closed: false, stroke: col, sw, amp: .8, twice: false, seed });
const text = (x, y, s, size, o = {}) => `<text x="${f(x)}" y="${f(y)}" font-family="${o.font || HAND}" font-size="${size}" font-weight="${o.weight || 400}" fill="${o.fill || INK}" text-anchor="${o.anchor || "middle"}" opacity="${o.opacity ?? 1}">${s}</text>`;
const at = (x, y, s, inner, op = 1, rot = 0) => op <= 0 || s <= 0 ? "" : `<g transform="translate(${f(x)} ${f(y)}) rotate(${f(rot)}) scale(${fs(s)})" opacity="${fo(clamp(op))}">${inner}</g>`;

// ---- the palette (light, and dark) ----------------------------------------------------------------------------
const INK = DARK ? "#ecebf6" : "#22223a";
const PAPER = DARK ? "#1c1c2e" : "#fdfcf8";
const MUTE = DARK ? "#a3a3c0" : "#6e6e88";
const INDIGO = DARK ? "#8b87ff" : "#5046e5", WARM = "#d97757";
const PASTEL = DARK ? ["#2f3a5c", "#2d4a3f", "#4a3f2a", "#4a2f43", "#3a3366"] : ["#dfe8fb", "#dcf1e4", "#fff1c9", "#fde1ea", "#e7e3ff"];

// ---- the cast ---------------------------------------------------------------------------------------------------
function person(seed = 3) {                                       // you, at your laptop
  const skin = "#f6d2b5", hair = DARK ? "#8b87ff" : "#5b4fd6", body = DARK ? "#6a62e8" : "#7b72f2";
  return sketch([[-82, 92], [-80, 28], [-40, 4], [0, 0], [40, 4], [80, 28], [82, 92]], { fill: body, seed, closed: true, sw: 3 }) +
    sketch(circlePts(0, -52, 46), { fill: skin, seed: seed + 1 }) +
    sketch([[-46, -58], [-44, -88], [-14, -104], [22, -102], [46, -78], [44, -56], [20, -74], [-8, -70], [-30, -62]], { fill: hair, seed: seed + 2 }) +
    sketch(circlePts(-17, -48, 13), { seed: seed + 3, sw: 2.6 }) + sketch(circlePts(17, -48, 13), { seed: seed + 4, sw: 2.6 }) +
    line(-4, -48, 4, -48, { sw: 2.4, seed: seed + 5 }) +
    sketch([[-14, -22], [0, -16], [14, -22]], { closed: false, sw: 2.4, seed: seed + 6, twice: false }) +
    sketch(rectPts(-74, 24, 148, 78, 10), { fill: DARK ? "#3a3a52" : "#e3e4ee", seed: seed + 7 }) + sketch(circlePts(0, 62, 9), { seed: seed + 8, sw: 2.4 });
}
function claude(seed = 40, blink = 0) {                           // Claude Code: its terminal and its name, in its own warm colour
  return sketch(rectPts(-150, -48, 104, 96, 14), { fill: DARK ? "#0d0d14" : "#18181e", seed, stroke: INK }) +
    text(-98, 16, ">_", 44, { font: "Menlo, monospace", weight: 700, fill: blink > .5 ? WARM : "#ffffff" }) +
    sketch(rectPts(-40, -48, 196, 96, 12), { fill: DARK ? "#2a2232" : "#fff8f3", seed: seed + 1 }) +
    `<g font-family="Menlo, monospace" font-weight="800" font-size="31" fill="${DARK ? "#e9a58b" : "#f0b49c"}" stroke="${WARM}" stroke-width="1.6" paint-order="stroke" letter-spacing="2">` +
    `<text x="58" y="-6" text-anchor="middle">CLAUDE</text><text x="58" y="30" text-anchor="middle">CODE</text></g>`;
}
function orb(R, t, level, seed = 70, outline = true, rings = null) {   // Standby: the app's orb and its three bars
  const bars = [.24, .46, .33].map((b, i) => b + level * .13 * Math.sin(t * TAU + i * 1.3));
  const bw = R * .18, gap = R * .18;
  // Claude talking (rings = the time): two rings going out to 1.22x, 1.8 s each, the second 0.9 s behind
  const ring = rings === null ? "" : [0, .9].map(d => { const q = (((rings - d) % 1.8) + 1.8) % 1.8 / 1.8;
    return `<circle r="${f(R * lerp(1, 1.22, q))}" fill="none" stroke="#8f86ff" stroke-width="${f(R * .06)}" opacity="${fo(.55 * (1 - q))}"/>` }).join("");
  return ring + `<circle r="${f(R * 1.6)}" fill="url(#halo)"/><circle r="${f(R)}" fill="url(#orbfill)"/>` +
    (outline ? sketch(circlePts(0, 0, R), { seed, sw: 2.6, stroke: DARK ? "#c9c6ff" : "#3b2fc0", twice: false }) : "") +
    bars.map((h, i) => `<rect x="${f((i - 1) * (bw + gap) - bw / 2)}" y="${f(-h * R)}" width="${f(bw)}" height="${f(h * 2 * R)}" rx="${f(bw / 2)}" fill="#fff"/>`).join("");
}
const DEFS = `<defs>
  <radialGradient id="orbfill" cx=".34" cy=".28" r=".95"><stop offset="0" stop-color="#d9d6ff"/><stop offset=".32" stop-color="#8f86ff"/><stop offset=".66" stop-color="#5046e5"/><stop offset="1" stop-color="#3b2fc0"/></radialGradient>
  <radialGradient id="halo"><stop offset=".55" stop-color="#5046e5" stop-opacity=".28"/><stop offset="1" stop-color="#5046e5" stop-opacity="0"/></radialGradient>
  <radialGradient id="warmhalo"><stop offset="0" stop-color="${WARM}" stop-opacity=".45"/><stop offset="1" stop-color="${WARM}" stop-opacity="0"/></radialGradient>
  <filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="8" stdDeviation="10" flood-color="#1e1a50" flood-opacity="${DARK ? .5 : .14}"/></filter>
</defs>`;

// The busy, fuzzy day: things that come and go. Each is drawn in its own little card.
const THINGS = [
  (c, s) => sketch(rectPts(-78, -40, 156, 80, 18), { fill: c, seed: s }) + sketch([[-40, 38], [-52, 62], [-18, 40]], { fill: c, seed: s + 1, closed: false, sw: 3 }) +
    squiggle(-56, -14, 110, s, MUTE) + squiggle(-56, 10, 80, s + 2, MUTE),                                        // something said
  (c, s) => sketch(rectPts(-86, -56, 172, 112, 12), { fill: c, seed: s }) + [[-42, -20], [42, -20], [-42, 26], [42, 26]].map(([x, y], i) =>
    sketch(rectPts(x - 36, y - 20, 72, 40, 6), { fill: PAPER, seed: s + i + 1, sw: 2 }) + sketch(circlePts(x, y - 2, 9), { fill: ["#f6d2b5", "#e8b894", "#d9a27e", "#f2c9a8"][i], seed: s + i + 5, sw: 2 })).join(""),   // a call
  (c, s) => sketch(rectPts(-56, -56, 112, 112, 12), { fill: PAPER, seed: s }) + sketch(rectPts(-56, -56, 112, 30, 10), { fill: "#ff8f8f", seed: s + 1 }) +
    text(0, 30, "10:00", 30, { weight: 700 }),                                                                    // a calendar slot
  (c, s) => sketch(rectPts(-54, -54, 108, 108, 4), { fill: DARK ? "#5a5126" : "#fff0a6", seed: s }) + squiggle(-36, -16, 72, s, MUTE) + squiggle(-36, 10, 54, s + 1, MUTE),   // a note
  (c, s) => sketch(rectPts(-84, -50, 168, 100, 14), { fill: c, seed: s }) + sketch(rectPts(-66, -34, 84, 26, 12), { fill: PAPER, seed: s + 1, sw: 2 }) +
    sketch(rectPts(-12, 4, 80, 26, 12), { fill: DARK ? "#3a3366" : "#d9d4ff", seed: s + 2, sw: 2 }),             // a chat
  (c, s) => sketch([[0, -50], [30, -36], [36, -6], [18, 16], [16, 34], [-16, 34], [-18, 16], [-36, -6], [-30, -36]], { fill: DARK ? "#5a5126" : "#fff3b0", seed: s }) +
    line(-12, 44, 12, 44, { sw: 3, seed: s + 1 }) + [[-62, -40], [62, -40], [0, -78]].map(([x, y], i) => line(x * .72, y * .72, x, y, { sw: 3, seed: s + i + 2 })).join(""),   // an idea
];
// Where a thing comes and goes: its spot, its kind, when it starts. `life` is how long it stays.
function thing(i, cx, cy, born, life, extra = {}) {
  return { i, cx, cy, born, life, kind: i % THINGS.length, tint: PASTEL[i % PASTEL.length], seed: 200 + i * 13, rot: ((i * 37) % 11) - 5, ...extra };
}
function drawThing(th, t, opts = {}) {
  const age = t - th.born;
  if (age < 0 || age > th.life) return "";
  const s = (opts.scale || 1) * pop(span(age, 0, .45)) * (1 - out(span(age, th.life - .4, th.life)) * .9);
  const op = clamp(age / .15) * (1 - span(age, th.life - .35, th.life));
  return at(opts.x ?? th.cx, opts.y ?? th.cy, s * (opts.shrink ?? 1), THINGS[th.kind](th.tint, th.seed), op * (opts.opacity ?? 1), th.rot);
}

// ---- the four versions -----------------------------------------------------------------------------------------
function title(t, a, y = 128, o = {}) {
  const k = out(span(t, a, a + .9));
  return k <= 0 ? "" : `<g opacity="${fo(k)}" transform="translate(0 ${f((1 - k) * 18)})">` +
    text(800, y, "Your Claude Code, right beside you.", o.size || 72, { font: o.font || HAND, weight: o.weight || 700, fill: o.fill || INK }) +
    (o.mark === false ? "" : at(800, y + 58, 1, `<rect x="-78" y="-21" width="36" height="36" rx="10" fill="#5b4ff0"/>` +
      [-1, 0, 1].map((i, k2) => `<rect x="${-60 + i * 9 - 2}" y="${-3 - [5, 10, 7][k2]}" width="4.4" height="${[10, 20, 14][k2]}" rx="2.2" fill="#fff"/>`).join("") +
      text(-30, 11, "Standby", 30, { font: ROUND, weight: 700, anchor: "start" }), out(span(t, a + .4, a + 1.2)))) + "</g>";
}
const pill = (x, y, s, fill, op) => op <= 0 ? "" : `<g opacity="${fo(op)}">${sketch(rectPts(x - 12, y - 46, s.length * 22 + 50, 64, 22), { fill, seed: 9, sw: 2.4 })}${text(x + 13, y, s, 40, { anchor: "start" })}</g>`;


// ---- the hero's cast, shared with the feature films ----------------------------------------------------------
const Y = 690;                                                      // the one level: you, Standby and Claude Code sit on it
function face(x, y, r, skin, hair, seed) {                          // a small person: head, hair, shoulders
  return sketch([[x - r * 1.5, y + r * 2.6], [x - r * 1.4, y + r * 1.4], [x - r * .6, y + r * 1.05], [x + r * .6, y + r * 1.05], [x + r * 1.4, y + r * 1.4], [x + r * 1.5, y + r * 2.6]],
    { fill: hair[1], seed, sw: 2.2, closed: true }) + sketch(circlePts(x, y, r, 18), { fill: skin, seed: seed + 1, sw: 2.2 }) +
    sketch([[x - r, y - r * .1], [x - r * .9, y - r * .8], [x, y - r * 1.15], [x + r * .9, y - r * .8], [x + r, y - r * .1], [x + r * .3, y - r * .55], [x - r * .4, y - r * .5]], { fill: hair[0], seed: seed + 2, sw: 2 });
}
const SKIN = ["#f6d2b5", "#e8b894", "#c98f6b", "#f2c9a8"], HAIR = [["#4a3b2f", "#7b72f2"], ["#2b2b3a", "#5fb88a"], ["#c0703f", "#f2a65a"], ["#3b3486", "#e77ea6"]];
const bubble = (x, y, w, s, tail = -1) => sketch(rectPts(x - w / 2, y - 22, w, 44, 16), { fill: PAPER, seed: s, sw: 2.2 }) +
  sketch([[x + tail * 10, y + 18], [x + tail * 18, y + 34], [x + tail * -4, y + 20]], { fill: PAPER, seed: s + 1, closed: false, sw: 2.2 }) + squiggle(x - w / 2 + 16, y, w - 32, s, MUTE, 2.4);
const waves = (x, y, t, dir = 1) => [0, 1, 2].map(k => { const a = .5 + .5 * Math.sin(t * 5 - k * 1.1);
  return `<path d="M${f(x + dir * k * 12)} ${f(y - 10 - k * 6)} q ${f(dir * (8 + k * 3))} ${f(10 + k * 6)} 0 ${f(20 + k * 12)}" fill="none" stroke="${INDIGO}" stroke-width="3" stroke-linecap="round" opacity="${fo(.3 + .6 * a)}"/>` }).join("");
// What you hear and see in a day: four kinds, each in two looks, so the stream doesn't repeat itself.
const DAY = [
  ["online meeting", [(t, s) => {                                   // the grid
    const talking = Math.floor(t * .9) % 4;
    return sketch(rectPts(-125, -82, 250, 164, 14), { fill: PAPER, seed: s }) + sketch(rectPts(-125, -82, 250, 26, 12), { fill: PASTEL[0], seed: s + 1, sw: 2.2 }) +
      [[-114, -48], [4, -48], [-114, 14], [4, 14]].map(([x, y], i) => sketch(rectPts(x, y, 110, 56, 7), { fill: DARK ? "#2a2a40" : "#eef0f8", seed: s + 3 + i, sw: 2,
        stroke: i === talking ? INDIGO : INK }) + face(x + 55, y + 22, 11, SKIN[i], HAIR[i], s + 10 + i * 4)).join("");
  }, (t, s) => {                                                    // one speaker, the rest in a strip
    return sketch(rectPts(-125, -82, 250, 164, 14), { fill: PAPER, seed: s }) + sketch(rectPts(-125, -82, 250, 26, 12), { fill: PASTEL[0], seed: s + 1, sw: 2.2 }) +
      sketch(rectPts(-114, -48, 170, 120, 8), { fill: DARK ? "#2a2a40" : "#eef0f8", seed: s + 2, sw: 2, stroke: INDIGO }) + face(-29, -6, 22, SKIN[2], HAIR[2], s + 3) +
      [0, 1, 2].map(i => sketch(rectPts(64, -48 + i * 41, 50, 36, 6), { fill: DARK ? "#2a2a40" : "#eef0f8", seed: s + 8 + i, sw: 1.8 }) + face(89, -36 + i * 41, 7, SKIN[i], HAIR[(i + 1) % 4], s + 12 + i * 4)).join("");
  }]],
  ["in person", [(t, s) => {                                        // three of you
    const who = Math.floor(t * .8) % 3, xs = [-78, 0, 78];
    return xs.map((x, i) => face(x, 10, 24, SKIN[i + 1], HAIR[(i + 1) % 4], s + i * 5)).join("") + bubble(xs[who] + 20, -62, 92, s + 30 + who);
  }, (t, s) => {                                                    // two, talking it over
    const who = Math.floor(t * .9) % 2;
    return face(-50, 14, 28, SKIN[0], HAIR[2], s) + face(50, 14, 28, SKIN[3], HAIR[1], s + 5) + bubble(who ? 58 : -42, -64, 96, s + 20 + who, who ? 1 : -1);
  }]],
  ["thinking out loud", [(t, s) => face(-14, 4, 30, SKIN[0], HAIR[0], s) + waves(30, -6, t), (t, s) =>
    face(-30, 8, 28, SKIN[1], HAIR[3], s) + waves(10, -2, t) + sketch([[56, -70], [74, -60], [78, -40], [68, -26], [66, -14], [46, -14], [44, -26], [34, -40], [38, -60]], { fill: DARK ? "#5a5126" : "#fff3b0", seed: s + 9, sw: 2.2 })]],
  ["on your screen", [(t, s) => sketch(rectPts(-118, -78, 236, 140, 10), { fill: PAPER, seed: s }) + sketch([[-20, 62], [-30, 92], [30, 92], [20, 62]], { fill: DARK ? "#3a3a52" : "#e3e4ee", seed: s + 1, sw: 2.2 }) +
      [0, 1, 2].map(i => squiggle(-100, -50 + i * 22, 110 - i * 20, s + i, MUTE, 2.4)).join("") +
      [[30, 20, 26], [56, 4, 42], [82, -14, 60]].map(([x, y, h], i) => sketch(rectPts(x, y - (h - 26) * (.8 + .2 * Math.sin(t * 2 + i)), 18, h * (.8 + .2 * Math.sin(t * 2 + i)), 3), { fill: i === 2 ? INDIGO : "#a99bff", seed: s + 6 + i, sw: 1.8 })).join(""),
    (t, s) => sketch(rectPts(-118, -78, 236, 140, 10), { fill: DARK ? "#1a1a24" : "#23232e", seed: s, stroke: INK }) + sketch([[-20, 62], [-30, 92], [30, 92], [20, 62]], { fill: DARK ? "#3a3a52" : "#e3e4ee", seed: s + 1, sw: 2.2 }) +
      [[-100, -54, 70, "#8b87ff"], [-80, -34, 110, "#7fd1a8"], [-80, -14, 80, "#ffd25e"], [-100, 6, 60, "#8b87ff"], [-80, 26, 130, "#f79fc5"]].map(([x, y, w, c]) => `<rect x="${x}" y="${y}" width="${w}" height="8" rx="4" fill="${c}"/>`).join("")]],
];
// The person at the laptop, with a face that changes: unsure while it piles up, happy once Standby is there.
function you(seed, mood, talk, typing, t) {
  const skin = "#f6d2b5", hair = DARK ? "#8b87ff" : "#5b4fd6", body = DARK ? "#6a62e8" : "#7b72f2";
  const happy = [[-15, -24], [-6, -15], [6, -15], [15, -24]], flat = [[-12, -19], [-4, -19], [4, -19], [12, -19]], unsure = [[-12, -17], [-4, -21], [4, -17], [12, -21]];
  const mouth = (mood >= 0 ? flat.map((p, i) => [lerp(p[0], happy[i][0], mood), lerp(p[1], happy[i][1], mood)]) : flat.map((p, i) => [lerp(p[0], unsure[i][0], -mood), lerp(p[1], unsure[i][1], -mood)]));
  let g = sketch([[-82, 92], [-80, 28], [-40, 4], [0, 0], [40, 4], [80, 28], [82, 92]], { fill: body, seed, closed: true, sw: 3 }) +
    sketch(circlePts(0, -52, 46), { fill: skin, seed: seed + 1 }) +
    sketch([[-46, -58], [-44, -88], [-14, -104], [22, -102], [46, -78], [44, -56], [20, -74], [-8, -70], [-30, -62]], { fill: hair, seed: seed + 2 }) +
    sketch(circlePts(-17, -48, 13), { seed: seed + 3, sw: 2.6 }) + sketch(circlePts(17, -48, 13), { seed: seed + 4, sw: 2.6 }) + line(-4, -48, 4, -48, { sw: 2.4, seed: seed + 5 });
  if (mood < 0) g += `<g opacity="${fo(-mood)}">` + line(-28, -63, -9, -69, { sw: 2.6, seed: seed + 20, twice: false }) + line(9, -69, 28, -63, { sw: 2.6, seed: seed + 21, twice: false }) +
    `<path d="M54 -78 q 7 10 0 15 q -7 -5 0 -15 Z" fill="#8fc7ff" stroke="${INK}" stroke-width="1.8"/></g>`;             // a worried brow, a bead of sweat
  g += talk > .15 ? `<ellipse cx="0" cy="-19" rx="${f(8 + talk * 2)}" ry="${f(2 + talk * 5)}" fill="#7a3b3b" stroke="${INK}" stroke-width="2.2"/>`
    : sketch(mouth, { closed: false, sw: 2.6, seed: seed + 6, twice: false });
  g += sketch(rectPts(-74, 24, 148, 78, 10), { fill: DARK ? "#3a3a52" : "#e3e4ee", seed: seed + 7 }) + sketch(circlePts(0, 62, 9), { seed: seed + 8, sw: 2.4 });
  if (typing > 0) {                                                 // typing: a little bubble of dots, and the keys tapping
    g += `<g opacity="${fo(typing)}">` + sketch(rectPts(70, -96, 84, 40, 18), { fill: PAPER, seed: seed + 30, sw: 2.2 }) +
      [0, 1, 2].map(k => `<circle cx="${92 + k * 20}" cy="-76" r="5" fill="${INK}" opacity="${fo(.25 + .75 * (Math.floor(t * 6) % 3 === k))}"/>`).join("") +
      [[-40, 16], [8, 12], [44, 18]].map(([x, y], k) => (Math.floor(t * 8 + k) % 2 ? line(x, y, x + 8, y - 10, { sw: 2.2, seed: seed + 40 + k, twice: false }) : "")).join("") + "</g>";
  }
  return g;
}
// Claude Code, drawn as what it is: a terminal prompt, "> claude" and a blinking cursor, in a small window with the warm
// glow behind it. Never Anthropic's own mark. `angle` (time) blinks the cursor; `glow` warms the halo.
function spark(R, angle, glow, seed = 500) {
  const fz = R * .55, cw = fz * .6, x0 = -R * 1.9, w = R * 3.8, h = R * 2.0, on = Math.sin(angle * 4) > -.2;
  return `<circle r="${f(R * 2.6)}" fill="url(#warmhalo)" opacity="${fo(.45 + .55 * glow)}"/>` +
    sketch(rectPts(-w / 2, -h / 2, w, h, R * .28), { fill: PAPER, stroke: INK, sw: Math.max(1.6, R * .07), seed }) +
    text(x0 + R * .32, fz * .36, "> claude", fz, { font: "Menlo, monospace", weight: 700, fill: INK, anchor: "start" }) +
    (on ? `<rect x="${f(x0 + R * .32 + cw * 8 + R * .06)}" y="${f(-fz * .42)}" width="${f(cw * .9)}" height="${f(fz * .9)}" rx="${f(R * .04)}" fill="${WARM}"/>` : "");
}
function caption(t, lines) {                                       // the words at the top, one sentence at a time
  let g = "";
  for (const [a, b, s] of lines) {
    const i = out(span(t, a, a + .8)), o = span(t, b - .5, b), k = i * (1 - o);
    if (k > 0) g += `<g opacity="${fo(k)}" transform="translate(0 ${f((1 - i) * 16 - o * 10)})">${text(800, 118, s, 54, { weight: 700 })}</g>`;
  }
  return g;
}
const cubic = (a, b, c, d, q) => { const u = 1 - q; return u * u * u * a + 3 * u * u * q * b + 3 * u * q * q * c + q * q * q * d };
