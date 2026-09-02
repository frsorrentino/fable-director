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
from datetime import datetime, timedelta, timezone
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
# Input-equivalenti: rapporti di listino standard per rendere confrontabili le
# quattro componenti di usage (audit 2026-08 su 2.389 sessioni: cache_read =
# ~72% del costo main in eq, output = ~10% — i soli output token misurano un
# decimo della spesa reale; numeri e script in context-audit/DISTILLATO.md del
# workspace fable-director). FASE DI TARATURA: l'eq viene misurato e riportato
# ovunque, l'enforcement 2×/3× resta sui token dichiarati finché una taratura
# su budget reali non fissa le soglie eq.
EQ_MULT = {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_create": 1.25}
# I moltiplicatori sopra sono il DEFAULT universale; le parti che cambiano
# col listino di un modello (oggi: cache_read 0.025x su claude-fable-5-1)
# vivono in model-economics.json — shipped con il plugin, override utente in
# ~/.claude/fable-director/model-economics.json. Un modello nuovo e' una
# riga in 'models', mai un cambio di policy. Il modello va letto dal record
# che si sta contando (message.model), mai cachato a inizio sessione:
# /model puo' cambiarlo a meta' sessione.
ECON_SHIPPED = Path(__file__).resolve().parent.parent / "model-economics.json"
ECON_USER = BASE / "model-economics.json"
_ECON = None
# Tier di effort ammessi (allineati al frontmatter agent di Claude Code).
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


def model_economics():
    """{'default': {...}, 'models': {prefix: {...}}} — shipped + override
    utente fusi ('models' per chiave, 'default' per campo). File assenti o
    illeggibili → EQ_MULT puro: la contabilita' non dipende mai dal file."""
    global _ECON
    if _ECON is None:
        econ = {"default": dict(EQ_MULT), "models": {}}
        for path in (ECON_SHIPPED, ECON_USER):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            econ["default"].update(
                {k: float(v) for k, v in (data.get("default") or {}).items()
                 if k in EQ_MULT})
            for name, mult in (data.get("models") or {}).items():
                if isinstance(mult, dict):
                    econ["models"].setdefault(name, {}).update(
                        {k: float(v) for k, v in mult.items() if k in EQ_MULT})
        _ECON = econ
    return _ECON


def eq_mult(model=None):
    """Moltiplicatori eq per un id modello: match sul prefisso piu' lungo in
    'models' (cosi' 'claude-fable-5-1' copre anche varianti datate), altrimenti
    'default'. model None/unknown → default."""
    econ = model_economics()
    mult = dict(econ["default"])
    if model:
        best = max((k for k in econ["models"] if str(model).startswith(k)),
                   key=len, default=None)
        if best:
            mult.update(econ["models"][best])
    return mult


def eq_tokens(inp, out, cr, cc, model=None, cr_by_model=None):
    """Costo in input-equivalenti dalle quattro componenti pure di usage
    (inp = input_tokens puri, NON input+cache_create). `model` sceglie i
    moltiplicatori; `cr_by_model` ({id: cache_read}) sostituisce `cr` quando
    la sessione ha letto cache su piu' modelli (ogni quota alla SUA tariffa)."""
    m = eq_mult(model)
    if cr_by_model:
        cr_term = sum((v or 0) * eq_mult(k)["cache_read"]
                      for k, v in cr_by_model.items())
    else:
        cr_term = cr * m["cache_read"]
    return int(inp * m["input"] + out * m["output"]
               + cr_term + cc * m["cache_create"])
SENTINEL_MIN_RECORDS = 20  # sotto: transcript troppo corto per giudicare lo schema
# Riaperture da cui il rework smette di essere un fatto e diventa una diagnosi
# (contesto incompleto): sotto questa soglia si riporta il numero e basta —
# suggerire "spec incompleta" su 1-2 riaperture sarebbe overclaim.
REWORK_DIAG_MIN = 4


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


def find_usage(obj, in_subagent=False, model=None):
    """Yield (usage, in_subagent, model): usage annidati sotto toolUseResult
    sono l'aggregato di un subagent completato — il main transcript basta per
    la contabilità completa senza scansionare i file agent (né double
    counting). `model` = message.model piu' vicino nell'albero (None se
    assente): serve alla tariffa cache_read per modello, letta record per
    record — un /model a meta' sessione cambia la tariffa da li' in poi."""
    if isinstance(obj, dict):
        model = obj.get("model") if isinstance(obj.get("model"), str) else model
        usage = obj.get("usage")
        if isinstance(usage, dict) and any(k in usage for k in USAGE_KEYS):
            yield (usage, in_subagent, model)
        for k, v in obj.items():
            yield from find_usage(v, in_subagent or k == "toolUseResult", model)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_usage(v, in_subagent, model)


CACHE_RESET_THRESHOLD = 10_000  # cache_read "alto" prima di un reset sospetto
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}  # prima azione irreversibile


def find_tool_uses(obj, in_subagent=False):
    """(nome, input) dei tool_use nel main loop (sottoalberi toolUseResult
    esclusi). L'input serve al conteggio rework: file_path dei write-tool."""
    if isinstance(obj, dict):
        if not in_subagent and obj.get("type") == "tool_use" and obj.get("name"):
            tin = obj.get("input")
            yield obj["name"], (tin if isinstance(tin, dict) else {})
        for k, v in obj.items():
            yield from find_tool_uses(v, in_subagent or k == "toolUseResult")
    elif isinstance(obj, list):
        for v in obj:
            yield from find_tool_uses(v, in_subagent)


def path_tail(p):
    """Ultimi 2 componenti del path: abbastanza per riconoscere il file,
    niente alberi di clienti nel DB (stessa disciplina di fail-streak, che
    salva il binario e mai la riga di comando)."""
    parts = [x for x in str(p).replace("\\", "/").split("/") if x]
    return "/".join(parts[-2:]) if parts else str(p)


def rework_update(rw, name, tool_input):
    """Fold di un tool_use nei contatori di rework (in place). `rw` =
    {"last": str|None, "touch": {path: n}, "bouts": {path: n}} — chiavi path
    PIENE (l'identità serve al conteggio; il troncamento avviene solo
    all'esposizione, in rework_stats). Un "bout" = run massimale di tocchi
    consecutivi allo stesso file: modifiche consecutive sono iterazione
    normale, è il RITORNO su un file dopo averne toccati altri che segnala
    informazione arrivata dopo la scrittura (metrica InsForge/Chawla:
    re-edit = contesto incompleto al momento della scrittura)."""
    if name not in WRITE_TOOLS:
        return
    fp = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(fp, str) or not fp:
        return
    rw["touch"][fp] = rw["touch"].get(fp, 0) + 1
    if fp != rw.get("last"):
        rw["bouts"][fp] = rw["bouts"].get(fp, 0) + 1
    rw["last"] = fp


def rework_new():
    return {"last": None, "touch": {}, "bouts": {}}


def rework_stats(rw):
    """Contatori → payload esponibile (path troncati), None se zero write.
    reopens(file) = bouts − 1; worst = top 3 file per riaperture."""
    touch = (rw or {}).get("touch") or {}
    if not touch:
        return None
    bouts = rw.get("bouts") or {}
    reo = {f: n - 1 for f, n in bouts.items() if n > 1}
    worst = sorted(reo.items(), key=lambda x: (-x[1], x[0]))[:3]
    return {"write_touches": sum(touch.values()),
            "files_written": len(touch),
            "files_reopened": len(reo),
            "reopens": sum(reo.values()),
            "worst": [[path_tail(f), n] for f, n in worst]}


def sum_transcript(path):
    """Un solo passaggio sul main transcript: totali main/subagent, finestra
    temporale, deleghe completate, reset di prefisso cache (cache_read che
    torna a 0 nel main loop dopo letture alte = prefisso invalidato a metà
    sessione), prima azione irreversibile (turno/token: sessioni senza write
    restano None — analisi richiesta non è esitazione), statistiche tool."""
    main = dict.fromkeys(USAGE_KEYS, 0)
    sub = dict.fromkeys(USAGE_KEYS, 0)
    n_sub = 0
    cr_by_model = {}  # cache_read per modello (main+sub): tariffa eq per modello
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
    # Batching main-thread: tool call per turno-messaggio. Raggruppare per
    # message.id è obbligatorio — il 65% dei messaggi occupa più righe jsonl
    # (audit 2026-08): contare per riga darebbe sempre 1.0. Baseline misurata
    # 1.20; ogni call read-only solitaria paga un turno intero (~37k eq) di
    # ripresentazione contesto.
    batch_calls = 0
    batch_turn_ids = set()
    # Effort per messaggio (CC >=2.1.212 lo scrive nel transcript): dedup per
    # message.id — stessi spezzoni multi-riga del batching. La distribuzione
    # alimenta la taratura effort×tipo×costo nel report: quale effort serve a
    # quale lavoro diventa una domanda di dati, non di frontmatter.
    effort_mix = {}
    effort_seen = set()
    rw = rework_new()
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
            n_calls_rec = 0
            for name, tin in find_tool_uses(rec):
                n_calls_rec += 1
                tool_counts[name] = tool_counts.get(name, 0) + 1
                run_len = run_len + 1 if name == last_tool else 1
                last_tool = name
                if run_len > max_run[1]:
                    max_run = (name, run_len)
                if first_write_turn is None and name in WRITE_TOOLS:
                    first_write_turn = turns
                    tokens_before_first_write = main["output_tokens"]
                rework_update(rw, name, tin)
            if n_calls_rec and rec.get("type") == "assistant" \
                    and not rec.get("isSidechain"):
                mid = (rec.get("message") or {}).get("id")
                if mid:
                    batch_calls += n_calls_rec
                    batch_turn_ids.add(mid)
            if rec.get("type") == "assistant" and not rec.get("isSidechain") \
                    and rec.get("effort"):
                mid = (rec.get("message") or {}).get("id")
                if mid not in effort_seen:
                    if mid:
                        effort_seen.add(mid)
                    lvl = str(rec["effort"])
                    effort_mix[lvl] = effort_mix.get(lvl, 0) + 1
            rec_had_usage = False
            for usage, in_sub, model in find_usage(rec):
                rec_had_usage = True
                bucket = sub if in_sub else main
                for k in USAGE_KEYS:
                    bucket[k] += usage.get(k) or 0
                _cr = usage.get("cache_read_input_tokens") or 0
                if _cr:
                    cr_by_model[model or "unknown"] = \
                        cr_by_model.get(model or "unknown", 0) + _cr
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
    if batch_turn_ids:
        stats["tool_calls_per_turn"] = round(
            batch_calls / len(batch_turn_ids), 2)
    if effort_mix:
        stats["effort_mix"] = effort_mix
    if cr_by_model:
        stats["cache_read_by_model"] = cr_by_model
    # Rework: riaperture di file già scritti (informazione arrivata DOPO la
    # scrittura = contesto incompleto). Chiave presente solo se la sessione
    # ha scritto: l'assenza è "nessuna scrittura", non zero rework.
    rew = rework_stats(rw)
    if rew:
        stats["rework"] = rew
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
                for usage, _, model in find_usage(rec):
                    for k in USAGE_KEYS:
                        tot[k] += usage.get(k) or 0
                    _cr = usage.get("cache_read_input_tokens") or 0
                    if _cr:
                        tot.setdefault("cache_read_by_model", {})
                        tot["cache_read_by_model"][model or "unknown"] = \
                            tot["cache_read_by_model"].get(model or "unknown", 0) + _cr
    return tot, n_files


def derived_metrics(inp, out, cr, cc, main_out, sub_out, n_sub,
                    cr_by_model=None):
    """Metriche derivate; None dove il denominatore è zero. `cr_by_model`
    applica a ogni quota di cache_read la tariffa del SUO modello."""
    total_in = inp + cr + cc
    eq = eq_tokens(inp, out, cr, cc, cr_by_model=cr_by_model)
    return {
        "cache_hit_ratio": cr / (cr + cc) if (cr + cc) else None,
        "cache_efficiency": cr / total_in if total_in else None,
        "cache_investment": cc / cr if cr else None,   # None = cache mai riletta
        "delegation_overhead": sub_out / out if out else None,
        "coordination_cost": main_out / sub_out if sub_out else None,
        "n_subagent_files": n_sub,
        # Costo vero della sessione (vedi EQ_MULT): output/eq dice quanto del
        # costo il righello "output token" sta effettivamente vedendo.
        "eq_tokens": eq,
        "output_cost_share": (out * EQ_MULT["output"]) / eq if eq else None,
    }


# ---------- sottocomandi ----------

def similar_tasks_line(task_type, exclude_declared_at=None):
    """E3 / C1.2 (1.39): precedenti dello stesso tipo, in parole, al momento
    della rotta — non nel report che nessuno apre prima di stimare.
    '3 similar tasks before (design-review): they cost about a third of what
    was estimated; 2 closed fine, 1 blew the budget.' None sotto 1 precedente
    o senza tipo. Mostra fatti: non cambia rotte (override resta a N≥10)."""
    if not task_type or not DB_PATH.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        rows = con.execute("SELECT payload FROM events WHERE event='task_close' "
                           "ORDER BY ts DESC LIMIT 400").fetchall()
        con.close()
    except sqlite3.Error:
        return None
    ratios, outcomes, n = [], {}, 0
    for (pl,) in rows:
        try:
            t = json.loads(pl)
        except (json.JSONDecodeError, TypeError):
            continue
        if t.get("type") != task_type:
            continue
        if exclude_declared_at and t.get("declared_at") == exclude_declared_at:
            continue
        n += 1
        outcomes[t.get("outcome") or "?"] = outcomes.get(t.get("outcome") or "?", 0) + 1
        exp, act = t.get("expected_output_tokens"), t.get("actual_output_tokens")
        if exp and act is not None:
            ratios.append(act / exp)
        if n >= 20:
            break
    if not n:
        return None
    head = f"{n} similar task{'s' if n != 1 else ''} before ({task_type})"
    parts = []
    if ratios:
        med = sorted(ratios)[len(ratios) // 2]
        words = ratio_words(med, 1.0).replace("the estimate", "what was estimated")
        parts.append(("they " if n != 1 else "it ") + words)
    oc = []
    for k, label in (("ok", "closed fine"), ("flagged", "blew the budget"),
                     ("abandoned", "abandoned")):
        if outcomes.get(k):
            oc.append(f"{outcomes[k]} {label}")
    if oc:
        parts.append(", ".join(oc))
    return head + (": " + "; ".join(parts) if parts else "") + "."


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
                             "--agents": None, "--priority": None})
    if opts["--priority"] and opts["--priority"] not in ("normal", "incident"):
        sys.exit("invalid --priority (allowed: normal, incident)")
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
        # priority incident: questa sessione ha la precedenza sulla quota —
        # le ALTRE sessioni dello stesso account vedono negati i nuovi
        # fan-out finche' il flag vive (scadenza automatica, vedi PRIORITY).
        "priority": opts["--priority"] or "normal",
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
    if budget.get("priority") == "incident":
        priority_set(budget, cwd)
        print(f"FD ▲ incident priority on: other sessions on this account get "
              f"their new fan-outs held while the five-hour window is above "
              f"{PRIORITY_WINDOW_PCT:.0f}% — expires in {PRIORITY_TTL_S // 3600} h "
              f"or at budget-close.")
    # E3: memoria dei precedenti dello stesso tipo, al momento giusto.
    sim = similar_tasks_line(budget.get("type"), budget.get("declared_at"))
    if sim:
        print("FD ≈ " + sim)
    # Stima in USD (opt-in, mai inventata): solo se l'utente ha dichiarato il
    # listino in pricing.json ({"input_usd_per_mtok": N}). La stima eq è in
    # input-equivalenti di listino, quindi USD = eq × prezzo input. Utile per
    # allineare il paracadute nativo --max-budget-usd delle sessioni headless.
    try:
        pricing = json.loads((BASE / "pricing.json").read_text())
        per_mtok = float(pricing.get("input_usd_per_mtok"))
        est_eq = eq_tokens(max(exp_in, 0), exp_out, 0, 0)
        usd = est_eq / 1_000_000 * per_mtok
        print(f"  estimated ceiling ≈ ${usd:.2f} at declared list price "
              f"({est_eq:,} eq × ${per_mtok:g}/Mtok input) — for headless "
              f"batch sessions consider the native belt: "
              f"claude --max-budget-usd {max(usd * 3, 1):.0f}")
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass  # nessun listino dichiarato: niente numeri inventati


def resolve_budget_file(cwd_opt):
    """Risoluzione SESSION-FIRST del budget file per close/amend (incidente
    2026-08-21: un cd residuo nella shell ha fatto chiudere con outcome ok il
    budget stantio di un ALTRO cwd). Regole:
    - --cwd esplicito = intento dichiarato: slug diretto, nessun filtro;
    - senza --cwd, con CLAUDE_CODE_SESSION_ID nell'env: il budget open/flagged
      di QUESTA sessione vince sul cwd del processo (avviso se differiscono;
      più d'uno → ambiguo, si chiede --cwd);
    - fallback slug del cwd corrente, ma il budget FRESCO (<24h, stessa soglia
      orfano del lease di budget-open) di un'altra sessione si rifiuta:
      chiuderlo distruggerebbe il suo enforcement/post-mortem;
    - env senza session id → comportamento legacy, identico a prima.
    Ritorna (cwd, bfile): il cwd del budget risolto, così log_event e ricevuta
    portano il cwd giusto anche quando il processo sta altrove."""
    if cwd_opt:
        return cwd_opt, BUDGETS / f"{cwd_slug(cwd_opt)}.json"
    cwd = os.getcwd()
    sid = safe_sid(os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if sid and BUDGETS.is_dir():
        mine = []
        for p in sorted(BUDGETS.glob("*.json")):
            if p.name.endswith(".state.json"):
                continue
            try:
                b = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if b.get("status") in ("open", "flagged") \
                    and b.get("owner_sid") == sid:
                mine.append((p, b))
        if len(mine) == 1:
            p, b = mine[0]
            bcwd = b.get("cwd")
            if bcwd and str(bcwd) != str(cwd):
                print(f"FD nota: budget di questa sessione dichiarato in "
                      f"{bcwd} (processo in {cwd}) — risolto per sessione")
                return str(bcwd), p
            return cwd, p
        if len(mine) > 1:
            tasks = ", ".join(f"'{(b.get('task') or '?')[:40]}'"
                              for _, b in mine)
            sys.exit(f"budget ambiguo: questa sessione ha "
                     f"{len(mine)} budget aperti ({tasks}) — "
                     f"usa --cwd per indicare quale")
    bfile = BUDGETS / f"{cwd_slug(cwd)}.json"
    if sid and bfile.is_file():
        try:
            b = json.loads(bfile.read_text())
            other = b.get("owner_sid")
            declared = parse_ts(b.get("declared_at"))
            fresh = (declared is not None and
                     (datetime.now(timezone.utc) - declared)
                     .total_seconds() < 86400)
            if other and other != sid and fresh \
                    and b.get("status") in ("open", "flagged"):
                sys.exit(f"refused: il budget di questo cwd appartiene a "
                         f"un'altra sessione ({other[:8]}…, task "
                         f"'{b.get('task')}') ed è fresco (<24h) — "
                         f"--cwd esplicito solo se sai che è morta; "
                         f">24h diventa orfano e si chiude senza flag")
        except (json.JSONDecodeError, OSError):
            pass
    return cwd, bfile


def cmd_budget_amend(args):
    """Emendamento ESPLICITO del perimetro del budget aperto: il deny del
    perimeter-gate non si aggira, si emenda — e l'emendamento resta nel
    decision record (quante volte il lavoro reale sfonda il perimetro
    dichiarato è un dato di calibrazione, come le stime)."""
    opts = parse_opts(args, {"--add-paths": None, "--reason": None,
                             "--cwd": None, "--route": None})
    if not opts["--add-paths"] and not opts["--route"]:
        sys.exit("budget-amend requires --add-paths \"glob[,glob]\" and/or --route ROUTE")
    cwd, bfile = resolve_budget_file(opts["--cwd"])
    if not bfile.is_file():
        sys.exit(f"nessun budget file: {bfile}")
    budget = json.loads(bfile.read_text())
    if budget.get("status") != "open":
        sys.exit("budget-amend requires an OPEN budget")
    if opts["--route"]:
        # Cambio di rotta a meta' task = reversal (decisione iniziale
        # falsificata, non errore): loggato, e il budget ricorda la rotta nuova.
        if opts["--route"] not in ("inline", "workflow", "script", "agent", "external"):
            sys.exit("invalid --route")
        prev = budget.get("route")
        budget["route"] = opts["--route"]
        budget.setdefault("amendments", []).append({
            "route": opts["--route"], "from": prev, "reason": opts["--reason"],
            "ts": now_iso()})
        log_event("reversal", {"from": prev or "?", "to": opts["--route"],
                               "reason": opts["--reason"], "kind": "route",
                               "at": "budget-amend"}, cwd=cwd)
        if not opts["--add-paths"]:
            write_json_atomic(bfile, budget)
            print(f"route amended: {prev or '?'} → {opts['--route']} (reversal logged)")
            return
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


def fmt(n):
    """1234567 → '1.234.567' (separatore italiano, come la statusline)."""
    return f"{n:,.0f}".replace(",", ".")


# ---------- broker di quota tra sessioni (D.3.4) ----------
PRIORITY_FILE = BASE / "priority.json"
PRIORITY_TTL_S = 2 * 3600       # flag dimenticato: muore da solo
PRIORITY_WINDOW_PCT = 50.0      # sotto: quota abbondante, nessun freno


def account_key():
    return hashlib.sha256((os.environ.get("CLAUDE_CONFIG_DIR")
                           or str(Path.home() / ".claude")).encode()).hexdigest()[:8]


def priority_set(budget, cwd):
    """Scrive il flag di precedenza (una sessione per account alla volta)."""
    try:
        PRIORITY_FILE.write_text(json.dumps({
            "task": budget.get("task"), "session_id": budget.get("owner_sid"),
            "account": account_key(), "cwd": str(cwd), "since": now_iso(),
            "expires_at": (datetime.now(timezone.utc)
                           + timedelta(seconds=PRIORITY_TTL_S)).isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def priority_active(session_id=None):
    """Il flag vivo di UN'ALTRA sessione dello stesso account, o None.
    Scaduto → rimosso e None (fail-open: un flag vecchio non frena nulla)."""
    try:
        if not PRIORITY_FILE.is_file():
            return None
        pr = json.loads(PRIORITY_FILE.read_text(encoding="utf-8"))
        exp = datetime.fromisoformat(str(pr.get("expires_at")))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= exp:
            PRIORITY_FILE.unlink(missing_ok=True)
            return None
        if pr.get("account") != account_key():
            return None
        if session_id and pr.get("session_id") == session_id:
            return None
        return pr
    except Exception:
        return None


def priority_clear(session_id=None, cwd=None):
    """A budget-close: rimuove il flag se e' di questa sessione (o cwd)."""
    try:
        if not PRIORITY_FILE.is_file():
            return
        pr = json.loads(PRIORITY_FILE.read_text(encoding="utf-8"))
        if (session_id and pr.get("session_id") == session_id) or \
                (cwd and str(pr.get("cwd")) == str(cwd)):
            PRIORITY_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def ratio_words(actual, expected):
    """Rapporto consuntivo/stima in PAROLE (nessuna sigla a schermo)."""
    if not expected or actual is None:
        return None
    r = actual / expected
    if r < 0.4:
        return "cost about a third of the estimate"
    if r < 0.7:
        return "cost about half the estimate"
    if r < 1.3:
        return "cost about what was estimated"
    if r < 3:
        return f"cost {r:.1f} times the estimate"
    return f"cost more than three times the estimate ({r:.1f}×)"


def subagent_state(sid):
    """State del misuratore deleghe per la sessione (subagent-meter.py):
    conteggi, tipi, esiti. {} se assente."""
    if not sid:
        return {}
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(sid))[:120]
    try:
        return json.loads((BASE / "subagents" / f"{safe}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def external_calls_since(sid, cwd, since_iso):
    """Chiamate esterne (external_exec) del task: per sessione se nota,
    altrimenti per cwd da declared_at. {provider: n}."""
    out = {}
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
        if sid:
            rows = con.execute("SELECT payload FROM events WHERE event='external_exec' "
                               "AND session_id=? AND ts>=?", (sid, since_iso or "")).fetchall()
        else:
            rows = con.execute("SELECT payload FROM events WHERE event='external_exec' "
                               "AND cwd=? AND ts>=?", (str(cwd), since_iso or "")).fetchall()
        con.close()
        for (pl,) in rows:
            prov = (json.loads(pl).get("provider") or "?")
            out[prov] = out.get(prov, 0) + 1
    except Exception:
        pass
    return out


def receipt_lines(budget, cwd, sid):
    """Ricevuta LEGGIBILE del task chiuso: cosa era, cosa è costato, chi ha
    eseguito, com'è stato verificato, cosa è uscito dalla macchina. Ogni
    pezzo assente viene omesso, mai stimato. Ritorna (riga_di_chiusura,
    righe_dettaglio)."""
    outcome = budget.get("outcome") or "?"
    head = {"ok": "Closed fine", "flagged": "Closed flagged (post-mortem due)",
            "abandoned": "Closed as abandoned"}.get(outcome, f"Closed ({outcome})")
    bits = []
    rw = ratio_words(budget.get("actual_output_tokens"),
                     budget.get("expected_output_tokens"))
    if rw:
        bits.append(rw)
    st = subagent_state(sid)
    n_dlg = int(st.get("stopped") or 0) if st else 0
    oc = (st.get("outcomes") or {}) if st else {}
    if n_dlg:
        fails = sum(v for k, v in oc.items() if k in ("blocked", "needs_context", "abstain"))
        s_d = f"{n_dlg} delegation{'s' if n_dlg != 1 else ''}"
        if fails:
            s_d += f", {fails} failed"
        elif oc.get("unknown"):
            s_d += f", {oc['unknown']} without a status token"
        bits.append(s_d)
    elif budget.get("route") in ("agent", "workflow"):
        bits.append("no delegation recorded")
    reopens = budget.get("reopens")
    if reopens is not None:
        bits.append("no file reopened" if not reopens else
                    f"{reopens} file reopen{'s' if reopens != 1 else ''}")
    vrc = budget.get("verify_rc")
    if budget.get("verify"):
        if vrc is None:
            bits.append("verification not run")
        elif vrc == 0:
            bits.append("verification passed")
        elif vrc == "timeout":
            bits.append("verification timed out")
        else:
            bits.append(f"verification FAILED (exit {vrc})")
    ext = external_calls_since(sid, cwd, budget.get("declared_at"))
    if ext:
        bits.append("external: " + ", ".join(f"{k} ×{v}" for k, v in sorted(ext.items())))
    mins = None
    try:
        d0 = datetime.fromisoformat(str(budget.get("declared_at")).replace("Z", "+00:00"))
        d1 = datetime.fromisoformat(str(budget.get("closed_at")).replace("Z", "+00:00"))
        mins = int((d1 - d0).total_seconds() // 60)
    except (ValueError, TypeError):
        pass
    if mins is not None:
        bits.append(f"{mins} min" if mins < 120 else f"{mins // 60} h {mins % 60} min")
    line = f"{head}: {budget.get('task')}" + (" — " + ", ".join(bits) + "." if bits else ".")

    detail = []
    what = " / ".join(x for x in (budget.get("type"), budget.get("route"),
                                  f"effort {budget['effort']}" if budget.get("effort") else None) if x)
    if what:
        detail.append(f"what: {what}")
    if st and st.get("by_type"):
        who = ", ".join(f"{t} ×{n}" for t, n in sorted(st["by_type"].items(), key=lambda x: -x[1]))
        detail.append(f"executors: {who}")
    for lb in (st.get("last_blocked") or [])[-3:] if st else []:
        detail.append(f"  {lb.get('status', '?').upper()} from {lb.get('agent_type')}: "
                      f"{lb.get('blocker') or '(no blocker line)'}")
    for sw in budget.get("model_switches") or []:
        detail.append(f"model switch: {sw.get('from')} → {sw.get('to')}"
                      + (f" with {fmt(sw['context_tokens'])} tokens of context" if sw.get("context_tokens") else ""))
    if budget.get("actual_eq_tokens"):
        crm = budget.get("cache_read_by_model") or {}
        models = ", ".join(sorted(crm)) if crm else None
        detail.append(f"cost: {fmt(budget['actual_eq_tokens'])} eq"
                      + (f" (cache read on {models})" if models else ""))
    if budget.get("verify"):
        detail.append(f"verification: {budget['verify']}")
    if budget.get("data_class"):
        detail.append(f"data class: {budget['data_class']}")
    if budget.get("paths"):
        detail.append("write perimeter: " + ", ".join(budget["paths"]))
    return line, detail


def cmd_budget_close(args):
    opts = parse_opts(args, {"--outcome": "ok", "--cwd": None,
                             "--actual-output": None})
    cwd, bfile = resolve_budget_file(opts["--cwd"])
    if not bfile.is_file():
        sys.exit(f"nessun budget file: {bfile}")
    budget = json.loads(bfile.read_text())
    budget["status"] = "closed"
    budget["outcome"] = opts["--outcome"]
    budget["closed_at"] = now_iso()
    close_sid = None  # dallo state dello Stop hook: attribuzione di sessione
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
                close_sid = st.get("sid") or None
                # out/inp = main transcript; wf_out/wf_inp = agenti Workflow
                # (stessa contabilità dell'enforcement Stop, che li somma).
                budget.setdefault("actual_output_tokens",
                                  int(st.get("out") or 0) + int(st.get("wf_out") or 0))
                budget.setdefault("actual_input_tokens",
                                  int(st.get("inp") or 0) + int(st.get("wf_inp") or 0))
                # Consuntivo in eq (taratura soglie: vedi EQ_MULT). inp dello
                # state include cache_create → si scorpora coi campi cc/cr.
                _cr = int(st.get("cr") or 0) + int(st.get("wf_cr") or 0)
                _cc = int(st.get("cc") or 0) + int(st.get("wf_cc") or 0)
                _in = int(st.get("inp") or 0) + int(st.get("wf_inp") or 0)
                _out = int(st.get("out") or 0) + int(st.get("wf_out") or 0)
                _crm = {}
                for key in ("cr_by_model", "wf_cr_by_model"):
                    for m, v in (st.get(key) or {}).items():
                        _crm[m] = _crm.get(m, 0) + int(v or 0)
                budget.setdefault("actual_eq_tokens",
                                  eq_tokens(max(_in - _cc, 0), _out, _cr, _cc,
                                            cr_by_model=_crm or None))
                if _crm:
                    budget.setdefault("cache_read_by_model", _crm)
                # Rework del task (contato dallo Stop hook nello stesso scan):
                # nel task_close alimenta la vista per-tipo del report — un
                # tipo che riapre sempre = contratto/contesto sistematicamente
                # incompleto, non esecutore scarso.
                rew = rework_stats(st.get("rw") or {})
                if rew:
                    budget.setdefault("reopens", rew["reopens"])
                    budget.setdefault("rework_worst", rew["worst"])
                # Esito del verify eseguibile (Stop hook, C1.6): in ricevuta.
                if st.get("verify_rc") is not None:
                    budget.setdefault("verify_rc", st.get("verify_rc"))
                    budget.setdefault("verify_at", st.get("verify_at"))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    write_json_atomic(bfile, budget)
    if budget.get("priority") == "incident":
        priority_clear(budget.get("owner_sid"), cwd)
    # Lo state file dello Stop incrementale è chiavato su declared_at: a
    # budget chiuso è morto — rimuoverlo evita accumulo, non serve migrarlo.
    try:
        sfile.unlink()
    except OSError:
        pass
    log_event("task_close", budget, session_id=close_sid, cwd=cwd)
    # Ricevuta locale (provenance): snapshot machine-readable del task chiuso
    # — stima vs consuntivo, contratto verify, perimetro, emendamenti, esito.
    # Zero token modello: la scrive questo script, nessuno la rilegge se non
    # per audit. Cap 200 file (le più vecchie muoiono).
    rdir = BUDGETS.parent / "receipts"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        rdir.mkdir(parents=True, exist_ok=True)
        receipt = {k: budget.get(k) for k in (
            "task", "type", "route", "effort", "expected_output_tokens",
            "expected_input_tokens", "actual_output_tokens",
            "actual_input_tokens", "outcome", "verify", "data_class",
            "paths", "amendments", "declared_at", "closed_at", "owner_sid",
            "reopens", "rework_worst")}
        receipt["cwd"] = cwd
        try:
            receipt["plugin_version"] = json.loads(
                (Path(__file__).parent.parent / ".claude-plugin"
                 / "plugin.json").read_text()).get("version")
        except (json.JSONDecodeError, OSError):
            pass
        (rdir / f"{cwd_slug(cwd)}-{stamp}.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=1))
        old = sorted(rdir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for p in old[:-200]:
            p.unlink()
            p.with_suffix(".md").unlink(missing_ok=True)
    except Exception:
        pass
    # Ricevuta LEGGIBILE (1.39): la stessa chiusura in parole, per chi non
    # legge JSON — collega, cliente, la sessione che riparte da qui.
    line, detail = receipt_lines(budget, cwd, close_sid or budget.get("owner_sid"))
    try:
        md = [f"# {line}", "", f"- when: {budget.get('closed_at')}", f"- where: {cwd}"]
        md += [f"- {d.strip()}" for d in detail]
        (rdir / f"{cwd_slug(cwd)}-{stamp}.md").write_text("\n".join(md) + "\n",
                                                          encoding="utf-8")
    except Exception:
        pass
    print(line)
    for d in detail:
        print("  " + d)


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
    rows = con.execute("SELECT ts, session_id, event, payload "
                       "FROM events ORDER BY ts").fetchall()
    con.close()
    events = []
    mcp_sid = []  # (sid, event, payload) dei soli eventi MCP attribuiti a una
    #               sessione: servono al join caricati/chiamati della sezione
    #               STOCK. Le righe storiche senza session_id restano fuori:
    #               dato assente, non zero.
    hint_sid = []  # (sid, event, payload) per il join control-arm hint→esito
    for ts, sid, event, payload in rows:
        dt = parse_ts(ts)
        if dt and dt.timestamp() < cutoff:
            continue
        try:
            p = json.loads(payload or "{}")
        except json.JSONDecodeError:
            continue
        events.append((event, p))
        if sid and event in ("mcp_schema_load", "mcp_meter"):
            mcp_sid.append((sid, event, p))
        if sid and event in ("route_hint", "task_close"):
            hint_sid.append((ts, sid, event, p))
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

        # Effort mix (transcript CC >=2.1.212): distribuzione e costo eq per
        # effort dominante di sessione — dato per tarare effort×tipo, mai
        # target (spingere l'effort giù per far scendere l'eq è Goodhart).
        emix = {}
        for s in sessions:
            for lvl, n in (s.get("effort_mix") or {}).items():
                emix[lvl] = emix.get(lvl, 0) + int(n or 0)
        if emix:
            tot = sum(emix.values())
            parts = ", ".join(f"{l} {n} ({n / tot:.0%})"
                              for l, n in sorted(emix.items(), key=lambda x: -x[1]))
            print(f"effort mix (main messages): {parts}")
            by_dom = {}
            for s in sessions:
                em = s.get("effort_mix") or {}
                if em and s.get("eq_tokens"):
                    dom = max(em.items(), key=lambda x: x[1])[0]
                    by_dom.setdefault(dom, []).append(s["eq_tokens"])
            for d, vals in sorted(by_dom.items()):
                vals.sort()
                print(f"  sessions dominated by {d}: {len(vals)}, "
                      f"median {fmt(vals[len(vals) // 2])} eq")

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

    # Control arm dell'hint di rotta: il 10% dei prompt con match non riceve
    # l'hint (holdout deterministico per sessione+giorno); qui si confrontano
    # i due bracci. Numeri SOLO sopra soglia di sufficienza — sotto, medie di
    # braccio sono rumore e un rapporto sarebbe teatro (soglia e principio
    # dal control arm di ooples/token-optimizer-mcp, MIT).
    hints = [p for e, p in events if e == "route_hint"]
    armed = [p for p in hints if "holdout" in p]
    if armed:
        treated = [p for p in armed if not p.get("holdout")]
        withheld = [p for p in armed if p.get("holdout")]
        # Per-SESSIONE (review 1.35.1): braccio della sessione = braccio del
        # suo PRIMO hint armato; sessioni con hint in entrambi i bracci
        # (eventi legacy pre-fix) sono contaminate e si escludono; un
        # task_close conta come adozione solo se chiuso DOPO il primo hint
        # della sessione (un budget aperto prima non può essere effetto
        # dell'hint). Soglia di sufficienza sulle stesse unità del
        # confronto: sessioni, non eventi.
        first_hint = {}   # sid -> (ts, holdout) del primo hint armato
        arms_seen = {}    # sid -> set dei bracci visti
        closes = {}       # sid -> [(ts, route)]
        for ts, s, e, p in hint_sid:
            if e == "route_hint" and "holdout" in p:
                arms_seen.setdefault(s, set()).add(bool(p.get("holdout")))
                if s not in first_hint:
                    first_hint[s] = (ts, bool(p.get("holdout")))
            elif e == "task_close":
                closes.setdefault(s, []).append((ts, p.get("route") or "?"))
        mixed = {s for s, a in arms_seen.items() if len(a) > 1}
        NON_INLINE = {"external", "script", "workflow", "agent"}
        arm_n = {True: 0, False: 0}
        arm_adopted = {True: 0, False: 0}
        for s, (hts, hold) in first_hint.items():
            if s in mixed:
                continue
            arm_n[hold] += 1
            if any(cts >= hts and r in NON_INLINE
                   for cts, r in closes.get(s, [])):
                arm_adopted[hold] += 1
        print(f"\nRoute-hint control arm: {len(treated)} shown / "
              f"{len(withheld)} withheld events — "
              f"{arm_n[False]} shown / {arm_n[True]} withheld sessions"
              + (f", {len(mixed)} mixed-arm sessions excluded" if mixed
                 else ""))
        if arm_n[False] >= 20 and arm_n[True] >= 5:
            r_t = arm_adopted[False] / arm_n[False]
            r_w = arm_adopted[True] / arm_n[True]
            print(f"  cheap-route adoption (task_close after first hint): "
                  f"shown {r_t:.2f} ({arm_adopted[False]}/{arm_n[False]}) vs "
                  f"withheld {r_w:.2f} ({arm_adopted[True]}/{arm_n[True]}) "
                  f"— if equal, the hint changes nothing and can die")
        else:
            print("  insufficient data (need ≥20 shown and ≥5 withheld "
                  "SESSIONS) — numbers below this would be theatre")

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
        # Caricati vs chiamati, per sessione: una query `select:A,B,…` enumera
        # gli schemi entrati nel prefisso; il join con i mcp_meter della STESSA
        # sessione dice quanti sono poi stati chiamati davvero. Solo tool
        # mcp__* (i tool nativi caricati via select: non passano dal meter) e
        # solo righe attribuite (session_id presente). La query è troncata a
        # 120 char alla scrittura: se piena, l'ultimo nome può essere un
        # frammento e viene scartato — meglio un conteggio più corto che uno
        # falso.
        sel_loaded, called = {}, {}
        for sid, ev, p in mcp_sid:
            if ev == "mcp_meter":
                if p.get("tool"):
                    called.setdefault(sid, set()).add(p["tool"])
                continue
            q = str(p.get("query") or "")
            if not q.startswith("select:"):
                continue
            names = [t.strip() for t in q[len("select:"):].split(",")
                     if t.strip()]
            if len(q) >= 120 and names:
                names.pop()  # possibile frammento da troncamento
            names = {t for t in names if t.startswith("mcp__")}
            if names:
                sel_loaded.setdefault(sid, set()).update(names)
        if sel_loaded:
            n_loaded = sum(len(v) for v in sel_loaded.values())
            n_unused = sum(len(v - called.get(sid, set()))
                           for sid, v in sel_loaded.items())
            line = (f"  select: loads attributed to a session: {n_loaded} "
                    f"mcp tools loaded, {n_unused} never called in that "
                    f"session")
            if n_unused:
                line += (" — schema re-paid every turn for tools never used: "
                         "load fewer tools per select")
            print(line)

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

    # Rework: file riaperti dopo la prima scrittura (bout non consecutivi).
    # La firma dell'informazione arrivata DOPO la scrittura: il costo non sta
    # nelle tool call ma nei re-edit, perché ogni ritorno su un file già
    # scritto rispedisce la conversazione cresciuta. Metrica auto-scritta
    # da session-summary/Stop hook — mai autostima del modello.
    rew = [p.get("rework") for e, p in events if e == "session_summary"]
    rew = [r for r in rew if isinstance(r, dict) and r.get("write_touches")]
    if rew:
        tot = sum(r.get("reopens") or 0 for r in rew)
        n_aff = sum(1 for r in rew if (r.get("reopens") or 0) > 0)
        per = sorted((r.get("reopens") or 0) for r in rew)
        med = per[len(per) // 2]
        print(f"\nRework — files reopened after first write (late-arriving "
              f"info): {len(rew)} sessions with writes, {tot} reopens total, "
              f"median {med}/session, {n_aff} sessions affected")
        worst_all = {}
        for r in rew:
            for pair in (r.get("worst") or []):
                try:
                    tail, n = pair[0], int(pair[1])
                except (IndexError, TypeError, ValueError):
                    continue
                worst_all[tail] = worst_all.get(tail, 0) + n
        for tail, n in sorted(worst_all.items(), key=lambda x: -x[1])[:5]:
            print(f"  {tail}: {n} reopens")
        # Per-tipo dal task_close: un tipo che riapre SEMPRE = il contratto
        # consegna un contesto sistematicamente incompleto → candidato a un
        # "context pack" (script deterministico che raccoglie file/stato/schemi
        # PRIMA della delega) — speculare agli script-promotion candidates.
        by_type = {}
        for e2, p2 in events:
            if (e2 == "task_close" and p2.get("type")
                    and isinstance(p2.get("reopens"), int)):
                by_type.setdefault(p2["type"], []).append(p2["reopens"])
        cands = {t: v for t, v in by_type.items()
                 if len(v) >= 2 and sorted(v)[len(v) // 2] >= REWORK_DIAG_MIN}
        if cands:
            print("  Context-pack candidates (recurring type, median reopens "
                  f"≥ {REWORK_DIAG_MIN} — the spec ships incomplete context; "
                  "the executor is not the problem):")
            for t, v in sorted(cands.items(),
                               key=lambda x: -sorted(x[1])[len(x[1]) // 2]):
                print(f"    {t}: {len(v)} tasks, median "
                      f"{sorted(v)[len(v) // 2]} reopens")

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

    outcomes = [p for e, p in events if e == "delegation_outcome"]
    if outcomes:
        by = {}
        for o in outcomes:
            key = (o.get("agent_type") or "?", o.get("model") or "?")
            d = by.setdefault(key, {"n": 0, "ok": 0, "fail": 0, "unknown": 0, "eq": []})
            d["n"] += 1
            st = o.get("status")
            if st in ("ok", "concerns"):
                d["ok"] += 1
            elif st == "unknown":
                d["unknown"] += 1
            else:
                d["fail"] += 1
            if o.get("eq"):
                d["eq"].append(o["eq"])
        print("\nDelegation outcomes (status token read by the SubagentStop hook, "
              "never self-assessed):")
        for (atype, model), d in sorted(by.items(), key=lambda x: -x[1]["n"]):
            med = f", median {fmt(int(sorted(d['eq'])[len(d['eq']) // 2]))} eq" if d["eq"] else ""
            print(f"  {atype} on {model}: {d['n']} runs — ok {d['ok']}, failed {d['fail']}, "
                  f"no status token {d['unknown']}{med}")
        esc = [p for e, p in events if e == "escalation" and p.get("auto")]
        if esc:
            cls = {}
            for x in esc:
                cls[x.get("class")] = cls.get(x.get("class"), 0) + 1
            print("  rule-of-3 diagnoses (auto): " + ", ".join(f"{k} ×{v}" for k, v in cls.items()))

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


# ---------- sforzo per cliente e per deliverable (D.3.1) ----------
CLIENTS_FILE = BASE / "clients.json"
_STOP_TERMS = set("""della delle dello degli nella nelle questo questa questi
quello quella sono essere fare fatto anche ancora avere dove ogni perche perché
prima quando quindi senza sopra sotto tutto tutti tutte verso about after again
against because before being between could should their there these those
through under until where which while would files script scripts python tests
test task tasks claude fable director sessione session budget budgets prompt
modello modelli model agent agents subagent workflow report progetto cartella
folder directory cliente client""".split())


def rare_terms(text):
    import re as _re
    return {w for w in _re.findall(r"[a-zà-ú0-9_\-]{5,}", str(text).lower())
            if w not in _STOP_TERMS and not w.isdigit()}


def load_clients():
    """{"nome": ["pattern cwd o remote", ...]} — glob fnmatch o sottostringa
    sul cwd (con ~ espanso). Assente → {}."""
    try:
        data = json.loads(CLIENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}


def client_of(cwd, clients):
    import fnmatch
    c = str(cwd or "")
    for name, pats in clients.items():
        for pat in pats:
            pat = str(pat)
            if pat.startswith("~"):
                pat = str(Path.home()) + pat[1:]
            if pat in c or fnmatch.fnmatch(c, pat) or fnmatch.fnmatch(c, pat.rstrip("/") + "/*"):
                return name
    return None


def hours(sec):
    try:
        h = float(sec) / 3600
    except (TypeError, ValueError):
        return "?"
    return f"{h:.1f} h"


def cmd_effort(args):
    """`effort --client NAME [--month YYYY-MM]` — quanto COSTA servire quel
    cliente (ore macchina, eq, deliverable chiusi con esito, rework, deleghe
    interne ed esterne), su tutte le cartelle e gli account della macchina.
    `effort --like "testo"` — quanto SFORZO ha richiesto un deliverable
    simile (turni, deleghe, rework, giorni) prima di un preventivo a forfait.
    Mai una cifra in euro: sforzo, non fattura."""
    opts = parse_opts(args, {"--client": None, "--month": None, "--like": None,
                             "--list": None})
    if not DB_PATH.is_file():
        sys.exit("no telemetry yet")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    rows = con.execute("SELECT ts, event, session_id, cwd, payload FROM events "
                       "WHERE event IN ('session_summary','task_close','external_exec') "
                       "ORDER BY ts").fetchall()
    con.close()
    ev = []
    for ts, event, sid, cwd, pl in rows:
        try:
            ev.append((ts, event, sid, cwd, json.loads(pl or "{}")))
        except json.JSONDecodeError:
            continue

    if opts["--like"]:
        terms = rare_terms(opts["--like"])
        hits = []
        for ts, event, sid, cwd, p in ev:
            if event != "task_close":
                continue
            shared = terms & rare_terms(" ".join(str(p.get(k) or "") for k in ("task", "type", "verify")))
            if len(shared) >= 2 or (len(terms) == 1 and shared):
                hits.append((len(shared), ts, cwd, p))
        if not hits:
            print(f"no closed task resembles \"{opts['--like']}\" (needs two rare words in common).")
            return
        hits.sort(key=lambda x: (-x[0], x[1]))
        print(f"# Effort of deliverables resembling \"{opts['--like']}\" — {len(hits)} found\n")
        for _, ts, cwd, p in hits[:10]:
            bits = [ts[:10], "/".join(Path(str(cwd)).parts[-2:])]
            rw = ratio_words(p.get("actual_output_tokens"), p.get("expected_output_tokens"))
            if rw:
                bits.append(rw)
            if p.get("actual_eq_tokens"):
                bits.append(f"{fmt(p['actual_eq_tokens'])} eq")
            if p.get("reopens") is not None:
                bits.append("no file reopened" if not p["reopens"] else f"{p['reopens']} reopens")
            try:
                d0 = datetime.fromisoformat(str(p.get("declared_at")).replace("Z", "+00:00"))
                d1 = datetime.fromisoformat(str(p.get("closed_at")).replace("Z", "+00:00"))
                mins = int((d1 - d0).total_seconds() // 60)
                bits.append(f"{mins} min" if mins < 120 else f"{mins // 60} h")
            except (ValueError, TypeError):
                pass
            outcome = {"ok": "closed fine", "flagged": "blew the budget"}.get(p.get("outcome"), p.get("outcome"))
            print(f"- {p.get('task')} ({p.get('type') or 'no type'}, {p.get('route') or '?'}): "
                  f"{outcome} — " + ", ".join(bits))
        print("\nEffort, not price: turns, delegations and rework of the last similar deliverable.")
        return

    clients = load_clients()
    if not clients:
        sys.exit(f"no clients declared: write {CLIENTS_FILE} as "
                 '{"joyconcept": ["~/Desktop/*/joyconcept*", "git@github.com:org/joyconcept.git"]}')
    if not opts["--client"]:
        print("clients: " + ", ".join(sorted(clients)))
        return
    name = opts["--client"]
    if name not in clients:
        sys.exit(f"unknown client '{name}' (declared: {', '.join(sorted(clients)) or 'none'})")
    month = opts["--month"]
    sess, tasks, ext = [], [], {}
    for ts, event, sid, cwd, p in ev:
        if month and not ts.startswith(month):
            continue
        if client_of(cwd, clients) != name:
            continue
        if event == "session_summary":
            sess.append((ts, sid, cwd, p))
        elif event == "task_close":
            tasks.append((ts, cwd, p))
        elif event == "external_exec":
            ext[p.get("provider") or "?"] = ext.get(p.get("provider") or "?", 0) + 1
    if not sess and not tasks:
        print(f"nothing recorded for '{name}'" + (f" in {month}" if month else "") + ".")
        return
    span = f" in {month}" if month else " (all time)"
    print(f"# Cost of serving '{name}'{span} — effort, not price\n")
    secs = sum(float(p.get("duration_s") or 0) for _, _, _, p in sess)
    eq = sum(int(p.get("eq_tokens") or 0) for _, _, _, p in sess)
    turns = sum(int(p.get("n_usage_records") or 0) for _, _, _, p in sess)
    folders = sorted({"/".join(Path(str(c)).parts[-2:]) for _, _, c, _ in sess} |
                     {"/".join(Path(str(c)).parts[-2:]) for _, c, _ in tasks})
    accounts = sorted({p.get("account") for _, _, _, p in sess if p.get("account")})
    print(f"sessions: {len(sess)} ({turns} turns, {hours(secs)} of active session time, "
          f"{fmt(eq)} eq)" + (f" on accounts {', '.join(accounts)}" if accounts else ""))
    print("folders: " + ", ".join(folders))
    sub_out = sum(int(p.get("subagent_output") or 0) for _, _, _, p in sess)
    n_sub = sum(int(p.get("n_subagent_files") or 0) for _, _, _, p in sess)
    print(f"delegations: {n_sub} agent runs, {fmt(sub_out)} tokens delegated"
          + (f"; external: " + ", ".join(f"{k} ×{v}" for k, v in sorted(ext.items())) if ext else ""))
    if tasks:
        oc = {}
        for _, _, p in tasks:
            oc[p.get("outcome") or "?"] = oc.get(p.get("outcome") or "?", 0) + 1
        reopens = sum(int(p.get("reopens") or 0) for _, _, p in tasks)
        print(f"deliverables (task budgets closed): {len(tasks)} — "
              + ", ".join(f"{v} {({'ok': 'closed fine', 'flagged': 'blew the budget'}).get(k, k)}"
                          for k, v in sorted(oc.items()))
              + f"; {reopens} file reopens")
        for ts, cwd, p in tasks[-8:]:
            rw = ratio_words(p.get("actual_output_tokens"), p.get("expected_output_tokens"))
            print(f"  {ts[:10]} {p.get('task')} ({p.get('type') or 'no type'}): "
                  f"{({'ok': 'closed fine', 'flagged': 'blew the budget'}).get(p.get('outcome'), p.get('outcome'))}"
                  + (f", {rw}" if rw else ""))
    else:
        print("deliverables: no task budget closed for this client.")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    dispatch = {"budget-open": cmd_budget_open, "budget-close": cmd_budget_close,
                "budget-amend": cmd_budget_amend,
                "log": cmd_log, "session-summary": cmd_session_summary,
                "report": cmd_report, "effort": cmd_effort,
                "cache-get": cmd_cache_get, "cache-put": cmd_cache_put}
    if cmd not in dispatch:
        sys.exit(f"sottocomando sconosciuto: {cmd}\n{__doc__}")
    dispatch[cmd](args)


if __name__ == "__main__":
    main()
