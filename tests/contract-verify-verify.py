#!/usr/bin/env python3
"""Verifica deterministica — linter del contratto nel gate (C1.5) e verify
eseguibile allo Stop (C1.6 / E7), 1.39.0.

  L1 gate: prompt Agent senza Files/Verification e senza status token → una
     riga di avviso (systemMessage), MAI deny
  L2 gate: contratto completo (anche intestazioni italiane) → silenzio
  L3 gate: fork / Explore / prompt breve esenti
  V1 Stop: --verify comando che fallisce → state.verify_rc=1, systemMessage
     "verification failed" con exit e ultima riga; un solo JSON su stdout
  V2 Stop: nessuna scrittura nuova → non rieseguito (verify_at invariato)
  V3 Stop: dopo una scrittura, comando che passa → "passed again", rc 0
  V4 Stop: --verify in prosa → mai eseguito, nessun verify_rc
  V5 Stop: comando che supera il timeout → rc 'timeout' (timeout 60 s
     abbassato via FD_VERIFY_TIMEOUT_S per il test)
  V6 budget-close: verify_rc nella ricevuta ("verification passed")

Usage: python3 tests/contract-verify-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
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


def run(args, home, stdin=None, env_extra=None, cwd=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, env=env, input=stdin, timeout=90, cwd=cwd)


home = Path(tempfile.mkdtemp(prefix="fd-cv-home-"))
proj = Path(tempfile.mkdtemp(prefix="fd-cv-proj-"))
os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)
FDT = str(SCRIPTS / "fd-telemetry.py")
GATE = str(SCRIPTS / "pre-delegation-gate.py")
STOP = str(SCRIPTS / "stop-budget-check.py")

# ---- gate lint
r = run([FDT, "budget-open", "--task", "lint-test", "--expected-output", "100",
         "--verify", "python3 check.py", "--cwd", str(proj)], home)
assert r.returncode == 0, r.stderr


def gate(prompt, stype="general-purpose"):
    return run([GATE], home, json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(proj),
        "session_id": "s-lint", "tool_input": {"subagent_type": stype, "prompt": prompt,
                                              "description": "x"}}))


vague = "Occupati del refactor del modulo di pagamento e sistemare tutto quello che trovi, " * 4
r = gate(vague)
out = json.loads(r.stdout) if r.stdout.strip() else {}
check("L1 contratto vago → avviso con le parti mancanti, nessun deny",
      "permissionDecision" not in json.dumps(out)
      and "delegation contract incomplete" in out.get("systemMessage", "")
      and "Objective" in out.get("systemMessage", "") and "Files" in out.get("systemMessage", "")
      and "no status token" in out.get("systemMessage", ""),
      r.stdout + r.stderr)
full_it = ("Obiettivo: contare le funzioni.\nFile: src/a.py (sola lettura).\n"
           "Interfacce: JSON {n_def}.\nVincoli: nessuna modifica, max 200 token.\n"
           "Verifica: grep -c '^def ' src/a.py e riporta l'output. Chiudi con DONE o BLOCKED. ")
r = gate(full_it * 2)
out = json.loads(r.stdout) if r.stdout.strip() else {}
check("L2 contratto completo (intestazioni italiane) → nessun avviso di contratto",
      "delegation contract incomplete" not in out.get("systemMessage", ""), r.stdout)
r1 = gate(vague, stype="fork"); r2 = gate(vague, stype="Explore"); r3 = gate("breve " * 10)
check("L3 fork / Explore / prompt breve esenti",
      all("delegation contract incomplete" not in (x.stdout or "") for x in (r1, r2, r3)),
      r1.stdout + r2.stdout + r3.stdout)

# ---- verify eseguibile allo Stop
(proj / "check.py").write_text("import sys\nprint('checking')\nsys.exit(1)\n")
tr = proj / "sess.jsonl"


def usage(out, ts, write=None):
    rec = {"timestamp": ts, "type": "assistant", "message": {
        "model": "claude-sonnet-5", "usage": {"output_tokens": out, "input_tokens": 1,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0}}}
    if write:
        rec["message"]["content"] = [{"type": "tool_use", "name": "Write",
                                      "input": {"file_path": write, "content": "x"}}]
    return json.dumps(rec) + "\n"


tr.write_text(usage(10, "2030-01-01T10:00:00Z", write=str(proj / "a.py")))
payload = json.dumps({"cwd": str(proj), "transcript_path": str(tr),
                      "stop_hook_active": False, "session_id": "s-lint"})
r = run([STOP], home, payload)
bfile = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(str(proj))}.json"
sfile = bfile.with_name(bfile.stem + ".state.json")
st = json.loads(sfile.read_text())
outs = [l for l in r.stdout.splitlines() if l.strip()]
msg = json.loads(outs[0]) if outs else {}
check("V1 comando che fallisce → verify_rc 1, un solo JSON, 'verification failed' con exit",
      st.get("verify_rc") == 1 and len(outs) == 1
      and "FD ✗ verification failed: python3 check.py (exit 1)" in msg.get("systemMessage", "")
      and "do not report the task as finished" in msg.get("systemMessage", ""),
      r.stdout + r.stderr + json.dumps(st)[:300])
at1 = st.get("verify_at")
r = run([STOP], home, payload)
st = json.loads(sfile.read_text())
check("V2 nessuna scrittura nuova → non rieseguito, nessun messaggio",
      st.get("verify_at") == at1 and not r.stdout.strip(), r.stdout)
(proj / "check.py").write_text("print('ok')\n")
with open(tr, "a") as fh:
    fh.write(usage(10, "2030-01-01T10:20:00Z", write=str(proj / "b.py")))
r = run([STOP], home, payload)
st = json.loads(sfile.read_text())
msg = json.loads(r.stdout.strip().splitlines()[0]) if r.stdout.strip() else {}
check("V3 nuova scrittura + comando che passa → rc 0, 'passed again'",
      st.get("verify_rc") == 0 and "verification passed again" in msg.get("systemMessage", ""),
      r.stdout + json.dumps(st)[:300])

# V4 prosa
proj2 = Path(tempfile.mkdtemp(prefix="fd-cv-proj2-"))
run([FDT, "budget-open", "--task", "prosa", "--expected-output", "100",
     "--verify", "checklist: 3 file aggiornati, test verdi", "--cwd", str(proj2)], home)
tr2 = proj2 / "s.jsonl"; tr2.write_text(usage(10, "2030-01-01T10:00:00Z", write=str(proj2 / "a.py")))
r = run([STOP], home, json.dumps({"cwd": str(proj2), "transcript_path": str(tr2),
                                  "stop_hook_active": False}))
b2 = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(str(proj2))}.json"
st2 = json.loads(b2.with_name(b2.stem + ".state.json").read_text())
check("V4 verify in prosa → mai eseguito", "verify_rc" not in st2 and not r.stdout.strip(),
      json.dumps(st2)[:200] + r.stdout)

# V5 timeout
proj3 = Path(tempfile.mkdtemp(prefix="fd-cv-proj3-"))
run([FDT, "budget-open", "--task", "slow", "--expected-output", "100",
     "--verify", "python3 slow.py", "--cwd", str(proj3)], home)
(proj3 / "slow.py").write_text("import time\ntime.sleep(5)\n")
tr3 = proj3 / "s.jsonl"; tr3.write_text(usage(10, "2030-01-01T10:00:00Z", write=str(proj3 / "a.py")))
r = run([STOP], home, json.dumps({"cwd": str(proj3), "transcript_path": str(tr3),
                                  "stop_hook_active": False}), env_extra={"FD_VERIFY_TIMEOUT_S": "1"})
b3 = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(str(proj3))}.json"
st3 = json.loads(b3.with_name(b3.stem + ".state.json").read_text())
check("V5 timeout → rc 'timeout' e messaggio 'timed out'",
      st3.get("verify_rc") == "timeout" and "timed out" in r.stdout, r.stdout + json.dumps(st3)[:200])

# V6 ricevuta
r = run([FDT, "budget-close", "--outcome", "ok", "--cwd", str(proj)], home)
check("V6 budget-close: 'verification passed' nella riga di chiusura",
      "verification passed" in r.stdout.splitlines()[0], r.stdout)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
