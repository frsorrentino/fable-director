#!/usr/bin/env python3
"""Aggrega gli output JSON di run.sh → % risparmio token off vs on, per task e complessivo.

Uso: aggregate.py RESULTS_DIR
Metriche billable: input+output+cache_creation+cache_read (somma grezza) e total_cost_usd.
"""
import json, sys, statistics as st
from pathlib import Path
from collections import defaultdict

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

if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    tokens, costs = load(d)
    summarize("Token billable (somma input+output+cache)", tokens)
    summarize("Costo USD", costs)
    print("\nNota: il risparmio dipende dalla forma del task. Riporta N run, media e spread. "
          "Non estrapolare a 'ogni caso'.")
