#!/usr/bin/env bash
# SessionStart hook: inietta il kernel della policy (~500 token) nel contesto.
# Il corpo completo si carica on-demand invocando la skill fable-director:delega-efficiente.

# Payload dell'hook, letto UNA volta e ripassato ai figli che lo usano
# (session-hindsight.py prende il cwd da stdin). `source` distingue cinque
# aperture: startup | resume | clear | compact | fork. Fino a 1.28.x lo script
# non leggeva stdin e le trattava tutte uguali: a ogni COMPATTAZIONE bruciava
# un tentativo dei 3 dell'onboarding executor esterni e ripeteva l'hindsight
# già visto nella stessa sessione. Il kernel invece va riemesso sempre —
# dopo una compattazione il testo iniettato può non esserci più.
FD_INPUT=""
[ -t 0 ] || FD_INPUT="$(cat 2>/dev/null || true)"
FD_SOURCE="$(printf '%s' "$FD_INPUT" | python3 -c $'import json,sys\ntry:\n    d=json.load(sys.stdin)\n    print((d or {}).get("source") or "")\nexcept Exception:\n    print("")' 2>/dev/null || true)"

# Cap dell'harness (Claude Code 2.1.260, letto dal binario): l'output di un
# SessionStart vale come additionalContext, tetto 8000 caratteri / 200 righe,
# oltre = TRONCATO IN SILENZIO a meta' parola (misurato dalla community su
# claude-code#91870). Il kernel da solo e' ~6200 caratteri: i blocchi
# opzionali lo portavano al limite. Tutto passa da un buffer: kernel sempre
# intero, blocco XF solo se entra (e senza bruciare il tentativo), taglio
# finale VISIBILE se qualcosa sfora comunque.
FD_CAP_CHARS=7900
FD_CAP_LINES=195

fd_core() {
  printf 'FABLE-DIRECTOR KERNEL (delegation policy — full body: skill fable-director:delega-efficiente):\n'
  cat "${CLAUDE_PLUGIN_ROOT}/kernel.md" 2>/dev/null || true

  # Sentinella versione: avvisa se la cache in esecuzione è più vecchia della
  # sorgente marketplace locale (la cache non si auto-aggiorna mai).
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/version-sentinel.py" 2>/dev/null || true

  # Sessione forkata: budget e telemetria sono per-cwd, non per-sessione. Padre e
  # fork condividono lo stesso file di budget — è un limite dichiarato nel README,
  # qui diventa un avviso nel momento esatto in cui morde.
  if [ "$FD_SOURCE" = "fork" ]; then
    printf '\nFD ⑂ forked session: the open pre-budget is ONE FILE PER CWD, shared with the parent session — parallel budgeted work needs separate worktrees, not a fork.\n'
  fi

  # Righe corte e sempre volute PRIMA dei blocchi lunghi: se il cap taglia,
  # taglia in fondo.
  PB="$HOME/.claude/delega-playbook.md"
  if [ -f "$PB" ]; then
    printf '\nHeuristics playbook: %s — consult it before orchestrating batch/workflow/multi-agent work.\n' "$PB"
  else
    printf '\nPlaybook not initialized: copy %s to %s (only if missing, never overwrite).\n' "${CLAUDE_PLUGIN_ROOT}/playbook-template.md" "$PB"
  fi

  # Soft-deps shipped by the plugin (media-tools): added to the user's registry
  # once, never overwriting — one line only when something changes.
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/soft-deps-sync.py" 2>/dev/null || true

  # Hint legenda one-shot: la statusline usa sigle compatte — una volta sola,
  # suggerisci al modello di indicare il comando che le spiega.
  HELP_MARK="$HOME/.claude/fable-director/help-hint-shown"
  if [ ! -f "$HELP_MARK" ]; then
    mkdir -p "$HOME/.claude/fable-director" 2>/dev/null || true
    printf '\nSTATUSLINE LEGEND (mention to the user ONCE, then never again): /fable-director:help explains every statusline segment, symbol and color.\n'
    : > "$HELP_MARK" 2>/dev/null || true
  fi

  # Hindsight: ripesca gli sfondamenti già auto-registrati su QUESTO cwd. Muto
  # dove non c'è evidenza (zero token), tetto 5 righe dove c'è. Saltato dopo una
  # compattazione: stessa sessione, avviso già dato, ripeterlo è solo costo.
  if [ "$FD_SOURCE" != "compact" ]; then
    printf '%s' "$FD_INPUT" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/session-hindsight.py" 2>/dev/null || true
  fi
}
FD_OUT="$(fd_core)"

# Onboarding executor esterni, come DOMANDA a scelta multipla (1.17.1).
# Il vecchio "relay this notice" bruciava il one-shot anche se il modello non
# riferiva nulla; ora l'hook istruisce il modello a PORRE la domanda con
# AskUserQuestion (consegna interattiva, l'utente risponde), il marker finale
# si scrive solo a risposta data ("No"), e un contatore ferma comunque tutto
# alla 3ª sessione senza risposta. Il marker legacy (bruciato alla cieca dalle
# versioni <=1.17.0) viene ritirato: quegli utenti ricevono la domanda.
XF_CFG="$HOME/.claude/fable-director/cross-family.json"
XF_DONE="$HOME/.claude/fable-director/xf-onboarding-done"
XF_COUNT="$HOME/.claude/fable-director/xf-onboarding-count"
rm -f "$HOME/.claude/fable-director/xf-onboarding-shown" 2>/dev/null || true
# Solo APERTURE VERE consumano un tentativo (startup/resume, o `source` assente
# su Claude Code vecchi): compact/fork/clear sono la stessa sessione che
# ricomincia, e i 3 tentativi finivano bruciati senza che nessuno rispondesse.
case "$FD_SOURCE" in compact|fork|clear) XF_SKIP=1;; *) XF_SKIP=0;; esac
if [ "$XF_SKIP" = "0" ] && [ ! -f "$XF_CFG" ] && [ ! -f "$XF_DONE" ]; then
  mkdir -p "$HOME/.claude/fable-director" 2>/dev/null || true
  N=$(cat "$XF_COUNT" 2>/dev/null || echo 0)
  case "$N" in (*[!0-9]*|"") N=0;; esac
  if [ "$N" -lt 3 ]; then
    FD_XF="$(cat <<EOF

XF ONBOARDING — ASK THE USER NOW (attempt $((N + 1))/3): external free-tier executors are not configured. Before anything else, ask ONE multiple-choice question with the AskUserQuestion tool (plain question if unavailable): header "Executors"; question "fable-director can route non-quality-sensitive batch work and cross-family verification to free external models, at zero Claude tokens. Connect one now?"; options (1) "Gemini — free API key" (Google AI Studio, daily-reset limits), (2) "Codex CLI — ChatGPT plan" (usage included in the plan), (3) "Both", (4) "No — don't ask again". On 1-3: run python3 "${CLAUDE_PLUGIN_ROOT}/scripts/external-exec.py" --doctor and follow its guidance (paid API keys: same config entries with billing:"paid", consent-gated, never auto-proposed). On 4: run touch "$XF_DONE" so this never reappears. If the user skips it, do nothing — asked again next session (max 3, then silence).
EOF
)"
    # Il blocco entra solo se resta sotto il cap: altrimenti verrebbe troncato
    # a meta' e la domanda non arriverebbe mai — e il tentativo NON si consuma.
    if [ $(( ${#FD_OUT} + ${#FD_XF} + 1 )) -le "$FD_CAP_CHARS" ]; then
      FD_OUT="${FD_OUT}"$'\n'"${FD_XF}"
      echo $((N + 1)) > "$XF_COUNT" 2>/dev/null || true
    fi
  else
    : > "$XF_DONE" 2>/dev/null || true
  fi
fi

# Taglio finale, marcato: meglio una riga che dice "tagliato" di una parola a meta'.
printf '%s\n' "$FD_OUT" | FD_CAP_CHARS="$FD_CAP_CHARS" FD_CAP_LINES="$FD_CAP_LINES" python3 -c '
import os, sys
t = sys.stdin.read()
C, L = int(os.environ["FD_CAP_CHARS"]), int(os.environ["FD_CAP_LINES"])
cut = False
lines = t.split("\n")
if len(lines) > L:
    t = "\n".join(lines[:L]); cut = True
if len(t) > C:
    t = t[:C]; cut = True
if cut:
    t = t.rstrip() + "\n[fd: SessionStart output cut at the 8000-char/200-line hook cap]"
sys.stdout.write(t if t.endswith("\n") else t + "\n")
'
