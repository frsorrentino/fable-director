#!/usr/bin/env python3
"""Verifica deterministica — avviso cache scaduta alla ripresa (1.39.0, C2.2).

  P1 resume, cache scaduta, contesto 280k, 3h12: riga con pausa, token,
     costo; evento resume_stale in telemetria
  P2 resume con cache ancora calda: silenzio
  P3 startup (campi assenti): silenzio; contesto sotto 100k: silenzio
  P4 DB assente: la riga esce comunque, nessun crash

Usage: python3 tests/resume-stale-verify.py
"""
import json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(home, payload):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run([sys.executable, str(SCRIPTS / "session-hindsight.py")],
                          capture_output=True, text=True, env=env,
                          input=json.dumps(payload), timeout=60)


home = Path(tempfile.mkdtemp(prefix="fd-resume-home-"))
proj = tempfile.mkdtemp(prefix="fd-resume-proj-")
base = {"cwd": proj, "session_id": "s-res", "source": "resume",
        "seconds_since_last_response": 3 * 3600 + 12 * 60, "context_tokens": 280000,
        "prompt_cache_likely_expired": True, "estimated_cache_write_usd": 3.5}

# P4 prima: DB assente
r = run(home, base)
check("P4 DB assente: riga presente, nessun crash",
      r.returncode == 0 and "Resuming after 3 h 12 min" in r.stdout, r.stdout + r.stderr)
# crea DB via log_event di fd-telemetry
env = dict(os.environ, HOME=str(home)); env.pop("CLAUDE_CONFIG_DIR", None)
subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"), "log", "verification",
                "--json", '{"found": false, "kind": "seed"}'], env=env, capture_output=True, cwd=proj)
r = run(home, base)
db = home / ".claude" / "fable-director" / "telemetry.db"
con = sqlite3.connect(db)
ev = [json.loads(p) for (p,) in con.execute("select payload from events where event='resume_stale'")]
con.close()
check("P1 resume scaduta: riga con pausa, 280.000 token, $3.50; evento resume_stale",
      "the first turn re-caches ~280.000 tokens (~$3.50 at list price)" in r.stdout
      and "verified boundary" in r.stdout and len(ev) == 1 and ev[0]["context_tokens"] == 280000,
      r.stdout + r.stderr + str(ev))
r = run(home, dict(base, prompt_cache_likely_expired=False))
check("P2 cache calda: silenzio", "Resuming after" not in r.stdout, r.stdout)
r1 = run(home, {"cwd": proj, "session_id": "s", "source": "startup"})
r2 = run(home, dict(base, context_tokens=40000))
check("P3 startup / contesto piccolo: silenzio",
      "Resuming after" not in r1.stdout and "Resuming after" not in r2.stdout, r1.stdout + r2.stdout)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
