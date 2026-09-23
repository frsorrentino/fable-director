---
description: Record an observation on fable-director (a limit, an error, a workaround) in its local box, or read and triage the box — errors of the plugin's scripts are recorded by a hook already; use this for what a hook cannot see (a skill that misled you, a script that "succeeded" with a wrong result, the workaround that worked)
allowed-tools: Bash
---

Errors of fable-director's own scripts are recorded by a hook on their own (claude-observe, `observe/` in the plugin). Use this for what a hook cannot
see: a skill that misled you (delega-efficiente, analisi-video, verifica-modello), a script that "succeeded" with a wrong
result, and above all the workaround that worked and your judgement (D our defect, L someone else's limit, S the site's behaviour).

```
python3 "${CLAUDE_PLUGIN_ROOT}/observe/observe.py" ${ARGUMENTS}
```

Forms (TOOL is `fable-director`): `add TOOL "what happened" [--error "…"] [--workaround "…"] [--class D|L|S]`, `add --on ID --workaround "…"` to
enrich a record the hook wrote, `list [TOOL]`, `show ID`, `mark ID D|L|S|done`, `export TOOL`. Never put secrets,
passwords, tokens or a client's personal data in the text. To send: `report TOOL` prints ONE anonymized issue with all
the unsent observations of that tool (or a comment on an open issue about the same error); show it to the user as it
is, ask for a single yes, and only then run the `report TOOL --send HASH` it prints. Without gh it prints a prefilled
GitHub link for the user to open. What was sent is never offered again.
