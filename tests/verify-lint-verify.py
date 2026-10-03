#!/usr/bin/env python3
"""Verifica deterministica — lint del --verify (A, 1.54.0): un controllo vale
solo se puo' fallire.

  T1 comandi veri, anche quelli che il vecchio riconoscimento a runner fissi
     trattava come prosa (test, grep -q, git diff --quiet, script per percorso)
  T2 finti: true, echo, exit 0, `|| true`, `; exit 0`, `| tail`, grep -c senza
     confronto, git status/diff, --exit-zero, python -c print, bash -c 'exit 0'
  T3 prosa e prosa con un comando davanti
  T4 assente
  T5 casi reali di settembre 2026 (telemetria): `pytest | tail -3` e `grep -c`
     finti, `test ... && [ $(grep -c …) -ge 5 ]` vero
  T6 pipefail: l'ultimo stadio della pipe non decide piu' da solo
  T7 il motivo dice come correggere

Usage: python3 tests/verify-lint-verify.py   (exit 0 = all green)
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
spec = importlib.util.spec_from_file_location("verify_lint", SCRIPTS / "verify-lint.py")
vl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vl)
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def kinds(cases, expected):
    bad = [(c, vl.classify(c)) for c in cases if vl.classify(c)[0] != expected]
    return not bad, bad


ok, bad = kinds([
    "python3 -m pytest -q",
    "test -s out.md && grep -q '## Esito' out.md",
    "grep -q NOTIFICATO STATO.md",
    "git diff --quiet 01b1ae2 fb164d3 -- wear",
    "cd tools/promo/remotion && npm run check",
    "/tmp/x/scratchpad/verify87.sh",
    "./check.sh",
    "npm test && echo ok",
    "! grep -q TODO src/a.py",
    "FOO=1 make test",
    "test $(grep -c X f) -eq 0",
    "php tests/run.php && php bin/i18n-check.php",
    "pytest 2>&1 >/dev/null",
    "python3 -c 'import json,sys; assert json.load(open(\"a.json\"))[\"n\"] == 3'",
], "command")
check("T1 comandi veri riconosciuti", ok, bad)

fakes = [
    "true", ":", "echo ok", "exit 0", "npm test || true", "pytest || :",
    "pytest || exit 0", "pytest || echo failed", "pytest; exit 0", "pytest; true",
    "npm test 2>&1 | tail -3", "pytest | head -5", "grep -c X f.md",
    "rg --count X .", "git status", "git log -1", "git diff", "ruff check --exit-zero .",
    "python3 -c 'print(1)'", "node -e 'console.log(1)'", "bash -c 'exit 0'",
    "sh -c 'pytest || true'", "cd x && true", "ls out | wc -l",
]
ok, bad = kinds(fakes, "fake")
check("T2 controlli finti negati", ok, bad)

ok, bad = kinds([
    "nessun errore", "review report with graded findings",
    "checklist: ogni voce con link verificato",
    "docs/analisi-concorrenti-2026-09-24.md esiste; ogni repo ha riga",
    "ls analysis/ref-*/BREAKDOWN.md | wc -l ≥ 5; fermi in out/stills-prologo",
    "sembra a posto",
], "prose")
check("T3 prosa (anche con un comando davanti) riconosciuta", ok, bad)

ok, bad = kinds(["", None, "   "], "missing")
check("T4 verify assente", ok, bad)

r1 = vl.classify("cd fable-director && python3 -m pytest -q tests 2>&1 | tail -3")
r2 = vl.classify("test -f docs/a.md && grep -c '^## ' docs/a.md")
r3 = vl.classify("test -s docs/a.md && [ $(grep -cE 'https?://' docs/a.md) -ge 5 ]")
check("T5 casi reali: pipe in tail e grep -c finti, confronto vero",
      r1[0] == "fake" and "tail" in r1[1] and r2[0] == "fake" and "grep -c" in r2[1]
      and r3[0] == "command", (r1, r2, r3))

r = vl.classify("set -o pipefail; npm test | tail -3")
check("T6 pipefail dichiarato: la pipe in tail non e' finta", r[0] == "command", r)

r = vl.classify("npm test || true")
check("T7 il motivo nomina il difetto e la correzione",
      "swallows the failure" in r[1] and "exits non-zero" in r[1], r)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
