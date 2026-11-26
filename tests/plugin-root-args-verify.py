#!/usr/bin/env python3
"""Verifica deterministica — nessun comando passa una cartella del plugin (1.65.1).

La directory rifiuta un comando che nomina ${CLAUDE_PLUGIN_ROOT} da sola o una
cartella calcolata (COMMAND_PATH_COMPUTED): gli script lanciati dagli hook
nominano file, e leggono la radice da CLAUDE_PLUGIN_ROOT.

  R1 hooks.json: ogni ${CLAUDE_PLUGIN_ROOT} è seguito da /<percorso di un file>
  R2 session-kernel.sh, detach.sh, statusline-install.sh: niente dirname, niente cd,
     niente "${CLAUDE_PLUGIN_ROOT}" o "${CLAUDE_PLUGIN_ROOT}/scripts" come argomento
  R3 statusline-install.sh con CLAUDE_PLUGIN_ROOT: statusLine = bash "<radice>/scripts/statusline-ctx.sh"
  R4 senza CLAUDE_PLUGIN_ROOT, lanciato col percorso assoluto: stesso comando
  R5 senza CLAUDE_PLUGIN_ROOT, lanciato con un percorso relativo: percorso reso assoluto
  R6 session-kernel su Windows simulato: la riga FD Windows nomina py.sh e scripts/NAME.py
  R7 niente here-document negli script degli hook, commenti compresi; la fusione della statusLine
     e' un file (scripts/statusline-merge.py) lanciato per percorso letterale (1.65.3)

Usage: python3 tests/plugin-root-args-verify.py   (exit 0 = all green)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fable-director"
SCRIPTS = ROOT / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {str(evidence)[:600]}"))


hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
cmds = [h["command"] for groups in hooks["hooks"].values() for g in groups for h in g["hooks"]]
bad = [c for c in cmds for m in re.finditer(r"\$\{CLAUDE_PLUGIN_ROOT\}(.?)([^\"\s]*)", c)
       if m.group(1) != "/" or not re.search(r"\.[a-z]{2,3}$", m.group(2))]
check("R1 hooks.json: ${CLAUDE_PLUGIN_ROOT} always followed by a file path", not bad and cmds, bad)

offenders = {}
for name in ("session-kernel.sh", "detach.sh", "statusline-install.sh"):
    code = "\n".join(l for l in (SCRIPTS / name).read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))
    hits = [p for p in ("dirname", "cd \"", "cd $", '"${CLAUDE_PLUGIN_ROOT}"',
                        '"${CLAUDE_PLUGIN_ROOT}/scripts"') if p in code]
    if hits:
        offenders[name] = hits
check("R2 hook scripts compute no folder and pass none", not offenders, offenders)

heredocs = {n: [i for i, l in enumerate((SCRIPTS / n).read_text(encoding="utf-8").splitlines(), 1)
                 if "<<" in l]
            for n in ("session-kernel.sh", "statusline-install.sh", "detach.sh")}
inst = (SCRIPTS / "statusline-install.sh").read_text(encoding="utf-8")
check("R7 no here-document in hook scripts, comments included; the statusLine merge is a file (1.65.3)",
      not any(heredocs.values()) and (SCRIPTS / "statusline-merge.py").is_file()
      and 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/statusline-merge.py"' in inst,
      heredocs)


def install(home, env_root, script_arg, cwd=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT"):
        env.pop(k, None)
    if env_root:
        env["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    r = subprocess.run(["bash", script_arg], capture_output=True, text=True, env=env,
                       cwd=cwd, timeout=60)
    st = home / ".claude" / "settings.json"
    data = json.loads(st.read_text()) if st.is_file() else {}
    return r, (data.get("statusLine") or {}).get("command")


want = f'bash "{(SCRIPTS / "statusline-ctx.sh").as_posix()}"'
r, got = install(Path(tempfile.mkdtemp(prefix="fd-rootargs-a-")), True,
                 str(SCRIPTS / "statusline-install.sh"))
check("R3 with CLAUDE_PLUGIN_ROOT: statusLine names the plugin's statusline-ctx.sh",
      r.returncode == 0 and got == want, f"rc={r.returncode} got={got} err={r.stderr[:200]}")
r, got = install(Path(tempfile.mkdtemp(prefix="fd-rootargs-b-")), False,
                 str(SCRIPTS / "statusline-install.sh"))
check("R4 without it, absolute script path: same command",
      r.returncode == 0 and got == want, f"rc={r.returncode} got={got} err={r.stderr[:200]}")
r, got = install(Path(tempfile.mkdtemp(prefix="fd-rootargs-c-")), False,
                 "scripts/statusline-install.sh", cwd=str(ROOT))
check("R5 without it, relative script path: made absolute",
      r.returncode == 0 and got == f'bash "{ROOT.as_posix()}/scripts/statusline-ctx.sh"',
      f"rc={r.returncode} got={got} err={r.stderr[:200]}")

home = Path(tempfile.mkdtemp(prefix="fd-rootargs-k-"))
env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_PLUGIN_ROOT=str(ROOT),
           OSTYPE="msys")
env.pop("CLAUDE_CONFIG_DIR", None)
k = subprocess.run(["bash", "-c", f'OSTYPE=msys; . "{SCRIPTS / "session-kernel.sh"}"'],
                   capture_output=True, text=True, env=env, input="{}", timeout=120)
line = next((l for l in k.stdout.splitlines() if l.startswith("FD Windows:")), "")
check("R6 Windows kernel line names py.sh and scripts/NAME.py",
      f'bash "{SCRIPTS.as_posix()}/py.sh" "{SCRIPTS.as_posix()}/NAME.py" ARGS' in line,
      line or k.stdout[-400:])

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
