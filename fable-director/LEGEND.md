# fable-director — statusline legend

Rendered every turn. **Half-light when healthy, full words when something breaks**: healthy segments sit in quiet grey (245), colour is reserved for what deviates — thresholds, live effort, alarms.

Two rows with distinct jobs. **Row 1 is what you are** (model, effort, context, plan quotas, fail streak) — always present, never trimmed. **Row 2 is what is happening** (open budget, delegations, external calls, cache countdown) — it exists only while there's activity; at rest the statusline is a single quiet line. An expired cache alone doesn't summon row 2.

```
caveman │ ✦ FABLE5·max · ctx ▓▓▓░░░░░ 26%/1M · cmp 1 · 5H 71%→17:30 · 7D 46%→14 Jul · fail ×3
└ bdg ▓░░ 0.7×·high · dlg ⟲2 ≡ 41k · xf gemini 2/1500→09:00 · cache ◕ 47m
```

| Segment | Meaning |
|---|---|
| `caveman` | Badge of the caveman plugin, adopted by the zen theme: ochre signature (172) kept, brackets dropped. Any *other* third-party badge passes through untouched |
| `✦ FABLE5·max` | Model of the current session + **live** reasoning effort. Effort is grey up to `high`, yellow from `xhigh` — a forgotten `/effort max` burns quota silently. Absent when the model exposes no effort |
| `ctx ▓▓▓░░░░░ 26%/1M` | Context window used, 8-cell gauge (ceil: any usage lights the first cell). Grey <60%, yellow ≥60%, red ≥80%. `/1M` = extended window (26% of 1M ≠ 26% of 200k) |
| `cmp 1` | Context compactions this session (auto or `/compact`) — each one dropped context. Hidden until the first, always yellow |
| `5H 71%→17:30` | 5-hour plan quota used, → local reset time. Grey <60%, yellow ≥60%, red ≥80% |
| `7D 46%→14 Jul` | Weekly plan quota used, → reset day. Same thresholds |
| `bdg ▓░░ 0.7×·high` | Open task budget: **actual spend ÷ declared estimate** as a micro-gauge on the 0–3× checkpoint scale (one cell per whole × reached), `·high` = declared effort tier. `bdg ok` when no live ratio is available. Absent = no open budget |
| `fail ×3` | **Consecutive failing Bash commands** since the last success (your own denials don't count). Hidden below 2; yellow at 2, red at ≥3 — where the fail-streak hook injects the rule-of-3. Cleared by the next successful Bash |
| `cache ◕ 47m` | Prompt-cache countdown from the last API activity, with a quarter-clock (`●◕◑◔○`) of the TTL left: grey >10 min, yellow ≤10 min, red <1 min, `○ exp` = expired (next turn repays the prefix cold; alone it never summons row 2). TTL default 3600 s (Max plans); set `FD_CACHE_TTL_S=300` for 5-minute plans |
| `xf gemini 2/1500→09:00` | External free-tier calls in the **provider's own reset window**: used/limit, → local time the tier resets. Needs `limits.reset {period, tz}` declared in `cross-family.json` (Gemini: midnight Pacific); a provider without it shows plain `×N` on the UTC day and **no invented reset time**. Yellow at ≥80% of the tier, red at ≥95%; `gemini▲` = call in flight (orange) |
| `dlg ⟲2 ≡ 41k` | `⟲N` = delegations **in flight right now**, counted by the harness at `SubagentStart` (nested spawns included) — it appears before any token does, and disappears as they stop. Then output tokens delegated per model this session; `≡` = same model as the main loop; `≈` prefix = declared-only fallback (no transcript) |
| `✦≤26%` | **Ceiling** on the premium-model weekly window, shown only while that model drives the session: your plan reserves a fraction of the 7D capacity for it (declared in `plan-<acct>.json` as `premium_weekly_fraction`, e.g. `0.5`), and premium spend can never exceed total spend — so window% ≤ 7D% ÷ fraction. Always a bound (`≤`), never measured spend; when it saturates (7D ≥ fraction) it becomes `✦?` — check the usage page, never a made-up number. Absent without the plan file |
| `pr #42` | Open pull request for the current branch (data Claude Code already provides): green = approved, yellow = changes requested, half-light = pending. Row 2, never trimmed |

## Alarm states (full words, they replace the quiet form)

| You see | It means | What to do |
|---|---|---|
| `⚠ BUDGET 2.3× OF ESTIMATE` (yellow, row 2) | Spend passed 2× the declared estimate — checkpoint fired once | Reassess the route; a switch now is cheaper than a post-mortem at 3× |
| `✕ BUDGET 3× — POST-MORTEM DUE` (red **takeover**: solid-red block at the head of row 1, everything else falls to half-light) | Spend passed 3×: turn closure is blocked | Write the one-line post-mortem in the playbook, then `budget-close --outcome flagged` |
| `✕ ENFORCEMENT OFF` (red takeover) | The transcript can't be parsed (format changed): token accounting is unreliable, enforcement is suspended | Update the plugin |

On narrow screens row 2 degrades deterministically: `cache` drops first, then `dlg`, then `xf` — never the budget. Row 1 (identity, quotas, alarms) never degrades. Width comes from the real terminal (`COLUMNS`, Claude Code ≥ 2.1.153; fallback 120) and is measured in characters — the zen glyphs are multibyte-safe.

Renders are event-driven **plus** a 5-second timer (`refreshInterval`, written by the installer): event triggers go quiet precisely while the coordinator waits on background subagents, which is when `bdg` and `dlg ⟲N` move. `FD_STATUSLINE_REFRESH=<seconds>` before running the installer changes it, `0` disables the timer.

## Commands

| Command | What it does |
|---|---|
| `/fable-director:status` | The same state as a box-drawn bulletin (for smartphone/remote clients): quota bars, burn-rate sparkline from the quota history, honest freshness labels. `--detail` adds delegations and the last task receipt |
| `/fable-director:review` | Data-driven improvement plan from telemetry + playbook |
| `/fable-director:help` | This legend |

## Clickable segments (opt-in)

With `"statusline_links": true` in `~/.claude/fable-director/plan-<acct>.json`, four segments become OSC 8 hyperlinks (Ctrl+click): model → status.anthropic.com, `5H`/`7D`/`✦≤` → your plan's usage page (where the real per-model window lives), `xf` → AI Studio usage, `pr #42` → the pull request. **Off by default on purpose**: on terminals that open links in-place (some webview-based ones) a click can replace the terminal page and kill your session. Test yours first, in a throwaway terminal window: `printf '\e]8;;https://example.com\e\\test\e]8;;\e\\\n'` — enable only if Ctrl+click opens a **new** browser tab. URL length never counts toward the width degradation.

Health check for external free-tier models: `python3 <plugin>/scripts/external-exec.py --doctor [--ping]`
