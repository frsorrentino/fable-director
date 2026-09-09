# Brief: handoff tra sessioni (planned expiry operativo)

Data: 9 settembre 2026. Origine: sessione fable-director al 93% di contesto dopo
quattro release in un giorno. La statusline diceva "context 93% full — finish
the task and start a new session": dice cosa fare, non come. Il kernel già
afferma che a confine verificato un distillato su file + sessione fresca fa
risparmiare ~52% (81 sessioni lunghe, 72,5% del costo cade dopo un confine
riconoscibile), ma non esiste né il comando che scrive il distillato né la
proposta al momento giusto. Il resume freddo di quella sessione avrebbe
ricaricato 296k token (~$5,93 a listino): è il numero da mettere davanti
all'utente.

Richiesta di Franz, testuale: "ideale sarebbe proposta in prompt tra un task e
l'altro quando opportuno, del tipo: salva handoff e riavvia senza contesto?"

## Cosa esiste già (verificato il 9/09, non rifare)

- `scripts/stop-budget-check.py`: hook Stop, gira a fine di ogni turno main;
  vede budget aperto/chiuso e il verify. `scripts/route-hint.py`: hook
  UserPromptSubmit, inietta additionalContext (righe `[fd-route-hint]`,
  `[fd-memory]`). `scripts/session-kernel.sh`: SessionStart, buffer con cap
  7900 caratteri; margine residuo misurato ~350 caratteri (una riga da 85 ha
  fatto cadere il blocco onboarding, K1 rosso, 2026-09-08).
- `scripts/statusline-plain.py:117`: la riga rossa `context N% full — finish
  the task and start a new session`. Percentuale contesto scritta dalla
  statusline in `~/.claude/fable-director/sessions/<sid>.json` (`ctx_tokens`,
  `ctx_size`, `model`, `ts`): il gate la legge già.
- `commands/*.md`: frontmatter `description` + `allowed-tools`, corpo =
  istruzioni al modello (vedi `status.md`).
- `kernel.md` riga 15 "Planned expiry": paragrafo da aggiornare, non da
  allungare (6023 byte, cap SessionStart).
- Telemetria: `fd-telemetry.py` `log_event(event, payload)`; `report` per
  sezioni; `budget-close` è il segnale di confine più affidabile.

## Deliverable

### 1. Proposta al momento giusto (hook, deterministica)

Nel Stop hook: se in QUESTO turno c'è stato un confine verificato — un
`budget-close` con outcome ok, oppure il comando `--verify` passato, oppure un
commit atterrato (PostToolUse Bash già osservato da `fail-streak.py`: riusare,
non duplicare) — e il contesto della sessione è ≥ soglia (default 60%,
`~/.claude/fable-director/handoff.json` `{"threshold_pct": 60}`), scrivere un
marker `~/.claude/fable-director/handoff-due/<sid>.json` con `{pct, turns,
boundary: "budget-close|verify|commit", ts}`. Mai con un budget aperto. Una
volta per fascia (60, 80): il marker porta la fascia, non si riscrive nella
stessa.

In UserPromptSubmit (`route-hint.py` o script accanto, stessa forma): se il
marker esiste per la sessione ed è di meno di 30 minuti, iniettare UNA riga di
additionalContext e consumare il marker:

    [fd-handoff] context 71%, last task closed at a verified boundary. Before
    starting this prompt, ask the user in ONE line: "Save a handoff and restart
    in a fresh session (~half the cost of continuing), or go on here?" — then
    do what they say. If the prompt is clearly a continuation of the closed
    task, skip the question.

È il modello a porla, in prosa, tra un task e l'altro: è la richiesta. Loggare
`handoff_proposed` `{pct, turns, boundary}`.

### 2. Comando `/fable-director:handoff [--here]`

`commands/handoff.md`: il modello scrive il distillato in
`~/.claude/fable-director/handoffs/<cwd-slug>/<YYYY-MM-DD>.md` (fuori dal
repo; `--here` → `docs/handoff-<data>.md` nel progetto, per chi lo vuole
versionato). Sezioni fisse, ~2k token, niente narrazione:

1. Task e esito (una riga ciascuno, con versione/commit se c'è)
2. Decisioni prese e perché (solo quelle non deducibili dal codice)
3. Fatti verificati (numeri, comandi eseguiti, output decisivi)
4. Aperto: cosa manca, proposte non eseguite, domande all'utente
5. Percorsi toccati (file, cartelle, budget/ricevute)
6. Da invocare dopo: skill, comandi, brief da eseguire
7. Data e cosa invalida questo handoff

Regola nel comando: si scrive SOLO a confine verificato; se c'è un budget
aperto il comando lo dice e si ferma (a metà task il ragionamento in contesto
è portante). Alla fine il modello stampa il percorso e la frase: "Chiudi
questa sessione e apri `claude` nella stessa cartella: l'handoff viene letto
all'avvio." Poi `log_event("handoff_written", {pct, turns, bytes, path})`. Lo
slug del cwd è quello di `cwd_slug()` in fd-telemetry.py (canonico + hash).

### 3. Ripresa a SessionStart

`session-kernel.sh`: se esiste un handoff per il cwd più recente di 14 giorni,
UNA riga corta prima dei blocchi lunghi:

    Handoff 2026-09-09: ~/.claude/fable-director/handoffs/<slug>/2026-09-09.md — read it before starting.

Budget: ≤ 110 caratteri (usare `~` al posto di `$HOME`). Verificare con
`tests/kernel-source-verify.py` e `tests/hook-caps-verify.py` che K1 resti
verde con la riga presente e hindsight pieno. Un handoff più nuovo sostituisce
il precedente nella riga; i vecchi restano su disco.

### 4. Statusline

`statusline-plain.py:117` diventa
`context N% full — /fable-director:handoff, then a new session` (stessa
lunghezza circa; il test `statusline-plain-verify.py` va aggiornato). Sotto
l'80% non cambia nulla: la proposta arriva dall'hook, non dalla barra.

### 5. Kernel e skill

`kernel.md` "Planned expiry": stessa lunghezza o meno, nomina il comando:
"…propose `/fable-director:handoff` (distillate on disk) and a fresh session;
the hooks propose it for you at the first verified boundary above 60% context".
Una riga in `delega-efficiente` § Session boundaries, niente di più.

### 6. Telemetria e report

`report`: sezione "Handoff": proposte / scritte / sessioni proseguite oltre
l'80% senza handoff, e per le sessioni con handoff il costo della sessione
successiva nello stesso cwd rispetto alla media delle riprese fredde (il 52%
va confermato sui dati, non ripetuto).

### 7. Test, changelog, release

- `tests/handoff-verify.py`: H1 marker scritto solo con confine + soglia; H2
  nessun marker con budget aperto; H3 UserPromptSubmit inietta una volta e
  consuma; H4 marker più vecchio di 30 min ignorato; H5 riga SessionStart
  presente e ≤ 110 caratteri, K1 ancora verde; H6 comando rifiuta con budget
  aperto (testare l'istruzione nel .md: la stringa c'è); H7 statusline nuovo
  testo; H8 report con eventi finti.
- CHANGELOG 1.43.0 "handoff: the session ends on your terms" con i numeri di
  questo brief (296k token del resume freddo, 52% stimato da confermare).
- README "What you get": un bullet in ottica utente ("When a long session has
  done its job, the agent offers to write a two-page handoff and start fresh;
  the next session reads it at startup instead of re-paying the whole
  context").
- `bash release.sh 1.43.0`.

## Fuori scope

Niente riassunto automatico del transcript (è `/compact`, e lascia la
sessione lunga). Niente handoff a metà task. Niente scrittura senza che
l'utente abbia detto sì alla domanda o invocato il comando.
