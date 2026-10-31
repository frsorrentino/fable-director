#!/usr/bin/env python3
"""Verifica deterministica — account senza il limite di 5 ore: gate, statusline e testi
usano il ritmo settimanale. Il dato e' SCRITTO A MANO: nessun account lo riceve ancora,
quindi questo test prova la logica sulla forma attesa, non e' una misura.

  P1-3 dato assente, null, valore vecchio con reset scaduto: fan-out oltre il residuo → deny
  P4   file fermo da 30 min senza 5 ore: il deny resta (dentro la settimana la quota non scende)
  P5   valore scaduto su file fermo: sessione inattiva, nessun controllo (come prima)
  P6   a meno di un giorno dal reset il residuo e' tutto il restante, senza ingrandirlo
  P7   fan-out dentro il residuo: passa, senza avvisi
  P8   resume oltre il residuo: mai deny, avviso
  P9   consumo orario: 6%/h avvisa, 3%/h no
  P10  settimana quasi finita, agenti non dichiarati: il quota guard nega
  P11  settimana senza dati: avviso, nessun deny
  P12  failover: propone la rotta esterna quando il consumo e' sopra soglia
  P13  precedenza di un incidente: tiene i fan-out quando il consumo e' sopra soglia
  P14  statusline plain ed expert senza 5 ore; rate_limits null non la rompe
  P15  /status: riga del ritmo, nessuna riga cinque ore
  P16  advice: nessun «5h» nelle motivazioni, consumo alto → Opus medium
  P17  route-hint: settimana esaurita senza parlare di cinque ore
  P18  taratura: capacita' settimanale di default, override, misura dalle coppie
  I1-6 account con le 5 ore: stessi testi e stessi esiti di prima, anche con settimana
       quasi finita e consumo alto

Usage: python3 tests/gate-senza-cinque-ore-verify.py   (exit 0 = all green)
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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
FDT, GATE = str(SCRIPTS / "fd-telemetry.py"), str(SCRIPTS / "pre-delegation-gate.py")
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-p-home-"))
proj = tempfile.mkdtemp(prefix="fd-p-proj-")
os.environ["HOME"] = str(home)
os.environ.pop("CLAUDE_CONFIG_DIR", None)
BASE = home / ".claude" / "fable-director"
BASE.mkdir(parents=True)
ACCT = hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]
QUOTA = BASE / f"quota-{ACCT}.json"
HIST = BASE / f"quota-history-{ACCT}.jsonl"
SID = "s-pace"
NOW = time.time()
WEEK_RESET = int(NOW + 3 * 86400)


def env_():
    e = dict(os.environ, HOME=str(home), USERPROFILE=str(home), COLUMNS="140")
    for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "FD_STATUSLINE_MODE"):
        e.pop(k, None)
    return e


def run(args, stdin=None, sid=None):
    e = env_()
    if sid:
        e["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable] + args, capture_output=True, text=True, env=e,
                          input=stdin, timeout=90)


def quota(shape="absent", weekly=40.0, week_reset=WEEK_RESET, five=None, age=0):
    q = {"weekly_used_pct": weekly, "weekly_resets_at": week_reset}
    if weekly is None:
        q = {}
    if shape == "null":
        q["five_hour_used_pct"] = None
    elif shape == "expired":
        q.update(five_hour_used_pct=97.0, five_hour_resets_at=int(NOW - 3600))
    elif shape == "live":
        q.update(five_hour_used_pct=five, five_hour_resets_at=int(NOW + 3600))
    QUOTA.write_text(json.dumps(q))
    if age:
        os.utime(QUOTA, (NOW - age, NOW - age))


def history(rate_h):
    """Campioni dell'ultima ora a consumo costante rate_h %/h."""
    t0 = datetime.now(timezone.utc)
    rows = [{"ts": (t0 - timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "w": round(40 - rate_h * m / 60, 2), "r": None} for m in (50, 25, 0)]
    HIST.write_text("".join(json.dumps(r) + "\n" for r in rows))


def budget(agents=16, **kw):
    run([FDT, "budget-close", "--outcome", "abandoned", "--cwd", proj])
    a = [FDT, "budget-open", "--verify", "test -d .", "--task", kw.pop("task", "pannello"),
         "--expected-output", "100", "--route", "workflow", "--cost-ack",
         "--paths", "none", "--cwd", proj]
    if agents:
        a += ["--agents", str(agents)]
    for k, v in kw.items():
        a += [f"--{k.replace('_', '-')}", str(v)]
    r = run(a, sid=SID)
    assert r.returncode == 0, r.stdout + r.stderr


def settle():
    for b in (BASE / "budgets").glob("*.json"):
        d = json.loads(b.read_text())
        if isinstance(d, dict) and d.pop("reserve", None) is not None:
            b.write_text(json.dumps(d))


def gate(tool="Workflow", ti=None):
    settle()
    data = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": proj, "session_id": SID,
            "tool_input": ti if ti is not None else {"script": "export const meta = {name:'x',description:'x'}"}}
    r = run([GATE], json.dumps(data), sid=SID)
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    hs = out.get("hookSpecificOutput") or {}
    reason = hs.get("permissionDecisionReason", "") if hs.get("permissionDecision") == "deny" else None
    return reason, out.get("systemMessage", ""), r.stdout + r.stderr


budget()
# 40% usato, reset fra 3 giorni: residuo 20%/giorno. Capacita' di default 3,5M/0,245 = 14,3M eq,
# quindi residuo ≈ 2,9M eq contro 16 × 330k = 5,3M eq.
for name, shape in (("P1 dato assente", "absent"), ("P2 dato null", "null"), ("P3 reset scaduto", "expired")):
    quota(shape)
    reason, msg, raw = gate()
    check(f"{name}: deny sul ritmo, senza nominare le cinque ore",
          bool(reason) and "weekly pace" in reason and "20.0%/day" in reason
          and not re.search(r"five.hour|\b5h\b", reason, re.I), raw)

quota("absent", age=1800)
reason, _, raw = gate()
check("P4 file fermo da 30 min senza 5 ore: deny", bool(reason) and "weekly pace" in reason, raw)

quota("expired", age=1800)
reason, msg, raw = gate()
check("P5 valore scaduto su file fermo: nessun controllo", reason is None and "weekly pace" not in msg, raw)

quota("absent", week_reset=int(NOW + 7200))
reason, msg, raw = gate()
check("P6 due ore al reset: residuo = 60% del restante, 16 agenti entrano",
      reason is None and "weekly pace" not in msg, raw)

budget(agents=5)
quota("absent")
reason, msg, raw = gate()
check("P7 5 agenti dentro il residuo: passa, nessun avviso", reason is None and "weekly pace" not in msg, raw)

budget(agents=16)
reason, msg, raw = gate(ti={"resumeFromRunId": "wf_abc123", "script": "x"})
check("P8 resume oltre il residuo: mai deny, avviso", reason is None and "weekly pace" in msg, raw)

budget(agents=2)
quota("absent"); history(6)
reason, msg, raw = gate()
check("P9a 6%/h: avvisa con i numeri e il rimando al ritmo",
      reason is None and "6.0%/h" in msg and "5%/h" in msg and "weekly pace" in msg, raw)
history(3)
reason, msg, raw = gate()
check("P9b 3%/h: nessun avviso", reason is None and "%/h" not in msg, raw)
reason, msg, raw = gate(tool="Agent", ti={"subagent_type": "fable-director:fd-executor", "prompt": "x" * 10,
                                          "description": "d"})
check("P9c Agent a 3%/h: passa", reason is None, raw)
history(6)
reason, msg, raw = gate(tool="Agent", ti={"subagent_type": "fable-director:fd-executor", "prompt": "x" * 10,
                                          "description": "d"})
check("P9d Agent a 6%/h: avvisa anche lui", reason is None and "6.0%/h" in msg, raw)
HIST.unlink()

budget(agents=0)
quota("absent", weekly=99.5)
reason, _, raw = gate()
check("P10 settimana quasi finita, agenti non dichiarati: deny",
      bool(reason) and "weekly pace" in reason and not re.search(r"five.hour", reason, re.I), raw)

quota("absent", weekly=None)
reason, msg, raw = gate()
check("P11 nessun dato di quota: avviso, nessun deny",
      reason is None and "no live quota" in msg, raw)

# P12 failover
(BASE / "cross-family.json").write_text(json.dumps({"default": "gemini", "providers": {
    "gemini": {"type": "http", "billing": "free", "model": "g", "limits": {"rpd": 100}}}}))
budget(agents=2, type="classify", effort="low", data_class="internal")
quota("absent"); history(6)
reason, msg, raw = gate(tool="Agent", ti={"subagent_type": "fable-director:fd-executor", "prompt": "x" * 10,
                                          "description": "d"})
check("P12a failover sopra soglia: propone la rotta esterna, senza cinque ore",
      reason is None and "external-exec" in msg and "FD ⇄ weekly pace" in msg
      and not re.search(r"five.hour", msg, re.I), raw)
history(1)
reason, msg, raw = gate(tool="Agent", ti={"subagent_type": "fable-director:fd-executor", "prompt": "x" * 10,
                                          "description": "d"})
check("P12b sotto soglia: nessuna proposta", reason is None and "external-exec" not in msg, raw)
(BASE / "cross-family.json").unlink()

# P13 precedenza di un incidente in un'altra sessione
inc = tempfile.mkdtemp(prefix="fd-p-inc-")
r = run([FDT, "budget-open", "--verify", "test -d .", "--task", "sito giu", "--expected-output", "500",
         "--priority", "incident", "--cwd", inc], sid="s-inc")
assert r.returncode == 0, r.stderr
budget(agents=2)
quota("absent"); history(6)
reason, _, raw = gate()
check("P13a incidente e consumo alto: fan-out tenuto", bool(reason) and "another session" in reason
      and not re.search(r"five.hour", reason, re.I), raw)
history(1)
reason, _, raw = gate()
check("P13b incidente e consumo basso: passa", reason is None, raw)
run([FDT, "budget-close", "--outcome", "abandoned", "--cwd", inc], sid="s-inc")
HIST.unlink()


# P14 statusline
def plain(s):
    return re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b\[[0-9;]*m", "", s)


def render(limits, expert=False):
    cfg = BASE / "statusline.json"
    if expert:
        cfg.write_text(json.dumps({"mode": "expert"}))
    elif cfg.exists():
        cfg.unlink()
    d = {"model": {"display_name": "Fable 5.1"}, "session_id": "s-sl", "cwd": proj,
         "context_window": {"used_percentage": 26, "context_window_size": 200000}, "rate_limits": limits}
    r = subprocess.run(["bash", str(SCRIPTS / "statusline-ctx.sh")], input=json.dumps(d), capture_output=True,
                       text=True, env=env_(), timeout=120)
    if cfg.exists():
        cfg.unlink()
    return plain(r.stdout), r.stderr


week = {"used_percentage": 40, "resets_at": WEEK_RESET}
QUOTA.unlink()
for name, limits in (("assente", {"seven_day": week}), ("null", {"five_hour": None, "seven_day": week}),
                     ("scaduto", {"five_hour": {"used_percentage": 97, "resets_at": int(NOW - 3600)},
                                  "seven_day": week})):
    QUOTA.unlink(missing_ok=True)
    out, err = render(limits)
    check(f"P14 plain, 5 ore {name}: settimana e ritmo, nessun «quota N%, resets»",
          re.search(r"week 40%, pace 20%/day", out) and "quota 40" not in out and "quota 97" not in out
          and "context 26%" in out, out + err)
    QUOTA.unlink(missing_ok=True)
    out, err = render(limits, expert=True)
    check(f"P14 expert, 5 ore {name}: «pace 20%/d», nessun 5H", "pace 20%/d" in out and "5H" not in out
          and "7D 40%" in out, out + err)
out, err = render(None)
check("P14 rate_limits null: la statusline regge", "context 26%" in out, out + err)
history(6)
QUOTA.unlink(missing_ok=True)
out, err = render({"seven_day": week})
check("P14 consumo alto: la riga lo dice a parole", "6.0%/h" in out and "above" in out, out + err)
HIST.unlink()


# P15 /status
def status():
    return run([str(SCRIPTS / "fd-status.py")], sid=SID).stdout


quota("absent")
o = status()
check("P15a /status senza 5 ore: ritmo, nessuna riga cinque ore",
      re.search(r"weekly pace", o) and "20.0%/day" in o and not re.search(r"five-hour quota", o), o)
quota("expired")
o = status()
check("P15b /status con reset scaduto: nessun 97%", "97%" not in o and "weekly pace" in o, o)

# P16 advice
sys.path.insert(0, str(SCRIPTS))
sp = importlib.util.spec_from_file_location("advice_t", SCRIPTS / "advice.py")
adv = importlib.util.module_from_spec(sp); sp.loader.exec_module(adv)
snap = {"model": "claude-opus-5-5", "effort": "xhigh", "ctx_tokens": 100}
qa = {"weekly_used_pct": 75, "weekly_resets_at": WEEK_RESET}
a = adv.compute(snap, qa, {}, False, NOW)
check("P16a motivazione senza «5h»", "5h" not in a["reason"] and "settimana 75%" in a["reason"], a)
qe = dict(qa, five_hour_used_pct=97, five_hour_resets_at=int(NOW - 3600))
a = adv.compute(snap, qe, {}, False, NOW)
check("P16b valore scaduto: non conta come 97%", "5h" not in a["reason"] and "97" not in a["reason"], a)
a = adv.compute({"model": "claude-opus-5-5", "effort": "high", "ctx_tokens": 100},
                {"weekly_used_pct": 30, "weekly_resets_at": WEEK_RESET}, {}, False, NOW, burn=6.0)
check("P16c 6%/h: Opus medium con la ragione del ritmo",
      a["effort"] == "medium" and "6.0%/h" in a["reason"], a)
a = adv.compute({"model": "claude-opus-5-5", "effort": "high", "ctx_tokens": 100},
                {"weekly_used_pct": 30, "weekly_resets_at": WEEK_RESET}, {}, False, NOW)
check("P16d a riposo: quota ok col ritmo", "23.3%/g" in a["reason"] and "5h" not in a["reason"], a)
check("I1 advice con le 5 ore: testo di sempre",
      "quota 5h al 95%" in adv.compute(snap, dict(qa, five_hour_used_pct=95, five_hour_resets_at=int(NOW + 600)),
                                       {}, False, NOW, burn=9.0)["reason"])
# una finestra viva tiene fuori il ramo del consumo
a = adv.compute({"model": "claude-opus-5-5", "effort": "high", "ctx_tokens": 100},
                {"weekly_used_pct": 30, "five_hour_used_pct": 10, "five_hour_resets_at": int(NOW + 600)},
                {}, False, NOW, burn=9.0)
check("I2 advice con le 5 ore: il consumo orario non cambia l'esito",
      a["effort"] == "high" and a["reason"].startswith("quota ok (5h 10%, settimana 30%)"), a)


# P17 route-hint
def hint():
    d = {"hook_event_name": "UserPromptSubmit", "prompt": "riscrivi il modulo di pagamento adesso", "cwd": proj,
         "session_id": "s-rh"}
    (BASE / "route-hint-seen").mkdir(exist_ok=True)
    return run([str(SCRIPTS / "route-hint.py")], json.dumps(d)).stdout


quota("absent", weekly=99)
o = hint()
check("P17a settimana esaurita senza 5 ore: lo dice, niente cinque ore",
      "weekly" in o and "99%" in o and "five-hour" not in o, o)
quota("absent", weekly=50)
check("P17b settimana a meta': nessuna riga quota", "weekly limit" not in hint())

# P18 taratura
spec = importlib.util.spec_from_file_location("fdt", FDT)
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)
cal = fdt.workflow_calibration()
check("P18a default: finestra ÷ 0,245", abs(cal.get("weekly_eq", 0) - 3_500_000 / 0.245) < 1
      and "24.5" in cal.get("weekly_src", ""), cal)
(BASE / "cost-checkpoint.json").write_text(json.dumps({"weekly_eq": 9_000_000, "cost_ack": 0}))
cal = fdt.workflow_calibration()
check("P18b override weekly_eq", cal.get("weekly_eq") == 9_000_000, cal)
(BASE / "cost-checkpoint.json").unlink()
con = sqlite3.connect(fdt.DB_PATH)
con.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT, event TEXT, session_id TEXT, payload TEXT)")
t0 = datetime.now(timezone.utc) - timedelta(hours=20)
cols = [c[1] for c in con.execute("PRAGMA table_info(events)")]
for i in range(7):
    row = {"ts": (t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": "delegation_outcome",
           "session_id": "s-cal", "payload": json.dumps({"agent_type": "workflow-subagent", "eq": 1_000_000,
                                                         "weekly_pct": 10 + 2 * i})}
    con.execute(f"INSERT INTO events ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
con.commit(); con.close()
cal = fdt.workflow_calibration()
check("P18c misura: 1M eq ogni 2% = 50M eq", cal.get("weekly_eq") == 50_000_000 and "measured" in cal.get("weekly_src", ""), cal)
con = sqlite3.connect(fdt.DB_PATH); con.execute("DELETE FROM events WHERE session_id='s-cal'"); con.commit(); con.close()

# Account con le 5 ore: invariati, anche con la settimana quasi finita e il consumo alto
HIST.unlink(missing_ok=True)
budget(agents=16, expected_input=0)
history(9)
quota("live", weekly=99.5, five=95)
reason, _, raw = gate()
check("I3 5 ore al 95%: quota guard di sempre",
      bool(reason) and "quota guard — five-hour window at 95% used (ceiling 90%)" in reason, raw)
quota("live", weekly=99.5, five=60)
reason, _, raw = gate()
check("I4 5 ore al 60%, 16 agenti: window fit di sempre",
      bool(reason) and "cannot finish in the current five-hour window" in reason and "weekly pace" not in reason, raw)
quota("live", weekly=99.5, five=22)
reason, msg, raw = gate()
check("I5 5 ore al 22%: avviso di sempre, nessun ritmo",
      reason is None and "window fit tight" in msg and "1.9×" in msg and "weekly pace" not in msg and "%/h" not in msg, raw)
quota("live", weekly=99.5, five=5)
reason, msg, raw = gate()
check("I6 5 ore al 5%, settimana 99,5% e 9%/h: nessun deny e nessun avviso sul ritmo",
      reason is None and "weekly pace" not in msg and "%/h" not in msg, raw)
HIST.unlink(missing_ok=True)
QUOTA.unlink(missing_ok=True)
live = {"five_hour": {"used_percentage": 40, "resets_at": int(NOW + 2400)}, "seven_day": {"used_percentage": 46, "resets_at": WEEK_RESET}}
out, err = render(live)
check("I7 statusline con le 5 ore: «quota 40%, resets HH:MM», nessun ritmo",
      re.search(r"quota 40%, resets \d{2}:\d{2}", out) and "pace" not in out and "week " not in out, out + err)
QUOTA.unlink(missing_ok=True)
out, err = render(live, expert=True)
check("I8 expert con le 5 ore: 5H 40%, nessun ritmo", "5H 40%" in out and "pace" not in out, out + err)
quota("live", weekly=40, five=33)
o = status()
check("I9 /status con le 5 ore: riga cinque ore, nessun ritmo", "five-hour quota" in o and "33%" in o and "weekly pace" not in o, o)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
