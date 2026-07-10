#!/bin/bash
# fable-director — statusline:
#   [MODEL]         modello attivo (model.display_name)
#   [CTX %]         riempimento context window della conversazione
#   [5H %→HH:MM]    quota piano finestra 5 ore + orario reset locale
#   [7D %]          quota piano settimanale (se il campo esiste)
#   [BDG r×·eff]    pre-budget fable-director: ratio live output consumato/atteso
#                   (stessa contabilità dello Stop hook, incrementale) + effort
#                   dichiarato; verde <2×, giallo ≥2×, rosso ≥3× (flagged: 3×).
#                   Senza transcript degrada a ok|2×|3× dal solo budget file.
# Antepone il badge caveman se il plugin è presente nel profilo attivo.
#
# Setup in settings.json:
#   "statusLine": { "type": "command", "command": "bash \"<path>/scripts/statusline-ctx.sh\"" }

input=$(cat)

# Diagnostica: se esiste il flag file, salva l'ultimo stdin ricevuto (per capire
# quali campi il piano/account corrente espone). Attiva: touch <config>/fable-director/.statusline-debug
FD_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/fable-director"
[ -f "$FD_DIR/.statusline-debug" ] && printf '%s' "$input" > "$FD_DIR/statusline-last.json" 2>/dev/null

# Badge caveman se installato nel profilo attivo (override: CAVEMAN_STATUSLINE_SH)
CAVEMAN_SH="${CAVEMAN_STATUSLINE_SH:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/marketplaces/caveman/src/hooks/caveman-statusline.sh}"
badge=""
[ -f "$CAVEMAN_SH" ] && badge=$(printf '%s' "$input" | bash "$CAVEMAN_SH")

# Tutte le metriche in UNA passata python (statusline gira spesso: un solo processo).
# Campi assenti → "-" → il segmento si omette. Il budget file è di fable-director
# (fd-telemetry.py budget-open / stop-budget-check.py): qui SOLO lettura.
read -r model pct rl rlt wk wkt bdg xf dlg <<EOF
$(printf '%s' "$input" | python3 -c '
import json,sys,os,time
from pathlib import Path
model=pct=rl=rlt=wk=wkt=bdg=xf=dlg="-"
def fmt_reset(ts):
    # entro 24h: orario; oltre: "6 Jul"/"6 lug" (giorno + mese secondo il locale)
    try:
        ts=int(ts)
        lt=time.localtime(ts)
        if ts-time.time() < 86400: return time.strftime("%H:%M", lt)
        #   = spazio unificatore: si vede come spazio ma non spezza il read shell
        mon = time.strftime("%b", lt)
        return f"{lt.tm_mday}\u00a0{mon}"
    except Exception:
        return None
try:
    d=json.load(sys.stdin)
    m=d.get("model",{}).get("display_name")
    if m: model=str(m).replace(" ","")[:12].upper()
    p=d.get("context_window",{}).get("used_percentage")
    if p is not None: pct=f"{p:.0f}"
    fh=d.get("rate_limits",{}).get("five_hour",{})
    r=fh.get("used_percentage")
    if r is not None: rl=f"{r:.0f}"
    rlt=fmt_reset(fh.get("resets_at")) or "-"
    w=None; w_reset=None
    for k in ("seven_day","weekly","seven_days"):
        wd=d.get("rate_limits",{}).get(k,{})
        w=wd.get("used_percentage")
        if w is not None:
            wk=f"{w:.0f}"
            w_reset=wd.get("resets_at")
            wkt=fmt_reset(w_reset) or "-"
            break
    # Ponte quota → gate: salvo la quota vista qui, cosi il checkpoint costo
    # del pre-delegation-gate puo abbassare la soglia a quota scarsa. Best-effort.
    try:
        q={}
        if r is not None: q["five_hour_used_pct"]=round(float(r),1)
        if w is not None: q["weekly_used_pct"]=round(float(w),1)
        if w_reset: q["weekly_resets_at"]=w_reset
        if q:
            qd=Path.home()/".claude"/"fable-director"
            qd.mkdir(parents=True,exist_ok=True)
            qf=qd/"quota.json"
            new=json.dumps(q)
            # write-if-changed + atomica: render frequente, lettori concorrenti
            old_q=qf.read_text() if qf.is_file() else None
            if new!=old_q:
                tmpq=qf.with_name(f"quota.json.{os.getpid()}.tmp")
                tmpq.write_text(new); os.replace(tmpq,qf)
    except Exception:
        pass
    cwd=d.get("cwd") or os.getcwd()
    # slug identico a cwd_slug() in fd-telemetry.py (canonico + hash)
    import hashlib as _hl, re as _re
    _s=str(cwd).replace("\\","/")
    slug=(_re.sub(r"[^A-Za-z0-9]+","-",_s).strip("-")
          +"-"+_hl.sha256(_s.encode()).hexdigest()[:8])
    bf=Path.home()/".claude"/"fable-director"/"budgets"/f"{slug}.json"
    b_open=None; b_status=None; b_warned=False; b_eff=None; b_exp=0
    if bf.is_file():
        b=json.loads(bf.read_text())
        b_status=b.get("status")
        if b_status=="open":
            b_open=b; b_warned=bool(b.get("warned"))
            b_eff=b.get("effort")
            b_exp=int(b.get("expected_output_tokens") or 0)
    # [DLG] token EFFETTIVI delegati per modello reale, dal transcript della
    # sessione con scan INCREMENTALE (state file con offset: a ogni refresh si
    # leggono solo le righe nuove — mai rescan). Record sidechain (subagent)
    # → bucket per message.model; "≡" = stesso modello del main loop.
    # Fallback senza transcript: registro dichiarato del gate, prefisso "≈".
    def norm(m):
        # Confronta display_name ("Claude Sonnet 5") e model id anche versionato
        # ("claude-sonnet-5-20260701"): via prefisso claude in ogni forma,
        # separatori e suffisso data — altrimenti "≡" non combacia mai.
        import re as _re
        s=str(m).lower().replace(" ","").replace("-","").replace(".","")
        s=_re.sub(r"^claude","",s)
        s=_re.sub(r"20\d{6}$","",s)
        return s.upper()
    def fmtk(n):
        return f"{n/1000:.0f}k" if n >= 1000 else str(n)
    sid=d.get("session_id")
    # sid entra nei path di stato: allowlist stretta o niente (skip DLG)
    import re as _res
    if sid and not _res.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(sid)): sid=None
    tp=d.get("transcript_path")
    main_norm=norm(m or "")
    # Ratio budget live: STESSA contabilità dello Stop hook (find_usage
    # ricorsivo su ogni record, output dopo declared_at, timestamp mancante
    # → inferenza posizionale) ma incrementale: il contatore vive nello state
    # file insieme a offset, si azzera quando cambia declared_at.
    # NB: commenti senza apostrofi — questo blocco vive in una stringa
    # shell single-quoted, un apostrofo la tronca.
    from datetime import datetime as _dtb
    def pts(s):
        try: return _dtb.fromisoformat(str(s).replace("Z","+00:00"))
        except Exception: return None
    def usages(o):
        if isinstance(o,dict):
            u=o.get("usage")
            if isinstance(u,dict) and "output_tokens" in u: yield u
            for v in o.values(): yield from usages(v)
        elif isinstance(o,list):
            for v in o: yield from usages(v)
    b_decl=pts(b_open.get("declared_at")) if b_open else None
    spent=None
    if sid and tp and Path(tp).is_file():
        sf=Path.home()/".claude"/"fable-director"/"delegations"/f"{sid}.tok.json"
        state={"off":0,"models":{}}
        if sf.is_file():
            try: state=json.loads(sf.read_text())
            except Exception: pass
        bst=state.get("budget") or {}
        if b_open and bst.get("declared")!=b_open.get("declared_at"):
            bst={"declared":b_open.get("declared_at"),"out":0}  # budget nuovo
        size=Path(tp).stat().st_size
        if size < state.get("off",0):  # transcript ruotato
            state={"off":0,"models":{}}
            if b_open: bst={"declared":b_open.get("declared_at"),"out":0}
        if size > state.get("off",0):
            last_ts=pts(state.get("last_ts"))
            with open(tp, errors="replace") as fh:
                fh.seek(state.get("off",0))
                for line in fh:
                    try: rec=json.loads(line)
                    except Exception: continue
                    rts=pts(rec.get("timestamp"))
                    ts=rts or last_ts
                    if rts: last_ts=rts
                    if b_decl and ts and ts>=b_decl:
                        for u in usages(rec):
                            bst["out"]=bst.get("out",0)+(u.get("output_tokens") or 0)
                    if not rec.get("isSidechain"): continue
                    msg=rec.get("message") or {}
                    u=msg.get("usage") or {}
                    out=u.get("output_tokens") or 0
                    if not out: continue
                    mm=norm(msg.get("model") or "?")
                    key="≡" if mm==main_norm else (msg.get("model") or "?").replace("claude-","").upper()[:10]
                    state["models"][key]=state["models"].get(key,0)+out
                state["off"]=fh.tell()
            if last_ts: state["last_ts"]=last_ts.isoformat()
            state["budget"]=bst if b_open else {}
            sf.parent.mkdir(parents=True, exist_ok=True)
            tmps=sf.with_name(f"{sf.name}.{os.getpid()}.tmp")
            tmps.write_text(json.dumps(state)); os.replace(tmps,sf)  # atomica
        if b_open and bst.get("declared")==b_open.get("declared_at"):
            spent=int(bst.get("out",0))
        mm=state.get("models") or {}
        if mm:
            dlg=",".join(f"{k} {fmtk(v)}" for k,v in
                         sorted(mm.items(), key=lambda x:-x[1])[:4])
    elif sid:
        df=Path.home()/".claude"/"fable-director"/"delegations"/f"{sid}.json"
        if df.is_file():
            c=json.loads(df.read_text())
            parts=[("≡" if k=="inherit" else str(k).replace("claude-","").upper()[:10])+f"×{v}"
                   for k,v in sorted(c.items(), key=lambda x:-x[1])]
            if parts: dlg="≈"+",".join(parts[:4])
    # [BDG] = classe:testo — la classe colore (g/y/r) si decide qui, la shell
    # mappa solo ANSI. flagged → 3×; open → ratio live (soglie identiche allo
    # Stop hook: verde <2×, giallo ≥2×, rosso ≥3×) + effort dichiarato;
    # senza transcript degrada a ok|2× dal solo budget file.
    if b_status=="flagged":
        bdg="r:3×"
    elif b_status=="open":
        eff=("·"+str(b_eff)) if b_eff else ""
        if spent is not None and b_exp>0:
            ratio=spent/b_exp
            cls="g" if ratio<2 else ("y" if ratio<3 else "r")
            bdg=f"{cls}:{ratio:.1f}×{eff}"
        else:
            bdg=("y:2×" if b_warned else "g:ok")+eff
    # [XF] verifier cross-family (Gemini/DeepSeek/Codex): niente quota real-time
    # dai provider → ▶ = chiamata IN CORSO (marker di cross-verify.py, ignorato
    # se >15 min: processo morto); ×N = chiamate di oggi dalla telemetria locale.
    fd=Path.home()/".claude"/"fable-director"
    active=None
    af=fd/"xfam-active.json"
    if af.is_file():
        try:
            a=json.loads(af.read_text())
            from datetime import datetime as _dt, timezone as _tz
            st=_dt.fromisoformat(str(a.get("started","")).replace("Z","+00:00"))
            if (_dt.now(_tz.utc)-st).total_seconds() < 900: active=a.get("provider")
        except Exception: pass
    xcounts={}
    dbf=fd/"telemetry.db"
    if dbf.is_file():
        try:
            import sqlite3
            from datetime import datetime as _dt2, timezone as _tz2
            today=_dt2.now(_tz2.utc).strftime("%Y-%m-%d")
            con=sqlite3.connect(dbf, timeout=0.3)
            con.execute("PRAGMA busy_timeout=300")  # render: mai bloccare a lungo
            for (pl,) in con.execute("SELECT payload FROM events WHERE event=? AND ts >= ?", ("verification", today)):
                try: p=json.loads(pl or "{}")
                except Exception: continue
                if p.get("kind")=="cross-family" and p.get("provider"):
                    xcounts[p["provider"]]=xcounts.get(p["provider"],0)+1
            con.close()
        except Exception: pass
    xparts=[]
    for prov in sorted(set(list(xcounts)+([active] if active else []))):
        label=str(prov).upper()[:8]
        if prov==active: xparts.append(f"{label}▲")
        elif xcounts.get(prov): xparts.append(f"{label}×{xcounts[prov]}")
    if xparts: xf=",".join(xparts[:3])
except Exception:
    pass
print(model,pct,rl,rlt,wk,wkt,bdg,xf,dlg)
' 2>/dev/null)
EOF

color_for() {
  if [ "$1" -ge 80 ] 2>/dev/null; then printf '\033[38;5;196m'   # rosso
  elif [ "$1" -ge 60 ] 2>/dev/null; then printf '\033[38;5;220m' # giallo
  else printf '\033[38;5;114m'                                   # verde
  fi
}

out="$badge"
[ "$model" != "-" ] && out="$out $(printf '\033[38;5;75m[%s]\033[0m' "$model")"
[ "$pct" != "-" ] && out="$out $(printf "$(color_for "$pct")[CTX %s%%]\033[0m" "$pct")"
if [ "$rl" != "-" ]; then
  seg="[5H ${rl}%"
  [ "$rlt" != "-" ] && seg="${seg}→${rlt}"
  out="$out $(printf "$(color_for "$rl")%s]\033[0m" "$seg")"
fi
if [ "$wk" != "-" ]; then
  seg="[7D ${wk}%"
  [ "$wkt" != "-" ] && seg="${seg}→${wkt}"
  out="$out $(printf "$(color_for "$wk")%s]\033[0m" "$seg")"
fi
case "$bdg" in
  g:*) out="$out $(printf '\033[38;5;114m[BDG %s]\033[0m' "${bdg#g:}")" ;;
  y:*) out="$out $(printf '\033[38;5;220m[BDG %s]\033[0m' "${bdg#y:}")" ;;
  r:*) out="$out $(printf '\033[38;5;196m[BDG %s]\033[0m' "${bdg#r:}")" ;;
esac
[ "$xf" != "-" ] && [ -n "$xf" ] && \
  out="$out $(printf '\033[38;5;216m[XF %s]\033[0m' "$(printf '%s' "$xf" | tr ',' ' ')")"
[ "$dlg" != "-" ] && [ -n "$dlg" ] && \
  out="$out $(printf '\033[38;5;183m[DLG %s]\033[0m' "$(printf '%s' "$dlg" | tr ',' ' ')")"
printf '%s' "${out# }"
