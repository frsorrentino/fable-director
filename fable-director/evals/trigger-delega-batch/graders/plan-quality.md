---
type: llm
---

PASS if the answer proposes: a cheap executor for the 40 similar items (a batch or workflow with grouped items, a mid-tier model or a pinned executor agent), a way to verify the output that is not the executor's own claim (a deterministic check, a spot-check or a separate verifier), and a first canary item checked before the rest, or an equivalent way to catch a systemic failure early.
FAIL if it proposes running all 40 items inline on the main model one by one, or launches 40 independent subagents with no verification, or gives no verification step at all.
