// Execute the app page's script against a minimal fake DOM and check what it still draws itself:
// the call view's voice states, Settings (dictation, voice, recording, speech, your voice) and Usage.
// Standby's own view is agents.js, with its own tests.
const els = {};
const el = id => els[id] || (els[id] = {
  id, style: { setProperty(k, v) { this[k] = v } }, textContent: "", innerHTML: "", value: "", disabled: false, checked: false,
  contentEditable: "false", dataset: {},
  classList: { t: new Set(), add(c) { this.t.add(c) }, remove(c) { this.t.delete(c) },
    toggle(c, on) { on === undefined ? (this.t.has(c) ? this.t.delete(c) : this.t.add(c)) : (on ? this.t.add(c) : this.t.delete(c)) },
    contains(c) { return this.t.has(c) } },
  addEventListener() {}, focus() {}, setAttribute(k, v) { this["attr_" + k] = v }, querySelector: () => null, scrollTop: 0, clientHeight: 500, scrollHeight: 900,
  showModal() {}, close() {} });
global.document = { getElementById: el, querySelectorAll: () => [], querySelector: () => null, getSelection: () => ({ selectAllChildren() {} }),
  addEventListener() {},
  createElement: () => el("a-" + Math.random()), body: { appendChild() {} } };
global.settings = el("settings"); global.alert = () => {}; global.confirm = () => true; global.prompt = () => "x";
global.setInterval = () => 0; global.window = {};
const now = Date.now() / 1000;
let state = { sessions: [], live: null, starting: null, agent: { mode: "local", label: "this Mac" } };
const sessions = {
  "live-1": { info: { id: "live-1", title: "Pricing <review>", title_source: "ai", started: now - 65, live: true,
    workspace: "~/code/x", duration: 60, lines: 3, claude: true, mic: true, joined: true,
    runs: [{ start: now - 900, t: 0, end: now - 700, t_end: 11 }, { start: now - 60, t: 12 }] },
    chat: [{ role: "me", text: "what is Orion" }, { role: "ai", text: "The routing <b>cache</b>", steps: ["Reading ~/a.md", "Running standby-ctx recent"] },
      { role: "me", text: "details?" }, { role: "ai", text: "## Plan\n\n**Bold** and `code` here.\n\n- one\n- two\n\n```\nx <y>\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nSee https://example.com and [bad](javascript:alert(1)) <img src=x onerror=alert(1)>" }],
    transcript: [{ t: 5, speaker: "Room", spk: 1, text: "hello" }, { t: 9, speaker: "Room", spk: 3, text: "merged" },
      { t: 12, speaker: "Room", spk: 2, text: "new voice" }, { t: 14, speaker: "Room", text: "unlabelled" }],
    speakers: { alias: { "3": 1 }, names: { "1": "Alex" }, known: { "2": "Sam" } }, pending: null },
  "old-1": { info: { id: "old-1", title: "Standup", title_source: "user", started: now - 90000, live: false,
    duration: 600, lines: 10, claude: false, mic: true, ended: now - 89000 }, chat: [], transcript: [], speakers: { alias: {}, names: {} }, pending: null } };
const check = { where: "this Mac", results: [
  { group: "mac", ok: true, label: "Speech engine", detail: "installed" },
  { group: "agent", ok: false, label: "Claude Code CLI", detail: "not <found>" }] };
let shortResp = { ok: true }, projectsResp = { projects: [] }, checkResp = {}, dictResp = { text: "", done: false }, calls = [], attachResp = { files: [], max: 5, max_bytes: 25 * 1048576 };
let voiceStatus = { state: "listening", level: 0, you: "", claude: "", muted: false };
let stateHold = null;
let startResp = { error: "not found" }, recentsResp = { recents: [], total: 0 }, foldersResp = { folders: [] }, sessionsResp = { sessions: [] },
  checkResp55 = { steps: [], done: true }, checkN = 0, machinesResp = { machines: [] }, sessStartResp = { started: true }, resumeResp = { ok: true };          // when set, /api/state answers late, with what it saw when asked
global.fetch = async (p, o) => { calls.push([p, o && o.body]);
  if (p === "/api/state" && stateHold) { const seen = state; await stateHold; return { json: async () => seen } }
  return { json: async () =>
  p === "/api/state" ? state : p === "/api/check" ? check : p === "/api/dictate" ? { ok: true, seq: 5 }
  : p === "/api/dictation-app" && o && o.body && /shortcut|hold/.test(o.body) ? shortResp
  : p === "/api/projects" ? projectsResp : p === "/api/projects/check" ? checkResp
  : p === "/api/attach" ? attachResp
  : p.startsWith("/api/dictation/") ? dictResp : p === "/api/claude" ? { ok: true }
  : p === "/api/start" ? { started: true }
  : p.startsWith("/api/start?") ? startResp : p.startsWith("/api/start/recents") ? recentsResp
  : p.startsWith("/api/start/folders") ? foldersResp : p.startsWith("/api/start/sessions") ? sessionsResp
  : p === "/api/start/check" ? { id: "k" + (++checkN) } : p.startsWith("/api/start/check/") ? checkResp55
  : p === "/api/session/start" ? sessStartResp : p === "/api/machines" ? machinesResp
  : p === "/api/terminal" ? { ok: true, where: "devbox" } : p === "/api/resume" ? (o && o.body && o.body.includes("without_claude") ? { ok: true } : resumeResp) : p === "/api/session/copy" ? {}
  : p === "/api/voice" ? { ok: true, state: voiceStatus } : p.startsWith("/api/voice/") ? voiceStatus
  : sessions[p.split("/").pop()] || { ok: true } }; };
// Any error no check saw (an async page call, a timer, a handler) fails the run: an undeclared
// name in the project menu once threw while every check still said OK (found when the folder picker was ported).
const loose = [];
const loosen = kind => e => { loose.push(kind); process.exitCode = 1;
  console.log("  " + kind + ": " + (e && e.stack ? e.stack.split("\n").slice(0, 2).join(" ") : e)) };
process.on("uncaughtException", loosen("uncaught error"));
process.on("unhandledRejection", loosen("unhandled rejection"));
process.on("exit", () => { if (loose.length) console.log("FAILURES: " + loose.length + " error(s) outside the checks") });
eval(require("fs").readFileSync(process.env.UI_PAGE_JS, "utf8") + `
(async () => {
  const out = [], body = () => el("body").innerHTML, ok = (k, v, d) => out.push([k, !!v, d]);
  await new Promise(r => setTimeout(r, 20));
  // The old session view is gone: no list, chat, box or tabs; Standby's view (agents.js) is the window
  ok("no old session view in the page", typeof renderList === "undefined" && typeof send === "undefined" && typeof setTab === "undefined" && typeof openVoice === "undefined" && typeof startTalk === "undefined");
  // The call view (Claude Code's calls): the voice's states, as it paints them
  const voiceCalls = a => calls.filter(c => c[0] === "/api/voice" && JSON.parse(c[1]).action === a).length;
  attachVoice("live-1");
  ok("call view: its screen shows for the call's session", el("voice").style.display === "" && voice && voice.sid === "live-1");
  paintVoice({ state: "listening", level: 0.2, you: "what is <Orion>", claude: "", muted: false });
  ok("voice: listening shows the words heard, as text, under Listening (the row at the bottom names its state)", el("vyou").textContent === "what is <Orion>"
     && el("voice").className === "voice listening" && el("vstate").textContent === "Listening");
  ok("  and the orb grows with the voice", parseFloat(el("orb").style["--s"]) > 1, el("orb").style["--s"]);
  paintVoice({ state: "listening", level: 0, you: "", claude: "", muted: false, preparing: "Getting the voice ready… 40%" });
  ok("voice: the first time, it still says Listening", el("vstate").textContent === "Listening", el("vstate").textContent);
  ok("  and a quiet line says the voice is getting ready, with its percent", !el("vprep").hidden && el("vpreptxt").textContent === "Getting Standby's voice ready · 40%"
     && el("vprepfill").style["--p"] === "40%", [el("vpreptxt").textContent, el("vprepfill").style]);
  paintVoice({ state: "listening", level: 0, you: "", claude: "", muted: false, preparing: "Getting the voice ready…", preparing_pct: null });
  ok("  while loading, no number: no percent", el("vpreptxt").textContent === "Getting Standby's voice ready…" && el("vprep").classList.contains("unknown"), el("vpreptxt").textContent);
  paintVoice({ state: "listening", level: 0, you: "", claude: "", muted: false });
  ok("  and plain Listening after, the line gone", el("vstate").textContent === "Listening" && el("vprep").hidden);
  paintVoice({ state: "thinking", you: "what is Orion?", claude: "", step: "", muted: false });
  ok("voice: thinking says so", el("vstate").textContent === "Thinking…" && el("orb").title === "Tap to interrupt");
  paintVoice({ state: "thinking", you: "who owns it?", claude: "", step: "", action: "Searching your files", actions: 3, muted: false });
  ok("voice: Claude at work, its actions counted at the bottom right", el("vsteps").textContent === "Searching your files · 3" && el("vstate").textContent === "Thinking…", [el("vsteps").textContent, el("vstate").textContent]);
  paintVoice({ state: "thinking", you: "who owns it?", claude: "", step: "", action: "", actions: 4, muted: false });
  ok("  with no plain name: Taking actions", el("vsteps").textContent === "Taking actions · 4", el("vsteps").textContent);
  paintVoice({ state: "thinking", you: "who owns it?", claude: "", step: "Still waiting for Claude", actions: 0, muted: false });
  ok("  before the first: the step as today, under the orb", el("vstate").textContent === "Still waiting for Claude…" && el("vsteps").textContent === "", [el("vstate").textContent, el("vsteps").textContent]);
  paintVoice({ state: "listening", you: "", claude: "", holding: true, muted: false });
  // the user's own voice: its row while Remember voices is on, Forget only that
  el("s_remember").checked = true; MYVOICE = 260; paintMyVoice();
  ok("your voice: learnt, how long, and its own Forget", !el("myvoicef").hidden && el("s_myvoice").textContent === "Your voice \u00b7 4 min" && !el("s_myforget").hidden, el("s_myvoice").textContent);
  el("s_remember").checked = false; paintMyVoice();
  ok("  hidden while Remember voices is off", el("myvoicef").hidden);
  el("s_remember").checked = true; MYVOICE = null; paintMyVoice();
  ok("  not learnt yet: said so, nothing to forget", el("s_myvoice").textContent === "Your voice \u00b7 not learnt yet" && el("s_myforget").hidden);
  ok("voice: on hold, how to bring the answer back", el("vstate").textContent === "On hold · say “go on”" && el("voice").className.includes("holding"), el("vstate").textContent);
  paintVoice({ state: "listening", you: "", claude: "", notice: "That didn't sound like you", muted: false });
  ok("voice: a turn let go, not your voice: said under the orb", el("vstate").textContent === "That didn't sound like you", el("vstate").textContent);
  paintVoice({ state: "thinking", you: "what is Orion?", claude: "", step: "Looking it up online", muted: false });
  ok("  and what Claude is doing, in the small status line, never the big caption",
     el("vstate").textContent === "Looking it up online…" && el("vclaude").textContent === "");
  paintVoice({ state: "speaking", you: "what is Orion?", claude: "Orion is the routing cache.", muted: false });
  ok("voice: speaking shows Claude's sentence", el("vclaude").textContent === "Orion is the routing cache."
     && el("voice").className === "voice speaking");
  await voiceTap();
  ok("voice: a tap on the orb interrupts", voiceCalls("interrupt") === 1);
  paintVoice({ state: "muted", you: "", claude: "", muted: true });
  ok("voice: paused (muted) shows in the call's header: the play mark, Resume its tip, the state Paused", el("vmute").title === "Resume" && el("vstate").textContent === "Paused");
  paintVoice({ state: "muted", you: "", claude: "", muted: true, waiting: true });
  ok("  paused with work waiting: a red dot on Resume, and its tip", el("vmute").classList.contains("waiting") && /waiting: resume to hear it/.test(el("vmute").title));
  paintVoice({ state: "muted", you: "", claude: "", muted: true });
  ok("  nothing waiting: no dot, its tip plain Resume", !el("vmute").classList.contains("waiting") && el("vmute").title === "Resume");
  await voiceMute();
  ok("  and Resume unmutes", voiceCalls("unmute") === 1);
  await closeVoice();
  ok("voice: closing hides it and stops it on the server", el("voice").style.display === "none" && voiceCalls("stop") === 1);
  paintUsage({ range: "today", bucket: "hour", totals: { calls: 12, input: 9000, output: 800, cache_read: 50000, cache_write: 1000 },
    series: [{ t: now, calls: 12 }], models: [{ model: "claude-haiku-<x>", calls: 12, input: 9000, output: 800 }],
    kinds: [{ kind: "voice", calls: 10, input: 8000, output: 700, models: ["claude-haiku-<x>"] },
            { kind: "words", calls: 2, input: 1000, output: 100, models: ["claude-haiku-<x>"] }] });
  const ub = el("ubody").innerHTML;
  ok("usage: calls and tokens", ub.includes(">12<") && ub.includes("11K"));
  ok("  models by family, the exact id escaped in its tooltip", ub.includes(">Haiku<") && ub.includes("claude-haiku-&lt;x&gt;"));
  ok("  kinds in words, the word-fixes pass among them", ub.includes(">Voice turns<") && ub.includes(">Word fixes<"));
  ok("  never a dollar: no cost tile or column, no busiest minute", !ub.includes("Cost") && !ub.includes("$") && !ub.includes("Busiest"));
  paintUsage({ totals: { calls: 0 } });
  ok("  nothing yet: an empty state", el("ubody").innerHTML.includes("No calls yet"));
  paintUsage({ error: "ssh: <host> did not answer" });
  ok("  an error says so, escaped, not as an empty state", el("ubody").innerHTML.includes("not available right now") && el("ubody").innerHTML.includes("&lt;host&gt;"));
  // The dictation shortcut: presets or one recorded; the server judges, a refusal changes nothing.
  DICT = { on: true, running: true, missing: [], hold: false, shortcut: { id: "mickey", label: "Press F5", kind: "key" },
    shortcuts: [{ id: "mickey", label: "Press F5", kind: "key" }, { id: "control2", label: "Control twice", kind: "double" }] };
  paintDict();
  ok("shortcut: the field shows the one in use", !el("kshortf").hidden && el("kfield").innerHTML.includes("Press F5"), el("kfield").innerHTML);
  openShortcuts();
  ok("  the list: the presets, the one in use ticked, then Record a shortcut…", el("kmenu").innerHTML.includes("Control twice") && el("kmenu").innerHTML.includes("✓") && el("kmenu").innerHTML.includes("Record a shortcut…"), el("kmenu").innerHTML);
  recordShortcut();
  ok("  recording: the field says so", el("kfield").innerHTML.includes("Press the keys"), el("kfield").innerHTML);
  let stopped = 0; const kev = o => Object.assign({ preventDefault() { stopped++ }, stopPropagation() {}, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false }, o);
  calls.length = 0; shortResp = { ok: true, shortcut: { id: "custom", label: "⌥ Space", kind: "combo" } };
  recKey(kev({ code: "AltLeft", key: "Alt", altKey: true })); recKey(kev({ code: "Space", key: " ", altKey: true }));
  await new Promise(r => setTimeout(r, 20));
  ok("  a modifier and a key: sent as code, key and mods, and kept from the app", calls.some(c => c[0] === "/api/dictation-app" && c[1].includes('"code":"Space"') && c[1].includes('"mods":["alt"]')) && stopped === 2, calls);
  ok("  the server's name for it, in the field", el("kfield").innerHTML.includes("⌥ Space"), el("kfield").innerHTML);
  recordShortcut(); calls.length = 0;
  recKey(kev({ code: "AltRight", key: "Alt" })); recKey(kev({ code: "AltRight", key: "Alt" }));
  await new Promise(r => setTimeout(r, 20));
  ok("  one modifier twice: sent as a double tap", calls.some(c => c[0] === "/api/dictation-app" && c[1].includes('"double":"AltRight"')), calls);
  recordShortcut(); shortResp = { error: "⌘ Space is Spotlight's" };
  recKey(kev({ code: "Space", key: " ", metaKey: true })); await new Promise(r => setTimeout(r, 20));
  ok("  refused: why, in one line, and the shortcut stays", el("khint").textContent === "⌘ Space is Spotlight's" && el("kfield").innerHTML.includes("⌥ Space"), [el("khint").textContent, el("kfield").innerHTML]);
  recordShortcut(); calls.length = 0; recKey(kev({ code: "Escape", key: "Escape" }));
  ok("  Esc cancels, sending nothing", !calls.some(c => c[0] === "/api/dictation-app") && !el("kfield").innerHTML.includes("Press the keys"), calls);
  DICT.shortcuts.push({ id: "custom", label: "⌥ Space", kind: "combo" }); DICT.shortcut = { id: "custom", label: "⌥ Space", kind: "combo" }; openShortcuts();
  ok("  a recorded one is listed once", el("kmenu").innerHTML.split("⌥ Space").length === 2, el("kmenu").innerHTML);
  DICT.shortcut.problem = "⌥ Space: another app already uses it"; paintDict();
  ok("  taken later by another app: said under the field", el("khint").textContent === "⌥ Space: another app already uses it", el("khint").textContent);
  // Word fixes: the count, the suggestions, and Edit… opens the editor
  DICT.words = { count: 12, learned: 5, suggested: 2 }; paintDict();
  ok("dictation: word fixes, with their suggestions", el("kwords").innerHTML.includes("12 words Standby corrects") && el("kwords").innerHTML.includes("2 suggested") && !el("kwordsf").hidden, el("kwords").innerHTML);
  { const n = calls.length; await editWords();
    ok("  Edit… asks the dictation app for its editor", calls.slice(n).some(c => c[0] === "/api/dictation-app" && String(c[1]).includes("edit_words")), calls.slice(n)) }
  delete DICT.words;
  DICT = null;
  // Recording: Automatic, Mic only, Meeting audio only; no BlackHole to set up.
  setCapture("auto", true);
  ok("recording: Automatic says what it records", el("s_capture").value === "auto" && el("s_caphint").title.includes("the sound your Mac plays") && el("s_caphint").title.includes("whenever something is playing"), el("s_caphint").title);
  setCapture("meeting", true);
  ok("  meeting audio only: no microphone to pick", el("micf").style.display === "none");
  setCapture("mic", true);
  ok("  mic only: the microphone is back, and the hint is honest about headphones", el("micf").style.display === "" && el("s_caphint").title.includes("headphones"));
  ok("  the test's other channel is the meeting's audio", micTestHtml({ state: "running", channels: [{ channel: "Others", peak: null, heard: [], problem: [] }] }).includes("Meeting audio"));
  ok("  it names the app playing, and asks to keep it playing", (h => h.includes("Meeting audio · Zoom") && h.includes("keep it playing"))(micTestHtml({ state: "running", channels: [{ channel: "Others", device: "Zoom", peak: null, heard: [], problem: [] }] })));
  ok("  no call: the recorder's words", micTestHtml({ state: "done", channels: [{ channel: "Others", device: "", peak: 0, heard: [], problem: ["Nothing is playing to test. Start a call or a video, and test again."] }] }).includes("Nothing is playing to test"));
  // Settings > Dictation: the switch, one status line, rows only for what is missing.
  DICT = { on: true, running: true, old: false, updating: false, problem: null, shortcut: "Press F5", missing: [] }; paintDict();
  ok("dictation: ready says what to press", el("dictstate").innerHTML === "Ready. Press F5 in any app" && el("s_dict").checked, el("dictstate").innerHTML);
  ok("  and lists nothing", el("dictperms").innerHTML === "");
  for (const [k, want] of [[{ id: "mickey", label: "Press F5", kind: "key" }, "Ready. Press F5 in any app"], [{ id: "ctrl2", label: "Control twice", kind: "double" }, "Ready. Press Control twice in any app"]]) {
    DICT.shortcut = k; paintDict();
    ok("  the shortcut as the app sends it, said once: " + k.label, el("dictstate").innerHTML === want, el("dictstate").innerHTML) }
  DICT.shortcut = "Press F5";
  DICT.missing = [{ key: "mic", name: "Microphone", why: "hears you", action: "ask", button: "Allow" },
    { key: "post", name: "Accessibility", why: "pastes the <text>", action: "open", button: "Open Settings…" }]; paintDict();
  ok("dictation: the rows say what is missing, the state line does not repeat it", el("dictstate").innerHTML === "", el("dictstate").innerHTML);
  ok("  a row for each, with its fix", /Microphone[\\s\\S]*>Allow<[\\s\\S]*pastes the &lt;text&gt;[\\s\\S]*>Open Settings</.test(el("dictperms").innerHTML)
    && el("s_dictwhy").title.includes("on already in the dictation setup window") && el("s_dictwhy").hidden === false, el("s_dictwhy").title);
  DICT.updating = true; DICT.missing = []; paintDict();
  ok("dictation: updating to new code", el("dictstate").innerHTML.includes("Updating to the new version"));
  DICT.updating = false; DICT.problem = "SyntaxError: invalid syntax"; paintDict();
  ok("  a problem with new code shows as it is", el("dicterr").textContent === "SyntaxError: invalid syntax" && el("dicterr").style.display === "");
  DICT = { on: true, running: true, old: true, missing: [] }; paintDict();
  ok("dictation: an old copy offers a restart", /older version[\\s\\S]*Restart/.test(el("dictstate").innerHTML));
  DICT = { on: true, running: false, missing: [] }; paintDict();
  ok("dictation: on but not running offers Start", /Not running[\\s\\S]*Start/.test(el("dictstate").innerHTML));
  DICT = { on: false, running: false, missing: [] }; paintDict();
  ok("dictation: off says nothing leaves this Mac", el("dictstate").innerHTML.includes("Nothing leaves this Mac") && !el("s_dict").checked);
  calls.length = 0; await setDictation(true);
  ok("dictation: the switch acts at once", calls.some(c => c[0] === "/api/dictation-app" && JSON.parse(c[1]).action === "on"), calls);
  // Settings > Voice: automatic names what it picks, the voices grouped, the hint only without a good one.
  VOICES = { chosen: null, better: true, current: { id: "p.Ava", name: "Ava", quality: "premium" },
    voices: [{ id: "p.Ava", name: "Ava", quality: "premium", lang: "en-US", region: "US" },
      { id: "c.Daniel", name: "Daniel <x>", quality: "standard", lang: "en-GB", region: "UK" }] }; paintVoices();
  const vh = el("s_voice").innerHTML;
  ok("voice: automatic says what it picks", vh.startsWith('<option value="">Automatic (Ava, Premium)</option>'), vh.slice(0, 80));
  ok("  Premium first, by name and region, escaped", /optgroup label="Premium"[\\s\\S]*Ava \\(US\\)[\\s\\S]*optgroup label="Standard"[\\s\\S]*Daniel &lt;x&gt; \\(UK\\)/.test(vh)
    && !vh.includes("Enhanced"), vh);
  ok("  no hint when a good voice is installed", el("s_voicehint").hidden === true);
  VOICES = { chosen: null, better: false, current: { id: null, name: "System voice", quality: "standard" },
    voices: [{ id: "c.Samantha", name: "Samantha", quality: "standard", lang: "en-US", region: "US" }] }; paintVoices();
  ok("voice: none good installed, automatic is the system voice, and the hint shows once",
    el("s_voice").innerHTML.startsWith('<option value="">Automatic (System voice)</option>') && el("s_voicehint").hidden === false);
  ok("  no credit line for a macOS voice", el("s_voicecredit").hidden === true);
  // Standby's own voices: first, automatic is Jean, the credit Pocket TTS asks for, no Premium hint.
  VOICES = { chosen: null, better: false, credit: "Voice: Pocket TTS by Kyutai (CC BY 4.0)",
    current: { id: "pocket:jean", name: "Jean", quality: "standby" },
    standby: [{ id: "pocket:jean", name: "Jean", quality: "standby" }, { id: "pocket:alba", name: "Alba", quality: "standby" }],
    voices: [{ id: "c.Samantha", name: "Samantha", quality: "standard", lang: "en-US", region: "US" }] }; paintVoices();
  const sv = el("s_voice").innerHTML;
  ok("voice: Standby's own first, automatic is Jean",
    sv.startsWith('<option value="">Automatic (Jean)</option><optgroup label="Standby"><option value="pocket:jean">Jean</option>')
    && /Alba[\\s\\S]*optgroup label="Standard"[\\s\\S]*Samantha/.test(sv), sv.slice(0, 160));
  ok("  its credit shows, and no hint to get a macOS voice",
    el("s_voicecredit").hidden === false && el("s_voicecredit").title.includes("Pocket TTS by Kyutai") && el("s_voicehint").hidden === true);
  calls.length = 0; await pickVoice("c.Samantha");
  ok("voice: choosing one saves it at once", calls.some(c => c[0] === "/api/voices" && JSON.parse(c[1]).id === "c.Samantha"), calls);
  calls.length = 0; await pickVoice("");
  ok("  automatic again sends no voice", calls.some(c => c[0] === "/api/voices" && JSON.parse(c[1]).id === null), calls);
  // Try: it says what is really happening, never "Playing…" through a download.
  TRYING = 7;                    // followed already: no polling loop in the smoke
  const tl = () => el("s_tryprept").textContent, tb = el("s_voicetry");
  paintTry({ n: 7, voice: "Jean", state: "downloading", pct: null, left: null, error: null, downloaded: true });
  ok("try: downloading, before the first percentage: the line, its track moving", tb.textContent === "Downloading…" && tb.disabled === true
    && el("s_tryprep").hidden === false && el("s_tryprep").classList.contains("unknown") && tl() === "Downloading Jean, once only…", tl());
  paintTry({ n: 7, voice: "Jean", state: "downloading", pct: 42, left: 28, error: null, downloaded: true });
  ok("  how far and time left on the line, the button's words the same (its width never moves)",
    tb.textContent === "Downloading…" && tl() === "Downloading Jean, once only: 42% · about 30 s left"
    && !el("s_tryprep").classList.contains("unknown") && el("s_voiceprep").hidden === true, tl());
  paintTry({ n: 7, voice: "Jean", state: "downloading", pct: 97, left: 4, error: null, downloaded: true });
  ok("  under 10 s, and minutes when it is long", tl().endsWith("97% · under 10 s left")
    && (paintTry({ n: 7, voice: "Jean", state: "downloading", pct: 10, left: 130, error: null, downloaded: true }), tl().endsWith("10% · about 3 min left")), tl());
  paintTry({ n: 7, voice: "Jean", state: "downloading", pct: 50, left: null, error: null, downloaded: true });
  ok("  no time left while it is not known", tl() === "Downloading Jean, once only: 50%", tl());
  paintTry({ n: 7, voice: "Jean", state: "loading", pct: null, left: null, error: null, downloaded: true });
  ok("  then starting: the line goes", tb.textContent === "Starting…" && el("s_tryprep").hidden === true);
  paintTry({ n: 7, voice: "Jean", state: "playing", pct: null, left: null, error: null, downloaded: true });
  ok("  Playing only while it plays", tb.textContent === "Playing…");
  paintTry({ n: 7, voice: "Jean", state: "done", pct: null, left: null, error: null, downloaded: true });
  ok("  then Try, no Ready message", tb.textContent === "Try" && tb.disabled === false && el("s_tryprep").hidden === true);
  TRYFAIL = { n: 7, voice: "Jean <x>", state: "failed", error: "no connection to Hugging Face", downloaded: true }; paintTry(null);
  ok("try: a failed download says why on the line, with Try again, no toast",
    el("s_tryprep").hidden === false && el("s_tryprep").classList.contains("failed")
    && el("s_tryprept").innerHTML.startsWith("Couldn't download Jean &lt;x&gt;: no connection to Hugging Face. A macOS voice speaks until it's here.")
    && el("s_tryprept").innerHTML.includes('onclick="tryVoice()">Try again</button>') && tb.textContent === "Try", el("s_tryprept").innerHTML);
  TRYING = null;
  await tryVoice();
  ok("try: a macOS voice, said before the answer: Try after it, the old failure gone", tb.textContent === "Try" && tb.disabled === false
    && TRYFAIL === null && el("s_tryprep").hidden === true && calls.some(c => c[0] === "/api/voices" && JSON.parse(c[1]).action === "try"));
  // Settings > models: one this Claude Code runs another in place of is marked, and back when it is not.
  const opt = (value, textContent) => ({ value, textContent, disabled: false, dataset: {} });
  el("s_vmodel").options = [opt("haiku", "Haiku \u00b7 Recommended"), opt("sonnet", "Sonnet")];
  markModels(["sonnet"]);
  const [vhk, vs2] = el("s_vmodel").options;
  ok("models: one not available is disabled and says so", vs2.disabled && vs2.textContent === "Sonnet (not available)", vs2);
  ok("  the others as they were", !vhk.disabled && vhk.textContent === "Haiku \u00b7 Recommended");
  markModels([]);
  // Settings > Recording > Speech model: the size, English only, a download's progress.
  el("s_english").checked = true; setSpeech("small", true);
  ok("speech: Small, English: its hint", el("s_speech").value === "small" && el("s_speechhint").title.includes("Least memory"),
    el("s_speechhint").title);
  // It stored whatever the row showed, even untouched, which is how Large became a saved
  // choice on the user's Mac (2026-10-11). Now only a pick is sent.
  ok("  untouched: no speech model sent, so the default is never stored",
    formSettings().speech_model === undefined, formSettings());
  setSpeech("standard");
  ok("  picked: sent, and the hint follows", formSettings().speech_model === "standard"
    && el("s_speechhint").title.includes("balance"), [formSettings().speech_model, el("s_speechhint").title]);
  // On an 8 GB Mac: why Small, and what Standard costs there.
  el("s_english").checked = true; SPEECHSET = false; SPMAC = { default: "small", picked: false, memory: 8 };
  setSpeech("small", true);
  ok("speech, 8 GB Mac: Small, chosen for its memory, says so", el("s_speechhint").title
    === "Chosen for this Mac's 8 GB. About 1 GB of memory; more mistakes in a busy room.", el("s_speechhint").title);
  setSpeech("standard");
  ok("  Standard picked there: fewer mistakes, and heavy for this Mac", el("s_speechhint").title
    === "Fewer mistakes. About 3 GB of memory: heavy for this Mac.", el("s_speechhint").title);
  setSpeech("small");
  ok("  Small picked by hand: just what it is, not chosen for this Mac", el("s_speechhint").title
    === "Least memory, about 1 GB. More mistakes in a busy room.", el("s_speechhint").title);
  SPMAC = { default: "small", picked: true, memory: 8 }; SPEECHSET = false; setSpeech("small", true);
  ok("  a pick saved before: the same", el("s_speechhint").title.startsWith("Least memory, about 1 GB"));
  SPMAC = { default: "standard", picked: false, memory: 48 }; SPEECHSET = false; setSpeech("standard", true);
  ok("  a bigger Mac: as before", el("s_speechhint").title === "The balance of memory and accuracy.");
  setSpeech("small", true);
  ok("  and Small there: about 1 GB, not under 1 GB", el("s_speechhint").title
    === "Least memory, about 1 GB. More mistakes in a busy room.", el("s_speechhint").title);
  SPMAC = {}; setSpeech("standard");             // as the checks below found it: Standard, picked
  el("s_english").checked = false; setSpeech(undefined, true);
  ok("  other languages too: Standard's, saying why, and the size kept for English again",
    el("s_speech").value === "standard" && el("s_speechhint").title.includes("Small is English only")
    && formSettings().english_only === false, el("s_speechhint").title);
  paintSpeech({ model: "Standard, English", ready: false, bytes: 2471596080, download: { state: "downloading", done: 1e9, total: 4e9 } });
  ok("  downloading: its progress", el("s_speechdl").hidden === false
    && el("s_speechdl").textContent === "Downloading Standard, English… 25%. Used from your next session.", el("s_speechdl").textContent);
  paintSpeech({ model: "Standard, English", ready: false, bytes: 2471596080, download: null });
  ok("  not here yet: when, and its size", el("s_speechdl").textContent === "Standard, English downloads when a session starts (2.5 GB, once).",
    el("s_speechdl").textContent);
  paintSpeech({ model: "Standard, English", ready: true, bytes: 2471596080, download: { state: "ready", done: 1, total: 1 } });
  ok("  here: nothing to say", el("s_speechdl").hidden === true);
  paintSpeech({ model: "Standard, English", ready: true, bytes: 2471596080, download: null,
    moved: "Large, English wrote no capitals or full stops, so it is no longer offered: Standard, English now." });
  ok("  a model that was moved: Settings says so", el("s_speechdl").hidden === false
    && el("s_speechdl").textContent.startsWith("Large, English wrote no capitals"), el("s_speechdl").textContent);
  ok("  available again: its own label back", !vs2.disabled && vs2.textContent === "Sonnet", vs2);
  // Settings, one page in the one window: the sections listed, one open in place, back to the list, then out
  let shut = 0; settings.close = () => { shut++ };
  setHome();
  ok("settings: the sections listed, titled Settings", el("setmain").hidden === true && el("setnav").hidden === false && el("settitle").textContent === "Settings");
  setPane("recording");
  ok("  a section opens in place (its rows from the page)", el("setmain").hidden === false && el("setnav").hidden === true && SETP === "recording");
  setBack();
  ok("  back: to the list, Settings still open", el("setnav").hidden === false && SETP === null && shut === 0);
  setBack();
  ok("  back again: out of Settings", shut === 1);
  el("s_autoaccept").checked = true;
  ok("settings: starting without asking is saved with the rest (auto_accept)", formSettings().auto_accept === true);
  el("s_autoaccept").checked = false;
  const bad = out.filter(x => !x[1]);
  for (const [k, v, d] of out) console.log("  " + k.padEnd(52) + (v ? "OK" : "FAIL" + (d ? "  " + JSON.stringify(d) : "")));
  await new Promise(r => setTimeout(r, 0));     // a rejection landing just as the checks end still counts
  const nbad = bad.length + loose.length;
  console.log(nbad ? "FAILURES" : "ALL PASS"); process.exit(nbad ? 1 : 0);
})();`);
