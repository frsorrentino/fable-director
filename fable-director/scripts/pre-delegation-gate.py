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


def record_delegation(data):
    """Registro deleghe di sessione per il segmento [DLG] dello statusline:
    conteggio per modello DICHIARATO ('inherit' = modello di sessione).
    Registra alla richiesta (pre): se l'utente nega la permission dopo,
    sovrastima di 1 — accettabile per un indicatore live. Best-effort:
    mai bloccare il gate. Il file muore a SessionEnd (reap in telemetria)."""
    try:
        sid = data.get("session_id") or "unknown"
        model = (data.get("tool_input") or {}).get("model") or "inherit"
        d = Path.home() / ".claude" / "fable-director" / "delegations"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.json"
        counts = json.loads(f.read_text()) if f.is_file() else {}
        counts[model] = counts.get(model, 0) + 1
        f.write_text(json.dumps(counts))
    except Exception:
        pass


def announce_model(data):
    """Delega con modello dichiarato ESPLICITO (≠ inherit): rendila visibile
    in sessione. Inherit = stesso modello del main loop → silenzio, così i
    fan-out omogenei non producono N righe di rumore. Mostra il modello
    DICHIARATO: quello effettivo può degradare in silenzio (quiet fallback
    di Claude Code, vedi Known limits) — la verità post-task è
    session-cost-report.py (rendiconto per modello effettivo)."""
    ti = data.get("tool_input") or {}
    model = ti.get("model")
    if not model:
        return
    target = ti.get("subagent_type") or data.get("tool_name") or "delega"
    print(json.dumps({"systemMessage": (
        f"FD ▶ delega a modello esplicito: {target} → {model} "
        f"(dichiarato; effettivo verificabile post-task con "
        f"session-cost-report.py)")}, ensure_ascii=False))


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
            record_delegation(data)  # registro per lo statusline [DLG]
            announce_model(data)     # riga solo se modello esplicito ≠ inherit
            return  # budget valido: allow
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
