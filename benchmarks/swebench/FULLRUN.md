# FULL RUN — fable-director on/off on SWE-bench Verified (15 instances × 2 arms)

Date: 2026-08-12 · Same pipeline as the pilot (see [PILOT.md](PILOT.md)): Crostini aarch64,
official `swebench` 4.1.0 grader with local arm64 image builds, resolver `claude -p`
headless (`sonnet` / claude-sonnet-5), throwaway HOME per arm (A without plugin, B with
fable-director 1.37.0), sequential runs, identical prompt in both arms.
Hard-instance guards: `--max-turns 100`, 2700s timeout, 300k cap per run, 1.5M new-token
ceiling on the new runs.
Every "resolved" verdict comes from the official grader; gold sanity 15/15 before any
model tokens were spent. The harness itself lives outside this repo (local machine);
the grader verdict files are published alongside this report.

**Sample: 5 easy (from the pilot, not re-run) + 10 hard completed out of 15 planned — the
1.5M ceiling was reached at the end of tranche 2 (1,491,307 tokens) and tranche 3 never
started (complete-pairs rule: 8.7k residual < ~160k per pair). A stop condition foreseen
in the brief, not an accident.**

| instance | split | arm | resolved | new tokens | cache read | wall s | turns | $ | notes |
|---|---|---|---|---|---|---|---|---|---|
| django__django-16082 | easy | A | YES | 20,519 | 467,136 | 86 | 17 | 0.291 |  |
| django__django-16082 | easy | B | YES | 17,642 | 280,568 | 57 | 10 | 0.206 |  |
| django__django-16429 | easy | A | YES | 13,992 | 241,285 | 69 | 10 | 0.175 |  |
| django__django-16429 | easy | B | YES | 19,185 | 365,937 | 64 | 13 | 0.248 |  |
| psf__requests-1921 | easy | A | YES | 19,214 | 366,942 | 69 | 14 | 0.266 |  |
| psf__requests-1921 | easy | B | YES | 47,592 | 1,081,181 | 237 | 28 | 0.722 |  |
| sympy__sympy-22914 | easy | A | YES | 15,771 | 248,199 | 40 | 7 | 0.182 |  |
| sympy__sympy-22914 | easy | B | YES | 13,960 | 155,525 | 29 | 6 | 0.141 |  |
| sympy__sympy-23950 | easy | A | YES | 21,104 | 517,124 | 79 | 14 | 0.315 |  |
| sympy__sympy-23950 | easy | B | YES | 35,083 | 930,416 | 314 | 27 | 0.579 |  |
| django__django-14631 | hard | A | YES | 52,857 | 1,450,329 | 199 | 33 | 0.826 |  |
| django__django-14631 | hard | B | YES | 75,931 | 2,115,001 | 310 | 41 | 1.246 |  |
| django__django-15629 | hard | A | no | 33,992 | 909,378 | 135 | 26 | 0.526 |  |
| django__django-15629 | hard | B | no | 47,322 | 1,544,033 | 561 | 38 | 0.823 |  |
| django__django-16256 | hard | A | no | 24,233 | 600,186 | 119 | 21 | 0.382 |  |
| django__django-16256 | hard | B | no | 27,066 | 528,735 | 94 | 17 | 0.377 |  |
| django__django-16263 | hard | A | no | 203,808 | 7,940,781 | 1158 | 101 | 4.300 | max-turns |
| django__django-16263 | hard | B | YES | 110,252 | 3,400,934 | 894 | 65 | 1.852 |  |
| django__django-16560 | hard | A | YES | 61,746 | 1,871,413 | 251 | 40 | 1.078 |  |
| django__django-16560 | hard | B | YES | 74,967 | 2,823,619 | 324 | 53 | 1.479 |  |
| sympy__sympy-13877 | hard | A | YES | 48,498 | 1,150,292 | 350 | 28 | 0.735 |  |
| sympy__sympy-13877 | hard | B | YES | 42,532 | 957,771 | 202 | 25 | 0.637 |  |
| sympy__sympy-14248 | hard | A | no | 180,297 | 7,697,664 | 1037 | 101 | 3.976 | max-turns |
| sympy__sympy-14248 | hard | B | no | 138,222 | 4,821,203 | 660 | 74 | 2.728 |  |
| sympy__sympy-14531 | hard | A | YES | 34,198 | 885,264 | 262 | 28 | 0.555 |  |
| sympy__sympy-14531 | hard | B | YES | 98,638 | 3,344,207 | 634 | 65 | 1.876 |  |
| sympy__sympy-16597 | hard | A | YES | 61,455 | 1,793,363 | 631 | 41 | 1.020 |  |
| sympy__sympy-16597 | hard | B | YES | 58,863 | 2,014,249 | 447 | 43 | 1.082 |  |
| sympy__sympy-22080 | hard | A | no | 40,264 | 816,706 | 216 | 24 | 0.592 |  |
| sympy__sympy-22080 | hard | B | YES | 76,166 | 2,349,345 | 410 | 50 | 1.379 |  |

## Totals

| split | arm | resolved | new tokens | cache read | $ API-equiv | wall tot s |
|---|---|---|---|---|---|---|
| easy | A | 5/5 | 90,600 | 1,840,686 | 1.23 | 344 |
| easy | B | 5/5 | 133,462 | 2,813,627 | 1.90 | 701 |
| hard | A | 5/10 | 741,348 | 25,115,376 | 13.99 | 4,359 |
| hard | B | 7/10 | 749,959 | 23,899,097 | 13.48 | 4,535 |
| **TOT** | **A** | **10/15** | **831,948** | 26,956,062 | **15.22** | 4,703 |
| **TOT** | **B** | **12/15** | **883,421** | 26,712,724 | **15.38** | 5,236 |

## Reading (on/off delta)

- **Quality: no regression with the plugin — B resolves everything A resolves, plus 2
  more instances** (django-16263, sympy-22080; both discordant cases are pro-B).
  With n=10 hard and only 2 discordant pairs the difference is NOT statistically
  significant (McNemar, p=0.5): the defensible claim is **"quality protected"**
  (no instance lost with the plugin), not "quality improved".
- **Tokens: the plugin's overhead is visible only on the easy split** (+47%, 91k to
  133k). On the hard set tokens are at parity (741k vs 750k, +1.2%) and so is cost
  ($13.99 vs $13.48, slightly pro-B): the 2 baseline runs truncated at max-turns
  (16263-A, 14248-A) burned 384k tokens without resolving — more than the plugin
  ever cost.
- **Truncations**: 2, both in arm A (marked `max-turns`). A truncated run is graded
  unresolved by the grader, but the cause is the turn ceiling, not a rejection on the
  merits: without the guard they would have kept spending. B never touched the guards.
- Cost outliers: 16263-A ($4.30) and 14248-A ($3.98) alone account for 54% of A's
  hard-set cost.

## Cost honesty

The new-token ceiling held (1.49M of 1.5M), but the real spend on the new runs was
**~$27.5 API-equivalent — about 2× the pre-run estimate**. The driver is cache read
(~50M tokens across the two arms), which the ceiling did not count. This is the
project's own thesis biting its own benchmark: the dominant cost is context re-sent
every turn, not the tokens that are easy to count. Lesson applied to future runs:
cap in dollars, not in new tokens.

## Sample limits

- **arm64 bias declared ex ante**: building on aarch64 (conda defaults only; conda-forge
  excluded for the OOM documented in PILOT.md) imposes `python>=3.8` specs: all
  django <=3.2 instances (which require py3.6, absent from defaults linux-aarch64) are
  structurally excluded. 4 instances + 2 reserves were replaced after tranche 1's gold
  sanity (zero model tokens spent on the failures). The hard set therefore skews to
  recent versions (django 4.0-5.0, sympy 1.1-1.10).
- 5 hard instances planned and never run (tranche 3: sympy-19783, pytest-5787,
  pytest-8399, pytest-6197, requests-2317) — token ceiling reached.
- The 5 easy instances come from the pilot (same pipeline, same model, same caps; not
  re-run).
- One model (sonnet), one run per pair: no between-run variance estimate.
- The arms share via symlink ONLY `.credentials.json` with the real HOME (fix for OAuth
  token rotation); settings, plugins and state stay isolated per arm — verified: the
  plugin kernel is present in every B transcript, absent in every A transcript.

## Reproduction

As in [PILOT.md](PILOT.md); additionally: `tools/run_pilot.py --set "tranche:N"`, report
via `tools/make_fullrun_report.py`. Verdicts in the `arm{A,B}.*.json` files in this
folder (official grader reports); full logs in the harness' `logs/run_evaluation/`.
