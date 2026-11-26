#!/usr/bin/env python3
"""Verifica deterministica — fd-executor resta su sonnet (1.65.0).

Fino alla 1.64.9 il modulo hooks (mod) riscriveva il modello della chiamata;
la revisione della directory chiede che una mod lasci agent.spawn invariato,
quindi il vincolo e' un deny del gate classico (regola executor_model).

  E1 fd-executor con model "opus"             -> deny executor_model, dice di riprovare senza model
  E2 fd-executor con model "claude-opus-5-5"  -> deny executor_model (id completo, stessa famiglia)
  E3 fd-executor senza model                  -> nessun deny executor_model
  E4 fd-executor con model "sonnet"           -> nessun deny
  E5 fd-executor con model "claude-sonnet-5-5"-> nessun deny
  E6 general-purpose con model "opus"         -> nessun deny (il vincolo vale solo per fd-executor)
  E7 fd-executor ripreso (resume) con "opus"  -> nessun deny (tiene il modello su cui gira)
  E8 deny registrato in telemetria come gate_deny kind=executor_model
  E9 la mod non c'e' piu': nessuna voce modules in hooks.json, nessuna cartella hooks/mods

Usage: python3 tests/executor-pin-verify.py   (exit 0 = all green)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fable-director"
SCRIPTS = ROOT / "scripts"
FDT, GATE = str(SCRIPTS / "fd-telemetry.py"), str(SCRIPTS / "pre-delegation-gate.py")
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {str(evidence)[:600]}"))


home = Path(tempfile.mkdtemp(prefix="fd-pin-home-"))
proj = Path(tempfile.mkdtemp(prefix="fd-pin-proj-"))
SID = "s-pin"


def run(args, stdin=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "FD_REMIND_GAP_S"):
        env.pop(k, None)
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          env=env, input=stdin, timeout=120)


r = run([FDT, "budget-open", "--task", "pin", "--expected-output", "100000", "--cwd", str(proj),
         "--verify", "test -d .", "--route", "agent", "--paths", "none", "--cost-ack"])
assert r.returncode == 0, r.stderr


def gate(uid, subagent_type, model=None, resume=None):
    ti = {"subagent_type": subagent_type, "prompt": "x", "description": "x"}
    if model is not None:
        ti["model"] = model
    if resume is not None:
        ti["resume"] = resume
    data = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(proj),
            "session_id": SID, "tool_use_id": uid, "tool_input": ti}
    out = run([GATE], json.dumps(data))
    o = json.loads(out.stdout) if out.stdout.strip() else {}
    h = o.get("hookSpecificOutput") or {}
    return h.get("permissionDecisionReason", "") if h.get("permissionDecision") == "deny" else None


EX = "fable-director:fd-executor"
RULE = "[fable-director rule: executor_model]"

d = gate("e1", EX, "opus")
check("E1 fd-executor + opus -> deny executor_model, retry without model",
      d is not None and d.startswith(RULE) and "without the model parameter" in d, d)
d = gate("e2", EX, "claude-opus-5-5")
check("E2 fd-executor + full opus id -> deny executor_model", d is not None and d.startswith(RULE), d)
for name, uid, st, model, resume in (
        ("E3 fd-executor without model -> no executor_model deny", "e3", EX, None, None),
        ("E4 fd-executor + sonnet -> no deny", "e4", EX, "sonnet", None),
        ("E5 fd-executor + claude-sonnet-5-5 -> no deny", "e5", EX, "claude-sonnet-5-5", None),
        ("E6 general-purpose + opus -> no deny", "e6", "general-purpose", "opus", None),
        ("E7 resumed fd-executor + opus -> no deny", "e7", EX, "opus", "agent-123")):
    d = gate(uid, st, model, resume)
    check(name, d is None or not d.startswith(RULE), d)

db = home / ".claude" / "fable-director" / "telemetry.db"
kinds = []
if db.is_file():
    con = sqlite3.connect(db)
    for (payload,) in con.execute("SELECT payload FROM events WHERE event='gate_deny'"):
        try:
            kinds.append(json.loads(payload).get("kind"))
        except (TypeError, ValueError):
            pass
check("E8 deny logged as gate_deny kind=executor_model", kinds.count("executor_model") == 2, kinds)

hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
check("E9 no mod shipped: no modules entry, no hooks/mods folder",
      "modules" not in hooks and not (ROOT / "hooks" / "mods").exists(),
      {"modules": hooks.get("modules"), "mods_dir": (ROOT / "hooks" / "mods").exists()})

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
