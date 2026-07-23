#!/usr/bin/env python3
"""On-demand text status — the statusline for clients that can't see one.

Remote clients (smartphone, web) don't render the terminal statusline: this
script rebuilds the same segments from the on-disk state files and prints
them as text, visible on any client. Freshness is declared, never faked:
- budget ratio/effort: ALWAYS fresh (hooks write them every turn);
- 5H/7D quotas: as-of the last statusline render (quota-<account> file),
  with age — if no terminal renders, the data is old and says so;
- burn-rate: projected from the quota history's monotonic tail;
- delegations: current session's state file (CLAUDE_CODE_SESSION_ID);
- external: telemetry (today's cross-family + external-exec calls).

Usage: fd-status.py [--detail]   (from the project cwd; zero model tokens)
       --detail adds session delegations and the last task receipt.
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
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    return f"{mins // 60}h {mins % 60}m ago"


def main():
    detail = "--detail" in sys.argv[1:]
    lines = []
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    acct_name = Path(cfg).name
    acct = hashlib.sha256(cfg.encode()).hexdigest()[:8]

    # now: the one-line answer to "what is the plugin doing right now"
    bfile = BASE / "budgets" / f"{cwd_slug(os.getcwd())}.json"
    b = None
    if bfile.is_file():
        try:
            b = json.loads(bfile.read_text())
        except Exception:
            lines.append("now: budget file unreadable")
    if isinstance(b, dict) and b.get("status") == "open":
        seg = f"now: budget OPEN — '{b.get('task')}'"
        sf = bfile.with_name(bfile.stem + ".state.json")
        exp = int(b.get("expected_output_tokens") or 0)
        if sf.is_file() and exp > 0:
            try:
                spent = int(json.loads(sf.read_text()).get("out", 0))
                seg += f", {spent / exp:.1f}× of estimate"
            except Exception:
                pass
        if b.get("effort"):
            seg += f", effort {b['effort']}"
        lines.append(seg)
        if b.get("warned"):
            lines.append("  ⚠ 2× checkpoint already hit — route was reassessed"
                         " or is due")
        if b.get("schema_warned"):
            lines.append("  ✕ ENFORCEMENT OFF: transcript unreadable "
                         "(schema_anomaly) — accounting unreliable, update "
                         "the plugin")
    elif isinstance(b, dict) and b.get("status") == "flagged":
        lines.append(f"now: ✕ budget FLAGGED 3× — post-mortem due "
                     f"('{b.get('task')}')")
    elif isinstance(b, dict):
        lines.append(f"now: no open budget (last: {b.get('status')})")
    else:
        lines.append("now: no open budget for this cwd")

    # Plan quotas (as-of the last statusline render)
    qf = BASE / f"quota-{acct}.json"
    if not qf.is_file():
        qf = BASE / "quota.json"
    if qf.is_file():
        try:
            q = json.loads(qf.read_text())
            parts = []
            if q.get("five_hour_used_pct") is not None:
                parts.append(f"5H {q['five_hour_used_pct']:.0f}% used")
            if q.get("weekly_used_pct") is not None:
                parts.append(f"7D {q['weekly_used_pct']:.0f}% used")
            lines.append(f"quotas [{acct_name}]: " + " · ".join(parts)
                         + f"  (as-of {age_str(qf.stat().st_mtime)} — "
                         f"updated only by a statusline render)")
        except Exception:
            lines.append(f"quotas [{acct_name}]: file unreadable")
    else:
        lines.append(f"quotas [{acct_name}]: n/a — no statusline render "
                     f"from this account yet")

    # 7D burn-rate: projection from the LAST MONOTONIC tail of the quota
    # history (the weekly reset drops the % — only the stretch after the
    # last reset is extrapolated). Real signal required: ≥3 samples, ≥3h
    # span, >0.5% growth — otherwise silence, never an invented projection.
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
                    break  # higher quota going backwards = reset: stop
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
                        f"burn-rate 7D: ~{rate:.1f}%/h (last "
                        f"{span_h:.0f}h) — at this pace 100% "
                        f"~{eta.astimezone().strftime('%a %d/%m %H:%M')}")
    except Exception:
        pass

    # External executors/verifiers today (cross-family verification +
    # external_exec). Config present but zero calls → today's free credit
    # is dormant (it resets daily): a signal, not a fault.
    xf_cfg = None
    try:
        cf = BASE / "cross-family.json"
        if cf.is_file():
            xf_cfg = json.loads(cf.read_text())
    except Exception:
        pass
    try:
        provs = (xf_cfg or {}).get("providers") or {}
        # Finestra per provider da limits.reset {period: daily, tz: IANA}:
        # il conteggio parte dall'ultima mezzanotte NEL TZ DEL PROVIDER
        # (Gemini azzera a midnight Pacific, non UTC). Senza dichiarazione:
        # giorno UTC come prima, e nessun orario inventato. Logica duplicata
        # dalla statusline: script standalone, nessun modulo condiviso.
        windows, resets = {}, {}
        try:
            from zoneinfo import ZoneInfo
            for pk, pv in provs.items():
                rst = ((pv or {}).get("limits") or {}).get("reset") or {}
                if rst.get("period") == "daily" and rst.get("tz"):
                    try:
                        st = datetime.now(ZoneInfo(str(rst["tz"]))).replace(
                            hour=0, minute=0, second=0, microsecond=0)
                        windows[pk] = st.astimezone(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ")
                        resets[pk] = (st + timedelta(days=1)).astimezone(
                            ).strftime("%H:%M")
                    except Exception:
                        pass
        except Exception:
            pass
        con = sqlite3.connect(BASE / "telemetry.db", timeout=0.5)
        con.execute("PRAGMA busy_timeout=500")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        floor = min([today] + list(windows.values()))
        counts = {}
        for ev, ts, pl in con.execute(
                "SELECT event, ts, payload FROM events WHERE event IN "
                "('verification','external_exec') AND ts >= ?", (floor,)):
            try:
                p = json.loads(pl or "{}")
            except json.JSONDecodeError:
                continue
            if ev == "verification" and p.get("kind") != "cross-family":
                continue
            prov = p.get("provider")
            if prov and str(ts) >= windows.get(prov, today):
                counts[prov] = counts.get(prov, 0) + 1
        con.close()
        if counts:
            parts = []
            for k, v in sorted(counts.items()):
                rpd = ((provs.get(k) or {}).get("limits") or {}).get("rpd")
                parts.append(f"{k}×{v}" + (f"/{rpd} rpd" if rpd else "")
                             + (f" (resets {resets[k]})" if k in resets
                                else ""))
            # Split free/paid: fail-closed, billing assente = paid. La riga
            # segnala spesa vera solo quando c'è (zero rumore altrimenti).
            nfree = sum(v for k, v in counts.items()
                        if (provs.get(k) or {}).get("billing") == "free")
            npaid = sum(counts.values()) - nfree
            lines.append("external today: " + ", ".join(parts)
                         + (f" — {nfree} free, {npaid} PAID"
                            if npaid else ""))
        elif xf_cfg:
            lines.append("external today: 0 calls — today's free tier "
                         "unused (daily reset)")
    except Exception:
        pass

    if detail:
        # Session delegations
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
            tok = BASE / "delegations" / f"{sid}.tok.json"
            reg = BASE / "delegations" / f"{sid}.json"
            try:
                if tok.is_file():
                    mm = (json.loads(tok.read_text()).get("models") or {})
                    if mm:
                        lines.append("session delegations: " + ", ".join(
                            f"{k} {v // 1000}k" if v >= 1000 else f"{k} {v}"
                            for k, v in sorted(mm.items(),
                                               key=lambda x: -x[1])))
                elif reg.is_file():
                    c = json.loads(reg.read_text())
                    lines.append("session delegations (declared): "
                                 + ", ".join(f"{k}×{v}" for k, v in
                                             sorted(c.items(),
                                                    key=lambda x: -x[1])))
            except Exception:
                pass
        # Last receipt for this cwd
        try:
            slug = cwd_slug(os.getcwd())
            recs = sorted((BASE / "receipts").glob(f"{slug}-*.json"),
                          key=lambda p: p.name)
            if recs:
                r = json.loads(recs[-1].read_text())
                exp = r.get("expected_output_tokens") or 0
                act = r.get("actual_output_tokens")
                ratio = (f", {act / exp:.1f}× of estimate"
                         if act is not None and exp else "")
                lines.append(f"last receipt: '{r.get('task')}' — "
                             f"{r.get('outcome')}{ratio}"
                             + (f", verify: {r['verify']}"
                                if r.get("verify") else ""))
        except Exception:
            pass

    print("\n".join(lines))


if __name__ == "__main__":
    main()
