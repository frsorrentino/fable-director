#!/usr/bin/env python3
"""Verifica di fail-streak.py (1.20.0).

Costruisce transcript JSONL sintetici (stessa forma dei file reali: tool_use
con id+name, tool_result con tool_use_id+is_error) e un HOME usa-e-getta, poi
inchioda le regole:
  F1 3 fallimenti di fila       -> avviso + evento fail_streak
  F2 2 fallimenti               -> silenzio (la dottrina avvisa ALLA 3ª)
  F3 successo in mezzo          -> azzera lo streak, silenzio
  F4 6 di fila                  -> riavvisa (3 e 6, non a ogni fallimento)
  F5 4 di fila                  -> silenzio (avvisa solo ai multipli di 3)
  F6 negazione dell'utente      -> NON conta come fallimento (e' una sua scelta)
  F7 negazione tra 3 fallimenti -> non azzera: non e' un esito
  F8 fallimenti di ALTRI tool   -> ignorati (solo Bash)
  F9 payload: solo il BINARIO, mai la riga di comando (contiene segreti)
  F10 tool_name != Bash         -> esce subito, nessun evento
  F11 transcript assente/rotto  -> silenzio, exit 0
  F12 non blocca MAI            -> exit 0 anche quando avvisa
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "fable-director" / "scripts" / "fail-streak.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {detail}")


def mktranscript(path, seq):
    """seq: lista di (tool_name, esito) — esito: 'ok' | 'err' | 'deny'."""
    lines = []
    for i, (tool, outcome) in enumerate(seq):
        uid = f"tu_{i}"
        lines.append(json.dumps({"message": {"content": [
            {"type": "tool_use", "id": uid, "name": tool,
             "input": {"command": "secret-cmd --token=SEGRETISSIMO"}}]}}))
        text = {"ok": "done", "err": "Exit code 1\nboom",
                "deny": "The user doesn't want to proceed with this tool use."}[outcome]
        lines.append(json.dumps({"message": {"content": [
            {"type": "tool_result", "tool_use_id": uid,
             "is_error": outcome != "ok", "content": text}]}}))
    Path(path).write_text("\n".join(lines) + "\n")


def run(home, transcript, tool="Bash", cmd="rg --json 'x' /repo | head"):
    payload = {"tool_name": tool, "transcript_path": str(transcript),
               "session_id": "sid-test", "cwd": "/proj/x",
               "tool_input": {"command": cmd}}
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
        "SELECT payload FROM events WHERE event='fail_streak'")]
    con.close()
    return rows


def grind_state(home, sid="sid-test"):
    """Lo streak che la statusline leggera'. None = file assente."""
    f = home / ".claude" / "fable-director" / "grinding" / f"{sid}.json"
    if not f.is_file():
        return None
    return json.loads(f.read_text()).get("streak")


def case(tag, seq, tool="Bash", cmd="rg --json 'x' /repo | head"):
    home = Path(tempfile.mkdtemp(prefix=f"fd-fs-{tag}-"))
    t = home / "t.jsonl"
    mktranscript(t, seq)
    r = run(home, t, tool=tool, cmd=cmd)
    return home, r, events(home)


tmp = []
try:
    E, O, D = ("Bash", "err"), ("Bash", "ok"), ("Bash", "deny")

    h, r, ev = case("f1", [E, E, E]); tmp.append(h)
    check("F1 3 falliti -> avviso", "3 comandi Bash falliti" in r.stdout, r.stdout[:120])
    check("F1 evento fail_streak scritto", len(ev) == 1 and ev[0]["streak"] == 3, ev)
    check("F1 avviso porta i 4 tipi della rule-of-3",
          all(k in r.stdout for k in ("INFRA", "CAPABILITY", "APPROACH", "TOOL/TARGET")), r.stdout[:80])
    check("F12 non blocca mai (exit 0 anche avvisando)", r.returncode == 0)

    h, r, ev = case("f2", [E, E]); tmp.append(h)
    check("F2 2 falliti -> silenzio", r.stdout.strip() == "" and ev == [])

    h, r, ev = case("f3", [E, E, O, E]); tmp.append(h)
    check("F3 un successo azzera lo streak", r.stdout.strip() == "" and ev == [])

    h, r, ev = case("f4", [E] * 6); tmp.append(h)
    check("F4 6 di fila -> riavvisa", "6 comandi Bash falliti" in r.stdout and ev[0]["streak"] == 6)

    h, r, ev = case("f5", [E] * 4); tmp.append(h)
    check("F5 4 di fila -> silenzio (solo multipli di 3)", r.stdout.strip() == "" and ev == [])

    h, r, ev = case("f6", [D, D, D]); tmp.append(h)
    check("F6 negazioni utente NON sono fallimenti", r.stdout.strip() == "" and ev == [])

    h, r, ev = case("f7", [E, D, E, E]); tmp.append(h)
    check("F7 negazione in mezzo non azzera (non e' un esito)",
          "3 comandi Bash falliti" in r.stdout, r.stdout[:80])

    h, r, ev = case("f8", [("Read", "err"), ("Edit", "err"), ("Read", "err")]); tmp.append(h)
    check("F8 fallimenti di altri tool ignorati", r.stdout.strip() == "" and ev == [])

    h, r, ev = case("f9", [E, E, E], cmd="curl -H 'Authorization: Bearer SEGRETO' https://x"); tmp.append(h)
    check("F9 payload logga solo il binario", ev and ev[0]["binary"] == "curl", ev)
    check("F9 nessun segreto finito su disco",
          ev and "SEGRETO" not in json.dumps(ev), ev)

    h, r, ev = case("f10", [E, E, E], tool="Read"); tmp.append(h)
    check("F10 tool != Bash -> esce subito", r.stdout.strip() == "" and ev == [])

    # Stato per la statusline: scritto a OGNI Bash (l'hook e' l'autorita', la
    # statusline legge e basta). La soglia di VISUALIZZAZIONE (>=2) sta nella
    # statusline, non qui: qui si scrive sempre il valore vero, 0 compreso.
    h, r, ev = case("g1", [E, E, E]); tmp.append(h)
    check("G1 streak 3 scritto nello stato grinding", grind_state(h) == 3, grind_state(h))
    h, r, ev = case("g2", [E, E]); tmp.append(h)
    check("G2 streak 2 scritto anche se sotto la soglia del nudge",
          grind_state(h) == 2, grind_state(h))
    h, r, ev = case("g3", [E, E, O]); tmp.append(h)
    check("G3 un successo scrive streak 0 (azzera il segmento)", grind_state(h) == 0, grind_state(h))
    h, r, ev = case("g4", [E, E, E], tool="Read"); tmp.append(h)
    check("G4 tool != Bash non tocca lo stato (nessun file)", grind_state(h) is None)

    home = Path(tempfile.mkdtemp(prefix="fd-fs-f11-")); tmp.append(home)
    r = run(home, home / "non-esiste.jsonl")
    check("F11a transcript assente -> silenzio, exit 0",
          r.returncode == 0 and r.stdout.strip() == "")
    bad = home / "rotto.jsonl"; bad.write_text("non-json{{{\n{}\n")
    r = run(home, bad)
    check("F11b transcript corrotto -> silenzio, exit 0",
          r.returncode == 0 and r.stdout.strip() == "")
finally:
    for h in tmp:
        shutil.rmtree(h, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
