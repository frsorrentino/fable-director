#!/usr/bin/env python3
"""Verifica di `refreshInterval` nell'installer statusline.

Perché esiste questo test: gli update della statusline sono event-driven e la
documentazione dice esplicitamente che "possono zittirsi mentre il coordinatore
aspetta subagent in background". È il momento in cui questa statusline serve di
più (budget che sale, deleghe in volo, quota che scende) ed era esattamente il
momento in cui restava congelata. Il timer va scritto in settings.json, quindi
il contratto è dell'installer.

Contratto:
- install scrive refreshInterval (default 5s, minimo 1 come da doc);
- FD_STATUSLINE_REFRESH lo cambia; 0 (o negativo) lo omette del tutto;
- l'installazione è idempotente sul valore, non solo sul comando: una
  statusline vecchia SENZA timer viene aggiornata, una identica no;
- --remove toglie la chiave intera;
- una statusLine di terzi resta intatta (nessuna regressione).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director"
INSTALL = ROOT / "scripts" / "statusline-install.sh"
FAILS = []


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def run(cfg, *args, refresh=None):
    e = dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg))
    if refresh is not None:
        e["FD_STATUSLINE_REFRESH"] = str(refresh)
    else:
        e.pop("FD_STATUSLINE_REFRESH", None)
    return subprocess.run(["bash", str(INSTALL), *args], env=e, text=True,
                          capture_output=True, timeout=30)


def settings(cfg):
    f = cfg / "settings.json"
    return json.loads(f.read_text()) if f.is_file() else {}


tmp = Path(tempfile.mkdtemp(prefix="fd-statusline-test"))

print("statusline refreshInterval:")

# S1: default
cfg = tmp / "c1"
cfg.mkdir()
run(cfg)
sl = settings(cfg).get("statusLine") or {}
check("S1 install → refreshInterval 5s di default",
      sl.get("refreshInterval") == 5 and "statusline-ctx.sh" in sl.get("command", ""),
      str(sl))

# S2: idempotenza sul VALORE (non solo sul comando)
r = run(cfg)
check("S2 secondo install → già aggiornata, nessuna riscrittura",
      "già installata" in r.stdout, r.stdout.strip())

# S3: valore custom
cfg2 = tmp / "c2"
cfg2.mkdir()
run(cfg2, refresh=12)
check("S3 FD_STATUSLINE_REFRESH=12 → 12s",
      (settings(cfg2).get("statusLine") or {}).get("refreshInterval") == 12,
      str(settings(cfg2)))

# S4: disattivazione esplicita
cfg3 = tmp / "c3"
cfg3.mkdir()
run(cfg3, refresh=0)
sl3 = settings(cfg3).get("statusLine") or {}
check("S4 FD_STATUSLINE_REFRESH=0 → chiave assente (solo eventi)",
      "refreshInterval" not in sl3 and sl3.get("type") == "command", str(sl3))

# S5: valore sotto il minimo → alzato a 1 (la doc impone min 1)
cfg4 = tmp / "c4"
cfg4.mkdir()
run(cfg4, refresh="ciao")
check("S5 valore non numerico → default 5, nessun crash",
      (settings(cfg4).get("statusLine") or {}).get("refreshInterval") == 5,
      str(settings(cfg4)))

# S6: upgrade di un'installazione vecchia (senza timer)
cfg5 = tmp / "c5"
cfg5.mkdir()
(cfg5 / "settings.json").write_text(json.dumps({
    "statusLine": {"type": "command",
                   "command": f'bash "{ROOT}/scripts/statusline-ctx.sh"'},
    "altro": {"preservare": True}}))
r5 = run(cfg5)
s5 = settings(cfg5)
check("S6 installazione 1.28.x senza timer → aggiornata, altre chiavi intatte",
      (s5.get("statusLine") or {}).get("refreshInterval") == 5
      and s5.get("altro") == {"preservare": True}
      and "AGGIORNATA" in r5.stdout, r5.stdout.strip())

# S7: --remove toglie tutto
run(cfg5, "--remove")
check("S7 --remove → chiave statusLine assente, resto intatto",
      "statusLine" not in settings(cfg5)
      and settings(cfg5).get("altro") == {"preservare": True},
      str(settings(cfg5)))

# S8: statusLine di terzi intatta
cfg6 = tmp / "c6"
cfg6.mkdir()
third = {"type": "command", "command": "bash /opt/altro.sh"}
(cfg6 / "settings.json").write_text(json.dumps({"statusLine": third}))
r6 = run(cfg6)
check("S8 statusLine di terzi → non toccata, exit 2",
      r6.returncode == 2 and settings(cfg6).get("statusLine") == third,
      f"rc={r6.returncode} {settings(cfg6)}")

# --auto (1.50.1): dal SessionStart hook, muto salvo quando scrive
KERNEL = ROOT / "scripts" / "session-kernel.sh"
cfg7 = tmp / "c7"
cfg7.mkdir()
r7 = run(cfg7, "--auto")
s7 = settings(cfg7).get("statusLine") or {}
check("A1 --auto su settings vuoto → installa, una riga, refresh 5",
      r7.returncode == 0 and r7.stdout.strip() == "installed in settings.json" and "statusline-ctx.sh" in s7.get("command", "")
      and s7.get("refreshInterval") == 5, f"rc={r7.returncode} {r7.stdout.strip()} {s7}")
r7b = run(cfg7, "--auto")
check("A2 --auto una seconda volta → muto, exit 0",
      r7b.returncode == 0 and r7b.stdout.strip() == "" and r7b.stderr.strip() == "",
      f"rc={r7b.returncode} out={r7b.stdout.strip()!r} err={r7b.stderr.strip()!r}")
run(cfg7, "--remove")
r7c = run(cfg7, "--auto")
check("A3 dopo --remove, --auto non la rimette (marker di rinuncia)",
      (cfg7 / "fable-director" / "statusline-optout").is_file()
      and "statusLine" not in settings(cfg7) and r7c.stdout.strip() == "",
      f"{settings(cfg7)} out={r7c.stdout.strip()!r}")
run(cfg7)
check("A4 install esplicito → torna, marker di rinuncia tolto",
      "statusline-ctx.sh" in (settings(cfg7).get("statusLine") or {}).get("command", "")
      and not (cfg7 / "fable-director" / "statusline-optout").exists(), str(settings(cfg7)))
cfg8 = tmp / "c8"
cfg8.mkdir()
(cfg8 / "settings.json").write_text(json.dumps({"statusLine": third}))
r8 = run(cfg8, "--auto")
check("A5 --auto con statusLine di terzi → intatta, muto, exit 0",
      r8.returncode == 0 and r8.stdout.strip() == "" and r8.stderr.strip() == ""
      and settings(cfg8).get("statusLine") == third, f"rc={r8.returncode} {r8.stdout} {r8.stderr}")
# A6: la prima sessione (SessionStart) la installa e lo dice al modello; la seconda tace
home = tmp / "h9"
(home / ".claude").mkdir(parents=True)
e = dict(os.environ, HOME=str(home), CLAUDE_PLUGIN_ROOT=str(ROOT))
e.pop("CLAUDE_CONFIG_DIR", None)
k1 = subprocess.run(["bash", str(KERNEL)], env=e, text=True, capture_output=True, timeout=60,
                    input=json.dumps({"source": "startup", "cwd": str(tmp)}))
s9 = settings(home / ".claude").get("statusLine") or {}
check("A6 SessionStart startup → statusLine scritta in settings.json e riga STATUSLINE al modello",
      "statusline-ctx.sh" in s9.get("command", "") and "STATUSLINE installed in settings.json (auto)" in k1.stdout,
      f"{s9} | {k1.stdout[-300:]}")
k2 = subprocess.run(["bash", str(KERNEL)], env=e, text=True, capture_output=True, timeout=60,
                    input=json.dumps({"source": "startup", "cwd": str(tmp)}))
check("A7 seconda sessione → nessuna riga STATUSLINE",
      "STATUSLINE installed" not in k2.stdout, k2.stdout[-300:])

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto refreshInterval rispettato")
