#!/usr/bin/env python3
"""Verifica deterministica — failover di quota verso esecutori esterni (1.39.0, C1.4).

  X1 best_free_provider: sceglie il free tier con piu' margine oggi; paid e
     image esclusi; provider a rpd esaurito escluso
  X2 gate, finestra 85%, budget eleggibile (tipo, effort low, data internal):
     systemMessage con il comando external-exec pronto e il budget-amend
  X3 gate, finestra 40%: nessuna proposta
  X4 budget non eleggibile (effort high / restricted / senza tipo): nessuna proposta
  X5 quota_guard deny (95%, Workflow): la proposta e' appesa al motivo del deny
  X6 budget-amend --route external: rotta cambiata, reversal loggato
  X7 external-exec --provider auto: risolve al provider migliore (o unavailable)

Usage: python3 tests/external-failover-verify.py
"""
import importlib.util, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


home = Path(tempfile.mkdtemp(prefix="fd-fo-home-"))
proj = tempfile.mkdtemp(prefix="fd-fo-proj-")
base = home / ".claude" / "fable-director"; base.mkdir(parents=True)
os.environ["HOME"] = str(home)


def run(args, stdin=None, sid=None, cwd=None):
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop("CLAUDE_CONFIG_DIR", None); env.pop("CLAUDE_CODE_SESSION_ID", None)
    if sid:
        env["CLAUDE_CODE_SESSION_ID"] = sid
    return subprocess.run([sys.executable] + args, capture_output=True, text=True, env=env,
                          input=stdin, timeout=60, cwd=cwd)


spec = importlib.util.spec_from_file_location("ext", SCRIPTS / "external-exec.py")
ext = importlib.util.module_from_spec(spec); spec.loader.exec_module(ext)
cfg = {"default": "gemini", "providers": {
    "gemini": {"type": "http", "billing": "free", "model": "g", "limits": {"rpd": 100}},
    "codex": {"type": "cli", "billing": "free", "model": "c", "limits": {"rpd": 50}},
    "gemini-image": {"type": "image", "billing": "paid", "model": "i"},
    "paid": {"type": "http", "billing": "paid", "model": "p"}}}
(base / "cross-family.json").write_text(json.dumps(cfg))
check("X1 best_free_provider: margine residuo, esclusi paid/image/esauriti",
      ext.best_free_provider(cfg, {"gemini": 90, "codex": 10}) == "codex"
      and ext.best_free_provider(cfg, {"gemini": 10, "codex": 49}) == "gemini"
      and ext.best_free_provider(cfg, {"gemini": 100, "codex": 50}) is None)

FDT = str(SCRIPTS / "fd-telemetry.py"); GATE = str(SCRIPTS / "pre-delegation-gate.py")


def gate(used, tool="Agent", sid="s-fo"):
    q = base / "quota.json"
    q.write_text(json.dumps({"five_hour_used_pct": used, "weekly_used_pct": 10}))
    return run([GATE], json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": proj,
                                   "session_id": sid, "tool_input": {"subagent_type": "fable-director:fd-executor",
                                                                     "prompt": "x" * 10, "description": "d"}}))


def reopen(**kw):
    run([FDT, "budget-close", "--outcome", "abandoned", "--cwd", proj])
    args = [FDT, "budget-open", "--task", "batch seo", "--expected-output", "100", "--cwd", proj]
    for k, v in kw.items():
        args += [f"--{k}", v]
    r = run(args, sid="s-fo")
    assert r.returncode == 0, r.stderr


reopen(type="seo-batch", effort="low", **{"data-class": "internal"}, route="agent")
r = gate(85)
out = json.loads(r.stdout) if r.stdout.strip() else {}
msg = out.get("systemMessage", "")
check("X2 finestra 85%, budget eleggibile: proposta con comando pronto e budget-amend",
      "FD ⇄ five-hour window at 85%" in msg and "--provider codex" in msg
      and "--type seo-batch" in msg and "budget-amend --route external" in msg
      and "permissionDecision" not in json.dumps(out), r.stdout + r.stderr)
r = gate(40)
check("X3 finestra 40%: nessuna proposta", "FD ⇄" not in (r.stdout or ""), r.stdout)
reopen(type="seo-batch", effort="high")
r1 = gate(85)
reopen(type="seo-batch", effort="low", **{"data-class": "restricted"})
r2 = gate(85)
reopen(effort="low")
r3 = gate(85)
check("X4 effort high / restricted / senza tipo: nessuna proposta",
      all("FD ⇄" not in (x.stdout or "") for x in (r1, r2, r3)), r1.stdout + r2.stdout + r3.stdout)
reopen(type="seo-batch", effort="low", **{"data-class": "internal"}, route="agent")
r = gate(95, tool="Workflow")
out = json.loads(r.stdout) if r.stdout.strip() else {}
txt = json.dumps(out, ensure_ascii=False)
check("X5 quota_guard deny al 95%: proposta appesa al motivo",
      "deny" in txt and "quota guard" in txt and "FD ⇄" in txt and "--provider codex" in txt, r.stdout)
r = run([FDT, "budget-amend", "--route", "external", "--reason", "quota 95%", "--cwd", proj])
con = sqlite3.connect(base / "telemetry.db")
rev = [json.loads(p) for (p,) in con.execute("select payload from events where event='reversal'")]
con.close()
bfile = base / "budgets" / f"{ext.__dict__.get('cwd_slug', lambda c: None)(proj) or ''}.json"
spec2 = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(fdt)
b = json.loads((base / "budgets" / f"{fdt.cwd_slug(proj)}.json").read_text())
check("X6 budget-amend --route external: rotta cambiata, reversal loggato",
      "route amended: agent → external" in r.stdout and b.get("route") == "external"
      and any(x.get("kind") == "route" and x.get("to") == "external" for x in rev),
      r.stdout + r.stderr + str(rev))
r = run([str(SCRIPTS / "external-exec.py"), "--provider", "auto", "--spec", "ciao"],
        sid="s-fo", cwd=proj)
# budget aperto (rotta external): auto → codex (cli, binario assente → unavailable con NOME risolto)
check("X7 --provider auto risolve al provider migliore prima del preflight",
      "STATUS: unavailable" in r.stdout and "'codex'" in r.stdout, r.stdout + r.stderr)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
