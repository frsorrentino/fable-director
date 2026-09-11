# Concorrenti — verifica del 2026-09-11 (dal 2026-09-01)

Baseline: `docs/analisi-2026-09-01.md` §A. Metodo: `kb/strumenti/rubare-da-plugin-concorrente.md` (sorgente e changelog, non README; ogni adozione passa dagli assi del kernel; rifiuti con motivo). Dati: API GitHub l'11/09 alle 07:30 (stelle, push, release e commit dal 01/09), release note e changelog scaricati per intero, tre repo nuovi letti nel codice. Nessuna chiamata a modello esterno.

## 1. Cosa è successo ai concorrenti noti (01→11 settembre)

| Repo | Stelle | Push | Commit / release dal 01/09 | Novità che ci riguardano |
|---|---|---|---|---|
| **oh-my-claudecode** (concorrente più vicino) | 39.094 (+159) | 11/09 | 100+ commit, v5.2.0, v5.3.0 | Gate di approvazione remota + checkpoint/rollback del workspace (grafo); HUD che mostra l'aggiornamento di Claude Code disponibile; SessionEnd con «foreground ceilings» per runner; hook fail-open quando il consumer chiude stdout; stato per-tool e spawn dei worker limitati. 36 fix su Windows/hook. Nessun gate pre-spesa, nessuna verifica adversarial: invariato |
| ruflo (ex claude-flow) | 72.018 (+1.9k) | 11/09 | 40 commit, 8 release (3.38.21→3.41.2) | Federazione swarm cross-host (Nostr, canali cifrati), «claims» (un proprietario per risorsa), coordinatore Seraphina advisory. Fix: router che scalava di tier tenendo il `modelId` del tier economico; **PII regex rese stateless e bounded contro ReDoS**; benchmark inventato rimosso. Fuori dal nostro dominio, tranne il punto ReDoS (vedi §3) |
| claude-mem | 93.654 (+770) | 11/09 | 100+ commit, 9 patch 13.24.x | SessionStart con matcher `resume`; reinject della memoria dopo `/compact` e `/clear` (Codex); quota overage ignorata se inattiva. Conferma la strada della fase E dell'anonymizer: la mappa deve sopravvivere a compact e clear |
| headroom | 71.419 | 10/09 | 50 commit, Unreleased | **Routing di modello cost-aware opt-in nel proxy** (`HEADROOM_MODEL_ROUTES`); cache aligner che hasha il prefisso congelato reale e segnala `prefix_changed`; savings tracker corretto (registrava solo i risparmi da compressione). Architettura a proxy: incompatibile con l'asse 2, come già scritto in INTERNALS |
| caveman | 104.781 (+~5k) | 10/09 | 55 commit, v2.5.0, v2.6.0 | Registro ASD-STE100 nel prompt; **rinforzo per-turno più forte** (#951) — esattamente il pattern 1d che il prompt-audit del 09/09 gli imputa; proxy fail-closed su porta occupata; README riscritto sui risparmi misurati con grafici statici generati dalla tabella di benchmark committata |
| claude-hud | 27.911 | 05/09 | Unreleased | `display.showDailyCost`: spesa cumulata di oggi da `cost.total_cost_usd` dello stdin, ledger giornaliero che si azzera a mezzanotte; orologio della prompt-cache riferito all'inizio della richiesta, non alla risposta; Agent async trattato come background finché non arriva la task-notification |
| ccusage | 18.490 | 11/09 | 85 commit | Solo snapshot prezzi (LiteLLM, models.dev). Nulla |
| claude-code-router | 37.180 | 10/09 | v3.1.0 | Profili, gateway, Codex Responses. Nulla per noi |
| BMAD-METHOD | 52.875 | 10/09 | v6.12.0 | «Build decide quanta cerimonia serve DOPO aver indagato»; triage delle review con verdetto ed evidenza per finding. Analoghi al nostro fast path e al verificatore per finding |
| claude-plugins-official | 36.125 | 10/09 | 100+ commit | security-guidance 2.0.8 (repo risolto da comando e path modificati, SubagentStop); decine di plugin di vendor. Nessuna policy di delega |
| superpowers | 284.832 (+4.4k) | 11/09 | 0 commit | Fermo |
| SuperClaude, agent-os, claude-squad, codex-plugin-cc, claude-model-router-hook, cc-budget, tokenbudget, agent-review-panel | — | ≤ 02/09 | 0 | Fermi |

## 2. Repo nuovi

Ricerca GitHub per query (costo, budget, hook, delega, anonimizzazione, statusline, compaction, handoff) filtrata per creazione ≥ 15/07 o push ≥ 01/09. Nessun concorrente diretto nuovo: la categoria «pre-budget + enforcement + telemetria in input-equivalenti» resta senza analoghi. Dieci statusline nuove (tutte sotto 25 stelle, nessuna con quota o budget). Tre repo letti nel codice:

| Repo | Cosa è | Giudizio |
|---|---|---|
| **makinggainz/claude-code-token-optimization** + **claude-code-measure-efficiency** (0★, 30/07) | Stessa tesi nostra («ridurre i token senza spostare il ragionamento su modelli deboli»): 7 agenti pinnati per tier, blocco di policy in CLAUDE.md, `rtk` per l'output, igiene di sessione; e **cinque script di misura** sui transcript locali: mix di tier, decomposizione del costo in 4 componenti, proxy di rework, crescita per sessione, costo per turno **standardizzato per profondità**. Risultati onesti (una correzione pubblicata: bucketing per mtime e file-per-sessione gonfiavano il risultato) | Non un concorrente: un pari di misura. Due cose da prendere (§3). Il loro `per_turn.py` prezza la cache read a 0,1× per tutti i modelli: sulla 5.1 è 0,025×, il nostro `model-economics.json` è avanti |
| leogallego/claude-skills-tokens (0★) | Due skill di audit + 11 report di «ricerca» | Claim senza fonte verificabile nel testo: «regola dei 270 s» (contraddice il TTL di 1 h che Claude Code usa e che Anthropic ha confermato l'08/09), «autosave VS Code invalida la cache» (nessuna evidenza nei report). Non adottare |
| fernandoleyra/handoff-command (0★) | Comando `/handoff` con documento a sezioni fisse (~2000 parole), posizione per progetto in `.claude/handoff.json` | Sovrapponibile al nostro `/fable-director:handoff` (sette sezioni, ~2k token, trigger a soglia di contesto). Nessuna idea nuova, tranne il mirror facoltativo verso un vault Obsidian |

## 3. Adozioni proposte (ognuna passa dagli assi)

1. **Proxy di rework nel `report`** (da `friction.py`): tasso di errore dei tool (`tool_result.is_error`), tasso di correzione (regex sui turni umani: «non funziona», «rivedi», «hai saltato», «revert»), churn degli Edit sullo stesso file. Oggi la nostra telemetria ha `rework_worst` (riaperture) e `verification`, ma nessun proxy deterministico di attrito dai transcript; questi tre sono a zero token e rafforzano la guardia anti-Goodhart («qualità come vincolo») con numeri, non con la fiducia nel modello. Mezza giornata: un blocco «friction» in `session-cost-report.py` e in `fd-telemetry.py report`, con la loro avvertenza («misura attrito, non correttezza»).
2. **Costo per turno standardizzato per profondità** (da `per_turn.py`): bande fisse di profondità, costo per turno dentro la banda, ripesatura del periodo «dopo» sul mix di profondità del «prima». La nostra tesi «costo per turno ~quadratico nel contesto» (INTERNALS) viene da un'analisi una tantum; con la standardizzazione il confronto fra versioni del plugin (prima/dopo 1.44) smette di dipendere da quanto erano lunghe le sessioni. Stesso script, stessa mezza giornata; i subagent vanno attribuiti al turno che li ha lanciati (già lo facciamo con la lineage `toolUseResult`).
3. **Spesa di oggi in `/fable-director:status`** (da claude-hud `showDailyCost`): un numero, dai `session_summary` del giorno in eq e in USD di listino, con la riga di avvertenza «piano ad abbonamento: confronto, non bolletta». Un'ora. Nella statusline no: la riga plain mostra solo eccezioni.
4. **Probe anti-ReDoS nella suite dell'anonymizer** (da ruflo #3177): oggi misurato a mano su sette input patologici (peggior caso 224 ms su 10 KB di email finta, nessuna esplosione); un check A11 lo rende permanente. Dieci minuti.
5. **Nota di metodo nei benchmark** (da measure-efficiency §«Choosing a denominator»): dichiarare il denominatore (token di output vs turni) e riportare entrambi quando l'intervento tocca la verbosità; bucketing per timestamp del primo record, mai per mtime; i file dei subagent raggruppati per session id. Da verificare che `benchmarks/` e il `report` lo dicano; una riga dove manca.

## 4. Rifiuti, con motivo

- **Rinforzo per-turno del registro** (caveman 2.6): reinserimento di istruzioni a ogni prompt, pattern 1d dell'audit; su Fable 5.1 sottrae formattazione voluta e costa ~40 token a turno.
- **Routing cost-aware via proxy** (headroom, claude-code-router): sposta il traffico fuori dal modello di punta per classe di richiesta; l'asse 2 tiene il codice di produzione sul modello di punta e delega per contratto, non per intercettazione.
- **Federazione e claims** (ruflo): coordinamento fra host; il nostro caso è una macchina con più sessioni, che claude-master già copre.
- **Gate di approvazione remota e checkpoint del workspace** (oh-my-claudecode 5.3): dominio di claude-master (approvazioni da Telegram), non del plugin di costo; da segnalare alla master, non da costruire qui.
- **Regola dei 270 secondi e autosave** (leogallego): claim senza misura, in contrasto con il TTL di 1 h misurato e confermato.
- **Comando handoff a posizione configurabile**: il nostro ha già il trigger a soglia e le sezioni fisse; il mirror Obsidian è fuori scopo.

## 5. Cosa dice questo sulla nostra posizione

Nessuno dei 21 repo ha aggiunto in dieci giorni un pre-budget dichiarato, un enforcement 2×/3× o una contabilità in input-equivalenti pesata per modello: la tesi del plugin regge. La pressione competitiva è altrove: affidabilità degli hook (oh-my-claudecode e claude-mem hanno speso la settimana su timeout, fail-open, Windows) e **misura onesta** (makinggainz pubblica correzioni; caveman genera i grafici dal benchmark). Le due adozioni 1 e 2 sono esattamente lì: rendono verificabile con numeri di attrito e di profondità la nostra affermazione «qualità mai scambiata con i token».
