# Fable-director

Efficient-delegation policy for Claude Code: the top model plans, judges and verifies;
execution goes to the cheapest adequate means (script > mid-model > top model).
Like a Renaissance workshop: the master (the director) sketches and refines, the apprentices
execute, the workshop accrues craft.

## Components

| Piece | Role |
|---|---|
| `hooks/hooks.json` + `scripts/session-kernel.sh` | Injects the kernel at every SessionStart (~500 tokens with header and playbook pointer): 6 routing axes + never-delegate + pointer to the full policy |
| `kernel.md` | The kernel text |
| `skills/delega-efficiente/SKILL.md` | Full policy, loaded on-demand: delegation contract, falsifiable pre-budget (3× threshold, anti-Goodhart), rule-of-3 with best-of-3, script promotion, playbook rules, telemetry |
| `skills/delega-efficiente/tools/session-cost-report.py` | Token report per model/main/subagents from JSONL transcripts; cache/delegation metrics (alarms, not targets); reads the budget file on its own and prints ≥3× flags |
| `scripts/fd-telemetry.py` | Machine-readable pre-budget (`budget-open`/`budget-close`, `--type` for the density table), objective event logging to SQLite (`~/.claude/fable-director/telemetry.db`), aggregate `report` (encoded density N≥10, cache-thrash alarm), opt-in idempotent cache (`cache-get`/`cache-put --verified`). Never self-assigned quality scores |
| `scripts/stop-budget-check.py` (Stop hook) | Deterministic 3× enforcement: at each turn end it compares actual tokens against the open budget (lineage attribution: subagent usage sits inside toolUseResult in the main transcript — no mtime, no double counting); on overrun it blocks closing, auto-logs the `budget_flag` event to telemetry (deterministic, zero tokens: the model only owes the post-mortem) and imposes the post-mortem. Anti-loop: stop_hook_active + flagged status; budget >24h → stale |
| SessionEnd hook (`fd-telemetry.py session-summary`) | Logs at session end token totals, cache_hit_ratio/efficiency/investment, delegation_overhead, coordination_cost — zero model tokens |
| `playbook-template.md` | Playbook template: copy it to `~/.claude/delega-playbook.md` on first install (outside the plugin: updates don't touch it) |
| `scripts/statusline-ctx.sh` (optional) | Statusline: `[MODEL]` active, `[CTX %]` conversation context window, `[5H %→HH:MM]` 5-hour plan quota with reset time, `[7D %→"6 Jul"]` weekly quota with reset date (month follows the `LANG` locale), `[BDG r×·effort]` pre-budget live ratio (consumed/expected output, same accounting as the Stop hook, incremental transcript scan) + declared effort tier, degrading to `ok/2×/3×` without transcript, color thresholds 2×/3×; quota color thresholds 60/80; caveman badge if present. Enable it with `/fable-director:statusline` (INSTALL §6) |
| `commands/statusline.md` + `scripts/statusline-install.sh` | The `/fable-director:statusline` command that writes the statusLine to settings.json: idempotent, merge-safe installer that auto-resolves the real absolute path of the installation (GitHub or local directory), doesn't touch third-party statusLines, backs up. `--remove` to uninstall it |
| `agents/fd-executor.md` + `agents/fd-verifier.md` | Shipped agents with pinned reasoning tiers (`effort: low` / `effort: high` in frontmatter — the Agent tool has no per-call effort parameter): axis-4 batch executor under a strict spec contract, and the rung-3 adversarial verifier (read-only, artifact+rubric only) |
| `scripts/cross-verify.py` | Cross-family verifier (ladder rung 4): adversarial check by a different model family (Gemini/DeepSeek/Codex CLI), out of Claude quota, no silent fallback — `unavailable` is never "verified". Config `~/.claude/fable-director/cross-family.json` (`--init`) |
| `scripts/external-exec.py` (experimental) | External batch executor for non-code axis-4 items (extraction, classification, text transform) on free external tiers — zero Claude tokens. Same config and discipline as cross-verify (no silent fallback, greppable output), executor contract in the system prompt (`NEEDS_CONTEXT` → exit 2), built-in JSON rung-1 (`--schema-json`), logs `external_exec` per provider/type — `report` decides if the route gets promoted (DENSE, N≥10) |

## Installation

See `INSTALL.md` in the marketplace folder (one level up). In short:
`claude plugin marketplace add <path>` → `claude plugin install fable-director@pixelfarm --scope user` → init playbook.

## Learning loop

1. Approach/tool failure at the 3rd escalation or pre-budget overrun ≥3× → a `[candidate]` entry in the playbook. The overrun doesn't depend on the model's discipline: the Stop hook detects it and blocks closing until the post-mortem is written.
2. Second independent occurrence → `confirmed`.
3. Deterministic task resorted to ≥2 times → promoted to a script (`tools/` of the right repo) and recorded in the playbook.
4. Confirmed heuristics of general value are promoted into the plugin at the next release → they reach the whole team.

Playbook cap: 30 lines, consolidate before appending. `[seed]` entries = deliberately imported patterns.

## Soft dependencies

`caveman` (cavecrew-*, /caveman-stats) and `superpowers` (systematic-debugging, brainstorming):
without them, the policy degrades gracefully. Recommended for 1:1 behavior.
