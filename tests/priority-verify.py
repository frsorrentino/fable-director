#!/usr/bin/env python3
"""Verifica deterministica — broker di quota tra sessioni (1.39.0, D.3.4).

  Q1 budget-open --priority incident scrive priority.json con scadenza 2h e
     stampa l'avviso
  Q2 gate in un'ALTRA sessione, finestra 5h al 70%: fan-out Agent negato con
     il nome del task; evento gate_deny priority_hold
  Q3 stessa sessione dell'incidente: passa
  Q4 finestra al 20%: passa (quota abbondante)
  Q5 resumeFromRunId: passa sempre
  Q6 flag scaduto: rimosso, passa
  Q7 budget-close dell'incidente rimuove il flag
  Q8 --priority non valido rifiutato

Usage: python3 tests/priority-verify.py
"""
import json, os, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(args, home, stdin=None, sid=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    if sid:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          env=env, input=stdin, timeout=60)


home = Path(tempfile.mkdtemp(prefix="fd-prio-home-"))
inc = tempfile.mkdtemp(prefix="fd-prio-inc-")
oth = tempfile.mkdtemp(prefix="fd-prio-oth-")
FDT = str(SCRIPTS / "fd-telemetry.py"); GATE = str(SCRIPTS / "pre-delegation-gate.py")
base = home / ".claude" / "fable-director"

# budget normale nell'altra sessione (serve al gate)
r = run([FDT, "budget-open", "--task", "batch curiosita", "--expected-output", "100",
         "--cwd", oth], home, sid="s-other")
assert r.returncode == 0, r.stderr
# Q8
r = run([FDT, "budget-open", "--task", "x", "--expected-output", "10", "--priority", "urgent",
         "--cwd", inc], home, sid="s-inc")
check("Q8 --priority non valido rifiutato", r.returncode != 0 and "invalid --priority" in (r.stderr + r.stdout))
# Q1
r = run([FDT, "budget-open", "--task", "sito cliente giu", "--expected-output", "500",
         "--priority", "incident", "--cwd", inc], home, sid="s-inc")
pf = base / "priority.json"
pr = json.loads(pf.read_text()) if pf.is_file() else {}
exp_ok = False
try:
    exp = datetime.fromisoformat(pr["expires_at"])
    exp_ok = 7000 < (exp - datetime.now(timezone.utc)).total_seconds() <= 7200
except Exception:
    pass
check("Q1 priority.json scritto con task, sessione, scadenza ~2h; avviso stampato",
      pr.get("task") == "sito cliente giu" and pr.get("session_id") == "s-inc" and exp_ok
      and "incident priority on" in r.stdout, r.stdout + r.stderr + str(pr))


def gate(sid, cwd, used, tool="Agent", extra=None):
    q = base / "quota.json"
    q.write_text(json.dumps({"five_hour_used_pct": used, "weekly_used_pct": 10}))
    os.utime(q, None)
    ti = {"subagent_type": "general-purpose", "prompt": "x" * 10, "description": "d"}
    ti.update(extra or {})
    return run([GATE], home, json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                                         "cwd": cwd, "session_id": sid, "tool_input": ti}))


r = gate("s-other", oth, 70)
out = json.loads(r.stdout) if r.stdout.strip() else {}
hs = out.get("hookSpecificOutput", out)
check("Q2 altra sessione, finestra 70%: fan-out negato col nome del task",
      hs.get("permissionDecision") == "deny" and "sito cliente giu" in json.dumps(out)
      and "single turns are free" in json.dumps(out), r.stdout + r.stderr)
r = gate("s-inc", inc, 70)
check("Q3 la sessione dell'incidente passa", "deny" not in (r.stdout or ""), r.stdout)
r = gate("s-other", oth, 20)
check("Q4 finestra al 20%: passa", "deny" not in (r.stdout or ""), r.stdout)
r = gate("s-other", oth, 70, tool="Workflow", extra={"resumeFromRunId": "wf_abc123"})
check("Q5 resume passa sempre", "deny" not in (r.stdout or ""), r.stdout)
# Q6 scaduto
pr["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
pf.write_text(json.dumps(pr))
r = gate("s-other", oth, 70)
check("Q6 flag scaduto: rimosso e passa", "deny" not in (r.stdout or "") and not pf.is_file(), r.stdout)
# Q7 close rimuove
pf.write_text(json.dumps(dict(pr, expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())))
r = run([FDT, "budget-close", "--outcome", "ok", "--cwd", inc], home, sid="s-inc")
check("Q7 budget-close dell'incidente rimuove il flag", not pf.is_file(), r.stdout + r.stderr)
import sqlite3
con = sqlite3.connect(base / "telemetry.db")
n = con.execute("select count(*) from events where event='gate_deny' and payload like '%priority_hold%'").fetchone()[0]
con.close()
check("Q2b gate_deny priority_hold loggato", n == 1, str(n))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
