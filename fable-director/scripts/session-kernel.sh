#!/usr/bin/env bash
# SessionStart hook: inietta il kernel della policy (~480 token) nel contesto.
# Il corpo completo si carica on-demand invocando la skill fable-director:delega-efficiente.
printf 'FABLE-DIRECTOR KERNEL (policy di delega — corpo completo: skill fable-director:delega-efficiente):\n'
cat "${CLAUDE_PLUGIN_ROOT}/kernel.md" 2>/dev/null || true

PB="$HOME/.claude/delega-playbook.md"
if [ -f "$PB" ]; then
  printf '\nPlaybook euristiche: %s — consultalo prima di orchestrare batch/workflow/multi-agente.\n' "$PB"
else
  printf '\nPlaybook non inizializzato: copia %s in %s (solo se non esiste, mai sovrascrivere).\n' "${CLAUDE_PLUGIN_ROOT}/playbook-template.md" "$PB"
fi
