# Prompt-audit — 2026-09-26

Eseguito in sessione interattiva sulla cartella `fable-director` con la guida `prompt-audit.md` integrata in Claude Code 2.1.283 (la stessa a cui `/doctor prompt-audit` delega), più la testa che il comando nativo antepone: percorsi stantii, comandi stantii, file di istruzioni che si contraddicono. Due tentativi con `claude -p "/doctor prompt-audit fable-director"` non hanno prodotto rapporto (il primo scaduto a 15 minuti dopo 48 letture e 6 comandi negati in `dontAsk`, il secondo interrotto per il carico macchina): la testa è stata eseguita con due script a zero token, il resto a mano. I 7 hunk sono **applicati** (richiesta della master del 26/09: «applica ciò che trova»).

## Assunzioni

- **Modello bersaglio**: Claude Fable 5.1 (il modello che esegue l'audit; `~/.claude/settings.json`). Gli agenti pinnati: `fd-executor` su sonnet, `fd-verifier` `inherit`.
- **Scope**: la superficie prompt della cartella del plugin — `kernel.md`, tre skill, due agenti, sei comandi, `playbook-template.md`, `README.md` del plugin (è la scheda della directory), il testo iniettato da `session-kernel.sh` e `route-hint.py`. Fuori scope: `~/.claude/CLAUDE.md` e i README/INSTALL/ONBOARDING della radice (già passati il 25/09 e stamattina per il comando d'installazione).
- **Provenienza**: `docs/prompt-audit-2026-09-25.md` (7 hunk della 1.51.0) copre Gruppo 1; oggi il peso è sul Gruppo 2 e sulle righe scritte dopo il 25/09.

## Testa: percorsi, comandi, contraddizioni (zero token)

- **Percorsi**: ogni `scripts/…`, `tools/…`, `docs/…`, `model-rules/…` citato nella superficie esiste (script su 20 file, 0 mancanti; i tre falsi positivi della prima corsa erano `claude-observe/…` e l'esempio `tests/run.py` della statusline).
- **Comandi e flag**: ogni script citato esiste; ogni `--flag` e sottocomando citato accanto a uno script è definito nel suo sorgente (8 candidati, tutti falsi positivi: flag di `budget-open` sulla stessa riga di un altro script). Il comando morto `fable-director@pixelfarm` era già corretto stamattina.
- **Contraddizioni tra file**: una (H1) e una incompletezza (H7); la regola «la skill non cresce mai al netto» era violata dalla mia stessa aggiunta di stanotte (H5).

## Rilievi (per confidenza)

| # | Posizione | Evidenza | Pattern | Perché | Conf. | Azione |
|---|---|---|---|---|---|---|
| H1 | `fable-director/README.md:68` | «Enable it with `/fable-director:statusline` (INSTALL §6)» | G2 fatto stantio: contraddetto da `commands/statusline.md` («l'installazione avviene da sola alla prima sessione»), `INSTALL.md` §6 «automatic since 1.50.1», `session-kernel.sh:120` (`--auto`) | Il README è la scheda della directory: dice di lanciare un comando che serve solo a rimuovere | Alta | rewrite → «Installs itself at the first session; `--remove` turns it off» |
| H1b | `fable-director/README.md:68` | «plain by default since 1.39» | G1d fraseggio relativo a una versione precedente | Diff contro una versione che il lettore non ha visto | Media | rewrite → «plain by default» |
| H2 | `skills/delega-efficiente/SKILL.md:98` | «were invisible until measured at 9.2M fresh input in one session» | G2 narrazione storica | La regola vale per ciò che prescrive (il hook conta anche i file degli agenti Workflow), non per l'incidente | Media | rewrite, resta il fatto |
| H3 | `skills/delega-efficiente/SKILL.md:41` | «Never another account» senza ragione | G1e divieto senza provenienza | La ragione esiste (il contesto passerebbe a un'altra organizzazione) e va accanto al divieto | Media | rewrite con la ragione |
| H4 | `skills/verifica-modello/SKILL.md:3` | «both accounts (~/.claude, ~/.claude-pixel)» | G2 specifico volatile: percorso della macchina dell'autore in una skill distribuita (e nella scheda della directory) | Gli account sono dati in `model-rules/_targets.json`; la descrizione deve nominare la fonte, non i valori di una macchina | Media | rewrite |
| H5 | `skills/delega-efficiente/SKILL.md:60` (aggiunta del 26/09 00:40) | bullet nuovo sui workflow sotto fallback | G2 contraddizione con la meta-regola della riga 10 («never grows net») | Aggiunta netta senza cancellare nulla | Media | move: fusa nel bullet `model:`/`effort:` (riga 59), bullet cancellato |
| H6 | `playbook-template.md:312` | «Misurato: run wf_4798f715 del 03/09 … dal plugin 1.40.0 il gate fa l'aritmetica» | G2 narrazione storica (id di run, versione del plugin) | Archeologia in una voce di registro; vale solo per le copie nuove del template (il playbook dell'utente è fuori dal plugin) | Media | rewrite |
| H7 | `skills/delega-efficiente/SKILL.md:96` | «enforced on Write/Edit inside the project by a dedicated hook» | G2 fatto incompleto: `hooks.json` esegue `perimeter-gate.py` anche su `Bash` e `kernel.md:22` cita `deny_git` | La skill non dice che i sottocomandi git in `deny_git` sono negati su Bash | Media | rewrite |

**Flag (nessuna modifica)**
- `commands/help.md:6` — «Do NOT use the Read tool. Do NOT summarize» in grassetto (G1a). Operazione fragile «incolla verbatim»: la pressione è mirata a un fallimento osservato (il modello riassumeva la legenda). Bassa.
- Misure datate con versione di Claude Code (`CC 2.1.257`, `2.1.267`, `2.1.282`) in `delega-efficiente` — provenienza delle misure, non regole relative: si tengono (keep-list 1).
- `analisi-video:8` — l'incidente Gemini del 2026-09-08 è la ragione della regola «l'audio lo dice ffprobe»: si tiene.
- `delega-efficiente` a 31 KB — «SKILL.md non leggibile in una seduta» (G2): la meta-regola della riga 10 è il presidio; nessun taglio per lunghezza (keep-list 2).

Conteggi: Gruppo 1: 2 (H1b, H3); Gruppo 2: 5 (H1, H2, H4, H5, H6, H7 — H1 e H1b sulla stessa riga); Gruppo 3: 0; Gruppo 4: non applicabile (nessun codice di richiesta; roster agenti distinto; contabilità token presente).

## Verifica

- `claude plugin validate --strict fable-director` → verde dopo gli hunk.
- Nessun test asserisce le frasi modificate (`grep` su `tests/` e `model-rules/`: `.claude-pixel` compare solo nelle fixture, «never propose another account» è testo di `route-hint.py`, invariato).
- Rileggere il rapporto alla prossima release di modello (`verifica-modello`).
