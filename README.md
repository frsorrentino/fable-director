# 🎬 fable-director

**Token governance for Claude Code.** The top model *directs* — plans, judges, verifies — and sends execution to the cheapest adequate means: a deterministic script first, then a mid-tier model, the top model only where it truly matters.

![version](https://img.shields.io/badge/version-1.10.5-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A5CF6)

> Like a Renaissance workshop: the master sketches and refines, the apprentices execute, the workshop accrues craft. This plugin brings that discipline into Claude Code — in a way that is **measurable** and **enforced by hooks**, not left to good intentions.

---

## The problem

A powerful agent tends to do *everything* itself: it reads huge files, repeats deterministic work a script would do for free, delegates in bursts without knowing whether it pays off, burns context. Cost explodes and you don't notice until the bill (or the rate limit) arrives.

## The solution

fable-director injects an **always-on routing policy** and makes it **enforced by deterministic hooks**. It's not a hint in the prompt the model can ignore: it's enforcement.

---

## 🆕 What's new in 1.10.x

- **1.10.5 — lossless read-dedup (opt-in) + token-reduction stance.** New `read-dedup.py` PostToolUse hook: on a **re-read** of a file already seen this session it replaces Read's output with just the diff (or an "unchanged" marker), cutting the biggest agentic token sink — re-sending file content already in context. **Lossless and recoverable:** it only touches large files, skips partial (offset/limit) reads, and after a dedup the *next* read of that file passes through in full — so the model always has a one-read path back to full content even after a compact. **Opt-in by design** (not wired into the default hooks — colleagues on the marketplace build don't inherit it until you validate it): enable with `export FD_READ_DEDUP=1` or `touch ~/.claude/fable-director/read-dedup.on`, then add the hook (see [Token reduction](#-token-reduction-lossless-only)). The routing engine optimizes **cost per token**; this is the first lever that cuts the **token count** itself — and by policy it stays **lossless-only** (RAG-style lossy chunking is now a documented anti-pattern: −90% tokens bought with a correctness risk is the exact Goodhart failure the kernel forbids).
- **1.10.4 — gate denials become telemetry.** Every deny from the pre-delegation gate now logs a `gate_deny` event (kind `no_budget` / `stale_budget` / `flagged`, plus tool and declared model) and `report` prints the breakdown. Without it, post-hoc analysis couldn't distinguish "never attempted delegation" from "denied and fell back inline" — the exact blind spot hit while reading the shape-04 bench. Also fixed a systematically ambiguous shape-04 fixture: a quality-negative phrase ("pannello che si stacca") both arms read as a safety hazard against a ground truth of NO — safety-precision from earlier runs is not comparable (see [benchmarks/README](benchmarks/README.md)).
- **1.10.2 — cross-family lanes verified live.** Default Gemini model for new configs is `gemini-2.5-flash` (verified with a real call: `gemini-3-flash` doesn't exist on the AI Studio free tier, `gemini-flash-latest` answers 503 under load). Both lanes tested end-to-end — Gemini free API and Codex CLI (ChatGPT login) each returned a correct adversarial verdict. **When they fire and how to invoke them yourself: see [Cross-family verifier](#-cross-family-verifier--when-and-how) below.**
- **1.10.1 — `[DLG]` shows effective tokens, not declared calls.** The segment now reads the session transcript **incrementally** (a state file keeps the byte offset — each refresh parses only new lines, never a rescan) and shows **output tokens per effective model** of subagent work: `[DLG SONNET-5 41k HAIKU-4-5 3k]` (`≡` = subagents inheriting the main-loop model). Immune to the quiet-fallback blind spot and it weighs work, not call counts. Where the transcript isn't exposed it degrades to the gate's declared-call registry, marked `≈` — the two modes are visually distinct by design. Also new: **`[XF]` cross-family segment** — `GEMINI▲` while a `cross-verify.py` call is in flight, `CODEX×2` for today's calls (local telemetry; free tiers expose no quota API, so this is presence, not remaining quota).
- **1.10.0 — `[DLG]` statusline segment (declared calls).** The pre-delegation gate keeps a per-session registry of delegations counted by declared model. The registry dies at SessionEnd (48h orphan cleanup for crashed sessions).
- **Benchmark task shape 04 — where governance actually bites.** 40 synthetic customer reviews requiring per-item semantic judgment (closed-vocabulary sentiment/theme + safety-defect flagging, 6 planted defects with ground truth outside the task's view). Unlike shapes 01–03 (script-parity), this one can trigger real delegation → the gate and Stop hook finally get exercised in the `on` arm. `aggregate.py` now also reports **accuracy per arm** (sentiment/theme accuracy, safety recall/precision): savings only count at verified-equal quality — safety recall lost in one arm gets reported, not hidden.

<details><summary>1.9.x</summary>

- **1.9.2 — in-session model visibility.** When a delegation declares an explicit model (`Agent` calls with a `model` field), the gate now prints a one-line notice in session: `FD ▶ delega a modello esplicito: <agent> → <model>`. Inherit stays silent (homogeneous fan-outs produce zero noise). The line shows the *declared* model — the effective one can quietly degrade (see Known limits); post-task truth is `session-cost-report.py` (per-model token breakdown, already shipped).
- **1.9.1 — Codex CLI provider + local usage counter.** Third cross-family lane: `"type": "cli"` providers run a subprocess (Codex CLI with ChatGPT login — fixed-cost, no API billing); spec via stdin, unique mktemp output, `command -v` preflight, quota errors → `unavailable`. New `cross-verify.py --usage`: free tiers expose no quota API (neither Gemini nor ChatGPT/Codex), so this counts today's calls per provider from local telemetry against the declared limits in config (honest label: local counter, blind to key usage elsewhere; a real 429 still fails loudly).
- **Cross-family verifier (optional, ladder rung 4).** `scripts/cross-verify.py` — adversarial check on highest-stakes claims by a **different model family** (all-Claude ensembles share correlated blind spots by construction), out of Claude quota. Zero dependencies (stdlib HTTP), OpenAI-compatible endpoints behind a config file (`--init` creates it: Gemini free API / DeepSeek via OpenRouter free — URLs and models live in config, not code, because free tiers change monthly). **No silent fallback**: missing key, rate limit, endpoint down → `STATUS: unavailable` with the explicit instruction to degrade to the same-family fresh-context verifier — unavailable is never "verified".
- **Benchmark now measures the shipped stack.** The `on` arm injects the full enforcement stack via `--settings` (SessionStart kernel + PreToolUse gate + Stop 2×/3× check) instead of kernel-only `--append-system-prompt`. Budget files are wiped between runs. The published number, when it lands, will measure what you actually install.

<details><summary>1.8.x</summary>

- **1.8.2 — fresh-context verifier + known limits.** Verification ladder rung 3 now requires the LLM verifier to run in a fresh-context subagent that sees only artifact + rubric — never the maker's reasoning trail (inline self-critique is structurally self-preferential). New "Known limits" README section: enforcement is local-only (cloud/CMA sessions run outside the hook stack), statusline degradation, Claude Code's quiet model fallback, transcript schema dependency.
- **1.8.1 — skill-audit fixes.** "Never delegate" lists aligned between kernel and skill (the kernel was missing "decisions on how to count or report"); the skill's trigger description now covers gate denials; new playbook seed: spot delegations get an honest micro-budget for the single call — never a wide session-budget that kills the 2×/3× thresholds.
- **Cache-staleness sentinel.** Claude Code copies plugins to a cache at install time and never re-checks it: with a local marketplace the running plugin silently falls behind its source (we lived it: 1.0.0 running for days while the source was at 1.6.0). A `SessionStart` sentinel now compares the running version against every `directory`-type marketplace source and warns with the exact `claude plugin update` command. Warn only, never auto-update — a nested CLI call in a hook is slow, races the plugin registry, and the current session would stay on the old version anyway.
- **`/fable-director:review`** — the director reads its own telemetry. The learning loop used to write heuristics only on incidents (3× busts, rule-of-3); this command has the top model read `report` + playbook and produce a brutally honest improvement plan anchored **only to objective alarms** (max 5 recommendations, each citing the datum that justifies it; "no intervention justified by the data" is a valid outcome — inventing problems is forbidden).

<details><summary>1.7.0</summary>



- **Pre-delegation gate (the bootstrap gap is closed).** Until 1.6.0 the 2×/3× enforcement only bit *if* a budget had been opened — and opening it was a prompt-level instruction the model could skip. A new `PreToolUse` hook now **denies any `Agent`/`Task`/`Workflow` call with no open budget** for the cwd, replying with the exact `budget-open` command to run. Also denies delegation while a budget is `flagged` (no dodging the 3× post-mortem by delegating) or older than 24h. Fail-open by design: a gate bug can never block a legitimate delegation.
- **Transcript schema sentinel.** Token accounting reads Claude Code's undocumented JSONL transcript format; a silent field rename used to zero the counters without any error. Both the `Stop` hook and `session-summary` now detect "many valid records, zero recognized `usage`/`timestamp`" and **fail loudly**: one-time warning, `schema_anomaly` telemetry event, enforcement suspended instead of trusting zeros.
- **5-part spec contract for delegation prompts** (Objective / Files / Interfaces / Constraints / Verification) — replaces the old 4-component contract; context-free delegation is the test that the route is delegable at all.
- Statusline example renamed `[OPUS4.8]` → `[FABLE5]` (the director role matches the plugin's name; the model was never hardcoded).

</details>
</details>
</details>

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

No magic number: savings depend on your work mix, and this plugin is the first to refuse made-up estimates. But you know **where** they come from and you can **measure** them.

**Where the savings come from:**

- **Deterministic work → script:** a repeatable batch run by a script costs **0 model tokens**, versus the *N* calls of a naïve agent. On recurring deterministic work the cut is close to 100%.
- **Cardinality → mid-tier model:** *N* similar items go to a grouped mid-tier model instead of the top model, with a canary verified before the fan-out.
- **Cache locality:** a cost veto avoids subagent cold starts and cache invalidations that often cost more than the delegated work itself.
- **Lightweight kernel:** ~500 tokens per session always on, the heavy body loaded only when needed.

**Measure it on your real work** — instead of taking our word for it:

```bash
python3 fable-director/skills/delega-efficiente/tools/session-cost-report.py
```

It reads the real JSONL transcripts and prints cost per model/main/subagents, cache hit ratio
and delegation overhead. The `SessionEnd` hook accumulates the same data to SQLite: your
savings are a figure you read, not a percentage on a banner.

> Illustrative example (not a benchmark): converting 12 files with a promoted script →
> ~0 model tokens, versus ~12 model round-trips if the agent did it by hand.

**Reproducible benchmark.** [`benchmarks/`](benchmarks/) contains an A/B harness (same task
*without* and *with* the policy, tokens read from the real `claude -p` output, N runs per side,
4 task shapes). Numbers below come from this harness, with methodology, N and spread.

<!-- BENCH:RESULT — policy effect (equal model). Director topology: pending. -->
> 📐 **Measured — policy effect at equal model** (both arms `claude-sonnet-5`, N=3 per side, full enforcement stack injected via `--settings`, 2026-07-08):
>
> | Task shape | Tokens saved | Cost saved | Quality (on vs off) |
> |---|---|---|---|
> | 01 batch-deterministic | **+17.1%** | **+10.8%** | — |
> | 02 classification | +6.2% | −2.4% | — |
> | 03 mixed | +3.0% | −2.8% | — |
> | 04 semantic triage | −47.7%¹ | −76.0%¹ | identical (safety recall 100% both) |
>
> **Honest reading.** The policy pays where work is deterministic (01 — and it also made behavior *stable*: on-arm spread ±267 tokens vs ±40k off) and roughly breaks even where the base model already behaves well (02-03: kernel overhead ≈ the gain). ¹ Shape 04 is **not conclusive at N=3**: one outlier run ($2.00 vs ~$0.5 median) drives the number, spread is ±62% of the mean, and telemetry shows **no delegation happened** in the on-arm — consistent with axis 6 (for a cheap model on micro-items, inline beats delegation; there is no model differential to harvest at equal model). That differential is the *director topology* measurement (top model orchestrating, cheaper executors), planned separately — see the honesty section in [`benchmarks/README.md`](benchmarks/README.md). Quality was identical across arms.
>
> Reproduce: `cd benchmarks && MODEL=claude-sonnet-5 RUNS=3 bash run.sh`.

---

## 📟 The statusline

One glance at model, context and plan quotas — so you see the rate limit coming **before** it hits:

![fable-director statusline](assets/statusline.svg)

```
[FABLE5] [CTX 26%] [5H 71%→17:30] [7D 46%→14 Jul] [BDG ok] [XF GEMINI▲ CODEX×2] [DLG SONNET-5 41k ≡ 3k]
```

### Legend, segment by segment

| Segment | What it shows | Reads from |
|---|---|---|
| `[FABLE5]` | Model driving **this** conversation (the "director") | Claude Code session info |
| `[CTX 26%]` | How full the conversation's context window is | session info |
| `[5H 71%→17:30]` | 5-hour plan-window quota used + local reset time (the "Current session" in `/usage`) | plan rate limits |
| `[7D 46%→14 Jul]` | Weekly plan quota used + reset date | plan rate limits |
| `[BDG …]` | fable-director **pre-budget** state for this directory | budget file |
| `[XF …]` | **Cross-family verifier** (Gemini / Codex / DeepSeek) activity | marker file + local telemetry |
| `[DLG …]` | Work **delegated to subagents** this session, tokens per model | session transcript |

Segments with nothing to say disappear (no budget open → no `[BDG]`; no delegation → no `[DLG]`; no cross-family use today → no `[XF]`). Quota colors: green < 60%, yellow ≥ 60%, red ≥ 80%. With the **caveman** plugin its badge stays in front.

### `[BDG]` states

| You see | Meaning |
|---|---|
| `[BDG ok]` | A pre-budget is open, consumption under 2× the estimate |
| `[BDG 2×]` | Checkpoint hit: consumption passed 2× — the Stop hook asked the model to reassess the route |
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

An all-Claude ensemble shares correlated blind spots by construction; a different model family (Gemini, GPT, DeepSeek) catches what same-family verification can't. `scripts/cross-verify.py` is that lane — and it's **out of your Claude quota** (Gemini free tier / ChatGPT plan / OpenRouter free).

**When Claude invokes it on its own.** It is rung 4 of the verification ladder in the `delega-efficiente` skill — **optional and rare by design**. The director escalates to it only for the *highest-stakes* claims that have no objective test: an irreversible decision, a client-facing number it can't verify deterministically, a critical assumption everything else depends on. It is NOT called on every task — most verification stops at rung 1 (deterministic assertions) or rung 3 (same-family fresh-context verifier). When a call is in flight you see `[XF GEMINI▲]` in the statusline; today's calls show as `[XF CODEX×2]`.

**When YOU can invoke it.** Any time, two ways:

1. **Ask in session** — plain language works: *"verifica questo claim con il cross-family verifier"*, *"fai controllare a Gemini/Codex che…"*. Claude runs the script and reports the verdict.
2. **Directly from any shell:**
   ```bash
   python3 <plugin>/scripts/cross-verify.py \
     --claim "the migration script is idempotent" \
     --rubric "running it twice must not duplicate rows" \
     --context-file migration.sql \
     --provider gemini          # or codex | deepseek; omit → config default
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
