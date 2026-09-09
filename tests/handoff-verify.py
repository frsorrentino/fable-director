#!/usr/bin/env python3
"""Handoff 1.43.0 — the planned expiry made operational, deterministically.

Real scripts against a throwaway HOME; no model, no network.

  H1  --boundary: budget closed ok + context 70% → marker (band 60), once per band; 85% → band 80
  H2  --boundary: open budget → never a marker
  H3  --boundary: git commit in this turn's transcript, no budget at all → marker "commit"
  H4  --prompt: marker + real prompt → one [fd-handoff] line, marker consumed;
      slash command / one-word reply → nothing, marker kept
  H5  --prompt: marker older than 30 min → nothing, marker removed
  H6  --resume-line: newest handoff for the cwd (< 14 days) → one short line; none → silence
  H7  SessionStart with a handoff present still carries the onboarding block (cap respected)
  H8  --prepare: refused with an open budget; PATH in the store, or in docs/ with --here
  H9  --written: event logged, size printed; report shows the Handoff block
  H10 statusline says /fable-director:handoff at 85%

Usage: python3 tests/handoff-verify.py   (exit 0 = all green)
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fable-director"
SCRIPTS = ROOT / "scripts"
HANDOFF = str(SCRIPTS / "handoff.py")
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence[:700]}"))


def slug(cwd):
    s = str(cwd).replace("\\", "/")
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])


def run(args, home, stdin=None, cwd=None, env_extra=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_PLUGIN_ROOT=str(ROOT))
    env.pop("CLAUDE_SESSION_ID", None)
    env.update(env_extra or {})
    return subprocess.run(args, capture_output=True, text=True, env=env, cwd=cwd,
                          input=stdin, timeout=60, encoding="utf-8", errors="replace")


def fd(home):
    d = home / ".claude" / "fable-director"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot(home, sid, pct):
    (fd(home) / "sessions").mkdir(exist_ok=True)
    (fd(home) / "sessions" / f"{sid}.json").write_text(json.dumps(
        {"session_id": sid, "ctx_tokens": int(pct * 10000), "ctx_size": 1_000_000}))


def budget(home, cwd, status, outcome=None, closed_ago_s=0):
    (fd(home) / "budgets").mkdir(exist_ok=True)
    b = {"status": status, "task": "t", "declared_at": datetime.now(timezone.utc).isoformat(),
         "expected_output_tokens": 1000, "cwd": str(cwd)}
    if status == "closed":
        b["outcome"] = outcome
        b["closed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=closed_ago_s)).isoformat()
    (fd(home) / "budgets" / f"{slug(cwd)}.json").write_text(json.dumps(b))


def payload(sid, cwd, **kw):
    d = {"session_id": sid, "cwd": str(cwd), "hook_event_name": "Stop"}
    d.update(kw)
    return json.dumps(d)


def marker(home, sid):
    return fd(home) / "handoff-due" / f"{sid}.json"


def main():
    work = Path(tempfile.mkdtemp(prefix="fd-handoff-"))
    proj = work / "proj"
    proj.mkdir()

    # H1 — closed budget, 70% → band 60 once; then 85% → band 80.
    h1 = work / "h1"
    snapshot(h1, "s1", 70)
    budget(h1, proj, "closed", "ok")
    r = run([sys.executable, HANDOFF, "--boundary"], h1, stdin=payload("s1", proj))
    m1 = json.loads(marker(h1, "s1").read_text()) if marker(h1, "s1").is_file() else None
    marker(h1, "s1").unlink(missing_ok=True)
    run([sys.executable, HANDOFF, "--boundary"], h1, stdin=payload("s1", proj))
    again = marker(h1, "s1").is_file()
    snapshot(h1, "s1", 85)
    run([sys.executable, HANDOFF, "--boundary"], h1, stdin=payload("s1", proj))
    m2 = json.loads(marker(h1, "s1").read_text()) if marker(h1, "s1").is_file() else None
    check("H1 boundary: budget-close + 70% → band 60 once; 85% → band 80",
          m1 and m1["band"] == 60 and m1["boundary"] == "budget-close" and m1["pct"] == 70.0
          and not again and m2 and m2["band"] == 80,
          f"m1={m1} again={again} m2={m2} {r.stdout}{r.stderr}")

    # H2 — open budget → nothing, whatever the context.
    h2 = work / "h2"
    snapshot(h2, "s2", 95)
    budget(h2, proj, "open")
    run([sys.executable, HANDOFF, "--boundary"], h2, stdin=payload("s2", proj))
    check("H2 boundary: open budget → no marker", not marker(h2, "s2").is_file())

    # H3 — commit in this turn's transcript, no budget file at all.
    h3 = work / "h3"
    snapshot(h3, "s3", 65)
    tr = work / "t3.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "release it"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "git commit -q -m x"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": "[main 8cb5e09] release: 1.42.0\n 3 files changed"}]}},
    ]
    tr.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
    run([sys.executable, HANDOFF, "--boundary"], h3, stdin=payload("s3", proj, transcript_path=str(tr)))
    m3 = json.loads(marker(h3, "s3").read_text()) if marker(h3, "s3").is_file() else None
    # same transcript, but a NEW user turn after the commit → not this turn
    tr.write_text(tr.read_text() + json.dumps(
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "next"}]}}) + "\n")
    h3b = work / "h3b"
    snapshot(h3b, "s3", 65)
    run([sys.executable, HANDOFF, "--boundary"], h3b, stdin=payload("s3", proj, transcript_path=str(tr)))
    check("H3 boundary: commit in THIS turn → marker 'commit'; commit in a previous turn → nothing",
          m3 and m3["boundary"] == "commit" and not marker(h3b, "s3").is_file(), f"m3={m3}")

    # H4 — prompt: slash / short keep the marker; a real prompt consumes it, one line.
    h4 = work / "h4"
    (fd(h4) / "handoff-due").mkdir()
    marker(h4, "s4").write_text(json.dumps({"pct": 71.0, "band": 60, "boundary": "budget-close",
                                            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}))
    r_slash = run([sys.executable, HANDOFF, "--prompt"], h4, stdin=json.dumps({"session_id": "s4", "prompt": "/fable-director:status"}))
    kept1 = marker(h4, "s4").is_file()
    r_short = run([sys.executable, HANDOFF, "--prompt"], h4, stdin=json.dumps({"session_id": "s4", "prompt": "ok grazie"}))
    kept2 = marker(h4, "s4").is_file()
    r_real = run([sys.executable, HANDOFF, "--prompt"], h4, stdin=json.dumps({"session_id": "s4", "prompt": "adesso rifacciamo la home page del cliente"}))
    gone = not marker(h4, "s4").is_file()
    r_again = run([sys.executable, HANDOFF, "--prompt"], h4, stdin=json.dumps({"session_id": "s4", "prompt": "adesso rifacciamo la home page del cliente"}))
    check("H4 prompt: slash/short keep the marker; real prompt → one [fd-handoff] line, consumed",
          r_slash.stdout == "" and kept1 and r_short.stdout == "" and kept2
          and r_real.stdout.startswith("[fd-handoff] context 71.0%") and "ask the user in ONE line" in r_real.stdout
          and r_real.stdout.count("\n") == 1 and gone and r_again.stdout == "",
          r_slash.stdout + r_short.stdout + r_real.stdout + r_again.stdout)

    # H5 — stale marker (40 min) → nothing, removed.
    h5 = work / "h5"
    (fd(h5) / "handoff-due").mkdir()
    old = (datetime.now(timezone.utc) - timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker(h5, "s5").write_text(json.dumps({"pct": 71.0, "band": 60, "boundary": "verify", "ts": old}))
    r = run([sys.executable, HANDOFF, "--prompt"], h5, stdin=json.dumps({"session_id": "s5", "prompt": "un prompt vero e proprio"}))
    check("H5 prompt: marker older than 30 min → silence, marker removed",
          r.stdout == "" and not marker(h5, "s5").is_file(), r.stdout)

    # H6 — resume line: none → silence; store file today → one short line; docs/ newer wins.
    h6 = work / "h6"
    fd(h6)
    r0 = run([sys.executable, HANDOFF, "--resume-line"], h6, stdin=json.dumps({"cwd": str(proj)}))
    today = time.strftime("%Y-%m-%d")
    store = fd(h6) / "handoffs" / slug(proj)
    store.mkdir(parents=True)
    (store / f"{today}.md").write_text("# handoff\n")
    r1 = run([sys.executable, HANDOFF, "--resume-line"], h6, stdin=json.dumps({"cwd": str(proj)}))
    old_day = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    (store / f"{today}.md").rename(store / f"{old_day}.md")
    r2 = run([sys.executable, HANDOFF, "--resume-line"], h6, stdin=json.dumps({"cwd": str(proj)}))
    (proj / "docs").mkdir(exist_ok=True)
    (proj / "docs" / f"handoff-{today}.md").write_text("# handoff here\n")
    r3 = run([sys.executable, HANDOFF, "--resume-line"], h6, stdin=json.dumps({"cwd": str(proj)}))
    check("H6 resume-line: silence / one line ≤ 160 chars with ~ path / 20-day-old ignored / docs/ relative",
          r0.stdout == "" and r1.stdout.startswith(f"Handoff {today}: ~/.claude/fable-director/handoffs/")
          and r1.stdout.count("\n") == 1 and len(r1.stdout.strip()) <= 160
          and r2.stdout == "" and r3.stdout.strip() == f"Handoff {today}: docs/handoff-{today}.md — read it before starting.",
          r0.stdout + r1.stdout + r2.stdout + r3.stdout)

    # H7 — SessionStart with the handoff line still fits the onboarding block.
    h7 = work / "h7"
    fd(h7)
    st7 = fd(h7) / "handoffs" / slug(proj)
    st7.mkdir(parents=True)
    (st7 / f"{today}.md").write_text("# handoff\n")
    r = run(["bash", str(SCRIPTS / "session-kernel.sh")], h7,
            stdin=json.dumps({"source": "startup", "cwd": str(proj), "session_id": "s7"}))
    check("H7 SessionStart: handoff line present, onboarding block still present, under the cap",
          f"Handoff {today}:" in r.stdout and "XF ONBOARDING" in r.stdout
          and len(r.stdout) <= 7900 and "[fd: SessionStart output cut" not in r.stdout,
          f"len={len(r.stdout)} " + r.stdout[-600:])

    # H8 — prepare: refused with open budget; store path; --here path.
    h8 = work / "h8"
    fd(h8)
    budget(h8, proj, "open")
    r_ref = run([sys.executable, HANDOFF, "--prepare"], h8, cwd=proj)
    budget(h8, proj, "closed", "ok")
    r_ok = run([sys.executable, HANDOFF, "--prepare"], h8, cwd=proj)
    r_here = run([sys.executable, HANDOFF, "--prepare", "--here"], h8, cwd=proj)
    p_ok = re.search(r"^PATH: (.*)$", r_ok.stdout, re.M)
    p_here = re.search(r"^PATH: (.*)$", r_here.stdout, re.M)
    check("H8 prepare: refused (exit 1) with open budget; PATH in store / in docs/ with --here; 7 sections",
          r_ref.returncode == 1 and "STATUS: refused" in r_ref.stdout and "budget is open" in r_ref.stdout
          and r_ok.returncode == 0 and p_ok and p_ok.group(1).startswith(str(fd(h8) / "handoffs" / slug(proj)))
          and p_here and p_here.group(1) == str(proj / "docs" / f"handoff-{today}.md")
          and r_ok.stdout.count("\n  ") == 7 and "THEN: python3" in r_ok.stdout,
          r_ref.stdout + r_ok.stdout + r_here.stdout + r_ok.stderr)

    # H9 — written: event logged; report prints the Handoff block.
    dest = Path(p_ok.group(1))
    dest.write_text("# handoff\n\n## 1. Task and outcome\n- done\n")
    r_w = run([sys.executable, HANDOFF, "--written", str(dest)], h8, cwd=proj)
    run([sys.executable, HANDOFF, "--boundary"], h8, stdin=payload("s8", proj)) if False else None
    tele = str(SCRIPTS / "fd-telemetry.py")
    code = ("import importlib.util,sys; sp=importlib.util.spec_from_file_location('t', sys.argv[1]);"
            "m=importlib.util.module_from_spec(sp); sp.loader.exec_module(m);"
            "m.log_event('handoff_proposed', {'pct': 71.0, 'band': 60, 'boundary': 'budget-close'})")
    run([sys.executable, "-c", code, tele], h8, cwd=proj)
    r_rep = run([sys.executable, tele, "report", "--days", "1"], h8, cwd=proj)
    check("H9 written: STATUS ok with size; report: 'Handoff: 1 proposed (bands 60%: 1), 1 written'",
          r_w.returncode == 0 and "STATUS: ok" in r_w.stdout and "bytes" in r_w.stdout
          and "Handoff: 1 proposed at a verified boundary (bands 60%: 1), 1 written" in r_rep.stdout
          and "not yet" in r_rep.stdout,
          r_w.stdout + r_w.stderr + r_rep.stdout[-800:] + r_rep.stderr[-300:])

    # H10 — statusline text (the plain suite covers the rendering; here the string).
    src = (SCRIPTS / "statusline-plain.py").read_text()
    check("H10 statusline: 'context N% full — /fable-director:handoff, then a new session'",
          "full — /fable-director:handoff, then a new session" in src
          and "finish the task and start a new session" not in src)

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
