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
      // Only data files read by fable-director's own statusline and scripts,
      // never a build, start-up, settings or instructions file. Each path is
      // spelled out in the call so the directory review can read it.
      const acct = await accountHash((await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${home}/.claude`)
      const old = parse(await $.fs.read(`${home}/.claude/fable-director/quota-${acct}.json`).catch(() => undefined))
      const now = await $.clock.now()
      const mq = merge(q, old, Math.floor(now / 1000))
      if (!same(mq, old)) {
        await $.fs.write(`${home}/.claude/fable-director/quota-${acct}.json`, JSON.stringify(mq))
        await $.fs.write(`${home}/.claude/fable-director/usage-snapshot-${acct}.json`, JSON.stringify(snapshot(mq, now)))
        if (mq.weekly_used_pct !== old.weekly_used_pct || mq.five_hour_used_pct !== old.five_hour_used_pct) {
          const prev = (await $.fs.read(`${home}/.claude/fable-director/quota-history-${acct}.jsonl`).catch(() => '')).split('\n').filter(Boolean)
          prev.push(JSON.stringify({
            ts: new Date(now).toISOString().replace(/\.\d+Z$/, 'Z'),
            w: mq.weekly_used_pct ?? null, r: mq.five_hour_used_pct ?? null,
          }))
          await $.fs.write(`${home}/.claude/fable-director/quota-history-${acct}.jsonl`, prev.slice(-300).join('\n') + '\n')
        }
      }
    } catch {
      // best effort, as the statusline: a quota file is never worth an error
    }
    return next(e)
  })

  // Also raised for a workflow script's agent() (rewritable from Claude Code
  // 2.1.300) and when a finished agent is resumed (2.1.299, `resume` set). A
  // resumed agent keeps the model it already runs on: the pin applied when it
  // first started, and a switch mid-agent would re-write its whole context into
  // the cache at the new model's price. The engine honours only a deny there.
  on('agent.spawn', ($, e, next) => {
    if ((e as { resume?: unknown }).resume !== undefined) return next(e)
    const model = PINNED[e.subagentType]
    return model && e.model !== model ? next({ ...e, model }) : next(e)
  })
}
