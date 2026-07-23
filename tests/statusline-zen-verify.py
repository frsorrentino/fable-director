#!/usr/bin/env python3
"""Verifica fase-1 Zen HUD — statusline-ctx.sh.

Deterministica, HOME usa-e-getta, nessuna rete. Contratto (anteprima
approvata 2026-07-23):
- penombra: segmenti sani in grigio 245, MAI verde 114; soglie 60/80
  restano gialle/rosse;
- gauge: barra 8 celle (ceil, ▓ pieno / ░ vuoto) SOLO su ctx; /1M quando
  context_window_size = 1M; micro-barra bdg su scala 0-3x;
- effort live: ·max / ·xhigh giallo 220, ·high e sotto in 245, assente
  se il modello non espone effort;
- identita: "✦ FABLE5", niente quadre sui segmenti zen; cmp/fail restano
  deviazioni accese;
- badge caveman adottato: [CAVEMAN] → "caveman" in 172; badge dal formato
  sconosciuto passa intatto (passthrough);
- allarmi budget invariati: parole intere gialle/rosse (regressione).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "fable-director" / "scripts"
FAILS = []

DIM = "\x1b[38;5;245m"
YEL = "\x1b[38;5;220m"
RED = "\x1b[38;5;196m"
GRN = "\x1b[38;5;114m"
CAV = "\x1b[38;5;172m"


def check(name, ok, detail=""):
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def payload(ctx_pct=12, ctx_size=1_000_000, five=10, seven=10, effort="max",
            model="Fable 5"):
    d = {
        "session_id": "zen-test-session",
        "transcript_path": "/nonexistent/zen.jsonl",
        "cwd": os.getcwd(),
        "model": {"id": "claude-fable-5", "display_name": model},
        "context_window": {"used_percentage": ctx_pct,
                           "context_window_size": ctx_size},
        "rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": 4102444800},
            "seven_day": {"used_percentage": seven, "resets_at": 4102444800},
        },
    }
    if effort is not None:
        d["effort"] = {"level": effort}
    return json.dumps(d)


def render(home, stdin, **env):
    e = dict(os.environ, HOME=str(home), **{k: str(v) for k, v in env.items()})
    e.pop("CLAUDE_CONFIG_DIR", None)
    e.setdefault("CAVEMAN_STATUSLINE_SH", "/nonexistent/no-badge.sh")
    return subprocess.run(["bash", str(ROOT / "statusline-ctx.sh")],
                          input=stdin, capture_output=True, text=True,
                          env=e, timeout=30).stdout


def plain(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


tmp = Path(tempfile.mkdtemp(prefix="fd-zen-test"))
home = tmp / "home"
home.mkdir()

print("zen fase 1:")

# --- penombra e soglie ---
line = render(home, payload())
check("Z1 sano → grigio 245, mai verde 114",
      DIM in line and GRN not in line, line)
check("Z2 identita ✦ MODELLO, niente quadre zen",
      "✦ FABLE5" in plain(line) and "[FABLE5]" not in plain(line)
      and "[CTX" not in plain(line) and "[5H" not in plain(line), line)

hot = render(home, payload(seven=70))
check("Z3 7D 70% → giallo 220", YEL + "7D 70%" in hot.replace(" ", " "), hot)
crit = render(home, payload(seven=85))
check("Z4 7D 85% → rosso 196", RED + "7D 85%" in crit, crit)

# --- gauge ctx + /1M ---
check("Z5 ctx 12%/1M → barra ▓░░░░░░░ (ceil, 1 cella)",
      "ctx ▓░░░░░░░ 12%/1M" in plain(line), plain(line))
mid = render(home, payload(ctx_pct=42, ctx_size=200_000))
check("Z6 ctx 42% su 200k → 4 celle, niente /1M",
      "ctx ▓▓▓▓░░░░ 42%" in plain(mid) and "/1M" not in plain(mid), plain(mid))

# --- effort live ---
check("Z7 effort max → ·max giallo", YEL + "·max" in line, line)
low = render(home, payload(effort="high"))
check("Z8 effort high → ·high in penombra", DIM + "·high" in low, low)
noeff = render(home, payload(effort=None))
check("Z9 effort assente → nessun suffisso", "·max" not in plain(noeff)
      and "·high" not in plain(noeff), noeff)

# --- badge caveman ---
badge = tmp / "badge.sh"
badge.write_text("printf '\\033[38;5;172m[CAVEMAN]\\033[0m'")
withb = render(home, payload(), CAVEMAN_STATUSLINE_SH=badge)
check("Z10 [CAVEMAN] adottato → 'caveman' in 172, quadre via",
      CAV + "caveman" in withb and "[CAVEMAN]" not in plain(withb), withb)

alien = tmp / "alien.sh"
alien.write_text("printf '\\033[35m<<WEIRD>>\\033[0m'")
witha = render(home, payload(), CAVEMAN_STATUSLINE_SH=alien)
check("Z11 badge sconosciuto → passthrough intatto",
      "\x1b[35m<<WEIRD>>\x1b[0m" in witha, witha)

# --- budget: quieto dim, allarme a parole (regressione) ---
slug_src = str(Path(os.getcwd())).replace("\\", "/")
import hashlib
slug = (re.sub(r"[^A-Za-z0-9]+", "-", slug_src).strip("-")
        + "-" + hashlib.sha256(slug_src.encode()).hexdigest()[:8])
bdir = home / ".claude" / "fable-director" / "budgets"
bdir.mkdir(parents=True)
bf = bdir / f"{slug}.json"
bf.write_text(json.dumps({"status": "open", "task": "t", "effort": "high",
                          "declared_at": "2026-07-23T10:00:00Z"}))
quiet = render(home, payload())
check("Z12 budget aperto sano → 'bdg ok·high' in penombra",
      DIM + "bdg ok·high" in quiet, quiet)
bf.write_text(json.dumps({"status": "open", "task": "t", "effort": "high",
                          "warned": True,
                          "declared_at": "2026-07-23T10:00:00Z"}))
warn = render(home, payload())
check("Z13 warned 2× → parole intere gialle",
      YEL + "⚠ BUDGET 2× OF ESTIMATE·high" in warn, warn)
bf.write_text(json.dumps({"status": "flagged", "task": "t"}))
flag = render(home, payload())
check("Z14 flagged → parole intere rosse",
      RED + "✕ BUDGET 3× — POST-MORTEM DUE" in flag, flag)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto zen fase 1 rispettato")
