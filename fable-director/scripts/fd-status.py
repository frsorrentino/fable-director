#!/usr/bin/env python3
"""Status testuale on-demand — la statusline per chi non vede la statusline.

I client remote (smartphone, web) non renderizzano la statusline del
terminale: questo script ricostruisce gli stessi segmenti dai file di stato
su disco e li stampa come testo, visibile su qualunque client. Freschezza
dichiarata, mai finta:
- budget ratio/effort: SEMPRE freschi (li scrivono gli hook a ogni turno);
- quote 5H/7D: as-of ultimo render dello statusline (file quota-<account>),
  col timestamp — se nessun terminale renderizza, il dato è vecchio e lo dice;
- [DLG]: state file della sessione corrente (CLAUDE_CODE_SESSION_ID);
- [XF]: telemetria (chiamate cross-family di oggi) + marker attivo.

Uso: fd-status.py  (dal cwd del progetto; zero argomenti, zero token modello)
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path.home() / ".claude" / "fable-director"


def cwd_slug(cwd):
    s = str(cwd).replace("\\", "/")
    base = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return f"{base}-{hashlib.sha256(s.encode()).hexdigest()[:8]}"


def age_str(mtime):
    mins = int((datetime.now(timezone.utc).timestamp() - mtime) / 60)
    if mins < 1:
        return "adesso"
    if mins < 60:
        return f"{mins} min fa"
    return f"{mins // 60}h {mins % 60}m fa"


def main():
    lines = []
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    acct_name = Path(cfg).name
    acct = hashlib.sha256(cfg.encode()).hexdigest()[:8]

    # Quote piano (as-of ultimo render statusline)
    qf = BASE / f"quota-{acct}.json"
    if not qf.is_file():
        qf = BASE / "quota.json"
    if qf.is_file():
        try:
            q = json.loads(qf.read_text())
            parts = []
            if q.get("five_hour_used_pct") is not None:
                parts.append(f"5H {q['five_hour_used_pct']:.0f}% usato")
            if q.get("weekly_used_pct") is not None:
                parts.append(f"7D {q['weekly_used_pct']:.0f}% usato")
            lines.append(f"quote [{acct_name}]: " + " · ".join(parts)
                         + f"  (as-of {age_str(qf.stat().st_mtime)} — "
                         f"aggiornate solo da un render statusline)")
        except Exception:
            lines.append(f"quote [{acct_name}]: file illeggibile")
    else:
        lines.append(f"quote [{acct_name}]: n/d — nessun render statusline "
                     f"da questo account")

    # Burn-rate 7D: proiezione dall'ultima coda MONOTONA della storia quota
    # (il reset settimanale fa scendere la % — si estrapola solo il tratto
    # dopo l'ultimo reset). Serve segnale vero: ≥3 campioni, ≥3h di span,
    # crescita >0.5% — altrimenti silenzio, mai una proiezione inventata.
    try:
        hf = BASE / f"quota-history-{acct}.jsonl"
        if hf.is_file():
            pts = []
            for ln in hf.read_text().splitlines()[-300:]:
                try:
                    r = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if r.get("w") is not None:
                    pts.append((datetime.fromisoformat(
                        str(r["ts"]).replace("Z", "+00:00")), float(r["w"])))
            tail = []
            for t, w in reversed(pts):
                if tail and w > tail[-1][1] + 1e-9:
                    break  # quota più alta andando indietro = reset: stop
                tail.append((t, w))
            tail.reverse()
            if len(tail) >= 3:
                span_h = (tail[-1][0] - tail[0][0]).total_seconds() / 3600
                dw = tail[-1][1] - tail[0][1]
                if span_h >= 3 and dw > 0.5:
                    rate = dw / span_h
                    eta = tail[-1][0] + timedelta(
                        hours=(100 - tail[-1][1]) / rate)
                    lines.append(
                        f"burn-rate 7D: ~{rate:.1f}%/h (ultime "
                        f"{span_h:.0f}h) — a questo ritmo 100% "
                        f"~{eta.astimezone().strftime('%a %d/%m %H:%M')}")
    except Exception:
        pass

    # Budget (sempre fresco: scritto dagli hook)
    bfile = BASE / "budgets" / f"{cwd_slug(os.getcwd())}.json"
    if bfile.is_file():
        try:
            b = json.loads(bfile.read_text())
            st = b.get("status")
            if st == "open":
                seg = f"budget: APERTO — '{b.get('task')}'"
                eff = b.get("effort")
                sf = bfile.with_name(bfile.stem + ".state.json")
                exp = int(b.get("expected_output_tokens") or 0)
                if sf.is_file() and exp > 0:
                    try:
                        spent = int(json.loads(sf.read_text()).get("out", 0))
                        seg += f", consumo {spent / exp:.1f}× della stima"
                    except Exception:
                        pass
                if eff:
                    seg += f", effort {eff}"
                if b.get("warned"):
                    seg += "  ⚠ checkpoint 2× già scattato"
                if b.get("schema_warned"):
                    seg += ("  ⚠ ENFORCEMENT SOSPESO: transcript illeggibile "
                            "(schema_anomaly) — contabilità inaffidabile, "
                            "aggiorna il plugin")
                lines.append(seg)
            elif st == "flagged":
                lines.append(f"budget: FLAGGED 3× — post-mortem dovuto "
                             f"('{b.get('task')}')")
            else:
                lines.append(f"budget: nessuno aperto (ultimo: {st})")
        except Exception:
            lines.append("budget: file illeggibile")
    else:
        lines.append("budget: nessuno aperto per questo cwd")

    # DLG — deleghe della sessione corrente
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
        tok = BASE / "delegations" / f"{sid}.tok.json"
        reg = BASE / "delegations" / f"{sid}.json"
        try:
            if tok.is_file():
                mm = (json.loads(tok.read_text()).get("models") or {})
                if mm:
                    lines.append("deleghe sessione: " + ", ".join(
                        f"{k} {v // 1000}k" if v >= 1000 else f"{k} {v}"
                        for k, v in sorted(mm.items(), key=lambda x: -x[1])))
            elif reg.is_file():
                c = json.loads(reg.read_text())
                lines.append("deleghe sessione (dichiarate): " + ", ".join(
                    f"{k}×{v}" for k, v in sorted(c.items(), key=lambda x: -x[1])))
        except Exception:
            pass

    # XF — executor/verifier esterni oggi (verification cross-family +
    # external_exec). Config presente ma zero chiamate → credito free tier
    # del giorno dormiente (si resetta ogni giorno): segnale, non colpa.
    xf_cfg = None
    try:
        cf = BASE / "cross-family.json"
        if cf.is_file():
            xf_cfg = json.loads(cf.read_text())
    except Exception:
        pass
    try:
        con = sqlite3.connect(BASE / "telemetry.db", timeout=0.5)
        con.execute("PRAGMA busy_timeout=500")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        counts = {}
        for ev, pl in con.execute(
                "SELECT event, payload FROM events WHERE event IN "
                "('verification','external_exec') AND ts >= ?", (today,)):
            try:
                p = json.loads(pl or "{}")
            except json.JSONDecodeError:
                continue
            if ev == "verification" and p.get("kind") != "cross-family":
                continue
            if p.get("provider"):
                counts[p["provider"]] = counts.get(p["provider"], 0) + 1
        con.close()
        if counts:
            provs = (xf_cfg or {}).get("providers") or {}
            parts = []
            for k, v in sorted(counts.items()):
                rpd = ((provs.get(k) or {}).get("limits") or {}).get("rpd")
                parts.append(f"{k}×{v}" + (f"/{rpd} rpd" if rpd else ""))
            lines.append("esterni oggi: " + ", ".join(parts))
        elif xf_cfg:
            lines.append("esterni oggi: 0 chiamate — free tier del giorno "
                         "inutilizzato (reset giornaliero)")
    except Exception:
        pass

    print("\n".join(lines))


if __name__ == "__main__":
    main()
