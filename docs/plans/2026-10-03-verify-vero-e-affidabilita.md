# Controllo vero obbligatorio e affidabilità per tipo di compito (03/10/2026)

Origine: «Loops and Graphs», approvato da Franz il 03/10 alle 17:37 (riferito da
claude-master). Sintesi in `~/Desktop/workspaces/.claude/piani/2026-10-01-claude-master-mod-registro-compiti.md`,
sezioni «Correzione del 03/10» e B2. Il campo «controllo» dei nodi di
`claude-master plan` diventa il `--verify` della sessione che esegue il nodo.

## Fatti misurati prima di scrivere il codice

- 161 budget aperti negli ultimi 30 giorni: 124 con `--verify`, 37 senza.
- Dei 124: circa metà è prosa («review report with graded findings», «due
  risposte esterne ricevute»), che nessun hook può eseguire.
- Il riconoscimento dei comandi è troppo stretto: `VERIFY_RUNNERS` accetta solo
  `python3`, `bash`, `npm`… e quindi tratta come prosa, e non esegue mai,
  comandi veri usati ogni giorno: `test -s …`, `grep -q …`, `cd … && npm run check`,
  `git diff --quiet …`, `/tmp/…/scratchpad/verify87.sh`, `vendor/bin/phpunit`.
- Controlli finti già visti: `grep -c 'STATUS: ok' …` (stampa un conteggio,
  esce 0 con qualunque riga trovata), `npm test 2>&1 | tail -3` (l'esito è
  quello di `tail`, che non fallisce mai).
- Telemetria per il percorso: 249 `task_close` con `type`, `outcome`
  (ok/flagged/abandoned), stima e consuntivo, `reopens`, `verify_rc`; eventi
  `fail_streak` ed `escalation` per sessione. `delegation_outcome` ha lo stato
  noto solo in 37 casi su 6.519 (il resto è `unknown`): troppo poco per decidere
  per agente. Si mostra, non si usa per decidere.

## A. Controllo obbligatorio e vero

Nuovo modulo `scripts/verify-lint.py`, senza dipendenze, importato da
`fd-telemetry.py`, dal gate e dallo Stop hook. `classify(verify, cwd)` restituisce
`(kind, reason)`, con `kind` fra `missing`, `prose`, `fake`, `command`.

- **Comando:** il primo comando (dopo `!`, assegnazioni `X=y` e `cd dir &&`)
  è un eseguibile noto (runner di prima più `test`, `[`, `git`, `gh`, `grep`,
  `rg`, `jq`, `diff`, `cmp`, `ls`, `find`, `pnpm`, `yarn`, `uv`, `tsc`, `ruff`,
  `mypy`, `shellcheck`, `true`, `echo`, `exit`…), oppure un percorso a uno script
  (`/…`, `./…`, `~/…`, o relativo con `/` che esiste ed è eseguibile, o finisce
  in `.sh`/`.py`/`.js`/`.php`).
- **Finto**, cioè non può fallire, con un motivo che dice come correggerlo:
  - l'esito finale viene da un comando che esce sempre 0: `true`, `:`, `echo`,
    `printf`, `exit 0`, `wc`, e come ultimo stadio di una pipe anche `tail`,
    `head`, `cat`, `sort`, `uniq`, `tee`, `tr`, `cut`, `find`;
  - il fallimento viene ingoiato: `… || true`, `… || :`, `… || exit 0`,
    `… || echo …`, `…; true`, `…; exit 0`;
  - `grep -c` senza confronto (stampa un numero, non confronta col numero atteso);
  - comandi informativi: `git status`, `git log`, `git show`, `git diff` senza
    `--quiet`/`--exit-code`/`--check`;
  - `--exit-zero`;
  - `python -c`/`node -e` che contengono solo `print`, `pass`, `exit(0)`;
  - `bash -c`/`sh -c` con dentro uno dei casi sopra.
  - Con `set -o pipefail` la regola dell'ultimo stadio non si applica.
- **Dove agisce:**
  - `budget-open`: un `--verify` finto è rifiutato su qualunque rotta. Su una rotta
    che delega (`agent`, `workflow`, `bg-session`) anche un verify assente o in prosa
    è rifiutato.
  - Gate PreToolUse su `Agent`/`Task`/`Workflow`: il budget aperto deve avere un
    verify di tipo `command`; altrimenti **deny** con il motivo e il modo di
    correggerlo (`budget-amend --verify "…"`). Prima era un avviso una tantum.
    Il deny è registrato come `gate_deny` (`verify_missing`, `verify_prose`,
    `verify_fake`).
  - `budget-amend --verify "…"`: nuovo, per correggere il controllo senza
    chiudere il budget; passa dallo stesso lint, resta negli emendamenti.
  - Stop hook: `verify_command()` usa il nuovo riconoscimento, quindi ora esegue
    davvero `test`, `grep -q`, `git diff --quiet`, gli script per percorso.
- **Fuori perimetro, detto apertamente:** nessuna deroga. Anche le ricerche
  delegate (Explore, claude-code-guide) richiedono un controllo eseguibile,
  per esempio `test -s scratchpad/esito.md`. È attrito voluto (B2: «sembra a
  posto» non è un controllo); se si rivela eccessivo, la deroga si decide dopo,
  sui numeri dei `gate_deny`.

## B. Affidabilità per tipo di compito, dal percorso

Funzione `type_reliability(task_type)` in `fd-telemetry.py`, sugli ultimi 20
`task_close` dello stesso tipo negli ultimi 90 giorni.

- **Fallito:** `outcome` `flagged` o `abandoned`, oppure `verify_rc` diverso da 0
  alla chiusura.
- **Percorso faticoso** (fra i non falliti): nella finestra del compito, nella sua
  sessione, c'è almeno un `fail_streak` o un'`escalation`; oppure il consuntivo
  di output supera di 2× la stima; oppure `reopens ≥ 3`.
- **Fasce:**
  - `new`: meno di 3 precedenti; comportamento di oggi.
  - `fragile`: falliti ≥ 1/3, oppure falliti + faticosi ≥ 1/2.
  - `reliable`: almeno 5 precedenti, zero falliti, faticosi ≤ 1/5.
  - `standard`: il resto.
- **Effetti:**
  - `budget-open` salva la fascia nel budget (`reliability`) e la stampa in una
    riga con i numeri.
  - `fragile`: `budget-close --outcome ok` è rifiutato finché nella sessione, dopo
    l'apertura, non risulta un `fd-verifier` concluso (`delegation_outcome`).
    In alternativa `--no-verifier "motivo"`, che finisce nella ricevuta e nel
    `task_close`. Lo Stop hook riesegue il verify dopo ogni scrittura, senza
    l'intervallo minimo di 300 s. Il gate ricorda la regola a ogni delega che
    non è un `fd-verifier`.
  - `reliable`: verify rieseguito al massimo ogni 900 s invece di 300; il gate
    non ripete l'avviso sul contratto della delega. Il controllo resta
    obbligatorio.
  - Per agente: nella riga stampata compare il tasso `concerns`/`blocked` del tipo
    di agente quando ci sono almeno 5 esiti noti. Solo informazione.

## Test

- `tests/verify-lint-verify.py`: tabella di casi veri e finti, presi anche dai
  verify reali di settembre.
- `tests/reliability-verify.py`: DB di telemetria sintetico; le tre fasce;
  `budget-close` rifiutato senza `fd-verifier` e accettato dopo, o con `--no-verifier`;
  gate che nega senza verify, con prosa, con un verify finto; `budget-amend --verify`.
- Le suite esistenti che aprono budget senza `--verify` e poi chiamano il gate
  vengono aggiornate.
- Verde = `release.sh` passa tutte le suite.

## Rilascio

1.54.0 (minor: cambia il comportamento del gate). CHANGELOG, badge README,
`bash release.sh 1.54.0`. Coperto dall'approvazione del piano (regola del 03/10).
