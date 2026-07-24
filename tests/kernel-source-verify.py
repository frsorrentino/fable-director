#!/usr/bin/env python3
"""Verifica del kernel SessionStart consapevole di `source`.

Fino a 1.28.x session-kernel.sh non leggeva lo stdin dell'hook: trattava
startup, resume, clear, compact e fork come la stessa cosa. Conseguenze reali:
ogni COMPATTAZIONE bruciava uno dei 3 tentativi dell'onboarding executor esterni
(nessun umano ha mai visto quella domanda) e ripeteva l'hindsight già dato nella
stessa sessione.

Contratto verificato qui:
- il kernel viene emesso SEMPRE, qualunque sia il source (dopo una compattazione
  il testo iniettato può non esserci più: è il caso in cui serve di più);
- solo startup/resume (o `source` assente, Claude Code vecchi) consumano un
  tentativo di onboarding; compact/fork/clear non lo toccano;
- l'hindsight è saltato su compact e presente altrove;
- source=fork stampa l'avviso sul budget condiviso per-cwd;
- stdin assente o non-JSON non rompe nulla (degrada al comportamento vecchio).
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director"
SCRIPT = ROOT / "scripts" / "session-kernel.sh"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(home, payload, cwd="/tmp/fd-kernel-cwd"):
    e = dict(os.environ, HOME=str(home), CLAUDE_PLUGIN_ROOT=str(ROOT))
    e.pop("CLAUDE_CONFIG_DIR", None)
    p = subprocess.run(["bash", str(SCRIPT)], env=e, text=True,
                       input=payload, capture_output=True, timeout=30)
    return p.stdout


def payload(source, cwd="/tmp/fd-kernel-cwd"):
    d = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": cwd}
    if source:
        d["source"] = source
    return json.dumps(d)


def count(home):
    f = home / ".claude" / "fable-director" / "xf-onboarding-count"
    return int(f.read_text().strip()) if f.is_file() else 0


def fresh_home():
    tmp = Path(tempfile.mkdtemp(prefix="fd-kernel-test"))
    home = tmp / "home"
    base = home / ".claude" / "fable-director"
    base.mkdir(parents=True)
    # Storia di sfondamenti su QUESTO cwd: senza, l'hindsight è muto per
    # progetto e non si potrebbe distinguere "saltato" da "niente da dire".
    con = sqlite3.connect(base / "telemetry.db")
    con.execute("CREATE TABLE IF NOT EXISTS events("
                "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session_id TEXT, "
                "cwd TEXT, event TEXT NOT NULL, payload TEXT)")
    con.execute("INSERT INTO events(ts, session_id, cwd, event, payload) "
                "VALUES(datetime('now','-1 day'),'old','/tmp/fd-kernel-cwd',"
                "'budget_flag',?)",
                (json.dumps({"task": "triage recensioni", "ratio": 3.2,
                             "auto": True, "expected_output": 1000,
                             "actual_output": 3200}),))
    con.commit()
    con.close()
    return home


print("kernel SessionStart consapevole di source:")

# K1: startup → kernel + onboarding + hindsight, contatore a 1
home = fresh_home()
out = run(home, payload("startup"))
check("K1 startup → kernel, onboarding proposto, tentativo consumato",
      "FABLE-DIRECTOR KERNEL" in out and "XF ONBOARDING" in out
      and count(home) == 1, f"count={count(home)}")
check("K2 startup → hindsight ripescato",
      "triage recensioni" in out, out[-400:])

# K3: compact → kernel sì, onboarding no, contatore fermo, hindsight no
before = count(home)
out2 = run(home, payload("compact"))
check("K3 compact → kernel comunque emesso",
      "FABLE-DIRECTOR KERNEL" in out2)
check("K4 compact → nessun tentativo di onboarding bruciato",
      "XF ONBOARDING" not in out2 and count(home) == before,
      f"count={count(home)} before={before}")
check("K5 compact → hindsight saltato (già visto in questa sessione)",
      "triage recensioni" not in out2, out2[-300:])

# K6: fork → avviso budget condiviso, nessun tentativo bruciato
out3 = run(home, payload("fork"))
check("K6 fork → avviso sul budget per-cwd condiviso col padre",
      "forked session" in out3 and "worktree" in out3, out3[:300])
check("K7 fork → nessun tentativo di onboarding bruciato",
      "XF ONBOARDING" not in out3 and count(home) == before)

# K8: clear → nessun tentativo bruciato (stessa sessione che ricomincia)
out4 = run(home, payload("clear"))
check("K8 clear → nessun tentativo di onboarding bruciato",
      "XF ONBOARDING" not in out4 and count(home) == before)

# K9: resume → tentativo consumato (è un'apertura vera)
out5 = run(home, payload("resume"))
check("K9 resume → onboarding proposto, tentativo consumato",
      "XF ONBOARDING" in out5 and count(home) == before + 1,
      f"count={count(home)}")

# K10: payload senza source (Claude Code vecchi) → comportamento storico
home2 = fresh_home()
out6 = run(home2, payload(None))
check("K10 source assente → si comporta come prima (onboarding proposto)",
      "XF ONBOARDING" in out6 and count(home2) == 1)

# K11: stdin non-JSON → nessun crash, kernel comunque emesso
home3 = fresh_home()
out7 = run(home3, "non-json{{")
check("K11 stdin non-JSON → kernel emesso lo stesso",
      "FABLE-DIRECTOR KERNEL" in out7, out7[:200])

# K12: il kernel non è mai vuoto (kernel.md davvero incluso)
check("K12 kernel.md incluso nel testo iniettato",
      "delegation" in out.lower() and len(out) > 500)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto kernel source-aware rispettato")
