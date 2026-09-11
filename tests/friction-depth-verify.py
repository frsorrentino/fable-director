#!/usr/bin/env python3
"""Friction proxies and depth-standardized cost per turn (adopted 2026-09-11
from makinggainz/claude-code-measure-efficiency after the competitor review).

Synthetic transcripts in a throwaway project dir, real scripts:
  P1  session-cost-report --since: friction line per period (tool errors,
      corrections on real human turns only, edit churn on the same file)
  P2  depth table: bands filled per period, subagent eq attributed to the
      launching main turn, standardized figure printed
  P3  session-summary payload carries `friction` with the same counts
  P4  injected reminders and tool_result turns are not human turns

Usage: python3 tests/friction-depth-verify.py   (exit 0 = all green)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = HERE.parent / "fable-director" / "skills" / "delega-efficiente" / "tools" / "session-cost-report.py"
FDT = HERE.parent / "fable-director" / "scripts" / "fd-telemetry.py"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def rec(ts, kind, content, usage=None, mid=None, meta=False):
    r = {"timestamp": ts, "type": kind, "message": {"role": kind, "content": content}}
    if usage:
        r["message"]["usage"] = usage
        r["message"]["model"] = "claude-sonnet-5"
        r["message"]["id"] = mid or f"m{ts}"
    if meta:
        r["isMeta"] = True
    return r


def usage(out=100, cr=1000, cc=100):
    return {"input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}


def main():
    home = Path(tempfile.mkdtemp(prefix="fd-fric-home-"))
    proj = Path(tempfile.mkdtemp(prefix="fd-fric-proj-"))
    (home / ".claude" / "fable-director").mkdir(parents=True)
    d = "2026-08-20T10:00:%02dZ"
    before = [
        rec(d % 1, "user", "fai la cosa"),
        rec(d % 2, "assistant", [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/p/a.py"}}], usage()),
        rec(d % 3, "user", [{"type": "tool_result", "is_error": True, "content": "boom"}]),
        rec(d % 4, "assistant", [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/p/a.py"}}], usage()),
        rec(d % 5, "user", "<system-reminder>ignorato</system-reminder>"),
        rec(d % 6, "user", "non funziona, rivedi"),
        rec(d % 7, "assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}], usage()),
        rec(d % 8, "user", [{"type": "tool_result", "is_error": False, "content": "ok"}]),
        rec(d % 9, "user", "grazie", meta=True),
    ]
    a = "2026-09-05T10:00:%02dZ"
    after = [rec(a % 1, "user", "ok"), rec(a % 2, "assistant", [{"type": "text", "text": "x"}], usage(out=50))]
    sub = [rec(a % 3, "assistant", [{"type": "text", "text": "sub"}], usage(out=10, cr=0, cc=5000))]
    (proj / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in before) + "\n")
    (proj / "s2.jsonl").write_text("\n".join(json.dumps(r) for r in after) + "\n")
    (proj / "agent-x1.jsonl").write_text("\n".join(json.dumps(r) for r in sub) + "\n")
    env = dict(os.environ, HOME=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    r = subprocess.run([sys.executable, str(REPORT), "--since", "2026-09-01", str(proj)],
                       capture_output=True, text=True, env=env, timeout=120)
    out = r.stdout
    check("P1 friction before: 1/3 tool errors, 1/2 corrections, churn 1/2",
          "prima di 2026-09-01: tool error rate 33.3% (1/3)   correction rate 50.0% (1/2 turni umani)   edit churn 50.0% (1/2)" in out,
          out + r.stderr)
    check("P1 friction after: no errors, 0/1 corrections", "da 2026-09-01:      tool error rate 0.0% (0/0)   correction rate 0.0% (0/1 turni umani)" in out, out)
    lines = [ln for ln in out.splitlines() if ln.startswith("1-25")]
    check("P2 depth band 1-25 has 3 turns before and 1 after", lines and lines[0].split()[1] == "3" and lines[0].split()[3] == "1", lines)
    check("P2 subagent eq attributed to the after turn (after eq/turn > before eq/turn)",
          lines and int(lines[0].split()[4].replace(".", "")) > int(lines[0].split()[2].replace(".", "")), lines)
    check("P2 standardized figure printed", "standardizzato sul mix di profondità del prima" in out, out)

    r2 = subprocess.run([sys.executable, str(FDT), "session-summary", "--transcript", str(proj / "s1.jsonl"),
                         "--session-id", "s1", "--cwd", str(proj)], capture_output=True, text=True, env=env, timeout=120)
    import sqlite3
    con = sqlite3.connect(home / ".claude" / "fable-director" / "telemetry.db")
    row = con.execute("select payload from events where event='session_summary' order by ts desc limit 1").fetchone()
    fr = (json.loads(row[0]).get("friction") if row else None) or {}
    check("P3 session-summary payload carries friction counts",
          fr.get("tool_calls") == 3 and fr.get("tool_errors") == 1 and fr.get("human_turns") == 2
          and fr.get("corrections") == 1 and fr.get("edit_churn") == 1, (fr, r2.stdout + r2.stderr))
    check("P4 reminders, tool_result and isMeta turns are not human turns", fr.get("human_turns") == 2, fr)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
