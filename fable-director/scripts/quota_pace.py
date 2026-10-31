#!/usr/bin/env python3
"""Ritmo settimanale per gli account senza il limite di 5 ore.

L'assenza si riconosce dal dato dell'account (quota-<hash>.json), mai dal nome
del piano. Tre forme: chiave assente, null, valore vecchio con reset scaduto.
Finche' una finestra di 5 ore e' viva tutto resta com'era: state() ritorna None.

Il residuo e' quota settimanale restante diviso i giorni al reset. Sotto un
giorno il divisore resta 1: il residuo non supera mai quello che resta.

Dentro una settimana la quota non scende, quindi un file fermo sbaglia solo per
difetto: il residuo letto e' un tetto. Per questo qui non c'e' il limite di
freschezza dei controlli sulle 5 ore, e un deny su un file fermo resta giusto.
Eccezione: valore scaduto su file fermo. E' una sessione inattiva di cui non si
sa ancora niente, e si tace come prima.
"""
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

# Consumo orario oltre cui il gate avvisa. Misurato sullo storico dei due account
# (10/2026, ~300 campioni l'uno): una finestra di 5 ore piena valeva il 13,1% e il
# 24,5% della settimana, cioe' 2,6 e 4,9 %/h per arrivare al muro. A 5 %/h il muro
# di una volta sarebbe scattato su entrambi e la settimana intera finirebbe in 20
# ore; sta sopra il p90 dei tratti attivi dei due account (5,1 e 2,3 %/h), quindi
# non suona a ogni lavoro normale. Override: weekly_burn_pct_h_ceiling in
# cost-checkpoint.json.
BURN_CEILING_PCT_H = 5.0
BURN_WINDOW_S = 3600
BURN_MIN_SPAN_S = 1200      # sotto i 20 minuti un solo scatto dell'1% sembrerebbe un'impennata
PRESSED_WEEKLY_PCT = 85.0   # la soglia che advice.py usava gia' per la settimana
QUOTA_SNAPSHOT_MAX_AGE_S = 600


def base_dir():
    return Path.home() / ".claude" / "fable-director"


def acct_hash():
    return hashlib.sha256((os.environ.get("CLAUDE_CONFIG_DIR")
                           or str(Path.home() / ".claude")).encode()).hexdigest()[:8]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def five_hour_used(q, now=None):
    """Il valore delle 5 ore se la finestra e' viva, altrimenti None."""
    if not isinstance(q, dict):
        return None
    used = _num(q.get("five_hour_used_pct"))
    if used is None:
        return None
    reset = _num(q.get("five_hour_resets_at"))
    if reset is not None and reset <= (now or time.time()):
        return None
    return used


def _read():
    """(quota, mtime) del file dell'account, o (None, None)."""
    base = base_dir()
    for qf in (base / f"quota-{acct_hash()}.json", base / "quota.json"):
        try:
            if qf.is_file():
                q = json.loads(qf.read_text())
                return (q if isinstance(q, dict) else None), qf.stat().st_mtime
        except (OSError, ValueError):
            return None, None
    return None, None


def burn_pct_h(now=None, acct=None):
    """%/h settimanali nell'ultima ora, dallo storico della statusline. Si divide
    per il tempo fino ad ADESSO: chi ha smesso di consumare scende da solo."""
    now = now or time.time()
    pts = []
    try:
        hf = base_dir() / f"quota-history-{acct or acct_hash()}.jsonl"
        if not hf.is_file():
            return 0.0
        for ln in hf.read_text().splitlines()[-300:]:
            r = json.loads(ln)
            w = _num(r.get("w"))
            if w is None:
                continue
            t = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).timestamp()
            if now - t <= BURN_WINDOW_S:
                pts.append((t, w))
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0
    # un calo e' il reset settimanale: conta solo il tratto dopo
    for i in range(len(pts) - 1, 0, -1):
        if pts[i][1] < pts[i - 1][1]:
            pts = pts[i:]
            break
    if len(pts) < 2:
        return 0.0
    grown = pts[-1][1] - pts[0][1]
    return max(grown, 0.0) / (max(now - pts[0][0], BURN_MIN_SPAN_S) / 3600.0)


def burn_ceiling():
    try:
        cf = base_dir() / "cost-checkpoint.json"
        if cf.is_file():
            v = _num(json.loads(cf.read_text()).get("weekly_burn_pct_h_ceiling"))
            if v:
                return v
    except (OSError, ValueError, AttributeError):
        pass
    return BURN_CEILING_PCT_H


def residue_pct(weekly, reset, now=None):
    """(%/giorno, giorni al reset) dal dato settimanale; None se non c'e'."""
    now = now or time.time()
    w, r = _num(weekly), _num(reset)
    if w is None or r is None or r <= now:
        return None
    days = (r - now) / 86400.0
    return max(100.0 - w, 0.0) / max(days, 1.0), days


def state(now=None):
    """None = finestra di 5 ore viva, o nessun dato su cui dire altro: valgono i
    controlli di sempre. {"blind": True} = niente 5 ore e niente settimana.
    Altrimenti il ritmo: weekly, days, remaining, residue, burn, ceiling, over,
    pressed (consumo sopra soglia o settimana oltre l'85%)."""
    now = now or time.time()
    q, mtime = _read()
    if q is None or five_hour_used(q, now) is not None:
        return None
    if _num(q.get("five_hour_used_pct")) is not None and now - mtime > QUOTA_SNAPSHOT_MAX_AGE_S:
        return None
    w = _num(q.get("weekly_used_pct"))
    res = residue_pct(w, q.get("weekly_resets_at"), now)
    if res is None:
        return {"blind": True}
    burn, ceiling = burn_pct_h(now), burn_ceiling()
    return {"blind": False, "weekly": w, "days": res[1], "remaining": max(100.0 - w, 0.0),
            "residue": res[0], "resets_at": _num(q.get("weekly_resets_at")),
            "burn": burn, "ceiling": ceiling, "over": burn >= ceiling,
            "pressed": burn >= ceiling or w >= PRESSED_WEEKLY_PCT}
