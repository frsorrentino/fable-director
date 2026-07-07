# Benchmark — fable-director token savings

A **reproducible** measurement of token savings, not a percentage waved around.
It compares the same task run by Claude Code **without** and **with** the fable-director policy.

## Method

- **Arm `off`**: `claude -p "<task>"` with no kernel.
- **Arm `on`**: identical task + the fable-director kernel injected via `--append-system-prompt`
  (isolates the effect of the routing policy alone, the mechanism by which the plugin changes decisions).
- Tokens read from the `claude -p` JSON output (`.usage`, `.total_cost_usd`) — no estimates.
- Deterministic fixtures (fixed seed) regenerated before each run.
- **N runs per side** (default 3): report the mean and spread, not a single run.

## Tasks (3 shapes)

1. `01-batch-deterministico` — 30 number files → CSV of aggregates. Scriptable core.
2. `02-classificazione` — 30 strings → EMAIL/URL/PHONE/OTHER labels. Scriptable via regex.
3. `03-misto` — a deterministic part (means) + a judgment part (anomaly summary).

Savings are largest where the work is deterministic (the policy promotes it to a script → ~0
model tokens on the core) and tend to zero where even the base model would write a script anyway.
The three shapes exist precisely to show the **range**, not a cherry-picked number.

## How to run it

```bash
python3 gen_fixtures.py          # deterministic fixtures
RUNS=3 bash run.sh               # ~18 headless sessions (3 tasks × 2 arms × 3 runs)
# optional: MODEL=claude-opus-4-8 RUNS=3 bash run.sh
```

`run.sh` uses `--dangerously-skip-permissions` so it doesn't block on every write: it runs only
inside `benchmarks/` (local fixtures), but read the script before running it.
It consumes real plan / API quota.

Output: `results/<timestamp>/` (raw JSON + `summary.txt`).

## Honesty

- The number in the main README comes **from this harness**, with N, mean, spread and date.
- If the delta is small or noisy, that's what gets written. No extrapolation to "every case".
