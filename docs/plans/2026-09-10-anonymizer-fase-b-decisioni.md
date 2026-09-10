# Anonymizer — decisioni aperte per la fase B e proposta di release (2026-09-10)

Una schermata. Franz decide; niente è stato eseguito.

## Decisioni aperte

| # | Domanda | Opzioni | Raccomandazione |
|---|---|---|---|
| 1 | «Franz», «Francesco Sorrentino» e i collaboratori dell'agenzia: whitelist di progetto o PERSONA? | (a) whitelist in `.fd-anonymizer.json` dell'agenzia: i nomi dello staff non escono mai pseudonimizzati, i documenti restano leggibili al fornitore; (b) PERSONA come ogni altro nome: coerenza, ma «[PERSONA_3] deve confermare» in ogni spec | **(a)** per lo staff; i clienti e i loro referenti restano PERSONA/ORG. Oggi nel corpus privato sono annotati PERSONA: con (a) si aggiornano 3 annotazioni |
| 2 | Innesto in `external-exec.py` / `cross-verify.py` | (a) flag `--anonymize on\|off\|auto` (auto = `enabled` in config), `redact` su spec e input prima della chiamata, `restore` sull'output prima di `--out`; mappa per budget (`<cwd-slug>/<budget-id>`); (b) solo guardia PreToolUse che avvisa, innesto dopo | **(a)** con `auto` e `enabled: false` di default: chi non lo accende non cambia nulla. `media-analyze.py` solo blocco (file binari) |
| 3 | Evento `anonymizer` nel registro (telemetria) | (a) evento per ogni `redact` della rotta esterna: motore, categorie e conteggi, ms, `restored` — mai valori; letto da `report` in un blocco «anonymizer»; (b) niente evento, solo `DETAIL:` a schermo | **(a)**: senza registro non si misura l'uso reale e `report` non può dire «N run esterne, M valori mai usciti» |
| 4 | Classe dati `confidential` (§7.1 del piano) | `--data-class confidential` = la rotta esterna esce solo dopo `redact`, evento a registro; `restricted` resta muro | come deciso il 10/09: nuova classe, README aggiornato |
| 5 | Domini nudi in prosa («rinnovo dominio cliente.it» senza `https://`) | (a) regola con lista di TLD (`.it .com .eu .net .org …`) e whitelist; (b) solo dizionario | **(a)** in fase B, misurando i falsi positivi su nomi di file (`report.md` non è un dominio: escludere estensioni note) |

## Proposta di release (pronta, non eseguita)

- **Versione: 1.44.0** (minor: modulo nuovo, nessun cambiamento di comportamento per chi ha già installato: spento di default, nessun hook nuovo, nessun kernel modificato). I 4 commit locali non pushati (storia riscritta il 10/09 alle 10:15 prima di ogni push: i commit originali contenevano un nome di referente cliente, due clienti citati per nome nel piano e, nei test, la ragione sociale e la P.IVA vera di un cliente presi da un export; ora tutti sostituiti da valori sintetici, `git log -p origin/main..main` pulito).
- **Cosa cambia per chi installa**: nuova cartella `fable-director/anonymizer/` (~90 KB con il corpus pubblico) e `scripts/anonymizer.py`; comando `python3 <plugin>/scripts/anonymizer.py scan|redact|restore|test|status`; config facoltativa `~/.claude/fable-director/anonymizer.json` e `.fd-anonymizer.json`; nessuna dipendenza. `README`, `INTERNALS`, `CHANGELOG` già scritti sotto «Unreleased».
- **Passi** (da `release.sh`, che fa anche il push e l'install nei due account): `plugin.json` → `1.44.0`; badge README riga 3 → `version-1.44.0-blue`; CHANGELOG: rinominare «## Unreleased» in «## 1.44.x» e la voce in «**1.44.0 — anonymizer phase A: engine and CLI.**»; `git status` pulito (`release.sh` fa `git add -A`); poi `bash release.sh 1.44.0`.
- **Prima del push**, quattro controlli che il preflight non fa. Il quarto è l'anonymizer stesso sulla storia non pushata, col dizionario dell'agenzia (oggi quello del corpus privato: 301 ORG, 349 domini; in fase D quello generato dal gestionale, con le persone): push rifiutato se trova PERSONA o ORG; le altre categorie (CF, IBAN, TEL…) sono i valori sintetici del corpus pubblico e vanno letti a mano una volta:

```bash
REPO=~/Desktop/workspaces/personali/fable-director/fable-director-marketplace
X=$(mktemp -d); cp ~/.claude/fable-director/anonymizer-corpus/fd-anonymizer.json "$X/.fd-anonymizer.json"
( cd "$X" && git -C "$REPO" log -p origin/main..main | python3 "$REPO/fable-director/scripts/anonymizer.py" scan --stdin --json ) \
  | python3 -c "import json,sys; r=json.loads(sys.stdin.read().split('STATUS:')[0])[0]['counts']; bad={k:r[k] for k in ('PERSONA','ORG') if r.get(k)}; print('PUSH RIFIUTATO', bad) if bad else print('storia pulita dal dizionario:', r)"
rm -rf "$X"
```

Provato il 10/09 alle 10:15: prima della riscrittura ORG 29 (il cliente nei test); dopo, PERSONA 0, ORG 0. Limite dichiarato: vede solo ciò che sta nel dizionario (i due clienti citati per nome nel piano non c'erano: li ha trovati il grep). Gli altri tre: `git log --name-only origin/main..main | grep -i "corpus-private\|anonymizer-corpus"` deve essere vuoto (verificato: vuoto) e `git log -p origin/main..main | grep -iE '<nomi dei clienti citati nei documenti>'` vuoto (verificato dopo la riscrittura); `python3 fable-director/scripts/anonymizer.py test --strict` verde sul pubblico (verificato); nessun valore reale nei doc: `grep -rn` di nomi di clienti su `docs/` e `README.md` vuoto (i numeri in `docs/plans/2026-09-09-anonymizer.md` §8 sono aggregati; un nome di referente cliente c'era, tolto il 10/09 alle 10:05 prima di ogni push).
