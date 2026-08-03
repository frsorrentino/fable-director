#!/usr/bin/env python3
"""Verifica deterministica — contabilità in input-equivalenti (fase taratura).

Contesto (audit 2026-08 su 2.389 sessioni reali, context-audit/DISTILLATO.md
del workspace fable-director): cache_read = ~72% del costo main in eq, output
= ~10% — l'enforcement su soli output token vede un decimo della spesa. Da qui
la fase di taratura: l'eq viene MISURATO e riportato ovunque (state, budget,
telemetria), ma le soglie 2×/3× restano sui token dichiarati.

Runs the REAL scripts against a throwaway HOME:

  E1 eq_tokens: formula sui moltiplicatori di listino
  E2 stop hook: state file accumula cr/cc (main + agenti workflow)
  E3 stop hook 3×: budget flagged porta actual_eq_tokens e il flag_payload
     telemetria porta actual_eq
  E4 enforcement INVARIATO: eq enorme ma output sotto stima → nessun blocco
     (l'eq non è enforcement, per design)
  E5 budget-close: actual_eq_tokens dallo state (setdefault, esplicito vince)
  E6 state di versione precedente senza cr/cc: nessun crash, eq riparte da 0
  E7 session-summary: payload con eq_tokens e output_cost_share

Usage: python3 tests/eq-accounting-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"

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


def load_fdt():
    spec = importlib.util.spec_from_file_location(
        "fdt", SCRIPTS / "fd-telemetry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def usage_line(out=0, inp=0, cr=0, cc=0, ts="2030-01-01T10:00:00Z"):
    return json.dumps({"timestamp": ts, "message": {"usage": {
        "output_tokens": out, "input_tokens": inp,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cc}}}) + "\n"


def make_session(tmp, main_out, main_cr, main_cc, wf_out=0, wf_cr=0):
    tr = Path(tmp) / "sess.jsonl"
    tr.write_text(usage_line(out=main_out, inp=100, cr=main_cr, cc=main_cc))
    if wf_out or wf_cr:
        adir = tr.with_suffix("") / "subagents" / "workflows" / "wf_a"
        adir.mkdir(parents=True)
        (adir / "agent-a1.jsonl").write_text(
            usage_line(out=wf_out, inp=50, cr=wf_cr,
                       ts="2030-01-01T10:01:00Z"))
    return tr


def stop_payload(tr, proj):
    return json.dumps({"cwd": proj, "transcript_path": str(tr),
                       "stop_hook_active": False})


def slug_budget(home, mod, proj):
    return Path(home) / ".claude" / "fable-director" / "budgets" / \
        f"{mod.cwd_slug(proj)}.json"


mod = load_fdt()

# E1 — formula
eq = mod.eq_tokens(1000, 100, 50000, 2000)
want = int(1000 * 1.0 + 100 * 5.0 + 50000 * 0.1 + 2000 * 1.25)
check("E1 eq_tokens formula", eq == want, f"got {eq}, want {want}")

# E2+E3 — stop hook: bust 3× su output, eq nel budget e in telemetria
home = Path(tempfile.mkdtemp(prefix="fd-eq-home-"))
proj = tempfile.mkdtemp(prefix="fd-eq-proj-")
r = run(SCRIPTS / "fd-telemetry.py",
        ["budget-open", "--task", "eq-test", "--expected-output", "100",
         "--cwd", proj], home)
assert r.returncode == 0, r.stderr
tr = make_session(tempfile.mkdtemp(), main_out=400, main_cr=90000,
                  main_cc=7000, wf_out=50, wf_cr=10000)
r = run(SCRIPTS / "stop-budget-check.py", [], home, stdin=stop_payload(tr, proj))
bfile = slug_budget(home, mod, proj)
budget = json.loads(bfile.read_text())
state = json.loads(bfile.with_name(bfile.stem + ".state.json").read_text())
check("E2 state accumula cr/cc main+wf",
      state.get("cr") == 90000 and state.get("cc") == 7000
      and state.get("wf_cr") == 10000,
      f"cr={state.get('cr')} cc={state.get('cc')} wf_cr={state.get('wf_cr')}")
# actual_in = (100+7000)main + 50wf; eq = (in-cc)*1 + out*5 + cr*0.1 + cc*1.25
exp_eq = int((150) * 1.0 + 450 * 5.0 + 100000 * 0.1 + 7000 * 1.25)
db = home / ".claude" / "fable-director" / "telemetry.db"
flag_eq = None
if db.is_file():
    con = sqlite3.connect(db)
    row = con.execute("SELECT payload FROM events WHERE event='budget_flag' "
                      "ORDER BY ts DESC LIMIT 1").fetchone()
    con.close()
    if row:
        flag_eq = json.loads(row[0]).get("actual_eq")
check("E3 flagged: actual_eq_tokens in budget e telemetria",
      budget.get("status") == "flagged"
      and budget.get("actual_eq_tokens") == exp_eq and flag_eq == exp_eq,
      f"status={budget.get('status')} eq={budget.get('actual_eq_tokens')} "
      f"want={exp_eq} flag_eq={flag_eq}")

# E4 — eq colossale ma output sotto stima: NESSUN enforcement sull'eq
home4 = Path(tempfile.mkdtemp(prefix="fd-eq-home4-"))
proj4 = tempfile.mkdtemp(prefix="fd-eq-proj4-")
run(SCRIPTS / "fd-telemetry.py",
    ["budget-open", "--task", "eq-quiet", "--expected-output", "100000",
     "--cwd", proj4], home4)
tr4 = make_session(tempfile.mkdtemp(), main_out=10, main_cr=5_000_000,
                   main_cc=100)
r4 = run(SCRIPTS / "stop-budget-check.py", [], home4,
         stdin=stop_payload(tr4, proj4))
b4 = json.loads(slug_budget(home4, mod, proj4).read_text())
check("E4 eq non enforca: output sotto stima → nessun block/flag",
      b4.get("status") == "open" and r4.stdout.strip() == "",
      f"status={b4.get('status')} stdout={r4.stdout[:120]}")

# E5 — budget-close consuntiva l'eq dallo state
r5 = run(SCRIPTS / "fd-telemetry.py",
         ["budget-close", "--outcome", "ok", "--cwd", proj4], home4)
b5 = json.loads(slug_budget(home4, mod, proj4).read_text())
# state inp = input+cache_create (200); l'eq scorpora: input puro = 100
exp5 = int(100 * 1.0 + 10 * 5.0 + 5_000_000 * 0.1 + 100 * 1.25)
check("E5 budget-close: actual_eq_tokens dallo state",
      b5.get("actual_eq_tokens") == exp5,
      f"got {b5.get('actual_eq_tokens')}, want {exp5}, rc={r5.returncode} "
      f"{r5.stderr[:120]}")

# E6 — state di versione precedente (senza cr/cc): nessun crash, eq da 0
home6 = Path(tempfile.mkdtemp(prefix="fd-eq-home6-"))
proj6 = tempfile.mkdtemp(prefix="fd-eq-proj6-")
run(SCRIPTS / "fd-telemetry.py",
    ["budget-open", "--task", "eq-legacy", "--expected-output", "100",
     "--cwd", proj6], home6)
tr6 = make_session(tempfile.mkdtemp(), main_out=400, main_cr=1000, main_cc=0)
b6file = slug_budget(home6, mod, proj6)
declared = json.loads(b6file.read_text()).get("declared_at")
legacy = {"declared": declared, "path": str(tr6), "off": 0, "out": 0,
          "inp": 0, "n_rec": 0, "n_usage": 0, "n_ts": 0, "last_ts": None}
b6file.with_name(b6file.stem + ".state.json").write_text(json.dumps(legacy))
r6 = run(SCRIPTS / "stop-budget-check.py", [], home6,
         stdin=stop_payload(tr6, proj6))
b6 = json.loads(b6file.read_text())
check("E6 state legacy senza cr/cc: nessun crash, eq presente",
      r6.returncode == 0 and b6.get("status") == "flagged"
      and isinstance(b6.get("actual_eq_tokens"), int),
      f"rc={r6.returncode} status={b6.get('status')} "
      f"eq={b6.get('actual_eq_tokens')} err={r6.stderr[:120]}")

# E7 — session-summary: eq_tokens e output_cost_share nel payload
home7 = Path(tempfile.mkdtemp(prefix="fd-eq-home7-"))
proj7 = tempfile.mkdtemp(prefix="fd-eq-proj7-")
tr7 = make_session(tempfile.mkdtemp(), main_out=100, main_cr=10000,
                   main_cc=500)
r7 = run(SCRIPTS / "fd-telemetry.py", ["session-summary"], home7,
         stdin=json.dumps({"cwd": proj7, "transcript_path": str(tr7),
                           "session_id": "eq-test-sess"}))
db7 = home7 / ".claude" / "fable-director" / "telemetry.db"
payload = {}
if db7.is_file():
    con = sqlite3.connect(db7)
    row = con.execute("SELECT payload FROM events WHERE "
                      "event='session_summary' ORDER BY ts DESC LIMIT 1"
                      ).fetchone()
    con.close()
    if row:
        payload = json.loads(row[0])
check("E7 session-summary: eq_tokens + output_cost_share",
      isinstance(payload.get("eq_tokens"), int) and payload["eq_tokens"] > 0
      and 0 < (payload.get("output_cost_share") or 0) <= 1,
      f"eq={payload.get('eq_tokens')} share={payload.get('output_cost_share')} "
      f"rc={r7.returncode} err={r7.stderr[:160]}")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
