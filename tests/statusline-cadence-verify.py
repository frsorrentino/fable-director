#!/usr/bin/env python3
"""Statusline: una esecuzione alla volta, cache, parti pesanti a cadenza (1.63.1).

Perche esiste: su una macchina carica un giro di statusline-ctx.sh durava
10-14 s. Con refreshInterval 5 i giri si accavallavano (38 avvii in 20 s su
quattro sessioni), Claude Code annullava quello in corso a ogni aggiornamento
e la status bar spariva. Su Windows, in piu, il blocco `python -c` aveva
superato il tetto di 32 767 caratteri della riga di comando: Python non
partiva e la riga restava «Claude».

  C1 niente `python -c` nella statusline: il nucleo e statusline-core.py
  C2 un giro normale scrive la cache e toglie il lock (nessun file .lock)
  C3 lock preso da un altro giro: la riga in cache esce subito, nessun calcolo
  C4 lock orfano (piu vecchio di un minuto): ripreso, calcolo fatto, lock lasciato
  C5 calcolo lento: dopo FD_SL_WAIT_S esce la riga in cache; il calcolo finisce
     staccato e aggiorna la cache per il giro dopo
  C6 parti pesanti: entro FD_SL_HEAVY_S si riusa il risultato (heavy.json
     invariato); un effort diverso le ricalcola subito; a cadenza scaduta pure
  C7 modalita expert: i token passano ancora dalla shell (riga storica)

Usage: python3 tests/statusline-cadence-verify.py   (exit 0 = all green)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director" / "scripts"
SL = ROOT / "statusline-ctx.sh"
# su Windows subprocess cerca prima in System32, dove bash.exe e quello di WSL
BASH = shutil.which("bash") or "bash"
FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {detail}"))
    if not ok:
        FAILS.append(name)


def home_():
    h = Path(tempfile.mkdtemp(prefix="fd-sl-cadence-"))
    (h / ".claude" / "fable-director").mkdir(parents=True)
    return h


def env_(home, **kw):
    e = dict(os.environ, HOME=str(home), USERPROFILE=str(home), COLUMNS="140")
    for k in ("CLAUDE_CONFIG_DIR", "FD_STATUSLINE_MODE", "FD_SL_HEAVY_S", "FD_SL_WAIT_S"):
        e.pop(k, None)
    e.update({k: str(v) for k, v in kw.items()})
    return e


def stdin(sid, effort="high", pct=26):
    return json.dumps({
        "session_id": sid, "cwd": "/tmp/proj", "workspace": {"current_dir": "/tmp/proj"},
        "model": {"id": "claude-fable-5-1", "display_name": "Fable 5.1"},
        "effort": {"level": effort},
        "context_window": {"used_percentage": pct, "context_window_size": 200000}})


def run(home, sid, **kw):
    s = kw.pop("_stdin", None) or stdin(sid)
    t0 = time.time()
    r = subprocess.run([BASH, str(SL)], input=s, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env_(home, **kw), timeout=120)
    return r.stdout, time.time() - t0


def cache_dir(home):
    return home / ".claude" / "fable-director" / "statusline-cache"


# C1
src = SL.read_text(encoding="utf-8")
check("C1 niente python -c nella statusline, nucleo in statusline-core.py",
      '"$FD_PYTHON" -c' not in src and "statusline-core.py" in src
      and (ROOT / "statusline-core.py").is_file())

# C2
h = home_()
out, _ = run(h, "cad-c2")
sc = cache_dir(h)
check("C2 giro normale: riga resa, cache scritta, lock lasciato",
      "context 26%" in out and (sc / "cad-c2.out").is_file()
      and not (sc / "cad-c2.lock").exists(), f"{out!r} {sorted(p.name for p in sc.iterdir())}")

# C3
(sc / "cad-c2.lock").write_text("")
heavy = sc / "cad-c2.heavy.json"
before = heavy.read_text()
out, dt = run(h, "cad-c2", _stdin=stdin("cad-c2", pct=77))
check("C3 lock preso: esce subito la riga in cache, nessun calcolo",
      "context 26%" in out and "77" not in out and heavy.read_text() == before and dt < 5,
      f"{out!r} dt={dt:.2f}")

# C4
old = time.time() - 300
os.utime(sc / "cad-c2.lock", (old, old))
out, _ = run(h, "cad-c2", _stdin=stdin("cad-c2", pct=77))
check("C4 lock orfano (vuoto, file vecchio): ripreso, riga nuova, lock tolto",
      "context 77%" in out and not (sc / "cad-c2.lock").exists(), repr(out))
(sc / "cad-c2.lock").write_text(f"{int(time.time()) - 120}\n")
out, _ = run(h, "cad-c2", _stdin=stdin("cad-c2", pct=78))
check("C4b lock con l'ora di presa vecchia di 2 minuti: ripreso",
      "context 78%" in out and not (sc / "cad-c2.lock").exists(), repr(out))
(sc / "cad-c2.lock").write_text(f"{int(time.time())}\n")
out, _ = run(h, "cad-c2", _stdin=stdin("cad-c2", pct=79))
check("C4c lock con l'ora di presa di adesso: occupato, riga in cache",
      "context 78%" in out and (sc / "cad-c2.lock").exists(), repr(out))
(sc / "cad-c2.lock").write_text(f"{int(time.time()) - 120}\n")
run(h, "cad-c2", _stdin=stdin("cad-c2", pct=77))

# C5: interprete lento (3 s) dietro FD_PYTHON
slow = h / "slowpy.sh"
slow.write_text(f'#!/bin/bash\nsleep 3\nexec "{sys.executable}" "$@"\n')
slow.chmod(0o755)
out, dt = run(h, "cad-c2", _stdin=stdin("cad-c2", pct=55), FD_PYTHON=str(slow), FD_SL_WAIT_S="0.5")
check("C5a calcolo lento: dopo l'attesa massima esce la riga in cache",
      "context 77%" in out and dt < 2.5, f"{out!r} dt={dt:.2f}")
deadline = time.time() + 20
while time.time() < deadline and "context 55%" not in (sc / "cad-c2.out").read_text(encoding="utf-8"):
    time.sleep(0.2)
check("C5b il calcolo staccato finisce e aggiorna la cache, lock lasciato",
      "context 55%" in (sc / "cad-c2.out").read_text(encoding="utf-8")
      and not (sc / "cad-c2.lock").exists(),
      (sc / "cad-c2.out").read_text(encoding="utf-8"))
# un giro mentre quello lento e in corso stampa la cache senza aspettare
out1, _ = run(h, "cad-c2", _stdin=stdin("cad-c2", pct=60), FD_PYTHON=str(slow), FD_SL_WAIT_S="0.3")
out2, dt2 = run(h, "cad-c2", _stdin=stdin("cad-c2", pct=61))
check("C5c secondo giro durante quello lento: cache subito, nessun accavallamento",
      "context 61%" not in out2 and dt2 < 2.5, f"{out2!r} dt={dt2:.2f}")
deadline = time.time() + 20
while time.time() < deadline and (sc / "cad-c2.lock").exists():
    time.sleep(0.2)

# C6
h6 = home_()
run(h6, "cad-c6", FD_SL_HEAVY_S="30")
hv = cache_dir(h6) / "cad-c6.heavy.json"
at1 = json.loads(hv.read_text())["at"]
out, _ = run(h6, "cad-c6", FD_SL_HEAVY_S="30", _stdin=stdin("cad-c6", pct=40))
at2 = json.loads(hv.read_text())["at"]
check("C6a entro la cadenza: parti pesanti riusate, parti leggere fresche",
      at1 == at2 and "context 40%" in out, f"{at1} {at2} {out!r}")
run(h6, "cad-c6", FD_SL_HEAVY_S="30", _stdin=stdin("cad-c6", effort="max"))
at3 = json.loads(hv.read_text())["at"]
check("C6b effort cambiato: ricalcolo subito", at3 > at2, f"{at2} {at3}")
run(h6, "cad-c6", FD_SL_HEAVY_S="0", _stdin=stdin("cad-c6", effort="max"))
at4 = json.loads(hv.read_text())["at"]
check("C6c cadenza scaduta: ricalcolo", at4 > at3, f"{at3} {at4}")
snap = h6 / ".claude" / "fable-director" / "sessions" / "cad-c6.json"
check("C6d lo snapshot di sessione (gate, consiglio) esce dal giro pesante",
      snap.is_file() and json.loads(snap.read_text()).get("effort") == "max")

# C7
h7 = home_()
out, _ = run(h7, "cad-c7", FD_STATUSLINE_MODE="expert")
plain = out.replace("\x1b[0m", "")
check("C7 expert: riga storica dalla shell (modello e ctx)",
      "FABLE 5.1" in out and "ctx" in out and "FDTOK" not in out, repr(out))

print()
if FAILS:
    print(f"{len(FAILS)} FAIL: {', '.join(FAILS)}")
    sys.exit(1)
print("all green")
