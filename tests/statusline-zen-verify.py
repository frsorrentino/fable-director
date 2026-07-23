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
check("Z14 flagged → takeover a parole intere (fase 2: inverse in testa)",
      "✕ BUDGET 3× — POST-MORTEM DUE" in plain(flag)
      and flag.startswith("\x1b[48;5;196m"), flag)

# ---------- fase 2: riga 2 on-demand, takeover, COLUMNS, residuo free-tier ----------
print("zen fase 2:")

INV = "\x1b[48;5;196m"

home2 = tmp / "home2"
home2.mkdir()

# F1: nessuna attivita → UNA riga sola
idle = render(home2, payload())
check("F1 nessuna attivita → una riga", "\n" not in idle.strip(), idle)

# F2: budget aperto → riga 2 con └ e bdg; riga 1 senza bdg
b2 = home2 / ".claude" / "fable-director" / "budgets"
b2.mkdir(parents=True)
bf2 = b2 / f"{slug}.json"
bf2.write_text(json.dumps({"status": "open", "task": "t", "effort": "high",
                           "declared_at": "2026-07-23T10:00:00Z"}))
two = render(home2, payload())
rows = plain(two).split("\n")
check("F2 budget aperto → due righe, riga 2 = └ bdg",
      len(rows) == 2 and rows[1].startswith("└ ") and "bdg ok·high" in rows[1]
      and "bdg" not in rows[0], two)

# F3: takeover flagged → inverse bg rosso IN TESTA alla riga 1
bf2.write_text(json.dumps({"status": "flagged", "task": "t"}))
tko = render(home2, payload())
check("F3 flagged → takeover inverse in testa",
      tko.startswith(INV) and "✕ BUDGET 3× — POST-MORTEM DUE" in plain(tko),
      tko)

# F4: enforcement off → takeover inverse
bf2.write_text(json.dumps({"status": "open", "task": "t",
                           "schema_warned": True,
                           "declared_at": "2026-07-23T10:00:00Z"}))
eoff = render(home2, payload())
check("F4 schema_warned → ✕ ENFORCEMENT OFF inverse",
      eoff.startswith(INV) and "✕ ENFORCEMENT OFF" in plain(eoff), eoff)
bf2.unlink()

# F5/F6: residuo free-tier con finestra provider dichiarata
import sqlite3
from datetime import datetime, timedelta, timezone
fd2 = home2 / ".claude" / "fable-director"
(fd2 / "cross-family.json").write_text(json.dumps({
    "providers": {
        "gemini": {"billing": "free",
                   "limits": {"rpd": 1500,
                              "reset": {"period": "daily",
                                        "tz": "America/Los_Angeles"}}},
        "mystery": {"billing": "free", "limits": {"rpd": 50}},
    }}))
con = sqlite3.connect(fd2 / "telemetry.db")
con.execute("CREATE TABLE events (ts TEXT, event TEXT, payload TEXT)")
now = datetime.now(timezone.utc)
for prov in ("gemini", "mystery"):
    con.execute("INSERT INTO events VALUES (?,?,?)",
                (now.strftime("%Y-%m-%dT%H:%M:%SZ"), "verification",
                 json.dumps({"kind": "cross-family", "provider": prov})))
con.commit(); con.close()
resid = render(home2, payload())
check("F5 provider con reset → residuo n/rpd→HH:MM",
      re.search(r"gemini 1/1500→\d{2}:\d{2}", plain(resid)) is not None,
      plain(resid))
check("F6 provider senza reset → solo ×N, nessun orario inventato",
      re.search(r"mystery×1(?!/)", plain(resid)) is not None
      and "mystery×1→" not in plain(resid), plain(resid))

# F7: la chiamata di IERI (fuori finestra provider ma stesso giorno UTC no)
#     — un evento vecchio di 26h non deve contare nel residuo
con = sqlite3.connect(fd2 / "telemetry.db")
con.execute("INSERT INTO events VALUES (?,?,?)",
            ((now - timedelta(hours=26)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "verification",
             json.dumps({"kind": "cross-family", "provider": "gemini"})))
con.commit(); con.close()
resid2 = render(home2, payload())
check("F7 evento 26h fa fuori finestra → conteggio invariato",
      re.search(r"gemini 1/1500", plain(resid2)) is not None, plain(resid2))

# F8: COLUMNS stringe → riga 2 degrada (cache cade prima), mai bdg
bf2.write_text(json.dumps({"status": "open", "task": "t", "effort": "high",
                           "declared_at": "2026-07-23T10:00:00Z"}))
wide = render(home2, payload(), COLUMNS=200)
narrow = render(home2, payload(), COLUMNS=45)
wrows = plain(wide).split("\n")
nrows = plain(narrow).split("\n")
check("F8 COLUMNS 45 → riga 2 degrada ma bdg resta",
      len(nrows) == 2 and "bdg ok·high" in nrows[1]
      and len(nrows[1]) <= len(wrows[1]) and "xf" not in nrows[1], narrow)

# F9: cache SCADUTA da sola non giustifica la riga 2 (rumore permanente
#     su sessioni fredde); una cache viva si (timing deleghe, asse 6)
home3 = tmp / "home3"
home3.mkdir()
tr3 = tmp / "tr3.jsonl"
old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
    "%Y-%m-%dT%H:%M:%S.000Z")
tr3.write_text(json.dumps({"timestamp": old_ts, "message": {}}) + "\n")
p3 = json.loads(payload())
p3["transcript_path"] = str(tr3)
cold = render(home3, json.dumps(p3))
check("F9 solo cache exp → una riga (niente riga 2 di rumore)",
      "\n" not in cold.strip() and "cache" not in plain(cold), cold)
warm = render(home3, json.dumps(p3), FD_CACHE_TTL_S=86400)
check("F10 cache viva da sola → riga 2 col countdown",
      "\n" in warm and "cache" in plain(warm).split("\n")[1], warm)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto zen fase 1+2 rispettato")
