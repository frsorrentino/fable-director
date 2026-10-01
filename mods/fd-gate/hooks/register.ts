import type { Register } from 'claude-code'

// Prototype for fable-director: the core of pre-delegation-gate.py as a mod.
// Same budget file, same verdicts (no budget / corrupted / stale >24h /
// flagged → deny; open and fresh → allow); the advisory checks (quota guard,
// window fit, cost checkpoint...) stay in the Python hook. Measured against
// it for latency and for equal refusals; not wired into the plugin.

const hex = (buf: ArrayBuffer) =>
  [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('')

// cwd_slug() of fd-telemetry.py, byte for byte.
export const cwdSlug = async (cwd: string) => {
  const s = cwd.replaceAll('\\', '/')
  const base = s.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return `${base}-${hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))).slice(0, 8)}`
}

export type Verdict = { kind: 'allow' } | { kind: 'no_budget' | 'stale_budget' | 'flagged' }

export const judge = (raw: string | undefined, nowMs: number): Verdict => {
  let b: Record<string, unknown> | undefined
  try { b = raw === undefined ? undefined : JSON.parse(raw) } catch { b = undefined }
  if (b && typeof b === 'object' && b.status === 'open') {
    const t = Date.parse(String(b.declared_at ?? ''))
    if (!Number.isFinite(t)) return { kind: 'no_budget' }
    return nowMs - t <= 86400_000 ? { kind: 'allow' } : { kind: 'stale_budget' }
  }
  if (b && typeof b === 'object' && b.status === 'flagged') return { kind: 'flagged' }
  return { kind: 'no_budget' }
}

const REASON = {
  no_budget: '✕ FABLE-DIRECTOR delegation DENIED — no open pre-budget for this cwd. Open one with fd-telemetry.py budget-open, then retry.',
  stale_budget: '✕ FABLE-DIRECTOR delegation DENIED — this cwd\'s open budget is older than 24h. Close it, open the current task\'s pre-budget, then retry.',
  flagged: '✕ FABLE-DIRECTOR delegation DENIED — this cwd\'s budget is FLAGGED (≥3× bust). Close the post-mortem, then open the new pre-budget.',
} as const

// Executors that must not inherit the top model: agent → model.
const PINNED: Record<string, string> = { 'fable-director:fd-executor': 'sonnet' }

export const register: Register = on => {
  for (const tool of ['Agent', 'Task', 'Workflow'] as const) {
    on('tool.call', { tool }, async ($, e, next) => {
      const home = await $.env.get('HOME')
      if (!home) return next(e)  // fail-open, as the Python gate
      const file = `${home}/.claude/fable-director/budgets/${await cwdSlug(await $.session.cwd())}.json`
      const raw = await $.fs.read(file).catch(() => undefined)
      const v = judge(raw, Date.now())
      return v.kind === 'allow' ? next(e) : { deny: REASON[v.kind] }
    })
  }

  on('agent.spawn', ($, e, next) => {
    const model = PINNED[e.subagentType]
    // Even when the caller asks for another one: the pin is the point.
    return model && e.model !== model ? next({ ...e, model }) : next(e)
  })
}
