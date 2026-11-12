#!/usr/bin/env python3
"""Verifica deterministica — consiglio di modello ed effort per sessione (1.56.0,
contratto claude-master 1.37 parte A).

  A1 quota 5h ≥ 90% su Fable → Opus 5.5 medium, differs, reason ≤ 200
  A2 settimana ≥ 85% → Opus 5.5 medium
  A3 finestra premium (plan fraction) ≥ 80% su Fable → Opus 5.5 high + /advisor fable
  A4 quota ok, Fable high → nessun cambio, differs false
  A5 effort xhigh/max con quota ≥ 70% → high; sotto il 70% resta (scelta voluta)
  A6 modello non di punta come sessione principale → Opus 5.5
  A7 when: contesto fresco e nessun budget → now; contesto pieno o budget aperto → next_task
  A8 switch_cost_tokens = ctx_tokens; source fable-director; at intero
  A9 quota assente → nessun numero inventato, xhigh resta
  A10 CLI --session riscrive l'advice nello snapshot (scrittura atomica, altre chiavi intatte)
  A11 statusline reale: lo snapshot porta account, cwd e advice
  A12-A18 importanza del lavoro (1.64.0): alta → Fable 5.2 high/xhigh (5.1 fino
      alla 1.64.4), media → Opus 5.5 high, bassa → scelta attuale; quota e
      finestra Fable restano sopra
  A19-A22 fonti: registro di supervisor (cartella piu vicina, 24 h), poi
      «stakes» in .fd-perimeter.json o nel perimeter.json globale
  A23 statusline reale: importanza dal registro nello snapshot
  A24-A27 Fable 5.2 (1.64.5): ogni claude-fable-5-x e di punta; una sessione 5.2
      senza importanza resta su Fable 5.2 (anche [1m]); alta propone Fable 5.2 da
      Opus, da 5.2 e da 5.1; la finestra Fable vale anche da Fable 5.1

Dalla 1.64.5 le sessioni di prova girano su Fable 5.2, il Fable predefinito.

Usage: python3 tests/advice-verify.py   (exit 0 = all green)
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
ADV = SCRIPTS / "advice.py"
# su Windows subprocess cerca prima in System32, dove bash.exe e quello di WSL
BASH = shutil.which("bash") or "bash"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


spec = importlib.util.spec_from_file_location("advice", ADV)
adv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adv)

FABLE51, FABLE, OPUS = "claude-fable-5-1", "claude-fable-5-2", "claude-opus-5-5"
NOW = 1_791_223_253


def snap(model=FABLE, effort="high", ctx=30_000):
    return {"session_id": "s1", "model": model, "effort": effort, "ctx_tokens": ctx}


def q(r=10.0, w=20.0, resets=NOW + 3600):
    return {"five_hour_used_pct": r, "weekly_used_pct": w, "five_hour_resets_at": resets}


a = adv.compute(snap(), q(r=92), {}, False, NOW)
check("A1 5h 92% su Fable → Opus medium", a["model"] == OPUS and a["effort"] == "medium"
      and a["differs"] is True and "5h" in a["reason"] and len(a["reason"]) <= 200, str(a))

a = adv.compute(snap(), q(w=88), {}, False, NOW)
check("A2 settimana 88% → Opus medium", a["model"] == OPUS and a["effort"] == "medium", str(a))

a = adv.compute(snap(), q(w=42), {"premium_weekly_fraction": 0.5}, False, NOW)
check("A3 finestra Fable ≤84% → Opus high + /advisor fable",
      a["model"] == OPUS and a["effort"] == "high" and "/advisor fable" in a["reason"], str(a))

a = adv.compute(snap(), q(), {}, False, NOW)
check("A4 quota ok, Fable high → invariato", a["model"] == FABLE and a["effort"] == "high"
      and a["differs"] is False, str(a))

a = adv.compute(snap(effort="max"), q(w=72), {}, False, NOW)
b = adv.compute(snap(effort="max"), q(r=69, w=50), {}, False, NOW)
check("A5 max con settimana 72% → high; con 5h 69% resta max",
      a["model"] == FABLE and a["effort"] == "high" and a["differs"] is True
      and b["effort"] == "max" and b["differs"] is False, f"{a} {b}")

a = adv.compute(snap(model="claude-sonnet-5-5", effort="medium"), q(), {}, False, NOW)
check("A6 Sonnet come principale → Opus", a["model"] == OPUS and a["differs"] is True, str(a))

a1 = adv.compute(snap(ctx=30_000), q(), {}, False, NOW)
a2 = adv.compute(snap(ctx=300_000), q(), {}, False, NOW)
a3 = adv.compute(snap(ctx=30_000), q(), {}, True, NOW)
check("A7 when now / next_task", a1["when"] == "now" and a2["when"] == "next_task"
      and a3["when"] == "next_task", f"{a1['when']} {a2['when']} {a3['when']}")

check("A8 campi", a2["switch_cost_tokens"] == 300_000 and a2["source"] == "fable-director"
      and a2["at"] == NOW and set(a2) == {"model", "effort", "reason", "switch_cost_tokens",
                                          "at", "source", "when", "differs"}, str(a2))

a = adv.compute(snap(effort="xhigh"), None, None, False, NOW)
check("A9 quota assente → xhigh resta, nessun numero", a["model"] == FABLE
      and a["effort"] == "xhigh" and a["differs"] is False and "%" not in a["reason"], str(a))

# A10 CLI
home = Path(tempfile.mkdtemp(prefix="fd-adv"))
base = home / ".claude" / "fable-director"
(base / "sessions").mkdir(parents=True)
acct = hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]
(base / f"quota-{acct}.json").write_text(json.dumps(q(w=90)))
sf = base / "sessions" / "s1.json"
sf.write_text(json.dumps(dict(snap(), ts=NOW, account=acct, cwd=str(home), extra=1)))
env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))   # Windows: Path.home() = USERPROFILE
env.pop("CLAUDE_CONFIG_DIR", None)
r = subprocess.run([sys.executable, str(ADV), "--session", "s1"], capture_output=True,
                   text=True, env=env, timeout=30)
d = json.loads(sf.read_text())
check("A10 CLI --session scrive advice", r.returncode == 0 and d.get("extra") == 1
      and (d.get("advice") or {}).get("model") == OPUS
      and not list((base / "sessions").glob("*.tmp")), r.stderr[-300:] + str(d))

# A11 statusline reale
payload = {"session_id": "s2", "cwd": str(home), "workspace": {"current_dir": str(home)},
           "model": {"id": FABLE, "display_name": "Fable 5.2"}, "effort": {"level": "max"},
           "context_window": {"context_window_size": 1_000_000,
                              "current_usage": {"input_tokens": 1000,
                                                "cache_read_input_tokens": 20000}},
           "rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW + 3600},
                           "seven_day": {"used_percentage": 30, "resets_at": NOW + 86400}}}
r = subprocess.run([BASH, str(SCRIPTS / "statusline-ctx.sh")], input=json.dumps(payload),
                   capture_output=True, text=True, env=env, timeout=60)
f2 = base / "sessions" / "s2.json"
d2 = json.loads(f2.read_text()) if f2.is_file() else {}
check("A11 statusline: account, cwd, advice", d2.get("account") == acct
      and d2.get("cwd") == str(home) and (d2.get("advice") or {}).get("effort") == "max"
      and (d2.get("advice") or {}).get("when") == "now", r.stderr[-300:] + str(d2))

# --- 1.64.0: importanza del lavoro (asse 2) ---------------------------------
ALTA = {"level": "alta", "why": "ricorso in appello", "source": "task", "task": "acme-ricorso-05-11"}
MEDIA = {"level": "media", "why": None, "source": "task", "task": "t-media"}
BASSA = {"level": "bassa", "why": None, "source": "task", "task": "t-bassa"}

a = adv.compute(snap(model=OPUS, effort="medium"), q(r=10, w=30), {}, False, NOW, importance=ALTA)
check("A12 importanza alta, settimana 30% → Fable 5.2 xhigh, motivo in chiaro",
      a["model"] == FABLE and a["effort"] == "xhigh" and a["differs"] is True
      and "ricorso in appello" in a["reason"] and "importanza alta" in a["reason"]
      and "Fable 5.2 xhigh" in a["reason"], str(a))

a = adv.compute(snap(model=OPUS, effort="medium"), q(r=10, w=60), {}, False, NOW, importance=ALTA)
check("A13 alta, settimana 60% → Fable 5.2 high", a["model"] == FABLE and a["effort"] == "high", str(a))

a = adv.compute(snap(model=OPUS, effort="medium"), q(r=92, w=30), {}, False, NOW, importance=ALTA)
b = adv.compute(snap(model=OPUS, effort="medium"), q(r=10, w=88), {}, False, NOW, importance=ALTA)
check("A14 alta ma quota 5h 92% o settimana 88% → vincono le regole di quota",
      a["model"] == OPUS and a["effort"] == "medium" and b["model"] == OPUS
      and b["effort"] == "medium", f"{a} {b}")

a = adv.compute(snap(effort="max"), q(r=10, w=30), {}, False, NOW, importance=ALTA)
b = adv.compute(snap(effort="max"), q(r=10, w=72), {}, False, NOW, importance=ALTA)
c = adv.compute(snap(effort="xhigh"), q(r=10, w=60), {}, False, NOW, importance=ALTA)
check("A15 alta: max scelto resta sotto il 70%, a 72% → high; xhigh al 60% resta (almeno high)",
      a["effort"] == "max" and a["differs"] is False and b["effort"] == "high"
      and c["effort"] == "xhigh", f"{a['effort']} {b['effort']} {c['effort']}")

a = adv.compute(snap(model=FABLE, effort="high"), q(), {}, False, NOW, importance=MEDIA)
b = adv.compute(snap(model=OPUS, effort="low"), q(), {}, False, NOW, importance=MEDIA)
check("A16 media → Opus 5.5 high (da Fable high e da Opus low)",
      a["model"] == OPUS and a["effort"] == "high" and b["model"] == OPUS
      and b["effort"] == "high" and "importanza media" in a["reason"], f"{a} {b}")

a = adv.compute(snap(model=OPUS, effort="medium"), q(), {}, False, NOW, importance=BASSA)
check("A17 bassa → la scelta attuale, il motivo lo dice",
      a["model"] == OPUS and a["effort"] == "medium" and a["differs"] is False
      and "importanza bassa" in a["reason"], str(a))

a = adv.compute(snap(model=OPUS, effort="medium"), q(r=10, w=42),
                {"premium_weekly_fraction": 0.5}, False, NOW, importance=ALTA)
check("A18 alta ma finestra Fable ≥80% → Opus (effort al massimo high) + /advisor fable",
      a["model"] == OPUS and a["effort"] == "medium" and "/advisor fable" in a["reason"]
      and "ricorso in appello" in a["reason"], str(a))

# --- 1.64.5: Fable 5.2 (claude-fable-5-2) e di punta -------------------------
# Riprodotto il 13/11 sulla 1.64.4: senza importanza «pianifica e verifica: Opus 5.5»,
# con importanza alta Fable 5.1 (10/50 $ per Mtok) invece di Fable 5.2 (8/40 $).
a = adv.compute(snap(model=FABLE, effort="high", ctx=120_000), {"weekly_used_pct": 30}, {},
                False, NOW)
b = adv.compute(snap(model=FABLE + "[1m]", effort="xhigh"), q(), {}, False, NOW)
check("A24 Fable 5.2 senza importanza → resta Fable 5.2 (anche [1m]), niente Opus 5.5",
      a["model"] == FABLE and a["differs"] is False and "Opus" not in a["reason"]
      and "Fable 5.2 high" in a["reason"] and b["model"] == FABLE + "[1m]"
      and b["differs"] is False, f"{a} {b}")

a = adv.compute(snap(model=FABLE, effort="high"), q(r=10, w=30), {}, False, NOW, importance=ALTA)
b = adv.compute(snap(model=OPUS, effort="high"), q(r=10, w=30), {}, False, NOW, importance=ALTA)
check("A25 alta da Fable 5.2 e da Opus → Fable 5.2 xhigh, mai Fable 5.1",
      a["model"] == FABLE and a["effort"] == "xhigh" and "Fable 5.2 xhigh" in a["reason"]
      and "5.1" not in a["reason"] and b["model"] == FABLE and b["differs"] is True, f"{a} {b}")

a = adv.compute(snap(model=FABLE51, effort="high"), q(), {}, False, NOW)
b = adv.compute(snap(model=FABLE51, effort="high"), q(r=10, w=30), {}, False, NOW, importance=ALTA)
check("A26 Fable 5.1 resta di punta (niente Opus); con importanza alta → Fable 5.2",
      a["model"] == FABLE51 and a["differs"] is False and "Opus" not in a["reason"]
      and b["model"] == FABLE and b["differs"] is True and "Fable 5.2" in b["reason"], f"{a} {b}")

a = adv.compute(snap(model=FABLE51), q(w=42), {"premium_weekly_fraction": 0.5}, False, NOW)
check("A27 finestra Fable ≥80% anche da Fable 5.1 → Opus high + /advisor fable",
      a["model"] == OPUS and a["effort"] == "high" and "/advisor fable" in a["reason"], str(a))

# A19-A22: le fonti dell'importanza
t = Path(tempfile.mkdtemp(prefix="fd-imp"))
os.environ["HOME"] = str(t)
adv.BASE = t / ".claude" / "fable-director"
adv.BASE.mkdir(parents=True)
reg = t / "task-importance.json"
os.environ["CM_IMPORTANCE_FILE"] = str(reg)
work = t / "clienti" / "acme" / "contenzioso" / "atto"
work.mkdir(parents=True)
reg.write_text(json.dumps({"version": 1, "updated_at": NOW, "dirs": {
    str(t / "clienti" / "acme"): {"importance": "media", "task": "acme-generale", "why": "generale",
                                     "source": "declared", "state": "open", "last_step_at": NOW},
    str(t / "clienti" / "acme" / "contenzioso").replace("/", "\\").upper():
        {"importance": "alta", "task": "acme-ricorso", "why": "ricorso in appello",
         "source": "declared", "state": "running", "last_step_at": NOW}}}))
i = adv.importance_for(str(work), NOW)
check("A19 compito attivo: vince la cartella piu vicina, \\ e maiuscole indifferenti",
      i and i["level"] == "alta" and i["task"] == "acme-ricorso" and i["source"] == "task", str(i))

reg.write_text(json.dumps({"version": 1, "updated_at": NOW - 2 * 86400, "dirs": {
    str(t / "privato"): {"importance": "bassa", "task": "x"}}}))
(t / "clienti" / "acme" / ".fd-perimeter.json").write_text(json.dumps(
    {"never_write": [], "stakes": {"level": "alta", "why": "pratica legale"}}))
i = adv.importance_for(str(work), NOW)
check("A20 registro vecchio di 2 giorni ignorato; cartella dichiarata sopra → alta",
      i and i["level"] == "alta" and i["source"] == "folder" and i["why"] == "pratica legale", str(i))
a = adv.compute(snap(model=OPUS, effort="medium"), q(r=10, w=30), {}, False, NOW, importance=i)
check("A20b motivo in chiaro: «pratica legale ... Fable 5.2 xhigh»",
      a["reason"].startswith("pratica legale") and "Fable 5.2 xhigh" in a["reason"], a["reason"])

reg.write_text(json.dumps({"version": 1, "updated_at": NOW, "dirs": {
    str(t / "clienti" / "acme" / "contenzioso"): {"importance": "bassa", "task": "fattura"}}}))
i = adv.importance_for(str(work), NOW)
check("A21 il compito batte la cartella dichiarata (bassa su cartella alta)",
      i and i["level"] == "bassa" and i["source"] == "task", str(i))

reg.unlink()
(t / "clienti" / "acme" / ".fd-perimeter.json").unlink()
(adv.BASE / "perimeter.json").write_text(json.dumps(
    {"stakes": {str(t / "fornitori" / "beta"): "alta"}}))
(t / "fornitori" / "beta" / "prod").mkdir(parents=True)
i1 = adv.importance_for(str(t / "fornitori" / "beta" / "prod"), NOW)
i2 = adv.importance_for(str(t / "privato"), NOW)
check("A22 senza registro: «stakes» nel perimeter.json globale; altrove nessuna importanza",
      i1 and i1["level"] == "alta" and i1["source"] == "folder" and i2 is None, f"{i1} {i2}")

# A23 statusline reale: lo snapshot porta il consiglio di importanza
reg.write_text(json.dumps({"version": 1, "updated_at": int(time.time()), "dirs": {
    str(home): {"importance": "alta", "task": "acme-ricorso", "why": "ricorso in appello"}}}))
payload3 = dict(payload, session_id="s3", model={"id": OPUS, "display_name": "Opus 5.5"},
                effort={"level": "medium"})
env3 = dict(env, USERPROFILE=str(home), CM_IMPORTANCE_FILE=str(reg), FD_SL_HEAVY_S="0")
r = subprocess.run([BASH, str(SCRIPTS / "statusline-ctx.sh")], input=json.dumps(payload3),
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=env3, timeout=60)
f3 = base / "sessions" / "s3.json"
d3 = json.loads(f3.read_text()) if f3.is_file() else {}
ad = d3.get("advice") or {}
check("A23 statusline: importanza alta dal registro → Fable 5.2 nello snapshot",
      ad.get("model") == FABLE and ad.get("effort") in ("high", "xhigh")
      and "ricorso in appello" in (ad.get("reason") or ""), r.stderr[-300:] + str(d3))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
