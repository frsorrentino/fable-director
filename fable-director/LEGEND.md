# fable-director — statusline legend

One line, rendered every turn. Quiet when healthy, full words when something breaks.

```
[FABLE5] [CTX 42%] [CMP 1] [5H 35%→14:00] [7D 36%→Thu] [BDG 0.7×·high] [CACHE 47m] [XF CODEX×2] [DLG SONNET-5 41k]
```

| Segment | Meaning |
|---|---|
| `[FABLE5]` | Model of the current session |
| `[CTX 42%]` | Context window used. Green <60%, yellow ≥60%, red ≥80% |
| `[CMP 1]` | Context compactions this session (auto or `/compact`) — each one dropped context. Hidden until the first |
| `[5H 35%→14:00]` | 5-hour plan quota used, → local reset time |
| `[7D 36%→Thu]` | Weekly plan quota used, → reset day |
| `[BDG 0.7×·high]` | Open task budget: **actual spend ÷ declared estimate** (0.7× = under estimate), `·high` = declared effort tier. Absent = no open budget |
| `[CACHE 47m]` | Prompt-cache countdown from the last API activity: green >10 min, yellow ≤10 min, red <1 min, `exp` = expired (next turn repays the prefix cold). TTL default 3600 s (Max plans); set `FD_CACHE_TTL_S=300` for 5-minute plans |
| `[XF CODEX×2]` | External cross-family calls today (×N); `GEMINI▲` = call in flight |
| `[DLG SONNET-5 41k]` | Output tokens delegated per model this session; `≡` = same model as the main loop; `≈` prefix = declared-only fallback (no transcript) |

## Alarm states (full words, they replace the quiet form)

| You see | It means | What to do |
|---|---|---|
| `⚠ BUDGET 2.3× OF ESTIMATE` (yellow) | Spend passed 2× the declared estimate — checkpoint fired once | Reassess the route; a switch now is cheaper than a post-mortem at 3× |
| `✕ BUDGET 3× — POST-MORTEM DUE` (red) | Spend passed 3×: turn closure is blocked | Write the one-line post-mortem in the playbook, then `budget-close --outcome flagged` |
| `✕ ENFORCEMENT OFF` (red) | The transcript can't be parsed (format changed): token accounting is unreliable, enforcement is suspended | Update the plugin |

On narrow screens the line degrades deterministically: `[CACHE]` drops first, then `[DLG]`, then `[XF]` — never budget, quotas, or error states.

## Commands

| Command | What it does |
|---|---|
| `/fable-director:status` | This statusline as text (for smartphone/remote clients) + burn-rate projection. `--detail` adds delegations and the last task receipt |
| `/fable-director:review` | Data-driven improvement plan from telemetry + playbook |
| `/fable-director:help` | This legend |

Health check for external free-tier models: `python3 <plugin>/scripts/external-exec.py --doctor [--ping]`
