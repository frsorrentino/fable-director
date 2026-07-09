# 🎬 fable-director

**Token governance for Claude Code.** The top model *directs* — plans, judges, verifies — and sends execution to the cheapest adequate means: a deterministic script first, then a mid-tier model, the top model only where it truly matters.

![version](https://img.shields.io/badge/version-1.12.0-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A5CF6)

> Like a Renaissance workshop: the master sketches and refines, the apprentices execute, the workshop accrues craft. This plugin brings that discipline into Claude Code — in a way that is **measurable** and **enforced by hooks**, not left to good intentions.

---

## The problem

A powerful agent tends to do *everything* itself: it reads huge files, repeats deterministic work a script would do for free, delegates in bursts without knowing whether it pays off, burns context. Cost explodes and you don't notice until the bill (or the rate limit) arrives.

## The solution

fable-director injects an **always-on routing policy** and makes it **enforced by deterministic hooks**. It's not a hint in the prompt the model can ignore: it's enforcement.

---

## 🆕 What's new

- **1.12.0** — External executor (experimental): `external-exec.py` routes non-code axis-4 batches to free external tiers (Gemini Flash, Codex CLI) at zero Claude tokens — cross-family discipline (no silent fallback), built-in JSON rung-1, `external_exec` telemetry per provider/type; the route stays per-case until `report` shows DENSE ok-rate.
- **1.11.1** — `[BDG]` statusline segment shows a live consumed/expected ratio + declared effort tier (`[BDG 0.7×·high]`, Stop-hook accounting, incremental scan): the 2× checkpoint becomes visible on approach, not as a surprise block.
- **1.11.0** — Effort becomes a routing lever: two shipped agents with pinned reasoning tiers (`fd-executor`, effort `low`, for axis-4 batches; `fd-verifier`, effort `high`, for rung-3 adversarial verification), `budget-open --effort` to declare the tier, gate warns (never denies) on declared≠pinned mismatch, `report` breaks flag-rate down per tier — measurement first, enforcement only if the data earns it.
- **1.10.10** — Self-review with the plugin's own ladder (8 finder angles → inline verify → cross-family on Gemini *and* Codex): 6 real bugs fixed — expensive delegations missing from `[DLG]`, timezone-shifted commit windows in yield analysis, double-counted cost reports, discarded cross-family verdicts on uppercase fences, a `≡` marker that never matched, a misleading stale-budget deny — plus 2 hot-path efficiency cuts and doc-drift fixes.
- **1.10.9** — Cost checkpoint: a delegation above the cost ceiling (default 50k expected output, quota-aware) asks *you* instead of silently spending; `--cost-ack` to approve once per task.

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
| `[XF …]` | **Cross-family verifier** (Gemini / Codex / DeepSeek) activity | marker file + local telemetry |
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
