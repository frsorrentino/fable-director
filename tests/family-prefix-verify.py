#!/usr/bin/env python3
"""Verifica deterministica — prefisso di famiglia gemini:/codex: (1.39.0, D.3.3).

Provider CLI finto (python locale) in un HOME temporaneo: nessuna rete.

  F1 `gemini: scrivi …` → bozza iniettata con intestazione DRAFT e delimitatori;
     evento external_exec {mode: draft, ok}
  F2 `gemini? quanto …` → parere con intestazione OPINION ("report it AS IS")
  F3 prompt senza prefisso, o prefisso a meta' riga → silenzio
  F4 budget aperto restricted → "prefix ignored … restricted", nessuna chiamata
  F5 quality guard: fence di codice / path .py + "implementa" → ignorato
  F6 provider a pagamento → ignorato con la regola del consenso
  F7 provider che fallisce (exit 1) → "not available", turno normale, evento ok=false
  F8 provider non in config → una riga, turno normale
  F9 hooks.json registra family-prefix.py su UserPromptSubmit

Usage: python3 tests/family-prefix-verify.py
"""
import json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
HOOK = SCRIPTS / "family-prefix.py"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-prefix-home-"))
proj = tempfile.mkdtemp(prefix="fd-prefix-proj-")
base = home / ".claude" / "fable-director"; base.mkdir(parents=True)
fake = base / "fake-provider.py"
fake.write_text("import sys\nspec=sys.stdin.read()\n"
                "open(sys.argv[1],'w').write('BOZZA: ' + spec.splitlines()[-1][:60])\n")
fail = base / "fail-provider.py"
fail.write_text("import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
cfg = {"default": "gemini", "providers": {
    "gemini": {"type": "cli", "billing": "free", "model": "fake-flash",
               "command": [sys.executable, str(fake), "{output_file}"]},
    "codex": {"type": "cli", "billing": "paid", "model": "gpt-x",
              "command": [sys.executable, str(fake), "{output_file}"]},
    "gemini-stable": {"type": "cli", "billing": "free", "model": "fake-stable",
                      "command": [sys.executable, str(fail), "{output_file}"]}}}
(base / "cross-family.json").write_text(json.dumps(cfg))


def run(prompt, sid="s-pfx"):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True, env=env,
                          input=json.dumps({"prompt": prompt, "cwd": proj, "session_id": sid}),
                          timeout=60)


def events():
    try:
        con = sqlite3.connect(base / "telemetry.db")
        rows = [json.loads(p) for (p,) in con.execute(
            "select payload from events where event='external_exec' order by id")]
        con.close()
        return rows
    except Exception:
        return []


r = run("gemini: scrivi la lettera alla PA per il permesso edilizio")
ev = events()
check("F1 draft: intestazione DRAFT, bozza tra delimitatori, evento mode=draft ok",
      "[fd-external gemini] DRAFT from gemini (fake-flash)" in r.stdout
      and "BOZZA: scrivi la lettera alla PA" in r.stdout and r.stdout.count("-----") == 2
      and ev and ev[-1]["mode"] == "draft" and ev[-1]["ok"] is True and ev[-1]["type"] == "prefix-draft",
      r.stdout + r.stderr + str(ev[-1:]))
r = run("gemini? quanto costa una fresatrice CNC da banco per legno")
check("F2 opinion: intestazione OPINION 'AS IS'",
      "OPINION from gemini" in r.stdout and "AS IS" in r.stdout and "BOZZA: quanto costa" in r.stdout,
      r.stdout)
r1 = run("scrivi la lettera alla PA, poi gemini: rileggi"); r2 = run("Una domanda normale senza prefisso")
check("F3 senza prefisso / prefisso a meta' riga: silenzio", not r1.stdout and not r2.stdout, r1.stdout + r2.stdout)

# F4 restricted
env = dict(os.environ, HOME=str(home)); env.pop("CLAUDE_CONFIG_DIR", None); env["CLAUDE_CODE_SESSION_ID"] = "s-pfx"
subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"), "budget-open", "--task", "riservato",
                "--expected-output", "10", "--data-class", "restricted", "--cwd", proj], env=env, capture_output=True)
n_before = len(events())
r = run("gemini: riassumi il contratto del cliente")
check("F4 budget restricted: prefisso ignorato, nessuna chiamata",
      "prefix ignored" in r.stdout and "restricted" in r.stdout and len(events()) == n_before, r.stdout)
subprocess.run([sys.executable, str(SCRIPTS / "fd-telemetry.py"), "budget-close", "--outcome", "abandoned",
                "--cwd", proj], env=env, capture_output=True)

# F5 quality guard
r1 = run("gemini: implementa il fix in src/pagamenti.py per il bug del totale")
r2 = run("gemini: sistema questo\n```php\necho 1;\n```")
check("F5 quality guard: codice → ignorato",
      "quality guard" in r1.stdout and "quality guard" in r2.stdout and len(events()) == n_before,
      r1.stdout + r2.stdout)
# F6 paid
r = run("codex: scrivi un verbale della riunione di oggi")
check("F6 provider a pagamento: ignorato, consenso esplicito richiesto",
      "is billed" in r.stdout and "--paid-ok" in r.stdout and len(events()) == n_before, r.stdout)
# F7 provider che fallisce
r = run("gemini-stable: scrivi due righe di auguri")
ev = events()
check("F7 provider giu': 'not available', evento ok=false",
      "not available" in r.stdout and "Normal turn" in r.stdout and ev[-1]["ok"] is False
      and "BOZZA" not in r.stdout, r.stdout + str(ev[-1:]))
# F8 provider mancante
(base / "cross-family.json").write_text(json.dumps({"default": "gemini", "providers": {}}))
r = run("gemini: ciao")
check("F8 provider non in config: una riga, turno normale", "not in cross-family.json" in r.stdout, r.stdout)
# F9 hooks
h = json.loads((HERE.parent / "fable-director" / "hooks" / "hooks.json").read_text())["hooks"]
check("F9 hooks.json: family-prefix.py su UserPromptSubmit con python3",
      any("family-prefix.py" in hk["command"] and hk["command"].startswith("python3")
          for g in h["UserPromptSubmit"] for hk in g["hooks"]))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
