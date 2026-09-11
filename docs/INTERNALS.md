# How fable-director works

A **lightweight always-on kernel** (little context each session), a **heavy
on-demand body** (loaded only when the routing axes fire), and **enforcement via
hooks** — deterministic, not bypassable by the model.

## The 6 routing axes

The kernel decides where each task goes, top-down; a higher axis wins.

1. **Interactivity** — live / visual / iterating with the user? → top model
   inline, never delegate.
2. **Cost of error** — production code, client-facing numbers or wording,
   irreversible writes? → top model. When in doubt, it *is* quality-sensitive.
3. **Determinism** — is the core doable by code? → script, zero model tokens.
4. **Cardinality** — N similar items? → a workflow with a grouped mid-tier
   model, forced JSON schema, fan-out 1+(N-1): one canary verified **before**
   the rest.
5. **Verifiability** — is there an objective test? → deterministic assertions;
   if none → adversarial verification per finding.
6. **Cache locality** — every subagent pays a cold start, and switching model
   invalidates the cache. A cost veto on borderline routes, never on axes 1-2.

**Never delegate:** interactive debugging, aesthetics, client-facing numbers or
wording, production writes without a backup.

## The hooks, in Claude Code's lifecycle

- 🟢 **`SessionStart` (kernel)** — injects the 6 axes in ~500 tokens; the full
  policy body loads only on demand.
- 🧠 **`SessionStart` (hindsight)** — replays the budget busts this **cwd** has
  already paid for, auto-recorded by the Stop hook and never self-reported.
  Silent where there's no history (zero tokens), capped at 5 lines where there
  is. Registering without retrieving is an archive, not a memory.
- 🧭 **`UserPromptSubmit` (route hint)** — matches the prompt against
  `hint_keywords` declared per entry in `soft-deps.json` (opt-in) plus
  conservative cardinality signals; on a match injects up to 3
  `[fd-route-hint]` candidates the model must **evaluate** — the entry's
  `quality_guard` / `data_class` stay sovereign — and logs a `route_hint` event
  (names only, never the prompt text). Silent on no match.
- 🛑 **`PreToolUse` (gate)** — intercepts every `Agent` / `Task` / `Workflow`
  call. No machine-readable budget opened first (`budget-open`) → **denied**.
- 🚧 **`PreToolUse` (perimeter)** — the budget can declare *where* the task may
  write (`--paths`); `Write` / `Edit` outside it are **denied** until an explicit
  amendment. Your own `never_write` patterns (`.fd-perimeter.json` — e.g.
  `migrations/*`, `.env*`) are denied unconditionally, budget or not. Same
  config, opt-in `deny_git` key: destructive git commands (`reset --hard`,
  `clean -f`, `branch -D`, …) are **denied** on the `Bash` tool too — the file
  perimeter can't see them. Plain `git push` is deliberately not in the
  recommended set. Matching is token-based (1.35.1): a fragment fires when all
  its tokens appear in a git invocation, regardless of argument order, quoting
  or combined short flags (`git push origin main --force` and `git clean -fd`
  are caught; `gitbook` paths and fragments quoted inside a grep are not).
  `git -C <dir>` also loads the TARGET project's config. Known limit: a `cd`
  persisted in the Bash shell is invisible to the hook. A perimeter config
  that stops parsing warns instead of going silent (throttled per mtime).
- ⚖️ **`PostToolUse` (MCP meter)** — measures context weight along two distinct
  axes: *flow* (bytes each MCP server pushes into context, paid once per call)
  and *stock* (schema bytes a `ToolSearch` load injects into the prefix, re-paid
  **every turn**). The report keeps them separate and never sums them.
- 🪟 **`PostToolUse` (Workflow launch record)** — the delegation gate, in its
  PostToolUse role, persists per `runId` the script text and the `args` a
  Workflow was launched (or resumed) with, under
  `~/.claude/fable-director/workflows/`. The PreToolUse role compares every
  resume against it: args in a different form or a script edited before the
  first cached call means the resume cache is lost — said before the run, in
  agents that will re-run.
- 🔁 **`PostToolUse` (fail-streak)** — counts *consecutive* failing Bash
  commands, recomputed from the transcript each time so no counter can drift
  (resets on the first success; your own denials never count). At every 3rd it
  injects the rule of 3 — diagnose the failure **type** before retrying, blind
  escalation is itself waste — and shows `[FAIL ×N]` on the statusline.
  Advisory: it never blocks.
- 🛰️ **`SubagentStart` / `SubagentStop` (delegation meter)** — counts the
  delegations that actually **started**, nested ones included, which the gate
  never sees: since Claude Code 2.1.219 a subagent can spawn subagents three
  levels deep by default, so an authorised fan-out can multiply under a budget
  declared for one level. It also reads the **real** `effort.level` each
  subagent ran with and compares it to the tier pinned in its frontmatter — a
  mismatch means the pin was ignored, which used to be a silent degradation.
- ✋ **`Stop` (enforcement)** — at each turn end, compares real token usage
  against the declared budget. Warns once at 2×; at 3× **blocks the turn** until
  the post-mortem lands in the playbook.
- 📉 **`SessionEnd` (telemetry)** — logs session totals to SQLite in the
  background. Declared with an explicit `timeout: 30`: a SessionEnd hook with
  no timeout of its own was cancelled at 1.5 s until Claude Code 2.1.268
  (`CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` was ignored for it), and the
  summary of an 88 MB transcript takes ~3 s — in 30 days of local data 4 of 9
  sessions above 20 MB had no summary row. Every closed task leaves a local receipt (estimate vs actual,
  verification contract, perimeter, amendments) under
  `~/.claude/fable-director/receipts/`.

## Enforced, advisory, and what leaves your machine

| Enforced locally | Advisory to the model | Leaves your machine |
|---|---|---|
| The `PreToolUse` gate denies `Agent`/`Task`/`Workflow` delegation with no open machine-readable pre-budget. Every denial reads like a Claude Code 2.1.268 auto-mode denial: `[fable-director rule: <name>]` first, the safer route in the body, and a tail asking the model to finish unrelated work before stopping. The Stop hook warns at 2× and blocks at 3×. `external-exec.py` verifies an open budget itself. The perimeter hook denies `Write`/`Edit` outside the declared `--paths`, and always denies your `never_write` patterns. `--data-class restricted` blocks external routes. | The routing axes, the never-delegate rules, script promotion, the verification ladder and the playbook are policy: they guide decisions but don't mechanically force a route or a quality judgment. | External Gemini/Codex routes are opt-in. When used, the claim, rubric, context, spec and input content you supply are sent to that provider. |

Budget enforcement is local and depends on Claude Code providing a readable
transcript with the expected schema. Telemetry and the playbook stay under
`~/.claude/fable-director/` and `~/.claude/`. An external route that is
unavailable is never treated as verified or executed.

## How it learns from its own mistakes

Every mistake becomes a written lesson, and writing it is not optional.

1. **A blown estimate blocks the session until the lesson is written.** Past 3×
   the declared budget, the Stop hook refuses to close the turn until a one-line
   post-mortem — *which assumption broke?* — lands in the playbook. The overrun
   itself is already logged automatically.
2. **Lessons live in a small playbook with counters.** A rule is born
   `[candidate]` from one incident and is confirmed only on its **second
   independent occurrence** — one bad day never becomes doctrine. Each rule
   carries `uses / ok / ko`, updated by outcome.
3. **Rules earn their place or die.** The playbook is hard-capped at 30 lines;
   at the cap, the counters decide what is merged or deleted.
4. **Data can override policy, but only with evidence.** Telemetry breaks
   outcomes down per task type; measured data may change a routing rule only
   where there are **at least 10 closed tasks** of that type.
5. **Recurring work stops costing.** A task done twice is crystallized into a
   script, with a playbook line pointing at it.

Honest boundary: the *writing* of lessons is hook-enforced; *applying* them at
the next decision is policy the model follows. The playbook lives outside the
plugin (`~/.claude/delega-playbook.md`), so updates never erase what it learned.

## Components

| Piece | Role |
|---|---|
| **Kernel** (`SessionStart`) | Injects the 6 axes + never-delegate each session, ~500 tokens |
| **Hindsight** (`SessionStart`) | Replays this cwd's already-paid budget busts (max 5 lines); silent without history |
| **Skill `delega-efficiente`** | Full policy on demand: delegation contract, falsifiable pre-budget, rule of 3, script promotion, playbook rules |
| **`Stop` hook (budget-check)** | Deterministic 3× enforcement: blocks the turn from closing and imposes the post-mortem |
| **`SessionEnd` hook (telemetry)** | Logs tokens and cache/delegation metrics to SQLite; reaps per-session registries |
| **`SubagentStart`/`SubagentStop`** | Counts delegations as they start — nested included — and measures the effort each subagent really ran with |
| **Playbook** | Learned heuristics that survive updates |
| **`session-cost-report.py`** | Token report from the real JSONL transcripts; friction proxies (tool errors, corrections, edit churn) and cost per turn by session depth, `--since` for a depth-standardized before/after |
| **Statusline + installer** | `/fable-director:statusline`, idempotent and merge-safe |
| **Anonymizer** (`anonymizer/`, phase A) | Pseudonymisation engine and CLI; see the section below |

## Anonymizer: spans, map, corpus (phase A)

The module governs the data that leave the machine; the rest of the plugin
governs the cost. Design in `docs/superpowers/specs/2026-09-10-anonymizer-fase-a-design.md`,
phases in `docs/plans/2026-09-09-anonymizer.md`.

- **Engine** (`anonymizer/engine.py`): three pure functions, `scan`, `redact`,
  `restore`. It knows nothing about hooks, `external-exec.py` or Claude Code;
  adapters call it (today the CLI, in phase B the external routes, in phase E
  the hook adapters and a resident daemon).
- **Spans, not cascaded rewrites**: every engine gets the original text plus
  the spans already taken and returns `(start, end, cat, value, source)`. A
  character mask makes overlap checks O(1). `merge_spans` keeps the longest
  span, ties go to the earlier engine (`columns` → `rules` → `ner` →
  `dictionary`); one replacement pass, right to left. Offsets stay valid, so a
  corpus annotated on the original text scores span against span, and the NER
  of phase C is just one more producer.
- **Placeholders** `[CAT_N]`: N is per category, in reading order, stable
  inside a map: the same entity (e-mail case-insensitive, codes without spaces,
  phone digits with `0039` folded into `+39`, names casefolded) keeps its
  number; a second surface form gets a letter, `[ORG_3B]`, so
  `restore(redact(x)) == x` byte for byte. `restore` tolerates missing
  brackets and lower case, keeps unknown placeholders and counts them.
- **Map file**: `~/.claude/fable-director/anonymizer/maps/<name>.json`, dir
  0700, file 0600, atomic write; `status --purge` deletes maps older than
  `placeholders.map_ttl_days`.
- **Rules** (`rules/base.py`, `rules/it.py`): regex plus validator (CF, P.IVA
  and Luhn check digits, IBAN mod 97, public IP only). Tuned on the corpus:
  phones need context (`tel`, `cell`, `+39`…) or the mobile format; addresses
  need a proper name after the toponym (`via SGR 2` from an ANSI sequence is
  not one); URLs reduce to the host (`DOMINIO`), infrastructure hosts
  whitelisted; `git@github.com:` is not an e-mail; 11 digits after `cod. atto`
  are not a P.IVA.
- **Columns** (`columns.py`): format sniffed from the text (never the file
  name): CSV/TSV with a consistent delimiter over the first lines, or a JSON
  array of objects. Header normalised (accents folded, `Cliente_CRM(match)` →
  `cliente_crm_match`) and looked up in the merged profiles; a whole cell is
  one span, `domini`-style cells split on `|`. Exact offsets per cell; a CSV
  with multi-line quoted cells is left to the rules (`columns_skipped`).
- **Dictionary** (`dictionary.py`): per-project lists; whole entry on word
  boundaries, `PERSONA` also by single token of ≥ 4 letters when the
  occurrence starts with a capital, `ORG` also by stem without the company
  suffix. The whitelist (brands, cities, infra hosts) is excluded from every
  engine.
- **Corpus** (`corpus.py`): `docs/<id>.txt` + `docs/<id>.spans.json`;
  annotations are verified against the text before scoring. A prediction is a
  true positive with the same category and ≥ 50% overlap of the shorter span;
  a second prediction inside an already matched span (name and surname found
  separately) is not an error. Formatted categories carry the thresholds
  (recall ≥ 0.99, precision ≥ 0.90); `PERSONA` and `ORG` are informative
  until phase C. Public corpus: 10 synthetic documents generated by
  `corpus-public/gen.py` (valid check digits, invented people, `.example`
  domains, real export headers). Private corpus: real project documents,
  outside every repository (`corpus.private_dir`), annotated by the model and
  reviewed by sample — only aggregate numbers ever leave it.
- **Measured 2026-09-10** (private corpus, 33 documents: 12 external-route
  specs, 13 real exports, 8 real prose documents): formatted categories
  precision 1.000, recall 0.9975 (two protocol numbers listed after an `e`
  without their own `prot.`); `PERSONA` 326/331 and `ORG` 278/282 thanks to
  the exports and the dictionary, but in prose 0 of 6 person names — the
  phase-C number to beat. Round-trip exact on all 43 documents. Speed ≈ 1
  ms/KB (CLI test caps at 5 ms/KB on 200 KB).

## Token reduction, and why the plugin ships none

Routing cuts **cost per token** — a cheap executor does the heavy work. A
separate lever cuts the **token count** itself, but only where it is *provably
lossless*: trading correctness for tokens is the Goodhart failure the kernel
exists to prevent. Replacing a file read with top-k RAG chunks (−90% tokens)
drops dependent code and is a documented anti-pattern in the playbook; semantic
caching falls under the same ban.

**read-dedup, retired on measurement (1.18.0).** Versions 1.10.5–1.17.1 shipped
an opt-in `PostToolUse` hook that replaced identical re-reads with a diff.
Before promoting it to a default we measured the target on real traffic — 1,278
sessions across two accounts, using the `audit-reads` methodology from
[headroom](https://github.com/headroomlabs-ai/headroom): **identical re-reads
are 0.0–0.1% of Read bytes.** Headroom measured the same figure and removed
their equivalent mechanism too. A lever aimed at 0.1% is maintenance without
payoff, so it's gone. The same audit shows where re-read bytes actually are —
stale reads after edits (26–41%) and `cat -n` line-number scaffolding (4–7.5%) —
both outside what a simple lossless hook can fix.

If you want serious context compression, use a dedicated tool alongside
fable-director; the jobs compose, since they compress and the director governs.
[headroom](https://github.com/headroomlabs-ai/headroom) (Apache-2.0, local
proxy/library) is content-aware and reversible via retrieval, but it does modify
what the model sees — weigh that against axis 2 for quality-sensitive work; its
`wrap claude` forwards your OAuth login, so subscription billing is preserved,
and it disables Claude Code's `/remote-control` on ≥2.1.196.
[Token Optimizer](https://github.com/alexgreensh/token-optimizer) (local hooks,
noncommercial licence) is the other option. fable-director stays
governance-only: measure first, then decide.

## Auto mode and the plugin's walls

Auto mode (default on Pro/Max/Team since 2026-08-14) replaces per-command
approval prompts with a permission classifier. Three facts matter here:

- **The plugin's walls stand.** A PreToolUse hook answering `deny` — the
  pre-budget gate, the write perimeter, `deny_git` — takes precedence over the
  classifier. Auto mode suspends overly-broad user **allow rules**; it does not
  suspend hooks.
- **The classifier judges danger, not cost.** It blocks destructive commands;
  it has no opinion on how many tokens a delegation should burn or which
  executor is the cheap one. Budget enforcement and routing stay entirely on
  this plugin.
- **In bypass-permissions mode the walls are the only defense.** Sessions run
  with `--dangerously-skip-permissions` skip the classifier too: there,
  `never_write`, `deny_git` and the gate are the only deterministic protection
  left — which is precisely the mode where this plugin earns its keep.

## Native belts (Claude Code ≥ 2.1.212)

Claude Code ships deterministic spend caps of its own. They compose with the
plugin: the declared budget governs a TASK (2×/3× on what the model promised),
the native belts cap a SESSION — a parachute, not a policy. Worth turning on:

- `claude --max-budget-usd <n>` — hard dollar ceiling; since 2.1.212 it also
  halts running background subagents at the cap. `budget-open` prints a
  suggested value when you declare your list price in
  `~/.claude/fable-director/pricing.json` (`{"input_usd_per_mtok": 10}`).
- `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (default 20),
  `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` (default 200),
  `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` (default 200) — runaway-loop
  backstops far above any sane fan-out; the gate's pre-budget bites first.
- `workflowSizeGuideline` in settings — declare the same ~10-15-items-per-agent
  grouping axis 4 already prescribes, so dynamic workflows inherit it.
- `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` — holds 1M-window models to 200K via
  auto-compaction. With cost-per-turn growing ~quadratically with context
  (13.8k → 50k eq measured), the ceiling is the single biggest lever for
  sessions that don't genuinely need the million — but tasks that DO benefit
  from long context pay for it in quality: a per-workload choice, not a
  default. Planned expiry (kernel) remains the lossless version of the same
  idea: end at a verified boundary instead of compacting mid-flight.

## Claude Fable 5.1 and Claude Code 2.1.257 — what changed, what didn't (2026-09)

Only the ECONOMIC parts of the plugin are conditional on the model; every
structural rule stays universal. The model is read from the record being
counted (`message.model` in the transcript), never cached at session start —
`/model` can change it mid-session, and only `SessionStart` carries a `model`
field on hook stdin.

- **Cache reads at 0.025×** (vs 0.1× elsewhere): `model-economics.json`
  (shipped) / `~/.claude/fable-director/model-economics.json` (override).
  Consequence, measured on 21 days of traffic: the share of cost that is
  cache_read drops from 52% to 22%, cache WRITES rise to 60% — on 5.1 what
  hurts is losing the cache (resume after >1h idle: 76% of resets; model
  switch: 15%), not re-reading it. Axis 6 and the turn-economy paragraph of
  the kernel say both.
- **Fork vs fresh, re-measured** (n=3 each, CTX ≈150k, CC 2.1.257 keeps the
  cache in a fork): fork turn 3.7-5.4k eq at 0.025× against a 17-37k eq cold
  start — parity at ~7-12 turns on 5.1, ~2 turns on 0.1× models. The fork
  penalty collapsed on 5.1; the policy states it per model.
- **Executor on 5.1 at low effort** (n=3 real items): same eq (11.0k vs
  10.5k), same correctness (3/3 both), 5.5× the USD ($0.110 vs $0.020 per
  item). `fd-executor` stays on sonnet. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`
  (2.1.257) can override every agent's model from the environment: that is
  the user's lever, not the plugin's default.
- **Forced tool choice returns 400 on 5.1** (`tool_choice` any/tool). The
  plugin never sets it: batch schemas travel in the contract's Interfaces
  block and, for workflows, in `agent({schema})`, which the host handles.
  Nothing to change.
- **Implicit tool-call batching**: 5.1 batches less; Claude Code 2.1.257
  already appends "First privately list what you need next…" to tool results
  (verified in-session: string absent from the plugin, present in the
  results). The plugin adds no nudge — duplicating it makes the model answer
  the reminder instead of the user.
- **`experimental.cacheTtl: 1h` on `fd-verifier`**: not adopted. 24
  verifications lifetime give no measurement, and the host documents the 1h
  TTL as ignored on usage-credit plans.
- **Confirmed by Anthropic (2026-09-08, *Reducing cost and improving
  performance with Claude Platform*)**: cache reads at $0.25/MTok on 5.1; a
  mid-conversation effort change breaks the cache except on Opus 5 and Fable
  5.1 (no host hook for effort: advisory in the kernel, `model-switch.py`
  covers model switches only); the prompt-cache TTL is the wall for long tool
  calls and subagents (Claude Code: 1h). `maxEffortLevel` (2.1.267) caps every
  request, pinned agents included — measured 2026-09-09: `medium` cap,
  `fd-verifier` at `medium`, `effort_ignored` logged — a second cause the meter
  names besides old hosts ignoring the frontmatter.

## What Anthropic confirmed (2026-09-08)

Anthropic's own note on cost, [*Reducing cost and improving performance with
Claude Platform*](https://x.com/ClaudeDevs/status/2097369738968195513), lands on the same levers the plugin already
measures. Where each one lives here:

- **Cache reads on Claude Fable 5.1 cost $0.25/MTok** (four times less than
  elsewhere) → `model-economics.json` 0.025×: the number behind the fork-parity
  and turn-economy rules.
- **Changing effort mid-conversation breaks the cache like a model switch**,
  except on Opus 5 and Fable 5.1 → the kernel says to make either change where
  the cache is already lost (after a compaction, at a handoff boundary);
  `model-switch.py` warns on model switches — the host has no hook for effort
  yet.
- **Tool calls and subagents that outlive the cache TTL expire the parent's
  cache** → Claude Code runs a 1-hour TTL; the playbook sizes multi-wave
  workflows to that wall.
- **A stronger model at low effort beats a weaker one at high effort** within
  a family (Fable 5.1 low = Fable 5 high at a third of the cost) → effort is
  pinned by role (`fd-executor` low, `fd-verifier` high), never raised
  everywhere; across families the measured executor choice stands (`sonnet`
  low = 5.1 low in eq, 5.5× the dollars).
- **`/claude-api prompt-audit`**, the audit the note introduces, run on this
  plugin's own kernel, skills and agents (2026-09-09): no verification rituals,
  no scratchpads, no dated thinking settings; two contradictions fixed in
  1.43.1.

### Where it sits next to `/claude-api`

`/claude-api cost-optimize` and `hillclimb` work on application code that
calls the Claude API, measured against an eval; `prompt-audit` cleans prompts
and skills. fable-director governs Claude Code sessions: what a job may cost
before it delegates, who executes it, what gets verified, what the session
actually paid. Complementary, not competing: run `prompt-audit` on your skills
(this plugin did), keep `cost-optimize` for your API code, let fable-director
hold the line inside the session.

Telemetry reads the JSONL transcripts on your machine. For an organization
with an Admin API key, the usage and cost reports can sit next to them for the
org-wide view: on the list, not shipped.

## Known limits

- **Claude Code versions.** The statusline needs ≥ 2.1.x for `context_window`
  and `rate_limits`; older versions omit those segments without an error. Older
  versions may also ignore the `effort` frontmatter on `fd-executor` and
  `fd-verifier`, so those agents inherit the session effort; a `maxEffortLevel`
  cap below the pinned tier has the same effect on any version. Since 1.29.0 that
  degradation is no longer silent where `SubagentStop` exists — the meter logs
  `effort_ignored`. Effort coherence is warn-only by design.
- **Nested delegations.** A subagent spawning subagents runs under the **same**
  per-cwd budget as the first level. The gate flags it and the meter counts it,
  but neither denies it: a depth-3 cascade is your estimate breaking, not the
  gate failing. `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` is the hard backstop.
- **Concurrent sessions.** An open budget is one file per working directory,
  carrying a session lease: `budget-open` refuses to clobber another session's
  fresh budget (`--force` overrides) and the SessionEnd reaper only closes its
  own. Two sessions still can't hold budgets on the same directory — for
  parallel budgeted work, use separate worktrees.
- **Transcript dependency.** Token accounting reads Claude Code's undocumented
  JSONL schema. If at least 20 valid records contain no recognized usage or
  timestamp fields, the sentinel warns, logs `schema_anomaly` and **suspends**
  enforcement rather than silently counting zero.
- **In-flight subagents.** The Stop hook counts subagent usage once it appears
  in the main transcript, so work still in flight can be temporarily
  undercounted.
- **Remote environments.** Managed Agents, cloud routines and remote harnesses
  are outside the local hook stack: the injected policy may still apply, the
  gate, Stop check and telemetry do not.
- **Quiet model fallback.** Claude Code can silently substitute an unavailable
  subagent model. Treat a requested model as declared, and verify the effective
  one afterwards with `session-cost-report.py`.
