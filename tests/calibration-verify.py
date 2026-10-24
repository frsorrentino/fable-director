#!/usr/bin/env python3
"""Stime calibrate (idea 2 della ricerca concorrenti del 23/10/2026): dal
telemetry.db il rapporto consuntivo/preventivo per --type, mediana con un
minimo di campioni, mostrato a budget-open come suggerimento e MAI applicato
in silenzio; un sottocomando `calibration` stampa la tabella per tipo.

  C1 calibration_factors: 6 chiusure di un tipo → n=6, fattore = mediana;
     abandoned, senza consuntivo e fuori finestra (90 giorni) esclusi
  C2 sotto il minimo (5) nessun fattore, solo n e mediana indicativa;
     --min-n 3 lo sblocca
  C3 budget-open --type con fattore 1.9 e stima 10.000: riga FD ✎ con il
     suggerimento 19.000, "not applied"; il budget tiene 10.000 e registra
     il suggerimento
  C4 tipo sotto il minimo → nessuna riga, nessun campo calibration
  C5 fattore ≈1 (0.9-1.1) → la riga dice che la stima tiene, nessuna cifra
  C6 `calibration`: tabella con fattore per il tipo denso, "below 5" per
     quello sparso; --min-n 3 gli dà il fattore; --type filtra
  C7 senza telemetria: esce 0 con una frase, nessun traceback
  C8 il suggerimento resta nel task_close e nella ricevuta (adozione misurabile)

Usage: python3 tests/calibration-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
FDT = str(SCRIPTS / "fd-telemetry.py")
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-cal-home-"))
os.environ["HOME"] = str(home)
os.environ["USERPROFILE"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", FDT)
fdt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fdt)
SID = "11111111-2222-3333-4444-555555555555"


def run(args, cwd=None, sid=SID):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    if sid:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          env=env, timeout=90, cwd=str(cwd or home))


def proj():
    return Path(tempfile.mkdtemp(prefix="fd-cal-proj-"))


def budget_of(cwd):
    return json.loads((home / ".claude/fable-director/budgets"
                       / f"{fdt.cwd_slug(str(cwd))}.json").read_text())


def seed_close(ttype, expected, actual, outcome="ok"):
    pl = {"type": ttype, "outcome": outcome, "expected_output_tokens": expected,
          "declared_at": "2026-10-01T10:00:00Z", "closed_at": "2026-10-01T11:00:00Z"}
    if actual is not None:
        pl["actual_output_tokens"] = actual
    fdt.log_event("task_close", pl)


# ---- C7 prima dei semi: nessun DB
r = run([FDT, "calibration"])
check("C7 senza telemetria: esce 0 con una frase",
      r.returncode == 0 and "no closed task" in r.stdout and "Traceback" not in r.stderr,
      r.stdout + r.stderr)

# ---- semi: seo-batch denso (6 valide), code-review sparso (3), steady ≈1
for act in (15000, 18000, 20000, 17000, 25000, 30000):   # ratio 1.5 … 3.0, mediana 1.9
    seed_close("seo-batch", 10000, act)
seed_close("seo-batch", 10000, 1000, outcome="abandoned")  # parziale: escluso
seed_close("seo-batch", 10000, None)                        # senza consuntivo: escluso
seed_close("seo-batch", 10000, 90000)                       # fuori finestra (sotto)
con = fdt.open_db()
con.execute("UPDATE events SET ts='2025-01-01T00:00:00Z' WHERE event='task_close' "
            "AND json_extract(payload,'$.actual_output_tokens')=90000")
con.commit(); con.close()
for act in (500, 500, 500):
    seed_close("code-review", 1000, act)
for act in (9500, 10000, 10500, 10000, 10000):
    seed_close("steady", 10000, act)

# ---- C1 / C2: la funzione (assente sul codice vecchio: FAIL, non traceback)
def cal_fn(**kw):
    fn = getattr(fdt, "calibration_factors", None)
    return fn(**kw) if fn else {"missing": "fdt.calibration_factors assente"}


cal = cal_fn()
sb = cal.get("seo-batch") or {}
check("C1 6 chiusure → n=6, fattore mediana 1.9; abandoned/senza consuntivo/vecchie escluse",
      sb.get("n") == 6 and sb.get("factor") == 1.9, cal)
cr = cal.get("code-review") or {}
cr3 = (cal_fn(min_n=3).get("code-review") or {})
check("C2 sotto il minimo: n=3, fattore assente, mediana indicativa; --min-n 3 lo sblocca",
      cr.get("n") == 3 and cr.get("factor") is None and cr.get("median") == 0.5
      and cr3.get("factor") == 0.5, (cr, cr3))

# ---- C3: budget-open mostra il suggerimento, non lo applica
p = proj()
r = run([FDT, "budget-open", "--task", "t", "--expected-output", "10000",
         "--type", "seo-batch", "--verify", "test -d .", "--cwd", str(p)], p)
b = budget_of(p)
check("C3 budget-open: riga FD ✎ con 19.000 suggeriti e 'not applied'; il budget tiene 10.000",
      r.returncode == 0 and "FD ✎ seo-batch" in r.stdout and "19.000" in r.stdout
      and "not applied" in r.stdout and "1.9×" in r.stdout
      and b["expected_output_tokens"] == 10000
      and (b.get("calibration") or {}).get("factor") == 1.9
      and b["calibration"].get("suggested_output_tokens") == 19000
      and b["calibration"].get("n") == 6, r.stdout + r.stderr)

# ---- C8: il suggerimento resta nel task_close e nella ricevuta
r = run([FDT, "budget-close", "--cwd", str(p)], p)
con = fdt.open_db()
last = con.execute("SELECT payload FROM events WHERE event='task_close' "
                   "ORDER BY rowid DESC LIMIT 1").fetchone()[0]
con.close()
receipts = sorted((home / ".claude/fable-director/receipts").glob("*.json"),
                  key=lambda x: x.stat().st_mtime)
rec = json.loads(receipts[-1].read_text()) if receipts else {}
check("C8 task_close e ricevuta portano il suggerimento (adozione misurabile)",
      r.returncode == 0
      and (json.loads(last).get("calibration") or {}).get("suggested_output_tokens") == 19000
      and (rec.get("calibration") or {}).get("suggested_output_tokens") == 19000,
      (last, rec.get("calibration")))

# ---- C4: tipo sotto il minimo → silenzio
p = proj()
r = run([FDT, "budget-open", "--task", "t", "--expected-output", "1000",
         "--type", "code-review", "--verify", "test -d .", "--cwd", str(p)], p)
check("C4 tipo sotto il minimo: nessuna riga FD ✎, nessun campo calibration",
      r.returncode == 0 and "FD ✎" not in r.stdout and "calibration" not in budget_of(p),
      r.stdout + r.stderr)
run([FDT, "budget-close", "--cwd", str(p)], p)

# ---- C5: fattore ≈1 → la stima tiene
p = proj()
r = run([FDT, "budget-open", "--task", "t", "--expected-output", "10000",
         "--type", "steady", "--verify", "test -d .", "--cwd", str(p)], p)
check("C5 fattore 1.0: la riga dice che la stima tiene, nessuna cifra suggerita",
      "FD ✎ steady" in r.stdout and "hold" in r.stdout and "suggested" not in r.stdout
      and (budget_of(p).get("calibration") or {}).get("suggested_output_tokens") is None,
      r.stdout + r.stderr)
run([FDT, "budget-close", "--cwd", str(p)], p)

# ---- C6: la tabella
r = run([FDT, "calibration"])
lines = {ln.split()[0]: ln for ln in r.stdout.splitlines() if ln.strip()}
sb_line, cr_line = lines.get("seo-batch", ""), lines.get("code-review", "")
check("C6a tabella: seo-batch con 1.9× su 6, code-review 'below 5' senza fattore",
      r.returncode == 0 and "1.9×" in sb_line and " 6 " in f" {sb_line} "
      and "below 5" in cr_line and "0.5×" in cr_line, r.stdout + r.stderr)
r3 = run([FDT, "calibration", "--min-n", "3"])
cr3_line = next((ln for ln in r3.stdout.splitlines() if ln.startswith("code-review")), "")
rt = run([FDT, "calibration", "--type", "seo-batch"])
check("C6b --min-n 3 dà il fattore a code-review; --type filtra alla sola riga",
      "below" not in cr3_line and "0.5×" in cr3_line
      and "seo-batch" in rt.stdout and "code-review" not in rt.stdout
      and "steady" not in rt.stdout, (r3.stdout, rt.stdout))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
