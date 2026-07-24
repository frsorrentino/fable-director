#!/usr/bin/env python3
"""Verifica di due irrigidimenti di rotta (1.29.0).

1) Gate PreToolUse — delega ANNIDATA e effort NATIVO.
   Da Claude Code 2.1.219 la profondità di default dei subagent annidati è 3
   (prima 1): un subagent già autorizzato può generare nipoti che il gate non
   vedeva come delega nuova. Il payload porta `agent_id` solo DENTRO un
   subagent: è il segnale deterministico per riconoscerli e scriverli in
   telemetria (nessun deny — il budget del cwd li copre — ma non più invisibili).
   Sempre dal payload arriva `effort.level`, l'effort REALE della sessione:
   prima la coerenza col budget si poteva controllare solo sugli agent fd-*
   con effort pinnato nel frontmatter.

2) external-exec — un free tier che CHIUDE non è un rate limit.
   401/402/403, o 429 con un corpo che parla di billing, venivano riportati
   come "rate limit / endpoint changed?": diagnosi sbagliata che invita a
   ritentare una porta che non riaprirà (caso dichiarato: la gratuità di Grok
   è una promozione a termine). Ora è un fallimento di classe billing, loggato.
"""
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director"
GATE = ROOT / "scripts" / "pre-delegation-gate.py"
TELEMETRY = ROOT / "scripts" / "fd-telemetry.py"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


tmp = Path(tempfile.mkdtemp(prefix="fd-hardening-test"))
home = tmp / "home"
(home / ".claude" / "fable-director").mkdir(parents=True)
work = tmp / "work"
work.mkdir()
env = dict(os.environ, HOME=str(home))
env.pop("CLAUDE_CONFIG_DIR", None)
env.pop("CLAUDE_EFFORT", None)


def gate(payload):
    return subprocess.run([sys.executable, str(GATE)], env=env, text=True,
                          input=json.dumps(payload), capture_output=True,
                          timeout=30)


def events(kind):
    db = home / ".claude" / "fable-director" / "telemetry.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(db)
    rows = [r[0] for r in con.execute(
        "SELECT payload FROM events WHERE event=?", (kind,))]
    con.close()
    return rows


print("gate: delega annidata + effort nativo")

subprocess.run([sys.executable, str(TELEMETRY), "budget-open",
                "--task", "test hardening", "--expected-output", "1000",
                "--route", "agent", "--effort", "low"],
               env=env, cwd=str(work), capture_output=True, text=True,
               timeout=30)

# H1: chiamata dal thread principale (nessun agent_id) → nessun avviso annidato
r = gate({"hook_event_name": "PreToolUse", "tool_name": "Agent",
          "cwd": str(work), "session_id": "s1",
          "tool_input": {"subagent_type": "fable-director:fd-executor"}})
check("H1 delega di primo livello → nessun avviso di annidamento",
      "nested delegation" not in r.stdout and r.returncode == 0, r.stdout)

# H2: stessa chiamata DENTRO un subagent → avviso + evento in telemetria
r2 = gate({"hook_event_name": "PreToolUse", "tool_name": "Agent",
           "cwd": str(work), "session_id": "s1",
           "agent_id": "ag-1", "agent_type": "Explore",
           "tool_input": {"subagent_type": "fable-director:fd-executor"}})
check("H2 agent_id nel payload → avviso di delega annidata",
      "nested delegation" in r2.stdout and "Explore" in r2.stdout, r2.stdout)
check("H3 annidamento scritto in telemetria (lo scrive l'hook, non il modello)",
      len(events("nested_spawn")) == 1
      and "fd-executor" in events("nested_spawn")[0], str(events("nested_spawn")))
check("H4 la delega annidata NON viene negata (il budget del cwd la copre)",
      "\"deny\"" not in r2.stdout, r2.stdout)

# H5: effort nativo — target senza frontmatter, sessione a xhigh, budget low
r3 = gate({"hook_event_name": "PreToolUse", "tool_name": "Agent",
           "cwd": str(work), "session_id": "s1",
           "effort": {"level": "xhigh"},
           "tool_input": {"subagent_type": "general-purpose"}})
check("H5 target non pinnato + effort di sessione ≠ budget → mismatch visto",
      "effort mismatch" in r3.stdout
      and "inherits the session effort 'xhigh'" in r3.stdout, r3.stdout)
mism = events("effort_mismatch")
check("H6 evento effort_mismatch con origine 'session'",
      any('"origin": "session"' in m for m in mism), str(mism))

# H7: effort di sessione uguale al dichiarato → silenzio
r4 = gate({"hook_event_name": "PreToolUse", "tool_name": "Agent",
           "cwd": str(work), "session_id": "s1",
           "effort": {"level": "low"},
           "tool_input": {"subagent_type": "general-purpose"}})
check("H7 effort coerente → nessun avviso",
      "effort mismatch" not in r4.stdout, r4.stdout)

print()
print("external-exec: free tier chiuso ≠ rate limit")

os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location(
    "xexec", ROOT / "scripts" / "external-exec.py")
xexec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xexec)


def failure(code, body=b"", prov=None):
    e = urllib.error.HTTPError("https://x/api", code, "err", {},
                               io.BytesIO(body))
    buf = io.StringIO()
    rc = None
    try:
        with redirect_stdout(buf):
            xexec.http_failure(e, "grok", prov or {"model": "grok-4",
                                                   "billing": "free"})
    except SystemExit as ex:
        rc = ex.code
    return rc, buf.getvalue()


rc, out = failure(403, b'{"error":"forbidden"}')
check("X1 HTTP 403 → billing/accesso rifiutato, non 'rate limit'",
      rc == 1 and "billing refused" in out and "rate limit" not in out, out)

rc, out = failure(402, b'{"error":"payment required"}')
check("X2 HTTP 402 → stessa classe", rc == 1 and "billing refused" in out, out)

rc, out = failure(429, b'{"error":"quota exceeded, enable billing"}')
check("X3 HTTP 429 con corpo che parla di billing → classe billing",
      rc == 1 and "billing refused" in out, out)

rc, out = failure(429, b'{"error":"rate limit exceeded, retry in 20s"}')
check("X4 HTTP 429 transitorio → resta un rate limit (nessun falso allarme)",
      rc == 1 and "rate limit" in out and "billing refused" not in out, out)

rc, out = failure(500, b"boom")
check("X5 HTTP 500 → errore generico, dettaglio riportato",
      rc == 1 and "boom" in out and "billing refused" not in out, out)

blocked = events("external_exec")
check("X6 il blocco billing finisce in telemetria con check=billing-block",
      sum(1 for b in blocked if '"check": "billing-block"' in b) == 3,
      str(blocked))
check("X7 unavailable resta fail-closed (mai 'eseguito')",
      "NEVER treat unavailable as executed" in out, out)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto irrigidimenti di rotta rispettato")
