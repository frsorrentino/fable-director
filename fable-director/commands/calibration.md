---
description: Estimate calibration per task type — median actual/expected output tokens of the closed budgets, the factor budget-open suggests and never applies
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/fd-telemetry.py" calibration:*)
---

Run and report the output AS-IS (it is already formatted — do not summarize, do not recompute, do not round):

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/fd-telemetry.py" calibration ${ARGUMENTS}
```

The user may pass `--days N` (default 90), `--min-n N` (default 5: below it a type shows its median as indicative only, with no factor) or `--type slug` for one row.

The factor is a suggestion for the NEXT estimate of that type. Never change an open budget because of it, and never multiply an estimate by it silently: when you adopt it, say so in the budget-open line.
