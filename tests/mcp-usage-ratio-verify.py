#!/usr/bin/env python3
"""Verifica dell'attribuzione MCP per sessione (1.31.0): mcp-meter scrive
session_id+cwd e il report puo' fare il join caricati/chiamati — chi carica
30 schemi via `select:` e ne usa 2 paga la giacenza a ogni turno per niente.

  M1  mcp-meter scrive session_id e cwd (mcp_meter e mcp_schema_load)
  M2  report: join per sessione — tool caricati via select: mai chiamati
  M3  tool non-mcp__ nella select: (nativi) esclusi dal ratio
  M4  righe storiche senza session_id fuori dal ratio, dentro i totali
  M5  query a 120 char (troncata): l'ultimo frammento scartato, mai contato
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
METER = REPO / "fable-director" / "scripts" / "mcp-meter.py"
TELEMETRY = REPO / "fable-director" / "scripts" / "fd-telemetry.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {str(detail)[:300]}")


def run_meter(home, payload):
    return subprocess.run(
        [sys.executable, str(METER)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        input=json.dumps(payload), capture_output=True, text=True, timeout=30)


def seed(home, rows):
    """rows: (session_id|None, event, payload_dict)"""
    base = home / ".claude" / "fable-director"
    base.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(base / "telemetry.db")
    con.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,"
                " ts TEXT NOT NULL, session_id TEXT, cwd TEXT,"
                " event TEXT NOT NULL, payload TEXT)")
    for sid, event, payload in rows:
        con.execute("INSERT INTO events(ts, session_id, event, payload)"
                    " VALUES(datetime('now','-1 hours'), ?, ?, ?)",
                    (sid, event, json.dumps(payload)))
    con.commit()
    con.close()


def report(home):
    return subprocess.run(
        [sys.executable, str(TELEMETRY), "report", "--days", "7"],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30, cwd=str(home))


tmp = Path(tempfile.mkdtemp(prefix="fd-mcpratio-test-"))
try:
    # ---- M1: il meter scrive session_id e cwd ----
    h = tmp / "m1"
    run_meter(h, {"tool_name": "mcp__foo__bar", "tool_response": {"ok": 1},
                  "session_id": "S1", "cwd": "/proj/x"})
    run_meter(h, {"tool_name": "ToolSearch",
                  "tool_input": {"query": "select:mcp__foo__bar"},
                  "tool_response": {"schemas": "x" * 100},
                  "session_id": "S1", "cwd": "/proj/x"})
    con = sqlite3.connect(h / ".claude" / "fable-director" / "telemetry.db")
    rows = con.execute(
        "SELECT event, session_id, cwd FROM events ORDER BY id").fetchall()
    con.close()
    check("M1 mcp_meter con session_id+cwd",
          ("mcp_meter", "S1", "/proj/x") in rows, rows)
    check("M1 mcp_schema_load con session_id+cwd",
          ("mcp_schema_load", "S1", "/proj/x") in rows, rows)

    # ---- M2/M3: join caricati/chiamati, tool nativi esclusi ----
    h = tmp / "m2"
    seed(h, [
        ("S1", "mcp_schema_load",
         {"query": "select:mcp__a__x,mcp__a__y,EnterPlanMode", "bytes": 4000}),
        ("S1", "mcp_meter", {"server": "a", "tool": "mcp__a__x", "bytes": 500}),
        # altra sessione: chiamare mcp__a__y QUI non salva S1
        ("S2", "mcp_meter", {"server": "a", "tool": "mcp__a__y", "bytes": 500}),
    ])
    r = report(h)
    check("M2 ratio per sessione: 2 caricati, 1 mai chiamato",
          "2 mcp tools loaded, 1 never called" in r.stdout, r.stdout)
    check("M2 suggerimento presente quando c'e' spreco",
          "load fewer tools per select" in r.stdout, r.stdout)
    check("M3 tool nativo (EnterPlanMode) fuori dal conteggio",
          "3 mcp tools loaded" not in r.stdout, r.stdout)

    # ---- M4: righe senza sid fuori dal ratio, dentro i totali ----
    h = tmp / "m4"
    seed(h, [(None, "mcp_schema_load",
              {"query": "select:mcp__a__x", "bytes": 4000})])
    r = report(h)
    check("M4 solo righe legacy: totali STOCK si', ratio no",
          "STOCK" in r.stdout
          and "loads attributed" not in r.stdout, r.stdout)

    # ---- M5: query troncata a 120 char, frammento finale scartato ----
    h = tmp / "m5"
    q = ("select:mcp__srv__t1,mcp__srv__t2," + "mcp__srv__" + "z" * 200)[:120]
    seed(h, [
        ("S1", "mcp_schema_load", {"query": q, "bytes": 4000}),
        ("S1", "mcp_meter", {"server": "srv", "tool": "mcp__srv__t1",
                             "bytes": 500}),
    ])
    r = report(h)
    check("M5 frammento troncato scartato: 2 caricati, 1 mai chiamato",
          "2 mcp tools loaded, 1 never called" in r.stdout, r.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
