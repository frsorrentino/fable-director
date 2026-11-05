#!/usr/bin/env python3
"""fable-director — nucleo della statusline (1.63.1), lanciato da statusline-ctx.sh.

Prima viveva dentro statusline-ctx.sh come `python -c '<32 KB>'`: su Windows la
riga di comando ha un tetto di 32 767 caratteri e il blocco l'aveva superato,
quindi Python non partiva e la riga restava vuota. Da file non c'e tetto, il
bytecode va in cache e gli apostrofi nei commenti non rompono piu nulla.

Calcola tutte le metriche in UNA passata (vedi l'intestazione di
statusline-ctx.sh per il significato dei segmenti) e:
  - modalita expert: stampa i 25 token che la shell rende nella riga storica;
  - modalita plain (default): rende la riga in parole nello STESSO processo
    (statusline-plain.py importato, non un secondo interprete).

Parti pesanti (scan incrementale del transcript, deleghe in volo, chiamate
esterne da telemetry.db, snapshot di sessione con il consiglio, ritmo
settimanale): al piu ogni FD_SL_HEAVY_S secondi (default 30) per sessione,
in mezzo si riusa l'ultimo risultato da statusline-cache/<sid>.heavy.json.
Si ricalcolano subito se cambiano modello, effort, cartella o budget.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("FD_SL_SCRIPTS", HERE)


def _heavy_s():
    try:
        return max(0.0, float(os.environ.get("FD_SL_HEAVY_S", "30")))
    except ValueError:
        return 30.0


def compute(raw):
    import json,sys,os,time
    from pathlib import Path
    model=pct=rl=rlt=wk=wkt=bdg=xf=dlg=cache=cmp=eff=bar=win=dir=pace=brn="-"
    # anche i token calcolati piu sotto: un input rotto da una riga di "-",
    # non un NameError che svuota la statusline
    lk="0"; fw=prn=pru="-"; sid=None; b_open=None; bf=None; _fd_nfl=0; _fd_stuck=0
    import hashlib as _hl
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
        d=json.loads(raw)
        m=d.get("model",{}).get("display_name")
        # lo spazio del display_name si CONSERVA (OPUS 5, non OPUS5): viaggia
        # come virgola nel read shell, che lo ripristina — leggibilita a costo 1 char
        # "Fable 5.1 (200k)" (CC 2.1.259 aggiunge la finestra): via il suffisso tra parentesi
        if m: model=str(m).split(" (")[0][:12].upper().strip()
        p=d.get("context_window",{}).get("used_percentage")
        if p is not None:
            pct=f"{p:.0f}"
            # gauge 8 celle, ceil: anche 1% accende la prima — vuoto solo a 0
            cells=min(8,max(0,int(-(-float(p)*8//100))))
            bar="▓"*cells+"░"*(8-cells)
        ws=d.get("context_window",{}).get("context_window_size")
        if ws and int(ws)>200000: win=f"/{int(ws)//1000000}M"
        # cartella di lavoro (1.50.1): il nome, per distinguere le sessioni parallele
        # a vista; ULTIMO campo del read shell, quindi gli spazi passano
        wd=(d.get("workspace") or {}).get("current_dir") or d.get("cwd") or ""
        if wd: dir=os.path.basename(str(wd).rstrip("/")) or "/"
        # effort LIVE della sessione (non quello del budget): xhigh/max = quota
        # che brucia in silenzio, acceso giallo; high e sotto = penombra
        el=d.get("effort",{}).get("level")
        if el: eff=("y:" if el in ("xhigh","max") else "d:")+"·"+str(el)
        # "or {}": rate_limits e i suoi bucket possono arrivare null. Un valore col
        # reset scaduto vale come assente: quella finestra non esiste piu.
        rlim=d.get("rate_limits") or {}
        fh=rlim.get("five_hour") or {}
        r=fh.get("used_percentage")
        try:
            if fh.get("resets_at") is not None and float(fh.get("resets_at"))<=time.time(): r=None
        except (TypeError,ValueError): pass
        if r is not None: rl=f"{r:.0f}"
        rlt=(fmt_reset(fh.get("resets_at")) or "-") if r is not None else "-"
        w=None; w_reset=None
        for k in ("seven_day","weekly","seven_days"):
            wd=rlim.get(k) or {}
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
            if r is not None and fh.get("resets_at") is not None: q["five_hour_resets_at"]=fh.get("resets_at")
            # Sentinella bucket ignoti: se rate_limits espone una chiave nuova
            # (es. un futuro bucket per-modello) la registriamo nel quota file —
            # la si scopre alla prima sessione, non per caso. Mai interpretata.
            unk=sorted(set(rlim)
                       -{"five_hour","seven_day","weekly","seven_days"})
            if unk: q["unknown_buckets"]=unk
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
                # Fusione col file esistente (1.52.3): piu sessioni dello stesso
                # account ridisegnano a turno, ognuna con la SUA ultima lettura;
                # senza fusione vince chi ridisegna per ultima, anche se ferma da
                # ore. Dentro una finestra la quota non scende mai: a pari reset il
                # massimo e il dato piu recente; reset piu avanti = finestra nuova,
                # vince il nuovo; reset piu indietro = lettura vecchia, si ignora.
                # Un bucket assente nella lettura non cancella quello presente,
                # salvo finestra gia scaduta.
                old_q=qf.read_text() if qf.is_file() else None
                try: old_d=json.loads(old_q) if old_q else {}
                except Exception: old_d={}
                if not isinstance(old_d,dict): old_d={}
                def _rs(x):
                    try: return float(x)
                    except Exception: return None
                mq=dict(q)
                for pk,rk in (("weekly_used_pct","weekly_resets_at"),("five_hour_used_pct","five_hour_resets_at")):
                    ov,orr=old_d.get(pk),_rs(old_d.get(rk))
                    nv,nr=q.get(pk),_rs(q.get(rk))
                    if ov is None: continue
                    if nv is None:
                        if orr is None or orr>time.time():
                            mq[pk]=ov
                            if old_d.get(rk) is not None: mq[rk]=old_d.get(rk)
                        continue
                    if orr is None or nr is None: continue
                    if nr<orr-600: mq[pk]=ov; mq[rk]=old_d.get(rk)
                    elif abs(nr-orr)<=600:
                        try: mq[pk]=max(float(ov),float(nv))
                        except Exception: pass
                # write-if-changed + atomica: render frequente, lettori concorrenti
                if mq!=old_d:
                    tmpq=qf.with_name(f"{qf.name}.{os.getpid()}.tmp")
                    tmpq.write_text(json.dumps(mq)); os.replace(tmpq,qf)
                    # Snapshot gemello nello schema esterno di claude-hud
                    # (five_hour/seven_day + used_percentage/resets_at ISO):
                    # un utente claude-hud lo consuma via display.externalUsagePath.
                    try:
                        from datetime import datetime as _dts, timezone as _tzs
                        def _iso(ts):
                            try: return _dts.fromtimestamp(int(ts),_tzs.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                            except Exception: return None
                        snap={"updated_at":_dts.now(_tzs.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
                        if mq.get("five_hour_used_pct") is not None:
                            e={"used_percentage":round(float(mq["five_hour_used_pct"]))}
                            i=_iso(mq.get("five_hour_resets_at"))
                            if i: e["resets_at"]=i
                            snap["five_hour"]=e
                        if mq.get("weekly_used_pct") is not None:
                            e={"used_percentage":round(float(mq["weekly_used_pct"]))}
                            i=_iso(mq.get("weekly_resets_at"))
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
                        rowh=json.dumps({"ts":_dth.now(_tzh.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"w":mq.get("weekly_used_pct"),"r":mq.get("five_hour_used_pct")})
                        if (mq.get("weekly_used_pct"),mq.get("five_hour_used_pct"))!=(old_d.get("weekly_used_pct"),old_d.get("five_hour_used_pct")):
                            with open(hf,"a") as _fh: _fh.write(rowh+"\n")
                        if hf.stat().st_size>60000:
                            _tl=hf.read_text().splitlines()[-300:]
                            tmph=hf.with_name(f"{hf.name}.{os.getpid()}.tmp")
                            tmph.write_text("\n".join(_tl)+"\n"); os.replace(tmph,hf)
                    except Exception: pass
        except Exception:
            pass
        # Parti pesanti al piu ogni FD_SL_HEAVY_S (1.63.1): heavy=False riusa
        # il risultato in statusline-cache/<sid>.heavy.json. Chiave: modello,
        # effort, cartella e budget (dichiarazione + stato): se cambia uno di
        # questi si ricalcola subito, il consiglio e il budget non aspettano.
        heavy=True; H={}; _hk=None; _hf=None
        try:
            import re as _rh
            _hsid=d.get("session_id")
            if _hsid and _rh.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(_hsid)):
                _s0=str(d.get("cwd") or os.getcwd()).replace("\\","/")
                _bf0=(Path.home()/".claude"/"fable-director"/"budgets"/
                      (_rh.sub(r"[^A-Za-z0-9]+","-",_s0).strip("-")+"-"+hashlib.sha256(_s0.encode()).hexdigest()[:8]+".json"))
                _bk=None
                if _bf0.is_file():
                    try:
                        _b0=json.loads(_bf0.read_text())
                        _bk=[_b0.get("declared_at"),_b0.get("status")]
                    except Exception: _bk=["unreadable"]
                _hk=[(d.get("model") or {}).get("id"),m,el,
                     (d.get("workspace") or {}).get("current_dir") or d.get("cwd") or "",_bk]
                _hf=Path.home()/".claude"/"fable-director"/"statusline-cache"/f"{_hsid}.heavy.json"
                if _hf.is_file():
                    H=json.loads(_hf.read_text())
                    if (isinstance(H,dict) and H.get("key")==_hk
                            and 0<=time.time()-float(H.get("at") or 0)<_heavy_s()):
                        heavy=False
            if not isinstance(H,dict): H={}
        except Exception:
            heavy=True; H={}
        # Snapshot di SESSIONE per il gate (1.40): contesto corrente, modello,
        # effort e quota 5h — cosi il gate dei Workflow sa quanto pesa il thread
        # che lancia (ri-cache dopo il muro) e quale modello ereditano gli agenti.
        # Scrittura solo a cambiamento (o ogni 5 min per la freschezza).
        try:
            _sid=d.get("session_id")
            if heavy and _sid:
                cw=d.get("context_window") or {}
                cu=cw.get("current_usage") or {}
                ctx=sum(int(cu.get(k) or 0) for k in ("input_tokens","cache_creation_input_tokens","cache_read_input_tokens"))
                if not ctx and p is not None and cw.get("context_window_size"):
                    ctx=int(float(p)/100.0*int(cw.get("context_window_size")))
                snapd={"session_id":str(_sid),"model":(d.get("model") or {}).get("id"),"effort":el,"ctx_tokens":ctx,"ctx_size":cw.get("context_window_size"),"five_hour_used_pct":(round(float(r),1) if r is not None else None),"five_hour_resets_at":fh.get("resets_at"),"ts":int(time.time())}
                # (1.56) account, cwd e consiglio modello/effort per il relay di
                # claude-master (contratto 1.37 A): advice.py, regole nel docstring.
                import hashlib as _ha
                snapd["account"]=_ha.sha256((os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home()/".claude")).encode()).hexdigest()[:8]
                snapd["cwd"]=(d.get("workspace") or {}).get("current_dir") or d.get("cwd") or ""
                try:
                    import importlib.util as _iu
                    _sp=_iu.spec_from_file_location("fd_advice",os.path.join(os.environ["FD_SL_SCRIPTS"],"advice.py"))
                    _am=_iu.module_from_spec(_sp); _sp.loader.exec_module(_am)
                    snapd["advice"]=_am.for_snapshot(snapd)
                except Exception: pass
                sd=Path.home()/".claude"/"fable-director"/"sessions"
                sd.mkdir(parents=True,exist_ok=True)
                sf=sd/(str(_sid).replace("/","-")+".json")
                olds={}
                try:
                    if sf.is_file(): olds=json.loads(sf.read_text())
                except Exception: olds={}
                _noat=lambda v: {k:x for k,x in v.items() if k!="at"} if isinstance(v,dict) else v
                if any(_noat(olds.get(k))!=_noat(snapd[k]) for k in snapd if k!="ts") or time.time()-float(olds.get("ts") or 0)>300:
                    tmps=sf.with_name(f"{sf.name}.{os.getpid()}.tmp")
                    tmps.write_text(json.dumps(snapd)); os.replace(tmps,sf)
                    for _old in sd.glob("*.json"):
                        try:
                            if time.time()-_old.stat().st_mtime>7*86400: _old.unlink()
                        except Exception: pass
        except Exception:
            pass
        # Ritmo settimanale, solo dove la finestra di 5 ore non c e
        pace="-"; brn="-"
        try:
            if not heavy:
                pace=str(H.get("pace") or "-"); brn=str(H.get("brn") or "-")
            elif r is None and w is not None:
                import importlib.util as _iq
                _sq=_iq.spec_from_file_location("quota_pace",os.path.join(os.environ.get("FD_SL_SCRIPTS") or ".","quota_pace.py"))
                _qp=_iq.module_from_spec(_sq); _sq.loader.exec_module(_qp)
                _res=_qp.residue_pct(w,w_reset)
                if _res:
                    pace=f"{_res[0]:.0f}"
                    _b=_qp.burn_pct_h()
                    if _b>=_qp.burn_ceiling(): brn=f"{_b:.1f}"
        except Exception: pass
        # plan-<acct>.json: proprieta del piano DICHIARATE dall utente (il plugin
        # non conosce i piani): statusline_links attiva i link OSC 8 (opt-in,
        # solo dopo test sul proprio terminale — su webview che navigano in
        # place un click ucciderebbe la sessione); premium_weekly_fraction e la
        # quota della finestra settimanale riservata al modello premium.
        lk="0"; fw="-"
        try:
            import hashlib as _hp
            _acct=_hp.sha256((os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home()/".claude")).encode()).hexdigest()[:8]
            _pf=Path.home()/".claude"/"fable-director"/f"plan-{_acct}.json"
            if _pf.is_file():
                _plan=json.loads(_pf.read_text())
                if _plan.get("statusline_links"): lk="1"
                _frac=float(_plan.get("premium_weekly_fraction") or 0)
                _pm=str(_plan.get("premium_model_match") or "fable").lower()
                # Bound finestra premium: consumo premium ≤ consumo totale e
                # limite premium = frac × capacita → % finestra ≤ 7D% / frac.
                # SEMPRE un tetto (≤), mai il consumo reale; a bound saturo si
                # mostra ✦? — mai un numero finto. Solo sul modello premium.
                if _frac>0 and w is not None and _pm in str(m or "").lower().replace(" ",""):
                    _bound=float(w)/_frac
                    if _bound>=100: fw="y:✦?"
                    else:
                        _c="r:" if _bound>=80 else ("y:" if _bound>=60 else "g:")
                        fw=_c+f"✦≤{_bound:.0f}%"
        except Exception: pass
        # pr aperta sul branch (dati gia forniti da Claude Code, finora ignorati)
        prn="-"; pru="-"
        try:
            _pr=d.get("pr") or {}
            _n=_pr.get("number")
            if _n:
                _st=str(_pr.get("review_state") or "")
                _pc={"approved":"a:","changes_requested":"c:"}.get(_st,"p:")
                prn=_pc+"pr #"+str(_n)
                if _pr.get("url"): pru=str(_pr.get("url"))
        except Exception: pass
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
        spent=None; _lts_iso=None
        if heavy and sid and tp and Path(tp).is_file():
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
            _lts_iso=state.get("last_ts")
        elif heavy and sid:
            df=Path.home()/".claude"/"fable-director"/"delegations"/f"{sid}.json"
            if df.is_file():
                c=json.loads(df.read_text())
                parts=[("≡" if k=="inherit" else str(k).replace("claude-","").upper()[:10])+f"×{v}"
                       for k,v in sorted(c.items(), key=lambda x:-x[1])]
                if parts: dlg="≈"+",".join(parts[:4])
        # [dlg ⟲N] deleghe IN VOLO adesso, dal misuratore SubagentStart/Stop: e
        # il solo segnale che esiste PRIMA dei token nel transcript, e il solo che
        # vede gli spawn annidati. Con refreshInterval attivo e cio che tiene viva
        # la riga proprio mentre il coordinatore aspetta i subagent.
        # (Nessun apostrofo in questo blocco: il python vive dentro python3 -c '...',
        #  una virgoletta singola chiuderebbe la stringa shell — regola del file.)
        # (1.40.2) Agenti morti fuori dal conteggio: transcript fermo da 60 min,
        # run del Workflow gia completato, o nessun transcript dopo 24 h (stessa
        # regola di subagent-meter.py, che li toglie dal registro). stuck = minuti
        # di SILENZIO del vivo piu silenzioso (dal via se non ha ancora un file).
        def _fd_inflight(sgf, tp):
            n=0; st=0
            try:
                fl=json.loads(sgf.read_text()).get("inflight") or {}
                sdir=None
                if tp:
                    _tp=Path(str(tp)); sdir=_tp.with_suffix("") if _tp.suffix==".jsonl" else _tp
                now=time.time()
                from datetime import datetime as _dd, timezone as _zz
                done=set()
                if sdir is not None:
                    for wf in (sdir/"workflows").glob("wf_*.json"):
                        try:
                            if json.loads(wf.read_text()).get("status")=="completed": done.add(wf.stem)
                        except Exception: pass
                for aid,v in fl.items():
                    since=0
                    try:
                        t=_dd.fromisoformat(str(v.get("since")).replace("Z","+00:00"))
                        if t.tzinfo is None: t=t.replace(tzinfo=_zz.utc)
                        since=int((now-t.timestamp())//60)
                    except Exception: pass
                    path=None; hint=v.get("transcript")
                    if hint and Path(str(hint)).is_file(): path=Path(str(hint))
                    elif sdir is not None:
                        c=sdir/"subagents"/f"agent-{aid}.jsonl"
                        if c.is_file(): path=c
                        else:
                            for c in (sdir/"subagents"/"workflows").glob(f"*/agent-{aid}.jsonl"):
                                path=c; break
                    if path is None:
                        if since>=1440: continue
                        n+=1; st=max(st,since); continue
                    if path.parent.name in done: continue
                    silent=int((now-path.stat().st_mtime)//60)
                    if silent>=60: continue
                    n+=1; st=max(st,silent)
            except Exception: pass
            return n,st
        _fd_nfl=0; _fd_stuck=0
        if heavy and sid:
            try:
                _sg="".join(c if (c.isalnum() or c in "-_") else "-" for c in str(sid))[:120]
                sgf=Path.home()/".claude"/"fable-director"/"subagents"/f"{_sg}.json"
                if sgf.is_file():
                    _fd_nfl,_fd_stuck=_fd_inflight(sgf, d.get("transcript_path"))
                    nfl=_fd_nfl
                    if nfl>0: dlg=(f"⟲{nfl}" if dlg=="-" else f"⟲{nfl},{dlg}")
            except Exception: pass
        # Fuori dal giro pesante: ultimo risultato in cache (vedi heavy sopra).
        if not heavy:
            dlg=str(H.get("dlg") or "-"); cmp=str(H.get("cmp") or "-")
            _lts_iso=H.get("lts")
            try: _fd_nfl=int(H.get("nfl") or 0); _fd_stuck=int(H.get("stuck") or 0)
            except Exception: _fd_nfl=0; _fd_stuck=0
            if b_open and H.get("spent") is not None and H.get("spent_decl")==b_open.get("declared_at"):
                spent=int(H.get("spent"))
        # [CACHE] countdown TTL prompt-cache dal timestamp più recente visto
        # nel transcript (ultima attività API): scaduto = il prossimo turno
        # ripaga il prefisso a freddo — utile per il timing delle deleghe
        # (asse 6). TTL da FD_CACHE_TTL_S, default 3600 (Max; 300 = piani 5 min).
        lts=pts(_lts_iso) if _lts_iso else None
        if lts:
            try:
                from datetime import timezone as _tzk
                ttl=int(os.environ.get("FD_CACHE_TTL_S") or 3600)
                remc=ttl-(_dtb.now(_tzk.utc)-lts).total_seconds()
                # orologio a quarti: TTL residuo leggibile senza leggere i
                # minuti — pieno appena toccata, si svuota fino a ○ exp
                if remc<=0: cache="y:○,exp"
                else:
                    q=remc/max(ttl,1)
                    gl="●" if q>0.875 else ("◕" if q>0.625 else ("◑" if q>0.375 else ("◔" if q>0.125 else "○")))
                    lblc=f"{int(remc//60)}m" if remc>=120 else f"{int(remc)}s"
                    cache=("g:" if remc>600 else ("y:" if remc>=60 else "r:"))+gl+","+lblc
            except Exception: pass
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
        if heavy:
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
            # Query larga 24h SENZA zoneinfo (spawn misurato ~150 ms): copre ogni
            # finestra daily possibile. zoneinfo si importa SOLO se esistono eventi
            # esterni recenti — a riposo il render non paga il costo delle finestre.
            xrows=[]
            dbf=fd/"telemetry.db"
            if dbf.is_file():
                try:
                    import sqlite3
                    from datetime import datetime as _dt2, timedelta as _td2, timezone as _tz2
                    floor=(_dt2.now(_tz2.utc)-_td2(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    con=sqlite3.connect(dbf, timeout=0.3)
                    con.execute("PRAGMA busy_timeout=300")  # render: mai bloccare a lungo
                    for (ts,pl) in con.execute("SELECT ts, payload FROM events WHERE event=? AND ts >= ?", ("verification", floor)):
                        try: p=json.loads(pl or "{}")
                        except Exception: continue
                        if p.get("kind")=="cross-family" and p.get("provider"):
                            xrows.append((str(ts),p["provider"]))
                    con.close()
                except Exception: pass
            xmeta={}
            if xrows or active:
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
            if xrows:
                from datetime import datetime as _dt3, timezone as _tz3
                today=_dt3.now(_tz3.utc).strftime("%Y-%m-%d")
                for ts,prov in xrows:
                    lo=xmeta[prov][1] if prov in xmeta else today
                    if ts>=lo:
                        xcounts[prov]=xcounts.get(prov,0)+1
            xparts=[]
            xratio=0.0
            for prov in sorted(set(list(xcounts)+([active] if active else []))):
                label=str(prov).lower()[:8]
                star="▲" if prov==active else ""
                n=xcounts.get(prov,0)
                if prov in xmeta and n>0:
                    rpd,_,nxt=xmeta[prov]
                    # stesso principio delle quote: orario staccato, nessun glifo
                    xparts.append(f"{label}{star} {n}/{rpd} {nxt}")
                    xratio=max(xratio,n/rpd)
                elif n>0: xparts.append(f"{label}{star}×{n}")
                elif star: xparts.append(label+star)
            if xparts:
                xcls="r:" if xratio>=0.95 else ("y:" if xratio>=0.8 else "g:")
                xf=xcls+",".join(xparts[:3])
        else:
            xf=str(H.get("xf") or "-")
        # Salva il giro pesante (scrittura atomica, solo quando e stato fatto).
        if heavy and _hf is not None:
            try:
                _hf.parent.mkdir(parents=True,exist_ok=True)
                _hv={"at":time.time(),"key":_hk,"dlg":dlg,"cmp":cmp,"lts":_lts_iso,
                     "nfl":_fd_nfl,"stuck":_fd_stuck,"xf":xf,"pace":pace,"brn":brn,
                     "spent":spent,"spent_decl":(b_open.get("declared_at") if b_open else None)}
                _ht=_hf.with_name(f"{_hf.name}.{os.getpid()}.tmp")
                _ht.write_text(json.dumps(_hv)); os.replace(_ht,_hf)
            except Exception: pass
    except Exception:
        pass
    # bdg puo contenere spazi (allarmi a parole intere) ma la shell fa read
    # word-split: gli spazi viaggiano come virgole, la shell li ripristina
    bdg=str(bdg).replace(" ",",")
    # model conserva lo spazio del display_name: stessa disciplina virgola
    model=str(model).replace(" ",",")
    # dlg ha spazi interni ("SONNET-5 12k"): non è più ultimo campo del read
    # shell, quindi stessa disciplina di bdg (virgole, la shell le ripristina)
    dlg=str(dlg).replace(" ",",")
    # xf con residuo ("gemini 2/1500→09:00") ha spazi: stessa disciplina
    xf=str(xf).replace(" ",",")
    prn=str(prn).replace(" ",",")
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
    # [PLAIN 1.39] tre token in piu per la resa in parole: stuck = minuti del
    # delegato in volo da piu tempo; vrf = esito del verify eseguibile dallo
    # Stop hook (rc:comando); prio = task dell incidente di UN ALTRA sessione
    # che tiene la precedenza sulla quota. Assenti → "-" (mai inventati).
    stuck="-"; vrf="-"; prio="-"
    try:
        # minuti di silenzio del vivo piu silenzioso (vedi _fd_inflight sopra)
        if _fd_nfl and _fd_stuck: stuck=str(_fd_stuck)
    except Exception: pass
    try:
        if b_open:
            _sf=bf.with_name(bf.stem+".state.json")
            if _sf.is_file():
                _st=json.loads(_sf.read_text())
                if _st.get("verify_rc") is not None:
                    vrf=str(_st.get("verify_rc"))+":"+str(_st.get("verify_cmd") or "")
    except Exception: pass
    try:
        _pf=Path.home()/".claude"/"fable-director"/"priority.json"
        if _pf.is_file():
            _pr=json.loads(_pf.read_text())
            from datetime import datetime as _d3, timezone as _z3
            _ex=_d3.fromisoformat(str(_pr.get("expires_at")))
            if _ex.tzinfo is None: _ex=_ex.replace(tzinfo=_z3.utc)
            _acct=_hl.sha256((os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home()/".claude")).encode()).hexdigest()[:8]
            if _d3.now(_z3.utc) < _ex and _pr.get("account")==_acct and _pr.get("session_id")!=sid:
                prio=str(_pr.get("task") or "incident")[:60]
    except Exception: pass
    vrf=str(vrf).replace(" ",","); prio=str(prio).replace(" ",",")
    return [model,pct,rl,rlt,wk,wkt,bdg,xf,dlg,cache,cmp,grind,eff,bar,win,lk,fw,prn,pru,stuck,vrf,prio,pace,brn,dir]


NAMES = ("MODEL", "PCT", "RL", "RLT", "WK", "WKT", "BDG", "XF", "DLG", "CACHE", "CMP",
         "GRIND", "EFF", "BAR", "WIN", "LK", "FW", "PRN", "PRU", "STUCK", "VRF", "PRIO",
         "PACE", "BRN", "DIR")


def mode():
    """FD_STATUSLINE_MODE, poi statusline.json {"mode": ...}; default plain."""
    m = os.environ.get("FD_STATUSLINE_MODE", "")
    if not m:
        cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        try:
            with open(os.path.join(cfg, "fable-director", "statusline.json"), encoding="utf-8") as f:
                m = (json.load(f) or {}).get("mode") or ""
        except Exception:
            m = ""
    return m


def release_lock():
    """Toglie il lock di statusline-ctx.sh (il file statusline-cache/<sid>.lock)."""
    lk = os.environ.get("FD_SL_LOCK", "")
    if lk.endswith(".lock") and os.path.basename(os.path.dirname(lk)) == "statusline-cache":
        try:
            if os.path.isfile(lk):
                os.unlink(lk)
        except OSError:
            pass


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        tok = compute(raw)
    except Exception:
        tok = ["-"] * len(NAMES)
    finally:
        release_lock()
    if mode() == "expert":
        # la shell rende la riga storica: marcatore + 25 token separati da spazio
        sys.stdout.write("FDTOK " + " ".join(str(t) for t in tok) + "\n")
        return 0
    for k, v in zip(NAMES, tok):
        os.environ["FD_SL_" + k] = str(v)
    try:
        import importlib.util as iu
        sp = iu.spec_from_file_location("fd_statusline_plain", os.path.join(HERE, "statusline-plain.py"))
        mod = iu.module_from_spec(sp)
        sp.loader.exec_module(mod)
        return mod.main() or 0
    except Exception:
        sys.stdout.write("Claude")
        return 0


if __name__ == "__main__":
    sys.exit(main())
