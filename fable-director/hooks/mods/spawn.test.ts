import { test, expect } from 'claude-code/testing'

const spawn = (subagentType: string, model?: string, extra: Record<string, unknown> = {}) => ({
  tool_use_id: 't1', prompt: 'ok', description: 'probe', subagentType, model,
  provider: { plugin: 'fable-director', tier: 'user' }, parentModel: 'claude-opus-5-5',
  fork: false, permissionMode: 'default', background: false, ...extra,
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

// Claude Code 2.1.300: a workflow script's agent() can be rewritten too.
test('a workflow agent of type fd-executor is pinned like a tool call', async ($, on) => {
  on('agent.spawn', (_$, e) => ({ model: e.model ?? 'inherit' }))
  const wf = { workflow: { runId: 'wf_test', label: 'read' } }
  expect((await $.agent.spawn(spawn('fable-director:fd-executor', 'opus', wf))).model).toBe('sonnet')
  expect((await $.agent.spawn(spawn('general-purpose', 'opus', wf))).model).toBe('opus')
})

// Claude Code 2.1.299: agent.spawn also runs when a finished agent is resumed.
test('a resumed agent keeps the model it already runs on', async ($, on) => {
  on('agent.spawn', (_$, e) => ({ model: e.model ?? 'inherit' }))
  const res = { resume: { agentId: 'a1' } }
  expect((await $.agent.spawn(spawn('fable-director:fd-executor', 'opus', res))).model).toBe('opus')
  expect((await $.agent.spawn(spawn('fable-director:fd-executor', undefined, res))).model).toBe('inherit')
})

// Claude Code 2.1.302: forks are on by default in `claude -p` too. A fork
// inherits the parent's model whatever is asked: nothing to pin, and the
// budget gate on PreToolUse Agent counts and refuses it like any delegation
// (tests/fork-headless-verify.py).
test('a fork goes through untouched', async ($, on) => {
  on('agent.spawn', (_$, e) => ({ model: e.model ?? 'inherit' }))
  expect((await $.agent.spawn(spawn('fork', undefined, { fork: true }))).model).toBe('inherit')
  // The module leaves an asked model alone; the engine ignores it on a fork.
  expect((await $.agent.spawn(spawn('fork', 'opus', { fork: true }))).model).toBe('opus')
})
