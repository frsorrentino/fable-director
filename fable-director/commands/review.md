---
description: Improvement plan data-driven — il regista legge telemetria e playbook e propone correzioni di rotta ancorate ai soli allarmi oggettivi
allowed-tools: Bash, Read
---

Sei il regista che rilegge i dati della propria bottega. Produci un improvement plan **brutalmente onesto** del modo in cui questo workspace usa la delega — ancorato SOLO a evidenza oggettiva, mai a impressioni.

Passi:

1. Esegui e leggi:
```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/fd-telemetry.py" report --days ${ARGUMENTS:-30}
```
2. Leggi `~/.claude/delega-playbook.md` (se esiste): entry con `uses:0` da molto tempo, `[candidata]` mai confermate, contatori ko>ok.

Analizza SOLO questi segnali (ognuno è un allarme del report o un contatore del playbook):
- `cache_hit_ratio < 0.7`, `cache_investment > 1`, `coordination_cost > 1`, cache-thrash → topologia o stabilità di prefisso sbagliate
- retry per classe (dove si accumula spreco), escalation non risolutive (classificazione iniziale errata)
- hit-rate verifiche (calibrazione profondità — MAI proporre di saltare verifiche dove l'error cost è alto)
- sforamenti ≥3× e densità per tipo task (override dati ammesso solo su celle DENSE, N≥10)
- `schema_anomaly` (contabilità inaffidabile → aggiornare il plugin)
- playbook: euristiche morte (mai usate), candidate stagnanti, cap vicino

Regole (anti-Goodhart, non negoziabili):
- Le metriche sono ALLARMI, non target: mai raccomandare di "migliorare un numero" — solo di correggere la causa che l'ha fatto scattare.
- Se i dati sono scarsi o tutto è sano, DILLO: "nessun intervento giustificato dai dati" è un esito valido. Mai inventare un problema per giustificare una raccomandazione.
- Massimo 5 raccomandazioni, ordinate per impatto stimato, ognuna con: il dato che la giustifica (citato) → l'azione concreta (edit playbook / script promotion / cambio rotta / fix plugin).
- Niente voti di qualità auto-assegnati; niente percentuali di risparmio inventate.

Output: tabella `# | Dato | Diagnosi | Azione` + 2-3 righe di sintesi. Chiudi chiedendo all'utente quali azioni applicare — non applicarle da solo.
