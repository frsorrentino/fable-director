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
  E17 {prompt}: spec come argomento letterale, stdin vuoto, chiave dal
      config in env, cwd isolata vuota e rimossa (Antigravity CLI)
  E18 regressione: senza {prompt} la spec resta su stdin, nessuna chiave
  W1  CLI che scrive nel repo (senza isolated_cwd) → error worktree-write, exit 1
  W2  CLI che non scrive, repo con file non tracciati → ok (niente falso positivo)
  W3  CLI con isolated_cwd che scrive nella sua cartella → ok
  W4  fuori da un repo git → nessun controllo, ok
  S1-S11 input sensibili verso provider che addestrano: tema WP consentito,
      wp-config/dump/csv/.env/sensitive_paths rifiutati, --allow-sensitive
      consente e registra, segreti sempre [SECRET], provider senza
      addestramento invariato

Usage: python3 tests/external-exec-verify.py   (exit 0 = all green)
"""
import hashlib
import json
import os
import re
import sqlite3
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
stdin = sys.stdin.read()
if mode == "sleep":
    time.sleep(5)
if mode == "write":
    open("intruso.txt", "w").write("x")
text = "not a json {" if mode == "notjson" else json.dumps({"argv": args})
if mode == "probe":
    text = json.dumps({"argv": args, "stdin": stdin,
                       "key": os.environ.get("STUB_KEY"),
                       "cwd": os.getcwd(), "files": os.listdir(".")})
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
            "stub-arg": {
                "type": "cli",
                "command": [py, str(stub), "run", "-p={prompt}"],
                "model": "stub-arg-model",
                "api_key": "k-123",
                "api_key_env": "STUB_KEY",
                "isolated_cwd": True,
                "billing": "free",
            },
            "stub-notrain": {
                "type": "cli",
                "command": [py, str(stub), "run"],
                "model": "stub-notrain-model",
                "trains_on_inputs": False,
                "billing": "free",
            },
            "stub-nobilling": {
                "type": "cli",
                "command": [py, str(stub), "run", "--out", "{output_file}"],
                "model": "stub-nobilling-model",
            },
        },
    }
    config["sensitive_paths"] = [str(home / "pa-files")]
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

    # E15 — doctor: billing dichiarato mostrato, UNDECLARED è un problema.
    r = run(home, proj, ["--doctor"])
    check("E15 doctor reports billing class per provider",
          "billing: free" in r.stdout and "billing: PAID" in r.stdout
          and "$9.99" in r.stdout, r.stdout + r.stderr)
    check("E16 doctor flags undeclared billing as a problem (exit 1)",
          r.returncode == 1 and "billing UNDECLARED" in r.stdout
          and "fail-closed" in r.stdout, r.stdout + r.stderr)

    # E17 — {prompt}: argomento, env, cwd isolata.
    (proj / "secret.txt").write_text("x")
    r = run(home, proj, ["--spec", "hi {model}", "--provider", "stub-arg"],
            mode="probe")
    try:
        d = json.loads(r.stdout[r.stdout.index("{"):r.stdout.rindex("}") + 1])
    except ValueError:
        d = {}
    check("E17 {prompt} as literal argument, key in env, isolated empty cwd",
          r.returncode == 0 and d.get("stdin") == ""
          and any(a.startswith("-p=") and a.rstrip().endswith("hi {model}")
                  for a in d.get("argv", []))
          and d.get("key") == "k-123" and d.get("files") == []
          and Path(d.get("cwd", str(proj))) != proj
          and not Path(d.get("cwd", str(proj))).exists(),
          r.stdout + r.stderr)

    # E18 — regressione: spec su stdin, nessuna chiave iniettata.
    r = run(home, proj, ["--spec", "hi"], mode="probe")
    check("E18 without {prompt} the spec stays on stdin, no key injected",
          r.returncode == 0 and '"stdin": ' in r.stdout
          and "hi" in r.stdout.split('"stdin": ', 1)[1]
          and '"key": null' in r.stdout, r.stdout + r.stderr)

    # S — input sensibili ("stub" non dichiara trains_on_inputs: fail-closed).
    theme = proj / "wp-content" / "themes" / "t"
    theme.mkdir(parents=True)
    (theme / "functions.php").write_text("<?php add_action('init', 'x');")
    wpc = proj / "wp-config.php"
    wpc.write_text("<?php define( 'DB_PASSWORD', 'Hunter2!pw' );")
    (proj / "db.sql.gz").write_text("x")
    (proj / "orders.csv").write_text("email,total\na@b.example,3")
    (proj / ".env").write_text("API_TOKEN=abcdef123456")
    (proj / ".env.example").write_text("API_TOKEN=changeme")
    pa = home / "pa-files"
    pa.mkdir()
    (pa / "pratica.txt").write_text("x")

    def sent(r):
        return r.stdout.split('"stdin": ', 1)[1] if '"stdin": ' in r.stdout else ""

    r = run(home, proj, ["--spec", "hi", "--input", str(theme / "functions.php")],
            mode="probe")
    check("S1 client WordPress theme file is allowed",
          r.returncode == 0 and "add_action" in sent(r), r.stdout + r.stderr)
    for tag, f in (("S2 wp-config.php", wpc), ("S3 database dump", proj / "db.sql.gz"),
                   ("S4 tabular export", proj / "orders.csv"),
                   ("S5 sensitive_paths folder", pa / "pratica.txt")):
        r = run(home, proj, ["--spec", "hi", "--input", str(f)], mode="probe")
        check(f"{tag} refused without --allow-sensitive",
              r.returncode == 1 and field(r.stdout, "CHECK") == "sensitive-refused"
              and "--allow-sensitive" in r.stdout and '"stdin"' not in r.stdout,
              r.stdout + r.stderr)
    r = run(home, proj, ["--spec-file", str(proj / ".env")], mode="probe")
    check("S6 --spec-file .env refused",
          r.returncode == 1 and field(r.stdout, "CHECK") == "sensitive-refused",
          r.stdout + r.stderr)
    r = run(home, proj, ["--spec", "hi", "--input", str(proj / ".env.example")],
            mode="probe")
    check("S7 .env.example template allowed",
          r.returncode == 0 and "API_TOKEN" in sent(r), r.stdout + r.stderr)
    r = run(home, proj, ["--spec", "hi", "--input", str(wpc),
                         "--allow-sensitive", "asked for the DB migration review"],
            mode="probe")
    db = home / ".claude" / "fable-director" / "telemetry.db"
    rows = []
    if db.exists():
        con = sqlite3.connect(db)
        rows = [json.loads(p) for (p,) in con.execute(
            "SELECT payload FROM events WHERE event='sensitive_override'")]
        con.close()
    check("S8 --allow-sensitive sends, logs reason and file, secret masked",
          r.returncode == 0 and "DB_PASSWORD" in sent(r)
          and "[SECRET]" in sent(r) and "Hunter2!pw" not in r.stdout
          and any(x.get("reason") == "asked for the DB migration review"
                  and str(wpc) in x.get("files", []) for x in rows),
          r.stdout + r.stderr + str(rows))
    r = run(home, proj, ["--spec", "hi", "--input", str(wpc),
                         "--allow-sensitive", " "], mode="probe")
    check("S9 --allow-sensitive without a reason is an error",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error",
          r.stdout + r.stderr)
    key = "AIza" + "B" * 35
    r = run(home, proj, ["--spec", f"check key {key} please"], mode="probe")
    check("S10 inline spec: secrets masked, text allowed",
          r.returncode == 0 and key not in r.stdout and "[SECRET]" in sent(r)
          and "please" in sent(r), r.stdout + r.stderr)
    r = run(home, proj, ["--spec", f"key {key}", "--input", str(wpc),
                         "--provider", "stub-notrain"], mode="probe")
    check("S11 provider with trains_on_inputs false: no block, no masking",
          r.returncode == 0 and key in sent(r) and "Hunter2!pw" in sent(r),
          r.stdout + r.stderr)

    # W1-W4 — working tree intorno ai CLI esterni (1.49)
    gproj = Path(tempfile.mkdtemp(prefix="fd-xexec-git-"))
    subprocess.run(["git", "init", "-q"], cwd=gproj, check=True)
    (gproj / "gia-qui.txt").write_text("untracked prima della chiamata")
    (home / ".claude" / "fable-director" / "budgets" / f"{slug(gproj)}.json").write_text(
        json.dumps({"status": "open",
                    "declared_at": datetime.now(timezone.utc).isoformat()}))
    r = run(home, gproj, ["--provider", "stub-plain", "--spec", "fai x"], mode="write")
    check("W1 CLI writes into the repo -> error worktree-write, exit 1",
          r.returncode == 1 and field(r.stdout, "STATUS") == "error"
          and field(r.stdout, "CHECK") == "worktree-write"
          and "intruso.txt" in field(r.stdout, "DETAIL"), r.stdout + r.stderr)
    (gproj / "intruso.txt").unlink()
    r = run(home, gproj, ["--provider", "stub-plain", "--spec", "fai x"])
    check("W2 CLI that does not write, untracked files present -> ok",
          field(r.stdout, "STATUS") == "ok", r.stdout + r.stderr)
    r = run(home, gproj, ["--provider", "stub-arg", "--spec", "fai x"], mode="write")
    check("W3 isolated_cwd CLI writing in its own folder -> ok",
          field(r.stdout, "STATUS") == "ok" and not (gproj / "intruso.txt").exists(),
          r.stdout + r.stderr)
    r = run(home, proj, ["--provider", "stub-plain", "--spec", "fai x"], mode="write")
    check("W4 outside a git repo -> no check, ok",
          field(r.stdout, "STATUS") == "ok", r.stdout + r.stderr)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
