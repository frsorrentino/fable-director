# Mods nel plugin: studio per la release completa (2026-10-01)

Le misure dei tre prototipi sono in `docs/mods-prototipo-2026-10-01.md`. Questo documento
risponde a una domanda: come si pubblicano i mod dentro fable-director senza rompere niente
a chi non li ha.

## Fatti verificati

1. **Un `hooks.json` misto funziona su tutte le versioni provate.** Il file ha `hooks`
   classici e `modules` insieme.
   - Su 2.1.287 girano entrambi.
   - Su 2.1.284 (prima dei Mods) gli hook classici girano normalmente e il modulo viene
     saltato con una riga nel log di debug: «hooks modules are not turned on for installed
     plugins in this process».

   Non serve una versione minima di Claude Code: chi è indietro ha il plugin di oggi.
2. **Un plugin installato da marketplace carica i suoi mod.** La prova è stata fatta con un
   marketplace di tipo directory dichiarato via `--settings`, sul tuo account: modulo
   caricato, tier `user`, `plugin.register: admitted`.
   - Per i plugin installati il caricamento dipende da un interruttore di rilascio lato
     server. Il binario 2.1.287 contiene la riga «the rollout switch served off».
   - Su un account con l'interruttore spento vale il punto 1: restano gli hook classici.
3. **Il mod può avvisare gli hook classici della sua presenza.** `$.env.set('FD_MODS', '1')`
   in `session.start` arriva agli hook UserPromptSubmit e PreToolUse e alla shell di Bash.
   - Non arriva al SessionStart classico, che parte prima del mod.
   - Gli script Python possono quindi sapere, chiamata per chiamata, che il mod è attivo.
4. **Un deny del mod in `tool.call` ferma la catena.** Il PreToolUse Python non parte
   (`answered without next()`). Se il mod lascia passare, il gate Python gira come oggi.

## Proposta per 1.53.0

Il principio: il mod anticipa o aggiunge, il Python resta la rete. Nessuna funzione esiste
solo nel mod.

| mod | cosa entra nel plugin | cosa resta al Python |
|---|---|---|
| **fd-usage** | Scrive `quota-<acct>.json` con la stessa fusione della 1.52.3: a parità di finestra vince il valore più alto, un reset successivo vince sempre, un bucket assente si tiene fino alla scadenza. Aggiorna anche `quota-history` e `usage-snapshot`. Così le quote si aggiornano anche in `claude -p`, sul telefono e in Remote Control. | La statusline continua a scrivere con la stessa regola: due scrittori idempotenti, non serve coordinarli. |
| **fd-gate** | `tool.call` nega senza budget in circa 0,3 s invece di circa 1,3 s. Sul deny lancia `fd-telemetry.py log gate_deny` con `$.process`, così l'evento non si perde. `agent.spawn` impone `sonnet` a `fd-executor`. | Il gate Python resta intero: avvisi, quota guard, window fit, registro delle deleghe. Quando il mod lascia passare, il Python rifà il controllo del budget, che costa poco. |
| **fd-effort-probe** | Non entra. Nessun guadagno misurato. | — |

**Test.** Il controllo di equivalenza sui 7 casi diventa un test della suite: confronta il
mod e il gate Python. In più, `claude plugin test` dei mod in `release.sh`, saltato con un
avviso se il binario `claude` non c'è.

**Documentazione.** README e privacy devono dire tre cose:
- su Claude Code ≥ 2.1.287 parte anche un modulo;
- cosa scrive, cioè gli stessi file di oggi;
- che senza il modulo il plugin funziona come prima.

## Aperti, da chiudere prima di pubblicare

- **Windows.** Va verificato su Windows che `$.env.get('HOME')` restituisca qualcosa, oppure si
  usa `USERPROFILE`. `fd-usage` cade già su `USERPROFILE`, `fd-gate` non ancora. Senza
  `HOME` il gate lascia passare, come il Python in caso di errore.
- **Validazione della directory Anthropic.** La candidatura è ancora aperta. Va verificato che
  `claude plugin validate` del plugin intero passi con `modules`, e che la checklist della
  directory non rifiuti i plugin che contengono codice dei Mods.
- **Account con l'interruttore spento.** Non si può provare dal tuo account. Il comportamento
  atteso è quello di 2.1.284, cioè modulo saltato e hook classici attivi, ma non è misurato
  su 2.1.287.
