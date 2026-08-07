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
  background. Every closed task leaves a local receipt (estimate vs actual,
  verification contract, perimeter, amendments) under
  `~/.claude/fable-director/receipts/`.

## Enforced, advisory, and what leaves your machine

| Enforced locally | Advisory to the model | Leaves your machine |
|---|---|---|
| The `PreToolUse` gate denies `Agent`/`Task`/`Workflow` delegation with no open machine-readable pre-budget. The Stop hook warns at 2× and blocks at 3×. `external-exec.py` verifies an open budget itself. The perimeter hook denies `Write`/`Edit` outside the declared `--paths`, and always denies your `never_write` patterns. `--data-class restricted` blocks external routes. | The routing axes, the never-delegate rules, script promotion, the verification ladder and the playbook are policy: they guide decisions but don't mechanically force a route or a quality judgment. | External Gemini/Codex routes are opt-in. When used, the claim, rubric, context, spec and input content you supply are sent to that provider. |

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
| **`session-cost-report.py`** | Token report from the real JSONL transcripts |
| **Statusline + installer** | `/fable-director:statusline`, idempotent and merge-safe |

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

## Known limits

- **Claude Code versions.** The statusline needs ≥ 2.1.x for `context_window`
  and `rate_limits`; older versions omit those segments without an error. Older
  versions may also ignore the `effort` frontmatter on `fd-executor` and
  `fd-verifier`, so those agents inherit the session effort. Since 1.29.0 that
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
