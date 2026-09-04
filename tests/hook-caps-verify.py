#!/usr/bin/env python3
"""Verifica deterministica — cap dell'harness sugli output degli hook (1.40.4).

Claude Code 2.1.260 tronca IN SILENZIO i campi di un hook oltre un tetto
(letto dal binario): reason 2000 caratteri / 20 righe, systemMessage 4000,
additionalContext (e stdout di SessionStart) 8000 caratteri / 200 righe.
L'autore dell'hook non lo vede mai (claude-code#91870, misure della community).

  H1  Stop: reason con esito verify lungo → ≤ 2000 char, ≤ 20 righe, l'istruzione
      di blocco e' in testa, il taglio e' marcato
  H2  Stop: systemMessage da 6000 char → ≤ 4000, marcato
  H3  Stop: campi corti passano intatti (nessun marker)
  H4  SessionStart reale con TUTTI i blocchi opzionali (hindsight 5 righe, legenda,
      XF): non entra nel cap (misurato ~8250 char, prima veniva troncato in silenzio)
      → ≤ 8000, kernel intero, XF saltato pulito, tentativo NON consumato
  H4b SessionStart reale per un utente nuovo (senza hindsight): XF presente,
      tentativo consumato, ≤ 8000 senza marker
  H5  SessionStart con kernel gonfiato a 6300 char: kernel intero, XF SALTATO senza
      consumare il tentativo, output ≤ 8000 senza marker
  H6  SessionStart con kernel oltre il cap: output ≤ 8000 char e marker visibile

Usage: python3 tests/hook-caps-verify.py   (exit 0 = all green)
"""
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "fable-director"
SCRIPTS = ROOT / "scripts"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def emitted(mod, obj, verify=None):
    mod._VERIFY_MSG = verify
    mod._PRINTED = False
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.emit(obj)
    return json.loads(buf.getvalue())


sbc = load("stop-budget-check")
MARK = sbc.CAP_MARK

# H1
verify = "\n".join(f"verify line {i}: " + "x" * 120 for i in range(40))
out = emitted(sbc, {"decision": "block", "reason": "✕ FABLE-DIRECTOR 3× block — write the post-mortem"}, verify)
r = out["reason"]
check("H1 reason ≤ 2000 char e ≤ 20 righe",
      len(r) <= 2000 and r.count("\n") + 1 <= 20, f"len={len(r)} lines={r.count(chr(10)) + 1}")
check("H1 istruzione di blocco in testa, verify dopo", r.startswith("✕ FABLE-DIRECTOR 3× block") and "verify line 0" in r)
check("H1 taglio marcato", r.endswith(MARK))

# H2
out = emitted(sbc, {"systemMessage": "y" * 6000})
m = out["systemMessage"]
check("H2 systemMessage ≤ 4000 e marcato", len(m) <= 4000 and m.endswith(MARK), f"len={len(m)}")

# H3
out = emitted(sbc, {"decision": "block", "reason": "short"}, "verify: PASS")
check("H3 campi corti intatti", out["reason"] == "short\nverify: PASS" and MARK not in out["reason"])


def fresh_home(hindsight=True):
    tmp = Path(tempfile.mkdtemp(prefix="fd-caps-test"))
    home = tmp / "home"
    base = home / ".claude" / "fable-director"
    base.mkdir(parents=True)
    if hindsight:
        con = sqlite3.connect(base / "telemetry.db")
        con.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, "
                    "session_id TEXT, cwd TEXT, event TEXT NOT NULL, payload TEXT)")
        for i in range(5):
            con.execute("INSERT INTO events(ts, session_id, cwd, event, payload) VALUES("
                        "datetime('now','-1 day'),'old','/tmp/fd-caps-cwd','budget_flag',?)",
                        (json.dumps({"task": f"task {i} " + "t" * 60, "ratio": 3.2 + i, "auto": True,
                                     "expected_output": 1000, "actual_output": 3200}),))
        con.commit(); con.close()
    return home


def run_kernel(home, plugin_root=ROOT):
    e = dict(os.environ, HOME=str(home), CLAUDE_PLUGIN_ROOT=str(plugin_root))
    e.pop("CLAUDE_CONFIG_DIR", None)
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "s1",
                          "cwd": "/tmp/fd-caps-cwd", "source": "startup"})
    p = subprocess.run(["bash", str(SCRIPTS / "session-kernel.sh")], env=e, text=True,
                       input=payload, capture_output=True, timeout=60)
    return p.stdout


def xf_count(home):
    f = home / ".claude" / "fable-director" / "xf-onboarding-count"
    return int(f.read_text().strip()) if f.is_file() else 0


KMARK = "[fd: SessionStart output cut"
kernel_text = (ROOT / "kernel.md").read_text()

# H4
home = fresh_home()
out = run_kernel(home)
check("H4 SessionStart reale ≤ 8000 char e ≤ 200 righe",
      len(out) <= 8000 and out.count("\n") <= 200, f"len={len(out)} lines={out.count(chr(10))}")
check("H4 kernel intero", kernel_text.strip() in out)
check("H4 XF saltato pulito, tentativo NON consumato", "XF ONBOARDING" not in out and xf_count(home) == 0)
check("H4 nessun marker di taglio", KMARK not in out)
print(f"      evidenza: output reale con hindsight {len(out)} char, {out.count(chr(10))} righe (cap 8000/200)")

# H4b
home = fresh_home(hindsight=False)
out = run_kernel(home)
check("H4b utente nuovo: XF presente, tentativo consumato",
      "XF ONBOARDING" in out and xf_count(home) == 1, f"len={len(out)} count={xf_count(home)}")
check("H4b ≤ 8000 senza marker", len(out) <= 8000 and KMARK not in out, f"len={len(out)}")
print(f"      evidenza: output utente nuovo {len(out)} char (cap 8000)")


def fat_plugin(kernel_chars):
    tmp = Path(tempfile.mkdtemp(prefix="fd-caps-plugin"))
    (tmp / "scripts").mkdir()
    for f in ("session-kernel.sh", "session-hindsight.py", "version-sentinel.py"):
        shutil.copy(SCRIPTS / f, tmp / "scripts" / f)
    (tmp / "kernel.md").write_text("K" * kernel_chars + "\n")
    return tmp


# H5
home = fresh_home()
out = run_kernel(home, fat_plugin(6300))
check("H5 kernel 6300: intero, XF saltato, tentativo NON consumato",
      "K" * 6300 in out and "XF ONBOARDING" not in out and xf_count(home) == 0,
      f"xf_in={('XF ONBOARDING' in out)} count={xf_count(home)}")
check("H5 ≤ 8000 senza marker", len(out) <= 8000 and KMARK not in out, f"len={len(out)}")

# H6
home = fresh_home()
out = run_kernel(home, fat_plugin(9000))
check("H6 kernel 9000: output ≤ 8000 e marker visibile", len(out) <= 8000 and KMARK in out, f"len={len(out)}")

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} — {', '.join(FAILS)}"); sys.exit(1)
print("hook-caps-verify: all green")
