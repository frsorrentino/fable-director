#!/usr/bin/env python3
"""Budget writers against each other, deterministic: no repeated runs, no luck.

Each race is forced by running the other writer exactly inside the window, with
the real Stop hook and the real CLI.

  R1 Stop hook inside budget-amend: stays flagged, amendment kept, one 3x block
  R2 budget-amend inside the Stop hook (3x): flagged and amendment kept
  R3 budget-amend inside the Stop hook (2x): warned and amendment kept
  R4 budget-close inside the Stop hook: stays closed, no block
  R5 lock held by a stuck process: the Stop hook still blocks, in bounded time
  R6 budget-reserve.py missing: the Stop hook still blocks, budget-amend works
  R7 no lock primitive (no fcntl, no msvcrt): locked_update still writes

Usage: python3 tests/budget-amend-race-verify.py   (exit 0 = all hold)
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
TEL = SCRIPTS / "fd-telemetry.py"
HOOK = SCRIPTS / "stop-budget-check.py"
RESERVE = SCRIPTS / "budget-reserve.py"
BLOCK = "3× block"
# How long the amend stays inside its window waiting for the Stop hook. Without
# a lock the hook finishes well before; with one it is still waiting, whichever
# is slower only moves the hook after the amend: never a false red.
WINDOW_S = 1.5

passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + ("" if ok else f" — {str(evidence)[:300]}"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def budget_path(home, proj):
    s = str(proj).replace("\\", "/")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-") + "-" + hashlib.sha256(s.encode()).hexdigest()[:8]
    return Path(home) / ".claude" / "fable-director" / "budgets" / f"{slug}.json"


def read_budget(sc):
    return json.loads(budget_path(sc["home"], sc["proj"]).read_text())


def scenario(expected=100, tel=TEL):
    """Open budget and a transcript with 1000 output tokens spent after it."""
    home = tempfile.mkdtemp(prefix="fd-race-")
    (Path(home) / ".claude" / "fable-director").mkdir(parents=True)
    proj = Path(tempfile.mkdtemp(prefix="fd-race-p-"))
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    subprocess.run([sys.executable, str(tel), "budget-open", "--task", "t", "--expected-output",
                    str(expected), "--paths", "src/*", "--cwd", str(proj)],
                   env=env, capture_output=True, check=True, timeout=120)
    time.sleep(1.1)  # declared_at has one-second resolution
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    tr = proj / "t.jsonl"
    tr.write_text(json.dumps({"type": "assistant", "timestamp": ts, "sessionId": "s1", "requestId": "r1",
                              "message": {"id": "m1", "role": "assistant", "model": "claude-opus-5-5",
                                          "content": [{"type": "text", "text": "x"}],
                                          "usage": {"input_tokens": 10, "output_tokens": 1000}}}) + "\n")
    return {"home": home, "proj": proj, "env": env, "tr": tr}


def stop_payload(sc):
    return json.dumps({"cwd": str(sc["proj"]), "transcript_path": str(sc["tr"]),
                       "session_id": "s1", "hook_event_name": "Stop"})


def stop(sc, hook=HOOK):
    return subprocess.run([sys.executable, str(hook)], input=stop_payload(sc), capture_output=True,
                          text=True, encoding="utf-8", env=sc["env"], timeout=120)


def tel(sc, *args):
    return subprocess.run([sys.executable, str(TEL), *args, "--cwd", str(sc["proj"])],
                          capture_output=True, text=True, encoding="utf-8", env=sc["env"], timeout=120)


def rerun(sc, *args):
    return subprocess.run([sys.executable, __file__, *args], capture_output=True, text=True,
                          encoding="utf-8", env=sc["env"], timeout=120)


# ---- child processes: the real code with the other writer inside its window
def child_amend_with_stop_inside(proj, tr):
    m = load(TEL, "fdt")
    real, done = m.write_json_atomic, []

    def write(path, obj):
        if not done:
            done.append(1)
            sc = {"proj": proj, "tr": tr}
            p = subprocess.Popen([sys.executable, str(HOOK)], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=dict(os.environ))
            p.stdin.write(stop_payload(sc).encode())
            p.stdin.close()
            end = time.monotonic() + WINDOW_S
            while p.poll() is None and time.monotonic() < end:
                time.sleep(0.01)
            done.append(p)
        real(path, obj)

    m.write_json_atomic = write
    m.cmd_budget_amend(["--add-paths", "x/*", "--reason", "race", "--cwd", proj])
    p = done[1]
    out = p.stdout.read().decode("utf-8", "replace")
    p.wait(timeout=60)
    print("HOOK:" + ("block" if BLOCK in out else "pass"))


def child_stop_with_cli_inside(proj, tr, *cli):
    m = load(HOOK, "sbc")
    real = m.sum_session_incremental

    def scan(*a, **k):
        r = real(*a, **k)
        subprocess.run([sys.executable, str(TEL), *cli, "--cwd", proj], capture_output=True, env=dict(os.environ), timeout=120)
        return r

    m.sum_session_incremental = scan
    sys.stdin = io.StringIO(json.dumps({"cwd": proj, "transcript_path": tr, "session_id": "s1", "hook_event_name": "Stop"}))
    m.main()


def child_hold_lock(bfile):
    rm = load(RESERVE, "rm")

    def forever(_budget):
        print("held", flush=True)
        time.sleep(60)
        return None, False

    rm.locked_update(bfile, forever)


# ---- cases
def r1():
    sc = scenario()
    r = rerun(sc, "child-amend", str(sc["proj"]), str(sc["tr"]))
    b = read_budget(sc)
    check("R1 Stop inside amend: stays flagged", b.get("status") == "flagged", (b.get("status"), r.stdout[-200:], r.stderr[-300:]))
    check("R1 Stop inside amend: amendment kept", "x/*" in (b.get("paths") or []), b.get("paths"))
    check("R1 Stop inside amend: that Stop blocks", "HOOK:block" in r.stdout, r.stdout[-200:])
    again = stop(sc)
    check("R1 next Stop does not block a second time", BLOCK not in again.stdout, again.stdout[:200])


def r2():
    sc = scenario()
    r = rerun(sc, "child-stop", str(sc["proj"]), str(sc["tr"]), "budget-amend", "--add-paths", "x/*", "--reason", "race")
    b = read_budget(sc)
    check("R2 amend inside Stop 3x: blocks and flags", BLOCK in r.stdout and b.get("status") == "flagged", (b.get("status"), r.stderr[-300:]))
    check("R2 amend inside Stop 3x: amendment kept", "x/*" in (b.get("paths") or []), b.get("paths"))


def r3():
    sc = scenario(expected=400)
    r = rerun(sc, "child-stop", str(sc["proj"]), str(sc["tr"]), "budget-amend", "--add-paths", "x/*", "--reason", "race")
    b = read_budget(sc)
    check("R3 amend inside Stop 2x: warned", b.get("warned") is True and b.get("status") == "open", (b.get("warned"), b.get("status"), r.stderr[-300:]))
    check("R3 amend inside Stop 2x: amendment kept", "x/*" in (b.get("paths") or []), b.get("paths"))


def r4():
    sc = scenario()
    r = rerun(sc, "child-stop", str(sc["proj"]), str(sc["tr"]), "budget-close", "--outcome", "abandoned")
    b = read_budget(sc)
    check("R4 close inside Stop: stays closed", b.get("status") == "closed", (b.get("status"), r.stderr[-300:]))
    check("R4 close inside Stop: no block on a closed task", BLOCK not in r.stdout, r.stdout[:200])


def r5():
    sc = scenario()
    holder = subprocess.Popen([sys.executable, __file__, "child-hold", str(budget_path(sc["home"], sc["proj"]))],
                              stdout=subprocess.PIPE, text=True, env=sc["env"])
    try:
        holder.stdout.readline()
        t0 = time.monotonic()
        r = stop(sc)
        spent = time.monotonic() - t0
        b = read_budget(sc)
        check("R5 stuck lock: Stop still blocks and flags", BLOCK in r.stdout and b.get("status") == "flagged", (b.get("status"), r.stdout[:200], r.stderr[-300:]))
        check("R5 stuck lock: bounded wait", spent < 30, f"{spent:.1f}s")
    finally:
        holder.kill()
        holder.wait()


def r6():
    alone = Path(tempfile.mkdtemp(prefix="fd-race-alone-"))
    hook, teleme = alone / HOOK.name, alone / TEL.name
    shutil.copy(HOOK, hook)
    shutil.copy(TEL, teleme)
    sc = scenario()
    r = subprocess.run([sys.executable, str(teleme), "budget-amend", "--add-paths", "y/*", "--reason", "alone", "--cwd", str(sc["proj"])],
                       capture_output=True, text=True, encoding="utf-8", env=sc["env"], timeout=120)
    check("R6 no budget-reserve: amend still works", r.returncode == 0 and "y/*" in read_budget(sc)["paths"], r.stderr[-300:])
    r = stop(sc, hook=hook)
    b = read_budget(sc)
    check("R6 no budget-reserve: Stop still blocks and flags", BLOCK in r.stdout and b.get("status") == "flagged", (b.get("status"), r.stdout[:200], r.stderr[-300:]))


def r7():
    rm = load(RESERVE, "rm7")
    rm.fcntl = None
    rm.msvcrt = None
    d = Path(tempfile.mkdtemp(prefix="fd-race-nolock-"))
    f = d / "b.json"
    f.write_text('{"status": "open"}')

    def mutate(b):
        b["seen"] = True
        return True, True

    res, fresh = rm.locked_update(f, mutate)
    check("R7 no lock primitive: locked_update still writes", res is True and json.loads(f.read_text()).get("seen") is True, fresh)


def main():
    a = sys.argv[1:]
    if a and a[0] == "child-amend":
        return child_amend_with_stop_inside(a[1], a[2])
    if a and a[0] == "child-stop":
        return child_stop_with_cli_inside(a[1], a[2], *a[3:])
    if a and a[0] == "child-hold":
        return child_hold_lock(a[1])
    for case in (r1, r2, r3, r4, r5, r6, r7):
        case()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
