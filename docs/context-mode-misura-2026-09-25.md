# context-mode (mksglu) misurato sui nostri output di tool — 2026-09-25

Domanda (brief del 25/09, punto 6): context-mode taglia qualcosa che caveman e fable-director non coprono? Misura in sola lettura: clone in scratchpad, nessuna installazione nei settings, nessuna chiamata a modelli.

## Cos'è, letto nel codice (v1.0.169, 24.054 stelle, push del 24/09)

- **Licenza**: Elastic License 2.0 (`LICENSE`, `package.json`: `Elastic-2.0`). Source-available, non open source secondo l'OSI: fuori dal vincolo dell'autore «solo open source locale» per un'adozione, dentro per una lettura.
- **Non è un middleware che accorcia gli output**: è un server MCP (`ctx_batch_execute`, `ctx_execute`, `ctx_execute_file`, `ctx_search`, `ctx_fetch_and_index`) più hook. Il risparmio nasce quando il modello **scrive codice** che gira in sandbox e stampa solo il derivato («only what you console.log() enters your conversation»), oppure indicizza l'output in FTS5 e lo interroga. Il taglio è deciso dal modello a ogni chiamata, non dall'hook.
- **Hook** (`hooks/hooks.json`): PreToolUse su Bash/WebFetch/Read/Grep che **reindirizza**: `curl`/`wget` e `fetch(` in Bash vengono sostituiti (`action: modify`) con un `echo` che dice di usare `ctx_execute`/`ctx_fetch_and_index`; per gli altri comandi un «nudge» sopra una soglia di byte stimati; deny/ask sui pattern di sicurezza dell'utente. PostToolUse su quasi tutto: cattura eventi in un SQLite di sessione (memoria, non taglio). SessionStart: inietta un blocco `<context_window_protection>` di **4.323 caratteri** per sessione (`routing-block.mjs`, misurato con `createRoutingBlock`). PreCompact: snapshot. UserPromptSubmit e Stop: cattura.
- **Regola dichiarata** (`when_not_to_use`): Read resta giusto quando si deve fare Edit (servono i byte esatti); Bash resta giusto per osservare output brevi e per mutare stato. Quindi il taglio riguarda gli output che si vogliono **elaborare** (filtrare, contare, riassumere), non quelli che si devono leggere.
- Soglia automatica: output > 100 KB → puntatore FTS5 invece del contenuto (`BENCHMARK.md`, Parte 3). Il loro benchmark (21 scenari, 376 KB grezzi → 16,5 KB, «96 %») è su output di MCP server e tool di sviluppo scelti da loro.

## I nostri output, 30 giorni in questo cwd

Transcript di `~/.claude/projects/<questa cartella>/` (13 sessioni, 1.063 `tool_result`, 2,9 MB di testo entrato in contesto):

| Tool | Risultati | MB | Quota |
|---|---|---|---|
| Bash | 929 | 2,33 | 81,4 % |
| Read | 17 | 0,43 | 15,1 % |
| WebFetch | 15 | 0,04 | 1,3 % |
| Agent | 23 | 0,03 | 0,9 % |
| altri | 79 | 0,07 | 1,3 % |

| Fascia (byte) | Risultati | MB | Quota |
|---|---|---|---|
| 0–2k | 740 | 0,44 | 15,3 % |
| 2k–25k | 309 | 1,88 | 66,0 % |
| 25k–100k | 14 | 0,54 | 18,8 % |
| > 100k | 0 | 0 | 0 % |

Nessun output supera i 100 KB: il puntatore automatico di context-mode non sarebbe mai scattato. La fascia che pesa è 2–25k, quella che la skill `delega-efficiente` chiama «passa intera e gonfia in silenzio» (l'harness tronca sopra ~25k e salva su file).

Classificazione per intento della fascia 2k–100k (324 risultati, 2,43 MB), con le regole di context-mode stesse:

| Intento | Quota byte | Risultati | context-mode potrebbe |
|---|---|---|---|
| ricerca/elenco (grep, find, ls, git log/diff, wc) | 31,6 % | 130 | sì: conteggi e `path:riga` da codice in sandbox |
| lettura contenuto via cat/sed/head | 24,5 % | 87 | no per la loro regola (serve il testo esatto per Edit e per l'audit); sì dove si leggeva per riassumere |
| lettura contenuto via Read | 17,8 % | 11 | no (stessa regola); qui sono riletture dei file `tool-results/*.txt` salvati dall'harness |
| esecuzione test (suite `tests/*-verify.py`) | 15,5 % | 65 | sì: pass/fail e le righe FAIL, che già facciamo con `tail` |
| script nostri (`python3 - <<EOF`) | 8,5 % | 18 | no: sono già output derivati, lo stesso meccanismo |
| altro | 2,1 % | 13 | — |

Tetto teorico del taglio sui nostri dati: circa la metà dei byte della fascia (ricerca/elenco + test), se ogni chiamata fosse riscritta come codice che stampa il derivato. Prezzo: 4,3k caratteri di blocco iniettato a ogni sessione, una dipendenza Node in ogni hook (`node` a ogni PreToolUse su Bash/Read/Grep), e la riscrittura di ogni comando di ricerca in un programma.

## Cosa copre già chi

- **caveman** comprime l'**output del modello** (le risposte), non i risultati dei tool: nessuna sovrapposizione con context-mode.
- **fable-director** copre gli stessi byte con regole, non con un tool: «tool output is next turn's input: grep → head → partial read → summary script» (skill, riga «Harness mechanics»), `tools/skeleton.py` (firme a ~5 % del file) per gli esecutori, `mcp-meter.py` che misura il peso dei risultati MCP, il route verdict che porta i batch fuori dal thread principale. Non ha: un sandbox in cui il modello esegue codice e riporta solo il derivato (lo fa già `python3 - <<EOF`, che è il 8,5 % dei byte qui), un indice FTS5 interrogabile degli output grandi, una memoria di sessione in SQLite catturata dai PostToolUse.
- **Claude Code** già salva su file gli output oltre ~25k e li rilegge a fette: è la stessa idea del puntatore, con soglia quattro volte più bassa.

## Verdetto

Sui nostri output context-mode non taglia niente che non sia già raggiungibile con la disciplina esistente; il suo vantaggio è rendere quella disciplina un tool con nome invece di una regola, cosa che l'audit dei transcript dice avere effetto (regola vicina al gesto > pagina letta prima). Le due cose che non abbiamo e che meritano una riga nel backlog, senza installare nulla: (1) misurare nel `report` la fascia 2–25k per tool e per intento, così il numero sopra diventa un allarme e non una stima una tantum; (2) un indice locale degli output grandi salvati dall'harness (`tool-results/*.txt`), interrogabile con `grep`, invece della rilettura intera che qui vale il 17,8 % dei byte. Licenza e dipendenza Node chiudono la strada dell'adozione diretta.
