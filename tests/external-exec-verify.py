#!/usr/bin/env python3
"""Verifica deterministica delle feature 1.16.0 di external-exec.py
(distillate dalla review di openai/codex-plugin-cc, 2026-07-13).

Runs the REAL script against a throwaway HOME and a stub CLI provider:

  E1  placeholder {model}/{effort} resi dai default del provider
  E2  --effort override raggiunge il comando
  E3  --effort su template senza {effort} → error rumoroso (mai ignorato)
  E4  --schema-file: schema_args accodati + required-keys check → schema-valid
  E5  output senza chiave required → error schema-invalid, exit 1
  E6  timeout dal campo "timeout" del provider (niente --timeout esplicito)
  E7  --resume-last usa il template resume_command
  E8  --resume-last su provider senza resume_command → error rumoroso
  E9  --schema-json su output non-JSON → error json-invalid (regressione)
  E10 --model override raggiunge il comando

Usage: python3 tests/external-exec-verify.py   (exit 0 = all green)
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "fable-director" / "scripts" / "external-exec.py"

passed, failed = [], []

STUB = '''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
out = args[args.index("--out") + 1] if "--out" in args else None
mode = os.environ.get("STUB_MODE", "echo")
sys.stdin.read()
if mode == "sleep":
    time.sleep(5)
text = "not a json {" if mode == "notjson" else json.dumps({"argv": args})
if out:
    open(out, "w").write(text)
else:
    print(text)
'''


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def slug(cwd):
    s = str(cwd).replace("\\", "/")
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])


def setup():
    home = Path(tempfile.mkdtemp(prefix="fd-xexec-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-xexec-proj-"))
    stub = home / "stub-cli.py"
    stub.write_text(STUB)
    cfg_dir = home / ".claude" / "fable-director"
    (cfg_dir / "budgets").mkdir(parents=True)
    py = sys.executable
    config = {
        "default": "stub",
        "providers": {
            "stub": {
                "type": "cli",
                "command": [py, str(stub), "run", "{model}", "{effort}",
                            "--out", "{output_file}"],
                "resume_command": [py, str(stub), "resume", "{effort}",
                                   "--out", "{output_file}"],
                "schema_args": ["--schema", "{schema_file}"],
                "model": "stub-model-1",
                "effort": "high",
                "timeout": 2,
                "billing": "free",
            },
            "stub-plain": {
                "type": "cli",
                "command": [py, str(stub), "run", "--out", "{output_file}"],
                "model": "stub-plain-model",
                "billing": "free",
            },
            "stub-paid": {
                "type": "cli",
                "command": [py, str(stub), "run", "--out", "{output_file}"],
                "model": "stub-paid-model",
                "billing": "paid",
                "cost_note": "~$9.99/call",
            },
            "stub-nobilling": {
                "type": "cli",
                "command": [py, str(stub), "run", "--out", "{output_file}"],
                "model": "stub-nobilling-model",
            },
        },
    }
    (cfg_dir / "cross-family.json").write_text(json.dumps(config))
    (cfg_dir / "budgets" / f"{slug(proj)}.json").write_text(json.dumps({
        "status": "open",
        "declared_at": datetime.now(timezone.utc).isoformat(),
    }))
    return home, proj


def run(home, proj, args, mode="echo"):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               STUB_MODE=mode)
    return subprocess.run([sys.executable, str(SCRIPT)] + args,
                          capture_output=True, env=env, cwd=proj, timeout=60,
                          encoding="utf-8", errors="replace")


def field(stdout, key):
    m = re.search(rf"^{key}: (.*)$", stdout, re.MULTILINE)
    return m.group(1) if m else ""


def main():
    home, proj = setup()
    schema_ok = home / "schema-ok.json"
    schema_ok.write_text(json.dumps(
        {"type": "object", "required": ["argv"]}))
    schema_bad = home / "schema-bad.json"
    schema_bad.write_text(json.dumps(
        {"type": "object", "required": ["nope"]}))

    # E1 — default {model}/{effort} dal config.
    r = run(home, proj, ["--spec", "hi"])
    check("E1 placeholders from provider defaults",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok"
          and "stub-model-1" in r.stdout and '"high"' in r.stdout,
          r.stdout + r.stderr)

    # E2 — --effort override.
    r = run(home, proj, ["--spec", "hi", "--effort", "low"])
    check("E2 --effort override reaches command",
          r.returncode == 0 and '"low"' in r.stdout and '"high"' not in r.stdout,
          r.stdout + r.stderr)

    # E3 — --effort senza placeholder nel template → errore, mai ignorato.
    r = run(home, proj, ["--spec", "hi", "--provider", "stub-plain",
                         "--effort", "low"])
    check("E3 --effort without placeholder is a loud error",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "placeholder" in r.stdout, r.stdout + r.stderr)

    # E4 — --schema-file: schema_args accodati + check locale.
    r = run(home, proj, ["--spec", "hi", "--schema-file", str(schema_ok)])
    check("E4 --schema-file appends schema_args, CHECK schema-valid",
          r.returncode == 0 and field(r.stdout, "CHECK") == "schema-valid"
          and "--schema" in r.stdout and str(schema_ok) in r.stdout,
          r.stdout + r.stderr)

    # E5 — chiave required assente → schema-invalid, mai a valle.
    r = run(home, proj, ["--spec", "hi", "--schema-file", str(schema_bad)])
    check("E5 missing required key → schema-invalid",
          r.returncode == 1 and field(r.stdout, "CHECK") == "schema-invalid"
          and "nope" in r.stdout, r.stdout + r.stderr)

    # E6 — timeout dal provider (2s < sleep 5s), senza --timeout.
    r = run(home, proj, ["--spec", "hi"], mode="sleep")
    check("E6 provider-level timeout honored",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "timeout (2s)" in r.stdout, r.stdout + r.stderr)

    # E7 — --resume-last usa resume_command.
    r = run(home, proj, ["--spec", "delta", "--resume-last"])
    check("E7 --resume-last uses resume_command template",
          r.returncode == 0 and '"resume"' in r.stdout
          and '"run"' not in r.stdout, r.stdout + r.stderr)

    # E8 — --resume-last senza resume_command → errore rumoroso.
    r = run(home, proj, ["--spec", "delta", "--provider", "stub-plain",
                         "--resume-last"])
    check("E8 --resume-last without resume_command is a loud error",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and "resume_command" in r.stdout, r.stdout + r.stderr)

    # E9 — regressione: --schema-json su output non-JSON resta json-invalid.
    r = run(home, proj, ["--spec", "hi", "--schema-json"], mode="notjson")
    check("E9 --schema-json still rejects non-JSON output",
          r.returncode == 1 and field(r.stdout, "CHECK") == "json-invalid",
          r.stdout + r.stderr)

    # E10 — --model override.
    r = run(home, proj, ["--spec", "hi", "--model", "stub-model-2"])
    check("E10 --model override reaches command",
          r.returncode == 0 and "stub-model-2" in r.stdout
          and "stub-model-1" not in r.stdout, r.stdout + r.stderr)

    # E11 — provider paid senza --paid-ok → unavailable, mai eseguito.
    r = run(home, proj, ["--spec", "hi", "--provider", "stub-paid"])
    check("E11 paid provider without --paid-ok is refused",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout and "--paid-ok" in r.stdout
          and "$9.99" in r.stdout, r.stdout + r.stderr)

    # E12 — stesso provider con --paid-ok → esegue.
    r = run(home, proj, ["--spec", "hi", "--provider", "stub-paid",
                         "--paid-ok"])
    check("E12 paid provider with --paid-ok runs",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)

    # E13 — billing assente = paid (fail-closed).
    r = run(home, proj, ["--spec", "hi", "--provider", "stub-nobilling"])
    check("E13 missing billing field is fail-closed (treated as paid)",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout, r.stdout + r.stderr)

    # E14 — free provider resta invariato anche con --paid-ok presente.
    r = run(home, proj, ["--spec", "hi", "--paid-ok"])
    check("E14 --paid-ok on a free provider is a no-op",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
