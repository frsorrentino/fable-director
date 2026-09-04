#!/usr/bin/env python3
"""Verifica deterministica — economia dei Workflow nel gate (1.40).

  W1  window fit: 16 agenti dichiarati, finestra 5h al 22% → avviso "window fit
      tight" (≈1,9×), nessun deny
  W2  window fit: finestra al 60% → deny "cannot finish", con chunk e orario di reset
  W3  resume: mai deny (avviso al più); gli agenti già nel journal vengono sottratti
  W4  lint: agent() senza model: su sessione Fable, effort max ×3, 2 PDF, args come
      stringa JSON → quattro avvisi, delega permessa
  W5  heavy launcher: contesto 600k → avviso con l'eq di ri-cache
  W6  PostToolUse: record del lancio per runId (args + script)
  W7  resume hygiene: args diversi → avviso col valore del lancio; script cambiato →
      riga della prima differenza; identico → silenzio
  W8  meter: SubagentStop di un agente Workflow → delegation_outcome con run_id e
      five_hour_pct
  W9  taratura: coppie sintetiche → workflow_calibration "measured", report la stampa,
      il gate la cita
  W10 quota stantia (>10 min) → nessun fit check (fail-open)
  W11 statusline: sessions/<sid>.json (ctx_tokens, model) + five_hour_resets_at nel
      quota file

Usage: python3 tests/workflow-guard-verify.py   (exit 0 = all green)
"""
import hashlib
import importlib.util
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
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-wf-home-"))
proj = Path(tempfile.mkdtemp(prefix="fd-wf-proj-"))
os.environ["HOME"] = str(home)
os.environ.pop("CLAUDE_CONFIG_DIR", None)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)
FDT, GATE, METER = (str(SCRIPTS / n) for n in ("fd-telemetry.py", "pre-delegation-gate.py", "subagent-meter.py"))
BASE = home / ".claude" / "fable-director"
BASE.mkdir(parents=True, exist_ok=True)
ACCT = hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]
QUOTA = BASE / f"quota-{ACCT}.json"
SID = "s-wf-guard"
SESS_DIR = home / ".claude" / "projects" / "-proj" / SID
SESS_DIR.mkdir(parents=True)
TRANSCRIPT = SESS_DIR.with_suffix(".jsonl"); TRANSCRIPT.write_text("")
RESET_AT = int(time.time()) + 3600
RESET_HHMM = time.strftime("%H:%M", time.localtime(RESET_AT))


def run(args, stdin=None, extra=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CAVEMAN_STATUSLINE_SH="/nonexistent",
               COLUMNS="140")
    env.pop("CLAUDE_CONFIG_DIR", None); env.pop("CLAUDE_CODE_SESSION_ID", None); env.pop("FD_STATUSLINE_MODE", None)
    env.update(extra or {})
    return subprocess.run(args, capture_output=True, text=True, env=env, input=stdin, timeout=90)


def set_quota(pct, stale=False):
    QUOTA.write_text(json.dumps({"five_hour_used_pct": pct, "five_hour_resets_at": RESET_AT,
                                 "weekly_used_pct": 10.0}))
    if stale:
        old = time.time() - 1200
        os.utime(QUOTA, (old, old))


def set_session(ctx=600_000, model="claude-fable-5-1"):
    sd = BASE / "sessions"; sd.mkdir(exist_ok=True)
    (sd / f"{SID}.json").write_text(json.dumps({"session_id": SID, "model": model, "ctx_tokens": ctx,
                                                "ctx_size": 1000000, "ts": int(time.time())}))


def gate(tool_input, event="PreToolUse", **more):
    data = {"hook_event_name": event, "tool_name": "Workflow", "cwd": str(proj), "session_id": SID,
            "transcript_path": str(TRANSCRIPT), "tool_input": tool_input}
    data.update(more)
    r = run([sys.executable, GATE], json.dumps(data))
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return out, r


def deny_reason(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "") \
        if (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny" else None


SCRIPT = proj / "wf.js"
SCRIPT.write_text("""export const meta = { name: 'panel', description: 'x', phases: [{ title: 'Read' }] }
const F = { a: '/data/atti/Sentenza_2024.pdf', b: '/data/atti/Ricorso.PDF', c: '/data/ocr/Ricorso.txt' }
const readers = await parallel([1, 2, 3].map(i => () => agent(`read ${F.a} ${i}`, { effort: 'max', schema: {} })))
const judge = await agent('judge', { effort: 'max' })
return { readers, judge }
""")

r = run([sys.executable, FDT, "budget-open", "--task", "panel", "--expected-output", "400000",
         "--expected-input", "300000", "--agents", "16", "--route", "workflow", "--cost-ack", "--cwd", str(proj)])
assert r.returncode == 0, r.stderr
set_session(); set_quota(22)

# W1
out, r = gate({"scriptPath": str(SCRIPT), "args": {"data": "2026-09-03"}})
msg = out.get("systemMessage", "")
check("W1 window fit al 22%: avviso 'tight' (≈1,9×), nessun deny",
      deny_reason(out) is None and "window fit tight" in msg and "16 agents" in msg and "1.9×" in msg
      and "Delegation allowed" in msg, r.stdout + r.stderr)

# W2
set_quota(60)
out, r = gate({"scriptPath": str(SCRIPT), "args": {"data": "2026-09-03"}})
dr = deny_reason(out) or ""
check("W2 window fit al 60%: deny con chunk e orario di reset",
      "cannot finish in the current five-hour window" in dr and "chunk: at most 4 agents" in dr
      and f"reset at {RESET_HHMM}" in dr and "3.8×" in dr, r.stdout + r.stderr)

# W3 resume: journal con 10 agenti conclusi → 6 restanti; mai deny
jdir = SESS_DIR / "subagents" / "workflows" / "wf_test1"; jdir.mkdir(parents=True)
(jdir / "journal.jsonl").write_text("".join(json.dumps({"agent": i}) + "\n" for i in range(10)))
out, r = gate({"scriptPath": str(SCRIPT), "resumeFromRunId": "wf_test1", "args": {"data": "2026-09-03"}})
msg = out.get("systemMessage", "")
check("W3 resume oltre il residuo: avviso, mai deny; agenti nel journal sottratti (6 still to run)",
      deny_reason(out) is None and "resume: 6 agents still to run" in msg, r.stdout + r.stderr)

# W4 + W5 lint e heavy launcher (quota tornata al 22%)
set_quota(22)
out, r = gate({"scriptPath": str(SCRIPT), "args": '{"data": "2026-09-03"}'})
msg = out.get("systemMessage", "")
check("W4 lint: model: assente su Fable, effort max ×2+, 2 PDF, args stringa → avvisi, delega permessa",
      deny_reason(out) is None and "2 without `model:`" in msg and "claude-fable-5-1" in msg
      and "effort max/xhigh on 2 agent() calls" in msg and "2 .pdf path(s)" in msg
      and "args passed as a JSON string" in msg, r.stdout + r.stderr)
check("W5 heavy launcher: contesto 600k → avviso con 750k eq di ri-cache",
      "heavy launcher" in msg and "600k tokens" in msg and "750k eq" in msg, msg)

# W6 PostToolUse record
out, r = gate({"scriptPath": str(SCRIPT), "args": {"data": "2026-09-03"}}, event="PostToolUse",
              tool_response={"status": "launched", "runId": "wf_test1", "scriptPath": str(SCRIPT)})
rec_f = BASE / "workflows" / "wf_test1.json"
rec = json.loads(rec_f.read_text()) if rec_f.is_file() else {}
check("W6 PostToolUse: record del lancio con args e script",
      not out and rec.get("args") == {"data": "2026-09-03"} and rec.get("script") == SCRIPT.read_text()
      and rec.get("run_id") == "wf_test1", r.stdout + r.stderr + json.dumps(rec)[:200])

# W7 resume hygiene
out, r = gate({"scriptPath": str(SCRIPT), "resumeFromRunId": "wf_test1", "args": {"data": "2026-09-04"}})
m1 = out.get("systemMessage", "")
orig = SCRIPT.read_text(); SCRIPT.write_text(orig + "log('extra')\n")
out, r2 = gate({"scriptPath": str(SCRIPT), "resumeFromRunId": "wf_test1", "args": {"data": "2026-09-03"}})
m2 = out.get("systemMessage", "")
SCRIPT.write_text(orig)
out, r3 = gate({"scriptPath": str(SCRIPT), "resumeFromRunId": "wf_test1", "args": {"data": "2026-09-03"}})
m3 = out.get("systemMessage", "")
check("W7 resume hygiene: args diversi → valore del lancio; script cambiato → riga 6; identico → silenzio",
      "args differ" in m1 and '{"data": "2026-09-03"} then' in m1
      and "script changed" in m2 and "line 6" in m2
      and "resume hygiene" not in m3, m1 + "\n" + m2 + "\n" + m3)

# W8 meter
tr = jdir / "agent-a1.jsonl"
tr.write_text(json.dumps({"type": "assistant", "timestamp": "2026-09-03T10:00:00Z",
                          "message": {"id": "m1", "model": "claude-sonnet-5", "usage": {
                              "input_tokens": 1000, "output_tokens": 2000, "cache_read_input_tokens": 0,
                              "cache_creation_input_tokens": 30000},
                              "content": [{"type": "text", "text": "DONE"}]}}) + "\n")
r = run([sys.executable, METER], json.dumps({
    "hook_event_name": "SubagentStop", "session_id": SID, "cwd": str(proj), "agent_id": "a1",
    "agent_type": "workflow-subagent", "agent_transcript_path": str(tr), "last_assistant_message": "DONE"}))
con = sqlite3.connect(str(BASE / "telemetry.db"))
row = con.execute("SELECT payload FROM events WHERE event='delegation_outcome' ORDER BY id DESC LIMIT 1").fetchone()
con.close()
pl = json.loads(row[0]) if row else {}
check("W8 meter: delegation_outcome con run_id e five_hour_pct",
      pl.get("run_id") == "wf_test1" and pl.get("five_hour_pct") == 22 and pl.get("eq"), r.stderr + json.dumps(pl))

# W9 taratura: 5 coppie (Δ5%, 2 agenti da 300k tra i campioni) → finestra 12M eq
for i, pct in enumerate((10, 15, 20, 25, 30, 35)):
    fdt.log_event("delegation_outcome", {"agent_type": "workflow-subagent", "eq": 300000,
                                         "five_hour_pct": pct}, session_id="cal-s", cwd=str(proj))
    if i < 5:
        fdt.log_event("delegation_outcome", {"agent_type": "workflow-subagent", "eq": 300000},
                      session_id="cal-s", cwd=str(proj))
cal = fdt.workflow_calibration(days=1)
rep = run([sys.executable, FDT, "report", "--days", "1"]).stdout
set_quota(75)  # residuo 3M eq contro 4,8M stimati → avviso che cita la misura
out, r = gate({"scriptPath": str(SCRIPT), "args": {"data": "2026-09-03"}})
msg = out.get("systemMessage", "")
check("W9 taratura misurata: finestra 12M eq da 5 coppie, agente 300k, report e gate la citano",
      cal["window_src"].startswith("measured") and abs(cal["window_eq"] - 12_000_000) < 1
      and cal["agent_src"].startswith("measured") and cal["agent_eq"] == 300000 and cal["n_pairs"] == 5
      and "Workflow window calibration" in rep and "measured (5 five-hour quota deltas" in msg,
      json.dumps(cal) + "\n" + msg[-400:])

# W10 quota stantia → fail-open
set_quota(60, stale=True)
out, r = gate({"scriptPath": str(SCRIPT), "args": {"data": "2026-09-03"}})
check("W10 snapshot quota stantio: nessun fit check",
      deny_reason(out) is None and "window fit" not in out.get("systemMessage", ""), r.stdout)

# W11 statusline scrive sessions/<sid>.json e five_hour_resets_at
stdin = json.dumps({"model": {"display_name": "Fable 5.1", "id": "claude-fable-5-1"}, "session_id": SID,
                    "cwd": str(proj), "effort": {"level": "high"},
                    "context_window": {"used_percentage": 30, "context_window_size": 1000000,
                                       "current_usage": {"input_tokens": 100, "cache_creation_input_tokens": 5000,
                                                         "cache_read_input_tokens": 295000, "output_tokens": 50}},
                    "rate_limits": {"five_hour": {"used_percentage": 22, "resets_at": RESET_AT},
                                    "seven_day": {"used_percentage": 10, "resets_at": RESET_AT + 300000}}})
r = run(["bash", str(SCRIPTS / "statusline-ctx.sh")], stdin)
snap = json.loads((BASE / "sessions" / f"{SID}.json").read_text())
q = json.loads(QUOTA.read_text())
check("W11 statusline: sessions/<sid>.json con ctx_tokens 300100 e modello; quota con five_hour_resets_at",
      snap.get("ctx_tokens") == 300100 and snap.get("model") == "claude-fable-5-1" and snap.get("effort") == "high"
      and q.get("five_hour_resets_at") == RESET_AT and q.get("five_hour_used_pct") == 22,
      json.dumps(snap) + "\n" + json.dumps(q) + "\n" + r.stderr[-300:])

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
