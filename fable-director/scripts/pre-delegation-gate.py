#!/usr/bin/env python3
"""Hook PreToolUse (Agent|Task|Workflow): gate deterministico pre-delega.

Chiude il bootstrap gap: l'enforcement 2×/3× dello Stop hook morde solo se
un budget è stato aperto, ma `budget-open` era un'istruzione a livello di
prompt — il modello poteva saltarla e l'intero stack tornava discrezionale.
Questo hook rende l'apertura del budget NON opzionale: ogni chiamata di
delega senza budget aperto per il cwd viene negata, con nel reason il
comando esatto da eseguire. Il modello apre il budget (un turno) e ritenta:
la spesa è bloccata PRIMA, non contata dopo.

Fail-open by design: qualunque errore interno (stdin malformato, budget
file corrotto, filesystem) → exit 0 silenzioso. Un bug del gate non deve
mai negare una delega legittima; il costo del fail-open è tornare al mondo
pre-gate (solo Stop hook), mai peggio.

Casi budget file:
- status=open, dichiarato <24h  → allow (silenzio, zero token).
- status=open ma >24h           → deny: budget abbandonato, riaprine uno.
  (Stesso orizzonte dello Stop hook: un task di ieri non autorizza oggi.)
- status=flagged                → deny: post-mortem + budget-close dovuti
  PRIMA di nuove deleghe (altrimenti il blocco 3× si aggira delegando).
- assente / closed / stale / corrotto → deny: apri un budget.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    cwd = data.get("cwd") or os.getcwd()
    slug = "-" + str(cwd).strip("/").replace("/", "-").replace(".", "-")
    bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
    telemetry = Path(__file__).with_name("fd-telemetry.py")
    open_cmd = (f'{telemetry} budget-open --task "..." --expected-output N '
                f'[--expected-input N] [--type slug] '
                f'[--route agent|workflow] [--reason "axis..."]')

    budget = None
    if bfile.is_file():
        try:
            budget = json.loads(bfile.read_text())
        except (json.JSONDecodeError, OSError):
            budget = None

    if isinstance(budget, dict) and budget.get("status") == "open":
        declared = parse_ts(budget.get("declared_at"))
        now = datetime.now(timezone.utc)
        if declared and (now - declared).total_seconds() <= 86400:
            return  # budget valido: allow, zero rumore
        deny(
            "FABLE-DIRECTOR gate pre-delega: il budget aperto per questo cwd "
            f"è più vecchio di 24h (task abbandonato: '{budget.get('task')}'). "
            "Chiudilo (`fd-telemetry.py budget-close --outcome abandoned`) e "
            f"apri il pre-budget del task corrente, poi ritenta:\n{open_cmd}"
        )
        return

    if isinstance(budget, dict) and budget.get("status") == "flagged":
        deny(
            "FABLE-DIRECTOR gate pre-delega: il budget di questo cwd è FLAGGED "
            f"(sforamento ≥3× sul task '{budget.get('task')}'). Nuove deleghe "
            "negate finché il post-mortem non è chiuso: (1) diagnosi assunzione "
            "saltata → entry [candidata] nel playbook; (2) `fd-telemetry.py "
            "budget-close --outcome flagged`; (3) apri il nuovo pre-budget e "
            "ritenta."
        )
        return

    deny(
        "FABLE-DIRECTOR gate pre-delega: nessun pre-budget aperto per questo "
        "cwd. Ogni delega/orchestrazione richiede il pre-budget machine-"
        "readable PRIMA della chiamata (skill fable-director:delega-efficiente, "
        "sezione 'Falsifiable pre-budget'). Ancora la stima (input ≈ byte da "
        "leggere ÷ 4 × passaggi; output ≈ solo deliverable), poi esegui e "
        f"ritenta la chiamata:\n{open_cmd}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-open: un bug del gate non nega mai una delega
