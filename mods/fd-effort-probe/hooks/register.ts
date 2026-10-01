import type { Register, TurnStepInput } from 'claude-code'

// Experiment for fable-director, never on by default: with FD_EFFORT_PROBE set
// (low|medium|high), the main thread's continuation steps — the requests after
// a tool result, index > 0 — go out at that effort, while step 0, where the
// turn is planned, keeps the session's. Kernel reading: the top model plans
// and judges at full effort, the mechanical follow-up is cheap. FD_MODEL_PROBE
// does the same for the model. Every step is logged with its usage, so the
// cost of a run with and without the probe can be compared.

type Effort = NonNullable<TurnStepInput['effort']>
const EFFORTS = new Set(['low', 'medium', 'high', 'xhigh', 'max'])

export const plan = (e: Pick<TurnStepInput, 'index' | 'agentId'>, effort?: string, model?: string) => {
  if (e.agentId !== undefined || e.index === 0) return {}
  const out: { effort?: Effort; model?: string } = {}
  if (effort && EFFORTS.has(effort)) out.effort = effort as Effort
  if (model) out.model = model
  return out
}

export const register: Register = on => {
  const rows: string[] = []
  on('turn.step', async function* ($, e, next) {
    const change = plan(e, await $.env.get('FD_EFFORT_PROBE'), await $.env.get('FD_MODEL_PROBE'))
    const sent = { ...e, ...change }
    const r = yield* next(sent)
    const home = await $.env.get('HOME')
    if (home) {
      rows.push(JSON.stringify({
        session: await $.session.id(), turn: e.turnId, index: e.index, agent: e.agentId ?? null,
        model: sent.model, effort: sent.effort ?? null, rewritten: Object.keys(change),
        usage: r?.usage ?? null,
      }))
      await $.fs.write(`${home}/.claude/fable-director/mods/effort-probe-${await $.session.id()}.jsonl`,
        rows.join('\n') + '\n')
    }
    return r
  })
}
