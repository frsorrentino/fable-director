# Istruzioni ripetute come vincoli, ed euristica Markdown → HTML (03/10/2026)

Origine: approvato da Franz il 03/10 alle 18:32 (riferito da claude-master), dal giro
su X. Modello: Blume, «l'hai spiegato quattro volte questa settimana». Valgono il
requisito sui plugin usati da soli (convergenza del 03/10, ore 18:02) e la regola
sul piano approvato: approvato questo piano, sono approvati anche push e release.

## Divisione

- **fable-director trova** le istruzioni ripetute nei transcript: script, zero token.
- **claude-master le registra** nella tabella `constraints` di `~/.config/claude-master/tasks.db`
  e le allega ai compiti nuovi (`task add` lo fa già: `constraints_for`).
- **Senza claude-master** le proposte finiscono nel playbook
  (`~/.claude/delega-playbook.md`, come `[candidate]`) o in memoria.
- **Nessuna registrazione automatica:** il plugin propone; il vincolo entra solo
  con un sì di Franz. Un vincolo sbagliato allegato a ogni compito costa più di
  uno mancante.

## 1. Rilevatore: `scripts/repeat-finder.py`

- **Fonte:** turni umani veri dei transcript (`~/.claude/projects/*/*.jsonl` e
  `~/.claude-pixel/projects/*/*.jsonl`), ultimi 7 giorni (`--days N`). Si riusa
  `human_turn_text()` di `fd-telemetry.py`: esclude meta, sidechain, tool_result,
  testo che inizia con `<` (iniezioni degli hook, messaggi fra sessioni).
- **Esclusioni in più:**
  - blocchi incollati (`<pasted_content>`);
  - messaggi inoltrati da master («Da master», «Dall'utente via telefono»): il
    testo vero dentro conta una volta sola, non per ogni inoltro;
  - frasi sotto 6 parole e conferme («ok», «procedi», «sì»).
- **Unità:** la frase (split su `.`, `;`, a capo), normalizzata: minuscole,
  senza punteggiatura, numeri e percorsi sostituiti da segnaposto.
- **Raggruppamento:** shingle di 3 parole, Jaccard ≥ 0,5 fra frasi; un gruppo è
  una ripetizione quando compare in **almeno 3 sessioni diverse** (non 3 volte
  nella stessa: lì è insistenza, non abitudine).
- **Filtro istruzione:** il gruppo deve contenere un marcatore di regola
  (`sempre`, `mai`, `non`, `usa`, `lancia`, `prima di`, `ricorda`, `always`,
  `never`, `use`, `run`, `don't`…) o un comando fra backtick.
- **Esclusione dei già noti:** un gruppo il cui testo è già in `CLAUDE.md`
  (globale o del progetto), nella memoria o nei vincoli di claude-master non è
  proposto. È il caso più comune: Franz ripete ciò che il modello ignora, e lì il
  problema non è un vincolo mancante. Questi casi vanno in un elenco separato,
  «già scritto, ignorato N volte», che è l'informazione più utile.
- **Uscita:** JSON in `~/.claude/fable-director/repeats.json` e testo su stdout:
  per gruppo, frase rappresentativa, sessioni, progetti, date, ambito proposto
  (`--project` se tutte le sessioni sono di un progetto, altrimenti `--topic`).
- **Dati:** tutto resta sul disco. Nessuna chiamata a modelli, nessuna rotta
  esterna. Le frasi possono contenere nomi di clienti: il file è locale come i
  transcript.

## 2. Dove compare

- `fd-telemetry.py repeats [--days N]` esegue il rilevatore e stampa le proposte.
- `/fable-director:status` aggiunge una riga quando ci sono proposte nuove
  («3 istruzioni ripetute questa settimana: `fd-telemetry.py repeats`»).
- **Non nel kernel di SessionStart:** è a 3 caratteri dal tetto di 7.900.
- Su un sì, per ogni proposta:
  - con claude-master presente (`claude-master` sul PATH e `tasks.db` esistente):
    `claude-master task constraint add --project P "testo"` (CLI, mai import);
  - senza: una riga `[candidate]` nel playbook dell'utente.
- Telemetria: evento `repeat_proposed` e `repeat_accepted` (conteggi, mai il testo).

## 3. Euristica nel playbook

In `playbook-template.md` e, come `[candidate]`, nel playbook dell'utente:

> Rapporti e pagine leggibili: il modello scrive Markdown, uno script lo rende
> in HTML. Non far scrivere HTML al modello: costa circa 7 volte i token
> (fonte: github.com/QingYunA/answer-me-with-html, non misurato da noi).

Il «circa 1/7» resta attribuito alla fonte finché non lo misuriamo.

## Test

- `tests/repeat-finder-verify.py` con transcript sintetici:
  - R1 la stessa istruzione in 3 sessioni → un gruppo;
  - R2 la stessa in 1 sessione ripetuta 5 volte → nessun gruppo;
  - R3 conferme brevi, iniezioni `<…>`, messaggi inoltrati → esclusi;
  - R4 parafrasi (Jaccard ≥ 0,5) → stesso gruppo; frasi diverse → gruppi diversi;
  - R5 testo già in un CLAUDE.md → nell'elenco «già scritto, ignorato»;
  - R6 claude-master assente → nessuna chiamata, proposta verso il playbook;
  - R7 claude-master presente (finto sul PATH) → comando `constraint add` corretto;
  - R8 nessuna rete, nessun processo modello avviato.
- Prova reale prima del rilascio: `repeats --days 7` sui transcript veri, letto da
  me: le proposte devono essere istruzioni vere, non rumore. Se più di metà è
  rumore, si alzano le soglie prima di pubblicare.
- Verde = `release.sh` con tutte le suite.

## Rilascio

1.55.0, con CHANGELOG e badge. README: una riga nella sezione «Soft dependencies»
sotto claude-master, «facoltativa».
