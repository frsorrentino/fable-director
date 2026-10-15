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
  E8 scritture in cache da un'ora a 2×, da cinque minuti o non divise a 1,25×
  E9 stop hook: state accumula cc1h (main + agenti workflow), eq del budget a 2×
  E10 session-summary: cache_creation_1h nel payload, eq a 2× su quella quota
  E11 budget-close: eq dallo state con cc1h
  E12 subagent-meter: eq dell'agente con la quota da un'ora

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


def split_line(out=0, inp=0, cr=0, cc=0, cc1h=0, ts="2030-01-01T10:00:00Z",
               model=None, mid=None):
    """Usage come lo scrive Claude Code dalla 2.1.2xx: il totale delle scritture
    in cache e, in usage.cache_creation, la quota per durata."""
    msg = {"usage": {"output_tokens": out, "input_tokens": inp,
                     "cache_read_input_tokens": cr,
                     "cache_creation_input_tokens": cc,
                     "cache_creation": {"ephemeral_1h_input_tokens": cc1h,
                                        "ephemeral_5m_input_tokens": cc - cc1h}}}
    if model:
        msg["model"] = model
    if mid:
        msg["id"] = mid
    return json.dumps({"timestamp": ts, "type": "assistant", "message": msg}) + "\n"


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

# E8 — durata delle scritture in cache (listino: un'ora 2×, cinque minuti 1,25×)
eq8 = mod.eq_tokens(0, 0, 0, 1000, cc_1h=600)
check("E8 eq_tokens: quota da un'ora a 2×, resto a 1,25×",
      eq8 == int(400 * 1.25 + 600 * 2.0), f"got {eq8}")
check("E8b cc_1h oltre cc tagliato a cc; senza split tutto a 1,25×",
      mod.eq_tokens(0, 0, 0, 100, cc_1h=500) == 200
      and mod.eq_tokens(0, 0, 0, 100) == 125,
      f"{mod.eq_tokens(0, 0, 0, 100, cc_1h=500)} / {mod.eq_tokens(0, 0, 0, 100)}")
check("E8c cache_create_1h legge usage.cache_creation, 0 senza split",
      mod.cache_create_1h({"cache_creation": {"ephemeral_1h_input_tokens": 70,
                                              "ephemeral_5m_input_tokens": 30}}) == 70
      and mod.cache_create_1h({"cache_creation_input_tokens": 100}) == 0
      and mod.cache_create_1h({"cache_creation": "x"}) == 0)

# E9 — stop hook: cc1h nello state (main + agenti workflow) ed eq del budget
home9 = Path(tempfile.mkdtemp(prefix="fd-eq-home9-"))
proj9 = tempfile.mkdtemp(prefix="fd-eq-proj9-")
run(SCRIPTS / "fd-telemetry.py",
    ["budget-open", "--task", "eq-1h", "--expected-output", "100",
     "--cwd", proj9], home9)
tr9 = Path(tempfile.mkdtemp()) / "sess.jsonl"
tr9.write_text(split_line(out=400, inp=100, cr=90000, cc=7000, cc1h=7000))
adir = tr9.with_suffix("") / "subagents" / "workflows" / "wf_a"
adir.mkdir(parents=True)
(adir / "agent-a1.jsonl").write_text(
    split_line(out=50, inp=50, cr=10000, cc=1000, cc1h=400,
               ts="2030-01-01T10:01:00Z"))
run(SCRIPTS / "stop-budget-check.py", [], home9, stdin=stop_payload(tr9, proj9))
b9file = slug_budget(home9, mod, proj9)
b9 = json.loads(b9file.read_text())
st9 = json.loads(b9file.with_name(b9file.stem + ".state.json").read_text())
exp9 = int(150 * 1.0 + 450 * 5.0 + 100000 * 0.1 + 600 * 1.25 + 7400 * 2.0)
check("E9 state accumula cc1h main+wf",
      st9.get("cc1h") == 7000 and st9.get("wf_cc1h") == 400,
      f"cc1h={st9.get('cc1h')} wf_cc1h={st9.get('wf_cc1h')}")
check("E9b eq del budget: scritture da un'ora a 2×",
      b9.get("actual_eq_tokens") == exp9,
      f"got {b9.get('actual_eq_tokens')} want {exp9} status={b9.get('status')}")

# E10 — session-summary
home10 = Path(tempfile.mkdtemp(prefix="fd-eq-home10-"))
proj10 = tempfile.mkdtemp(prefix="fd-eq-proj10-")
tr10 = Path(tempfile.mkdtemp()) / "sess.jsonl"
tr10.write_text(split_line(out=100, inp=100, cr=10000, cc=500, cc1h=500))
r10 = run(SCRIPTS / "fd-telemetry.py", ["session-summary"], home10,
          stdin=json.dumps({"cwd": proj10, "transcript_path": str(tr10),
                            "session_id": "eq-1h-sess"}))
p10 = {}
db10 = home10 / ".claude" / "fable-director" / "telemetry.db"
if db10.is_file():
    con = sqlite3.connect(db10)
    row = con.execute("SELECT payload FROM events WHERE event='session_summary'"
                      " ORDER BY ts DESC LIMIT 1").fetchone()
    con.close()
    p10 = json.loads(row[0]) if row else {}
exp10 = int(100 * 1.0 + 100 * 5.0 + 10000 * 0.1 + 500 * 2.0)
check("E10 session-summary: cache_creation_1h ed eq a 2×",
      p10.get("cache_creation_1h") == 500 and p10.get("eq_tokens") == exp10,
      f"cc1h={p10.get('cache_creation_1h')} eq={p10.get('eq_tokens')} want {exp10} "
      f"err={r10.stderr[:160]}")

# E11 — budget-close: eq dallo state con cc1h (nessun flag: lo scrive la close)
home11 = Path(tempfile.mkdtemp(prefix="fd-eq-home11-"))
proj11 = tempfile.mkdtemp(prefix="fd-eq-proj11-")
run(SCRIPTS / "fd-telemetry.py",
    ["budget-open", "--task", "eq-1h-close", "--expected-output", "100000",
     "--cwd", proj11], home11)
tr11 = Path(tempfile.mkdtemp()) / "sess.jsonl"
tr11.write_text(split_line(out=10, inp=100, cr=1000, cc=1000, cc1h=1000))
run(SCRIPTS / "stop-budget-check.py", [], home11, stdin=stop_payload(tr11, proj11))
run(SCRIPTS / "fd-telemetry.py", ["budget-close", "--outcome", "ok",
                                  "--cwd", proj11], home11)
b11 = json.loads(slug_budget(home11, mod, proj11).read_text())
exp11 = int(100 * 1.0 + 10 * 5.0 + 1000 * 0.1 + 1000 * 2.0)
check("E11 budget-close: eq dallo state con la quota da un'ora",
      b11.get("actual_eq_tokens") == exp11,
      f"got {b11.get('actual_eq_tokens')} want {exp11}")

# E12 — subagent-meter: eq dell'agente (Sonnet 5.5: cache_read 0,05×)
spec = importlib.util.spec_from_file_location("sam", SCRIPTS / "subagent-meter.py")
sam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sam)
tr12 = Path(tempfile.mkdtemp()) / "agent.jsonl"
tr12.write_text(split_line(out=10, inp=10, cr=1000, cc=100, cc1h=100,
                           model="claude-sonnet-5-5", mid="m1"))
model12, _, tot12 = sam.agent_usage(tr12)
eq12 = sam.eq_of(model12, tot12)
exp12 = int(10 * 1.0 + 10 * 5.0 + 1000 * 0.05 + 100 * 2.0)
check("E12 subagent-meter: eq con la quota da un'ora e la tariffa del modello",
      eq12 == exp12, f"got {eq12} want {exp12} tot={tot12}")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
