# Benchmark — fable-director token savings

A **reproducible** measurement of token savings, not a percentage waved around.
It compares the same task run by Claude Code **without** and **with** the fable-director policy.

## Method

- **Arm `off`**: `claude -p "<task>"` with no kernel and no hooks.
- **Arm `on`**: identical task + the **full enforcement stack** injected via `--settings`:
  SessionStart (kernel), PreToolUse (pre-delegation gate), Stop (2×/3× budget check) — the
  same hooks the plugin ships, with absolute paths. This measures what you actually install,
  not just the policy's influence on decisions. Budget files are wiped between runs so one
  run's budget can't authorize or pollute the next.
- Tokens read from the `claude -p` JSON output (`.usage`, `.total_cost_usd`) — no estimates.
- Deterministic fixtures (fixed seed) regenerated before each run.
- **N runs per side** (default 3): report the mean and spread, not a single run.

## Tasks (4 shapes)

1. `01-batch-deterministico` — 30 number files → CSV of aggregates. Scriptable core.
2. `02-classificazione` — 30 strings → EMAIL/URL/PHONE/OTHER labels. Scriptable via regex.
3. `03-misto` — a deterministic part (means) + a judgment part (anomaly summary).
4. `04-triage-recensioni` — 40 reviews needing per-item semantic judgment (sentiment/theme +
   safety flags with ground truth): the shape where delegation and the gate actually fire.

Savings are largest where the work is deterministic (the policy promotes it to a script → ~0
model tokens on the core) and tend to zero where even the base model would write a script anyway.
The shapes exist precisely to show the **range**, not a cherry-picked number.

## How to run it

```bash
python3 gen_fixtures.py          # deterministic fixtures
RUNS=3 bash run.sh               # ~24 headless sessions (4 tasks × 2 arms × 3 runs)
# optional: MODEL=claude-fable-5 RUNS=3 bash run.sh
```

`run.sh` uses `--dangerously-skip-permissions` so it doesn't block on every write: it runs only
inside `benchmarks/` (local fixtures), but read the script before running it.
It consumes real plan / API quota.

Output: `results/<timestamp>/` (raw JSON + `summary.txt`).

## Honesty

- The number in the main README comes **from this harness**, with N, mean, spread and date.
- If the delta is small or noisy, that's what gets written. No extrapolation to "every case".
- 2026-07-08: fixed a systematically ambiguous shape-04 fixture — a quality-negative phrase
  ("pannello che si stacca") that both arms read as a safety hazard while ground truth said NO
  (the likely cause of the identical 85% safety precision in both arms). **Safety-precision
  numbers from runs before this date are not comparable with later runs.**

### What this harness measures — and what it doesn't

Both arms run the **same model** (`MODEL=...`). The A/B therefore measures the **policy effect
at equal model**: script-first routing, grouped batches, output contracts, and the enforcement
stack actually firing. It does **not** measure the second — and larger — component of
fable-director's value: the **model differential** of the director topology, where an expensive
top model (Fable/Opus) does only planning/judgment and the heavy tokens land on cheaper
executors. A cheap model delegating to itself has ~zero differential by construction.

Measuring that requires the top model as the orchestrator arm
(`MODEL=claude-fable-5 RUNS=1-2`, ideally on task shape 04): few runs suffice — the expected
delta is large. Publish the two numbers separately, with these exact labels: *policy effect
(equal model)* vs *director topology (top model orchestrating)*. Reporting one as the other
is the kind of number this project refuses to ship.

**External anchor.** Anthropic's own cookbook measures exactly this topology — a `claude-fable-5`
coordinator that plans and synthesizes while `claude-sonnet-5` workers do all the token-heavy
reading ([`CMA_plan_big_execute_small`](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_plan_big_execute_small.ipynb)):
**~2.5× cheaper, ~3× faster**, 84–98% of input tokens billed at the worker rate. Two caveats
that transfer to our number: (1) the differential only appears when the task is **read/token-heavy
on the workers** (shape 04's per-item review qualifies; a light task shows ~nothing — consistent
with our N=3 equal-model finding); (2) their run is on Managed Agents infrastructure, **outside**
fable-director's local hook stack — same topology, different enforcement surface. So ~2.5× is an
expectation anchor, not a target: we still publish our own measured number on our own stack.
