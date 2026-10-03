#!/usr/bin/env python3
"""Verifica deterministica — contratto del compito condiviso con claude-master
(convergenza approvata il 03/10/2026). Lo schema e' COPIATO, mai importato:
ogni plugin funziona da solo. Questo test:

  C1 la copia di fable-director e' JSON valido, schema_version 1
  C2 le corrispondenze con budget-open esistono: check.cmd (--verify),
     perimeter (--paths), data_class (--data-class), type (--type),
     measures.reliability e measures.verify_rc (scritti da fable-director)
  C3 se la copia di claude-master e' presente, i byte coincidono; altrimenti
     SKIP (fable-director installato da solo non ne dipende)

Percorso di claude-master: CLAUDE_MASTER_SCHEMA, altrimenti i cloni vicini.
Usage: python3 tests/task-contract-schema-verify.py   (exit 0 = all green)
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OURS = HERE.parent / "fable-director" / "schemas" / "task-contract.v1.json"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


raw = OURS.read_bytes()
doc = json.loads(raw)
check("C1 JSON valido, schema_version 1",
      doc["properties"]["schema_version"] == {"const": 1})
task = doc["$defs"]["task"]["properties"]
meas = doc["$defs"]["measures"]["properties"]
check("C2 campi che fable-director legge e scrive",
      "cmd" in task["check"]["properties"] and "perimeter" in task
      and "data_class" in task and "type" in task
      and "reliability" in meas and "verify_rc" in meas, sorted(task) + sorted(meas))

ws = HERE.parents[2]  # .../workspaces/personali
cands = [os.environ.get("CLAUDE_MASTER_SCHEMA")] + [
    str(ws / d / "claude-master" / "schemas" / "task-contract.v1.json")
    for d in ("claude-master", "claude-master-registro")]
theirs = [Path(c) for c in cands if c and Path(c).is_file()]
if not theirs:
    print("SKIP  C3 copia di claude-master assente: fable-director da solo")
else:
    for t in theirs:
        check(f"C3 byte identici a {t}", t.read_bytes() == raw)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
