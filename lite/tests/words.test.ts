import { expect, test } from 'claude-code/testing'
import { joined, continues, noteLine, startNote } from '../hooks/words.js'

test('lines split at pauses join into the sentence; a finished sentence, another speaker or a long gap does not', () => {
  const L = (t: number, end: number, text: string, speaker = 1) => ({ t, end, text, speaker })
  const out = joined([L(126, 128, 'It didn\'t actually open'), L(128.1, 129.8, 'uh, in a'), L(129.9, 132.7, 'It did open a new window, but it didn\'t'),
    L(132.8, 136, 'change the focus of the screen.'), L(137.1, 140, 'Which I clicked on to there.'), L(140.5, 141, 'Yes', 2), L(150, 151, 'later', 2)])
  expect(out.map((l: any) => l.text)).toEqual([
    'It didn\'t actually open uh, in a It did open a new window, but it didn\'t change the focus of the screen.',
    'Which I clicked on to there.', 'Yes', 'later'])
  expect(continues(L(0, 1, 'Okay?'), L(1.2, 2, 'next'))).toBe(false)
})

test('the note that a meeting started reads as one dim line in the chat, the batches as before', () => {
  const n = startNote('Planning', 1760700000)
  expect(n.startsWith('[Standby · meeting started · ')).toBe(true)
  expect(noteLine(n)).toMatch(/^Standby · meeting started \d\d:\d\d$/)
  expect(noteLine('[Standby · meeting · 10:00-10:02]\nNadia: ship')).toBe('Standby · meeting lines 10:00 to 10:02')
  expect(noteLine('[Standby · call summary · 14:02-14:20]\nThe TTL stays an hour.')).toBe('Standby · call summary 14:02 to 14:20')
})
