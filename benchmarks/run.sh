#!/usr/bin/env bash
# Benchmark A/B del risparmio token di fable-director.
#   arm "off" = Claude Code senza policy (nessun kernel)
#   arm "on"  = stesso task + kernel fable-director iniettato via --append-system-prompt
# Misura i token dall'output JSON di `claude -p` (.usage + .total_cost_usd).
#
# ATTENZIONE: consuma quota del piano / API reale. RUNS=3 per lato di default.
# Uso:  RUNS=3 [MODEL=claude-opus-4-8] bash run.sh
set -euo pipefail
cd "$(dirname "$0")"

KERNEL="../fable-director/kernel.md"
RUNS="${RUNS:-3}"
OUT="results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

model_arg=(); [ -n "${MODEL:-}" ] && model_arg=(--model "$MODEL")

reset_fixtures() {
  python3 gen_fixtures.py >/dev/null
  rm -f fixtures/batch/results.csv fixtures/batch/report.txt fixtures/classify/labels.csv
}

run_one() { # $1=arm(off|on) $2=taskfile $3=idx
  reset_fixtures
  local extra=()
  [ "$1" = "on" ] && extra=(--append-system-prompt "$(cat "$KERNEL")")
  echo "  [$1] $(basename "$2") run $3"
  claude -p "$(cat "$2")" --output-format json \
    --dangerously-skip-permissions "${model_arg[@]}" "${extra[@]}" \
    > "$OUT/$(basename "$2" .md)__$1__$3.json" 2>"$OUT/$(basename "$2" .md)__$1__$3.err" \
    || echo "    (run fallito, vedi .err)"
}

for t in tasks/*.md; do
  for i in $(seq 1 "$RUNS"); do
    run_one off "$t" "$i"
    run_one on  "$t" "$i"
  done
done

echo "== aggregazione =="
python3 aggregate.py "$OUT" | tee "$OUT/summary.txt"
echo "risultati grezzi in $OUT/"
