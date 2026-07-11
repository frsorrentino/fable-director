---
description: Stato fable-director come testo in conversazione (quote, budget live, deleghe, cross-family) — per client senza statusline (smartphone/remote)
allowed-tools: Bash
---

Esegui e riporta l'output COSì COM'È (è già formattato, freschezza inclusa — non riassumere, non abbellire, non aggiungere commenti):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fd-status.py"
```

Unica aggiunta ammessa: se una riga contiene un allarme (⚠, FLAGGED), una frase di contesto su cosa fare.
