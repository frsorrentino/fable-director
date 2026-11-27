#!/bin/bash
# fable-director — installer idempotente della statusline in settings.json.
#
# Perché serve: la statusLine NON è un componente che un plugin può registrare
# da solo (a differenza di hook/skill/command). Va scritta in settings.json.
# Questo script lo fa in modo deterministico, merge-safe e uguale su ogni macchina:
# si auto-localizza accanto a statusline-ctx.sh, quindi il path assoluto scritto in
# settings.json è sempre quello reale di QUESTA installazione (GitHub o directory locale).
#
# Uso:
#   bash statusline-install.sh          # installa / aggiorna il path
#   bash statusline-install.sh --remove # rimuove SOLO la nostra statusLine (e la esclude dall'auto-install)
#   bash statusline-install.sh --auto   # dal SessionStart hook (1.50.1): installa o aggiorna la nostra
#                                       # senza chiedere, muto se c'e' gia', di terzi o rimossa con --remove
#   bash statusline-install.sh --expert # riga storica con le sigle (default: plain, parole)
#   bash statusline-install.sh --plain  # torna alla resa in parole
#
# Dopo la scrittura: riavviare Claude Code (la statusLine è letta all'avvio).

set -euo pipefail

# Every file is named as "${CLAUDE_PLUGIN_ROOT}/scripts/..." (1.65.3): set for
# hooks, so for the SessionStart --auto run. From /fable-director:statusline
# it may be missing: the installer's own absolute path, which the command
# spells out, gives the plugin root. Backslashes become slashes (Windows).
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ] || [ ! -f "${CLAUDE_PLUGIN_ROOT}/scripts/statusline-ctx.sh" ]; then
  SELF="${BASH_SOURCE[0]}"
  case "$SELF" in /*|[A-Za-z]:[/\\]*) ;; *) SELF="$PWD/$SELF" ;; esac
  SELF="$(printf '%s' "$SELF" | tr '\\' '/')"
  CLAUDE_PLUGIN_ROOT="${SELF%/scripts/*}"
fi
TARGET="$(printf '%s' "${CLAUDE_PLUGIN_ROOT}/scripts/statusline-ctx.sh" | tr '\\' '/')"
CFG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CFG_DIR/settings.json"

if [ ! -f "$TARGET" ]; then
  echo "ERRORE: statusline-ctx.sh non trovato accanto all'installer ($TARGET)." >&2
  exit 1
fi

MODE="install"
[ "${1:-}" = "--remove" ] && MODE="remove"
[ "${1:-}" = "--auto" ] && MODE="auto"
# Marker di rinuncia: scritto da --remove, letto da --auto (una statusLine tolta
# apposta non deve ricomparire alla sessione dopo); un install esplicito lo toglie.
OPTOUT="$CFG_DIR/fable-director/statusline-optout"
# --expert / --plain: modalita di resa (1.39). plain = parole, solo eccezioni
# (default); expert = la riga storica con le sigle. Scrive
# <config>/fable-director/statusline.json e NON tocca settings.json.
if [ "${1:-}" = "--expert" ] || [ "${1:-}" = "--plain" ]; then
  SL_DIR="$CFG_DIR/fable-director"
  printf '{"mode": "%s"}\n' "${1#--}" > "$SL_DIR/statusline.json"
  echo "statusline mode: ${1#--} ($SL_DIR/statusline.json) — takes effect at the next refresh"
  exit 0
fi

# Tutta la logica di merge in python (scripts/statusline-merge.py, 1.65.3: un
# file, non codice letto da stdin): parsing/scrittura JSON deterministici,
# preserva le altre chiavi, non tocca una statusLine di terzi.
CLAUDE_SETTINGS="$SETTINGS" FD_TARGET="$TARGET" FD_MODE="$MODE" FD_OPTOUT="$OPTOUT" \
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/py.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/statusline-merge.py"
