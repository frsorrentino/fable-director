#!/usr/bin/env python3
"""Verifica dell'onboarding XF a domanda multipla in session-kernel.sh (1.17.1).

HOME usa-e-getta, si esegue session-kernel.sh e si asserisce:
  X1 config assente, prima sessione -> istruzione AskUserQuestion (attempt 1/3)
  X2 sessioni 2 e 3 -> istruzione ripetuta con contatore giusto
  X3 4a sessione senza risposta -> silenzio + marker done scritto (stop nag)
  X4 config presente -> mai la domanda
  X5 marker done presente (utente ha risposto No) -> mai la domanda
  X6 marker legacy xf-onboarding-shown -> ritirato, la domanda appare
  X7 contatore corrotto -> trattato come 0, nessun crash
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "fable-director"
KERNEL = PLUGIN / "scripts" / "session-kernel.sh"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {detail}")


def run(home):
    return subprocess.run(
        ["bash", str(KERNEL)],
        env={"HOME": str(home), "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
             "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30)


ASK = "XF ONBOARDING — ASK THE USER NOW"

tmp = Path(tempfile.mkdtemp(prefix="fd-xf-test-"))
try:
    # X1-X3: escalation del contatore fino al silenzio
    h = tmp / "h1"
    h.mkdir()
    r1 = run(h)
    check("X1 prima sessione: domanda (1/3)",
          ASK in r1.stdout and "attempt 1/3" in r1.stdout, r1.stdout[-200:])
    r2 = run(h)
    check("X2a seconda sessione: domanda (2/3)", "attempt 2/3" in r2.stdout)
    r3 = run(h)
    check("X2b terza sessione: domanda (3/3)", "attempt 3/3" in r3.stdout)
    r4 = run(h)
    done = h / ".claude" / "fable-director" / "xf-onboarding-done"
    check("X3 quarta sessione: silenzio + done", ASK not in r4.stdout and done.exists())
    r5 = run(h)
    check("X3b quinta sessione: ancora silenzio", ASK not in r5.stdout)

    # X4: config presente -> mai la domanda
    h = tmp / "h2"
    (h / ".claude" / "fable-director").mkdir(parents=True)
    (h / ".claude" / "fable-director" / "cross-family.json").write_text("{}")
    r = run(h)
    check("X4 config presente: silenzio", ASK not in r.stdout)

    # X5: utente ha già risposto No
    h = tmp / "h3"
    (h / ".claude" / "fable-director").mkdir(parents=True)
    (h / ".claude" / "fable-director" / "xf-onboarding-done").touch()
    r = run(h)
    check("X5 done presente: silenzio", ASK not in r.stdout)

    # X6: marker legacy ritirato, domanda riappare
    h = tmp / "h4"
    (h / ".claude" / "fable-director").mkdir(parents=True)
    legacy = h / ".claude" / "fable-director" / "xf-onboarding-shown"
    legacy.touch()
    r = run(h)
    check("X6 legacy ritirato + domanda",
          not legacy.exists() and ASK in r.stdout)

    # X7: contatore corrotto -> come 0, nessun crash
    h = tmp / "h5"
    (h / ".claude" / "fable-director").mkdir(parents=True)
    (h / ".claude" / "fable-director" / "xf-onboarding-count").write_text("garbage\n")
    r = run(h)
    check("X7 contatore corrotto: domanda (1/3), exit 0",
          "attempt 1/3" in r.stdout and r.returncode == 0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
