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
        return (f"  {day}  BUST {r} ({dim}: stimati {exp}, spesi {act})"
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
    payload = {}
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready:
                payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    return (payload.get("cwd")
            or os.environ.get("CLAUDE_PROJECT_DIR")
            or os.getcwd())


def main():
    cwd = hook_cwd()
    db = Path.home() / ".claude" / "fable-director" / "telemetry.db"
    if not db.is_file():
        return
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
    rows = cwd_slug_match(con, cwd)
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
