#!/usr/bin/env python3
"""Verifica delle integrazioni da Claude Code >= 2.1.212 (release 1.36.0).

- F1 effort_mix: conteggio per messaggio con dedup message.id, sidechain
  esclusi, chiave assente senza campo effort (dato assente != zero);
- F2 report: sezione "effort mix" con mediana eq per effort dominante;
- F3 fd-status --all: budget aperti di tutta la macchina dai file, con
  ratio dallo state; nessun budget -> messaggio esplicito;
- F4 hindsight: CLAUDE.md pesante -> riga /doctor; leggero -> silenzio;
- F5 DirectoryAdded: con perimetro aperto -> systemMessage con l'amend
  precompilato; senza perimetro -> nessun output;
- F6 budget-open: pricing.json dichiarato -> stima USD; assente -> niente
  numeri inventati.

Tutto in HOME e cwd usa-e-getta.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(script, args, home, stdin=None, cwd=None, env_extra=None):
    e = dict(os.environ, HOME=str(home))
    e.pop("CLAUDE_CONFIG_DIR", None)
    if env_extra:
        e.update(env_extra)
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / script)] + args,
                       input=stdin, capture_output=True, text=True, env=e,
                       cwd=cwd or home, timeout=60)
    return p.stdout + p.stderr


with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    fd = home / ".claude" / "fable-director"
    (fd / "budgets").mkdir(parents=True)
    proj = Path(td) / "proj"
    proj.mkdir()

    # --- F1: effort_mix in sum_transcript ---
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fdt", ROOT / "scripts" / "fd-telemetry.py")
    fdt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fdt)
    tr = Path(td) / "t.jsonl"
    recs = [
        {"type": "assistant", "effort": "high",
         "message": {"id": "m1", "content": []}},
        {"type": "assistant", "effort": "high",          # stesso id: 1 solo
         "message": {"id": "m1", "content": []}},
        {"type": "assistant", "effort": "low",
         "message": {"id": "m2", "content": []}},
        {"type": "assistant", "effort": "high", "isSidechain": True,
         "message": {"id": "m3", "content": []}},        # sidechain: fuori
        {"type": "assistant",                            # senza effort
         "message": {"id": "m4", "content": []}},
    ]
    tr.write_text("\n".join(json.dumps(r) for r in recs))
    *_, stats = fdt.sum_transcript(tr)
    check("F1 effort_mix dedup+sidechain",
          stats.get("effort_mix") == {"high": 1, "low": 1},
          str(stats.get("effort_mix")))
    tr2 = Path(td) / "t2.jsonl"
    tr2.write_text(json.dumps({"type": "assistant",
                               "message": {"id": "x", "content": []}}))
    *_, stats2 = fdt.sum_transcript(tr2)
    check("F1b senza effort: chiave assente", "effort_mix" not in stats2)

    # --- F2: report con sezione effort mix ---
    import sqlite3
    from datetime import datetime, timezone
    con = sqlite3.connect(fd / "telemetry.db")
    con.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, "
                "ts TEXT NOT NULL, session_id TEXT, cwd TEXT, "
                "event TEXT NOT NULL, payload TEXT)")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i in range(3):
        con.execute("INSERT INTO events(ts,session_id,cwd,event,payload) "
                    "VALUES(?,?,?,?,?)", (now, f"s{i}", "/x",
                    "session_summary", json.dumps({
                        "input_tokens": 100, "output_tokens": 1000,
                        "cache_read": 5000, "cache_creation": 500,
                        "main_output": 1000, "subagent_output": 0,
                        "eq_tokens": 6000 + i,
                        "effort_mix": {"high": 10, "low": 2}})))
    con.commit()
    con.close()
    out = run("fd-telemetry.py", ["report"], home)
    check("F2 report effort mix",
          "effort mix (main messages)" in out
          and "dominated by high" in out, out[:400])

    # --- F3: fd-status --all ---
    b = {"task": "flotta di prova", "status": "open",
         "expected_output_tokens": 10000,
         "declared_at": now, "cwd": "/x/progetto-uno"}
    (fd / "budgets" / "slug-aaaa1111.json").write_text(json.dumps(b))
    (fd / "budgets" / "slug-aaaa1111.state.json").write_text(json.dumps(
        {"declared": now, "out": 7000, "wf_out": 0}))
    (fd / "budgets" / "slug-bbbb2222.json").write_text(json.dumps(
        {"task": "chiuso", "status": "closed",
         "expected_output_tokens": 5, "declared_at": now, "cwd": "/x/due"}))
    out = run("fd-status.py", ["--all"], home)
    check("F3 fleet: budget aperto con ratio",
          "progetto-uno" in out and "0.7×" in out, out)
    check("F3b fleet: il chiuso non compare", "chiuso" not in out, out)
    (fd / "budgets" / "slug-aaaa1111.json").write_text(json.dumps(
        dict(b, status="closed")))
    out = run("fd-status.py", ["--all"], home)
    check("F3c fleet vuota: messaggio esplicito", "no open budgets" in out,
          out)

    # --- F4: hindsight CLAUDE.md hygiene ---
    heavy = Path(td) / "heavy"
    heavy.mkdir()
    (heavy / "CLAUDE.md").write_text("x" * 20_000)
    out = run("session-hindsight.py", [], home,
              stdin=json.dumps({"cwd": str(heavy)}), cwd=heavy)
    check("F4 CLAUDE.md pesante: riga /doctor", "/doctor" in out, out)
    light = Path(td) / "light"
    light.mkdir()
    (light / "CLAUDE.md").write_text("poco")
    out = run("session-hindsight.py", [], home,
              stdin=json.dumps({"cwd": str(light)}), cwd=light)
    check("F4b leggero: silenzio", "/doctor" not in out, out)

    # --- F5: DirectoryAdded ---
    slug_mod = fdt.cwd_slug(str(proj))
    (fd / "budgets" / f"{slug_mod}.json").write_text(json.dumps(
        {"task": "t", "status": "open", "paths": ["src/*"],
         "declared_at": now, "cwd": str(proj),
         "expected_output_tokens": 10}))
    out = run("perimeter-gate.py", [], home,
              stdin=json.dumps({"hook_event_name": "DirectoryAdded",
                                "directory": "/altro/posto",
                                "cwd": str(proj)}))
    check("F5 dir aggiunta con perimetro: amend suggerito",
          "budget-amend" in out and "/altro/posto" in out, out)
    (fd / "budgets" / f"{slug_mod}.json").unlink()
    out = run("perimeter-gate.py", [], home,
              stdin=json.dumps({"hook_event_name": "DirectoryAdded",
                                "directory": "/altro", "cwd": str(proj)}))
    check("F5b senza perimetro: nessun output", out.strip() == "", out)

    # --- F6: budget-open con/senza pricing ---
    out = run("fd-telemetry.py",
              ["budget-open", "--task", "t-usd", "--expected-output", "50000",
               "--expected-input", "100000", "--cwd", str(proj)], home)
    check("F6 senza pricing: niente USD", "estimated ceiling" not in out, out)
    run("fd-telemetry.py", ["budget-close", "--outcome", "abandoned",
                            "--cwd", str(proj)], home)
    (fd / "pricing.json").write_text(json.dumps({"input_usd_per_mtok": 10}))
    out = run("fd-telemetry.py",
              ["budget-open", "--task", "t-usd2", "--expected-output", "50000",
               "--expected-input", "100000", "--cwd", str(proj)], home)
    # eq = 100000×1 + 50000×5 = 350000 → $3.50 a 10$/Mtok
    check("F6b con pricing: USD corretto",
          "estimated ceiling ≈ $3.50" in out and "--max-budget-usd" in out,
          out)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} check falliti: {', '.join(FAILS)}")
    sys.exit(1)
print("Tutti i check cc-features superati.")
