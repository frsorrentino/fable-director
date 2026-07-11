#!/usr/bin/env python3
"""One-command Windows verification of the fable-director enforcement layer.

Runs the REAL scripts (no re-implementation) against a throwaway HOME and
checks every Windows-specific behavior fixed in 1.12.5 and 1.15.1:

  A. UTF-8 output — deny/warn messages containing ✕ ⚠ × ≥ must not crash
     under a cp1252 console (issue #1 bug 1: crash was swallowed by
     fail-open = silent allow).
  B. cwd_slug — backslash paths and drive colons produce a legal, stable
     filename, identical between telemetry and gate (issue #1 bug 2).
  C. Budget roundtrip — open → gate denies without budget / allows with →
     close; state files land where every consumer finds them.
  D. Perimeter — never_write denies; cross-drive targets (relpath raises
     across drives) must STILL enforce never_write (1.15.1 fix); fnmatch
     case-insensitivity on Windows is reported, not asserted.
  E. external-exec — refuses without budget, refuses stale budget, both
     with readable output.

Usage (from the repo root, on the Windows machine, any console):
    python tests\\windows-verify.py
Requires: Python 3.8+, this repo. No Claude Code, no network, no API keys.
Exit 0 = all green. Any FAIL prints the evidence.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
IS_WIN = os.name == "nt"

passed = []
failed = []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {name}" + ("" if ok else f" — {evidence[:200]}"))


def run(script, args=None, stdin=None, env=None, cp1252=False):
    """Run a real script. cp1252=True forces the legacy Windows console
    encoding on the child (PYTHONIOENCODING) to reproduce issue #1."""
    e = dict(os.environ)
    if env:
        e.update(env)
    if cp1252:
        e["PYTHONIOENCODING"] = "cp1252"
        e.pop("PYTHONUTF8", None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)] + (args or []),
        input=(stdin.encode() if isinstance(stdin, str) else stdin),
        capture_output=True, env=e, timeout=60)


def main():
    home = Path(tempfile.mkdtemp(prefix="fd-winverify-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-winverify-proj-"))
    env = {"HOME": str(home), "USERPROFILE": str(home)}
    print(f"platform: {sys.platform} · python {sys.version.split()[0]} · "
          f"throwaway HOME: {home}\n")

    # B — cwd_slug stability across separators (the gate receives '/'
    # while telemetry sees '\\' on Windows: both must hit the same file).
    sys.path.insert(0, str(SCRIPTS))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fdt", SCRIPTS / "fd-telemetry.py")
    fdt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fdt)
    p_back = str(proj).replace("/", "\\")
    p_fwd = str(proj).replace("\\", "/")
    slug_a, slug_b = fdt.cwd_slug(p_back), fdt.cwd_slug(p_fwd)
    check("B1 cwd_slug backslash == forward-slash", slug_a == slug_b,
          f"{slug_a} != {slug_b}")
    bad = set(':\\/<>"|?*') & set(slug_a)
    check("B2 slug is a legal filename", not bad, f"illegal chars: {bad}")

    # C — budget roundtrip through the real CLI.
    r = run("fd-telemetry.py", ["budget-open", "--task", "win verify",
                                "--expected-output", "500",
                                "--cwd", str(proj)], env=env)
    check("C1 budget-open succeeds", r.returncode == 0,
          r.stderr.decode(errors="replace"))
    bfile = home / ".claude" / "fable-director" / "budgets" / f"{slug_a}.json"
    check("C2 budget file exists where consumers look", bfile.is_file(),
          str(bfile))

    # A + C — gate: allow with budget, deny without, under cp1252.
    gate_in = json.dumps({"cwd": str(proj), "tool_name": "Agent",
                          "tool_input": {}})
    r = run("pre-delegation-gate.py", stdin=gate_in, env=env, cp1252=True)
    check("C3 gate allows with open budget (cp1252 console)",
          r.returncode == 0 and b'"deny"' not in r.stdout,
          r.stdout.decode(errors="replace"))
    gate_nb = json.dumps({"cwd": str(proj) + "-nobudget",
                          "tool_name": "Agent", "tool_input": {}})
    r = run("pre-delegation-gate.py", stdin=gate_nb, env=env, cp1252=True)
    deny = b'"deny"' in r.stdout
    check("A1 gate DENIES without budget under cp1252 (no silent allow)",
          deny and r.returncode == 0, r.stdout.decode(errors="replace")
          or r.stderr.decode(errors="replace"))

    # D — perimeter: never_write deny (cp1252 too), then cross-drive.
    fdbase = home / ".claude" / "fable-director"
    fdbase.mkdir(parents=True, exist_ok=True)
    (fdbase / "perimeter.json").write_text(
        json.dumps({"never_write": ["*.env"]}))
    peri_in = json.dumps({"cwd": str(proj), "tool_name": "Write",
                          "tool_input": {"file_path": str(proj) + os.sep + "x.env"}})
    r = run("perimeter-gate.py", stdin=peri_in, env=env, cp1252=True)
    check("D1 never_write denies under cp1252", b'"deny"' in r.stdout,
          r.stdout.decode(errors="replace"))
    if IS_WIN:
        cur_drive = os.path.splitdrive(str(proj))[0]  # e.g. 'C:'
        other = next((f"{d}:" for d in "DEFGH"
                      if f"{d}:" != cur_drive and Path(f"{d}:\\").exists()),
                     None)
        if other:
            cross = json.dumps({"cwd": str(proj), "tool_name": "Write",
                                "tool_input": {"file_path": other + "\\t.env"}})
            r = run("perimeter-gate.py", stdin=cross, env=env)
            check("D2 cross-drive target STILL hits never_write (1.15.1)",
                  b'"deny"' in r.stdout, r.stdout.decode(errors="replace"))
        else:
            print("[SKIP] D2 cross-drive: no second drive on this machine")
        import fnmatch
        print(f"[INFO] D3 fnmatch case-insensitive here: "
              f"{fnmatch.fnmatch('A.ENV', '*.env')} (Windows: expected True "
              f"— write never_write patterns in the real case)")
    else:
        print("[SKIP] D2/D3: cross-drive and case checks are Windows-only")

    # E — external-exec refusals, readable under cp1252.
    r = run("external-exec.py", ["--spec", "x"],
            env={**env, "HOME": str(home)}, cp1252=True)
    ok_msg = b"no open pre-budget" in r.stdout
    check("E1 external-exec refuses without budget (readable, cp1252)",
          ok_msg, r.stdout.decode(errors="replace"))
    # stale budget (>24h)
    b = json.loads(bfile.read_text())
    b["declared_at"] = (datetime.now(timezone.utc)
                        - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bfile.write_text(json.dumps(b))
    r = run("external-exec.py", ["--spec", "x"], env=env)
    # NB: external-exec usa il SUO cwd — qui gira dal repo, quindi il budget
    # del proj non lo vede: testiamo dal proj via cwd del sottoprocesso.
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "external-exec.py"), "--spec", "x"],
        capture_output=True, cwd=str(proj),
        env={**os.environ, **env}, timeout=60)
    check("E2 external-exec refuses stale (>24h) budget",
          b"older than 24h" in r.stdout, r.stdout.decode(errors="replace"))

    # C — close cleanly (restore a valid declared_at first).
    b["declared_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bfile.write_text(json.dumps(b))
    r = run("fd-telemetry.py", ["budget-close", "--outcome", "ok",
                                "--cwd", str(proj)], env=env)
    check("C4 budget-close succeeds", r.returncode == 0,
          r.stderr.decode(errors="replace"))

    print(f"\n{len(passed)} passed, {len(failed)} failed"
          + ("" if IS_WIN else "  (run this on the WINDOWS machine for the "
             "checks that matter — POSIX run only proves the harness)"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
