#!/usr/bin/env python3
"""Verifica deterministica — dichiarazione di chiusura con il --verify
fallito (1.49): lo Stop hook blocca UNA volta per esito di verifica.

  C1 verify fallito + ultimo messaggio "Tutto fatto." → decision block con
     comando ed exit; state.claim_blocked_at; evento claim_block
  C2 stesso esito, stessa dichiarazione al turno dopo → nessun nuovo blocco
  C3 verify fallito, messaggio che dice il problema → nessun blocco (solo
     l'avviso del verify)
  C4 verify che passa + dichiarazione → nessun blocco
  C5 last_assistant_message nel payload ha la precedenza sul transcript
  C6 nuova scrittura, verify di nuovo fallito + dichiarazione → blocca di nuovo
  C7 --verify in prosa + dichiarazione → nessun blocco (niente fatto da
     confrontare)

Usage: python3 tests/claim-gate-verify.py   (exit 0 = all green)
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


def run(args, home, stdin=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, env=env, input=stdin, timeout=90)


home = Path(tempfile.mkdtemp(prefix="fd-cg-home-"))
os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)
FDT = str(SCRIPTS / "fd-telemetry.py")
STOP = str(SCRIPTS / "stop-budget-check.py")


def usage(ts, write=None, text=None):
    rec = {"timestamp": ts, "type": "assistant", "message": {
        "model": "claude-sonnet-5", "usage": {"output_tokens": 10, "input_tokens": 1,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0},
        "content": []}}
    if write:
        rec["message"]["content"].append({"type": "tool_use", "name": "Write",
                                          "input": {"file_path": write, "content": "x"}})
    if text:
        rec["message"]["content"].append({"type": "text", "text": text})
    return json.dumps(rec) + "\n"


def setup(verify, check_src):
    proj = Path(tempfile.mkdtemp(prefix="fd-cg-proj-"))
    run([FDT, "budget-open", "--task", "claim", "--expected-output", "100000",
         "--verify", verify, "--cwd", str(proj)], home)
    (proj / "check.py").write_text(check_src)
    return proj


def stop(proj, tr, extra=None):
    p = {"cwd": str(proj), "transcript_path": str(tr), "stop_hook_active": False,
         "session_id": "s-claim"}
    p.update(extra or {})
    r = run([STOP], home, json.dumps(p))
    outs = [l for l in r.stdout.splitlines() if l.strip()]
    return (json.loads(outs[0]) if outs else {}), r


def state(proj):
    b = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(str(proj))}.json"
    return json.loads(b.with_name(b.stem + ".state.json").read_text())


def claim_events():
    db = home / ".claude" / "fable-director" / "telemetry.db"
    con = sqlite3.connect(db)
    n = con.execute("SELECT count(*) FROM events WHERE event='claim_block'").fetchone()[0]
    con.close()
    return n


FAIL = "import sys\nprint('2 failed')\nsys.exit(1)\n"

# C1
proj = setup("python3 check.py", FAIL)
tr = proj / "s.jsonl"
tr.write_text(usage("2030-01-01T10:00:00Z", write=str(proj / "a.py"))
              + usage("2030-01-01T10:00:05Z", text="Ho sistemato il modulo.\n\nTutto fatto."))
msg, r = stop(proj, tr)
st = state(proj)
check("C1 verify fallito + 'Tutto fatto.' → block con comando ed exit, marcato, evento",
      msg.get("decision") == "block"
      and "python3 check.py (exit 1)" in msg.get("reason", "")
      and "not finished" in msg.get("reason", "")
      and st.get("claim_blocked_at") == st.get("verify_at")
      and claim_events() == 1,
      r.stdout + r.stderr + json.dumps(st)[:300])

# C2
msg, r = stop(proj, tr)
check("C2 stesso esito, stessa dichiarazione → nessun nuovo blocco",
      msg.get("decision") != "block" and claim_events() == 1, r.stdout)

# C3
proj3 = setup("python3 check.py", FAIL)
tr3 = proj3 / "s.jsonl"
tr3.write_text(usage("2030-01-01T10:00:00Z", write=str(proj3 / "a.py"))
               + usage("2030-01-01T10:00:05Z",
                       text="Il test fallisce ancora (exit 1): due casi da sistemare."))
msg, r = stop(proj3, tr3)
check("C3 messaggio che dice il problema → solo l'avviso del verify, niente blocco",
      msg.get("decision") != "block"
      and "verification failed" in msg.get("systemMessage", ""), r.stdout)

# C4
proj4 = setup("python3 check.py", "print('ok')\n")
tr4 = proj4 / "s.jsonl"
tr4.write_text(usage("2030-01-01T10:00:00Z", write=str(proj4 / "a.py"))
               + usage("2030-01-01T10:00:05Z", text="All done."))
msg, r = stop(proj4, tr4)
check("C4 verify che passa + dichiarazione → nessun blocco",
      msg.get("decision") != "block" and state(proj4).get("verify_rc") == 0, r.stdout)

# C5
proj5 = setup("python3 check.py", FAIL)
tr5 = proj5 / "s.jsonl"
tr5.write_text(usage("2030-01-01T10:00:00Z", write=str(proj5 / "a.py"))
               + usage("2030-01-01T10:00:05Z", text="Tutto fatto."))
msg, r = stop(proj5, tr5, {"last_assistant_message": "Il verify fallisce: non chiudo."})
check("C5 last_assistant_message ha la precedenza sul transcript",
      msg.get("decision") != "block", r.stdout)

# C6
with open(tr, "a") as fh:
    fh.write(usage("2030-01-01T10:20:00Z", write=str(proj / "b.py"))
             + usage("2030-01-01T10:20:05Z", text="Corretto.\nTask completed: ready for review."))
msg, r = stop(proj, tr)
check("C6 nuovo esito fallito + dichiarazione → blocca di nuovo",
      msg.get("decision") == "block" and claim_events() == 2, r.stdout + r.stderr)

# C7
proj7 = setup("checklist: test verdi", FAIL)
tr7 = proj7 / "s.jsonl"
tr7.write_text(usage("2030-01-01T10:00:00Z", write=str(proj7 / "a.py"))
               + usage("2030-01-01T10:00:05Z", text="Tutto fatto."))
msg, r = stop(proj7, tr7)
check("C7 --verify in prosa → nessun blocco", msg.get("decision") != "block", r.stdout)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
