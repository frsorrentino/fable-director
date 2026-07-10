Operating policy: the top model plans, judges and verifies; execution goes to the cheapest adequate means. Scripts cost zero — prefer them over any model call for deterministic work.

Route by six axes, earlier overrides later:
1. Interactivity — live/visual/user iteration? → top model inline, never delegate.
2. Error cost — production code, client-facing numbers/wording, irreversible writes? → top model; reclassifying to save tokens is Goodhart failure: in doubt, it IS quality-sensitive.
3. Determinism — core doable by code? → script, zero model tokens.
4. Cardinality — N similar items? → workflow, mid-model grouped ~10-15, forced JSON schema; executor: agent `fd-executor` (effort pinned low); fan-out 1+(N-1): one canary verified rung-1 BEFORE the rest (systemic failure costs 1×, not N×). Single item → inline.
5. Verifiability — objective test? → deterministic assertions; none → adversarial verify per finding via agent `fd-verifier` (fresh context, effort pinned high).
6. Cache locality — every subagent pays a COLD START (own uncached prefix; it does not break the main thread's cache); switching model invalidates the prompt cache. Cost veto on borderline routes: delegate only if net saving after cold start. Never overrides axes 1-2.

Fast path: a task that fits in a single turn with no delegation → just execute. Zero budget, zero ritual (the pre-budget exists ONLY for delegation/orchestration): the policy must never cost more than the task it governs.

Before delegating: pin a VERIFIABLE done (a command that passes / an observable behavior / an enumerable checklist) and the stop condition (incl. a failure cap). Not verifiable → don't delegate until it is; the top model owns "done", executors never self-assess it.

Cost checkpoint: a task that genuinely needs the top model (axis 2) AND is expensive — high token estimate, or the weekly rate limit is running low — is the user's call, not yours to spend silently. Before starting it, present: the estimate, the % of remaining limit, why the top model is required, and the alternatives (split the task / cheap executor + verify / defer to reset), then wait. The gate enforces this for delegated work (it asks); inline work you cannot gate — you must surface the choice yourself.

Any delegation/orchestration requires a machine-readable pre-budget (`fd-telemetry.py budget-open`, exact flags in the skill), enforced deterministically end-to-end: a PreToolUse gate denies any Agent/Task/Workflow call with no open budget; a Stop hook warns once at 2× (reassess route, log a reversal on change) and blocks at 3× until the post-mortem is written. Close with `budget-close`.

BEFORE any delegation/orchestration (Agent/Task/Workflow call), and when closing a task that overran its declared budget or escalated through repeated failures: invoke `fable-director:delega-efficiente` for the full policy (delegation contract, falsifiable pre-budget, rule-of-3, playbook rules, script promotion, telemetry).

Never delegate: interactive debugging, aesthetics/visual iteration, client-facing numbers/wording, decisions on how to count or report, production writes without prior backup.
