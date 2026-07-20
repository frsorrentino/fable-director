#!/usr/bin/env python3
"""Verifica deterministica 1.22.0 — i token del Workflow tool entrano nel
righello, e il fan-out a quota esausta viene fermato prima.

Contesto (misurato 2026-07-20, sessione reale): gli agenti del Workflow tool
NON compaiono mai in toolUseResult del main transcript — 9,2M input freschi
invisibili a enforcement e telemetria; un run 6-lenti è morto al session
limit con 594k token bruciati e 0/7 agenti completati.

Runs the REAL scripts against a throwaway HOME:

  A1 stop hook: 3× bust scatta dai SOLI token workflow (main sotto stima)
  A2 stop hook: secondo giro incrementale, niente double counting
  A3 budget-close: actual = main + wf dallo state file
  A4 session-summary: payload con wf_agents/wf_output/wf_input_fresh,
     subagent_output include gli agenti workflow
  G1 gate: NUOVO Workflow a quota 5h ≥ soglia → deny (kind=quota_guard,
     [DLG] non incrementato)
  G2 gate: resumeFromRunId alla stessa quota → allow
  G3 gate: snapshot quota stantio (>10 min) → allow (fail-open)
  G4 gate: quota sotto soglia → allow
  G5 gate: Agent tool alla stessa quota → allow (guardia solo Workflow)
  G6 gate: soglia custom da cost-checkpoint.json rispettata
  C1 budget-open --agents: stima sotto l'ancora → warning
  C2 budget-open --agents: stima adeguata → nessun warning
  C3 budget-open --agents negativo → refused

Usage: python3 tests/workflow-tokens-verify.py   (exit 0 = all green)
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
FIXTURE = HERE / "transcript-contract" / "06-workflow-agents.jsonl"

passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(script, args, home, stdin=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([sys.executable, str(script)] + args,
                          capture_output=True, text=True, env=env,
                          input=stdin, timeout=60)


def fresh_home():
    return Path(tempfile.mkdtemp(prefix="fd-wf-home-"))


def budget_open(home, proj, expected_out, extra=None):
    return run(SCRIPTS / "fd-telemetry.py",
               ["budget-open", "--task", "wf-test", "--expected-output",
                str(expected_out), "--cwd", proj] + (extra or []), home)


def make_session(tmp):
    """Sessione finta: main transcript minuscolo + un agente workflow grasso.
    Timestamp 2030: sempre post-declared (declared_at = adesso)."""
    tr = Path(tmp) / "sess.jsonl"
    tr.write_text('{"timestamp":"2030-01-01T10:00:00Z",'
                  '"message":{"usage":{"output_tokens":10,"input_tokens":5}}}\n')
    adir = tr.with_suffix("") / "subagents" / "workflows" / "wf_a"
    adir.mkdir(parents=True)
    (adir / "agent-a1.jsonl").write_text(
        '{"timestamp":"2030-01-01T10:01:00Z","message":{"usage":'
        '{"output_tokens":400,"input_tokens":20,'
        '"cache_creation_input_tokens":300}}}\n')
    return tr


def stop_hook(home, proj, transcript):
    return run(SCRIPTS / "stop-budget-check.py", [], home,
               stdin=json.dumps({"cwd": proj, "transcript_path": str(transcript)}))


def gate(home, proj, tool_name="Workflow", tool_input=None, sid="sid-wf-test"):
    env_stdin = json.dumps({"tool_name": tool_name,
                            "tool_input": tool_input or {},
                            "cwd": proj, "session_id": sid})
    return run(SCRIPTS / "pre-delegation-gate.py", [], home, stdin=env_stdin)


def write_quota(home, pct, age_s=0):
    base = home / ".claude" / "fable-director"
    base.mkdir(parents=True, exist_ok=True)
    acct = hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]
    q = base / f"quota-{acct}.json"
    q.write_text(json.dumps({"five_hour_used_pct": pct, "weekly_used_pct": 10.0}))
    if age_s:
        t = time.time() - age_s
        os.utime(q, (t, t))
    return q


def decision(r):
    try:
        return (json.loads(r.stdout).get("hookSpecificOutput") or {}) \
            .get("permissionDecision")
    except (json.JSONDecodeError, ValueError):
        return None


def db_events(home, kind):
    db = home / ".claude" / "fable-director" / "telemetry.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(db)
    rows = [json.loads(p or "{}") for (p,) in con.execute(
        "SELECT payload FROM events WHERE event=?", (kind,))]
    con.close()
    return rows


# ---- A: stop hook + budget-close ----

home = fresh_home()
with tempfile.TemporaryDirectory() as tmp:
    proj = tmp
    tr = make_session(tmp)
    budget_open(home, proj, 100)  # 3× = 300: solo il wf (400) può sfondare
    r = stop_hook(home, proj, tr)
    # il json.dumps dell'hook fa escape unicode: "3×" appare come "3×"
    blocked = ('"decision": "block"' in r.stdout
               and "FABLE-DIRECTOR 3" in json.loads(r.stdout).get("reason", ""))
    bfile = next(f for f in (home / ".claude" / "fable-director"
                             / "budgets").glob("*.json")
                 if ".state." not in f.name)
    b = json.loads(bfile.read_text())
    check("A1 3× bust dai soli token workflow",
          blocked and b.get("status") == "flagged"
          and b.get("actual_output_tokens") == 410
          and b.get("actual_input_tokens") == 325,
          f"stdout={r.stdout[:200]!r} budget={b}")

home = fresh_home()
with tempfile.TemporaryDirectory() as tmp:
    proj = tmp
    tr = make_session(tmp)
    budget_open(home, proj, 100000)  # mai bust: si testa l'incrementale
    stop_hook(home, proj, tr)
    sfile = next((home / ".claude" / "fable-director" / "budgets")
                 .glob("*.state.json"))
    st1 = json.loads(sfile.read_text())
    stop_hook(home, proj, tr)  # nessun dato nuovo
    st2 = json.loads(sfile.read_text())
    check("A2 scan incrementale, niente double counting",
          st1["out"] + st1["wf_out"] == st2["out"] + st2["wf_out"] == 410
          and st1["wf_inp"] == st2["wf_inp"] == 320,
          f"st1={st1} st2={st2}")

    r = run(SCRIPTS / "fd-telemetry.py",
            ["budget-close", "--outcome", "ok", "--cwd", proj], home)
    closes = db_events(home, "task_close")
    check("A3 budget-close somma main + workflow",
          closes and closes[-1].get("actual_output_tokens") == 410
          and closes[-1].get("actual_input_tokens") == 325,
          f"close={closes[-1] if closes else None} stderr={r.stderr[:200]!r}")

home = fresh_home()
with tempfile.TemporaryDirectory() as tmp:
    r = run(SCRIPTS / "fd-telemetry.py",
            ["session-summary", "--transcript", str(FIXTURE),
             "--session-id", "s-wf", "--cwd", tmp], home)
    summaries = db_events(home, "session_summary")
    s = summaries[-1] if summaries else {}
    # fixture 06: wf full-scan out=10300 in_fresh=47030 n=2; toolUseResult 500.
    # n_subagent_files = 1 record usage toolUseResult (Agent tool) + 2 file wf.
    check("A4 session-summary include gli agenti workflow",
          s.get("wf_agents") == 2 and s.get("wf_output") == 10300
          and s.get("wf_input_fresh") == 47030
          and s.get("subagent_output") == 500 + 10300
          and s.get("n_subagent_files") == 3,
          f"summary={s} stderr={r.stderr[:200]!r}")

# ---- G: quota guard nel gate ----

def gate_env(pct=None, age_s=0, ceiling_cfg=None):
    home = fresh_home()
    proj = tempfile.mkdtemp(prefix="fd-wf-proj-")
    budget_open(home, proj, 100)
    if pct is not None:
        write_quota(home, pct, age_s)
    if ceiling_cfg is not None:
        (home / ".claude" / "fable-director" / "cost-checkpoint.json") \
            .write_text(json.dumps({"five_hour_pct_ceiling": ceiling_cfg}))
    return home, proj


home, proj = gate_env(pct=95.0)
r = gate(home, proj)
denies = db_events(home, "gate_deny")
dlg = list((home / ".claude" / "fable-director" / "delegations").glob("*.json")) \
    if (home / ".claude" / "fable-director" / "delegations").is_dir() else []
check("G1 nuovo Workflow a quota 95% → deny quota_guard, [DLG] intatto",
      decision(r) == "deny" and "quota guard" in r.stdout
      and any(d.get("kind") == "quota_guard" for d in denies) and not dlg,
      f"stdout={r.stdout[:200]!r} denies={denies}")

home, proj = gate_env(pct=95.0)
r = gate(home, proj, tool_input={"resumeFromRunId": "wf_abc123"})
check("G2 resume alla stessa quota → allow",
      decision(r) is None, f"stdout={r.stdout[:200]!r}")

home, proj = gate_env(pct=95.0, age_s=700)
r = gate(home, proj)
check("G3 snapshot stantio → allow (fail-open)",
      decision(r) is None, f"stdout={r.stdout[:200]!r}")

home, proj = gate_env(pct=50.0)
r = gate(home, proj)
check("G4 quota sotto soglia → allow",
      decision(r) is None, f"stdout={r.stdout[:200]!r}")

home, proj = gate_env(pct=95.0)
r = gate(home, proj, tool_name="Agent",
         tool_input={"subagent_type": "general-purpose"})
check("G5 Agent tool alla stessa quota → allow (guardia solo Workflow)",
      decision(r) is None, f"stdout={r.stdout[:200]!r}")

home, proj = gate_env(pct=75.0, ceiling_cfg=70)
r = gate(home, proj)
check("G6 soglia custom (70) da cost-checkpoint.json → deny a 75%",
      decision(r) == "deny", f"stdout={r.stdout[:200]!r}")

# ---- C: budget-open --agents ----

home = fresh_home()
with tempfile.TemporaryDirectory() as tmp:
    r = budget_open(home, tmp, 12000,
                    extra=["--agents", "16", "--expected-input", "120000",
                           "--route", "workflow"])
    check("C1 stima sotto l'ancora fan-out → warning",
          r.returncode == 0 and "fan-out anchor" in r.stdout
          and "320000" in r.stdout and "272000" in r.stdout,
          f"stdout={r.stdout[:300]!r}")

home = fresh_home()
with tempfile.TemporaryDirectory() as tmp:
    r = budget_open(home, tmp, 400000,
                    extra=["--agents", "16", "--expected-input", "500000"])
    check("C2 stima adeguata → nessun warning",
          r.returncode == 0 and "fan-out anchor" not in r.stdout,
          f"stdout={r.stdout[:300]!r}")

home = fresh_home()
with tempfile.TemporaryDirectory() as tmp:
    r = budget_open(home, tmp, 1000, extra=["--agents", "-3"])
    check("C3 --agents negativo → refused",
          r.returncode != 0 and "--agents" in (r.stderr + r.stdout),
          f"rc={r.returncode} stderr={r.stderr[:200]!r}")

print(f"\n{len(passed)} PASS, {len(failed)} FAIL")
sys.exit(1 if failed else 0)
