#!/usr/bin/env python3
"""Verifica di route-hint.py (1.23.0).

HOME usa-e-getta con soft-deps.json/cross-family.json sintetici, poi inchioda:
  R1  keyword parola singola matcha        -> hint con nome entry + classe
  R2  nessun match                         -> silenzio totale
  R3  confine di parola ("formato" NON scatta su keyword "form")
  R4  frase multi-parola matcha come substring
  R5  segnale cardinalità + provider       -> hint external-exec asse 4
  R6  segnale cardinalità SENZA provider   -> silenzio (rotta inesistente)
  R7  prompt corto                         -> silenzio (mai un task)
  R8  slash command                        -> silenzio
  R9  config corrotta                      -> fail-open: silenzio, exit 0
  R10 entry senza hint_keywords            -> ignorata (opt-in)
  R11 cap a 3 candidati
  R12 telemetria: evento route_hint a match, NESSUN evento a silenzio
  R13 exit 0 sempre (mai bloccare il prompt)
  R14 payload telemetria: solo nomi match + lunghezza, MAI il testo del prompt
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "fable-director" / "scripts" / "route-hint.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {detail}")


DEPS = {
    "_schema": "commento: le voci _ vanno ignorate",
    "gemini-docs": {
        "classes": ["documentation-lookup", "doc-qa"],
        "hint_keywords": ["documentazione", "pdf", "leggi la doc"],
    },
    "chrome-bridge": {
        "classes": ["browser-automation"],
        "hint_keywords": ["form", "screenshot"],
    },
    "senza-keywords": {"classes": ["qualcosa"], "note": "nessuna hint_keywords"},
    "extra-a": {"classes": ["a"], "hint_keywords": ["alfa-kw"]},
    "extra-b": {"classes": ["b"], "hint_keywords": ["beta-kw"]},
}
XFAM = {"providers": {"gemini": {}, "grok": {}}}


def mkhome(deps=DEPS, xfam=XFAM, raw=None):
    home = Path(tempfile.mkdtemp(prefix="fd-rh-"))
    base = home / ".claude" / "fable-director"
    base.mkdir(parents=True)
    if raw is not None:
        (base / "soft-deps.json").write_text(raw)
        (base / "cross-family.json").write_text(raw)
    else:
        (base / "soft-deps.json").write_text(json.dumps(deps))
        (base / "cross-family.json").write_text(json.dumps(xfam))
    return home


def run(home, prompt):
    payload = {"prompt": prompt, "session_id": "sid-test", "cwd": "/proj/x"}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        input=json.dumps(payload), capture_output=True, text=True, timeout=30)


def events(home):
    db = home / ".claude" / "fable-director" / "telemetry.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(db)
    rows = [json.loads(p) for (p,) in con.execute(
        "SELECT payload FROM events WHERE event='route_hint'")]
    con.close()
    return rows


tmp = []
try:
    h = mkhome(); tmp.append(h)
    r = run(h, "devo consultare la documentazione di PrestaShop per il modulo carrello")
    check("R1 keyword singola -> hint con entry e classe",
          "[fd-route-hint]" in r.stdout and "gemini-docs" in r.stdout
          and "documentation-lookup" in r.stdout, r.stdout[:150])
    check("R13a exit 0 con hint", r.returncode == 0)
    ev = events(h)
    check("R12a evento route_hint scritto a match",
          len(ev) == 1 and "gemini-docs" in ev[0]["matches"], ev)
    check("R14 telemetria senza testo del prompt",
          ev and "PrestaShop" not in json.dumps(ev) and "prompt_len" in ev[0], ev)

    h = mkhome(); tmp.append(h)
    r = run(h, "sistemare il colore del bottone nella pagina delle impostazioni")
    check("R2 nessun match -> silenzio", r.stdout.strip() == "", r.stdout[:100])
    check("R12b nessun evento a silenzio", events(h) == [])
    check("R13b exit 0 a silenzio", r.returncode == 0)

    h = mkhome(); tmp.append(h)
    r = run(h, "convertire il file nel formato corretto per la stampa")
    check("R3 'formato' non scatta su keyword 'form'",
          r.stdout.strip() == "", r.stdout[:100])

    h = mkhome(); tmp.append(h)
    r = run(h, "prima di rispondere leggi la doc del progetto per capire il flusso")
    check("R4 frase multi-parola matcha come substring",
          "gemini-docs" in r.stdout, r.stdout[:150])

    h = mkhome(); tmp.append(h)
    r = run(h, "aggiorna il campo meta description su tutti gli articoli del blog")
    check("R5 cardinalità + provider -> hint external-exec",
          "external-exec" in r.stdout and "asse 4" in r.stdout
          and "gemini" in r.stdout, r.stdout[:200])

    h = mkhome(xfam={"providers": {}}); tmp.append(h)
    r = run(h, "aggiorna il campo meta description su tutti gli articoli del blog")
    check("R6 cardinalità senza provider -> silenzio",
          r.stdout.strip() == "", r.stdout[:100])

    h = mkhome(); tmp.append(h)
    r = run(h, "ok documentazione")
    check("R7 prompt corto -> silenzio", r.stdout.strip() == "")

    h = mkhome(); tmp.append(h)
    r = run(h, "/status con documentazione e pdf e screenshot inclusi")
    check("R8 slash command -> silenzio", r.stdout.strip() == "")

    h = mkhome(raw="non-json{{{"); tmp.append(h)
    r = run(h, "devo consultare la documentazione di PrestaShop per il modulo")
    check("R9 config corrotta -> fail-open silenzioso, exit 0",
          r.returncode == 0 and r.stdout.strip() == "", r.stdout[:100])

    h = mkhome(); tmp.append(h)
    r = run(h, "il tool senza-keywords non deve mai comparire, serve qualcosa di suo")
    check("R10 entry senza hint_keywords ignorata",
          "senza-keywords" not in r.stdout, r.stdout[:100])

    h = mkhome(); tmp.append(h)
    r = run(h, "documentazione pdf con screenshot del form, alfa-kw e beta-kw e tutti i casi")
    lines = [l for l in r.stdout.splitlines() if l.startswith("- ")]
    check("R11 cap a 3 candidati", len(lines) == 3, r.stdout)
finally:
    for h in tmp:
        shutil.rmtree(h, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
