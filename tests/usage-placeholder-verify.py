#!/usr/bin/env python3
"""Verifica deterministica — usage segnaposto nei transcript dei subagenti
(Claude Code < 2.1.307: le risposte in streaming di un subagente venivano
registrate con output_tokens segnaposto e stop_reason vuoto; corretto nella
2.1.307). Ogni misura letta da quei file va marcata, mai corretta a mano.

  P1 agent_usage_meta: 2 turni segnaposto (output 1, stop_reason vuoto) → placeholder_turns 2, version
  P2 agent_usage_meta: usage vero (output 100, stop_reason end_turn) → placeholder_turns 0
  P3 SubagentStop: delegation_outcome porta cc_version; usage_placeholder solo se tutti i turni sono segnaposto
  P4 session-summary: wf_placeholder_turns dai file agente Workflow, cc_version dal main transcript
  P5 workflow_calibration: gli esiti marcati usage_placeholder non entrano nella mediana per agente
  P6 usage-placeholder-audit.py: conteggio per versione di Claude Code su un albero di progetti
  P7 report: riga di avviso con versioni viste e agenti/sessioni marcati

Usage: python3 tests/usage-placeholder-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-ph-home-"))
proj = Path(tempfile.mkdtemp(prefix="fd-ph-proj-"))
os.environ["HOME"] = str(home)
os.environ.pop("CLAUDE_CONFIG_DIR", None)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fdt = load("fdt_ph", SCRIPTS / "fd-telemetry.py")
sam = load("sam_ph", SCRIPTS / "subagent-meter.py")
FDT, METER = str(SCRIPTS / "fd-telemetry.py"), str(SCRIPTS / "subagent-meter.py")
AUDIT = SCRIPTS / "usage-placeholder-audit.py"
SID = "s-placeholder"


def run(args, stdin=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          env=env, input=stdin, timeout=60)


def agent_lines(version, placeholder, turns=2, model="claude-sonnet-5-5",
                ts="2030-01-01T10:0%d:00Z", tag=""):
    """Record come li scrive Claude Code: due righe per messaggio (thinking +
    text) con lo stesso usage; segnaposto = output_tokens 1 e stop_reason null."""
    out = []
    for i in range(turns):
        u = {"input_tokens": 5, "output_tokens": 1 if placeholder else 100,
             "cache_read_input_tokens": 8000 * i,
             "cache_creation_input_tokens": 8000 if i == 0 else 300}
        msg = {"id": f"msg_{version}{tag}_{i}", "model": model,
               "stop_reason": None if placeholder else "end_turn", "usage": u}
        for block in ({"type": "thinking", "thinking": ""},
                      {"type": "text", "text": "DONE" if i == turns - 1 else "working"}):
            out.append(json.dumps({"type": "assistant", "isSidechain": True,
                                   "version": version, "timestamp": ts % i,
                                   "message": dict(msg, content=[block])}))
    return "\n".join(out) + "\n"


tmp = Path(tempfile.mkdtemp(prefix="fd-ph-tr-"))
ph = tmp / "agent-ph.jsonl"; ph.write_text(agent_lines("2.1.306", True))
real = tmp / "agent-real.jsonl"; real.write_text(agent_lines("2.1.307", False))
mixed = tmp / "agent-mixed.jsonl"
mixed.write_text(agent_lines("2.1.306", True, turns=1)
                 + agent_lines("2.1.306", False, turns=1, ts="2030-01-01T10:1%d:00Z", tag="b"))

# P1
meta_fn = getattr(sam, "agent_usage_meta", None)
try:
    m1 = meta_fn(ph) if meta_fn else None
except Exception as e:  # noqa: BLE001
    m1 = e
ok1 = isinstance(m1, tuple) and len(m1) == 4 and m1[1] == 2 \
    and m1[3].get("placeholder_turns") == 2 and m1[3].get("version") == "2.1.306"
check("P1 agent_usage_meta: 2 turni segnaposto → placeholder_turns 2, version 2.1.306", ok1, repr(m1))

# P2
try:
    m2 = meta_fn(real) if meta_fn else None
except Exception as e:  # noqa: BLE001
    m2 = e
ok2 = isinstance(m2, tuple) and m2[1] == 2 and m2[2]["output_tokens"] == 200 \
    and m2[3].get("placeholder_turns") == 0 and m2[3].get("version") == "2.1.307"
check("P2 agent_usage_meta: usage vero → placeholder_turns 0, output 200", ok2, repr(m2))
m_old = sam.agent_usage(real)
check("P2b agent_usage resta a tre valori (model, turns, tot)",
      isinstance(m_old, tuple) and len(m_old) == 3 and m_old[1] == 2, repr(m_old))


# P3
def stop(name, tr, atype="fable-director:fd-executor"):
    d = {"hook_event_name": "SubagentStop", "session_id": SID, "cwd": str(proj),
         "agent_id": name, "agent_type": atype, "agent_transcript_path": str(tr),
         "last_assistant_message": "DONE", "stop_hook_active": False}
    return run([METER], json.dumps(d))


def events(kind):
    db = home / ".claude" / "fable-director" / "telemetry.db"
    if not db.is_file():
        return []
    con = sqlite3.connect(db)
    rows = [json.loads(p) for (p,) in con.execute(
        "select payload from events where event=? order by ts, id", (kind,))]
    con.close()
    return rows


r = stop("a-ph", ph)
r2 = stop("a-real", real)
r3 = stop("a-mixed", mixed)
ev = events("delegation_outcome")
by_id = {e.get("agent_id"): e for e in ev}
e_ph, e_real, e_mix = by_id.get("a-ph", {}), by_id.get("a-real", {}), by_id.get("a-mixed", {})
check("P3 SubagentStop: segnaposto → usage_placeholder true, placeholder_turns 2, cc_version 2.1.306, eq comunque calcolato",
      r.returncode == 0 and e_ph.get("usage_placeholder") is True and e_ph.get("placeholder_turns") == 2
      and e_ph.get("cc_version") == "2.1.306" and e_ph.get("eq") and e_ph.get("turns") == 2,
      f"rc={r.returncode} {e_ph} err={r.stderr}")
check("P3b SubagentStop: usage vero → cc_version 2.1.307, nessuna chiave usage_placeholder",
      r2.returncode == 0 and e_real.get("cc_version") == "2.1.307"
      and "usage_placeholder" not in e_real and "placeholder_turns" not in e_real,
      f"rc={r2.returncode} {e_real}")
check("P3c SubagentStop: misto (1 su 2) → placeholder_turns 1, usage_placeholder assente (eq parziale, non monco)",
      r3.returncode == 0 and e_mix.get("placeholder_turns") == 1 and "usage_placeholder" not in e_mix,
      f"rc={r3.returncode} {e_mix}")

# P4
sdir = home / ".claude" / "projects" / "-proj" / "sess-ph"
tr_main = sdir.with_suffix(".jsonl")
sdir.mkdir(parents=True)
tr_main.write_text(
    '{"type":"user","version":"2.1.306","timestamp":"2030-01-01T10:00:00Z","message":{"role":"user","content":"x"}}\n'
    '{"type":"assistant","version":"2.1.306","timestamp":"2030-01-01T10:00:05Z","message":{"id":"m0","model":"claude-fable-5-1","stop_reason":"end_turn","usage":{"output_tokens":10,"input_tokens":5}}}\n')
wdir = sdir / "subagents" / "workflows" / "wf_ph"
wdir.mkdir(parents=True)
(wdir / "agent-w1.jsonl").write_text(agent_lines("2.1.306", True))
(wdir / "agent-w2.jsonl").write_text(agent_lines("2.1.306", False, turns=1))
tot, n = fdt.sum_workflow_agents(tr_main)
check("P4 sum_workflow_agents: turns 3, placeholder_turns 2, cc_version 2.1.306, 2 file",
      n == 2 and tot.get("turns") == 3 and tot.get("placeholder_turns") == 2
      and tot.get("cc_version") == "2.1.306" and tot["output_tokens"] == 102, json.dumps(tot))
r = run([FDT, "session-summary", "--transcript", str(tr_main), "--session-id", "sess-ph", "--cwd", str(proj)])
ss = [s for s in events("session_summary") if s.get("wf_agents")]
s = ss[-1] if ss else {}
check("P4b session-summary: wf_placeholder_turns 2 e cc_version 2.1.306 nel payload",
      r.returncode == 0 and s.get("wf_placeholder_turns") == 2 and s.get("cc_version") == "2.1.306"
      and s.get("wf_agents") == 2, f"rc={r.returncode} {s} err={r.stderr}")

# P5
for i in range(6):
    fdt.log_event("delegation_outcome", {"agent_type": "workflow-subagent", "eq": 300000 + i},
                  session_id="cal-ph", cwd=str(proj))
    fdt.log_event("delegation_outcome", {"agent_type": "workflow-subagent", "eq": 1000 + i,
                                         "usage_placeholder": True, "placeholder_turns": 3},
                  session_id="cal-ph", cwd=str(proj))
cal = fdt.workflow_calibration(days=1)
check("P5 workflow_calibration: 6 veri + 6 segnaposto → mediana sui 6 veri (300003), n_agents 6",
      cal["n_agents"] == 6 and cal["agent_eq"] == 300003 and cal["agent_src"].startswith("measured"),
      json.dumps(cal))

# P6
root = Path(tempfile.mkdtemp(prefix="fd-ph-root-"))
s1 = root / "-proj-a" / "sess1" / "subagents"; s1.mkdir(parents=True)
(s1 / "agent-a.jsonl").write_text(agent_lines("2.1.306", True))
s2 = root / "-proj-b" / "sess2" / "subagents" / "workflows" / "wf_1"; s2.mkdir(parents=True)
(s2 / "agent-b.jsonl").write_text(agent_lines("2.1.307", False))
(s2 / "agent-c.jsonl").write_text(agent_lines("2.1.307", False, turns=3))
(root / "-proj-a" / "sess1.jsonl").write_text(
    '{"type":"assistant","version":"2.1.306","timestamp":"2030-01-01T10:00:05Z","message":{"id":"m0","model":"claude-fable-5-1","stop_reason":"end_turn","usage":{"output_tokens":10,"input_tokens":5}}}\n')
r = run([str(AUDIT), "--root", str(root), "--json"]) if AUDIT.is_file() else None
try:
    aj = json.loads(r.stdout) if r else {}
except ValueError:
    aj = {}
bv = aj.get("by_version") or {}
check("P6 audit --json: per versione agenti/turni/segnaposto (2.1.306: 1 agente, 2 turni, 2 segnaposto; 2.1.307: 2 agenti, 5 turni, 0)",
      r is not None and r.returncode == 0
      and bv.get("2.1.306", {}).get("agents") == 1 and bv["2.1.306"].get("turns") == 2
      and bv["2.1.306"].get("placeholder_turns") == 2 and bv["2.1.306"].get("agents_placeholder") == 1
      and bv.get("2.1.307", {}).get("agents") == 2 and bv["2.1.307"].get("turns") == 5
      and bv["2.1.307"].get("placeholder_turns") == 0,
      (r.stdout[-600:] + r.stderr[-300:]) if r else "script assente")
r = run([str(AUDIT), "--root", str(root)]) if AUDIT.is_file() else None
check("P6b audit testo: riga per versione e totale sotto la 2.1.307",
      r is not None and r.returncode == 0 and "2.1.306" in r.stdout and "2.1.307" in r.stdout
      and "< 2.1.307" in r.stdout, (r.stdout[-600:] + r.stderr[-300:]) if r else "script assente")

# P7
r = run([FDT, "report", "--days", "1"])
check("P7 report: avviso usage placeholder con versioni viste e conteggi",
      r.returncode == 0 and "placeholder" in r.stdout and "2.1.306" in r.stdout and "2.1.307" in r.stdout,
      r.stdout[-1200:] + r.stderr[-300:])

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
