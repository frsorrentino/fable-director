# Anonymizer fase A — design

Data: 2026-09-10. Deriva da `docs/plans/2026-09-09-anonymizer.md` (§2, §2b, §5, §6 riga A, §7 decisioni). Decisioni di brainstorming prese il 10/09 (00:14-00:34): corpus reale pescato dalle cartelle pixelfarm (b), segnaposto `[UPPER_N]` (b), motore a raccolta di span con sostituzione unica (2). Sessione autonoma: dove il piano lasciava una scelta, vale l'opzione raccomandata.

## 1. Consegna della fase A

Motori `rules(it)`, `columns`, `dictionary`; mappa dei segnaposto con `restore`; CLI `scan/redact/restore/test/status`; corpus con verità nota (12 documenti esterni + ~20 reali dei progetti, annotati dal modello, revisione a campione di Franz) fuori repo; corpus sintetico in repo per la suite pubblica; test `tests/anonymizer-verify.py` nella suite di `release.sh`.

Accettazione (piano §6): sulle categorie con formato, recall ≥ 99% e precisione ≥ 90% sul corpus annotato; `restore(redact(x)) == x` su tutto il corpus. PERSONA, ORGANIZZAZIONE, DATA_NASCITA senza contesto: riportate ma **informative** (arrivano con NER in fase C e col dizionario generato in fase D): non fanno fallire `test`.

Fuori scope: innesto in `external-exec.py`/`cross-verify.py`, guardia PreToolUse, classe `confidential`, evento a registro (fase B); NER (C); generatore del dizionario (D); demone e hook (E).

## 2. Architettura

```
fable-director/anonymizer/            pacchetto Python, stdlib pura
  __init__.py                         versione, export di engine
  engine.py                           redact(text, pmap, config) / restore(text, pmap) / scan(text, config)
  spans.py                            Span, merge_spans (sovrapposizioni), apply_spans
  config.py                           default + carica ~/.claude/fable-director/anonymizer.json + .fd-anonymizer.json
  pmap.py                             PlaceholderMap: valore → [CAT_N], persistenza JSON chmod 600
  rules/__init__.py                   registro dei pacchetti per paese
  rules/base.py                       regole indipendenti dal paese (EMAIL, IP, URL, CARTA, IBAN generico)
  rules/it.py                         CF, P.IVA, IBAN IT, TEL, TARGA, INDIRIZZO, CAP_COMUNE, DATA_NASCITA, PROTOCOLLO
  columns.py                          profili di intestazione → categoria; CSV/TSV/JSON tabellare
  dictionary.py                       liste per progetto: match esatto e per token
  corpus.py                           formato annotazioni, confronto span, metriche
fable-director/scripts/anonymizer.py  CLI (STATUS:/OUTPUT:/DETAIL:)
tests/anonymizer-verify.py            suite deterministica
tests/fixtures/anonymizer/            corpus sintetico pubblico + annotazioni + dizionario di prova
```

`scripts/anonymizer.py` aggiunge `<plugin>/` a `sys.path` e importa `anonymizer` (stesso schema degli altri script che usano `sys.path`). Il pacchetto non importa nulla da `scripts/`: il proxy dell'agenzia (§7.5) lo importa così com'è.

### 2.1 Span e motore centrale (`spans.py`, `engine.py`)

`Span = (start, end, cat, value, source)`; `source` ∈ `rules|columns|dictionary|ner`. Ogni motore riceve `(text, config, taken)` dove `taken` sono gli span già accettati, e restituisce nuovi span sul **testo originale**. Ordine dei motori: `columns`, `rules`, `dictionary` (columns prima: sul tabellare la cella intera è la verità, e le regex non devono spezzarla; sul non tabellare `columns` non produce nulla e l'ordine è indifferente). Il NER in fase C si aggiunge dopo `rules`.

`merge_spans`: ordina per `(start, -lunghezza, priorità del motore)`; scarta chi si sovrappone a uno span già accettato. Vince il più lungo, a parità il motore più a monte nell'ordine. `apply_spans`: sostituzione da destra a sinistra con `pmap.placeholder(cat, value)`.

`redact` restituisce `(testo, RedactReport)`; il report ha solo conteggi per categoria e motore, tempi, numero di segnaposto nuovi/riusati. Mai valori. `scan` = stessa pipeline senza sostituire né toccare la mappa.

### 2.2 Segnaposto e mappa (`pmap.py`)

Forma `[CAT_N]`, N progressivo per categoria, a partire da 1. Chiave di normalizzazione per «stesso valore → stesso segnaposto»: per categoria: EMAIL/URL/DOMINIO minuscolo; CF/IBAN/TARGA/PIVA maiuscolo senza spazi; TEL solo cifre con prefisso normalizzato (`0039`→`+39`); PERSONA/ORG/INDIRIZZO: casefold, spazi compressi. Il valore restituito dal `restore` è la **prima forma vista**.

`restore(text, pmap)`: regex `\[?\b(CAT)_(\d+)\b\]?` case-insensitive sulle categorie note della mappa; segnaposto sconosciuto resta com'è e viene contato in `unknown`. Tollera parentesi mancanti e minuscolo (output «sporco» del fornitore).

File: `~/.claude/fable-director/anonymizer/maps/<nome>.json`, `chmod 600`, cartella `chmod 700`. In fase A il nome lo dà `--map`; in fase B sarà `<cwd-slug>/<budget-id>`. Contenuto: `{"version":1,"created":…,"style":"[UPPER_N]","entries":{"EMAIL":{"1":{"value":"…","key":"…"}}}}`. Scadenza `map_ttl_days` applicata da `status` (elenca e cancella le mappe scadute con `--purge`).

### 2.3 Regole (`rules/`)

Ogni regola: `(cat, regex compilata, validatore opzionale, gruppo del valore)`. Correzioni misurate in §5 del piano, applicate:

- **TEL**: scatta solo con contesto entro 12 caratteri prima (`tel`, `cell`, `telefono`, `mobile`, `phone`, `whatsapp`, `fax`) oppure prefisso `+39`/`0039` oppure formato mobile `3\d{2}` seguito da 6-7 cifre; opzione `tel_requires_context` (default `true`): a `false` accetta anche i fissi senza contesto. Numeri stranieri: `+\d{1,3}` seguito da 6-12 cifre con separatori.
- **INDIRIZZO**: toponimo + nome con almeno una maiuscola (esclude `via SGR 2`) + numero civico; CAP e comune facoltativi e inclusi nello span se adiacenti.
- **URL**: opzione `url` = `domain-only` (default): span sul solo host, categoria `DOMINIO`; `full` = URL intero; `off`. Host in `whitelist` (es. `github.com`, `shields.io`, quelli del dizionario `public_domains`) esclusi.
- **IP**: esclusi loopback, `0.0.0.0`, privati (10/8, 172.16/12, 192.168/16) e link-local.
- **CF** con check digit; **P.IVA** con check digit (contesto `p.iva|partita iva|vat|IT` oppure 11 cifre isolate con check digit valido e non precedute da `n.`/`prot.`); **IBAN** IT con mod 97 e IBAN generico (2 lettere + 2 cifre + 11-30 alfanumerici) con mod 97; **CARTA** con Luhn; **TARGA** formato `AA 000 AA` (due lettere, tre cifre, due lettere, senza `I O Q U` nelle lettere come da normativa; i falsi positivi si misurano sul corpus); **DATA_NASCITA** solo con contesto (`nat[oa] … il`, `data di nascita`, `nascita:`); **PROTOCOLLO** come nel prototipo.
- **EMAIL** dal prototipo; il dominio dell'email non genera un secondo span `DOMINIO`.

Pacchetti: `base` sempre; `it` di default; struttura pronta per altri paesi (`packs` in config) senza scriverli ora.

### 2.4 Colonne (`columns.py`)

Riconosce il formato dal testo (non dal nome file): prima riga con ≥ 2 campi e delimitatore `,` `;` `\t` coerente su ≥ 3 righe → CSV/TSV; testo che si carica come JSON e ha un array di oggetti con chiavi ripetute → JSON tabellare. Altrimenti nessuno span.

Profili (intestazione normalizzata: minuscolo, spazi/`-`/`*` rimossi) → categoria:

| Profilo | Esempi |
|---|---|
| `generic` | `email`, `e-mail`, `mail`, `nome`, `cognome`, `name`, `first_name`, `last_name`, `telefono`, `phone`, `cellulare`, `indirizzo`, `address`, `cap`, `città`, `city`, `codice_fiscale`, `cf`, `piva`, `vat`, `iban`, `data_di_nascita`, `birthday`, `dob`, `pec` |
| `woocommerce` | `billing_first_name`, `billing_last_name`, `billing_email`, `billing_phone`, `billing_address_1/2`, `billing_city`, `billing_postcode`, `shipping_*`, `customer_email`, `customer_user` |
| `cf7` | `your-name`, `your-email`, `your-tel`, `your-phone`, `your-message` (→ nessuna categoria: la cella libera passa a `rules`) |
| `brevo` | `EMAIL`, `NOME`, `COGNOME`, `SMS`, `INDIRIZZO`, `PHONE`, `BIRTHDAY` |
| `mailchimp` | `Email Address`, `First Name`, `Last Name`, `Phone`, `Address`, `Birthday` |
| `prestashop` | `Customer e-mail`, `Lastname`, `Firstname`, `Address 1`, `Address 2`, `Phone`, `Mobile Phone`, `Company`, `VAT number`, `DNI`, `Birthday` |
| `pixelbox` | `cliente`, `vat`, `crm`, `fic_cliente`, `email`, `pec`, `domini`, `Cliente_CRM(match)`, `Email_CRM`, `Domini`, `ClienteStem` |

Categorie prodotte: EMAIL, PERSONA, TEL, INDIRIZZO, CAP_COMUNE, CF, PIVA, IBAN, DATA_NASCITA, ORG, DOMINIO. Cella vuota → nessuno span. Celle con più valori separati da ` | ` o `;` dentro un campo `domini` → uno span per valore. `columns.extra` in config aggiunge intestazioni proprie `{"intestazione": "CAT"}`.

Il profilo attivo non serve dichiararlo: tutte le intestazioni dei profili elencati in `columns.profiles` formano un'unica tabella di lookup; un conflitto (stessa intestazione, categoria diversa) è un errore di configurazione segnalato da `status`.

### 2.5 Dizionario (`dictionary.py`)

Da `.fd-anonymizer.json` (progetto) e `dictionary.files` (globale):

```json
{"dictionary": {"PERSONA": ["Mario Rossi"], "ORG": ["Ortensia Web S.r.l.s."], "DOMINIO": ["ortensiaweb.example"], "EMAIL": []},
 "whitelist": ["Pixelfarm", "WooCommerce", "Crotone"],
 "columns": {"extra": {"referente": "PERSONA"}}}
```

Match: esatto (casefold, spazi compressi, confini di parola) sull'intera voce; per token sui cognomi/nomi di `PERSONA` con ≥ 4 caratteri solo se il token intero compare in un contesto con iniziale maiuscola (evita `rossi` colore). Le voci ORG hanno anche la forma «stem» senza suffisso societario (`S.r.l.`, `srl`, `S.p.A.`, `snc`, `sas`, `di …`). Whitelist: valori esclusi da tutti i motori (confronto casefold); vale anche per gli host dei domini.

### 2.6 Configurazione (`config.py`)

Default come nel piano §3 con `placeholders.style` = `[UPPER_N]`. Fusione: default ← globale ← progetto (`.fd-anonymizer.json` cercato dalla cwd verso l'alto fino alla radice git o home). `enabled` non conta in fase A (la CLI è esplicita); conta dalla B.

### 2.7 CLI (`scripts/anonymizer.py`)

```
anonymizer.py scan    FILE... [--json]            conteggi per categoria e motore, nessuna scrittura
anonymizer.py redact  FILE [--map NAME] [--out F] [--engines rules,columns,dictionary]
anonymizer.py restore FILE --map NAME [--out F]
anonymizer.py test    [--corpus DIR] [--json] [--strict]   metriche su corpus annotato; --strict = exit 1 sotto soglia
anonymizer.py status  [--purge]                  config effettiva, profili, dizionario caricato, mappe e scadenze
```

Output sempre `STATUS: ok|error|unavailable` / `OUTPUT:` / `DETAIL:`. `redact` senza `--out` scrive su stdout; `--map` assente → nome `default`. `--stdin` accettato al posto di FILE. Mai valori a stdout salvo `redact` del testo stesso e `test --show-misses` (esplicito, per la revisione).

### 2.8 Corpus e metriche (`corpus.py`)

Layout di una cartella corpus: `docs/<id>.txt` + `docs/<id>.spans.json`:

```json
{"doc": "<id>", "annotator": "model|human", "reviewed": false,
 "spans": [{"start": 120, "end": 136, "cat": "EMAIL", "text": "x@y.it"}],
 "ignore": ["PERSONA"]}
```

`text` è ridondante: `test` verifica che `text == doc[start:end]` e fallisce se l'annotazione è marcia. Confronto: uno span predetto è vero positivo se stessa categoria e sovrapposizione ≥ 50% con uno span annotato (le regole includono o meno il CAP: non deve contare come errore). Metriche per categoria: TP/FP/FN, precisione, recall; totale sulle categorie «con formato» (`EMAIL, CF, PIVA, IBAN, TEL, TARGA, CARTA, IP, DOMINIO, PROTOCOLLO, INDIRIZZO, DATA_NASCITA, CAP_COMUNE`) contro le soglie; le altre informative. Più il round-trip: `restore(redact(x)) == x` per ogni documento con una mappa temporanea.

Corpus **pubblico** (`tests/fixtures/anonymizer/`): ~10 documenti sintetici generati una volta e committati (CF/PIVA/IBAN/carte con check digit validi ma di persone inesistenti, nomi inventati, tre export finti con le intestazioni vere di Brevo, PrestaShop, WooCommerce, un CSV pixelbox finto, un testo legale finto con protocolli, un README con URL pubblici che NON devono scattare). Annotato a mano nel codice che lo genera (`tests/fixtures/anonymizer/gen.py`, rieseguibile, deterministico con seed).

Corpus **privato** (`~/.claude/fable-director/anonymizer-corpus/docs/`): i 12 esistenti spostati nel layout + ~20 documenti reali dell'agenzia, estratti (prime 40-80 righe) di export del gestionale (dossier e solleciti clienti, report fatturazione, registro domini), di newsletter (Brevo, Mailchimp, rivenditori), di clienti PrestaShop (export admin e tabella grezza), un export di prodotti come negativo, modelli di email e procedure dei preventivi, documenti giuridici della kb personale. Nessun nome di file o di cliente in questo documento: il repo è pubblico. Annotazione: prima passata del motore, poi lettura e correzione del modello in questa sessione, `annotator: model`, `reviewed: false`; Franz mette `reviewed: true` a campione. `test` con `--corpus` esplicito o, se la cartella privata esiste, la aggiunge al pubblico e lo dice in `DETAIL`; assente → solo pubblico, nessun errore.

### 2.9 Suite (`tests/anonymizer-verify.py`)

Come gli altri `*-verify.py`: HOME usa-e-getta, esegue la CLI reale e importa il pacchetto. Casi:

- A1 check digit: CF/PIVA/IBAN/Luhn validi e non validi.
- A2 regole singole: ogni categoria con positivi e negativi noti (TEL senza contesto non scatta, `via SGR 2` no, `127.0.0.1` no, `github.com` in whitelist no).
- A3 merge degli span: sovrapposizione, vince il più lungo; email non produce DOMINIO.
- A4 columns: i tre export finti; cella intera; separatori `;` e `\t`; JSON tabellare; testo non tabellare → zero span.
- A5 dictionary: esatto, per token con maiuscola, stem ORG, whitelist.
- A6 pmap: stesso valore stessa forma normalizzata → stesso segnaposto; persistenza e `chmod 600`; restore tollerante (`[email_1]`, `EMAIL_1`); segnaposto sconosciuto conservato e contato.
- A7 round-trip su tutto il corpus pubblico.
- A8 `test --strict` sul corpus pubblico passa le soglie; il rapporto non contiene alcun valore del corpus (grep sull'output).
- A9 CLI: `STATUS:` in ogni comando, `scan` non scrive mappe, `redact --out` + `restore --out`, `status --purge` con una mappa scaduta.
- A10 prestazioni: ≤ 5 ms/KB su 200 KB (10× il prototipo: margine per il merge).

## 3. Errori e limiti dichiarati

- File non decodificabile → `STATUS: error`, `DETAIL: not text`; binari mai letti.
- Mappa corrotta → `error`, mai sovrascritta in silenzio.
- `restore` con mappa senza una categoria → segnaposti conservati, `unknown: n` nel report.
- `columns` non gestisce CSV con celle multilinea diverse da quote standard: usa `csv` di stdlib, gli offset si ricostruiscono per riga; se il parsing non ricostruisce esattamente il testo (offset non allineati), il documento cade su `rules` e il report lo dice (`columns: skipped, offset mismatch`).
- Nomi di persona fuori dal dizionario e fuori dalle colonne: non visti (fase C).

## 4. Documentazione

`fable-director/README.md` e `README.md`: sezione breve «Anonymizer (fase A: CLI)» con i comandi e la frase «spento di default, nessun valore nei log». `docs/INTERNALS.md`: pipeline degli span e formato mappa/corpus. `CHANGELOG.md` sotto Unreleased. `docs/plans/2026-09-09-anonymizer.md` §6 riga A: stato e data. Nessuna release in questa sessione (la fa Franz).
