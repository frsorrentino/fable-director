#!/usr/bin/env python3
"""Genera fixture deterministiche per il benchmark (seed fisso → riproducibile)."""
import random, string, sys
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures"
random.seed(42)

def gen_batch(n=30):
    d = ROOT / "batch"; d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        nums = [random.randint(1, 999) for _ in range(random.randint(5, 15))]
        (d / f"item{i:03d}.txt").write_text("\n".join(map(str, nums)) + "\n")

def gen_classify(n=30):
    d = ROOT / "classify"; d.mkdir(parents=True, exist_ok=True)
    kinds = [
        lambda: f"user{random.randint(1,99)}@example.com",
        lambda: f"https://site{random.randint(1,99)}.org/path",
        lambda: f"+39 3{random.randint(10,99)} {random.randint(1000000,9999999)}",
        lambda: "".join(random.choices(string.ascii_letters, k=random.randint(4, 10))),
    ]
    lines = [random.choice(kinds)() for _ in range(n)]
    (d / "items.txt").write_text("\n".join(lines) + "\n")

if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    gen_batch(); gen_classify()
    print(f"fixtures generate in {ROOT}")
