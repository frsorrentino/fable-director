# 🎬 fable-director

**Token governance for Claude Code.** The top model *directs* — plans, judges, verifies — and sends execution to the cheapest adequate means: a deterministic script first, then a mid-tier model, the top model only where it truly matters.

![version](https://img.shields.io/badge/version-1.16.0-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A5CF6)

> Like a Renaissance workshop: the master sketches and refines, the apprentices execute, the workshop accrues craft. This plugin brings that discipline into Claude Code — in a way that is **measurable** and **enforced by hooks**, not left to good intentions.

**Positioning, in three clauses:**
1. **Quality is a constraint, token minimization is the objective** — quality never enters the trade-off; tokens get cut only in the space the constraint leaves open.
2. **Optimization is deterministic** — enforced by blocking scripts and hooks, not by prompt suggestions the model can ignore.
3. **Transparency is guaranteed** — objective telemetry and benchmarks that publish their own limits, negative numbers included.

---

## The problem

A powerful agent tends to do *everything* itself: it reads huge files, repeats deterministic work a script would do for free, delegates in bursts without knowing whether it pays off, burns context. Cost explodes and you don't notice until the bill (or the rate limit) arrives.

## The solution

fable-director cuts the problem at the root, with enforcement — not with a hint in the prompt the model can ignore:

1. **Enforced budget control** — a pre-delegation gate and a Stop hook block the model when it spends beyond its own estimate.
2. **Work off your Claude quota** — adversarial verification and (experimentally) non-code bulk work can run on free external models (Gemini, Codex); Claude keeps the planning and checking share.
3. **Objective local telemetry** — session logs parsed in the background into a local SQLite database: token usage, cache efficiency, delegation overhead. Zero model tokens spent on bookkeeping.

## 🏗️ How it works — hooks in the Claude Code lifecycle

- 🟢 **`SessionStart` (kernel):** injects the routing policy — the 6 axes — in ~500 tokens; the full policy body loads only on demand.
- 🛑 **`PreToolUse` (gate):** intercepts every `Agent`/`Task`/`Workflow` call — no machine-readable budget opened first (`budget-open`) → **the call is denied**.
- 🚧 **`PreToolUse` (perimeter):** the budget can declare *where* the task may write (`--paths`); `Write`/`Edit` outside it are **denied** until an explicit amendment. Your own `never_write` patterns (`.fd-perimeter.json` — e.g. `migrations/*`, `.env*`) are denied unconditionally, budget or not.
- ⚖️ **`PostToolUse` (MCP meter):** measures how many bytes each MCP server's results push into context — the report shows who bloats; zero model tokens.
- ✋ **`Stop` (enforcement):** at each turn end, compares real token usage against the declared budget. Warns once at 2×; at 3× **blocks the turn** until the post-mortem lands in the playbook.
- 📉 **`SessionEnd` (telemetry):** logs session totals to SQLite in the background — statistics without spending a model token. Every closed task also leaves a local **receipt** (estimate vs actual, verification contract, perimeter, amendments) under `~/.claude/fable-director/receipts/`.

## What is enforced, what is advisory, what leaves your machine

| Enforced locally | Advisory to the model | Leaves your machine |
|---|---|---|
| The `PreToolUse` gate denies `Agent`/`Task`/`Workflow` delegation without an open machine-readable pre-budget. The Stop hook checks an open budget at 2× and blocks at 3×. `external-exec.py` verifies an open budget itself. The perimeter hook denies `Write`/`Edit` outside the budget's declared `--paths` and always denies your `never_write` patterns. `--data-class restricted` blocks external routes. | The kernel's routing axes, "never delegate" rules, script promotion, verification ladder, and playbook are policy: they guide decisions but do not mechanically force a route or a quality judgment. | External Gemini/Codex routes are opt-in. When used, the claim, rubric, context, spec, and input content supplied to that route are sent to its configured provider. |

Budget enforcement is local and depends on Claude Code providing a readable transcript with the expected schema. Telemetry and the playbook stay under `~/.claude/fable-director/` and `~/.claude/` on your machine. An external route that is unavailable is never treated as verified or executed.

## 🔁 How it learns from its own mistakes

Every mistake becomes a written lesson — and writing it is not optional:

1. **A blown estimate blocks the session until the lesson is written.** When real spend passes 3× the declared budget, the Stop hook refuses to close the turn until a one-line post-mortem (*which assumption broke?*) lands in the playbook. The overrun itself is already logged automatically.
2. **Lessons live in a small playbook with counters.** A rule is born `[candidate]` from one incident and becomes confirmed only on its **second independent occurrence** — one bad day never becomes doctrine. Every rule carries `uses / ok / ko` counters, updated by outcome.
3. **Rules earn their place or die.** The playbook is hard-capped at 30 lines: at the cap, the counters decide what gets merged or deleted. Unused rules don't accumulate.
4. **Data can override the policy — but only with evidence.** Telemetry breaks outcomes down per task type; measured data may change a routing rule only where there are **at least 10 closed tasks** of that type. Below that, rules stay rules.
5. **Recurring work stops costing.** A task done twice gets crystallized into a script: near-zero AI cost from then on, with a playbook line pointing at it.

Honest boundary, same as the table above: the *writing* of lessons is hook-enforced; *applying* them at the next decision is policy the model follows. And the playbook lives outside the plugin (`~/.claude/delega-playbook.md`), so updates never erase what it learned.

---

## 🆕 What's new

- **1.17.0** — Auto-update self-enables on GitHub-marketplace installs (announced, reversible, opt-out always respected)
- **1.16.1** — Hook scripts run through their interpreter, not the `+x` bit — durable across installs
- **1.16.0** — External executor upgrades distilled from OpenAI's codex-plugin-cc: `--schema-file`, `--resume-last` delta-retry, `--model`/`--effort` overrides, XML-block prompt contracts
- **1.15.4** — Optional Grok (xAI) lane in the cross-family verifier (paid, opt-in via `XAI_API_KEY`)
- **1.15.3** — Executor anti-loop + injection hardening (google/skills sweep); budget accounting fixes

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
| **Recurring jobs** (the same task every week) | Full AI cost, every single time | The plugin turns the repeatable core into a script: from the second run on, **that specific job** costs close to zero — the first run and the surrounding supervision still cost normally | ✅ **the biggest saving — grows with use** |
| **Non-code batches** (classify, extract, transform — experimental) | On your Claude quota | If you connect free external models (Gemini API key or Codex CLI — one-time setup, section *External free-tier models* below), the bulk work runs there; Claude still plans and checks, so a supervision share stays on your quota | ✅ **bulk off the Claude quota** |

To be clear: these deep cuts apply to **specific jobs the plugin can script or route
externally** — not to your Claude usage as a whole. The 20-25% above is what a single-shot
benchmark can see; these two rows are where the design aims, on the jobs that qualify.

**And it improves with time, by design** — see [How it learns from its own mistakes](#-how-it-learns-from-its-own-mistakes) above. The benchmark measures day one; the design compounds after it.

**Included whatever the savings:** predictable behavior (same task → same steps),
automatic brakes on spending (a wrong cost estimate gets caught *while it happens*, not on
the bill), and verified quality (results are checked, not assumed).

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
N runs per side). A positive percentage means savings; a negative one means the policy cost more.

**Summary — all measured shapes, one table** (per-run detail in the blocks below):

| Measured task | Sample | Spread | Token saving | USD saving | Failure or limit |
|---|---:|---:|---:|---:|---|
| **05 — 240 long reviews, ~124k tokens mandatory reading** (2026-07-10) | **N=4 off / N=3 on** | **±33%** | **+24.6%** | +1.7% (≈ parity) | One on-run died on the plan's 5h session limit and was excluded. Quality: on ≥ off; safety recall **97%** in both arms. |
| **01 — batch-deterministic** (2026-07-10, fast path) | N=3 per arm | on ±103; off ±11k | **+22.5%** | +4.8% | The kernel fast path made the on-arm near-deterministic; small, task-specific result. |
| **02 — classification** (2026-07-10, fast path) | N=3 per arm | off ±176; on ±181 | **−5.1%** | −7.0% | A small task still pays the kernel's fixed cost. |
| **04 — 40 short reviews** (2026-07-09) | N=2 per side | not reported | −173% | −135% | Zero delegations attempted: the policy was pure overhead. Theme quality 98% vs 100%. |

The read-heavy result measures **disciplined delegation vs naive delegation**, not delegation vs inline: the off-arm also delegated. Recurring script promotion and external execution may save more on qualifying work, but they are not measured by this single-session table.

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

One glance at model, context and plan quotas — so you see the rate limit coming **before** it hits. **Quiet when healthy, loud in plain words when broken**: compact tags like `[BDG 0.7×·high]` while everything is fine, full-word alarms when it isn't — `⚠ BUDGET 2.3× OF ESTIMATE`, `✕ BUDGET 3× — POST-MORTEM DUE`, `✕ ENFORCEMENT OFF` — with text markers that survive terminals without color. On narrow screens the line degrades deterministically (`[DLG]` drops first, then `[XF]`, never an alarm).

In-session legend any time: **`/fable-director:help`**. On clients with no terminal statusline (smartphone remote control, web) use **`/fable-director:status`**: leads with a *now:* line (open budget, live spend ratio, effort), plus quotas with honest freshness labels and a 7-day burn-rate projection; `--detail` adds session delegations and the last task receipt.

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

## 🧬 External free-tier models (Gemini, Codex) — verifier and executor

**Already have a Google account or a ChatGPT account? It pays to connect them.**
A Google account gets you a free Gemini API key (AI Studio) whose free-tier limits
**reset every day** — a day without calls is free capacity lost. A ChatGPT account gets
you the Codex CLI with usage included in your plan. Prefer paid models instead? The same
config entries take any paid API key — the telemetry judges outcomes the same way.
One-time setup, and the plugin manages them with the same discipline as everything else:
**no silent fallback** (a missing key or a down endpoint fails loudly, never pretends),
every call logged to telemetry, output contracts checked.

The plugin is **proactive about this, deterministically**: on first run (no config yet) a
one-shot notice suggests connecting the accounts; once configured, the delegation gate
suggests the external route by itself when your telemetry confirms a task type works there
(ok-rate ≥ 0.9 on N ≥ 10 runs — data, not enthusiasm), and nudges **once a day** when the
daily free credits are still untouched. Guided setup and health check any time:

```bash
python3 <plugin>/scripts/external-exec.py --doctor          # static checks
python3 <plugin>/scripts/external-exec.py --doctor --ping   # + 1 live call per provider
```

**Separate ledgers, always.** External usage is never mixed with your Claude accounting:
the budget enforcement (2×/3× Stop hook) counts **Claude transcript tokens only**, while
external volume is tracked in its own telemetry events and shown separately (`report`,
`[XF]` statusline segment, `/fable-director:status`). Declare `--route external` at
`budget-open` to keep the decision record clean. The two roles:

**Privacy.** The boundary is enforceable, not just declared: open the budget with
`--data-class restricted` and both `external-exec.py` and `cross-verify.py` refuse to run
for that task — deterministically, script-side. External models are optional. `cross-verify.py` sends the claim, rubric, and any `--context-file` artifact you provide; experimental `external-exec.py` sends the task spec and submitted `--input` content to the selected Gemini API or Codex CLI provider. Treat those materials as third-party disclosures: do not submit secrets, personal data, or proprietary content you are not permitted to share. Local telemetry records call metadata such as provider, model, task type, outcome, and validation status — not the submitted artifact or executor output.

**Role 1 — independent verifier** (`scripts/cross-verify.py`). An all-Claude ensemble shares correlated blind spots by construction; a different model family (Gemini, GPT) catches what same-family verification can't — and it's **out of your Claude quota**. A third OpenRouter-based lane (DeepSeek) existed until 2026-07: dropped when the last free DeepSeek variant left OpenRouter — two uncorrelated families are enough, and a lane that can silently die isn't worth its maintenance.

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

**Role 2 — external executor** (`scripts/external-exec.py`, experimental). For **non-code batches** (classify, extract, transform text) the bulk work can run on the free external models instead of your Claude quota — Claude keeps planning and checking the result. Built-in guardrails: the external model gets a complete spec and must answer in the required format (JSON is validated before anything moves downstream — malformed output is rejected, not passed along), an honest `NEEDS_CONTEXT` stops the run instead of guessing, and every call logs provider/type/outcome so `report` shows where this route actually works. It stays a per-case, experimental route until that data is dense. Since 1.16.0 (distilled from OpenAI's [codex-plugin-cc](https://github.com/openai/codex-plugin-cc)): `--schema-file` enforces a JSON Schema provider-side (Codex `--output-schema`) plus a local required-keys re-check; `--resume-last` continues the last Codex thread of this cwd for a cheap sequential delta-retry after `needs_context`/`json-invalid` instead of resending the whole spec (never in parallel batches); `--model`/`--effort` are runtime overrides on one placeholder-driven provider entry (`--effort low` for bulk exec, the verify default stays `high`); the provider entry can declare its own default `timeout`.

**Setup for both roles** (once): `cross-verify.py --init` creates `~/.claude/fable-director/cross-family.json`, then add your Gemini key (AI Studio) and/or `codex login`. A third, **optional paid lane** is included in the default config: Grok (xAI) — OpenAI-compatible API, activates only if you export `XAI_API_KEY` (no documented free tier as of July 2026, ≈$0.003 per verification with `grok-4.3`); useful as a decorrelated third family when Gemini 503s and the Codex window is spent. **No silent fallback**: anything missing → `STATUS: unavailable` + explicit instruction to fall back to the normal Claude route. An `unavailable` is never "verified" (nor "executed").

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

- **Claude Code versions.** The optional statusline needs Claude Code ≥ 2.1.x for `context_window` and `rate_limits`; older versions omit those segments without an error. Older Claude Code versions may ignore the `effort` frontmatter on `fd-executor` and `fd-verifier`, so those agents inherit the session effort instead — silent degradation, no error. Effort coherence (budget `--effort` vs pinned tier) is a warn-only check by design.
- **Concurrent sessions.** An open budget is one file per working directory. Since 1.13.0 it carries a session lease: `budget-open` refuses to clobber another session's fresh open budget (`--force` to override) and the SessionEnd reaper only closes its own. The file is still one per cwd, so two sessions can't hold budgets on the same directory at once — for parallel budgeted work use separate worktrees.
- **Transcript dependency.** Token accounting reads Claude Code's undocumented JSONL transcript schema. If at least 20 valid records contain no recognized usage or timestamp fields, the schema sentinel warns, logs `schema_anomaly`, and suspends budget enforcement rather than silently counting zero. Update the plugin before relying on accounting again.
- **In-flight subagents.** The Stop hook counts subagent usage after it appears in the main transcript, so work still in flight can be temporarily undercounted.
- **Remote environments.** Managed Agents, cloud routines, and remote harnesses are outside the local hook stack: the injected policy may still apply, but the local gate, Stop check, and telemetry do not.
- **Quiet model fallback.** Claude Code can silently substitute an unavailable subagent model. Treat a requested model as declared; verify the effective model afterward with `session-cost-report.py`.

---

## 🚀 Installation

```bash
claude plugin marketplace add frsorrentino/fable-director
claude plugin install fable-director@pixelfarm --scope user
```

That's all: from the first session the plugin **enables its own auto-update** (announced
in-session, reversible — set `"autoUpdate": false` under `extraKnownMarketplaces.pixelfarm`
in `settings.json` to opt out, and that choice is respected forever). Updates download in
the background; each new session starts on the latest version. No-CLI alternative and
zip-migration notes: **[ONBOARDING.md](ONBOARDING.md)**.

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

[MIT](LICENSE) © 2026 Francesco Sorrentino
