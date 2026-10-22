#!/usr/bin/env python3
"""Verifica deterministica — un messaggio su piu' righe jsonl conta una volta.
Claude Code scrive ogni blocco di un messaggio assistant (thinking, text,
tool_use) su una riga propria e ripete l'usage su ognuna: uguale nel main,
crescente negli agenti in streaming. Per message.id vale il massimo per chiave.

  D1 sum_transcript: tre righe dello stesso messaggio + un messaggio a riga singola → totali e n_usage_records per messaggio
  D2 sum_transcript: l'aggregato annidato sotto toolUseResult conta una volta per record, fuori dalla deduplica
  D3 sum_transcript: righe senza message.id ne' requestId contano riga per riga (nessun criterio per unirle)
  D4 sum_workflow_agents: usage crescente fra le righe di un messaggio → output finale, cache_read una volta
  D5 stop-budget-check scan_jsonl: messaggio spezzato fra due Stop → contato una volta, memoria dei mid limitata
  D6 session-summary: payload con usage_dedup 1 e output deduplicato

Usage: python3 tests/usage-dedup-verify.py   (exit 0 = all green)
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


home = Path(tempfile.mkdtemp(prefix="fd-dd-home-"))
proj = Path(tempfile.mkdtemp(prefix="fd-dd-proj-"))
os.environ["HOME"] = str(home)
os.environ.pop("CLAUDE_CONFIG_DIR", None)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fdt = load("fdt_dd", SCRIPTS / "fd-telemetry.py")
sbc = load("sbc_dd", SCRIPTS / "stop-budget-check.py")
FDT = str(SCRIPTS / "fd-telemetry.py")


def asst(mid, usage, block="text", ts="2030-01-01T10:00:00Z", side=False):
    msg = {"id": mid, "model": "claude-sonnet-5-5", "role": "assistant",
           "content": [{"type": block}], "usage": usage}
    if mid is None:
        msg.pop("id")
    return json.dumps({"type": "assistant", "isSidechain": side,
                       "timestamp": ts, "message": msg})


def u(inp, out, cr, cc):
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}


A = u(5, 50, 1000, 200)
B = u(3, 20, 1200, 100)
main_lines = [asst("m_a", A, "thinking"), asst("m_a", A, "text"),
              asst("m_a", A, "tool_use"), asst("m_b", B)]

# D1
t1 = proj / "d1.jsonl"
t1.write_text("\n".join(main_lines) + "\n")
main, sub, n_sub, *_, stats = fdt.sum_transcript(t1)
want = {"input_tokens": 8, "output_tokens": 70,
        "cache_read_input_tokens": 2200, "cache_creation_input_tokens": 300}
check("D1 sum_transcript: totali per messaggio, n_usage_records 2",
      all(main[k] == v for k, v in want.items()) and stats.get("n_usage_records") == 2
      and (stats.get("cache_read_by_model") or {}).get("claude-sonnet-5-5") == 2200,
      f"main={main} stats.n_usage_records={stats.get('n_usage_records')} crm={stats.get('cache_read_by_model')}")

# D2
tool_res = json.dumps({"type": "user", "timestamp": "2030-01-01T10:00:09Z",
                       "message": {"role": "user", "content": [{"type": "tool_result"}]},
                       "toolUseResult": {"usage": u(7, 30, 500, 40)}})
t2 = proj / "d2.jsonl"
t2.write_text("\n".join(main_lines + [tool_res]) + "\n")
main2, sub2, n_sub2, *_ = fdt.sum_transcript(t2)
check("D2 sum_transcript: aggregato toolUseResult contato una volta, main invariato",
      sub2["output_tokens"] == 30 and sub2["input_tokens"] == 7 and n_sub2 == 1
      and main2["output_tokens"] == 70, f"sub={sub2} n_sub={n_sub2} main={main2}")

# D3
t3 = proj / "d3.jsonl"
t3.write_text("\n".join([asst(None, B), asst(None, B)]) + "\n")
main3, *_ = fdt.sum_transcript(t3)
check("D3 sum_transcript: righe senza id contano riga per riga",
      main3["output_tokens"] == 40, f"main={main3}")

# D4
sdir = home / ".claude" / "projects" / "-proj" / "sess-dd"
tr_main = sdir.with_suffix(".jsonl")
wdir = sdir / "subagents" / "workflows" / "wf_dd"
wdir.mkdir(parents=True)
tr_main.write_text("\n".join(main_lines) + "\n")
growing = [asst("w_1", u(4, 1, 900, 300), "thinking", side=True),
           asst("w_1", u(4, 1, 900, 300), "text", side=True),
           asst("w_1", u(4, 120, 900, 300), "tool_use", side=True),
           asst("w_2", u(2, 60, 1500, 50), side=True)]
(wdir / "agent-w1.jsonl").write_text("\n".join(growing) + "\n")
tot, n = fdt.sum_workflow_agents(tr_main)
check("D4 sum_workflow_agents: output finale per messaggio, cache_read una volta",
      n == 1 and tot["output_tokens"] == 180 and tot["cache_read_input_tokens"] == 2400
      and tot["input_tokens"] == 6 and tot["cache_creation_input_tokens"] == 350,
      json.dumps(tot))

# D5
t5 = proj / "d5.jsonl"
t5.write_text("\n".join(growing[:2]) + "\n")
st = {"off": 0, "out": 0, "inp": 0, "cr": 0, "cc": 0, "last_ts": None}
sbc.scan_jsonl(t5, st, None)
first_out = st["out"]
with open(t5, "a") as fh:
    fh.write("\n".join(growing[2:]) + "\n")
st = json.loads(json.dumps(st))  # come fra due Stop: lo state passa da disco
sbc.scan_jsonl(t5, st, None)
check("D5a scan_jsonl: messaggio spezzato fra due Stop contato una volta",
      first_out == 1 and st["out"] == 180 and st["cr"] == 2400 and st["inp"] == 6 + 350,
      f"primo giro out={first_out} state={ {k: st.get(k) for k in ('out', 'inp', 'cr', 'cc')} }")
mem = {}
for i in range(sbc.MIDS_KEEP + 10):
    sbc.usage_inc(mem, f"m{i}", u(1, 1, 1, 1))
check("D5b usage_inc: memoria limitata agli ultimi MIDS_KEEP messaggi",
      len(mem["mids"]) == sbc.MIDS_KEEP and "m0" not in mem["mids"]
      and f"m{sbc.MIDS_KEEP + 9}" in mem["mids"], f"{len(mem['mids'])} mid")

# D6
env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
env.pop("CLAUDE_CONFIG_DIR", None)
env.pop("CLAUDE_CODE_SESSION_ID", None)
r = subprocess.run([sys.executable, FDT, "session-summary", "--transcript", str(tr_main),
                    "--session-id", "sess-dd", "--cwd", str(proj)],
                   capture_output=True, text=True, env=env, timeout=60)
db = home / ".claude" / "fable-director" / "telemetry.db"
rows = []
if db.is_file():
    con = sqlite3.connect(db)
    rows = [json.loads(p) for (p,) in con.execute(
        "select payload from events where event='session_summary' order by ts, id")]
    con.close()
s = rows[-1] if rows else {}
check("D6 session-summary: usage_dedup 1, output main 70 + agenti 180",
      r.returncode == 0 and s.get("usage_dedup") == 1 and s.get("output_tokens") == 250
      and s.get("main_output") == 70, f"rc={r.returncode} {s} err={r.stderr[-300:]}")

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
