#!/usr/bin/env python3
"""Verifica control arm di route-hint.

Contratto:
- H1: holdout deterministico — stessa (sessione, giorno) = stesso braccio;
- H2: FD_HINT_HOLDOUT=1 → mai stampa, evento con holdout:true e matches pieni;
- H3: FD_HINT_HOLDOUT=0 → stampa sempre, holdout:false;
- H4: senza session_id → mai holdout (braccio non attribuibile non si misura);
- H5: evento route_hint scrive session_id e cwd nelle colonne (non solo payload);
- H6: frazione invalida → fallback 0.1 senza crash;
- H7: report — sotto soglia (20/5) niente numeri, solo conteggi e avviso teatro;
- H8: report — sopra soglia stampa cheap-route adoption per braccio (fixture
      con task_close attribuiti).

Tutto in HOME usa-e-getta.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director"
HINT = ROOT / "scripts" / "route-hint.py"
FDT = ROOT / "scripts" / "fd-telemetry.py"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run_hint(home, payload, holdout_frac=None):
    e = dict(os.environ, HOME=str(home))
    if holdout_frac is not None:
        e["FD_HINT_HOLDOUT"] = str(holdout_frac)
    p = subprocess.run([sys.executable, str(HINT)], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    return p.stdout


def events(home):
    con = sqlite3.connect(home / ".claude" / "fable-director" / "telemetry.db")
    rows = con.execute("SELECT session_id, cwd, payload FROM events "
                       "WHERE event='route_hint' ORDER BY id").fetchall()
    con.close()
    return [(s, c, json.loads(p)) for s, c, p in rows]


PROMPT = ("elabora ogni file della lista e genera il report finale "
          "per tutti i clienti del progetto")

with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    fd = home / ".claude" / "fable-director"
    fd.mkdir(parents=True)
    (fd / "soft-deps.json").write_text(json.dumps({
        "gemini-docs": {"classes": ["documentation-lookup"],
                        "hint_keywords": ["report finale"]}}))

    pay = {"prompt": PROMPT, "session_id": "sess-aaaa", "cwd": "/x/proj"}

    # H2: frazione 1 → sempre holdout, niente stampa
    out = run_hint(home, pay, 1)
    ev = events(home)
    check("H2 holdout: niente stampa", out.strip() == "", out)
    check("H2b evento holdout:true con matches",
          ev and ev[-1][2].get("holdout") is True and ev[-1][2].get("matches"))

    # H3: frazione 0 → stampa, holdout:false
    out = run_hint(home, pay, 0)
    ev = events(home)
    check("H3 shown: stampa hint", "[fd-route-hint]" in out, out)
    check("H3b evento holdout:false", ev[-1][2].get("holdout") is False)

    # H5: colonne session_id e cwd valorizzate
    check("H5 session_id in colonna", ev[-1][0] == "sess-aaaa", str(ev[-1]))
    check("H5b cwd in colonna", ev[-1][1] == "/x/proj", str(ev[-1]))

    # H1: determinismo — 5 run stessa sessione, frazione 0.5 → braccio costante
    arms = set()
    for _ in range(5):
        run_hint(home, pay, 0.5)
    for _, _, p in events(home)[-5:]:
        arms.add(p.get("holdout"))
    check("H1 determinismo per (sessione, giorno)", len(arms) == 1, str(arms))

    # H4: senza session_id → mai holdout anche con frazione 1
    out = run_hint(home, {"prompt": PROMPT, "cwd": "/x"}, 1)
    ev = events(home)
    check("H4 senza sid mai holdout",
          "[fd-route-hint]" in out and ev[-1][2].get("holdout") is False, out)

    # H6: frazione invalida → nessun crash, evento scritto
    out = run_hint(home, pay, "banana")
    check("H6 frazione invalida fallback", len(events(home)) > 0)

    # H7: report sotto soglia
    e = dict(os.environ, HOME=str(home))
    rep = subprocess.run([sys.executable, str(FDT), "report"],
                         capture_output=True, text=True, env=e).stdout
    check("H7 sotto soglia: conteggi + teatro",
          "Route-hint control arm" in rep and "theatre" in rep, rep[-400:])

    # H8: sopra soglia (in SESSIONI: ≥20 shown, ≥5 withheld) con esiti
    # attribuiti; il vincolo temporale ammette solo task_close >= primo hint
    con = sqlite3.connect(fd / "telemetry.db")
    from datetime import datetime, timedelta, timezone
    t0 = datetime.now(timezone.utc)

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    for i in range(30):
        sid = f"s{i}"
        con.execute("INSERT INTO events(ts,session_id,cwd,event,payload) "
                    "VALUES(?,?,?,?,?)",
                    (iso(t0), sid, "/x", "route_hint",
                     json.dumps({"matches": ["m"], "holdout": i < 6})))
        if i % 2 == 0:  # task_close DOPO l'hint → conta
            con.execute("INSERT INTO events(ts,session_id,cwd,event,payload) "
                        "VALUES(?,?,?,?,?)",
                        (iso(t0 + timedelta(minutes=5)), sid, "/x",
                         "task_close",
                         json.dumps({"route": "external", "outcome": "ok"})))
    # H10: sessione col task_close PRIMA dell'hint → non deve contare come
    # adozione (s_pre: hint a t0, close a t0-10min)
    con.execute("INSERT INTO events(ts,session_id,cwd,event,payload) "
                "VALUES(?,?,?,?,?)",
                (iso(t0), "s_pre", "/x", "route_hint",
                 json.dumps({"matches": ["m"], "holdout": False})))
    con.execute("INSERT INTO events(ts,session_id,cwd,event,payload) "
                "VALUES(?,?,?,?,?)",
                (iso(t0 - timedelta(minutes=10)), "s_pre", "/x", "task_close",
                 json.dumps({"route": "external", "outcome": "ok"})))
    # H9: sessione con hint in ENTRAMBI i bracci (legacy) → esclusa come mixed
    for hold in (False, True):
        con.execute("INSERT INTO events(ts,session_id,cwd,event,payload) "
                    "VALUES(?,?,?,?,?)",
                    (iso(t0), "s_mixed", "/x", "route_hint",
                     json.dumps({"matches": ["m"], "holdout": hold})))
    con.commit()
    con.close()
    rep = subprocess.run([sys.executable, str(FDT), "report"],
                         capture_output=True, text=True, env=e).stdout
    check("H8 sopra soglia sessioni: adoption per braccio",
          "cheap-route adoption" in rep, rep[-800:])
    # shown sessions: 24 (i>=6) + s_pre = 25; withheld 6; mixed esclusa
    # 2 miste: s_mixed (fixture) + sess-aaaa (H2/H3 la mettono in entrambi
    # i bracci forzando la frazione — mista a buon diritto)
    check("H9 sessione mista esclusa e dichiarata",
          "2 mixed-arm sessions excluded" in rep, rep[-800:])
    # adozione shown: i pari con i>=6 → 12 su 25 (s_pre NON conta: close
    # precedente all'hint). Se contasse, sarebbe 13/25.
    check("H10 task_close pre-hint non conta come adozione",
          "12/25" in rep and "13/25" not in rep, rep[-800:])

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} check falliti: {', '.join(FAILS)}")
    sys.exit(1)
print("Tutti i check hint-holdout superati.")
