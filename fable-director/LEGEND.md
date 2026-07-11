# fable-director — statusline legend

One line, rendered every turn. Quiet when healthy, full words when something breaks.

```
[FABLE5] [CTX 42%] [5H 35%→14:00] [7D 36%→Thu] [BDG 0.7×·high] [XF CODEX×2] [DLG SONNET-5 41k]
```

| Segment | Meaning |
|---|---|
| `[FABLE5]` | Model of the current session |
| `[CTX 42%]` | Context window used. Green <60%, yellow ≥60%, red ≥80% |
| `[5H 35%→14:00]` | 5-hour plan quota used, → local reset time |
| `[7D 36%→Thu]` | Weekly plan quota used, → reset day |
| `[BDG 0.7×·high]` | Open task budget: **actual spend ÷ declared estimate** (0.7× = under estimate), `·high` = declared effort tier. Absent = no open budget |
| `[XF CODEX×2]` | External cross-family calls today (×N); `GEMINI▲` = call in flight |
| `[DLG SONNET-5 41k]` | Output tokens delegated per model this session; `≡` = same model as the main loop; `≈` prefix = declared-only fallback (no transcript) |

## Alarm states (full words, they replace the quiet form)

| You see | It means | What to do |
|---|---|---|
| `⚠ BUDGET 2.3× OF ESTIMATE` (yellow) | Spend passed 2× the declared estimate — checkpoint fired once | Reassess the route; a switch now is cheaper than a post-mortem at 3× |
| `✕ BUDGET 3× — POST-MORTEM DUE` (red) | Spend passed 3×: turn closure is blocked | Write the one-line post-mortem in the playbook, then `budget-close --outcome flagged` |
| `✕ ENFORCEMENT OFF` (red) | The transcript can't be parsed (format changed): token accounting is unreliable, enforcement is suspended | Update the plugin |

On narrow screens the line degrades deterministically: `[DLG]` drops first, then `[XF]` — never budget, quotas, or error states.

## Commands

| Command | What it does |
|---|---|
| `/fable-director:status` | This statusline as text (for smartphone/remote clients) + burn-rate projection. `--detail` adds delegations and the last task receipt |
| `/fable-director:review` | Data-driven improvement plan from telemetry + playbook |
| `/fable-director:help` | This legend |

Health check for external free-tier models: `python3 <plugin>/scripts/external-exec.py --doctor [--ping]`
