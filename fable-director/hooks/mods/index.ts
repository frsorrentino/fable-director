import type { Register } from 'claude-code'
import { accountHash, merge, readings, same, snapshot, type Quota } from './usage'

// fable-director's hooks module (Claude Code ≥ 2.1.287 with Mods on). Every
// classic hook in hooks.json keeps running: this module only adds what the
// classic hooks cannot do. Where it does not load, the plugin works as before.

// Executors that must not run on the top model, whatever the caller asks.
const PINNED: Record<string, string> = { 'fable-director:fd-executor': 'sonnet' }

const parse = (raw: string | undefined): Quota => {
  try {
    const d = raw === undefined ? {} : JSON.parse(raw)
    return d && typeof d === 'object' && !Array.isArray(d) ? d : {}
  } catch {
    return {}
  }
}

export const register: Register = on => {
  on('session.measure', async ($, e, next) => {
    if (!e.changed.includes('rateLimits')) return next(e)
    const q = readings(e.rateLimits)
    if (q.five_hour_used_pct === undefined && q.weekly_used_pct === undefined) return next(e)
    const home = (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE'))
    if (!home) return next(e)
    try {
      const dir = `${home}/.claude/fable-director`
      const acct = await accountHash((await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${home}/.claude`)
      const qf = `${dir}/quota-${acct}.json`
      const old = parse(await $.fs.read(qf).catch(() => undefined))
      const now = await $.clock.now()
      const mq = merge(q, old, Math.floor(now / 1000))
      if (!same(mq, old)) {
        await $.fs.write(qf, JSON.stringify(mq))
        await $.fs.write(`${dir}/usage-snapshot-${acct}.json`, JSON.stringify(snapshot(mq, now)))
        if (mq.weekly_used_pct !== old.weekly_used_pct || mq.five_hour_used_pct !== old.five_hour_used_pct) {
          const hf = `${dir}/quota-history-${acct}.jsonl`
          const prev = (await $.fs.read(hf).catch(() => '')).split('\n').filter(Boolean)
          prev.push(JSON.stringify({
            ts: new Date(now).toISOString().replace(/\.\d+Z$/, 'Z'),
            w: mq.weekly_used_pct ?? null, r: mq.five_hour_used_pct ?? null,
          }))
          await $.fs.write(hf, prev.slice(-300).join('\n') + '\n')
        }
      }
    } catch {
      // best effort, as the statusline: a quota file is never worth an error
    }
    return next(e)
  })

  on('agent.spawn', ($, e, next) => {
    const model = PINNED[e.subagentType]
    return model && e.model !== model ? next({ ...e, model }) : next(e)
  })
}
