---
description: Rimuove (--remove, per sempre) o rimette la statusline fable-director in settings.json — l'installazione avviene da sola alla prima sessione, questo comando serve solo per toglierla o ripristinarla
allowed-tools: Bash
---

Esegui l'installer idempotente della statusline fable-director e riporta l'esito **verbatim**.

Comando da lanciare (passa gli argomenti dell'utente, es. `--remove`):

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/statusline-install.sh" $ARGUMENTS
```

Regole:
- Riporta l'output dello script così com'è (successo, aggiornamento, o warning statusLine di terzi).
- Se lo script esce con codice ≠0 per una statusLine di terzi già presente, NON tentare di
  sovrascriverla: spiega all'utente che deve rimuoverla a mano e rilanciare.
- Ricorda all'utente che la statusLine compare solo dopo il **riavvio di Claude Code**.
- Non fare altro: niente edit manuali di settings.json, lo script fa tutto.
