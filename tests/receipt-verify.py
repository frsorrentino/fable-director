#!/usr/bin/env python3
"""Verifica deterministica — ricevuta leggibile a budget-close (1.39.0, C1.3 + E9;
ricevuta completa e percorso stampato dalla 1.60.0).

  R1 ratio_words: rapporti in parole, mai sigle
  R2 budget-close stampa la riga di chiusura in parole con deleghe, riaperture,
     verify non eseguita, durata; scrive receipts/<slug>-<stamp>.md
  R3 dettaglio: executors per tipo, BLOCKED con riga di blocco, model switch,
     costo eq, verification, data class, perimetro
  R4 pezzi assenti omessi (nessuna delega, nessun verify): niente inventato
  R5 fd-status --receipts elenca le ricevute del cwd (prima riga del .md)
  R6 budget-close stampa il percorso della ricevuta .md
  R7 la ricevuta porta preventivo e consumo in token
  R8 la ricevuta porta l'esito della verifica accanto al comando
  R9 la ricevuta porta apertura, chiusura e durata
  R10 consumo non misurato e verify assente: detti nella ricevuta, non omessi
  R11 verify fallito o scaduto, verificatore eseguito o saltato: in ricevuta

Usage: python3 tests/receipt-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(args, home, stdin=None, cwd=None, sid=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    if sid:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, env=env, input=stdin, timeout=60, cwd=cwd)


home = Path(tempfile.mkdtemp(prefix="fd-rcpt-home-"))
proj = tempfile.mkdtemp(prefix="fd-rcpt-proj-")
os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)
FDT = str(SCRIPTS / "fd-telemetry.py")
SID = "sess-rcpt-1"

# R1
check("R1 ratio_words in parole",
      fdt.ratio_words(30, 100) == "cost about a third of the estimate"
      and fdt.ratio_words(50, 100) == "cost about half the estimate"
      and fdt.ratio_words(100, 100) == "cost about what was estimated"
      and fdt.ratio_words(160, 100) == "cost 1.6 times the estimate"
      and fdt.ratio_words(400, 100).startswith("cost more than three times")
      and fdt.ratio_words(None, 100) is None and fdt.ratio_words(5, 0) is None)

# R2/R3 — budget con deleghe (state del meter), switch, verify, perimetro
r = run([FDT, "budget-open", "--task", "batch descrizioni", "--expected-output", "100",
         "--type", "seo-batch", "--route", "agent", "--effort", "low",
         "--verify", "python3 tests/check.py", "--data-class", "internal",
         "--paths", "src/**", "--cwd", proj], home, sid=SID)
assert r.returncode == 0, r.stderr
bfile = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(proj)}.json"
b = json.loads(bfile.read_text())
b["actual_output_tokens"] = 160
b["reopens"] = 0
b["actual_eq_tokens"] = 45000
# due Opus nella stessa sessione (opus-5 ripresa, poi opus-5-5 dal picker):
# id completi, mai fusi per famiglia
b["cache_read_by_model"] = {"claude-sonnet-5": 300000, "claude-opus-5": 100000,
                            "claude-opus-5-5": 200000}
b["model_switches"] = [{"from": "claude-fable-5-1", "to": "claude-opus-5",
                        "context_tokens": 280000},
                       {"from": "claude-opus-5", "to": "claude-opus-5-5",
                        "context_tokens": 150000}]
bfile.write_text(json.dumps(b))
sdir = home / ".claude" / "fable-director" / "subagents"; sdir.mkdir(parents=True)
(sdir / f"{SID}.json").write_text(json.dumps({
    "inflight": {}, "started": 3, "stopped": 3,
    "by_type": {"fable-director:fd-executor": 3},
    "outcomes": {"ok": 2, "blocked": 1},
    "last_blocked": [{"agent_type": "fable-director:fd-executor", "status": "blocked",
                      "blocker": "Error: 403 from provider"}]}))
r = run([FDT, "budget-close", "--outcome", "ok", "--cwd", proj], home, sid=SID)
out = r.stdout
first = out.splitlines()[0] if out else ""
check("R2 riga di chiusura in parole",
      first.startswith("Closed fine: batch descrizioni — cost 1.6 times the estimate, "
                       "3 delegations, 1 failed, no file reopened, verification not run, ")
      and first.endswith(" min."),
      first + r.stderr)
rdir = home / ".claude" / "fable-director" / "receipts"
mds = sorted(rdir.glob("*.md"))
md = mds[-1].read_text() if mds else ""
check("R3 dettaglio + .md: executors, BLOCKED, model switch, costo, verification, data class, perimetro",
      "executors: fable-director:fd-executor ×3" in out
      and "BLOCKED from fable-director:fd-executor: Error: 403 from provider" in out
      and "model switch: claude-fable-5-1 → claude-opus-5 with 280.000 tokens of context" in out
      and "model switch: claude-opus-5 → claude-opus-5-5 with 150.000 tokens of context" in out
      and "cost: 45.000 eq (cache read on claude-opus-5, claude-opus-5-5, claude-sonnet-5)" in out
      and "verification: python3 tests/check.py" in out
      and "data class: internal" in out and "write perimeter: src/**" in out
      and md.startswith("# Closed fine: batch descrizioni") and "- executors:" in md,
      out + "\n---\n" + md[:400])

# R4 — budget minimo: niente delega, niente verify → niente inventato
proj2 = tempfile.mkdtemp(prefix="fd-rcpt-proj2-")
r = run([FDT, "budget-open", "--task", "fix rapido", "--expected-output", "50",
         "--cwd", proj2], home)
r = run([FDT, "budget-close", "--outcome", "abandoned", "--cwd", proj2], home)
first = r.stdout.splitlines()[0] if r.stdout else ""
check("R4 pezzi assenti omessi: nessuna delega, nessun verify, nessuna riapertura inventata",
      first.startswith("Closed as abandoned: fix rapido") and "delegation" not in first
      and "verification" not in first and "reopen" not in first,
      first + r.stderr)

# R5 — fd-status --receipts nel cwd del primo progetto
r = run([str(SCRIPTS / "fd-status.py"), "--receipts"], home, cwd=proj)
check("R5 fd-status --receipts elenca la ricevuta del cwd",
      r.returncode == 0 and "Closed fine: batch descrizioni" in r.stdout
      and "fix rapido" not in r.stdout, r.stdout + r.stderr)
r = run([str(SCRIPTS / "fd-status.py"), "--receipts", "all"], home, cwd=proj)
check("R5b --receipts all: entrambe", "fix rapido" in r.stdout and "batch descrizioni" in r.stdout,
      r.stdout)

# R6-R9 — ricevuta completa: preventivo, consumo, esito della verifica e
# durata nel .md, e budget-close ne stampa il percorso
proj3 = tempfile.mkdtemp(prefix="fd-rcpt-proj3-")
r = run([FDT, "budget-open", "--task", "ricevuta completa", "--expected-output", "100",
         "--expected-input", "20000", "--verify", "python3 tests/check.py",
         "--cwd", proj3], home)
assert r.returncode == 0, r.stderr
bfile3 = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(proj3)}.json"
b = json.loads(bfile3.read_text())
b["declared_at"] = (datetime.now(timezone.utc) - timedelta(minutes=125, seconds=20)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
b.update(actual_output_tokens=160, actual_input_tokens=30000, verify_rc=0)
bfile3.write_text(json.dumps(b))
r = run([FDT, "budget-close", "--outcome", "ok", "--cwd", proj3], home)
out = r.stdout
printed = [ln.split("receipt: ", 1)[1].strip() for ln in out.splitlines()
           if ln.strip().startswith("receipt: ")]
rpath = Path(printed[-1]) if printed else None
md = rpath.read_text(encoding="utf-8") if rpath and rpath.is_file() else ""
check("R6 budget-close stampa il percorso della ricevuta .md",
      rpath is not None and rpath.parent == rdir and rpath.suffix == ".md"
      and md.startswith("# Closed fine: ricevuta completa"),
      out + r.stderr)
check("R7 ricevuta: preventivo e consumo in token",
      "- estimate: 100 output tokens, 20.000 input tokens\n" in md
      and "- actual: 160 output tokens, 30.000 input tokens\n" in md,
      md or out)
check("R8 ricevuta: esito della verifica accanto al comando",
      "- verification: python3 tests/check.py — passed\n" in md
      and "  verification: python3 tests/check.py — passed\n" in out,
      md or out)
check("R9 ricevuta: apertura, chiusura e durata",
      f"- opened: {b['declared_at']}\n" in md and "- closed: " in md
      and "- duration: 2 h 5 min\n" in md,
      md or out)

# R10 — il budget di R4 (chiuso subito, niente Stop hook, niente verify):
# la ricevuta dice cosa manca invece di tacerlo
mds2 = sorted(rdir.glob(f"{fdt.cwd_slug(proj2)}-*.md"))
md2 = mds2[-1].read_text(encoding="utf-8") if mds2 else ""
check("R10 consumo non misurato e verify assente: detti, non omessi",
      "- estimate: 50 output tokens\n" in md2 and "- actual: not measured\n" in md2
      and "- verification: none declared\n" in md2 and "- duration: 0 min\n" in md2,
      md2)

# R11 — esito negativo del verify e verificatore del tipo fragile
_, d_fail = fdt.receipt_lines({"task": "t", "outcome": "ok", "expected_output_tokens": 50,
                               "verify": "pytest -q", "verify_rc": 1,
                               "no_verifier": "solo docs toccati"}, proj2, None)
_, d_time = fdt.receipt_lines({"task": "t", "outcome": "ok", "expected_output_tokens": 50,
                               "verify": "pytest -q", "verify_rc": "timeout",
                               "verifier_ran": True}, proj2, None)
check("R11 verify fallito o scaduto e verificatore: in ricevuta",
      "verification: pytest -q — FAILED (exit 1)" in d_fail
      and "verifier: skipped — solo docs toccati" in d_fail
      and "verification: pytest -q — timed out" in d_time
      and f"verifier: {fdt.VERIFIER_AGENT} ran during the task" in d_time,
      d_fail + d_time)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
