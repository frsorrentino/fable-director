#!/usr/bin/env python3
"""Verifica deterministica — rotta bg-session dietro flag (1.39.0, C1.7).

  B1 senza FD_BG_SESSION=1: --route bg-session rifiutato
  B2 con flag ma senza --cwd: rifiutato
  B3 apertura dal padre: budget nella cartella figlia con parent_session
  B4 SessionStart nella figlia: riga "Budget inherited from session …"
  B5 budget-close nella figlia: ricevuta cita il padre

Usage: python3 tests/bg-session-verify.py
"""
import importlib.util, json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-bg-home-"))
child = tempfile.mkdtemp(prefix="fd-bg-child-")
os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)


def run(script, args, stdin=None, env_extra=None):
    env = dict(os.environ, HOME=str(home)); env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None); env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPTS / script)] + args, capture_output=True,
                          text=True, env=env, input=stdin, timeout=60)


base = ["budget-open", "--task", "migrazione lunga", "--expected-output", "5000",
        "--route", "bg-session", "--verify", "python3 tests/run.py"]
r = run("fd-telemetry.py", base + ["--cwd", child], env_extra={"CLAUDE_CODE_SESSION_ID": "parent-1"})
check("B1 senza flag: rifiutato", r.returncode != 0 and "behind a flag" in (r.stderr + r.stdout), r.stderr)
r = run("fd-telemetry.py", base, env_extra={"FD_BG_SESSION": "1", "CLAUDE_CODE_SESSION_ID": "parent-1"})
check("B2 con flag senza --cwd: rifiutato", r.returncode != 0 and "requires --cwd" in (r.stderr + r.stdout), r.stderr)
r = run("fd-telemetry.py", base + ["--cwd", child], env_extra={"FD_BG_SESSION": "1", "CLAUDE_CODE_SESSION_ID": "parent-1"})
bfile = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(child)}.json"
b = json.loads(bfile.read_text()) if bfile.is_file() else {}
check("B3 budget nella cartella figlia con parent_session e route bg-session",
      r.returncode == 0 and b.get("route") == "bg-session" and b.get("parent_session") == "parent-1",
      r.stdout + r.stderr + json.dumps(b)[:200])
r = run("session-hindsight.py", [], stdin=json.dumps({"cwd": child, "source": "startup", "session_id": "child-1"}))
check("B4 SessionStart figlia: budget ereditato in una riga",
      "Budget inherited from session parent-1: 'migrazione lunga' — expected output 5000 tokens, verify: python3 tests/run.py" in r.stdout,
      r.stdout + r.stderr)
r = run("fd-telemetry.py", ["budget-close", "--outcome", "ok", "--cwd", child], env_extra={"CLAUDE_CODE_SESSION_ID": "child-1"})
check("B5 ricevuta cita il padre", "opened by parent session parent-1 (bg-session route)" in r.stdout, r.stdout + r.stderr)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
