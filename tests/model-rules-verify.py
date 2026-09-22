#!/usr/bin/env python3
"""Verifica deterministica — model-rules-check.py (regole per modello, solo lettura).

  R1 account puliti (regola stop, TASKS.md, switchModelsOnFlag, niente frasi): exit 0
  R2 forbid: frase in CLAUDE.md, skill, agente, kernel di un plugin nostro → file:riga
  R3 forbid non tocca plugin altrui ne' falsi positivi noti ("explain the reasoning so that")
  R4 require: regola stop e TASKS.md assenti → una segnalazione per account
  R5 setting: switchModelsOnFlag assente → segnalazione; false conta come scelta fatta
  R6 --model col prefisso piu' lungo ([1m] incluso); modello senza regole → exit 2
  R7 nessun file modificato (mtime e contenuto invariati)
  R8 --json strutturato

Usage: python3 tests/model-rules-verify.py   (exit 0 = all green)
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "fable-director" / "scripts" / "model-rules-check.py"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(home, *args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, env=env, timeout=60)


def w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def account(home, name, claude_md="", settings=None, kernel="", skills=None,
            agents=None, foreign_skill=""):
    acc = home / name
    if claude_md is not None:
        w(acc / "CLAUDE.md", claude_md)
    w(acc / "settings.json", json.dumps(settings if settings is not None else {}))
    plug = acc / "plugins" / "cache" / "fsorrentino" / "fable-director" / "9.9.9"
    w(plug / "kernel.md", kernel)
    other = acc / "plugins" / "cache" / "x" / "altrui" / "1.0.0"
    w(other / "skills" / "a" / "SKILL.md", foreign_skill)
    w(acc / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": {
        "fable-director@fsorrentino": [{"installPath": str(plug)}],
        "altrui@x": [{"installPath": str(other)}]}}))
    for rel, text in (skills or {}).items():
        w(acc / "skills" / rel / "SKILL.md", text)
    for rel, text in (agents or {}).items():
        w(acc / "agents" / f"{rel}.md", text)
    return acc


GOOD_MD = "# Istruzioni\n\n## Quando fermarti\n\nVai avanti.\n"
GOOD_KERNEL = "Long task: checklist in `TASKS.md`.\n"

# R1
home = Path(tempfile.mkdtemp(prefix="fd-rules-"))
for n in (".claude", ".claude-pixel"):
    account(home, n, claude_md=GOOD_MD, settings={"switchModelsOnFlag": True},
            kernel=GOOD_KERNEL,
            skills={"ok": "Explain the reasoning so that the model understands why."},
            foreign_skill="Think step by step.")
r = run(home, "--model", "claude-opus-5-5")
check("R1 account puliti: exit 0, 'clean' per entrambi, skill altrui ignorata, falso positivo noto evitato",
      r.returncode == 0 and r.stdout.count("  clean") == 2 and "0 finding(s)" in r.stdout,
      r.stdout + r.stderr)

# R2-R5
home = Path(tempfile.mkdtemp(prefix="fd-rules-"))
a1 = account(home, ".claude", claude_md="# X\nPlease think hard before answering.\n",
             settings={}, kernel="Show your reasoning in the reply.\n",
             skills={"s1": "line\nWork step-by-step.\n"},
             agents={"ag": "Think out loud."})
a2 = account(home, ".claude-pixel", claude_md=None, settings={"switchModelsOnFlag": False},
             kernel=GOOD_KERNEL)
before = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
          for p in home.rglob("*") if p.is_file()}
r = run(home, "--model", "claude-opus-5-5[1m]")
out = r.stdout
check("R2 forbid: CLAUDE.md:2, skill:2, agente:1, kernel del plugin nostro:1",
      r.returncode == 1
      and "no-think-hard  ~/.claude/CLAUDE.md:2" in out
      and "no-think-hard  ~/.claude/skills/s1/SKILL.md:2" in out
      and "no-visible-reasoning  ~/.claude/agents/ag.md:1" in out
      and "no-visible-reasoning  ~/.claude/plugins/cache/fsorrentino/fable-director/9.9.9/kernel.md:1" in out,
      out)
check("R3 plugin altrui mai letto", "altrui" not in out, out)
blk1 = out.split("== claude-opus-5-5 — ~/.claude-pixel")[0]
blk2 = (out.split("== claude-opus-5-5 — ~/.claude-pixel")[1].split("\nWhy:")[0]
        if "~/.claude-pixel" in out else "")
check("R4 require: stop-rule mancante su entrambi, TASKS.md mancante solo dove non c'e'",
      "REQUIRE  stop-rule" in blk1 and "REQUIRE  stop-rule" in blk2
      and "task-list-file" in blk1 and "task-list-file" not in blk2, out)
check("R5 setting: assente → segnalazione; false = scelta fatta, nessuna segnalazione",
      "switchModelsOnFlag not set" in blk1 and "switchModelsOnFlag" not in blk2, out)
after = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
         for p in home.rglob("*") if p.is_file()}
check("R7 nessun file creato o modificato", before == after,
      str(set(after) ^ set(before)))

# R6
r6 = run(home, "--model", "claude-sonnet-5")
check("R6 modello senza file di regole → exit 2 con messaggio",
      r6.returncode == 2 and "no rule file for claude-sonnet-5" in r6.stderr, r6.stderr)

# R8
r8 = run(home, "--model", "claude-opus-5-5", "--json")
try:
    j = json.loads(r8.stdout)
except Exception:
    j = {}
check("R8 --json: conteggio e risultati per account",
      r8.returncode == 1 and j.get("findings", 0) >= 8 and len(j.get("results", [])) == 2
      and all("rule" in f for b in j["results"] for f in b["findings"]),
      r8.stdout[:400])

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
