// What Standby says inside Claude Code: pure functions, no mods API, so they test on their own.

// "12:04" from seconds
export function clock(s) {
  s = Math.max(0, Math.floor(s || 0))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(r).padStart(2, '0')
}

// "14:02" from an epoch in seconds, local time
export function hhmm(t) {
  const d = new Date((t || 0) * 1000)
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
}

// One transcript line as it reads in the pane and in a batch: "Nadia: it ships Friday"
export function said(line, names) {
  const who = (names && names[line.speaker]) || line.name || line.speaker || 'Someone'
  return who + ': ' + String(line.text || '').trim()
}

// A batch of meeting lines, labelled so it reads on its own (Claude, and Remote Control on a phone)
export function batchText(lines, names, title) {
  if (!lines.length) return ''
  const span = hhmm(lines[0].t) + (lines.length > 1 ? '-' + hhmm(lines[lines.length - 1].t) : '')
  return '[Standby · ' + (title || 'meeting') + ' · ' + span + ']\n' + lines.map(l => said(l, names)).join('\n')
}

// A Standby note as the chat shows it: "Standby · meeting lines 10:00 to 10:02", "Standby · meeting
// started 10:00", or null for any other message
export function noteLine(text) {
  const m = /^\[Standby · ([^\]·]+?) · (\d\d:\d\d)(?:-(\d\d:\d\d))?\]/.exec(String(text || ''))
  if (!m) return null
  if (/ started$/.test(m[1])) return 'Standby · ' + m[1] + ' ' + m[2]
  if (/ summary$/.test(m[1])) return 'Standby · ' + m[1] + ' ' + m[2] + (m[3] ? ' to ' + m[3] : '')
  return 'Standby · ' + m[1] + ' lines ' + m[2] + (m[3] ? ' to ' + m[3] : '')
}

// On ask, the one note when a meeting starts: Claude knows there's a meeting, and how to read it
export function startNote(title, now) {
  return '[Standby · meeting started · ' + hhmm(now || Date.now() / 1000) + ']\n' +
    'Standby is recording a meeting' + (title ? ' (' + title + ')' : '') + ' on the user\u2019s Mac. Its transcript comes with ' +
    'the user\u2019s prompts while it runs; afterwards, the standby meeting tool reads it. Nothing to answer now.'
}

// A meeting that ended outside this session (Standby's window, a long silence): said once, so Claude never
// answers from memory that it's still recording (the user's VM run, 2026-10-27)
export function endNote(title, now) {
  return '[Standby · meeting ended · ' + hhmm(now || Date.now() / 1000) + ']\n' +
    'The meeting Standby was recording' + (title ? ' (' + title + ')' : '') + ' has ended, outside this session ' +
    '(in Standby\u2019s window, or by itself). Nothing is recording now; the standby meeting tool reads its transcript. ' +
    'Nothing to answer now.'
}

// Split a stream's text into whole JSON lines, keeping a partial tail for the next chunk
export function jsonLines(buffer, chunk) {
  const text = buffer + chunk, parts = text.split('\n'), rest = parts.pop()
  const out = []
  for (const p of parts) {
    const s = p.trim()
    if (!s) continue
    try { out.push(JSON.parse(s)) } catch { /* not ours: skip */ }
  }
  return { events: out, rest }
}

// What a tool call is doing, in a few words the voice can say
export function toolWords(e) {
  const t = e.tool || ''
  if (t === 'Bash') return 'running ' + String(e.description || e.command || 'a command').slice(0, 80)
  if (t === 'Edit' || t === 'Write' || t === 'MultiEdit') return 'editing ' + String(e.file_path || 'a file').split('/').pop()
  if (t === 'Read') return 'reading ' + String(e.file_path || 'a file').split('/').pop()
  if (t === 'Grep' || t === 'Glob') return 'searching the code'
  if (t === 'WebSearch' || t === 'WebFetch') return 'looking it up online'
  if (t === 'Agent' || t === 'Task') return 'handing part of it to a helper'
  return 'using ' + t.replace(/^mcp__[^_]+__/, '')
}

// Speech to text ends a line at every pause, so one sentence can come as several ("It didn't
// actually open" / "uh, in a" / ...). A line continues the one before when the same person goes on
// within a moment and the one before hasn't ended its sentence. Shown joined; the transcript keeps them apart.
export const JOIN_GAP = 2.5          // s between one line's end and the next one's start
export function continues(prev, line) {
  if (!prev || prev.speaker !== line.speaker) return false
  if (/[.?!\u2026]["\u201d')\]]?\s*$/.test(String(prev.text || ''))) return false
  const end = prev.end != null ? prev.end : prev.t
  return line.t != null && end != null && line.t - end <= JOIN_GAP && String(prev.text || '').length < 400
}
export function joined(lines) {
  const out = []
  for (const l of lines) {
    const last = out[out.length - 1]
    if (last && continues(last, l)) out[out.length - 1] = Object.assign({}, last, { text: last.text.trim() + ' ' + String(l.text || '').trim(), end: l.end != null ? l.end : l.t })
    else out.push(Object.assign({}, l))
  }
  return out
}

// The band's time: whole minutes, so it changes once a minute ("4m", or "4 min" where there's room)
export function minutes(s, long) {
  const m = Math.floor(Math.max(0, Number(s) || 0) / 60)
  return long ? (m < 1 ? 'under a minute' : m + ' min') : (m < 1 ? '<1m' : m + 'm')
}

// "/standby name Speaker 1 Nadia": the longest speaker label the words start with, then the name
export function nameArgs(args, labels) {
  const text = String(args || '').trim(), low = text.toLowerCase()
  const hit = labels.filter(l => low.startsWith(String(l.label).toLowerCase() + ' ') || low.startsWith(String(l.speaker).toLowerCase() + ' '))
    .map(l => ({ l, n: low.startsWith(String(l.label).toLowerCase() + ' ') ? String(l.label).length : String(l.speaker).length }))
    .sort((a, b) => b.n - a.n)[0]
  if (!hit) return null
  const name = text.slice(hit.n).trim()
  return name ? { speaker: hit.l.speaker, label: hit.l.label, name } : null
}

// /standby help: each subcommand in one plain line, and the plain-words way
export function helpText(can) {
  const rows = [
    ['/standby', 'turn Standby on for this session'],
    ['/standby listen', 'listen to the meeting; Claude gets the transcript with your prompts'],
    ['/standby listen live', 'listen, and pass the transcript to Claude as it goes'],
    ['/standby pause', 'pause listening (/standby resume to go on)'],
    ['/standby stop', 'stop listening; the summary shows here and stays in Standby'],
    ['/standby name Speaker 1 Nadia', 'name a speaker; Standby will know their voice'],
  ]
  if (can && can.window) rows.push(['/standby window', 'open Standby on this session'])
  rows.push(['/standby call', 'talk with Standby\u2019s voice (/standby end to hang up)'])
  rows.push(['/standby settings', 'Standby\u2019s settings, their values and choices'],
    ['/standby set transcript live', 'change one (any setting, any of its choices)'])
  rows.push(['/standby off', 'turn Standby off for this session'], ['/standby help', 'this list'])
  const width = Math.max(...rows.map(r => r[0].length))
  // a first line of its own: Claude Code puts the plugin's name in front of it
  return 'what it does here:\n' + rows.map(r => r[0].padEnd(width + 2) + r[1]).join('\n') +
    '\n\nOr just ask Claude in your own words: \u201cstart listening\u201d, \u201cstop listening\u201d, \u201cwhat did they decide?\u201d.'
}

// /standby settings: one line each, "transcript   ask (ask, live)   how a meeting's transcript ...";
// a long list of choices (the voices, the mics) shows its first few
export function settingsText(list) {
  if (!list || !list.length) return 'Standby didn\u2019t list its settings.'
  const shown = c => c.length > 6 ? c.slice(0, 6).join(', ') + ', and ' + (c.length - 6) + ' more' : c.join(', ')
  const width = Math.max(...list.map(s => s.name.length))
  return 'Standby\u2019s settings:\n' + list.map(s => s.name.padEnd(width + 2) + s.value + '  (' + shown(s.choices || []) + ')  ' + s.about).join('\n') +
    '\n\nChange one with /standby set <name> <value>, or ask Claude.'
}

// "/standby set mic AirPods Pro": the name, then the rest as the value
export function setArgs(args) {
  const m = /^(\S+)\s+(.+)$/.exec(String(args || '').trim())
  return m ? { name: m[1], value: m[2].trim() } : null
}
