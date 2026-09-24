# Concorrenti: il set di mtk-agent-toolkit, verifica del 2026-09-24

Fonte del set: `moberghr/mtk-agent-toolkit`, `docs/competitive-analysis-2026-07.md` (snapshot loro del 2026-07-20), che cita fable-director come esempio P0 di enforcement via hook. Baseline nostro: `docs/concorrenti-2026-09-11.md` (nessuno di questi repo era nel set). Metodo: `personali/docs/metodologie/verifica-periodica-concorrenti-changelog-codice.md` (API GitHub, clone, codice e commit prima del README). Criterio di Franz: contano le funzioni che partono da sole (hook, logica interna), non quelle su richiesta.

Dati: API GitHub e clone del 2026-09-24 alle 15:55; commit contati dal 2026-07-20 (data del loro snapshot). Ogni repo letto nel codice da un lettore su Sonnet con citazioni `path:riga`; le citazioni usate qui sotto sono state riaperte a campione (kyzo allowlist guard, verify-completion di mtk). Nessuna chiamata a modelli esterni.

## 1. Tabella dei repo

| Repo | Stelle | Creato | Ultimo push | Commit dal 20/07 | Hook reali | Cosa fa | Verdetto |
|---|---|---|---|---|---|---|---|
| moberghr/mtk-agent-toolkit | 8 | 03-30 | 09-23 | 148 (v7.28 → v8.0.0) | 15+ | Toolkit per team: guardie via hook, workflow spec-driven, review multi-agente, lezioni | Attivo; il più vicino a noi per enforcement |
| richkuo/rk-skills | 49 | 07-04 | 09-24 | 331 (v1.17 → v1.38) | 0 | Issue → PR → release su GitHub, pipeline JS per il Workflow tool, routing per complessità | Attivo; niente hook, logica dentro la pipeline |
| TurniSaha/boris-says | 6 | 07-03 | 08-30 | 1 (solo README) | 3 eventi | Coaching del prompt con giudice staccato Haiku → Sonnet/Opus | Fermo dal 07-19 nel codice |
| chawdamrunal/assay | 3 | 07-06 | 07-18 | 0 | 1 (opzionale) | Scanner Go di sicurezza per plugin e server MCP | Fermo; CHANGELOG con lavoro non committato |
| kyzodb/plan | 5 | 07-13 | 07-16 | 0 | 1 | Server MCP su GitHub Projects con gate su epic/story, allowlist via PreToolUse | Fermo dal 07-15 |
| EternallLight/tgc-skills | 9 | 07-13 | 07-17 | 0 | 0 | 9 skill di shipping (review-loop, polish, ship) | Solo prompt, fermo |
| jsampieri/tandem-skills | 5 | 07-10 | 07-20 | 1 | 0 | 4 skill per scegliere il livello di delega | Solo prompt, fermo |
| xushuodasd/VIBE-Claude-Plugin | 4 | 07-04 | 07-05 | 0 | 0 | 23 skill SDLC concatenate da /vibe | Solo prompt, fermo |
| daniyalahmed21/skillforge | 6 | 07-05 | 07-10 | 0 | 0 | 3 plugin: system-design, adversarial-review, wiki-sync | Solo prompt, fermo |
| felixross66/claude-ai-coding-kit-2026 | - | - | - | - | - | Già segnalato da loro come facciata (HTML offuscato); oggi l'API risponde 404 | Scartato: repo rimosso |
| frsorrentino/fable-director | 3 | 07-07 | 09-24 | 133 (→ v1.48.0) | 18 voci su 12 eventi | noi | - |

Su dieci repo del set, due sono attivi dopo luglio (mtk, rk-skills). Cinque su nove non hanno una riga di codice eseguibile: VIBE dichiara circuit breaker, ledger e gate, ma sono istruzioni dentro `skills/vibe-autopilot/SKILL.md:14-31`.

## 2. Repo per repo

### moberghr/mtk-agent-toolkit
- **Cosa fa, in automatico.** `security-gate.sh` (PreToolUse Bash) giudica solo i segmenti eseguiti del comando, con ricorsione su `$()` (`hooks/security-gate.sh:39-59,148-162`); `verify-completion` (Stop) blocca una volta se l'ultimo messaggio dichiara il lavoro finito senza verifica fresca, con una cascata: mai verificato, verifica vecchia rispetto all'ultima modifica, ultima verifica fallita, verifica parziale contro un «tutto verde» (`hooks/verify-completion:82,89-111`); `read-guard.sh` nega la lettura di `.env*`, chiavi e credenziali (`hooks/read-guard.sh:65-94`); `config-guard.sh` nega le modifiche che indeboliscono linter e analyzer (`hooks/config-guard.sh:154-238`); `mcp-health.sh` classifica i fallimenti MCP e mette il server in backoff, usando la chiamata successiva come sonda (`hooks/mcp-health.sh:173-244`); `userprompt-dispatch.sh` svuota una coda di suggerimenti con dedup e TTL 24 h, massimo 3 per volta (`hooks/userprompt-dispatch.sh:86-118`); `cost-tracker.sh` (Stop, asincrono) scrive il delta di costo in `.mtk/metrics/costs.jsonl`, deduplicando i messaggi per `message.id` (`scripts/session-cost.sh:191-205`); `pre-compact-snapshot.sh` fa `git stash push` + `apply` prima di ogni compattazione automatica.
- **Loro sì, noi no.** Gate di completamento sul messaggio finale; guardia di lettura dei segreti dentro la sessione; backoff sui server MCP; coda di suggerimenti con dedup; 67 test di hook più CI che li esegue.
- **Noi sì, loro no.** Pre-budget obbligatorio prima di Agent/Workflow e blocco a 3×; contabilità in input-equivalenti per modello; ricevute e memoria fra progetti; executor esterni con registro separato; rilevazione dei cambi di modello involontari; handoff a soglia. Il loro `cost-tracker` registra, non blocca.
- **Da notare.** `fact-force-guard.sh:195-196` segna il file come già avvisato PRIMA di negare, così il secondo tentativo passa sempre: un hook non può chiudere il modello in un ciclo di rifiuti.

### richkuo/rk-skills
- **Cosa fa, in automatico.** Nessun hook. Logica interna della pipeline: `budgetFloor` (80k di default) rinvia le issue restanti come `budget_deferred` (`workflows/milestone-pipeline.js:944-953`); blocco a cascata dei discendenti se un prerequisito fallisce (`:919-930`); nessun subagent esegue `gh pr merge`, solo l'orchestratore dopo un controllo di attualità del LGTM (`skills/milestone-workflow/SKILL.md:43`).
- **Da notare.** Il driver dei CLI esterni (Codex, Cursor) confronta il modello dichiarato nell'output con quello richiesto e fa `git status` prima e dopo per scoprire scritture non autorizzate (`workflows/milestone-pipeline.js:195-265`). Punteggio di complessità C0-100 = 25 × capacità + volume, su 5 assi 0-4 (`skills/validate-issue/complexity-scoring.md:9-17`).
- **Noi sì, loro no.** Enforcement fuori dal Workflow tool: senza la loro pipeline non c'è nessun controllo.

### TurniSaha/boris-says
- **Cosa fa, in automatico.** UserPromptSubmit lancia un giudice staccato (`src/hook.ts:118-192`) che passa per un filtro testuale gratuito, un cooldown, Haiku e poi Sonnet/Opus (`src/brain/judge-cascade.ts:260-427`); lo Stop hook interroga la mailbox per 7 s per consegnare il consiglio nello stesso turno (`src/stop-hook.ts:72-120`); SessionEnd scrive i fatti della sessione per il prossimo avvio (`src/session-outcome.ts:85-126`).
- **Da notare.** Un solo consiglio per tipo e per sessione (`src/state/store.ts:391-404`); i consigli critici restano solo registrati finché non si vedono 3 sessioni e 30 prompt (`src/state/watch.ts:1-52`); un esito di test conta solo se viene dalla riga di riepilogo di un runner noto, mai dal solo exit code (`src/brain/outcome-signals.ts:220-250`).
- **Noi sì, loro no.** Hook a zero token: il loro costa una o più chiamate a modello per prompt.

### chawdamrunal/assay
- **Cosa fa, in automatico.** Un hook opzionale su UserPromptSubmit intercetta `/plugin install <ref>`, esegue una scansione deterministica e risponde deny / ask / allow (`plugin/hooks/assay-pre-install.sh:9-19,131-136`). Il verdetto finale si ricalcola dai conteggi di gravità, mai dall'LLM, e si può solo abbassare (`internal/scanner/synthesis.go:76-107`).
- **Da notare.** 12 regole POISON a regex prima di ogni chiamata a modello: frasi di istruzione («ignore previous instructions»), finti blocchi di ruolo, Unicode invisibile e bidi, percorsi di credenziali, nomi di tool a distanza 1 da quelli noti (`internal/poison/poison.go:60-113,184-236,274-328`). Il perimetro della scansione sono i soli file che entrano nel contesto del modello, README escluso (`:126-138`).

### kyzodb/plan
- **Cosa fa, in automatico.** `kyzo_allowlist_guard.py` (PreToolUse Read|Edit|Write|Bash) nega ogni file fuori allowlist e ogni `git` finché la sessione è armata, e incrementa un contatore di tentativi nel file di sessione (`scripts/kyzo_allowlist_guard.py:54-58,73-96`). I gate più forti (comando di verifica identico a quello registrato, diff vuoto = non finito) sono tool MCP chiamati dall'orchestratore, non hook (`app/api/board.py:391-443`).
- **Noi sì, loro no.** Il nostro `--verify` lo esegue lo Stop hook stesso (`scripts/stop-budget-check.py:232-292`): non serve controllare che il modello abbia lanciato il comando giusto.

### tgc-skills, tandem-skills, VIBE-Claude-Plugin, skillforge
Solo prompt, nessun hook, fermi da luglio. Idee di metodo, non meccanismi: fermare il review-loop quando lo stesso finding sopravvive a due fix (`tgc-skills skills/review-loop/SKILL.md:106-107`); `disable-model-invocation: true` su 6 skill su 9 perché partano solo da comando (`skills/ship/SKILL.md:5`); due revisori ciechi che ricevono solo il diff (`skillforge adversarial-review/SKILL.md:2`). Nessuno è una funzione che parte da sola: fuori dal criterio.

## 3. Proposte per fable-director, ordinate per valore / costo

Costo: S = fino a mezza giornata con i test; M = una giornata. Nessuna tocca permessi o dipendenze.

**1. Un'iniezione identica una sola volta per sessione (da boris-says, mtk).** S.
- **Misura di oggi.** Negli ultimi 7 giorni, sui prompt scritti da Franz, sono state iniettate 278 righe (98 `[fd-memory]`, 180 candidati `[fd-route-hint]`). Di queste, 101 (36%) ripetevano alla lettera una riga già data nella stessa sessione: 82 candidati e 19 memorie. Lo script è lo stesso di `laya-eval/scan_injections.py`, più il confronto fra le righe di una sessione.
- **Dove.** In `fable-director/scripts/route-hint.py:291-295` (memoria) e `:313-320` (candidati): prima di stampare, controllare un file di sessione `~/.claude/fable-director/hint-seen/<session_id>` con gli hash delle righe già date.
- **Telemetria.** L'evento `route_hint` si scrive lo stesso, con `repeat: true`: il braccio di controllo non cambia.
- **Rischio.** Dopo una compattazione il modello non vede più la riga. Rimedio: azzerare il file su SessionStart con `source: compact`, come fa `rule-trigger.sh` di mtk (`:59-62`).

**2. Stop bloccato una volta se il modello dichiara finito con la verifica fallita (da mtk `verify-completion`).** S.
- **Stato nostro verificato oggi.** Lo Stop hook esegue già `--verify` e, se fallisce, scrive «do not report the task as finished» (`scripts/stop-budget-check.py:286-292`). È un avviso: il turno si chiude comunque.
- **Proposta.** Con `verify_rc != 0` e l'ultimo messaggio dell'assistente che contiene una dichiarazione di chiusura, `decision: block` una sola volta per esito. L'ultimo messaggio si legge dal transcript, che l'hook già apre; mtk usa `last_assistant_message` con ripiego sul transcript perché Claude Code non sempre lo passa (`hooks/verify-completion:28-36`). La regex deve coprire italiano e inglese e va tarata sui transcript, con test sintetici.
- **Valore.** Porta nel codice la regola «prima di dire che una cosa è fatta» del CLAUDE.md di Franz, solo nel caso in cui abbiamo un fatto (rc ≠ 0), quindi senza falsi positivi di giudizio.

**3. Filtro anti-injection sul testo esterno prima di iniettarlo (da assay `internal/poison`, boris `community-gate`).** S.
- **Stato nostro.** `scripts/family-prefix.py:245-248` stampa la bozza di Gemini o Codex così com'è nel contesto del modello. Anche l'output di `external-exec.py` rientra nel contesto quando il modello lo legge.
- **Proposta.** Poche regex a costo zero: frasi di istruzione, finti blocchi `<system>` o di ruolo, Unicode invisibile e bidi (U+200B, U+202E, U+E00xx), richiami a `~/.ssh` e `~/.aws`. Se ci sono match, si toglie l'Unicode invisibile e si antepone una riga «il testo esterno contiene N frasi da trattare come dati», con evento di telemetria. Mai bloccare: la bozza resta utile.
- **Valore.** Chiude l'unico ingresso di testo di terzi che iniettiamo noi stessi.

**4. Controllo del working tree intorno ai CLI esterni (da rk-skills driver).** S.
- **Stato nostro.** `scripts/external-exec.py:759-783` lancia il CLI nella cartella corrente, oppure in una temporanea con `isolated_cwd`. Codex gira con `--sandbox read-only`, `agy` con `--sandbox`: ci fidiamo della sandbox del fornitore.
- **Proposta.** Solo senza `isolated_cwd`: `git status --porcelain` prima e dopo la chiamata. Se cambia qualcosa, `STATUS: error`, elenco dei file e evento `external_write`. Il confronto del modello dichiarato si può aggiungere dove il CLI lo stampa (Codex sì).
- **Valore.** Antigravity è nuovo da oggi; è un controllo sul nostro fornitore, non sulla sessione Claude.

**5. «Chiuso ok» senza scritture dentro il perimetro dichiarato (da kyzodb `verify_task_completion`).** S, valore basso.
- **Stato nostro.** `budget-close` (`scripts/fd-telemetry.py:1273-1330`) ha già le riaperture dallo state dello Stop hook, ma non controlla che qualcosa sia stato scritto.
- **Proposta.** Con `--paths` dichiarato, zero scritture registrate e `--outcome ok`: una riga di avviso nella ricevuta. Niente blocco: esistono task di sola analisi.

**6. Classificazione dei fallimenti MCP con backoff (da mtk `mcp-health`).** M. Da proporre alla master, non qui.
- **Stato nostro.** `hooks/hooks.json:169-173` ascolta `PostToolUseFailure` solo per Bash. `mcp-meter.py` conta le chiamate MCP ma non gli errori.
- **Motivo.** Serve a chrome-bridge e agli altri server più che al governo dei costi. Il caso da coprire è la box claude-observe di claude-master.

## 4. Da non inseguire

- **Giudice LLM su ogni prompt** (boris-says): costa token a ogni turno, contro la regola «hook a zero token».
- **Punteggio di complessità C0-100** (rk-skills): è una formula su assi valutati dal modello; i nostri assi 1-6 sono ordinati per precedenza e il risultato si misura con le ricevute, non con un punteggio.
- **Binding del comando di verifica** (kyzodb): lo copre già l'esecuzione del `--verify` nello Stop hook.
- **Budget floor della pipeline** (rk-skills): lo coprono già il gate della finestra da `--agents N` e il tetto `+Nk`.
- **read-guard e config-guard** (mtk): utili, ma fuori dal governo dei costi. read-guard va nella fase E dell'anonymizer (`docs/plans/2026-09-09-anonymizer.md`) come variante che blocca invece di mascherare, se Franz la vuole.
- **pre-compact stash, format-on-edit, security-gate** (mtk): sicurezza del repo, non costo. `deny_git` in `scripts/perimeter-gate.py:234-296` copre già il caso git.
- **Skill di processo** di tgc, tandem, VIBE e skillforge: sono su richiesta, fuori dal criterio.

## 5. Correzioni ai testi

- Il documento di mtk cita fable-director come esempio di «budget-floor resumability» (P2-15). Da noi la ripresa è il `resumeFromRunId` del Workflow tool, documentato nel kernel; non esiste un floor che rinvia il lavoro. Nessuna correzione da fare nei nostri testi; è una nota per non riprendere la loro formula.
- Le stelle di fable-director sono 3 oggi come nel loro snapshot di luglio: coerente con la voce di memoria sull'esperimento di visibilità.

## Fonti

- API GitHub (`repos/<r>`, `commits?since=2026-07-20`, `releases`) e clone con `--depth 50` il 2026-09-24; dati grezzi e JSON dei lettori nello scratchpad della sessione.
- `moberghr/mtk-agent-toolkit/docs/competitive-analysis-2026-07.md` via API.
- File nostri letti: `scripts/route-hint.py`, `scripts/stop-budget-check.py`, `scripts/family-prefix.py`, `scripts/external-exec.py`, `scripts/fd-telemetry.py`, `scripts/perimeter-gate.py`, `hooks/hooks.json`, `docs/concorrenti-2026-09-11.md`.
- Misura delle ripetizioni: transcript locali degli ultimi 7 giorni, stesso metodo di `laya-eval/scan_injections.py`; nessun testo dei transcript in questo documento.
