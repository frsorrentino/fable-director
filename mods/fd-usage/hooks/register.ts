import type { Register, SessionMeasureInput } from 'claude-code'

// Prototype for fable-director: the engine pushes the status line's figures
// (session.measure), so quotas reach the gates and /fable-director:status even
// where no status line runs (`claude -p`, phone, remote). It writes a twin of
// quota-<acct>.json, same keys, in mods/usage-<acct>.json: the statusline file
// stays the reference until the two are compared on real sessions.

const hex = (buf: ArrayBuffer) =>
  [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('')

// Same account hash as statusline-ctx.sh: sha256(CLAUDE_CONFIG_DIR or ~/.claude)[:8].
export const accountHash = async (configDir: string) =>
  hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(configDir))).slice(0, 8)

const epoch = (iso?: string) => {
  const ms = iso ? Date.parse(iso) : NaN
  return Number.isFinite(ms) ? Math.round(ms / 1000) : undefined
}

// quota-<acct>.json keys, as statusline-ctx.sh writes them.
export const toQuota = (e: Pick<SessionMeasureInput, 'rateLimits' | 'cost' | 'context'>) => {
  const q: Record<string, unknown> = {}
  for (const r of e.rateLimits) {
    if (r.kind === 'five_hour') {
      q.five_hour_used_pct = r.percentUsed
      q.five_hour_resets_at = epoch(r.resetsAt)
    } else if (r.kind === 'seven_day') {
      q.weekly_used_pct = r.percentUsed
      q.weekly_resets_at = epoch(r.resetsAt)
    } else {
      ;((q.unknown_buckets ??= []) as string[]).push(r.kind)
    }
  }
  if (e.cost) q.cost_usd = Math.round(e.cost.usd * 10000) / 10000
  q.context = e.context
  return q
}

export const register: Register = on => {
  on('session.measure', async ($, e, next) => {
    const home = (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE'))
    if (home) {
      const configDir = (await $.env.get('CLAUDE_CONFIG_DIR')) ?? `${home}/.claude`
      const acct = await accountHash(configDir)
      const q = { ...toQuota(e), source: 'session.measure', at: Math.round((await $.clock.now()) / 1000) }
      await $.fs.write(`${home}/.claude/fable-director/mods/usage-${acct}.json`, JSON.stringify(q))
    }
    return next(e)
  })
}
