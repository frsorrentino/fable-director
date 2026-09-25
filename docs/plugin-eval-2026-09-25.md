# Plugin eval: le skill scattano sui trigger dichiarati? — 2026-09-25

Domanda (brief del 25/09, punto 4; EVMBench: «skills often fail to trigger — test that they fire»). Strumento: `claude plugin eval` di Claude Code 2.1.282, suite in `fable-director/evals/`, template da `claude plugin eval init --bare`. Ogni caso è un prompt scritto come lo scriverebbe un utente, senza nominare la skill, più un grader `tool_used: Skill` con `input_match` sul nome della skill e un controllo sulla risposta. In un run a due bracci il grader `Skill` è un indicatore («plugin-fired»), non entra nel punteggio: il punteggio misura la risposta, l'indicatore misura il trigger.

## Casi

| Caso | Prompt (sintesi) | Skill attesa | Controllo sulla risposta |
|---|---|---|---|
| trigger-delega-batch | 40 schede prodotto da riscrivere con subagenti senza bruciare la quota: come organizzo e verifico? | delega-efficiente | giudice: esecutore economico + verifica non auto-dichiarata + canary; regex `budget` |
| trigger-video-storyboard | spot di 68 s e storyboard con voce: cosa riuso, cosa rifaccio, la voce sta nei tempi? | analisi-video | regex `video-sheet\|ffprobe\|contact sheet\|tts-timing` |
| trigger-model-check | è uscito Opus 5.5: CLAUDE.md, skill e agenti vanno ancora bene? controllo senza modificare | verifica-modello | regex `model-rules-check\|prompt-audit` |
| trigger-gate-denied | un hook ha bloccato il subagente con «pre-delegation gate: no open budget»: cosa faccio? | delega-efficiente | regex `budget-open` |
| no-trigger-typo | «recieve» → «receive» nel README, dimmi la riga | delega-efficiente NON deve scattare (`min: 0, max: 0, arm: both`) | regex `receive` |
| no-trigger-codec-question | H.264 vs H.265 per Instagram, tre righe | analisi-video NON deve scattare | regex `H\.?26[45]` |

`allowed_tools: [Read, Glob, Grep, Skill]`, `max_turns: 12`, `runs: 2`. Il sandbox dell'eval non carica settings, CLAUDE.md né altri plugin: scatta solo ciò che il plugin porta con sé (kernel dal SessionStart, skill, agenti).

## Risultati

Run completo, con e senza plugin (`claude plugin eval . --trust-plugin --no-publish -j 2`): 504 s, 3,74 $ di modello (bracci più giudice haiku).

| Caso | Con plugin | Senza plugin | Δ | Skill scattata (con plugin) |
|---|---|---|---|---|
| trigger-delega-batch | 1,0 | 1,0 | 0 | **0/2** |
| trigger-video-storyboard | 1,0 | 0 | +1 | 2/2 |
| trigger-model-check | 1,0 | 0 | +1 | 2/2 |
| trigger-gate-denied | 1,0 | 0 | +1 | 2/2 |
| no-trigger-typo | 1,0 | 0,75 | +0,25 | 0/2 (giusto) |
| no-trigger-codec-question | 1,0 | 1,0 | 0 | 0/2 (giusto) |
| **suite** | **1,0** | 0,46 | **+0,54** | |

Letture:
- Tre skill su tre scattano dove il prompt porta il loro dominio, e nessuna scatta dove non deve. Senza plugin le stesse domande finiscono senza il comando giusto (`model-rules-check`, `video-sheet`, `budget-open`) e, sul modello, la `verifica-modello` costa 0,68–0,70 $ a run contro 0,21–0,22 con il plugin: il baseline gira 12–13 turni a cercare.
- **Il caso batch è il rilievo.** La risposta era buona in tutti i run (il kernel iniettato al SessionStart porta i sei assi, il giudice ha promosso 2/2 con e 2/2 senza), ma `delega-efficiente` non è stata caricata: 0/2, confermato 0/4 in un secondo run a un braccio (0/6 in totale). Con l'`allowed_tools` senza Bash e Agent il modello pianifica in un turno solo dal kernel. È esattamente il fallimento silenzioso di EVMBench: il punteggio non lo vede, l'indicatore sì.
- Causa probabile: la `description` chiudeva con «The always-on kernel already carries the 6 routing axes; load this full body only when the axes fire, not merely because a session started» — letta come «non serve caricarla». Il prompt-audit del mattino l'aveva segnalata (enumerazione dei trigger, «da decidere dopo l'eval»).

## A/B sulla description

Riscritta come domanda dell'utente («Use when the user asks how to organize, split or delegate work - a batch of similar items, several files, subagents, workflows, a cheaper model, a quota to protect - and before launching any Agent/Task/Workflow call, …»), senza la frase sul kernel; stesso prompt, 4 run, un braccio, 91 s, 0,88 $:

| Description | Skill scattata | Risposta nomina il budget |
|---|---|---|
| precedente (enumerazione + «il kernel ha già gli assi») | 0/6 | 2/6 |
| nuova (la domanda dell'utente, cosa c'è nel corpo) | **4/4** | 4/4 (`apro un budget`; il regex iniziale cercava solo `budget-open|pre-budget`, allargato a `budget`) |

La risposta con la skill caricata costa 0,22 $ a run contro 0,13 senza: il corpo della skill (31k byte) entra nel contesto. È il prezzo che la vecchia description evitava, a spese del contratto, dei flag del pre-budget e della scala di verifica che il piano poi non aveva.

## Come rieseguire

```bash
cd fable-director-marketplace/fable-director
claude plugin eval . --trust-plugin --no-publish -j 2          # suite intera, due bracci
claude plugin eval . --trust-plugin --no-publish --ablation none --runs 4 --case trigger-delega-batch
```

`--no-publish` tiene il rapporto HTML in `evals/results/<timestamp>/` (ignorato da git). Ogni run è una sessione `claude` figlia sul modello predefinito dell'account: i nostri hook girano dentro e scrivono `session_summary` nella telemetria vera (13 sessioni in più oggi). Costo di riferimento: ~0,15 $ a run con il plugin, ~0,04–0,08 senza, più il giudice.

## Prossimi passi proposti

- Un caso per il fork (`claude --fork` / sessione forkata) e uno per `analisi-video` con un file audio-only: coprono i due rami della procedura non toccati.
- Rilanciare la suite a ogni release che tocchi una `description` o il kernel (`--ablation none` basta per il trigger, dimezza il costo).
- Verificare se le description di `pixelfarm` e `claude-master` hanno la stessa frase «già coperto altrove»: il trigger-eval di oggi dice che costa il caricamento.
