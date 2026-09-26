---
name: verifica-modello
description: Use when a new Claude model arrives or becomes the default (e.g. Claude Opus 5.5), or when asked whether CLAUDE.md, skills, agents or the plugin kernel still fit the current model's guidance — a zero-token check of every configured account (`model-rules/_targets.json`) and our installed plugins against the per-model rules file and the signals of the bundled prompt-audit guide, then the full pass with the native /doctor prompt-audit (Claude Code ≥ 2.1.283; /claude-api prompt-audit before); never edits.
---

# Verifica modello

Two passes, the first at zero model tokens:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/model-rules-check.py" --model <model id> --audit-signals
```

Without `--model` it runs every rules file. Exit 0 = clean, 1 = findings (the signals never change the exit code).

What it reads: each account's `CLAUDE.md`, `skills/**/SKILL.md`, `agents/*.md`, `settings.json`, and kernel/`CLAUDE.md`/skills/agents of our plugins as installed (`model-rules/_targets.json`: accounts and plugin names). What it checks: `model-rules/<model>.json` — `forbid` (a line that matches is a finding, with file:line), `require` (at least one file per account must match), `setting` (the key must be set in settings.json). With `--audit-signals` it also greps the `Signals:` lines of `prompt-audit.md`, the guide of the claude-api skill bundled with Claude Code, and prints the matches as candidates grouped by the guide's row.

The second pass is the guide itself: `/doctor prompt-audit <path>` (native since Claude Code 2.1.283, also `/checkup prompt-audit`) on the files the check named (or on the whole plugin surface at a model release) gives every finding its pattern, the reason it is obsolete for the target model and a confidence, plus the proposed diff; it hands off to the same bundled guide and puts first what the guide alone cannot see: stale paths, stale commands and instruction files that contradict each other. On an older Claude Code, `/claude-api prompt-audit <paths>` is the same pass without that head. The rules file never copies the guide: its `forbid` rules cover only what the guide's signals do not grep (Italian phrasing, visible reasoning), each with an `audit_row` pointing at the row of the guide that explains it; `require` and `setting` rules are what the guide cannot see (a stop section, a task file, a settings key).

Rules:
- Report only. Show the findings to the user with the rule's `why`; any edit to a CLAUDE.md, a skill or a setting is the user's call, file by file.
- A `forbid` match or a signal hit is a candidate, not a verdict: read the line in context before calling it a problem (a phrase quoted as an example of what not to write is fine; `3.7M` is not a model name).
- The installed plugin copy is what the model reads: a finding there that is already fixed in the repository clears at the next release.
- `STATUS: unavailable` on the signals means the bundled guide was not found (older Claude Code): say so and run the rules alone, or pass `--audit-file PATH`.
- A new model = a new `model-rules/<model id>.json` built from its official guide, with the source in `source`. Never edit the script for a model.
