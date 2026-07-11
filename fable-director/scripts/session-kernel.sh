#!/usr/bin/env bash
# SessionStart hook: inietta il kernel della policy (~500 token) nel contesto.
# Il corpo completo si carica on-demand invocando la skill fable-director:delega-efficiente.
printf 'FABLE-DIRECTOR KERNEL (delegation policy — full body: skill fable-director:delega-efficiente):\n'
cat "${CLAUDE_PLUGIN_ROOT}/kernel.md" 2>/dev/null || true

# Sentinella versione: avvisa se la cache in esecuzione è più vecchia della
# sorgente marketplace locale (la cache non si auto-aggiorna mai).
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/version-sentinel.py" 2>/dev/null || true

# Onboarding one-shot executor esterni: config assente + notice mai mostrato
# → istruzione al modello di proporlo UNA volta. Marker = mai più ripetuto.
XF_CFG="$HOME/.claude/fable-director/cross-family.json"
XF_MARK="$HOME/.claude/fable-director/xf-onboarding-shown"
if [ ! -f "$XF_CFG" ] && [ ! -f "$XF_MARK" ]; then
  mkdir -p "$HOME/.claude/fable-director" 2>/dev/null || true
  cat <<EOF

XF ONBOARDING (relay this to the user at the start, ONCE — this notice will not reappear): external free-tier executors are not configured. If the user has a Google account (→ free Gemini API key, limits reset daily) or a ChatGPT account (→ Codex CLI, usage included in the plan), connecting them pays off: fable-director routes non-quality-sensitive batches to those models at zero Claude tokens, with verification and telemetry. Paid API keys work in the same config entries. Guided setup: python3 "${CLAUDE_PLUGIN_ROOT}/scripts/external-exec.py" --doctor
EOF
  : > "$XF_MARK" 2>/dev/null || true
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
