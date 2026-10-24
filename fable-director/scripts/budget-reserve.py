#!/usr/bin/env python3
"""Riserva del budget per le deleghe parallele (1.60.0).

Il buco che chiude. Lo Stop hook confronta il consumo con la stima a FINE
turno: N agenti lanciati nello stesso messaggio spendono tutti prima che un
controllo possa scattare — con i contatori a consuntivo 30 esecuzioni
concorrenti su 30 sforano (Codex CLI, rollout budget; flynn/budget riserva
prima e salda dopo). Qui il gate IMPEGNA la stima di ogni delega nel budget
aperto prima che parta, e lo Stop hook salda a fine giro con il consumo vero.

Modello (per budget file, chiave `reserve`):
- entries: una voce per delega permessa {id, tool, n, out, inp, at} —
  quota = stima / --agents (ancora empirica 20k out / 17k in per agente
  quando --agents manca, mai sopra la stima); un Workflow vale N voci in una;
- mark_out/mark_inp: consuntivo (state file dello Stop hook) all'ultimo saldo;
- impegnato = marca + max(consumo oltre la marca, riserva aperta) + questa
  delega: il consumo gia' misurato e la riserva non si sommano, perche' la
  riserva e' la stima dello stesso lavoro che il consumo misurera';
- impegnato ≥ 2× la stima → avviso una volta per giro; ≥ 3× → deny (regola
  `reservation`): il tetto che lo Stop hook farebbe scattare a spesa fatta.

Saldo (Stop hook, a ogni fine turno): il consumo oltre la marca paga le voci
dalla piu' vecchia; restano solo le piu' recenti quante sono gli agenti ancora
in volo nel registro del misuratore (SubagentStart/Stop), ridotte di quanto
gia' pagato; la marca avanza al consuntivo; evento `reservation_settle`
(riservato contro misurato: dato di taratura per le stime).

Scritture concorrenti: N gate partono nello stesso istante per N deleghe
nello stesso messaggio — read-modify-write sotto flock dove esiste (Windows:
solo rinomina atomica, un aggiornamento puo' perdersi). Fail-open ovunque:
un errore qui non nega mai una delega e non blocca mai uno Stop.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl  # assente su Windows: sotto si degrada a sola rinomina atomica
except ImportError:
    fcntl = None

# Stesse ancore del fan-out in fd-telemetry.py budget-open (playbook confermata):
# ~20k output e ~17k input di cold start PER AGENTE, prima dei file letti.
ANCHOR_OUT = 20_000
ANCHOR_IN = 17_000
WARN_RATIO = 2.0   # come il checkpoint 2× dello Stop hook
DENY_RATIO = 3.0   # come il blocco 3× dello Stop hook
ENTRY_TTL_S = 86400  # una voce piu' vecchia del budget stesso (24h) non impegna piu'
# Deleghe sequenziali dentro UN turno (sessione notturna `claude -p` = un turno
# solo): senza rilascio le voci di agenti gia' finiti impegnerebbero fino allo
# Stop e il gate negherebbe la decima delega da 5k reali per 200k riservati.
# Una voce piu' vecchia di GRACE_S, oltre gli agenti in volo, si rilascia; le
# voci piu' giovani restano sempre (N gate dello stesso messaggio partono prima
# che SubagentStart li registri in volo).
GRACE_S = 120
MAX_ENTRIES = 500


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def dot(n):
    """1234567 → '1.234.567' (separatore delle migliaia come nella statusline)."""
    return f"{float(n):,.0f}".replace(",", ".")


def fmt_k(n):
    n = float(n)
    if n >= 1e6:
        return (f"{n / 1e6:.1f}M").replace(".0M", "M")
    return f"{n / 1e3:.0f}k"


def locked_update(bfile, mutate):
    """Read-modify-write del budget file sotto lock. `mutate(budget)` ritorna
    (result, changed): con changed falso il file non viene riscritto. Ritorna
    (result, budget); (None, None) se il file manca o non si legge."""
    bfile = Path(bfile)
    lock = bfile.with_name(bfile.stem + ".lock")
    fh = None
    try:
        if fcntl is not None:
            fh = open(lock, "a+")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            budget = json.loads(bfile.read_text())
        except (OSError, ValueError):
            return None, None
        if not isinstance(budget, dict):
            return None, None
        result, changed = mutate(budget)
        if changed:
            tmp = bfile.with_name(f"{bfile.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(budget, ensure_ascii=False, indent=1))
            os.replace(tmp, bfile)
        return result, budget
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def state_actual(bfile, declared_at):
    """Consuntivo (out, inp) dallo state file dello Stop hook: main + agenti
    Workflow, stessa contabilita' dell'enforcement. (0, 0) se lo state manca
    o appartiene a un budget precedente (chiave declared_at)."""
    sfile = Path(bfile).with_name(Path(bfile).stem + ".state.json")
    try:
        st = json.loads(sfile.read_text())
    except (OSError, ValueError):
        return 0, 0
    if not isinstance(st, dict) or st.get("declared") != declared_at:
        return 0, 0
    return (int(st.get("out") or 0) + int(st.get("wf_out") or 0),
            int(st.get("inp") or 0) + int(st.get("wf_inp") or 0))


def share(budget):
    """(out, inp) impegnati da UN agente: stima / --agents; senza --agents
    l'ancora empirica, mai sopra la stima. L'input conta solo se dichiarato
    (lo Stop hook non lo enforce altrimenti)."""
    exp_out = int(budget.get("expected_output_tokens") or 0)
    exp_in = int(budget.get("expected_input_tokens") or 0)
    n = int(budget.get("agents") or 0)
    if n > 0:
        return -(-exp_out // n), (-(-exp_in // n) if exp_in else 0)
    out = min(ANCHOR_OUT, exp_out) if exp_out else ANCHOR_OUT
    inp = min(ANCHOR_IN, exp_in) if exp_in else 0
    return out, inp


def fanout(budget, tool_name, n_done=0, script=""):
    """Agenti che questa chiamata fa partire: 1 per Agent/Task; per Workflow
    il fan-out dichiarato (--agents) meno quelli gia' nel journal al resume,
    altrimenti le chiamate agent( nello script (almeno 1)."""
    if tool_name != "Workflow":
        return 1
    n = int(budget.get("agents") or 0)
    if n > 0:
        return max(n - int(n_done or 0), 0)
    return max(len(re.findall(r"\bagent\s*\(", script or "")), 1)


def _block(budget):
    res = budget.get("reserve")
    if not isinstance(res, dict):
        res = {}
        budget["reserve"] = res
    res.setdefault("entries", [])
    res.setdefault("mark_out", 0)
    res.setdefault("mark_inp", 0)
    res.setdefault("warned", False)
    return res


def _prune(res, now=None):
    now = now or time.time()
    keep = []
    for e in res["entries"]:
        t = parse_ts(e.get("at"))
        if t is not None and now - t.timestamp() > ENTRY_TTL_S:
            continue
        keep.append(e)
    res["entries"] = keep[-MAX_ENTRIES:]


def _release_finished(res, n_inflight, now=None):
    """Rilascia le voci di deleghe gia' finite: tiene le voci piu' giovani di
    GRACE_S e, fra le piu' vecchie, solo le piu' recenti quanti sono gli agenti
    in volo. Il loro consumo vero entra nel consuntivo (state file) quando lo
    scan lo misura; fino ad allora l'impegno e' quello di prima della 1.60."""
    now = now or time.time()
    young, old = [], []
    for e in res["entries"]:
        t = parse_ts(e.get("at"))
        (young if t is not None and now - t.timestamp() < GRACE_S else old).append(e)
    kept_old = old[len(old) - n_inflight:] if n_inflight > 0 else []
    if len(kept_old) == len(old):
        return False
    res["entries"] = [e for e in res["entries"] if e in young or e in kept_old]
    return True


def committed(budget, bfile, add_out=0, add_inp=0):
    """Vista impegnata del budget: marca + max(consumo oltre la marca, riserva
    aperta) + l'aggiunta. Ritorna un dict con totali, rapporti e dimensione
    dominante — la stessa aritmetica per gate, Stop e promemoria."""
    res = _block(budget)
    actual_out, actual_in = state_actual(bfile, budget.get("declared_at"))
    d_out = max(actual_out - int(res["mark_out"] or 0), 0)
    d_in = max(actual_in - int(res["mark_inp"] or 0), 0)
    r_out = sum(int(e.get("out") or 0) for e in res["entries"])
    r_in = sum(int(e.get("inp") or 0) for e in res["entries"])
    c_out = int(res["mark_out"] or 0) + max(d_out, r_out) + int(add_out)
    c_in = int(res["mark_inp"] or 0) + max(d_in, r_in) + int(add_inp)
    exp_out = int(budget.get("expected_output_tokens") or 0)
    exp_in = int(budget.get("expected_input_tokens") or 0)
    ratio_out = c_out / exp_out if exp_out > 0 else 0.0
    ratio_in = c_in / exp_in if exp_in > 0 else 0.0
    dim = "input" if ratio_in > ratio_out else "output"
    return {"out": c_out, "inp": c_in, "reserved_out": r_out, "reserved_inp": r_in,
            "actual_out": actual_out, "actual_in": actual_in,
            "exp_out": exp_out, "exp_in": exp_in,
            "ratio": max(ratio_out, ratio_in), "dim": dim,
            "n": len(res["entries"])}


def reserve(bfile, entry_id, tool_name, n, task_fallback=None, n_inflight=None):
    """Impegna la quota di questa delega nel budget aperto. Ritorna un dict
    {decision: allow|deny, warn, ratio, ...} oppure None se non c'e' niente
    da riservare (file assente, budget non aperto, n = 0, stima assente).
    Con n_inflight (registro del misuratore) rilascia prima le voci di
    deleghe gia' finite (vedi GRACE_S)."""
    n = int(n or 0)

    def mutate(budget):
        if budget.get("status") != "open":
            return None, False
        exp_out = int(budget.get("expected_output_tokens") or 0)
        if exp_out <= 0 or n <= 0:
            return None, False
        res = _block(budget)
        _prune(res)
        if n_inflight is not None:
            _release_finished(res, max(int(n_inflight), 0))
        s_out, s_in = share(budget)
        add_out, add_in = s_out * n, s_in * n
        view = committed(budget, bfile, add_out, add_in)
        view["task"] = budget.get("task") or task_fallback
        view["add_out"], view["add_inp"], view["n_new"] = add_out, add_in, n
        if view["ratio"] >= DENY_RATIO:
            view["decision"] = "deny"
            return view, False
        res["entries"].append({"id": str(entry_id), "tool": str(tool_name), "n": n,
                               "out": add_out, "inp": add_in, "at": now_iso()})
        view["n"] = len(res["entries"])
        view["decision"] = "allow"
        view["warn"] = bool(view["ratio"] >= WARN_RATIO and not res.get("warned"))
        if view["warn"]:
            res["warned"] = True
        return view, True

    result, _ = locked_update(bfile, mutate)
    return result


def inflight_count(session_id):
    """Agenti ancora in volo per questa sessione secondo il misuratore
    SubagentStart/Stop (subagent-meter.py); 0 se il registro manca."""
    sid = str(session_id or "nosession")
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in sid)[:120]
    path = Path.home() / ".claude" / "fable-director" / "subagents" / f"{safe}.json"
    try:
        st = json.loads(path.read_text())
        return len(st.get("inflight") or {})
    except (OSError, ValueError, AttributeError):
        return 0


def settle(bfile, actual_out, actual_in, n_inflight):
    """Saldo a fine giro: il consumo oltre la marca paga le voci dalla piu'
    vecchia; restano le piu' recenti quante sono gli agenti in volo (ridotte
    di quanto pagato, mai a zero); la marca avanza; l'avviso 2× si riarma.
    Ritorna (summary, budget): summary None se non c'era niente da saldare."""
    actual_out, actual_in = int(actual_out or 0), int(actual_in or 0)
    n_inflight = max(int(n_inflight or 0), 0)

    def mutate(budget):
        if budget.get("status") != "open" or not isinstance(budget.get("reserve"), dict):
            return None, False
        res = _block(budget)
        _prune(res)
        entries = res["entries"]
        d_out = max(actual_out - int(res["mark_out"] or 0), 0)
        d_in = max(actual_in - int(res["mark_inp"] or 0), 0)
        summary = {"n": len(entries),
                   "reserved_out": sum(int(e.get("out") or 0) for e in entries),
                   "reserved_inp": sum(int(e.get("inp") or 0) for e in entries),
                   "actual_out": d_out, "actual_inp": d_in}
        pay_out, pay_in = d_out, d_in
        for e in entries:
            take = min(int(e.get("out") or 0), pay_out)
            e["out"] = int(e.get("out") or 0) - take
            pay_out -= take
            take = min(int(e.get("inp") or 0), pay_in)
            e["inp"] = int(e.get("inp") or 0) - take
            pay_in -= take
        kept = entries[len(entries) - n_inflight:] if n_inflight else []
        kept = [e for e in kept if int(e.get("out") or 0) > 0 or int(e.get("inp") or 0) > 0]
        res["entries"] = kept
        res["mark_out"] = max(actual_out, int(res["mark_out"] or 0))
        res["mark_inp"] = max(actual_in, int(res["mark_inp"] or 0))
        res["warned"] = False
        res["settled_at"] = now_iso()
        summary["kept"] = len(kept)
        return (summary if summary["n"] else None), True

    return locked_update(bfile, mutate)


def deny_text(view):
    """Testo del deny (regola `reservation`): le deleghe gia' in volo, il
    totale con questa, il tetto che verrebbe superato e le due uscite."""
    dim = view["dim"]
    exp = view["exp_in"] if dim == "input" else view["exp_out"]
    have = view["reserved_inp"] if dim == "input" else view["reserved_out"]
    tot = view["inp"] if dim == "input" else view["out"]
    unit = "fresh input" if dim == "input" else "output"
    return (f"✕ FABLE-DIRECTOR reservation — {view['n']} delegations already in flight "
            f"commit {dot(have)} {unit} tokens of the {dot(exp)} declared for task "
            f"'{view.get('task')}'; this call would take the commitment to {dot(tot)} "
            f"({view['ratio']:.1f}×), past the 3× ceiling before any Stop check could fire.\n"
            f"Wait for the in-flight delegations to settle (the Stop hook settles "
            f"reservations at the end of the turn), or close this budget and reopen it "
            f"with the real fan-out (--agents N) and a revised estimate.")


def warn_text(view):
    dim = view["dim"]
    exp = view["exp_in"] if dim == "input" else view["exp_out"]
    tot = view["inp"] if dim == "input" else view["out"]
    unit = "fresh input" if dim == "input" else "output"
    return (f"FD ⚠ reservation — {view['n']} delegations in flight commit {dot(tot)} {unit} "
            f"tokens of the {dot(exp)} declared for task '{view.get('task')}' "
            f"({view['ratio']:.1f}×): the fan-out runs past the estimate before any Stop "
            f"check; at 3× the gate denies. Reassess the fan-out now, or close and reopen "
            f"the budget with the real figure. Delegation allowed.")


def reminder_line(budget, actual_out, actual_in, now=None):
    """Una riga per il modello dentro un turno lungo: consumo, rapporto,
    riserva in volo e margine prima del checkpoint 2×."""
    now = now or datetime.now(timezone.utc)
    declared = parse_ts(budget.get("declared_at"))
    mins = int((now - declared).total_seconds() // 60) if declared else 0
    exp_out = int(budget.get("expected_output_tokens") or 0)
    exp_in = int(budget.get("expected_input_tokens") or 0)
    res = budget.get("reserve") if isinstance(budget.get("reserve"), dict) else {}
    entries = res.get("entries") or []
    r_out = sum(int(e.get("out") or 0) for e in entries)
    r_in = sum(int(e.get("inp") or 0) for e in entries)
    mark_out, mark_in = int(res.get("mark_out") or 0), int(res.get("mark_inp") or 0)
    c_out = mark_out + max(max(actual_out - mark_out, 0), r_out)
    c_in = mark_in + max(max(actual_in - mark_in, 0), r_in)
    parts = [f"output {fmt_k(actual_out)} of {fmt_k(exp_out)} "
             f"({(actual_out / exp_out if exp_out else 0):.1f}×)"]
    if exp_in:
        parts.append(f"fresh input {fmt_k(actual_in)} of {fmt_k(exp_in)} "
                     f"({actual_in / exp_in:.1f}×)")
    line = f"[fable-director] budget '{budget.get('task')}' open {mins} min: " + ", ".join(parts)
    if entries:
        n = sum(int(e.get("n") or 1) for e in entries)
        line += (f"; {n} delegation{'s' if n != 1 else ''} in flight reserving "
                 f"{fmt_k(r_out)} output")
    room = [f"{fmt_k(max(WARN_RATIO * exp_out - c_out, 0))} output"]
    if exp_in:
        room.append(f"{fmt_k(max(WARN_RATIO * exp_in - c_in, 0))} input")
    line += (f". Room before the 2× checkpoint: {', '.join(room)}. "
             f"Close it with budget-close when the task is verified.")
    return line
