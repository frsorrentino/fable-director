---
name: delega-efficiente
description: Use when planning how to execute a task that involves delegation or orchestration - before launching subagents/workflows, choosing models, starting multi-item or multi-file batch work, consuming large tool outputs, when the context window is filling up, when the pre-delegation gate denied an Agent/Task/Workflow call, or when closing a task that blew past its expected cost or escalated through repeated failures. The always-on kernel already carries the 6 routing axes; load this full body only when the axes fire, not merely because a session started.
---

# Delega efficiente

Operating policy (user directive): the top model plans, judges and verifies; execution goes to the cheapest adequate means. Scripts cost zero — prefer them over any model call for deterministic work. Quality-sensitive code always goes to the top model, accepting extra tokens.

Policy complexity budget (meta-rule): every addition to this skill must delete an existing policy or replace two exceptions — the skill never grows net. Same principle as the playbook's 30-line cap: each rule is a permanent cognitive cost paid at every invocation.

## Route by task properties

Any route involving delegation/orchestration declares a one-line pre-budget (see "Falsifiable pre-budget"). Before planning batch/workflow/multi-agent work, consult the playbook at `~/.claude/delega-playbook.md` (init from the plugin's `playbook-template.md` if missing; a team may point it to a shared repo file via symlink).

Route the REMAINDER, not the task: sunk tokens never justify staying on the current route — compare the cost of the NEXT step only. A mid-task route change is a `reversal` (log it — see Telemetry): not an error, a falsified initial decision.

Score the task on six axes, top to bottom; earlier axes override later ones:

1. **Interactivity** — needs live iteration (visual, browser, user feedback)? → top model inline, never delegate.
2. **Error cost** — production code, client-facing numbers/wording, irreversible or externally visible writes? → top model, accepting extra tokens. Reclassifying a task as low-error-cost to save tokens is the canonical Goodhart failure: when in doubt, it IS quality-sensitive. Risk class only ratchets UP mid-task, never down.
3. **Determinism** — is the core doable by code? → script, zero model tokens (see Script promotion).
4. **Cardinality** — N similar items? → workflow, mid-model agents grouped ~10-15 by affinity (brand/domain/file), forced JSON schema. Default executor: agent `fd-executor` (shipped with the plugin, effort pinned `low` in frontmatter — the Agent tool has no per-call effort parameter, so pinned agents are the only real effort lever; declare the tier with `budget-open --effort`). Single item, 1-2 files → inline, zero orchestration. Fan-out is 1+(N-1): ONE canary verified rung-1 before the rest — a systemic failure (hallucinated schema, changed DOM) costs 1× not N×. Canary guards systemic failures only; skip for playbook-confirmed recurring pipelines.
5. **Verifiability** — objective test exists? → deterministic assertions (ladder rung 1). None → contract-first checklist + adversarial verifier per finding on top model (rung 3).
6. **Cache locality** — every subagent pays a COLD START: own uncached prefix, re-fed context (it does NOT break the main thread's cache); switching model or editing plugins/skills mid-session invalidates the prompt cache. Before spawning: does the cold start cost more than the delegation saves? Topology follows: one workflow with grouped items ≫ N single-item subagents (one warm prefix vs N cold starts); forks share the parent's prefix. Cost VETO on borderline axis-4/5 routes — never forces delegation, never overrides axes 1-2.

Axes compose rather than exclude: a batch (4) of quality-sensitive items (2) → mid-model executes, top model reviews every diff before delivery.

Route verdict: on every non-trivial task, one line — cheaper/external route yes/no and why, citing both the allowing and the forbidding axis. `[fd-route-hint]` lines (UserPromptSubmit hook, keyword match on `hint_keywords` in soft-deps.json + cardinality signals) are deterministic CANDIDATES to evaluate, never directives; `quality_guard`/`data_class` of the matched entry stay sovereign. Hint events land in telemetry (`route_hint`) so `/review` can compare hinted vs taken routes.

**Binding precedents** — anchor the axes, not exhaustive:
- Production code (any stack) → axis 2: top model inline; mid-model ONLY for verbatim transcription from a complete spec; new features via brainstorm→spec→plan. Verify: tests/lint/dry-run + top-model diff review.
- Bug fixing → axes 1+2: top model inline (systematic debugging); delegate only broad code search (a read-only search agent). Verify: reproduce → fix → re-test.
- Web design / UI → axis 1: never delegate aesthetics. Verify: screenshot + user.
- SEO / migrations / imports → axis 4. Verify: assertion ladder. Non-code batch items may route to a free external executor (`scripts/external-exec.py` — Gemini/Codex via cross-family config, setup check `--doctor`, logs `external_exec`, built-in JSON rung-1; `--schema-file` enforces a JSON Schema provider-side (Codex `--output-schema`) plus a local required-keys re-check; `--resume-last` continues the last Codex thread of this cwd for a sequential delta-retry after `needs_context`/`json-invalid` — never in parallel batches; `--effort low` for massive batches vs the config's `high` verify default, one placeholder-driven provider entry): per-case choice until `report` shows a DENSE ok-rate for that type (the gate itself suggests the route once a type is confirmed, and nudges once/day on idle daily free credits); axis 2 items never take this route. When configured, PROPOSE it to the user for eligible batches. Accounting is a SEPARATE LEDGER: declare `--route external`; the 2×/3× budget counts Claude transcript tokens only, external volume lives in `external_exec` events (`report`, `[XF]`, `/status`) and is never mixed with the Claude budget.
- Text/content editing → mid-model with primary source; client-facing tone and numbers hit axis 2. Verify: spot-check. Axis-2-borderline content: draft-and-patch — mid drafts the complete deliverable, top model EDITS the draft instead of re-authoring. NEVER draft-and-patch for production code (Goodhart reentry).
- Consulting / web research → fan-out on mid-model, synthesis and judgment on top model; adversarial verify risky claims only.

## Delegation contract

- Before decomposing, pin the TASK's verifiable **done** — a command that passes, an observable behavior, or an enumerable checklist — plus the **stop condition** (include a failure cap, e.g. "stop after 3 attempts, emit a blocker report"). If done isn't verifiable, interview until it is BEFORE delegating: the top model owns "done", executors never self-assess it. Unverifiable done means the delegation isn't ready — not a licence to wing it. (This is the up-front twin of the Verification ladder, which is HOW you check; this is WHAT counts as finished.)
- Every delegation prompt is a 5-part spec contract — Objective / Files (exact paths in scope) / Interfaces (output format + status token) / Constraints (tools, sources, boundaries, hard caps) / Verification (the command or check the subagent must run and report actual output of). Vague delegation ("occupati di X") duplicates work — forbidden; a spec the subagent can execute without shared context is the test that the route is delegable at all. For GPT/Codex-family executors (external route) wrap the 5 parts in XML blocks with stable tag names (`<task>`, `<output_contract>`, `<default_follow_through_policy>`, `<grounding_rules>`): block contracts hold better than prose on that family (distilled from openai/codex-plugin-cc); tighten the contract before raising effort.
- The subagent ends with a status token: `DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED / ABSTAIN`. Grant explicit permission to ABSTAIN when unsure — an honest abstention triggers immediate escalation and skips a whole verify-fail-diagnose cycle; plausible-but-wrong is the worst outcome. Status feeds the rule-of-3 diagnosis.
- Interfaces in practice: hard cap (default ~1-2k tokens), forced schema (strict JSON or grep-able `path:line — finding` lines), full-content dumps forbidden — paths, counts, anomalies only. The subagent's output is the next turn's input: a token the orchestrator won't consume is waste.
- Scaling bands: simple fact-finding → 1 agent; direct comparisons → 2-4; broad research → 10+. Never exceed the band. Multi-agent costs an order of magnitude more than a single chat (measure YOUR ratio: `report` delegation_overhead — asserted multipliers from old research decay): if task value doesn't justify that multiple, don't orchestrate.

## Harness mechanics (learned from real waste)

- NEVER read a workflow's full output/TaskOutput dump. Extract via script from `<transcriptDir>/journal.jsonl` (raw result per agent; map agentId→item by grepping each `agent-*.jsonl` prompt).
- Run failed partway (session limit, hang): relaunch with `resumeFromRunId` — completed agents replay from cache, only failures re-run. Hung run: `TaskStop` first. Never restart from zero.
- Subagents return path + counts + anomalies, never full content.
- Tool output is next turn's input — the same discipline applies to YOUR tool calls: never cat whole files; grep → head → partial Read (offset/limit) → summary script. Same for `git diff` (`--stat`, `--name-only` first) and logs (`tail`, pattern filter). The harness truncates >25k, but the 2-20k band passes whole and silently bloats context.

## Verification ladder

1. Deterministic assertions covering ~100% (counts, grep, length/schema checks, tests).
2. Top-model spot-check on 2-3 samples.
3. LLM verifiers only for individually risky claims — use agent `fd-verifier` (shipped with the plugin: read-only, effort pinned `high`, one verdict per rubric finding), always in a fresh-context subagent that receives ONLY artifact + rubric, never the maker's reasoning trail (inline self-critique is structurally self-preferential: the maker prefers conclusions consistent with what it already wrote); per-item across a batch only when no objective test exists.
4. (optional, rare, highest stakes) Cross-family verifier: `scripts/cross-verify.py --claim ... --rubric ... [--type SLUG]` — a different model family has uncorrelated blind spots, and it's out of Claude quota. Soft-dep: on `STATUS: unavailable` fall back to rung 3 EXPLICITLY — unavailable is never "verified".
   - WHEN it's worth the rung (decorrelation on an OBJECTIVE claim rungs 1-3 don't settle — NOT "the other model is better", an unverified capability claim that decays): seed candidates `cross-lingua` (IT↔EN client deliverables — Gemini's different multilingual training is genuinely uncorrelated), `security-review` (shared "looks-safe" blind spots break across families), `spec-compliance` / numeric-algorithmic (the other family derives it independently). NOT for subjective/aesthetic (no ground truth → decorrelation is noise) nor anything a rung-1 assertion already covers.
   - These 3 are a SEED, not doctrine: always pass `--type <slug>` so telemetry logs which types cross-family actually REFUTES. `fd-telemetry.py report` breaks hit-rate down per type — the list is confirmed or killed by data (same discipline as the per-type density table), never by asserted model rankings.

Gate depth on OBSERVED risk too, not only predicted: huge diff, many files touched, flaky tests escalate the rung even when the task was classified low-risk — never de-escalate below the axis-2 floor. A clean verification is paid insurance, not waste: the logged hit-rate (see Telemetry) calibrates depth per task type, it is NEVER a reason to skip verification where error cost is high.

## Escalation on repeated failure (rule of 3)

Diagnose the failure TYPE before retrying — blind model escalation is itself waste:
- infra (timeout, 403, rate/session limit) → retry/resume, same executor; escalation doesn't help
- capability (wrong/incomplete output again) → if output is objectively verifiable, first try best-of-3: three independent generations on the SAME executor, pick the best (pays when top costs ≫ 3× mid). Still failing → escalate model
- approach (same error, same strategy) → change strategy/diagnosis, not just the model
- tool/target (web nav misses element, form won't submit) → change tool or technique (e.g. static fetch → real browser, blind selector → wait-for-element → screenshot), not the model

Ladder: 1st failure → targeted fix, retry same executor. 2nd → change something STRUCTURAL (model ↑ or tool or approach, per type above). 3rd → stop the loop: top model takes it inline, or ask the user. Never an identical 4th automatic attempt.

No-progress termination (independent of retries and budget): if the last ~5 turns produced no new artifact, test result, or verifiable fact, stop and ask the user. Does not apply to user-requested analysis, where prose IS the artifact.

## Falsifiable pre-budget

Before executing any task that involves delegation or orchestration, the plan states one line: `approach / fallback / expected input tokens / expected output tokens`. Estimation anchors (don't guess from feel): expected input ≈ bytes of files/outputs to be read ÷ 4, times the number of passes — a FRESH-token budget: cache reads are excluded from enforcement accounting by design; expected output ≈ size of the DELIVERABLE only (schema × N items), reasoning excluded. Cache is never budgeted ex ante (noise that improves no decision) — analyzed ex post only.

Then IMMEDIATELY mirror the estimate machine-readably — a PreToolUse gate denies any Agent/Task/Workflow call with no open budget, so opening it is not optional (the same gate also denies NEW Workflow calls when the five-hour quota window is above ~90% — a fan-out that dies mid-flight at the session limit burns tokens with zero deliverable; resume via `resumeFromRunId` always passes):
`<plugin>/scripts/fd-telemetry.py budget-open --task "..." --expected-output N [--expected-input N] [--agents N] [--type slug] [--route inline|workflow|script|agent|external] [--reason "axis2>axis4"] [--alternative "..."] [--effort low|medium|high|xhigh|max] [--verify "cmd/checklist"] [--data-class public|internal|restricted] [--paths "glob,glob"]`
(`--agents` = declared fan-out: the CLI warns (never denies) when the estimate sits below the empirical per-agent anchor — ~20k output and ~17k cold-start input PER AGENT before any file is read (confirmed playbook rule: per-agent overhead dominates the deliverable ~25×). `--type` = task category slug — reuse existing ones, feeds the density table. `--route/--reason/--alternative` = decision record: which route, why, what was discarded — it feeds reversal analysis, costs one line. `--effort` = declared reasoning tier: real lever only through pinned agents `fd-executor`/`fd-verifier`; on mismatch the gate warns and logs `effort_mismatch`, never denies. `--verify` = the delegation contract's verifiable done made machine-readable — absent, the gate warns once per budget. `--data-class restricted` = inputs must not leave the machine: external-exec/cross-verify refuse deterministically. `--paths` = the task's write perimeter, enforced on Write/Edit inside the project by a dedicated hook — out-of-perimeter writes are denied until `budget-amend --add-paths "..." --reason "..."` (explicit, logged; files outside the project — scratchpad, /tmp — are never constrained); user `never_write` patterns in `.fd-perimeter.json` are denied unconditionally.)
`budget-close` captures the actual in/out consumption from the Stop hook's state file: `report` then shows estimate calibration per type/route (median actual/expected) and the script-promotion queue (types recurring ≥2 on model routes) — read both before re-estimating similar tasks.
This writes `~/.claude/fable-director/budgets/<cwd-slug>.json`. The plugin's Stop hook compares actual tokens (since declaration, from the main transcript PLUS the Workflow tool's agent files — those never surface in the main transcript and were invisible until measured at 9.2M fresh input in one session) at every turn end: at ≥2× it warns ONCE (checkpoint: reassess the route now — a reversal at 2× is cheaper than a post-mortem at 3×); at ≥3× it BLOCKS closure and demands the post-mortem. Thresholds on consumed tokens only, never on self-estimated progress.

The estimate is a FALSIFICATION SIGNAL, never a selection constraint — the quality routes above take lexical priority (production code stays on the top model even if it busts the estimate; switching executor to honor a declared budget is Goodhart failure, forbidden).

At task end run `tools/session-cost-report.py` (this skill's dir; reads the budget file automatically, prints cache/delegation metrics and the 3× flags) or `budget.spent()` inside workflows, then close: `fd-telemetry.py budget-close --outcome ok|flagged|abandoned`.
- actual ≥ 3× estimate (either dimension) → the Stop hook already blocked closure and auto-logged the `budget_flag` event; you owe only the mini post-mortem (which assumption broke?) → playbook entry, then `budget-close --outcome flagged`.
- under 3× → no action. LLM estimates are noisy; don't post-mortem noise.

## Orchestration playbook (learning loop)

`~/.claude/delega-playbook.md` (external to the plugin so updates never overwrite it): capped registry of delegation heuristics, one line each, HARD CAP 30.
- Consult before planning batch/workflow/multi-agent orchestration. Not loaded for small inline tasks.
- Write an entry when: rule-of-3 ends at level 3 for approach/tool failure, or pre-budget busts ≥3×. Entry = root cause → heuristic, tagged `[candidata]`.
- `[candidata]` becomes a confirmed rule only on its 2nd independent occurrence — n=1 is overfitting, never generalize a single incident into a ban.
- `[seed]` entries are allowed: proven patterns imported deliberately (not incident-born), exempt from the double-confirmation rule but counted in the cap.
- Each entry carries counters `(uses:N ok:N ko:N)`: increment `uses` when you apply the heuristic, `ok`/`ko` by objective outcome. With small N the counters inform consolidation at cap time (drop the never-used, keep the proven); automatic ranking only when N justifies it — ordering by counters at N≤5 is noise.
- At cap: consolidate/merge/delete before appending (counters decide what dies first). Append-only growth forbidden.

## Script promotion

When model tokens were spent on deterministic work (parsing, extraction, transformation, reporting) and the task has recurred ≥2 times or recurrence is certain: the final step of the task is not closure — it is crystallizing the operation into a script in the target repo's `tools/` (in client repos propose the commit, don't auto-commit) plus one index line in the playbook. That task routes as "Script (zero cost)" from then on. Do NOT promote one-offs or unstable interfaces: script rot is real cost.

Prompt promotion — same logic, for instructions instead of operations: a delegation prompt repeated ≥3 times near-identically becomes a skill or a CLAUDE.md entry. Propose it; the user confirms.

## Telemetry (objective events only)

SQLite DB `~/.claude/fable-director/telemetry.db`; CLI `<plugin>/scripts/fd-telemetry.py`. The SessionEnd hook auto-logs a `session_summary` per session (zero model tokens: token totals, cache/delegation/coordination metrics, cache_resets, first-write turn, tool counts). This auto-path is the reliable data; the model-logged events below are opt-in and only worth the keystroke when you'll actually read the `report` later.

- `budget_flag` (the 3× bust) is logged AUTOMATICALLY by the Stop hook — deterministic, no model action. At the post-mortem you only diagnose the broken assumption + write the playbook entry + `budget-close`; you do NOT log the flag. Those flags come back at you: the SessionStart **hindsight** hook replays this cwd's past busts (task, ratio, real consumption; max 5 lines, silent where there's no history). When today's task resembles one of them, the past ACTUAL beats today's estimate — it is measured, not intuited.
- The auto/model-logged gap is MEASURED, not asserted: over the DB's whole lifetime, model-logged events landed **1 reversal and 0 escalations** against **4 auto-written `budget_flag`s**. What a hook writes is recorded; what the model promises to write is not. Design consequence, beyond telemetry: if a signal matters, make a hook write it — never rely on the model's discipline to log it.
- Log AT EVENT TIME (never reconstructed), `fd-telemetry.py log <kind> --json '{...}'`, one line each: `retry` `{class,tokens_est}` at the retry · `escalation` `{class,resolution}` at level 3 (re-log `resolved:bool` once the outcome is objective — unresolved = the classification was wrong) · `reversal` `{from,to}` at any route change · `verification` `{found:bool,kind}` per verification · `script_promotion` `{script,tokens_pre_promotion}`.
- NEVER log self-assessed quality scores — quality is derived only from objective indicators (tests, rollback, later fix). Visual work has no objective proxy: stays heuristic-routed forever.
- Every derived metric is an ALARM with a threshold, not a target — gaming coordination_cost or verification volume down is Goodhart reentry; cache-thrash is diagnostic, never blocking.
- `fd-telemetry.py report [--days N]` aggregates all of the above. Density is CODIFIED: data may override a routing rule only for task types marked DENSE (N≥10 closed tasks); elsewhere rules stay priors.

## Idempotency cache (opt-in)

Recurring deterministic transform → promote to a script instead (zero cost, first choice). Only when that's impossible (semi-deterministic transform on unchanged input — periodic import, SEO re-run): `cache-get KEY [--model M]` before delegating, on miss run + verify rung-1 + `cache-put KEY --file F --verified [--model M]` (unverified writes refused). `KEY = sha256(schema_version + prompt_template + input)`; the CLI then mixes plugin version and `--model` into the effective key — a plugin upgrade or executor change invalidates stale hits by construction. TTL 90d, cap 500.

## Session boundaries

Near context limit or handing off: write a resume note (state, run ids, journal paths, next command) to a repo file, commit. Persist reusable scripts to the repo, not /tmp.

## Never delegate

Interactive debugging, aesthetics/visual iteration, client-facing numbers/wording, decisions on how to count or report, production writes without prior backup.
