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
read -r model pct rl rlt wk wkt bdg xf dlg cache cmp grind eff bar win <<EOF
$(printf '%s' "$input" | python3 -c '
import json,sys,os,time
from pathlib import Path
model=pct=rl=rlt=wk=wkt=bdg=xf=dlg=cache=cmp=eff=bar=win="-"
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
    if p is not None:
        pct=f"{p:.0f}"
        # gauge 8 celle, ceil: anche 1% accende la prima — vuoto solo a 0
        cells=min(8,max(0,int(-(-float(p)*8//100))))
        bar="▓"*cells+"░"*(8-cells)
    ws=d.get("context_window",{}).get("context_window_size")
    if ws and int(ws)>200000: win=f"/{int(ws)//1000000}M"
    # effort LIVE della sessione (non quello del budget): xhigh/max = quota
    # che brucia in silenzio, acceso giallo; high e sotto = penombra
    el=d.get("effort",{}).get("level")
    if el: eff=("y:" if el in ("xhigh","max") else "d:")+"·"+str(el)
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
            # quota PER-ACCOUNT: le quote sono del piano attivo, non della
            # macchina — con 2 account (CLAUDE_CONFIG_DIR diversi) un file
            # unico fa leggere al gate le soglie di un altro account.
            # NB niente apostrofi qui: stringa shell single-quoted.
            import hashlib as _hq
            acct=_hq.sha256((os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home()/".claude")).encode()).hexdigest()[:8]
            qf=qd/f"quota-{acct}.json"
            new=json.dumps(q)
            # write-if-changed + atomica: render frequente, lettori concorrenti
            old_q=qf.read_text() if qf.is_file() else None
            if new!=old_q:
                tmpq=qf.with_name(f"{qf.name}.{os.getpid()}.tmp")
                tmpq.write_text(new); os.replace(tmpq,qf)
                # Snapshot gemello nello schema esterno di claude-hud
                # (five_hour/seven_day + used_percentage/resets_at ISO):
                # un utente claude-hud lo consuma via display.externalUsagePath.
                try:
                    from datetime import datetime as _dts, timezone as _tzs
                    def _iso(ts):
                        try: return _dts.fromtimestamp(int(ts),_tzs.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                        except Exception: return None
                    snap={"updated_at":_dts.now(_tzs.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
                    if r is not None:
                        e={"used_percentage":round(float(r))}
                        i=_iso(fh.get("resets_at"))
                        if i: e["resets_at"]=i
                        snap["five_hour"]=e
                    if w is not None:
                        e={"used_percentage":round(float(w))}
                        i=_iso(w_reset)
                        if i: e["resets_at"]=i
                        snap["seven_day"]=e
                    us=qd/f"usage-snapshot-{acct}.json"
                    tmpu=us.with_name(f"{us.name}.{os.getpid()}.tmp")
                    tmpu.write_text(json.dumps(snap)); os.replace(tmpu,us)
                except Exception: pass
                # storia quota per il burn-rate di fd-status: append solo a
                # quota cambiata, cap dimensione con rewrite della coda
                try:
                    from datetime import datetime as _dth, timezone as _tzh
                    hf=qd/f"quota-history-{acct}.jsonl"
                    rowh=json.dumps({"ts":_dth.now(_tzh.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"w":q.get("weekly_used_pct"),"r":q.get("five_hour_used_pct")})
                    with open(hf,"a") as _fh: _fh.write(rowh+"\n")
                    if hf.stat().st_size>60000:
                        _tl=hf.read_text().splitlines()[-300:]
                        tmph=hf.with_name(f"{hf.name}.{os.getpid()}.tmp")
                        tmph.write_text("\n".join(_tl)+"\n"); os.replace(tmph,hf)
                except Exception: pass
    except Exception:
        pass
    cwd=d.get("cwd") or os.getcwd()
    # slug identico a cwd_slug() in fd-telemetry.py (canonico + hash)
    import hashlib as _hl, re as _re
    _s=str(cwd).replace("\\","/")
    slug=(_re.sub(r"[^A-Za-z0-9]+","-",_s).strip("-")
          +"-"+_hl.sha256(_s.encode()).hexdigest()[:8])
    bf=Path.home()/".claude"/"fable-director"/"budgets"/f"{slug}.json"
    b_open=None; b_status=None; b_warned=False; b_eff=None; b_exp=0; b_sch=False
    if bf.is_file():
        b=json.loads(bf.read_text())
        b_status=b.get("status")
        b_sch=bool(b.get("schema_warned"))
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
            # lettura binaria, offset avanzato SOLO a fine riga completa:
            # una riga in scrittura viene ripresa al giro dopo — prima
            # veniva consumata a meta e i suoi token persi per sempre
            # (review esterna 2026-07-11; stessa disciplina dello Stop hook)
            with open(tp,"rb") as fh:
                fh.seek(state.get("off",0))
                data=fh.read()
            rows=data.split(b"\n")
            tail=rows.pop()
            consumed=len(data)-len(tail)
            for raw in rows:
                if not raw.strip(): continue
                try: rec=json.loads(raw.decode(errors="replace"))
                except Exception: continue
                rts=pts(rec.get("timestamp"))
                ts=rts or last_ts
                if rts: last_ts=rts
                if b_decl and ts and ts>=b_decl:
                    for u in usages(rec):
                        bst["out"]=bst.get("out",0)+(u.get("output_tokens") or 0)
                if rec.get("subtype")=="compact_boundary":
                    state["cmp"]=state.get("cmp",0)+1
                if not rec.get("isSidechain"): continue
                msg=rec.get("message") or {}
                u=msg.get("usage") or {}
                out=u.get("output_tokens") or 0
                if not out: continue
                mm=norm(msg.get("model") or "?")
                key="≡" if mm==main_norm else (msg.get("model") or "?").replace("claude-","").upper()[:10]
                state["models"][key]=state["models"].get(key,0)+out
            state["off"]=state.get("off",0)+consumed
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
        # [CMP] compattazioni di questa sessione (record compact_boundary):
        # ogni compattazione = contesto perso, segnale visibile solo se >0
        ncmp=int(state.get("cmp") or 0)
        if ncmp: cmp=str(ncmp)
        # [CACHE] countdown TTL prompt-cache dal timestamp più recente visto
        # nel transcript (ultima attività API): scaduto = il prossimo turno
        # ripaga il prefisso a freddo — utile per il timing delle deleghe
        # (asse 6). TTL da FD_CACHE_TTL_S, default 3600 (Max; 300 = piani 5 min).
        lts=pts(state.get("last_ts"))
        if lts:
            try:
                from datetime import timezone as _tzk
                ttl=int(os.environ.get("FD_CACHE_TTL_S") or 3600)
                remc=ttl-(_dtb.now(_tzk.utc)-lts).total_seconds()
                if remc<=0: cache="y:exp"
                else:
                    lblc=f"{int(remc//60)}m" if remc>=120 else f"{int(remc)}s"
                    cache=("g:" if remc>600 else ("y:" if remc>=60 else "r:"))+lblc
            except Exception: pass
    elif sid:
        df=Path.home()/".claude"/"fable-director"/"delegations"/f"{sid}.json"
        if df.is_file():
            c=json.loads(df.read_text())
            parts=[("≡" if k=="inherit" else str(k).replace("claude-","").upper()[:10])+f"×{v}"
                   for k,v in sorted(c.items(), key=lambda x:-x[1])]
            if parts: dlg="≈"+",".join(parts[:4])
    # [BDG] = classe:testo — la classe colore (g/y/r) si decide qui, la shell
    # mappa solo ANSI. Principio: quieto quando sano (sigla compatta BDG),
    # PAROLE INTERE quando in allarme (2x, 3x, enforcement rotto) + marcatori
    # testuali che sopravvivono senza colore. Soglie identiche allo Stop hook.
    if b_status=="flagged":
        bdg="r:✕ BUDGET 3× — POST-MORTEM DUE"
    elif b_status=="open":
        beff=("·"+str(b_eff)) if b_eff else ""
        if spent is not None and b_exp>0:
            ratio=spent/b_exp
            if ratio>=3: bdg=f"r:✕ BUDGET {ratio:.1f}× OF ESTIMATE{beff}"
            elif ratio>=2: bdg=f"y:⚠ BUDGET {ratio:.1f}× OF ESTIMATE{beff}"
            else:
                # micro-barra 0-3x: una cella per checkpoint intero raggiunto
                bc="▓"*max(1,min(2,int(ratio)+1))
                bdg=f"g:bdg {bc}{chr(9617)*(3-len(bc))} {ratio:.1f}×{beff}"
        else:
            bdg=("y:⚠ BUDGET 2× OF ESTIMATE"+beff) if b_warned else ("g:bdg ok"+beff)
    # schema_anomaly sul budget aperto = contabilita transcript inaffidabile:
    # enforcement di fatto spento — va urlato, non contato zero in silenzio
    if b_sch and b_status=="open":
        bdg="r:✕ ENFORCEMENT OFF"
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
    # Residuo free-tier per FINESTRA DEL PROVIDER: chi dichiara in config
    # limits.reset {period: daily, tz: IANA} viene contato dalla sua ultima
    # mezzanotte in quel tz (Gemini azzera a midnight Pacific, non a
    # mezzanotte UTC) e mostra residuo n/rpd + orario locale del prossimo
    # azzeramento. Chi non dichiara la finestra resta al conteggio giorno
    # UTC e NON mostra orario: fallback onesto, mai inventato.
    xf_cfg=None
    try:
        cfp=fd/"cross-family.json"
        if cfp.is_file(): xf_cfg=json.loads(cfp.read_text())
    except Exception: pass
    xmeta={}
    try:
        from zoneinfo import ZoneInfo as _ZI
        from datetime import datetime as _dtw, timedelta as _tdw, timezone as _tzw
        for pk,pv in ((xf_cfg or {}).get("providers") or {}).items():
            lim=(pv or {}).get("limits") or {}
            rst=lim.get("reset") or {}
            if (pv or {}).get("billing")=="free" and lim.get("rpd") \
               and rst.get("period")=="daily" and rst.get("tz"):
                try:
                    tzp=_ZI(str(rst["tz"]))
                    nl=_dtw.now(tzp)
                    st=nl.replace(hour=0,minute=0,second=0,microsecond=0)
                    xmeta[pk]=(int(lim["rpd"]),
                               st.astimezone(_tzw.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                               (st+_tdw(days=1)).astimezone().strftime("%H:%M"))
                except Exception: pass
    except Exception: pass
    xcounts={}
    dbf=fd/"telemetry.db"
    if dbf.is_file():
        try:
            import sqlite3
            from datetime import datetime as _dt2, timezone as _tz2
            today=_dt2.now(_tz2.utc).strftime("%Y-%m-%d")
            floor=min([today]+[m[1] for m in xmeta.values()])
            con=sqlite3.connect(dbf, timeout=0.3)
            con.execute("PRAGMA busy_timeout=300")  # render: mai bloccare a lungo
            for (ts,pl) in con.execute("SELECT ts, payload FROM events WHERE event=? AND ts >= ?", ("verification", floor)):
                try: p=json.loads(pl or "{}")
                except Exception: continue
                if p.get("kind")!="cross-family" or not p.get("provider"): continue
                prov=p["provider"]
                lo=xmeta[prov][1] if prov in xmeta else today
                if str(ts)>=lo:
                    xcounts[prov]=xcounts.get(prov,0)+1
            con.close()
        except Exception: pass
    xparts=[]
    xratio=0.0
    for prov in sorted(set(list(xcounts)+([active] if active else []))):
        label=str(prov).lower()[:8]
        star="▲" if prov==active else ""
        n=xcounts.get(prov,0)
        if prov in xmeta and n>0:
            rpd,_,nxt=xmeta[prov]
            xparts.append(f"{label}{star} {n}/{rpd}→{nxt}")
            xratio=max(xratio,n/rpd)
        elif n>0: xparts.append(f"{label}{star}×{n}")
        elif star: xparts.append(label+star)
    if xparts:
        xcls="r:" if xratio>=0.95 else ("y:" if xratio>=0.8 else "g:")
        xf=xcls+",".join(xparts[:3])
except Exception:
    pass
# bdg puo contenere spazi (allarmi a parole intere) ma la shell fa read
# word-split: gli spazi viaggiano come virgole, la shell li ripristina
bdg=str(bdg).replace(" ",",")
# dlg ha spazi interni ("SONNET-5 12k"): non è più ultimo campo del read
# shell, quindi stessa disciplina di bdg (virgole, la shell le ripristina)
dlg=str(dlg).replace(" ",",")
# xf con residuo ("gemini 2/1500→09:00") ha spazi: stessa disciplina
xf=str(xf).replace(" ",",")
# [FAIL xN] grinding: lo streak di Bash falliti lo calcola l hook fail-streak
# (autorita), qui si LEGGE soltanto il suo file di stato per il sid corrente.
# Quieto sotto 2; mostrato da 2 in su (il nudge del hook scatta a 3).
grind="-"
try:
    if sid:
        gf=Path.home()/".claude"/"fable-director"/"grinding"/f"{sid}.json"
        if gf.is_file():
            gs=int(json.loads(gf.read_text()).get("streak") or 0)
            if gs>=2: grind=str(gs)
except Exception:
    pass
print(model,pct,rl,rlt,wk,wkt,bdg,xf,dlg,cache,cmp,grind,eff,bar,win)
' 2>/dev/null)
EOF

color_for() {
  if [ "$1" -ge 80 ] 2>/dev/null; then printf '\033[38;5;196m'   # rosso
  elif [ "$1" -ge 60 ] 2>/dev/null; then printf '\033[38;5;220m' # giallo
  else printf '\033[38;5;245m'                                   # penombra: sano = quieto
  fi
}

# Adozione badge caveman nello stile zen: strip ANSI e, SOLO se il testo è il
# badge noto [CAVEMAN] / [CAVEMAN:MODE], re-render minuscolo in 172 (firma
# ocra preservata, quadre via) + eventuale suffisso savings in penombra.
# Qualunque altro formato passa INTATTO: mai riscrivere ciò che non si conosce.
if [ -n "$badge" ]; then
  bplain=$(printf '%s' "$badge" | sed 's/\x1b\[[0-9;]*m//g')
  case "$bplain" in
    "[CAVEMAN]"*|"[CAVEMAN:"*)
      bmode=$(printf '%s' "$bplain" | sed -n 's/^\[CAVEMAN:\([A-Z-]*\)\].*/\1/p' | tr '[:upper:]' '[:lower:]')
      bsuf=$(printf '%s' "$bplain" | sed 's/^\[CAVEMAN[^]]*\]//')
      badge=$(printf '\033[38;5;172mcaveman%s\033[0m' "${bmode:+:$bmode}")
      [ -n "$bsuf" ] && badge="$badge$(printf '\033[38;5;245m%s\033[0m' "$bsuf")"
      ;;
  esac
fi

SEP=$(printf '\033[38;5;239m · \033[0m')
pre=""
[ -n "$badge" ] && pre="$badge$(printf '\033[38;5;239m │ \033[0m')"
out=""
app() { [ -n "$out" ] && out="$out$SEP"; out="$out$1"; }

# ✦ MODELLO in penombra + effort LIVE attaccato (giallo da xhigh: brucia quota)
if [ "$model" != "-" ]; then
  seg="$(printf '\033[38;5;245m\342\234\246 %s\033[0m' "$model")"
  case "$eff" in
    y:*) seg="$seg$(printf '\033[38;5;220m%s\033[0m' "${eff#y:}")" ;;
    d:*) seg="$seg$(printf '\033[38;5;245m%s\033[0m' "${eff#d:}")" ;;
  esac
  app "$seg"
fi
# ctx: gauge 8 celle + /1M se window estesa
if [ "$pct" != "-" ]; then
  gb=""; [ "$bar" != "-" ] && gb="$bar "
  gw=""; [ "$win" != "-" ] && gw="$win"
  app "$(printf "$(color_for "$pct")ctx %s%s%%%s\033[0m" "$gb" "$pct" "$gw")"
fi
# cmp solo se ≥1 compattazione: contesto perso, deviazione per natura
[ "$cmp" != "-" ] && [ -n "$cmp" ] && app "$(printf '\033[38;5;220mcmp %s\033[0m' "$cmp")"
if [ "$rl" != "-" ]; then
  seg="5H ${rl}%"
  [ "$rlt" != "-" ] && seg="${seg}→${rlt}"
  app "$(printf "$(color_for "$rl")%s\033[0m" "$seg")"
fi
if [ "$wk" != "-" ]; then
  seg="7D ${wk}%"
  [ "$wkt" != "-" ] && seg="${seg}→${wkt}"
  app "$(printf "$(color_for "$wk")%s\033[0m" "$seg")"
fi
# fail ×N grinding: stato PROTETTO, resta in riga 1 accanto alle quote —
# uno streak esiste anche senza budget/deleghe e non deve dipendere dalla
# riga 2. Giallo a 2 (early warning), rosso da 3 (il nudge e scattato).
if [ "$grind" != "-" ] && [ -n "$grind" ]; then
  if [ "$grind" -ge 3 ] 2>/dev/null; then
    app "$(printf '\033[38;5;196mfail \303\227%s\033[0m' "$grind")"
  else
    app "$(printf '\033[38;5;220mfail \303\227%s\033[0m' "$grind")"
  fi
fi
# Takeover: budget flagged / enforcement off (classe r:) va IN TESTA alla
# riga 1 a sfondo pieno — la gerarchia visiva si inverte con la priorita.
# Le classi g:/y: del budget vivono invece nella riga 2 (attivita).
tko=""; seg_bdg=""
case "$bdg" in
  r:*) tko="$(printf '\033[48;5;196m\033[38;5;16m %s \033[0m' "$(printf '%s' "${bdg#r:}" | tr ',' ' ')") " ;;
  g:*) seg_bdg="$(printf '\033[38;5;245m%s\033[0m' "$(printf '%s' "${bdg#g:}" | tr ',' ' ')")" ;;
  y:*) seg_bdg="$(printf '\033[38;5;220m%s\033[0m' "$(printf '%s' "${bdg#y:}" | tr ',' ' ')")" ;;
esac
seg_xf=""; seg_dlg=""; seg_cache=""
case "$cache" in
  g:*) seg_cache="$(printf '\033[38;5;245mcache %s\033[0m' "${cache#g:}")" ;;
  y:*) seg_cache="$(printf '\033[38;5;220mcache %s\033[0m' "${cache#y:}")" ;;
  r:*) seg_cache="$(printf '\033[38;5;196mcache %s\033[0m' "${cache#r:}")" ;;
esac
# xf: residuo a soglia (y ≥80%, r ≥95% del free tier), acceso 216 con
# chiamata in flight (▲), penombra a riposo
if [ "$xf" != "-" ] && [ -n "$xf" ]; then
  xtxt="$(printf '%s' "${xf#[gyr]:}" | tr ',' ' ')"
  case "$xf" in
    r:*) seg_xf="$(printf '\033[38;5;196mxf %s\033[0m' "$xtxt")" ;;
    y:*) seg_xf="$(printf '\033[38;5;220mxf %s\033[0m' "$xtxt")" ;;
    *▲*) seg_xf="$(printf '\033[38;5;216mxf %s\033[0m' "$xtxt")" ;;
    *)   seg_xf="$(printf '\033[38;5;245mxf %s\033[0m' "$xtxt")" ;;
  esac
fi
[ "$dlg" != "-" ] && [ -n "$dlg" ] && \
  seg_dlg="$(printf '\033[38;5;245mdlg %s\033[0m' "$(printf '%s' "$dlg" | tr ',' ' ')")"
# Larghezza in CARATTERI (glifi zen multibyte; wc -c li conterebbe tripli).
# COLUMNS lo fornisce Claude Code ≥2.1.153; assente → 120 conservativo.
plain_len() { printf '%s' "$1" | sed 's/\x1b\[[0-9;]*m//g' | LC_ALL=C.UTF-8 wc -m 2>/dev/null || printf '%s' "$1" | sed 's/\x1b\[[0-9;]*m//g' | wc -c; }
W="${COLUMNS:-120}"
case "$W" in (*[!0-9]*|"") W=120 ;; esac
# Riga 2 on-demand — l ATTIVITA (budget, deleghe, esterni, cache): esiste
# solo quando succede qualcosa; a riposo la statusline resta a una riga.
# Degradazione deterministica dentro la riga 2: cade cache, poi dlg, poi
# xf — MAI il budget. La riga 1 (identita e quote) non degrada mai.
L2P="$(printf '\033[38;5;239m\342\224\224 \033[0m')"
compose2() {
  c=""
  a2() { [ -n "$c" ] && c="$c$SEP"; c="$c$1"; }
  [ -n "$seg_bdg" ] && a2 "$seg_bdg"
  [ "$1" -ge 2 ] && [ -n "$seg_dlg" ] && a2 "$seg_dlg"
  [ "$1" -ge 1 ] && [ -n "$seg_xf" ] && a2 "$seg_xf"
  # cache SCADUTA non giustifica da sola la riga 2 (sessione fredda =
  # rumore permanente); un countdown vivo si — serve al timing deleghe
  [ "$1" -ge 3 ] && [ -n "$seg_cache" ] && { [ "$cache" != "y:exp" ] || [ -n "$c" ]; } && a2 "$seg_cache"
  [ -n "$c" ] && printf '%s%s' "$L2P" "$c"
}
line1="$tko$pre$out"
line2="$(compose2 3)"
[ -n "$line2" ] && [ "$(plain_len "$line2")" -gt "$W" ] && line2="$(compose2 2)"  # cade cache
[ -n "$line2" ] && [ "$(plain_len "$line2")" -gt "$W" ] && line2="$(compose2 1)"  # cade dlg
[ -n "$line2" ] && [ "$(plain_len "$line2")" -gt "$W" ] && line2="$(compose2 0)"  # cade xf
printf '%s' "$line1"
[ -n "$line2" ] && printf '\n%s' "$line2"
exit 0
