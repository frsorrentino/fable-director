#!/usr/bin/env python3
"""Verifica 1.52.3 — quota file condiviso fra sessioni dello stesso account.

Piu sessioni dello stesso account ridisegnano la statusline a turno, ognuna con
la propria ultima lettura di rate_limits: senza fusione vince l'ultima che
ridisegna, anche se ferma da ore, e quota-<acct>.json / usage-snapshot /
quota-history oscillano (58 → 61 → 62 → 58...). Dentro una finestra la quota
non scende mai: a pari resets_at il massimo e il dato piu recente; un resets_at
piu avanti e una finestra nuova e vince; uno piu indietro e una lettura vecchia
e si ignora. Deterministica, HOME usa-e-getta, nessuna rete.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director" / "scripts"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


home = Path(tempfile.mkdtemp(prefix="fd-quota-merge"))
fd = home / ".claude" / "fable-director"
acct = hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]
qf = fd / f"quota-{acct}.json"
us = fd / f"usage-snapshot-{acct}.json"
hf = fd / f"quota-history-{acct}.jsonl"

NOW = int(time.time())
WK = NOW + 3 * 86400        # reset settimanale corrente
FH = NOW + 3 * 3600         # reset 5h corrente


def render(weekly=None, wreset=WK, five=None, freset=FH):
    rl = {}
    if five is not None:
        rl["five_hour"] = {"used_percentage": five, "resets_at": freset}
    if weekly is not None:
        rl["seven_day"] = {"used_percentage": weekly, "resets_at": wreset}
    payload = json.dumps({"model": {"display_name": "Opus 5"},
                          "session_id": "quotamerge", "cwd": str(home),
                          "context_window": {"used_percentage": 10.0},
                          "rate_limits": rl})
    e = dict(os.environ, HOME=str(home))
    e.pop("CLAUDE_CONFIG_DIR", None)
    subprocess.run(["bash", str(ROOT / "statusline-ctx.sh")], input=payload,
                   capture_output=True, text=True, env=e, timeout=120)
    return json.loads(qf.read_text())


def history():
    return [json.loads(x) for x in hf.read_text().splitlines() if x.strip()]


print("tre sessioni dello stesso account a turno (caso del 01/10):")
A = dict(weekly=58)                 # sessione ferma da ore, five_hour assente
B = dict(weekly=61, five=6)
C = dict(weekly=62, five=10)        # la lettura piu recente
for s in (A, B, C, A, B, A, C, B):
    q = render(**s)
check("M1 weekly resta 62 qualunque sessione ridisegni per ultima",
      q.get("weekly_used_pct") == 62.0, q)
check("M2 five_hour resta 10, la lettura senza five_hour non lo cancella",
      q.get("five_hour_used_pct") == 10.0, q)
ws = [r["w"] for r in history()]
check("M3 storia senza oscillazioni (weekly mai in discesa)",
      all(b >= a for a, b in zip(ws, ws[1:])), ws)
check("M4 nessuna riga di storia se la fusione non cambia il valore",
      len(ws) == 3, ws)
s = json.loads(us.read_text())
check("M5 usage-snapshot fuso: seven_day 62, five_hour 10",
      s.get("seven_day", {}).get("used_percentage") == 62
      and s.get("five_hour", {}).get("used_percentage") == 10, s)

print("finestre nuove e letture vecchie:")
q = render(weekly=62, five=1, freset=FH + 5 * 3600)
check("M6 five_hour in finestra nuova: vince il nuovo anche se piu basso",
      q.get("five_hour_used_pct") == 1.0 and q.get("five_hour_resets_at") == FH + 5 * 3600, q)
q = render(weekly=2, wreset=WK + 7 * 86400, five=1, freset=FH + 5 * 3600)
check("M7 settimana nuova: vince il nuovo anche se piu basso",
      q.get("weekly_used_pct") == 2.0 and q.get("weekly_resets_at") == WK + 7 * 86400, q)
q = render(weekly=62, wreset=WK, five=10, freset=FH)
check("M8 sessione ferma alla finestra precedente: ignorata",
      q.get("weekly_used_pct") == 2.0 and q.get("five_hour_used_pct") == 1.0, q)
s = json.loads(us.read_text())
check("M9 usage-snapshot segue la fusione",
      s.get("seven_day", {}).get("used_percentage") == 2
      and s.get("five_hour", {}).get("used_percentage") == 1, s)

print("five_hour scaduto:")
qf.write_text(json.dumps({"weekly_used_pct": 2.0, "weekly_resets_at": WK + 7 * 86400,
                          "five_hour_used_pct": 40.0, "five_hour_resets_at": NOW - 60}))
q = render(weekly=2, wreset=WK + 7 * 86400)
check("M10 lettura senza five_hour e finestra 5h vecchia gia scaduta: cade",
      "five_hour_used_pct" not in q, q)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} check falliti: {', '.join(FAILS)}")
    sys.exit(1)
print("quota-merge: tutti i check passati")
