#!/usr/bin/env python3
"""Concurrency stress test — hammers the shared-state paths with real
concurrent processes and asserts INVARIANTS, not implementations.

Every real bug found by the adversarial reviews lived in shared state
(budget file vs Stop hook, lease TOCTOU, statusline vs growing transcript,
SQLite under concurrent hooks): races are found by hammering, not by
reading. This runs the REAL scripts/functions — zero model tokens,
CI-friendly (< ~60s).

Invariants:
  T1 a FLAGGED budget is never resurrected to open by concurrent amends
  T2 no telemetry event is silently lost under concurrent writers (WAL)
  T3 concurrent statusline renders converge to the exact token total,
     and a later serial render lands on the exact grown total
  T4 concurrent gate calls without budget ALL deny and ALL get logged
  T5 concurrent budget-opens leave one coherent, parseable budget file

Usage: python3 tests/concurrency-stress.py   (exit 0 = all invariants hold)
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from multiprocessing import Process
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"

passed = []
failed = []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}"
          + ("" if ok else f" — {str(evidence)[:250]}"))


def fresh_home():
    home = Path(tempfile.mkdtemp(prefix="fd-stress-"))
    (home / ".claude" / "fable-director").mkdir(parents=True)
    return home


def env_for(home, sid=None):
    e = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    if sid:
        e["CLAUDE_CODE_SESSION_ID"] = sid
    return e


def cli(home, *args, sid=None, stdin=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "fd-telemetry.py"), *args],
        capture_output=True, env=env_for(home, sid),
        input=(stdin.encode() if stdin else None), timeout=120)


def slug_for(cwd):
    import hashlib
    import re
    s = str(cwd).replace("\\", "/")
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])


# ---------------------------------------------------------------- T1
def t1_amend_never_resurrects_flagged():
    home = fresh_home()
    proj = tempfile.mkdtemp(prefix="fd-t1-")
    cli(home, "budget-open", "--task", "t1", "--expected-output", "100",
        "--paths", "src/*", "--cwd", proj)
    bfile = home / ".claude" / "fable-director" / "budgets" / f"{slug_for(proj)}.json"

    def amender(i):
        for k in range(12):
            cli(home, "budget-amend", "--add-paths", f"w{i}/{k}/*",
                "--reason", "stress", "--cwd", proj)

    def flagger():
        import time
        time.sleep(0.4)  # a metà della gragnuola di amend
        # mimo lo Stop hook: read → flagged → replace atomico
        b = json.loads(bfile.read_text())
        b["status"] = "flagged"
        tmp = bfile.with_name(f"{bfile.name}.flag.tmp")
        tmp.write_text(json.dumps(b))
        os.replace(tmp, bfile)

    procs = [Process(target=amender, args=(i,)) for i in range(4)]
    procs.append(Process(target=flagger))
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    final = json.loads(bfile.read_text())
    check("T1 flagged never resurrected by concurrent amends",
          final.get("status") == "flagged", final.get("status"))


# ---------------------------------------------------------------- T2
def t2_no_lost_telemetry_events():
    home = fresh_home()
    n_procs, n_events = 8, 100

    def writer(i):
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fdt", SCRIPTS / "fd-telemetry.py")
        fdt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fdt)
        for k in range(n_events):
            fdt.log_event("retry", {"class": f"w{i}", "k": k})

    procs = [Process(target=writer, args=(i,)) for i in range(n_procs)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    import sqlite3
    con = sqlite3.connect(home / ".claude" / "fable-director" / "telemetry.db")
    n = con.execute("SELECT COUNT(*) FROM events WHERE event='retry'"
                    ).fetchone()[0]
    con.close()
    check(f"T2 zero lost events ({n_procs}×{n_events} concurrent writers)",
          n == n_procs * n_events, f"{n} != {n_procs * n_events}")


# ---------------------------------------------------------------- T3
def t3_statusline_converges():
    home = fresh_home()
    proj = tempfile.mkdtemp(prefix="fd-t3-")
    cli(home, "budget-open", "--task", "t3", "--expected-output", "10000",
        "--cwd", proj)
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    tp = home / "transcript.jsonl"
    n_lines, tok_each = 60, 10
    rows = [json.dumps({
        "timestamp": (now + timedelta(seconds=5 + i)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "message": {"usage": {"output_tokens": tok_each,
                              "input_tokens": 1}}}) for i in range(n_lines)]
    tp.write_text("\n".join(rows[:40]) + "\n")
    sid = "stress-t3"
    stdin = json.dumps({"cwd": proj, "model": {"display_name": "Fable 5"},
                        "session_id": sid, "transcript_path": str(tp)})

    def render():
        subprocess.run(["bash", str(SCRIPTS / "statusline-ctx.sh")],
                       input=stdin.encode(), capture_output=True,
                       env=env_for(home, sid), timeout=120)

    procs = [Process(target=render) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    sf = home / ".claude" / "fable-director" / "delegations" / f"{sid}.tok.json"
    st = json.loads(sf.read_text())
    check("T3a concurrent renders converge to exact total (40×10)",
          st["budget"]["out"] == 400, st["budget"])
    # crescita + render seriale: totale esatto, niente doppi conteggi
    tp.write_text("\n".join(rows) + "\n")
    render()
    st = json.loads(sf.read_text())
    check("T3b serial render after growth lands exactly (60×10)",
          st["budget"]["out"] == 600, st["budget"])


# ---------------------------------------------------------------- T4
def t4_gate_all_deny_all_logged():
    home = fresh_home()
    proj = tempfile.mkdtemp(prefix="fd-t4-")  # nessun budget
    stdin = json.dumps({"cwd": proj, "tool_name": "Agent", "tool_input": {},
                        "session_id": "stress-t4"})
    results = home / "gate-results"
    results.mkdir()

    def gate(i):
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "pre-delegation-gate.py")],
            input=stdin.encode(), capture_output=True,
            env=env_for(home), timeout=120)
        (results / f"{i}").write_bytes(r.stdout)

    n = 10
    procs = [Process(target=gate, args=(i,)) for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    denies = sum(1 for f in results.iterdir() if b'"deny"' in f.read_bytes())
    check(f"T4a all {n} concurrent gates deny", denies == n, f"{denies}/{n}")
    import sqlite3
    con = sqlite3.connect(home / ".claude" / "fable-director" / "telemetry.db")
    logged = con.execute("SELECT COUNT(*) FROM events WHERE event='gate_deny'"
                         ).fetchone()[0]
    con.close()
    check(f"T4b all {n} denies logged (no lost gate_deny)",
          logged == n, f"{logged}/{n}")


# ---------------------------------------------------------------- T5
def t5_open_race_leaves_coherent_file():
    home = fresh_home()
    proj = tempfile.mkdtemp(prefix="fd-t5-")
    sids = [f"sess-{i}" for i in range(8)]

    def opener(sid):
        cli(home, "budget-open", "--task", f"by-{sid}",
            "--expected-output", "100", "--cwd", proj, sid=sid)

    procs = [Process(target=opener, args=(s,)) for s in sids]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    bfile = home / ".claude" / "fable-director" / "budgets" / f"{slug_for(proj)}.json"
    try:
        b = json.loads(bfile.read_text())
        coherent = (b.get("status") == "open"
                    and b.get("owner_sid") in sids
                    and b.get("task") == f"by-{b['owner_sid']}"
                    and int(b.get("expected_output_tokens")) == 100)
        check("T5 concurrent opens leave one coherent parseable budget",
              coherent, b)
    except Exception as e:
        check("T5 concurrent opens leave one coherent parseable budget",
              False, f"unparseable: {e}")


def main():
    for t in (t1_amend_never_resurrects_flagged, t2_no_lost_telemetry_events,
              t3_statusline_converges, t4_gate_all_deny_all_logged,
              t5_open_race_leaves_coherent_file):
        t()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
