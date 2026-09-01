#!/usr/bin/env python3
"""Verifica deterministica — parametri economici per modello (1.39.0).

Le parti ECONOMICHE del plugin sono condizionali al modello, le regole
strutturali no: cache_read vale 0,025× su claude-fable-5-1 e 0,1× altrove
(model-economics.json shipped, override utente in
~/.claude/fable-director/model-economics.json). Il modello si legge record
per record (message.model), mai cachato a inizio sessione.

  M1 shipped: eq_mult('claude-fable-5-1').cache_read == 0.025, sonnet 0.1
  M2 prefisso più lungo: id datati/varianti ricadono sul prefisso giusto
  M3 override utente: 'models' si fonde, 'default' per campo; file rotto ignorato
  M4 eq_tokens(cr_by_model=...) pesa ogni quota alla SUA tariffa
  M5 find_usage porta il modello del record (fd-telemetry + stop hook)
  M6 stop hook: state accumula cr_by_model su transcript a due modelli,
     eq nel budget = somma per tariffa, budget-close scrive cache_read_by_model
  M7 session-cost-report: colonna eq per modello e totale = somma righe

Usage: python3 tests/model-economics-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "fable-director"
SCRIPTS = PLUGIN / "scripts"
SCR = PLUGIN / "skills" / "delega-efficiente" / "tools" / "session-cost-report.py"

passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def run(script, args, home, stdin=None, cwd=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([sys.executable, str(script)] + args,
                          capture_output=True, text=True, env=env,
                          input=stdin, timeout=60, cwd=cwd)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def usage_line(model, out=0, inp=0, cr=0, cc=0, ts="2030-01-01T10:00:00Z"):
    return json.dumps({"timestamp": ts, "type": "assistant", "message": {
        "model": model, "usage": {
            "output_tokens": out, "input_tokens": inp,
            "cache_read_input_tokens": cr,
            "cache_creation_input_tokens": cc}}}) + "\n"


# M1 — shipped
home = Path(tempfile.mkdtemp(prefix="fd-econ-home-"))
os.environ["HOME"] = str(home)
fdt = load(SCRIPTS / "fd-telemetry.py", "fdt")
check("M1 shipped: fable 0.025, sonnet 0.1",
      fdt.eq_mult("claude-fable-5-1")["cache_read"] == 0.025
      and fdt.eq_mult("claude-sonnet-5")["cache_read"] == 0.1
      and fdt.eq_mult(None)["cache_read"] == 0.1,
      f"{fdt.eq_mult('claude-fable-5-1')} / {fdt.eq_mult('claude-sonnet-5')}")

# M2 — prefisso più lungo
check("M2 prefisso: variante datata ricade su fable 0.025, fable-5 resta 0.1",
      fdt.eq_mult("claude-fable-5-1-20260901")["cache_read"] == 0.025
      and fdt.eq_mult("claude-fable-5")["cache_read"] == 0.1,
      str(fdt.eq_mult("claude-fable-5")))

# M3 — override utente
udir = home / ".claude" / "fable-director"
udir.mkdir(parents=True)
(udir / "model-economics.json").write_text(json.dumps({
    "default": {"output": 4.0},
    "models": {"claude-haiku-4-5": {"cache_read": 0.05}}}))
fdt2 = load(SCRIPTS / "fd-telemetry.py", "fdt2")
m_h = fdt2.eq_mult("claude-haiku-4-5-20251001")
check("M3a override: modello nuovo dell'utente e default per campo",
      m_h["cache_read"] == 0.05 and m_h["output"] == 4.0
      and fdt2.eq_mult("claude-fable-5-1")["cache_read"] == 0.025,
      str(m_h))
(udir / "model-economics.json").write_text("{not json")
fdt3 = load(SCRIPTS / "fd-telemetry.py", "fdt3")
check("M3b override rotto: ignorato, shipped intatto",
      fdt3.eq_mult("claude-fable-5-1")["cache_read"] == 0.025
      and fdt3.eq_mult("x")["output"] == 5.0)
(udir / "model-economics.json").unlink()

# M4 — cr_by_model
eq = fdt.eq_tokens(0, 0, 0, 0, cr_by_model={"claude-fable-5-1": 100000,
                                            "claude-sonnet-5": 100000})
check("M4 eq per modello: 100k fable + 100k sonnet = 2.500 + 10.000",
      eq == 12500, str(eq))
check("M4b senza cr_by_model: tariffa del modello passato",
      fdt.eq_tokens(0, 0, 100000, 0, model="claude-fable-5-1") == 2500
      and fdt.eq_tokens(0, 0, 100000, 0) == 10000)

# M5 — find_usage porta il modello
rec = json.loads(usage_line("claude-fable-5-1", cr=10))
got = list(fdt.find_usage(rec))
sbc = load(SCRIPTS / "stop-budget-check.py", "sbc")
got2 = list(sbc.find_usage(rec))
check("M5 find_usage → (usage, in_sub, model) / (usage, model)",
      got and got[0][2] == "claude-fable-5-1" and got2 and got2[0][1] == "claude-fable-5-1",
      f"{got} / {got2}")

# M6 — stop hook + budget-close su transcript a due modelli
proj = tempfile.mkdtemp(prefix="fd-econ-proj-")
r = run(SCRIPTS / "fd-telemetry.py",
        ["budget-open", "--task", "econ-test", "--expected-output", "5",
         "--cwd", proj], home)  # 20 out su 5 stimati = 4×: il budget viene riscritto con l'eq
assert r.returncode == 0, r.stderr
tr = Path(tempfile.mkdtemp()) / "sess.jsonl"
tr.write_text(usage_line("claude-fable-5-1", out=10, inp=5, cr=200000, cc=1000)
              + usage_line("claude-sonnet-5", out=10, inp=5, cr=100000, cc=0,
                           ts="2030-01-01T10:01:00Z"))
payload = json.dumps({"cwd": proj, "transcript_path": str(tr),
                      "stop_hook_active": False})
r = run(SCRIPTS / "stop-budget-check.py", [], home, stdin=payload)
bfile = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(proj)}.json"
state = json.loads(bfile.with_name(bfile.stem + ".state.json").read_text())
budget = json.loads(bfile.read_text())
crm = state.get("cr_by_model") or {}
want_eq = int(10 * 1.0 + 20 * 5.0 + 200000 * 0.025 + 100000 * 0.1 + 1000 * 1.25)
check("M6a state.cr_by_model accumula per modello",
      crm.get("claude-fable-5-1") == 200000 and crm.get("claude-sonnet-5") == 100000,
      str(crm))
check("M6b actual_eq_tokens nel budget (bust 3×) = somma per tariffa (16.360)",
      budget.get("actual_eq_tokens") == want_eq,
      f"got {budget.get('actual_eq_tokens')} want {want_eq}")
r = run(SCRIPTS / "fd-telemetry.py", ["budget-close", "--outcome", "flagged",
                                      "--cwd", proj], home)
closed = json.loads(bfile.read_text())
check("M6c budget-close scrive cache_read_by_model ed eq per tariffa",
      closed.get("cache_read_by_model", {}).get("claude-fable-5-1") == 200000
      and closed.get("actual_eq_tokens") == want_eq,
      json.dumps({k: closed.get(k) for k in ("cache_read_by_model", "actual_eq_tokens")}))

# M7 — session-cost-report con colonna eq per modello
pdir = home / ".claude" / "projects" / fdt.cwd_slug(proj).replace(
    fdt.cwd_slug(proj), "-" + proj.strip("/").replace("/", "-").replace(".", "-"))
pdir.mkdir(parents=True, exist_ok=True)
(pdir / "s1.jsonl").write_text(tr.read_text())
r = run(SCR, [], home, cwd=proj)
out = r.stdout
fable_row = [l for l in out.splitlines() if l.startswith("claude-fable-5-1")]
sonnet_row = [l for l in out.splitlines() if l.startswith("claude-sonnet-5")]
tot_row = [l for l in out.splitlines() if l.startswith("TOTALE")]
ok7 = bool(fable_row and sonnet_row and tot_row)
if ok7:
    f_eq = int(fable_row[0].split()[5].replace(".", ""))
    s_eq = int(sonnet_row[0].split()[5].replace(".", ""))
    t_eq = int(tot_row[0].split()[5].replace(".", ""))
    ok7 = (f_eq == int(5 + 50 + 200000 * 0.025 + 1250)
           and s_eq == int(5 + 50 + 100000 * 0.1)
           and t_eq == f_eq + s_eq and "0.025" in fable_row[0])
check("M7 session-cost-report: eq per riga alla tariffa del modello, totale = somma",
      ok7, out[:600] + (r.stderr or ""))

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
