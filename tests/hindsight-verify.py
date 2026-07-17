#!/usr/bin/env python3
"""Verifica di session-hindsight.py e del ramo ToolSearch di mcp-meter.py (1.19.0).

Costruisce un HOME usa-e-getta con un telemetry.db sintetico e inchioda le regole:
  H1 cwd con budget_flag -> stampa le righe, ratio e task inclusi
  H2 cwd senza eventi     -> silenzio totale (zero token dove non c'e' evidenza)
  H3 doppione identico    -> deduplicato (non brucia uno slot del tetto)
  H4 tetto MAX_LINES      -> mai piu' di 5 righe di evidenza
  H5 match esatto sul cwd -> un flag del parent non e' evidenza sul figlio
  H6 lookback             -> eventi oltre 120gg esclusi (stack cambiato = dato che mente)
  H7 reversal             -> reso nel formato from -> to
  H8 DB assente/corrotto  -> silenzio, exit 0 (mai disturbare l'avvio sessione)
  M1 ToolSearch           -> logga mcp_schema_load con bytes+query (giacenza)
  M2 tool non-MCP         -> nessun evento
  M3 ToolSearch vuoto     -> nessun evento (niente righe a zero byte)
  M4 mcp__* invariato     -> continua a loggare mcp_meter (nessuna regressione)
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HINDSIGHT = REPO / "fable-director" / "scripts" / "session-hindsight.py"
METER = REPO / "fable-director" / "scripts" / "mcp-meter.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {detail}")


def mkdb(home, rows):
    """rows: (offset_giorni, cwd, event, payload_dict)"""
    base = home / ".claude" / "fable-director"
    base.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(base / "telemetry.db")
    con.execute("CREATE TABLE IF NOT EXISTS events("
                "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, "
                "cwd TEXT, event TEXT NOT NULL, payload TEXT)")
    for days, cwd, event, payload in rows:
        con.execute("INSERT INTO events(ts, cwd, event, payload) VALUES("
                    "datetime('now', ?), ?, ?, ?)",
                    (f"-{days} days", cwd, event, json.dumps(payload)))
    con.commit()
    con.close()


def run_hindsight(home, cwd, via="stdin"):
    """via="stdin": il cwd arriva nel payload dell'hook (rotta reale, come ogni
    altro hook del plugin). via="env": ripiego CLAUDE_PROJECT_DIR."""
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    stdin = ""
    if via == "stdin":
        stdin = json.dumps({"hook_event_name": "SessionStart", "cwd": cwd,
                            "session_id": "test"})
    else:
        env["CLAUDE_PROJECT_DIR"] = cwd
    return subprocess.run(
        [sys.executable, str(HINDSIGHT)], env=env, input=stdin,
        capture_output=True, text=True, timeout=30)


def run_meter(home, payload):
    return subprocess.run(
        [sys.executable, str(METER)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        input=json.dumps(payload), capture_output=True, text=True, timeout=30)


def events_of(home, event):
    db = home / ".claude" / "fable-director" / "telemetry.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(db)
    rows = [json.loads(p) for (p,) in con.execute(
        "SELECT payload FROM events WHERE event=?", (event,))]
    con.close()
    return rows


BUST = {"task": "triage 240 recensioni", "ratio": 26.3, "dim": "output",
        "expected": 12000, "actual": 315103, "auto": True}

tmp = Path(tempfile.mkdtemp(prefix="fd-hindsight-test-"))
try:
    # H1 / H2: evidenza presente vs assente
    h = tmp / "h1"
    mkdb(h, [(2, "/proj/a", "budget_flag", BUST)])
    r = run_hindsight(h, "/proj/a")
    check("H1 flag reso con ratio e task",
          "26.3x" in r.stdout and "triage 240 recensioni" in r.stdout, r.stdout)
    check("H1 consuntivo reale mostrato", "315103" in r.stdout, r.stdout)
    r = run_hindsight(h, "/proj/senza-storia")
    check("H2 cwd pulito -> silenzio", r.stdout.strip() == "", r.stdout)

    # H3: doppione identico deduplicato
    h = tmp / "h3"
    mkdb(h, [(2, "/proj/a", "budget_flag", BUST),
             (2, "/proj/a", "budget_flag", BUST)])
    r = run_hindsight(h, "/proj/a")
    check("H3 doppione deduplicato", r.stdout.count("26.3x") == 1, r.stdout)

    # H4: tetto rigido a 5 righe di evidenza
    h = tmp / "h4"
    mkdb(h, [(i, "/proj/a", "budget_flag",
              {**BUST, "task": f"task-{i}", "actual": 1000 + i}) for i in range(12)])
    r = run_hindsight(h, "/proj/a")
    check("H4 tetto MAX_LINES rispettato",
          sum(1 for ln in r.stdout.splitlines() if "BUST" in ln) == 5, r.stdout)

    # H5: match esatto, niente prefix-match sul parent
    h = tmp / "h5"
    mkdb(h, [(2, "/proj", "budget_flag", BUST)])
    r = run_hindsight(h, "/proj/figlio")
    check("H5 flag del parent non contamina il figlio",
          r.stdout.strip() == "", r.stdout)

    # H6: fuori finestra di lookback
    h = tmp / "h6"
    mkdb(h, [(400, "/proj/a", "budget_flag", BUST)])
    r = run_hindsight(h, "/proj/a")
    check("H6 evento oltre 120gg escluso", r.stdout.strip() == "", r.stdout)

    # H7: reversal
    h = tmp / "h7"
    mkdb(h, [(2, "/proj/a", "reversal",
              {"from": "verifier-subagents", "to": "inline-verify", "at": "2x"})])
    r = run_hindsight(h, "/proj/a")
    check("H7 reversal reso from -> to",
          "verifier-subagents -> inline-verify" in r.stdout, r.stdout)

    # H9: il cwd arriva dal payload dell'hook (rotta reale, non solo da env).
    # Regressione storica: la 1a stesura leggeva SOLO CLAUDE_PROJECT_DIR — env
    # che nessun altro hook del plugin usa e mai verificata a SessionStart. I
    # test passavano perche' gliela passavano loro. Qui si inchioda la rotta vera.
    h = tmp / "h9"
    mkdb(h, [(2, "/proj/a", "budget_flag", BUST)])
    r = run_hindsight(h, "/proj/a", via="stdin")
    check("H9 cwd letto dal payload stdin", "26.3x" in r.stdout, r.stdout)
    r = run_hindsight(h, "/proj/altro", via="stdin")
    check("H9 payload con cwd diverso -> silenzio", r.stdout.strip() == "", r.stdout)
    r = run_hindsight(h, "/proj/a", via="env")
    check("H9 ripiego CLAUDE_PROJECT_DIR ancora valido", "26.3x" in r.stdout, r.stdout)

    # H10: stdin vuoto/spazzatura non deve bloccare ne' crashare
    h = tmp / "h10"
    mkdb(h, [(2, "/proj/a", "budget_flag", BUST)])
    r = subprocess.run([sys.executable, str(HINDSIGHT)],
                       env={"HOME": str(h), "PATH": "/usr/bin:/bin"},
                       input="non-json{{{", capture_output=True, text=True, timeout=10)
    check("H10 stdin non-JSON -> exit 0, nessun crash", r.returncode == 0, r.stderr)

    # H8: DB assente e DB corrotto -> silenzio, exit 0
    h = tmp / "h8-vuoto"
    h.mkdir()
    r = run_hindsight(h, "/proj/a")
    check("H8 DB assente -> silenzio + exit 0",
          r.returncode == 0 and r.stdout.strip() == "", r.stdout)
    h = tmp / "h8-corrotto"
    (h / ".claude" / "fable-director").mkdir(parents=True)
    (h / ".claude" / "fable-director" / "telemetry.db").write_text("garbage")
    r = run_hindsight(h, "/proj/a")
    check("H8 DB corrotto -> silenzio + exit 0",
          r.returncode == 0 and r.stdout.strip() == "", r.stdout)

    # M1: ToolSearch -> mcp_schema_load (giacenza)
    h = tmp / "m1"
    h.mkdir()
    run_meter(h, {"tool_name": "ToolSearch",
                  "tool_input": {"query": "select:mcp__chrome-bridge__click"},
                  "tool_response": {"schemas": "x" * 4000}})
    loads = events_of(h, "mcp_schema_load")
    check("M1 ToolSearch loggato come giacenza", len(loads) == 1, loads)
    check("M1 bytes e query catturati",
          loads and loads[0]["bytes"] > 4000 and "chrome-bridge" in loads[0]["query"],
          loads)

    # M2 / M3: non-MCP e risposta vuota -> niente
    h = tmp / "m2"
    h.mkdir()
    run_meter(h, {"tool_name": "Read", "tool_response": {"x": 1}})
    run_meter(h, {"tool_name": "ToolSearch", "tool_response": None})
    check("M2 tool non-MCP non loggato", events_of(h, "mcp_meter") == [])
    check("M3 ToolSearch vuoto non loggato", events_of(h, "mcp_schema_load") == [])

    # M4: nessuna regressione sul ramo mcp__*
    h = tmp / "m4"
    h.mkdir()
    run_meter(h, {"tool_name": "mcp__chrome-bridge__click",
                  "tool_response": {"ok": True}})
    meters = events_of(h, "mcp_meter")
    check("M4 ramo mcp__* invariato",
          len(meters) == 1 and meters[0]["server"] == "chrome-bridge", meters)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
