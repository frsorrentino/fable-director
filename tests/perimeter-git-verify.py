#!/usr/bin/env python3
"""Verifica livello 3 del perimetro: deny_git su comandi Bash.

Contratto:
- opt-in: nessuna chiave deny_git → allow sempre (G1);
- frammento dichiarato → deny anche in compound command (G2, G3);
- `git push` semplice NON bloccato dal set consigliato (G4);
- comando senza `git` non matcha mai, anche se contiene il frammento (G5);
- config globale in ~/.claude/fable-director/perimeter.json vale (G6);
- config malformata → fail-open (G7);
- regressione: Write con never_write continua a funzionare come prima (G8);
- normalizzazione spazi: `git  reset   --hard` matcha comunque (G9).

Tutto in HOME e cwd usa-e-getta: mai la HOME reale.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director"
SCRIPT = ROOT / "scripts" / "perimeter-gate.py"
FAILS = []

RECOMMENDED = ["reset --hard", "clean -f", "branch -D",
               "checkout .", "restore .", "push --force", "push -f"]


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(home, cwd, payload):
    e = dict(os.environ, HOME=str(home))
    p = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    return p.stdout


def denied(out):
    try:
        return (json.loads(out)["hookSpecificOutput"]["permissionDecision"]
                == "deny")
    except (json.JSONDecodeError, KeyError):
        return False


def bash(cmd, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd},
            "cwd": str(cwd)}


with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    proj = Path(td) / "proj"
    (home / ".claude" / "fable-director").mkdir(parents=True)
    proj.mkdir()

    # G1: nessuna config → allow
    out = run(home, proj, bash("git reset --hard HEAD~1", proj))
    check("G1 opt-in: senza deny_git allow", not denied(out), out)

    (proj / ".fd-perimeter.json").write_text(
        json.dumps({"deny_git": RECOMMENDED}))

    # G2: frammento dichiarato → deny
    out = run(home, proj, bash("git reset --hard HEAD~1", proj))
    check("G2 reset --hard deny", denied(out), out)

    # G3: compound command → deny
    out = run(home, proj, bash("cd /x && git clean -fd", proj))
    check("G3 compound clean -fd deny", denied(out), out)

    # G4: push semplice non nel set consigliato → allow
    out = run(home, proj, bash("git push origin main", proj))
    check("G4 push semplice allow", not denied(out), out)

    # G5: niente 'git' nel comando → allow anche col frammento
    out = run(home, proj, bash("npm run reset --hard", proj))
    check("G5 comando non-git allow", not denied(out), out)

    # G9: spazi multipli normalizzati → deny
    out = run(home, proj, bash("git  reset   --hard", proj))
    check("G9 spazi multipli deny", denied(out), out)

    # G6: config solo globale → deny
    proj2 = Path(td) / "proj2"
    proj2.mkdir()
    gcf = home / ".claude" / "fable-director" / "perimeter.json"
    gcf.write_text(json.dumps({"deny_git": ["branch -D"]}))
    out = run(home, proj2, bash("git branch -D feature", proj2))
    check("G6 config globale deny", denied(out), out)
    gcf.unlink()

    # G7: config malformata → fail-open
    proj3 = Path(td) / "proj3"
    proj3.mkdir()
    (proj3 / ".fd-perimeter.json").write_text("{not json")
    out = run(home, proj3, bash("git reset --hard", proj3))
    check("G7 config rotta fail-open", not denied(out), out)

    # G8: regressione never_write su Write
    (proj / ".fd-perimeter.json").write_text(
        json.dumps({"never_write": [".env*"], "deny_git": RECOMMENDED}))
    out = run(home, proj, {"tool_name": "Write",
                           "tool_input": {"file_path": ".env"},
                           "cwd": str(proj)})
    check("G8 never_write su Write ancora deny", denied(out), out)
    out = run(home, proj, {"tool_name": "Write",
                           "tool_input": {"file_path": "src/ok.py"},
                           "cwd": str(proj)})
    check("G8b Write fuori pattern allow", not denied(out), out)

    # --- Review 1.35.1: bypass chiusi e falsi positivi rimossi ---
    (proj / ".fd-perimeter.json").write_text(
        json.dumps({"deny_git": RECOMMENDED}))

    # B1: riordino argomenti → deny
    out = run(home, proj, bash("git push origin main --force", proj))
    check("B1 push origin main --force deny", denied(out), out)
    out = run(home, proj, bash("git reset -q --hard", proj))
    check("B1b reset -q --hard deny", denied(out), out)

    # B2: quoting → deny
    out = run(home, proj, bash('git reset "--hard" HEAD~1', proj))
    check("B2 reset \"--hard\" deny", denied(out), out)

    # B3: flag corta combinata → deny (clean -fd contiene -f)
    out = run(home, proj, bash("git clean -fd", proj))
    check("B3 clean -fd deny", denied(out), out)

    # B4: 'gitbook' nel path o frammento citato in grep → allow
    out = run(home, proj, bash('grep -r "reset --hard" /home/u/gitbook/', proj))
    check("B4 grep in gitbook allow", not denied(out), out)

    # B5: commit message che cita la policy → allow (token dopo -m sono
    # un argomento quotato, shlex li tiene ma 'push'+'--force' come token
    # separati non ci sono)
    out = run(home, proj, bash('git commit -m "vietato usare push --force"',
                               proj))
    check("B5 commit msg con frammento allow", not denied(out), out)

    # B6: git checkout feature (senza '.') → allow; checkout -- . → deny
    out = run(home, proj, bash("git checkout feature-branch", proj))
    check("B6 checkout branch allow", not denied(out), out)
    out = run(home, proj, bash("git checkout -- .", proj))
    check("B6b checkout -- . deny", denied(out), out)

    # B7: git -C verso progetto protetto da sessione esterna → deny
    projC = Path(td) / "protetto"
    projC.mkdir()
    (projC / ".fd-perimeter.json").write_text(
        json.dumps({"deny_git": ["reset --hard"]}))
    neutral = Path(td) / "neutrale"
    neutral.mkdir()
    out = run(home, neutral,
              bash(f"git -C {projC} reset --hard", neutral))
    check("B7 git -C progetto protetto deny", denied(out), out)

    # B8: deny_git stringa invece di lista → chiave ignorata con warning,
    # git normale NON negato
    projS = Path(td) / "projS"
    projS.mkdir()
    (projS / ".fd-perimeter.json").write_text(
        json.dumps({"deny_git": "reset --hard"}))
    out = run(home, projS, bash("git status", projS))
    check("B8 config stringa: git status allow", not denied(out), out)
    check("B8b warning presente", "must be a LIST" in out, out)

    # B9: config JSON rotta → warning systemMessage (non silenzio), throttled
    projB = Path(td) / "projB"
    projB.mkdir()
    (projB / ".fd-perimeter.json").write_text("{broken")
    out = run(home, projB, bash("git reset --hard", projB))
    check("B9 config rotta: warning non-bloccante",
          not denied(out) and "NOT parseable" in out, out)
    out = run(home, projB, bash("git reset --hard", projB))
    check("B9b warning throttled al secondo giro",
          "NOT parseable" not in out, out)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} check falliti: {', '.join(FAILS)}")
    sys.exit(1)
print("Tutti i check perimeter-git superati.")
