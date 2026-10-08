#!/usr/bin/env python3
"""Verifica deterministica — guardie fail-closed con onFailure: "block" (1.57.0).

Claude Code 2.1.295 accetta `onFailure: "block"` sugli hook command e HTTP: se
l'hook non parte, va in timeout o esce con un codice diverso da 0/2, l'azione
viene bloccata invece di passare. Le versioni precedenti ignorano la chiave
(misurato su 2.1.294: l'hook gira e il fallimento resta non bloccante).

  G1  ogni hook PreToolUse (gate deleghe, perimetro Write/Edit, deny_git Bash)
      ha onFailure "block"
  G2  nessun hook di misura (telemetria, PostToolUse, Stop, SessionStart…)
      lo porta: un loro guasto non deve mai fermare il lavoro
  G3  nessun hook con onFailure è async (la chiave lì viene ignorata)

Usage: python3 tests/guard-onfailure-verify.py   (exit 0 = all green)
"""
import json
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "fable-director" / "hooks" / "hooks.json"
GUARD_EVENTS = {"PreToolUse"}

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


hooks = json.loads(HOOKS.read_text())["hooks"]

guards = [(m.get("matcher"), h) for m in hooks.get("PreToolUse", []) for h in m["hooks"]]
missing = [mt for mt, h in guards if h.get("onFailure") != "block"]
check("G1 PreToolUse: tutte le guardie con onFailure block",
      guards and not missing, f"senza: {missing}")
check("G1 PreToolUse: gate deleghe e perimetro coperti",
      {"Agent|Task|Workflow", "Write|Edit|NotebookEdit", "Bash"} <= {mt for mt, _ in guards},
      f"matcher: {[mt for mt, _ in guards]}")

leaks = [ev for ev, ms in hooks.items() if ev not in GUARD_EVENTS
         for m in ms for h in m["hooks"] if "onFailure" in h]
check("G2 hook di misura senza onFailure", not leaks, f"eventi: {leaks}")

asyncs = [ev for ev, ms in hooks.items() for m in ms for h in m["hooks"]
          if "onFailure" in h and (h.get("async") or h.get("asyncRewake"))]
check("G3 nessun onFailure su hook async", not asyncs, f"eventi: {asyncs}")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} — {', '.join(FAILS)}"); sys.exit(1)
print("guard-onfailure-verify: all green")
