#!/usr/bin/env python3
"""Verifica di scripts/py.sh, il launcher Python portabile (1.52.1, test Windows del 27/09).

Su Windows `python3` e' l'alias del Microsoft Store: esiste sul PATH, stampa «Python non e' stato trovato»
ed esce 9009/49. Qui lo si simula su Linux con uno stub in una cartella WindowsApps/ e PATH minimi.

  L1 normale: py.sh esegue python3, stdout UTF-8, PYTHONUTF8=1 esportato
  L2 stub dello Store davanti + `python` vero -> sceglie python, non lo stub
  L3 nessun Python: una riga su stderr, exit 0, stdout vuoto
  L4 OSTYPE=msys: percorso trovato in cache; cache con percorso sparito -> ignorata
  L5 sourced: FD_PYTHON esportato; senza Python ritorna 1 senza uscire
  L6 hooks.json: nessun python3 nudo, ogni comando passa da py.sh
  L7 command body e skill: nessun python3 (observe.md e' generato da claude-observe)
  L8 session-kernel senza Python: kernel intero + avviso, niente domanda XF, contatore intatto
  L9 statusline senza Python: una riga che lo dice, exit 0
  L10 un hook vero (perimeter-gate) dietro lo stub, via py.sh: exit 0, niente messaggio dello Store
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "fable-director"
PYSH = PLUGIN / "scripts" / "py.sh"
BASH = shutil.which("bash") or "/bin/bash"
STORE_MSG = "Python non è stato trovato"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {detail}")


def run(argv, path, home, stdin="", extra=None):
    env = {"HOME": str(home), "PATH": path, "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    env.update(extra or {})
    return subprocess.run(argv, env=env, input=stdin, capture_output=True, text=True, timeout=120)


def bindir(root, tools, python=True):
    """Una cartella con solo i comandi indicati (symlink ai veri) + eventualmente `python` vero."""
    d = root / "bin"
    d.mkdir(parents=True, exist_ok=True)
    for t in tools:
        w = shutil.which(t)
        if w and not (d / t).exists():
            (d / t).symlink_to(w)
    if python and not (d / "python").exists():
        (d / "python").symlink_to(sys.executable)
    return d


tmp = Path(tempfile.mkdtemp(prefix="fd-pysh-test-"))
try:
    home = tmp / "home"
    home.mkdir()
    store = tmp / "WindowsApps"
    store.mkdir()
    stub = store / "python3"
    stub.write_text(f"#!/bin/sh\necho \"{STORE_MSG}; eseguire senza argomenti da installare dal Microsoft Store\" >&2\nexit 49\n")
    stub.chmod(0o755)
    tools = ["bash", "cat", "mkdir", "rm", "dirname", "sed", "head", "tr"]
    real = bindir(tmp / "real", tools)
    bare = bindir(tmp / "bare", tools, python=False)

    # L1
    r = run([BASH, str(PYSH), "-c", "import os,sys; print(sys.stdout.encoding, os.environ.get('PYTHONUTF8'), '▓')"],
            os.environ.get("PATH", "/usr/bin:/bin"), home)
    check("L1 normale: UTF-8 e PYTHONUTF8=1", r.returncode == 0 and r.stdout.split() == ["utf-8", "1", "▓"],
          repr((r.returncode, r.stdout, r.stderr)))

    # L2
    r = run([BASH, str(PYSH), "-c", "import sys; print(sys.executable)"], f"{store}:{real}", home)
    check("L2 stub dello Store saltato, sceglie python", r.returncode == 0 and r.stdout.strip() == str(real / "python")
          and STORE_MSG not in r.stderr, repr((r.returncode, r.stdout, r.stderr)))

    # L3
    r = run([BASH, str(PYSH), "-c", "print(1)"], f"{store}:{bare}", home)
    check("L3 nessun Python: exit 0, stdout vuoto, una riga su stderr",
          r.returncode == 0 and r.stdout == "" and r.stderr.count("\n") == 1 and "no Python 3 found" in r.stderr,
          repr((r.returncode, r.stdout, r.stderr)))

    # L4
    cache = home / ".claude" / "fable-director" / "python-path"
    r = run([BASH, "-c", f'OSTYPE=msys; . "{PYSH}"; echo "$FD_PYTHON"'], f"{store}:{real}", home)
    check("L4a OSTYPE=msys: percorso scritto in cache",
          cache.is_file() and cache.read_text().strip() == str(real / "python"), repr((r.stdout, r.stderr)))
    cache.write_text(str(tmp / "sparito" / "python.exe") + "\n")
    r = run([BASH, "-c", f'OSTYPE=msys; . "{PYSH}"; echo "$FD_PYTHON"'], f"{store}:{real}", home)
    check("L4b cache con percorso sparito: riprova e si corregge",
          r.stdout.strip() == str(real / "python") and cache.read_text().strip() == str(real / "python"),
          repr((r.stdout, cache.read_text())))
    cache.unlink()

    # L5
    r = run([BASH, "-c", f'. "{PYSH}"; echo "rc=$? $FD_PYTHON $PYTHONUTF8"'], f"{store}:{real}", home)
    check("L5a sourced: FD_PYTHON e PYTHONUTF8 esportati", r.stdout.strip() == f"rc=0 {real / 'python'} 1", repr(r.stdout))
    r = run([BASH, "-c", f'. "{PYSH}"; echo "rc=$? continua"'], f"{store}:{bare}", home)
    check("L5b sourced senza Python: ritorna 1 e lo script continua", r.stdout.strip() == "rc=1 continua", repr(r.stdout))

    # L6
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for ev in hooks["hooks"].values() for e in ev for h in e["hooks"]]
    bad = [c for c in cmds if c.lstrip().startswith("python")]
    viapy = [c for c in cmds if ".py" in c.split()[-1] or ".py\"" in c]
    check("L6a hooks.json: nessun comando che inizia con python", not bad, bad)
    check("L6b hooks.json: ogni script .py passa da py.sh (scripts/ o observe/)",
          all(c.startswith(('bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" ', 'bash "${CLAUDE_PLUGIN_ROOT}/observe/py.sh" '))
              for c in cmds if ".py" in c), viapy)

    # L7
    offenders = []
    for f in list((PLUGIN / "commands").glob("*.md")) + list((PLUGIN / "skills").glob("*/SKILL.md")):
        if f.name == "observe.md":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if "python3 " in line:
                offenders.append(f"{f.relative_to(PLUGIN)}:{i}")
    check("L7 comandi e skill senza python3", not offenders, offenders)

    # L8
    h8 = tmp / "h8"
    h8.mkdir()
    r = run([BASH, str(PLUGIN / "scripts" / "session-kernel.sh")], f"{store}:{bare}", h8, stdin='{"source":"startup"}')
    xf_count = h8 / ".claude" / "fable-director" / "xf-onboarding-count"
    check("L8a kernel senza Python: esce intero", r.returncode == 0 and "FABLE-DIRECTOR KERNEL" in r.stdout
          and "Before delegating" in r.stdout, repr((r.returncode, r.stdout[-300:], r.stderr[-300:])))
    check("L8b kernel senza Python: avviso gate spenti", "NO PYTHON 3" in r.stdout, r.stdout[-300:])
    check("L8c kernel senza Python: niente domanda XF, contatore intatto",
          "XF ONBOARDING" not in r.stdout and not xf_count.exists())
    check("L8d kernel senza Python: niente messaggio dello Store", STORE_MSG not in r.stdout + r.stderr, r.stderr[-300:])

    # L9
    r = run([BASH, str(PLUGIN / "scripts" / "statusline-ctx.sh")], f"{store}:{bare}", home, stdin="{}")
    check("L9 statusline senza Python: lo dice, exit 0",
          r.returncode == 0 and "no Python 3 found" in r.stdout and STORE_MSG not in r.stdout,
          repr((r.returncode, r.stdout, r.stderr)))

    # L10
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(tmp),
                          "tool_input": {"file_path": str(tmp / "x.txt"), "content": "→ ✓"}})
    r = run([BASH, str(PYSH), str(PLUGIN / "scripts" / "perimeter-gate.py")], f"{store}:{real}", home, stdin=payload)
    check("L10 hook vero dietro lo stub: exit 0, nessun messaggio dello Store",
          r.returncode == 0 and STORE_MSG not in r.stderr, repr((r.returncode, r.stdout, r.stderr)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
