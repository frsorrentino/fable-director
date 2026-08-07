#!/usr/bin/env python3
"""Hook Stop: enforcement deterministico del pre-budget (2× checkpoint, 3× blocco).

Il learning loop non dipende più dalla disciplina del modello a fine task:
questo hook gira a ogni fine turno, e se esiste un budget file aperto per il
cwd corrente confronta i token effettivi (dal transcript, dopo declared_at)
con la stima dichiarata. Contabilità input = token FRESCHI (input + cache
creation): i cache READ sono esclusi BY DESIGN — misurano riletture di
prefisso già pagate altrove, non lavoro nuovo; il budget input va quindi
letto come "fresh-token budget", mai come bolletta totale. Due soglie:
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

Sentinella di schema: ≥20 record validi ma zero usage o zero timestamp
riconosciuti → il formato transcript è cambiato; warning una tantum
(schema_warned nel budget file) + evento schema_anomaly, enforcement sospeso
invece di contare zeri in silenzio.

Attribuzione per lineage, non per mtime. Due sorgenti, mai sovrapposte:
- Agent tool: l'usage aggregato del subagent completato è dentro il main
  transcript (toolUseResult.usage) — find_usage lo raccoglie, niente scan
  di file agent, niente double counting.
- Workflow tool: i suoi agenti NON compaiono mai in toolUseResult (verificato
  2026-07-20 su sessione reale: 5 call Workflow, zero usage annidati) —
  vivono in <sessiondir>/subagents/workflows/wf_*/agent-*.jsonl e vanno
  scansionati lì, o l'enforcement è cieco proprio sulla route più costosa
  (misurato: 9,2M input freschi invisibili in una sessione).
Record senza timestamp: inferenza posizionale — il JSONL è append-only
cronologico, vale il timestamp dell'ultimo record che lo precede.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows cp1252: i messaggi con × ≥ crasherebbero print() (issue #1).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens")
SENTINEL_MIN_RECORDS = 20  # sotto: transcript troppo corto per giudicare lo schema


_FDT = None


def _fdt():
    """fd-telemetry.py caricato una volta per processo (log_event, walker dei
    tool_use e helper di rework single-sourced lì). None su qualunque errore:
    la telemetria e il rework degradano in silenzio, l'enforcement — il
    lavoro primario di questo hook — non dipende mai dal modulo."""
    global _FDT
    if _FDT is None:
        try:
            spec = importlib.util.spec_from_file_location(
                "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _FDT = mod
        except Exception:
            _FDT = False
    return _FDT or None


def log_telemetry(event, payload, cwd):
    """Persist an objective event to telemetry deterministically, reusing
    fd-telemetry's log_event so the DB schema stays single-sourced.
    Best-effort: a telemetry failure must NEVER block the hook's primary job
    (the enforcement itself). Without this the objective event would depend
    on the model remembering to log it — the exact discipline-gap this
    plugin exists to close."""
    try:
        mod = _fdt()
        if mod:
            mod.log_event(event, payload, cwd=cwd)
    except Exception:
        pass


def write_json_atomic(path, obj):
    """Identico a fd-telemetry.write_json_atomic (writer standalone)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


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


def scan_jsonl(path, sub, since):
    """Scan INCREMENTALE di un JSONL: aggiorna `sub` in place (off in BYTE,
    totali cumulativi out/inp, last_ts per l'inferenza posizionale; i
    contatori sentinella n_rec/n_usage/n_ts solo se già presenti in `sub` —
    la sentinella di schema giudica il main, non i file agente). Il vecchio
    sum_file rileggeva l'intero JSONL a ogni Stop → costo quadratico sui
    task lunghi (review duale 2026-07-10). Lettura in binario e offset
    avanzato SOLO a fine riga completa: una riga parziale (transcript in
    scrittura) viene ripresa al giro dopo — un indicatore live può
    permettersi di perderla, l'enforcement no."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < int(sub.get("off", 0)):  # transcript ruotato/troncato
        for k in ("off", "out", "inp", "cr", "cc", "n_rec", "n_usage", "n_ts"):
            if k in sub:
                sub[k] = 0
        sub["last_ts"] = None
        if "rw" in sub:
            sub["rw"] = {"last": None, "touch": {}, "bouts": {}}
    if size <= int(sub.get("off", 0)):
        return
    try:
        with open(path, "rb") as fh:
            fh.seek(int(sub.get("off", 0)))
            data = fh.read()
    except OSError:
        return
    chunks = data.split(b"\n")
    tail = chunks.pop()  # riga possibilmente incompleta: non consumarla
    sub["off"] = int(sub.get("off", 0)) + len(data) - len(tail)
    last_ts = parse_ts(sub.get("last_ts"))
    sentinel = "n_rec" in sub
    for raw in chunks:
        line = raw.decode(errors="replace").strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if sentinel:
            sub["n_rec"] += 1
        raw_ts = parse_ts(rec.get("timestamp"))
        if raw_ts and sentinel:
            sub["n_ts"] += 1
        ts = raw_ts or last_ts  # inferenza posizionale
        if ts:
            last_ts = ts
        pre_since = since is not None and (ts is None or ts < since)
        if pre_since and (not sentinel or sub["n_usage"]):
            continue  # prefisso storico: al main serve solo per la sentinella
        usages = list(find_usage(rec))
        if usages and sentinel:
            sub["n_usage"] += 1
        if pre_since:
            continue
        # Rework (solo lo state del MAIN porta "rw"; i file agente Workflow
        # restano fuori in v1 — la metrica misura il main loop): fold dei
        # write-tool post-since. Helper single-sourced in fd-telemetry;
        # modulo assente → il rework degrada, l'enforcement no.
        rw = sub.get("rw")
        if rw is not None:
            mod = _fdt()
            if mod:
                for name, tin in mod.find_tool_uses(rec):
                    mod.rework_update(rw, name, tin)
        for usage in usages:
            sub["out"] += usage.get("output_tokens") or 0
            sub["inp"] += (usage.get("input_tokens") or 0) + \
                          (usage.get("cache_creation_input_tokens") or 0)
            # Componenti pure per il costo in eq (misura, non enforcement:
            # cache_read = ~72% del costo reale ma resta fuori da inp/out —
            # vedi EQ_MULT in fd-telemetry.py). Stato di versioni precedenti
            # senza le chiavi: si parte da 0 dall'offset corrente.
            sub["cr"] = (sub.get("cr") or 0) + \
                (usage.get("cache_read_input_tokens") or 0)
            sub["cc"] = (sub.get("cc") or 0) + \
                (usage.get("cache_creation_input_tokens") or 0)
    sub["last_ts"] = last_ts.isoformat() if last_ts else None


def scan_workflow_agents(transcript, since, state):
    """Token degli agenti del Workflow tool: file propri sotto
    <sessiondir>/subagents/workflows/, mai aggregati nel main (vedi docstring
    di modulo). Stessa disciplina del main: scan incrementale per file
    (offset nella mappa state['wf']), filtro since per record — ogni record
    agente porta il proprio timestamp. Totali riportati anche in
    state['wf_out']/['wf_inp'] così budget-close li legge senza rifare lo scan."""
    wf = state.setdefault("wf", {})
    sdir = transcript.with_suffix("")  # <sessiondir> = transcript senza .jsonl
    try:
        agent_files = sorted(sdir.glob("subagents/workflows/wf_*/agent-*.jsonl"))
    except OSError:
        agent_files = []
    for path in agent_files:
        key = f"{path.parent.name}/{path.name}"
        sub = wf.setdefault(key, {"off": 0, "out": 0, "inp": 0, "last_ts": None})
        scan_jsonl(path, sub, since)
    state["wf_out"] = sum(s.get("out") or 0 for s in wf.values())
    state["wf_inp"] = sum(s.get("inp") or 0 for s in wf.values())
    state["wf_cr"] = sum(s.get("cr") or 0 for s in wf.values())
    state["wf_cc"] = sum(s.get("cc") or 0 for s in wf.values())
    return state["wf_out"], state["wf_inp"]


def sum_session_incremental(transcript, since, state_file, declared_iso,
                            sid=None):
    """Totali della sessione dopo `since`: main transcript + agenti Workflow.
    Lo stato (chiavato su declared_at: budget nuovo → rescan) vive in un file
    accanto al budget; i contatori della sentinella restano cumulativi
    sull'intero main (il primo giro parte da offset 0)."""
    state = {"declared": declared_iso, "path": str(transcript), "off": 0,
             "out": 0, "inp": 0, "cr": 0, "cc": 0,
             "n_rec": 0, "n_usage": 0, "n_ts": 0,
             "last_ts": None, "rw": {"last": None, "touch": {}, "bouts": {}}}
    if state_file.is_file():
        try:
            prev = json.loads(state_file.read_text())
            if (prev.get("declared") == declared_iso
                    and prev.get("path") == str(transcript)):
                state = prev
                # state scritto da una versione senza rework: si parte da qui
                state.setdefault("rw", {"last": None, "touch": {}, "bouts": {}})
            elif prev.get("declared") == declared_iso and prev.get("sid"):
                # Reset per transcript diverso (seconda sessione stesso cwd):
                # il sid del PRIMO Stop dopo declared_at resta — è il più
                # vicino alla sessione che ha aperto il budget; senza questo
                # budget-close attribuiva il task_close all'ultima sessione
                # che ha stoppato (review 1.35.1).
                state["sid"] = prev["sid"]
        except (json.JSONDecodeError, OSError):
            pass
    if sid and not state.get("sid"):
        # session_id nello state: budget-close lo propaga al task_close così
        # il report può fare il join hint→esito per sessione.
        state["sid"] = str(sid)
    scan_jsonl(transcript, state, since)
    wf_out, wf_inp = scan_workflow_agents(transcript, since, state)
    try:
        write_json_atomic(state_file, state)
    except OSError:
        pass  # stato non persistito: il prossimo giro riparte dal vecchio offset
    return (state["out"] + wf_out, state["inp"] + wf_inp,
            (state["n_rec"], state["n_usage"], state["n_ts"]), state)


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    if data.get("stop_hook_active"):
        return
    cwd = data.get("cwd") or os.getcwd()
    # Slug: identico a cwd_slug() in fd-telemetry.py (canonico + hash)
    s = str(cwd).replace("\\", "/")
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
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
        write_json_atomic(bfile, budget)
        return

    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return
    # Main transcript (usage Agent tool incluso via toolUseResult) + file
    # agente del Workflow tool: vedi docstring di modulo per l'attribuzione.
    state_file = bfile.with_name(bfile.stem + ".state.json")
    actual_out, actual_in, (n_rec, n_usage, n_ts), state = \
        sum_session_incremental(
            Path(transcript), declared, state_file, budget.get("declared_at"),
            sid=data.get("session_id"))
    # Rework del task finora (None se zero write o modulo assente).
    mod = _fdt()
    rw_stats = mod.rework_stats(state.get("rw")) if mod else None
    # Costo in eq del task finora (misura in fase di taratura, MAI enforcement:
    # le soglie 2×/3× restano sui token dichiarati — vedi EQ_MULT).
    actual_eq = None
    if mod and hasattr(mod, "eq_tokens"):
        cr = (state.get("cr") or 0) + (state.get("wf_cr") or 0)
        cc = (state.get("cc") or 0) + (state.get("wf_cc") or 0)
        actual_eq = mod.eq_tokens(max(actual_in - cc, 0), actual_out, cr, cc)

    # Sentinella di schema: molti record validi ma zero usage o zero timestamp
    # riconosciuti = formato transcript probabilmente cambiato. I conteggi
    # sarebbero zeri: enforcement mai attivato, telemetria falsata — in
    # silenzio. Fallire rumorosamente (una volta sola) e NON enforcare su
    # numeri inaffidabili.
    if n_rec >= SENTINEL_MIN_RECORDS and (n_usage == 0 or n_ts == 0):
        missing = "usage" if n_usage == 0 else "timestamp"
        if not budget.get("schema_warned"):
            budget["schema_warned"] = True
            write_json_atomic(bfile, budget)
            log_telemetry("schema_anomaly", {
                "source": "stop-budget-check", "missing": missing,
                "n_records": n_rec, "transcript": str(transcript),
                "auto": True}, cwd)
            print(json.dumps({"systemMessage": (
                f"✕ FABLE-DIRECTOR schema sentinel — {n_rec} transcript "
                f"records but zero recognized '{missing}' fields: the Claude "
                f"Code transcript format has likely changed.\n"
                f"Consequence: budget ENFORCEMENT IS OFF (counts would read "
                f"0). Tell the user and update the fable-director plugin."
            )}, ensure_ascii=False))
        return

    out_bust = actual_out >= 3 * exp_out
    in_bust = exp_in > 0 and actual_in >= 3 * exp_in

    if not (out_bust or in_bust):
        warn = actual_out >= 2 * exp_out or (exp_in > 0 and actual_in >= 2 * exp_in)
        if warn and not budget.get("warned"):
            budget["warned"] = True
            write_json_atomic(bfile, budget)
            # Riga rework al checkpoint: il fatto sempre (se c'è), la diagnosi
            # solo sopra soglia — 1-2 riaperture non provano niente, un file
            # riaperto 2+ volte o 4+ totali sì (REWORK_DIAG_MIN in fd-telemetry).
            rework_note = ""
            if rw_stats and rw_stats.get("reopens"):
                w = (rw_stats.get("worst") or [["?", 0]])[0]
                rework_note = (
                    f"\nRework so far: {rw_stats['reopens']} reopens across "
                    f"{rw_stats['files_reopened']} files (worst: {w[0]} ×{w[1]}).")
                diag_min = getattr(mod, "REWORK_DIAG_MIN", 4)
                if w[1] >= 2 or rw_stats["reopens"] >= diag_min:
                    rework_note += (
                        " Information keeps landing AFTER files are written: "
                        "the task context was incomplete — complete the "
                        "picture (files/state/schema in the spec) before "
                        "resuming; it costs less than another reopen cycle.")
            print(json.dumps({"decision": "block", "reason": (
                f"⚠ FABLE-DIRECTOR 2× checkpoint — actual spend (output "
                f"{actual_out}, fresh input {actual_in}) passed TWICE the "
                f"pre-budget for task '{budget.get('task')}'."
                f"{rework_note}\n"
                f"This is not the 3× block: reassess the route NOW — "
                f"switching here costs less than a post-mortem at 3×.\n"
                f"If you switch: fd-telemetry.py log reversal --json "
                f"'{{\"from\":\"...\",\"to\":\"...\",\"at\":\"2x-checkpoint\"}}'\n"
                f"If the route still holds, continue and close the turn "
                f"normally."
            )}))
        return

    budget["status"] = "flagged"
    budget["actual_output_tokens"] = actual_out
    budget["actual_input_tokens"] = actual_in
    if actual_eq is not None:
        budget["actual_eq_tokens"] = actual_eq
    budget["flagged_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_json_atomic(bfile, budget)

    dim = "output" if out_bust else "input"
    actual = actual_out if out_bust else actual_in
    expected = exp_out if out_bust else exp_in
    ratio = round(actual / expected, 1) if expected else None
    # Deterministic capture of the objective bust: the model no longer has to
    # remember to log it (below, step-3 removed). Runs exactly once — the next
    # turn returns early on status != "open".
    flag_payload = {"task": budget.get("task"), "ratio": ratio, "dim": dim,
                    "actual": actual, "expected": expected, "auto": True}
    if actual_eq is not None:
        flag_payload["actual_eq"] = actual_eq  # dato di taratura per le soglie eq
    # Il rework viaggia con lo sfondamento: hindsight lo ripesca sul cwd e il
    # post-mortem parte già sapendo SE l'informazione arrivava dopo la scrittura.
    if rw_stats and rw_stats.get("reopens"):
        flag_payload["reopens"] = rw_stats["reopens"]
        worst = rw_stats.get("worst") or []
        if worst:
            flag_payload["worst_file"] = worst[0][0]
    log_telemetry("budget_flag", flag_payload, cwd)

    reason = (
        f"✕ FABLE-DIRECTOR 3× block — actual {dim} {actual} tokens ≥ 3× the "
        f"declared estimate ({expected}) for task '{budget.get('task')}'.\n"
        f"Closure is blocked until the mini post-mortem is written (the bust "
        f"itself is already logged: budget_flag event, automatic):\n"
        f"(1) which pre-budget assumption broke?\n"
        f"(2) write the [candidate] entry in ~/.claude/delega-playbook.md "
        f"(root cause → heuristic)\n"
        f"(3) fd-telemetry.py budget-close --outcome flagged  (scripts in "
        f"<plugin fable-director>/scripts/)"
    )
    print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
