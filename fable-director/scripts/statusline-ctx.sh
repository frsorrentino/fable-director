#!/bin/bash
# fable-director — statusline:
#   [MODEL]         modello attivo (model.display_name)
#   [CTX %]         riempimento context window della conversazione
#   [5H % HH:MM]    quota piano finestra 5 ore + orario reset locale (staccato, in 239)
#   [7D %]          quota piano settimanale (se il campo esiste)
#   [pace %/d]      senza finestra 5 ore: settimana restante / giorni al reset,
#                   con il consumo orario se sopra soglia (quota_pace.py)
#   [BDG r×·eff]    pre-budget fable-director: ratio live output consumato/atteso
#                   (stessa contabilità dello Stop hook, incrementale) + effort
#                   dichiarato; verde <2×, giallo ≥2×, rosso ≥3× (flagged: 3×).
#                   Senza transcript degrada a ok|2×|3× dal solo budget file.
# Posticipa il badge caveman (se il plugin è presente nel profilo attivo) in
# coda a riga 1, e lo lascia cadere per primo quando la larghezza non basta.
#
# Setup in settings.json:
#   "statusLine": { "type": "command", "command": "bash \"<path>/scripts/statusline-ctx.sh\"" }

# Calcolo in statusline-core.py (1.63.1): da file e non piu con python -c, che su
# Windows superava il tetto di 32 767 caratteri della riga di comando.
#
# Una esecuzione alla volta per sessione (1.63.1). Su una macchina carica un
# giro durava 10-14 s: con refreshInterval 5 si accavallavano, Claude Code
# annullava quello in corso a ogni aggiornamento e la riga spariva. Ora:
#   - lock per sessione: il file statusline-cache/<sid>.lock, creato con
#     noclobber (O_EXCL, atomico) e tolto da statusline-core.py a fine calcolo;
#   - il calcolo gira staccato e scrive statusline-cache/<sid>.out; qui si
#     aspetta al massimo FD_SL_WAIT_S secondi (default 2), poi si stampa
#     l ultima riga in cache e il calcolo finisce per il giro dopo;
#   - lock gia preso: si stampa subito la cache e si esce;
#   - le parti pesanti al piu ogni FD_SL_HEAVY_S secondi (statusline-core.py).

# stdin senza processi (prima: $(cat)); read -d '' arriva a EOF e torna 1
IFS= read -r -d '' input || true

# Interprete risolto una volta per refresh (py.sh: su Windows `python3` e' l'alias
# dello Store; il percorso trovato e' in cache, niente probe a ogni refresh). Senza
# Python la statusline lo dice, invece di restare vuota.
case "$0" in */*|*\\*) SL_DIR="${0%[/\\]*}" ;; *) SL_DIR=. ;; esac   # dirname senza processo; \ = Windows
. "$SL_DIR/py.sh" 2>/dev/null || { printf 'fable-director: no Python 3 found — install Python 3.8+\n'; exit 0; }

# Diagnostica: se esiste il flag file, salva l'ultimo stdin ricevuto (per capire
# quali campi il piano/account corrente espone). Attiva: touch <config>/fable-director/.statusline-debug
FD_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/fable-director"
[ -f "$FD_DIR/.statusline-debug" ] && printf '%s' "$input" > "$FD_DIR/statusline-last.json" 2>/dev/null

# Badge caveman se il plugin e' attivo nel profilo. Si legge il SUO file di stato
# (dati), non si esegue il suo script: il plugin non lancia codice che sta fuori
# da se'. Stesse regole di caveman-statusline.sh: file della sessione, poi il
# flag globale; niente symlink; 64 byte, solo [a-z0-9-]; whitelist dei modi.
CFG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
badge=""
# session_id con la regex di bash, senza processi (prima: grep | head | sed)
cv_sid=""
_re='"session_id"[[:space:]]*:[[:space:]]*"([^"]*)"'
[[ $input =~ $_re ]] && cv_sid=${BASH_REMATCH[1]}
case "$cv_sid" in ''|*[!A-Za-z0-9_-]*) cv_sid="" ;; esac
[ "${#cv_sid}" -gt 128 ] && cv_sid=""
cv_flag="$CFG_DIR/.caveman-active"
[ -n "$cv_sid" ] && [ -f "$CFG_DIR/.caveman-sessions/$cv_sid.mode" ] && cv_flag="$CFG_DIR/.caveman-sessions/$cv_sid.mode"
# Solo se caveman e' installato (come prima: il suo script presente, non eseguito).
if [ -f "$CFG_DIR/plugins/marketplaces/caveman/src/hooks/caveman-statusline.sh" ] && [ -f "$cv_flag" ] && [ ! -L "$cv_flag" ]; then
  cv_mode=$(head -c 64 "$cv_flag" 2>/dev/null | tr -d '\n\r' | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')
  case "$cv_mode" in
    full) badge=$(printf '\033[38;5;172m[CAVEMAN]\033[0m') ;;
    lite|ultra|wenyan-lite|wenyan|wenyan-full|wenyan-ultra|commit|review|compress)
      badge=$(printf '\033[38;5;172m[CAVEMAN:%s]\033[0m' "$(printf '%s' "$cv_mode" | tr '[:lower:]' '[:upper:]')") ;;
  esac
fi

# Resa expert: la riga storica con le sigle, dai 25 token di statusline-core.py.
expert_render() {
  read -r model pct rl rlt wk wkt bdg xf dlg cache cmp grind eff bar win lk fw prn pru stuck vrf prio pace brn dir <<EOF
$1
EOF
  dir=${dir%$'\r'}   # Windows: print() chiude con \r\n, il \r finiva nel nome cartella

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

  # Link OSC 8 (opt-in via plan file): il testo resta identico, il wrap
  # aggiunge solo la marcatura URL. Il terminale che non li rende li ignora.
  osc8() { # $1=url $2=testo-gia-colorato
    if [ "$lk" = "1" ] && [ -n "$1" ] && [ "$1" != "-" ]; then
      printf '\033]8;;%s\033\\%s\033]8;;\033\\' "$1" "$2"
    else printf '%s' "$2"; fi
  }
  SEP=$(printf '\033[38;5;239m · \033[0m')
  out=""
  app() { [ -n "$out" ] && out="$out$SEP"; out="$out$1"; }
  USAGE_URL="https://claude.ai/settings/usage"

  # Riga 1 — IDENTITA e QUOTE. Degrada solo nella DECORAZIONE, mai nel dato:
  #   liv 2 = completa · liv 1 = senza gauge ctx · liv 0 = anche senza orari reset
  # La gauge e ridondante (la percentuale le sta accanto e il colore porta gia
  # l allarme); gli orari di reset sono il dato di comodo che cede per ultimo.
  # Numeri, sigle e colori restano a qualunque larghezza.
  compose1() {
    out=""
    # ✦ MODELLO in penombra + effort LIVE attaccato (giallo da xhigh: brucia quota)
    if [ "$model" != "-" ]; then
      seg="$(printf '\033[38;5;245m\342\234\246 %s\033[0m' "$(printf '%s' "$model" | tr ',' ' ')")"
      case "$eff" in
        y:*) seg="$seg$(printf '\033[38;5;220m%s\033[0m' "${eff#y:}")" ;;
        d:*) seg="$seg$(printf '\033[38;5;245m%s\033[0m' "${eff#d:}")" ;;
      esac
      app "$(osc8 "https://status.anthropic.com" "$seg")"
    fi
    # cartella di lavoro (1.50.2), in penombra dopo il modello: con piu sessioni
    # aperte e cio che le distingue. Cade per prima (liv 0, terminale stretto).
    if [ "$dir" != "-" ] && [ "$1" -ge 1 ]; then
      app "$(printf '\033[38;5;245m\342\226\270 %s\033[0m' "$dir")"
    fi
    # ctx: gauge 8 celle (solo liv 2) + /1M se window estesa
    if [ "$pct" != "-" ]; then
      gb=""; [ "$1" -ge 2 ] && [ "$bar" != "-" ] && gb="$bar "
      gw=""; [ "$win" != "-" ] && gw="$win"
      app "$(printf "$(color_for "$pct")ctx %s%s%%%s\033[0m" "$gb" "$pct" "$gw")"
    fi
    # cmp solo se ≥1 compattazione: contesto perso, deviazione per natura
    [ "$cmp" != "-" ] && [ -n "$cmp" ] && app "$(printf '\033[38;5;220mcmp %s\033[0m' "$cmp")"
    # Orario di azzeramento SENZA glifo: staccato da uno spazio e in 239, la
    # penombra dei separatori. Nessun simbolo da imparare e nessuna freccia che
    # suggerisca una tendenza. Senza colore resta comunque distinguibile: la
    # FORMA lo dichiara (17:10 ha i due punti, "30 Jul" il mese) — il marcatore
    # che sopravvive al monocromatico e il formato stesso, non il colore.
    if [ "$rl" != "-" ]; then
      seg="$(printf "$(color_for "$rl")5H %s%%\033[0m" "$rl")"
      [ "$rlt" != "-" ] && [ "$1" -ge 1 ] && seg="$seg$(printf '\033[38;5;239m %s\033[0m' "$rlt")"
      app "$(osc8 "$USAGE_URL" "$seg")"
    fi
    if [ "$wk" != "-" ]; then
      seg="$(printf "$(color_for "$wk")7D %s%%\033[0m" "$wk")"
      [ "$wkt" != "-" ] && [ "$1" -ge 1 ] && seg="$seg$(printf '\033[38;5;239m %s\033[0m' "$wkt")"
      app "$(osc8 "$USAGE_URL" "$seg")"
    fi
    if [ "$pace" != "-" ]; then
      if [ "$brn" != "-" ]; then
        app "$(printf '\033[38;5;220mpace %s%%/d %s%%/h\033[0m' "$pace" "$brn")"
      else
        app "$(printf '\033[38;5;245mpace %s%%/d\033[0m' "$pace")"
      fi
    fi
    # Bound finestra premium (✦≤N% / ✦? a bound saturo) — vive accanto al 7D
    # da cui deriva; stesso link usage: li c e il valore vero per-modello.
    case "$fw" in
      g:*) app "$(osc8 "$USAGE_URL" "$(printf '\033[38;5;245m%s\033[0m' "${fw#g:}")")" ;;
      y:*) app "$(osc8 "$USAGE_URL" "$(printf '\033[38;5;220m%s\033[0m' "${fw#y:}")")" ;;
      r:*) app "$(osc8 "$USAGE_URL" "$(printf '\033[38;5;196m%s\033[0m' "${fw#r:}")")" ;;
    esac
    # fail ×N grinding: stato PROTETTO, resta in riga 1 accanto alle quote —
    # uno streak esiste anche senza budget/deleghe e non deve dipendere dalla
    # riga 2. Giallo a 2 (early warning), rosso da 3 (il nudge e scattato).
    # Non degrada MAI: e un dato, e quello che ti sta costando di piu.
    if [ "$grind" != "-" ] && [ -n "$grind" ]; then
      if [ "$grind" -ge 3 ] 2>/dev/null; then
        app "$(printf '\033[38;5;196mfail \303\227%s\033[0m' "$grind")"
      else
        app "$(printf '\033[38;5;220mfail \303\227%s\033[0m' "$grind")"
      fi
    fi
    printf '%s' "$out"
  }
  # Takeover: budget flagged / enforcement off (classe r:) va IN TESTA alla
  # riga 1 a sfondo pieno — la gerarchia visiva si inverte con la priorita.
  # Le classi g:/y: del budget vivono invece nella riga 2 (attivita).
  tko=""; seg_bdg=""
  case "$bdg" in
    r:*) tko="$(printf '\033[48;5;196m\033[38;5;16m %s \033[0m' "$(printf '%s' "${bdg#r:}" | tr ',' ' ')") " ;;
    g:*) seg_bdg="$(printf '\033[38;5;245m%s\033[0m' "$(printf '%s' "${bdg#g:}" | tr ',' ' ')")" ;;
    y:*) seg_bdg="$(printf '\033[38;5;220m%s\033[0m' "$(printf '%s' "${bdg#y:}" | tr ',' ' ')")" ;;
  esac
  # pr aperta: colore da review_state (verde=approved e l unico verde della
  # riga: significa mergiabile; giallo=changes_requested; penombra=pending)
  seg_pr=""
  if [ "$prn" != "-" ] && [ -n "$prn" ]; then
    ptxt="$(printf '%s' "${prn#[acp]:}" | tr ',' ' ')"
    case "$prn" in
      a:*) seg_pr="$(printf '\033[38;5;114m%s\033[0m' "$ptxt")" ;;
      c:*) seg_pr="$(printf '\033[38;5;220m%s\033[0m' "$ptxt")" ;;
      *)   seg_pr="$(printf '\033[38;5;245m%s\033[0m' "$ptxt")" ;;
    esac
    seg_pr="$(osc8 "$pru" "$seg_pr")"
  fi
  seg_xf=""; seg_dlg=""; seg_cache=""
  case "$cache" in
    g:*) seg_cache="$(printf '\033[38;5;245mcache %s\033[0m' "$(printf '%s' "${cache#g:}" | tr ',' ' ')")" ;;
    y:*) seg_cache="$(printf '\033[38;5;220mcache %s\033[0m' "$(printf '%s' "${cache#y:}" | tr ',' ' ')")" ;;
    r:*) seg_cache="$(printf '\033[38;5;196mcache %s\033[0m' "$(printf '%s' "${cache#r:}" | tr ',' ' ')")" ;;
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
    seg_xf="$(osc8 "https://aistudio.google.com/usage" "$seg_xf")"
  fi
  [ "$dlg" != "-" ] && [ -n "$dlg" ] && \
    seg_dlg="$(printf '\033[38;5;245mdlg %s\033[0m' "$(printf '%s' "$dlg" | tr ',' ' ')")"
  # Larghezza in CARATTERI (glifi zen multibyte; wc -c li conterebbe tripli).
  # COLUMNS lo fornisce Claude Code ≥2.1.153; assente → 120 conservativo.
  # NB il terminatore OSC 8 e ESC + UN backslash: in sed va scritto \\ (due
  # backslash = due letterali, non matcha mai → le URL finivano contate nella
  # larghezza e la riga 2 degradava troppo presto).
  strip_esc() { sed -e 's/\x1b][^\x07\x1b]*\x07//g' -e 's/\x1b][^\x1b]*\x1b\\//g' -e 's/\x1b\[[0-9;]*m//g'; }
  plain_len() { printf '%s' "$1" | strip_esc | LC_ALL=C.UTF-8 wc -m 2>/dev/null || printf '%s' "$1" | strip_esc | wc -c; }
  W="${COLUMNS:-120}"
  case "$W" in (*[!0-9]*|"") W=120 ;; esac
  # Riga 2 on-demand — l ATTIVITA (budget, deleghe, esterni, cache): esiste
  # solo quando succede qualcosa; a riposo la statusline resta a una riga.
  # Degradazione deterministica dentro la riga 2: cade cache, poi dlg, poi
  # xf — MAI il budget. Riga 1 degrada solo nella decorazione (vedi compose1).
  L2P="$(printf '\033[38;5;239m\342\224\224 \033[0m')"
  compose2() {
    c=""
    a2() { [ -n "$c" ] && c="$c$SEP"; c="$c$1"; }
    [ -n "$seg_bdg" ] && a2 "$seg_bdg"
    [ -n "$seg_pr" ] && a2 "$seg_pr"
    [ "$1" -ge 2 ] && [ -n "$seg_dlg" ] && a2 "$seg_dlg"
    [ "$1" -ge 1 ] && [ -n "$seg_xf" ] && a2 "$seg_xf"
    # cache: non CREA mai la riga 2 se non e azionabile. Piena (g:, TTL >10m)
    # o scaduta da freddo non cambiano nessuna decisione e a riposo rendevano
    # la seconda riga permanente — contro la sua stessa regola. Restano visibili
    # quando la riga 2 esiste gia (informazione gratis, zero righe in piu).
    # Agli sgoccioli (y:/r:, TTL ≤10m) si mostra da sola: li il timing di una
    # delega cambia davvero — dopo la scadenza il turno dopo ripaga il prefisso.
    if [ "$1" -ge 3 ] && [ -n "$seg_cache" ]; then
      case "$cache" in
        g:*|y:○,exp) [ -n "$c" ] && a2 "$seg_cache" ;;
        *)           a2 "$seg_cache" ;;
      esac
    fi
    [ -n "$c" ] && printf '%s%s' "$L2P" "$c"
  }
  line1="$tko$(compose1 2)"
  # Badge caveman in CODA e non piu in testa: e l unico elemento sacrificabile
  # di riga 1 — non e un dato, e uno stato che l utente ha impostato lui e che
  # ogni risposta gli riconferma. Entra solo se ci sta: mai a costo di un numero.
  if [ -n "$badge" ]; then
    cand="$line1$(printf '\033[38;5;239m │ \033[0m')$badge"
    [ "$(plain_len "$cand")" -le "$W" ] && line1="$cand"
  fi
  [ "$(plain_len "$line1")" -gt "$W" ] && line1="$tko$(compose1 1)"  # cade la gauge ctx
  [ "$(plain_len "$line1")" -gt "$W" ] && line1="$tko$(compose1 0)"  # cadono gli orari reset
  line2="$(compose2 3)"
  [ -n "$line2" ] && [ "$(plain_len "$line2")" -gt "$W" ] && line2="$(compose2 2)"  # cade cache
  [ -n "$line2" ] && [ "$(plain_len "$line2")" -gt "$W" ] && line2="$(compose2 1)"  # cade dlg
  [ -n "$line2" ] && [ "$(plain_len "$line2")" -gt "$W" ] && line2="$(compose2 0)"  # cade xf
  printf '%s' "$line1"
  [ -n "$line2" ] && printf '\n%s' "$line2"
}

# Un giro completo: nucleo Python (plain: riga gia resa; expert: token), poi
# scrittura atomica della cache. Un risultato vuoto non sovrascrive la cache.
# FD_SL_LOCK: il nucleo lascia il lock appena ha finito, anche se fallisce.
render() {
  local res tmp
  res=$(printf '%s' "$input" | FD_SL_BADGE="$badge" FD_SL_LOCK="$SC_LOCK" "$FD_PYTHON" "$SL_DIR/statusline-core.py" 2>/dev/null)
  case "$res" in
    FDTOK\ *) res=$(expert_render "${res#FDTOK }") ;;
  esac
  [ -n "$res" ] || return 0
  tmp="$SC_OUT.$$.$RANDOM"
  printf '%s' "$res" > "$tmp" 2>/dev/null && mv -f "$tmp" "$SC_OUT" 2>/dev/null
}

SC="$FD_DIR/statusline-cache"
[ -d "$SC" ] || mkdir -p "$SC" 2>/dev/null
SC_KEY="${cv_sid:-nosid}"
SC_OUT="$SC/$SC_KEY.out"
SC_LOCK="$SC/$SC_KEY.lock"
# Cache letta senza processi (read -d '' fino a EOF). Nessuna cache ancora (il
# primo giro della sessione non ha finito): il nome del modello dall input,
# mai una riga vuota.
show_cache() {
  local c="" _rm='"display_name"[[:space:]]*:[[:space:]]*"([^"]*)"'
  if [ -s "$SC_OUT" ]; then
    IFS= read -r -d '' c < "$SC_OUT"
  else
    c="Claude"
    [[ $input =~ $_rm ]] && c="${BASH_REMATCH[1]%% (*}"
    c=$(printf '\033[38;5;245m%s\033[0m' "${c:0:24}")
  fi
  printf '%s' "$c"
  return 0
}
# Il lock porta l ora di presa (epoch, scritta da printf: nessun processo).
printf -v SC_NOW '%(%s)T' -1
take_lock() { local rc; set -C; { printf '%s\n' "$SC_NOW" > "$SC_LOCK"; } 2>/dev/null; rc=$?; set +C; return $rc; }

if ! take_lock; then
  # Gia in corso per questa sessione: l ultima riga subito, niente secondo giro.
  # Lock orfano (giro ucciso prima della fine): preso da piu di FD_SL_LOCK_S
  # secondi (default 60) → lo si riprende, riscrivendolo. Un lock vuoto (scrittura
  # mai finita) vale per l eta del file, con find: caso raro, l unico processo qui.
  lt=""; IFS= read -r lt < "$SC_LOCK" 2>/dev/null
  lt=${lt%$'\r'}
  stale=0
  case "$lt" in
    ''|*[!0-9]*) [ -n "$(find "$SC_LOCK" -maxdepth 0 -mmin +1 2>/dev/null)" ] && stale=1 ;;
    *) [ $(( SC_NOW - lt )) -ge "${FD_SL_LOCK_S:-60}" ] && stale=1 ;;
  esac
  if [ "$stale" = 0 ]; then
    show_cache
    exit 0
  fi
  printf '%s\n' "$SC_NOW" >| "$SC_LOCK" 2>/dev/null
fi

# Calcolo staccato: stdio lontani da quelli di Claude Code (che quindi non lo
# aspetta) e immune all annullamento del giro in primo piano. Alla fine scrive
# "done" sulla pipe: read -t aspetta quello, senza polling. Attesa massima
# FD_SL_WAIT_S (default 2 s); senza cache ancora, FD_SL_FIRST_WAIT_S (8 s).
w="${FD_SL_WAIT_S:-2}"
[ -s "$SC_OUT" ] || w="${FD_SL_FIRST_WAIT_S:-8}"
case "$w" in (*[!0-9.]*|""|.*) w=2 ;; esac
# (exec </dev/null 2>/dev/null per primo: uno stderr ereditato terrebbe aperta
#  la pipe di Claude Code fino alla fine del calcolo, misurato su Windows)
exec 3< <(exec </dev/null 2>/dev/null; trap '' HUP INT TERM; render >/dev/null; echo done)
IFS= read -r -t "$w" -u 3 _done
show_cache
exit 0
