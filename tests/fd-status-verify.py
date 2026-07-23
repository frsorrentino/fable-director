#!/usr/bin/env python3
"""Verifica bollettino /status (fd-status.py, fase 3 zen).

Deterministica, HOME usa-e-getta. Contratto:
- box drawing (┌ │ └) con titolo fable-director e larghezza coerente;
- quote come barre ▓░ a 10 celle + percentuale + reset;
- sparkline ▁▂▃▅ del burn-rate costruita dalla quota-history (coda
  monotona), presente SOLO con storia sufficiente (≥3 campioni, ≥3h,
  crescita >0.5%) — mai una proiezione inventata;
- freshness dichiarata (as-of) sempre presente accanto alle quote.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director" / "scripts"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(home):
    e = dict(os.environ, HOME=str(home))
    e.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run([sys.executable, str(ROOT / "fd-status.py")],
                          capture_output=True, text=True, env=e,
                          timeout=30).stdout


tmp = Path(tempfile.mkdtemp(prefix="fd-status-test"))
home = tmp / "home"
base = home / ".claude" / "fable-director"
base.mkdir(parents=True)

acct = hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]
(base / f"quota-{acct}.json").write_text(json.dumps(
    {"five_hour_used_pct": 35.0, "weekly_used_pct": 70.0}))

print("bollettino /status:")

out = run(home)
check("B1 box drawing con titolo",
      "┌─ fable-director" in out and out.count("│") >= 4
      and "└" in out, out)
check("B2 quote a barre 10 celle",
      re.search(r"5H\s+▓▓▓░░░░░░░ 35%", out) is not None
      and re.search(r"7D\s+▓▓▓▓▓▓▓░░░ 70%", out) is not None, out)
check("B3 freshness dichiarata accanto alle quote",
      "as-of" in out, out)
check("B4 senza history → nessuna riga burn (mai inventata)",
      "burn" not in out, out)

# history con coda monotona: 6 campioni in 6h, +6%
now = datetime.now(timezone.utc)
rows = []
for i in range(6):
    rows.append(json.dumps({
        "ts": (now - timedelta(hours=5 - i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "w": 64.0 + i * 1.2, "r": 30.0}))
(base / f"quota-history-{acct}.jsonl").write_text("\n".join(rows) + "\n")
out2 = run(home)
check("B5 con history → burn con sparkline e proiezione 100%",
      re.search(r"burn\s+~[\d.]+%/h [▁▂▃▄▅▆▇█]{3,}", out2) is not None
      and "100%" in out2, out2)

# B6: bucket ignoti registrati dalla statusline → riga informativa
(base / f"quota-{acct}.json").write_text(json.dumps(
    {"five_hour_used_pct": 35.0, "weekly_used_pct": 70.0,
     "unknown_buckets": ["seven_day_opus"]}))
out3 = run(home)
check("B6 unknown bucket → riga new col nome del campo",
      "seven_day_opus" in out3, out3)

# B7: plan file con frazione premium → riga bound ✦ ≤N%
(base / f"plan-{acct}.json").write_text(json.dumps(
    {"premium_weekly_fraction": 0.5}))
(base / f"quota-{acct}.json").write_text(json.dumps(
    {"five_hour_used_pct": 35.0, "weekly_used_pct": 13.0}))
out4 = run(home)
check("B7 plan con frazione → bound ✦ ≤26% dichiarato come tetto",
      re.search(r"✦\s*≤26%", out4) is not None and "bound" in out4, out4)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto bollettino rispettato")
