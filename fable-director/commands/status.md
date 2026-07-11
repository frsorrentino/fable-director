---
description: fable-director status as in-conversation text (quotas, live budget, burn-rate, external calls) — for clients with no statusline (smartphone/remote)
allowed-tools: Bash
---

Run and report the output AS-IS (it is already formatted, freshness included — do not summarize, do not embellish, do not add commentary):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fd-status.py" ${ARGUMENTS}
```

The user may pass `--detail` for session delegations and the last task receipt.

Only allowed addition: if a line contains an alarm (⚠, ✕, FLAGGED), one sentence of context on what to do.
