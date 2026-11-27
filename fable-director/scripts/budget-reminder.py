#!/usr/bin/env python3
"""Hook PostToolUse (Bash|Write|Edit): promemoria del budget dentro i turni lunghi.

Il buco che chiude. Lo Stop hook parla solo a fine turno, e solo a 2× e 3×:
in un turno lungo il modello lavora per decine di minuti senza mai sapere
quanto del budget dichiarato ha gia' speso (misurato su 14.000+ messaggi da
Ramp Labs: un agente non cita mai da solo il budget scritto nel prompt).
Questo hook, a ogni chiamata di uno strumento di lavoro, controlla se e'
passato l'intervallo (default 600 s, `FD_REMIND_GAP_S` o `remind_gap_s` in
cost-checkpoint.json; 0 = spento) dall'apertura del budget o dall'ultimo
promemoria e, se si', aggiorna il conteggio con lo stesso scan incrementale
dello Stop hook e manda al modello UNA riga (additionalContext): consumo,
rapporto, riserva in volo e margine prima del checkpoint 2×.

Costo: una lettura del budget file per chiamata; lo scan solo quando il
promemoria e' dovuto. Un budget senza consumo tace. Advisory puro: exit 0
sempre, fail-open su qualunque errore.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_GAP_S = 600


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def gap_s():
    env = os.environ.get("FD_REMIND_GAP_S")
    if env is not None and env.strip() != "":
        try:
            return max(int(env), 0)
        except ValueError:
            pass
    cfg = Path.home() / ".claude" / "fable-director" / "cost-checkpoint.json"
    try:
        v = json.loads(cfg.read_text()).get("remind_gap_s")
        if v is not None:
            return max(int(v), 0)
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_GAP_S


def load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        return
    gap = gap_s()
    if gap <= 0:
        return
    cwd = data.get("cwd") or os.getcwd()
    # Slug: identico a cwd_slug() in fd-telemetry.py (canonico + hash)
    s = str(cwd).replace("\\", "/")
    slug = (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
            + "-" + hashlib.sha256(s.encode()).hexdigest()[:8])
    bfile = Path.home() / ".claude" / "fable-director" / "budgets" / f"{slug}.json"
    if not bfile.is_file():
        return
    budget = json.loads(bfile.read_text())
    if budget.get("status") != "open":
        return
    declared = parse_ts(budget.get("declared_at"))
    if declared is None:
        return
    now = datetime.now(timezone.utc)
    if (now - declared).total_seconds() > 86400:
        return  # budget abbandonato: lo marca lo Stop hook
    last = parse_ts(budget.get("reminded_at")) or declared
    if (now - last).total_seconds() < gap:
        return
    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return
    # Stesso scan incrementale dello Stop hook (offset nello state file):
    # il conteggio avanza qui a meta' turno, lo Stop riparte da dove e' arrivato.
    sbc = load("stop-budget-check")
    state_file = bfile.with_name(bfile.stem + ".state.json")
    actual_out, actual_in, _, _ = sbc.sum_session_incremental(
        Path(transcript), declared, state_file, budget.get("declared_at"),
        sid=data.get("session_id"))
    if actual_out <= 0 and actual_in <= 0:
        return  # niente speso, niente da ricordare
    rm = load("budget-reserve")
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    def mutate(b):
        if b.get("status") != "open":
            return False, False
        b["reminded_at"] = stamp
        return True, True

    ok, fresh = rm.locked_update(bfile, mutate)
    if not ok:
        return
    line = rm.reminder_line(fresh, actual_out, actual_in, now)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": line}}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # advisory: non deve MAI disturbare la sessione
