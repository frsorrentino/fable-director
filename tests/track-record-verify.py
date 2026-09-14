#!/usr/bin/env python3
"""Verifica del track record nel suggerimento di rotta (14/09/2026).

Il tasso di riuscita dei provider esterni esisteva gia', ma viveva solo in
`report`, che si legge DOPO. Qui arriva nel momento in cui la rotta si sceglie.
Stessa regola del perimetro, misurata il 14/09/2026 su 161 sessioni: quello che
si chiede nel momento del gesto viene usato (72-100 %), quello che sta in una
pagina letta un'ora prima no (0-9 %).

  T1  >= 3 run per provider   -> "andate finora: nome ok/tot"
  T2  < 3 run                 -> omesso (sotto soglia e' rumore)
  T3  provider sotto l'80 %   -> avviso «metti in conto una verifica in piu'»
  T4  tutti sopra l'80 %      -> nessun avviso, solo i numeri
  T5  ledger vuoto            -> nessuna clausola, riga invariata
  T6  telemetria irraggiungibile -> riga invariata, nessuna eccezione
  T7  provider non proposti   -> non compaiono (solo i free della riga)
"""
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "fable-director" / "scripts" / "route-hint.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))


def load(base):
    spec = importlib.util.spec_from_file_location("rh", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.base_dir = lambda: Path(base)
    return mod


def seed(base, runs):
    """runs: [(provider, ok), ...]"""
    d = Path(base)
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "telemetry.db")
    con.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, "
                "ts TEXT NOT NULL, session_id TEXT, cwd TEXT, event TEXT NOT NULL, "
                "payload TEXT)")
    for prov, ok in runs:
        con.execute("INSERT INTO events(ts, event, payload) VALUES("
                    "strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'external_exec', ?)",
                    (json.dumps({"provider": prov, "ok": ok}),))
    con.commit()
    con.close()


base = tempfile.mkdtemp()
seed(base, [("gemini", True)] * 9 + [("gemini", False)]
      + [("codex", True)] * 2                      # sotto soglia: 2 run
      + [("vecchio", True)] * 5)                   # non fra i proposti
rh = load(base)
out = rh.track_record({"gemini", "codex"})
check("T1 provider con >= 3 run -> compare con ok/tot", "gemini 9/10" in out, out)
check("T2 provider con 2 run -> omesso", "codex" not in out, out)
check("T7 provider non proposto -> assente", "vecchio" not in out, out)
check("T4 tutti sopra l'80 % -> nessun avviso",
      "verifica in piu" not in out, out)
shutil.rmtree(base, ignore_errors=True)

base = tempfile.mkdtemp()
seed(base, [("gemini", True)] * 2 + [("gemini", False)] * 3)   # 2/5 = 40 %
rh = load(base)
out = rh.track_record({"gemini"})
check("T3 sotto l'80 % -> avviso di verifica in piu'",
      "gemini 2/5" in out and "verifica in piu" in out and "3 volte su 5" in out,
      out)
shutil.rmtree(base, ignore_errors=True)

base = tempfile.mkdtemp()
seed(base, [])
rh = load(base)
check("T5 ledger vuoto -> nessuna clausola", rh.track_record({"gemini"}) == "")
shutil.rmtree(base, ignore_errors=True)

rh = load("/dev/null/non-esiste")
check("T6 telemetria irraggiungibile -> stringa vuota, nessuna eccezione",
      rh.track_record({"gemini"}) == "")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
