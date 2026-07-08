# 🎬 fable-director

**Token governance for Claude Code.** The top model *directs* — plans, judges, verifies — and sends execution to the cheapest adequate means: a deterministic script first, then a mid-tier model, the top model only where it truly matters.

![version](https://img.shields.io/badge/version-1.8.1-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A5CF6)

> Like a Renaissance workshop: the master sketches and refines, the apprentices execute, the workshop accrues craft. This plugin brings that discipline into Claude Code — in a way that is **measurable** and **enforced by hooks**, not left to good intentions.

---

## The problem

A powerful agent tends to do *everything* itself: it reads huge files, repeats deterministic work a script would do for free, delegates in bursts without knowing whether it pays off, burns context. Cost explodes and you don't notice until the bill (or the rate limit) arrives.

## The solution

fable-director injects an **always-on routing policy** and makes it **enforced by deterministic hooks**. It's not a hint in the prompt the model can ignore: it's enforcement.

---

## 🆕 What's new in 1.8.x

- **1.8.1 — skill-audit fixes.** "Never delegate" lists aligned between kernel and skill (the kernel was missing "decisions on how to count or report"); the skill's trigger description now covers gate denials; new playbook seed: spot delegations get an honest micro-budget for the single call — never a wide session-budget that kills the 2×/3× thresholds.
- **Cache-staleness sentinel.** Claude Code copies plugins to a cache at install time and never re-checks it: with a local marketplace the running plugin silently falls behind its source (we lived it: 1.0.0 running for days while the source was at 1.6.0). A `SessionStart` sentinel now compares the running version against every `directory`-type marketplace source and warns with the exact `claude plugin update` command. Warn only, never auto-update — a nested CLI call in a hook is slow, races the plugin registry, and the current session would stay on the old version anyway.
- **`/fable-director:review`** — the director reads its own telemetry. The learning loop used to write heuristics only on incidents (3× busts, rule-of-3); this command has the top model read `report` + playbook and produce a brutally honest improvement plan anchored **only to objective alarms** (max 5 recommendations, each citing the datum that justifies it; "no intervention justified by the data" is a valid outcome — inventing problems is forbidden).

<details><summary>1.7.0</summary>



- **Pre-delegation gate (the bootstrap gap is closed).** Until 1.6.0 the 2×/3× enforcement only bit *if* a budget had been opened — and opening it was a prompt-level instruction the model could skip. A new `PreToolUse` hook now **denies any `Agent`/`Task`/`Workflow` call with no open budget** for the cwd, replying with the exact `budget-open` command to run. Also denies delegation while a budget is `flagged` (no dodging the 3× post-mortem by delegating) or older than 24h. Fail-open by design: a gate bug can never block a legitimate delegation.
- **Transcript schema sentinel.** Token accounting reads Claude Code's undocumented JSONL transcript format; a silent field rename used to zero the counters without any error. Both the `Stop` hook and `session-summary` now detect "many valid records, zero recognized `usage`/`timestamp`" and **fail loudly**: one-time warning, `schema_anomaly` telemetry event, enforcement suspended instead of trusting zeros.
- **5-part spec contract for delegation prompts** (Objective / Files / Interfaces / Constraints / Verification) — replaces the old 4-component contract; context-free delegation is the test that the route is delegable at all.
- Statusline example renamed `[OPUS4.8]` → `[FABLE5]` (the director role matches the plugin's name; the model was never hardcoded).

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
3 task shapes). The measured percentage will be published here with methodology, N and spread —
not before it's actually been measured.

<!-- BENCH:RESULT — replace once the benchmark has been run -->
> 📐 *Measurement pending.* Run it yourself: `cd benchmarks && RUNS=3 bash run.sh`.

---

## 📟 The statusline

One glance at model, context and plan quotas — so you see the rate limit coming **before** it hits:

![fable-director statusline](assets/statusline.svg)

```
[FABLE5] [CTX 5%] [5H 6%→19:40] [7D 70%→9 Jul] [BDG ok]
```

- `[FABLE5]` active model
- `[CTX 5%]` how full the conversation's context window is
- `[5H 6%→19:40]` 5-hour plan-window quota + local reset time (the "Current session" in `/usage`)
- `[7D 70%→9 Jul]` weekly quota + reset date
- `[BDG ok]` fable-director pre-budget state (`ok` / `2×` / `3×`)

Color thresholds 60/80. If you have the **caveman** plugin, its badge stays in front.

**Enable it with one command** (idempotent, merge-safe, path auto-resolved per machine):

```
/fable-director:statusline
```

Then restart Claude Code. `--remove` to take it out. It won't touch a third-party statusLine already present and it backs up `settings.json`.

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
| **`SessionEnd` hook (telemetry)** | Logs tokens and cache/delegation metrics to SQLite, zero model tokens |
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
