# 🎬 fable-director

![version](https://img.shields.io/badge/version-1.23.0-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-8A5CF6)

**Keeps Claude Code from spending your quota on work the top model didn't need to do.**

What you get:

- **Your quota lasts longer on the jobs that actually eat it** — ~25% fewer tokens on big reading jobs, ~20% on repetitive mechanical work.
- **The job you run every week stops costing you** — repeatable work gets promoted to a script; from the second run, that job is close to free.
- **Your agent can't quietly overspend** — it has to say what a job should cost before it delegates, and a hook blocks the turn at 3× that number. You find out while it happens, not when the limit hits.
- **Bulk work stops eating your Claude quota** — non-code batches and verification can run on free external models; Claude keeps the planning and the checking.
- **You can see where your tokens went** — real numbers from your own session logs, not guesses, and produced without spending a single model token.
- **The agent can't write where you didn't allow it** — a task declares which paths it may touch; anything outside is denied, and your `never_write` patterns always are.
- **Sensitive work never leaves your machine** — mark it restricted and the external routes refuse to run, deterministically.
- **The same mistake doesn't cost you twice** — a job that already blew its estimate says so at session start, on the project where it happened.
- **Quality is never the thing that gets cut** — equal or better everywhere it saves; it's a constraint, not part of the trade.

**The honest price:** small one-off tasks cost **~5% more** — the fixed premium for the checks. If your work is mostly quick one-offs, this plugin is not for you.

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

## 🏗️ How it works — hooks in the Claude Code lifecycle

- 🟢 **`SessionStart` (kernel):** injects the routing policy — the 6 axes — in ~500 tokens; the full policy body loads only on demand.
- 🧠 **`SessionStart` (hindsight):** replays the budget busts this **cwd** has already paid for — auto-recorded by the Stop hook, never self-reported. Silent where there's no history (zero tokens), hard-capped at 5 lines where there is. Registering without retrieving is an archive, not a memory.
- 🧭 **`UserPromptSubmit` (route hint):** matches the prompt against `hint_keywords` declared per-entry in `soft-deps.json` (opt-in) and conservative cardinality signals when external providers are configured; on match injects up to 3 `[fd-route-hint]` candidate lines the model must **evaluate** — the entry's `quality_guard`/`data_class` stay sovereign — and logs a `route_hint` event (names only, never the prompt text). Silent on no match: zero tokens.
- 🛑 **`PreToolUse` (gate):** intercepts every `Agent`/`Task`/`Workflow` call — no machine-readable budget opened first (`budget-open`) → **the call is denied**.
- 🚧 **`PreToolUse` (perimeter):** the budget can declare *where* the task may write (`--paths`); `Write`/`Edit` outside it are **denied** until an explicit amendment. Your own `never_write` patterns (`.fd-perimeter.json` — e.g. `migrations/*`, `.env*`) are denied unconditionally, budget or not.
- ⚖️ **`PostToolUse` (MCP meter):** measures context weight along **two** distinct axes — *flow* (bytes each MCP server's results push into context, paid once per call) and *stock* (schema bytes a `ToolSearch` load injects into the prefix, re-paid **every turn** of the session). The report keeps them separate and never sums them; zero model tokens.
- 🔁 **`PostToolUse` (fail-streak):** counts *consecutive* failing Bash commands, recomputed from the transcript each time so there's no counter to drift (resets on the first success; your own denials never count). At every 3rd it injects the rule-of-3 — diagnose the failure **type** before retrying, blind escalation is itself waste — surfaces the streak on the statusline as `[FAIL ×N]`, and logs it. Advisory: it never blocks.
- ✋ **`Stop` (enforcement):** at each turn end, compares real token usage against the declared budget. Warns once at 2×; at 3× **blocks the turn** until the post-mortem lands in the playbook.
- 📉 **`SessionEnd` (telemetry):** logs session totals to SQLite in the background — statistics without spending a model token. Every closed task also leaves a local **receipt** (estimate vs actual, verification contract, perimeter, amendments) under `~/.claude/fable-director/receipts/`.

## What is enforced, what is advisory, what leaves your machine

| Enforced locally | Advisory to the model | Leaves your machine |
|---|---|---|
| The `PreToolUse` gate denies `Agent`/`Task`/`Workflow` delegation without an open machine-readable pre-budget. The Stop hook checks an open budget at 2× and blocks at 3×. `external-exec.py` verifies an open budget itself. The perimeter hook denies `Write`/`Edit` outside the budget's declared `--paths` and always denies your `never_write` patterns. `--data-class restricted` blocks external routes. | The kernel's routing axes, "never delegate" rules, script promotion, verification ladder, and playbook are policy: they guide decisions but do not mechanically force a route or a quality judgment. | External Gemini/Codex routes are opt-in. When used, the claim, rubric, context, spec, and input content supplied to that route are sent to its configured provider. |

Budget enforcement is local and depends on Claude Code providing a readable transcript with the expected schema. Telemetry and the playbook stay under `~/.claude/fable-director/` and `~/.claude/` on your machine. An external route that is unavailable is never treated as verified or executed.

## 💸 How much do you save?

**The honest one-sentence answer:** this is not a plugin that saves tokens on every task — it makes spend predictable, verified and disciplined, and on read-heavy loads it cuts about a quarter of the tokens without giving up quality.

The long answer, measured by running the **same tasks with and without the plugin**, several times each, counting real tokens and real dollars:

| Kind of work | With the plugin | Verdict |
|---|---|---|
| **Big reading jobs** (e.g. 240 long customer reviews) | Same result in 3-7 steps instead of 3-32, **~25% fewer tokens**, same dollars, quality equal or slightly better | ✅ **saves** |
| **Repetitive mechanical work** (e.g. 30 data files) | **~20% fewer tokens** and near-identical behavior every run | ✅ **saves** |
| **Quick small tasks** (one question, one small fix) | **~5% more** — the fixed price of the always-on checks, like an insurance premium | ➖ **small premium** |
| **Quality of results** | Equal or better everywhere it saves (e.g. 98% vs 95%); never traded for savings | 🛡️ **protected** |
| **Recurring jobs** (the same task every week) | The repeatable core becomes a script: from the second run, **that job** is close to free | ✅ **the biggest saving — grows with use** |
| **Non-code batches** (classify, extract, transform) | The bulk runs on free external models; Claude still plans and checks | ✅ **off your Claude quota** |

To be clear: the deep cuts apply to **specific jobs the plugin can script or route externally**, not to your Claude usage as a whole. The 20-25% is what a single-shot benchmark can see; the last two rows are where the design actually aims, and they compound over weeks — see [How it learns from its own mistakes](#-how-it-learns-from-its-own-mistakes).

Two findings worth more than the percentages:

- **The top model already delegates on its own — chaotically.** Even with no plugin it hands work to cheaper models. So the value isn't *making delegation happen*: it's making it **disciplined** — 3-7 steps instead of 3-32, explicit specs, checked results, and a brake that genuinely caught a 26× wrong cost estimate *during* the benchmark.
- **It knows when NOT to hand work around.** On a task of 40 tiny items it correctly refused to delegate: splitting work that small costs more than it saves.

**Don't take our word for it — measure your own work:**

```bash
python3 fable-director/skills/delega-efficiente/tools/session-cost-report.py
```

**The full data is published, negative numbers included** — method, per-run tables, variance (±33% on the read-heavy shape), the run that died on a session limit, and what the harness *can't* measure: **[benchmarks/README.md](benchmarks/)**. One of the four shapes came out at **−5.1%**; it's in there too.

## ⚠️ Known limits (honest by design)

- **Claude Code versions.** The optional statusline needs Claude Code ≥ 2.1.x for `context_window` and `rate_limits`; older versions omit those segments without an error. Older Claude Code versions may ignore the `effort` frontmatter on `fd-executor` and `fd-verifier`, so those agents inherit the session effort instead — silent degradation, no error. Effort coherence (budget `--effort` vs pinned tier) is a warn-only check by design.
- **Concurrent sessions.** An open budget is one file per working directory. Since 1.13.0 it carries a session lease: `budget-open` refuses to clobber another session's fresh open budget (`--force` to override) and the SessionEnd reaper only closes its own. The file is still one per cwd, so two sessions can't hold budgets on the same directory at once — for parallel budgeted work use separate worktrees.
- **Transcript dependency.** Token accounting reads Claude Code's undocumented JSONL transcript schema. If at least 20 valid records contain no recognized usage or timestamp fields, the schema sentinel warns, logs `schema_anomaly`, and suspends budget enforcement rather than silently counting zero. Update the plugin before relying on accounting again.
- **In-flight subagents.** The Stop hook counts subagent usage after it appears in the main transcript, so work still in flight can be temporarily undercounted.
- **Remote environments.** Managed Agents, cloud routines, and remote harnesses are outside the local hook stack: the injected policy may still apply, but the local gate, Stop check, and telemetry do not.
- **Quiet model fallback.** Claude Code can silently substitute an unavailable subagent model. Treat a requested model as declared; verify the effective model afterward with `session-cost-report.py`.

## 🔁 How it learns from its own mistakes

Every mistake becomes a written lesson — and writing it is not optional:

1. **A blown estimate blocks the session until the lesson is written.** When real spend passes 3× the declared budget, the Stop hook refuses to close the turn until a one-line post-mortem (*which assumption broke?*) lands in the playbook. The overrun itself is already logged automatically.
2. **Lessons live in a small playbook with counters.** A rule is born `[candidate]` from one incident and becomes confirmed only on its **second independent occurrence** — one bad day never becomes doctrine. Every rule carries `uses / ok / ko` counters, updated by outcome.
3. **Rules earn their place or die.** The playbook is hard-capped at 30 lines: at the cap, the counters decide what gets merged or deleted. Unused rules don't accumulate.
4. **Data can override the policy — but only with evidence.** Telemetry breaks outcomes down per task type; measured data may change a routing rule only where there are **at least 10 closed tasks** of that type. Below that, rules stay rules.
5. **Recurring work stops costing.** A task done twice gets crystallized into a script: near-zero AI cost from then on, with a playbook line pointing at it.

Honest boundary, same as the table above: the *writing* of lessons is hook-enforced; *applying* them at the next decision is policy the model follows. And the playbook lives outside the plugin (`~/.claude/delega-playbook.md`), so updates never erase what it learned.

## 🧭 The 6 routing axes

The kernel decides where each task goes, top-down (a higher axis wins):

1. **Interactivity** — live / visual / iterating with the user? → top model inline, never delegate.
2. **Cost of error** — production code, client-facing numbers/wording, irreversible writes? → top model. When in doubt, it *is* quality-sensitive.
3. **Determinism** — is the core doable by code? → script, zero model tokens.
4. **Cardinality** — N similar items? → a workflow with a grouped mid-tier model, forced JSON schema, fan-out 1+(N-1): one canary verified **before** the rest.
5. **Verifiability** — an objective test? → deterministic assertions; if none → adversarial verification per finding.
6. **Cache locality** — every subagent pays a cold start; switching model invalidates the cache. A cost veto on borderline routes.

**Never delegate:** interactive debugging, aesthetics, client-facing numbers/wording, production writes without a backup.

## 🧬 External free-tier models (Gemini, Codex)

**Already have a Google or a ChatGPT account? It pays to connect them.** Their free tiers **reset every day** — a day without calls is capacity lost, not saved.

**Setup, once:** `cross-verify.py --init` writes `~/.claude/fable-director/cross-family.json`; add a Gemini key ([AI Studio](https://aistudio.google.com/apikey)) and/or run `codex login`. Check it with `external-exec.py --doctor`.

Two roles, both **off your Claude quota**:

- **Independent verifier.** An all-Claude ensemble shares correlated blind spots by construction; a different model family catches what same-family verification can't. It is rung 4 of the verification ladder — **rare by design**: the director escalates to it only for high-stakes claims with no objective test, never on every task. You can also call it yourself: `cross-verify.py --claim "..." --rubric "..."`.
- **External executor** (experimental). For **non-code batches** (classify, extract, transform) the bulk work runs there while Claude keeps the planning and the checking. Guardrails: the external model gets a complete spec and must answer in the required format — malformed output is rejected, never passed downstream — and an honest `NEEDS_CONTEXT` stops the run instead of guessing. Every call logs provider/type/outcome, so `report` shows where this route actually works; it stays per-case until that data is dense.

**Separate ledgers, always.** External usage is never mixed with your Claude accounting: the 2×/3× budget counts Claude tokens only.

**Privacy is enforced, not promised.** Open the budget with `--data-class restricted` and the external routes refuse to run — deterministically, not by good intentions. When you do use them, what leaves your machine is the claim, rubric, spec and input you supplied.

**No silent fallback.** Missing key, dead endpoint, spent window → `STATUS: unavailable` and an explicit instruction to fall back to the normal Claude route. An `unavailable` is never "verified", nor "executed".

Optional paid third lane: Grok (xAI), OpenAI-compatible, active only if you export `XAI_API_KEY` (≈$0.003 per verification; no free tier as of July 2026). Useful when Gemini 503s and the Codex window is spent.

## 📟 The statusline

One glance at model, context and plan quotas — so you see the rate limit coming **before** it hits. **Quiet when healthy, loud in plain words when broken**: compact tags like `[BDG 0.7×·high]` while everything is fine, full-word alarms when it isn't (`⚠ BUDGET 2.3× OF ESTIMATE`, `✕ BUDGET 3× — POST-MORTEM DUE`), with text markers that survive terminals without color. On narrow screens it degrades deterministically — never dropping a budget, quota or alarm state.

![fable-director statusline](assets/statusline.svg)

```
[FABLE5] [CTX 26%] [CMP 1] [5H 71%→17:30] [7D 46%→14 Jul] [BDG 0.7×·high] [FAIL ×3] [CACHE 47m] [XF GEMINI×2] [DLG SONNET-5 41k]
```

Read left to right — each tag answers one question, and colour goes green → yellow → red as it needs attention:

| Tag | What it tells you |
|---|---|
| `[FABLE5]` | Which model is driving this session |
| `[CTX 26%]` | How full the context window is — red near the top means a compaction is coming |
| `[CMP 1]` | How many times context was compacted this session (each one dropped history); hidden until the first |
| `[5H 71%→17:30]` | Your 5-hour plan quota used, and when it resets |
| `[7D 46%→14 Jul]` | Your weekly plan quota used, and when it resets |
| `[BDG 0.7×·high]` | Current task spend vs the estimate it declared (0.7× = under budget); turns to a full-word alarm at 2× and 3× |
| `[FAIL ×3]` | Bash commands failing in a row — a sign you're grinding; shows from 2, red at 3 where the plugin nudges you to step back |
| `[CACHE 47m]` | How long the prompt cache stays warm — cheap to keep working now, a fresh start costs more |
| `[XF GEMINI×2]` | Calls sent to a free external model today (verification or bulk work, off your Claude quota) |
| `[DLG SONNET-5 41k]` | Work handed to cheaper models this session, and how much |

Healthy tags stay compact and quiet; when something breaks they turn into full words that survive terminals without colour (`⚠ BUDGET 2.3× OF ESTIMATE`, `✕ BUDGET 3× — POST-MORTEM DUE`). On narrow screens the line trims the least urgent tags first (`[CACHE]`, then `[DLG]`, then `[XF]`) and never drops a budget, quota or alarm.

**Turn it on:** `/fable-director:statusline`, then restart Claude Code. Idempotent, backs up `settings.json`, won't touch a third-party statusLine already there; `--remove` takes it out.

**Deeper reference** — every alarm state, the colour thresholds, the `[BDG]`/`[XF]` sub-states — is one command away in-session: `/fable-director:help`. (This table is the friendly intro; that one is the full spec, shipped with the plugin so it never drifts from the code.)

**No terminal statusline** (phone, web client): `/fable-director:status` prints the same state as text — open budget, live spend ratio, quotas with honest freshness labels, 7-day burn-rate projection. `--detail` adds session delegations and the last task receipt.

## ♻️ Token reduction (lossless-only) — and why the plugin ships none

Routing cuts **cost per token** (cheap executor does the heavy work). A separate lever cuts the **token count** itself — but only where it's **provably lossless**, because trading correctness for tokens is the Goodhart failure the kernel exists to prevent. Never lossy retrieval: replacing a file read with top-k RAG chunks (−90% tokens) drops dependent code and is a **documented anti-pattern** in the playbook. Semantic caching (approximate match) falls under the same ban.

**read-dedup, retired on measurement (1.18.0).** Versions 1.10.5–1.17.1 shipped an opt-in `PostToolUse` hook that replaced identical re-reads with a diff. Before promoting it to a default we measured the target on real traffic — 1,278 sessions across two accounts, using the audit methodology from [headroom](https://github.com/headroomlabs-ai/headroom) (`audit-reads`): **identical re-reads are 0.0–0.1% of Read bytes**. Headroom measured the same 0.1% on their traffic and removed their equivalent mechanism too. A lever aimed at 0.1% is maintenance without payoff, so it's gone; the `SessionEnd` reaper still cleans up legacy `read-cache/` dirs for anyone who had it enabled. The same audit shows where re-read bytes actually are — stale reads after edits (26–41%) and `cat -n` line-number scaffolding (4–7.5%) — both outside what a simple lossless hook can fix without touching content.

**If you want serious context compression**, use a dedicated tool alongside fable-director — the jobs compose (they compress, director governs): [headroom](https://github.com/headroomlabs-ai/headroom) (Apache-2.0, local proxy/library; content-aware and *reversible via retrieval*, but it does modify what the model sees — weigh that against axis 2 for quality-sensitive work; its `wrap claude` forwards your OAuth login, so subscription billing is preserved, and it disables Claude Code's `/remote-control` on ≥2.1.196) or [Token Optimizer](https://github.com/alexgreensh/token-optimizer) (local hooks, noncommercial license). fable-director stays governance-only: measure first, then decide — the lesson this section now records.

## 🧩 Components

| Piece | Role |
|---|---|
| **Kernel** (SessionStart hook) | Injects the 6 axes + never-delegate each session, ~500 tokens |
| **Hindsight** (SessionStart hook) | Replays this cwd's already-paid budget busts (auto-recorded, max 5 lines); silent where there's no history |
| **Skill `delega-efficiente`** | Full policy on-demand: delegation contract, falsifiable pre-budget, rule-of-3 best-of-3, script promotion, playbook rules |
| **`Stop` hook (budget-check)** | Deterministic 3× enforcement: compares actual tokens against the open budget, blocks the turn from closing and imposes the post-mortem |
| **`SessionEnd` hook (telemetry)** | Logs tokens and cache/delegation metrics to SQLite, zero model tokens; reaps per-session registries |
| **Playbook** | Learned heuristics that survive updates |
| **`session-cost-report.py`** | Token report from the real JSONL transcripts |
| **Statusline + installer** | `/fable-director:statusline` writes the statusLine to settings.json, idempotent and merge-safe |

Architecture: a **lightweight always-on kernel** (little context each session) + a **heavy on-demand body** (loaded only when the axes fire) + **external enforcement via hooks** (deterministic, not bypassable by the model).

## 🤝 Soft dependencies

Works on its own. These optional companions save further tokens, degrading gracefully when absent.

- **[`chrome-bridge`](https://github.com/frsorrentino/chrome-bridge)** — browser automation (same author): zero-token CLI lane, **2.3–2.8× fewer tokens** than the official Chrome extension. Routed by the kernel out of the box.
- **[`caveman`](https://github.com/JuliusBrussee/caveman)** — compressed output style, **~65% fewer output tokens** (measured).
- **[`superpowers`](https://github.com/obra/superpowers-marketplace)** — process discipline (systematic-debugging, brainstorming): a well-framed task delegates better.

## 🆕 What's new

- **1.24.0** — Paid providers consent-gated (`billing` field fail-closed + `--paid-ok`); Gemini image route (`type: "image"`)
- **1.23.0** — Proactive route verdict: `[fd-route-hint]` at prompt time from soft-deps keywords + cardinality signals
- **1.22.0** — Workflow agent tokens enter enforcement/telemetry; quota guard on new fan-outs; `--agents N` estimate anchor
- **1.21.0** — `[FAIL ×N]` on the statusline; legend back in the README
- **1.20.0** — Fail-streak hook: rule-of-3 injected at 3 consecutive Bash failures

Full history: [CHANGELOG.md](CHANGELOG.md).

## Requirements

- Claude Code ≥ 2.1.x (for the `context_window`/`rate_limits` fields in the statusline; on versions without them it degrades silently)
- `python3` and `bash` on the PATH

## License

[MIT](LICENSE) © 2026 Francesco Sorrentino
