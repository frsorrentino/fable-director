#!/usr/bin/env python3
"""Verifica deterministica — memoria al momento giusto (1.39.0: E1, E3/C1.2, D.3.2).

  H1 SessionStart (startup): "Last session in this folder: N days ago, T turns;
     last task budget: closed fine (type), N days ago." da session_summary +
     task_close dello stesso cwd; sessione corrente esclusa
  H2 SessionStart (compact): niente riga (stessa sessione)
  H3 cwd senza sessioni: niente riga, niente inventato
  H4 budget-open --type con 3 precedenti: "3 similar tasks before (t): they cost
     about a third of what was estimated; 2 closed fine, 1 blew the budget."
  H5 budget-open senza tipo o tipo mai visto: nessuna riga
  H6 UserPromptSubmit: ricevuta ok di un'ALTRA cartella con ≥2 termini rari in
     comune → una riga [fd-memory] con task, cartella, data, path ricevuta
  H7 stessa cartella, esito flagged, o 1 solo termine: silenzio

Usage: python3 tests/memory-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(args, home, stdin=None, cwd=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, env=env, input=stdin, timeout=60, cwd=cwd)


home = Path(tempfile.mkdtemp(prefix="fd-mem-home-"))
proj = tempfile.mkdtemp(prefix="fd-mem-proj-")
other = tempfile.mkdtemp(prefix="fd-mem-other-")
os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)


def ts_days_ago(d):
    return (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")


def insert(event, payload, cwd, days, sid="old-sess"):
    import sqlite3
    fdt.log_event(event, payload, session_id=sid, cwd=cwd)
    con = sqlite3.connect(fdt.DB_PATH)
    con.execute("UPDATE events SET ts=? WHERE id=(SELECT max(id) FROM events)", (ts_days_ago(days),))
    con.commit(); con.close()


# H1..H3
insert("session_summary", {"n_usage_records": 212, "eq_tokens": 100}, proj, 3)
insert("task_close", {"type": "design-review", "outcome": "ok", "expected_output_tokens": 100,
                      "actual_output_tokens": 30, "declared_at": "x1"}, proj, 5)


def start(source, cwd, sid="new-sess"):
    return run([str(SCRIPTS / "session-hindsight.py")], home,
               json.dumps({"cwd": cwd, "source": source, "session_id": sid}))


r = start("startup", proj)
check("H1 startup: ultima sessione + ultimo task budget in parole",
      "Last session in this folder: 3 days ago, 212 turns; last task budget: closed fine (design-review), 5 days ago." in r.stdout,
      r.stdout + r.stderr)
r = start("compact", proj)
check("H2 compact: niente riga", "Last session" not in r.stdout, r.stdout)
r = start("startup", other)
check("H3 cwd senza sessioni: silenzio", "Last session" not in r.stdout, r.stdout)

# H4/H5
insert("task_close", {"type": "design-review", "outcome": "ok", "expected_output_tokens": 100,
                      "actual_output_tokens": 35, "declared_at": "x2"}, other, 9)
insert("task_close", {"type": "design-review", "outcome": "flagged", "expected_output_tokens": 100,
                      "actual_output_tokens": 320, "declared_at": "x3"}, other, 12)
r = run([str(SCRIPTS / "fd-telemetry.py"), "budget-open", "--task", "review nuovo",
         "--expected-output", "100", "--type", "design-review", "--cwd", proj], home)
check("H4 budget-open: 3 precedenti in parole",
      "FD ≈ 3 similar tasks before (design-review): they cost about a third of what was estimated; 2 closed fine, 1 blew the budget." in r.stdout,
      r.stdout + r.stderr)
run([str(SCRIPTS / "fd-telemetry.py"), "budget-close", "--outcome", "ok", "--cwd", proj], home)
r = run([str(SCRIPTS / "fd-telemetry.py"), "budget-open", "--task", "altro",
         "--expected-output", "100", "--type", "mai-visto", "--cwd", proj], home)
r2 = run([str(SCRIPTS / "fd-telemetry.py"), "budget-open", "--task", "altro2",
          "--expected-output", "100", "--cwd", other], home)
check("H5 tipo mai visto / senza tipo: nessuna riga",
      "similar task" not in r.stdout and "similar task" not in r2.stdout, r.stdout + r2.stdout)

# H6/H7 — ricevute
rdir = home / ".claude" / "fable-director" / "receipts"; rdir.mkdir(parents=True, exist_ok=True)
(rdir / "other-20260730T100000Z.json").write_text(json.dumps({
    "task": "scraping anomalo su prestashop joyconcept: rate limit e pulizia log",
    "verify": "log puliti e rate limit attivo", "type": "incident", "outcome": "ok",
    "cwd": other, "closed_at": "2026-07-30T10:00:00Z"}))
(rdir / "other-20260730T100000Z.md").write_text("# Closed fine: scraping anomalo\n")
(rdir / "same-20260801T100000Z.json").write_text(json.dumps({
    "task": "migrazione database wordpress verso hosting siteground", "outcome": "ok", "cwd": proj,
    "closed_at": "2026-08-01T10:00:00Z"}))
(rdir / "flag-20260802T100000Z.json").write_text(json.dumps({
    "task": "scraping prestashop fallito con rate limit", "outcome": "flagged", "cwd": other,
    "closed_at": "2026-08-02T10:00:00Z"}))


def prompt(text, cwd=proj):
    return run([str(SCRIPTS / "route-hint.py")], home,
               json.dumps({"prompt": text, "cwd": cwd, "session_id": "s9"}))


r = prompt("Un altro sito PrestaShop ha traffico anomalo, sembra scraping: cosa faccio?")
check("H6 prompt con 2 termini rari (prestashop, scraping) → riga [fd-memory] dalla ricevuta ok di un'altra cartella",
      "[fd-memory] a similar task closed fine elsewhere: \"scraping anomalo su prestashop joyconcept" in r.stdout
      and "(2026-07-30)" in r.stdout and "other-20260730T100000Z.md" in r.stdout,
      r.stdout + r.stderr)
r = prompt("Migrazione database wordpress verso siteground: come procedo?", cwd=proj)
r2 = prompt("Il sito PrestaShop va aggiornato alla versione nuova, procedi con calma")
check("H7 stessa cartella esclusa; 1 solo termine → silenzio",
      "[fd-memory]" not in r.stdout and "[fd-memory]" not in r2.stdout, r.stdout + r2.stdout)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
