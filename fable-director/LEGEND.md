# fable-director — statusline legend

**Every segment says what to do, not what it measures.** Words, no abbreviations; only exceptions. When everything is fine the line is short and quiet:

```
Fable 5.1 · quota ok until 17:30                                   caveman
```

With agents running (a state, not an alarm — it tells you not to close the session):

```
Fable 5.1 · quota ok until 17:30 · 2 agents working
```

When something needs you, the exception replaces the quiet text or goes to a second row; more than one exception = row 2, most urgent first. Colour never carries information alone: the words are always there.

| You see | It means | What to do |
|---|---|---|
| `budget over 3× — post-mortem before closing` (red block) | the task spent three times its declared estimate; closing is blocked | write the one-line post-mortem in the playbook, then `budget-close --outcome flagged` |
| `verification failed: python3 tests/run.py (exit 1)` | the command you declared as "done" does not pass | do not report the task as finished; fix, the check re-runs after your next write |
| `quota almost gone, resets in 40 min` | five-hour plan window at 80% or more | inline work and closures only until the reset; batches wait |
| `budget checks off (transcript unreadable) — update the plugin` | the transcript format changed; spend accounting is unreliable | update fable-director |
| `context almost full (85%) — finish the task and start a new session` | the conversation is near the window ceiling | close at a verified boundary, distill, start fresh |
| `1 agent stuck for 32 min — check /tasks` | a delegated agent has been running for over half an hour | open `/tasks`, resume or stop it |
| `budget over 2.3× — reconsider the route` | spend passed twice the estimate (checkpoint) | switch route now if the plan was wrong; a reversal here is cheaper than a post-mortem at 3× |
| `another session has priority (incident: …) — new fan-outs wait` | a session on this account opened a budget with `--priority incident` | single turns are free; fan-outs wait until it closes (2 h at most) |
| `weekly quota 72% used, resets Thu 4` / `weekly quota almost gone` | weekly plan window at 60% / 80% | plan the week; premium work first |
| `3 commands failed in a row — change approach` | the same failure keeps repeating | change something structural: tool, diagnosis or model, not a fourth identical retry |
| `effort max on` | a high reasoning effort is active and costs more | `/effort high` if the task does not need it |
| `context 62% full` | more than half the window used | fine; batch tool calls, avoid re-reading |
| `gemini free calls almost used up (1300/1500), resets 09:00` | the external free tier is nearly exhausted for today | keep the rest for what matters |
| `PR #42 approved` / `PR #42: changes requested` / `PR #42 pending` | the open pull request of this branch | as it says |
| `caveman` | a third-party mode you switched on yourself | nothing |

The numbers behind these lines live in `/fable-director:status` (one row per item, `name: number — what it means`).

## Expert mode (optional)

`bash <plugin>/scripts/statusline-install.sh --expert` (or `FD_STATUSLINE_MODE=expert`) restores the historic dense line; `--plain` returns to words. Its legend:

```
✦ FABLE 5·max · ctx ▓▓▓░░░░░ 26%/1M · cmp 1 · 5H 71% 17:30 · 7D 46% 14 Jul · fail ×3 │ caveman
└ bdg ▓░░ 0.7×·high · dlg ⟲2 ≡ 41k · xf gemini 2/1500 09:00 · cache ◕ 47m
```

| Segment | Meaning |
|---|---|
| `caveman` | Badge of the caveman plugin, adopted by the zen theme: ochre signature (172) kept, brackets dropped. It sits at the **tail** of row 1 and is the first thing dropped when width runs short — it's a state you set yourself, not a number you'd lose. Any *other* third-party badge passes through untouched |
| `✦ FABLE 5·max` | Model of the current session + **live** reasoning effort. Effort is grey up to `high`, yellow from `xhigh` — a forgotten `/effort max` burns quota silently. Absent when the model exposes no effort |
| `ctx ▓▓▓░░░░░ 26%/1M` | Context window used, 8-cell gauge (ceil: any usage lights the first cell). Grey <60%, yellow ≥60%, red ≥80%. `/1M` = extended window (26% of 1M ≠ 26% of 200k) |
| `cmp 1` | Context compactions this session (auto or `/compact`) — each one dropped context. Hidden until the first, always yellow |
| `5H 71% 17:30` | 5-hour plan quota used, then the local time it resets — **no arrow, no symbol**: the time is set apart by a space and rendered in the deepest half-light (239), so it reads as a different kind of number. Nothing to learn, and the shape itself (`17:30` has a colon, `14 Jul` a month) still identifies it on a monochrome terminal. Quota colour: grey <60%, yellow ≥60%, red ≥80% |
| `7D 46% 14 Jul` | Weekly plan quota used, then the reset day (month follows your locale). Same thresholds |
| `bdg ▓░░ 0.7×·high` | Open task budget: **actual spend ÷ declared estimate** as a micro-gauge on the 0–3× checkpoint scale (one cell per whole × reached), `·high` = declared effort tier. `bdg ok` when no live ratio is available. Absent = no open budget |
| `fail ×3` | **Consecutive failing Bash commands** since the last success (your own denials don't count). Hidden below 2; yellow at 2, red at ≥3 — where the fail-streak hook injects the rule-of-3. Cleared by the next successful Bash |
| `cache ◕ 47m` | Prompt-cache countdown from the last API activity, with a quarter-clock (`●◕◑◔○`) of the TTL left: grey >10 min, yellow ≤10 min, red <1 min, `○ exp` = expired (next turn repays the prefix cold). **Shown on its own only when it's actionable** — TTL ≤10 min, the window where delegation timing actually changes. A full or an expired-cold cache changes no decision, so it never summons row 2; it rides along, free, whenever row 2 already exists. TTL default 3600 s (Max plans); set `FD_CACHE_TTL_S=300` for 5-minute plans |
| `xf gemini 2/1500 09:00` | External free-tier calls in the **provider's own reset window**: used/limit, then the local time the tier resets. Needs `limits.reset {period, tz}` declared in `cross-family.json` (Gemini: midnight Pacific); a provider without it shows plain `×N` on the UTC day and **no invented reset time**. Yellow at ≥80% of the tier, red at ≥95%; `gemini▲` = call in flight (orange) |
| `dlg ⟲2 ≡ 41k` | `⟲N` = delegations **in flight right now**, counted by the harness at `SubagentStart` (nested spawns included) — it appears before any token does, and disappears as they stop. Then output tokens delegated per model this session; `≡` = same model as the main loop; `≈` prefix = declared-only fallback (no transcript) |
| `✦≤26%` | **Ceiling** on the premium-model weekly window, shown only while that model drives the session: your plan reserves a fraction of the 7D capacity for it (declared in `plan-<acct>.json` as `premium_weekly_fraction`, e.g. `0.5`), and premium spend can never exceed total spend — so window% ≤ 7D% ÷ fraction. Always a bound (`≤`), never measured spend; when it saturates (7D ≥ fraction) it becomes `✦?` — check the usage page, never a made-up number. Absent without the plan file |
| `pr #42` | Open pull request for the current branch (data Claude Code already provides): green = approved, yellow = changes requested, half-light = pending. Row 2, never trimmed |

## Alarm states (full words, they replace the quiet form)

| You see | It means | What to do |
|---|---|---|
| `⚠ BUDGET 2.3× OF ESTIMATE` (yellow, row 2) | Spend passed 2× the declared estimate — checkpoint fired once | Reassess the route; a switch now is cheaper than a post-mortem at 3× |
| `✕ BUDGET 3× — POST-MORTEM DUE` (red **takeover**: solid-red block at the head of row 1, everything else falls to half-light) | Spend passed 3×: turn closure is blocked | Write the one-line post-mortem in the playbook, then `budget-close --outcome flagged` |
| `✕ ENFORCEMENT OFF` (red takeover) | The transcript can't be parsed (format changed): token accounting is unreliable, enforcement is suspended | Update the plugin |

On narrow screens both rows degrade deterministically, and the rule is the same: **decoration goes, data stays.** Row 1 sheds the `caveman` badge first, then the `ctx` gauge (the percentage right next to it already says it, and the colour already carries the alarm), then the reset times — the model, every percentage, `fail ×N` and the alarms survive at any width. Row 2 sheds `cache` first, then `dlg`, then `xf` — never the budget. Width comes from the real terminal (`COLUMNS`, Claude Code ≥ 2.1.153; fallback 120) and is measured in characters — the zen glyphs are multibyte-safe.

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
