#!/usr/bin/env python3
"""The installed plugin is the reviewed one, byte for byte (Anthropic directory).

  I1  hooks run through py.sh write nothing inside the plugin (no __pycache__)
  I2  anonymizer.py, run without py.sh as observe.py runs it, writes nothing either
  I3  allowed-tools: no bare Bash, no relative path, no wildcard inside the path
  I4  the opt-in media venv installs exact versions only
  I5  the statusline never runs another plugin's script
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fable-director"
fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  → {detail}"))
    if not ok:
        fails.append(name)


def tree(p):
    return sorted(str(f.relative_to(p)) for f in p.rglob("*"))


tmp = Path(tempfile.mkdtemp(prefix="fd-immutable-"))
plugin = tmp / "fable-director"
shutil.copytree(ROOT, plugin, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
home = tmp / "home"
home.mkdir()
before = tree(plugin)
env = {k: v for k, v in os.environ.items()
       if k not in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE", "CLAUDE_CONFIG_DIR")}
env.update(HOME=str(home), USERPROFILE=str(home), XDG_CACHE_HOME=str(tmp / "cache"),
           CLAUDE_PLUGIN_ROOT=str(plugin))
hook_in = json.dumps({"session_id": "imm", "cwd": str(tmp), "prompt": "ciao",
                      "hook_event_name": "UserPromptSubmit"})

# I1: route-hint and family-prefix load fd-telemetry.py / external-exec.py as modules.
for script in ("route-hint.py", "family-prefix.py"):
    subprocess.run(["bash", str(plugin / "scripts/py.sh"), str(plugin / "scripts" / script)],
                   input=hook_in, capture_output=True, text=True, env=env, timeout=120)
subprocess.run(["bash", str(plugin / "scripts/statusline-ctx.sh")], input="{}",
               capture_output=True, text=True, env=env, timeout=120)
after = tree(plugin)
check("I1 hooks via py.sh → nessun file nuovo nel plugin", after == before,
      sorted(set(after) - set(before))[:5])
check("I1b la cache bytecode va fuori dal plugin",
      (tmp / "cache" / "fable-director" / "pycache").is_dir(), "pycache prefix vuoto")

# I2
subprocess.run([sys.executable, str(plugin / "scripts/anonymizer.py"), "redact", "--stdin",
                "--map", "imm"], input="Mario Rossi, via Roma 1", capture_output=True,
               text=True, env=env, timeout=120)
after2 = tree(plugin)
check("I2 anonymizer.py senza py.sh → nessun file nuovo nel plugin", after2 == before,
      sorted(set(after2) - set(before))[:5])

# I3
bad = []
for md in sorted((ROOT / "commands").glob("*.md")) + sorted((ROOT / "skills").rglob("SKILL.md")):
    m = re.search(r"^allowed-tools:\s*(.*)$", md.read_text(encoding="utf-8"), re.M)
    if not m:
        continue
    for tool in re.findall(r"\w+(?:\([^)]*\))?", m.group(1)):
        if tool == "Bash":
            bad.append(f"{md.name}: Bash nudo")
        elif tool.startswith("Bash("):
            cmd = tool[5:-1]
            if cmd.endswith(":*"):
                cmd = cmd[:-2]
            if "*" in cmd or '"${CLAUDE_PLUGIN_ROOT}/' not in cmd:
                bad.append(f"{md.name}: {tool}")
check("I3 allowed-tools solo script del plugin, percorsi assoluti senza jolly", not bad, bad)

# I4
src = (ROOT / "scripts/media-doctor.py").read_text(encoding="utf-8")
pk = re.search(r"^PACKAGES = (\[.*\])$", src, re.M)
pk = json.loads(pk.group(1)) if pk else []
check("I4 venv media: versioni esatte", pk and all(re.fullmatch(r"[\w.-]+==[\w.]+", p) for p in pk), pk)

# I5: no `bash "$X"` / `. "$X"` on a path that is not ours
sl = (ROOT / "scripts/statusline-ctx.sh").read_text(encoding="utf-8")
ext = [l for l in sl.splitlines()
       if re.search(r'(^|[|;&(]\s*)(bash|sh|\.|source)\s+"?\$(?!\(dirname)', l.strip())]
check("I5 statusline: nessuno script esterno eseguito", not ext, ext)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAIL'}")
sys.exit(1 if fails else 0)
