#!/usr/bin/env bash
# SessionStart hook: inietta il kernel della policy (~500 token) nel contesto.
# Il corpo completo si carica on-demand invocando la skill fable-director:delega-efficiente.
printf 'FABLE-DIRECTOR KERNEL (delegation policy — full body: skill fable-director:delega-efficiente):\n'
cat "${CLAUDE_PLUGIN_ROOT}/kernel.md" 2>/dev/null || true

# Sentinella versione: avvisa se la cache in esecuzione è più vecchia della
# sorgente marketplace locale (la cache non si auto-aggiorna mai).
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/version-sentinel.py" 2>/dev/null || true

# Hindsight: ripesca gli sfondamenti già auto-registrati su QUESTO cwd. Muto
# dove non c'è evidenza (zero token), tetto 5 righe dove c'è.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/session-hindsight.py" 2>/dev/null || true

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
if [ ! -f "$XF_CFG" ] && [ ! -f "$XF_DONE" ]; then
  mkdir -p "$HOME/.claude/fable-director" 2>/dev/null || true
  N=$(cat "$XF_COUNT" 2>/dev/null || echo 0)
  case "$N" in (*[!0-9]*|"") N=0;; esac
  if [ "$N" -lt 3 ]; then
    echo $((N + 1)) > "$XF_COUNT" 2>/dev/null || true
    cat <<EOF

XF ONBOARDING — ASK THE USER NOW (attempt $((N + 1))/3): external free-tier executors are not configured. Before anything else this session, pose ONE multiple-choice question with the AskUserQuestion tool (plain question if the tool is unavailable) — header "Executors", question "fable-director can route non-quality-sensitive batch work and cross-family verification to free external models, at zero Claude tokens. Connect one now?", options: (1) "Gemini — free API key" for a Google AI Studio key with daily-reset limits; (2) "Codex CLI — ChatGPT plan" usage included in their plan; (3) "Both"; (4) "No — don't ask again". On 1-3: run python3 "${CLAUDE_PLUGIN_ROOT}/scripts/external-exec.py" --doctor and follow its guidance (paid API keys work in the same config entries). On 4: run touch "$XF_DONE" so this never reappears. If the user skips the question, do nothing — it will be asked again (max 3 sessions, then silence).
EOF
  else
    : > "$XF_DONE" 2>/dev/null || true
  fi
fi

# Hint legenda one-shot: la statusline usa sigle compatte — una volta sola,
# suggerisci al modello di indicare il comando che le spiega.
HELP_MARK="$HOME/.claude/fable-director/help-hint-shown"
if [ ! -f "$HELP_MARK" ]; then
  mkdir -p "$HOME/.claude/fable-director" 2>/dev/null || true
  printf '\nSTATUSLINE LEGEND (mention to the user ONCE, then never again): /fable-director:help explains every statusline segment, symbol and color.\n'
  : > "$HELP_MARK" 2>/dev/null || true
fi

PB="$HOME/.claude/delega-playbook.md"
if [ -f "$PB" ]; then
  printf '\nHeuristics playbook: %s — consult it before orchestrating batch/workflow/multi-agent work.\n' "$PB"
else
  printf '\nPlaybook not initialized: copy %s to %s (only if missing, never overwrite).\n' "${CLAUDE_PLUGIN_ROOT}/playbook-template.md" "$PB"
fi
