import { expect, mock, test } from 'claude-code/testing'

// Every use of Claude Code's own question dialog ($.ui.ask), in any test: must stay empty (the last test)
const DIALOGS: string[] = []

// The Standby app as the mod sees it: its HTTP answers, and its live stream
function fakeApp(on: any, events: any[], seen: any) {
  on('env.get', ($: any, e: any) => ({ value: e.name === 'HOME' ? '/tmp/standby-test-home' : (seen.env || {})[e.name] }))
  on('fs.read', ($: any, e: any) => ({ value: String(e.path || '').endsWith('ui.token') ? 'tok-launch-1\n' : '8765\n' }))
  on('session.id', () => ({ value: 'cc-1' }))
  on('session.cwd', () => ({ value: '/work' }))
  on('session.surfaces', () => ({ value: ['terminal', 'mobile'] }))
  on('ui.open', ($: any, e: any) => { (seen.opened ||= []).push(e.id); return { value: { isPlaced: true } } })
  on('ui.close', () => ({ value: undefined }))
  on('ui.toast', ($: any, e: any) => { seen.status.push(e.text); return { value: undefined } })
  on('command.register', () => (seen.refuseCommand ? { deny: seen.refuseCommand } : { value: undefined }))
  on('tool.register', ($: any, e: any) => { (seen.tools ||= []).push(e.name); return { value: { tool: e.name } } })
  // Claude Code's own question dialog: Standby never uses it (the user, 2026-10-22): any use fails the test
  on('tool.call', { tool: 'AskUserQuestion' }, ($: any, e: any) => {
    DIALOGS.push(JSON.stringify(e.questions || e))
    throw new Error('Standby asked in Claude Code\'s own question dialog')
  })
  on('process.run', ($: any, e: any) => {
    const argv = (e && (e.argv || e.args || e.command)) || []
    if (Array.isArray(argv) && argv[0] === 'open') { (seen.launched ||= []).push(argv.join(' ')); seen.down = false }
    return { value: { exitCode: 0, stdout: '', stderr: '' } }
  })
  on('turn.abort', ($: any, e: any) => { seen.aborted.push(e.turnId); return { value: undefined } })
  on('prompt.submit', ($: any, e: any) => { if (seen.refuse) return { drop: seen.refuse }; seen.submitted.push(e); return { text: e.text } })
  on('http.fetch', ($: any, e: any) => {
    const url = String(e.url)
    ;(seen.tokens ||= []).push(((e.init && e.init.headers) || {})['x-standby-token'])
    if (seen.hold && url.includes('/api/lite/incoming?id=') && url.includes('&wait='))      // held until the band answers
      return new Promise(res => { seen.held = (ans: string) => res({ value: { status: 200, ok: true, headers: {},
        text: JSON.stringify({ answer: ans === 'decline' ? 'decline' : 'accept' }) } }) })
    if (seen.down) throw new Error('connection refused')      // Standby not running
    // a start the app takes longer over than the request waits (macOS asking for the mic): the request fails
    if (seen.slowStart && /\/api\/lite\/(call|listen)$/.test(url.split('?')[0]) && e.init && e.init.body) { seen.slowStart(); throw new Error('timed out') }
    const body = e.init && e.init.body ? JSON.parse(e.init.body) : null
    if (body) seen.posts.push({ path: url.replace(/^http:\/\/127\.0\.0\.1:\d+/, ''), body })
    const answer = url.includes('/api/lite/state') ? { app: '1.0', can: seen.can ?? { listen: true }, live: seen.idle || (seen.liveFor !== undefined && seen.liveFor-- <= 0) ? null : seen.live ?? { kind: 'meeting', session: 'm1', recorded_s: 64 } }
      : url.includes('/api/lite/call') && !url.includes('/end') ? { session: 'c1', ...(seen.already ? { already: true } : {}) }
      : url.includes('/api/lite/listen') ? { session: 'm1', pass: seen.pass ?? 'live' }
      : url.includes('/api/lite/transcript') ? { session: 'm1', text: '[Standby · meeting · Planning · 10:00-10:01]\n10:00 Nadia: We ship Friday.' }
      : url.includes('/api/lite/batch?') ? (seen.batch ? seen.batch(url) : {})
      : url.includes('/api/settings/named') ? { settings: [{ name: 'transcript', about: 'how a meeting\u2019s transcript reaches Claude Code', value: 'ask', choices: ['ask', 'live'] },
          { name: 'mic', about: 'the microphone', value: 'default', choices: ['default', 'MacBook Pro Microphone', 'AirPods Pro', 'A', 'B', 'C', 'D', 'E'] }] }
      : url.endsWith('/api/settings') && body ? (['ask', 'live'].includes(body.value) ? { ok: true, name: body.name, value: body.value } : { error: body.name + ' can be ask, live.' })
      : url.includes('/api/lite/card/add') ? (seen.addAnswer ?? { ok: true })
      // Standby's incoming panel: raised (or accepted at once on Always accept), then the user's answer
      : url.includes('/api/lite/incoming/answer') && body ? (seen.held ? seen.held(body.answer) : null, { ok: true })
      : url.includes('/api/lite/incoming') && body ? ((seen.asked ||= []).push(body.kind), seen.always ? { answer: 'accept' } : { id: 'in1' })
      : url.includes('/api/lite/incoming?id=') ? (seen.answer === 'expired' ? { answer: 'decline', expired: true } : { answer: seen.answer ?? 'accept' })
      : url.includes('/api/lite/card') ? (seen.noCard ? {} : Object.assign({ title: 'Planning', points: ['Ship Friday'], duration_s: 1500 }, seen.cardKind ? { kind: seen.cardKind } : {}))
      : { ok: true }
    const reply = { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(answer) } }
    // a request the app takes its time over: held until the test lets it go (seen.release())
    if (seen.holdPath && url.replace(/^http:\/\/127\.0\.0\.1:\d+/, '').split('?')[0] === seen.holdPath)
      return new Promise(res => { seen.release = () => res(reply) })
    return reply
  })
  on('process.spawn', async function* ($: any, e: any) {
    seen.spawned = (e && (e.argv || e.args)) || []
    seen.spawns = (seen.spawns || 0) + 1
    // the stream comes in pieces that aren't lines
    const text = events.map(ev => JSON.stringify(ev)).join('\n') + '\n'
    for (const piece of [text.slice(0, 17), text.slice(17)]) if (piece) yield { stream: 'stdout', text: piece }
    // more events later, when a test sends them (seen.more([...]))
    while (seen.waitMore) {
      const more: any[] = await new Promise(r => { seen.more = r })
      if (!more) break
      yield { stream: 'stdout', text: more.map(ev => JSON.stringify(ev)).join('\n') + '\n' }
    }
    return { value: { code: 0, signal: null } }
  })
}

const settle = () => new Promise(r => setTimeout(r, 30))

test('/standby turns it on, with one toast the first time', async ($, on) => {
  const seen = { posts: [], submitted: [], aborted: [], status: [] as any[] }
  mock.clock(on)
  fakeApp(on, [], seen)
  const r = await $.command.run({ command: 'standby', args: '' })
  expect(r.text).toBe('Standby is on for this session.')
  expect(seen.status).toEqual(['Standby is on for this session'])
  await $.command.run({ command: 'standby', args: '' })
  expect(seen.status.length).toBe(1)
})

test('meeting lines arrive, the engine\'s batch becomes a quiet labelled note, then its suggestions', async ($, on) => {
  const seen = { posts: [] as any[], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  const session = mock.session(on)
  const t = 1760700000
  fakeApp(on, [
    { meet: 'line', i: 1, t, speaker: 'S1', text: 'We ship Friday.', session: 'm1' },
    { meet: 'rename', session: 'm1', renamed: { S1: 'Nadia' } },
    { meet: 'line', i: 2, t: t + 60, speaker: 'S2', text: 'Tests first.', session: 'm1' },
  ], seen)
  // the engine's push policy: the second line makes a batch
  let asked = 0
  seen.batch = () => (++asked === 2 ? { text: '[Standby · meeting · 10:00-10:01]\nNadia: We ship Friday.\nS2: Tests first.', through: 2 } : {})
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const notes = session.appended()
  expect(notes.length).toBe(1)
  const text = notes[0].message.content[0].text
  expect(text).toBe('[Standby · meeting · 10:00-10:01]\nNadia: We ship Friday.\nS2: Tests first.')
  expect(seen.posts.find(p => p.path === '/api/lite/batch/sent').body).toEqual({ session: 'm1', through: 2 })
  expect(asked).toBe(2)
})

test('a call hands the spoken words to this session, and an interruption aborts the running turn', async ($, on) => {
  const seen = { posts: [] as any[], submitted: [] as any[], aborted: [] as string[], status: [] }
  mock.clock(on)
  on('turn.start', ($: any, e: any) => ({ turnId: e.turnId }))
  fakeApp(on, [{ call: 'abort' }, { call: 'handoff', session: 'c1', id: 'c1-1', text: 'run the tests and tell me' }], seen)
  await $.turn.start({ turnId: 't9' })
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(seen.aborted).toEqual(['t9'])                  // the user's stop; a hand-off never stops the running turn
  expect(seen.submitted.some(s => s.text === 'run the tests and tell me')).toBe(true)
  // the session has it: said back, so the voice only says it went once it did
  expect(seen.posts.find(p => p.path === '/api/lite/progress' && p.body.kind === 'received').body).toEqual(
    { session: 'c1', id: 'c1-1', kind: 'received', text: '' })
})

test('a hand-off while the session is busy: said back as queued at once, then received when it enters', async ($, on) => {
  const seen = { posts: [] as any[], submitted: [] as any[], aborted: [] as string[], status: [] }
  mock.clock(on)
  on('turn.start', ($: any, e: any) => ({ turnId: e.turnId }))
  fakeApp(on, [{ call: 'handoff', session: 'c1', id: 'c1-3', text: 'wait ten seconds' }], seen)
  await $.turn.start({ turnId: 't9' })
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const said = seen.posts.filter(p => p.path === '/api/lite/progress').map(p => p.body.kind)
  expect(said).toEqual(['queued', 'received'])
  expect(seen.aborted).toEqual([])                   // the running turn goes on
})

test('a hand-off while the session is idle: received, never queued', async ($, on) => {
  const seen = { posts: [] as any[], submitted: [] as any[], aborted: [] as string[], status: [] }
  mock.clock(on)
  fakeApp(on, [{ call: 'handoff', session: 'c1', id: 'c1-4', text: 'run the tests' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(seen.posts.filter(p => p.path === '/api/lite/progress').map(p => p.body.kind)).toEqual(['received'])
})

test('a hand-off Claude Code turns away: said back as undelivered, with why', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], refuse: 'a Stop stands' }
  mock.clock(on)
  fakeApp(on, [{ call: 'handoff', session: 'c1', id: 'c1-2', text: 'remove the caption' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(seen.submitted.length).toBe(0)
  expect(seen.posts.find((p: any) => p.path === '/api/lite/progress').body).toEqual(
    { session: 'c1', id: 'c1-2', kind: 'undelivered', text: 'a Stop stands' })
})

for (const kind of ['meet', 'call']) test('a ' + kind + ' card event shows the summary on the band, nothing to choose, nothing to Claude', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  const session = mock.session(on)
  fakeApp(on, [{ [kind]: 'card', session: 'm1' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'Summary · Planning' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: 'Ship Friday' })).toBeDefined()
  for (const key of ['keep', 'forget']) expect(await band.find({ key })).toBeUndefined()
  // the summary stays in Standby: no note, no turn
  expect(session.appended().length).toBe(0)
  expect(seen.submitted.length).toBe(0)
})

test('on ask (the default): one note that a meeting started, no batches; each prompt carries the transcript', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], pass: 'ask', live: { kind: 'meeting', session: 'm1', recorded_s: 64, pass: 'ask', title: 'Planning' } }
  mock.clock(on)
  const session = mock.session(on)
  const t = 1760700000
  fakeApp(on, [{ meet: 'line', i: 1, t, speaker: 'S1', text: 'We ship Friday.', session: 'm1' }], seen)
  // the engine: nothing on its own on ask; everything so far when a prompt asks (all=1)
  seen.batch = (url: string) => url.includes('all=1') ? { text: '[Standby · meeting · 10:00-10:00]\n10:00 Nadia: We ship Friday.', through: 1 } : {}
  const r = await $.command.run({ command: 'standby', args: 'listen' })
  expect(r.text).toBe('Listening. Claude here gets the transcript with each of your prompts.')
  expect(seen.posts.find((p: any) => p.path === '/api/lite/listen').body).toEqual({ cc: 'cc-1', cwd: '/work' })
  await settle()
  const notes = session.appended().map((n: any) => n.message.content[0].text)
  expect(notes.length).toBe(1)
  expect(notes[0]).toMatch(/^\[Standby · meeting started · \d\d:\d\d\]\nStandby is recording a meeting \(Planning\)/)
  await $.prompt.submit({ text: 'what did they decide?' })
  const sent = seen.submitted.find((x: any) => x.text === 'what did they decide?')
  expect(sent.context).toEqual(['[Standby · meeting · 10:00-10:00]\n10:00 Nadia: We ship Friday.'])
  // nothing new: the next prompt goes as it is
  seen.batch = () => ({})
  await $.prompt.submit({ text: 'thanks' })
  expect(seen.submitted.find((x: any) => x.text === 'thanks').context).toBeUndefined()
  // stopped on ask: what's unsent waits to be asked, it isn't pushed
  await $.command.run({ command: 'standby', args: 'stop' })
  expect(session.appended().length).toBe(1)
})

test('/standby listen live starts it live; the meeting tool reads the transcript afterwards', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  const session = mock.session(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: 'listen live' })
  expect(seen.posts.find((p: any) => p.path === '/api/lite/listen').body).toEqual({ cc: 'cc-1', cwd: '/work', pass: 'live' })
  expect(session.appended().length).toBe(0)
  const m = await $.tool.call({ tool: 'mcp__standby__meeting' })
  expect(m.result).toBe('[Standby · meeting · Planning · 10:00-10:01]\n10:00 Nadia: We ship Friday.')
  expect((await $.tool.call({ tool: 'mcp__standby__listen', pass: 'ask' } as any) as any).result).toBe('Accepted: listening is starting')
  await settle()                                   // it starts on the band, after the tool's answer
  expect(seen.posts.filter((p: any) => p.path === '/api/lite/listen')[1].body).toEqual({ cc: 'cc-1', cwd: '/work', pass: 'ask' })
})

const BAND = { plugin: 'standby', component: 'AbovePrompt', requestId: 'band', viewport: { columns: 120, rows: 40 },
  props: { hasSurvey: false, isWorking: false, maxRows: 4, bodyColumns: 120, scroll: { offset: 0, bodyRows: 4 }, view: {} } } as const

test('the band while a meeting records: one line that never wraps, the minutes, p pause · e end, and the latest line under it', async ($, on) => {
  const seen: any = { posts: [] as any[], submitted: [], aborted: [], status: [], opened: [] }
  mock.clock(on)
  fakeApp(on, [{ meet: 'line', i: 1, t: 1760700000, speaker: 1, name: 'Speaker 1', text: 'We ship Friday.' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await $.command.run({ command: 'standby', args: 'listen' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'Listening · the meeting' })).toMatchObject({ props: { wrap: 'truncate-end' } })
  expect(await band.find({ type: 'Text', text: '1m' })).toBeDefined()
  expect(await band.find({ key: 'pause' })).toMatchObject({ props: { label: 'pause', plain: true } })
  expect(await band.find({ key: 'stop' })).toMatchObject({ props: { label: 'end' } })
  expect(await band.find({ key: 'call' })).toBeUndefined()
  // so you can see it's working: the latest finished line, dim, one line, under the status
  expect(await band.find({ type: 'Text', text: 'Speaker 1: We ship Friday.' })).toMatchObject({ props: { dimColor: true, wrap: 'truncate-end' } })
  expect(seen.opened).toEqual([])
  // VS Code: no boxes (it draws a Button as one), no keys on the row (Pause and Stop are in the window)
  const panel = await $.ui.mount({ ...BAND, requestId: 'band2', surface: 'vscode' } as any)
  expect(await panel.find({ type: 'Button' })).toBeUndefined()
  expect(await panel.find({ type: 'Text', text: 'Listening · the meeting' })).toBeDefined()
  expect(await panel.find({ type: 'Text', text: 'Speaker 1: We ship Friday.' })).toBeDefined()
})

test('on a call: one line, p pause · e end, and the latest words under it, the voice after ✻', async ($, on) => {
  const seen: any = { posts: [] as any[], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [{ call: 'caption', session: 'c1', you: 'run the tests', said: '' }, { call: 'caption', session: 'c1', you: 'run the tests', said: 'On it.' },
    { call: 'state', session: 'c1', phase: 'speaking' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'On a call with Claude' })).toBeDefined()
  expect(await band.find({ key: 'mute' })).toMatchObject({ props: { label: 'pause' } })
  expect(await band.find({ key: 'end' })).toMatchObject({ props: { label: 'end' } })
  expect(await band.find({ type: 'Text', text: '\u273b On it.' })).toMatchObject({ props: { dimColor: true, wrap: 'truncate-end' } })    // the voice speaking: its line
})

test('VS Code while live: quiet links beside Full window, Pause and End, short (the user, 2026-10-21)', async ($, on) => {
  const seen: any = { posts: [] as any[], submitted: [], aborted: [], status: [], can: { listen: true, window: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const panel = await $.ui.mount({ ...BAND, surface: 'vscode' } as any)
  const text = async (key: string) => (await panel.find({ key }))?.props.text
  expect([await text('pause'), await text('stop'), await text('window')]).toEqual([
    '[Pause](https://standby.invalid/pause)', '[End](https://standby.invalid/stop)', '[Full window](https://standby.invalid/window)'])
  for (const key of ['pause', 'stop', 'window']) expect((await panel.find({ key })).props.dimColor).toBe(true)
  await panel.press({ key: 'pause', link: { href: 'https://standby.invalid/pause' } } as any)
  expect(seen.posts.some((p: any) => p.path === '/api/lite/pause')).toBe(true)
  await panel.press({ key: 'window', link: { href: 'https://standby.invalid/window' } } as any)
  expect(seen.posts.some((p: any) => p.path === '/api/window')).toBe(true)
  await panel.press({ key: 'stop', link: { href: 'https://standby.invalid/stop' } } as any)
  await (await bandOf($, 'vscode')).press({ key: 'confirmYes', link: { href: 'https://standby.invalid/confirmYes' } } as any)
  expect(seen.posts.some((p: any) => p.path === '/api/lite/stop')).toBe(true)
})

test('VS Code, a paused meeting: Resume and End', async ($, on) => {
  const seen: any = { posts: [] as any[], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: { kind: 'meeting', session: 'm1', started: 1, paused: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'b-paused', surface: 'vscode' } as any)
  expect((await panel.find({ key: 'pause' })).props.text).toBe('[Resume](https://standby.invalid/pause)')
  expect((await panel.find({ key: 'stop' })).props.text).toBe('[End](https://standby.invalid/stop)')
  await panel.press({ key: 'pause', link: { href: 'https://standby.invalid/pause' } } as any)
  expect(seen.posts.some((p: any) => p.path === '/api/lite/resume')).toBe(true)
})

test('VS Code, a call: Pause (Resume while paused) and End, beside Full window', async ($, on) => {
  const seen: any = { posts: [] as any[], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'b-call', surface: 'vscode' } as any)
  expect([(await panel.find({ key: 'mute' })).props.text, (await panel.find({ key: 'end' })).props.text, (await panel.find({ key: 'window' })).props.text]).toEqual([
    '[Pause](https://standby.invalid/mute)', '[End](https://standby.invalid/end)', '[Full window](https://standby.invalid/window)'])
  await panel.press({ key: 'mute', link: { href: 'https://standby.invalid/mute' } } as any)
  expect(seen.posts.find((p: any) => p.path === '/api/lite/mute').body).toEqual({ session: 'c1', muted: true })
  await panel.press({ key: 'end', link: { href: 'https://standby.invalid/end' } } as any)
  await (await bandOf($, 'vscode')).press({ key: 'confirmYes', link: { href: 'https://standby.invalid/confirmYes' } } as any)
  expect(seen.posts.some((p: any) => p.path === '/api/lite/call/end')).toBe(true)
})

test('VS Code, a paused call: Resume, which goes on with it', async ($, on) => {
  const seen: any = { posts: [] as any[], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening', muted: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'b-call-muted', surface: 'vscode' } as any)
  expect((await panel.find({ key: 'mute' })).props.text).toBe('[Resume](https://standby.invalid/mute)')
  await panel.press({ key: 'mute', link: { href: 'https://standby.invalid/mute' } } as any)
  expect(seen.posts.find((p: any) => p.path === '/api/lite/mute').body).toEqual({ session: 'c1', muted: false })
})

test('/standby name Speaker 1 Nadia: names the speaker, and says Standby will know their voice', async ($, on) => {
  const seen: any = { posts: [] as any[], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  fakeApp(on, [{ meet: 'line', i: 1, t: 1760700000, speaker: 1, name: 'Speaker 1', text: 'We ship Friday.' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const r = await $.command.run({ command: 'standby', args: 'name Speaker 1 Nadia Ruiz' })
  expect(r.text).toBe('Speaker 1 is Nadia Ruiz now. Standby will know their voice.')
  expect(seen.posts.find((p: any) => p.path === '/api/speaker').body).toEqual({ id: 'm1', spk: '1', name: 'Nadia Ruiz' })
  const who = await $.command.run({ command: 'standby', args: 'name Nadia' })
  expect(who.text).toMatch(/^Name whom\?/)
})

test('a meeting note reads in the chat as one dim line', async ($, on) => {
  const seen = { posts: [] as any[], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  fakeApp(on, [], seen)
  const msg = (text: string) => ({ plugin: 'standby', component: 'UserMessage', requestId: 'u' + text.length, viewport: { columns: 100, rows: 30 }, props: { text, origin: { kind: 'user' }, isExpanded: false } })
  const note = await $.ui.mount({ ...msg('[Standby · meeting · 10:00-10:02]\nNadia: ship Friday'), surface: 'terminal' } as any)
  expect(await note.find({ type: 'Text', text: 'Standby · meeting lines 10:00 to 10:02' })).toBeDefined()
  const plain = await $.ui.mount({ ...msg('fix the tests'), surface: 'terminal' } as any)
  expect(await plain.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
})

const ENGINE = (on: any) => on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

for (const surface of ['terminal', 'vscode']) test('nothing live, no summary waiting: no band at all (' + surface + ')', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, can: { listen: true, window: true } }
  mock.clock(on)
  ENGINE(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const band = await $.ui.mount({ ...BAND, requestId: 'idle-' + surface, surface } as any)
  expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  for (const key of ['listen', 'call', 'window']) expect(await band.find({ key })).toBeUndefined()
  expect(await band.find({ type: 'Svg' })).toBeUndefined()
})

test('a call: the session\'s tools reach the voice (at most every 2 s), and the turn\'s end with its reason', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  const clock = mock.clock(on)
  on('turn.start', ($: any, e: any) => ({ turnId: e.turnId }))
  on('turn.complete', () => ({ text: '' }))
  on('tool.call', () => ({ result: 'ok' }))
  fakeApp(on, [{ call: 'caption', session: 'c1', you: 'run the tests' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  const r = await $.command.run({ command: 'standby', args: 'call' })
  expect(r.text).toBe('On a call. Talk; real work comes here.')
  await clock.advance(5000)
  await $.turn.start({ turnId: 't1' })
  await $.tool.call({ tool: 'Bash', command: 'npm test', description: 'Run the tests' })
  await $.tool.call({ tool: 'Read', file_path: '/w/a.ts' })
  await clock.advance(2500)
  await $.tool.call({ tool: 'Edit', file_path: '/w/src/cache.ts' })
  await settle()
  const tools = seen.posts.filter((p: any) => p.path === '/api/lite/progress' && p.body.kind === 'tool').map((p: any) => p.body.text)
  expect(tools).toEqual(['running Run the tests', 'editing cache.ts'])
  await $.turn.complete({ turnId: 't1', answer: '', durationMs: 900, isAborted: true, usage: null })
  const done = seen.posts.find((p: any) => p.path === '/api/lite/progress' && p.body.kind === 'done').body
  expect(done).toMatchObject({ session: 'c1', turnId: 't1', reason: 'aborted' })
})

test('startup: the tools register even when /standby can\'t; idle, no band', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, refuseCommand: '"/standby" refused: it is the plugin\'s /standby:standby' }
  mock.clock(on)
  ENGINE(on)
  fakeApp(on, [], seen)
  on('session.start', () => ({ cwd: '/work' }))
  on('ui.log', () => ({ value: undefined }))
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await settle()
  expect(['listen', 'stop', 'meeting', 'call', 'end_call'].every(t => (seen.tools || []).includes(t))).toBe(true)
  expect(['keep', 'forget'].some(t => (seen.tools || []).includes(t))).toBe(false)
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
})

test('a terminal that doesn\'t save the conversation: no band, and Listen says why it can\'t', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, env: { CLAUDE_CODE_CHILD_SESSION: '1' } }
  mock.clock(on)
  ENGINE(on)
  fakeApp(on, [], seen)
  on('session.start', () => ({ cwd: '/work' }))
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  const r = await $.tool.call({ tool: 'mcp__standby__listen' })
  expect(r.result).toMatch(/env -u CLAUDE_CODE_CHILD_SESSION claude$/)
  expect(seen.posts.some((p: any) => p.path === '/api/lite/listen')).toBe(false)
})

test('the compact window: offered only when the app has it, opened with the live session', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ key: 'window' })).toMatchObject({ props: { label: 'full window' } })
  await band.press({ key: 'window' })
  expect(seen.posts.find((p: any) => p.path === '/api/window').body).toEqual({ session: 'm1', cc: 'cc-1' })
  const r = await $.command.run({ command: 'standby', args: 'window' })
  expect(r.text).toBe('Standby\u2019s window is open.')
})

test('no compact window in this Standby: no button for it', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ key: 'window' })).toBeUndefined()
})

test('/standby help, and any word it doesn\'t know: each subcommand in a plain line, and the plain-words way', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  const r = await $.command.run({ command: 'standby', args: 'help' })
  expect(r.text).toContain('/standby listen')
  expect(r.text).toContain('/standby stop')
  expect(r.text).toContain('/standby pause')
  expect(r.text).toContain('/standby window')
  expect(r.text).toContain('/standby name Speaker 1 Nadia')
  expect(r.text).toContain('/standby off')
  expect(r.text).toContain('/standby settings')
  expect(r.text).toContain('Or just ask Claude in your own words')
  expect(r.text).toContain('/standby call')
  const odd = await $.command.run({ command: 'standby', args: 'record' })
  expect(odd.text).toBe(r.text)
})

test('the recorder\'s question about what to record is left to the app: nothing in the band, nothing to Claude, no tool for it', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  const session = mock.session(on)
  const ask = { id: 'app:com.google.Chrome', text: 'Record Google Chrome? A call is playing in it.', actions: [{ label: 'Record Chrome', value: 'use' }, { label: 'Not this time', value: 'skip' }] }
  seen.live = { kind: 'meeting', session: 'm1', recorded_s: 64, ask }
  fakeApp(on, [{ meet: 'capture', session: 'm1', ask }], seen)
  on('session.start', () => ({ cwd: '/work' }))
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: /Record Google Chrome/ })).toBeUndefined()
  expect(await band.find({ key: 'ask0' })).toBeUndefined()
  expect(session.appended().length).toBe(0)
  expect((seen.tools || []).includes('answer')).toBe(false)
})

test('after a meeting, its summary on the band, nothing to choose; gone at the next prompt, or after a while', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  const clock = mock.clock(on)
  on('turn.complete', () => ({ text: '' }))
  fakeApp(on, [{ meet: 'card', session: 'm1' }], seen)
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'Summary · Planning' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: 'Ship Friday' })).toMatchObject({ props: { wrap: 'truncate-end' } })
  expect(await band.find({ key: 'keep' })).toBeUndefined()
  expect(await band.find({ key: 'forget' })).toBeUndefined()
  const panel = await $.ui.mount({ ...BAND, requestId: 'band2', surface: 'vscode' } as any)
  expect(await panel.find({ type: 'Button' })).toBeUndefined()
  // the person can close it: a quiet Close link in VS Code, 1 in the terminal
  expect((await panel.find({ key: 'close' })).props.dimColor).toBe(true)
  expect(await band.find({ key: 'close' })).toMatchObject({ props: { label: 'close' } })
  // the next prompt: seen, gone
  await $.prompt.submit({ text: 'what did we decide?' })
  const after = await $.ui.mount({ ...BAND, requestId: 'band3', surface: 'terminal' })
  expect(await after.find({ type: 'Text', text: 'Summary · Planning' })).toBeUndefined()
  expect(await after.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
})

test('after a call, its summary offers Add to Claude Code or Forget, and waits for the choice (the user, 2026-10-21)', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, cardKind: 'call' }
  const clock = mock.clock(on)
  on('turn.complete', () => ({ text: '' }))
  fakeApp(on, [{ call: 'card', session: 'c1' }], seen)
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'cc1', surface: 'vscode' } as any)
  expect([(await panel.find({ key: 'add' })).props.text, (await panel.find({ key: 'forget' })).props.text]).toEqual([
    '[Add to Claude Code](https://standby.invalid/add)', '[Forget](https://standby.invalid/forget)'])
  expect(await panel.find({ key: 'close' })).toBeUndefined()
  const term = await $.ui.mount({ ...BAND, requestId: 'cc2', surface: 'terminal' })
  expect(await term.find({ key: 'forget' })).toMatchObject({ props: { label: 'forget' } })
  expect(await term.find({ key: 'add' })).toMatchObject({ props: { label: 'add to Claude Code' } })
  // it waits: not gone after the half minute a meeting's summary has, nor at the next prompt
  await clock.advance(31000)
  await $.prompt.submit({ text: 'what next?' })
  const later = await $.ui.mount({ ...BAND, requestId: 'cc3', surface: 'vscode' } as any)
  expect(await later.find({ key: 'add' })).toBeDefined()
  // Add: the app writes and sends the summary; the card goes, a toast says so
  await later.press({ key: 'add', link: { href: 'https://standby.invalid/add' } } as any)
  expect(seen.posts.find((p: any) => p.path === '/api/lite/card/add').body).toEqual({ session: 'c1' })
  expect(seen.status).toContain('Added to Claude Code')
  const gone = await $.ui.mount({ ...BAND, requestId: 'cc4', surface: 'vscode' } as any)
  expect(await gone.find({ key: 'add' })).toBeUndefined()
})

test('a call\'s summary: Forget closes it and sends nothing; an Add that can\'t yet keeps it, to try again', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, cardKind: 'call', addAnswer: { error: 'That call\u2019s summary isn\u2019t ready yet' } }
  mock.clock(on)
  fakeApp(on, [{ call: 'card', session: 'c1' }], seen)
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'cf1', surface: 'vscode' } as any)
  await panel.press({ key: 'add', link: { href: 'https://standby.invalid/add' } } as any)
  expect(seen.status).toContain('That call\u2019s summary isn\u2019t ready yet')
  const still = await $.ui.mount({ ...BAND, requestId: 'cf2', surface: 'vscode' } as any)
  expect(await still.find({ key: 'add' })).toBeDefined()
  // nothing new for Claude Code: said, and the card goes as after an Add
  seen.addAnswer = { error: 'There was nothing new for Claude Code in that call' }
  await still.press({ key: 'add', link: { href: 'https://standby.invalid/add' } } as any)
  expect(seen.status).toContain('There was nothing new for Claude Code in that call')
  expect(await (await $.ui.mount({ ...BAND, requestId: 'cf3', surface: 'vscode' } as any)).find({ key: 'add' })).toBeUndefined()
})

test('a call\'s summary: Forget closes it, nothing sent', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, cardKind: 'call' }
  mock.clock(on)
  fakeApp(on, [{ call: 'card', session: 'c1' }], seen)
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'cg1', surface: 'vscode' } as any)
  await panel.press({ key: 'forget', link: { href: 'https://standby.invalid/forget' } } as any)
  expect(seen.posts.some((p: any) => p.path === '/api/lite/card/add')).toBe(false)
  expect(await (await $.ui.mount({ ...BAND, requestId: 'cg2', surface: 'vscode' } as any)).find({ key: 'forget' })).toBeUndefined()
})

test('the summary on the band closes when Close is pressed', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  fakeApp(on, [{ meet: 'card', session: 'm1' }], seen)
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'band-c', surface: 'vscode' } as any)
  await panel.press({ key: 'close', link: { href: 'https://standby.invalid/close' } } as any)
  const after = await $.ui.mount({ ...BAND, requestId: 'band-c2', surface: 'vscode' } as any)
  expect(await after.find({ type: 'Text', text: 'Summary · Planning' })).toBeUndefined()
})

test('the moment a meeting ends: its name and Summarizing, its dots moving, until the summary comes', async ($, on) => {
  // live once (the band learns what it is), then ended, its summary not made yet
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], noCard: true, liveFor: 1, live: { kind: 'meeting', session: 'm1', recorded_s: 240, title: 'Fares sync' } }
  mock.clock(on)
  fakeApp(on, [{ meet: 'state', session: 'm1', live: false, paused: false, ended: true }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Text', text: 'Summary · Fares sync' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: '4m' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: 'Summarizing .' })).toMatchObject({ props: { dimColor: true } })
})

test('a meeting that stopped itself after a silence says so on the band, under its name', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], liveFor: 1, live: { kind: 'meeting', session: 'm1', recorded_s: 900, title: 'Fares sync' } }
  mock.clock(on)
  fakeApp(on, [{ meet: 'state', session: 'm1', live: false, paused: false, ended: true, note: 'Stopped after 15 minutes of silence' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  // the summary in (its card has no note of its own here): the note stays
  expect(await band.find({ type: 'Text', text: 'Summary · Planning' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: 'Stopped after 15 minutes of silence' })).toMatchObject({ props: { dimColor: true } })
})

test('VS Code: Standby\'s mark leads the row in place of the word, and the row never wraps (Full window stays on it)', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await $.ui.mount({ ...BAND, requestId: 'b-logo', surface: 'vscode' } as any)
  const svg = await panel.find({ type: 'Svg' })
  expect(svg).toMatchObject({ props: { alt: 'Standby', width: 16, height: 16 } })
  expect(svg.props.source).toContain('<svg')
  expect(await panel.find({ type: 'Text', text: 'Standby' })).toBeUndefined()
  expect(await panel.find({ type: 'Box', props: { flexWrap: 'nowrap' } })).toBeDefined()
  const term = await $.ui.mount({ ...BAND, requestId: 'b-term', surface: 'terminal' } as any)
  expect(await term.find({ type: 'Svg' })).toBeUndefined()
  expect(await term.find({ key: 'stop' })).toBeDefined()
})

test('a call: the second line is the latest words as they come, one speaker, no You: or Claude: label', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [
    { call: 'caption', session: 'c1', you: 'what is in the' },
    { call: 'caption', session: 'c1', you: 'what is in the README?' },
    { call: 'state', session: 'c1', phase: 'thinking' },
    { call: 'caption', session: 'c1', said: 'It covers setup and' },
    { call: 'caption', session: 'c1', said: 'It covers setup and the tests.' },
    { call: 'state', session: 'c1', phase: 'listening' },
    { call: 'caption', session: 'c1', you: 'and the' },                          // the next turn, under way
  ], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await $.ui.mount({ ...BAND, requestId: 'c-1', surface: 'terminal' } as any)
  // the latest words, the user's plain: no waiting for the turn to end, no labels
  expect(await band.find({ type: 'Text', text: 'and the' })).toMatchObject({ props: { dimColor: true, wrap: 'truncate-end' } })
  expect(await band.find({ type: 'Text', text: /You:|Claude:/ })).toBeUndefined()
})

test('Standby closed after the session started: listening opens it in the background, then listens', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  seen.down = true                                   // the app is quit later
  const r = await $.command.run({ command: 'standby', args: 'listen' })
  await settle()
  expect(seen.launched).toEqual(['open -g -a Standby'])
  expect(seen.posts.some((p: any) => p.path === '/api/lite/listen')).toBe(true)
})

test('the stream to Standby drops (Standby restarted): it reconnects by itself, so lines keep reaching Claude', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  const clock = mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const first = seen.spawns
  await clock.advance(1500)
  await settle()
  expect(seen.spawns).toBeGreaterThan(first)
})

test('settings as a command and a tool: listed with values and choices, one changed by name, the app checking the value', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const list = await $.command.run({ command: 'standby', args: 'settings' })
  expect(list.text).toContain('transcript')
  expect(list.text).toContain('ask  (ask, live)')
  expect(list.text).toContain('and 2 more')                 // a long list of choices shows its first few
  expect(list.text).toContain('/standby set <name> <value>')
  const set = await $.command.run({ command: 'standby', args: 'set transcript live' })
  expect(set.text).toBe('transcript is now live.')
  expect(seen.posts.find((p: any) => p.path === '/api/settings').body).toEqual({ name: 'transcript', value: 'live' })
  const bad = await $.command.run({ command: 'standby', args: 'set transcript loud' })
  expect(bad.text).toBe('transcript can be ask, live.')
  const half = await $.command.run({ command: 'standby', args: 'set transcript' })
  expect(half.text).toContain('/standby set transcript live')
  const tool = await $.tool.call({ tool: 'mcp__standby__settings', setting: 'transcript', value: 'ask' } as any)
  expect(tool.result).toBe('transcript is now ask.')
  const listed = await $.tool.call({ tool: 'mcp__standby__settings' } as any)
  expect(listed.result).toContain('Standby\u2019s settings:')
  expect(seen.posts.some((p: any) => p.path === '/api/lite/pass')).toBe(false)     // nothing live: the default only
})

test('the transcript setting during a meeting: the meeting already running switches too', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const r = await $.command.run({ command: 'standby', args: 'set transcript live' })
  expect(r.text).toBe('transcript is now live, for this meeting too.')
  expect(seen.posts.find((p: any) => p.path === '/api/lite/pass').body).toEqual({ session: 'm1', pass: 'live' })
})

test('asking for a call while this session is on one: says so plainly, and how to end it', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' }, already: true }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const r = await $.command.run({ command: 'standby', args: 'call' })
  expect(r.text).toBe('You\u2019re already on this call. To end it, say goodbye or use /standby end.')
  const t = await $.tool.call({ tool: 'mcp__standby__call' } as any)
  expect(t.result).toBe(r.text)
})

test('a call\'s summary the user added: a quiet note in this session, no turn', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  const session = mock.session(on)
  const text = '[Standby · call summary · 14:02-14:20]\nThe PM agreed the TTL stays at an hour.'
  fakeApp(on, [{ call: 'summary', session: 'c1', text }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(JSON.stringify(session.appended())).toContain('The PM agreed the TTL stays at an hour.')
  expect(seen.submitted.length).toBe(0)
})

test('Claude starting a call or listening on its own: it rings on the band; only Accept starts it', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  seen.answer = 'decline'
  const no = await $.tool.call({ tool: 'mcp__standby__call' } as any)
  expect(seen.asked).toEqual(['call'])
  expect(no.result).toBe('Declined')
  expect(seen.posts.some((p: any) => p.path === '/api/lite/call')).toBe(false)
  const nolisten = await $.tool.call({ tool: 'mcp__standby__listen' } as any)
  expect(nolisten.result).toBe('Declined')
  expect(seen.posts.some((p: any) => p.path === '/api/lite/listen')).toBe(false)
  seen.answer = 'accept'
  const yes = await $.tool.call({ tool: 'mcp__standby__call' } as any)
  expect(yes.result).toBe('Accepted: the call is starting')
  await settle()                                   // the call starts on the band, after the tool's answer
  expect(seen.posts.some((p: any) => p.path === '/api/lite/call')).toBe(true)
})

test('the band rings while the tool waits: Accept, Decline, Always accept; the click answers it', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, hold: true }
  mock.clock(on)
  ENGINE(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const waiting = $.tool.call({ tool: 'mcp__standby__call' } as any)
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' } as any)
  expect(await band.find({ type: 'Text', text: 'Incoming call' })).toBeDefined()
  expect(await band.find({ type: 'Text', text: '30s' })).toBeDefined()           // the time left to answer
  for (const key of ['accept', 'decline', 'always']) expect(await band.find({ key })).toBeDefined()
  expect(seen.posts.some((p: any) => p.path === '/api/lite/call')).toBe(false)      // nothing yet: it waits
  await band.press({ key: 'accept' })
  const r: any = await waiting
  expect(seen.posts.find((p: any) => p.path === '/api/lite/incoming/answer').body.answer).toBe('accept')
  expect(r.result).toBe('Accepted: the call is starting')
  await settle()                                   // the call starts on the band, after the tool's answer
  expect(await band.find({ type: 'Text', text: 'Incoming call' })).toBeUndefined()      // answered: the ring is gone
})

test('in VS Code the ring is links; Decline starts nothing', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, hold: true }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const waiting = $.tool.call({ tool: 'mcp__standby__listen' } as any)
  await settle()
  const band = await $.ui.mount({ ...BAND, surface: 'vscode' } as any)
  expect(await band.find({ type: 'Text', text: 'Listen to your meeting?' })).toBeDefined()
  await band.press({ key: 'decline', link: { href: 'https://standby.invalid/decline' } } as any)
  const r: any = await waiting
  expect(r.result).toBe('Declined')
  expect(seen.posts.some((p: any) => p.path === '/api/lite/listen')).toBe(false)
})

test('the user may take their time: the tool still answers after the hook\'s own 10 s (the wait is in the app)', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, hold: true }
  const clock = mock.clock(on)
  ENGINE(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const waiting = $.tool.call({ tool: 'mcp__standby__call' } as any)
  await settle()
  for (let i = 0; i < 12; i++) { await clock.advance(1000); await settle() }
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' } as any)
  await band.press({ key: 'accept' })
  const r: any = await waiting
  expect(r.result).toBe('Accepted: the call is starting')
  await settle()                                   // the call starts on the band, after the tool's answer
})

// ---------------------------------------------------------------- the band, stage by stage (the approved mockup)
const bandOf = async ($: any, surface = 'terminal') => $.ui.mount({ ...BAND, requestId: 'band-' + Math.random(), surface } as any)
const textOn = async (band: any, text: string) => band.find({ type: 'Text', text })

test('Connecting: after Accept, the moving dots and End, until the call is live', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, holdPath: '/api/lite/call', waitMore: true }
  const clock = mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  const r: any = await $.tool.call({ tool: 'mcp__standby__call' } as any)
  expect(r.result).toBe('Accepted: the call is starting')
  await settle()
  let band = await bandOf($)
  expect(await textOn(band, 'Connecting .')).toBeDefined()
  expect(await textOn(band, '(0s)')).toBeUndefined()                            // no seconds: the words and the dots
  expect(await band.find({ key: 'end' })).toBeDefined()
  await new Promise(r => setTimeout(r, 1100))                                   // the dots move once a second
  band = await bandOf($)
  expect(await textOn(band, 'Connecting . .')).toBeDefined()                   // the dots move
  seen.idle = false; seen.live = { kind: 'call', session: 'c1', started: 1 }
  seen.release(); await settle()
  band = await bandOf($)
  expect(await textOn(band, 'Connecting . .')).toBeDefined()             // the call is up, the voice not ready yet: still Connecting
  seen.more([{ call: 'state', session: 'c1', phase: 'speaking' }]); await settle()
  band = await bandOf($)
  expect(await textOn(band, 'On a call with Claude')).toBeDefined()         // its greeting: on the call
  expect(await textOn(band, 'Connecting . .')).toBeUndefined()
  seen.more(null)
})

test('End while connecting: the call is ended as soon as it starts, Ending meanwhile', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, holdPath: '/api/lite/call' }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  void $.command.run({ command: 'standby', args: 'call' })
  await settle()
  let band = await bandOf($)
  await band.press({ key: 'end' })
  await (await bandOf($)).press({ key: 'confirmYes' })
  band = await bandOf($)
  expect(await textOn(band, 'Ending the call .')).toBeDefined()
  seen.idle = false; seen.live = { kind: 'call', session: 'c1', started: 1 }
  seen.holdPath = null; seen.release(); await settle(); await settle()
  expect(seen.posts.some((p: any) => p.path === '/api/lite/call/end')).toBe(true)
})

for (const [name, ev, want] of [['listening, nothing said: Listening', { phase: 'listening' }, 'Listening'],
    ['the voice thinking: Thinking, the dots moving', { phase: 'thinking' }, '\u273b Thinking .'],
    ['work taken deeper: Working on it', { phase: 'listening', working: true }, '\u273b Working on it .']] as const)
  test('on the call, ' + name, async ($, on) => {
    const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1 } }
    mock.clock(on)
    fakeApp(on, [Object.assign({ call: 'state', session: 'c1' }, ev)], seen)
    await $.command.run({ command: 'standby', args: '' })
    await settle()
    const band = await bandOf($)
    expect(await textOn(band, 'On a call with Claude')).toBeDefined()
    expect(await textOn(band, want)).toMatchObject({ props: { dimColor: true } })
  })

test('paused: Paused on top, Resume in Pause\'s place, and how to go on under it', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, muted: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  expect(await textOn(band, 'Paused · on a call with Claude')).toBeDefined()
  expect(await band.find({ key: 'mute' })).toMatchObject({ props: { label: 'resume' } })
  expect(await textOn(band, 'Paused. Resume when you’re ready to talk')).toBeDefined()
})

test('the voice coming back: Trying again, the dots moving', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1 } }
  mock.clock(on)
  fakeApp(on, [{ call: 'state', session: 'c1', phase: 'listening', retrying_since: Date.now() / 1000 }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await textOn(await bandOf($), 'The voice isn\u2019t answering. Trying again .')).toBeDefined()
})

test('the voice stopped: what to do, with End and no Pause', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1 } }
  mock.clock(on)
  fakeApp(on, [{ call: 'state', session: 'c1', phase: 'listening', notice: 'Standby\'s voice isn\'t working (it stopped).' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  expect(await textOn(band, 'The voice stopped. End the call and start it again')).toBeDefined()
  expect(await band.find({ key: 'mute' })).toBeUndefined()
  expect(await band.find({ key: 'end' })).toBeDefined()
})

test('Ending: from End until the summary\'s row is up', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1 }, holdPath: '/api/lite/call/end' }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  void $.command.run({ command: 'standby', args: 'end' })
  await settle()
  const band = await bandOf($)
  expect(await textOn(band, 'Ending the call .')).toBeDefined()
  expect(await textOn(band, 'On a call with Claude')).toBeUndefined()
  seen.release()
})

test('stages 7b and 7c · Adding, the dots moving, then Added to Claude Code for a moment', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, holdPath: '/api/lite/card/add', cardKind: 'call' }
  const clock = mock.clock(on)
  fakeApp(on, [{ call: 'card', session: 'c1' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  ;(await bandOf($)).press({ key: 'add' })
  await settle()
  let band = await bandOf($)
  expect(await textOn(band, 'Adding to Claude Code .')).toBeDefined()
  seen.release(); await settle()
  band = await bandOf($)
  expect(await textOn(band, 'Added to Claude Code')).toBeDefined()
  expect(await textOn(band, '✓')).toBeDefined()
})

for (const [answer, said, line] of [['decline', 'Declined', 'Call declined'],
    ['expired', 'No answer: the call wasn\u2019t started', 'No answer \u00b7 the call wasn\u2019t started']] as const)
  test('' + line + ': said on the band for a moment; Claude told in the tool\'s answer', async ($, on) => {
    const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, answer }
    mock.clock(on)
    ENGINE(on)
    fakeApp(on, [], seen)
    await $.command.run({ command: 'standby', args: '' })
    const r: any = await $.tool.call({ tool: 'mcp__standby__call' } as any)
    expect(r.result).toBe(said)
    expect(await textOn(await bandOf($), line)).toBeDefined()
    expect(seen.posts.some((p: any) => p.path === '/api/lite/call')).toBe(false)
  })

test('Standby not answering during a call: the band stays and says so, then comes back', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1 } }
  const clock = mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  seen.down = true
  await clock.advance(2100); await settle()
  let band = await bandOf($)
  expect(await textOn(band, 'Standby isn’t answering · trying again .')).toBeDefined()
  seen.down = false
  await clock.advance(2100); await settle()
  band = await bandOf($)
  expect(await textOn(band, 'On a call with Claude')).toBeDefined()
})

test('listening, the same stages: Starting to listen, then Listening; Ending, then Summarizing', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, holdPath: '/api/lite/listen' }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  void $.command.run({ command: 'standby', args: 'listen' })
  await settle()
  let band = await bandOf($)
  expect(await textOn(band, 'Starting to listen .')).toBeDefined()
  seen.idle = false; seen.live = { kind: 'meeting', session: 'm1', recorded_s: 64, title: 'Planning' }
  seen.holdPath = '/api/lite/stop'; seen.release(); await settle()
  band = await bandOf($)
  expect(await textOn(band, 'Listening · Planning')).toBeDefined()
  void $.command.run({ command: 'standby', args: 'stop' })
  await settle()
  band = await bandOf($)
  expect(await textOn(band, 'Ending .')).toBeDefined()
  seen.release()
})

test('a meeting ended outside this session (Standby\u2019s window): Claude is told once, so it never says it\u2019s still recording', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'meeting', session: 'm1', recorded_s: 64, pass: 'ask', title: 'Planning' } }
  mock.clock(on)
  const session = mock.session(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(session.appended().length).toBe(0)
  seen.idle = true                                   // End in Standby's window
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const notes = session.appended().map((n: any) => n.message.content[0].text)
  expect(notes.length).toBe(1)
  expect(notes[0]).toMatch(/^\[Standby · meeting ended · \d\d:\d\d\]\nThe meeting Standby was recording \(Planning\) has ended, outside this session/)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(session.appended().length).toBe(1)          // once
})

test('a meeting or call deleted in Standby: its summary card goes from the band', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, cardKind: 'call', waitMore: true }
  mock.clock(on)
  ENGINE(on)
  fakeApp(on, [{ call: 'card', session: 'c1' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await (await bandOf($)).find({ key: 'add' })).toBeDefined()          // the call's summary, waiting for Add or Forget
  seen.more([{ deleted: true, session: 'c2' }]); await settle()
  expect(await (await bandOf($)).find({ key: 'add' })).toBeDefined()          // another session deleted: it stays
  seen.more([{ deleted: true, session: 'c1' }]); await settle()
  const band = await bandOf($)
  expect(await band.find({ key: 'add' })).toBeUndefined()
  expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()     // nothing of Standby's left
  seen.more(null)
})

test('Full window brings Standby to the front on every click, not only the first', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  for (let i = 0; i < 3; i++) await $.command.run({ command: 'standby', args: 'window' })
  expect(seen.posts.filter((p: any) => p.path === '/api/window').length).toBe(3)
  // each click: the app in front (here) hands Standby the front, after it has the right window up
  expect(seen.launched).toEqual(['open -a Standby', 'open -a Standby', 'open -a Standby'])
})

// ---------------------------------------------------------------- the terminal's letters: typed alone, then Enter
const typed = async ($: any, text: string) => $.prompt.submit({ text })
const posted = (seen: any, path: string) => seen.posts.some((p: any) => p.path === path)

test('letters · a live meeting: p pauses, nothing reaches Claude; "p" with more words is a prompt', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  expect(await textOn(band, 'p')).toMatchObject({ props: { weight: 'bold' } })
  expect(await textOn(band, ' pause')).toMatchObject({ props: { weight: 'bold' } })
  expect(await textOn(band, 'Listening · the meeting')).toBeDefined()
  await typed($, 'p')
  expect(posted(seen, '/api/lite/pause')).toBe(true)
  expect(seen.submitted.some((s: any) => s.text === 'p')).toBe(false)
  await typed($, 'p nfjlsndfj')
  expect(seen.submitted.some((s: any) => s.text === 'p nfjlsndfj')).toBe(true)
  expect(seen.posts.filter((p: any) => p.path === '/api/lite/pause').length).toBe(1)
})

test('letters · one the band doesn\'t show now is a prompt: "a" during a meeting, "x"', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  await bandOf($)
  for (const t of ['a', 'x']) await typed($, t)
  expect(seen.submitted.map((s: any) => s.text)).toEqual(['a', 'x'])
})

test('letters · a paused meeting: the state in normal brightness, p resumes', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'meeting', session: 'm1', started: 1, paused: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  expect((await textOn(band, 'Paused · the meeting')).props.dimColor).toBeFalsy()
  expect(await band.find({ key: 'pause' })).toMatchObject({ props: { label: 'resume' } })
  await typed($, 'p')
  expect(posted(seen, '/api/lite/resume')).toBe(true)
})

for (const keep of [true, false])
  test('letters · e on a meeting: the band asks "Stop listening?"; ' + (keep ? 'k keeps listening' : 'e stops'), async ($, on) => {
    const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
    mock.clock(on)
    fakeApp(on, [], seen)
    await $.command.run({ command: 'standby', args: '' })
    await settle()
    await bandOf($)
    await typed($, 'e')
    const band = await bandOf($)
    expect(await textOn(band, 'Stop listening?')).toBeDefined()
    expect(await band.find({ key: 'confirmYes' })).toMatchObject({ props: { label: 'stop' } })
    expect(await band.find({ key: 'confirmNo' })).toMatchObject({ props: { label: 'keep listening' } })
    expect(posted(seen, '/api/lite/stop')).toBe(false)
    await typed($, keep ? 'k' : 'e')
    expect(posted(seen, '/api/lite/stop')).toBe(!keep)
    if (keep) expect(await textOn(await bandOf($), 'Listening · the meeting')).toBeDefined()
    expect(seen.submitted.length).toBe(0)
  })

for (const keep of [true, false])
  test('letters · e on a call: the band asks "End the call?", e end in red; ' + (keep ? 'k keeps talking, then p pauses' : 'e ends it'), async ($, on) => {
    const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
    mock.clock(on)
    fakeApp(on, [], seen)
    await $.command.run({ command: 'standby', args: '' })
    await settle()
    await bandOf($)
    await typed($, 'e')
    const band = await bandOf($)
    expect(await textOn(band, 'End the call?')).toBeDefined()
    expect(await band.find({ key: 'confirmYes' })).toMatchObject({ props: { label: 'end' } })
    expect(await band.find({ key: 'confirmNo' })).toMatchObject({ props: { label: 'keep talking' } })
    expect(await band.find({ type: 'Text', text: 'e', color: 'red' } as any)).toBeDefined()
    await typed($, keep ? 'k' : 'e')
    expect(posted(seen, '/api/lite/call/end')).toBe(!keep)
    if (keep) {
      expect(await textOn(await bandOf($), 'On a call with Claude')).toBeDefined()
      await typed($, 'p')
      expect(seen.posts.find((p: any) => p.path === '/api/lite/mute').body).toEqual({ session: 'c1', muted: true })
    }
    expect(seen.submitted.length).toBe(0)
  })

test('letters · the question goes by itself after 10 s: the band as it was, and nothing ended', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  const clock = mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  await bandOf($)
  await typed($, 'e')
  expect(await textOn(await bandOf($), 'End the call?')).toBeDefined()
  await clock.advance(9000); await settle()
  expect(await textOn(await bandOf($), 'End the call?')).toBeDefined()
  await clock.advance(1100); await settle()
  const band = await bandOf($)
  expect(await textOn(band, 'End the call?')).toBeUndefined()
  expect(await textOn(band, 'On a call with Claude')).toBeDefined()
  await typed($, 'e')
  expect(posted(seen, '/api/lite/call/end')).toBe(false)                // e asks again, never ends at once
})

test('letters · VS Code\'s End asks on the band too: End and Keep talking as links; Keep talking keeps the call', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  await (await bandOf($, 'vscode')).press({ key: 'end', link: { href: 'https://standby.invalid/end' } } as any)
  const panel = await bandOf($, 'vscode')
  expect(await textOn(panel, 'End the call?')).toBeDefined()
  expect((await panel.find({ key: 'confirmYes' })).props).toMatchObject({ text: '[End](https://standby.invalid/confirmYes)', dimColor: false })
  expect((await panel.find({ key: 'confirmNo' })).props).toMatchObject({ text: '[Keep talking](https://standby.invalid/confirmNo)', dimColor: true })
  await panel.press({ key: 'confirmNo', link: { href: 'https://standby.invalid/confirmNo' } } as any)
  expect(posted(seen, '/api/lite/call/end')).toBe(false)
  expect(await textOn(await bandOf($, 'vscode'), 'On a call with Claude')).toBeDefined()
})

test('letters · o opens the window', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await (await bandOf($)).find({ key: 'window' })).toMatchObject({ props: { label: 'full window' } })
  await typed($, 'o')
  expect(posted(seen, '/api/window')).toBe(true)
  expect(seen.submitted.length).toBe(0)
})

test('letters · the hint under the actions the first time; gone once a letter is used', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await textOn(await bandOf($), '  type a letter and press Enter')).toMatchObject({ props: { dimColor: true } })
  expect(await textOn(await bandOf($, 'vscode'), '  type a letter and press Enter')).toBeUndefined()
  await typed($, 'p')
  expect(await textOn(await bandOf($), '  type a letter and press Enter')).toBeUndefined()
})

for (const [key, answer] of [['a', 'accept'], ['d', 'decline'], ['y', 'always']] as const)
  test('letters · the ring: ' + key + ' answers ' + answer, async ($, on) => {
    const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, hold: true }
    mock.clock(on)
    ENGINE(on)
    fakeApp(on, [], seen)
    await $.command.run({ command: 'standby', args: '' })
    const waiting = $.tool.call({ tool: 'mcp__standby__call' } as any)
    await settle()
    expect(await textOn(await bandOf($), 'Incoming call')).toBeDefined()
    await typed($, key)
    const r: any = await waiting
    expect(r.result).toBe(answer === 'decline' ? 'Declined' : 'Accepted: the call is starting')
    expect(seen.submitted.length).toBe(0)
    await settle()                                 // the call starts on the band, after the tool's answer
  })

test('letters · Connecting: e asks, then ends the call as it starts', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, holdPath: '/api/lite/call' }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  void $.command.run({ command: 'standby', args: 'call' })
  await settle()
  await bandOf($)
  await typed($, 'e')
  expect(await textOn(await bandOf($), 'End the call?')).toBeDefined()
  await typed($, 'e')
  expect(await textOn(await bandOf($), 'Ending the call .')).toBeDefined()
  seen.idle = false; seen.live = { kind: 'call', session: 'c1', started: 1 }
  seen.holdPath = null; seen.release(); await settle(); await settle()
  expect(posted(seen, '/api/lite/call/end')).toBe(true)
})

for (const [key, add] of [['a', true], ['f', false]] as const)
  test('letters · a call\'s summary: ' + key + (add ? ' adds it to Claude Code' : ' forgets it'), async ($, on) => {
    const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true, cardKind: 'call' }
    mock.clock(on)
    fakeApp(on, [{ call: 'card', session: 'c1' }], seen)
    on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
    await $.command.run({ command: 'standby', args: '' })
    await settle()
    expect(await (await bandOf($)).find({ key: 'add' })).toMatchObject({ props: { label: 'add to Claude Code' } })
    await typed($, key)
    expect(posted(seen, '/api/lite/card/add')).toBe(add)
    if (!add) expect(await (await bandOf($)).find({ key: 'forget' })).toBeUndefined()
    expect(seen.submitted.length).toBe(0)
  })

test('letters · a meeting\'s summary: c closes it, nothing to Claude', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  fakeApp(on, [{ meet: 'card', session: 'm1' }], seen)
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await textOn(await bandOf($), 'Summary · Planning')).toBeDefined()
  await typed($, 'c')
  expect(await textOn(await bandOf($), 'Summary · Planning')).toBeUndefined()
  expect(seen.submitted.length).toBe(0)
})

// ---------------------------------------------------------------- a paused call with something waiting
for (const surface of ['terminal', 'vscode'])
  test('a paused call with something waiting (' + surface + '): a red dot on Resume, and what it means under it', async ($, on) => {
    const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, muted: true, phase: 'muted' } }
    mock.clock(on)
    fakeApp(on, [{ call: 'state', session: 'c1', phase: 'muted', waiting: true }], seen)
    await $.command.run({ command: 'standby', args: '' })
    await settle()
    const band = await bandOf($, surface)
    expect(await textOn(band, ' \u2022')).toMatchObject({ props: { color: 'red' } })
    expect(await textOn(band, 'Paused. Something’s waiting: resume to hear it')).toBeDefined()
  })

test('a paused call with nothing waiting: no dot', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, muted: true, phase: 'muted' } }
  mock.clock(on)
  fakeApp(on, [{ call: 'state', session: 'c1', phase: 'muted' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await textOn(await bandOf($), ' \u2022')).toBeUndefined()
  expect(await textOn(await bandOf($), 'Paused. Resume when you’re ready to talk')).toBeDefined()
})

// ---------------------------------------------------------------- a call into the mini window, first on the band
test('a call: p pause · e end | m mini window · o full window; m then Enter sends the call to the mini window', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true, orb: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  const orb = await band.find({ key: 'mini' })
  expect(orb).toMatchObject({ props: { label: 'mini window' } })
  const order = ['mute', 'end', 'mini', 'window']
  const keys = (await band.findAll({ type: 'Button' })).map((b: any) => b.props.key ?? b.key)
  expect(keys.filter((k: any) => order.includes(k))).toEqual(order)
  expect(await textOn(band, ' | ')).toMatchObject({ props: { dimColor: true } })          // the quiet divider, once
  expect((await band.findAll({ type: 'Text' })).filter((t: any) => (t.text ?? t.props.children) === ' | ').length).toBe(1)
  await typed($, 'm')
  expect(seen.posts.find((p: any) => p.path === '/api/lite/orb').body).toEqual({ cc: 'cc-1' })
  expect(seen.submitted.length).toBe(0)
  expect(seen.status).not.toContain('The call is in the mini window.')
})

test('VS Code, a call: the Mini window link, a plain quiet link as the others, and it sends the call to the mini window', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true, orb: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await bandOf($, 'vscode')
  expect((await panel.find({ key: 'mini' })).props.text).toBe('[Mini window](https://standby.invalid/mini)')
  expect((await panel.find({ key: 'mini' })).props.dimColor).toBe(true)                // as Pause, End and the window's
  expect(await textOn(panel, '\u25cf ')).toBeUndefined()                                 // no dot of its own
  expect((await panel.findAll({ type: 'Svg' })).map((v: any) => v.props.alt)).toEqual(['Standby'])        // only the row's own mark
  // one line: the link's own box never wraps, as every row on the band
  const boxes = (await panel.findAll({ type: 'Box' })).filter((b: any) => b.props.flexDirection === 'row' && JSON.stringify(b).includes('standby.invalid/mini'))
  expect(boxes.length).toBeGreaterThan(0)
  for (const b of boxes) expect(b.props.flexWrap).toBe('nowrap')
  // and at a narrow width the call's words still lead the row, cut with … rather than wrapped
  const narrow = await $.ui.mount({ ...BAND, requestId: 'narrow', surface: 'vscode', viewport: { columns: 60, rows: 40 },
    props: { ...BAND.props, bodyColumns: 60 } } as any)
  expect(await textOn(narrow, 'On a call with Claude')).toMatchObject({ props: { wrap: 'truncate-end' } })
  await panel.press({ key: 'mini', link: { href: 'https://standby.invalid/mini' } } as any)
  expect(posted(seen, '/api/lite/orb')).toBe(true)
})

test('the mini window: never for a meeting, and not where Standby has none', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true, orb: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await (await bandOf($)).find({ key: 'mini' })).toBeUndefined()
  await typed($, 'm')
  expect(seen.submitted.map((s: any) => s.text)).toEqual(['m'])
  seen.live = { kind: 'call', session: 'c1', started: 1, phase: 'listening' }; seen.can = { listen: true, window: true }
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await (await bandOf($)).find({ key: 'mini' })).toBeUndefined()
})

// Last: no test above made Standby use Claude Code's own question dialog, anywhere (the user, 2026-10-22)
test('Standby never uses Claude Code\'s own question dialog ($.ui.ask): not once in all the tests above', () => {
  expect(DIALOGS).toEqual([])
})

// ---------------------------------------------------------------- typed into the session's chat during a call
test('on a call, what the person types reaches the call: "typed", with how many images', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  await bandOf($)
  await $.prompt.submit({ text: 'what does this error mean?' })
  const typed = seen.posts.filter((p: any) => p.path === '/api/lite/progress' && p.body.kind === 'typed')
  expect(typed.map((p: any) => p.body)).toEqual([{ session: 'c1', kind: 'typed', text: 'what does this error mean?', images: 0 }])
  expect(seen.submitted.some((s: any) => s.text === 'what does this error mean?')).toBe(true)     // and it goes to Claude as ever
})

test('on a call: the voice\'s own hand-off and a band key are never "typed"; images are counted, never sent', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [{ call: 'handoff', session: 'c1', id: 'c1-9', text: 'From your call: run the tests' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  await bandOf($)
  await typed($, 'p')                                       // a band key: pause
  const kinds = () => seen.posts.filter((p: any) => p.path === '/api/lite/progress').map((p: any) => p.body.kind)
  expect(kinds()).not.toContain('typed')
  expect(seen.submitted.some((s: any) => s.text === 'From your call: run the tests')).toBe(true)
  await $.prompt.submit({ text: 'look at this', attachments: [{ type: 'image', mediaType: 'image/png' }, { type: 'image' }] } as any)
  const t = seen.posts.filter((p: any) => p.path === '/api/lite/progress' && p.body.kind === 'typed')
  expect(t.map((p: any) => [p.body.text, p.body.images])).toEqual([['look at this', 2]])
  expect(JSON.stringify(t[0].body)).not.toContain('image/png')
})

test('no call (a meeting, or nothing live): what they type is never "typed"', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  await $.prompt.submit({ text: 'what did they decide?' })
  expect(seen.posts.some((p: any) => p.body && p.body.kind === 'typed')).toBe(false)
})

// The row's order (the user, 2026-10-23): the call's own actions, a quiet "|", then the windows
const rowOf = async (band: any) => (await band.findAll({ type: 'Markdown' } as any)).map((m: any) => m.props.text.replace(/\]\(.*\)/, '').replace('[', ''))
const divs = async (band: any) => (await band.findAll({ type: 'Text' })).filter((t: any) => (t.text ?? t.props.children) === '|' && t.props.dimColor).length

test('VS Code, a call: Pause, End | Mini window, Full window; Resume keeps its dot', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true, orb: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [{ call: 'state', session: 'c1', phase: 'listening' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await bandOf($, 'vscode')
  expect(await rowOf(panel)).toEqual(['Pause', 'End', 'Mini window', 'Full window'])
  expect(await divs(panel)).toBe(1)
})

test('VS Code, a paused call with something waiting: Resume first, with its dot', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true, orb: true },
    live: { kind: 'call', session: 'c1', started: 1, muted: true, phase: 'muted' } }
  mock.clock(on)
  fakeApp(on, [{ call: 'state', session: 'c1', phase: 'muted', waiting: true }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await bandOf($, 'vscode')
  expect(await rowOf(panel)).toEqual(['Resume', 'End', 'Mini window', 'Full window'])
  expect(await textOn(panel, ' \u2022')).toMatchObject({ props: { color: 'red' } })
})

test('a failed call: End | Mini window, Full window', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true, orb: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening' } }
  mock.clock(on)
  fakeApp(on, [{ call: 'state', session: 'c1', phase: 'listening', notice: 'The voice stopped' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await rowOf(await bandOf($, 'vscode'))).toEqual(['End', 'Mini window', 'Full window'])
  expect(await divs(await bandOf($, 'vscode'))).toBe(1)
  const keys = (await (await bandOf($)).findAll({ type: 'Button' })).map((b: any) => b.props.key ?? b.key)
  expect(keys).toEqual(['end', 'mini', 'window'])
})

test('a meeting: Pause, End | Full window; with no window, no divider left over', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await rowOf(await bandOf($, 'vscode'))).toEqual(['Pause', 'End', 'Full window'])
  expect(await divs(await bandOf($, 'vscode'))).toBe(1)
  seen.can = { listen: true }
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await rowOf(await bandOf($, 'vscode'))).toEqual(['Pause', 'End'])
  expect(await divs(await bandOf($, 'vscode'))).toBe(0)
  expect((await (await bandOf($)).findAll({ type: 'Text' })).some((t: any) => (t.text ?? t.props.children) === ' | ')).toBe(false)
})

// ---------------------------------------------------------------- what macOS refuses, or will ask for
const MIC_NO = 'Standby can’t hear your mic · allow it in Privacy & Security > Microphone'
const SOUND_NO = 'Standby can’t hear the meeting’s sound · allow it in Privacy & Security > Screen & System Audio Recording'
const MIC_ASK = 'macOS will ask for your microphone · choose Allow'
const SOUND_ASK = 'macOS will ask for Screen & System Audio Recording · choose Allow; Standby takes only the sound'
const meetingWith = (more: any) => ({ kind: 'meeting', session: 'm1', started: 1, capture: 'auto', ...more })

test('access · the mic refused: said in the warning colour in place of the words, s settings opens its pane', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: meetingWith({ mic: 'denied', meeting_audio: 'denied' }) }
  mock.clock(on)
  fakeApp(on, [{ meet: 'line', i: 1, t: 1760700000, speaker: 1, name: 'Speaker 1', text: 'We ship Friday.' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  expect(await textOn(band, MIC_NO)).toMatchObject({ props: { color: '#d29922', wrap: 'truncate-end' } })    // the mic before the sound
  expect(await textOn(band, SOUND_NO)).toBeUndefined()
  expect(await textOn(band, 'Speaker 1: We ship Friday.')).toBeUndefined()
  const keys = (await band.findAll({ type: 'Button' })).map((b: any) => b.props.key ?? b.key)
  expect(keys).toEqual(['pause', 'stop', 'window', 'settings'])
  await typed($, 's')
  expect(seen.posts.find((p: any) => p.path === '/api/lite/permission').body).toEqual({ session: 'm1' })
  expect(seen.submitted.some((s: any) => s.text === 's')).toBe(false)
  expect(seen.status).toEqual(['Standby is on for this session'])     // no toast for it: System Settings opens
})

test('access · the meeting’s sound refused, in VS Code: its line, and Open Settings after Full window', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: meetingWith({ mic: 'allowed', meeting_audio: 'denied' }) }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const panel = await bandOf($, 'vscode')
  expect(await textOn(panel, SOUND_NO)).toMatchObject({ props: { color: '#d29922' } })
  expect(await rowOf(panel)).toEqual(['Pause', 'End', 'Full window', 'Open Settings'])
  expect((await panel.find({ key: 'settings' })).props.dimColor).toBe(true)
  await panel.press({ key: 'settings', link: { href: 'https://standby.invalid/settings' } } as any)
  expect(posted(seen, '/api/lite/permission')).toBe(true)
})

test('access · heads-ups: dim, the mic first, then the meeting’s sound; no Settings; gone once allowed', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: meetingWith({ mic: 'ask', meeting_audio: 'ask' }) }
  mock.clock(on)
  fakeApp(on, [{ meet: 'line', i: 1, t: 1760700000, speaker: 1, name: 'Speaker 1', text: 'We ship Friday.' }], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  let band = await bandOf($)
  expect(await textOn(band, MIC_ASK)).toMatchObject({ props: { dimColor: true } })
  expect(await band.find({ key: 'settings' })).toBeUndefined()
  seen.live = meetingWith({ mic: 'allowed', meeting_audio: 'ask' })
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  band = await bandOf($)
  expect(await textOn(band, SOUND_ASK)).toMatchObject({ props: { dimColor: true } })
  seen.live = meetingWith({ mic: 'allowed', meeting_audio: 'not_asked' })     // no app playing: macOS won't ask yet
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await textOn(await bandOf($), SOUND_ASK)).toBeUndefined()
  seen.live = meetingWith({ mic: 'allowed', meeting_audio: 'allowed' })
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  band = await bandOf($)
  expect(await textOn(band, SOUND_ASK)).toBeUndefined()
  expect(await textOn(band, 'Speaker 1: We ship Friday.')).toBeDefined()
})

test('access · a call: the mic only, never the meeting’s sound; the voice failing still comes first', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening', mic: 'allowed', meeting_audio: 'ask' } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(await textOn(await bandOf($), SOUND_ASK)).toBeUndefined()
  seen.live = { kind: 'call', session: 'c1', started: 1, phase: 'listening', mic: 'denied' }
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  expect(await textOn(band, MIC_NO)).toMatchObject({ props: { color: '#d29922' } })
  expect((await band.findAll({ type: 'Button' })).map((b: any) => b.props.key ?? b.key)).toEqual(['mute', 'end', 'window', 'settings'])
})

test('access · a folder macOS refuses the call’s Claude: said plainly, with Settings', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], can: { listen: true, window: true },
    live: { kind: 'call', session: 'c1', started: 1, phase: 'listening', mic: 'allowed', folder_denied: 'Desktop' } }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  const band = await bandOf($)
  expect(await textOn(band, 'Standby can’t read your Desktop folder · allow it in Privacy & Security > Files & Folders'))
    .toMatchObject({ props: { color: '#d29922' } })
  await typed($, 's')
  expect(posted(seen, '/api/lite/permission')).toBe(true)
})

test('a first call the app takes long to start (macOS asking for the mic): Connecting goes on, never "Standby isn’t running"', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  fakeApp(on, [], seen)
  seen.slowStart = () => { seen.idle = false; seen.live = { kind: 'call', session: 'c9', started: 1, phase: 'listening' } }   // it comes up meanwhile
  const r = await $.command.run({ command: 'standby', args: 'call' })
  expect(r.text).toBe('On a call. Talk; real work comes here.')
  expect(seen.status.some((t: string) => t.includes('isn’t running'))).toBe(false)
})

test('a start that fails while the app really is down still says so', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [], idle: true }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  seen.slowStart = () => { seen.down = true }
  const r = await $.command.run({ command: 'standby', args: 'call' })
  expect(r.text).toContain('isn’t running')
})

test('every request to Standby carries this launch\'s token, from its file; the stream reads it from a file, never the command line', async ($, on) => {
  const seen: any = { posts: [], submitted: [], aborted: [], status: [] }
  mock.clock(on)
  fakeApp(on, [], seen)
  await $.command.run({ command: 'standby', args: '' })
  await settle()
  expect(seen.tokens.length).toBeGreaterThan(0)
  expect(seen.tokens.every((t: any) => t === 'tok-launch-1')).toBe(true)
  expect(seen.spawned).toContain('-H')
  expect(seen.spawned[seen.spawned.indexOf('-H') + 1]).toBe('@/tmp/standby-test-home/.standby/ui.header')
  expect(seen.spawned.join(' ')).not.toContain('tok-launch-1')
})
