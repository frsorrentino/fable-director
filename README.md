# 🎬 fable-director

**Token governance for Claude Code.** The top model *directs* — plans, judges, verifies — and sends execution to the cheapest adequate means: a deterministic script first, then a mid-tier model, the top model only where it truly matters.

![version](https://img.shields.io/badge/version-1.12.3-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A5CF6)

> Like a Renaissance workshop: the master sketches and refines, the apprentices execute, the workshop accrues craft. This plugin brings that discipline into Claude Code — in a way that is **measurable** and **enforced by hooks**, not left to good intentions.

**Positioning, in three clauses:**
1. **Quality is a constraint, token minimization is the objective** — quality never enters the trade-off; tokens get cut only in the space the constraint leaves open.
2. **Optimization is deterministic** — enforced by blocking scripts and hooks, not by prompt suggestions the model can ignore.
3. **Transparency is guaranteed** — objective telemetry and benchmarks that publish their own limits, negative numbers included.

---

## The problem

A powerful agent tends to do *everything* itself: it reads huge files, repeats deterministic work a script would do for free, delegates in bursts without knowing whether it pays off, burns context. Cost explodes and you don't notice until the bill (or the rate limit) arrives.

## The solution

fable-director injects an **always-on routing policy** and makes it **enforced by deterministic hooks**. It's not a hint in the prompt the model can ignore: it's enforcement.

---

## 🆕 What's new

- **1.12.3** — Cross-family verifier goes two-lane: dead DeepSeek lane removed (last free variant left OpenRouter), defaults upgraded and verified live in 1.12.2 — `gemini-3-flash-preview` + `gpt-5.6-terra`, with an explicit `gemini-stable` fallback, never a silent one.
- **1.12.1** — Kernel fast path: a single-turn task pays zero ritual — the policy must never cost more than the task.
- **1.12.0** — External executor (experimental): non-code batches on free external tiers, zero Claude tokens.
- **1.11.x** — Effort as a routing lever (shipped `fd-executor`/`fd-verifier` agents with pinned tiers) + live budget ratio in the statusline (`[BDG 0.7×·high]`).
- **1.10.10** — Self-review with the plugin's own ladder: 6 real bugs found and fixed.

Full history: [CHANGELOG.md](CHANGELOG.md).

---

## ⭐ Advantages at a glance

| | Advantage | How |
|---|---|---|
| 🧭 | **Every task goes to the right means** | A 6-axis routing kernel injected each session (~500 tokens): inline vs delegate vs script vs workflow, with a clear precedence order |
| 💰 | **Deterministic work at zero cost** | The policy pushes repeatable work into scripts — zero model tokens instead of N calls |
| 🛡️ | **Budget enforced, not suggested** | Machine-readable pre-budget + a `PreToolUse` gate that denies delegation with no open budget + a `Stop` hook that deterministically blocks at 3× and forces a post-mortem. Anti-Goodhart by construction |
| 📊 | **Real telemetry, zero overhead** | A `SessionEnd` hook logs tokens, cache hit ratio, delegation overhead to SQLite — without spending model tokens |
| 🧠 | **The workshop learns** | A heuristics playbook that survives updates: `[candidate]` → confirmed on the 2nd occurrence, uses/ok/ko counters |
| 📟 | **You always know where you stand** | A statusline with model, context %, 5h and 7d plan quotas with reset times, budget state |
| 🧾 | **Honest token accounting** | A report from real JSONL transcripts: cost per model/main/subagents, cache metrics, ≥3× flags |

---

## 💸 How much do you save?

No magic number: savings depend on the kind of work. We ran the **same tasks with and
without the plugin**, several times each, and counted real tokens and real dollars.
Here is the honest answer, plain and measured:

| Kind of work | Without the plugin | With the plugin | Verdict |
|---|---|---|---|
| **Big reading jobs** (e.g. analyze 240 long customer reviews) | The AI reads everything and hands work around chaotically — anywhere from 3 to 32 steps, cost swings run to run | Same result in 3-7 steps, **~25% fewer tokens**, same dollars, quality equal or slightly better | ✅ **saves** |
| **Repetitive mechanical work** (e.g. process 30 data files) | Works, but each run behaves differently | **~20% fewer tokens** and near-identical behavior every run | ✅ **saves** |
| **Quick small tasks** (one question, one small fix) | Baseline | **~5% more** — the fixed price of the safety checks, like an insurance premium | ➖ small premium |
| **Quality of results** | 94-100% accurate on our test sets | Equal or better everywhere the plugin saves (e.g. 98% vs 95%); never traded for savings | 🛡️ **protected** |
| **Recurring jobs** (the same task every week) | Full AI cost, every time | The plugin turns it into a script: **near-zero AI cost from the second time on** — real but by nature not visible in a one-shot benchmark | ✅ **biggest win over time** |

**Three things you buy regardless of savings:** predictable behavior (same task → same
steps), automatic brakes on spending (a wrong cost estimate gets caught *while it happens*,
not on the bill), and verified quality (results are checked, not assumed).

**Measure it on your own work** — instead of taking our word for it:

```bash
python3 fable-director/skills/delega-efficiente/tools/session-cost-report.py
```

It reads your real session logs and prints what each model actually cost you. Your savings
are a figure you read, not a percentage on a banner.

<details>
<summary>📐 <b>The full measured numbers</b> (for technical readers: N, spread, dates, methodology)</summary>

Everything below comes from the reproducible A/B harness in [`benchmarks/`](benchmarks/)
(same task *without* and *with* the policy, tokens read from the real `claude -p` output,
N runs per side).

<!-- BENCH:RESULT — policy effect (equal model, sonnet + fable) + director topology attempt: measured 2026-07-09. -->
> 📐 **Measured — policy effect at equal model** (full enforcement stack via `--settings`; shape-04 quality numbers before the 2026-07-08 fixture fix are not comparable):
>
> | Task shape | sonnet-5, N=3 (07-08) — tokens / USD | fable-5, N=3 (07-09, pre fast-path) | fable-5, N=3 (07-10, **with 1.12.1 fast path**) |
> |---|---|---|---|
> | 01 batch-deterministic | **+17.1% / +10.8%** | −38.9% / −15.1% | **+22.5% / +4.8%** |
> | 02 classification | +6.2% / −2.4% | −5.4% / −8.8% | −5.1% / −7.0% |
> | 03 mixed | +3.0% / −2.8% | −24.2% / −12.7% | (not re-measured) |
> | 04 semantic triage | (pre-fix, not comparable) | **+11.4% / +13.2%**, quality 100% both arms | (not re-measured) |
>
> **The equal-model effect is model-dependent — and the fast path was measured, not assumed.** On sonnet the policy pays where work is deterministic (+17% on 01, and it stabilizes behavior: spread ±267 vs ±40k tokens). On fable the pre-1.12.1 numbers were negative on small shapes: the measured overhead had a behavioral share (policy ceremony on tasks too small to benefit). The 1.12.1 kernel fast path removed it: shape 01 flipped from −38.9% to **+22.5%** tokens (on-arm spread ±103 vs off ±11k — near-deterministic behavior), while shape 02 stayed at ≈−5%: that residue is the kernel's fixed share (~3.5k tokens on a 70k baseline), the insurance premium that remains by design.
>
> 📐 **Measured — director topology** (`MODEL=claude-fable-5` orchestrating, N=2 per side):
>
> | Shape | Tokens saved | Cost saved | Quality (on vs off) |
> |---|---|---|---|
> | 04 — 40 short reviews (2026-07-09, N=2) | −173%¹ | −135%¹ | theme 98% vs 100% |
> | **05 — 240 long reviews, ~124k tokens mandatory reading (2026-07-10, N=4 off / 3 on²)** | **+24.6%** (±33% spread) | +1.7% (≈ parity) | **on ≥ off** (sentiment 98% vs 95%, theme 100% both, safety recall 97% both, precision 100% both) |
>
> **Honest reading, including the surprise.** ¹ On the small shape the policy is pure overhead: telemetry shows zero delegations attempted — the top model correctly declined to delegate 40 micro-items (axis 6), and the delta is policy ceremony at top-model rates. On the worker-heavy shape the forensics upend the framing: **the off-arm delegates too** — Fable natively fans out to sonnet workers without any policy. So the measured differential is not "delegation vs inline"; it is **disciplined delegation vs naive delegation**: fewer, more stable turns (3-7 vs 3-32), ~25% fewer billable tokens at equal USD cost (worker cache reads dominate billing in both arms), slightly better sentiment accuracy — and the enforcement stack fired for real mid-run (budgets opened, one 26× `budget_flag` caught a bad estimate, rung-1+2 verification logged). ² First N=2 measured −51% tokens; consolidation to N=4/3 halved it — variance is high (±150-180k tokens per arm), which is why the spread is published with the number. One on-run died on the plan's 5h session limit and is excluded (aggregate.py now skips `is_error` runs). Safety recall is below 100% in *both* arms on this harder shape — the shape's ceiling, reported not hidden.
>
> Reproduce: `cd benchmarks && RUNS=3 bash run.sh` (equal model) · `MODEL=claude-fable-5 TASKS='tasks/05*.md' RUNS=2 bash run.sh` (topology, ~$15/side).

</details>

### What the benchmarks actually say — in plain language

1. **On small tasks the plugin costs a small fixed premium (~5%).** That's the price of the always-on safety checks — an insurance premium. It used to be bigger: we measured the overhead, fixed its avoidable part (version 1.12.1), and re-measured to confirm — the repetitive-work test flipped from −39% to +22.5% tokens saved.
2. **The plugin knows when NOT to hand work around.** On a task of 40 tiny items it correctly refused to delegate: splitting work that small costs more than it saves, and the plugin encodes that.
3. **On big reading jobs the saving is real: ~25% of tokens at equal cost and quality.** 240 long reviews: −24.6% tokens (variance is high, ±33% — we publish it), dollars unchanged, quality equal or slightly better.
4. **The most interesting finding: the top model already delegates on its own.** Even with no plugin, it hands work to cheaper models — chaotically. The plugin's value is not *making delegation happen* — it's making it **disciplined**: 3-7 steps instead of 3-32, explicit instructions, checked results, and a spending brake that genuinely caught a 26× wrong cost estimate *during* the benchmark.
5. **What no single-shot benchmark can measure** is where the plugin actually aims: turning recurring tasks into scripts (near-zero cost from the second time on) and accumulating verified know-how in its playbook. Those effects show up over weeks of use, not in one session.

**One sentence:** this is not a plugin that saves tokens on every task — it makes spend predictable, verified and disciplined, and on read-heavy loads it cuts about a quarter of the tokens without giving up quality.

---

## 📟 The statusline

One glance at model, context and plan quotas — so you see the rate limit coming **before** it hits:

![fable-director statusline](assets/statusline.svg)

```
[FABLE5] [CTX 26%] [5H 71%→17:30] [7D 46%→14 Jul] [BDG 0.7×·high] [XF GEMINI▲ CODEX×2] [DLG SONNET-5 41k ≡ 3k]
```

### Legend, segment by segment

| Segment | What it shows | Reads from |
|---|---|---|
| `[FABLE5]` | Model driving **this** conversation (the "director") | Claude Code session info |
| `[CTX 26%]` | How full the conversation's context window is | session info |
| `[5H 71%→17:30]` | 5-hour plan-window quota used + local reset time (the "Current session" in `/usage`) | plan rate limits |
| `[7D 46%→14 Jul]` | Weekly plan quota used + reset date | plan rate limits |
| `[BDG …]` | fable-director **pre-budget**: live consumed/expected output ratio + declared effort tier | budget file + session transcript (incremental) |
| `[XF …]` | **Cross-family verifier** (Gemini / Codex) activity | marker file + local telemetry |
| `[DLG …]` | Work **delegated to subagents** this session, tokens per model | session transcript |

Segments with nothing to say disappear (no budget open → no `[BDG]`; no delegation → no `[DLG]`; no cross-family use today → no `[XF]`). Quota colors: green < 60%, yellow ≥ 60%, red ≥ 80%. With the **caveman** plugin its badge stays in front.

### `[BDG]` states

| You see | Meaning |
|---|---|
| `[BDG 0.7×·high]` | Pre-budget open: output consumed so far is 0.7× the declared estimate, declared effort tier `high`. Green < 2×, yellow ≥ 2×, red ≥ 3× — the same accounting and thresholds as the Stop hook, so you see the 2× checkpoint **coming** instead of discovering it when it fires. The ratio updates incrementally from the session transcript (only new lines are read at each render) |
| `[BDG ok]` / `[BDG 2×]` | Fallback when the transcript isn't exposed: budget-file state only (`2×` = the Stop hook checkpoint already fired). The `·effort` suffix still shows if declared |
| `[BDG 3×]` | Blown: ≥3× the estimate — turn closure was blocked until the post-mortem |

### `[XF]` states — cross-family verifier

The external providers expose **no real-time quota API**, so this segment shows *presence*, not remaining quota:

| You see | Meaning |
|---|---|
| *(segment absent)* | No cross-family calls today, none running |
| `GEMINI▲` | A Gemini verification call is **in flight right now** (`▲` disappears when it returns; stale markers >15 min are ignored) |
| `CODEX×2` | 2 Codex calls completed **today** — counted locally by this machine's telemetry, blind to usage of the same key elsewhere |
| `GEMINI▲ CODEX×2` | Both: Gemini running now, Codex used twice today |

Limits check: `cross-verify.py --usage` compares today's counts against the free-tier limits declared in config.

### `[DLG]` states — delegated work

| You see | Meaning |
|---|---|
| *(segment absent)* | No subagent work in this session |
| `SONNET-5 41k` | Subagents running on Sonnet 5 produced **41k output tokens** so far (effective model, read from the transcript — immune to Claude Code's quiet model fallback) |
| `≡ 3k` | Subagents running on the **same model as the main loop** (inherit) produced 3k tokens |
| `≈SONNET-5×2` | Fallback mode (`≈` prefix): transcript not exposed by this Claude Code version → counts **declared** delegation calls from the gate instead of effective tokens |

**Enable it with one command** (idempotent, merge-safe, path auto-resolved per machine):

```
/fable-director:statusline
```

Then restart Claude Code. `--remove` to take it out. It won't touch a third-party statusLine already present and it backs up `settings.json`.

---

## 🧬 Cross-family verifier — when and how

An all-Claude ensemble shares correlated blind spots by construction; a different model family (Gemini, GPT) catches what same-family verification can't. `scripts/cross-verify.py` is that lane — and it's **out of your Claude quota** (Gemini free tier / ChatGPT plan). A third OpenRouter-based lane (DeepSeek) existed until 2026-07: dropped when the last free DeepSeek variant left OpenRouter — two uncorrelated families are enough for the rung, and a lane that can silently die isn't worth its maintenance.

**When Claude invokes it on its own.** It is rung 4 of the verification ladder in the `delega-efficiente` skill — **optional and rare by design**. The director escalates to it only for the *highest-stakes* claims that have no objective test: an irreversible decision, a client-facing number it can't verify deterministically, a critical assumption everything else depends on. It is NOT called on every task — most verification stops at rung 1 (deterministic assertions) or rung 3 (same-family fresh-context verifier). When a call is in flight you see `[XF GEMINI▲]` in the statusline; today's calls show as `[XF CODEX×2]`.

**When YOU can invoke it.** Any time, two ways:

1. **Ask in session** — plain language works: *"verifica questo claim con il cross-family verifier"*, *"fai controllare a Gemini/Codex che…"*. Claude runs the script and reports the verdict.
2. **Directly from any shell:**
   ```bash
   python3 <plugin>/scripts/cross-verify.py \
     --claim "the migration script is idempotent" \
     --rubric "running it twice must not duplicate rows" \
     --context-file migration.sql \
     --provider gemini          # or codex | gemini-stable; omit → config default
   ```
   Output is grep-able (`STATUS` / `PROVIDER` / `VERDICT: refuted|supported|uncertain` / `REASONING`). `--usage` shows today's local call counts against the declared free-tier limits.

**Setup** (once): `cross-verify.py --init` creates `~/.claude/fable-director/cross-family.json`, then add your Gemini key (AI Studio) and/or `codex login`. **No silent fallback**: anything missing → `STATUS: unavailable` + explicit instruction to fall back to the same-family verifier. An `unavailable` is never "verified".

---

## ♻️ Token reduction (lossless-only)

Routing cuts **cost per token** (cheap executor does the heavy work). A separate lever cuts the **token count** itself — but only where it's **provably lossless**, because trading correctness for tokens is the Goodhart failure the kernel exists to prevent.

**The rule.** Reduce tokens by *not re-sending* what's already in context (dedup/diff), by *not re-doing* verified work (idempotent exact-hash cache), or by *reversible* compression. Never by lossy retrieval: replacing a file read with top-k RAG chunks (−90% tokens) drops dependent code and is a **documented anti-pattern** in the playbook. Semantic caching (approximate match) falls under the same ban.

**`read-dedup.py` (opt-in).** A `PostToolUse` hook on `Read`. On a re-read of a file already seen this session it returns only the diff since the previous read (or a short "unchanged" marker), instead of the full content.

- **Lossless & recoverable.** Large files only (> ~2 KB); partial reads (offset/limit) always pass through untouched; a diff is emitted only when it's meaningfully smaller than the file. After any dedup, the *next* read of that file passes through in full — so even if the earlier read was compacted away, the model recovers full content in one more read.
- **Off by default, zero cost when off.** Not in the shipped hooks, so it never runs (no subprocess, no risk) until you opt in.
- **Enable it:**
  ```bash
  export FD_READ_DEDUP=1            # or: touch ~/.claude/fable-director/read-dedup.on
  ```
  then add to your `~/.claude/settings.json` hooks (or the plugin's `hooks/hooks.json`):
  ```json
  "PostToolUse": [
    { "matcher": "Read",
      "hooks": [ { "type": "command",
        "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/read-dedup.py\"" } ] }
  ]
  ```
  Per-session caches live under `~/.claude/fable-director/read-cache/` and are reaped at `SessionEnd`. Validate it in one real session before relying on it.

---

## ⚠️ Known limits (honest by design)

- **Enforcement is local-only.** Hooks (pre-delegation gate, 2×/3× Stop check, telemetry) run on your machine. Sessions on Claude Managed Agents, cloud routines, or any remote harness run **outside** the enforcement stack: the policy kernel still applies if injected, but nothing blocks there.
- **Statusline degrades silently** on Claude Code versions that don't expose the quota fields (< 2.1.x): missing segments simply don't render — no error.
- **Claude Code's quiet model fallback.** If a subagent pins a model that isn't available on your plan/session, Claude Code silently falls back to another model — it does not fail. Routing decisions that assume a specific executor tier should verify it (the statusline shows the active model for the main loop only).
- **Transcript schema dependency.** Token accounting reads Claude Code's undocumented JSONL transcript format. Since 1.7.0 a schema sentinel fails loudly (warning + `schema_anomaly` event, enforcement suspended) instead of silently counting zeros — but accounting remains unavailable until the plugin is updated for the new format.
- **Per-agent `effort` needs a recent Claude Code.** The `fd-executor`/`fd-verifier` agents pin their reasoning tier via the `effort` frontmatter field. Older Claude Code versions ignore unknown frontmatter fields: the agents still work, they just inherit the session effort — silent degradation, no error. Effort coherence (budget `--effort` vs pinned tier) is a warn-only check by design: it never blocks a delegation.

---

## 🚀 Installation

**From this repo:**

```bash
claude plugin marketplace add frsorrentino/fable-director
claude plugin install fable-director@pixelfarm --scope user
```

Then:

1. Initialize the playbook (one-off; it lives outside the plugin so updates don't touch it):
   copy `fable-director/playbook-template.md` to `~/.claude/delega-playbook.md`.
2. Enable the statusline: `/fable-director:statusline` → restart Claude Code.

Full details, manual hook merge and edge cases in **[INSTALL.md](INSTALL.md)**.

---

## 🧭 The 6 routing axes

The kernel decides where each task goes, top-down (a higher axis wins):

1. **Interactivity** — live / visual / iterating with the user? → top model inline, never delegate.
2. **Cost of error** — production code, client-facing numbers/wording, irreversible writes? → top model. When in doubt, it *is* quality-sensitive.
3. **Determinism** — is the core doable by code? → script, zero model tokens.
4. **Cardinality** — N similar items? → a workflow with a grouped mid-tier model, forced JSON schema, fan-out 1+(N-1): one canary verified **before** the rest.
5. **Verifiability** — an objective test? → deterministic assertions; if none → adversarial verification per finding.
6. **Cache locality** — every subagent pays a cold start; switching model invalidates the cache. A cost veto on borderline routes.

**Never delegate:** interactive debugging, aesthetics, client-facing numbers/wording, production writes without a backup.

---

## 🧩 Components

| Piece | Role |
|---|---|
| **Kernel** (SessionStart hook) | Injects the 6 axes + never-delegate each session, ~500 tokens |
| **Skill `delega-efficiente`** | Full policy on-demand: delegation contract, falsifiable pre-budget, rule-of-3 best-of-3, script promotion, playbook rules |
| **`Stop` hook (budget-check)** | Deterministic 3× enforcement: compares actual tokens against the open budget, blocks the turn from closing and imposes the post-mortem |
| **`SessionEnd` hook (telemetry)** | Logs tokens and cache/delegation metrics to SQLite, zero model tokens; reaps per-session registries |
| **`read-dedup.py` (opt-in PostToolUse)** | Lossless re-read dedup: returns diffs instead of re-sending file content already in context — cuts token count, off by default |
| **Playbook** | Learned heuristics that survive updates |
| **`session-cost-report.py`** | Token report from the real JSONL transcripts |
| **Statusline + installer** | `/fable-director:statusline` writes the statusLine to settings.json, idempotent and merge-safe |

Architecture: a **lightweight always-on kernel** (little context each session) + a **heavy on-demand body** (loaded only when the axes fire) + **external enforcement via hooks** (deterministic, not bypassable by the model).

---

## 🤝 Soft dependencies

Works on its own. With the [`caveman`](https://github.com/JuliusBrussee/caveman) (compressed output, `/caveman-stats`) and [`superpowers`](https://github.com/obra/superpowers-marketplace) (systematic-debugging, brainstorming) plugins it shines, degrading gracefully when absent.

## Requirements

- Claude Code ≥ 2.1.x (for the `context_window`/`rate_limits` fields in the statusline; on versions without them it degrades silently)
- `python3` and `bash` on the PATH

## License

[MIT](LICENSE) © 2026 Pixelfarm
