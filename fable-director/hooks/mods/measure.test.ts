import { test, expect, mock } from 'claude-code/testing'
import type { On } from 'claude-code'

const HOME = '/home/u'
const DIR = `${HOME}/.claude/fable-director`
const ACCT = '99b1807e' // sha256('/home/u/.claude')[:8]

// The file system beneath the plugin, in memory.
const memfs = (on: On, files: Record<string, string>) => {
  on('fs.read', (_$, e) => {
    const text = files[e.path]
    return text === undefined ? { deny: `ENOENT: ${e.path}` } : { value: text }
  })
  on('fs.write', (_$, e) => { files[e.path] = e.text; return { value: undefined } })
  on('session.measure', (_$, e) => ({ changed: [...e.changed] }))
  return files
}

const measure = (pct: number) => ({
  context: { window: 200000 }, changed: ['rateLimits'],
  rateLimits: [
    { kind: 'five_hour', percentUsed: pct, resetsAt: '2026-10-01T22:00:00.000Z' },
    { kind: 'seven_day', percentUsed: 6, resetsAt: '2026-10-07T09:00:00.000Z' },
  ],
}) as never

test('a stale quota file is corrected; snapshot and history follow', async ($, on) => {
  mock.env(on, { HOME })
  mock.clock(on, { now: 1790880000000 })
  const files = memfs(on, {
    [`${DIR}/quota-${ACCT}.json`]: JSON.stringify({ five_hour_used_pct: 99, five_hour_resets_at: 1790874000 }),
  })
  await $.session.measure(measure(8))
  expect(JSON.parse(files[`${DIR}/quota-${ACCT}.json`] ?? '{}')).toEqual({
    five_hour_used_pct: 8, five_hour_resets_at: 1790892000, weekly_used_pct: 6, weekly_resets_at: 1791363600,
  })
  expect(JSON.parse(files[`${DIR}/usage-snapshot-${ACCT}.json`] ?? '{}').five_hour.used_percentage).toBe(8)
  const hist = (files[`${DIR}/quota-history-${ACCT}.jsonl`] ?? '').trim().split('\n')
  expect(hist.length).toBe(1)
  expect(JSON.parse(hist[0] ?? '{}').r).toBe(8)
})

test('the same reading again writes nothing', async ($, on) => {
  mock.env(on, { HOME })
  mock.clock(on, { now: 1790880000000 })
  const files = memfs(on, {})
  await $.session.measure(measure(8))
  const before = { ...files }
  await $.session.measure(measure(8))
  expect(files).toEqual(before)
  expect((files[`${DIR}/quota-history-${ACCT}.jsonl`] ?? '').trim().split('\n').length).toBe(1)
})

test('no HOME, nothing written', async ($, on) => {
  mock.env(on, {})
  const files = memfs(on, {})
  await $.session.measure(measure(8))
  expect(Object.keys(files).length).toBe(0)
})
