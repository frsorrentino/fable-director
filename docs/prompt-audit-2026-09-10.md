# Prompt-audit — 2026-09-10 (completamento del 09/09: claude-master)

Eseguito con `/claude-api prompt-audit` (skill integrata, Claude Code 2.1.267). Nessun file modificato: le proposte con azione stanno in `docs/prompt-audit-2026-09-10-claude-master.patch` (`git apply --check` passato nel repo `personali/claude-master`), da applicare hunk per hunk. Franz decide riga per riga.

## Assunzioni

- **Modello bersaglio**: Claude Fable 5.1 (`~/.claude/settings.json`), effort high; subagenti `fd-executor` su Sonnet 5 a effort low.
- **Scope richiesto** (master, 10/09 09:50): kernel e skill di fable-director, `~/.claude/CLAUDE.md`, skill di claude-master, skill d'agenzia in `pixelfarm/strumenti/claude-plugins`.
- **Già fatto il 09/09** su tutto tranne claude-master: rapporto `personali/fable-director/prompt-audit-2026-09-09/report.md` (18 rilievi con hunk, 13 flag). Stato verificato oggi: i 5 hunk fable-director sono applicati (release 1.43.1, `c59e0e0`: nessuna occorrenza di «think longer», «Scaling bands», «never cat whole», «1 reversal and 0 escalations»); i 13 hunk pixelfarm sono applicati (commit `2a04bc7`, plugin 0.3.1: nessuna occorrenza di «Non chiedere conferma», «sempre sicura», `rsync -avz --delete`). Le superfici fable-director e `~/.claude/CLAUDE.md` sono **invariate** dal 09/09 (`git diff 432bd0a..HEAD` vuoto su kernel, skill, agenti, comandi): nessun rilievo nuovo, i 13 flag del 09/09 restano aperti per Franz (riepilogo in fondo).
- **Provenienza claude-master**: `git blame` nel repo `personali/claude-master`; tutte le righe citate sono del commit `ce37e94` (2026-09-09), scritte già per Fable 5.1: nessun fossile di modello precedente. Il rilievo qui è di forma, non di generazione.

## Inventario claude-master

| Superficie | Byte | Quando entra in contesto |
|---|---|---|
| `kernel_it.md` (SessionStart, hook `cm-hook.py`) | 1.6k | ogni sessione |
| `skills/sessions/SKILL.md` | 11.1k | su lancio/lista/chiusura/messaggio a sessioni |
| `skills/screen-layout/SKILL.md` | 3.2k | su disposizione finestre |
| `commands/{close,launch,quota,report,restart,sessions}.md` | 2.8k | su invocazione |
| hook `UserPromptSubmit` `[ora locale]` | ~20 byte/turno | ogni prompt |

Superfici pulite: `kernel_it.md` (otto regole, ognuna con la ragione accanto; le maiuscole sono nomi di cose — SUGGERIMENTO, VUOTA — non booster), i sei comandi (l'unico «AS-IS»/«END THE TURN» ha la ragione accanto: lo Stop hook esegue il riavvio), l'hook dell'ora (dato, non istruzione: il modello non ha orologio; la doppia iniezione del 09/09 è risolta). Nessun pattern 1b (scaffold), 1d (fossili di modello), Gruppo 3 (tool description) o Gruppo 4.

## Sommario

| Gruppo | Rilievi | Con hunk |
|---|---|---|
| 2 narrazioni storiche (date di incidenti, id di test) | 4 | 4 |
| 2 enumerazione dei trigger nella description | 2 | 2 |
| flag (bassa confidenza) | 2 | 0 |

Impatto: basso. La skill è recente e scritta con le ragioni; i quattro rilievi storici tolgono archeologia (una data, un id di test, un incidente) lasciando la regola e il suo perché; le due description passano da liste di sinonimi a categorie di intento con pochi esempi, come vuole la guida (le liste crescono di una frase per ogni trigger mancato e generalizzano peggio). Sui trigger la guida ammette «urgenza calibrata»: se Franz ha misurato che le liste servono all'attivazione, i due hunk si scartano.

## Rilievi con azione (nel patch)

| id | Posizione | Evidenza | Pattern | Perché | Conf. | Azione |
|---|---|---|---|---|---|---|
| CM-1 | `skills/sessions/SKILL.md:3` | description con 10 trigger italiani + 7 inglesi elencati | Gruppo 2 «trigger-case enumeration» | La description viaggia in ogni richiesta; una lista di quasi-sinonimi tassa ogni turno e generalizza peggio delle categorie di intento | Media | rewrite: categorie (aprire, elencare, chiudere/riavviare, messaggi e segnalazioni, attendere, ripristinare) + 5 esempi |
| CM-2 | `skills/sessions/SKILL.md:9-10` | «Le regole qui sotto sono state pagate sul campo: valgono più dei comandi.» | 1a registro / Gruppo 2 narrazione | L'autorità di una regola è ciò che prescrive, non la sua storia; la frase alza il registro senza aggiungere vincoli | Media | rewrite: «Le regole qui sotto valgono più dei comandi.» |
| CM-3 | `skills/sessions/SKILL.md:41` | «(successo il 2026-08-24)» | Gruppo 2 narrazione storica | Data di incidente; la regola (`--resume <id>` con più conversazioni) resta con la sua ragione | Media | remove la parentesi |
| CM-4 | `skills/sessions/SKILL.md:121-122` | «l'08/09/2026 è morta `fable-director-2` al posto di `fable-director`» | Gruppo 2 narrazione storica | Il meccanismo (match per prefisso senza `=`) è il contenuto; l'incidente datato è archeologia | Media | rewrite: «`-t fable-director` può uccidere `fable-director-2`» |
| CM-5 | `skills/sessions/SKILL.md:131` | «(T51)» | Gruppo 2 id di test | Riferimento a un test interno che il modello non può leggere | Media | remove |
| CM-6 | `skills/screen-layout/SKILL.md:3` | description con 6 trigger italiani + 4 inglesi | Gruppo 2 enumerazione | Come CM-1 | Media | rewrite: categorie (affiancare, unire in schede, spostare di monitor, layout con nome) + 4 esempi |

## Flag (nessun hunk)

- **`skills/sessions/SKILL.md:170`** «(qui `--dangerously-skip-permissions`)»: specifica volatile (viene dalla config `session.claude_args`); ma è un fatto d'ambiente che spiega perché il percorso è l'unico controllo rimasto. Bassa; tenere.
- **Ridondanza funzionante** fra `kernel_it.md` (regole 1, 2, 3, 5) e la skill `sessions` (§ Parlare, § Chiudere, § Tempo): stesse regole, stesse ragioni, il kernel sempre presente e la skill a richiesta. Non si contraddicono: la guida dice di lasciarla. Tenere.

## I 13 flag del 09/09 ancora aperti (decide Franz)

`kernel.md:9` maiuscole contrastive (tenere) e refuso «2.389» → «2,389»; `kernel.md:19` countdown 2×/3× (deliberato, osservare nel `report`); `SKILL.md:35` flag di `external-exec.py` in prosa (spostare nell'`--help`: miglioramento adiacente); `SKILL.md:34` claim su CC 2.1.198 senza data di verifica; `SKILL.md:126` cinque eventi «AT EVENT TIME» loggati a singhiozzo (rimedio coerente: hook, non enfasi); `commands/review.md:6` «brutally honest»; `commands/help.md:6` «Do NOT use the Read tool» (scoped, tenere); `nuova-sessione:412` (skill in ritiro); `migrate-ortensia web:356` fraseggio relativo; `figma-to-wordpress:23` parent pinnato; `figma-to-wordpress:557` vs `migrate-ortensia web:65` nomi chiavi `opzioni_tema()` (verificare sul sorgente); `deploy-siteground:439` «SEMPRE il remote SSH» (ragione presente); fuori scope ma a ogni turno: caveman (~600 token/sessione + ~40/turno, reinserimento a ogni prompt) e superpowers (~700 token/sessione di enfasi).

## Come applicare

```
cd ~/Desktop/workspaces/personali/claude-master
git apply --check ~/Desktop/workspaces/personali/fable-director/fable-director-marketplace/docs/prompt-audit-2026-09-10-claude-master.patch
git apply         ~/Desktop/workspaces/personali/fable-director/fable-director-marketplace/docs/prompt-audit-2026-09-10-claude-master.patch
```

Per prendere solo alcuni hunk: `git apply --include='*/sessions/*'`, oppure `git apply -3` e poi `git checkout -p`. Verifica dopo: le due skill devono ancora attivarsi sulle frasi tolte dalla description («lancia claude in», «affianca le sessioni»): una prova ciascuna in una sessione nuova.
