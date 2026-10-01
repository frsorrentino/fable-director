import { test, expect } from 'claude-code/testing'
import { plan } from './register'

test('step 0 keeps the session effort', () => {
  expect(plan({ index: 0 }, 'low')).toEqual({})
})
test('continuation steps take the probe effort and model', () => {
  expect(plan({ index: 2 }, 'low', 'claude-sonnet-5-5')).toEqual({ effort: 'low', model: 'claude-sonnet-5-5' })
})
test('subagent steps are left alone', () => {
  expect(plan({ index: 3, agentId: 'a1' }, 'low')).toEqual({})
})
test('no probe set, no rewrite; unknown effort ignored', () => {
  expect(plan({ index: 1 })).toEqual({})
  expect(plan({ index: 1 }, 'turbo')).toEqual({})
})
