#!/usr/bin/env python3
"""Verifica deterministica — esito per delega letto dall'hook (1.39.0, C1.1).

Il token di stato del contratto (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT /
BLOCKED / ABSTAIN) viene letto a SubagentStop da last_assistant_message (o
dal transcript dell'agente), mai inferito; usage e modello dal transcript
dell'agente; eq alla tariffa del suo modello; rule of 3 diagnosticata
dall'hook al secondo fallimento dello stesso tipo.

  O1 DONE: evento delegation_outcome {status ok, model, turns, eq}; exit 0
  O2 BLOCKED con riga di blocco infra: status blocked, blocker, class infra
  O3 nessun token: status unknown, mai inferito
  O4 secondo BLOCKED dello stesso tipo: exit 1 + avviso rule-of-3 su stderr,
     evento escalation {class, auto}
  O5 fallback: last_assistant_message assente → letto dal transcript
  O6 state file: outcomes, failures_by_type, last_blocked (max 5)
  O7 report: sezione "Delegation outcomes" con ok/failed/no status token

Usage: python3 tests/delegation-outcome-verify.py   (exit 0 = all green)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
METER = SCRIPTS / "subagent-meter.py"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(args, home, stdin):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, env=env, input=stdin, timeout=60)


home = Path(tempfile.mkdtemp(prefix="fd-outcome-home-"))
tmp = Path(tempfile.mkdtemp(prefix="fd-outcome-tr-"))
SID = "sess-outcome-1"


def transcript(name, text, model="claude-sonnet-5", turns=2):
    p = tmp / f"agent-{name}.jsonl"
    lines = []
    for i in range(turns):
        mid = f"msg_{name}_{i}"
        u = {"input_tokens": 5, "output_tokens": 100, "cache_read_input_tokens": 8000 * i,
             "cache_creation_input_tokens": 8000 if i == 0 else 300}
        # due righe per messaggio (thinking + text) con lo stesso usage: dedup
        lines.append(json.dumps({"type": "assistant", "message": {
            "id": mid, "model": model, "usage": u,
            "content": [{"type": "thinking", "thinking": ""}]}}))
        lines.append(json.dumps({"type": "assistant", "message": {
            "id": mid, "model": model, "usage": u,
            "content": [{"type": "text", "text": text if i == turns - 1 else "working"}]}}))
    p.write_text("\n".join(lines) + "\n")
    return p


def stop(name, text, atype="fable-director:fd-executor", pass_text=True, **kw):
    tr = transcript(name, text, **kw)
    d = {"hook_event_name": "SubagentStop", "session_id": SID, "cwd": str(tmp),
         "agent_id": name, "agent_type": atype, "agent_transcript_path": str(tr),
         "stop_hook_active": False}
    if pass_text:
        d["last_assistant_message"] = text
    return run([str(METER)], home, json.dumps(d))


def events(kind):
    con = sqlite3.connect(home / ".claude" / "fable-director" / "telemetry.db")
    rows = [json.loads(p) for (p,) in con.execute(
        "select payload from events where event=? order by ts, id", (kind,))]
    con.close()
    return rows


# O1
r = stop("a1", '{"file": "x.py", "n_def": 8}\nDONE')
ev = events("delegation_outcome")
want_eq = int(2 * 5 * 1.0 + 200 * 5.0 + 8000 * 0.1 + (8000 + 300) * 1.25)
check("O1 DONE → status ok, model, turns 2 (dedup), eq alla tariffa sonnet",
      r.returncode == 0 and len(ev) == 1 and ev[0]["status"] == "ok"
      and ev[0]["model"] == "claude-sonnet-5" and ev[0]["turns"] == 2
      and ev[0]["eq"] == want_eq and ev[0]["output_tokens"] == 200,
      f"rc={r.returncode} {ev} want_eq={want_eq} err={r.stderr}")

# O2
r = stop("a2", "Tried curl three times.\nError: connection timed out after 30s\nBLOCKED")
ev = events("delegation_outcome")
check("O2 BLOCKED → status blocked, blocker = riga prima del token, exit 0 (primo)",
      r.returncode == 0 and ev[-1]["status"] == "blocked"
      and ev[-1]["blocker"].startswith("Error: connection timed out"),
      f"rc={r.returncode} {ev[-1]}")

# O3
r = stop("a3", "Here is the analysis, everything looks fine and complete.")
ev = events("delegation_outcome")
check("O3 nessun token → unknown, mai inferito dal tono",
      ev[-1]["status"] == "unknown" and ev[-1]["blocker"] is None, str(ev[-1]))

# O4 secondo BLOCKED stesso tipo → rule of 3
r = stop("a4", "grep found nothing after 3 attempts\nBLOCKED")
esc = events("escalation")
check("O4 secondo BLOCKED fd-executor → exit 1, avviso rule-of-3, escalation capability auto",
      r.returncode == 1 and "2 BLOCKED from fable-director:fd-executor" in r.stderr
      and "No identical 4th attempt" in r.stderr and len(esc) == 1
      and esc[0]["class"] == "capability" and esc[0]["auto"] is True and esc[0]["n"] == 2,
      f"rc={r.returncode} err={r.stderr!r} esc={esc}")
# O5 fallback dal transcript
r = stop("a5", "Fresh context, verified.\nABSTAIN", atype="fable-director:fd-verifier",
         pass_text=False, model="claude-fable-5-1")
ev = events("delegation_outcome")
check("O5 senza last_assistant_message → token dal transcript, model fable, eq a 0.025",
      ev[-1]["status"] == "abstain" and ev[-1]["model"] == "claude-fable-5-1"
      and ev[-1]["eq"] == int(2 * 5 * 1.0 + 200 * 5.0 + 8000 * 0.025 + 8300 * 1.25),
      str(ev[-1]))

# O6 state file
st = json.loads((home / ".claude" / "fable-director" / "subagents" / f"{SID}.json").read_text())
check("O6 state: outcomes, failures_by_type, last_blocked",
      st["outcomes"] == {"ok": 1, "blocked": 2, "unknown": 1, "abstain": 1}
      and st["failures_by_type"]["fable-director:fd-executor"] == 2
      and len(st["last_blocked"]) == 3 and st["last_blocked"][-1]["status"] == "abstain",
      json.dumps({k: st[k] for k in ("outcomes", "failures_by_type", "last_blocked")}))

# O7 report
r = run([str(SCRIPTS / "fd-telemetry.py"), "report", "--days", "1"], home, None)
check("O7 report: sezione Delegation outcomes con conteggi e diagnosi",
      "Delegation outcomes" in r.stdout
      and "fable-director:fd-executor on claude-sonnet-5: 4 runs — ok 1, failed 2, no status token 1" in r.stdout
      and "rule-of-3 diagnoses (auto): capability ×1" in r.stdout,
      r.stdout[-900:] + r.stderr)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
