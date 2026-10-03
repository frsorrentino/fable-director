#!/usr/bin/env python3
"""Verifica deterministica — controllo obbligatorio nel gate e affidabilita'
per tipo di compito (A+B, 1.54.0).

  G1 gate: budget senza --verify → deny verify_missing, con budget-amend nel motivo
  G2 gate: --verify in prosa → deny verify_prose
  G3 budget-open: --verify finto rifiutato su qualunque rotta
  G4 budget-open --route agent senza verify comando → rifiutato
  G5 budget-amend --verify: un finto e' rifiutato, un comando vero sblocca il gate
     e resta negli emendamenti
  R1 tipo con 3 falliti su 6 → fragile, riga FD ⚖ e fascia nel budget
  R2 fragile: budget-close --outcome ok rifiutato senza fd-verifier
  R3 fragile: fd-verifier concluso nella sessione → ok accettato, in ricevuta
  R4 fragile: --no-verifier "motivo" → ok accettato, motivo in ricevuta
  R5 fragile: --outcome abandoned non chiede niente
  R6 percorso: fail_streak nella finestra di sessione rende fragile un tipo
     senza esiti falliti
  R7 6 chiusure pulite → reliable; meno di 3 → new, nessuna riga
  R8 gate: tipo fragile → promemoria su delega normale, silenzio su fd-verifier
  S1 Stop hook: fragile ricontrolla subito dopo un PASS, standard aspetta

Usage: python3 tests/reliability-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
FDT = str(SCRIPTS / "fd-telemetry.py")
GATE = str(SCRIPTS / "pre-delegation-gate.py")
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-rel-home-"))
os.environ["HOME"] = str(home)
os.environ["USERPROFILE"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", FDT)
fdt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fdt)
SID = "11111111-2222-3333-4444-555555555555"


def run(args, cwd, stdin=None, sid=SID):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    if sid:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          env=env, input=stdin, timeout=90, cwd=str(cwd))


def proj():
    return Path(tempfile.mkdtemp(prefix="fd-rel-proj-"))


def gate(cwd, stype="general-purpose"):
    r = run([GATE], cwd, json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(cwd),
        "session_id": SID, "tool_input": {"subagent_type": stype, "prompt": "x",
                                          "description": "x"}}))
    return r, (json.loads(r.stdout) if r.stdout.strip() else {})


def denied(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


def reason(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")


def opened(cwd, *extra):
    return run([FDT, "budget-open", "--task", "t", "--expected-output", "1000",
                "--cwd", str(cwd)] + list(extra), cwd)


def budget_of(cwd):
    return json.loads((home / ".claude/fable-director/budgets"
                       / f"{fdt.cwd_slug(str(cwd))}.json").read_text())


def seed_close(ttype, outcome="ok", sid=None, declared="2026-10-01T10:00:00Z",
               closed="2026-10-01T11:00:00Z", **kw):
    fdt.log_event("task_close", dict({"type": ttype, "outcome": outcome,
                                      "owner_sid": sid, "declared_at": declared,
                                      "closed_at": closed,
                                      "expected_output_tokens": 1000,
                                      "actual_output_tokens": 900}, **kw))


# ---- A: controllo obbligatorio nel gate
p = proj()
opened(p)
r, out = gate(p)
check("G1 budget senza --verify → deny verify_missing con budget-amend",
      denied(out) and "no check that can fail" in reason(out)
      and "budget-amend --verify" in reason(out), r.stdout + r.stderr)
run([FDT, "budget-close", "--outcome", "abandoned", "--cwd", str(p)], p)

p = proj()
opened(p, "--verify", "review report with graded findings")
r, out = gate(p)
check("G2 verify in prosa → deny", denied(out) and "prose" in reason(out),
      r.stdout + r.stderr)

p2 = proj()
r = opened(p2, "--verify", "npm test || true")
check("G3 budget-open rifiuta un verify finto", r.returncode != 0
      and "swallows the failure" in r.stderr, r.stdout + r.stderr)

r = opened(p2, "--route", "agent", "--paths", "src/**", "--verify", "tutto ok")
check("G4 --route agent senza comando → rifiutato", r.returncode != 0
      and "needs a --verify command" in r.stderr, r.stdout + r.stderr)

r1 = run([FDT, "budget-amend", "--verify", "echo ok", "--cwd", str(p)], p)
r2 = run([FDT, "budget-amend", "--verify", "test -s out.md", "--reason", "prosa",
          "--cwd", str(p)], p)
rg, out = gate(p)
b = budget_of(p)
check("G5 budget-amend --verify: finto rifiutato, vero sblocca il gate",
      r1.returncode != 0 and r2.returncode == 0 and not denied(out)
      and b["verify"] == "test -s out.md"
      and b["amendments"][-1]["from"] == "review report with graded findings",
      (r1.stderr, r2.stdout + r2.stderr, rg.stdout, b.get("amendments")))
run([FDT, "budget-close", "--outcome", "abandoned", "--cwd", str(p)], p)

# ---- B: affidabilita' per tipo
for o in ("ok", "flagged", "ok", "abandoned", "ok", "flagged"):
    seed_close("fragile-type", o)
p = proj()
r = opened(p, "--type", "fragile-type", "--verify", "test -d .")
b = budget_of(p)
check("R1 3 falliti su 6 → fragile, riga e fascia nel budget",
      b.get("reliability", {}).get("tier") == "fragile"
      and "FD ⚖ fragile-type: 3 of the last 6 failed" in r.stdout, r.stdout + r.stderr)

r = run([FDT, "budget-close", "--cwd", str(p)], p)
check("R2 fragile: close ok rifiutato senza fd-verifier",
      r.returncode != 0 and "is fragile" in r.stderr
      and budget_of(p)["status"] == "open", r.stdout + r.stderr)

fdt.log_event("delegation_outcome", {"agent_type": "fable-director:fd-verifier",
                                     "status": "concerns"}, session_id=SID)
r = run([FDT, "budget-close", "--cwd", str(p)], p)
receipts = sorted((home / ".claude/fable-director/receipts").glob("*.json"),
                  key=lambda x: x.stat().st_mtime)
rec = json.loads(receipts[-1].read_text()) if receipts else {}
check("R3 fd-verifier nella sessione → ok accettato, in ricevuta",
      r.returncode == 0 and rec.get("verifier_ran") is True, r.stdout + r.stderr)

p = proj()
opened(p, "--type", "fragile-type", "--verify", "test -d .")
r = run([FDT, "budget-close", "--no-verifier", "solo documentazione", "--cwd", str(p)], p)
receipts = sorted((home / ".claude/fable-director/receipts").glob("*.json"),
                  key=lambda x: x.stat().st_mtime)
rec = json.loads(receipts[-1].read_text())
check("R4 --no-verifier motivato → ok accettato, motivo in ricevuta",
      r.returncode == 0 and rec.get("no_verifier") == "solo documentazione",
      r.stdout + r.stderr)

p = proj()
opened(p, "--type", "fragile-type", "--verify", "test -d .")
r = run([FDT, "budget-close", "--outcome", "abandoned", "--cwd", str(p)], p)
check("R5 fragile: abandoned non chiede verifier", r.returncode == 0, r.stderr)

OTHER = "99999999-0000-0000-0000-000000000000"
for i in range(4):
    seed_close("grind-type", "ok", sid=OTHER if i < 2 else None)
fdt.log_event("fail_streak", {"streak": 3, "auto": True}, session_id=OTHER)
# il fail_streak e' scritto adesso: la finestra dei due task deve contenerlo
now = fdt.now_iso()
con = fdt.open_db()
con.execute("UPDATE events SET payload=json_set(payload,'$.declared_at','2026-01-01T00:00:00Z',"
            "'$.closed_at','2099-01-01T00:00:00Z') WHERE event='task_close' "
            "AND json_extract(payload,'$.owner_sid')=?", (OTHER,))
con.commit(); con.close()
rel = fdt.type_reliability("grind-type")
check("R6 fail_streak nella finestra di sessione → percorso faticoso, fragile",
      rel["tier"] == "fragile" and rel["failed"] == 0 and rel["strained"] == 2, rel)

for _ in range(6):
    seed_close("steady-type", "ok", verify_rc=0)
seed_close("rare-type", "ok")
p = proj()
r = opened(p, "--type", "rare-type", "--verify", "test -d .")
check("R7 6 pulite → reliable; 1 precedente → new senza riga",
      fdt.type_reliability("steady-type")["tier"] == "reliable"
      and budget_of(p)["reliability"]["tier"] == "new" and "FD ⚖" not in r.stdout,
      (fdt.type_reliability("steady-type"), r.stdout))
run([FDT, "budget-close", "--cwd", str(p)], p)

p = proj()
opened(p, "--type", "fragile-type", "--verify", "test -d .")
_, o1 = gate(p)
_, o2 = gate(p, "fable-director:fd-verifier")
check("R8 gate: promemoria fragile sulle deleghe, silenzio sul verifier",
      "is fragile" in o1.get("systemMessage", "")
      and "is fragile" not in o2.get("systemMessage", ""), (o1, o2))

# ---- S1 Stop hook: intervallo del ricontrollo per fascia
spec = importlib.util.spec_from_file_location("sbc", SCRIPTS / "stop-budget-check.py")
sbc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sbc)
p = proj()
last = datetime.now(timezone.utc).isoformat()


def rerun(tier):
    budget = {"verify": "test -d .", "cwd": str(p), "reliability": {"tier": tier}}
    state = {"verify_rc": 0, "verify_at": last, "verify_touches": 1}
    sbc.run_verify(budget, state, str(p), {"write_touches": 2})
    return state["verify_at"] != last


check("S1 fragile ricontrolla subito dopo un PASS, standard aspetta",
      rerun("fragile") and not rerun("standard") and not rerun("reliable"))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
