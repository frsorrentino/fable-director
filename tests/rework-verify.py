#!/usr/bin/env python3
"""Verifica della metrica rework (1.31.0): file riaperti dopo la prima
scrittura = informazione arrivata DOPO = contesto incompleto alla scrittura.

Zero hook nuovi: il conteggio vive nei due parser di transcript esistenti
(sum_transcript per la session-summary, scan_jsonl dello Stop hook per il
budget live). Qui si inchiodano le regole:

  R1  bout consecutivi sullo stesso file = iterazione, MAI reopen;
      A->B->A = 1 reopen; NotebookEdit conta; tool_use senza path ignorato;
      scritture dentro toolUseResult (subagent) escluse
  R2  sessione senza write -> stats SENZA chiave "rework" (assenza != zero)
  R3  Stop hook incrementale: 2 run accumulano lo state; il bout a cavallo
      dei run NON raddoppia; al 2x la riga "Rework so far" c'e', la diagnosi
      sotto soglia NO
  R4  sopra soglia (file x2 o totale >= REWORK_DIAG_MIN) la diagnosi c'e'
  R5  al 3x il budget_flag porta reopens + worst_file (path troncato)
  R6  budget-close copia reopens/rework_worst nel budget chiuso e in receipt
  R7  hindsight rende le riaperture nella riga BUST
  R8  report: sezione Rework + context-pack candidates per tipo
  R9  rotazione transcript -> contatori rework azzerati col resto
"""
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "fable-director" / "scripts"
STOP = SCRIPTS / "stop-budget-check.py"
TELEMETRY = SCRIPTS / "fd-telemetry.py"
HINDSIGHT = SCRIPTS / "session-hindsight.py"

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {str(detail)[:300]}")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


NOW = datetime.now(timezone.utc)


def usage_rec(out_tokens, at=None):
    return {"timestamp": iso(at or NOW - timedelta(seconds=100)),
            "message": {"usage": {"input_tokens": 10,
                                  "output_tokens": out_tokens}}}


def write_rec(tool, fp, at=None, key="file_path"):
    return {"timestamp": iso(at or NOW - timedelta(seconds=90)),
            "message": {"content": [{"type": "tool_use", "name": tool,
                                     "input": {key: fp}, "id": "t"}]}}


def mk_budget(home, cwd, fdt, expected_out=100, declared=None):
    bdir = home / ".claude" / "fable-director" / "budgets"
    bdir.mkdir(parents=True, exist_ok=True)
    budget = {"task": "test-rework", "type": "seo-batch",
              "expected_output_tokens": expected_out,
              "expected_input_tokens": 0,
              "declared_at": declared or iso(NOW - timedelta(hours=1)),
              "cwd": cwd, "status": "open"}
    bfile = bdir / f"{fdt.cwd_slug(cwd)}.json"
    bfile.write_text(json.dumps(budget))
    return bfile


def run_stop(home, cwd, transcript):
    return subprocess.run(
        [sys.executable, str(STOP)],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        input=json.dumps({"cwd": cwd, "transcript_path": str(transcript),
                          "session_id": "test"}),
        capture_output=True, text=True, timeout=30)


def run_telemetry(home, args, cwd=None):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, str(TELEMETRY)] + args,
        env=env, capture_output=True, text=True, timeout=30,
        cwd=cwd or str(home))


fdt = load(TELEMETRY, "fdt_rework_test")
tmp = Path(tempfile.mkdtemp(prefix="fd-rework-test-"))
try:
    # ---- R1: regole del fold (in-process, nessun file) ----
    rw = fdt.rework_new()
    for name, tin in [
            ("Write", {"file_path": "/p/a.py"}),
            ("Edit", {"file_path": "/p/a.py"}),      # consecutivo: stesso bout
            ("Edit", {"file_path": "/p/b.py"}),
            ("Edit", {"file_path": "/p/a.py"}),      # ritorno: reopen di a
            ("Read", {"file_path": "/p/a.py"}),      # non write-tool
            ("NotebookEdit", {"notebook_path": "/p/n.ipynb"}),
            ("Edit", {})]:                            # senza path
        fdt.rework_update(rw, name, tin)
    s = fdt.rework_stats(rw)
    check("R1 bout consecutivi non contano, A->B->A = 1 reopen",
          s and s["reopens"] == 1 and s["files_reopened"] == 1, s)
    check("R1 touches/files corretti (NotebookEdit incluso, no-path escluso)",
          s and s["write_touches"] == 5 and s["files_written"] == 3, s)
    check("R1 worst con path troncato a 2 componenti",
          s and s["worst"] == [["p/a.py", 1]], s)

    # R1b: scritture dentro toolUseResult escluse dal walker
    rec = {"message": {"content": [
        {"type": "tool_use", "name": "Agent", "input": {}, "id": "t"}]},
        "toolUseResult": {"content": [
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "/sub/x.py"}, "id": "s"}]}}
    names = [n for n, _ in fdt.find_tool_uses(rec)]
    check("R1b tool_use del subagent (toolUseResult) escluso",
          names == ["Agent"], names)

    # ---- R2: sessione senza write -> niente chiave rework ----
    t = tmp / "r2.jsonl"
    t.write_text(json.dumps(usage_rec(50)) + "\n")
    *_, stats = fdt.sum_transcript(t)
    check("R2 nessuna write -> chiave rework assente",
          "rework" not in stats, stats.get("rework"))

    # ---- R3: Stop hook, 2 run incrementali, 2x sotto soglia diagnosi ----
    h = tmp / "r3"
    cwd = "/proj/rework"
    mk_budget(h, cwd, fdt, expected_out=100)
    t = h / "transcript.jsonl"
    part1 = [usage_rec(150),
             write_rec("Write", "/x/a.py"),
             write_rec("Edit", "/x/b.py"),
             write_rec("Edit", "/x/a.py")]      # reopen a
    t.write_text("".join(json.dumps(r) + "\n" for r in part1))
    r = run_stop(h, cwd, t)
    check("R3 run1 sotto 2x: nessun blocco", '"decision"' not in r.stdout,
          r.stdout)
    with t.open("a") as fh:
        for rec in [write_rec("Edit", "/x/a.py"),   # cavallo dei run: stesso bout
                    write_rec("Edit", "/x/b.py"),   # reopen b
                    usage_rec(100)]:
            fh.write(json.dumps(rec) + "\n")
    r = run_stop(h, cwd, t)
    # lo stdout e' JSON con ensure_ascii (× = ×): si parsa, non si greppa
    reason = json.loads(r.stdout).get("reason", "") if r.stdout.strip() else ""
    check("R3 2x checkpoint scatta", '"decision": "block"' in r.stdout
          and "2× checkpoint" in reason, r.stdout)
    check("R3 riga rework fattuale presente",
          "Rework so far: 2 reopens across 2 files" in r.stdout, r.stdout)
    check("R3 sotto soglia: niente diagnosi",
          "context was incomplete" not in r.stdout, r.stdout)
    st = json.loads((h / ".claude" / "fable-director" / "budgets" /
                     f"{fdt.cwd_slug(cwd)}.state.json").read_text())
    check("R3 state accumulato: a toccato 3 volte, bout non raddoppiato",
          st["rw"]["touch"]["/x/a.py"] == 3
          and st["rw"]["bouts"]["/x/a.py"] == 2, st["rw"])

    # ---- R4: diagnosi sopra soglia (worst >= 2) ----
    h = tmp / "r4"
    mk_budget(h, cwd, fdt, expected_out=100)
    t = h / "transcript.jsonl"
    seq = [usage_rec(250)] + [write_rec("Edit", f)
                              for f in ("/x/a.py", "/x/b.py", "/x/a.py",
                                        "/x/b.py", "/x/a.py")]
    t.write_text("".join(json.dumps(r) + "\n" for r in seq))
    r = run_stop(h, cwd, t)
    check("R4 sopra soglia: diagnosi contesto incompleto",
          "Rework so far: 3 reopens" in r.stdout
          and "context was incomplete" in r.stdout, r.stdout)

    # ---- R5: 3x -> budget_flag con reopens e worst_file ----
    h = tmp / "r5"
    mk_budget(h, cwd, fdt, expected_out=100)
    t = h / "transcript.jsonl"
    seq = [usage_rec(400), write_rec("Write", "/x/a.py"),
           write_rec("Edit", "/x/b.py"), write_rec("Edit", "/x/a.py")]
    t.write_text("".join(json.dumps(r) + "\n" for r in seq))
    r = run_stop(h, cwd, t)
    reason = json.loads(r.stdout).get("reason", "") if r.stdout.strip() else ""
    check("R5 3x blocca con post-mortem", "3× block" in reason, r.stdout)
    db = h / ".claude" / "fable-director" / "telemetry.db"
    con = sqlite3.connect(db)
    flags = [json.loads(p) for (p,) in con.execute(
        "SELECT payload FROM events WHERE event='budget_flag'")]
    con.close()
    check("R5 budget_flag porta reopens + worst_file troncato",
          flags and flags[0].get("reopens") == 1
          and flags[0].get("worst_file") == "x/a.py", flags)

    # ---- R6: budget-close copia il rework nel task_close e in receipt ----
    r = run_telemetry(h, ["budget-close", "--outcome", "flagged",
                          "--cwd", cwd])
    check("R6 budget-close esce pulito", r.returncode == 0,
          r.stdout + r.stderr)
    bfile = (h / ".claude" / "fable-director" / "budgets" /
             f"{fdt.cwd_slug(cwd)}.json")
    closed = json.loads(bfile.read_text())
    check("R6 budget chiuso con reopens e rework_worst",
          closed.get("reopens") == 1
          and closed.get("rework_worst") == [["x/a.py", 1]], closed)
    receipts = list((h / ".claude" / "fable-director" / "receipts").glob("*.json"))
    rec = json.loads(receipts[0].read_text()) if receipts else {}
    check("R6 receipt porta reopens", rec.get("reopens") == 1, rec)

    # ---- R7: hindsight rende le riaperture ----
    h = tmp / "r7"
    base = h / ".claude" / "fable-director"
    base.mkdir(parents=True)
    con = sqlite3.connect(base / "telemetry.db")
    con.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, ts TEXT NOT NULL,"
                " session_id TEXT, cwd TEXT, event TEXT NOT NULL, payload TEXT)")
    con.execute("INSERT INTO events(ts, cwd, event, payload) VALUES("
                "datetime('now','-2 days'), ?, 'budget_flag', ?)",
                ("/proj/a", json.dumps({"task": "batch seo", "ratio": 4.2,
                                        "dim": "output", "expected": 10000,
                                        "actual": 42000, "auto": True,
                                        "reopens": 12,
                                        "worst_file": "routes/api.py"})))
    con.commit()
    con.close()
    r = subprocess.run(
        [sys.executable, str(HINDSIGHT)],
        env={"HOME": str(h), "PATH": "/usr/bin:/bin"},
        input=json.dumps({"hook_event_name": "SessionStart",
                          "cwd": "/proj/a", "session_id": "test"}),
        capture_output=True, text=True, timeout=30)
    check("R7 hindsight mostra riaperture e file",
          "12 riaperture (routes/api.py)" in r.stdout, r.stdout)

    # ---- R8: report con sezione Rework e context-pack candidates ----
    h = tmp / "r8"
    base = h / ".claude" / "fable-director"
    base.mkdir(parents=True)
    con = sqlite3.connect(base / "telemetry.db")
    con.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, ts TEXT NOT NULL,"
                " session_id TEXT, cwd TEXT, event TEXT NOT NULL, payload TEXT)")
    for i, reo in enumerate((5, 6)):
        con.execute("INSERT INTO events(ts, event, payload) VALUES("
                    "datetime('now','-1 hours'), 'session_summary', ?)",
                    (json.dumps({"input_tokens": 10, "output_tokens": 10,
                                 "rework": {"write_touches": 10,
                                            "files_written": 4,
                                            "files_reopened": 2,
                                            "reopens": reo,
                                            "worst": [["x/a.py", reo - 2]]}}),))
        con.execute("INSERT INTO events(ts, event, payload) VALUES("
                    "datetime('now','-1 hours'), 'task_close', ?)",
                    (json.dumps({"type": "seo-batch", "outcome": "ok",
                                 "route": "workflow", "reopens": reo}),))
    con.commit()
    con.close()
    r = run_telemetry(h, ["report", "--days", "7"])
    check("R8 sezione Rework presente",
          "Rework — files reopened after first write" in r.stdout
          and "11 reopens total" in r.stdout, r.stdout)
    check("R8 worst file aggregato", "x/a.py: 7 reopens" in r.stdout, r.stdout)
    check("R8 context-pack candidates per tipo",
          "Context-pack candidates" in r.stdout
          and "seo-batch: 2 tasks" in r.stdout, r.stdout)

    # ---- R9: rotazione transcript azzera anche il rework ----
    sbc = load(STOP, "sbc_rework_test")
    t = tmp / "r9.jsonl"
    t.write_text(json.dumps(write_rec("Edit", "/x/nuovo.py")) + "\n")
    sub = {"off": 10 ** 9, "out": 5, "inp": 5,
           "rw": {"last": "/x/vecchio.py",
                  "touch": {"/x/vecchio.py": 5},
                  "bouts": {"/x/vecchio.py": 3}},
           "last_ts": None}
    sbc.scan_jsonl(t, sub, None)
    check("R9 rotazione: rework riparte dal file nuovo",
          list(sub["rw"]["touch"]) == ["/x/nuovo.py"]
          and sub["rw"]["bouts"]["/x/nuovo.py"] == 1, sub["rw"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
