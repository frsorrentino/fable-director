import type { SessionRateLimit } from 'claude-code'

// session.measure → quota-<acct>.json, the file the gates and /fable-director:status
// read. Same keys, same account hash and same merge rule as statusline-ctx.sh
// (1.52.3), so the two writers agree: the statusline where it draws, this
// module everywhere else too (claude -p, phone, Remote Control).

export type Quota = Record<string, unknown>

const hex = (buf: ArrayBuffer) =>
  [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('')

// sha256(CLAUDE_CONFIG_DIR or ~/.claude)[:8], as statusline-ctx.sh.
export const accountHash = async (configDir: string) =>
  hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(configDir))).slice(0, 8)

const epoch = (iso?: string) => {
  const ms = iso ? Date.parse(iso) : NaN
  return Number.isFinite(ms) ? Math.round(ms / 1000) : undefined
}

const num = (x: unknown) => {
  const n = typeof x === 'number' ? x : typeof x === 'string' ? Number(x) : NaN
  return Number.isFinite(n) ? n : undefined
}

// The readings, keyed as the statusline keys them.
export const readings = (limits: readonly SessionRateLimit[]): Quota => {
  const q: Quota = {}
  const unknown: string[] = []
  for (const r of limits) {
    if (r.kind === 'five_hour') {
      q.five_hour_used_pct = r.percentUsed
      const t = epoch(r.resetsAt)
      if (t !== undefined) q.five_hour_resets_at = t
    } else if (r.kind === 'seven_day') {
      q.weekly_used_pct = r.percentUsed
      const t = epoch(r.resetsAt)
      if (t !== undefined) q.weekly_resets_at = t
    } else {
      unknown.push(r.kind)
    }
  }
  if (unknown.length) q.unknown_buckets = unknown.sort()
  return q
}

// Merge with the file, rule of statusline-ctx.sh: inside one window the quota
// never goes down (same reset: the higher value); a later reset is a new
// window (the new value); an earlier one is an old reading (ignored); a
// bucket missing from the reading keeps the one on file until it expires.
export const merge = (q: Quota, old: Quota, nowS: number): Quota => {
  const mq: Quota = { ...q }
  for (const [pk, rk] of [['weekly_used_pct', 'weekly_resets_at'], ['five_hour_used_pct', 'five_hour_resets_at']] as const) {
    const ov = old[pk], orr = num(old[rk])
    const nv = q[pk], nr = num(q[rk])
    if (ov === undefined || ov === null) continue
    if (nv === undefined || nv === null) {
      if (orr === undefined || orr > nowS) {
        mq[pk] = ov
        if (old[rk] !== undefined && old[rk] !== null) mq[rk] = old[rk]
      }
      continue
    }
    if (orr === undefined || nr === undefined) continue
    if (nr < orr - 600) {
      mq[pk] = ov
      mq[rk] = old[rk]
    } else if (Math.abs(nr - orr) <= 600) {
      const a = num(ov), b = num(nv)
      if (a !== undefined && b !== undefined) mq[pk] = Math.max(a, b)
    }
  }
  return mq
}

const canon = (o: Quota) => JSON.stringify(Object.keys(o).sort().map(k => [k, num(o[k]) ?? o[k]]))
export const same = (a: Quota, b: Quota) => canon(a) === canon(b)

const iso = (s: unknown) => {
  const n = num(s)
  return n === undefined ? undefined : new Date(n * 1000).toISOString()
}

// usage-snapshot-<acct>.json, claude-hud's external schema.
export const snapshot = (mq: Quota, nowMs: number) => {
  const snap: Record<string, unknown> = { updated_at: new Date(nowMs).toISOString() }
  for (const [key, pk, rk] of [['five_hour', 'five_hour_used_pct', 'five_hour_resets_at'], ['seven_day', 'weekly_used_pct', 'weekly_resets_at']] as const) {
    const v = num(mq[pk])
    if (v === undefined) continue
    const e: Record<string, unknown> = { used_percentage: Math.round(v) }
    const i = iso(mq[rk])
    if (i) e.resets_at = i
    snap[key] = e
  }
  return snap
}
