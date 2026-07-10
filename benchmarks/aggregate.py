#!/usr/bin/env python3
"""Aggrega gli output JSON di run.sh → % risparmio token off vs on, per task e complessivo.

Uso: aggregate.py RESULTS_DIR
Metriche billable: input+output+cache_creation+cache_read (somma grezza) e total_cost_usd.
"""
import csv, json, sys, statistics as st
from pathlib import Path
from collections import defaultdict

# Shape con ground truth semantica: (prefisso task, file expected)
GRADED = [("04", Path(__file__).parent / "expected" / "04-reviews.json"),
          ("05", Path(__file__).parent / "expected" / "05-reviews.json")]

TOK = ("input_tokens", "output_tokens",
       "cache_creation_input_tokens", "cache_read_input_tokens")

def load(d):
    # tokens[task][arm] = [somma_token,...]; costs[task][arm] = [usd,...]
    tokens = defaultdict(lambda: defaultdict(list))
    costs  = defaultdict(lambda: defaultdict(list))
    for f in sorted(Path(d).glob("*.json")):
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        parts = f.stem.split("__")
        if len(parts) < 3:
            continue
        task, arm = parts[0], parts[1]
        u = j.get("usage", {}) or {}
        tot = sum(int(u.get(k, 0) or 0) for k in TOK)
        if tot:
            tokens[task][arm].append(tot)
        c = j.get("total_cost_usd")
        if isinstance(c, (int, float)):
            costs[task][arm].append(float(c))
    return tokens, costs

def pct(off, on):
    return (off - on) / off * 100 if off else float("nan")

def summarize(name, data):
    print(f"\n=== {name} ===")
    per_task_pct = []
    for task in sorted(data):
        off = data[task].get("off", []); on = data[task].get("on", [])
        if not off or not on:
            print(f"  {task}: dati incompleti (off={len(off)} on={len(on)})")
            continue
        mo, mn = st.mean(off), st.mean(on)
        p = pct(mo, mn); per_task_pct.append(p)
        so = f"±{st.pstdev(off):.0f}" if len(off) > 1 else ""
        sn = f"±{st.pstdev(on):.0f}" if len(on) > 1 else ""
        print(f"  {task:28s} off={mo:10.1f}{so:>7}  on={mn:10.1f}{sn:>7}  → risparmio {p:5.1f}%")
    if per_task_pct:
        print(f"  {'MEDIA per-task':28s} {'':>26}{'':>16}  → risparmio {st.mean(per_task_pct):5.1f}%")

def quality(d, prefix, expected_path):
    """Accuracy del task vs ground truth, per arm: il risparmio conta solo
    a parità di risultato verificato. Safety = la metrica che pesa (error
    cost alto): recall mancata lì vale più di ogni token risparmiato."""
    if not expected_path.is_file():
        return
    exp = json.loads(expected_path.read_text())
    rows_by_arm = defaultdict(list)  # arm -> [(sent_ok, tema_ok, safety_pred, rid), ...]
    for f in sorted(Path(d).glob(f"{prefix}-*__*__*.triage.csv")):
        arm = f.stem.split("__")[1]
        got = {}
        try:
            with open(f, newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("id"):
                        got[r["id"].strip()] = r
        except (OSError, csv.Error):
            continue
        rows_by_arm[arm].append(got)
    if not rows_by_arm:
        return
    print(f"\n=== Qualità task {prefix} (vs ground truth, media sui run) ===")
    for arm in sorted(rows_by_arm):
        sent = tema = 0.0
        srec, sprec = [], []
        runs = rows_by_arm[arm]
        for got in runs:
            n = len(exp)
            sent += sum(1 for k, v in exp.items()
                        if (got.get(k) or {}).get("sentiment", "").strip().lower() == v["sentiment"]) / n
            tema += sum(1 for k, v in exp.items()
                        if (got.get(k) or {}).get("tema", "").strip().lower() == v["tema"]) / n
            true_yes = {k for k, v in exp.items() if v["segnalazione_sicurezza"] == "YES"}
            pred_yes = {k for k, r in got.items()
                        if (r.get("segnalazione_sicurezza") or "").strip().upper() == "YES"}
            srec.append(len(true_yes & pred_yes) / len(true_yes) if true_yes else 1.0)
            sprec.append(len(true_yes & pred_yes) / len(pred_yes) if pred_yes else 0.0)
        k = len(runs)
        print(f"  [{arm}] run={k}  sentiment {sent/k:.0%}  tema {tema/k:.0%}  "
              f"safety recall {st.mean(srec):.0%} precision {st.mean(sprec):.0%}")
    print("  ⚠ safety recall < 100% in un arm = risparmio comprato con qualità: riportalo, non nasconderlo.")


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    tokens, costs = load(d)
    summarize("Token billable (somma input+output+cache)", tokens)
    summarize("Costo USD", costs)
    for prefix, expected_path in GRADED:
        quality(d, prefix, expected_path)
    print("\nNota: il risparmio dipende dalla forma del task. Riporta N run, media e spread. "
          "Non estrapolare a 'ogni caso'.")
