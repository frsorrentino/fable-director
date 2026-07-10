#!/usr/bin/env bash
# Benchmark A/B del risparmio token di fable-director.
#   arm "off" = Claude Code senza policy (nessun kernel, nessun hook)
#   arm "on"  = stesso task + STACK DI ENFORCEMENT COMPLETO iniettato via --settings:
#               SessionStart (kernel) + PreToolUse (gate pre-delega) + Stop (check 2×/3×).
#               Misura ciò che il plugin spedisce, non solo la policy sulle decisioni.
# Misura i token dall'output JSON di `claude -p` (.usage + .total_cost_usd).
#
# ATTENZIONE: consuma quota del piano / API reale. RUNS=3 per lato di default.
# Nota: i run "on" scrivono budget file ed eventi telemetria reali
# (cwd = questa dir → slug dedicato); reset_fixtures pulisce il budget tra i run.
# Uso:  RUNS=3 [MODEL=claude-fable-5] [TASKS='tasks/04*.md'] bash run.sh
set -euo pipefail
cd "$(dirname "$0")"

PLUGROOT="$(cd ../fable-director && pwd)"
RUNS="${RUNS:-3}"
OUT="results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

# Settings arm "on": stesso stack di hooks.json del plugin, con path assoluti
# (CLAUDE_PLUGIN_ROOT non esiste fuori dal plugin installato). SessionEnd escluso:
# solo logging, non enforcement — meno rumore nel DB telemetria.
cat > "$OUT/bench-settings.json" <<EOF
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command",
      "command": "CLAUDE_PLUGIN_ROOT='$PLUGROOT' bash '$PLUGROOT/scripts/session-kernel.sh'"}]}],
    "PreToolUse": [{"matcher": "Agent|Task|Workflow", "hooks": [{"type": "command",
      "command": "CLAUDE_PLUGIN_ROOT='$PLUGROOT' python3 '$PLUGROOT/scripts/pre-delegation-gate.py'"}]}],
    "Stop": [{"hooks": [{"type": "command",
      "command": "CLAUDE_PLUGIN_ROOT='$PLUGROOT' python3 '$PLUGROOT/scripts/stop-budget-check.py'"}]}]
  }
}
EOF

# Slug del budget file per QUESTO cwd (stessa logica di cwd_slug() in
# fd-telemetry: base OS-agnostica + hash del path canonico): va pulito tra
# i run, o il budget del run N autorizza/sporca il run N+1.
CWD_CANON=$(pwd | tr '\\' '/')
CWD_BASE=$(printf '%s' "$CWD_CANON" | sed -E 's/[^A-Za-z0-9]+/-/g; s/^-+//; s/-+$//')
CWD_HASH=$(printf '%s' "$CWD_CANON" | sha256sum | cut -c1-8)
BUDGET_FILE="$HOME/.claude/fable-director/budgets/${CWD_BASE}-${CWD_HASH}.json"

model_arg=(); [ -n "${MODEL:-}" ] && model_arg=(--model "$MODEL")

reset_fixtures() {
  python3 gen_fixtures.py >/dev/null
  rm -f fixtures/batch/results.csv fixtures/batch/report.txt fixtures/classify/labels.csv
  rm -f fixtures/reviews/triage.csv fixtures/reviews_xl/triage.csv
  rm -f "$BUDGET_FILE"
}

save_artifacts() { # $1=arm $2=taskfile $3=idx — salva i deliverable PRIMA del prossimo reset
  local base="$OUT/$(basename "$2" .md)__$1__$3"
  [ -f fixtures/reviews/triage.csv ] && cp fixtures/reviews/triage.csv "$base.triage.csv"
  [ -f fixtures/reviews_xl/triage.csv ] && cp fixtures/reviews_xl/triage.csv "$base.triage.csv"
  # Artefatti 01-03: senza, la qualità di quelle shape resta non verificata
  # (review duale 2026-07-10) — aggregate.py li valida contro il ricalcolo.
  [ -f fixtures/batch/results.csv ] && cp fixtures/batch/results.csv "$base.results.csv"
  [ -f fixtures/classify/labels.csv ] && cp fixtures/classify/labels.csv "$base.labels.csv"
  [ -f fixtures/batch/report.txt ] && cp fixtures/batch/report.txt "$base.report.txt"
  return 0
}

run_one() { # $1=arm(off|on) $2=taskfile $3=idx
  reset_fixtures
  local extra=()
  [ "$1" = "on" ] && extra=(--settings "$OUT/bench-settings.json")
  echo "  [$1] $(basename "$2") run $3"
  claude -p "$(cat "$2")" --output-format json \
    --dangerously-skip-permissions "${model_arg[@]}" "${extra[@]}" \
    > "$OUT/$(basename "$2" .md)__$1__$3.json" 2>"$OUT/$(basename "$2" .md)__$1__$3.err" \
    || echo "    (run fallito, vedi .err)"
  save_artifacts "$1" "$2" "$3"
}

# Default: shape 01-04. La 05 (worker-heavy, ~150k token di lettura per run)
# si lancia esplicitamente: TASKS='tasks/05*.md'.
for t in ${TASKS:-tasks/0[1-4]*.md}; do
  for i in $(seq 1 "$RUNS"); do
    run_one off "$t" "$i"
    run_one on  "$t" "$i"
  done
done

echo "== aggregazione =="
python3 aggregate.py "$OUT" | tee "$OUT/summary.txt"
echo "risultati grezzi in $OUT/"
