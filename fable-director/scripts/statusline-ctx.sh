#!/bin/bash
# fable-director — statusline:
#   [MODEL]         modello attivo (model.display_name)
#   [CTX %]         riempimento context window della conversazione
#   [5H %→HH:MM]    quota piano finestra 5 ore + orario reset locale
#   [7D %]          quota piano settimanale (se il campo esiste)
#   [BDG ok|2×|3×]  stato pre-budget fable-director (solo lettura del budget file)
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
            (qd/"quota.json").write_text(json.dumps(q))
    except Exception:
        pass
    cwd=d.get("cwd") or os.getcwd()
    slug="-"+str(cwd).strip("/").replace("/","-").replace(".","-")
    bf=Path.home()/".claude"/"fable-director"/"budgets"/f"{slug}.json"
    if bf.is_file():
        b=json.loads(bf.read_text())
        st=b.get("status")
        if st=="open": bdg="2x" if b.get("warned") else "ok"
        elif st=="flagged": bdg="3x"
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
    tp=d.get("transcript_path")
    main_norm=norm(m or "")
    if sid and tp and Path(tp).is_file():
        sf=Path.home()/".claude"/"fable-director"/"delegations"/f"{sid}.tok.json"
        state={"off":0,"models":{}}
        if sf.is_file():
            try: state=json.loads(sf.read_text())
            except Exception: pass
        size=Path(tp).stat().st_size
        if size < state.get("off",0): state={"off":0,"models":{}}  # transcript ruotato
        if size > state.get("off",0):
            with open(tp, errors="replace") as fh:
                fh.seek(state.get("off",0))
                for line in fh:
                    try: rec=json.loads(line)
                    except Exception: continue
                    if not rec.get("isSidechain"): continue
                    msg=rec.get("message") or {}
                    u=msg.get("usage") or {}
                    out=u.get("output_tokens") or 0
                    if not out: continue
                    mm=norm(msg.get("model") or "?")
                    key="≡" if mm==main_norm else (msg.get("model") or "?").replace("claude-","").upper()[:10]
                    state["models"][key]=state["models"].get(key,0)+out
                state["off"]=fh.tell()
            sf.parent.mkdir(parents=True, exist_ok=True)
            sf.write_text(json.dumps(state))
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
            con=sqlite3.connect(dbf)
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
  ok) out="$out $(printf '\033[38;5;114m[BDG ok]\033[0m')" ;;
  2x) out="$out $(printf '\033[38;5;220m[BDG 2×]\033[0m')" ;;
  3x) out="$out $(printf '\033[38;5;196m[BDG 3×]\033[0m')" ;;
esac
[ "$xf" != "-" ] && [ -n "$xf" ] && \
  out="$out $(printf '\033[38;5;216m[XF %s]\033[0m' "$(printf '%s' "$xf" | tr ',' ' ')")"
[ "$dlg" != "-" ] && [ -n "$dlg" ] && \
  out="$out $(printf '\033[38;5;183m[DLG %s]\033[0m' "$(printf '%s' "$dlg" | tr ',' ' ')")"
printf '%s' "${out# }"
