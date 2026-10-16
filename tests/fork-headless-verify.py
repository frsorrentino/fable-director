#!/usr/bin/env python3
"""Verifica deterministica — i fork passano dalla guardia anche in `claude -p`.

Da Claude Code 2.1.302 i fork (`subagent_type: "fork"`) sono accesi di default in
`claude -p`, nell'Agent SDK, nell'app desktop e in VS Code. Una chiamata Agent con
subagent_type fork arriva all'hook PreToolUse come ogni altra delega: la guardia
non deve trattarla come esente. Prova reale del 16/10 (2.1.302, `claude -p
--model haiku`): senza budget gate_deny no_budget con subagent_type fork; con
budget aperto il fork parte, [DLG] conta 1 e il meter registra agent_type fork.

  F1 matcher PreToolUse della guardia copre Agent, senza filtri per modalita'
  F2 fork senza budget aperto -> deny no_budget, gate_deny con subagent_type fork
  F3 fork con budget aperto -> permesso, conteggio [DLG] della sessione a 1
  F4 secondo fork nella stessa sessione -> conteggio a 2
  F5 fork esente dal lint del contratto (eredita il contesto), ma contato lo stesso

Usage: python3 tests/fork-headless-verify.py   (exit 0 = all green)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "fable-director"
SCRIPTS = PLUGIN / "scripts"
FDT, GATE = str(SCRIPTS / "fd-telemetry.py"), str(SCRIPTS / "pre-delegation-gate.py")
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-fork-home-"))
proj = Path(tempfile.mkdtemp(prefix="fd-fork-proj-"))
BASE = home / ".claude" / "fable-director"
SID = "s-fork-headless"


def run(args, stdin=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "FD_STATUSLINE_MODE"):
        env.pop(k, None)
    return subprocess.run(args, capture_output=True, text=True, env=env, input=stdin,
                          cwd=str(proj), timeout=90)


def gate(prompt="reply OK"):
    # Payload come quello di `claude -p` 2.1.302: tool Agent, subagent_type fork
    data = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(proj),
            "session_id": SID, "permission_mode": "dontAsk",
            "tool_input": {"subagent_type": "fork", "description": "fork", "prompt": prompt}}
    r = run([sys.executable, GATE], json.dumps(data))
    return (json.loads(r.stdout) if r.stdout.strip() else {}), r


def decision(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


def dlg():
    f = BASE / "delegations" / f"{SID}.json"
    return json.loads(f.read_text()) if f.is_file() else {}


# F1
hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
gates = [h for h in hooks if any("pre-delegation-gate.py" in x.get("command", "")
                                 for x in h.get("hooks", []))]
check("F1 matcher della guardia copre Agent",
      len(gates) == 1 and "Agent" in gates[0]["matcher"].split("|")
      and not any(k in gates[0] for k in ("if", "when")),
      str(gates))

# F2
out, r = gate()
reason = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
rows = []
db = BASE / "telemetry.db"
if db.is_file():
    con = sqlite3.connect(db)
    rows = [json.loads(p) for (p,) in con.execute(
        "SELECT payload FROM events WHERE event='gate_deny' AND session_id=?", (SID,))]
    con.close()
check("F2 fork senza budget negato, gate_deny con subagent_type fork",
      decision(out) == "deny" and "no_budget" in reason and dlg() == {}
      and any(x.get("kind") == "no_budget" and x.get("subagent_type") == "fork" for x in rows),
      f"out={out} rows={rows} stderr={r.stderr[-300:]}")

# F3
o = run([sys.executable, FDT, "budget-open", "--task", "fork headless", "--expected-output", "200",
         "--expected-input", "2000", "--route", "agent", "--paths", "none",
         "--verify", "test -d .", "--reason", "asse 6 fork"])
out, r = gate()
check("F3 fork con budget aperto permesso e contato in [DLG]",
      o.returncode == 0 and decision(out) not in ("deny", "ask") and dlg() == {"inherit": 1},
      f"open={o.returncode} {o.stderr[-300:]} out={out} dlg={dlg()}")

# F4
out, r = gate("second fork")
check("F4 secondo fork contato", decision(out) not in ("deny", "ask") and dlg() == {"inherit": 2},
      f"out={out} dlg={dlg()}")

# F5
msg = out.get("systemMessage", "")
check("F5 fork senza avviso di contratto ma contato",
      "contract" not in msg.lower() and dlg().get("inherit") == 2, msg)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
