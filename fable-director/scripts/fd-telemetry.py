#!/usr/bin/env python3
"""Telemetria fable-director — solo eventi oggettivi, su SQLite. Zero token di modello.

DB: ~/.claude/fable-director/telemetry.db (tabella events: ts, session_id, cwd, event, payload JSON)
Budget file: ~/.claude/fable-director/budgets/<cwd-slug>.json (letto dall'hook Stop per l'enforcement 3×)

Eventi ammessi: task_open, task_close, budget_flag, retry, escalation, script_promotion,
verification, session_summary, schema_anomaly (auto: sentinella formato transcript —
molti record ma zero usage/timestamp riconosciuti = contabilità inaffidabile, fallire
rumorosamente), gate_deny (auto: scritto dal gate PreToolUse a ogni delega negata —
distingue "mai tentata delega" da "negata e ripiegata inline" nelle analisi post-hoc).
MAI voti di qualità auto-assegnati: la qualità è derivata
solo da indicatori oggettivi (test pass/fail, rollback, fix successivo).

Sottocomandi:
  budget-open  --task S --expected-output N [--expected-input N] [--type SLUG]
               [--approach S] [--fallback S] [--agents N]
               [--route inline|workflow|script|agent|external] [--reason S] [--alternative S]
               [--effort low|medium|high|xhigh|max] [--cost-ack]
               scrive il budget file (status=open) e logga task_open;
               --cost-ack = l'utente ha già approvato il costo di questo task (il
               checkpoint del gate è stato presentato e accettato) → il gate non ri-chiede;
               --type = categoria task per la tabella empirica (es. seo-batch, code-review);
               --route/--reason/--alternative = decision record: quale rotta, perché
               (es. "axis2>axis4"), quale scartata — serve alla telemetria (reversal
               analysis), non al modello;
               --effort = tier di reasoning dichiarato per la delega (applicabile solo
               via agent con effort pinnato in frontmatter: fd-executor=low,
               fd-verifier=high — il tool Agent non ha parametro effort per-call);
               il gate verifica la coerenza dichiarato/pinnato (warn, mai deny);
               --verify "cmd/checklist" = evidenza di accettazione dichiarata
               (il gate avvisa una volta se assente, mai nega);
               --data-class public|internal|restricted = classificazione input:
               restricted BLOCCA external-exec.py e cross-verify.py per il cwd;
               --paths "glob[,glob]" = perimetro scritture del task (enforced
               dal hook perimeter-gate su Write/Edit dentro il progetto);
               --agents N = fan-out previsto: la stima viene confrontata con
               l'ancora empirica per agente (~20k out, ~17k in di cold start —
               playbook confermata) e avvisa se sotto, mai nega
  budget-amend --add-paths "glob[,glob]" [--reason S]
               estende il perimetro del budget aperto (emendamento esplicito,
               loggato come perimeter_amend)
  budget-close [--outcome ok|flagged|abandoned]
               marca il budget file closed e logga task_close; il consuntivo
               (actual in/out) viene catturato dallo state file dello Stop
               hook → alimenta la sezione calibrazione del report
  log EVENT [--json '{...}']
               logga un evento puntuale (retry, escalation, verification, script_promotion, budget_flag)
  session-summary [--transcript P --session-id S --cwd P]
               (hook SessionEnd: legge lo stdin JSON dell'hook) calcola totali token,
               metriche cache/delega e reset di prefisso dal main transcript
               (usage Agent tool dentro toolUseResult: niente double counting) più
               i file agente del Workflow tool (subagents/workflows/ — MAI aggregati
               nel main, verificato 2026-07-20)
  report [--days N]
               aggrega gli eventi: cache metrics, overhead delega, spreco per categoria,
               hit-rate verifiche, densità per tipo task (soglia override: N≥10).
               Le metriche sono ALLARMI, non target.
  cache-get KEY / cache-put KEY (--file F | --output S) --verified
               cache idempotente opt-in per output LLM su input invariati.
               Si scrive SOLO con --verified (output passato da verifica deterministica
               rung-1). KEY = sha256 di schema_version + prompt + contenuto input.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows: stdout/stderr default cp1252 → i nostri messaggi con ≈ → × ▶
# crashano in UnicodeEncodeError e il fail-open li ingoia (issue #1).
# Reconfigure innocuo su POSIX.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path.home() / ".claude" / "fable-director"
DB_PATH = BASE / "telemetry.db"
BUDGETS = BASE / "budgets"
USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")
# Tier di effort ammessi (allineati al frontmatter agent di Claude Code).
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
SENTINEL_MIN_RECORDS = 20  # sotto: transcript troppo corto per giudicare lo schema


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def write_json_atomic(path, obj):
    """Scrittura atomica: tmp nella stessa dir + os.replace. I file di stato
    (budget, delegations) sono condivisi tra hook concorrenti — una write
    parziale letta da un altro processo è JSON corrotto e il gate ci fallisce
    sopra in fail-open = enforcement spento (review duale 2026-07-10).
    Da tenere IDENTICO negli altri writer standalone (gate, stop hook)."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def safe_sid(sid):
    """session_id entra nei path di stato: allowlist stretta o None (skip).
    Un sid con separatori potrebbe uscire dalle dir di stato (finding review
    duale 2026-07-10) — mai normalizzare (collisioni), solo rifiutare."""
    s = str(sid or "")
    return s if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", s) else None


def cwd_slug(cwd):
    """Slug leggibile + hash breve del path CANONICALIZZATO.
    - hash: rompe le collisioni del solo replace (`a.b` vs `a-b` → stesso
      file, review cross-family 2026-07-10);
    - base OS-agnostica via re.sub (issue #1: su Windows backslash e drive
      colon rendevano il filename illegale — `E:\\...` non scriveva MAI);
    - canonicalizzazione `\\`→`/` PRIMA dell'hash: su Windows il gate riceve
      il cwd con `/` e la telemetria con `\\` — senza, i due producono slug
      diversi e il budget di uno è invisibile all'altro.
    Da tenere IDENTICO in: pre-delegation-gate.py, stop-budget-check.py,
    external-exec.py, statusline-ctx.sh, session-cost-report.py
    (load_budget_file) e benchmarks/run.sh."""
    s = str(cwd).replace("\\", "/")
    base = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return f"{base}-{hashlib.sha256(s.encode()).hexdigest()[:8]}"


def open_db():
    BASE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=2.0)
    # WAL: letture (statusline) e scritture (hook) concorrenti senza perdersi
    # eventi in silenzio; busy_timeout evita il fallimento immediato su lock
    # (review duale 2026-07-10: eventi persi = telemetria falsata senza errore).
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=2000")
    con.execute("CREATE TABLE IF NOT EXISTS events("
                "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, "
                "cwd TEXT, event TEXT NOT NULL, payload TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS llm_cache("
                "key TEXT PRIMARY KEY, ts TEXT NOT NULL, output TEXT NOT NULL)")
    # Lo statusline interroga events(event, ts) a ogni render su un DB globale
    # che cresce per sempre: senza indice è un full scan a ogni turno.
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_event_ts "
                "ON events(event, ts)")
    return con


def log_event(event, payload, session_id=None, cwd=None):
    """INSERT con retry+backoff: sotto contesa vera (molti hook concorrenti)
    un singolo busy_timeout scade e l'evento sparirebbe in silenzio —
    trovato dallo stress test 2026-07-11: 113/800 eventi persi con 8 writer.
    Dopo i retry l'errore viene ALZATO: il chiamante best-effort lo ingoia
    dove è giusto ingoiarlo, la CLI lo mostra."""
    import random
    import time
    row = (now_iso(), session_id, str(cwd or os.getcwd()),
           event, json.dumps(payload, ensure_ascii=False))
    last = None
    for attempt in range(6):
        con = None
        try:
            con = open_db()
            con.execute("INSERT INTO events(ts, session_id, cwd, event, "
                        "payload) VALUES(?,?,?,?,?)", row)
            con.commit()
            con.close()
            return
        except sqlite3.OperationalError as e:
            last = e
            try:
                if con is not None:
                    con.close()
            except Exception:
                pass
            time.sleep(0.05 * (2 ** attempt) + random.random() * 0.05)
    raise last


def find_usage(obj, in_subagent=False):
    """Yield (usage, in_subagent): usage annidati sotto toolUseResult sono
    l'aggregato di un subagent completato — il main transcript basta per la
    contabilità completa senza scansionare i file agent (né double counting)."""
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if isinstance(usage, dict) and any(k in usage for k in USAGE_KEYS):
            yield (usage, in_subagent)
        for k, v in obj.items():
            yield from find_usage(v, in_subagent or k == "toolUseResult")
    elif isinstance(obj, list):
        for v in obj:
            yield from find_usage(v, in_subagent)


CACHE_RESET_THRESHOLD = 10_000  # cache_read "alto" prima di un reset sospetto
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}  # prima azione irreversibile


def find_tool_uses(obj, in_subagent=False):
    """Nomi dei tool_use nel main loop (i sottoalberi toolUseResult sono esclusi)."""
    if isinstance(obj, dict):
        if not in_subagent and obj.get("type") == "tool_use" and obj.get("name"):
            yield obj["name"]
        for k, v in obj.items():
            yield from find_tool_uses(v, in_subagent or k == "toolUseResult")
    elif isinstance(obj, list):
        for v in obj:
            yield from find_tool_uses(v, in_subagent)


def sum_transcript(path):
    """Un solo passaggio sul main transcript: totali main/subagent, finestra
    temporale, deleghe completate, reset di prefisso cache (cache_read che
    torna a 0 nel main loop dopo letture alte = prefisso invalidato a metà
    sessione), prima azione irreversibile (turno/token: sessioni senza write
    restano None — analisi richiesta non è esitazione), statistiche tool."""
    main = dict.fromkeys(USAGE_KEYS, 0)
    sub = dict.fromkeys(USAGE_KEYS, 0)
    n_sub = 0
    n_records = n_usage_recs = 0
    cache_resets = 0
    had_high_read = False
    first_ts = last_ts = None
    turns = 0
    first_write_turn = None
    tokens_before_first_write = None
    tool_counts = {}
    last_tool = None
    run_len = 0
    max_run = (None, 0)
    try:
        fh = open(path, errors="replace")
    except OSError:
        return main, sub, n_sub, cache_resets, first_ts, last_ts, {}
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_records += 1
            ts = parse_ts(rec.get("timestamp"))
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            for name in find_tool_uses(rec):
                tool_counts[name] = tool_counts.get(name, 0) + 1
                run_len = run_len + 1 if name == last_tool else 1
                last_tool = name
                if run_len > max_run[1]:
                    max_run = (name, run_len)
                if first_write_turn is None and name in WRITE_TOOLS:
                    first_write_turn = turns
                    tokens_before_first_write = main["output_tokens"]
            rec_had_usage = False
            for usage, in_sub in find_usage(rec):
                rec_had_usage = True
                bucket = sub if in_sub else main
                for k in USAGE_KEYS:
                    bucket[k] += usage.get(k) or 0
                if in_sub:
                    n_sub += 1
                else:
                    turns += 1
                    cr = usage.get("cache_read_input_tokens") or 0
                    if cr == 0 and had_high_read:
                        cache_resets += 1
                        had_high_read = False
                    elif cr > CACHE_RESET_THRESHOLD:
                        had_high_read = True
            if rec_had_usage:
                n_usage_recs += 1
    stats = {
        "n_records": n_records,
        "n_usage_records": n_usage_recs,
        "first_write_turn": first_write_turn,
        "tokens_before_first_write": tokens_before_first_write,
        "tool_counts": tool_counts,
        "max_tool_run": {"tool": max_run[0], "len": max_run[1]} if max_run[0] else None,
    }
    return main, sub, n_sub, cache_resets, first_ts, last_ts, stats


def sum_workflow_agents(path):
    """Token degli agenti del Workflow tool: vivono in file propri sotto
    <sessiondir>/subagents/workflows/wf_*/agent-*.jsonl e NON compaiono mai
    in toolUseResult del main transcript (verificato 2026-07-20 su sessione
    reale: 5 call Workflow, zero usage annidati — l'aggregato nel main vale
    solo per l'Agent tool). Senza questo scan delegation_overhead e
    coordination_cost leggono zero proprio sulla route di delega più costosa
    (misurato: 9,2M input freschi invisibili in una sessione).
    Ritorna (totali usage, n file agente)."""
    tot = dict.fromkeys(USAGE_KEYS, 0)
    n_files = 0
    sdir = Path(path).with_suffix("")  # <sessiondir> = transcript senza .jsonl
    try:
        agent_files = list(sdir.glob("subagents/workflows/wf_*/agent-*.jsonl"))
    except OSError:
        agent_files = []
    for f in agent_files:
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        n_files += 1
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for usage, _ in find_usage(rec):
                    for k in USAGE_KEYS:
                        tot[k] += usage.get(k) or 0
    return tot, n_files


def derived_metrics(inp, out, cr, cc, main_out, sub_out, n_sub):
    """Metriche derivate; None dove il denominatore è zero."""
    total_in = inp + cr + cc
    return {
        "cache_hit_ratio": cr / (cr + cc) if (cr + cc) else None,
        "cache_efficiency": cr / total_in if total_in else None,
        "cache_investment": cc / cr if cr else None,   # None = cache mai riletta
        "delegation_overhead": sub_out / out if out else None,
        "coordination_cost": main_out / sub_out if sub_out else None,
        "n_subagent_files": n_sub,
    }


# ---------- sottocomandi ----------

def cmd_budget_open(args):
    cost_ack = "--cost-ack" in args
    if cost_ack:
        args = [a for a in args if a != "--cost-ack"]
    force = "--force" in args
    if force:
        args = [a for a in args if a != "--force"]
    opts = parse_opts(args, {"--task": None, "--expected-output": None,
                             "--expected-input": None, "--type": None,
                             "--approach": None, "--fallback": None, "--cwd": None,
                             "--route": None, "--reason": None, "--alternative": None,
                             "--effort": None, "--verify": None,
                             "--data-class": None, "--paths": None,
                             "--agents": None})
    if opts["--data-class"] and opts["--data-class"] not in (
            "public", "internal", "restricted"):
        sys.exit("invalid --data-class (allowed: public, internal, restricted)")
    if not opts["--task"] or not opts["--expected-output"]:
        sys.exit("budget-open requires --task and --expected-output")
    if opts["--effort"] and opts["--effort"] not in EFFORT_LEVELS:
        sys.exit(f"invalid --effort: {opts['--effort']} "
                 f"(allowed: {', '.join(sorted(EFFORT_LEVELS))})")
    # Stime non positive = enforcement 2×/3× morto by construction (lo Stop
    # hook esce su exp_out <= 0): un bypass, non una stima. Rifiuta rumorosamente.
    try:
        exp_out = int(opts["--expected-output"])
        exp_in = int(opts["--expected-input"] or 0)
        n_agents = int(opts["--agents"] or 0)
    except ValueError:
        sys.exit("--expected-output/--expected-input/--agents must be integers")
    if exp_out <= 0:
        sys.exit("--expected-output must be > 0: a non-positive estimate "
                 "disables the 2×/3× enforcement (a bypass, not an estimate)")
    if exp_in < 0:
        sys.exit("--expected-input cannot be negative")
    if n_agents < 0:
        sys.exit("--agents cannot be negative")
    # Ancora empirica fan-out (playbook confermata, 2 incidenti: l'overhead
    # per agente domina il deliverable ~25×): ~20k output e ~17k input di
    # cold start PER AGENTE, prima ancora dei file letti. Stima sotto
    # l'ancora → warn, mai deny: la stima è un segnale di falsificazione,
    # non un vincolo di selezione.
    if n_agents:
        floor_out, floor_in = n_agents * 20_000, n_agents * 17_000
        low = []
        if exp_out < floor_out:
            low.append(f"output {exp_out} < {floor_out} (= {n_agents} × ~20k "
                       f"reasoning+tool-call per agente)")
        if exp_in and exp_in < floor_in:
            low.append(f"input {exp_in} < {floor_in} (= {n_agents} × ~17k "
                       f"prefisso cold-start, esclusi i file)")
        if low:
            print("FD ⚠ estimate below the fan-out anchor — "
                  + "; ".join(low)
                  + " — raise the estimate or shrink the fan-out "
                    "(group items ~10-15 per agent).")
    cwd = opts["--cwd"] or os.getcwd()
    BUDGETS.mkdir(parents=True, exist_ok=True)
    # Lease: owner = sessione che apre (CLAUDE_CODE_SESSION_ID nell'env Bash).
    # Un budget OPEN fresco di un'ALTRA sessione non si calpesta in silenzio:
    # sovrascriverlo distruggerebbe il suo enforcement (warned/flagged persi).
    # --force per il caso deliberato. Assente owner/env → comportamento legacy.
    owner = safe_sid(os.environ.get("CLAUDE_CODE_SESSION_ID"))
    bfile_pre = BUDGETS / f"{cwd_slug(cwd)}.json"
    if bfile_pre.is_file() and not force:
        try:
            prev = json.loads(bfile_pre.read_text())
            prev_owner = prev.get("owner_sid")
            declared_prev = parse_ts(prev.get("declared_at"))
            fresh = (declared_prev is not None and
                     (datetime.now(timezone.utc) - declared_prev).total_seconds() < 86400)
            if prev.get("status") == "open" and fresh:
                if prev_owner and owner and prev_owner != owner:
                    sys.exit(f"budget-open refused: another session ({prev_owner[:8]}…, "
                             f"task '{prev.get('task')}') has an OPEN budget for "
                             f"this cwd. Overwriting it would destroy its "
                             f"enforcement. Use --force only if you know that "
                             f"session is dead, or work from a separate "
                             f"cwd/worktree.")
                # Stessa sessione (o owner assente): sovrascrivere in silenzio
                # azzera declared_at (la baseline dell'enforcement riparte) e
                # perde warned/decision record del budget precedente. La
                # correzione di stima è legittima ma passa da un close
                # esplicito, che resta nel decision record.
                sys.exit(f"budget-open refused: a budget is already OPEN for "
                         f"this cwd (task '{prev.get('task')}'). Re-opening "
                         f"would reset the enforcement baseline and drop the "
                         f"previous decision record. Close it first "
                         f"(budget-close --outcome abandoned), then re-open "
                         f"with the revised estimate — or use --force "
                         f"deliberately.")
        except (json.JSONDecodeError, OSError):
            pass  # file corrotto: la nuova open lo rimpiazza (atomicamente)
    budget = {
        "task": opts["--task"],
        "type": opts["--type"],
        "approach": opts["--approach"],
        "fallback": opts["--fallback"],
        "route": opts["--route"],
        "reason": opts["--reason"],
        "alternative": opts["--alternative"],
        # effort dichiarato: leva reale solo se la delega usa un agent con
        # effort pinnato (fd-executor/fd-verifier); il gate confronta i due.
        "effort": opts["--effort"],
        # verify: evidenza di accettazione dichiarata (comando/checklist) —
        # il "done verificabile" del kernel reso machine-readable; il gate
        # avvisa (mai nega) se assente. data_class: restricted BLOCCA le
        # rotte esterne (external-exec/cross-verify) deterministicamente.
        "verify": opts["--verify"],
        "data_class": opts["--data-class"],
        # paths: perimetro scritture dichiarato (glob fnmatch, virgole) —
        # enforced dal hook perimeter-gate su Write/Edit dentro il progetto;
        # si estende solo con budget-amend (emendamento esplicito, loggato).
        "paths": [p.strip() for p in (opts["--paths"] or "").split(",")
                  if p.strip()] or None,
        "expected_output_tokens": exp_out,
        "expected_input_tokens": exp_in,
        # agents: fan-out dichiarato — decision record e denominatore per la
        # calibrazione per-agente del report (assente = non dichiarato).
        "agents": n_agents or None,
        # cost_ack: l'utente ha già approvato un task sopra la soglia di costo
        # (checkpoint del gate presentato e accettato) → il gate non ri-chiede.
        "cost_ack": cost_ack,
        # owner_sid: lease di sessione — il reaper chiude i budget PROPRI
        # subito e quelli altrui mai (salvo orfani >24h); budget-open non
        # calpesta budget open altrui freschi.
        "owner_sid": owner,
        "declared_at": now_iso(),
        "cwd": str(cwd),
        "status": "open",
    }
    bfile = BUDGETS / f"{cwd_slug(cwd)}.json"
    write_json_atomic(bfile, budget)
    log_event("task_open", budget, cwd=cwd)
    print(f"budget open: {bfile}")


def cmd_budget_amend(args):
    """Emendamento ESPLICITO del perimetro del budget aperto: il deny del
    perimeter-gate non si aggira, si emenda — e l'emendamento resta nel
    decision record (quante volte il lavoro reale sfonda il perimetro
    dichiarato è un dato di calibrazione, come le stime)."""
    opts = parse_opts(args, {"--add-paths": None, "--reason": None,
                             "--cwd": None})
    if not opts["--add-paths"]:
        sys.exit("budget-amend requires --add-paths \"glob[,glob]\"")
    cwd = opts["--cwd"] or os.getcwd()
    bfile = BUDGETS / f"{cwd_slug(cwd)}.json"
    if not bfile.is_file():
        sys.exit(f"nessun budget file: {bfile}")
    budget = json.loads(bfile.read_text())
    if budget.get("status") != "open":
        sys.exit("budget-amend requires an OPEN budget")
    new = [p.strip() for p in opts["--add-paths"].split(",") if p.strip()]
    # Ri-lettura FRESCA immediatamente prima della write: uno Stop hook
    # concorrente può aver flaggato il budget tra la nostra read e qui —
    # sovrascrivere riaprirebbe un budget bloccato (review esterna 2026-07-11).
    # La finestra residua è µs; la mutazione avviene sull'oggetto fresco.
    budget = json.loads(bfile.read_text())
    if budget.get("status") != "open":
        sys.exit(f"budget-amend aborted: budget status changed to "
                 f"'{budget.get('status')}' meanwhile — resolve that first")
    paths = budget.get("paths") or []
    budget["paths"] = paths + [p for p in new if p not in paths]
    budget.setdefault("amendments", []).append(
        {"added": new, "reason": opts["--reason"], "at": now_iso()})
    write_json_atomic(bfile, budget)
    log_event("perimeter_amend", {"added": new, "reason": opts["--reason"],
                                  "task": budget.get("task")}, cwd=cwd)
    print(f"perimeter amended: +{', '.join(new)}")


def cmd_budget_close(args):
    opts = parse_opts(args, {"--outcome": "ok", "--cwd": None,
                             "--actual-output": None})
    cwd = opts["--cwd"] or os.getcwd()
    bfile = BUDGETS / f"{cwd_slug(cwd)}.json"
    if not bfile.is_file():
        sys.exit(f"nessun budget file: {bfile}")
    budget = json.loads(bfile.read_text())
    budget["status"] = "closed"
    budget["outcome"] = opts["--outcome"]
    budget["closed_at"] = now_iso()
    if opts["--actual-output"]:
        budget["actual_output_tokens"] = int(opts["--actual-output"])
    # Consuntivo dallo state file dello Stop hook (stessa contabilità
    # dell'enforcement): senza questo, ogni chiusura butta via l'actual e la
    # calibrazione delle stime resta impossibile. Esplicito vince su misurato.
    sfile = bfile.with_name(bfile.stem + ".state.json")
    if sfile.is_file():
        try:
            st = json.loads(sfile.read_text())
            if st.get("declared") == budget.get("declared_at"):
                # out/inp = main transcript; wf_out/wf_inp = agenti Workflow
                # (stessa contabilità dell'enforcement Stop, che li somma).
                budget.setdefault("actual_output_tokens",
                                  int(st.get("out") or 0) + int(st.get("wf_out") or 0))
                budget.setdefault("actual_input_tokens",
                                  int(st.get("inp") or 0) + int(st.get("wf_inp") or 0))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    write_json_atomic(bfile, budget)
    # Lo state file dello Stop incrementale è chiavato su declared_at: a
    # budget chiuso è morto — rimuoverlo evita accumulo, non serve migrarlo.
    try:
        sfile.unlink()
    except OSError:
        pass
    log_event("task_close", budget, cwd=cwd)
    # Ricevuta locale (provenance): snapshot machine-readable del task chiuso
    # — stima vs consuntivo, contratto verify, perimetro, emendamenti, esito.
    # Zero token modello: la scrive questo script, nessuno la rilegge se non
    # per audit. Cap 200 file (le più vecchie muoiono).
    try:
        rdir = BUDGETS.parent / "receipts"
        rdir.mkdir(parents=True, exist_ok=True)
        receipt = {k: budget.get(k) for k in (
            "task", "type", "route", "effort", "expected_output_tokens",
            "expected_input_tokens", "actual_output_tokens",
            "actual_input_tokens", "outcome", "verify", "data_class",
            "paths", "amendments", "declared_at", "closed_at", "owner_sid")}
        receipt["cwd"] = cwd
        try:
            receipt["plugin_version"] = json.loads(
                (Path(__file__).parent.parent / ".claude-plugin"
                 / "plugin.json").read_text()).get("version")
        except (json.JSONDecodeError, OSError):
            pass
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (rdir / f"{cwd_slug(cwd)}-{stamp}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=1))
        old = sorted(rdir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for p in old[:-200]:
            p.unlink()
    except Exception:
        pass
    print(f"budget closed ({opts['--outcome']}): {budget.get('task')}")


ALLOWED_EVENTS = {"task_open", "task_close", "budget_flag", "retry", "escalation",
                  "script_promotion", "verification", "session_summary", "reversal",
                  "schema_anomaly", "external_exec"}


def cmd_log(args):
    if not args:
        sys.exit(f"log requires EVENT among: {', '.join(sorted(ALLOWED_EVENTS))}")
    event = args.pop(0)
    if event not in ALLOWED_EVENTS:
        sys.exit(f"event not allowed: {event} (no subjective metrics)")
    opts = parse_opts(args, {"--json": "{}", "--session-id": None, "--cwd": None})
    try:
        payload = json.loads(opts["--json"])
    except json.JSONDecodeError as e:
        sys.exit(f"invalid --json: {e}")
    log_event(event, payload, session_id=opts["--session-id"], cwd=opts["--cwd"])
    print(f"logged: {event}")


REAP_MIN_AGE_S = 6 * 3600     # legacy senza owner: sotto, non toccare
REAP_FOREIGN_AGE_S = 24 * 3600  # budget di ALTRA sessione: orfano solo oltre


def reap_open_budget(cwd, session_id=None):
    """SessionEnd: un budget ancora 'open' che il modello non ha chiuso è orfano.
    Lo chiudo come abandoned così un Stop hook di una sessione futura non agisce
    su un budget morto e il report non resta falsato da un task svanito in
    silenzio. Tocco SOLO status=open — flagged/closed/stale restano intatti.
    Semantica di LEASE (owner_sid scritto da budget-open):
    - budget MIO (owner == sessione che finisce) → chiudo subito, a qualunque
      età: la sessione muore, il suo budget con lei;
    - budget di un'ALTRA sessione → solo se più vecchio di 24h (orfano di
      sessione crashata): mai spegnere l'enforcement di una concorrente viva;
    - senza owner (legacy/env assente) → orizzonte prudente 6h.
    Best-effort: non deve mai far fallire la session-summary."""
    if not cwd:
        return
    bfile = BUDGETS / f"{cwd_slug(cwd)}.json"
    if not bfile.is_file():
        return
    try:
        budget = json.loads(bfile.read_text())
        if budget.get("status") != "open":
            return
        declared = parse_ts(budget.get("declared_at"))
        age = (datetime.now(timezone.utc) - declared).total_seconds() if declared else None
        owner = budget.get("owner_sid")
        sid = safe_sid(session_id)
        if owner and sid and owner == sid:
            pass  # mio: chiudi subito
        elif owner:
            if age is None or age < REAP_FOREIGN_AGE_S:
                return  # di un'altra sessione, non orfano: non toccare
        elif age is not None and age < REAP_MIN_AGE_S:
            return  # legacy senza owner: orizzonte prudente
        budget["status"] = "closed"
        budget["outcome"] = "abandoned"
        budget["closed_at"] = now_iso()
        write_json_atomic(bfile, budget)
        log_event("task_close", budget, cwd=cwd)
    except (json.JSONDecodeError, OSError):
        return


def reap_delegations(session_id):
    """SessionEnd: il registro deleghe serve solo allo statusline live —
    rimuovi quello della sessione + orfani >48h (sessioni crashate).
    Best-effort, mai bloccante."""
    try:
        d = BASE / "delegations"
        if not d.is_dir():
            return
        session_id = safe_sid(session_id)  # sid nei path: allowlist o skip
        if session_id:
            for suffix in (".json", ".tok.json"):
                f = d / f"{session_id}{suffix}"
                if f.is_file():
                    f.unlink()
        cutoff = datetime.now(timezone.utc).timestamp() - 172800
        for f in d.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
    except OSError:
        return


def git_yield(cwd, first_ts):
    """Yield analysis (idea da CodeBurn): quanti commit + righe nette ha
    prodotto la sessione. Denominatore di RESA che manca: misuriamo il costo,
    non se la spesa ha prodotto lavoro tenuto. ALLARME diagnostico, MAI target:
    planning/debug legittimamente non committano — un token/commit alto NON
    condanna, segnala solo dove guardare. Best-effort: git assente o cwd non
    repo → None, mai bloccante."""
    if not cwd or not first_ts:
        return None
    try:
        import subprocess
        # Offset esplicito: senza, git interpreta il timestamp nel fuso locale
        # e su macchine non-UTC la finestra dei commit slitta dell'offset.
        since = first_ts.strftime("%Y-%m-%dT%H:%M:%S%z")
        inside = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return None
        n = subprocess.run(
            ["git", "-C", str(cwd), "log", "--since", since, "--oneline"],
            capture_output=True, text=True, timeout=5)
        if n.returncode != 0:
            return None
        commits = [l for l in n.stdout.splitlines() if l.strip()]
        return {"commits": len(commits)}
    except Exception:
        return None


def reap_read_cache(session_id):
    """SessionEnd: pulizia LEGACY — read-dedup è stato ritirato in 1.18.0
    (misurato 0,0–0,1% dei Read bytes su 1278 sessioni reali; stessa misura
    di headroom, che ha rimosso il proprio equivalente). Il reaper resta per
    ripulire le read-cache/ di chi lo aveva abilitato. Best-effort."""
    try:
        d = BASE / "read-cache"
        if not d.is_dir():
            return
        import shutil
        session_id = safe_sid(session_id)  # sid nei path: allowlist o skip
        if session_id:
            sd = d / session_id
            if sd.is_dir():
                shutil.rmtree(sd, ignore_errors=True)
        cutoff = datetime.now(timezone.utc).timestamp() - 172800
        for sd in d.iterdir():
            if sd.is_dir() and sd.stat().st_mtime < cutoff:
                shutil.rmtree(sd, ignore_errors=True)
    except OSError:
        return


def cmd_session_summary(args):
    opts = parse_opts(args, {"--transcript": None, "--session-id": None, "--cwd": None})
    transcript, session_id, cwd = opts["--transcript"], opts["--session-id"], opts["--cwd"]
    if not transcript and not sys.stdin.isatty():
        # invocato come hook SessionEnd: input JSON su stdin
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError:
            return
        transcript = data.get("transcript_path")
        session_id = data.get("session_id")
        cwd = data.get("cwd")
    reap_open_budget(cwd, session_id)  # prima del check transcript: l'orfano va mietuto comunque
    reap_delegations(session_id)
    reap_read_cache(session_id)
    if not transcript or not Path(transcript).is_file():
        return
    main_tot, sub_tot, n_sub, cache_resets, first_ts, last_ts, stats = \
        sum_transcript(Path(transcript))
    # Agenti Workflow: fusi nei totali subagent (le metriche derivate restano
    # un'unica contabilità di delega) ma anche riportati a parte nel payload —
    # l'allarme input-dominated del report ha bisogno del per-agente.
    wf_tot, n_wf = sum_workflow_agents(Path(transcript))
    for k in USAGE_KEYS:
        sub_tot[k] += wf_tot[k]
    n_sub += n_wf
    # Sentinella di schema: molti record validi ma zero usage o zero timestamp
    # riconosciuti = formato transcript cambiato → la summary conterebbe zeri
    # in silenzio. Logga l'anomalia (rumore nel report) e avvisa su stderr.
    n_rec = stats.get("n_records") or 0
    if n_rec >= SENTINEL_MIN_RECORDS and \
            ((stats.get("n_usage_records") or 0) == 0 or first_ts is None):
        missing = "usage" if (stats.get("n_usage_records") or 0) == 0 else "timestamp"
        log_event("schema_anomaly", {
            "source": "session-summary", "missing": missing,
            "n_records": n_rec, "transcript": str(transcript), "auto": True,
        }, session_id=session_id, cwd=cwd)
        print(f"fable-director schema sentinel: {n_rec} records but zero "
              f"recognized '{missing}' — transcript format changed? "
              f"Token accounting unreliable.", file=sys.stderr)
    inp = main_tot["input_tokens"] + sub_tot["input_tokens"]
    out = main_tot["output_tokens"] + sub_tot["output_tokens"]
    cr = main_tot["cache_read_input_tokens"] + sub_tot["cache_read_input_tokens"]
    cc = main_tot["cache_creation_input_tokens"] + sub_tot["cache_creation_input_tokens"]
    payload = {
        "input_tokens": inp, "output_tokens": out,
        "cache_read": cr, "cache_creation": cc,
        "main_output": main_tot["output_tokens"],
        "subagent_output": sub_tot["output_tokens"],
        "cache_resets": cache_resets,
        "duration_s": (last_ts - first_ts).total_seconds() if first_ts and last_ts else None,
        # account = basename del config dir (".claude" di default; qualunque
        # CLAUDE_CONFIG_DIR alternativo, es. ".claude-work"): la memoria di
        # apprendimento è UNICA e condivisa per design, ma il report può
        # distinguere le esperienze per account.
        "account": Path(os.environ.get("CLAUDE_CONFIG_DIR")
                        or Path.home() / ".claude").name,
    }
    if n_wf:
        payload["wf_agents"] = n_wf
        payload["wf_output"] = wf_tot["output_tokens"]
        payload["wf_input_fresh"] = (wf_tot["input_tokens"]
                                     + wf_tot["cache_creation_input_tokens"])
    yld = git_yield(cwd, first_ts)
    if yld is not None:
        payload["commits"] = yld["commits"]
    payload.update(stats)
    payload.update(derived_metrics(inp, out, cr, cc,
                                   main_tot["output_tokens"],
                                   sub_tot["output_tokens"], n_sub))
    log_event("session_summary", payload, session_id=session_id, cwd=cwd)


def cmd_report(args):
    opts = parse_opts(args, {"--days": "30"})
    days = int(opts["--days"])
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    con = open_db()
    rows = con.execute("SELECT ts, event, payload FROM events ORDER BY ts").fetchall()
    con.close()
    events = []
    for ts, event, payload in rows:
        dt = parse_ts(ts)
        if dt and dt.timestamp() < cutoff:
            continue
        try:
            events.append((event, json.loads(payload or "{}")))
        except json.JSONDecodeError:
            continue
    if not events:
        print(f"No events in the last {days} days.")
        return

    def fmt(n):
        return f"{n:,.0f}".replace(",", ".")

    print(f"# fable-director telemetry — last {days} days, {len(events)} events\n")

    sessions = [p for e, p in events if e == "session_summary"]
    if sessions:
        inp = sum(s.get("input_tokens") or 0 for s in sessions)
        out = sum(s.get("output_tokens") or 0 for s in sessions)
        cr = sum(s.get("cache_read") or 0 for s in sessions)
        cc = sum(s.get("cache_creation") or 0 for s in sessions)
        m_out = sum(s.get("main_output") or 0 for s in sessions)
        s_out = sum(s.get("subagent_output") or 0 for s in sessions)
        m = derived_metrics(inp, out, cr, cc, m_out, s_out,
                            sum(s.get("n_subagent_files") or 0 for s in sessions))
        print(f"Sessions: {len(sessions)} — input {fmt(inp)}, output {fmt(out)}, "
              f"cache_read {fmt(cr)}, cache_creation {fmt(cc)}")
        alarms = []
        if m["cache_hit_ratio"] is not None:
            inv = m["cache_investment"]
            inv_s = "∞ (mai riletta)" if inv is None else f"{inv:.2f}"
            print(f"cache_hit_ratio: {m['cache_hit_ratio']:.2f}  "
                  f"cache_efficiency: {m['cache_efficiency']:.2f}  "
                  f"cache_investment: {inv_s}")
            if m["cache_hit_ratio"] < 0.7:
                alarms.append("cache_hit_ratio < 0.7: prefisso instabile o sessioni troppo frammentate")
            if inv is not None and inv > 1:
                alarms.append("cache_investment > 1: si crea più cache di quanta se ne rilegga")
        if m["delegation_overhead"] is not None and s_out:
            coord = m["coordination_cost"]
            coord_s = "-" if coord is None else f"{coord:.2f}"
            print(f"delegation_overhead: {m['delegation_overhead']:.2f}  "
                  f"coordination_cost: {coord_s}")
            if coord is not None and coord > 1:
                alarms.append("coordination_cost > 1: l'orchestratore spende più dei subagenti")
        # Agenti Workflow: input fresco PER AGENTE come discriminante — sopra
        # ~100k/agente il corpus viene ri-letto cold da ogni agente (misurato
        # 2026-07-20: 267k/agente su audit legale, contro ~17k di solo
        # prefisso). L'allarme punta al rimedio, non al colpevole.
        wf_agents = sum(s.get("wf_agents") or 0 for s in sessions)
        if wf_agents:
            wf_in = sum(s.get("wf_input_fresh") or 0 for s in sessions)
            wf_out = sum(s.get("wf_output") or 0 for s in sessions)
            per_agent = wf_in / wf_agents
            print(f"workflow agents: {wf_agents} — output {fmt(wf_out)}, "
                  f"fresh input {fmt(wf_in)} (~{fmt(per_agent)}/agente)")
            if per_agent > 100_000:
                alarms.append(
                    f"workflow input-dominated: ~{fmt(per_agent)} fresh input/agente "
                    f"— corpus ri-letto cold da ogni agente? Pre-digest inline una "
                    f"volta e passa estratti mirati (skill delega-efficiente)")
        resets = sum(s.get("cache_resets") or 0 for s in sessions)
        if resets:
            alarms.append(f"cache-thrash: {resets} mid-session prefix resets "
                          f"(model switch/plugin edit/compact?) — diagnostic, never blocking")
        # Yield: output token per commit prodotto. Solo sessioni con dato git
        # (git_yield → None su cwd non-repo). RESA, non target: sessioni di
        # planning/debug non committano legittimamente, non le condanna.
        with_git = [s for s in sessions if s.get("commits") is not None]
        commits = sum(s.get("commits") or 0 for s in with_git)
        if with_git:
            g_out = sum(s.get("output_tokens") or 0 for s in with_git)
            if commits:
                print(f"yield: {commits} commits from {len(with_git)} git sessions "
                      f"(~{fmt(g_out / commits)} output tokens/commit) — "
                      f"diagnostic YIELD, never a target: planning/debug do not commit")
            else:
                print(f"yield: 0 commits from {len(with_git)} git sessions "
                      f"(~{fmt(g_out)} output tokens without commits) — normal "
                      f"for planning/debug/review; an alarm only if code was expected")
        for a in alarms:
            print(f"⚠ ALARM (not a target): {a}")

    retries = [p for e, p in events if e == "retry"]
    if retries:
        by_class = {}
        for r in retries:
            c = r.get("class", "?")
            by_class.setdefault(c, [0, 0])
            by_class[c][0] += 1
            by_class[c][1] += r.get("tokens_est") or 0
        print("\nRetries per class (potential waste):")
        for c, (n, tok) in sorted(by_class.items()):
            print(f"  {c}: {n} retries, ~{fmt(tok)} tokens")

    reversals = [p for e, p in events if e == "reversal"]
    if reversals:
        pairs = {}
        for r in reversals:
            key = f"{r.get('from', '?')}→{r.get('to', '?')}"
            pairs[key] = pairs.get(key, 0) + 1
        pairs_s = ", ".join(f"{k}×{v}" for k, v in sorted(pairs.items(), key=lambda x: -x[1]))
        print(f"\nReversals: {len(reversals)} ({pairs_s}) — not errors: initial policy "
              f"falsified; recurring patterns = playbook candidates")

    escs = [p for e, p in events if e == "escalation"]
    if escs:
        with_outcome = [x for x in escs if "resolved" in x]
        unresolved = sum(1 for x in with_outcome if not x.get("resolved"))
        extra = ""
        if with_outcome:
            extra = (f"; with outcome: {len(with_outcome)}, unresolved: {unresolved}"
                     + (" ⚠ classificazione iniziale probabilmente errata" if unresolved else ""))
        print(f"\nEscalations: {len(escs)}{extra}")

    verifs = [p for e, p in events if e == "verification"]
    if verifs:
        found = sum(1 for v in verifs if v.get("found"))
        print(f"\nVerifications: {len(verifs)}, problems found: {found} "
              f"(hit-rate {found / len(verifs):.2f}) — calibrate depth, NEVER skip on high error-cost")
        # Cross-family per tipo di task: quali tipi il verifier di famiglia
        # diversa refuta davvero. Rende "quali tipi sono affini" una domanda
        # di dati (asserire bravura-modello decade a ogni release, vietato).
        xf = [v for v in verifs if v.get("kind") == "cross-family"]
        if xf:
            by_type = {}
            for v in xf:
                t = v.get("type") or "(senza tipo)"
                by_type.setdefault(t, [0, 0])
                by_type[t][0] += 1
                if v.get("found"):
                    by_type[t][1] += 1
            print("  cross-family per type (hit-rate = refutations/calls; "
                  "N≥10 = affinity confirmed by data, not asserted):")
            for t, (n, fnd) in sorted(by_type.items(), key=lambda x: -x[1][0]):
                dense = "DENSO" if n >= 10 else "sparso"
                print(f"    {t}: {n} calls, {fnd} refuted "
                      f"(hit-rate {fnd / n:.2f}) — {dense}")

    ext = [p for e, p in events if e == "external_exec"]
    if ext:
        by_pt = {}
        for x in ext:
            key = f"{x.get('provider', '?')}/{x.get('type') or '(senza tipo)'}"
            by_pt.setdefault(key, [0, 0, 0])
            by_pt[key][0] += 1
            if x.get("ok"):
                by_pt[key][1] += 1
            if x.get("check") in ("json-invalid", "needs_context", "empty"):
                by_pt[key][2] += 1
        print("\nExternal executors per provider/type (experimental route: "
              "playbook promotion is decided by these numbers, N≥10):")
        for key, (n, ok, bad) in sorted(by_pt.items(), key=lambda x: -x[1][0]):
            dense = "DENSO" if n >= 10 else "sparso"
            print(f"  {key}: {n} runs, {ok} ok, {bad} rejects "
                  f"(ok-rate {ok / n:.2f}) — {dense}")
        tin = sum(x.get("chars_in") or 0 for x in ext) // 4
        tout = sum(x.get("chars_out") or 0 for x in ext) // 4
        print(f"  estimated external volume: ~{fmt(tin)} tokens in, ~{fmt(tout)} "
              f"tokens out — SEPARATE LEDGER, off the Claude quota (the "
              f"2×/3× budget counts Claude transcript tokens only)")

    # Calibrazione stime: rapporto actual/expected per tipo — l'errore di
    # stima è un dato, non una colpa. N<5 = indicativo, non direttivo.
    closes = [p for e, p in events if e == "task_close"]
    cal = [p for p in closes
           if (p.get("expected_output_tokens") or 0) > 0
           and p.get("actual_output_tokens") is not None]
    if cal:
        by_t = {}
        for p in cal:
            k = (p.get("type") or "(senza tipo)", p.get("route") or "?")
            by_t.setdefault(k, []).append(
                p["actual_output_tokens"] / p["expected_output_tokens"])
        print("\nEstimate calibration (actual/expected output, median — "
              "above 1 you underestimate, below 1 you overestimate):")
        for (t, r), ratios in sorted(by_t.items(), key=lambda x: -len(x[1])):
            ratios.sort()
            med = ratios[len(ratios) // 2]
            tag = "" if len(ratios) >= 5 else " (small N, indicative)"
            print(f"  {t} [{r}]: median {med:.1f}× over {len(ratios)} tasks"
                  f"{tag}")

    # Coda script-promotion: tipi ricorrenti (≥2 chiusure ok) su rotte
    # modello — candidati alla cristallizzazione in script (asse 3). La
    # decisione resta umana: qui solo l'evidenza e i token in gioco.
    rec = {}
    for p in closes:
        t = p.get("type")
        if (t and p.get("outcome") == "ok"
                and (p.get("route") or "") != "script"):
            rec.setdefault(t, [0, 0])
            rec[t][0] += 1
            rec[t][1] += p.get("actual_output_tokens") \
                or p.get("expected_output_tokens") or 0
    queue = {t: v for t, v in rec.items() if v[0] >= 2}
    if queue:
        print("\nScript-promotion candidates (recurring type on model routes "
              "— consider crystallizing, skip if already scripted or the "
              "interface is unstable):")
        for t, (n, tok) in sorted(queue.items(), key=lambda x: -x[1][1]):
            print(f"  {t}: {n} tasks closed ok, ~{fmt(tok)} output tokens "
                  f"spent on model routes")

    # Perimetro: deny e emendamenti — tanti emendamenti = perimetri dichiarati
    # sistematicamente più stretti del lavoro reale (dato di calibrazione).
    pdeny = [p for e, p in events if e == "perimeter_deny"]
    pamend = [p for e, p in events if e == "perimeter_amend"]
    if pdeny or pamend:
        nw = sum(1 for p in pdeny if p.get("level") == "never_write")
        print(f"\nWrite perimeter: {len(pdeny)} denies "
              f"({nw} never_write), {len(pamend)} amendments")

    mcps = [p for e, p in events if e == "mcp_meter"]
    if mcps:
        by_srv = {}
        for m in mcps:
            k = m.get("server") or "?"
            by_srv.setdefault(k, [0, 0])
            by_srv[k][0] += 1
            by_srv[k][1] += m.get("bytes") or 0
        print("\nMCP context weight — FLOW (tool results enter whole, paid "
              "once per call — here you see who bloats):")
        for k, (n, byt) in sorted(by_srv.items(), key=lambda x: -x[1][1]):
            print(f"  {k}: {n} calls, ~{fmt(byt // 4)} estimated tokens")

    # Giacenza: gli schemi caricati da ToolSearch restano nel prefisso e si
    # ripagano a ogni turno. Grandezza diversa dal flusso sopra: non sommarle.
    loads = [p for e, p in events if e == "mcp_schema_load"]
    if loads:
        tot = sum(p.get("bytes") or 0 for p in loads)
        print(f"\nMCP context weight — STOCK ({len(loads)} ToolSearch loads, "
              f"~{fmt(tot // 4)} estimated tokens of schema injected into the "
              f"prefix; unlike flow, this is re-paid EVERY turn of the session):")
        by_q = {}
        for p in loads:
            k = (p.get("query") or "?")[:60]
            by_q.setdefault(k, [0, 0])
            by_q[k][0] += 1
            by_q[k][1] += p.get("bytes") or 0
        for k, (n, byt) in sorted(by_q.items(), key=lambda x: -x[1][1])[:8]:
            print(f"  {n}× \"{k}\" — ~{fmt(byt // 4)} tokens")

    # Grinding: streak auto-rilevati dall'hook PostToolUse su Bash. Da leggere
    # INSIEME a escalation: molti fail_streak e zero escalation = il modello
    # macina e non diagnostica, cioe' la rule-of-3 resta lettera morta.
    streaks = [p for e, p in events if e == "fail_streak"]
    if streaks:
        by_bin = {}
        for s in streaks:
            k = s.get("binary") or "?"
            by_bin[k] = by_bin.get(k, 0) + 1
        worst = max((s.get("streak") or 0) for s in streaks)
        tops = ", ".join(f"{k}×{v}" for k, v in
                         sorted(by_bin.items(), key=lambda x: -x[1])[:5])
        n_esc = len([p for e, p in events if e == "escalation"])
        print(f"\nGrinding: {len(streaks)} fail-streak auto-detected "
              f"(worst {worst} consecutive) — {tops}")
        if not n_esc:
            print("  ⚠ zero `escalation` logged against them: the streaks were "
                  "detected but never diagnosed — rule-of-3 is not being applied")

    promos = [p for e, p in events if e == "script_promotion"]
    if promos:
        tok = sum(p.get("tokens_pre_promotion") or 0 for p in promos)
        print(f"\nScripts promoted: {len(promos)} (~{fmt(tok)} tokens spent before promotion)")

    anomalies = [p for e, p in events if e == "schema_anomaly"]
    if anomalies:
        print(f"\n⚠ SCHEMA ALARM: {len(anomalies)} transcript format anomalies "
              f"(zero recognized usage/timestamps) — token accounting "
              f"unreliable in those sessions, update the plugin")

    denies = [p for e, p in events if e == "gate_deny"]
    if denies:
        by_kind = {}
        for d in denies:
            k = d.get("kind", "?")
            by_kind[k] = by_kind.get(k, 0) + 1
        kinds_s = ", ".join(f"{k}×{v}" for k, v in
                            sorted(by_kind.items(), key=lambda x: -x[1]))
        print(f"\nGate deny: {len(denies)} ({kinds_s}) — deleghe tentate e negate "
              f"dal gate; molti no_budget = il modello salta il pre-budget, "
              f"molti flagged = post-mortem che non vengono chiusi")

    mismatches = [p for e, p in events if e == "effort_mismatch"]
    if mismatches:
        pairs = {}
        for m in mismatches:
            key = f"{m.get('declared', '?')}≠{m.get('pinned', '?')}"
            pairs[key] = pairs.get(key, 0) + 1
        pairs_s = ", ".join(f"{k}×{v}" for k, v in
                            sorted(pairs.items(), key=lambda x: -x[1]))
        print(f"\nEffort mismatch: {len(mismatches)} ({pairs_s}) — budget and agent "
              f"in disaccordo; ricorrente = la rotta dichiarata non riflette "
              f"l'esecutore reale, candidato playbook")

    flags = [p for e, p in events if e == "budget_flag"]
    opened = sum(1 for e, _ in events if e == "task_open")
    closed_tasks = [p for e, p in events if e == "task_close"]
    print(f"\nTasks: {opened} opened, {len(closed_tasks)} closed, {len(flags)} busts ≥3×")

    # Breakdown per effort dichiarato: misura se il tier low regge davvero
    # (flag rate vs tier alti). Dato che decide la promozione warn→deny;
    # senza N, il tier resta euristica.
    by_effort = {}
    for t in closed_tasks:
        eff = t.get("effort")
        if not eff:
            continue
        by_effort.setdefault(eff, [0, 0])
        by_effort[eff][0] += 1
        if t.get("outcome") == "flagged":
            by_effort[eff][1] += 1
    if by_effort:
        order = {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}
        print("Tasks per declared effort (high flag rate on low = tier "
              "insufficient for that type):")
        for eff, (n, fl) in sorted(by_effort.items(),
                                   key=lambda x: order.get(x[0], 9)):
            print(f"  {eff}: {n} tasks, {fl} flagged")

    # Densità per tipo: i dati sovrascrivono una regola di routing SOLO nelle
    # celle marcate dense (N≥10 task chiusi). Soglia codificata, non a giudizio.
    by_type = {}
    for t in closed_tasks:
        typ = t.get("type") or "(senza tipo)"
        by_type.setdefault(typ, [0, 0])
        by_type[typ][0] += 1
        if t.get("outcome") == "flagged":
            by_type[typ][1] += 1
    if by_type:
        print("Density per task type (data override allowed only if DENSE, N≥10):")
        for typ, (n, fl) in sorted(by_type.items(), key=lambda x: -x[1][0]):
            dense = "DENSE — eligible for override" if n >= 10 else "sparse — heuristics only"
            print(f"  {typ}: {n} tasks, {fl} flagged — {dense}")

    con = open_db()
    n_cache = con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    con.close()
    if n_cache:
        print(f"\nIdempotency cache: {n_cache} entries")


CACHE_CAP = 500          # voci massime
CACHE_TTL_DAYS = 90      # scadenza


def cache_effective_key(caller_key, model):
    """Chiave effettiva = chiave del chiamante + versione plugin + modello.
    Un upgrade di plugin o un cambio di executor invalida la cache DA SOLO:
    un hit stale post-upgrade propaga a costo zero output di una logica che
    non esiste più (review duale 2026-07-10, proposta Gemini). La versione
    viene da plugin.json accanto allo script; assente → 'unknown' (degrada
    a invalidazione per modello soltanto)."""
    try:
        pj = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
        ver = json.loads(pj.read_text()).get("version", "unknown")
    except Exception:
        ver = "unknown"
    return hashlib.sha256(
        f"{caller_key}:{ver}:{model or '-'}".encode()).hexdigest()


def cmd_cache_get(args):
    if not args:
        sys.exit("cache-get richiede KEY (sha256 di schema_version+prompt+input) "
                 "[--model M]")
    key = args[0]
    opts = parse_opts(args[1:], {"--model": None})
    key = cache_effective_key(key, opts["--model"])
    con = open_db()
    row = con.execute("SELECT output, ts FROM llm_cache WHERE key=?", (key,)).fetchone()
    con.close()
    if not row:
        sys.exit(1)  # miss: exit code 1, nessun output
    output, ts = row
    dt = parse_ts(ts)
    if dt and (datetime.now(timezone.utc) - dt).days > CACHE_TTL_DAYS:
        sys.exit(1)  # scaduta: trattala come miss (la prune la rimuoverà)
    print(output)


def cmd_cache_put(args):
    if not args:
        sys.exit("cache-put richiede KEY")
    key = args.pop(0)
    verified = "--verified" in args
    if verified:
        args.remove("--verified")
    opts = parse_opts(args, {"--file": None, "--output": None, "--model": None})
    key = cache_effective_key(key, opts["--model"])
    if not verified:
        sys.exit("cache-put rifiutato: serve --verified — si cachano SOLO output "
                 "passati da verifica deterministica rung-1 (un output cached non "
                 "verificato propaga errori a costo zero)")
    if opts["--file"]:
        output = Path(opts["--file"]).read_text(errors="replace")
    elif opts["--output"]:
        output = opts["--output"]
    else:
        sys.exit("cache-put richiede --file o --output")
    con = open_db()
    con.execute("INSERT OR REPLACE INTO llm_cache(key, ts, output) VALUES(?,?,?)",
                (key, now_iso(), output))
    # prune: TTL poi cap (le più vecchie muoiono prima)
    cutoff = (datetime.now(timezone.utc)).timestamp() - CACHE_TTL_DAYS * 86400
    for k, ts in con.execute("SELECT key, ts FROM llm_cache").fetchall():
        dt = parse_ts(ts)
        if dt and dt.timestamp() < cutoff:
            con.execute("DELETE FROM llm_cache WHERE key=?", (k,))
    n = con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    if n > CACHE_CAP:
        con.execute("DELETE FROM llm_cache WHERE key IN "
                    "(SELECT key FROM llm_cache ORDER BY ts LIMIT ?)", (n - CACHE_CAP,))
    con.commit()
    con.close()
    print(f"cache scritta: {key[:16]}…")


def parse_opts(args, spec):
    opts = dict(spec)
    i = 0
    while i < len(args):
        if args[i] in opts and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            sys.exit(f"argomento non riconosciuto: {args[i]}")
    return opts


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    dispatch = {"budget-open": cmd_budget_open, "budget-close": cmd_budget_close,
                "budget-amend": cmd_budget_amend,
                "log": cmd_log, "session-summary": cmd_session_summary,
                "report": cmd_report,
                "cache-get": cmd_cache_get, "cache-put": cmd_cache_put}
    if cmd not in dispatch:
        sys.exit(f"sottocomando sconosciuto: {cmd}\n{__doc__}")
    dispatch[cmd](args)


if __name__ == "__main__":
    main()
