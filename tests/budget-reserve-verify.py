#!/usr/bin/env python3
"""Verifica deterministica — riserva del budget per le deleghe parallele e
promemoria del budget dentro i turni lunghi (1.60.0).

Il gate impegna la stima di ogni delega nel budget aperto PRIMA che parta
(riserva); lo Stop hook, a fine giro, salda la riserva con il consumo vero.
Cosi' N agenti lanciati nello stesso messaggio non sforano il tetto: lo Stop
hook li vedrebbe solo a fine turno, a spesa fatta.

  R1  4 gate concorrenti sotto un budget da 4 agenti: tutti permessi, 4 voci
      di riserva nel budget file, somma esatta (nessun aggiornamento perso)
  R2  quota per agente = stima / --agents: all'8ª delega (2×) avviso UNA volta,
      alla 12ª (3×) deny con regola `reservation`, gate_deny registrato,
      nessuna voce aggiunta dalla delega negata
  R3  Stop hook: saldo con il consumo vero — voci azzerate, marca al
      consuntivo, evento reservation_settle; la delega dopo passa in silenzio
      e l'avviso 2× torna a scattare nel giro successivo
  R4  agenti ancora in volo (registro del misuratore): le voci piu' recenti
      restano, ridotte di quanto il consuntivo ha gia' pagato
  R5  senza --agents: ancora empirica (20k out) per delega, mai oltre la stima
  R6  Workflow: una voce per lancio con N = --agents (resume: meno gli agenti
      gia' nel journal)
  R7  promemoria PostToolUse (Bash): dopo FD_REMIND_GAP_S dall'apertura una
      riga additionalContext con consumo, riserva e margine al 2×; poi
      silenzio per l'intervallo; 0 = spento; budget senza consumo = silenzio
  R9  deleghe sequenziali nello stesso turno: le voci piu' vecchie di
      GRACE_S oltre gli agenti in volo si rilasciano al gate successivo (niente
      falso deny in un turno lungo); le voci giovani restano sempre
  R8  hooks.json monta il promemoria su PostToolUse Bash|Write|Edit via py.sh

Usage: python3 tests/budget-reserve-verify.py   (exit 0 = all green)
"""
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from multiprocessing import Process
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fable-director"
SCRIPTS = ROOT / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {str(evidence)[:600]}"))


home = Path(tempfile.mkdtemp(prefix="fd-reserve-home-"))
os.environ["HOME"] = str(home)
os.environ.pop("CLAUDE_CONFIG_DIR", None)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)
FDT, GATE, STOP, REMIND = (str(SCRIPTS / n) for n in (
    "fd-telemetry.py", "pre-delegation-gate.py", "stop-budget-check.py", "budget-reminder.py"))
BASE = home / ".claude" / "fable-director"
BASE.mkdir(parents=True, exist_ok=True)
SID = "s-reserve"


def run(args, stdin=None, extra=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "FD_REMIND_GAP_S"):
        env.pop(k, None)
    env.update(extra or {})
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          env=env, input=stdin, timeout=120)


def fresh_proj():
    proj = Path(tempfile.mkdtemp(prefix="fd-reserve-proj-"))
    sess = home / ".claude" / "projects" / ("-" + proj.name) / SID
    sess.mkdir(parents=True)
    tr = sess.with_suffix(".jsonl")
    tr.write_text("")
    return proj, tr


def bopen(proj, out, inp=0, agents=None, route="agent", extra=None):
    args = [FDT, "budget-open", "--task", f"riserva {proj.name[-4:]}", "--expected-output", str(out),
            "--cwd", str(proj), "--verify", "test -d .", "--route", route, "--paths", "none", "--cost-ack"]
    if inp:
        args += ["--expected-input", str(inp)]
    if agents:
        args += ["--agents", str(agents)]
    r = run(args + (extra or []))
    assert r.returncode == 0, r.stderr
    return r


def bfile_of(proj):
    return BASE / "budgets" / f"{fdt.cwd_slug(str(proj))}.json"


def budget_of(proj):
    return json.loads(bfile_of(proj).read_text())


def gate(proj, tr, uid, tool="Agent", tool_input=None, extra_env=None):
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(proj), "session_id": SID,
            "transcript_path": str(tr), "tool_use_id": uid,
            "tool_input": tool_input or {"subagent_type": "fable-director:fd-executor",
                                         "prompt": "x", "description": "x"}}
    r = run([GATE], json.dumps(data), extra=extra_env)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return out, r


def deny_reason(out):
    h = out.get("hookSpecificOutput") or {}
    return h.get("permissionDecisionReason", "") if h.get("permissionDecision") == "deny" else None


def usage(ts, out, inp=0, mid=None):
    rec = {"timestamp": ts, "type": "assistant", "requestId": mid or f"m-{ts}-{out}",
           "message": {"id": mid or f"m-{ts}-{out}", "model": "claude-sonnet-5", "content": [],
                       "usage": {"output_tokens": out, "input_tokens": inp,
                                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}
    return json.dumps(rec) + "\n"


def stop(proj, tr):
    p = {"hook_event_name": "Stop", "cwd": str(proj), "transcript_path": str(tr),
         "stop_hook_active": False, "session_id": SID}
    r = run([STOP], json.dumps(p))
    outs = [l for l in r.stdout.splitlines() if l.strip()]
    return (json.loads(outs[0]) if outs else {}), r


def events(name):
    con = sqlite3.connect(str(BASE / "telemetry.db"))
    rows = [json.loads(p) for (p,) in con.execute(
        "SELECT payload FROM events WHERE event=? ORDER BY id", (name,))]
    con.close()
    return rows


def reserve_of(proj):
    return budget_of(proj).get("reserve") or {}


def entries_of(proj):
    return reserve_of(proj).get("entries") or []


# ---------------------------------------------------------------- R1
proj, tr = fresh_proj()
bopen(proj, 80000, 68000, agents=4)
results = home / "r1"
results.mkdir()


def _gate_proc(i):
    out, r = gate(proj, tr, f"tu-{i}")
    (results / f"{i}.json").write_text(json.dumps({"out": out, "err": r.stderr}))


procs = [Process(target=_gate_proc, args=(i,)) for i in range(1, 5)]
for p in procs:
    p.start()
for p in procs:
    p.join(timeout=120)
outs = [json.loads(f.read_text()) for f in sorted(results.iterdir())]
ents = entries_of(proj)
check("R1 4 gate concorrenti: tutti permessi, 4 voci, 80.000 out / 68.000 in riservati",
      len(outs) == 4 and all(deny_reason(o["out"]) is None for o in outs)
      and len(ents) == 4 and sum(e["out"] for e in ents) == 80000
      and sum(e["inp"] for e in ents) == 68000
      and {e["id"] for e in ents} == {f"tu-{i}" for i in range(1, 5)},
      json.dumps(outs)[:300] + "\n" + json.dumps(ents)[:300])
check("R1b sotto la stima: nessun avviso di riserva",
      not any("reservation" in (o["out"].get("systemMessage") or "") for o in outs),
      json.dumps(outs)[:300])

# ---------------------------------------------------------------- R2
msgs = {}
for i in range(5, 12):
    out, r = gate(proj, tr, f"tu-{i}")
    msgs[i] = (out, r)
m7 = msgs[7][0].get("systemMessage") or ""
m8 = msgs[8][0].get("systemMessage") or ""
m9 = msgs[9][0].get("systemMessage") or ""
check("R2 7ª delega (1,75×): nessun avviso; 8ª (2×): avviso di riserva con il rapporto",
      "reservation" not in m7 and "reservation" in m8 and "2.0×" in m8
      and "8 delegations" in m8 and "Delegation allowed" in m8,
      m7 + "\n---\n" + m8 + "\n" + msgs[8][1].stderr)
check("R2b l'avviso scatta una volta per giro: 9ª-11ª in silenzio, flag reserve_warned",
      "reservation" not in m9 and all(deny_reason(msgs[i][0]) is None for i in range(9, 12))
      and reserve_of(proj).get("warned") is True, m9)
out12, r12 = gate(proj, tr, "tu-12")
dr = deny_reason(out12) or ""
denies = [e for e in events("gate_deny") if e.get("kind") == "reservation"]
check("R2c 12ª delega (3×): deny con regola reservation, 3× ceiling nominato, gate_deny registrato",
      "[fable-director rule: reservation]" in dr and "3× ceiling" in dr and "11 delegations" in dr
      and "240" in dr.replace(".", "").replace(",", "") and len(denies) == 1
      and denies[0].get("tool") == "Agent", dr + "\n" + r12.stderr + json.dumps(denies))
check("R2d la delega negata non riserva: restano 11 voci",
      len(entries_of(proj)) == 11 and sum(e["out"] for e in entries_of(proj)) == 220000,
      json.dumps(entries_of(proj))[:200])

# ---------------------------------------------------------------- R3
tr.write_text(usage("2030-01-01T10:00:00Z", 50000, 10000))
msg, r = stop(proj, tr)
res = reserve_of(proj)
settles = events("reservation_settle")
check("R3 Stop: voci saldate, marca al consuntivo (50.000 out), evento reservation_settle",
      msg.get("decision") != "block" and res.get("entries") == [] and res.get("mark_out") == 50000
      and res.get("mark_inp") == 10000 and len(settles) == 1 and settles[0].get("n") == 11
      and settles[0].get("reserved_out") == 220000 and settles[0].get("actual_out") == 50000
      and settles[0].get("kept") == 0 and settles[0].get("auto") is True,
      r.stdout + r.stderr + json.dumps(res)[:300] + json.dumps(settles)[:300])
out13, r13 = gate(proj, tr, "tu-13")
check("R3b dopo il saldo la delega passa in silenzio (50k + 20k = 0,9×)",
      deny_reason(out13) is None and "reservation" not in (out13.get("systemMessage") or "")
      and len(entries_of(proj)) == 1 and reserve_of(proj).get("warned") is not True,
      r13.stdout + r13.stderr)
for i in range(14, 19):
    out, r = gate(proj, tr, f"tu-{i}")
m18 = out.get("systemMessage") or ""
check("R3c giro nuovo: a 50k + 6×20k = 170k (2,1×) l'avviso torna a scattare",
      "reservation" in m18 and "2.1×" in m18, m18 + r.stderr)

# ---------------------------------------------------------------- R4
proj4, tr4 = fresh_proj()
bopen(proj4, 80000, 68000, agents=4)
for uid in ("tu-a", "tu-b", "tu-c"):
    gate(proj4, tr4, uid)
sub = BASE / "subagents"; sub.mkdir(exist_ok=True)
now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
(sub / f"{SID}.json").write_text(json.dumps({
    "inflight": {"a2": {"type": "fable-director:fd-executor", "since": now_iso},
                 "a3": {"type": "fable-director:fd-executor", "since": now_iso}},
    "started": 3, "stopped": 1, "by_type": {}, "effort_ignored": 0, "nested_seen": 0,
    "outcomes": {}, "failures_by_type": {}, "last_blocked": []}))
tr4.write_text(usage("2030-01-01T10:00:00Z", 30000, 5000))
msg, r = stop(proj4, tr4)
res4 = reserve_of(proj4)
ents4 = res4.get("entries") or []
check("R4 2 agenti in volo: restano le 2 voci piu' recenti, pagate dal consuntivo (10k + 20k out)",
      [e["id"] for e in ents4] == ["tu-b", "tu-c"] and [e["out"] for e in ents4] == [10000, 20000]
      and res4.get("mark_out") == 30000 and events("reservation_settle")[-1].get("kept") == 2,
      r.stdout + r.stderr + json.dumps(res4)[:400])
(sub / f"{SID}.json").unlink()

# ---------------------------------------------------------------- R5
proj5, tr5 = fresh_proj()
bopen(proj5, 30000)
m = {}
for i in range(1, 6):
    out, r = gate(proj5, tr5, f"tu-{i}")
    m[i] = out
check("R5 senza --agents: 20k per delega; 3ª = 2× avviso, 5ª (100k ≥ 90k) deny",
      deny_reason(m[2]) is None and "reservation" not in (m[2].get("systemMessage") or "")
      and "reservation" in (m[3].get("systemMessage") or "")
      and deny_reason(m[4]) is None and deny_reason(m[5]) is not None
      and all(e["out"] == 20000 for e in entries_of(proj5)),
      json.dumps(m)[:600])

# ---------------------------------------------------------------- R6
proj6, tr6 = fresh_proj()
bopen(proj6, 400000, 300000, agents=16, route="workflow")
wf_input = {"script": "export const meta = {name:'p',description:'d'}\nawait agent('a', {model:'sonnet'})\n"}
o1, r1 = gate(proj6, tr6, "tu-w1", tool="Workflow", tool_input=wf_input)
e6 = entries_of(proj6)
check("R6 Workflow: una voce con n = 16 agenti e la stima intera (400k out), silenzio a 1×",
      deny_reason(o1) is None and "reservation" not in (o1.get("systemMessage") or "")
      and len(e6) == 1 and e6[0]["n"] == 16 and e6[0]["out"] == 400000 and e6[0]["tool"] == "Workflow",
      r1.stdout + r1.stderr + json.dumps(e6))
o2, r2 = gate(proj6, tr6, "tu-w2", tool="Workflow", tool_input=wf_input)
check("R6b secondo lancio nuovo: 800k = 2× → avviso, permesso",
      deny_reason(o2) is None and "reservation" in (o2.get("systemMessage") or ""), r2.stdout)
jdir = tr6.with_suffix("") / "subagents" / "workflows" / "wf_r6"
jdir.mkdir(parents=True)
(jdir / "journal.jsonl").write_text("".join(json.dumps({"agent": i}) + "\n" for i in range(10)))
o3, r3 = gate(proj6, tr6, "tu-w3", tool="Workflow", tool_input=dict(wf_input, resumeFromRunId="wf_r6"))
e6 = entries_of(proj6)
check("R6c resume con 10 agenti nel journal: voce da 6 agenti (150k out)",
      deny_reason(o3) is None and len(e6) == 3 and e6[-1]["n"] == 6 and e6[-1]["out"] == 150000,
      r3.stdout + r3.stderr + json.dumps(e6)[:300])

# ---------------------------------------------------------------- R7
proj7, tr7 = fresh_proj()
bopen(proj7, 100000, 200000)
bf7 = bfile_of(proj7)
b7 = json.loads(bf7.read_text())
b7["declared_at"] = (datetime.now(timezone.utc) - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
bf7.write_text(json.dumps(b7))
gate(proj7, tr7, "tu-r7")
tr7.write_text(usage("2030-01-01T10:00:00Z", 38000, 90000))


def remind(proj, tr, extra_env=None, tool="Bash"):
    data = {"hook_event_name": "PostToolUse", "tool_name": tool, "cwd": str(proj), "session_id": SID,
            "transcript_path": str(tr), "tool_input": {"command": "ls"}, "tool_response": {}}
    r = run([REMIND], json.dumps(data), extra=extra_env)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    return ((out.get("hookSpecificOutput") or {}).get("additionalContext") or ""), r


line, r = remind(proj7, tr7)
check("R7 promemoria dopo 15 min: consumo, rapporto, riserva in volo e margine al 2×",
      line.startswith("[fable-director] budget '") and "open 15 min" in line
      and "output 38k of 100k (0.4×)" in line and "fresh input 90k of 200k (0.5×)" in line
      and "1 delegation in flight reserving 20k output" in line
      and "162k output" in line and "310k input" in line and "budget-close" in line
      and len(line) <= 400,
      line + "\n" + r.stdout + r.stderr)
check("R7b il promemoria resta sotto l'intervallo: seconda chiamata in silenzio, reminded_at scritto",
      remind(proj7, tr7)[0] == "" and budget_of(proj7).get("reminded_at"), r.stdout)
check("R7c FD_REMIND_GAP_S=0 spegne il promemoria",
      remind(proj7, tr7, extra_env={"FD_REMIND_GAP_S": "0"})[0] == "", "")
proj7d, tr7d = fresh_proj()
bopen(proj7d, 100000)
bf = bfile_of(proj7d); b = json.loads(bf.read_text())
b["declared_at"] = (datetime.now(timezone.utc) - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
bf.write_text(json.dumps(b))
line_d, r_d = remind(proj7d, tr7d)
check("R7d budget vecchio ma senza consumo: silenzio", line_d == "" and not r_d.stderr, r_d.stdout + r_d.stderr)
b["declared_at"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
bf.write_text(json.dumps(b))
tr7d.write_text(usage("2030-01-01T10:00:00Z", 5000, 1000))
check("R7e budget aperto da 2 min con consumo: ancora sotto l'intervallo, silenzio",
      remind(proj7d, tr7d)[0] == "", "")

# ---------------------------------------------------------------- R9
proj9, tr9 = fresh_proj()
bopen(proj9, 30000)
for i in range(1, 5):
    gate(proj9, tr9, f"tu-s{i}")
b9 = budget_of(proj9)
old_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
for e in b9["reserve"]["entries"][:3]:
    e["at"] = old_at   # 3 deleghe finite da 10 min, la 4ª appena lanciata
bfile_of(proj9).write_text(json.dumps(b9))
out9, r9 = gate(proj9, tr9, "tu-s5")
ids9 = [e["id"] for e in entries_of(proj9)]
check("R9 tre deleghe finite da 10 min, nessuna in volo: rilasciate, la 5ª passa (40k = 1,3×)",
      deny_reason(out9) is None and ids9 == ["tu-s4", "tu-s5"], json.dumps(out9) + str(ids9))
b9 = budget_of(proj9)
for e in b9["reserve"]["entries"]:
    e["at"] = old_at
bfile_of(proj9).write_text(json.dumps(b9))
(sub / f"{SID}.json").write_text(json.dumps({
    "inflight": {"a9": {"type": "fable-director:fd-executor", "since": now_iso}},
    "started": 5, "stopped": 4, "by_type": {}, "effort_ignored": 0, "nested_seen": 0,
    "outcomes": {}, "failures_by_type": {}, "last_blocked": []}))
gate(proj9, tr9, "tu-s6")
ids9b = [e["id"] for e in entries_of(proj9)]
check("R9b un agente in volo: resta la sua voce (la piu' recente fra le vecchie) piu' la nuova",
      ids9b == ["tu-s5", "tu-s6"], str(ids9b))
(sub / f"{SID}.json").unlink()

# ---------------------------------------------------------------- R8
hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
post = [(m.get("matcher"), h) for m in hooks.get("PostToolUse", []) for h in m["hooks"]]
mine = [(mt, h) for mt, h in post if "budget-reminder.py" in h.get("command", "")]
check("R8 hooks.json: budget-reminder.py su PostToolUse Bash|Write|Edit via py.sh, senza onFailure",
      len(mine) == 1 and set(mine[0][0].split("|")) >= {"Bash", "Write", "Edit"}
      and "py.sh" in mine[0][1]["command"] and "onFailure" not in mine[0][1], json.dumps(mine))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
