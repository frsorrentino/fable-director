# Misure aperte dopo Claude Code 2.1.280 (2026-09-22)

Voci del brief della master del 22/09 che richiedono dati, non codice. Nessuna è implementata.

## Fork ripresi e cache (brief punto 3)

2.1.280 corregge i fork ripresi che ricostruivano la lista dei tool invece di rimandare quella usata la prima volta, rompendo il prompt caching di quell'agente. Il costo dei fork ripresi misurato fino alla 2.1.278 contiene quei cache miss.

- Prima di ritarare le soglie dell'asse 6, dividere i fork ripresi per versione: ≤ 2.1.278 e ≥ 2.1.280 (la 2.1.279 non ha release).
- La prima misura della parità su Claude Opus 5.5 (cache read 0.05×) viene dalle sessioni Opus 5.5 con fork, solo ≥ 2.1.280. Fino ad allora il valore tra le due ancore (0.025× → ~7-12 turni, 0.1× → ~2) è un'estrapolazione e non va scritto come fatto.

## Altre correzioni della 2.1.280 che sporcano le serie storiche (brief punto 6)

- Report di un subagente perso dopo una compattazione: una parte dei subagenti "che non hanno riferito" prima della 2.1.280 può essere questo difetto. Dividere per versione se la metrica si usa.
- Messaggi a un subagente in background persi a fine turno (headless/SDK): rilevante per `SendMessage` dai workflow.
- Shell in background con uscite non-zero innocue segnalate come fallimento (es. grep senza risultati): se Stop hook o `--verify` leggono lo stato dei task in background, possibili falsi negativi prima della 2.1.280.
- `hook_execution_complete` OTel porta le dimensioni dell'output: misura esterna di quanto iniettano kernel, route-hint e fd-memory. Utile solo con OTel acceso (oggi spento).
- Cambio modello da app host con cache miss (corretto): non tocca `model-switch.py`, che ragiona sul cambio da terminale.

## Non verificato

- `cache_create` di Claude Opus 5.5: le note di rilascio non lo dichiarano; resta il default 1.25×.
- `display_name` della statusline per Opus 5.5 con contesto 1M: `norm()` distingue `OPUS5` da `OPUS55`, ma un suffisso nel nome visualizzato (es. contesto) non è stato osservato dal vivo.
