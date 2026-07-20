#!/usr/bin/env python3
"""Fixture suite del contratto transcript — quali path dello schema JSONL
(non documentato da Claude Code) l'enforcement considera affidabili.

La sentinella runtime becca l'ASSENZA dello schema (zero usage/timestamp);
questa suite becca il DRIFT: se un upgrade del plugin cambia il parsing, i
totali attesi divergono QUI, all'update, non in produzione contando token
sbagliati (review duale 2026-07-10, proposta Codex).

Ogni fixture .jsonl ha accanto un .expected.json:
  {"stop": {"out": N, "inp": N},        # totali stop-budget-check post-declared (solo main)
   "wf": {"out": N, "inp": N},          # totali agenti Workflow post-declared (opzionale;
                                        # la fixture ha una dir <stem>/subagents/workflows/)
   "summary": {"main_out": N, "sub_out": N},  # split sum_transcript (opzionale)
   "wf_summary": {"out": N, "in_fresh": N, "n": N},  # sum_workflow_agents (opzionale)
   "sentinel": true}                     # atteso schema_anomaly (opzionale)

Uso: python3 tests/transcript-contract/run.py   (exit 0 = contratto rispettato)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPTS = HERE.parent.parent / "fable-director" / "scripts"
# declared_at: recente (il budget non deve andare stale, orizzonte 24h) ma
# PRIMA dei timestamp delle fixture — che per questo vivono nel futuro fisso
# 2030: statiche e sempre post-declared.
from datetime import datetime, timezone, timedelta
DECLARED = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stop_totals(fixture, home):
    """Esegue stop-budget-check con budget aperto e legge i totali dallo
    state file — il percorso REALE dell'enforcement, non una re-implementazione."""
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    cwd = "/contract/test"
    subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"),
                    "budget-open", "--task", "contract", "--expected-output",
                    "999999999", "--cwd", cwd],
                   env=env, capture_output=True, check=True)
    # declared_at nel passato: le fixture usano timestamp 2025+
    budgets = home / ".claude" / "fable-director" / "budgets"
    bfile = next(f for f in budgets.glob("*.json") if ".state." not in f.name)
    b = json.loads(bfile.read_text())
    b["declared_at"] = DECLARED
    bfile.write_text(json.dumps(b))
    r = subprocess.run([sys.executable, str(SCRIPTS / "stop-budget-check.py")],
                       input=json.dumps({"cwd": cwd,
                                         "transcript_path": str(fixture)}),
                       env=env, capture_output=True, text=True)
    sentinel = "schema sentinel" in r.stdout
    state_files = list(budgets.glob("*.state.json"))
    if not state_files:
        return None, None, None, None, sentinel
    st = json.loads(state_files[0].read_text())
    return (st["out"], st["inp"],
            st.get("wf_out") or 0, st.get("wf_inp") or 0, sentinel)


def main():
    telemetry = load_mod("fd_telemetry", SCRIPTS / "fd-telemetry.py")
    failures = 0
    fixtures = sorted(HERE.glob("*.jsonl"))
    if not fixtures:
        sys.exit("nessuna fixture in tests/transcript-contract/")
    for fx in fixtures:
        exp = json.loads(fx.with_suffix(".expected.json").read_text())
        with tempfile.TemporaryDirectory() as td:
            out, inp, wf_out, wf_inp, sentinel = stop_totals(fx, Path(td))
        ok = True
        detail = []
        if "stop" in exp:
            e = exp["stop"]
            if out != e["out"] or inp != e["inp"]:
                ok = False
                detail.append(f"stop: atteso out={e['out']} inp={e['inp']}, "
                              f"reale out={out} inp={inp}")
        if "wf" in exp:
            e = exp["wf"]
            if wf_out != e["out"] or wf_inp != e["inp"]:
                ok = False
                detail.append(f"wf: atteso out={e['out']} inp={e['inp']}, "
                              f"reale out={wf_out} inp={wf_inp}")
        if exp.get("sentinel") and not sentinel:
            ok = False
            detail.append("sentinella attesa ma non scattata")
        if not exp.get("sentinel") and sentinel:
            ok = False
            detail.append("sentinella scattata ma non attesa")
        if "summary" in exp:
            main_tot, sub_tot, *_ = telemetry.sum_transcript(fx)
            e = exp["summary"]
            if (main_tot.get("output_tokens", 0) != e["main_out"]
                    or sub_tot.get("output_tokens", 0) != e["sub_out"]):
                ok = False
                detail.append(f"summary: atteso main={e['main_out']} "
                              f"sub={e['sub_out']}, reale "
                              f"main={main_tot.get('output_tokens', 0)} "
                              f"sub={sub_tot.get('output_tokens', 0)}")
        if "wf_summary" in exp:
            wf_tot, n_wf = telemetry.sum_workflow_agents(fx)
            e = exp["wf_summary"]
            in_fresh = (wf_tot.get("input_tokens", 0)
                        + wf_tot.get("cache_creation_input_tokens", 0))
            if (wf_tot.get("output_tokens", 0) != e["out"]
                    or in_fresh != e["in_fresh"] or n_wf != e["n"]):
                ok = False
                detail.append(f"wf_summary: atteso out={e['out']} "
                              f"in_fresh={e['in_fresh']} n={e['n']}, reale "
                              f"out={wf_tot.get('output_tokens', 0)} "
                              f"in_fresh={in_fresh} n={n_wf}")
        print(f"[{'PASS' if ok else 'FAIL'}] {fx.name}"
              + (": " + "; ".join(detail) if detail else ""))
        failures += 0 if ok else 1
    if failures:
        sys.exit(f"\n{failures} fixture FALLITE: il contratto transcript è "
                 f"cambiato — aggiornare parsing o fixture PRIMA di rilasciare.")
    print(f"\ncontratto rispettato: {len(fixtures)} fixture")


if __name__ == "__main__":
    main()
