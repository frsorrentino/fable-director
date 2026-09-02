#!/usr/bin/env python3
"""SessionStart: rimette in contesto gli sfondamenti gia' pagati su QUESTO cwd.

Il buco: `budget_flag` viene auto-scritto dallo Stop hook a 3×, con task, ratio
e dimensione. Poi resta nel DB e non lo rilegge nessuno. La telemetria del
2026-07 lo dimostra: "triage recensioni" ha sfondato 3 volte in 3 giorni sullo
stesso cwd (3.2× / 38.8× / 26.3×). Al 2° e al 3° tentativo il dato c'era gia'.
Registrare senza ripescare non e' memoria: e' un archivio.

Perche' SOLO budget_flag (+ reversal se c'e'):
gli eventi che il modello deve loggare a mano non atterrano — 1 reversal e 0
escalation in tutta la vita del DB, contro 4 budget_flag tutti "auto": true.
Si inietta cio' che un hook scrive deterministicamente, non cio' che il modello
si e' impegnato a scrivere.

Costo: paga solo dove c'e' evidenza. Nessun flag su questo cwd -> stampa nulla,
zero token. Tetto rigido MAX_LINES: la memoria che ripesca tutto ricrea la
context dilution che il resto del plugin combatte.

Fail-silent: mai disturbare l'avvio della sessione.
"""
import json
import os
import select
import sqlite3
import sys
from pathlib import Path

MAX_LINES = 5          # tetto rigido: sotto, e' un promemoria; sopra, e' zavorra
LOOKBACK_DAYS = 120    # oltre, lo stack tecnico e' cambiato e il dato mente


def cwd_slug_match(con, cwd):
    """Match esatto sul cwd. Niente prefix-match: un flag del parent non e'
    evidenza sul figlio (benchmarks/ != marketplace/).
    Sovra-preleva: la dedup a valle scarta righe e il tetto va applicato DOPO,
    altrimenti un doppione mangia uno degli slot."""
    return con.execute(
        "SELECT ts, event, payload FROM events "
        "WHERE cwd = ? AND event IN ('budget_flag', 'reversal') "
        "AND ts >= datetime('now', ?) "
        "ORDER BY ts DESC LIMIT ?",
        (str(cwd), f"-{LOOKBACK_DAYS} days", MAX_LINES * 6),
    ).fetchall()


def dedupe_key(event, payload):
    """Lo stesso sfondamento puo' comparire piu' volte nel DB (osservato:
    2026-07-08, riga identica x2). Deduplico la VISTA, mai il dato: il DB resta
    la storia integrale, qui conta non bruciare uno slot su un doppione."""
    try:
        p = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if event == "budget_flag":
        return ("budget_flag", p.get("task"), p.get("dim"), p.get("actual"))
    if event == "reversal":
        return ("reversal", p.get("from"), p.get("to"))
    return None


def fmt(ts, event, payload):
    try:
        p = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    day = str(ts)[:10]
    if event == "budget_flag":
        task = str(p.get("task", "?"))[:70]
        ratio = p.get("ratio")
        dim = p.get("dim", "?")
        exp, act = p.get("expected"), p.get("actual")
        r = f"{ratio:.1f}x" if isinstance(ratio, (int, float)) else "?"
        # Rework allo sfondamento (se lo Stop hook l'ha registrato): dice al
        # post-mortem SE il contesto arrivava dopo la scrittura — la causa
        # tipica dello sforamento non e' l'esecutore, e' lo spec incompleto.
        rew = ""
        if isinstance(p.get("reopens"), int) and p["reopens"] > 0:
            wf = p.get("worst_file")
            rew = (f", {p['reopens']} riaperture"
                   + (f" ({wf})" if wf else ""))
        return (f"  {day}  BUST {r} ({dim}: stimati {exp}, spesi {act}{rew})"
                f" — \"{task}\"")
    if event == "reversal":
        return (f"  {day}  REVERSAL {p.get('from', '?')} -> {p.get('to', '?')}"
                f" (a {p.get('at', '?')})")
    return None


def hook_cwd():
    """Il cwd arriva dal JSON su stdin, come per OGNI altro hook del plugin
    (perimeter-gate, stop-budget-check, pre-delegation-gate: `data["cwd"]`).
    Nessuno legge stdin prima di noi in session-kernel.sh, quindi il payload
    e' ancora integro.

    Guardia anti-blocco: stdin puo' essere un TTY (invocazione manuale) o
    restare aperto senza dati — leggerlo alla cieca appenderebbe l'avvio della
    sessione. select() con timeout corto: nel dubbio si degrada, non si blocca.
    Ordine: payload > CLAUDE_PROJECT_DIR > getcwd()."""
    payload = hook_payload()
    return (payload.get("cwd")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd())


_PAYLOAD = None


def hook_payload():
    """Payload stdin letto UNA volta (cwd, source, session_id)."""
    global _PAYLOAD
    if _PAYLOAD is not None:
        return _PAYLOAD
    payload = {}
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready:
                payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    _PAYLOAD = payload if isinstance(payload, dict) else {}
    return _PAYLOAD


def days_ago(ts):
    """'3 days ago' / 'yesterday' / 'today' da un timestamp ISO UTC."""
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        d = (datetime.now(timezone.utc) - t).days
    except (ValueError, TypeError):
        return None
    return "today" if d <= 0 else "yesterday" if d == 1 else f"{d} days ago"


RESUME_MIN_CONTEXT = 100_000


def human_gap(seconds):
    try:
        sec = int(seconds)
    except (TypeError, ValueError):
        return None
    if sec < 3600:
        return f"{sec // 60} min"
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def resume_cache_line(payload):
    """C2.2 (1.39): alla ripresa dopo una pausa il cache e' scaduto e il primo
    turno riscrive TUTTO il contesto (misurato 2026-08-11..09-01: 76% dei
    reset di cache sono riprese dopo >1h, re-cache mediano 280k token, ~20%
    del costo a tariffe 5.1). L'host (CC ≥2.1.251) passa staleness, contesto
    e costo stimato: qui diventano una riga di consiglio, mai un blocco. Campi
    assenti → silenzio."""
    if payload.get("source") not in ("resume", "fork"):
        return None
    if not payload.get("prompt_cache_likely_expired"):
        return None
    ctx = payload.get("context_tokens")
    if not isinstance(ctx, (int, float)) or ctx < RESUME_MIN_CONTEXT:
        return None
    gap = human_gap(payload.get("seconds_since_last_response"))
    usd = payload.get("estimated_cache_write_usd")
    cost = f" (~${usd:.2f} at list price)" if isinstance(usd, (int, float)) else ""
    return (f"FD ⚠ Resuming after {gap or 'a long pause'}: the prompt cache expired, "
            f"so the first turn re-caches ~{int(ctx):,} tokens{cost}. If the last task "
            f"closed at a verified boundary (tests green, commit landed), a fresh "
            f"session with a short distillate costs a fraction of that; if the work "
            f"is mid-task, continue — the reasoning in this context is load-bearing."
            ).replace(",", ".")


def inherited_budget_line(cwd):
    """C1.7: la sessione figlia (claude --bg / nuova-sessione) trova il budget
    aperto per la SUA cartella da un'altra sessione con --route bg-session."""
    try:
        import hashlib
        import re as _re
        s = str(cwd).replace("\\", "/")
        slug = (_re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
                + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
        bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
        b = json.loads(bfile.read_text(encoding="utf-8"))
        if b.get("status") != "open" or b.get("route") != "bg-session":
            return None
        return (f"FD ▷ Budget inherited from session {str(b.get('parent_session') or '?')[:8]}: "
                f"'{b.get('task')}' — expected output {b.get('expected_output_tokens')} tokens"
                + (f", verify: {b['verify']}" if b.get("verify") else "")
                + ". Close it here with budget-close when the work is done; the receipt names the parent.")
    except Exception:
        return None


def last_session_line(con, cwd, session_id=None):
    """E1 (1.39): 'Last session in this folder: 3 days ago, 212 turns; last
    task budget: closed fine (design-review), 5 days ago.' Dati: session_summary
    e task_close dello stesso cwd (match esatto). Nessuna sessione → None.
    Esclude la sessione corrente (una summary puo' esistere per un resume)."""
    row = None
    for r in con.execute(
            "SELECT ts, session_id, payload FROM events WHERE cwd = ? AND "
            "event = 'session_summary' ORDER BY ts DESC LIMIT 3", (str(cwd),)):
        if session_id and r[1] == session_id:
            continue
        row = r
        break
    if not row:
        return None
    ts, _, payload = row
    try:
        p = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        p = {}
    ago = days_ago(ts) or "?"
    turns = p.get("n_usage_records")
    line = f"Last session in this folder: {ago}"
    if turns:
        line += f", {turns} turns"
    tc = con.execute(
        "SELECT ts, payload FROM events WHERE cwd = ? AND event = 'task_close' "
        "ORDER BY ts DESC LIMIT 1", (str(cwd),)).fetchone()
    if tc:
        try:
            t = json.loads(tc[1])
        except (json.JSONDecodeError, TypeError):
            t = {}
        word = {"ok": "closed fine", "flagged": "blew the budget",
                "abandoned": "abandoned"}.get(t.get("outcome"), t.get("outcome") or "?")
        typ = f" ({t['type']})" if t.get("type") else ""
        line += f"; last task budget: {word}{typ}, {days_ago(tc[0]) or '?'}"
    else:
        line += "; no task budget was used here"
    return line + "."


CLAUDE_MD_HEAVY_BYTES = 12_000  # ~3k token pagati OGNI turno, tutta la sessione


def claude_md_hygiene(cwd):
    """Una riga se il CLAUDE.md del progetto è pesante: da CC 2.1.206 /doctor
    sa proporre il taglio del contenuto derivabile dal codebase. Check
    statico (dimensione), mai bloccante, tace sotto soglia."""
    try:
        f = Path(cwd) / "CLAUDE.md"
        size = f.stat().st_size
        if size >= CLAUDE_MD_HEAVY_BYTES:
            print(f"\nCLAUDE.md pesa {size // 1024}KB (~{size // 4:,} token "
                  f"pagati a OGNI turno): /doctor sa proporre il taglio del "
                  f"contenuto che Claude può derivare dal codebase.")
    except OSError:
        pass


def main():
    cwd = hook_cwd()
    claude_md_hygiene(cwd)
    db = Path.home() / ".claude" / "fable-director" / "telemetry.db"
    pl = hook_payload()
    rc = resume_cache_line(pl)
    if (pl.get("source") or "startup") in ("startup", "resume"):
        ib = inherited_budget_line(cwd)
        if ib:
            print("\n" + ib)
    if not db.is_file():
        if rc:
            print("\n" + rc)
        return
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    rows = cwd_slug_match(con, cwd)
    if rc:
        print("\n" + rc)
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "fd_telemetry", Path(__file__).with_name("fd-telemetry.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.log_event("resume_stale", {
                "seconds_since_last_response": pl.get("seconds_since_last_response"),
                "context_tokens": pl.get("context_tokens"),
                "estimated_cache_write_usd": pl.get("estimated_cache_write_usd"),
                "source": pl.get("source"), "auto": True},
                session_id=pl.get("session_id"), cwd=cwd)
        except Exception:
            pass
    # E1: solo all'avvio vero (startup/resume); compact/clear/fork sono la
    # stessa sessione.
    if (pl.get("source") or "startup") in ("startup", "resume"):
        try:
            ls = last_session_line(con, cwd, pl.get("session_id"))
        except Exception:
            ls = None
        if ls:
            print("\n" + ls)
    con.close()
    lines, seen = [], set()
    for ts, event, payload in rows:          # gia' ORDER BY ts DESC: tengo il piu' recente
        key = dedupe_key(event, payload)
        if key is None or key in seen:
            continue
        line = fmt(ts, event, payload)
        if not line:
            continue
        seen.add(key)
        lines.append(line)
        if len(lines) >= MAX_LINES:
            break
    if not lines:
        return
    print("\nHINDSIGHT — questo cwd ha gia' pagato questi sfondamenti "
          "(auto-registrati dallo Stop hook, non stime):")
    print("\n".join(lines))
    print("Se il task di oggi somiglia a uno di questi, il consuntivo qui "
          "sopra vale piu' della stima di oggi: e' misurato, non intuito. "
          "Non e' un divieto — e' il prezzo gia' pagato una volta.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
