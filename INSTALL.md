# Fable-director — installazione

> Istruzioni operative per Claude Code. Se un utente ti indica questo zip o questa cartella
> chiedendo di installare il plugin, segui questi passi nell'ordine. Non saltare il punto 4.

## 1. Posizione permanente

La cartella `fable-director-marketplace/` deve vivere in un percorso STABILE: dopo l'installazione
il plugin viene letto da qui, non copiato altrove. Se hai ricevuto uno zip:

```bash
mkdir -p ~/claude-plugins && unzip <file>.zip -d ~/claude-plugins/
```

Percorso marketplace risultante: `~/claude-plugins/fable-director-marketplace` (deve contenere `.claude-plugin/marketplace.json`).

## 2. Registrazione marketplace + installazione

Prova prima la via non interattiva (CLI):

```bash
claude plugin marketplace add ~/claude-plugins/fable-director-marketplace
claude plugin install fable-director@pixelfarm --scope user
claude plugin list   # verifica: fable-director presente e enabled
```

Se i comandi CLI non sono disponibili nella versione installata, chiedi all'utente di eseguire
in sessione questi due slash command (sono comandi utente, non puoi lanciarli tu):

```
/plugin marketplace add ~/claude-plugins/fable-director-marketplace
/plugin install fable-director@pixelfarm
```

## 3. Inizializza il playbook (MAI sovrascrivere)

```bash
[ -f ~/.claude/delega-playbook.md ] || cp ~/claude-plugins/fable-director-marketplace/fable-director/playbook-template.md ~/.claude/delega-playbook.md
```

Se il file esiste già, NON toccarlo: contiene euristiche accumulate dall'utente.
Team con playbook condiviso: al posto della copia, symlink al file nel repo di team.

## 4. Verifica

Nuova sessione Claude Code, poi controlla:
- a SessionStart compare il blocco `FABLE-DIRECTOR KERNEL` (~500 token, 6 assi);
- la skill è elencata come `fable-director:delega-efficiente`;
- `python3 <marketplace>/fable-director/skills/delega-efficiente/tools/session-cost-report.py --help` non è richiesto: lo script si lancia senza argomenti dalla dir di un progetto.

## 5. Fallback senza sistema plugin

Solo se il plugin system non è utilizzabile:
1. `cp -r fable-director/skills/delega-efficiente ~/.claude/skills/`
2. Fondi (merge, mai sovrascrivere il file) l'hook di `fable-director/hooks/hooks.json` dentro
   `~/.claude/settings.json`, sostituendo `${CLAUDE_PLUGIN_ROOT}` con il path assoluto
   della cartella `fable-director/`.
3. Punto 3 (playbook) invariato.

## 6. Statusline (opzionale)

Mostra sempre `[MODEL]`, `[CTX %]` (context window conversazione), `[5H %→HH:MM]` (quota piano 5 ore con orario di reset, la
"Current session" di /usage), `[7D %→reset]` (quota settimanale) e `[BDG]` (stato pre-budget fable-director).

La statusLine NON è un componente che il plugin possa auto-registrare (a differenza di
hook/skill/command): va scritta in `settings.json`. Per rendere lo step uguale e a prova di
errore su ogni macchina, il plugin fornisce un **installer** che la scrive da solo, risolvendo
il path assoluto reale di QUESTA installazione (si auto-localizza accanto allo script — funziona
sia con marketplace da GitHub sia aggiunto come directory locale).

**Via consigliata (chiunque, dopo install o update):**

```
/fable-director:statusline
```

Idempotente: reinstalla → aggiorna il path se cambiato; se esiste già una statusLine di terzi
NON la tocca (avvisa). Rimozione: `/fable-director:statusline --remove`. Backup automatico in
`settings.json.bak`. **Serve riavviare Claude Code** perché la statusLine è letta all'avvio.

Equivalente senza slash command (stesso effetto):

```bash
bash "<installLocation>/fable-director/scripts/statusline-install.sh"
```

Solo se preferisci l'edit manuale di settings.json (merge, non sovrascrivere una statusLine
esistente): `"statusLine": { "type": "command", "command": "bash \"<installLocation>/fable-director/scripts/statusline-ctx.sh\"" }`.

Richiede Claude Code ≥2.1.x (campi `context_window`/`rate_limits` nello stdin); su versioni
prive dei campi degrada in silenzio. Se il plugin caveman è presente, il suo badge resta.

## Dipendenze soft

La policy cita i plugin `caveman` (agenti cavecrew, /caveman-stats) e `superpowers`
(systematic-debugging, brainstorming). Senza di essi funziona comunque, degradando con
grazia (Explore vanilla al posto di cavecrew, niente stats hook). Consigliata l'installazione
per comportamento 1:1.

## Cosa fa il plugin, in breve

- **SessionStart hook** → inietta il kernel (6 assi di routing + never-delegate, ~500 token).
- **Skill `fable-director:delega-efficiente`** (on-demand) → policy completa: delegation contract,
  pre-budget falsificabile con soglia 3×, rule-of-3 con best-of-3, promozione script, regole playbook,
  telemetria a eventi oggettivi.
- **Stop hook (`stop-budget-check.py`)** → enforcement deterministico del 3× sul budget aperto
  (`~/.claude/fable-director/budgets/<cwd-slug>.json`, scritto da `fd-telemetry.py budget-open`):
  a sforamento blocca la chiusura del turno finché il post-mortem non è scritto.
- **SessionEnd hook (`fd-telemetry.py session-summary`)** → logga su SQLite
  (`~/.claude/fable-director/telemetry.db`) totali token e metriche cache/delega, zero token di modello.
- **`~/.claude/delega-playbook.md`** (esterno, sopravvive agli update) → euristiche apprese:
  `[candidata]` → confermata alla 2ª occorrenza; voci `[seed]`; contatori `(uses/ok/ko)`;
  tetto 30 con consolidamento.
- **`tools/session-cost-report.py`** → rendiconto token reale dai transcript JSONL, metriche
  cache/delega, flag ≥3× (legge il budget file da solo).
- **`scripts/statusline-ctx.sh`** (opzionale, §6) → statusline con `[MODEL]`, `[CTX %]`, `[5H %→HH:MM]`, `[7D %→reset]`, `[BDG]`.
  Abilitala con il comando **`/fable-director:statusline`** (o `scripts/statusline-install.sh`): scrive
  la statusLine in settings.json risolvendo il path da solo, idempotente e merge-safe.
