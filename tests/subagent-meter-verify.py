#!/usr/bin/env python3
"""Verifica del misuratore deleghe (subagent-meter.py, hook SubagentStart/Stop).

Deterministica, HOME usa-e-getta. Contratto:
- SubagentStart registra l'agent in volo e incrementa i totali per tipo;
- SubagentStop lo toglie dal volo e incrementa gli stop;
- 12 start CONCORRENTI non perdono conteggi (lock + rename atomico): è lo
  scenario reale del fan-out di un workflow, e senza lock l'ultimo writer
  vince;
- effort reale ≠ effort pinnato nel frontmatter → `effort_ignored` in stato
  E in telemetria: è il degrado che il README elencava come silenzioso;
- agent non nostro (nessun frontmatter) → nessun mismatch inventato;
- stdin spazzatura o evento sconosciuto → exit 0, nessuno stato, nessun crash:
  un misuratore non deve mai rompere la sessione che misura;
- hooks.json dichiara davvero i due eventi verso questo script.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director"
SCRIPT = ROOT / "scripts" / "subagent-meter.py"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(home, payload, wait=True):
    e = dict(os.environ, HOME=str(home))
    e.pop("CLAUDE_CONFIG_DIR", None)
    p = subprocess.Popen([sys.executable, str(SCRIPT)], env=e,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    if not wait:
        p.stdin.write(json.dumps(payload))
        p.stdin.close()
        return p
    o, err = p.communicate(json.dumps(payload), timeout=30)
    return p.returncode, o, err


def state(home, sid="s1"):
    f = home / ".claude" / "fable-director" / "subagents" / f"{sid}.json"
    return json.loads(f.read_text()) if f.is_file() else None


def start(sid="s1", aid="a1", atype="Explore"):
    return {"hook_event_name": "SubagentStart", "session_id": sid,
            "agent_id": aid, "agent_type": atype, "cwd": "/tmp"}


def stop(sid="s1", aid="a1", atype="Explore", effort=None):
    d = {"hook_event_name": "SubagentStop", "session_id": sid,
         "agent_id": aid, "agent_type": atype, "cwd": "/tmp"}
    if effort:
        d["effort"] = {"level": effort}
    return d


tmp = Path(tempfile.mkdtemp(prefix="fd-submeter-test"))
home = tmp / "home"
(home / ".claude" / "fable-director").mkdir(parents=True)

print("misuratore deleghe (SubagentStart/SubagentStop):")

# M1: start registra il volo
rc, _, err = run(home, start())
st = state(home)
check("M1 SubagentStart → 1 in volo, totale per tipo",
      rc == 0 and st and len(st["inflight"]) == 1
      and st["started"] == 1 and st["by_type"].get("Explore") == 1,
      f"rc={rc} st={st} err={err}")

# M2: stop lo toglie dal volo
run(home, stop())
st = state(home)
check("M2 SubagentStop → volo vuoto, stopped=1",
      st and st["inflight"] == {} and st["stopped"] == 1, str(st))

# M3: 12 start concorrenti, nessun conteggio perso
procs = [run(home, start(sid="s2", aid=f"a{i}", atype="fd-executor"),
             wait=False) for i in range(12)]
for p in procs:
    p.wait(timeout=30)
st2 = state(home, "s2")
check("M3 12 start concorrenti → 12 contati, 12 in volo",
      st2 and st2["started"] == 12 and len(st2["inflight"]) == 12,
      str(st2))

# M4: effort reale ≠ pinnato → evento misurato (stato + telemetria)
run(home, start(sid="s3", aid="x1", atype="fable-director:fd-executor"))
run(home, stop(sid="s3", aid="x1", atype="fable-director:fd-executor",
               effort="xhigh"))
st3 = state(home, "s3")
db = home / ".claude" / "fable-director" / "telemetry.db"
rows = []
if db.is_file():
    con = sqlite3.connect(db)
    rows = list(con.execute(
        "SELECT payload FROM events WHERE event='effort_ignored'"))
    con.close()
check("M4 effort pinnato low, reale xhigh → effort_ignored in stato",
      st3 and st3["effort_ignored"] == 1
      and (st3.get("last_effort_ignored") or {}).get("pinned") == "low"
      and (st3.get("last_effort_ignored") or {}).get("actual") == "xhigh",
      str(st3))
check("M5 effort_ignored anche in telemetria (lo scrive un hook, non il modello)",
      len(rows) == 1 and "xhigh" in rows[0][0], str(rows))

# M6: agent non nostro → nessun mismatch inventato
run(home, start(sid="s4", aid="y1", atype="general-purpose"))
run(home, stop(sid="s4", aid="y1", atype="general-purpose", effort="max"))
st4 = state(home, "s4")
check("M6 agent senza frontmatter → nessun effort_ignored",
      st4 and st4["effort_ignored"] == 0, str(st4))

# M7: input spazzatura / evento ignoto → exit 0 e nessuno stato nuovo
p = subprocess.Popen([sys.executable, str(SCRIPT)],
                     env=dict(os.environ, HOME=str(home)),
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True)
o, e2 = p.communicate("non-json{{", timeout=30)
rc2 = p.returncode
rc3, _, _ = run(home, {"hook_event_name": "Notification", "session_id": "s9"})
check("M7 stdin non-JSON → exit 0, muto", rc2 == 0 and o == "", f"rc={rc2} o={o!r}")
check("M8 evento non gestito → exit 0, nessun file di stato",
      rc3 == 0 and state(home, "s9") is None)

# M9: hooks.json dichiara i due eventi verso questo script
hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
declared = {ev: json.dumps(hooks.get(ev, [])) for ev in
            ("SubagentStart", "SubagentStop")}
check("M9 hooks.json monta il misuratore su Start e Stop",
      all("subagent-meter.py" in v for v in declared.values()),
      str(declared))

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto misuratore deleghe rispettato")
