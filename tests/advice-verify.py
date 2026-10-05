#!/usr/bin/env python3
"""Verifica deterministica — consiglio di modello ed effort per sessione (1.56.0,
contratto claude-master 1.37 parte A).

  A1 quota 5h ≥ 90% su Fable → Opus 5.5 medium, differs, reason ≤ 200
  A2 settimana ≥ 85% → Opus 5.5 medium
  A3 finestra premium (plan fraction) ≥ 80% su Fable → Opus 5.5 high + /advisor fable
  A4 quota ok, Fable high → nessun cambio, differs false
  A5 effort xhigh/max con quota ≥ 70% → high; sotto il 70% resta (scelta voluta)
  A6 modello non di punta come sessione principale → Opus 5.5
  A7 when: contesto fresco e nessun budget → now; contesto pieno o budget aperto → next_task
  A8 switch_cost_tokens = ctx_tokens; source fable-director; at intero
  A9 quota assente → nessun numero inventato, xhigh resta
  A10 CLI --session riscrive l'advice nello snapshot (scrittura atomica, altre chiavi intatte)
  A11 statusline reale: lo snapshot porta account, cwd e advice

Usage: python3 tests/advice-verify.py   (exit 0 = all green)
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
ADV = SCRIPTS / "advice.py"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


spec = importlib.util.spec_from_file_location("advice", ADV)
adv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adv)

FABLE, OPUS = "claude-fable-5-1", "claude-opus-5-5"
NOW = 1_791_223_253


def snap(model=FABLE, effort="high", ctx=30_000):
    return {"session_id": "s1", "model": model, "effort": effort, "ctx_tokens": ctx}


def q(r=10.0, w=20.0, resets=NOW + 3600):
    return {"five_hour_used_pct": r, "weekly_used_pct": w, "five_hour_resets_at": resets}


a = adv.compute(snap(), q(r=92), {}, False, NOW)
check("A1 5h 92% su Fable → Opus medium", a["model"] == OPUS and a["effort"] == "medium"
      and a["differs"] is True and "5h" in a["reason"] and len(a["reason"]) <= 200, str(a))

a = adv.compute(snap(), q(w=88), {}, False, NOW)
check("A2 settimana 88% → Opus medium", a["model"] == OPUS and a["effort"] == "medium", str(a))

a = adv.compute(snap(), q(w=42), {"premium_weekly_fraction": 0.5}, False, NOW)
check("A3 finestra Fable ≤84% → Opus high + /advisor fable",
      a["model"] == OPUS and a["effort"] == "high" and "/advisor fable" in a["reason"], str(a))

a = adv.compute(snap(), q(), {}, False, NOW)
check("A4 quota ok, Fable high → invariato", a["model"] == FABLE and a["effort"] == "high"
      and a["differs"] is False, str(a))

a = adv.compute(snap(effort="max"), q(w=72), {}, False, NOW)
b = adv.compute(snap(effort="max"), q(r=69, w=50), {}, False, NOW)
check("A5 max con settimana 72% → high; con 5h 69% resta max",
      a["model"] == FABLE and a["effort"] == "high" and a["differs"] is True
      and b["effort"] == "max" and b["differs"] is False, f"{a} {b}")

a = adv.compute(snap(model="claude-sonnet-5-5", effort="medium"), q(), {}, False, NOW)
check("A6 Sonnet come principale → Opus", a["model"] == OPUS and a["differs"] is True, str(a))

a1 = adv.compute(snap(ctx=30_000), q(), {}, False, NOW)
a2 = adv.compute(snap(ctx=300_000), q(), {}, False, NOW)
a3 = adv.compute(snap(ctx=30_000), q(), {}, True, NOW)
check("A7 when now / next_task", a1["when"] == "now" and a2["when"] == "next_task"
      and a3["when"] == "next_task", f"{a1['when']} {a2['when']} {a3['when']}")

check("A8 campi", a2["switch_cost_tokens"] == 300_000 and a2["source"] == "fable-director"
      and a2["at"] == NOW and set(a2) == {"model", "effort", "reason", "switch_cost_tokens",
                                          "at", "source", "when", "differs"}, str(a2))

a = adv.compute(snap(effort="xhigh"), None, None, False, NOW)
check("A9 quota assente → xhigh resta, nessun numero", a["model"] == FABLE
      and a["effort"] == "xhigh" and a["differs"] is False and "%" not in a["reason"], str(a))

# A10 CLI
home = Path(tempfile.mkdtemp(prefix="fd-adv"))
base = home / ".claude" / "fable-director"
(base / "sessions").mkdir(parents=True)
acct = hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]
(base / f"quota-{acct}.json").write_text(json.dumps(q(w=90)))
sf = base / "sessions" / "s1.json"
sf.write_text(json.dumps(dict(snap(), ts=NOW, account=acct, cwd=str(home), extra=1)))
env = dict(os.environ, HOME=str(home))
env.pop("CLAUDE_CONFIG_DIR", None)
r = subprocess.run([sys.executable, str(ADV), "--session", "s1"], capture_output=True,
                   text=True, env=env, timeout=30)
d = json.loads(sf.read_text())
check("A10 CLI --session scrive advice", r.returncode == 0 and d.get("extra") == 1
      and (d.get("advice") or {}).get("model") == OPUS
      and not list((base / "sessions").glob("*.tmp")), r.stderr[-300:] + str(d))

# A11 statusline reale
payload = {"session_id": "s2", "cwd": str(home), "workspace": {"current_dir": str(home)},
           "model": {"id": FABLE, "display_name": "Fable 5.1"}, "effort": {"level": "max"},
           "context_window": {"context_window_size": 1_000_000,
                              "current_usage": {"input_tokens": 1000,
                                                "cache_read_input_tokens": 20000}},
           "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW + 3600},
                           "seven_day": {"used_percentage": 30, "resets_at": NOW + 86400}}}
r = subprocess.run(["bash", str(SCRIPTS / "statusline-ctx.sh")], input=json.dumps(payload),
                   capture_output=True, text=True, env=env, timeout=60)
f2 = base / "sessions" / "s2.json"
d2 = json.loads(f2.read_text()) if f2.is_file() else {}
check("A11 statusline: account, cwd, advice", d2.get("account") == acct
      and d2.get("cwd") == str(home) and (d2.get("advice") or {}).get("effort") == "max"
      and (d2.get("advice") or {}).get("when") == "now", r.stderr[-300:] + str(d2))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
