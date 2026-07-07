# Fable-director

Policy di delega efficiente per Claude Code: il top model pianifica, giudica e verifica;
l'esecuzione va al mezzo più economico adeguato (script > mid-model > top model).
Come nella bottega rinascimentale: il maestro (il director) disegna e rifinisce, gli apprendisti
eseguono, la bottega accumula mestiere.

## Componenti

| Pezzo | Ruolo |
|---|---|
| `hooks/hooks.json` + `scripts/session-kernel.sh` | Inietta il kernel a ogni SessionStart (~500 token con header e puntatore playbook): 6 assi di routing + never-delegate + puntatore alla policy completa |
| `kernel.md` | Il testo del kernel |
| `skills/delega-efficiente/SKILL.md` | Policy completa, caricata on-demand: delegation contract, pre-budget falsificabile (soglia 3×, anti-Goodhart), rule-of-3 con best-of-3, promozione script, regole playbook, telemetria |
| `skills/delega-efficiente/tools/session-cost-report.py` | Rendiconto token per modello/main/subagenti dai transcript JSONL; metriche cache/delega (allarmi, non target); legge da solo il budget file e stampa i flag ≥3× |
| `scripts/fd-telemetry.py` | Pre-budget machine-readable (`budget-open`/`budget-close`, `--type` per la tabella densità), log eventi oggettivi su SQLite (`~/.claude/fable-director/telemetry.db`), `report` aggregato (densità codificata N≥10, allarme cache-thrash), cache idempotente opt-in (`cache-get`/`cache-put --verified`). Mai voti di qualità auto-assegnati |
| `scripts/stop-budget-check.py` (hook Stop) | Enforcement deterministico del 3×: a ogni fine turno confronta i token effettivi col budget aperto (attribuzione per lineage: l'usage dei subagenti è dentro toolUseResult nel main transcript — niente mtime, niente double counting); a sforamento blocca la chiusura, auto-logga l'evento `budget_flag` in telemetria (deterministico, zero token: il modello deve solo il post-mortem) e impone il post-mortem. Anti-loop: stop_hook_active + status flagged; budget >24h → stale |
| hook SessionEnd (`fd-telemetry.py session-summary`) | Logga a fine sessione totali token, cache_hit_ratio/efficiency/investment, delegation_overhead, coordination_cost — zero token di modello |
| `playbook-template.md` | Template del playbook: va copiato in `~/.claude/delega-playbook.md` alla prima installazione (fuori dal plugin: gli update non lo toccano) |
| `scripts/statusline-ctx.sh` (opzionale) | Statusline: `[MODEL]` attivo, `[CTX %]` context window conversazione, `[5H %→HH:MM]` quota piano 5 ore con orario di reset, `[7D %→"6 lug"]` quota settimanale con data reset, `[BDG ok/2×/3×]` stato pre-budget (sola lettura del budget file), soglie colore 60/80; badge caveman se presente. Abilitala con `/fable-director:statusline` (INSTALL §6) |
| `commands/statusline.md` + `scripts/statusline-install.sh` | Comando `/fable-director:statusline` che scrive la statusLine in settings.json: installer idempotente e merge-safe che auto-risolve il path assoluto reale dell'installazione (GitHub o directory locale), non tocca statusLine di terzi, fa backup. `--remove` per disinstallarla |

## Installazione

Vedi `INSTALL.md` nella cartella marketplace (un livello sopra). In breve:
`claude plugin marketplace add <path>` → `claude plugin install fable-director@pixelfarm --scope user` → init playbook.

## Ciclo di apprendimento

1. Fallimento approach/tool alla 3ª escalation o sforamento pre-budget ≥3× → voce `[candidata]` nel playbook. Lo sforamento non dipende dalla disciplina del modello: lo rileva l'hook Stop e blocca la chiusura finché il post-mortem non è scritto.
2. Seconda occorrenza indipendente → `confermata`.
3. Task deterministico ricorso ≥2 volte → promosso a script (`tools/` del repo giusto) e registrato nel playbook.
4. Le euristiche confermate di valore generale si promuovono nel plugin alla release successiva → arrivano a tutto il team.

Tetto playbook: 30 righe, consolidare prima di appendere. Voci `[seed]` = pattern importati deliberatamente.

## Dipendenze soft

`caveman` (cavecrew-*, /caveman-stats) e `superpowers` (systematic-debugging, brainstorming):
senza, la policy degrada con grazia. Consigliati per comportamento 1:1.
