# PILOT — fable-director on/off on SWE-bench Verified (n=5)

Date: 2026-08-11 · Machine: Crostini aarch64, 6.5G RAM, 8 cores, docker 20.10 ·
Grader: official `swebench` 4.1.0 (pip), local arm64 image builds ·
Resolver: `claude -p` headless, model `sonnet` (claude-sonnet-5), `--max-turns 80`, 30 min timeout ·
Arm A: throwaway HOME without the plugin · Arm B: throwaway HOME with fable-director 1.37.0
(from the local marketplace; the `~/.claude/plugins` cache ships 1.36.0).
Arm verification: FABLE-DIRECTOR kernel present in 5/5 B transcripts, absent in 5/5 A transcripts.

**These numbers are a pilot (n=5, easy instances): they validate the pipeline and the
costs — they do NOT belong in the README.**

## Results (official grader)

Gold sanity: gold patches → 5/5 resolved on arm64 (grading pipeline validated before
spending model tokens).

| instance | arm | resolved | new tokens | cache read | wall s | turns | $ API-equiv |
|---|---|---|---|---|---|---|---|
| django__django-16082 | A | YES | 20,519 | 467,136 | 86 | 17 | 0.291 |
| django__django-16082 | B | YES | 17,642 | 280,568 | 57 | 10 | 0.206 |
| django__django-16429 | A | YES | 13,992 | 241,285 | 69 | 10 | 0.175 |
| django__django-16429 | B | YES | 19,185 | 365,937 | 64 | 13 | 0.248 |
| psf__requests-1921 | A | YES | 19,214 | 366,942 | 69 | 14 | 0.266 |
| psf__requests-1921 | B | YES | 47,592 | 1,081,181 | 237 | 28 | 0.722 |
| sympy__sympy-22914 | A | YES | 15,771 | 248,199 | 40 | 7 | 0.182 |
| sympy__sympy-22914 | B | YES | 13,960 | 155,525 | 29 | 6 | 0.141 |
| sympy__sympy-23950 | A | YES | 21,104 | 517,124 | 79 | 14 | 0.315 |
| sympy__sympy-23950 | B | YES | 35,083 | 930,416 | 314 | 27 | 0.579 |

Totals: **A 5/5 resolved, 90,600 new tokens, $1.23** · **B 5/5 resolved, 133,462 new
tokens, $1.90**.

- "New tokens" = non-cached input + output + cache write, from the run's transcript
  jsonl, deduplicated by `message.id` (messages split across lines repeat id and usage).
  Cache read counted separately.
- $ = the CLI's `total_cost_usd` (API-equivalent; on a Max subscription this is quota
  consumption, not an invoice).
- No run exceeded the 300k cap; pilot total 224k against a 1M ceiling.
- Quality: 5/5 in both arms — at n=5 on easy instances this does NOT demonstrate
  "quality protected"; it only demonstrates the pipeline measures what it must.
- Observed B overhead: +47% new tokens, +53% cache read, +54% cost. Driven by 2
  outliers (requests-1921, sympy-23950: ~2× turns). At n=5 this is compatible with
  noise, not a verdict: the full run will tell.

## Methodological decisions

- Identical prompt in both arms (issue + rules: non-test files only, no commits).
- Sequential, never parallel runs: uncontended wall-clock.
- The 300k per-run cap is enforced post-run (the jsonl is read at run end); in-flight
  guards: max-turns 80 + 1800s timeout.
- Instance selection: gold patch 1 file <400 chars, problem statement <4k chars,
  pure-python repos (django/sympy/requests). 5 reserves, never needed.

## arm64 issues encountered (all resolved)

1. Prebuilt SWE-bench images are x86_64: local build required (expected).
2. swebench 4.1.0 hardcodes `arch="x86_64"` and the CLI exposes no flag → wrapper
   `tools/run_eval_arm.py` forces `arch="arm64"` (upstream arm64 Dockerfile; the grader
   itself is untouched).
3. Docker 20.10 reuses cached amd64 `ubuntu:22.04` ignoring `--platform` →
   `docker rmi` + explicit arm64 pull.
4. `conda create` OOM-killed (exit 137) on the conda-forge aarch64 repodata; no swap
   possible in Crostini LXC → removed the `conda config --append channels conda-forge`
   line from the base Dockerfile (patched at runtime by the wrapper). The 5 pilot
   instances use defaults+pip only; a conda-forge-only package in another instance
   would fail the env build → replace the instance or assess case by case.
5. Claude Code's transcript slug converts `_`/`.` to `-` as well (fixed in the runner).

## Full-run projection (20 instances × 2 arms, honest)

Base: pilot means A 18.1k / B 26.7k new tokens per run; mean run wall A 69s / B 140s;
env+instance build ~10-15 min per new instance; grading ~3-8 min per run.

- New tokens: **~0.9M expected** (896k from means), but B's variance (14k-48k) can push
  to 1.2-1.5M → the current 1M ceiling risks a mid-run stop: propose 1.5M for the full run.
- API-equivalent cost: **~$12-13** (A ~$5, B ~$7.5); on Max this is quota, to be spread
  so it doesn't eat the weekly window. *(Post-run note: this estimate proved ~2× low —
  cache read dominated. See FULLRUN.md, "Cost honesty".)*
- Total wall: **~5-6h** (builds ~2.5-3h for ~15 new instances, runs ~70-90 min
  sequential, grading ~2h) — only grading parallelizes, carefully (RAM, max_workers 1-2).
- Disk: the pilot consumed ~12G of images (5 instances); 20 instances ≈ +30-35G → the
  27G free do NOT suffice: `--cache_level env` + instance-image cleanup between
  tranches, or pilot image cleanup.
- Residual arm risk: instances with conda-forge deps or C builds (old numpy on
  matplotlib/scikit — not in the list) → stay on django/sympy/requests or accept
  substitutions.
