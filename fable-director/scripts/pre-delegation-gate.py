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
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows cp1252: senza utf-8 il deny con ≈ → × crasha e il fail-open lo
# ingoia → ogni delega senza budget passerebbe in silenzio (issue #1).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


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


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


DEFAULT_CEILING = 50_000    # token output attesi oltre cui scatta il checkpoint


def cost_checkpoint(budget):
    """Taglio 1+2: task costoso → l'utente decide in base ai propri rate limit.
    Ritorna un reason (→ `ask`) se il pre-budget dichiara un output atteso sopra
    la soglia e l'utente non ha già dato l'ack; None altrimenti (→ allow).

    Soglia: env FD_COST_CEILING o ~/.claude/fable-director/cost-checkpoint.json
    {output_ceiling, weekly_pct_floor}, default 50k. Se lo statusline ha scritto
    la quota (quota.json) e il residuo weekly è sotto il floor, la soglia si
    abbassa (a quota scarsa anche un task medio merita il checkpoint). Il costo
    non è enforceable sull'inline (nessun tool-call da intercettare) — lì è il
    kernel a far porre la scelta; questo copre la DELEGA. Best-effort/fail-open:
    qualunque errore → None (allow), un bug del checkpoint non blocca mai."""
    try:
        if budget.get("cost_ack"):
            return None  # già presentato e approvato
        exp = int(budget.get("expected_output_tokens") or 0)
        base = Path.home() / ".claude" / "fable-director"
        cfg = {}
        cfile = base / "cost-checkpoint.json"
        if cfile.is_file():
            cfg = json.loads(cfile.read_text())
        env_ceil = os.environ.get("FD_COST_CEILING")
        ceiling = int(env_ceil) if env_ceil else int(cfg.get("output_ceiling", DEFAULT_CEILING))
        floor = float(cfg.get("weekly_pct_floor", 25))

        # Quota residua weekly da quota.json (scritto dallo statusline). Assente
        # → checkpoint solo su soglia assoluta (degrada senza, come da Known limits).
        weekly_remaining = None
        qfile = base / "quota.json"
        if qfile.is_file():
            try:
                used = json.loads(qfile.read_text()).get("weekly_used_pct")
                if used is not None:
                    weekly_remaining = 100.0 - float(used)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                weekly_remaining = None

        scarce = weekly_remaining is not None and weekly_remaining < floor
        # A quota scarsa la soglia scende al 30% del ceiling: anche un task medio
        # merita conferma quando resta poco limite.
        eff_ceiling = ceiling * 0.3 if scarce else ceiling
        if exp <= eff_ceiling:
            return None

        def dot(n):  # separatore migliaia "." solo sui numeri, non sul testo
            return f"{n:,.0f}".replace(",", ".")
        q = (f" Quota weekly residua ~{weekly_remaining:.0f}%."
             if weekly_remaining is not None else "")
        return (
            f"FABLE-DIRECTOR checkpoint costo: questa delega dichiara ~{dot(exp)} "
            f"token output attesi (soglia {dot(eff_ceiling)}"
            f"{', abbassata perché quota scarsa' if scarce else ''}).{q} "
            "Decidi tu in base ai tuoi rate limit. Se procedi, il top model "
            "dovrebbe averti già presentato: stima, perché serve questo costo, "
            "alternative (spezzare il task / esecutore economico + verifica / "
            "rimandare al reset). Per non ri-chiedere sullo stesso task, riapri "
            "il budget con --cost-ack dopo l'ok."
        )
    except Exception:
        return None


def log_gate_deny(data, kind, budget=None):
    """Evento telemetria `gate_deny`: senza, l'analisi post-hoc non distingue
    "mai tentata delega" da "delega negata e ripiegata inline" (emerso dal
    benchmark shape 04). Scrittura sqlite diretta (fd-telemetry.py sarebbe
    importabile via importlib, come fa cross-verify.py, ma il gate resta
    autonomo: hot path PreToolUse, zero dipendenze da caricare — lo schema
    events va tenuto allineato a open_db() in fd-telemetry.py).
    Best-effort: un errore qui non deve mai impedire il deny."""
    try:
        ti = data.get("tool_input") or {}
        payload = {
            "kind": kind,  # no_budget | stale_budget | flagged
            "tool": data.get("tool_name"),
            "subagent_type": ti.get("subagent_type"),
            "model": ti.get("model") or "inherit",
        }
        if isinstance(budget, dict):
            payload["task"] = budget.get("task")
            payload["effort"] = budget.get("effort")
        base = Path.home() / ".claude" / "fable-director"
        base.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(base / "telemetry.db")
        con.execute("CREATE TABLE IF NOT EXISTS events("
                    "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, "
                    "cwd TEXT, event TEXT NOT NULL, payload TEXT)")
        con.execute(
            "INSERT INTO events(ts, session_id, cwd, event, payload) "
            "VALUES(?,?,?,?,?)",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             data.get("session_id"), str(data.get("cwd") or os.getcwd()),
             "gate_deny", json.dumps(payload, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception:
        pass


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
    session-cost-report.py (rendiconto per modello effettivo).
    Ritorna la riga (o None): il chiamante stampa UN solo systemMessage."""
    ti = data.get("tool_input") or {}
    model = ti.get("model")
    if not model:
        return None
    target = ti.get("subagent_type") or data.get("tool_name") or "delega"
    return (f"FD ▶ delega a modello esplicito: {target} → {model} "
            f"(dichiarato; effettivo verificabile post-task con "
            f"session-cost-report.py)")


def agent_pinned_effort(subagent_type):
    """Effort pinnato nel frontmatter dell'agent shipped col plugin (agents/).
    Parse a runtime invece di mappa hardcoded: zero drift se il frontmatter
    cambia. Solo agent fd-* del plugin; per tutti gli altri ritorna None
    (l'effort eredita dalla sessione, nessuna coerenza da verificare)."""
    if not subagent_type:
        return None
    name = str(subagent_type).split(":")[-1]
    f = Path(__file__).resolve().parent.parent / "agents" / f"{name}.md"
    if not f.is_file():
        return None
    in_fm = False
    for line in f.read_text(errors="replace").splitlines():
        if line.strip() == "---":
            if in_fm:
                break
            in_fm = True
            continue
        if in_fm and line.startswith("effort:"):
            return line.split(":", 1)[1].strip() or None
    return None


def effort_coherence(data, budget):
    """Coerenza effort dichiarato (budget --effort) vs pinnato (frontmatter
    agent fd-*). Mismatch → warn + evento telemetria `effort_mismatch`,
    MAI deny: l'effort dichiarato è un decision record, non un vincolo di
    selezione (bloccare qui sarebbe il Goodhart del budget). Ritorna la riga
    di warn o None. Best-effort: qualunque errore → None."""
    try:
        declared = (budget or {}).get("effort")
        if not declared:
            return None
        ti = data.get("tool_input") or {}
        pinned = agent_pinned_effort(ti.get("subagent_type"))
        if not pinned or pinned == declared:
            return None
        try:
            payload = {"declared": declared, "pinned": pinned,
                       "subagent_type": ti.get("subagent_type"),
                       "task": (budget or {}).get("task")}
            base = Path.home() / ".claude" / "fable-director"
            base.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(base / "telemetry.db")
            con.execute("CREATE TABLE IF NOT EXISTS events("
                        "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, "
                        "cwd TEXT, event TEXT NOT NULL, payload TEXT)")
            con.execute(
                "INSERT INTO events(ts, session_id, cwd, event, payload) "
                "VALUES(?,?,?,?,?)",
                (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 data.get("session_id"), str(data.get("cwd") or os.getcwd()),
                 "effort_mismatch", json.dumps(payload, ensure_ascii=False)))
            con.commit()
            con.close()
        except Exception:
            pass
        return (f"FD ⚠ effort incoerente: budget dichiara '{declared}' ma "
                f"{ti.get('subagent_type')} ha effort pinnato '{pinned}' "
                f"(frontmatter). Delega consentita — verifica la rotta o "
                f"riapri il budget con l'effort giusto.")
    except Exception:
        return None


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    cwd = data.get("cwd") or os.getcwd()
    # Slug: identico a cwd_slug() in fd-telemetry.py (canonico + hash)
    s = str(cwd).replace("\\", "/")
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
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
        if declared is None:
            # declared_at assente/malformato ≠ budget vecchio: diagnosi onesta
            # (file editato a mano o schema estraneo), non "più vecchio di 24h".
            log_gate_deny(data, "no_budget", budget)
            deny(
                "FABLE-DIRECTOR gate pre-delega: il budget di questo cwd è "
                "privo di declared_at valido (file corrotto o schema estraneo). "
                f"Riapri il pre-budget del task corrente e ritenta:\n{open_cmd}"
            )
            return
        now = datetime.now(timezone.utc)
        if (now - declared).total_seconds() <= 86400:
            # Registro PRIMA del checkpoint: se l'ask viene approvato l'hook non
            # gira di nuovo — senza questo, proprio le deleghe più costose
            # sparirebbero dal [DLG]. Se l'utente nega, sovrastima di 1: stesso
            # tradeoff già accettato in record_delegation (registra alla richiesta).
            record_delegation(data)  # registro per lo statusline [DLG]
            checkpoint = cost_checkpoint(budget)
            if checkpoint:
                ask(checkpoint)  # task costoso: l'utente decide sui suoi limiti
                return
            # UN solo systemMessage: due print JSON separati romperebbero il
            # parsing dell'output hook.
            msgs = [m for m in (announce_model(data),
                                effort_coherence(data, budget)) if m]
            if msgs:
                print(json.dumps({"systemMessage": "\n".join(msgs)},
                                 ensure_ascii=False))
            return  # budget valido: allow
        log_gate_deny(data, "stale_budget", budget)
        deny(
            "FABLE-DIRECTOR gate pre-delega: il budget aperto per questo cwd "
            f"è più vecchio di 24h (task abbandonato: '{budget.get('task')}'). "
            "Chiudilo (`fd-telemetry.py budget-close --outcome abandoned`) e "
            f"apri il pre-budget del task corrente, poi ritenta:\n{open_cmd}"
        )
        return

    if isinstance(budget, dict) and budget.get("status") == "flagged":
        log_gate_deny(data, "flagged", budget)
        deny(
            "FABLE-DIRECTOR gate pre-delega: il budget di questo cwd è FLAGGED "
            f"(sforamento ≥3× sul task '{budget.get('task')}'). Nuove deleghe "
            "negate finché il post-mortem non è chiuso: (1) diagnosi assunzione "
            "saltata → entry [candidata] nel playbook; (2) `fd-telemetry.py "
            "budget-close --outcome flagged`; (3) apri il nuovo pre-budget e "
            "ritenta."
        )
        return

    log_gate_deny(data, "no_budget")
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
