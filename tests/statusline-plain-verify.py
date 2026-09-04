#!/usr/bin/env python3
"""Verifica deterministica — statusline PLAIN (1.39.0, sezione F).

Criterio: ogni segmento dice cosa fare, in parole; a riposo la riga tace.

  T1 stato normale: "Fable 5.1 · quota ok until HH:MM · context 26%", nessuna sigla, una riga
  T2 agenti al lavoro: "2 agents working"
  T3 quota 5h ≥80: "quota 90% used, resets in N min"; 60-79: "quota 71% used, resets HH:MM";
     100: "quota 100% used, resets in N min" (mai "almost")
  T4 contesto: "context N%" SEMPRE in riga 1 (giallo da 60, rosso da 80); ≥80 anche
     "context 85% full — finish the task and start a new session" in riga 2
  T5 budget 2×: "budget over 2.3× — reconsider the route"; 3× flagged: testo rosso, nessuno sfondo
     "budget over 3× — post-mortem before closing"
  T6 verify fallito: "verification failed: python3 tests/run.py (exit 1)"
  T7 agente fermo ≥30 min: "1 agent stuck for 32 min — check /tasks"; sotto: no
  T8 3 comandi falliti: "3 commands failed in a row — change approach"; effort max: "effort max on"
  T9 priorita di un altra sessione: "another session has priority (incident: …)"
  T10 piu eccezioni → riga 2, ordine di priorita, degradazione a larghezza ridotta
  T11 nessuna sigla della riga storica (ctx, 5H, 7D, bdg, dlg, cmp, cache, xf, ✦≤)
  T12 modalita expert via statusline.json: riga storica intatta
  T13 statusline-install.sh --expert / --plain scrive il file di modalita

Usage: python3 tests/statusline-plain-verify.py
"""
import json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fable-director" / "scripts"
passed, failed = [], []


def check(name, ok, evidence=""):
    (passed if ok else failed).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {evidence}"))


def plain(s):
    s = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", s)
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


tmp = Path(tempfile.mkdtemp(prefix="fd-plain-"))
home = tmp / "home"; home.mkdir()
base = home / ".claude" / "fable-director"; base.mkdir(parents=True)
cwd = str(tmp / "proj"); Path(cwd).mkdir()
SID = "sess-plain"


def stdin(pct=26, rl=40, wk=46, effort=None, reset_in=2400, transcript=None):
    d = {"model": {"display_name": "Fable 5.1 (200k)"}, "session_id": SID, "cwd": cwd,
         **({"transcript_path": transcript} if transcript else {}),
         "context_window": {"used_percentage": pct, "context_window_size": 1000000},
         "rate_limits": {"five_hour": {"used_percentage": rl, "resets_at": int(time.time()) + reset_in},
                         "seven_day": {"used_percentage": wk, "resets_at": int(time.time()) + 300000}}}
    if effort:
        d["effort"] = {"level": effort}
    return json.dumps(d)


def render(s, **env):
    e = dict(os.environ, HOME=str(home), CAVEMAN_STATUSLINE_SH="/nonexistent", COLUMNS="140")
    e.pop("CLAUDE_CONFIG_DIR", None); e.pop("FD_STATUSLINE_MODE", None)
    e.update({k: str(v) for k, v in env.items()})
    return plain(subprocess.run(["bash", str(ROOT / "statusline-ctx.sh")], input=s, capture_output=True,
                                text=True, env=e, timeout=30).stdout)


SIGLE = re.compile(r"\bctx\b|\b5H\b|\b7D\b|\bbdg\b|\bdlg\b|\bcmp\b|\bcache\b|\bxf\b|✦|▓|░|⟲")

out = render(stdin())
check("T1 normale: 'Fable 5.1 · quota ok until HH:MM · context 26%', una riga, nessuna sigla",
      re.fullmatch(r"Fable 5\.1 · quota ok until \d{2}:\d{2} · context 26%", out.strip()) is not None
      and "\n" not in out.strip() and not SIGLE.search(out), repr(out))

# T2 agenti in volo
sdir = base / "subagents"; sdir.mkdir()
since_ok = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
(sdir / f"{SID}.json").write_text(json.dumps({"inflight": {"a": {"type": "x", "since": since_ok},
                                                           "b": {"type": "x", "since": since_ok}},
                                              "started": 2, "stopped": 0}))
out = render(stdin())
check("T2 agenti al lavoro", "2 agents working" in out and "stuck" not in out, out)
(sdir / f"{SID}.json").unlink()

out80 = render(stdin(rl=90, reset_in=40 * 60)); out60 = render(stdin(rl=71)); out100 = render(stdin(rl=100, reset_in=40 * 60))
check("T3 quota 5h: '90% used, resets in 40 min' / '71% used, resets HH:MM' / '100% used', mai 'almost'",
      "quota 90% used, resets in 40 min" in out80 and re.search(r"quota 71% used, resets \d{2}:\d{2}", out60)
      and "quota 100% used, resets in 40 min" in out100 and "almost" not in out80 + out100,
      out80 + "\n" + out60 + "\n" + out100)
out = render(stdin(pct=85)); outfull = render(stdin(pct=100)); out65 = render(stdin(pct=65, wk=72, effort="max"), COLUMNS="90")
check("T4 contesto: percentuale sempre in riga 1 (anche a 65% su 90 colonne), frase da 80",
      out.partition("\n")[0].startswith("Fable 5.1 · quota ok until") and "· context 85%" in out.partition("\n")[0]
      and "context 85% full — finish the task and start a new session" in out
      and "context 100% full — finish the task" in outfull and "almost" not in out + outfull
      and "· context 65%" in out65.partition("\n")[0], out + "\n" + outfull + "\n" + out65)

# T5 budget 2× e 3×
FDT = str(ROOT / "fd-telemetry.py")
env = dict(os.environ, HOME=str(home), CLAUDE_CODE_SESSION_ID=SID); env.pop("CLAUDE_CONFIG_DIR", None)
subprocess.run([sys.executable, FDT, "budget-open", "--task", "t", "--expected-output", "100", "--cwd", cwd],
               env=env, capture_output=True)
import importlib.util
spec = importlib.util.spec_from_file_location("fdt", FDT); os.environ["HOME"] = str(home)
fdt = importlib.util.module_from_spec(spec); spec.loader.exec_module(fdt)
bfile = base / "budgets" / f"{fdt.cwd_slug(cwd)}.json"
b = json.loads(bfile.read_text()); b["warned"] = True; bfile.write_text(json.dumps(b))
out2 = render(stdin())
b["status"] = "flagged"; bfile.write_text(json.dumps(b))
out3 = render(stdin())
raw3 = subprocess.run(["bash", str(ROOT / "statusline-ctx.sh")], input=stdin(), capture_output=True, text=True,
                      env=dict(os.environ, HOME=str(home), CAVEMAN_STATUSLINE_SH="/nonexistent", COLUMNS="140"),
                      timeout=30).stdout
check("T5 budget 2× in parole; 3× testo rosso senza sfondo",
      "budget over 2× — reconsider the route" in out2 and "budget over 3× — post-mortem before closing" in out3
      and "bdg" not in out2 and "\x1b[48;" not in raw3 and "\x1b[38;5;196m" in raw3, out2 + "\n" + out3 + "\n" + repr(raw3))
b["status"] = "open"; b["warned"] = False; bfile.write_text(json.dumps(b))

# T6 verify fallito (state file)
sfile = bfile.with_name(bfile.stem + ".state.json")
sfile.write_text(json.dumps({"verify_rc": 1, "verify_cmd": "python3 tests/run.py"}))
out = render(stdin())
check("T6 verifica fallita in parole", "verification failed: python3 tests/run.py (exit 1)" in out, out)
sfile.unlink()

# T7 agente fermo
since_old = (datetime.now(timezone.utc) - timedelta(minutes=32)).isoformat()
(sdir / f"{SID}.json").write_text(json.dumps({"inflight": {"a": {"type": "x", "since": since_old}},
                                              "started": 1, "stopped": 0}))
out = render(stdin())
check("T7 agente fermo da 32 min", "1 agent stuck for 32 min — check /tasks" in out, out)
(sdir / f"{SID}.json").unlink()

# T7b agenti morti (1.40.2): transcript fermo da 2h o run completato → né stuck né working
sess = tmp / "sess"; (sess / "subagents" / "workflows" / "wf_done").mkdir(parents=True); (sess / "workflows").mkdir()
tp = str(sess) + ".jsonl"; Path(tp).write_text("")
dead_f = sess / "subagents" / "agent-a.jsonl"; dead_f.write_text("{}\n")
_old = time.time() - 7200; os.utime(dead_f, (_old, _old))
(sess / "subagents" / "workflows" / "wf_done" / "agent-w.jsonl").write_text("{}\n")
(sess / "workflows" / "wf_done.json").write_text(json.dumps({"status": "completed"}))
(sess / "subagents" / "agent-b.jsonl").write_text("{}\n")
(sdir / f"{SID}.json").write_text(json.dumps({"inflight": {"a": {"type": "x", "since": since_old},
                                                           "w": {"type": "x", "since": since_old},
                                                           "b": {"type": "x", "since": since_old}},
                                              "started": 3, "stopped": 0}))
out = render(stdin(transcript=tp))
check("T7b agenti morti: fermo 2h e run completato fuori; il vivo con transcript fresco = '1 agent working'",
      "stuck" not in out and "1 agent working" in out, out)
(sdir / f"{SID}.json").unlink()

# T8 grinding + effort
gdir = base / "grinding"; gdir.mkdir()
(gdir / f"{SID}.json").write_text(json.dumps({"streak": 3}))
out = render(stdin(effort="max"))
check("T8 comandi falliti + effort max in parole",
      "3 commands failed in a row — change approach" in out and "effort max on" in out, out)
(gdir / f"{SID}.json").unlink()

# T9 priorita altrui
(base / "priority.json").write_text(json.dumps({
    "task": "sito cliente giu", "session_id": "other", "cwd": "/x",
    "account": __import__("hashlib").sha256(str(home / ".claude").encode()).hexdigest()[:8],
    "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}))
out = render(stdin())
check("T9 precedenza di un'altra sessione", "another session has priority (incident: sito cliente giu)" in out, out)
(base / "priority.json").unlink()

# T10 piu eccezioni: riga 2, ordine, degradazione
out = render(stdin(pct=85, rl=90, wk=72, reset_in=40 * 60))
l1, _, l2 = out.partition("\n")
check("T10 piu eccezioni → riga 2 con la piu urgente prima",
      l1.strip() == "Fable 5.1 · context 85%" and l2.startswith("└ quota 90% used, resets in 40 min") and "context 85% full" in l2, out)
narrow = render(stdin(pct=85, rl=90, wk=72, reset_in=40 * 60), COLUMNS="60")
check("T10b larghezza ridotta: cade la meno urgente, resta la piu urgente",
      "quota 90% used, resets in 40 min" in narrow and "weekly" not in narrow, narrow)
check("T11 nessuna sigla storica in nessuna resa plain",
      not SIGLE.search(out) and not SIGLE.search(out2) and not SIGLE.search(out80), out)

# T12 expert via file
(base / "statusline.json").write_text(json.dumps({"mode": "expert"}))
out = render(stdin())
check("T12 expert via statusline.json: riga storica",
      "✦ FABLE 5.1" in out and "ctx" in out and "5H 40%" in out, out)
(base / "statusline.json").unlink()

# T13 install --expert / --plain
env2 = dict(os.environ, HOME=str(home)); env2.pop("CLAUDE_CONFIG_DIR", None)
r = subprocess.run(["bash", str(ROOT / "statusline-install.sh"), "--expert"], env=env2, capture_output=True, text=True)
m1 = json.loads((base / "statusline.json").read_text()).get("mode")
subprocess.run(["bash", str(ROOT / "statusline-install.sh"), "--plain"], env=env2, capture_output=True, text=True)
m2 = json.loads((base / "statusline.json").read_text()).get("mode")
check("T13 install --expert / --plain", m1 == "expert" and m2 == "plain" and "statusline mode" in r.stdout,
      r.stdout + r.stderr)

print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
