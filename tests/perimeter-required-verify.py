#!/usr/bin/env python3
"""Verifica del perimetro obbligatorio sulle rotte che delegano (14/09/2026).

Misura che ha motivato la regola, su 30 giorni di telemetria reale: dei 50
budget aperti, 36 dichiaravano il perimetro di scrittura (72 %) — l'unica
regola del kernel rimasta sotto il 90 %. Delle 26 deleghe, 5 partivano senza:
senza `--paths` l'hook sulle scritture non ha nulla da confrontare e un
esecutore puo' scrivere ovunque nel progetto.

  P1  --route agent senza --paths     -> rifiuta, con il comando giusto nel messaggio
  P2  --route workflow senza --paths  -> rifiuta
  P3  --route bg-session senza --paths-> rifiuta (dietro il suo flag)
  P4  --route agent con --paths       -> apre, perimetro nel budget
  P5  --route agent --paths none      -> apre, rinuncia registrata e in ricevuta
  P6  --route inline senza --paths    -> apre in silenzio (scrive il modello principale)
  P7  --route external senza --paths  -> apre in silenzio
  P8  nessuna --route senza --paths   -> apre in silenzio (nessuna delega dichiarata)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FDT = REPO / "fable-director" / "scripts" / "fd-telemetry.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(home, *args, env=None):
    e = dict(os.environ, HOME=home)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(FDT), *args], capture_output=True,
                          text=True, env=e)


def budget_file(home, cwd):
    d = Path(home) / ".claude" / "fable-director" / "budgets"
    files = list(d.glob("*.json")) if d.is_dir() else []
    return json.loads(files[0].read_text()) if files else {}


home = tempfile.mkdtemp()
cwd = tempfile.mkdtemp()
base = ["budget-open", "--task", "t", "--expected-output", "100", "--cwd", cwd]

r = run(home, *base, "--route", "agent")
check("P1 agent senza --paths -> rifiuta",
      r.returncode != 0 and "requires --paths" in (r.stdout + r.stderr),
      (r.stdout + r.stderr)[:90])
check("P1b il messaggio insegna la via d'uscita esplicita",
      "--paths none" in (r.stdout + r.stderr))

r = run(home, *base, "--route", "workflow")
check("P2 workflow senza --paths -> rifiuta", r.returncode != 0)

r = run(home, *base, "--route", "bg-session", "--cwd", cwd,
        env={"FD_BG_SESSION": "1"})
check("P3 bg-session senza --paths -> rifiuta",
      r.returncode != 0 and "--paths" in (r.stdout + r.stderr),
      (r.stdout + r.stderr)[:90])

r = run(home, *base, "--route", "agent", "--paths", "src/**,tests/**")
check("P4 agent con --paths -> apre", r.returncode == 0, (r.stdout + r.stderr)[:90])
b = budget_file(home, cwd)
check("P4b perimetro registrato nel budget",
      b.get("paths") == ["src/**", "tests/**"], str(b.get("paths")))
run(home, "budget-close", "--outcome", "ok", "--cwd", cwd)

r = run(home, *base, "--route", "agent", "--paths", "none")
check("P5 --paths none -> apre", r.returncode == 0, (r.stdout + r.stderr)[:90])
b = budget_file(home, cwd)
check("P5b rinuncia registrata, nessun perimetro finto",
      b.get("paths_waived") is True and not b.get("paths"), str(b)[:120])
r = run(home, "budget-close", "--outcome", "ok", "--cwd", cwd)
check("P5c la ricevuta dice che la rinuncia era voluta",
      "waived on purpose" in r.stdout, r.stdout[:120])

for rotta in ("inline", "external"):
    r = run(home, *base, "--route", rotta)
    check(f"P6/P7 {rotta} senza --paths -> apre in silenzio",
          r.returncode == 0 and "--paths" not in r.stdout,
          (r.stdout + r.stderr)[:90])
    run(home, "budget-close", "--outcome", "ok", "--cwd", cwd)

r = run(home, *base)
check("P8 nessuna rotta dichiarata -> apre in silenzio", r.returncode == 0,
      (r.stdout + r.stderr)[:90])

shutil.rmtree(home, ignore_errors=True)
shutil.rmtree(cwd, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
