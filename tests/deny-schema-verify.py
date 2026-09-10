#!/usr/bin/env python3
"""Deny messages follow one schema (Claude Code 2.1.268 auto-mode denials):
the rule that blocked the call is named first, the body says the safer route,
the tail asks to finish unrelated work before stopping — and the text stays
under the harness cap (2000 chars / 20 lines).

  D1  pre-delegation-gate, Agent with no open budget -> [fable-director rule: no_budget] + tail
  D2  perimeter-gate, Write on a never_write path   -> [fable-director rule: never_write] + tail
  D3  perimeter-gate, Bash matching deny_git         -> [fable-director rule: deny_git] + tail
  D4  every reason <= 2000 chars and <= 20 lines

Usage: python3 tests/deny-schema-verify.py   (exit 0 = all green)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
S = HERE.parent / "fable-director" / "scripts"
TAIL = "finish the work that does not depend on this call before stopping to ask the user."
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(script, home, payload):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    r = subprocess.run([sys.executable, str(script)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    h = out.get("hookSpecificOutput") or {}
    return h.get("permissionDecision"), h.get("permissionDecisionReason", ""), r


def main():
    home = Path(tempfile.mkdtemp(prefix="fd-deny-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-deny-proj-"))
    (home / ".claude" / "fable-director" / "budgets").mkdir(parents=True)
    reasons = []

    d, reason, r = run(S / "pre-delegation-gate.py", home, {
        "hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": str(proj),
        "session_id": "deny-test", "tool_input": {"prompt": "do x", "subagent_type": "general-purpose"}})
    reasons.append(reason)
    check("D1 no budget: rule named first and tail present",
          d == "deny" and reason.startswith("[fable-director rule: no_budget]") and TAIL in reason,
          r.stdout + r.stderr)

    (proj / ".fd-perimeter.json").write_text(json.dumps({"never_write": ["*.env", "secrets/**"],
                                                          "deny_git": ["push --force"]}))
    d, reason, r = run(S / "perimeter-gate.py", home, {
        "hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": str(proj),
        "session_id": "deny-test", "tool_input": {"file_path": str(proj / "prod.env"), "content": "x"}})
    reasons.append(reason)
    check("D2 never_write: rule named first and tail present",
          d == "deny" and reason.startswith("[fable-director rule: never_write]") and TAIL in reason,
          r.stdout + r.stderr)

    d, reason, r = run(S / "perimeter-gate.py", home, {
        "hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(proj),
        "session_id": "deny-test", "tool_input": {"command": "git push --force origin main"}})
    reasons.append(reason)
    check("D3 deny_git: rule named first and tail present",
          d == "deny" and reason.startswith("[fable-director rule: deny_git]") and TAIL in reason,
          r.stdout + r.stderr)

    check("D4 every reason under the harness cap (2000 chars, 20 lines)",
          all(len(x) <= 2000 and len(x.splitlines()) <= 20 for x in reasons),
          [(len(x), len(x.splitlines())) for x in reasons])

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
