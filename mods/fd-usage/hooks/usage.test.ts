import { test, expect } from 'claude-code/testing'
import { accountHash, toQuota } from './register'

test('account hash matches statusline-ctx.sh', async () => {
  // python3 -c "import hashlib;print(hashlib.sha256(b'/home/u/.claude').hexdigest()[:8])"
  expect(await accountHash('/home/u/.claude')).toBe('99b1807e')
})

test('rate limits map to quota-<acct>.json keys', () => {
  const q = toQuota({
    rateLimits: [
      { kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-01T22:00:00.000Z' },
      { kind: 'seven_day', percentUsed: 6, resetsAt: '2026-10-07T09:00:00.000Z' },
      { kind: 'spend_limit', percentUsed: 1 },
    ],
    cost: { usd: 1.234567 },
    context: { window: 200000 } as never,
  })
  expect(q.five_hour_used_pct).toBe(23.5)
  expect(q.five_hour_resets_at).toBe(1790892000)
  expect(q.weekly_used_pct).toBe(6)
  expect(q.weekly_resets_at).toBe(1791363600)
  expect(q.unknown_buckets).toEqual(['spend_limit'])
  expect(q.cost_usd).toBe(1.2346)
})
