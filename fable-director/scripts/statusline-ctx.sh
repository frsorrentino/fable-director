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
read -r model pct rl rlt wk wkt bdg <<EOF
$(printf '%s' "$input" | python3 -c '
import json,sys,os,time
from pathlib import Path
model=pct=rl=rlt=wk=wkt=bdg="-"
def fmt_reset(ts):
    # entro 24h: orario; oltre: "6 lug" (giorno + mese, indipendente dal locale)
    try:
        ts=int(ts)
        lt=time.localtime(ts)
        if ts-time.time() < 86400: return time.strftime("%H:%M", lt)
        mesi=["gen","feb","mar","apr","mag","giu","lug","ago","set","ott","nov","dic"]
        #   = spazio unificatore: si vede come spazio ma non spezza il read shell
        return f"{lt.tm_mday} {mesi[lt.tm_mon-1]}"
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
    for k in ("seven_day","weekly","seven_days"):
        wd=d.get("rate_limits",{}).get(k,{})
        w=wd.get("used_percentage")
        if w is not None:
            wk=f"{w:.0f}"
            wkt=fmt_reset(wd.get("resets_at")) or "-"
            break
    cwd=d.get("cwd") or os.getcwd()
    slug="-"+str(cwd).strip("/").replace("/","-").replace(".","-")
    bf=Path.home()/".claude"/"fable-director"/"budgets"/f"{slug}.json"
    if bf.is_file():
        b=json.loads(bf.read_text())
        st=b.get("status")
        if st=="open": bdg="2x" if b.get("warned") else "ok"
        elif st=="flagged": bdg="3x"
except Exception:
    pass
print(model,pct,rl,rlt,wk,wkt,bdg)
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
printf '%s' "${out# }"
