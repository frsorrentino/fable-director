# External free-tier models (Gemini, Codex)

**Already have a Google or a ChatGPT account? It pays to connect them.** Their
free tiers **reset every day** — a day without calls is capacity lost, not
saved.

## Setup, once

```bash
cross-verify.py --init          # writes ~/.claude/fable-director/cross-family.json
```

Add a Gemini key from [AI Studio](https://aistudio.google.com/apikey), and/or
run `codex login`. Check the result with `external-exec.py --doctor`.

## Two roles, both off your Claude quota

**Independent verifier.** An all-Claude ensemble shares correlated blind spots
by construction; a different model family catches what same-family verification
can't. This is rung 4 of the verification ladder and is **rare by design** — the
director escalates to it only for high-stakes claims with no objective test,
never on every task. You can also call it yourself:

```bash
cross-verify.py --claim "..." --rubric "..."
```

**External executor** (experimental). For **non-code batches** — classify,
extract, transform — the bulk runs there while Claude keeps the planning and the
checking. The external model gets a complete spec and must answer in the
required format: malformed output is rejected, never passed downstream, and an
honest `NEEDS_CONTEXT` stops the run instead of guessing. Every call logs
provider, type and outcome, so `report` shows where this route actually works;
it stays per-case until that data is dense.

## The guarantees

**Separate ledgers, always.** External usage is never mixed with your Claude
accounting: the 2×/3× budget counts Claude tokens only.

**Privacy is enforced, not promised.** Open the budget with `--data-class
restricted` and the external routes refuse to run — deterministically, not by
good intentions. When you do use them, what leaves your machine is the claim,
rubric, spec and input you supplied.

**No silent fallback.** Missing key, dead endpoint, spent window →
`STATUS: unavailable`, plus an explicit instruction to fall back to the normal
Claude route. An `unavailable` is never "verified", nor "executed".

**A free tier that closes is not a rate limit.** Since 1.29.0, `401`/`402`/`403`
— and a `429` whose body talks about billing or credit — are reported as an
access/billing refusal, logged as `billing-block`, and never dressed up as a
transient quota error. Free windows do end (Grok's is a time-limited
promotion), and a message saying "retry later" about a door that won't reopen is
worse than no message.

## Running under Claude Code's native sandbox

Subagents inherit the parent's sandbox, so external calls need the provider's
domains in `sandbox.network.allowedDomains` —
`generativelanguage.googleapis.com` for Gemini, `api.x.ai` for Grok — or they
fail as network errors. `sandbox.credentials` with `mode: "mask"` is the native
way to keep those API keys out of unrelated subprocesses.

## Optional paid third lane

Grok (xAI), OpenAI-compatible, active only if you export `XAI_API_KEY` —
≈$0.003 per verification, no free tier as of July 2026. Useful when Gemini 503s
and the Codex window is spent. Providers whose config says anything other than
`billing: "free"` are consent-gated: never proposed by default, and run only
after you say so.
