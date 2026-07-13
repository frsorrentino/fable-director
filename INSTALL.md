# Fable-director — installation

> Operating instructions for Claude Code. If a user points you at this zip or folder and asks
> you to install the plugin, follow these steps in order. Don't skip step 4.

> **Shortcut — auto-updating install.** If the user has network access to GitHub, prefer
> the GitHub route in **[ONBOARDING.md](ONBOARDING.md)**: two commands, and from 1.17.0
> auto-update self-enables at the first session. The steps below are for zip/local-directory
> installs (air-gapped or development), which do NOT self-update.

## 1. Permanent location

The `fable-director-marketplace/` folder must live at a STABLE path: after installation the
plugin is read from here, not copied elsewhere. If you received a zip:

```bash
mkdir -p ~/claude-plugins && unzip <file>.zip -d ~/claude-plugins/
```

Resulting marketplace path: `~/claude-plugins/fable-director-marketplace` (must contain `.claude-plugin/marketplace.json`).

## 2. Register marketplace + install

Try the non-interactive path first (CLI):

```bash
claude plugin marketplace add ~/claude-plugins/fable-director-marketplace
claude plugin install fable-director@pixelfarm --scope user
claude plugin list   # check: fable-director present and enabled
```

If the CLI commands aren't available in the installed version, ask the user to run these two
slash commands in-session (they are user commands, you can't run them yourself):

```
/plugin marketplace add ~/claude-plugins/fable-director-marketplace
/plugin install fable-director@pixelfarm
```

## 3. Initialize the playbook (NEVER overwrite)

```bash
[ -f ~/.claude/delega-playbook.md ] || cp ~/claude-plugins/fable-director-marketplace/fable-director/playbook-template.md ~/.claude/delega-playbook.md
```

If the file already exists, do NOT touch it: it holds heuristics accrued by the user.
Teams with a shared playbook: instead of copying, symlink to the file in the team repo.

## 4. Verify

Start a new Claude Code session, then check:
- at SessionStart the `FABLE-DIRECTOR KERNEL` block appears (~500 tokens, 6 axes);
- the skill is listed as `fable-director:delega-efficiente`;
- `python3 <marketplace>/fable-director/skills/delega-efficiente/tools/session-cost-report.py --help` is not required: the script runs with no arguments from a project's directory.

## 4b. Connect free-tier external models (optional, recommended)

Tell the user clearly: **if they have a Google account or a ChatGPT account, connecting
them pays off** — a free Gemini API key (AI Studio, limits reset daily) and/or the Codex
CLI (usage included in the ChatGPT plan) let fable-director run non-quality-sensitive
batches and cross-family verification **off the Claude quota**. Paid API keys work in the
same config entries if they prefer paid models. Guided setup:

```bash
python3 <marketplace>/fable-director/scripts/external-exec.py --doctor
```

The doctor prints what is missing and the exact command to fix it (`cross-verify.py
--init` creates the config). Skipping this is fine: the plugin shows a one-shot notice at
first session start and works fully without external models.

## 5. Fallback without the plugin system

Only if the plugin system is unusable:
1. `cp -r fable-director/skills/delega-efficiente ~/.claude/skills/`
2. Merge (merge, never overwrite the file) the hook from `fable-director/hooks/hooks.json` into
   `~/.claude/settings.json`, replacing `${CLAUDE_PLUGIN_ROOT}` with the absolute path of the
   `fable-director/` folder.
3. Step 3 (playbook) unchanged.

## 6. Statusline (optional)

Always shows `[MODEL]`, `[CTX %]` (conversation context window), `[5H %→HH:MM]` (5-hour plan
quota with reset time, the "Current session" in /usage), `[7D %→reset]` (weekly quota) and
`[BDG]` (fable-director pre-budget state).

The statusLine is NOT a component the plugin can auto-register (unlike hooks/skills/commands):
it must be written to `settings.json`. To make the step uniform and foolproof on every machine,
the plugin ships an **installer** that writes it for you, resolving the real absolute path of
THIS installation (it self-locates next to the script — works both with a marketplace installed
from GitHub and one added as a local directory).

**Recommended path (anyone, after install or update):**

```
/fable-director:statusline
```

Idempotent: reinstall → updates the path if it changed; if a third-party statusLine already
exists it does NOT touch it (warns). Removal: `/fable-director:statusline --remove`. Automatic
backup to `settings.json.bak`. **You must restart Claude Code** because the statusLine is read at startup.

Equivalent without the slash command (same effect):

```bash
bash "<installLocation>/fable-director/scripts/statusline-install.sh"
```

Only if you prefer editing settings.json by hand (merge, don't overwrite an existing statusLine):
`"statusLine": { "type": "command", "command": "bash \"<installLocation>/fable-director/scripts/statusline-ctx.sh\"" }`.

Requires Claude Code ≥2.1.x (`context_window`/`rate_limits` fields in stdin); on versions without
those fields it degrades silently. The month in the reset date follows the locale (`LANG`).
If the caveman plugin is present, its badge stays in front.

## Soft dependencies

The policy references the `caveman` (cavecrew agents, /caveman-stats) and `superpowers`
(systematic-debugging, brainstorming) plugins. Without them it still works, degrading
gracefully (vanilla Explore instead of cavecrew, no stats hook). Installing them is recommended
for 1:1 behavior.

## What the plugin does, in short

- **SessionStart hook** → injects the kernel (6 routing axes + never-delegate, ~500 tokens).
- **Skill `fable-director:delega-efficiente`** (on-demand) → full policy: delegation contract,
  falsifiable pre-budget with a 3× threshold, rule-of-3 with best-of-3, script promotion, playbook
  rules, telemetry on objective events.
- **Stop hook (`stop-budget-check.py`)** → deterministic 3× enforcement on the open budget
  (`~/.claude/fable-director/budgets/<cwd-slug>.json`, written by `fd-telemetry.py budget-open`):
  on overrun it blocks the turn from closing until the post-mortem is written.
- **SessionEnd hook (`fd-telemetry.py session-summary`)** → logs to SQLite
  (`~/.claude/fable-director/telemetry.db`) token totals and cache/delegation metrics, zero model tokens.
- **`~/.claude/delega-playbook.md`** (external, survives updates) → learned heuristics:
  `[candidate]` → confirmed on the 2nd occurrence; `[seed]` entries; `(uses/ok/ko)` counters;
  cap of 30 with consolidation.
- **`tools/session-cost-report.py`** → real token report from the JSONL transcripts, cache/delegation
  metrics, ≥3× flag (reads the budget file on its own).
- **`scripts/statusline-ctx.sh`** (optional, §6) → statusline with `[MODEL]`, `[CTX %]`, `[5H %→HH:MM]`, `[7D %→reset]`, `[BDG]`.
  Enable it with the **`/fable-director:statusline`** command (or `scripts/statusline-install.sh`): it writes
  the statusLine to settings.json resolving the path on its own, idempotent and merge-safe.
