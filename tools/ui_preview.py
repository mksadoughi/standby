#!/usr/bin/env python3
"""Screenshots of the app window in every state, with made-up data, for UI work.

    python3 tools/ui_preview.py [out_dir] [--dark | --both] [--only=chat,transcript]
    python3 tools/ui_preview.py [out_dir] [--dark | --both] --companion        (mac/companion.html, every state)

Renders mac/ui.py's page in headless Chrome against a fake /api: no recorder,
no Claude, and nothing from ~/.standby is read. One PNG per state in out_dir
(default /tmp/standby-preview). The window is drawn at the app's default size.
One Chrome takes every screenshot of a run, light and dark.
"""
import base64
import fcntl
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
STATES = ["settings", "settings-help", "settings-call", "settings-recording", "usage", "usage-today", "usage-empty", "dictation", "dictation-shortcut", "dictation-recording", "settings-voice-prep", "settings-try-start", "settings-try-mid", "settings-try-failed", "settings-try-playing", "dictation-words", "settings-talkover", "settings-speech-dl", "settings-speech-8gb", "settings-speech-8gb-standard",
          "mx-past", "mx-row-menu", "mx-delete-ask", "mx-past-session", "mx-past-open", "mx-past-open-menu", "mx-past-empty", "mx-live", "mx-hear", "mx-hear-open", "mx-hear-off", "mx-hear-new", "mx-ask", "mx-pausing", "mx-ending", "mx-live-menu", "mx-paused", "mx-micno", "mx-soundno", "mx-soundask", "mx-spkname", "cc-live", "cc-livemenu", "cc-call", "cc-call-tasks", "cc-call-thinking", "cc-call-pausing", "cc-call-paused", "cc-call-waiting", "cc-call-working", "cc-callmenu", "cc-callcopy", "cc-callend", "cc-ended", "cc-long", "cc-past", "cc-past-menu"]

FAKE = r"""
const NOW = Date.now() / 1000, SCENE = location.hash.slice(1);
const live = { id: "live-1", title: "Trip search latency: Q4 plan", title_source: "ai", started: NOW - 1534, live: true,
  workspace: "~/code/trips", duration: 1530, lines: 42, claude: true, mic: true, joined: true, runs: [{ start: NOW - 1534, t: 0 }] };
if (SCENE === "talk-live") Object.assign(live, { kind: "talk", title: "Thinking out loud: Q4 plan" });
if (SCENE === "talk-wait" || SCENE === "join-failed" || SCENE === "chat") Object.assign(live, { where: { machine: "ssh:devbox", name: "devbox", folder: "/home/you/code/trips", session_title: "Fix the routing cache TTL" }, machine_name: "devbox" });
if (SCENE === "talk-wait") live.claude_wait = { why: "open_elsewhere", where: "VS Code" };
if (SCENE === "join-failed") live.claude_wait = { why: "failed", text: "devbox isn't answering.", fix: "Check that ssh devbox works in Terminal.", name: "devbox", machine: "ssh:devbox" };     // a talk going on: its own colour
const old = (id, title, ago, mins, extra) => Object.assign({ id, title, title_source: "ai", started: NOW - ago, live: false,
  duration: mins * 60, lines: 80, claude: true, mic: true, joined: true, ended: NOW - ago + mins * 60, runs: [{ start: NOW - ago, t: 0 }] }, extra || {});
const others = [old("t1", "Why dictation doesn't paste", 2 * 3600, 4, { kind: "talk", joined: true }),
  old("t2", "Thinking through the Q4 plan", 2 * 86400 + 5000, 9, { kind: "talk", joined: true }), old("s2", "Weekly standup", 86400 + 3600, 14), old("s3", "Onboarding flow design review", 3 * 86400, 47),
  old("s4", "1:1 with Ines", 4 * 86400, 30), old("s5", "Incident retro: map tiles timeout", 6 * 86400, 52)];
others.sort((a, b) => b.started - a.started);          // newest first, as the server lists them
const transcript = [
  { t: 4, src: "s2", spk: 1, text: "Okay, let's get started. The main thing today is the p99 on trip search." },
  { t: 11, src: "s2", spk: 1, text: "We're at about 480 milliseconds and the budget for Q4 is 300." },
  { t: 19, src: "s2", spk: 2, text: "Most of that is the routing call. It goes to Orion on every page load." },
  { t: 27, src: "s1", spk: 3, text: "Can we cache the route for the session?" },
  { t: 33, src: "s2", spk: 2, text: "For logged-in users, yes. Guests are trickier because of regional fares." },
  { t: 45, src: "s2", spk: 1, text: "Let's take that offline. Theo, can you size the cache work?" },
  { t: 52, src: "s2", spk: 2, text: "Sure, I'll have a number by Thursday." },
  { t: 61, src: "s3", spk: 3, text: "One more thing: the load test last week used the old timetable stub." },
  { t: 70, src: "s2", spk: 1, text: "Good catch. We should rerun it before we commit to anything." }];
const chat = [
  { role: "me", text: "What did Nadia mean by the budget?" },
  { role: "ai", steps: ["Reading the transcript", "Reading ~/code/trips/docs/slo.md"],
    text: "She means the **p99 latency target for trip search in Q4: 300 ms**. Today it is about 480 ms, so the team needs to cut roughly 180 ms.\n\nTheo said most of it is the routing call to Orion on every page load." },
  { role: "me", text: "Draft a short follow-up for the team" },
  { role: "ai", steps: ["Reading the transcript"],
    text: "**Trip search latency: follow-ups**\n\n- **Theo**: size the route cache for logged-in users, by Thursday.\n- **Jules**: rerun last week's load test with the current timetable stub.\n- **Nadia**: decide how guests are handled (regional fares).\n\nTarget: p99 under `300 ms` in Q4, from about 480 ms today." }];
const pending = { question: "Find how we handled the Orion cache last quarter", started: NOW - 9, progress: [
  { kind: "tool", text: "Searching ~/code/trips for \"orion cache\"" },
  { kind: "tool", text: "Reading docs/adr/014-route-cache.md" },
  { kind: "delta", text: "Last quarter the team **added a 60-second cache** in front of Orion for the map page only (ADR 014). " },
  { kind: "delta", text: "It was not used on trip search because" }] };
const speakers = { alias: {}, names: { "1": "Nadia" }, known: { "2": "Theo" } };
const sessions = { "live-1": { info: live, chat: ["listening", "p-starters", "suggest-listen"].includes(SCENE) ? [] : chat, transcript, speakers,
  pending: SCENE === "working" ? pending : null } };
// What it records: the mic by its device and the call's sound by its app; paused,
// the recorder's words, and the pause on record where it happened.
// the call's sound refused, the switch on for an older copy (known or not).
const REFUSED = { id: "s2", kind: "meeting", name: "Zoom", bundle: "us.zoom.xos", level: 0, ok: false, state: "error",
  cause: "permission", reason: "allow Standby to hear it",
  detail: "Allow Standby in System Settings, Privacy & Security, Screen & System Audio Recording, to hear Zoom's side of the call." };
const SOURCES = ["transcript-permission", "transcript-stale", "stale-confirm"].includes(SCENE)
  ? [{ id: "s3", kind: "mic", name: "AirPods Pro", transport: "bluetooth", level: 0.05, ok: true, state: "live" },
     Object.assign({}, REFUSED, SCENE === "transcript-permission" ? {} : { stale: true })]
  : SCENE === "transcript-paused"
  ? [{ kind: "mic", name: "AirPods Pro", level: 0.05, ok: true, state: "live" },
     { kind: "meeting", name: "Zoom", level: 0, ok: false, state: "paused", reason: "the screen is asleep", detail: "The screen is asleep or locked, so macOS offers nothing to capture. It resumes by itself." }]
  : [{ id: "s3", kind: "mic", name: "AirPods Pro", transport: "bluetooth", level: 0.05, ok: true, state: "live" },
     { id: "s2", kind: "meeting", name: "Zoom", bundle: "us.zoom.xos", level: 0.03, ok: true, state: "recording" }];
live.sources = SOURCES.map(({ level, ...x }) => x);
live.sources_seen = [{ id: "s1", kind: "mic", name: "MacBook Pro Microphone", transport: "builtin" },
  { id: "s2", kind: "meeting", name: "Zoom", bundle: "us.zoom.xos" }, { id: "s3", kind: "mic", name: "AirPods Pro", transport: "bluetooth" }];
// The recorder's question, as devices.ASKS words it.
const ASK = { id: "mic:abc", about: { kind: "mic", name: "AirPods Pro" },
  text: "AirPods Pro connected: record from their mic? Their sound drops to call quality while it records.",
  actions: [{ label: "Switch", value: "use" }, { label: "Keep this mic", value: "keep" }] };
if (SCENE === "capture-ask") live.ask = ASK;
if (SCENE === "transcript-voice") live.events = [{ kind: "voice", from: 20, to: 62 }, { kind: "mic_muted", from: 40, to: 40 + 180 }];
if (SCENE === "transcript-paused") live.events = [{ kind: "meeting", from: 15, to: 15 + 4 * 60, cause: "screen_asleep", reason: "the screen was asleep" }];
for (const s of others) sessions[s.id] = { info: s, chat: [], transcript: transcript.slice(0, 4), speakers, pending: null };
sessions.t1.transcript = []; sessions.t1.chat = [
  { role: "me", voice: true, text: "Dictation hears me but nothing gets pasted. What's going on?" },
  { role: "ai", steps: ["Reading ~/.standby/dictation.log"], text: "Standby Dictation doesn't have **Accessibility** permission, so it can transcribe but can't paste.\n\nOpen System Settings > Privacy & Security > Accessibility and turn on Standby Dictation. If it's already on, turn it off and on again." },
  { role: "me", voice: true, text: "Done. Does it work now?" },
  { role: "ai", text: "Yes. The last dictation pasted into Notes a few seconds ago." }];
const STATE = {
  welcome: { sessions: [], live: null, starting: null },
  home: { sessions: others, live: null, starting: null },
  starting: { sessions: others, live: null, starting: { state: "starting" } },
  ended: { sessions: others, live: null, starting: null } }[SCENE] || { sessions: [live, ...others], live: "live-1", starting: null };
STATE.agent = { mode: "local", label: "this Mac" };
// Claude can't be reached, in its own words.
if (SCENE.startsWith("claude-down")) STATE.claude_error = { kind: "signin", text: "Claude can't sign in.", fix: "Run claude, then /login, and try again.",
  detail: "claude: Not logged in · Please run /login", at: NOW - 30 };
// Usage: a made-up day, the meeting agent on Opus, the voice on Sonnet.
const DAY0 = new Date(); DAY0.setHours(0, 0, 0, 0);
const HOURS = [0,0,0,0,0,0,0,0,0,6,14,9,3,0,11,18,12,4,0,0,2,0,0,0];
// Every range ends now: today is the hours so far, 3 hours is by ten minutes, the last one partial.
const NOW_S = Date.now() / 1000, TEN = Math.floor(NOW_S / 600) * 600;
const TENS = [0,1,2,1,3,2,4,3,2,3,4,5,3,2,4,3,2,1];
const USAGE = SCENE === "usage-empty" ? { range: "today", bucket: "hour", totals: { calls: 0 }, series: [], models: [], kinds: [] } : {
  range: "today", bucket: "hour", from: DAY0 / 1000, to: NOW_S, partial: true,
  totals: { calls: 79, input: 412000, output: 38500, cache_read: 2310000, cache_write: 96000 },
  series: HOURS.slice(0, new Date().getHours() + 1).map((c, i) => ({ t: DAY0 / 1000 + i * 3600, calls: c })),
  models: [{ model: "claude-haiku-4-5", calls: 66, input: 281000, output: 25800, cache_read: 1710000, cache_write: 66000 },
           { model: "claude-sonnet-4-5", calls: 9, input: 61000, output: 8700, cache_read: 260000, cache_write: 8000 },
           { model: "claude-opus-4-5", calls: 4, input: 70000, output: 4000, cache_read: 340000, cache_write: 22000 }],
  kinds: [{ kind: "voice", calls: 52, input: 220000, output: 18100, cache_read: 1400000, cache_write: 50000, models: ["claude-haiku-4-5"] },
          { kind: "decisions", calls: 9, input: 61000, output: 8700, cache_read: 260000, cache_write: 8000, models: ["claude-sonnet-4-5"] },
          { kind: "title", calls: 7, input: 21000, output: 400, cache_read: 120000, cache_write: 6000, models: ["claude-haiku-4-5"] },
          { kind: "cc-gist", calls: 5, input: 30000, output: 5300, cache_read: 150000, cache_write: 8000, models: ["claude-haiku-4-5"] },
          { kind: "words", calls: 4, input: 70000, output: 4000, cache_read: 340000, cache_write: 22000, models: ["claude-opus-4-5"] },
          { kind: "call-summary", calls: 2, input: 10000, output: 2000, cache_read: 40000, cache_write: 2000, models: ["claude-haiku-4-5"] }] };
// Suggested questions: from what Claude hears, or follow-ups to its last answer.
if (SCENE === "suggest-listen") sessions["live-1"].suggest = { session: "live-1", from: "listen", at: NOW - 5,
  items: ["Who owns the cache work?", "What's the risk for guest riders?", "When is the load test rerun?"] };
if (SCENE === "suggest") sessions["live-1"].suggest = { session: "live-1", from: "answer", at: NOW - 5,
  items: ["Send it to Nadia and Theo", "Add the Q4 deadline", "Make it shorter"] };
// words the meeting's own Claude taught dictation; the transcript shown fixed.
if (SCENE === "learned" || SCENE === "learned-transcript") {
  sessions["live-1"].learned = [["or ion", "Orion"], ["p ninety nine", "p99"]];
  Object.assign(sessions["live-1"].transcript[2], { heard: "Most of that is the routing call. It goes to or ion on every page load." });
  Object.assign(sessions["live-1"].transcript[0], { heard: "Okay, let's get started. The main thing today is the p ninety nine on trip search." }) }
if (SCENE === "ended") sessions.s2.suggest = { session: "s2", from: "end", at: NOW - 600,
  items: ["Summarize", "Action items", "Draft a follow-up"] };
if (SCENE === "suggest-talk") sessions.t1.suggest = { session: "t1", from: "answer", at: NOW - 5,
  items: ["How do I check Input Monitoring?", "Why did it work before?"] };
// Made-up screens, for the pictures the scenes show.
const SHOT = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400">' +
  '<rect width="640" height="400" fill="#2b2f36"/><rect width="170" height="400" fill="#23262c"/><rect x="200" y="40" width="300" height="24" rx="5" fill="#7aa7ff"/>' +
  '<rect x="200" y="96" width="390" height="14" rx="5" fill="#8b949e"/><rect x="200" y="126" width="340" height="14" rx="5" fill="#8b949e"/>' +
  '<rect x="200" y="156" width="370" height="14" rx="5" fill="#ff7b72"/><rect x="200" y="186" width="240" height="14" rx="5" fill="#8b949e"/></svg>');
// Several: a slide and a call beside the editor, three waiting, so the button is full.
const SLIDE = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><rect width="640" height="400" fill="#fafafa"/>' +
  '<rect x="48" y="44" width="300" height="28" rx="5" fill="#1d1d1f"/><rect x="80" y="300" width="68" height="60" fill="#3f7cf3"/><rect x="176" y="240" width="68" height="120" fill="#3f7cf3"/>' +
  '<rect x="272" y="192" width="68" height="168" fill="#3f7cf3"/><rect x="368" y="264" width="68" height="96" fill="#93bdff"/><rect x="464" y="156" width="68" height="204" fill="#3f7cf3"/></svg>');
const CALL = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><rect width="640" height="400" fill="#2b2b2e"/>' +
  '<rect x="20" y="20" width="292" height="172" rx="10" fill="#4a5468"/><rect x="328" y="20" width="292" height="172" rx="10" fill="#5b4a46"/>' +
  '<rect x="20" y="208" width="292" height="172" rx="10" fill="#465b4e"/><rect x="328" y="208" width="292" height="172" rx="10" fill="#56506a"/></svg>');
const SNAPS3 = [{ id: "s1", at: NOW - 250, age: 250, thumb: SLIDE }, { id: "s2", at: NOW - 130, age: 130, thumb: SHOT }, { id: "s3", at: NOW - 20, age: 20, thumb: CALL }];
// files waiting for the next question, a question that took two, and a drop under way
const FILES4 = [{ id: "f1", name: "Q4 routing plan.pdf", kind: "pdf", words: "PDF", size: 2202009, at: NOW - 60 },
  { id: "f2", name: "Trip search SLO review.docx", kind: "document", words: "Word document", size: 48213, at: NOW - 40 },
  { id: "f3", name: "Pasted text.txt", kind: "text", words: "text", size: 14200, at: NOW - 20 }];
STATE.files = { files: ["files", "files-drop"].includes(SCENE) ? FILES4 : [], max: 5, max_bytes: 25 * 1048576, full: false };
if (SCENE === "files-sent") sessions["live-1"].chat = [chat[0], chat[1],
  { role: "me", text: "Does Nadia's plan match the SLO doc?", files: FILES4.slice(0, 2) },
  { role: "ai", steps: ["Reading Q4 routing plan.pdf", "Reading Trip search SLO review.docx"], text: "Mostly. The plan's **300 ms** p99 target is the SLO's, but the plan counts guests separately, and the SLO doesn't." }];
const CHECK = { where: "this Mac", results: [
  { group: "mac", ok: true, label: "Speech model", detail: "parakeet-tdt-0.6b-v3" },
  { group: "mac", ok: true, label: "Microphone", detail: "MacBook Pro Microphone" },
  { group: "mac", ok: true, label: "Speaker labels", detail: "TitaNet small" },
  { group: "agent", ok: true, label: "Claude Code CLI", detail: "2.1.4" },
  { group: "agent", ok: false, label: "Meeting skill", detail: "not linked: run ./install.sh" }] };
const SETTINGS = { settings: { model: "opus", push_lines: 10,
  speakers: true, remember_voices: true, voices: ["Nadia", "Theo"], my_voice: 260, loopback: false, capture: "auto", mic_default: "AirPods Pro",
  mic_device: "", talk_over: true,
  talk_over_note: SCENE === "settings-talkover" ? "Needs the Mac's default microphone (Settings > Recording)" : "",
  // the speech model, and in settings-speech-dl, Standard on its way. An 8 GB Mac,
  // Small by its memory (settings-speech-8gb), or Standard picked there (-standard).
  speech: SCENE === "settings-speech-8gb" ? { size: "small", english_only: true, default: "small", picked: false, memory: 8,
      model: "Small, English", bytes: 458690629, ready: true, download: null }
    : SCENE === "settings-speech-8gb-standard" ? { size: "standard", english_only: true, default: "small", picked: true, memory: 8,
      model: "Standard, English", bytes: 2471596080, ready: true, download: null }
    : { size: "standard", english_only: true, default: "standard", picked: false, memory: 48, model: "Standard, English",
      bytes: 2471596080, ready: SCENE !== "settings-speech-dl",
      download: SCENE === "settings-speech-dl" ? { state: "downloading", done: 1040000000, total: 2471596080 } : null } },
  mics: ["MacBook Pro Microphone", "AirPods Pro"] };
// As on a Mac with no Premium voice yet: automatic is the system voice, and the hint shows.
// Standby's own voice (Jean), its credit, and, in settings-voice-prep, its first download.
const VOICES_FIX = { chosen: null, better: false, current: { id: "standby:jean", name: "Jean", quality: "standby" },
  standby: [{ id: "standby:jean", name: "Jean", quality: "standby" }], credit: "Voice: Pocket TTS by Kyutai (CC BY 4.0)",
  preparing: SCENE === "settings-voice-prep" ? "Getting the voice ready… 42%" : "", preparing_pct: SCENE === "settings-voice-prep" ? 42 : null,
  voices: [["Samantha", "US"], ["Daniel", "UK"], ["Karen", "Australia"], ["Moira", "Ireland"]].map(([n, r]) =>
    ({ id: "com.apple.voice.compact." + n, name: n, quality: "standard", lang: "en", region: r })) };
// Try in Jean, its first time: before the first percentage, mid-download, playing; failed is set on the page.
const TRY_FIX = { "settings-try-start": { state: "downloading", pct: null, left: null },
  "settings-try-mid": { state: "downloading", pct: 42, left: 28 }, "settings-try-playing": { state: "playing", pct: null, left: null } }[SCENE];
if (TRY_FIX) VOICES_FIX.trying = Object.assign({ n: 7, voice: "Jean", error: null, downloaded: true }, TRY_FIX);
// what the start window is given: the machines, the recents (20 of 23), a folder's sessions, the check.
const START_SESS = [{ id: "c1", title: "Fix the routing cache TTL", at: NOW - 720, tokens: 84000, open: null },
  { id: "c2", title: "Spike: guest fares", at: NOW - 3 * 3600, tokens: 40000, open: null },
  { id: "c3", title: "Add retries to the fares client", at: NOW - 86400 * 4, tokens: 310000, open: { where: "VS Code", busy: false } },
  { id: "c4", title: "Map refactor", at: NOW - 86400 * 11, tokens: 22000, open: null }];
const R_ = (m, f, s, ago) => ({ machine: m === "This Mac" ? "local" : "ssh:" + m, machine_name: m === "This Mac" ? "this Mac" : m, folder: (m === "This Mac" ? "/Users/you/" : "/home/you/") + f, folder_name: f.split("/").pop(),
  session: s ? { id: "r" + ago, title: s } : null, at: NOW - ago });
const RECENTS = [R_("devbox", "code/trips", "Fix the routing cache TTL", 720), R_("This Mac", "notes", null, 3600), R_("devbox", "code/trips", "Spike: guest fares", 3 * 3600),
  R_("build-01", "code/api", "Add retries to the fares client", 86400 + 3600), R_("This Mac", "code/routing", "Draft the Q4 latency doc", 86400 + 7200), R_("devbox", "code/platform", null, 86400 + 9000),
  R_("This Mac", "notes", "Weekly planning", 86400 + 20000), R_("devbox", "code/trips", "Orion timeout review", 3 * 86400), R_("build-01", "infra", "Rotate the deploy keys", 3 * 86400 + 4000),
  R_("This Mac", "website", "Landing page copy", 4 * 86400), R_("devbox", "code/search", "Index rebuild plan", 4 * 86400 + 3000), R_("This Mac", "notes", null, 5 * 86400),
  R_("devbox", "code/routing", "Fare zones edge cases", 5 * 86400 + 600), R_("build-01", "code/api", "Rate limiter design", 8 * 86400), R_("This Mac", "talks", "Conference outline", 9 * 86400),
  R_("devbox", "code/trips", "Map refactor", 10 * 86400), R_("This Mac", "notes", "Interview notes", 11 * 86400), R_("devbox", "code/mobile", "Push token bug", 13 * 86400),
  R_("build-01", "infra", "Disk alerts", 14 * 86400), R_("This Mac", "code/routing", "Time zone rounding", 17 * 86400)];
const START = { kind: "meeting", total: SCENE === "start-first" ? 0 : 23, recents: SCENE === "start-first" ? [] : RECENTS, first: { machine: "local", folder: "/Users/you/code/trips" },
  machines: [{ key: "local", name: "this Mac", status: "ok" }, { key: "ssh:devbox", name: "devbox", status: SCENE.includes("down") ? "bad" : "ok" }, { key: "ssh:build-01", name: "build-01", status: "bad" }] };
const STEP6 = ["answers", "helper", "claude", "signin", "folder", "session"];
const CHECK55 = () => SCENE === "start-update" ? { done: true, ready: false, progress: 17, steps: [{ key: "answers", state: "ok" }, { key: "helper", state: "fail", update: true,
    text: "Standby's helper on devbox is out of date.", fix: "Update links Standby's own; your checkout isn't touched.", replaces: "/home/you/old-standby/plugin/bin/standby-agent" }].concat(STEP6.slice(2).map(key => ({ key, state: "todo" }))) }
  : SCENE.includes("down") ? { done: true, ready: false, steps: [{ key: "answers", state: "fail", text: "devbox isn't answering.", fix: "Check that ssh devbox works in Terminal.",
    detail: "ssh: connect to host devbox port 22: Operation timed out" }].concat(STEP6.slice(1).map(key => ({ key, state: "todo" }))) }
  : SCENE === "start-vscode" ? { done: false, ready: false, steps: STEP6.slice(0, 5).map(key => ({ key, state: "ok" })).concat([{ key: "session", state: "wait",
    text: "Open in VS Code: close it there, and Standby connects", fix: "Or use a copy.", open: { where: "VS Code", busy: false } }]) }
  : SCENE === "start" ? { done: false, ready: false, steps: STEP6.map(key => ({ key, state: key === "signin" ? "run" : key === "folder" || key === "session" ? "todo" : "ok" })) }
  : { done: true, ready: true, steps: STEP6.map(key => ({ key, state: "ok" })) };
// The shortcut: the one in use, the presets, and Hold to talk.
const DICTATION = { on: true, running: true, old: false, updating: false, problem: null, hold: false,
  words: { count: 12, learned: 5, suggested: 2 },
  shortcut: SCENE === "dictation-shortcut" ? { id: "custom", label: "⌥ Space", kind: "combo" } : { id: "mickey", label: "Press F5", kind: "key" },
  shortcuts: [{ id: "mickey", label: "Press F5", kind: "key" }, { id: "control2", label: "Control twice", kind: "double" },
    { id: "fn2", label: "Fn (Globe) twice", kind: "double" }, { id: "rcommand2", label: "Right Command twice", kind: "double" },
    { id: "roption2", label: "Right Option twice", kind: "double" }],
  missing: SCENE === "dictation" ? [{ key: "mic", name: "Microphone", why: "hears you", action: "ask", button: "Allow" },
    { key: "post", name: "Accessibility", why: "pastes the text", action: "open", button: "Open Settings…" }] : [] };
const VOICE = SCENE === "voice-holding" ? { state: "listening", level: 0, you: "", claude: "", holding: true, muted: false }   // "hold on"
  : SCENE === "voice-steps" ? { state: "thinking", level: 0, you: "Who owns the cache work, and when does it land?", claude: "",
    step: "", action: "Searching your files", actions: 3, muted: false }      // Claude at work: its steps counted
  : SCENE === "cc-call-tasks" ? { state: "listening", level: 0, you: "", claude: "", muted: false, tasks: [
      { id: "t1", state: "running", label: "Third pressure test: a 3000-word essay", since: NOW - 40, last: NOW - 5 },
      { id: "t2", state: "queued", label: "Note the cut-off words in the observations", since: NOW - 20, last: NOW - 20 }] }
  : SCENE === "cc-call-thinking" ? { state: "thinking", level: 0, you: "And how long does the fares cache keep a route?", claude: "", muted: false, tasks: [] }
  : SCENE === "cc-call-paused" ? { state: "muted", level: 0, you: "", claude: "", muted: true }
  : SCENE === "cc-call-waiting" ? { state: "muted", level: 0, you: "", claude: "", muted: true, waiting: true }
  : SCENE === "voice-preparing" ? { state: "listening", level: 0.02, you: "", claude: "", muted: false, preparing: "Getting the voice ready… 42%", preparing_pct: 42 }
  : { state: "speaking", level: 0, you: "What's the one thing we have to decide this week?",
  claude: "Whether guests get the route cache too. Nadia wants a call on regional fares first.", muted: false };
// Standby's view's server answers, for the previews only (the app never shows made-up data): the made-up
// Orion team's folders, Claude Code sessions and what they recorded; window.fetch below answers from it.
const MXFAKE = (() => {
  const T = Date.now() / 1000, at = m => T - m * 60, day = 1440;
  // two folders, their Claude Code sessions (agents made by /standby), and what they recorded
  const ws = [{id: "w-api", name: "orion-api", machine: "local", folder: "/Users/you/code/orion-api", agents: ["a-planner", "a-d1", "a-d2"]},
    {id: "w-web", name: "orion-web", machine: "local", folder: "/Users/you/code/orion-web", agents: ["a-lead", "a-fe"]}];
  const A = (id, w, name) => ({id, workspace: w, name, title: name, cc: true, color: 0, live: {}});
  const ag = [A("a-planner", "w-api", "Guest TTL fix"), A("a-d1", "w-api", "Fare page numbers"), A("a-d2", "w-api", "Routing cache review"),
    A("a-lead", "w-web", "Trip search redesign"), A("a-fe", "w-web", "Release notes")];
  let n = 0; const C = (kind, from, ago, mins, x) => Object.assign({id: "c-" + (++n), kind, from, to: "you", at: at(ago), session: "sample-" + kind + "-" + n,
    live: false, duration_s: mins * 60, started_at: at(ago), ended_at: at(ago) + mins * 60, memory: "saved", points: []}, x);
  const items = [
    C("call", "a-planner", 32, 4, {gist: "Ran the routing cache tests; all 112 pass", points: ["All 112 routing cache tests pass", "Nothing depends on the guest TTL"]}),
    C("meeting", "a-d2", 95, 48, {title: "Q4 latency planning", names: ["Nadia", "Toby", "Jules"], points: ["p99 target for Q4: 300 ms on trip search", "Regional fares aren't cached yet (Jules)"]}),
    C("call", "a-d1", day + 300, 3, {gist: "Fare page numbers for the cache key", points: ["Ines brings the fare page numbers by Thursday"]}),
    C("meeting", "a-planner", day + 420, 18, {title: "Guest TTL review", names: ["Nadia", "Theo"], points: ["Guest TTL ships today (p99 280 ms)", "Fare region in the cache key: not this week, measure first"]}),
    C("call", "a-fe", day + 600, 1, {}),
    C("meeting", "a-lead", 3 * day, 22, {title: "Interview loop debrief", names: ["Toby", "Ines"], points: ["Two strong candidates for the frontend role"]}),
    C("call", "a-planner", 3 * day + 200, 9, {gist: "Guest TTL: ten minutes, then measure", points: ["Ten minutes for guests, then measure"]}),
    C("meeting", "a-planner", 3 * day + 330, 12, {title: "", names: ["Theo"], points: ["The load test runs nightly from now on"]}),
  ];
  const now = () => Date.now() / 1000;
  return {
    answer(p, b) {
      if (p === "/api/msg/workspaces" && !b) return {workspaces: ws, agents: ag};
      if (p === "/api/msg/past") return {items: items.slice().sort((x, y) => (y.at || 0) - (x.at || 0))};
      if (p.startsWith("/api/msg/mac/")) { const c = items.find(m => m.session === b.session); if (!c) return {error: "That meeting has ended"};
        if (p.endsWith("pause")) c.paused = true, c.live = false; else if (p.endsWith("resume")) c.paused = false, c.live = true;
        else { this.stopMac(b.session); c.memory = "saved" }
        return {ok: true, session: b.session} }
      if (p === "/api/msg/call/end") { const c = items.find(m => m.session === b.session); if (c) Object.assign(c, {live: false, duration_s: Math.max(1, Math.round(now() - c.at)), memory: "saved"});
        for (const a of ag) if (a.live && a.live.call === b.session) a.live.call = null; return {ok: true} }
      if (p === "/api/msg/delete") { const c = items.find(m => m.session === b.session); if (!c) return {error: "No such meeting or call"};
        if (c.live || c.paused) return {error: "That one is still live: end it first"}; items.splice(items.indexOf(c), 1); return {ok: true, session: b.session} }
      if (p === "/api/speaker") { this._names = Object.assign(this._names || {}, {[b.spk]: b.name}); return {ok: true} }
      if (p === "/api/msg/memory") { const c = items.find(m => m.session === b.session); if (c) c.memory = b.keep ? "kept" : "forgotten"; return {ok: true} }
      if (p === "/api/claude") { const d = this.session(b.id); d.info.claude = b.on; return {ok: true} }
      return {ok: true};
    },
    // what the recorder says of a live session's sources: a refused mic or meeting sound when a scene asks
    live(sid) { const c = items.find(m => m.session === sid) || {};
      return {partial: c.live ? "Ines: if the fares endpoint gets the same cache" : "",
        sources: this.micNo ? [{kind: "mic", ok: false, cause: "permission", reason: "not allowed", detail: "Allow Standby in System Settings > Privacy & Security > Microphone. It records by itself once allowed."}]
          : this.soundNo ? [{kind: "meeting", ok: false, cause: "permission", reason: "not allowed", detail: "Allow Standby in System Settings > Privacy & Security > Screen & System Audio Recording."}] : [],
        meeting_audio: this.soundAsk ? "ask" : undefined,
        hearing: c.live && this.hearing ? this.hearing : undefined} },
    // something live in a session, as Claude Code would start it (the previews' stand-in for /standby listen and call)
    startMeeting(agent, title) { const c = {id: "c-live-m", kind: "meeting", from: agent, to: "you", at: now(), session: "sample-meet", title: title || "Meeting", live: true, duration_s: 0, points: [], names: ["Nadia", "Theo", "Ines"]};
      items.push(c); ag.find(a => a.id === agent).live.meeting = c.session; return c },
    startCall(agent) { const c = {id: "c-live-c", kind: "call", from: agent, to: "you", at: now(), session: "sample-call-live", live: true, duration_s: 0, points: []};
      items.push(c); ag.find(a => a.id === agent).live.call = c.session; return c },
    session(sid) {
      const c = items.find(m => m.session === sid) || {}, t0 = c.at || now() - 600;
      if (!this._s) this._s = {}; const S0 = this._s[sid] || (this._s[sid] = {chat: [], n: 0, claude: true});
      const say = [["2", "So the p99 target for Q4 is 300 milliseconds on trip search."], ["3", "Most of it is the routing call to Orion on every page load."],
        ["me", "The cache fix went in yesterday: 280 in the load test."], ["4", "Then guests are the open part. Regional fares aren't cached yet."], ["5", "If the fares endpoint gets the same cache, Thursday."]];
      if (c.live) S0.n = Math.min(say.length, S0.n + (Math.random() < .4 ? 1 : 0)); else if (!S0.n) S0.n = say.length;
      const tr = c.kind === "call" ? [] : say.slice(0, Math.max(2, S0.n)).map(([k, text], i) => k === "me" ? {t: 20 + i * 40, speaker: "Me", text} : {t: 20 + i * 40, spk: +k, text});
      return {info: {id: sid, kind: c.kind === "call" ? "talk" : "meeting", title: c.title || (c.kind === "call" ? "Talk" : "Meeting"), title_source: c.title ? "user" : "default", live: !!c.live, started: t0, duration: c.duration_s || Math.round(now() - t0),
        runs: now() - t0 > 140 ? [{start: t0, t: 0, end: t0 + 70}, {start: t0 + 130, t: 70}] : [{start: t0, t: 0}], claude: S0.claude !== false, events: [{kind: "mic_muted", from: 95, to: 125}].concat(S0.offAt ? [{kind: "source_off", name: "Zoom", from: S0.offAt, to: null}] : []),
        ask: c.live && this.ask ? this.ask : null},
        chat: S0.chat, transcript: tr, speakers: {alias: {}, names: Object.assign({"2": "Nadia", "3": "Theo", "4": "Jules"}, this._names || {}), known: {"5": "Ines"}}, pending: null, suggest: null};
    },
    stopMac(sid) { for (const m of items) if (m.session === sid) { m.live = false; m.duration_s = Math.round(now() - m.at); m.points = ["p99 target for Q4: 300 ms on trip search", "Regional fares aren't cached yet", "Same cache on the fares endpoint, by Thursday"] }
      for (const a of ag) if (a.live && a.live.meeting === sid) a.live.meeting = null },
  };
})();
window.fetch = async (p, o) => { const id = p.split("?")[0].split("/").pop(), body = o && o.body ? JSON.parse(o.body) : undefined;
  const mx = p.startsWith("/api/msg/") || p === "/api/speaker" || p === "/api/claude" ? MXFAKE.answer(p, body)
    : p === "/api/capture/answer" ? (MXFAKE.ask = null, {ok: true})
    : p === "/api/live/source" ? ((MXFAKE.hearing || []).forEach(x => { if (x.id === body.id) x.on = body.on }), {ok: true})
    : p.startsWith("/api/session/sample-") ? MXFAKE.session(id) : p.startsWith("/api/live/sample-") ? MXFAKE.live(id) : null;
  if (mx) return { json: async () => JSON.parse(JSON.stringify(mx)) };
  const r = p === "/api/state" ? STATE : p === "/api/check" ? CHECK : p === "/api/settings" ? SETTINGS
  : p === "/api/speech" ? SETTINGS.settings.speech
  : p === "/api/projects" ? { where: "this Mac", default: "/Users/you/code/trips", projects: [
      { path: "/Users/you/code/trips", name: "trips", at: NOW - 2 * 3600, claude_md: true, exists: true },
      { path: "/Users/you/code/routing", name: "routing", at: NOW - 26 * 3600, claude_md: true, exists: true },
      { path: "/Users/you/code/platform/api", name: "api", at: NOW - 3 * 86400, claude_md: false, exists: true },
      { path: "/Users/you/code/fares/api", name: "api", at: NOW - 5 * 86400, claude_md: true, exists: true },
      { path: "/Users/you/notes", name: "notes", at: NOW - 9 * 86400, claude_md: false, exists: true }] }
  : p === "/api/projects/check" ? { exists: true, claude_md: true, path: "/Users/you/code/trips", where: "this Mac" }
  : p.startsWith("/api/start?") ? START : p.startsWith("/api/start/recents") ? { recents: RECENTS, total: RECENTS.length }
  : p.startsWith("/api/start/folders") ? { folders: [{ path: "/Users/you/code/trips", name: "trips", at: NOW - 7200 }, { path: "/Users/you/code/routing", name: "routing", at: NOW - 86400 },
      { path: "/Users/you/code/fares/api", name: "api", at: NOW - 86400 * 4 }, { path: "/Users/you/notes", name: "notes", at: NOW - 86400 * 9 }] }
  : p.startsWith("/api/start/sessions") ? { sessions: START_SESS } : p === "/api/start/check" ? { id: "k1" } : p.startsWith("/api/start/check/") ? CHECK55()
  : p.startsWith("/api/usage") ? (p.includes("range=3h") && SCENE !== "usage-empty" ? Object.assign({}, USAGE, { range: "3h", bucket: "10 minutes",
      from: TEN - 17 * 600, series: TENS.map((c, i) => ({ t: TEN - (17 - i) * 600, calls: c })) }) : USAGE)
  : p === "/api/prompt" ? { prompt: "", default: "", custom: false } : p === "/api/dictation-app" ? DICTATION
  : p === "/api/voices" ? VOICES_FIX
  : p.startsWith("/api/pending/") ? { pending: SCENE === "working" ? pending : null }
  : p.startsWith("/api/live/") ? { at: NOW, age: 0.3, level: 0.05, speaking: true, partial: "so if we move the cache in front of", sources: SOURCES }
  : p.startsWith("/api/voice") ? (p === "/api/voice" ? { ok: true, state: VOICE } : VOICE)
  : p.startsWith("/api/session/") ? sessions[id] : { ok: true };
  return { json: async () => JSON.parse(JSON.stringify(r)) } };
"""

SCENES = r"""
(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  await wait(50);
  if (SCENE === "starting") { cur = "__starting__"; await refresh() }
  // Standby's view, on its own sample data: past meetings and calls by folder and Claude Code session
  // opened from Claude Code (mxFocusSession): the "Guest TTL fix" session, a call or a meeting live in it
  if (SCENE.startsWith("cc-")) { await wait(300); S.live = null;
    if (SCENE === "cc-long") MXFAKE.answer("/api/msg/workspaces").agents.find(x => x.id === "a-planner").name = "Standby Planner tester handoff for the guest TTL";
    if (SCENE.startsWith("cc-call")) { const c = MXFAKE.startCall("a-planner"); await mxFocusSession({session: c.session, kind: "call", live: true}); await wait(5200)
      // the call so far: a question, work handed to Claude Code, and what came back
      const S0 = MXFAKE.session(c.session) && MXFAKE._s[c.session], t = Date.now() / 1000 - 300;
      S0.chat.push({ role: "me", text: "Run the routing cache tests and tell me if the guest TTL breaks anything.", at: t, voice: true },
        { role: "ai", text: "Sure, I'll run them now.", handoff: "Run the routing cache tests and tell me if the guest TTL breaks anything.", at: t + 2, voice: true },
        { role: "me", text: "And how long does the fares cache keep a route?", at: t + 20, voice: true },
        { role: "ai", text: "Fifteen minutes for members, two for guests, since this morning's fix.", at: t + 23, voice: true },
        { role: "me", text: "Is the fares cache warm for guests too?", at: t + 30, voice: true },
        { role: "ai", text: "Mostly. Guests share the member cache for the first two minutes, then", at: t + 32, voice: true, cut: true, heard: "Mostly. Guests share the member cache for the first two minutes." },
        { role: "ai", text: "The tests are done: all 112 pass, and nothing depends on the guest TTL.", result: "All 112 routing cache tests pass. Nothing depends on the guest TTL; `test_reroute` is slow (4.2 s) but passes.", reason: "done", at: t + 260, voice: true });
      // deeper work going on: asked, not back yet
      if (SCENE === "cc-call-working") S0.chat.push({ role: "me", text: "Can you check why the fares cache misses for guests?", at: t + 70, voice: true },
        { role: "ai", text: "Let me look into the fares cache.", handoff: "Find why the fares cache misses for guest requests", at: t + 72, voice: true }) }
    else if (!SCENE.startsWith("cc-past")) { MXFAKE.startMeeting("a-planner", "Weekly routing sync"); await mxFocusSession({session: "sample-meet", kind: "meeting", live: true}); for (let k = 0; k < 6; k++) await mxPoll() }
    if (SCENE === "cc-ended" || SCENE === "cc-long") { await mxEndMeeting("sample-meet"); await mxPoll() }
    if (SCENE === "cc-livemenu") { const c = MX.items.find(m => m.session === "sample-meet"); c.pass = "ask"; const b = document.querySelector('#mview header button[title="More"]'); mxWinMenu({ stopPropagation() {}, currentTarget: b }, c.id) }
    // the header's buttons mid-way: what a click shows at once, before the voice has done it
    if (SCENE === "cc-call-pausing") { VPEND = { to: true, at: Date.now() + 60000 }; const b = $("vmute"); b.textContent = "Pausing\u2026"; b.disabled = true }
    if (SCENE === "cc-callmenu") { const b = document.querySelector('.vhead button[title="More"]'); mxCallMenu({ stopPropagation() {}, currentTarget: b }) }
    // a pointer can't hover in a screenshot: one message's Copy shown as hover would
    if (SCENE === "cc-callcopy") { await wait(1800); const rows = document.querySelectorAll("#mxcalllog .mx-qa,#mxcalllog .mx-ho"); [rows[1], rows[rows.length - 1]].forEach(r => { if (r) r.querySelector(".mx-cp").style.opacity = 1 }) }
    if (SCENE === "cc-callend") { await mxEndCall(); await mxLoad(); await wait(600); mxPaint(); await wait(400) }
    if (SCENE === "cc-past") { MXFAKE.startMeeting("a-planner", "Weekly routing sync"); await mxFocusSession({session: "sample-meet", kind: "meeting", live: true}); mxBack(); await wait(300) }
    if (SCENE === "cc-past-menu") { MXFAKE.startMeeting("a-planner", "Weekly routing sync"); await mxFocusSession({session: "sample-meet", kind: "meeting", live: true}); mxBack(); await wait(300);
      const b = document.querySelector(".mx-scope"); mxScopeMenu({ stopPropagation() {}, currentTarget: b }) }
    await wait(300); return }
  if (SCENE.startsWith("mx-")) { await wait(300); S.live = null;
    if (SCENE === "mx-past-empty") { const A0 = MXFAKE.answer; MXFAKE.answer = (p, b) => p === "/api/msg/past" ? {items: []} : A0.call(MXFAKE, p, b) }
    if (SCENE !== "mx-past" && SCENE !== "mx-past-empty") MXFAKE.startMeeting("a-planner", "Routing cache sync");
    if (SCENE === "mx-micno") MXFAKE.micNo = true;
    // what the meeting hears (mx-hear*): your mic and Zoom; one turned off; Chrome joining
    if (SCENE.startsWith("mx-hear")) MXFAKE.hearing = [{id: "mic", kind: "mic", name: "Your mic", detail: "MacBook Air Microphone", on: true, heard: true},
      {id: "us.zoom.xos", kind: "app", name: "Zoom", detail: "", on: SCENE !== "mx-hear-off", heard: true}];
    if (SCENE === "mx-ask") MXFAKE.ask = {id: "mic:airpods", text: "AirPods Pro connected: record from their mic? Their sound drops to call quality while it records.",
      actions: [{label: "Switch", value: "use"}, {label: "Keep this mic", value: "keep"}]};
    if (SCENE === "mx-soundno") MXFAKE.soundNo = true;
    if (SCENE === "mx-soundask") MXFAKE.soundAsk = true;
    await mxGo(); await wait(200);
    // deleting one: its row's menu (the ⋯ shown as a pointer would), then the question
    if (SCENE === "mx-row-menu" || SCENE === "mx-delete-ask") { const row = [...document.querySelectorAll(".mx-prow[data-sid]")].find(r => !r.classList.contains("live"));
      row.classList.add("hov"); const b = row.querySelector(".mx-pmore"); mxRowMenu({ stopPropagation() {}, currentTarget: b }, row.dataset.sid);
      if (SCENE === "mx-delete-ask") { const btn = [...document.querySelectorAll("#mxmenu .mi")].find(x => x.textContent.startsWith("Delete")); btn.click() } await wait(200) }
    if (SCENE === "mx-past-session") { mxNavPick("a-planner"); await wait(100) }
    if (SCENE === "mx-past-open" || SCENE === "mx-past-open-menu") { mxNavPick("a-planner"); await mxPastOpen(MX.items.find(x => x.title === "Guest TTL review").session); for (let k = 0; k < 3; k++) await mxPoll() }
    if (SCENE === "mx-past-open-menu") { const it = MX.items.find(x => x.title === "Guest TTL review"), b = document.querySelector('#mview header button[title="More"]');
      mxWinMenu({ stopPropagation() {}, currentTarget: b }, it.id); await wait(100) }
    if (["mx-live", "mx-hear", "mx-hear-open", "mx-hear-off", "mx-hear-new", "mx-ask", "mx-pausing", "mx-ending", "mx-paused", "mx-micno", "mx-soundno", "mx-soundask", "mx-spkname", "mx-live-menu"].includes(SCENE)) {
      await mxOpenWin("sample-meet"); for (let k = 0; k < 6; k++) await mxPoll();
      if (SCENE === "mx-paused") await mxPause("sample-meet", true);
      if (SCENE.startsWith("mx-hear") && SCENE !== "mx-hear") { MXHEAR.open = true; mxPaint() }
      if (SCENE === "mx-hear-new") { MXFAKE.hearing.push({id: "com.google.Chrome", kind: "app", name: "Google Chrome", detail: "", on: true, heard: true}); await mxPoll() }
      if (SCENE === "mx-hear-off") { const s0 = MXFAKE.session("sample-meet"); MXFAKE._s["sample-meet"].offAt = 50; await mxPoll() }
      if (SCENE === "mx-pausing" || SCENE === "mx-ending") { MX.busy = { sid: "sample-meet", what: SCENE === "mx-ending" ? "end" : "pause", words: SCENE === "mx-ending" ? "Ending\u2026" : "Pausing\u2026" }; mxPaint() }
      if (SCENE === "mx-spkname") { const w = document.querySelector("#mbody .mx-who"); if (w) mxNameSpk(w, "sample-meet", "2"); await wait(100) }
      await mxPoll(); mxPaint();
      if (SCENE === "mx-live-menu") { const b = document.querySelector('#mview header button[title="More"]'); mxWinMenu({ stopPropagation() {}, currentTarget: b }, "c-live-m") } }
    await wait(300); return }
  if (SCENE === "settings") { await openSettings() }
  if (SCENE === "settings-call" || SCENE === "settings-talkover") { await openSettings(); setPane("call") }
  if (SCENE === "dictation" || SCENE === "dictation-words") { await openSettings(); setPane("dictation") }
  if (SCENE === "settings-recording" || SCENE.startsWith("settings-speech")) { await openSettings(); setPane("recording") }
  if (SCENE === "usage" || SCENE === "usage-empty") { await openSettings(); setPane("usage") }
  if (SCENE === "settings-help") { await openSettings(); setPane("dictation"); await wait(200); mxHelp(document.querySelector("#kholdf .mx-help")) }
  if (SCENE === "dictation-shortcut") { await openSettings(); setPane("dictation"); openShortcuts() }
  if (SCENE === "dictation-recording") { await openSettings(); setPane("dictation"); recordShortcut() }
  if (SCENE === "usage-today") { await openSettings(); setPane("usage"); await loadUsage("today") }
  if (SCENE === "settings-voice-prep") { await openSettings(); setPane("call") }
  if (SCENE.startsWith("settings-try-")) { TRYING = 7;          // followed already: the fixture stays as it is
    if (SCENE === "settings-try-failed") TRYFAIL = { n: 7, voice: "Jean", state: "failed", error: "no connection to Hugging Face", downloaded: true };
    await openSettings(); setPane("call") }
})();
"""


# The orb's window, then the words' window (the "words-" scenes, at /companion#words).
CMP_SCENES = ["idle", "recording", "recording-roll", "recording-hover", "left-hover",
              "listening", "hearing", "thinking", "thinking-steps", "thinking-steps-hover", "holding", "speaking", "muted", "muted-waiting", "muted-waiting-hover", "voice-hover", "left-voice-hover",
              "preparing", "down", "down-hover", "micdenied", "words-micdenied", "words-speaking"]
CMP_FAKE = r"""
const SCENE = new URLSearchParams(location.search).get("s") || "idle";
// A live call: the orb is for calls only (a meeting never has one).
const LIVE = () => ({ id: "talk-1", kind: "talk", call: { agent: "a-planner", name: "Planner", started: Date.now() / 1000 - 87 } });
const V = (state, extra) => Object.assign({ state, level: 0, you: "", claude: "", step: "", muted: state === "muted" }, extra || {});
const DATA = { idle: { live: null, voice: V("off") }, recording: { live: LIVE(), voice: V("off") },
  preparing: { live: LIVE(), voice: V("listening", { preparing: "Getting the voice ready… 42%", preparing_pct: 42 }) },
  down: { live: LIVE(), voice: V("off"), claude_error: { kind: "signin", text: "Claude can't sign in.", fix: "Run claude, then /login, and try again.", detail: "", at: 1 } },
  listening: { live: LIVE(), voice: V("listening") }, hearing: { live: LIVE(), voice: V("listening", { level: .2 }) }, voice: { live: LIVE(), voice: V("listening") },
  thinking: { live: LIVE(), voice: V("thinking", { step: "Looking it up online" }) },
  holding: { live: LIVE(), voice: V("listening", { holding: true }) },
  "thinking-steps": { live: LIVE(), voice: V("thinking", { action: "Searching your files", actions: 3 }) },
  "thinking-steps-hover": { live: LIVE(), voice: V("thinking", { actions: 4 }) },
  speaking: { live: LIVE(), voice: V("speaking", { claude: "Whether guests get the route cache too." }) },
  muted: { live: LIVE(), voice: V("muted") }, "muted-waiting": { live: LIVE(), voice: V("muted", { waiting: true }) },
  left: { live: LIVE(), voice: V("off") }, "left-voice": { live: LIVE(), voice: V("listening") },
  micdenied: { live: Object.assign(LIVE(), { mic_denied: true }), voice: V("off") },
  "words-micdenied": { live: Object.assign(LIVE(), { mic_denied: true }), voice: V("off") },
  "recording-roll": { live: LIVE(), voice: V("off") },
  // on a call, just the orb: no time or captions above it
  "words-speaking": { live: LIVE(), voice: V("speaking", { claude: "Running them now." }) } };
// ?live=1: your voice moves while it hears you, so a page can show the real orb moving
const LIVEV = new URLSearchParams(location.search).get("live");
const SAID = t => (Math.sin(t * 2.1) > -.35 ? 1 : 0) * Math.max(0, Math.abs(Math.sin(t * 19.5)) * .7 + Math.abs(Math.sin(t * 33 + 1)) * .3) * .35;
window.fetch = async p => ({ json: async () => p === "/api/companion" ? Object.assign({ starting: null }, (d => LIVEV && d.voice && d.voice.state === "listening" && SCENE === "hearing" ? Object.assign({}, d, { voice: Object.assign({}, d.voice, { level: SAID(Date.now() / 1000) }) }) : d)(DATA[SCENE.replace("-hover", "")] || DATA.idle)) : { ok: true } });
"""
CMP_AFTER = r"""
setTimeout(() => { if (SCENE.startsWith("left")) edge("left", "bottom");
  if (SCENE.endsWith("-hover")) { open = true; render() }
  // the pill caught half way out from under the orb
  if (SCENE.endsWith("-roll")) { open = true; render(); setTimeout(() => document.getAnimations().forEach(a => { a.pause(); a.currentTime = 120 }), 40) } }, 60);
"""


def companion_page():
    html = (ROOT / "mac" / "companion.html").read_text()
    i = html.rindex("<script>")
    return html[:i] + "<script>" + CMP_FAKE + "</script>" + html[i:].replace(
        "</body>", "<script>" + CMP_AFTER + "</script></body>")


def page():
    src = (ROOT / "mac" / "ui.py").read_text()
    html = src[src.index('PAGE = r"""') + len('PAGE = r"""'):]
    html = html[:html.index('"""')]
    i = html.rindex("<script>")
    html = (ROUNDED + LIGHTS + html[:i]) + "<script>" + FAKE + "</script>" + html[i:].replace(
        "</body>", "<script>" + SCENES + "</script></body>")
    # The agents view's own files, inline: the preview has no server
    mx = ROOT / "mac"
    return html.replace('<link rel="stylesheet" href="/agents.css">', "<style>" + (mx / "agents.css").read_text() + "</style>").replace(
        '<script src="/agents.js"></script>', "<script>" + (mx / "agents.js").read_text() + "</script>")


# Chrome has no ui-rounded (WebKit's SF Pro Rounded), so it takes the name as a family's:
# give it the system's own file, and the preview shows the app's real type.
ROUNDED = ('<style>@font-face{font-family:"ui-rounded";src:url("file:///System/Library/Fonts/SFNSRounded.ttf");'
           'font-weight:100 900}</style>')
# And the window's traffic lights, as the app has them (ui.py's lights()), so the headers' corner reads
# as it does on the Mac.
LIGHTS = ('<style>html.lights body:before,html.lights dialog.setwin[open]:before{content:"";position:fixed;z-index:100;left:20px;top:20px;width:12px;height:12px;border-radius:50%;'
          'background:#ff5f57;box-shadow:20px 0 #febc2e,40px 0 #28c840}</style><script>document.documentElement.classList.add("lights")</script>')


class Chrome:
    """One headless Chrome for the whole run, driven over its DevTools pipe.

    Each Chrome start can flash an icon in the user's Dock, so every screenshot
    of a run, light and dark, comes from this one process (team rule: never
    start apps in a loop)."""

    def __init__(self):
        # A profile of its own: two runs at once (two worktrees) can't share one
        import tempfile
        self.profile = tempfile.mkdtemp(prefix="standby-preview-chrome-")
        to_chrome, self._w = os.pipe()       # Chrome reads commands on fd 3
        self._r, from_chrome = os.pipe()     # and writes replies on fd 4

        def wire():
            hi = [fcntl.fcntl(fd, fcntl.F_DUPFD, 10) for fd in (to_chrome, from_chrome)]
            os.dup2(hi[0], 3)
            os.dup2(hi[1], 4)
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
             "--user-data-dir=" + self.profile, "--remote-debugging-pipe", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=False, preexec_fn=wire)
        os.close(to_chrome)
        os.close(from_chrome)
        self._buf, self._id, self.events = b"", 0, []
        page = next(t for t in self.call("Target.getTargets")["targetInfos"] if t["type"] == "page")
        self.session = self.call("Target.attachToTarget", targetId=page["targetId"], flatten=True)["sessionId"]
        self.call("Page.enable", session=True)

    def _read(self, timeout=30):
        while b"\0" not in self._buf:
            if not select.select([self._r], [], [], timeout)[0]:
                raise RuntimeError("Chrome said nothing for %d s" % timeout)
            chunk = os.read(self._r, 1 << 20)
            if not chunk:
                raise RuntimeError("Chrome closed its DevTools pipe")
            self._buf += chunk
        msg, self._buf = self._buf.split(b"\0", 1)
        return json.loads(msg)

    def call(self, method, session=False, **params):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params}
        if session:
            msg["sessionId"] = self.session
        os.write(self._w, json.dumps(msg).encode() + b"\0")
        while True:
            m = self._read()
            if m.get("id") == self._id:
                if "error" in m:
                    raise RuntimeError("%s: %s" % (method, m["error"].get("message")))
                return m.get("result", {})
            self.events.append(m.get("method"))

    def shoot(self, url, png, dark, size=(1100, 760), scale=2, settle=1.2, quality=None, clip=None, pad=0):
        self.call("Emulation.setDeviceMetricsOverride", session=True, width=size[0], height=size[1],
                  deviceScaleFactor=scale, mobile=False)
        self.call("Emulation.setEmulatedMedia", session=True,
                  features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}])
        # The same address again would only jump within the page (no load), so go
        # through a blank page first.
        if url == getattr(self, "_last", None):
            self.call("Page.navigate", session=True, url="about:blank")
            time.sleep(0.2)
        self._last = url
        self.events = []
        self.call("Page.navigate", session=True, url=url)
        for _ in range(200):
            if "Page.loadEventFired" in self.events:
                break
            self.events.append(self._read().get("method"))
        time.sleep(settle)                   # the page's own fetches and timers
        shot = {"format": "jpeg", "quality": quality} if quality else {"format": "png"}
        if clip:                             # just one element of the page, by CSS selector
            r = self.call("Runtime.evaluate", session=True, returnByValue=True, expression=(
                "(()=>{const r=document.querySelector(%s).getBoundingClientRect();"
                "return {x:r.x-%d,y:r.y-%d,width:r.width+%d,height:r.height+%d,scale:1}})()" % (json.dumps(clip), pad, pad, 2 * pad, 2 * pad)))
            shot["clip"] = r["result"]["value"]
        data = self.call("Page.captureScreenshot", session=True, **shot)["data"]
        png.write_bytes(base64.b64decode(data))
        return png.exists()

    def close(self):
        try:
            self.call("Browser.close")
        except (RuntimeError, OSError):
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        import shutil
        shutil.rmtree(self.profile, ignore_errors=True)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    themes = [False, True] if "--both" in sys.argv else [("--dark" in sys.argv)]
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), "")
    out = Path(args[0] if args else "/tmp/standby-preview")
    out.mkdir(parents=True, exist_ok=True)
    html_path = out / "page.html"
    html_path.write_text(page())
    chrome = Chrome()
    try:
        for dark in themes:
            sfx = "-dark" if dark else ""
            if "--companion" in sys.argv:
                # The real companion page, every state against a fake /api/companion.
                cmp_path = out / "companion-page.html"
                cmp_path.write_text(companion_page())
                shots = []
                for scene in CMP_SCENES:
                    png = out / ("companion-%s%s.png" % (scene, sfx))
                    url = "%s?s=%s%s" % (cmp_path.as_uri(), scene, "#words" if "words" in scene else "")
                    ok = chrome.shoot(url, png, dark, size=(480, 160), settle=0.8)
                    print(("ok   " if ok else "FAIL ") + str(png))
                    shots.append(png)
                # One sheet of every state, and the same in two halves, each small enough
                # to read in a review without a shell.
                bg = ("#1b1b1d", "#bbb") if dark else ("#c9c9ce", "#333")
                rows = (len(shots) + 2) // 3
                cut = (rows + 1) // 2 * 3
                for name, part in (("", shots), ("-1", shots[:cut]), ("-2", shots[cut:])):
                    sheet = out / ("companion-sheet%s%s.html" % (sfx, name))
                    sheet.write_text("<body style='margin:0;padding:16px;background:%s;display:grid;grid-template-columns:1fr 1fr 1fr;"
                                     "gap:14px;font:12px system-ui;color:%s'>" % bg + "".join(
                                         "<div>%s<img src='%s' style='width:100%%;display:block;margin-top:4px'></div>"
                                         % (p.stem.replace("companion-", ""), p.name) for p in part) + "</body>")
                    png = sheet.with_suffix(".png")
                    ok = chrome.shoot(sheet.as_uri(), png, False, size=(1200, 16 + (len(part) + 2) // 3 * 170), scale=1, settle=0.5)
                    print(("ok   " if ok else "FAIL ") + str(png))
                continue
            shots = []
            for state in (only.split(",") if only else STATES):
                png = out / ("%s%s.png" % (state, sfx))
                # A new query string makes Chrome load the page afresh for each state.
                if state.endswith("@wide"):           # any state, the window stretched wide
                    ok = chrome.shoot("%s?s=%s#%s" % (html_path.as_uri(), state[:-5], state[:-5]), png, dark, size=(1100, 760), settle=4)
                elif state.startswith(("cc-", "mx-")):   # the window, at its size
                    ok = chrome.shoot("%s?s=%s#%s" % (html_path.as_uri(), state, state), png, dark,
                                      size=(500, 720), settle=9 if state.startswith("cc-call") else 4)
                else:                                 # the window is one size
                    ok = chrome.shoot("%s?s=%s#%s" % (html_path.as_uri(), state, state), png, dark, size=(500, 720))
                print(("ok   " if ok else "FAIL ") + str(png))
                shots.append(png)
            # All states on one page, to look at them side by side.
            sheet = out / ("sheet%s.html" % sfx)
            sheet.write_text("<body style='margin:0;padding:16px;background:#888;display:grid;grid-template-columns:1fr 1fr;"
                             "gap:16px;font:13px system-ui;color:#fff'>" + "".join(
                                 "<div>%s<img src='%s' style='width:100%%;display:block;margin-top:4px'></div>" % (p.stem, p.name)
                                 for p in shots) + "</body>")
            png = sheet.with_suffix(".png")
            ok = chrome.shoot(sheet.as_uri(), png, False, size=(1400, 16 + (len(shots) + 1) // 2 * 476), scale=1, settle=0.5)
            print(("ok   " if ok else "FAIL ") + str(png))
    finally:
        chrome.close()


if __name__ == "__main__":
    main()
