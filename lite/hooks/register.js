// Standby inside Claude Code. The Standby app does the hearing, speaking and remembering
// (127.0.0.1, its own port); this module is what the person sees here, and the one way
// in and out of this session: a meeting's transcript with the person's prompts (or, set to live, as
// quiet notes as it goes), a call's hand-offs as turns. A summary stays in Standby: never sent here.
import { said, jsonLines, minutes, noteLine, toolWords, joined, helpText, nameArgs, startNote, endNote, settingsText, setArgs } from './words.js'

// A key drawn as a link (VS Code, the desktop): an address nothing opens, answered by the mod
const KEY_LINK = 'https://standby.invalid/'
// Standby's mark, as the app icon draws it small (mac/icons.py, wave_icon small): three white bars on the
// indigo-to-violet rounded square. For VS Code and the desktop, one text line high.
const LOGO = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 94 94"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' +
  '<stop offset="0" stop-color="#5046e5"/><stop offset="1" stop-color="#8b3fe8"/></linearGradient></defs>' +
  '<rect width="94" height="94" rx="22" fill="url(#g)"/><g fill="#fff">' +
  '<rect x="27.5" y="37" width="11" height="20" rx="5.5"/><rect x="41.5" y="28.25" width="11" height="37.5" rx="5.5"/>' +
  '<rect x="55.5" y="34.5" width="11" height="25" rx="5.5"/></g></svg>'      // the pane: only the summary, once, after a meeting or a call (nothing streams here)
const MARK = '#6d63ff'      // Standby's own colour, for a call's mark (a recording is red)
const WARN = '#d29922'      // the band's word that something isn't working (the voice, Standby)
// Claude Code started from inside another (its terminal app carries CLAUDE_CODE_CHILD_SESSION, which
// a new window of that app inherits too) doesn't save its conversation, so Standby can't listen in it
const UNSAVED = 'Standby can\u2019t listen here: this Claude Code doesn\u2019t save its conversation. ' +
  'Quit it and start it with: env -u CLAUDE_CODE_CHILD_SESSION claude'
// What an action says when it worked: a band press shows nothing then (the band itself changes)
const LISTENING = { ask: 'Listening. Claude here gets the transcript with each of your prompts.', live: 'Listening. Claude here hears the meeting as it goes.' }
const OK = new Set(['Standby\u2019s window is open.', LISTENING.ask, LISTENING.live, 'Stopped. The summary comes in a moment.',
  'On a call. Talk; real work comes here.', 'Call ended.', 'Listening again.', 'Paused.', 'Muted.', 'Unmuted.', 'The call is in the mini window.'])
const S = {
  on: false,            // /standby turned it on for this session
  port: 8765,
  cc: '',               // this Claude Code session's id
  st: null,             // the app's state: { app, can, live }
  lines: [],            // the live meeting's lines, newest last
  names: {},            // speaker -> the name the engine knows
  pending: [],          // lines Claude hasn't had yet (before the next batch)
  card: null,           // the ended meeting's or call's card: { session, title, points, gist }
  turn: null,           // the session's running turn id, or null when idle
  stream: null,         // the app's live stream (a spawned curl)
  caption: '',          // the last line said, for the band
  call: null,           // { session } while a call is live
  fromCall: new Set(),  // the words of a call's hand-offs, to mark them in the chat
  toasted: false,       // the one toast, on the first /standby
  unsaved: false,       // this Claude Code doesn't save its conversation (started inside another): Listen can't work
  saidTool: 0,          // when a call's voice was last told what the session is doing
  heard: { you: '', said: '' },   // a call's turn as it goes (the captions)
  said: null,           // the last finished exchange of a call, for the band's second line
  holding: 0,           // inside a tool call of ours: a turn is running, whatever turn.start said
  later: [],            // turns to submit once the running one ends (a hook can't wait on its own turn)
  noted: new Set(),     // meetings this session was told have started (on ask)
  incoming: null,       // { id, kind, expires }: Claude asks to start a call or listening, on the band until answered
  starting: null,       // { kind, since, cancel? }: a call or listening starting, until it's live
  ending: null,         // { kind, since }: ended, until its summary's row is up
  flash: null,          // { mark, color, text, until }: a short line that then goes (declined, no answer, added)
  callState: { phase: '', since: 0, working: false, workingSince: 0, notice: '', retrying: 0, waiting: false },   // the call's voice
  unreach: 0,
  confirm: null,        // End asked on the band: { what, question, yes, no, until }
  keysNow: [],          // the terminal band's actions as last drawn: [[number, what]]
  hintAt: 0,            // when the "press a number" hint first showed (once per session)
  keyUsed: false,       // one of them was pressed: the hint has done its job           // since when Standby hasn't answered while something was going on, or 0
}

// ---------------------------------------------------------------- the app

// Whether this Standby has its compact window
function canWindow() { return !!(S.st && S.st.can && S.st.can.window) }

// What macOS refuses the live meeting or call, or will ask for it (the app's "mic" and "meeting_audio"):
// the line under the top one, in place of the words. The mic before the meeting's sound; a call has no
// meeting sound. A refusal is in WARN, with Settings on the top line; a heads-up is dim, and goes once allowed.
const ACCESS = {
  mic: { denied: 'Standby can’t hear your mic · allow it in Privacy & Security > Microphone',
    ask: 'macOS will ask for your microphone · choose Allow' },
  meeting_audio: { denied: 'Standby can’t hear the meeting’s sound · allow it in Privacy & Security > Screen & System Audio Recording',
    ask: 'macOS will ask for Screen & System Audio Recording · choose Allow; Standby takes only the sound' },
}
function access(live) {
  for (const what of live.kind === 'call' ? ['mic'] : ['mic', 'meeting_audio']) {
    const text = ACCESS[what][live[what]]
    if (text) return { text, refused: live[what] === 'denied' }
  }
  // a folder macOS refuses the call's Claude (its project is in it, and Don't Allow was chosen)
  if (live.folder_denied) return { refused: true,
    text: 'Standby can\u2019t read your ' + live.folder_denied + ' folder \u00b7 allow it in Privacy & Security > Files & Folders' }
  return null
}

// System Settings at the pane macOS refuses (the app picks it), on the user's click
async function openSettings($) {
  const live = S.st && S.st.live
  if (!live) return 'closed'
  const r = await app($, '/api/lite/permission', { session: live.session })
  return r.error || 'closed'
}

// Standby's compact window: the app opens it (or brings it to the front); nothing comes back here
async function openWindow($) {
  if (!S.on) await turnOn($)
  if (!canWindow()) return 'Standby\u2019s window isn\u2019t in this version of Standby.'
  const live = S.st && S.st.live
  const r = await app($, '/api/window', { session: live ? live.session : null, cc: S.cc })
  if (r.error) return r.error
  // In front, every time: macOS (14 on) lets an app in the background raise itself only when the app in
  // front hands it over, and the app in front is this one (the terminal, VS Code). Standby asking alone
  // brought its window up the first time, then never.
  await $.process.run(['open', '-a', 'Standby']).catch(() => {})
  return 'Standby’s window is open.'
}

// The app's port and this launch's token, as it wrote them in ~/.standby (a new app may have moved, or
// started again with a new token): every request carries the token
async function where($) {
  const home = (await $.env.get('HOME')) + '/.standby/'
  const port = await $.fs.read(home + 'ui.port').catch(() => '')
  if (/^\d+$/.test(String(port).trim())) S.port = Number(String(port).trim())
  S.token = String(await $.fs.read(home + 'ui.token').catch(() => '')).trim()
  S.header = home + 'ui.header'
}

async function app($, path, body, again) {
  try {
    const r = await $.http.fetch('http://127.0.0.1:' + S.port + path, body === undefined ? { headers: { 'x-standby-token': S.token || '' } } :
      { method: 'POST', headers: { 'content-type': 'application/json', 'x-standby-token': S.token || '' }, body: JSON.stringify(body) })
    if (r.status === 401 && !again) { await where($); return app($, path, body, true) }    // Standby started again: its new token
    if (r.status === 404) return { error: 'This Standby doesn\u2019t have that yet: update it' }
    return r.ok ? JSON.parse(r.text || '{}') : { error: 'Standby answered ' + r.status }
  } catch (err) {
    return { error: 'Standby isn’t running' }
  }
}

async function refresh($) {
  const st = await app($, '/api/lite/state?cc=' + encodeURIComponent(S.cc))
  // it quit, or is too busy to answer, while something was going on: the band stays, says so, and comes back by itself
  const going = !!(S.starting || S.ending || (S.st && S.st.live) || (S.card && S.card.waiting) || S.unreach)
  S.unreach = st.error === 'Standby isn\u2019t running' && going ? S.unreach || Date.now() : 0
  S.st = st.error ? { app: null, error: st.error } : Object.assign({}, st, { app: true })
  if (S.st.live) S.lastLive = Object.assign({ seenAt: Date.now() / 1000 }, S.st.live)   // what the summary will be of
  else if (!st.error && S.lastLive && !S.lastLive.carded && !S.ending && !(S.card && S.card.session === S.lastLive.session)) {
    // it ended (the voice's goodbye, End in the window, a quiet while): Ending, until its summary's row is up
    S.lastLive.carded = true
    S.ending = { kind: S.lastLive.kind === 'call' ? 'call' : 'listen', since: Date.now() }
    if (S.lastLive.kind !== 'call') void note($, endNote(S.lastLive.title)).catch(() => {})   // so Claude knows
    void cardOf($, S.lastLive.session)
  }
  $.ui.invalidate('ui.render')
}

// The app's live stream: meeting lines, renames, a call's captions and hand-offs. Runs while
// /standby is on; leaving the loop ends curl.
async function listen($) {
  if (S.stream) return
  const url = 'http://127.0.0.1:' + S.port + '/api/lite/stream?cc=' + encodeURIComponent(S.cc)
  // the token from its file (-H @file), never on the command line, where ps would show it
  S.stream = $.process.spawn({ argv: ['curl', '-sN', '--max-time', '0', '-H', '@' + (S.header || ''), url] })
  let rest = ''
  try {
    for await (const chunk of S.stream) {
      if (!S.on) break
      if (chunk.stream !== 'stdout') continue
      const got = jsonLines(rest, chunk.text)
      rest = got.rest
      for (const ev of got.events) await heard($, ev)
      if (S.resync) break
    }
  } catch (err) {
    // the app went away: the state poll says so, and /standby reconnects
  }
  S.stream = null
  if (S.resync && S.on) {
    S.resync = false
    const live = S.st && S.st.live, last = S.lines.length ? S.lines[S.lines.length - 1].i : -1
    if (live && live.kind === 'meeting') {
      const got = await app($, '/api/lite/lines?session=' + encodeURIComponent(live.session) + '&after=' + last)
      for (const l of got.lines || []) await heard($, Object.assign({ meet: 'line', session: live.session }, l))
    }
    void listen($)
  } else if (S.on) {
    // the stream ended (Standby quit or restarted): reconnect once it answers again, so a meeting's
    // lines keep reaching Claude; the state poll keeps the band right meanwhile
    void (async () => {
      for (let i = 0; i < 120 && S.on && !S.stream; i++) {
        await $.clock.sleep(i < 10 ? 1000 : 5000)
        await refresh($).catch(() => {})
        if (S.st && S.st.app) { void listen($); return }
      }
    })().catch(() => {})
  }
}

async function heard($, ev) {
  if (ev.resync) { S.resync = true; return }
  if (ev.deleted) {
    // the meeting or call was deleted in Standby: its summary card goes from the band too, and an
    // Ending waiting for its card stops waiting
    if (S.card && S.card.session === ev.session) S.card = null
    if (S.lastLive && S.lastLive.session === ev.session) { S.lastLive.carded = true; S.ending = null }
    $.ui.invalidate('ui.render')
    return
  }
  if (ev.meet === 'line') {
    S.lines.push(ev); S.pending.push(ev)
    const tail = joined(S.lines.slice(-8)); S.caption = said(tail[tail.length - 1], S.names); S.captionOf = ev.session
    if (S.lines.length > 2000) S.lines.splice(0, S.lines.length - 2000)
    await batch($, ev.session)
  } else if (ev.meet === 'rename') {
    Object.assign(S.names, ev.renamed || {})
  } else if (ev.meet === 'state') {
    await refresh($)
    if (ev.ended) void cardOf($, ev.session, ev.note)
  } else if (ev.meet === 'card' || ev.call === 'card') {
    // the engine made the card: shown here once, as the engine has it; it stays in Standby
    if (!S.card || S.card.session !== ev.session) void cardOf($, ev.session)
  } else if (ev.call === 'state') {
    const cs = S.callState, now = Date.now()
    if (ev.phase !== cs.phase) { cs.phase = ev.phase; cs.since = now }
    if (!!ev.working !== cs.working) { cs.working = !!ev.working; cs.workingSince = now }
    cs.notice = ev.notice || ''
    cs.waiting = !!ev.waiting
    cs.retrying = ev.retrying_since ? ev.retrying_since * 1000 : 0
    if (S.starting && S.starting.voice && (ev.phase === 'speaking' || ev.phase === 'ended')) S.starting = null
    if (ev.phase === 'ended') { S.call = null; S.caption = ''; S.said = null; S.last = null; S.heard = { you: '', said: '' }; void cardOf($, ev.session) }
    // a turn done (the voice back to listening): the band's second line takes the exchange, once
    else if (ev.phase === 'listening' && (S.heard.you || S.heard.said)) { S.said = Object.assign({}, S.heard); S.heard = { you: '', said: '' } }
    await refresh($)
  } else if (ev.call === 'caption') {
    // the voice ready: its greeting heard, or they spoke first; the call shows from here
    if (S.starting && S.starting.voice && (ev.said || (ev.you && !/^\[CALL START\]/.test(String(ev.you))))) S.starting = null
    S.caption = ev.said || ev.you || S.caption
    if (ev.you) S.heard.you = String(ev.you).replace(/^\[CALL START\]\s*/, '')     // the greeting's hidden marker: never shown
    if (ev.said) S.heard.said = ev.said
    // the band's second line: the latest words, whoever said them, as soon as they're heard
    if (ev.said || ev.you) { S.last = ev.said ? { voice: true, text: ev.said } : { voice: false, text: ev.you }; $.ui.invalidate('ui.render') }
  } else if (ev.call === 'handoff') {
    // The person's own words, said in a call: real work for this session, never stopping what it's on
    // (Claude Code takes it into a running turn). Standby hears whether it really got here
    S.fromCall.add(ev.text.trim())
    void handOn($, ev)
  } else if (ev.call === 'summary') {
    // After a call, the user chose to add it here: what it discussed that this session didn't know yet.
    // A quiet note, read with the next prompt: no turn of its own
    await note($, ev.text)
  } else if (ev.call === 'abort') {
    if (S.turn) await $.turn.abort({ turnId: S.turn })
  }
  $.ui.invalidate('ui.render')
}

// The engine decides when a batch is due (its push policy); each one is a quiet note here
async function batch($, session, all) {
  const b = await app($, '/api/lite/batch?session=' + encodeURIComponent(session) + (all ? '&all=1' : ''))
  if (!b.text) return ''
  if (!all) await note($, b.text)
  await app($, '/api/lite/batch/sent', { session, through: b.through })
  S.pending = S.pending.filter(l => l.i > b.through)
  return b.text
}

// The ended meeting's card, once the engine has made it
async function cardOf($, session, note) {
  // at once: what ended, "Summarizing…" where its points will be (the summary takes a few seconds)
  if (!S.card || S.card.session !== session) {
    S.ending = null                                         // the summary's row takes over from Ending
    const l = S.lastLive && S.lastLive.session === session ? S.lastLive : {}
    const secs = l.recorded_s != null ? l.recorded_s : l.started ? (l.seenAt || Date.now() / 1000) - l.started : null
    S.card = { session, kind: l.kind || null, title: l.title || (l.kind === 'call' ? 'Call' : 'the meeting'), duration_s: secs, waiting: true, shownAt: Date.now(), note: note || null }
    $.ui.invalidate('ui.render')
  } else {
    if (note && !S.card.note) { S.card.note = note; $.ui.invalidate('ui.render') }     // its word came after the card
    if (!S.card.waiting) return                             // this one's summary is here already
  }
  try {                                                     // ends quietly with the session (the mod unloaded)
  for (let i = 0; i < 60; i++) {
    const c = await app($, '/api/lite/card?session=' + encodeURIComponent(session))
    if (c.title || c.points || c.gist) {
      const said = S.card && S.card.session === session ? S.card.note : null, kind = S.card && S.card.session === session ? S.card.kind : null
      S.card = Object.assign({ session, shownAt: Date.now(), kind }, c, { note: c.note || said || null }); S.lines = []; S.pending = []
      // shown on the band for a while, then gone (or at the next prompt): everything is kept, nothing to choose
      void $.clock.sleep(SUMMARY_MS + 100).then(() => $.ui.invalidate('ui.render')).catch(() => {})
      $.ui.invalidate('ui.render')
      return
    }
    await $.clock.sleep(2000)
  }
  } catch (err) {}
}

// A call's hand-off, into the session now, and word back of whether it entered: the voice says it
// went only once it has
async function handOn($, ev) {
  // the session busy (a turn running): Claude Code queues it and starts it when that turn ends; said now,
  // so the voice knows it waits rather than went missing
  if (S.turn || S.holding) await app($, '/api/lite/progress', { session: ev.session, id: ev.id || '', kind: 'queued', text: '' })
  let r
  try { r = await $.prompt.submit({ text: ev.text, asUser: true }) } catch (err) { r = { drop: String((err && err.message) || err) } }
  const got = !!r && !r.drop
  await app($, '/api/lite/progress', { session: ev.session, id: ev.id || '', kind: got ? 'received' : 'undelivered', text: got ? '' : String((r && r.drop) || '') })
}

// A turn Standby starts: now if the session is idle, else once the running turn ends
function submit($, text, asUser) {
  if (S.turn || S.holding) { S.later.push({ text, asUser: !!asUser }); return }
  $.prompt.submit(asUser ? { text, asUser: true } : { text })
}

// A user-role note Claude reads and the person doesn't see as typed: no turn of its own
async function note($, text) {
  await $.session.append({ message: { type: 'user', content: [{ type: 'text', text }] } })
}

// ---------------------------------------------------------------- the actions (buttons, /standby, tools)

async function turnOn($, quiet) {
  S.on = true
  S.cc = await $.session.id()
  await ensureApp($, quiet)
  void listen($)
  return S.st.app ? 'Standby is on for this session.' : 'Standby isn’t running, and it didn’t open.'
}

// Standby the app, running: opened in the background (no window) when it's asked for, never just
// because Claude Code started (quiet). Its port is read afresh, as a new app may have moved.
async function ensureApp($, quiet) {
  await where($)
  await refresh($)
  if (!S.st.app && !quiet) {
    await $.process.run(['open', '-g', '-a', 'Standby', '--args', '--background'])   // its window stays closed until asked for
    for (let i = 0; i < 30; i++) {             // up to about 15 s while it starts
      await where($)
      await refresh($)
      if (S.st.app) break
      // waited in a $ call: a hook's own time (10 s) would run out on clock sleeps while the app starts
      await $.process.run(['/bin/sleep', '0.5']).catch(() => {})
    }
    if (S.st.app) void listen($)
  }
  return !!S.st.app
}

// how: "ask" or "live", else the meeting setting in Standby's Settings
async function startListening($, how) {
  if (S.unsaved) return UNSAVED
  if (!(S.st && S.st.live && S.st.live.kind === 'meeting')) startingNow($, 'listen')
  if (!S.on) await turnOn($)
  else if (!(await ensureApp($))) return startFailed($, 'listen', 'Standby isn\u2019t running, and it didn\u2019t open.')
  void listen($)
  let r = await app($, '/api/lite/listen', { cc: S.cc, cwd: await $.session.cwd(), ...(how ? { pass: how } : {}) })
  if (r.error === DOWN) { const live = await stillStarting($, 'listen'); if (live) r = { session: live.session, pass: live.pass } }
  if (r.setup) r = await setupDone($, 'listen')
  if (r.cancelled) return setupCancelled($, 'listen')
  if (r.error) return startFailed($, 'listen', r.error)
  const cancelled = S.starting && S.starting.cancel
  S.starting = null
  S.card = null; S.lines = []; S.pending = []
  if (S.captionOf && r.session && S.captionOf !== r.session) S.caption = ''   // never an earlier meeting's words under this one
  await refresh($)
  if (cancelled) { await stopListening($); return 'Stopped.' }      // End pressed while it was starting
  const live = S.st && S.st.live, pass = r.pass || (live && live.pass) || 'live'
  // on ask, one line so Claude knows there is a meeting to ask about (its transcript comes later)
  if (pass === 'ask' && !S.noted.has(r.session)) { S.noted.add(r.session); await note($, startNote(live && live.title)) }
  return LISTENING[pass] || LISTENING.live
}

async function stopListening($) {
  const live = S.st && S.st.live
  if (!live || live.kind !== 'meeting') return 'Nothing is recording.'
  // set to live, what was said since the last batch goes too; on ask, it waits to be asked
  if (live.pass !== 'ask') {
    const rest = await batch($, live.session, true)
    if (rest) await note($, rest)
  }
  S.ending = { kind: 'listen', since: Date.now() }
  $.ui.invalidate('ui.render')
  const r = await app($, '/api/lite/stop', { session: live.session })
  if (r.error) S.ending = null
  await refresh($)
  return r.error || 'Stopped. The summary comes in a moment.'
}

async function pause($) {
  const live = S.st && S.st.live
  if (!live) return 'Nothing is live.'
  const r = await app($, live.paused ? '/api/lite/resume' : '/api/lite/pause', { session: live.session })
  await refresh($)
  return r.error || (live.paused ? 'Listening again.' : 'Paused.')
}

// The call into the mini window in the corner, as the window's Mini window button: calls only
async function toOrb($) {
  const live = S.st && S.st.live
  if (!live || live.kind !== 'call') return 'No call is live.'
  const r = await app($, '/api/lite/orb', { cc: S.cc })
  return r.error || 'The call is in the mini window.'
}

async function mute($) {
  const live = S.st && S.st.live
  if (!live || live.kind !== 'call') return 'No call is live.'
  const r = await app($, '/api/lite/mute', { session: live.session, muted: !live.muted })
  await refresh($)
  return r.error || (live.muted ? 'Unmuted.' : 'Muted.')
}

// /standby name Speaker 1 Nadia: the speaker as the meeting labels them, then the name
async function nameSpeaker($, args) {
  const live = S.st && S.st.live
  const session = live && live.kind === 'meeting' ? live.session : S.card ? S.card.session : null
  if (!session) return 'No meeting to name anyone in.'
  let labels = speakerLabels()
  if (!labels.length) {
    const got = await app($, '/api/lite/lines?session=' + encodeURIComponent(session) + '&after=0')
    for (const l of got.lines || []) S.lines.push(l)
    Object.assign(S.names, got.renamed || {})
    labels = speakerLabels()
  }
  const m = nameArgs(args, labels)
  if (!m) return labels.length ? 'Name whom? /standby name ' + labels[0].label + ' Nadia (' + labels.map(x => x.label).join(', ') + ')'
    : 'Nobody has spoken yet.'
  const r = await app($, '/api/speaker', { id: session, spk: m.speaker, name: m.name })
  if (r.error) return r.error
  S.names[m.speaker] = m.name
  $.ui.invalidate('ui.render')
  return m.label + ' is ' + m.name + ' now. Standby will know their voice.'
}
// who has spoken, as the meeting labels them now: [{ speaker, label }]
function speakerLabels() {
  const seen = new Map()
  for (const l of S.lines) if (l.speaker != null && !seen.has(String(l.speaker))) seen.set(String(l.speaker), S.names[l.speaker] || l.name || 'Speaker ' + l.speaker)
  return [...seen].map(([speaker, label]) => ({ speaker, label }))
}

// A band button: the action, and its answer as a toast only when it didn't happen
// What ends something (a call, listening) asks once, on the band itself (never Claude Code's own question
// dialog): the row becomes the question, with its two answers, for CONFIRM_MS, then it's as it was
const ENDS = { endCall: ['End the call?', 'End', 'Keep talking'], cancelStart: ['End the call?', 'End', 'Keep talking'],
  stopListening: ['Stop listening?', 'Stop', 'Keep listening'] }
const CONFIRM_MS = 10000
const DIV = '|'                         // in a band's actions: a quiet divider, the call's own from the windows
const HINT_MS = 60000                   // how long the "press a number" hint shows, the first time

async function press($, what, sure) {
  if (ENDS[what] && !sure) {
    const [question, yes, no] = what === 'cancelStart' && S.starting && S.starting.kind !== 'call' ? ENDS.stopListening : ENDS[what]
    const c = S.confirm = { what, question, yes, no, until: Date.now() + CONFIRM_MS }
    void $.clock.sleep(CONFIRM_MS).then(() => { if (S.confirm === c) S.confirm = null; $.ui.invalidate('ui.render') }).catch(() => {})
    $.ui.invalidate('ui.render')
    return 'asked'
  }
  if (what === 'confirmYes' || what === 'confirmNo') {
    const c = S.confirm
    S.confirm = null
    $.ui.invalidate('ui.render')
    return c && what === 'confirmYes' ? press($, c.what, true) : 'closed'
  }
  const r = what === 'startListening' ? await startListening($) : what === 'startCall' ? await startCall($)
    : what === 'pause' ? await pause($) : what === 'stopListening' ? await stopListening($)
    : what === 'mute' ? await mute($) : what === 'openWindow' ? await openWindow($) : what === 'toOrb' ? await toOrb($)
    : what === 'endCall' ? await endCall($) : what === 'openSettings' ? await openSettings($)
    : what === 'cancelStart' ? (S.starting && S.starting.voice ? (S.starting = null, void endCall($))     // the call is up: end it
      : S.starting && (S.starting.cancel = true, S.ending = { kind: S.starting.kind, since: Date.now() }),
      $.ui.invalidate('ui.render'), 'closed')
    : what === 'closeSummary' ? (S.card && (S.card.seen = true), $.ui.invalidate('ui.render'), 'closed')
    : what === 'addSummary' ? await addSummary($)
    : what === 'incomingAccept' ? await answerIncoming($, 'accept')
    : what === 'incomingDecline' ? await answerIncoming($, 'decline')
    : what === 'incomingAlways' ? await answerIncoming($, 'always')
    : 'No such Standby action'

  if (!OK.has(r) && r !== 'closed' && r !== 'asked') $.ui.toast(r)
  return r
}

async function startCall($) {
  startingNow($, 'call')
  if (!S.on) await turnOn($)
  else if (!(await ensureApp($))) return startFailed($, 'call', 'Standby isn\u2019t running, and it didn\u2019t open.')
  void listen($)
  let r = await app($, '/api/lite/call', { cc: S.cc, cwd: await $.session.cwd() })
  if (r.error === DOWN) { const live = await stillStarting($, 'call'); if (live) r = { session: live.session } }
  if (r.setup) r = await setupDone($, 'call')
  if (r.cancelled) return setupCancelled($, 'call')
  if (r.error) return startFailed($, 'call', r.error)
  const cancelled = S.starting && S.starting.cancel
  const since = (S.starting && S.starting.since) || Date.now()
  S.starting = null
  if (r.already) { $.ui.invalidate('ui.render'); return 'You\u2019re already on this call. To end it, say goodbye or use /standby end.' }
  // still Connecting until the voice is ready: its greeting starts, or they speak first (or a while passes)
  if (!cancelled) S.starting = { kind: 'call', since, voice: true, until: Date.now() + VOICE_READY_MS }
  S.call = { session: r.session }
  S.caption = ''
  S.callState = { phase: 'listening', since: Date.now(), working: false, workingSince: 0, notice: '', retrying: 0, waiting: false }
  await refresh($)
  if (cancelled) { await endCall($); return 'Call ended.' }            // End pressed while it was connecting
  return 'On a call. Talk; real work comes here.'
}

// A start can take longer than a request waits: on a Mac's first call or meeting macOS asks for the
// microphone, and the app waits for the answer (72 s on a fresh Mac, 2026-10-27), so the request fails
// though nothing is wrong. The app still answering means it's starting, not down: Connecting goes on
// until it's live (or the user ends it, or a while passes).
const DOWN = 'Standby isn\u2019t running'
async function stillStarting($, kind) {
  for (let i = 0; i < 240; i++) {                // up to about 2 minutes
    await refresh($)
    if (!(S.st && S.st.app) || !S.starting || S.starting.cancel) return null
    const live = S.st.live
    if (live && live.kind === (kind === 'call' ? 'call' : 'meeting')) return live
    // waited in a $ call: a hook's own time (10 s) would run out on clock sleeps
    await $.process.run(['/bin/sleep', '0.5']).catch(() => {})
  }
  return null
}

// A first call or meeting: Standby holds the start and asks in its own setup window (the mic, a meeting's
// sound), then starts it itself, after a Quit & Reopen too. The band says to finish there, and the start
// goes on once it's live; the window closed, or End here, cancels it.
const SETUP_MS = 15 * 60 * 1000          // as long as Standby holds a start for its setup
async function setupDone($, kind) {
  if (!S.starting) startingNow($, kind)
  S.starting.setup = true
  $.ui.invalidate('ui.render')
  const want = kind === 'call' ? 'call' : 'meeting', end = Date.now() + SETUP_MS
  while (Date.now() < end) {
    if (!S.starting) return { cancelled: true }
    if (S.starting.cancel) { await app($, '/api/setup', { do: 'cancel' }); return { cancelled: true } }
    await refresh($)
    if (!(S.st && S.st.app)) await where($)         // Standby reopening (Quit & Reopen): its new port and token
    else {
      const live = S.st.live, held = S.st.setup
      if (live && live.kind === want) return { session: live.session, pass: live.pass }
      if (held && held.phase === 'failed') { void app($, '/api/setup', { do: 'close' }); return { error: held.error || 'it didn\u2019t start' } }
      if (!held) return { cancelled: true }         // the window closed (or Cancel): nothing is held now
    }
    // waited in a $ call: a hook's own time (10 s) would run out on clock sleeps
    await $.process.run(['/bin/sleep', '0.5']).catch(() => {})
  }
  return { error: 'setup wasn\u2019t finished' }
}
function setupCancelled($, kind) {
  S.starting = null
  S.ending = null
  flash($, '\u25c9', null, (kind === 'call' ? 'Call cancelled' : 'Listening cancelled') + ' \u00b7 setup wasn\u2019t finished', 6000)
  return kind === 'call' ? 'Call cancelled: setup wasn\u2019t finished.' : 'Listening cancelled: setup wasn\u2019t finished.'
}

const VOICE_READY_MS = 45000            // the most Connecting waits for the voice's greeting before showing the call

// The band says what's happening while a call or listening starts, from the click on: connecting, with
// moving dots and the seconds, until it's live; or why it couldn't, for a few seconds
function startingNow($, kind) {
  S.starting = kind ? { kind, since: Date.now() } : null
  $.ui.invalidate('ui.render')
}
function startFailed($, kind, why) {
  S.starting = null
  flash($, '\u25c9', null, (kind === 'call' ? 'Couldn\u2019t start the call \u00b7 ' : 'Couldn\u2019t start listening \u00b7 ') + why, 6000)
  return why
}
// A short line on the band that then goes: declined, no answer, added, a start that failed
function flash($, mark, color, text, ms) {
  S.flash = { mark, color, text, until: Date.now() + (ms || 3000) }
  $.ui.invalidate('ui.render')
  void $.clock.sleep((ms || 3000) + 100).then(() => $.ui.invalidate('ui.render')).catch(() => {})
}

// Add to Claude Code: Standby writes what the call discussed that this session doesn't know yet and puts it
// in as a quiet note (the app's /api/lite/card/add, a few seconds). Done, or nothing new: the card goes.
// Not ready, or it couldn't: the card stays, to try again.
async function addSummary($) {
  const c = S.card; if (!c || c.adding) return 'closed'
  c.adding = Date.now(); $.ui.invalidate('ui.render')
  let r; try { r = await app($, '/api/lite/card/add', { session: c.session }) } catch (e) { r = { error: 'Standby isn\u2019t answering' } }
  c.adding = false
  const nothing = r && r.error === 'There was nothing new for Claude Code in that call'
  if (r && (r.ok || nothing)) c.seen = true
  if (r && r.ok) flash($, '\u2713', 'green', 'Added to Claude Code')
  $.ui.invalidate('ui.render')
  return r && r.ok ? 'Added to Claude Code' : (r && r.error) || 'Couldn\u2019t add it: try again'
}

async function endCall($) {
  const live = S.st && S.st.live
  if (!live || live.kind !== 'call') return 'No call is live.'
  S.ending = { kind: 'call', since: Date.now() }
  $.ui.invalidate('ui.render')
  const r = await app($, '/api/lite/call/end', { session: live.session })
  if (r.error) S.ending = null
  S.call = null
  await refresh($)
  return r.error || 'Call ended.'
}

// After a meeting or a call its summary is on the band this long (or until the next prompt), then gone
const SUMMARY_MS = 30000
const SUMMARIZING_MS = 120000          // while the summary is on its way (the engine gives up after about that)
// A call's summary offers Add to Claude Code or Forget (the user, 2026-10-21): it stays until one is chosen
const choosing = () => !!(S.card && S.card.kind === 'call' && !S.card.waiting && !S.card.seen)
const summaryShown = () => !!(S.card && S.card.shownAt && !S.card.seen && (choosing() || Date.now() - S.card.shownAt < (S.card.waiting ? SUMMARIZING_MS : SUMMARY_MS)))

// Standby's settings, by name: listed (no name), or one changed; the app checks the value
async function settings($, name, value) {
  if (!S.on) await turnOn($, true)
  if (!name) {
    const r = await app($, '/api/settings/named')
    return r.error || settingsText(r.settings)
  }
  if (value === undefined || value === null || String(value).trim() === '') return 'Say what to set ' + name + ' to: /standby settings lists the choices.'
  const r = await app($, '/api/settings', { name, value: String(value) })
  if (r.error) return r.error
  // The setting is for new meetings; a meeting already running here switches with it
  const live = S.st && S.st.live
  if (r.name === 'transcript' && live && live.kind === 'meeting') {
    const m = await app($, '/api/lite/pass', { session: live.session, pass: r.value })
    if (!m.error) return 'transcript is now ' + r.value + ', for this meeting too.'
  }
  return r.name + ' is now ' + r.value + '.'
}

// A meeting's transcript for Claude, when it asks (the meeting tool): this session's latest
// meeting, live or ended, all of it
async function meetingText($) {
  if (!S.on) await turnOn($, true)
  const r = await app($, '/api/lite/transcript?cc=' + encodeURIComponent(S.cc))
  return r.error || r.text
}

// Claude starting a call or listening on its own (its tools): it rings on the band (Accept, Decline,
// Always accept), and nothing starts without the user's Accept, or their Always accept from before: the
// mic never opens on Claude's word alone. Typed /standby call and /standby
// listen, and the band's buttons, are the user's own choice: not asked.
const ASK_FIRST = {
  call: ['call', 'The user declined the call. Nothing was started.'],
  listen: ['meeting', 'The user declined listening. Nothing was started.'],
}
const ASK_WAIT_S = 31                     // the app declines it unanswered after 30 s
// Claude asking for a call or listening: { go: true } to start it now (nothing to ask: already on), { accepted:
// true } once they said yes (it starts on the band, the tool answers at once), or { result } for Claude
async function askToStart($, act) {
  const [kind] = ASK_FIRST[act]
  const live = S.st && S.st.live
  if (live && live.kind === kind) return { go: true }   // already on: nothing new starts, nothing to ask
  if (act === 'listen' && S.unsaved) return { result: UNSAVED }   // it couldn't listen here anyway: never asked for nothing
  if (!S.on) await turnOn($)
  else await ensureApp($)
  const r = await app($, '/api/lite/incoming', { cc: S.cc, kind: act })
  if (r.error) return { result: r.error }
  if (r.answer === 'accept') return { accepted: true }
  if (!r.id) return { result: 'Declined' }
  // On the band until they answer. The wait is one request the app holds (a hook's own time is short, and a
  // wait inside a $ call isn't counted): never a sleep loop, which would outlast the hook's budget.
  S.incoming = { id: r.id, kind: act, expires: Date.now() + 30000 }
  $.ui.invalidate('ui.render')
  let a
  try {
    a = await app($, '/api/lite/incoming?id=' + encodeURIComponent(r.id) + '&wait=' + ASK_WAIT_S)
  } finally {
    if (S.incoming && S.incoming.id === r.id) S.incoming = null
    $.ui.invalidate('ui.render')
  }
  if (a.answer === 'accept') return { accepted: true }
  const call = act === 'call'
  if (a.expired) {
    flash($, '\u25c9', null, call ? 'No answer \u00b7 the call wasn\u2019t started' : 'No answer \u00b7 listening wasn\u2019t started')
    return { result: call ? 'No answer: the call wasn\u2019t started' : 'No answer: listening wasn\u2019t started' }
  }
  flash($, '\u25c9', null, call ? 'Call declined' : 'Listening declined')
  return { result: 'Declined' }
}

// The band's answer to Claude's request: Accept, Decline or Always accept
async function answerIncoming($, answer) {
  const inc = S.incoming
  if (!inc) return 'closed'
  S.incoming = null
  if (answer !== 'decline') S.starting = { kind: inc.kind === 'call' ? 'call' : 'listen', since: Date.now() }     // connecting, at once
  $.ui.invalidate('ui.render')
  const r = await app($, '/api/lite/incoming/answer', { id: inc.id, answer })
  return r.error || 'closed'
}

// Anything on the band whose dots or seconds move
function moving() {
  const cs = S.callState, live = S.st && S.st.live
  return !!(S.incoming || S.starting || S.ending || S.unreach || S.flash || (S.card && (S.card.waiting || S.card.adding))
    || (live && live.kind === 'call' && (cs.phase === 'thinking' || cs.working || cs.retrying)))
}
const DOTS = ['.', '. .', '. . .']

// ---------------------------------------------------------------- the hooks

// The band itself (above): what's on it now, one stage at a time
async function band($, e, next) {
  const live = S.st && S.st.app && S.st.live
  const waiting = !live && summaryShown()
  if (S.flash && Date.now() > S.flash.until) S.flash = null
  if (S.starting && S.starting.voice && Date.now() > S.starting.until) S.starting = null
  if (!S.on || (!live && !waiting && !S.incoming && !S.starting && !S.ending && !S.flash && !S.unreach)) return next(e)
  const { Box, Text, Button, Markdown, Svg } = $.ui.resolve(e)
  const term = e.surface === 'terminal'
  const mark = term ? [] : [Svg({ source: LOGO, alt: 'Standby', width: 16, height: 16 })]
  const fixed = (text, props) => Box({ flexShrink: 0, children: [Text(Object.assign({ children: text }, props || {}))] })
  // text that takes what room is left and is cut with … rather than wrapped
  const fit = (text, props) => Box({ flexGrow: 1, flexShrink: 1, minWidth: 0, overflow: 'hidden',
    children: [Text(Object.assign({ wrap: 'truncate-end', children: text }, props || {}))] })
  const line = kids => Box({ flexDirection: 'row', flexWrap: 'nowrap', alignItems: 'center', columnGap: 1, children: kids })
  // the terminal's actions: "1 pause · 2 end | 3 window", each number in Standby's colour and the word bold,
  // only the separators dim. Each is its number pressed in an empty prompt, no Enter: Claude Code's own band
  // hotkey, so nothing is typed and nothing reaches the chat (or Enter on it with the band focused)
  // a list's groups, the call's own actions then the windows: DIV between them, dropped where nothing follows
  const groups = list => list.filter((x, i) => x !== DIV || (i > 0 && i < list.length - 1 && list[i - 1] !== DIV))
  const digits = list => {
    list = groups(list)
    const num = new Map()                // numbered across the whole band, in the order drawn
    for (const k of list) if (k !== DIV) { S.keysNow.push([String(S.keysNow.length + 1), k[3]]); num.set(k, S.keysNow.length) }
    return Box({ flexDirection: 'row', flexWrap: 'nowrap', flexShrink: 0, children:
      list.flatMap((k, i) => k === DIV ? [Text({ dimColor: true, children: ' | ' })]
        : [...(i && list[i - 1] !== DIV ? [Text({ dimColor: true, children: ' \u00b7 ' })] : []),
          Button({ key: k[0], label: k[2], plain: true, hotkey: String(num.get(k)), onPress: async () => { S.keyUsed = true; await press($, k[3]) },
            children: [Text({ color: k[5] ? 'red' : MARK, weight: 'bold', children: String(num.get(k)) }), Text({ weight: 'bold', children: ' ' + k[2] }),
              ...(k[4] ? [Text({ key: k[0] + '-dot', color: 'red', children: ' \u2022' })] : [])] })]) })
  }
  // VS Code and the desktop draw a Button as a box: a quiet link instead, which the mod answers itself
  // a red dot beside one: something waits behind it (Resume, on a paused call)
  // one line, never wrapped: the link and a waiting dot on it, as the row's other links
  const link = (key, word, what, quiet, dot) => Box({ flexDirection: 'row', flexWrap: 'nowrap', flexShrink: 0, alignItems: 'center', children: [
    Markdown({ key, dimColor: !!quiet,
    text: '[' + word + '](' + KEY_LINK + key + ')', pressableLinks: [KEY_LINK + key], onLinkPress: async () => { await press($, what) } }),
    ...(dot ? [Text({ key: key + '-dot', color: 'red', children: ' \u2022' })] : [])] })
  // the same actions: number keys in the terminal, quiet links elsewhere (links on the top line only)
  const keyset = (keys, words) => term ? [digits(keys)]
    : [Box({ flexDirection: 'row', flexWrap: 'nowrap', flexShrink: 0, columnGap: 1, children: groups(words).map(w => w === DIV ? Text({ dimColor: true, children: '|' }) : link(...w)) })]
  const now = Date.now(), secsOf = t => Math.max(0, Math.floor((now - t) / 1000))
  // a slow step: its plain words and the dots moving, so it's plainly not stuck
  const slow = (text, since, props) => [fit(text + ' ' + DOTS[secsOf(since) % 3], props)]
  // the dim line under the top one: what's happening now
  const under = kids => [Box({ flexDirection: 'row', flexWrap: 'nowrap', columnGap: 1, children: [...(term ? [] : [Box({ width: 1, flexShrink: 0 })]), ...kids] })]
  const below = (text, props) => text ? under([fit(text, Object.assign({ dimColor: true }, props || {}))]) : []
  const belowSlow = (text, since, props) => under(slow(text, since, Object.assign({ dimColor: true }, props || {})))
  const win = canWindow()

  // End, asked: the row is the question and its two answers, for a while (then it's as it was), while
  // there's still something to end
  if (S.confirm && (Date.now() > S.confirm.until || (!live && !S.starting))) S.confirm = null
  if (S.confirm) {
    const c = S.confirm, call = c.question === ENDS.endCall[0]
    return line([...mark, fixed(call ? '\u25c9' : '\u25cf', { color: 'red' }), fit(c.question, { weight: 'semibold' }),
      ...keyset([['confirmYes', 'e', c.yes.toLowerCase(), 'confirmYes', false, true], ['confirmNo', 'k', c.no.toLowerCase(), 'confirmNo']],
        [['confirmYes', c.yes, 'confirmYes', false], ['confirmNo', c.no, 'confirmNo', true]])])
  }
  // Claude asks: it rings, with the time left, and nothing starts without the click
  if (S.incoming) {
    const call = S.incoming.kind === 'call', left = Math.max(0, Math.ceil((S.incoming.expires - now) / 1000))
    return line([...mark, fixed('◉', { color: MARK }), fit(call ? 'Incoming call' : 'Listen to your meeting?', { weight: 'semibold' }),
      fixed(left + 's', { dimColor: true }),
      ...keyset([['accept', 'a', call ? 'accept' : 'listen', 'incomingAccept'], ['decline', 'd', 'decline', 'incomingDecline'], ['always', 'y', 'always accept', 'incomingAlways']],
        [['accept', call ? 'Accept' : 'Listen', 'incomingAccept'], ['decline', 'Decline', 'incomingDecline', true], ['always', 'Always accept', 'incomingAlways', true]])])
  }
  // a short line that then goes: declined, no answer, added, a start that failed
  if (S.flash) return line([...mark, fixed(S.flash.mark, S.flash.color ? { color: S.flash.color } : { dimColor: true }),
    fit(S.flash.text, S.flash.color ? {} : { dimColor: true })])
  // connecting (or starting to listen), until it's live; End stops it
  if (S.starting && !S.ending) {
    const call = S.starting.kind === 'call'
    const top = line([...mark, fixed(call ? '◉' : '●', { color: call ? MARK : 'red' }), ...slow(call ? 'Connecting' : 'Starting to listen', S.starting.since),
      ...keyset([['end', 'e', 'end', 'cancelStart']], [['end', 'End', 'cancelStart', true]])])
    // waiting on Standby's setup window (a first call or meeting): where to go, dim, under it
    if (!S.starting.setup) return top
    return Box({ flexDirection: 'column', rowGap: 0, children: [top,
      ...below(call ? 'Allow the mic in Standby\u2019s window to start' : 'Finish setup in Standby\u2019s window')] })
  }
  // Standby not answering (it quit, or the Mac is too busy to): the band stays, and comes back by itself
  if (S.unreach) return line([...mark, fixed('◉', { color: WARN }), ...slow('Standby isn’t answering · trying again', S.unreach)])
  // ending, until the summary's row is up
  if (S.ending) return line([...mark, ...(term ? [fixed('⏺', { dimColor: true })] : []), ...slow(S.ending.kind === 'call' ? 'Ending the call' : 'Ending', S.ending.since, { dimColor: true })])
  if (!live) {
    // the summary, after a meeting or a call: a meeting's shown for a while; a call's until it's added or forgotten
    const c = S.card, points = c.waiting ? null : c.points && c.points.length ? c.points.slice(0, 3) : [c.gist || 'Nothing was decided.']
    // a meeting's: a quiet "close" (the terminal's 1); a call's: Add to Claude Code or Forget, until one is chosen
    const close = choosing()
      ? (c.adding ? slow('Adding to Claude Code', c.adding, { dimColor: true })
        : keyset([['add', 'a', 'add to Claude Code', 'addSummary'], ['forget', 'f', 'forget', 'closeSummary']],
          [['add', 'Add to Claude Code', 'addSummary', true], ['forget', 'Forget', 'closeSummary', true]]))
      : keyset([['close', 'c', 'close', 'closeSummary']], [['close', 'Close', 'closeSummary', true]])
    const rows = [line([...mark, ...(term ? [fixed('⏺')] : []), fit('Summary · ' + (c.title || 'the meeting'), { weight: 'semibold' }), ...(c.duration_s != null ? [fixed(minutes(c.duration_s, !term), { dimColor: true })] : []), ...close])]
    // it stopped itself ("Stopped after 15 minutes of silence"): said once, dim, under its name
    if (c.note) rows.push(Box({ flexDirection: 'row', flexWrap: 'nowrap', children: [
      fixed(term ? '     ' : '   ', { dimColor: true }), fit(c.note, { dimColor: true })] }))
    if (!points) rows.push(Box({ flexDirection: 'row', flexWrap: 'nowrap', columnGap: 1, children: [
      fixed(term ? '  ⎿ ' : '  ', { dimColor: true }), ...slow('Summarizing', c.shownAt, { dimColor: true })] }))
    else points.forEach((pt, i) => rows.push(Box({ flexDirection: 'row', flexWrap: 'nowrap', children: [
      fixed(term ? (i ? '     ' : '  ⎿  ') : '   · ', { dimColor: true }), fit(pt)] })))
    return Box({ flexDirection: 'column', rowGap: 0, children: rows })
  }
  const time = fixed(minutes(live.recorded_s != null ? live.recorded_s : Date.now() / 1000 - (live.started || Date.now() / 1000), !term), { dimColor: true })
  // the call's own actions first, then a quiet "|", then the windows
  const winKey = () => (win ? [['window', 'o', 'full window', 'openWindow']] : [])
  const winWord = win ? [['window', 'Full window', 'openWindow', true]] : []
  // macOS refusing it something: the line says what, and Settings opens the pane, after the windows
  const perm = access(live)
  const setKey = perm && perm.refused ? [['settings', 's', 'settings', 'openSettings']] : []
  const setWord = setKey.length ? [['settings', 'Open Settings', 'openSettings', true]] : []
  const permLine = perm ? below(perm.text, perm.refused ? { dimColor: false, color: WARN } : {}) : null
  if (live.kind === 'meeting') {
    const title = live.title || 'the meeting'
    const keys = keyset([['pause', 'p', live.paused ? 'resume' : 'pause', 'pause'], ['stop', 'e', 'end', 'stopListening'], DIV, ...winKey(), ...setKey],
      [['pause', live.paused ? 'Resume' : 'Pause', 'pause', true], ['stop', 'End', 'stopListening', true], DIV, ...winWord, ...setWord])
    const top = live.paused
      ? line([...mark, fixed('‖'), fit('Paused · ' + title), time, ...keys])
      : line([...mark, fixed('●', { color: 'red' }), fit('Listening · ' + title), time, ...keys])
    return Box({ flexDirection: 'column', children: [top, ...(permLine || below(S.caption))] })
  }
  // on a call: the top line says where the call is, the dim one under it what's happening now
  const cs = S.callState, failed = !!cs.notice, paused = !!live.muted || cs.phase === 'muted'
  // paused, with what came back held for Resume: a red dot on Resume, and the voice says it on Resume
  const held = paused && (cs.waiting || !!live.waiting)
  // the call into the mini window in the corner (m), where Standby has it: the first of the windows
  const orbKey = S.st && S.st.can && S.st.can.orb ? [['mini', 'm', 'mini window', 'toOrb']] : []
  const orbWord = orbKey.length ? [['mini', 'Mini window', 'toOrb', true]] : []
  const keys = failed
    ? keyset([['end', 'e', 'end', 'endCall'], DIV, ...orbKey, ...winKey(), ...setKey], [['end', 'End', 'endCall', true], DIV, ...orbWord, ...winWord, ...setWord])
    : keyset([['mute', 'p', paused ? 'resume' : 'pause', 'mute', held], ['end', 'e', 'end', 'endCall'], DIV, ...orbKey, ...winKey(), ...setKey],
      [['mute', paused ? 'Resume' : 'Pause', 'mute', true, held], ['end', 'End', 'endCall', true], DIV, ...orbWord, ...winWord, ...setWord])
  // which session it's with, by its own name: the hand-offs go there, whichever panel is watched
  const who = live.name || 'Claude'
  const top = paused
    ? line([...mark, fixed('‖'), fit('Paused · on a call with ' + who), time, ...keys])
    : line([...mark, fixed('◉', { color: MARK }), fit('On a call with ' + who), time, ...keys])
  // the voice's words after Claude Code's own ✻, the user's plain
  const said = S.last ? (S.last.voice ? '✻ ' : '') + S.last.text : ''
  const sub = cs.retrying ? belowSlow('The voice isn’t answering. Trying again', cs.retrying, { dimColor: false, color: WARN })
    : failed ? below('The voice stopped. End the call and start it again', { dimColor: false, color: WARN })
    : permLine ? permLine
    : held ? below('Paused. Something’s waiting: resume to hear it')
    : paused ? below('Paused. Resume when you’re ready to talk')
    : cs.phase === 'speaking' ? below(said || '✻')
    : cs.phase === 'thinking' ? belowSlow('✻ Thinking', cs.since)
    : cs.working ? belowSlow('✻ Working on it', cs.workingSince)
    : cs.phase === 'listening' ? below(S.heard.you || 'Listening')
    : below(said || 'Listening')
  return Box({ flexDirection: 'column', children: [top, ...sub] })
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    // Each piece on its own: one that fails (a name taken, say) never keeps the others away.
    // The tools first, so Claude can always run Standby when asked in words (a phone included).
    for (const [name, description, props] of [
      ['listen', 'Start listening to the meeting on this Mac. Its transcript then comes with the user\u2019s prompts (or as it goes, if they ask for it live)',
        { pass: { type: 'string', enum: ['ask', 'live'], description: 'live only if the user asks for the transcript as it goes' } }],
      ['stop', 'Stop listening to the meeting; Standby keeps its summary'],
      ['call', 'Start a voice call with Standby, which hands real work to this session'],
      ['end_call', 'End the voice call'],
      ['meeting', 'Read the transcript of the meeting Standby recorded for this session (the latest, live or ended): use it whenever the user asks about the meeting and the transcript isn\u2019t already here'],
      ['settings', 'Standby\u2019s settings: with no setting, lists each with its value and choices; with a setting and a value, changes it (only when the user asks)',
        { setting: { type: 'string', description: 'the setting, as the list names it (transcript, capture, mic, voice, ...)' },
          value: { type: 'string', description: 'one of its choices' } }],
    ]) {
      try { await $.tool.register({ name, description, inputSchema: { type: 'object', properties: props || {} } }) }
      catch (err) { $.ui.log('the ' + name + ' tool isn\u2019t available: ' + err.message) }
    }
    try { await $.command.register({ name: 'standby', description: 'Standby: Claude listens to your meeting with you', argumentHint: '[listen|stop|call|end|name|window|settings|set|off|help]', immediate: true }) }
    catch (err) { $.ui.log('/standby isn\u2019t available (' + err.message + '); the band and asking Claude still work') }
    // The band shows from the start: loading the plugin is asking for Standby
    S.unsaved = !!(await $.env.get('CLAUDE_CODE_CHILD_SESSION'))
    void turnOn($, true)
    $.clock.every(2000, async () => { if (S.on) await refresh($) })
    $.clock.every(1000, () => { if (S.on && moving()) $.ui.invalidate('ui.render') })
    return started
  })

  on('command.run', { command: 'standby' }, async ($, e) => {
    const arg = String(e.args || '').trim()
    if (arg === 'off') { S.on = false; $.ui.invalidate('ui.render'); return { text: 'Standby is off for this session.' } }
    if (arg === 'listen' || arg === 'listen ask' || arg === 'listen live') return { text: await startListening($, arg.split(' ')[1]) }
    if (arg === 'stop') return { text: await stopListening($) }
    if (arg === 'window') return { text: await openWindow($) }
    if (arg === 'settings') return { text: await settings($) }
    if (arg === 'set' || arg.startsWith('set ')) {
      const got = setArgs(arg.slice(3))
      return { text: got ? await settings($, got.name, got.value) : 'Which one, to what? For example /standby set transcript live; /standby settings lists them.' }
    }
    if (arg === 'name' || arg.startsWith('name ')) return { text: await nameSpeaker($, arg.slice(4).trim()) }
    if (arg === 'call') return { text: await startCall($) }
    if (arg === 'end') return { text: await endCall($) }
    if (arg === 'pause' || arg === 'resume') {
      const live = S.st && S.st.live
      if (!live || live.kind !== 'meeting') return { text: 'Nothing is recording.' }
      if ((arg === 'pause') === !!live.paused) return { text: live.paused ? 'Already paused.' : 'Already listening.' }
      return { text: await pause($) }
    }
    if (arg) {                                   // help, or anything it doesn't know: the same list
      if (!S.st) await refresh($)
      return { text: helpText(S.st && S.st.can) }
    }
    const said = await turnOn($)
    if (!S.toasted && S.st && S.st.app) { S.toasted = true; $.ui.toast('Standby is on for this session') }
    return { text: said }
  })

  on('tool.call', { tool: /^mcp__standby__/ }, async ($, e) => {
    const act = e.tool.replace('mcp__standby__', '')
    S.holding++
    try {
    if (ASK_FIRST[act]) {
      const asked = await askToStart($, act)
      if (asked.result) return { result: asked.result }
      if (asked.accepted) {                     // they said yes: it starts on the band; Claude is told at once
        if (act === 'call') void startCall($)
        else void startListening($, ['ask', 'live'].includes(e.pass) ? e.pass : undefined)
        return { result: act === 'call' ? 'Accepted: the call is starting' : 'Accepted: listening is starting' }
      }
    }
    const how = ['ask', 'live'].includes(e.pass) ? e.pass : undefined     // the tool's arguments ride on the event
    const answer = act === 'listen' ? await startListening($, how) : act === 'stop' ? await stopListening($)
      : act === 'call' ? await startCall($) : act === 'end_call' ? await endCall($)
      : act === 'meeting' ? await meetingText($) : act === 'settings' ? await settings($, e.setting, e.value)
      : 'No such Standby action'
    return { result: answer }
    } finally { S.holding-- }
  })

  // Turns: whether the session is busy, and a call's progress back to the voice
  on('turn.start', async ($, e, next) => { S.turn = e.turnId; return next(e) })
  on('turn.complete', async ($, e, next) => {
    S.turn = null
    const due = S.later.splice(0)
    for (const t of due) $.prompt.submit(t.asUser ? { text: t.text, asUser: true } : { text: t.text })
    if (S.call && !e.agentId) {
      const reason = e.isAborted ? 'aborted' : (e.reason || 'answer')
      await app($, '/api/lite/progress', { session: S.call.session, turnId: e.turnId, kind: 'done', text: e.answer || '', reason })
    }
    return next(e)
  })

  // In a call: what the session is doing, so the voice can say it when asked ("it's running the tests")
  on('tool.call', async ($, e, next) => {
    if (S.call && !e.agentId) {
      const now = await $.clock.now()
      if (now - S.saidTool > 2000) {
        S.saidTool = now
        void app($, '/api/lite/progress', { session: S.call.session, turnId: S.turn || '', kind: 'tool', text: toolWords(e) })
      }
    }
    return next(e)
  })

  // What the person types during a meeting carries the lines Claude hasn't had yet: on ask, all of
  // the transcript the first time, then what's new; set to live, the lines since the last batch
  on('prompt.submit', async ($, e, next) => {
    const mine = e.origin && e.origin.kind === 'plugin' && e.origin.name === 'standby'      // a hand-off of the voice's
    if (summaryShown() && !choosing()) { S.card.seen = true; $.ui.invalidate('ui.render') }
    const live = S.st && S.st.live
    // On a call, what the person types or pastes here (never the voice's own hand-offs, a band key, a
    // command, or another session's message) reaches the call: the voice hears of it, and says the answer
    const own = !e.origin || e.origin.kind === 'composer' || e.origin.kind === 'bridge'
    if (S.on && live && live.kind === 'call' && !e.command && !mine && own) {
      const images = (e.attachments || []).filter(a => a && a.type === 'image').length
      await app($, '/api/lite/progress', { session: live.session, kind: 'typed', text: String(e.text || ''), images })
    }
    if (!S.on || !live || live.kind !== 'meeting' || (live.pass !== 'ask' && !S.pending.length)) return next(e)
    const extra = await batch($, live.session, true)
    return extra ? next({ ...e, context: [...(e.context ?? []), extra] }) : next(e)
  }).catch(async ($, e, next) => next(e))

  // The band above the prompt, as Claude Code's own spinner row reads ("✻ Thinking… (12s · esc to
  // interrupt)"). With nothing live and no summary waiting, there's no band at all. While something is live: one line that never wraps (the mark, what it
  // is, its name cut short rather than wrapped, the minutes, Full window) and under it, dim, the
  // latest of it, so you can see it's working: a meeting's last finished line, a call's last exchange
  // (each changes only when a line or a turn is done). In VS Code and the desktop, quiet text links beside
  // Full window: Pause (Resume while paused) and End, short (the user, 2026-10-21); the
  // terminal keeps its number keys; /standby and plain words do the same. After a meeting or a
  // call, its summary, the one time the band takes more room: its points, until the next prompt or
  // about 30 s (everything is kept in Standby; nothing to choose), then nothing.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.surface !== 'terminal') return band($, e, next)
    S.keysNow = []
    const tree = await band($, e, next)
    if (!S.keysNow.length || S.keyUsed) return tree
    // the first time it offers keys: how they work, dim, under them (once per Claude Code session)
    S.hintAt = S.hintAt || Date.now()
    if (Date.now() - S.hintAt > HINT_MS) return tree
    const { Box, Text } = $.ui.resolve(e)
    return Box({ flexDirection: 'column', children: [tree, Box({ flexDirection: 'row', children: [Text({ dimColor: true, children: '  press a number' })] })] })
  })


  // In the chat: a meeting note reads as one dim line, a call's hand-off says it came from the call
  on('ui.render', { component: 'UserMessage' }, async ($, e, next) => {
    const text = String((e.props && e.props.text) || '')
    const line = noteLine(text)
    if (line) { const { Text } = $.ui.resolve(e); return Text({ dimColor: true, children: line }) }
    if (S.fromCall.has(text.trim())) {
      const { Box, Text } = $.ui.resolve(e)
      return Box({ flexDirection: 'column', children: [Text({ dimColor: true, children: 'from your call' }), await next(e)] })
    }
    return next(e)
  })

}
