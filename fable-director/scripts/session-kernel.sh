#!/usr/bin/env bash
# SessionStart hook: inietta il kernel della policy (~480 token) nel contesto.
# Il corpo completo si carica on-demand invocando la skill fable-director:delega-efficiente.
printf 'FABLE-DIRECTOR KERNEL (policy di delega — corpo completo: skill fable-director:delega-efficiente):\n'
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

XF ONBOARDING (riferiscilo all'utente in apertura, UNA sola volta — questo notice non riapparirà): gli executor esterni free-tier non sono configurati. Se l'utente ha un account Google (→ chiave API Gemini gratuita, limiti che si resettano ogni giorno) o un account ChatGPT (→ Codex CLI, uso incluso nel piano), conviene collegarli: fable-director orchestra i batch non quality-sensitive su quei modelli a zero token Claude, con verifica e telemetria. In alternativa funzionano chiavi API a pagamento nelle stesse voci di config. Setup guidato: python3 "${CLAUDE_PLUGIN_ROOT}/scripts/external-exec.py" --doctor
EOF
  : > "$XF_MARK" 2>/dev/null || true
fi

PB="$HOME/.claude/delega-playbook.md"
if [ -f "$PB" ]; then
  printf '\nPlaybook euristiche: %s — consultalo prima di orchestrare batch/workflow/multi-agente.\n' "$PB"
else
  printf '\nPlaybook non inizializzato: copia %s in %s (solo se non esiste, mai sovrascrivere).\n' "${CLAUDE_PLUGIN_ROOT}/playbook-template.md" "$PB"
fi
