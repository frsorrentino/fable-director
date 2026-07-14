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

        # Quota residua weekly dal file PER-ACCOUNT scritto dallo statusline
        # (le quote sono del piano attivo: con 2 account un file unico farebbe
        # leggere le soglie dell'account sbagliato). Fallback al legacy
        # quota.json per statusline non ancora aggiornate. Assente →
        # checkpoint solo su soglia assoluta (degrada, come da Known limits).
        weekly_remaining = None
        acct = hashlib.sha256((os.environ.get("CLAUDE_CONFIG_DIR")
                               or str(Path.home() / ".claude")).encode()).hexdigest()[:8]
        qfile = base / f"quota-{acct}.json"
        if not qfile.is_file():
            qfile = base / "quota.json"
        if qfile.is_file():
            try:
                used = json.loads(qfile.read_text()).get("weekly_used_pct")
                if used is not None:
                    weekly_remaining = 100.0 - float(used)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                weekly_remaining = None
        if weekly_remaining is None:
            # Interop claude-hud: chi usa claude-hud come statusline non fa mai
            # girare la nostra (un solo slot statusLine) → il ponte quota resta
            # vuoto. claude-hud però può scrivere lo stesso dato in uno snapshot
            # locale (display.externalUsageWritePath): leggilo come fallback,
            # solo se fresco (<10 min — dato stantio peggio di nessun dato).
            try:
                cfgdir = Path(os.environ.get("CLAUDE_CONFIG_DIR")
                              or (Path.home() / ".claude"))
                hcfg = cfgdir / "plugins" / "claude-hud" / "config.json"
                hp = (json.loads(hcfg.read_text()).get("display", {})
                      .get("externalUsageWritePath")) if hcfg.is_file() else None
                if hp and Path(hp).is_file():
                    snap = json.loads(Path(hp).read_text())
                    from datetime import datetime, timezone
                    t = datetime.fromisoformat(
                        str(snap.get("updated_at", "")).replace("Z", "+00:00"))
                    sd = (snap.get("seven_day") or {}).get("used_percentage")
                    if sd is not None and \
                            (datetime.now(timezone.utc) - t).total_seconds() < 600:
                        weekly_remaining = 100.0 - float(sd)
            except Exception:
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
            f"⚠ FABLE-DIRECTOR cost checkpoint — this delegation declares "
            f"~{dot(exp)} expected output tokens (threshold {dot(eff_ceiling)}"
            f"{', lowered because quota is scarce' if scarce else ''}).{q}\n"
            "Your call, based on your rate limits. Before proceeding the top "
            "model should have shown you: the estimate, why this cost is "
            "needed, and the alternatives (split the task / cheap executor + "
            "verify / defer to reset).\n"
            "To avoid being asked again on this same task: reopen the budget "
            "with --cost-ack after your ok."
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
        con = sqlite3.connect(base / "telemetry.db", timeout=1.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=1000")
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
        # sid entra nel path: allowlist stretta o skip — mai normalizzare
        # (collisioni), mai fidarsi di input esterno nei path (review 2026-07-10)
        sid = str(data.get("session_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
            return
        model = (data.get("tool_input") or {}).get("model") or "inherit"
        d = Path.home() / ".claude" / "fable-director" / "delegations"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.json"
        counts = json.loads(f.read_text()) if f.is_file() else {}
        counts[model] = counts.get(model, 0) + 1
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(counts))
        os.replace(tmp, f)  # atomico: il file è condiviso con lo statusline
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
    return (f"FD ▶ delegating to explicit model: {target} → {model} "
            f"(as declared; verify the effective model post-task with "
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
            con = sqlite3.connect(base / "telemetry.db", timeout=1.0)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=1000")
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
        return (f"FD ⚠ effort mismatch — the budget declares '{declared}' but "
                f"{ti.get('subagent_type')} has effort pinned to '{pinned}' "
                f"(frontmatter). Delegation allowed — double-check the route "
                f"or reopen the budget with the right effort.")
    except Exception:
        return None


def verify_contract(budget, bfile):
    """Contratto qualità: il kernel chiede un done VERIFICABILE prima di
    delegare — --verify lo rende machine-readable. Assente → UNA avvertenza
    per budget (flag verify_warned nel file, scrittura atomica), mai deny:
    il lavoro esplorativo non merita attrito, ma la delega senza evidenza
    di accettazione dichiarata deve almeno costare una riga di coscienza.
    Best-effort: qualunque errore → None."""
    try:
        if not isinstance(budget, dict):
            return None
        if budget.get("verify") or budget.get("verify_warned"):
            return None
        budget["verify_warned"] = True
        tmp = bfile.with_name(f"{bfile.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(budget, ensure_ascii=False))
        os.replace(tmp, bfile)
        return ("FD ⚠ delegation without declared acceptance evidence — the "
                "budget has no --verify (a command that passes / an "
                "enumerable checklist). Delegation allowed — but pin the "
                "verifiable done NOW and declare it at the next budget-open.")
    except Exception:
        return None


def xf_advisory(budget):
    """Advisory rotta esterna, mai deny/ask, max UNA nota al giorno. Due
    trigger in ordine di forza:
    1. tipo CONFERMATO dai dati: il --type del budget ha ok-rate ≥0.9 con
       N≥10 su un provider esterno (stessa soglia DENSE del report) →
       suggerisci quella rotta con i numeri. Proattivo per-task, data-driven,
       zero hardcode.
    2. crediti dormienti: esterni configurati ma zero chiamate oggi (i free
       tier si resettano ogni giorno — il credito non usato è capacita persa).
    Solo su deleghe dove la rotta esterna è plausibile: effort non-high
    (asse 2 e verify restano su Claude), route workflow/agent/external.
    Anti-Goodhart: propone dove la qualità non paga pedaggio, non spinge a
    bruciare crediti. Best-effort: qualunque errore → None."""
    try:
        base = Path.home() / ".claude" / "fable-director"
        if not (base / "cross-family.json").is_file():
            return None
        if (budget or {}).get("effort") in ("high", "xhigh", "max"):
            return None
        route = (budget or {}).get("route")
        if route not in (None, "", "workflow", "agent", "external"):
            return None
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        mark = base / "xf-nudge.json"
        try:
            if json.loads(mark.read_text()).get("date") == today:
                return None
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        used_today = 0
        by_prov = {}  # provider → [n, ok] per il --type del budget
        btype = (budget or {}).get("type")
        con = sqlite3.connect(base / "telemetry.db", timeout=0.5)
        con.execute("PRAGMA busy_timeout=500")
        for ev, ts, pl in con.execute(
                "SELECT event, ts, payload FROM events WHERE event IN "
                "('external_exec','verification')"):
            try:
                p = json.loads(pl or "{}")
            except json.JSONDecodeError:
                continue
            if ev == "verification" and p.get("kind") != "cross-family":
                continue
            if not p.get("provider"):
                continue
            if str(ts) >= today:
                used_today += 1
            if ev == "external_exec" and btype and p.get("type") == btype:
                by_prov.setdefault(p["provider"], [0, 0])
                by_prov[p["provider"]][0] += 1
                if p.get("ok"):
                    by_prov[p["provider"]][1] += 1
        con.close()
        msg = None
        for prov, (n, ok) in sorted(by_prov.items(), key=lambda x: -x[1][0]):
            if n >= 10 and ok / n >= 0.9:
                msg = (f"FD note: type '{btype}' is CONFIRMED on external "
                       f"executor '{prov}' (ok {ok}/{n}, DENSE cell) — "
                       f"advantageous route for non-quality-sensitive items: "
                       f"scripts/external-exec.py --provider {prov} --type "
                       f"{btype}. Off the Claude quota. Advisory only.")
                break
        if msg is None and used_today == 0:
            msg = ("FD note (1×/day): external executors are configured but "
                   "0 calls today — free tiers reset daily. If this "
                   "delegation's items are not quality-sensitive (axis 4, "
                   "never axis 2), consider scripts/external-exec.py. "
                   "Advisory only.")
        if msg is None:
            return None
        tmp = mark.with_name(f"{mark.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"date": today}))
        os.replace(tmp, mark)  # atomico: letto da gate concorrenti
        return msg
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
                "✕ FABLE-DIRECTOR delegation DENIED — this cwd's budget has "
                "no valid declared_at (corrupted file or foreign schema).\n"
                f"Reopen the current task's pre-budget and retry:\n{open_cmd}"
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
                                effort_coherence(data, budget),
                                verify_contract(budget, bfile),
                                xf_advisory(budget)) if m]
            if msgs:
                print(json.dumps({"systemMessage": "\n".join(msgs)},
                                 ensure_ascii=False))
            return  # budget valido: allow
        log_gate_deny(data, "stale_budget", budget)
        deny(
            "✕ FABLE-DIRECTOR delegation DENIED — this cwd's open budget is "
            f"older than 24h (abandoned task: '{budget.get('task')}').\n"
            "Close it (`fd-telemetry.py budget-close --outcome abandoned`), "
            f"open the current task's pre-budget, then retry:\n{open_cmd}"
        )
        return

    if isinstance(budget, dict) and budget.get("status") == "flagged":
        log_gate_deny(data, "flagged", budget)
        deny(
            "✕ FABLE-DIRECTOR delegation DENIED — this cwd's budget is "
            f"FLAGGED (≥3× bust on task '{budget.get('task')}').\n"
            "New delegations stay denied until the post-mortem is closed:\n"
            "(1) diagnose the broken assumption → [candidate] playbook entry\n"
            "(2) fd-telemetry.py budget-close --outcome flagged\n"
            "(3) open the new pre-budget and retry."
        )
        return

    log_gate_deny(data, "no_budget")
    deny(
        "✕ FABLE-DIRECTOR delegation DENIED — no open pre-budget for this "
        "cwd.\n"
        "Every delegation/orchestration requires the machine-readable "
        "pre-budget BEFORE the call (skill fable-director:delega-efficiente, "
        "'Falsifiable pre-budget').\n"
        "Anchor the estimate (input ≈ bytes to read ÷ 4 × passes; output ≈ "
        f"deliverable only), then run and retry the call:\n{open_cmd}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-open: un bug del gate non nega mai una delega
