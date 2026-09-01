#!/usr/bin/env python3
"""Verifica deterministica — hook PreModelSwitch/PostModelSwitch (1.39.0).

  S1 Pre, cache calda, contesto grande: exit 1 (avviso, mai blocco), stderr
     con from→to, token re-cached e (da Fable 5.1) i thinking block persi
  S2 Pre, stesso modello: exit 0, silenzio
  S3 Pre, cache fredda e contesto piccolo: exit 0, silenzio, ma evento loggato
  S4 telemetria: evento `reversal` {kind: model_switch, from, to}
  S5 budget aperto della sessione: `model_switches` appeso
  S6 Post (source resume): exit 0, stdout per Claude con la tariffa nuova
  S7 hooks.json registra entrambi gli eventi sullo script
  S8 stdin illeggibile: exit 0, nessun crash

Usage: python3 tests/model-switch-verify.py   (exit 0 = all green)
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
HOOK = SCRIPTS / "model-switch.py"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(args, home, stdin, extra_env=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(extra_env or {})
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, env=env, input=stdin, timeout=60)


home = Path(tempfile.mkdtemp(prefix="fd-switch-home-"))
proj = tempfile.mkdtemp(prefix="fd-switch-proj-")
SID = "sess-switch-1"


def payload(**kw):
    d = {"hook_event_name": "PreModelSwitch", "session_id": SID, "cwd": proj,
         "from_model": "claude-fable-5-1", "to_model": "claude-opus-5",
         "requested_model": "opus", "source": "command",
         "context_tokens": 280000, "prompt_cache_warm": True,
         "cache_ttl": "1h", "estimated_cache_write_usd": 1.75,
         "pricing": "catalog"}
    d.update(kw)
    return json.dumps(d)


# budget aperto per la sessione (S5)
r = run([str(SCRIPTS / "fd-telemetry.py"), "budget-open", "--task", "switch-test",
         "--expected-output", "100", "--cwd", proj], home,
        None, {"CLAUDE_CODE_SESSION_ID": SID})
assert r.returncode == 0, r.stderr

# S1
r = run([str(HOOK)], home, payload())
check("S1 Pre cache calda: exit 1 + avviso con re-cache e thinking block",
      r.returncode == 1 and "fable-5-1 → opus-5" in r.stderr
      and "280.000" in r.stderr and "thinking blocks" in r.stderr
      and "proceeds" in r.stderr and r.stdout == "",
      f"rc={r.returncode} err={r.stderr!r} out={r.stdout!r}")

# S2
r = run([str(HOOK)], home, payload(to_model="claude-fable-5-1"))
check("S2 stesso modello: exit 0 silenzio",
      r.returncode == 0 and not r.stdout and not r.stderr, f"{r.returncode} {r.stderr!r}")

# S3
r = run([str(HOOK)], home, payload(from_model="claude-opus-5", to_model="claude-sonnet-5",
                                   prompt_cache_warm=False, context_tokens=5000))
check("S3 cache fredda e contesto piccolo: exit 0 silenzio",
      r.returncode == 0 and not r.stderr, f"{r.returncode} {r.stderr!r}")

# S4 telemetria
db = home / ".claude" / "fable-director" / "telemetry.db"
con = sqlite3.connect(db)
rows = [json.loads(p) for (p,) in con.execute(
    "select payload from events where event='reversal' order by ts")]
con.close()
check("S4 reversal loggati (S1 e S3, non S2): kind model_switch, from/to",
      len(rows) == 2 and rows[0].get("kind") == "model_switch"
      and rows[0].get("from") == "claude-fable-5-1" and rows[0].get("to") == "claude-opus-5"
      and rows[1].get("to") == "claude-sonnet-5",
      str(rows))

# S5 budget
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec)
os.environ["HOME"] = str(home)
spec.loader.exec_module(fdt)
bfile = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(proj)}.json"
b = json.loads(bfile.read_text())
sw = b.get("model_switches") or []
check("S5 budget aperto: model_switches appeso (2 cambi)",
      len(sw) == 2 and sw[0]["from"] == "claude-fable-5-1" and sw[0]["context_tokens"] == 280000,
      str(sw))

# S6 Post
r = run([str(HOOK)], home, payload(hook_event_name="PostModelSwitch", source="resume",
                                   from_model="claude-opus-5", to_model="claude-fable-5-1"))
check("S6 Post resume: exit 0, stdout per Claude con tariffa cache 0.025",
      r.returncode == 0 and "opus-5 → fable-5-1" in r.stdout and "0.025" in r.stdout
      and not r.stderr, f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

# S7 hooks.json
h = json.loads((HERE.parent / "fable-director" / "hooks" / "hooks.json").read_text())["hooks"]
ok7 = all(ev in h and any("model-switch.py" in hk["command"] and hk["command"].startswith("python3")
                          for g in h[ev] for hk in g["hooks"])
          for ev in ("PreModelSwitch", "PostModelSwitch"))
check("S7 hooks.json: Pre e Post registrati con prefisso python3", ok7)

# S8
r = run([str(HOOK)], home, "{not json")
check("S8 stdin illeggibile: exit 0", r.returncode == 0 and not r.stderr, r.stderr)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
