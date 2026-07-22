#!/usr/bin/env python3
"""Guardia billing di cross-verify.py (1.24.0).

HOME usa-e-getta + provider CLI stub che risponde col JSON verdetto:
  C1  provider free → esegue, STATUS ok
  C2  provider paid senza --paid-ok → unavailable, exit 1, mai eseguito
  C3  provider paid con --paid-ok → esegue
  C4  billing assente = paid (fail-closed)

Usage: python3 tests/cross-verify-billing.py   (exit 0 = all green)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "fable-director" / "scripts" / "cross-verify.py"

passed, failed = [], []

STUB = '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
out = args[args.index("--out") + 1] if "--out" in args else None
sys.stdin.read()
text = json.dumps({"verdict": "supported", "reasoning": "stub"})
if out:
    open(out, "w").write(text)
else:
    print(text)
'''


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n      {evidence}"))


def setup():
    home = Path(tempfile.mkdtemp(prefix="fd-xvb-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-xvb-proj-"))
    stub = home / "stub-cli.py"
    stub.write_text(STUB)
    py = sys.executable
    base = {"type": "cli",
            "command": [py, str(stub), "run", "--out", "{output_file}"],
            "model": "stub-v"}
    config = {"default": "vfree", "providers": {
        "vfree": {**base, "billing": "free"},
        "vpaid": {**base, "billing": "paid", "cost_note": "~$0.003/verify"},
        "vnobilling": dict(base),
    }}
    cfg_dir = home / ".claude" / "fable-director"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "cross-family.json").write_text(json.dumps(config))
    return home, proj


def run(home, proj, args):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--claim", "x"] + args,
        capture_output=True, env=env, cwd=proj, timeout=60,
        encoding="utf-8", errors="replace")


def field(stdout, key):
    m = re.search(rf"^{key}: (.*)$", stdout, re.MULTILINE)
    return m.group(1) if m else ""


def main():
    home, proj = setup()

    r = run(home, proj, ["--provider", "vfree"])
    check("C1 free provider verifies",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)

    r = run(home, proj, ["--provider", "vpaid"])
    check("C2 paid provider without --paid-ok is refused",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout and "--paid-ok" in r.stdout
          and "$0.003" in r.stdout, r.stdout + r.stderr)

    r = run(home, proj, ["--provider", "vpaid", "--paid-ok"])
    check("C3 paid provider with --paid-ok verifies",
          r.returncode == 0 and field(r.stdout, "STATUS") == "ok",
          r.stdout + r.stderr)

    r = run(home, proj, ["--provider", "vnobilling"])
    check("C4 missing billing field is fail-closed",
          r.returncode == 1 and field(r.stdout, "STATUS") == "unavailable"
          and "is billed" in r.stdout, r.stdout + r.stderr)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
