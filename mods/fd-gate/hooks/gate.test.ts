import { test, expect } from 'claude-code/testing'
import { cwdSlug, judge } from './register'

const NOW = Date.parse('2026-10-01T20:00:00Z')

test('slug matches fd-telemetry.py cwd_slug', async () => {
  // python3: cwd_slug('/home/u/proj.x') and cwd_slug('E:\\work\\a b')
  expect(await cwdSlug('/home/u/proj.x')).toBe('home-u-proj-x-7986da8f')
  expect(await cwdSlug('E:\\work\\a b')).toBe('E-work-a-b-abeca30f')
})

test('verdicts match pre-delegation-gate.py', () => {
  expect(judge(undefined, NOW).kind).toBe('no_budget')
  expect(judge('{not json', NOW).kind).toBe('no_budget')
  expect(judge('{"status":"closed"}', NOW).kind).toBe('no_budget')
  expect(judge('{"status":"open"}', NOW).kind).toBe('no_budget')
  expect(judge('{"status":"open","declared_at":"2026-10-01T19:00:00Z"}', NOW).kind).toBe('allow')
  expect(judge('{"status":"open","declared_at":"2026-09-29T19:00:00Z"}', NOW).kind).toBe('stale_budget')
  expect(judge('{"status":"flagged"}', NOW).kind).toBe('flagged')
})
