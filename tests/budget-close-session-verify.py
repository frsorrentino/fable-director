#!/usr/bin/env python3
"""Verifica deterministica della risoluzione SESSION-FIRST di budget-close /
budget-amend (incidente 2026-08-21: cd residuo nella shell → chiuso con
outcome ok il budget stantio di un altro cwd).

Runs the REAL scripts against a throwaway HOME:

  C1 close senza --cwd da un cwd DIVERSO, stesso sid → risolve il budget
     della sessione (nota "risolto per sessione"), lo chiude
  C2 close senza --cwd sul budget FRESCO di un'altra sessione → refused,
     budget intatto
  C3 come C2 ma con --cwd esplicito → chiuso (intento dichiarato)
  C4 budget di altra sessione ma orfano (>24h) → chiuso senza flag
  C5 env senza session id → comportamento legacy (slug del cwd corrente)
  C6 due budget aperti della stessa sessione → ambiguo, exit con richiesta
     --cwd, entrambi intatti
  A1 budget-amend senza --cwd da cwd diverso, stesso sid → emenda il budget
     della sessione

Usage: python3 tests/budget-close-session-verify.py   (exit 0 = all green)
"""
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


def run(args, home, sid=None, cwd=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    if sid:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py")] + args,
                          capture_output=True, env=env, cwd=cwd, timeout=60)


def fresh_home():
    return Path(tempfile.mkdtemp(prefix="fd-close-home-"))


def proj():
    return tempfile.mkdtemp(prefix="fd-close-proj-")


def topen(home, p, task, sid=None):
    return run(["budget-open", "--task", task, "--expected-output", "100",
                "--cwd", p], home, sid=sid)


def bfile_for(home, p):
    import importlib.util
    spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
    fdt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fdt)
    return home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(p)}.json"


def main():
    # C1 — session-first: close da cwd diverso, stesso sid.
    home, pa, pb = fresh_home(), proj(), proj()
    topen(home, pa, "mine", sid="sess-a")
    r = run(["budget-close", "--outcome", "ok"], home, sid="sess-a", cwd=pb)
    b = json.loads(bfile_for(home, pa).read_text())
    check("C1 session-first close from other cwd",
          r.returncode == 0 and b.get("status") == "closed"
          and "risolto per sessione" in r.stdout.decode(errors="replace"),
          r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace"))

    # C2 — budget fresco di un'altra sessione: refused, intatto.
    home, pa = fresh_home(), proj()
    topen(home, pa, "theirs", sid="sess-a")
    r = run(["budget-close", "--outcome", "ok"], home, sid="sess-b", cwd=pa)
    b = json.loads(bfile_for(home, pa).read_text())
    check("C2 other-session fresh budget refused",
          r.returncode != 0 and b"refused" in r.stderr
          and b.get("status") == "open",
          r.stderr.decode(errors="replace"))

    # C3 — stesso stato, --cwd esplicito: chiuso.
    r = run(["budget-close", "--outcome", "ok", "--cwd", pa], home, sid="sess-b")
    b = json.loads(bfile_for(home, pa).read_text())
    check("C3 explicit --cwd closes other-session budget",
          r.returncode == 0 and b.get("status") == "closed",
          r.stderr.decode(errors="replace"))

    # C4 — orfano >24h di altra sessione: chiuso senza --cwd.
    home, pa = fresh_home(), proj()
    topen(home, pa, "orphan", sid="sess-a")
    bf = bfile_for(home, pa)
    b = json.loads(bf.read_text())
    b["declared_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    bf.write_text(json.dumps(b))
    r = run(["budget-close", "--outcome", "abandoned"], home, sid="sess-b", cwd=pa)
    b = json.loads(bf.read_text())
    check("C4 orphan >24h closable without --cwd",
          r.returncode == 0 and b.get("status") == "closed",
          r.stderr.decode(errors="replace"))

    # C5 — nessun session id nell'env: legacy, slug del cwd corrente.
    home, pa = fresh_home(), proj()
    topen(home, pa, "legacy")
    r = run(["budget-close", "--outcome", "ok"], home, cwd=pa)
    b = json.loads(bfile_for(home, pa).read_text())
    check("C5 no-sid legacy resolution by cwd",
          r.returncode == 0 and b.get("status") == "closed",
          r.stderr.decode(errors="replace"))

    # C6 — due budget aperti stessa sessione: ambiguo, entrambi intatti.
    home, pa, pb, pc = fresh_home(), proj(), proj(), proj()
    topen(home, pa, "uno", sid="sess-a")
    topen(home, pb, "due", sid="sess-a")
    r = run(["budget-close", "--outcome", "ok"], home, sid="sess-a", cwd=pc)
    ba = json.loads(bfile_for(home, pa).read_text())
    bb = json.loads(bfile_for(home, pb).read_text())
    check("C6 two open budgets ambiguous",
          r.returncode != 0 and b"ambiguo" in r.stderr
          and ba.get("status") == "open" and bb.get("status") == "open",
          r.stderr.decode(errors="replace"))

    # A1 — amend session-first da cwd diverso.
    home, pa, pb = fresh_home(), proj(), proj()
    topen(home, pa, "perimetro", sid="sess-a")
    r = run(["budget-amend", "--add-paths", "src/*.py", "--reason", "test"],
            home, sid="sess-a", cwd=pb)
    b = json.loads(bfile_for(home, pa).read_text())
    check("A1 session-first amend from other cwd",
          r.returncode == 0 and "src/*.py" in (b.get("paths") or []),
          r.stderr.decode(errors="replace"))

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
