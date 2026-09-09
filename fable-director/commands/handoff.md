---
description: Write a ~2k-token handoff of this session to disk (decisions, verified facts, open items, paths, what to invoke next) so a fresh session can continue at a fraction of the cost — only at a verified task boundary; --here writes it into the project's docs/
allowed-tools: Bash, Write, Read
---

A long session pays its whole context again at every turn; a cold resume re-caches all of it (measured: 296k tokens, ~$6, to reopen one session). A handoff on disk plus a fresh session costs a fraction. Do exactly this:

1. Run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" --prepare ${ARGUMENTS}
```

   If it prints `STATUS: refused`, report its DETAIL line to the user as-is and stop: a budget is open, the task is not at a verified boundary, and mid-task the reasoning in this context is load-bearing.

2. Write the file at the `PATH:` it printed, with the seven `SECTIONS:` as H2 headings, in that order. Rules:
   - about 2k tokens at most; facts with numbers, commands and paths; no narrative, no praise, no restating what the code or the git log already shows;
   - section 2 only for decisions someone would otherwise re-litigate; section 4 lists proposals you made that the user did not take up, and open questions;
   - section 6 names the skills, commands or brief files the next session should invoke first;
   - section 7 carries today's date and what would make this handoff stale (a release, a file, a decision).

3. Run the `THEN:` command it printed (`--written PATH`): it logs the event and prints the size.

4. Tell the user in two lines: the path, and that closing this session and opening `claude` in the same folder is enough — the handoff is announced at startup.
