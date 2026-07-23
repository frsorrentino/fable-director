# fable-director — statusline legend

One line, rendered every turn. **Half-light when healthy, full words when something breaks**: healthy segments sit in quiet grey (245), colour is reserved for what deviates — thresholds, live effort, alarms.

```
caveman │ ✦ FABLE5·max · ctx ▓▓▓░░░░░ 26%/1M · cmp 1 · 5H 71%→17:30 · 7D 46%→14 Jul · bdg ▓░░ 0.7×·high · fail ×3 · cache 47m · xf gemini×2 · dlg ≡ 41k
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
| `cache 47m` | Prompt-cache countdown from the last API activity: grey >10 min, yellow ≤10 min, red <1 min, `exp` = expired (next turn repays the prefix cold). TTL default 3600 s (Max plans); set `FD_CACHE_TTL_S=300` for 5-minute plans |
| `xf gemini×2` | External cross-family calls today (×N); `gemini▲` = call in flight (segment lights up orange while active, grey otherwise) |
| `dlg ≡ 41k` | Output tokens delegated per model this session; `≡` = same model as the main loop; `≈` prefix = declared-only fallback (no transcript) |

## Alarm states (full words, they replace the quiet form)

| You see | It means | What to do |
|---|---|---|
| `⚠ BUDGET 2.3× OF ESTIMATE` (yellow) | Spend passed 2× the declared estimate — checkpoint fired once | Reassess the route; a switch now is cheaper than a post-mortem at 3× |
| `✕ BUDGET 3× — POST-MORTEM DUE` (red) | Spend passed 3×: turn closure is blocked | Write the one-line post-mortem in the playbook, then `budget-close --outcome flagged` |
| `✕ ENFORCEMENT OFF` (red) | The transcript can't be parsed (format changed): token accounting is unreliable, enforcement is suspended | Update the plugin |

On narrow screens the line degrades deterministically: `cache` drops first, then `dlg`, then `xf` — never budget, quotas, or error states. Width is measured in characters (the zen glyphs are multibyte-safe).

## Commands

| Command | What it does |
|---|---|
| `/fable-director:status` | This statusline as text (for smartphone/remote clients) + burn-rate projection. `--detail` adds delegations and the last task receipt |
| `/fable-director:review` | Data-driven improvement plan from telemetry + playbook |
| `/fable-director:help` | This legend |

Health check for external free-tier models: `python3 <plugin>/scripts/external-exec.py --doctor [--ping]`
