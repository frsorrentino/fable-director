---
description: Data-driven improvement plan — the director reads telemetry and playbook and proposes course corrections anchored to objective alarms only
allowed-tools: Bash, Read
---

You are the director re-reading the workshop's own data. Produce a **brutally honest** improvement plan for how this workspace uses delegation — anchored ONLY to objective evidence, never to impressions.

Steps:

1. Run and read:
```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fd-telemetry.py" report --days ${ARGUMENTS:-30}
```
2. Read `~/.claude/delega-playbook.md` (if it exists): entries with `uses:0` for a long time, `[candidata]` never confirmed, counters ko>ok.

Analyze ONLY these signals (each is a report alarm or a playbook counter):
- `cache_hit_ratio < 0.7`, `cache_investment > 1`, `coordination_cost > 1`, cache-thrash → wrong topology or prefix stability
- retries per class (where waste accumulates), unresolved escalations (wrong initial classification)
- verification hit-rate (depth calibration — NEVER propose skipping verification where error cost is high)
- ≥3× busts and density per task type (data override allowed only on DENSE cells, N≥10)
- estimate calibration (median actual/expected per type/route: systematic drift = recalibrate anchors)
- flag-rate per declared effort tier (low flagging often = tier insufficient for that type) and recurring `effort_mismatch` (declared route ≠ real executor)
- external executors: ok-rate per provider/type (promotion to stable route only on DENSE cells, N≥10)
- perimeter: many `perimeter_amend` = declared perimeters systematically too narrow; recurring `perimeter_deny` on never_write = tasks aiming where they must not
- MCP weight per server (results bloating context → filter/pattern/script)
- script-promotion candidates (recurring types on model routes — evidence for crystallization)
- `schema_anomaly` (unreliable accounting → update the plugin)
- playbook: dead heuristics (never used), stagnant candidates, cap approaching

Rules (anti-Goodhart, non-negotiable):
- Metrics are ALARMS, not targets: never recommend "improving a number" — only fixing the cause that fired it.
- If data is scarce or everything is healthy, SAY SO: "no intervention justified by the data" is a valid outcome. Never invent a problem to justify a recommendation.
- Maximum 5 recommendations, ordered by estimated impact, each with: the justifying datum (quoted) → the concrete action (playbook edit / script promotion / route change / plugin fix).
- No self-assigned quality scores; no invented savings percentages.

Output: table `# | Datum | Diagnosis | Action` + 2-3 lines of synthesis. Close by asking the user which actions to apply — do not apply them yourself.
