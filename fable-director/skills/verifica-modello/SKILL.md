---
name: verifica-modello
description: Use when a new Claude model arrives or becomes the default (e.g. Claude Opus 5.5), or when asked whether CLAUDE.md, skills, agents or the plugin kernel still fit the current model's guidance — runs a read-only check of both accounts (~/.claude, ~/.claude-pixel) and our installed plugins against the per-model rules file, and reports; never edits.
---

# Verifica modello

Deterministic check, zero model tokens until you read the output:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/model-rules-check.py" --model <model id>
```

Without `--model` it runs every rules file. Exit 0 = clean, 1 = findings.

What it reads: each account's `CLAUDE.md`, `skills/**/SKILL.md`, `agents/*.md`, `settings.json`, and kernel/`CLAUDE.md`/skills/agents of our plugins as installed (`model-rules/_targets.json`: accounts and plugin names). What it checks: `model-rules/<model>.json` — `forbid` (a line that matches is a finding, with file:line), `require` (at least one file per account must match), `setting` (the key must be set in settings.json).

Rules:
- Report only. Show the findings to the user with the rule's `why`; any edit to a CLAUDE.md, a skill or a setting is the user's call, file by file.
- A `forbid` match is a candidate, not a verdict: read the line in context before calling it a problem (a phrase quoted as an example of what not to write is fine).
- The installed plugin copy is what the model reads: a finding there that is already fixed in the repository clears at the next release.
- A new model = a new `model-rules/<model id>.json` built from its official guide, with the source in `source`. Never edit the script for a model.
