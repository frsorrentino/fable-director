# Concorrenti: due aggiunte dal brief X del 25/09 — StackReplay e Agent Eval Foundry

Integra `docs/concorrenti-2026-09-24.md` (set di mtk-agent-toolkit). Metodo invariato: API GitHub, clone in scratchpad, codice prima del README (`personali/docs/metodologie/verifica-periodica-concorrenti-changelog-codice.md`). Dati del 2026-09-25 alle 16:40; commit contati dal 2026-07-20. Nessuna chiamata a modelli esterni.

## 1. Tabella

| Repo | Stelle | Creato | Ultimo push | Commit dal 20/07 | Licenza | Hook reali | Cosa fa | Verdetto |
|---|---|---|---|---|---|---|---|---|
| btsouth/stackreplay | 4 | 09-21 | 09-25 | 81 | AGPL-3.0 | 0 | Legge la cronologia locale di Claude Code / Codex / altri, la normalizza in eventi (token per categoria, modello, timestamp) e la rigioca contro piani in abbonamento e listini API | Attivo (RC1 su stackreplay.com dal 24/09); strumento di confronto, non di governo |
| mstevens843/agent-eval-foundry | 0 | 08-29 | 09-22 | 90 | MIT | 0 | Sistema per produrre task di benchmark per agenti: contratti, verificatori, mutanti, screening di modelli, audit avversariali | Attivo; metodologia, non un plugin |

Nessuno dei due ha una funzione che parte da sola dentro Claude Code (criterio dell'autore): StackReplay è un CLI e una web app locali, la Foundry è un repository di task e report.

## 2. StackReplay (Tyler, post X del 25/09)

- **Cosa fa, letto nel codice.** L'adapter Claude Code (`packages/adapters/src/adapters/claude-code.ts:78-82`) legge dai JSONL `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` e `output_tokens_details.thinking_tokens`, con la nota che «cache reads exceed `input_tokens` in the overwhelming majority of records» (righe 42-44). `scan` (`apps/cli/src/commands/scan.ts:11-12`) è dichiarato read-only: stampa eventi, sessioni, progetti e i cinque secchi di token (uncached, cache read, cache write, output, reasoning; righe 75-80). `replay` prezza lo stesso carico «at a provider's published API list prices» (`replay.ts:19`) e stampa `Plan cost`, `Overage cost`, `Target cost`, `Difference` (righe 307-316). I listini sono YAML con fonte, data di verifica e stato (`packages/catalog/data/pricing/claude-fable-5-1-pricing.yaml`: input 10, output 50, cache read 0,25, cache write 12,50 per milione, `checkedAt: 2026-09-23`, thinking «billed as output»). I piani (`data/plans/anthropic-claude-max-20x.yaml`) portano solo limiti qualitativi citati dal supporto Anthropic: «no absolute numeric allowance published».
- **Privacy**: modello dati senza campo per prompt e risposte; parsing in Web Worker nel browser, identità dei progetti hashata con sale locale (README, «Privacy architecture»); un test di browser fallisce se una richiesta porta contenuto del carico.
- **Loro sì, noi no.** Il rigioco di un carico reale contro le meccaniche dichiarate di un altro piano (finestre 5h, tetti settimanali, regole per modello) e contro il listino API, con catalogo versionato e fonti datate; adapter per sei strumenti.
- **Noi sì, loro no.** Tutto ciò che agisce durante la sessione: pre-budget, gate, 2×/3×, perimetro, ricevute. La loro contabilità è la nostra `session_summary` con un catalogo di prezzi in più: i cinque secchi coincidono con i nostri (`model-economics.json`, eq per modello).
- **Da prendere.** Il formato dei listini (YAML con `sources[].checkedAt`, `verificationStatus`) è il modo giusto di tenere `model-economics.json` onesto: oggi i nostri rapporti (0,025× cache read su Fable 5.1, 0,05× su Opus 5.5) non hanno né fonte né data accanto. Una riga per modello con URL e data basta.
- **Da non inseguire.** Il confronto fra piani: la scelta dell'abbonamento non è un problema del plugin, e i piani non pubblicano numeri.

## 3. Agent Eval Foundry (mattinfra, post X: «sei programmi passano i test, i loro verificatori approvano lo stesso errore»)

- **Cosa dice il repo.** `FINDING.md` (02/09, «Failure rate cannot tell you whether your benchmark is hard»): cinque risultati di punta dell'autore ritirati perché il verificatore nascosto codificava una regola assente dalla specifica; «a genuinely hard task and an underspecified one produce exactly the same evidence: frontier models failing in correlated ways». Regola che ne deriva (riga 57): «a counted failure is not difficulty evidence until somebody has said why it failed, from a closed vocabulary, and **nobody may label their own trials**». Sui task «checker-required», dove l'agente deve consegnare anche il proprio verificatore: «Most finalist failures concern the agent's required checker, even when its service implementation passes. A checker that accepts an invalid execution or rejects a valid one fails that deliverable» (`README.md:124`). È il caso del post: implementazioni che passano i test e verificatori scritti dallo stesso soggetto che approvano lo stesso errore, perché condividono il punto cieco.
- **Perché conferma `fd-verifier`.** La nostra scelta è la stessa regola: il verificatore riceve solo artifact e rubrica, mai il ragionamento del maker (`agents/fd-verifier.md`), perché «inline self-critique is structurally self-preferential» (`delega-efficiente`, rung 3). La Foundry aggiunge due cose che non abbiamo: un vocabolario chiuso delle cause di fallimento (capability / underspecification / harness / …) etichettato da qualcuno che non ha prodotto il tentativo, e i «mutanti» (varianti sbagliate note) per provare che un verificatore rifiuta davvero — «my verifier catches all N of my mutants feels like coverage evidence and is not» (riga 231) se i mutanti li scrive chi ha scritto il verificatore.
- **Da prendere.** (1) Nel `report`, la classe della `escalation` (oggi `unclassified` scritta dall'hook, la classe la aggiunge il modello) dovrebbe venire da un vocabolario chiuso e da chi non ha eseguito il tentativo: è la stessa regola. (2) Un mutante per rubrica quando si scrive un `--verify` comando: un caso che deve fallire, così il verify che passa sempre si vede. Entrambe righe di backlog, non lavoro di oggi.
- **Da non inseguire.** Il sistema di produzione di task: è un benchmark per laboratori, non uno strumento di sessione. Zero stelle e un solo autore.

## 4. Fonti

- github.com/btsouth/stackreplay — clone del 2026-09-25 (commit «Merge pull request #7», 09:18 EDT); `docs/IMPLEMENTATION_STATUS.md` (RC1 del 24/09: 871 test unitari, 296 Playwright).
- github.com/mstevens843/agent-eval-foundry — clone del 2026-09-25 (commit «Fix baseline witness matching», 21/09); `FINDING.md`, `README.md`.
- Brief X: `personali/docs/trend-x-claude-code-2026-09-25.md`.
