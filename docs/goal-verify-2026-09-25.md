# `/goal` e `budget-open --verify` — decisione del 2026-09-25

Domanda (brief del 25/09, punto 3): il `--verify` del pre-budget può diventare anche il `/goal` della sessione, così che lo Stop hook del plugin e il goal nativo misurino la stessa condizione?

## Cosa fa `/goal` in Claude Code 2.1.282 (letto nel binario)

- `/goal <condizione>` registra a runtime uno Stop hook di tipo `prompt` (matcher vuoto): a ogni fine turno «a separate check decides whether the condition is met, and Claude keeps working until it is». È un giudizio di un modello, non un comando; ha un timeout (`goal_check_timeout`) e un tetto di iterazioni (`goal_check_capped`). Si chiude da solo quando la condizione è soddisfatta; `/goal clear` lo ferma prima.
- Esiste un tool `ProposeGoal` con cui il modello propone un goal; l'utente lo approva in un dialogo (impostazione: chiedi / chiedi sempre / disabilitato). Un `/goal` digitato non passa dal dialogo.
- Vincoli: solo workspace fidati; non parte se gli hook sono limitati (`disableAllHooks`, `allowManagedHooksOnly`).
- Nessuna API con cui uno script di hook imposta o legge il goal: lo imposta l'utente (o il modello via `ProposeGoal`, con consenso).

## Cosa fa già il plugin

- `--verify` **comando** (inizia con un runner noto: python3, pytest, bash, npm, make, …): lo Stop hook lo esegue dopo ogni scrittura, a zero token, scrive `verify_rc` nello stato e dal 1.49 blocca una volta la dichiarazione di chiusura se l'ultimo esito è fallito. L'esito finisce nella ricevuta di `budget-close`.
- `--verify` **checklist** (prosa): nessun hook la esegue; è il «done» dichiarato che il regista controlla da sé.

## Decisione

Non si unificano. Le due condizioni sono già la stessa quando `--verify` è un comando, e lì il goal nativo aggiungerebbe una chiamata a un modello a ogni Stop per ripetere ciò che l'hook misura gratis; non esiste un modo per farlo impostare da uno script, quindi l'unione costerebbe una `ProposeGoal` per budget più il dialogo di consenso, per zero informazione in più.

Dove il goal nativo è invece il pezzo mancante è la checklist in prosa: nessun hook la può eseguire, e un giudice per turno che «blocca finché non è soddisfatta» è esattamente quello che il contratto chiede al regista. Implementato il minimo: `budget-open` con un `--verify` in prosa stampa una riga

```
FD ◎ verify is a checklist, not a command: the Stop hook cannot run it. `/goal <checklist>` hands it to the native goal check (model-judged at every stop, blocks stopping until met, auto-clears).
```

e nulla per un `--verify` comando. Test: `tests/contract-verify-verify.py` V4a.

## Cosa cambierebbe la decisione

- Un'API o un hook event per impostare il goal da uno script (allora `budget-open --verify "cmd"` potrebbe registrarlo e `budget-close` cancellarlo, senza dialogo).
- La possibilità di dichiarare il goal come comando invece che come giudizio (a quel punto sarebbe lo stesso meccanismo del nostro Stop hook, e il nostro potrebbe ritirarsi).
- Il modello del `goal_check` e il suo costo per turno resi visibili: oggi non si vede nel transcript, quindi non si misura.
