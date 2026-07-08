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
               [--approach S] [--fallback S]
               [--route inline|workflow|script|agent] [--reason S] [--alternative S]
               scrive il budget file (status=open) e logga task_open;
               --type = categoria task per la tabella empirica (es. seo-batch, code-review);
               --route/--reason/--alternative = decision record: quale rotta, perché
               (es. "axis2>axis4"), quale scartata — serve alla telemetria (reversal
               analysis), non al modello
  budget-close [--outcome ok|flagged|abandoned]
               marca il budget file closed e logga task_close
  log EVENT [--json '{...}']
               logga un evento puntuale (retry, escalation, verification, script_promotion, budget_flag)
  session-summary [--transcript P --session-id S --cwd P]
               (hook SessionEnd: legge lo stdin JSON dell'hook) calcola totali token,
               metriche cache/delega e reset di prefisso dal main transcript
               (l'usage dei subagenti è dentro toolUseResult: niente scan file, niente double counting)
  report [--days N]
               aggrega gli eventi: cache metrics, overhead delega, spreco per categoria,
               hit-rate verifiche, densità per tipo task (soglia override: N≥10).
               Le metriche sono ALLARMI, non target.
  cache-get KEY / cache-put KEY (--file F | --output S) --verified
               cache idempotente opt-in per output LLM su input invariati.
               Si scrive SOLO con --verified (output passato da verifica deterministica
               rung-1). KEY = sha256 di schema_version + prompt + contenuto input.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path.home() / ".claude" / "fable-director"
DB_PATH = BASE / "telemetry.db"
BUDGETS = BASE / "budgets"
USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")
SENTINEL_MIN_RECORDS = 20  # sotto: transcript troppo corto per giudicare lo schema


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def cwd_slug(cwd):
    return "-" + str(cwd).strip("/").replace("/", "-").replace(".", "-")


def open_db():
    BASE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS events("
                "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, "
                "cwd TEXT, event TEXT NOT NULL, payload TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS llm_cache("
                "key TEXT PRIMARY KEY, ts TEXT NOT NULL, output TEXT NOT NULL)")
    return con


def log_event(event, payload, session_id=None, cwd=None):
    con = open_db()
    con.execute("INSERT INTO events(ts, session_id, cwd, event, payload) VALUES(?,?,?,?,?)",
                (now_iso(), session_id, str(cwd or os.getcwd()),
                 event, json.dumps(payload, ensure_ascii=False)))
    con.commit()
    con.close()


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
    opts = parse_opts(args, {"--task": None, "--expected-output": None,
                             "--expected-input": None, "--type": None,
                             "--approach": None, "--fallback": None, "--cwd": None,
                             "--route": None, "--reason": None, "--alternative": None})
    if not opts["--task"] or not opts["--expected-output"]:
        sys.exit("budget-open richiede --task e --expected-output")
    cwd = opts["--cwd"] or os.getcwd()
    BUDGETS.mkdir(parents=True, exist_ok=True)
    budget = {
        "task": opts["--task"],
        "type": opts["--type"],
        "approach": opts["--approach"],
        "fallback": opts["--fallback"],
        "route": opts["--route"],
        "reason": opts["--reason"],
        "alternative": opts["--alternative"],
        "expected_output_tokens": int(opts["--expected-output"]),
        "expected_input_tokens": int(opts["--expected-input"] or 0),
        "declared_at": now_iso(),
        "cwd": str(cwd),
        "status": "open",
    }
    bfile = BUDGETS / f"{cwd_slug(cwd)}.json"
    bfile.write_text(json.dumps(budget, ensure_ascii=False, indent=1))
    log_event("task_open", budget, cwd=cwd)
    print(f"budget aperto: {bfile}")


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
    bfile.write_text(json.dumps(budget, ensure_ascii=False, indent=1))
    log_event("task_close", budget, cwd=cwd)
    print(f"budget chiuso ({opts['--outcome']}): {budget.get('task')}")


ALLOWED_EVENTS = {"task_open", "task_close", "budget_flag", "retry", "escalation",
                  "script_promotion", "verification", "session_summary", "reversal",
                  "schema_anomaly"}


def cmd_log(args):
    if not args:
        sys.exit(f"log richiede EVENT fra: {', '.join(sorted(ALLOWED_EVENTS))}")
    event = args.pop(0)
    if event not in ALLOWED_EVENTS:
        sys.exit(f"evento non ammesso: {event} (niente metriche soggettive)")
    opts = parse_opts(args, {"--json": "{}", "--session-id": None, "--cwd": None})
    try:
        payload = json.loads(opts["--json"])
    except json.JSONDecodeError as e:
        sys.exit(f"--json non valido: {e}")
    log_event(event, payload, session_id=opts["--session-id"], cwd=opts["--cwd"])
    print(f"loggato: {event}")


def reap_open_budget(cwd):
    """SessionEnd: un budget ancora 'open' che il modello non ha chiuso è orfano.
    Lo chiudo come abandoned così un Stop hook di una sessione futura non agisce
    su un budget morto e il report non resta falsato da un task svanito in
    silenzio. Tocco SOLO status=open — flagged/closed/stale restano intatti.
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
        budget["status"] = "closed"
        budget["outcome"] = "abandoned"
        budget["closed_at"] = now_iso()
        bfile.write_text(json.dumps(budget, ensure_ascii=False, indent=1))
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
        since = first_ts.strftime("%Y-%m-%dT%H:%M:%S")
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
    """SessionEnd: la read-cache (hook read-dedup) è per-sessione — rimuovi la
    dir della sessione + orfane >48h (sessioni crashate). Best-effort."""
    try:
        d = BASE / "read-cache"
        if not d.is_dir():
            return
        import shutil
        if session_id:
            sd = d / str(session_id)
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
    reap_open_budget(cwd)  # prima del check transcript: l'orfano va mietuto comunque
    reap_delegations(session_id)
    reap_read_cache(session_id)
    if not transcript or not Path(transcript).is_file():
        return
    main_tot, sub_tot, n_sub, cache_resets, first_ts, last_ts, stats = \
        sum_transcript(Path(transcript))
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
        print(f"fable-director sentinella schema: {n_rec} record ma zero "
              f"'{missing}' riconosciuti — formato transcript cambiato? "
              f"Contabilità token inaffidabile.", file=sys.stderr)
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
    }
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
        print(f"Nessun evento negli ultimi {days} giorni.")
        return

    def fmt(n):
        return f"{n:,.0f}".replace(",", ".")

    print(f"# Telemetria fable-director — ultimi {days} giorni, {len(events)} eventi\n")

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
        print(f"Sessioni: {len(sessions)} — input {fmt(inp)}, output {fmt(out)}, "
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
        resets = sum(s.get("cache_resets") or 0 for s in sessions)
        if resets:
            alarms.append(f"cache-thrash: {resets} reset di prefisso a metà sessione "
                          f"(cambio modello/edit plugin/compact?) — diagnostico, mai blocking")
        # Yield: output token per commit prodotto. Solo sessioni con dato git
        # (git_yield → None su cwd non-repo). RESA, non target: sessioni di
        # planning/debug non committano legittimamente, non le condanna.
        with_git = [s for s in sessions if s.get("commits") is not None]
        commits = sum(s.get("commits") or 0 for s in with_git)
        if with_git:
            g_out = sum(s.get("output_tokens") or 0 for s in with_git)
            if commits:
                print(f"yield: {commits} commit da {len(with_git)} sessioni git "
                      f"(~{fmt(g_out / commits)} output token/commit) — RESA "
                      f"diagnostica, mai target: planning/debug non committano")
            else:
                print(f"yield: 0 commit da {len(with_git)} sessioni git "
                      f"(~{fmt(g_out)} output token senza commit) — normale per "
                      f"planning/debug/review; allarme solo se atteso codice")
        for a in alarms:
            print(f"⚠ ALLARME (non target): {a}")

    retries = [p for e, p in events if e == "retry"]
    if retries:
        by_class = {}
        for r in retries:
            c = r.get("class", "?")
            by_class.setdefault(c, [0, 0])
            by_class[c][0] += 1
            by_class[c][1] += r.get("tokens_est") or 0
        print("\nRetry per classe (spreco potenziale):")
        for c, (n, tok) in sorted(by_class.items()):
            print(f"  {c}: {n} retry, ~{fmt(tok)} token")

    reversals = [p for e, p in events if e == "reversal"]
    if reversals:
        pairs = {}
        for r in reversals:
            key = f"{r.get('from', '?')}→{r.get('to', '?')}"
            pairs[key] = pairs.get(key, 0) + 1
        pairs_s = ", ".join(f"{k}×{v}" for k, v in sorted(pairs.items(), key=lambda x: -x[1]))
        print(f"\nReversal: {len(reversals)} ({pairs_s}) — non errori: policy iniziale "
              f"falsificata; pattern ricorrenti = candidati playbook")

    escs = [p for e, p in events if e == "escalation"]
    if escs:
        with_outcome = [x for x in escs if "resolved" in x]
        unresolved = sum(1 for x in with_outcome if not x.get("resolved"))
        extra = ""
        if with_outcome:
            extra = (f"; con esito: {len(with_outcome)}, non risolutive: {unresolved}"
                     + (" ⚠ classificazione iniziale probabilmente errata" if unresolved else ""))
        print(f"\nEscalation: {len(escs)}{extra}")

    verifs = [p for e, p in events if e == "verification"]
    if verifs:
        found = sum(1 for v in verifs if v.get("found"))
        print(f"\nVerifiche: {len(verifs)}, problemi trovati: {found} "
              f"(hit-rate {found / len(verifs):.2f}) — calibra la profondità, MAI saltare su error-cost alto")
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
            print("  cross-family per tipo (hit-rate = refutazioni/chiamate; "
                  "N≥10 = affinità confermata dai dati, non asserita):")
            for t, (n, fnd) in sorted(by_type.items(), key=lambda x: -x[1][0]):
                dense = "DENSO" if n >= 10 else "sparso"
                print(f"    {t}: {n} chiamate, {fnd} refutate "
                      f"(hit-rate {fnd / n:.2f}) — {dense}")

    promos = [p for e, p in events if e == "script_promotion"]
    if promos:
        tok = sum(p.get("tokens_pre_promotion") or 0 for p in promos)
        print(f"\nScript promossi: {len(promos)} (~{fmt(tok)} token spesi prima della promozione)")

    anomalies = [p for e, p in events if e == "schema_anomaly"]
    if anomalies:
        print(f"\n⚠ ALLARME schema: {len(anomalies)} anomalie formato transcript "
              f"(zero usage/timestamp riconosciuti) — contabilità token "
              f"inaffidabile in quelle sessioni, aggiornare il plugin")

    dedups = [p for e, p in events if e == "read_dedup"]
    if dedups:
        tok = sum(d.get("tokens_est") or 0 for d in dedups)
        by_kind = {}
        for d in dedups:
            by_kind[d.get("kind", "?")] = by_kind.get(d.get("kind", "?"), 0) + 1
        kinds_s = ", ".join(f"{k}×{v}" for k, v in
                            sorted(by_kind.items(), key=lambda x: -x[1]))
        print(f"\nRead-dedup: {len(dedups)} riletture deduplicate ({kinds_s}), "
              f"~{fmt(tok)} token risparmiati (lossless, ≈char/4)")

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

    flags = [p for e, p in events if e == "budget_flag"]
    opened = sum(1 for e, _ in events if e == "task_open")
    closed_tasks = [p for e, p in events if e == "task_close"]
    print(f"\nTask: {opened} aperti, {len(closed_tasks)} chiusi, {len(flags)} sforamenti ≥3×")

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
        print("Densità per tipo task (override dati ammesso solo se DENSA, N≥10):")
        for typ, (n, fl) in sorted(by_type.items(), key=lambda x: -x[1][0]):
            dense = "DENSA — eligible per override" if n >= 10 else "sparsa — solo euristiche"
            print(f"  {typ}: {n} task, {fl} flaggati — {dense}")

    con = open_db()
    n_cache = con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    con.close()
    if n_cache:
        print(f"\nCache idempotente: {n_cache} voci")


CACHE_CAP = 500          # voci massime
CACHE_TTL_DAYS = 90      # scadenza


def cmd_cache_get(args):
    if not args:
        sys.exit("cache-get richiede KEY (sha256 di schema_version+prompt+input)")
    key = args[0]
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
    opts = parse_opts(args, {"--file": None, "--output": None})
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
                "log": cmd_log, "session-summary": cmd_session_summary,
                "report": cmd_report,
                "cache-get": cmd_cache_get, "cache-put": cmd_cache_put}
    if cmd not in dispatch:
        sys.exit(f"sottocomando sconosciuto: {cmd}\n{__doc__}")
    dispatch[cmd](args)


if __name__ == "__main__":
    main()
