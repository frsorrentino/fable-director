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
- identita: "✦ FABLE 5" (lo spazio del display_name si conserva), niente
  quadre sui segmenti zen; cmp/fail restano deviazioni accese;
- badge caveman adottato: [CAVEMAN] → "caveman" in 172, IN CODA a riga 1 e
  primo a cadere per larghezza; badge dal formato sconosciuto passa intatto;
- riga 1 degrada solo nella decorazione: badge → gauge ctx → orari di
  reset; percentuali, sigle e allarmi sopravvivono a qualunque larghezza;
- orario di reset SENZA glifo: staccato da uno spazio, in 239 (la forma
  dell orario lo dichiara anche senza colore);
- cache: crea la riga 2 solo quando e azionabile (TTL ≤10m); piena o
  scaduta-fredda si mostra solo se la riga 2 esiste gia;
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
            model="Fable 5", pr=None, extra_bucket=None):
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
    if pr is not None:
        d["pr"] = pr
    if extra_bucket:
        d["rate_limits"][extra_bucket] = {"used_percentage": 42}
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
check("Z2 identita ✦ MODELLO (spazio conservato), niente quadre zen",
      "✦ FABLE 5" in plain(line) and "FABLE5" not in plain(line)
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
check("Z10b badge in CODA a riga 1, non piu in testa",
      plain(withb).split("\n")[0].endswith("caveman")
      and not plain(withb).startswith("caveman"), plain(withb))

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
check("F5 provider con reset → residuo n/rpd HH:MM (nessun glifo)",
      re.search(r"gemini 1/1500 \d{2}:\d{2}", plain(resid)) is not None,
      plain(resid))
check("F6 provider senza reset → solo ×N, nessun orario inventato",
      re.search(r"mystery×1(?!/)", plain(resid)) is not None
      and "mystery×1→" not in plain(resid)
      and not re.search(r"mystery×1\s+\d{2}:\d{2}", plain(resid)), plain(resid))

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

# F9-F11: la cache CREA la riga 2 solo se e azionabile. Scaduta da freddo =
#     rumore permanente su sessioni fredde; PIENA = nessuna decisione cambia
#     (a riposo rendeva la seconda riga di fatto permanente); agli sgoccioli
#     (TTL ≤10m) si: li il timing di una delega cambia davvero (asse 6).
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
check("F10 cache PIENA da sola → nessuna riga 2 (non e azionabile)",
      "\n" not in warm.strip() and "cache" not in plain(warm), warm)

# TTL 7500 s con ultima attivita 2h fa → 300 s residui: la finestra in cui
# il timing di una delega cambia. Da sola deve tirare su la riga 2.
short = render(home3, json.dumps(p3), FD_CACHE_TTL_S=7500)
check("F10b cache agli sgoccioli (≤10m) da sola → riga 2 col countdown",
      "\n" in short and "cache" in plain(short).split("\n")[1], short)

# F11: orologio cache — glifo a quarti dal TTL residuo (2h su 24h → ●),
# visibile in compagnia (budget aperto): informazione gratis, zero righe in piu
b3 = home3 / ".claude" / "fable-director" / "budgets"
b3.mkdir(parents=True, exist_ok=True)
(b3 / f"{slug}.json").write_text(json.dumps(
    {"status": "open", "task": "t", "effort": "high",
     "declared_at": "2026-07-23T10:00:00Z"}))
warm2 = render(home3, json.dumps(p3), FD_CACHE_TTL_S=86400)
check("F11 cache ricca in compagnia → orologio ● pieno",
      re.search(r"cache ● \d+m", plain(warm2)) is not None
      and "bdg ok·high" in plain(warm2), plain(warm2))
expc = render(home3, json.dumps(p3), FD_CACHE_TTL_S=300)
check("F12 cache exp con compagnia → ○ exp in riga 2",
      "cache ○ exp" in plain(expc)
      and "bdg ok·high" in plain(expc), plain(expc))

# ---------- fase 4: link OSC 8, pr, bound Fable, bucket ignoti ----------
print("zen fase 4:")

OSC = "\x1b]8;;"
home4 = tmp / "home4"
home4.mkdir()
fd4 = home4 / ".claude" / "fable-director"
fd4.mkdir(parents=True)


def acct_of(home):
    return hashlib.sha256(str(home / ".claude").encode()).hexdigest()[:8]


# L1: senza plan file → nessun OSC 8, nessun bound
off = render(home4, payload())
check("L1 default → nessun link, nessun bound",
      OSC not in off and "✦≤" not in plain(off), off)

# plan file: links on + frazione premium dichiarata
(fd4 / f"plan-{acct_of(home4)}.json").write_text(json.dumps(
    {"statusline_links": True, "premium_weekly_fraction": 0.5}))

# L2: links on → 7D wrappato verso usage, modello verso status page
on = render(home4, payload())
check("L2 links on → 7D linkato alla pagina usage",
      "]8;;https://claude.ai/settings/usage" in on, on)
check("L3 links on → modello linkato alla status page",
      "]8;;https://status.anthropic.com" in on, on)

# L4: bound Fable — 7D 13%, frazione 0.5, modello Fable → ✦≤26%
b13 = render(home4, payload(seven=13))
check("L4 modello Fable, 7D 13% → ✦≤26%",
      "✦≤26%" in plain(b13), plain(b13))
# L5: stessa sessione su Sonnet → bound assente (condizionale al modello)
b_son = render(home4, payload(seven=13, model="Sonnet 5"))
check("L5 modello Sonnet → bound assente",
      "✦≤" not in plain(b_son) and "✦?" not in plain(b_son), plain(b_son))
# L6: bound saturo (7D 60% ≥ 50%) → ✦? mai un numero finto
b_sat = render(home4, payload(seven=60))
check("L6 7D 60% → bound saturo ✦?",
      "✦?" in plain(b_sat) and "✦≤" not in plain(b_sat), plain(b_sat))

# L7: pr aperta → segmento in riga 2, linkato al pr.url
prd = {"number": 42, "url": "https://github.com/o/r/pull/42",
       "review_state": "approved"}
wpr = render(home4, payload(pr=prd))
check("L7 pr aperta → 'pr #42' in riga 2, linkato",
      "pr #42" in plain(wpr).split("\n")[-1]
      and "]8;;https://github.com/o/r/pull/42" in wpr, wpr)

# L8: bucket rate_limits sconosciuto → registrato nel quota file
render(home4, payload(extra_bucket="seven_day_opus"))
qf4 = json.loads((fd4 / f"quota-{acct_of(home4)}.json").read_text())
check("L8 bucket ignoto → unknown_buckets nel quota file",
      qf4.get("unknown_buckets") == ["seven_day_opus"], str(qf4))

# ---------- fase 5: degradazione riga 1 (decorazione si, dato mai) ----------
print("zen fase 5:")


def visible(s):
    """Larghezza PERCEPITA: via SGR e via le marcature OSC 8 (le URL non
    occupano celle sullo schermo — se le contassimo qui misureremmo una
    riga che nessuno vede, che e esattamente il bug del 2026-07-28)."""
    s = re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\\\?", "", s)
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


home5 = tmp / "home5"
home5.mkdir()
r1_full = visible(render(home5, payload(), CAVEMAN_STATUSLINE_SH=badge,
                         COLUMNS=200)).split("\n")[0]
RESET = "1\u00a0Jan"  # resets_at del payload: oltre 24h → giorno + mese
check("D1 largo → badge in coda, gauge e orari di reset tutti presenti",
      r1_full.endswith("caveman") and "▓░░░░░░░" in r1_full
      and r1_full.count(RESET) == 2, r1_full)
check("D1b orario staccato e in 239, nessun glifo tra % e ora",
      "\x1b[38;5;239m 1\u00a0Jan" in render(home5, payload(), COLUMNS=200)
      and "↻" not in r1_full and "%→" not in r1_full, r1_full)

# Un carattere meno del necessario: cade il badge, NON la gauge.
d1 = visible(render(home5, payload(), CAVEMAN_STATUSLINE_SH=badge,
                    COLUMNS=len(r1_full) - 1)).split("\n")[0]
check("D2 -1 char → cade il badge, la gauge resta",
      "caveman" not in d1 and "▓░░░░░░░" in d1 and len(d1) <= len(r1_full) - 1,
      d1)

# Ancora un carattere meno: cade la gauge, la percentuale ctx resta.
d2 = visible(render(home5, payload(), CAVEMAN_STATUSLINE_SH=badge,
                    COLUMNS=len(d1) - 1)).split("\n")[0]
check("D3 ancora stretto → cade la gauge, ctx 12%/1M resta",
      "▓" not in d2 and "░" not in d2 and "ctx 12%/1M" in d2
      and RESET in d2, d2)

# Ultimo gradino: cadono gli orari di reset, le percentuali di quota no.
d3 = visible(render(home5, payload(), CAVEMAN_STATUSLINE_SH=badge,
                    COLUMNS=len(d2) - 1)).split("\n")[0]
check("D4 minimo → cadono gli orari di reset, restano 5H/7D e ctx",
      RESET not in d3 and "5H 10%" in d3 and "7D 10%" in d3
      and "ctx 12%/1M" in d3, d3)

# D5: sotto il minimo il dato NON si taglia oltre — meglio andare a capo che
# mentire su una quota. La riga resta identica a D4.
d4 = visible(render(home5, payload(), CAVEMAN_STATUSLINE_SH=badge,
                    COLUMNS=20)).split("\n")[0]
check("D5 sotto il minimo → il dato non si taglia oltre", d4 == d3, d4)

# D6 (regressione 2026-07-28): con i link OSC 8 attivi la riga non degrada a
# parita di testo visibile. Il vecchio strip_esc non toglieva le marcature
# OSC, quindi ogni URL veniva contata nella larghezza e la riga si accorciava
# senza motivo. home4 ha gia il plan file con statusline_links: true.
linked = render(home4, payload(), COLUMNS=200)
r1_linked = visible(linked).split("\n")[0]
exact = render(home4, payload(), COLUMNS=len(r1_linked))
check("D6 link OSC 8 → le URL non consumano larghezza",
      "\x1b]8;;" in exact and visible(exact).split("\n")[0] == r1_linked, exact)

print()
if FAILS:
    print(f"FAIL: {len(FAILS)} — " + ", ".join(FAILS))
    sys.exit(1)
print("OK: contratto zen fase 1+2+4 rispettato")
