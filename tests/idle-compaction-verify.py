#!/usr/bin/env python3
"""Verifica deterministica — compattazione a sessione ferma (Claude Code >= 2.1.299).

Una conversazione da 200k+ token lasciata ferma ~54 minuti viene compattata
prima che la cache da un'ora scada (`"idleCompaction": false` la spegne). Nella
trascrizione e' un `compact_boundary` con trigger "auto", come una compattazione
normale: la distingue solo la pausa che la precede. La telemetria la conta a
parte e la ripresa che segue non finisce fra i `cache_resets`.

  I1 compattazione dopo 55 min di pausa: idle_compactions 1, la ripresa con
     cache_read 0 NON conta come cache reset
  I2 compattazione normale (pausa di 2 min): idle 0, il reset dopo resta un reset
  I3 ripresa dopo piu' di un'ora senza compattazione: un cache reset, idle 0
  I4 session-summary: idle_compactions nel payload
  I5 ripresa a cache fredda con il prefisso globale ancora in cache (caso reale
     master 15/10: 25.893 letti, 196.460 riscritti): un reset
  I6 tool output grosso (riscrive tanto ma rilegge tutto il contesto): nessun reset
  I7 come I5 con il messaggio della ripresa spezzato su tre righe: sempre un reset

Usage: python3 tests/idle-compaction-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fdt)


def turn(ts, mid, cr, cc=500):
    return {"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "role": "assistant", "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 5, "output_tokens": 50, "cache_read_input_tokens": cr,
                  "cache_creation_input_tokens": cc}}}


def prompt(ts, text="vai"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def boundary(ts):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": ts,
            "compactMetadata": {"trigger": "auto", "preTokens": 300000, "postTokens": 15000}}


def summary(ts):
    return {"type": "user", "timestamp": ts, "isCompactSummary": True,
            "message": {"role": "user", "content": "This session is being continued..."}}


def write(recs):
    p = Path(tempfile.mkdtemp(prefix="fd-idle-")) / "t.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return p


head = [prompt("2030-01-01T10:00:00Z"), turn("2030-01-01T10:00:10Z", "m1", 300000),
        prompt("2030-01-01T10:05:00Z"), turn("2030-01-01T10:05:10Z", "m2", 300000)]

# I1 — pausa di 55 minuti prima della compattazione, ripresa ore dopo a cache fredda
t1 = write(head + [boundary("2030-01-01T11:00:10Z"), summary("2030-01-01T11:00:11Z"),
                   prompt("2030-01-01T15:00:00Z"), turn("2030-01-01T15:00:10Z", "m3", 0, 20000),
                   prompt("2030-01-01T15:01:00Z"), turn("2030-01-01T15:01:10Z", "m4", 20000)])
_, _, _, resets1, *_, stats1 = fdt.sum_transcript(t1)
check("I1 compattazione a sessione ferma contata a parte, ripresa non e' un reset",
      stats1.get("idle_compactions") == 1 and resets1 == 0,
      f"idle={stats1.get('idle_compactions')} resets={resets1}")

# I2 — compattazione normale a meta' lavoro (pausa di 2 minuti)
t2 = write(head + [boundary("2030-01-01T10:07:10Z"), summary("2030-01-01T10:07:11Z"),
                   turn("2030-01-01T10:07:20Z", "m3", 0, 20000)])
_, _, _, resets2, *_, stats2 = fdt.sum_transcript(t2)
check("I2 compattazione normale: idle 0, il reset dopo resta un reset",
      stats2.get("idle_compactions") == 0 and resets2 == 1,
      f"idle={stats2.get('idle_compactions')} resets={resets2}")

# I3 — ripresa dopo piu' di un'ora senza compattazione
t3 = write(head + [prompt("2030-01-01T12:00:00Z"), turn("2030-01-01T12:00:10Z", "m3", 0, 300000)])
_, _, _, resets3, *_, stats3 = fdt.sum_transcript(t3)
check("I3 ripresa a cache fredda senza compattazione: un reset, idle 0",
      stats3.get("idle_compactions") == 0 and resets3 == 1,
      f"idle={stats3.get('idle_compactions')} resets={resets3}")

# I5 — caso reale: master 15/10 alle 10:04, cache globale del system prompt
# ancora calda (~26k letti), tutto il resto riscritto
t5 = write([prompt("2030-01-01T06:30:00Z"), turn("2030-01-01T06:32:55Z", "m1", 215740, 2679),
            prompt("2030-01-01T06:35:00Z"), turn("2030-01-01T06:35:23Z", "m2", 218419, 1567),
            prompt("2030-01-01T06:43:00Z"), turn("2030-01-01T06:43:19Z", "m3", 219986, 1940),
            prompt("2030-01-01T08:04:00Z"), turn("2030-01-01T08:04:30Z", "m4", 25893, 196460),
            prompt("2030-01-01T08:58:00Z"), turn("2030-01-01T08:58:03Z", "m5", 222353, 5433)])
_, _, _, resets5, *_ = fdt.sum_transcript(t5)
check("I5 ripresa a cache fredda con prefisso globale caldo: un reset",
      resets5 == 1, f"resets={resets5}")

# I6 — un tool output da 150k: riscrittura grande, ma il contesto e' riletto
t6 = write(head + [prompt("2030-01-01T10:06:00Z"), turn("2030-01-01T10:06:10Z", "m3", 300500, 150000),
                   prompt("2030-01-01T10:07:00Z"), turn("2030-01-01T10:07:10Z", "m4", 450500, 800)])
_, _, _, resets6, *_ = fdt.sum_transcript(t6)
check("I6 tool output grosso con contesto riletto: nessun reset",
      resets6 == 0, f"resets={resets6}")

# I7 — Claude Code scrive un messaggio su piu' righe con lo stesso id e usage
t7 = write([prompt("2030-01-01T06:30:00Z"), turn("2030-01-01T06:32:55Z", "m1", 215740, 2679),
            prompt("2030-01-01T06:43:00Z"), turn("2030-01-01T06:43:19Z", "m3", 219986, 1940),
            prompt("2030-01-01T08:04:00Z")]
           + [turn("2030-01-01T08:04:30Z", "m4", 25893, 196460)] * 3
           + [prompt("2030-01-01T08:58:00Z"), turn("2030-01-01T08:58:03Z", "m5", 222353, 5433)])
_, _, _, resets7, *_ = fdt.sum_transcript(t7)
check("I7 ripresa spezzata su tre righe: un reset solo",
      resets7 == 1, f"resets={resets7}")

# I4 — il payload della session-summary porta il conteggio
home = Path(tempfile.mkdtemp(prefix="fd-idle-home-"))
env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
env.pop("CLAUDE_CONFIG_DIR", None)
r = subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"), "session-summary"],
                   input=json.dumps({"cwd": str(home), "transcript_path": str(t1),
                                     "session_id": "idle-test"}),
                   capture_output=True, text=True, env=env, timeout=60)
payload = {}
db = home / ".claude" / "fable-director" / "telemetry.db"
if db.is_file():
    con = sqlite3.connect(db)
    row = con.execute("SELECT payload FROM events WHERE event='session_summary'").fetchone()
    con.close()
    payload = json.loads(row[0]) if row else {}
check("I4 session-summary: idle_compactions e cache_resets nel payload",
      payload.get("idle_compactions") == 1 and payload.get("cache_resets") == 0,
      f"{ {k: payload.get(k) for k in ('idle_compactions', 'cache_resets')} } err={r.stderr[:160]}")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
