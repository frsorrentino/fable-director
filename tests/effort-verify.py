#!/usr/bin/env python3
"""Verifica deterministica — sforzo per cliente e per deliverable (1.39.0, D.3.1).

  C1 clients.json assente: istruzione, nessun numero inventato
  C2 --client su due cartelle e due account: sessioni, ore, eq, deleghe,
     deliverable con esito e riaperture; nessun simbolo di valuta
  C3 --month filtra
  C4 --like: deliverable simili con ratio in parole, eq, durata; niente sotto
     due termini in comune
  C5 cliente sconosciuto: errore con l'elenco

Usage: python3 tests/effort-verify.py
"""
import importlib.util, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-effort-home-"))
base = home / ".claude" / "fable-director"; base.mkdir(parents=True)
os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)


def run(args):
    env = dict(os.environ, HOME=str(home)); env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"), "effort"] + args,
                          capture_output=True, text=True, env=env, timeout=60)


def insert(event, payload, cwd, ts, sid="s"):
    fdt.log_event(event, payload, session_id=sid, cwd=cwd)
    con = sqlite3.connect(fdt.DB_PATH)
    con.execute("UPDATE events SET ts=? WHERE id=(SELECT max(id) FROM events)", (ts,))
    con.commit(); con.close()


cwd1 = str(home / "work" / "joyconcept-site"); cwd2 = str(home / "work" / "joyconcept-module")
cwd3 = str(home / "work" / "altro-cliente")
insert("session_summary", {"duration_s": 5400, "eq_tokens": 200000, "n_usage_records": 120,
                           "subagent_output": 5000, "n_subagent_files": 3, "account": ".claude"},
       cwd1, "2026-08-05T10:00:00Z", "a")
insert("session_summary", {"duration_s": 1800, "eq_tokens": 50000, "n_usage_records": 40,
                           "subagent_output": 0, "n_subagent_files": 0, "account": ".claude-pixel"},
       cwd2, "2026-08-20T10:00:00Z", "b")
insert("session_summary", {"duration_s": 3600, "eq_tokens": 90000, "n_usage_records": 80},
       cwd3, "2026-08-21T10:00:00Z", "c")
insert("task_close", {"task": "modulo prestashop spedizioni personalizzate", "type": "ps-module",
                      "route": "agent", "outcome": "ok", "expected_output_tokens": 100,
                      "actual_output_tokens": 160, "reopens": 2, "actual_eq_tokens": 120000,
                      "declared_at": "2026-08-05T10:00:00Z", "closed_at": "2026-08-05T12:30:00Z"},
       cwd1, "2026-08-05T12:30:00Z", "a")
insert("task_close", {"task": "incidente scraping joyconcept", "type": "incident", "route": "inline",
                      "outcome": "flagged", "expected_output_tokens": 50, "actual_output_tokens": 200,
                      "reopens": 0, "declared_at": "2026-08-20T10:00:00Z", "closed_at": "2026-08-20T10:40:00Z"},
       cwd2, "2026-08-20T10:40:00Z", "b")
insert("external_exec", {"provider": "gemini", "ok": True}, cwd1, "2026-08-05T11:00:00Z", "a")

r = run(["--client", "joyconcept"])
check("C1 clients.json assente: istruzione", r.returncode != 0 and "no clients declared" in (r.stderr + r.stdout),
      r.stdout + r.stderr)
(base / "clients.json").write_text(json.dumps({"joyconcept": ["~/work/joyconcept*"], "altro": ["~/work/altro-cliente"]}))
r = run(["--client", "joyconcept"])
out = r.stdout
check("C2 --client: due cartelle, due account, ore, eq, deleghe, deliverable, riaperture, niente valuta",
      "sessions: 2 (160 turns, 2.0 h of active session time, 250.000 eq) on accounts .claude, .claude-pixel" in out
      and "work/joyconcept-module" in out and "work/joyconcept-site" in out
      and "delegations: 3 agent runs, 5.000 tokens delegated; external: gemini ×1" in out
      and "deliverables (task budgets closed): 2 — 1 blew the budget, 1 closed fine; 2 file reopens" in out
      and "modulo prestashop spedizioni personalizzate (ps-module): closed fine, cost 1.6 times the estimate" in out
      and "$" not in out and "€" not in out and "altro-cliente" not in out,
      out + r.stderr)
r = run(["--client", "joyconcept", "--month", "2026-08"]); r2 = run(["--client", "joyconcept", "--month", "2026-07"])
check("C3 --month filtra", "sessions: 2" in r.stdout and "nothing recorded" in r2.stdout, r.stdout + r2.stdout)
r = run(["--like", "un modulo prestashop per le spedizioni"])
r2 = run(["--like", "un modulo generico"])
check("C4 --like: deliverable simile con parole, eq, durata; sotto soglia niente",
      "modulo prestashop spedizioni personalizzate (ps-module, agent): closed fine" in r.stdout
      and "cost 1.6 times the estimate" in r.stdout and "120.000 eq" in r.stdout and "2 reopens" in r.stdout
      and "2 h" in r.stdout and "Effort, not price" in r.stdout
      and "no closed task resembles" in r2.stdout,
      r.stdout + r2.stdout)
r = run(["--client", "boh"])
check("C5 cliente sconosciuto: errore con elenco", r.returncode != 0 and "declared: altro, joyconcept" in (r.stderr + r.stdout),
      r.stderr)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
