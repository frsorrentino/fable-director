# Mods prototipo — Claude Code 2.1.287 (2026-10-01)

Tre mod in `mods/`, nessuno collegato al plugin. Si caricano a mano con
`claude --plugin-dir mods/<nome>`. Ogni mod ha i suoi test (`claude plugin test mods/<nome>`).

## fd-usage — quote e costo da `session.measure`

Il motore spinge a ogni turno le cifre della statusline: contesto, `rateLimits` con
`percentUsed` e `resetsAt`, costo. Il mod le scrive in
`~/.claude/fable-director/mods/usage-<acct>.json`, con le chiavi di
`quota-<acct>.json` e lo stesso hash dell'account (`sha256(CLAUDE_CONFIG_DIR o ~/.claude)[:8]`).
In più scrive `cost_usd` e `context`.

**Prova** (sessione `claude -p` vera, 21:53): `five_hour_used_pct` 0, `five_hour_resets_at`
1790902200, `weekly_used_pct` 6 e `weekly_resets_at` 1791424800 sono identici al file
scritto dalla statusline della sessione interattiva. Costo 0,2158 USD, contesto 38.864/1M.

**Vantaggio:** le quote arrivano anche dove non gira la statusline: `claude -p`,
telefono, Remote Control. Oggi in quei casi il gate e `/fable-director:status` leggono
l'ultimo render di un'altra sessione.

**Prima di adottarlo:** il file della statusline fa anche la fusione fra sessioni dello
stesso account (1.52.3), la storia per il burn-rate e lo snapshot claude-hud. Il mod
non fa nessuna delle tre. Le letture si possono togliere solo quando i Mods saranno
disponibili a tutti gli utenti del plugin. Oggi i Mods richiedono 2.1.287 e restano in
anteprima.

## fd-effort-probe — effort e modello per step (`turn.step`)

Il mod è spento di default. Con `FD_EFFORT_PROBE=low|medium|high` cambia l'effort degli
step di continuazione del thread principale, cioè le richieste dopo un risultato di tool
(`index > 0`). Lo step 0, dove il turno si pianifica, tiene l'effort della sessione.
`FD_MODEL_PROBE` fa lo stesso con il modello. Ogni step finisce in
`mods/effort-probe-<sessione>.jsonl` con il proprio `usage`.

**Prova:** stesso task (tre `wc -l` in sequenza, poi la somma), Opus 5.5 a medium,
2 run per braccio.

| braccio | effort per step | cache write | cache read | output | eq (×input) |
|---|---|---|---|---|---|
| off | medium ×4 | 28.775 | 126.955 | 361 | 50.477 |
| off | medium ×4 | 23.128 | 125.888 | 349 | 43.252 |
| low | medium, low ×3 | 23.128 | 125.888 | 346 | 43.237 |
| low | medium, low ×3 | 23.480 | 126.240 | 467 | 44.317 |

- La riscrittura funziona: il log mostra `low` sugli step 1-3.
- Cambiare l'effort a metà turno non ha perso la cache: a parità di run la cache write è identica (23.128).
- Su questo task il costo non cambia: l'output è circa l'1% dell'eq e domina il rumore della cache.
- Il guadagno possibile sta solo nei turni con molto ragionamento negli step di continuazione. Va misurato su un task così, con N≥4, prima di qualsiasi attivazione.

## fd-gate — il gate dei budget come mod, più `agent.spawn`

`tool.call` su Agent, Task e Workflow legge lo stesso file di budget del gate Python, con
lo stesso slug (`cwd_slug`, verificato anche su un percorso Windows). Dà gli stessi
verdetti: nessun budget, file corrotto, budget chiuso, `declared_at` assente, budget
più vecchio di 24 h e budget flagged portano a deny; un budget aperto e fresco porta ad
allow. Gli avvisi del gate (quota guard, window fit, cost checkpoint, eccetera) restano
nel Python. `agent.spawn` impone `sonnet` a `fable-director:fd-executor` anche quando
chi chiama chiede un altro modello.

**Equivalenza dei rifiuti:** la matrice di 7 casi dà lo stesso verdetto nel gate Python
vero (`pre-delegation-gate.py` via `py.sh`) e nel mod: 7 su 7.

**Latenza:**

| gate | latenza |
|---|---|
| gate Python | 1,0-1,3 s di mediana a ogni chiamata (avvio di bash e Python) |
| mod, prima chiamata | 0,7-0,9 s (worker a freddo) |
| mod, chiamate seguenti | circa 0,3 s |

**`agent.spawn`:** in una run vera è stato chiesto `fd-executor` con `model: "opus"`.
Il subagente ha girato su `claude-sonnet-5-5`: 8 token di output, 0,017 USD.

**Prima di adottarlo:** il mod non sostituisce il gate, ne anticipa solo il nucleo. Se
nega, il PreToolUse Python non parte (`answered without next()`) e la telemetria
`gate_deny` si perde. La migrazione intera segue l'ordine di
`docs/function-hooks-mods-impatto-2026-09-25.md`.
