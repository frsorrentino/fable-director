import { test, expect } from 'claude-code/testing'

const spawn = (subagentType: string, model?: string) => ({
  tool_use_id: 't1', prompt: 'ok', description: 'probe', subagentType, model,
  provider: { plugin: 'fable-director', tier: 'user' }, parentModel: 'claude-opus-5-5',
  fork: false, permissionMode: 'default', background: false,
}) as never

test('fd-executor runs on sonnet even when opus is asked', async ($, on) => {
  on('agent.spawn', (_$, e) => ({ model: e.model ?? 'inherit' }))
  expect((await $.agent.spawn(spawn('fable-director:fd-executor', 'opus'))).model).toBe('sonnet')
  expect((await $.agent.spawn(spawn('fable-director:fd-executor'))).model).toBe('sonnet')
})

test('other agents keep what was asked', async ($, on) => {
  on('agent.spawn', (_$, e) => ({ model: e.model ?? 'inherit' }))
  expect((await $.agent.spawn(spawn('general-purpose', 'opus'))).model).toBe('opus')
  expect((await $.agent.spawn(spawn('Explore'))).model).toBe('inherit')
})
