import { test, expect } from 'claude-code/testing'
import { accountHash, merge, readings, same, snapshot } from './usage'

const R5 = 1790892000, RW = 1791363600

test('account hash matches statusline-ctx.sh', async () => {
  expect(await accountHash('/home/u/.claude')).toBe('99b1807e')
})

test('readings use the statusline keys', () => {
  expect(readings([
    { kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-01T22:00:00.000Z' },
    { kind: 'seven_day', percentUsed: 6, resetsAt: '2026-10-07T09:00:00.000Z' },
    { kind: 'spend_limit', percentUsed: 1 },
  ])).toEqual({ five_hour_used_pct: 23.5, five_hour_resets_at: R5, weekly_used_pct: 6, weekly_resets_at: RW, unknown_buckets: ['spend_limit'] })
})

test('same window: the higher value wins', () => {
  const m = merge({ five_hour_used_pct: 10, five_hour_resets_at: R5 }, { five_hour_used_pct: 30, five_hour_resets_at: R5 + 60 }, R5 - 3600)
  expect(m.five_hour_used_pct).toBe(30)
})

test('later reset is a new window, earlier reset an old reading', () => {
  expect(merge({ five_hour_used_pct: 2, five_hour_resets_at: R5 + 18000 }, { five_hour_used_pct: 90, five_hour_resets_at: R5 }, R5).five_hour_used_pct).toBe(2)
  const m = merge({ five_hour_used_pct: 2, five_hour_resets_at: R5 - 18000 }, { five_hour_used_pct: 40, five_hour_resets_at: R5 }, R5 - 20000)
  expect(m.five_hour_used_pct).toBe(40)
  expect(m.five_hour_resets_at).toBe(R5)
})

test('missing bucket kept until it expires', () => {
  const old = { weekly_used_pct: 6, weekly_resets_at: RW, five_hour_used_pct: 50, five_hour_resets_at: R5 }
  expect(merge({ weekly_used_pct: 6, weekly_resets_at: RW }, old, R5 - 60).five_hour_used_pct).toBe(50)
  expect(merge({ weekly_used_pct: 6, weekly_resets_at: RW }, old, R5 + 60).five_hour_used_pct).toBe(undefined)
})

test('6 and 6.0 are the same file', () => {
  expect(same({ weekly_used_pct: 6 }, { weekly_used_pct: 6.0 })).toBe(true)
})

test('snapshot in claude-hud schema', () => {
  expect(snapshot({ five_hour_used_pct: 23.5, five_hour_resets_at: R5 }, 0)).toEqual({
    updated_at: '1970-01-01T00:00:00.000Z', five_hour: { used_percentage: 24, resets_at: '2026-10-01T22:00:00.000Z' },
  })
})
