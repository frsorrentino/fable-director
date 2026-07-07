#!/usr/bin/env python3
"""Rendiconto token di sessioni Claude Code dai transcript JSONL — zero token di modello.

Somma i blocchi `usage` (input/output/cache) per modello e per file transcript,
separando main loop e subagenti. Confronta col pre-budget dichiarato (soglia 3×).

Uso:
  session-cost-report.py [PROJECT_DIR] [--session SESSION_ID] [--budget N_OUTPUT_TOKENS]
                         [--budget-input N_INPUT_TOKENS]

  PROJECT_DIR     dir progetto (default: quella del cwd corrente, cercata in
                  $CLAUDE_CONFIG_DIR/projects, altrimenti ~/.claude/projects)
  --session       filtra i file il cui nome contiene SESSION_ID
  --budget        output token attesi dichiarati nel pre-budget; stampa ratio e flag ≥3×
  --budget-input  input token attesi (task read-heavy: scansioni repo, log, ricerca)

Senza --budget, se esiste un budget file per il cwd
(~/.claude/fable-director/budgets/<slug>.json, status open/flagged) le stime
vengono lette da lì. Oltre ai totali stampa le metriche derivate di cache e
delega (allarmi, non target: ottimizzarle al ribasso è Goodhart reentry).
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")


def find_usage(obj, model_hint=None, in_tool_result=False):
    """Cerca ricorsivamente blocchi usage con i campi token; yield (model, usage).

    Salta gli usage annidati sotto toolUseResult: sono l'aggregato di un
    subagent già contato dal suo file agent-*.jsonl (che porta anche il
    modello) — contarli entrambi raddoppierebbe i subagenti."""
    if isinstance(obj, dict):
        model = obj.get("model", model_hint)
        usage = obj.get("usage")
        if not in_tool_result and isinstance(usage, dict) \
                and any(k in usage for k in USAGE_KEYS):
            yield (model or "unknown", usage)
        for k, v in obj.items():
            yield from find_usage(v, model, in_tool_result or k == "toolUseResult")
    elif isinstance(obj, list):
        for v in obj:
            yield from find_usage(v, model_hint, in_tool_result)


def project_dirs_for_cwd():
    cwd_slug = "-" + str(Path.cwd()).strip("/").replace("/", "-").replace(".", "-")
    dirs = []
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    bases = [Path(cfg) / "projects"] if cfg else []
    bases.append(Path.home() / ".claude" / "projects")
    for base in bases:
        if not base.is_dir():
            continue
        exact = base / cwd_slug
        if exact.is_dir():
            dirs.append(exact)
        else:
            # fallback: match sul nome della dir corrente
            tail = Path.cwd().name.replace(".", "-")
            dirs.extend(p for p in base.iterdir() if p.is_dir() and tail in p.name)
    return dirs


def load_budget_file():
    """Budget file scritto da fd-telemetry.py budget-open per il cwd corrente."""
    slug = "-" + str(Path.cwd()).strip("/").replace("/", "-").replace(".", "-")
    bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
    if not bfile.is_file():
        return None
    try:
        b = json.loads(bfile.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return b if b.get("status") in ("open", "flagged") else None


def main():
    args = sys.argv[1:]
    budget = None
    budget_input = None
    session_filter = None
    dirs = []
    while args:
        a = args.pop(0)
        if a == "--budget":
            budget = int(args.pop(0))
        elif a == "--budget-input":
            budget_input = int(args.pop(0))
        elif a == "--session":
            session_filter = args.pop(0)
        else:
            dirs.append(Path(a))
    budget_task = None
    if budget is None:
        bf = load_budget_file()
        if bf:
            budget = bf.get("expected_output_tokens") or None
            budget_input = budget_input or bf.get("expected_input_tokens") or None
            budget_task = bf.get("task")
    if not dirs:
        dirs = project_dirs_for_cwd()
    if not dirs:
        sys.exit("Nessuna project dir trovata: passala come argomento.")

    per_model = defaultdict(lambda: defaultdict(int))
    per_file = defaultdict(lambda: defaultdict(int))
    bad_lines = 0
    files = []
    for d in dirs:
        files.extend(sorted(d.rglob("*.jsonl")))
    if session_filter:
        files = [f for f in files if session_filter in str(f)]
    if not files:
        sys.exit(f"Nessun transcript .jsonl in: {', '.join(map(str, dirs))}")

    for f in files:
        kind = "subagent" if "agent" in f.name else "main"
        with open(f, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                for model, usage in find_usage(rec):
                    for k in USAGE_KEYS:
                        v = usage.get(k) or 0
                        per_model[model][k] += v
                        per_file[(kind, f.name)][k] += v

    def fmt(n):
        return f"{n:,}".replace(",", ".")

    print(f"# Report token — {len(files)} transcript, "
          f"{bad_lines} righe illeggibili (ignorate)\n")
    print(f"{'modello':<40} {'input':>12} {'output':>12} {'cache_read':>12} {'cache_new':>12}")
    tot = defaultdict(int)
    for model, u in sorted(per_model.items()):
        print(f"{model:<40} {fmt(u['input_tokens']):>12} {fmt(u['output_tokens']):>12} "
              f"{fmt(u['cache_read_input_tokens']):>12} {fmt(u['cache_creation_input_tokens']):>12}")
        for k in USAGE_KEYS:
            tot[k] += u[k]
    print(f"{'TOTALE':<40} {fmt(tot['input_tokens']):>12} {fmt(tot['output_tokens']):>12} "
          f"{fmt(tot['cache_read_input_tokens']):>12} {fmt(tot['cache_creation_input_tokens']):>12}")

    main_out = sum(u["output_tokens"] for (k, _), u in per_file.items() if k == "main")
    sub_out = sum(u["output_tokens"] for (k, _), u in per_file.items() if k == "subagent")
    print(f"\noutput main loop: {fmt(main_out)}  |  output subagenti: {fmt(sub_out)}")

    # Metriche derivate — ALLARMI, non target da ottimizzare.
    inp, out = tot["input_tokens"], tot["output_tokens"]
    cr, cc = tot["cache_read_input_tokens"], tot["cache_creation_input_tokens"]
    total_in = inp + cr + cc
    alarms = []
    print("\n## Metriche derivate (allarmi, non target)")
    if cr + cc:
        hit = cr / (cr + cc)
        eff = cr / total_in if total_in else 0
        inv_s = "∞ (cache mai riletta)" if cr == 0 else f"{cc / cr:.2f}"
        print(f"cache_hit_ratio: {hit:.2f}   cache_efficiency: {eff:.2f}   "
              f"cache_investment: {inv_s}")
        if hit < 0.7:
            alarms.append("cache_hit_ratio < 0.7: prefisso instabile (cambi modello/plugin a metà sessione?)")
        if cr and cc / cr > 1:
            alarms.append("cache_investment > 1: cache scritta più di quanta riletta")
    if sub_out:
        overhead = sub_out / out if out else 0
        coord_s = f"{main_out / sub_out:.2f}"
        print(f"delegation_overhead: {overhead:.2f}   coordination_cost: {coord_s}")
        if main_out / sub_out > 1:
            alarms.append("coordination_cost > 1: orchestratore spende più dei subagenti")
    for a in alarms:
        print(f"⚠ {a}")

    if budget:
        actual = tot["output_tokens"]
        ratio = actual / budget
        flag = "≥3× → POST-MORTEM DOVUTO" if ratio >= 3 else "sotto soglia 3×, ok"
        task_s = f" (task: {budget_task})" if budget_task else ""
        print(f"\npre-budget output{task_s}: {fmt(budget)}  actual: {fmt(actual)}  "
              f"ratio: {ratio:.1f}× — {flag}")
    if budget_input:
        actual_in = tot["input_tokens"] + tot["cache_creation_input_tokens"]
        ratio = actual_in / budget_input
        flag = "≥3× → POST-MORTEM DOVUTO" if ratio >= 3 else "sotto soglia 3×, ok"
        print(f"pre-budget input (fresh=input+cache_creation): {fmt(budget_input)}  "
              f"actual: {fmt(actual_in)}  ratio: {ratio:.1f}× — {flag}")


if __name__ == "__main__":
    main()
