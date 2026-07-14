#!/usr/bin/env python3
"""Verifica 1.18.0 — interop claude-hud + segmenti statusline CACHE/CMP.

Deterministica, HOME usa-e-getta, nessuna rete. Copre:
- gate cost-checkpoint: fallback allo snapshot usage di claude-hud quando il
  ponte quota di fable-director manca (fresco → usato, stantio → ignorato,
  quota propria → vince, config corrotto → fail-open);
- statusline: [CMP n] da compact_boundary, [CACHE ...] countdown/exp da
  FD_CACHE_TTL_S, [DLG] con token dopo lo spostamento dei campi, e lo
  snapshot usage-snapshot-<acct>.json nello schema esterno di claude-hud.
"""
import hashlib
import importlib.util
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


def now_iso(delta_s=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


# ---------- gate: fallback snapshot claude-hud ----------
tmp = Path(tempfile.mkdtemp(prefix="fd-hud-test"))
os.environ["HOME"] = str(tmp)
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.environ.pop("FD_COST_CEILING", None)

spec = importlib.util.spec_from_file_location("gate", ROOT / "pre-delegation-gate.py")
gate = importlib.util.module_from_spec(spec)
sys.modules["gate"] = gate
spec.loader.exec_module(gate)

BUDGET = {"expected_output_tokens": 20000}  # sotto il ceiling 50k, sopra il 30%

print("gate cost-checkpoint:")
check("H1 nessuna quota, sotto ceiling → allow", gate.cost_checkpoint(BUDGET) is None)

hud = tmp / ".claude" / "plugins" / "claude-hud"
hud.mkdir(parents=True)
snapf = tmp / "hudsnap.json"
(hud / "config.json").write_text(
    json.dumps({"display": {"externalUsageWritePath": str(snapf)}}))
snapf.write_text(json.dumps(
    {"updated_at": now_iso(), "seven_day": {"used_percentage": 90}}))
r = gate.cost_checkpoint(BUDGET)
check("H2 snapshot hud fresco + quota scarsa → checkpoint", bool(r and "scarce" in r), repr(r))

snapf.write_text(json.dumps(
    {"updated_at": now_iso(-3600), "seven_day": {"used_percentage": 90}}))
check("H3 snapshot hud stantio (>10 min) → ignorato", gate.cost_checkpoint(BUDGET) is None)

fd = tmp / ".claude" / "fable-director"
fd.mkdir(parents=True)
acct = hashlib.sha256(str(tmp / ".claude").encode()).hexdigest()[:8]
(fd / f"quota-{acct}.json").write_text(json.dumps({"weekly_used_pct": 10.0}))
snapf.write_text(json.dumps(
    {"updated_at": now_iso(), "seven_day": {"used_percentage": 90}}))
check("H4 quota propria presente → vince sullo snapshot hud",
      gate.cost_checkpoint(BUDGET) is None)

(hud / "config.json").write_text("{corrotto")
(fd / f"quota-{acct}.json").unlink()
check("H5 config hud corrotto → fail-open", gate.cost_checkpoint(BUDGET) is None)

# ---------- statusline: CACHE / CMP / DLG / snapshot ----------
print("statusline:")
work = tmp / "sl"
work.mkdir()
tr = work / "tr.jsonl"
now = datetime.now(timezone.utc)
rows = [
    {"type": "assistant", "timestamp": now_iso(-1800),
     "message": {"model": "claude-fable-5", "usage": {"output_tokens": 500}}},
    {"type": "system", "subtype": "compact_boundary", "timestamp": now_iso(-1200),
     "content": "Conversation compacted"},
    {"isSidechain": True, "type": "assistant", "timestamp": now_iso(-900),
     "message": {"model": "claude-sonnet-5", "usage": {"output_tokens": 12000}}},
    {"type": "assistant", "timestamp": now_iso(-780),
     "message": {"model": "claude-fable-5", "usage": {"output_tokens": 800}}},
]
tr.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
stdin_payload = json.dumps({
    "model": {"display_name": "Fable 5"},
    "session_id": "hudinteroptest", "transcript_path": str(tr), "cwd": str(work),
    "context_window": {"used_percentage": 42.0},
    "rate_limits": {
        "five_hour": {"used_percentage": 10,
                      "resets_at": int(now.timestamp()) + 7200},
        "seven_day": {"used_percentage": 31,
                      "resets_at": int(now.timestamp()) + 172800}}})


def render(home, **env):
    e = dict(os.environ, HOME=str(home), **{k: str(v) for k, v in env.items()})
    e.pop("CLAUDE_CONFIG_DIR", None)
    out = subprocess.run(["bash", str(ROOT / "statusline-ctx.sh")],
                         input=stdin_payload, capture_output=True, text=True,
                         env=e, timeout=30).stdout
    return re.sub(r"\x1b\[[0-9;]*m", "", out)


h1 = tmp / "home-a"
line = render(h1)
check("S1 [CMP 1] da compact_boundary", "[CMP 1]" in line, line)
check("S2 [CACHE Nm] countdown con TTL default 3600", re.search(r"\[CACHE \d+m\]", line) is not None, line)
check("S3 [DLG] conserva i token dopo lo shift dei campi",
      re.search(r"\[DLG SONNET-5 12k\]", line) is not None, line)

line2 = render(tmp / "home-b", FD_CACHE_TTL_S=300)
check("S4 [CACHE exp] con TTL 300 e ultima attività 13 min fa", "[CACHE exp]" in line2, line2)

snaps = list((h1 / ".claude" / "fable-director").glob("usage-snapshot-*.json"))
ok = False
if snaps:
    s = json.loads(snaps[0].read_text())
    ok = (s.get("five_hour", {}).get("used_percentage") == 10
          and s.get("seven_day", {}).get("used_percentage") == 31
          and str(s.get("updated_at", "")).endswith("Z")
          and str(s.get("seven_day", {}).get("resets_at", "")).endswith("Z"))
check("S5 usage-snapshot in schema claude-hud (percentuali + ISO Z)", ok,
      snaps[0].read_text() if snaps else "nessuno snapshot")

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} check falliti: {', '.join(FAILS)}")
    sys.exit(1)
print("hud-interop: tutti i check passati")
