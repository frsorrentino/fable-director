#!/usr/bin/env python3
"""Hook Stop: enforcement deterministico del pre-budget (2× checkpoint, 3× blocco).

Il learning loop non dipende più dalla disciplina del modello a fine task:
questo hook gira a ogni fine turno, e se esiste un budget file aperto per il
cwd corrente confronta i token effettivi (dal transcript, dopo declared_at)
con la stima dichiarata. Due soglie:
- ≥2× (una volta sola): checkpoint — rivaluta la strategia ORA, un cambio di
  rotta a 2× costa meno del post-mortem a 3×. Solo soglie sul consumato,
  mai proiezioni di avanzamento (autostima del modello = rumore).
- ≥3×: blocca la chiusura del turno e impone il post-mortem.
Costo: zero token; uscita immediata se non c'è budget aperto.

Protezioni anti-loop:
- stop_hook_active nello stdin → mai ribloccare (il blocco è già avvenuto);
- al checkpoint il budget file guadagna warned=true → non si ripete;
- al blocco 3× il budget file passa a status=flagged → i turni successivi passano;
- budget più vecchio di 24h → status=stale, nessun blocco (task abbandonato).

Attribuzione per lineage, non per mtime: il main transcript contiene già
l'usage aggregato di ogni subagent completato (toolUseResult.usage), quindi
basta il main transcript — niente scan di file agent, niente double counting,
niente dipendenza dall'orologio. Subagenti ancora in volo: contati al
completamento (sottoconteggio temporaneo, conservativo).
Record senza timestamp: inferenza posizionale — il JSONL è append-only
cronologico, vale il timestamp dell'ultimo record che lo precede.
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")


def log_budget_flag(payload, cwd):
    """Persist the 3× bust to telemetry deterministically, reusing fd-telemetry's
    log_event so the DB schema stays single-sourced. Best-effort: a telemetry
    failure must NEVER block the hook's primary job (the enforcement itself).
    Without this the objective bust event would depend on the model remembering
    to log it — the exact discipline-gap this plugin exists to close."""
    try:
        spec = importlib.util.spec_from_file_location(
            "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.log_event("budget_flag", payload, cwd=cwd)
    except Exception:
        pass


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def find_usage(obj):
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if isinstance(usage, dict) and any(k in usage for k in USAGE_KEYS):
            yield usage
        for v in obj.values():
            yield from find_usage(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_usage(v)


def sum_file(path, since):
    out = inp = 0
    last_ts = None
    try:
        fh = open(path, errors="replace")
    except OSError:
        return 0, 0
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(rec.get("timestamp")) or last_ts  # inferenza posizionale
            if ts:
                last_ts = ts
            if since is not None and (ts is None or ts < since):
                continue
            for usage in find_usage(rec):
                out += usage.get("output_tokens") or 0
                inp += (usage.get("input_tokens") or 0) + \
                       (usage.get("cache_creation_input_tokens") or 0)
    return out, inp


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if data.get("stop_hook_active"):
        return
    cwd = data.get("cwd") or os.getcwd()
    slug = "-" + str(cwd).strip("/").replace("/", "-").replace(".", "-")
    bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
    if not bfile.is_file():
        return
    try:
        budget = json.loads(bfile.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if budget.get("status") != "open":
        return
    exp_out = int(budget.get("expected_output_tokens") or 0)
    exp_in = int(budget.get("expected_input_tokens") or 0)
    if exp_out <= 0:
        return
    declared = parse_ts(budget.get("declared_at"))
    if declared is None:
        return
    now = datetime.now(timezone.utc)
    if (now - declared).total_seconds() > 86400:
        budget["status"] = "stale"
        bfile.write_text(json.dumps(budget, ensure_ascii=False, indent=1))
        return

    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return
    # Solo main transcript: l'usage dei subagenti completati è già dentro
    # (toolUseResult.usage) e find_usage lo raccoglie ricorsivamente.
    actual_out, actual_in = sum_file(Path(transcript), declared)

    out_bust = actual_out >= 3 * exp_out
    in_bust = exp_in > 0 and actual_in >= 3 * exp_in

    if not (out_bust or in_bust):
        warn = actual_out >= 2 * exp_out or (exp_in > 0 and actual_in >= 2 * exp_in)
        if warn and not budget.get("warned"):
            budget["warned"] = True
            bfile.write_text(json.dumps(budget, ensure_ascii=False, indent=1))
            print(json.dumps({"decision": "block", "reason": (
                f"FABLE-DIRECTOR checkpoint 2×: consumo attuale (output {actual_out}, "
                f"input fresh {actual_in}) ha superato il doppio del pre-budget per il "
                f"task '{budget.get('task')}'. NON è il blocco 3×: rivaluta ORA la "
                f"strategia — cambiare rotta adesso costa meno del post-mortem. Se "
                f"cambi rotta logga: fd-telemetry.py log reversal --json "
                f"'{{\"from\":\"...\",\"to\":\"...\",\"at\":\"2x-checkpoint\"}}'. Se la "
                f"rotta resta valida, prosegui e chiudi il turno normalmente."
            )}))
        return

    budget["status"] = "flagged"
    budget["actual_output_tokens"] = actual_out
    budget["actual_input_tokens"] = actual_in
    budget["flagged_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    bfile.write_text(json.dumps(budget, ensure_ascii=False, indent=1))

    dim = "output" if out_bust else "input"
    actual = actual_out if out_bust else actual_in
    expected = exp_out if out_bust else exp_in
    ratio = round(actual / expected, 1) if expected else None
    # Deterministic capture of the objective bust: the model no longer has to
    # remember to log it (below, step-3 removed). Runs exactly once — the next
    # turn returns early on status != "open".
    log_budget_flag({"task": budget.get("task"), "ratio": ratio, "dim": dim,
                     "actual": actual, "expected": expected, "auto": True}, cwd)

    reason = (
        f"FABLE-DIRECTOR budget enforcement: {dim} effettivo {actual} token ≥ 3× "
        f"la stima dichiarata ({expected}) per il task '{budget.get('task')}'. "
        f"Lo sforamento è GIÀ registrato in telemetria (evento budget_flag, auto). "
        f"Prima di chiudere resta OBBLIGATORIO il mini post-mortem: (1) identifica "
        f"quale assunzione del pre-budget è saltata; (2) scrivi l'entry [candidata] "
        f"nel playbook ~/.claude/delega-playbook.md (root cause → euristica); (3) "
        f"chiudi il budget: fd-telemetry.py budget-close --outcome flagged. Script "
        f"in <plugin fable-director>/scripts/."
    )
    print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
