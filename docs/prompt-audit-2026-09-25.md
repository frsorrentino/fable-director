# Prompt-audit — 2026-09-25

Eseguito con `/claude-api prompt-audit` (skill integrata, Claude Code 2.1.282: la guida ha righe nuove rispetto al 09/09 — cluster di divieti 1e, coreografia dell'output 1f, soppressori di aggiornamenti e regole anti-formattazione in 1d, separazione trigger/comportamento nel Gruppo 3, checklist Opus 5.5). Le correzioni ad alta e media confidenza sono **applicate** nel working tree (compito 1 del brief del 25/09); il diff è `git diff` sul repo marketplace, un hunk per rilievo.

## Assunzioni

- **Modello bersaglio**: Claude Fable 5.1 (`~/.claude/settings.json`: `claude-fable-5-1[1m]`, effort high). Claude Opus 5.5 come modello in arrivo: le sue regole (thinking sempre acceso, niente «non pensare», effort unico controllo) valgono per gli agenti pinnati che potrebbero finirci.
- **Scope**: la superficie prompt del plugin fable-director — `kernel.md`, le tre skill, i due agenti, i sei comandi, `playbook-template.md`, il testo iniettato dagli hook (`session-kernel.sh`, `route-hint.py`) — più `~/.claude/CLAUDE.md`. Il plugin non ha un `CLAUDE.md` proprio: il kernel ne fa le veci.
- **Provenienza**: `git log c59e0e0..HEAD` (dalla 1.43.1, dove furono applicati i 5 hunk del 09/09): sette commit toccano la superficie; tutte le righe citate sono scritte per Fable 5.1, nessun fossile di generazione precedente. I rilievi sono di forma (fraseggio relativo a una versione precedente della skill, narrazioni di incidente, registro), non di modello.

## Inventario

| Superficie | Byte | Quando entra in contesto |
|---|---|---|
| `kernel.md` (SessionStart) | 6.1k | ogni sessione, riemesso dopo compattazione |
| `skills/delega-efficiente/SKILL.md` | 31.0k | a ogni delega/orchestrazione |
| `skills/analisi-video/SKILL.md` | 5.3k | su video/audio |
| `skills/verifica-modello/SKILL.md` | 1.7k | a un cambio di modello |
| `agents/fd-executor.md`, `fd-verifier.md` | 4.0k + 2.4k | prompt di ogni subagente |
| `commands/*.md` (6) | 9.4k | su invocazione |
| `playbook-template.md` → `~/.claude/delega-playbook.md` | 4.8k | prima di orchestrare |
| hook: `[fd-route-hint]`, `[fd-memory]`, hindsight, handoff, XF onboarding | ≤ 8k cap | a prompt / a sessione |
| `~/.claude/CLAUDE.md` | 2.9k | ogni sessione |

Superfici pulite: `kernel.md`, `fd-verifier.md`, `verifica-modello` (riscritta per il compito, non per l'audit), `commands/{handoff,help,observe,status}`, `playbook-template.md` (voci datate `[seed 2026-07]`: è il formato del registro, la data è un campo), gli hook (dati e domande, non istruzioni), `~/.claude/CLAUDE.md` (contiene già i frammenti della guida Fable 5.1: «Quando fermarti», «lead with the outcome», niente pulizia intorno a una correzione).

Scansione dei segnali greppabili della guida: densità di maiuscole 11/141 righe nella skill grande, tutte con la ragione accanto; nessun `think step by step`, `scratchpad`, `<thinking>`, cadenza «ogni N tool call», «hold findings», regola anti-formattazione. I rilievi vengono dalle righe 1d (fraseggio relativo alla migrazione), Gruppo 2 (narrazioni storiche, specifiche non trasferibili) e 1a/1b (registro, prosa che governa la profondità del ragionamento).

## Sommario

| Gruppo | Rilievi | Applicati |
|---|---|---|
| 1d fraseggio relativo a una versione precedente | 3 (FD-1, FD-3, FD-5) | 3 |
| Gruppo 2 narrazione storica / specifica non trasferibile | 2 (FD-4, FD-6) | 2 |
| 1b prosa che governa il thinking | 1 (FD-2) | 1 |
| 1a registro | 1 (FD-7) | 1 |
| flag (bassa confidenza o decisione dell'autore) | 6 | 0 |

I tre di maggior impatto: **FD-1** la sezione Telemetria descriveva un diff rispetto alla skill precedente («the three below are no longer asked of you», «stopped being a rule and became a QUESTION») — il modello non ha mai visto quella versione, legge alternative fantasma; **FD-2** l'esecutore a effort low riceveva «trascrivere, non ragionare», una regola «non pensare» che su Sonnet 5 con thinking adattivo e su Opus 5.5 non si può seguire (la checklist Opus 5.5 la mette fra le cose da cancellare); **FD-4** una skill di un plugin pubblico su GitHub puntava a un percorso privato di un cliente come layout di riferimento.

## Rilievi applicati

| id | Posizione | Evidenza | Pattern | Perché obsoleto | Conf. | Azione |
|---|---|---|---|---|---|---|
| FD-1 | `skills/delega-efficiente/SKILL.md:124-125` | «the reason the three below are no longer asked of you», «it stopped being a rule and became a QUESTION», «AUTOMATIC, no model action (2026-09-14)» | 1d fraseggio relativo alla migrazione | Il testo è un diff contro una versione della skill che il modello non ha mai visto; le regole vanno scritte come se fossero le sole mai esistite. La misura (161 sessioni, 0-9%) resta come ragione in una frase | Alta | rewrite: regole al presente — cosa scrive un hook, cosa logga il modello (`retry`, `reversal`), `script_promotion` è la domanda di `budget-close` |
| FD-2 | `agents/fd-executor.md:14-15` | «il tuo lavoro è trascrivere, non ragionare» | 1b prosa che governa la profondità del thinking | L'effort è già pinnato `low` nel frontmatter: è quello il controllo. Una regola «non ragionare» su thinking sempre acceso non si può seguire e la checklist Opus 5.5 dice di cancellarla | Media (Alta su Opus 5.5) | rewrite: «la spec è completa e si esegue così com'è, senza ampliarla» |
| FD-3 | `commands/statusline.md:2` | «dalla 1.50.1 si installa da sola alla prima sessione» | 1d fraseggio relativo + pin di versione nel testo di trigger | La description viaggia in ogni richiesta; «dalla 1.50.1» è storia, non regola | Media | rewrite: stato corrente («l'installazione avviene da sola alla prima sessione, il comando serve solo per toglierla o ripristinarla») |
| FD-4 | `skills/analisi-video/SKILL.md:26` | «Reference layout: `~/Desktop/workspaces/<agenzia>/clienti/<cliente>/docs/analisi-video-….md` §3 and §6» | Gruppo 2 specifica volatile non trasferibile | Percorso privato in un plugin pubblico: non esiste su nessun'altra macchina e nomina un cliente dell'agenzia. Il layout si descrive in una riga | Alta | rewrite: colonne delle due tabelle (blocco/slot, chiede, esiste, manca, lettere; voce, base del conteggio, ore) e la riga di conteggio |
| FD-5 | `skills/delega-efficiente/SKILL.md:33` | «since CC 2.1.198 the built-in Explore inherits the SESSION model up to opus» | 1d fraseggio relativo («since») + claim su versione senza verifica (flag del 09/09) | Verificato oggi nel binario 2.1.282: l'agente Explore ha `model:"inherit"`. La regola vale al presente | Media | rewrite: «runs on the session model (`model: inherit`, verified in Claude Code 2.1.282)» |
| FD-6 | `skills/delega-efficiente/SKILL.md:45` | «(measured: A/B ponytail 2026-08, stem-matching case)» | Gruppo 2 narrazione storica (id di incidente) | Nessuno può decodificare «ponytail» dalla skill; la regola («gli edge case vanno nel contratto») regge da sola. La data resta come convenzione di casa | Media | rewrite: «(measured in an A/B, 2026-08)» |
| FD-7 | `commands/review.md:6` | «Produce a **brutally honest** improvement plan … anchored ONLY to objective evidence» | 1a registro | Il registro del prompt diventa il registro dell'output: «brutally» produce severità artificiale, mentre il vincolo vero è già scritto («anchored only to objective evidence») | Media | rewrite: tolto l'aggettivo, resta il vincolo |

## Flag (nessuna modifica)

- **`skills/delega-efficiente/SKILL.md:3`** — description con ~9 casi di trigger elencati. Gruppo 2 «trigger-case enumeration» contro Gruppo 3 «il testo di trigger può portare urgenza calibrata, idealmente tarata su un trigger eval». È esattamente il compito 4 (plugin eval sui trigger): misurato nel pomeriggio, la description non scattava sul prompt del batch (0/6) e la frase «the kernel already carries the axes» era la causa probabile; riscritta come domanda dell'utente, 4/4 (`docs/plugin-eval-2026-09-25.md`).
- **`kernel.md:15` + Stop hook 2×/3× + `[fd-handoff]` a 60/80%** — Gruppo 4 «budget countdown nel contesto» e, nella guida Fable 5.1, «context anxiety… most often when the harness surfaces a remaining-token countdown». Qui è una riga sola con l'antidoto accanto («never mid-task; the user's call»). Da osservare nel `report`, non da cambiare.
- **`skills/delega-efficiente/SKILL.md:47`** «hard cap (default ~1-2k tokens)» — 1f (tetti numerici) contro keep-list 4 (il contratto di un tool resta): è un campo dell'Interfaces del contratto verso un esecutore, non prosa per l'utente. Tenuto.
- **`skills/delega-efficiente/SKILL.md:93`** — i flag di `budget-open` in prosa (flag del 09/09): miglioramento adiacente (spostarli nell'`--help`), non cruft.
- **`agents/fd-executor.md:17`** «Regole non negoziabili» — titolo con registro 1a; le otto regole sotto portano ognuna la ragione. Basso.
- **`agents/fd-executor.md:7`** `model: sonnet` — con Opus 5.5 la misura del 2026-09-01 (Fable a effort low = stesso eq, 5,5× USD) va rifatta: il kernel lo dice già («extrapolation until measured»).

Fuori scope, invariato dal 09/09: caveman (reinserimento a ogni prompt + regole anti-formattazione: 1d, su Fable 5.1 sottrae formattazione voluta) e superpowers («EXTREMELY_IMPORTANT… 1% chance… MUST»: 1a). Entrambi attivi anche in questa sessione.

## verifica-modello ↔ prompt-audit

La skill duplicava due regole della guida (`no-think-hard`, `no-visible-reasoning` = riga 1b) come regex proprie. Ora:

- `model-rules-check.py --audit-signals` legge le righe `**Signals:**` di `prompt-audit.md` (skill claude-api integrata, versione più alta sotto `bundled-skills/`, o `--audit-file PATH`) e le greppa sulla stessa superficie: i match escono come candidati raggruppati per riga della guida, non cambiano l'exit code, `skills/synced/` (skill Anthropic copiate da claude.ai) escluse. Guida assente → `STATUS: unavailable`, mai silenzio.
- Le regole `forbid` del JSON restano solo per ciò che i segnali della guida non greppano (varianti italiane, ragionamento visibile), con `audit_row` che punta alla riga della guida; `require` e `setting` restano perché la guida non li vede.
- La skill dice che il passaggio completo è `/claude-api prompt-audit <paths>`.

Test: `tests/model-rules-verify.py` 11/11 (R9 segnali da file, R9b `--json`, R10 guida assente).

## Misura prima/dopo

Non eseguita, come il 09/09: due sessioni di prova per sette hunk di forma non le valgono. La sola modifica che tocca ogni item delegato è FD-2; se `report` mostra un cambio di `rework` o di ok-rate per `fd-executor` nelle prossime settimane, si guarda lì.
