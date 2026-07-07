# Benchmark — risparmio token fable-director

Misura **riproducibile** del risparmio token, non una percentuale sbandierata.
Confronta lo stesso task eseguito da Claude Code **senza** e **con** la policy fable-director.

## Metodo

- **Arm `off`**: `claude -p "<task>"` senza kernel.
- **Arm `on`**: identico task + kernel fable-director iniettato via `--append-system-prompt`
  (isola l'effetto della sola policy di routing, il meccanismo con cui il plugin cambia le decisioni).
- Token letti dall'output JSON di `claude -p` (`.usage`, `.total_cost_usd`) — nessuna stima.
- Fixture deterministiche (seed fisso) rigenerate prima di ogni run.
- **N run per lato** (default 3): si riportano media e spread, non un singolo run.

## Task (3 forme)

1. `01-batch-deterministico` — 30 file di numeri → CSV di aggregati. Cuore scriptabile.
2. `02-classificazione` — 30 stringhe → label EMAIL/URL/PHONE/OTHER. Scriptabile via regex.
3. `03-misto` — parte deterministica (medie) + parte di giudizio (sintesi anomalie).

Il risparmio è massimo dove il lavoro è deterministico (la policy promuove a script → ~0 token
di modello sul cuore) e tende a zero dove anche il modello base scriverebbe comunque uno script.
Le tre forme servono proprio a mostrare il **range**, non un numero cherry-picked.

## Come lanciarlo

```bash
python3 gen_fixtures.py          # fixture deterministiche
RUNS=3 bash run.sh               # ~18 sessioni headless (3 task × 2 arm × 3 run)
# opzionale: MODEL=claude-opus-4-8 RUNS=3 bash run.sh
```

`run.sh` usa `--dangerously-skip-permissions` per non bloccarsi su ogni scrittura: gira solo
dentro `benchmarks/` (fixture locali), ma leggi lo script prima di eseguirlo.
Consuma quota del piano / API reale.

Output: `results/<timestamp>/` (JSON grezzi + `summary.txt`).

## Onestà

- Il numero nel README principale viene **da questo harness**, con N, media, spread e data.
- Se il delta è piccolo o rumoroso, si scrive quello. Nessuna estrapolazione a "ogni caso".
