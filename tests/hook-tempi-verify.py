#!/usr/bin/env python3
"""Verifica deterministica — tempi degli hook di fable-director (fd-hook-lenti-16-11).

/doctor del 16/11/2026 (trascrizioni 05-16/11): Stop scaduti 82 volte in 10 giorni,
71 a 5 s sull'observe stop che fd e supervisor lanciavano con lo STESSO comando
(Claude Code lo fondeva in uno); lo Stop di fd senza timeout teneva il turno fino
alla fine del --verify (fino a 60 s). Misure sui payload veri, a riposo, CPU:
stop-budget-check 0,5 s, observe stop 0,4-0,7 s, family-prefix 0,6 s, route-hint
1,1 s (0,4 s per rileggere 204 ricevute a ogni prompt), perimeter-gate 0,6 s; sotto
carico 9-12 sul Chromebook, con i quattro Stop lanciati insieme, 2-12 s.

Soglie scritte:
  - ogni hook di fd ha un `timeout` esplicito; Stop stop-budget-check ≤ 30 s,
    PreToolUse Bash ≤ 15 s, UserPromptSubmit ≤ 10 s ciascuno;
  - observe stop: SINCRONO (in Claude Code 2.1.325 il systemMessage di un hook
    async non arriva a schermo: diventa contesto meta per il modello al turno
    dopo), timeout 10-20 s (sotto i 20 s dello Stop di supervisor che gira in
    parallelo), comando unico di fd (col nome del plugin), diverso da quello che
    sync.py scrive per tutti;
  - Stop con un --verify lento: la parte che blocca il turno ≤ 6 s di orologio
    (attesa in linea FD_VERIFY_INLINE_S = 2 s, il resto in sottofondo);
  - CPU mediana su 5 giri ≤ 1,0 s per family-prefix, route-hint (400 ricevute),
    perimeter-gate su Bash, stop-budget-check senza verify da lanciare;
  - route-hint: al secondo prompt nessuna ricevuta riletta (indice).

  T1 ogni voce di hooks.json ha un timeout intero
  T2 timeout entro le soglie per Stop, PreToolUse Bash, UserPromptSubmit
  T3 observe stop: sincrono, timeout 10-20 s, comando con "fable-director", lo script accetta l'argomento
  T4 Stop con verify da 20 s: torna in ≤ 6 s; lo state segna il lavoro in corso
  T5 a verify finito, lo Stop dopo riporta il fallimento (exit 1) e il claim gate blocca «Tutto fatto.»
  T6 verify veloce: esito nello STESSO Stop, come prima
  T7 nessun secondo verify mentre il primo gira
  T8 route-hint: secondo prompt senza riletture; una ricevuta nuova entra nell'indice
  T9 CPU mediana ≤ 1,0 s per i quattro hook del percorso caldo

Usage: python3 tests/hook-tempi-verify.py   (exit 0 = all green)
"""
import importlib.util
import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fable-director"
SCRIPTS = ROOT / "scripts"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


home = Path(tempfile.mkdtemp(prefix="fd-tempi-home-"))
ENV = dict(os.environ, HOME=str(home), USERPROFILE=str(home), XDG_STATE_HOME=str(home / ".state"),
           XDG_CONFIG_HOME=str(home / ".config"), CLAUDE_PLUGIN_ROOT=str(ROOT))
for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SESSION_ID", "FD_VERIFY_TIMEOUT_S", "FD_VERIFY_INLINE_S"):
    ENV.pop(k, None)
os.environ["HOME"] = str(home)
spec = importlib.util.spec_from_file_location("fdt", SCRIPTS / "fd-telemetry.py")
fdt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fdt)
FDT = str(SCRIPTS / "fd-telemetry.py")
STOP = str(SCRIPTS / "stop-budget-check.py")


def run(args, stdin=None, timeout=120):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True, env=ENV,
                          input=stdin, timeout=timeout)


# ------------------------------------------------------------------ T1-T3 hooks.json
hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())["hooks"]
entries = [(ev, e.get("matcher"), h) for ev, L in hooks.items() for e in L for h in e["hooks"]]
missing = [f"{ev}/{m}: {h['command'][-50:]}" for ev, m, h in entries if not isinstance(h.get("timeout"), int)]
check("T1 ogni hook di fd ha un timeout esplicito", not missing, "; ".join(missing))


def timeouts(ev, matcher=None, needle=""):
    return [h.get("timeout") for e, m, h in entries
            if e == ev and (matcher is None or m == matcher) and needle in h["command"]]


t_stop = timeouts("Stop", needle="stop-budget-check")
t_bash = timeouts("PreToolUse", "Bash")
t_ups = timeouts("UserPromptSubmit")
ok = (t_stop and all(isinstance(t, int) and 5 <= t <= 30 for t in t_stop)
      and t_bash and all(isinstance(t, int) and 5 <= t <= 15 for t in t_bash)
      and len(t_ups) == 2 and all(isinstance(t, int) and 3 <= t <= 10 for t in t_ups))
check("T2 timeout entro le soglie (Stop ≤ 30, Bash ≤ 15, UserPromptSubmit ≤ 10)", bool(ok),
      f"Stop {t_stop} Bash {t_bash} UPS {t_ups}")

obs = [h for e, m, h in entries if e == "Stop" and "observe.py" in h["command"]]
SYNC_CMD = 'bash "${CLAUDE_PLUGIN_ROOT}/observe/py.sh" "${CLAUDE_PLUGIN_ROOT}/observe/observe.py" stop'
r = None
if obs:
    cmd = obs[0]["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))
    r = subprocess.run(["bash", "-c", cmd], input=json.dumps({"cwd": str(home)}), capture_output=True,
                       text=True, env=ENV, timeout=60)
check("T3 observe stop sincrono, timeout 10-20 s, comando unico di fd che lo script accetta",
      len(obs) == 1 and not obs[0].get("async") and not obs[0].get("asyncRewake")
      and isinstance(obs[0].get("timeout"), int) and 10 <= obs[0]["timeout"] <= 20
      and "fable-director" in obs[0]["command"] and obs[0]["command"] != SYNC_CMD
      and r is not None and r.returncode == 0,
      json.dumps(obs) + (f" rc={r.returncode} {r.stderr[-200:]}" if r else ""))


# ------------------------------------------------------------------ T4-T7 verify in sottofondo
def usage(ts, write=None, text=None):
    rec = {"timestamp": ts, "type": "assistant", "message": {
        "model": "claude-sonnet-5", "usage": {"output_tokens": 10, "input_tokens": 1,
                                             "cache_read_input_tokens": 0,
                                             "cache_creation_input_tokens": 0},
        "content": []}}
    if write:
        rec["message"]["content"].append({"type": "tool_use", "name": "Write",
                                          "input": {"file_path": write, "content": "x"}})
    if text:
        rec["message"]["content"].append({"type": "text", "text": text})
    return json.dumps(rec) + "\n"


def setup(check_src):
    proj = Path(tempfile.mkdtemp(prefix="fd-tempi-proj-"))
    run([FDT, "budget-open", "--task", "tempi", "--expected-output", "100000",
         "--verify", "python3 check.py", "--cwd", str(proj)])
    (proj / "check.py").write_text(check_src)
    tr = proj / "s.jsonl"
    tr.write_text(usage("2030-01-01T10:00:00Z", write=str(proj / "a.py"))
                  + usage("2030-01-01T10:00:05Z", text="Ho sistemato il modulo."))
    return proj, tr


def stop(proj, tr, extra=None):
    p = {"cwd": str(proj), "transcript_path": str(tr), "stop_hook_active": False, "session_id": "s-tempi"}
    p.update(extra or {})
    t = time.monotonic()
    r = run([STOP], json.dumps(p))
    outs = [l for l in r.stdout.splitlines() if l.strip()]
    return (json.loads(outs[0]) if outs else {}), time.monotonic() - t, r


def state(proj):
    b = home / ".claude" / "fable-director" / "budgets" / f"{fdt.cwd_slug(str(proj))}.json"
    try:
        return json.loads(b.with_name(b.stem + ".state.json").read_text())
    except (OSError, ValueError):
        return {}


SLOW = "import sys, time\ntime.sleep(20)\nprint('2 failed')\nsys.exit(1)\n"
proj, tr = setup(SLOW)
msg, wall, r = stop(proj, tr)
st = state(proj)
check("T4 verify da 20 s: lo Stop torna in ≤ 6 s e lascia il verify in corso",
      wall <= 6.0 and st.get("verify_job") and st.get("verify_rc") is None,
      f"{wall:.1f} s, state {json.dumps(st)[:300]} {r.stderr[-300:]}")

# T7: mentre gira, una scrittura nuova non lancia un secondo verify
tr.write_text(tr.read_text() + usage("2030-01-01T10:00:10Z", write=str(proj / "b.py")))
job = (st.get("verify_job") or {}).get("id")
msg7, wall7, _ = stop(proj, tr)
st7 = state(proj)
check("T7 nessun secondo verify mentre il primo gira",
      job and (st7.get("verify_job") or {}).get("id") == job and wall7 <= 6.0,
      f"{job} → {st7.get('verify_job')} {wall7:.1f} s")

deadline = time.time() + 60
res = None
while time.time() < deadline:
    files = list((home / ".claude" / "fable-director" / "budgets").glob("*.verify-result.json"))
    if files:
        res = files[0]
        break
    time.sleep(0.5)
tr.write_text(tr.read_text() + usage("2030-01-01T10:00:30Z", text="Tutto fatto."))
msg5, wall5, r5 = stop(proj, tr, {"last_assistant_message": "Ho sistemato il modulo.\n\nTutto fatto."})
st5 = state(proj)
check("T5 a verify finito lo Stop dopo riporta exit 1 e il claim gate blocca la chiusura",
      # b.py e' stato scritto dopo il primo lancio: un secondo verify (nuovo id) e' giusto
      res is not None and st5.get("verify_rc") == 1
      and (st5.get("verify_job") or {}).get("id") != job
      and msg5.get("decision") == "block" and "python3 check.py (exit 1)" in msg5.get("reason", ""),
      f"res={res} state={json.dumps(st5)[:300]} out={r5.stdout[:300]} err={r5.stderr[-200:]}")

projf, trf = setup("import sys\nprint('1 failed')\nsys.exit(1)\n")
msg6, wall6, r6 = stop(projf, trf)
check("T6 verify veloce: esito nello stesso Stop, come prima",
      "verification failed" in msg6.get("systemMessage", "") and state(projf).get("verify_rc") == 1
      and not state(projf).get("verify_job"),
      f"{r6.stdout[:300]} {r6.stderr[-200:]}")

# ------------------------------------------------------------------ T8 route-hint: indice delle ricevute
rdir = home / ".claude" / "fable-director" / "receipts"
rdir.mkdir(parents=True, exist_ok=True)
for i in range(400):
    (rdir / f"r{i:04d}.json").write_text(json.dumps({
        "outcome": "ok", "task": f"lavoro numero{i:04d} generico qualsiasi", "cwd": f"/altro/p{i}",
        "verify": "python3 tests/x.py", "type": "code", "closed_at": "2026-11-01T10:00:00Z"}))
(rdir / "r9999.json").write_text(json.dumps({
    "outcome": "ok", "task": "migrazione database clienti postgres", "cwd": "/altro/db",
    "verify": "pytest", "type": "code", "closed_at": "2026-11-02T10:00:00Z"}))
spec = importlib.util.spec_from_file_location("route_hint", SCRIPTS / "route-hint.py")
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)
reads = []
_orig = Path.read_text


def counting(self, *a, **k):
    if self.parent == rdir and self.suffix == ".json":
        reads.append(self.name)
    return _orig(self, *a, **k)


Path.read_text = counting
prompt = "fai la migrazione del database clienti su postgres"
first = rh.solved_elsewhere(prompt, "/qui")
reads.clear()
second = rh.solved_elsewhere(prompt, "/qui")
n_second = len(reads)
(rdir / "r9998.json").write_text(json.dumps({
    "outcome": "ok", "task": "rotazione certificati nginx scaduti", "cwd": "/altro/web",
    "verify": "curl", "type": "ops", "closed_at": "2026-11-03T10:00:00Z"}))
reads.clear()
third = rh.solved_elsewhere("rinnova i certificati nginx scaduti", "/qui")
Path.read_text = _orig
check("T8 route-hint: secondo prompt senza riletture, ricevuta nuova trovata",
      first and second == first and "migrazione database" in first and n_second == 0
      and third and "rotazione certificati" in third and reads == ["r9998.json"],
      f"first={first!r} second={second!r} riletture={n_second} third={third!r} {reads[:5]}")


# ------------------------------------------------------------------ T9 CPU del percorso caldo
def cpu_of(cmd, payload):
    out = []
    for _ in range(5):
        r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        subprocess.run(cmd, input=payload, capture_output=True, text=True, env=ENV, timeout=60)
        r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        out.append(r1.ru_utime - r0.ru_utime + r1.ru_stime - r0.ru_stime)
    return statistics.median(out)


cwdp = Path(tempfile.mkdtemp(prefix="fd-tempi-cwd-"))
trp = cwdp / "s.jsonl"
trp.write_text(usage("2030-01-01T10:00:00Z", text="ok"))
base = {"session_id": "s-cpu", "transcript_path": str(trp), "cwd": str(cwdp)}
py = ["bash", str(SCRIPTS / "py.sh")]
cases = {
    "family-prefix": (py + [str(SCRIPTS / "family-prefix.py")],
                      dict(base, hook_event_name="UserPromptSubmit", prompt=prompt)),
    "route-hint": (py + [str(SCRIPTS / "route-hint.py")],
                   dict(base, hook_event_name="UserPromptSubmit", prompt=prompt)),
    "perimeter-gate Bash": (py + [str(SCRIPTS / "perimeter-gate.py")],
                            dict(base, hook_event_name="PreToolUse", tool_name="Bash",
                                 tool_input={"command": "git status --short"})),
    "stop-budget-check": (py + [STOP], dict(base, hook_event_name="Stop", stop_hook_active=False)),
}
cpu = {k: cpu_of(c, json.dumps(p)) for k, (c, p) in cases.items()}
check("T9 CPU mediana ≤ 1,0 s per i quattro hook del percorso caldo",
      all(v <= 1.0 for v in cpu.values()), json.dumps({k: round(v, 2) for k, v in cpu.items()}))
print("     CPU mediana: " + ", ".join(f"{k} {v:.2f} s" for k, v in cpu.items()))

print(f"\n{'ALL GREEN' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
