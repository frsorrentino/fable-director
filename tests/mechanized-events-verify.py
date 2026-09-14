#!/usr/bin/env python3
"""Verifica dei tre eventi meccanizzati (14/09/2026).

Misura che ha motivato il cambio, su 30 giorni e 161 sessioni di telemetria
reale: gli eventi scritti da un hook arrivano sempre (161 session_summary su
161 sessioni), quelli che il kernel CHIEDE al modello di registrare quasi mai
— `verification` 5 volte su 53 task chiusi (9 %), `escalation` e
`script_promotion` zero volte. Qui si inchioda che ora li scrive il meccanismo.

  M1  verify che passa           -> verification found=False, kind=rung1-command
  M2  verify che fallisce        -> verification found=True, rc riportato
  M3  --verify in PROSA          -> nessuna esecuzione e nessun evento
  M4  niente di nuovo da verificare -> nessun secondo evento (no doppioni)
  M5  streak 3                   -> escalation UNA volta, class unclassified
  M6  streak 1, 2                -> nessuna escalation
  M7  streak 6, 9                -> escalation resta una (non a ogni multiplo)
  M8  escalation: solo il BINARIO nel payload, mai la riga di comando
  M9  tipo ricorrente (>=2 ok su rotta modello) -> domanda di promozione
  M10 tipo nuovo / rotta script / esito flagged -> nessuna domanda
  M11 telemetria rotta           -> gli hook non sollevano mai
"""
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "fable-director" / "scripts"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def eventi(home, kind=None):
    db = Path(home) / ".claude" / "fable-director" / "telemetry.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    q = "SELECT event, payload FROM events"
    rows = list(con.execute(q + (" WHERE event=?" if kind else ""),
                            (kind,) if kind else ()))
    con.close()
    return [(e, json.loads(p)) for e, p in rows]


# ---------------------------------------------------------------- verification
home = tempfile.mkdtemp()
os.environ["HOME"] = home
sb = load("sb", "stop-budget-check.py")

st = {}
sb.run_verify({"verify": "python3 -c pass"}, st, ".", {"write_touches": 1})
ev = eventi(home, "verification")
check("M1 verify che passa -> found=False",
      len(ev) == 1 and ev[0][1].get("found") is False
      and ev[0][1].get("kind") == "rung1-command" and ev[0][1].get("rc") == 0,
      str(ev))

st2 = {}
sb.run_verify({"verify": 'python3 -c "raise SystemExit(3)"'}, st2, ".",
              {"write_touches": 1})
ev = eventi(home, "verification")
check("M2 verify che fallisce -> found=True, rc=3",
      len(ev) == 2 and ev[1][1].get("found") is True and ev[1][1].get("rc") == 3,
      str(ev))

st3 = {}
sb.run_verify({"verify": "checklist: le tre schede rispondono"}, st3, ".",
              {"write_touches": 1})
check("M3 --verify in prosa -> non eseguito, nessun evento",
      st3.get("verify_rc") is None and len(eventi(home, "verification")) == 2)

# stesso state, stesse scritture: non c'e' niente di nuovo da verificare
sb.run_verify({"verify": "python3 -c pass"}, st, ".", {"write_touches": 1})
check("M4 niente di nuovo -> nessun doppione",
      len(eventi(home, "verification")) == 2)
shutil.rmtree(home, ignore_errors=True)

# ------------------------------------------------------------------ escalation
home = tempfile.mkdtemp()
os.environ["HOME"] = home
fs = load("fs", "fail-streak.py")
fs.bash_outcomes = lambda _p: []


def streak(n, cmd="pytest -q tests"):
    fs.trailing_streak = lambda _o: n
    sys.stdin = io.StringIO(json.dumps({
        "tool_name": "Bash", "session_id": "t1", "cwd": "/tmp",
        "transcript_path": "", "tool_input": {"command": cmd}}))
    with contextlib.redirect_stdout(io.StringIO()):
        fs.main()


streak(1)
streak(2)
check("M6 streak 1 e 2 -> nessuna escalation", not eventi(home, "escalation"))

streak(3)
esc = eventi(home, "escalation")
check("M5 streak 3 -> escalation una volta, class unclassified",
      len(esc) == 1 and esc[0][1].get("class") == "unclassified"
      and esc[0][1].get("streak") == 3 and esc[0][1].get("auto") is True,
      str(esc))
check("M8 payload: solo il binario, mai la riga",
      esc[0][1].get("binary") == "pytest"
      and "tests" not in json.dumps(esc[0][1]), str(esc[0][1]))

streak(4)
streak(6)
streak(9)
check("M7 streak 4, 6, 9 -> escalation resta una",
      len(eventi(home, "escalation")) == 1)
check("M7b fail_streak invece cresce ai multipli di 3",
      len(eventi(home, "fail_streak")) == 3,
      str(len(eventi(home, "fail_streak"))))
shutil.rmtree(home, ignore_errors=True)

# ----------------------------------------------------------- domanda promozione
home = tempfile.mkdtemp()
os.environ["HOME"] = home
fdt = load("fdt", "fd-telemetry.py")
db = Path(home) / ".claude" / "fable-director"
db.mkdir(parents=True, exist_ok=True)
con = sqlite3.connect(db / "telemetry.db")
con.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT "
            "NOT NULL, session_id TEXT, cwd TEXT, event TEXT NOT NULL, payload TEXT)")
for _ in range(2):
    con.execute("INSERT INTO events(ts, event, payload) VALUES("
                "strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'task_close', ?)",
                (json.dumps({"type": "seo-batch", "outcome": "ok", "route": "workflow"}),))
con.execute("INSERT INTO events(ts, event, payload) VALUES("
            "strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'task_close', ?)",
            (json.dumps({"type": "una-volta", "outcome": "ok", "route": "inline"}),))
con.commit()
con.close()
fdt.DB_PATH = db / "telemetry.db"

q = fdt.promotion_question({"type": "seo-batch", "outcome": "ok", "route": "workflow"}, ".")
check("M9 tipo ricorrente -> domanda di promozione",
      bool(q) and "seo-batch" in q and "2 volte" in q, str(q))
check("M10a tipo visto una volta sola -> nessuna domanda",
      fdt.promotion_question({"type": "una-volta", "outcome": "ok",
                              "route": "inline"}, ".") is None)
check("M10b rotta script -> esente",
      fdt.promotion_question({"type": "seo-batch", "outcome": "ok",
                              "route": "script"}, ".") is None)
check("M10c esito flagged -> nessuna domanda",
      fdt.promotion_question({"type": "seo-batch", "outcome": "flagged",
                              "route": "workflow"}, ".") is None)
check("M10d senza type -> nessuna domanda",
      fdt.promotion_question({"outcome": "ok", "route": "inline"}, ".") is None)

fdt.DB_PATH = Path("/dev/null/non-esiste/telemetry.db")
check("M11 telemetria irraggiungibile -> None, mai un'eccezione",
      fdt.promotion_question({"type": "seo-batch", "outcome": "ok",
                              "route": "workflow"}, ".") is None)
shutil.rmtree(home, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
