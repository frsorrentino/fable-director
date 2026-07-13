#!/usr/bin/env python3
"""Verifica deterministica dei fix 1.15.3 (doppio budget-open + scope report).

Runs the REAL scripts against a throwaway HOME:

  R1 budget-open sopra budget OPEN stessa sessione → refused, file intatto
  R2 open → close → open con stima rivista → ok
  R3 open → open --force → ok (sovrascrittura deliberata)
  R4 open senza session id → secondo open refused (path owner assente)
  R5 open sessione A → open sessione B → refused (messaggio cross-session)
  S1 session-cost-report: confronto pre-budget scoped a declared_at
     (record usage precedenti alla dichiarazione esclusi dall'actual)

Usage: python3 tests/budget-reopen-verify.py   (exit 0 = all green)
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
REPORT = (HERE.parent / "fable-director" / "skills" / "delega-efficiente"
          / "tools" / "session-cost-report.py")

passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(script, args, home, sid=None, cwd=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    if sid:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable, str(script)] + args,
                          capture_output=True, env=env, cwd=cwd, timeout=60)


def fresh():
    return Path(tempfile.mkdtemp(prefix="fd-reopen-home-")), \
           tempfile.mkdtemp(prefix="fd-reopen-proj-")


def topen(home, proj, task, sid=None, extra=None):
    return run(SCRIPTS / "fd-telemetry.py",
               ["budget-open", "--task", task, "--expected-output", "100",
                "--cwd", proj] + (extra or []), home, sid=sid)


def bfile_for(home, proj):
    sys.path.insert(0, str(SCRIPTS))
    import importlib.util
    spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
    fdt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fdt)
    return home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(proj)}.json"


def main():
    # R1 — stesso sid: secondo open rifiutato, primo budget intatto.
    home, proj = fresh()
    r1 = topen(home, proj, "first", sid="sess-a")
    r2 = topen(home, proj, "second", sid="sess-a")
    b = json.loads(bfile_for(home, proj).read_text())
    check("R1 re-open same session refused", r1.returncode == 0
          and r2.returncode != 0 and b"already OPEN" in r2.stderr,
          r2.stderr.decode(errors="replace"))
    check("R1b first budget untouched", b.get("task") == "first", b)

    # R2 — close esplicito poi ri-open: ok.
    r = run(SCRIPTS / "fd-telemetry.py",
            ["budget-close", "--outcome", "abandoned", "--cwd", proj],
            home, sid="sess-a")
    r2 = topen(home, proj, "revised", sid="sess-a")
    b = json.loads(bfile_for(home, proj).read_text())
    check("R2 close then re-open ok", r.returncode == 0 and r2.returncode == 0
          and b.get("task") == "revised" and b.get("status") == "open",
          r2.stderr.decode(errors="replace"))

    # R3 — --force sovrascrive deliberatamente.
    r3 = topen(home, proj, "forced", sid="sess-a", extra=["--force"])
    b = json.loads(bfile_for(home, proj).read_text())
    check("R3 --force overwrites", r3.returncode == 0 and b.get("task") == "forced",
          r3.stderr.decode(errors="replace"))

    # R4 — owner assente (nessun session id): secondo open comunque rifiutato.
    home, proj = fresh()
    topen(home, proj, "first")
    r = topen(home, proj, "second")
    check("R4 re-open without session id refused", r.returncode != 0
          and b"already OPEN" in r.stderr, r.stderr.decode(errors="replace"))

    # R5 — cross-session: messaggio dedicato preservato.
    home, proj = fresh()
    topen(home, proj, "first", sid="sess-a")
    r = topen(home, proj, "second", sid="sess-b")
    check("R5 cross-session refusal message preserved", r.returncode != 0
          and b"another session" in r.stderr, r.stderr.decode(errors="replace"))

    # S1 — report scoped a declared_at.
    home, proj = fresh()
    now = datetime.now(timezone.utc)
    topen(home, proj, "scoped")
    bf = bfile_for(home, proj)
    b = json.loads(bf.read_text())
    b["declared_at"] = now.isoformat()
    bf.write_text(json.dumps(b))
    pdir = home / ".claude" / "projects" / ("-" + proj.strip("/")
                                            .replace("/", "-").replace(".", "-"))
    pdir.mkdir(parents=True)
    rows = [
        {"timestamp": (now - timedelta(hours=2)).isoformat(),
         "message": {"model": "m", "usage": {"input_tokens": 7,
                                             "output_tokens": 1000}}},
        {"timestamp": (now + timedelta(minutes=1)).isoformat(),
         "message": {"model": "m", "usage": {"input_tokens": 3,
                                             "output_tokens": 10}}},
    ]
    (pdir / "t.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    r = run(REPORT, [], home, cwd=proj)
    out = r.stdout.decode(errors="replace")
    check("S1 pre-budget actual scoped to declared_at (10, not 1.010)",
          "actual: 10 " in out and "scope: da declared_at" in out, out)
    check("S1b session totals still whole-scope (1.010)", "1.010" in out, out)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
