#!/usr/bin/env python3
"""Consiglio di modello ed effort per sessione (1.56.0, contratto claude-master
1.37 parte A): il relay lo legge da ~/.claude/fable-director/sessions/<sid>.json,
chiave "advice", e lo mostra nei selettori «Modello» ed «Effort» dell'app.

Regole (la prima che scatta vince; deterministiche, zero token):
  1. quota 5h >= 90%            -> Opus 5.5, effort al massimo medium
  2. settimana >= 85%           -> Opus 5.5, effort al massimo medium
  3. su Fable, finestra premium -> Opus 5.5 high + /advisor fable: Opus esegue,
     (7D / premium_weekly_fraction  Fable giudica (bound dichiarato in
     di plan-<acct>.json) >= 80%   plan-<acct>.json, mai una misura)
  4. sessione principale non di punta -> Opus 5.5: la principale pianifica e
     verifica, l'esecuzione va agli agenti
  5. effort xhigh/max           -> high: consuma quota in silenzio
  altrimenti la scelta attuale.

Un cambio di modello o di effort riscrive la cache: switch_cost_tokens = il
contesto attuale. when = "now" solo a contesto fresco (< 50k) e senza budget
aperto nel cwd; altrimenti "next_task" (dopo /clear o al compito nuovo). Il
consiglio non si applica mai da solo. Quota assente -> nessun numero inventato.

CLI:
  advice.py --session SID   ricalcola l'advice nello snapshot di SID
  advice.py --all           tutte le sessioni con snapshot < 6 h
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

FABLE, OPUS = "claude-fable-5-1", "claude-opus-5-5"
TOP = (FABLE, OPUS)
NAMES = {FABLE: "Fable 5.1", OPUS: "Opus 5.5"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FRESH_CTX = 50_000
LIVE_S = 6 * 3600


def _name(m):
    return NAMES.get(m, m or "?")


def _cap(effort, top):
    """effort ridotto a `top` se lo supera; sconosciuto -> top."""
    if effort in EFFORTS and EFFORTS.index(effort) <= EFFORTS.index(top):
        return effort
    return top


def _pct(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute(snap, quota, plan, budget_open, now):
    model = snap.get("model") or ""
    effort = snap.get("effort")
    ctx = int(snap.get("ctx_tokens") or 0)
    quota, plan = quota or {}, plan or {}
    r, w = _pct(quota.get("five_hour_used_pct")), _pct(quota.get("weekly_used_pct"))
    frac = _pct(plan.get("premium_weekly_fraction"))
    base = model.split("[")[0]
    is_fable = base.startswith(FABLE)

    if r is not None and r >= 90:
        m, e = OPUS, _cap(effort, "medium")
        reset = quota.get("five_hour_resets_at")
        at = time.strftime(" fino al reset delle %H:%M", time.localtime(int(reset))) \
            if reset else ""
        why = f"quota 5h al {r:.0f}%: Opus 5.5 a effort {e}{at}; al muro /low-priority"
    elif w is not None and w >= 85:
        m, e = OPUS, _cap(effort, "medium")
        why = f"settimana al {w:.0f}%: Opus 5.5 a effort {e} per i lavori nuovi"
    elif is_fable and w is not None and frac and w / frac >= 80:
        m, e = OPUS, _cap(effort, "high")
        why = (f"finestra Fable ≤{min(100, w / frac):.0f}%: Opus 5.5 esegue, "
               f"/advisor fable per i giudizi")
    elif base and not any(base.startswith(t) for t in TOP):
        m, e = OPUS, _cap(effort, "high")
        why = "la sessione principale pianifica e verifica: Opus 5.5; l'esecuzione va agli agenti"
    elif effort in ("xhigh", "max"):
        m, e = model, "high"
        why = f"effort {effort} consuma quota in silenzio: high basta, alzalo per il compito difficile"
    else:
        m, e = model, effort
        why = (f"quota ok (5h {r:.0f}%, settimana {w:.0f}%): {_name(m)} {e} va bene"
               if r is not None and w is not None else f"{_name(m)} {e} va bene")

    differs = (m.split("[")[0] != base) or (e != effort)
    when = "now" if ctx < FRESH_CTX and not budget_open else "next_task"
    if differs and when == "next_task":
        why += "; cambia dopo /clear o al compito nuovo"
    return {"model": m, "effort": e, "reason": why[:200], "switch_cost_tokens": ctx,
            "at": int(now), "source": "fable-director", "when": when, "differs": differs}


# --- I/O ------------------------------------------------------------------

BASE = Path.home() / ".claude" / "fable-director"


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def default_account():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return hashlib.sha256(cfg.encode()).hexdigest()[:8]


def cwd_slug(cwd):
    s = str(cwd).replace("\\", "/")
    base = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return f"{base}-{hashlib.sha256(s.encode()).hexdigest()[:8]}"


def budget_is_open(cwd):
    if not cwd:
        return False
    b = _load(BASE / "budgets" / f"{cwd_slug(cwd)}.json")
    return bool(b) and b.get("status") == "open"


def for_snapshot(snap, now=None):
    acct = snap.get("account") or default_account()
    return compute(snap, _load(BASE / f"quota-{acct}.json"),
                   _load(BASE / f"plan-{acct}.json"),
                   budget_is_open(snap.get("cwd")), now or time.time())


def rewrite(sf):
    snap = _load(sf)
    if not isinstance(snap, dict) or not snap.get("model"):
        return False
    snap["advice"] = for_snapshot(snap)
    tmp = sf.with_name(f"{sf.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(snap))
    os.replace(tmp, sf)
    return True


def main(argv):
    sd = BASE / "sessions"
    if len(argv) == 2 and argv[0] == "--session":
        sf = sd / f"{argv[1].replace('/', '-')}.json"
        if not rewrite(sf):
            print(f"no snapshot with a model for {argv[1]}", file=sys.stderr)
            return 1
        return 0
    if argv == ["--all"]:
        n = 0
        for sf in sd.glob("*.json"):
            try:
                if time.time() - sf.stat().st_mtime > LIVE_S:
                    continue
            except OSError:
                continue
            n += rewrite(sf)
        print(f"advice: {n} sessions")
        return 0
    print(__doc__.split("CLI:")[1].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
